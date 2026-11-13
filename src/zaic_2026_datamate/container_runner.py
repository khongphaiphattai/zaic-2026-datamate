"""Container entrypoint, execution trace logging, and read-only /data runner."""

from __future__ import annotations

import hashlib
import inspect
import json
import math
import os
import random
import tempfile
import time
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from zaic_2026_datamate.data_loader import BusinessContext, DataLoader
from zaic_2026_datamate.llm_client import OpenAICompatibleClient
from zaic_2026_datamate.pipeline import (
    PipelineConfig,
    build_agent,
    production_pipeline_config,
)
from zaic_2026_datamate.provenance import fetch_model_provenance
from zaic_2026_datamate.submission import write_submission


@dataclass(frozen=True)
class DatasetPaths:
    """Resolved input file paths inside a read-only split or container /data mount."""

    questions_path: Path
    data_dir: Path
    calendar_path: Path | None = None


def discover_dataset_paths(data_root: str | Path) -> DatasetPaths:
    """Locate questions.jsonl, data/, and optional calendar.csv under a read-only root."""
    root = Path(data_root)
    if not root.exists():
        raise FileNotFoundError(f"Data directory not found: {root}")

    candidates: list[Path] = []
    if (root / "questions.jsonl").is_file() and (root / "data").is_dir():
        candidates.append(root)
    else:
        for depth_pattern in ("*/questions.jsonl", "*/*/questions.jsonl"):
            for q_file in sorted(root.glob(depth_pattern)):
                parent = q_file.parent
                if (parent / "data").is_dir():
                    candidates.append(parent)
            if candidates:
                break

    if not candidates:
        raise FileNotFoundError(
            f"Could not find questions.jsonl paired with data/ under {root}"
        )
    if len(candidates) > 1:
        raise ValueError(
            f"Ambiguous dataset layout under {root}: {[str(c) for c in candidates]}"
        )

    split_dir = candidates[0]
    cal = split_dir / "calendar.csv"
    if not cal.is_file():
        cal_root = root / "calendar.csv"
        cal = cal_root if cal_root.is_file() else None
    return DatasetPaths(
        questions_path=split_dir / "questions.jsonl",
        data_dir=split_dir / "data",
        calendar_path=cal,
    )


def format_submission_row(
    question_id: str,
    ans_type: str,
    ans_val: Any,
    reason: str | None = None,
) -> dict[str, Any]:
    """Format one prediction into a benchmark-compliant submission row."""
    if ans_type == "abstain":
        return {"id": question_id, "type": "abstain"}
    if ans_type == "unanswerable":
        return {
            "id": question_id,
            "type": "unanswerable",
            "reason": reason or "Không đủ dữ liệu để trả lời",
        }
    val = ans_val
    if ans_type == "number" and val is not None:
        val = (
            float(round(val, 2))
            if isinstance(val, float) and not val.is_integer()
            else int(round(val))
        )
    elif ans_type == "percent" and val is not None:
        val = float(round(val, 2))
    return {"id": question_id, "type": ans_type, "answer": val}


def sanitize_json_value(value: Any) -> Any:
    """Recursively convert pandas/numpy/non-finite values into strict JSON-safe types."""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, str)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, (pd.Timestamp, datetime, date)):
        return None if pd.isna(value) else value.isoformat()
    if hasattr(value, "item") and callable(value.item):
        return sanitize_json_value(value.item())
    if isinstance(value, pd.DataFrame):
        return sanitize_json_value(value.head(5).to_dict(orient="records"))
    if isinstance(value, dict):
        return {str(k): sanitize_json_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [sanitize_json_value(v) for v in value]
    try:
        if pd.isna(value):
            return None
    except TypeError, ValueError:
        pass
    return str(value)


def _describe_generated_sql_or_code(
    context: BusinessContext,
    route: str,
    agent: Any = None,
) -> str:
    """Return the executed DuckDB SQL(s) or deterministic code-path identifier."""
    if context.sql_history:
        return ";\n".join(context.sql_history)
    if context.last_sql:
        return context.last_sql
    slow = getattr(agent, "slow_engine", None)
    if slow is not None and getattr(slow, "last_sql_attempt", None):
        return str(slow.last_sql_attempt)
    if route == "paraphrase_fast_path":
        canon = getattr(agent, "last_canonical_question", None)
        return f"python:paraphrase_fast.DailyOrderParaphraseAgent(canonical={canon!r})"
    if route == "monthly_growth_path":
        return "python:monthly_growth.MonthlyGrowthAgent"
    if route == "mtd_growth_path":
        return "python:mtd_growth.MTDGrowthAgent"
    if context.last_code_path:
        return f"python:{context.last_code_path}"
    return f"python:route:{route}"


def build_question_log_record(
    *,
    question_id: str,
    business_id: str,
    question: str,
    as_of_date: str | None,
    ans_type: str,
    ans_val: Any,
    reason: str | None,
    route: str,
    elapsed_seconds: float,
    context: BusinessContext,
    agent: Any = None,
) -> dict[str, Any]:
    """Build a JSON-safe per-question execution log record per ZAIC Guide §4.6."""
    final_row = format_submission_row(question_id, ans_type, ans_val, reason)
    exec_output: dict[str, Any] = {
        "type": ans_type,
        "value": ans_val,
        "reason": reason,
    }
    if context.last_execution_output is not None:
        exec_output["sql_output"] = context.last_execution_output
    if route == "monthly_growth_path" and getattr(agent, "last_monthly_growth", None):
        exec_output["monthly_growth_audit"] = agent.last_monthly_growth
    if route == "mtd_growth_path" and getattr(agent, "last_mtd_growth", None):
        exec_output["mtd_growth_audit"] = agent.last_mtd_growth

    return {
        "question_id": question_id,
        "business_id": business_id,
        "question": question,
        "as_of_date": as_of_date,
        "route": route,
        "final_answer": sanitize_json_value(final_row),
        "elapsed_time": round(float(elapsed_seconds), 6),
        "generated_sql_or_code": _describe_generated_sql_or_code(
            context, route, agent
        ),
        "execution_output": sanitize_json_value(exec_output),
    }


def _instantiate_loader(
    loader_cls: type[Any],
    data_dir: Path,
    calendar_path: Path | None,
    *,
    use_default_calendar: bool,
    temp_dir: Path | None,
) -> Any:
    """Instantiate DataLoader (or test mock) while keeping compatibility with simple stubs."""
    sig = inspect.signature(loader_cls)
    kwargs: dict[str, Any] = {}
    if "use_default_calendar" in sig.parameters:
        kwargs["use_default_calendar"] = use_default_calendar
    if "temp_dir" in sig.parameters and temp_dir is not None:
        kwargs["temp_dir"] = temp_dir
    return loader_cls(data_dir, calendar_path, **kwargs)


def run_pipeline_to_result(
    *,
    questions_path: str | Path,
    data_dir: str | Path,
    calendar_path: str | Path | None,
    output_path: str | Path,
    config: PipelineConfig,
    log_dir: str | Path | None = None,
    temp_dir: str | Path | None = None,
    base_url: str | None = None,
    limit: int | None = None,
    overwrite: bool = False,
    use_default_calendar: bool = True,
    progress_callback: Callable[[int, int, dict[str, int], float], None] | None = None,
    loader_cls: type[Any] = DataLoader,
    provenance_fn: Callable[[str, str], Any] = fetch_model_provenance,
    client_cls: type[Any] = OpenAICompatibleClient,
) -> dict[str, Any]:
    """Run the full DataMate pipeline deterministically and write submission + logs."""
    random.seed(42)
    os.environ.setdefault("PYTHONHASHSEED", "42")

    questions_path = Path(questions_path)
    data_dir = Path(data_dir)
    cal_path = Path(calendar_path) if calendar_path is not None else None
    output_path = Path(output_path)

    resolved_temp: Path | None = None
    if temp_dir is not None:
        resolved_temp = Path(temp_dir)
        resolved_temp.mkdir(parents=True, exist_ok=True)
        os.environ["TMPDIR"] = str(resolved_temp)
        os.environ["DUCKDB_TEMP_DIRECTORY"] = str(resolved_temp)
        tempfile.tempdir = str(resolved_temp)

    if limit is not None and (limit <= 0 or output_path.name == "submission.jsonl"):
        raise ValueError(
            "--limit requires a positive limit and a separate preview output filename"
        )
    if output_path.exists() and not overwrite:
        raise FileExistsError(
            f"Output exists: {output_path}; use --overwrite explicitly"
        )

    raw_questions = [
        json.loads(line)
        for line in questions_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if limit is not None:
        raw_questions = raw_questions[:limit]
    expected_ids = [q["id"] for q in raw_questions]

    llm_client = None
    if config.enable_slow_path:
        url = base_url or os.getenv("LLM_BASE_URL", "http://localhost:8000/v1")
        provenance_fn(url, config.model)
        llm_client = client_cls(base_url=url, model=config.model)

    loader = _instantiate_loader(
        loader_cls,
        data_dir,
        cal_path,
        use_default_calendar=use_default_calendar,
        temp_dir=resolved_temp,
    )
    agent = build_agent(config, llm_client)
    business_cache: dict[str, BusinessContext] = {}

    stats: dict[str, int] = {
        "total": 0,
        "answered": 0,
        "fast_path": 0,
        "slow_path": 0,
        "paraphrase_fast_path": 0,
        "monthly_growth_path": 0,
        "mtd_growth_path": 0,
        "abstain": 0,
        "unanswerable": 0,
        "number": 0,
        "percent": 0,
        "string": 0,
        "date": 0,
        "boolean": 0,
        "list": 0,
        "table": 0,
    }
    submission_rows: list[dict[str, Any]] = []
    start_time = time.monotonic()

    resolved_log_dir = Path(log_dir) if log_dir is not None else None
    log_handle = None
    if resolved_log_dir is not None:
        resolved_log_dir.mkdir(parents=True, exist_ok=True)
        log_handle = (resolved_log_dir / "questions.jsonl").open(
            "w", encoding="utf-8"
        )

    try:
        total_q = len(raw_questions)
        for idx, q in enumerate(raw_questions):
            qid = q["id"]
            bid = q["business_id"]
            q_text = q["question"]
            as_of_date = q.get("as_of_date")
            stats["total"] += 1

            if bid not in business_cache:
                business_cache[bid] = loader.load_business(bid)
            ctx = business_cache[bid]

            q_t0 = time.monotonic()
            ans_type, ans_val, reason, route = agent.answer_query(
                question=q_text,
                context=ctx,
                as_of_date=as_of_date,
            )
            q_elapsed = time.monotonic() - q_t0

            if ans_type == "abstain":
                stats["abstain"] += 1
            elif ans_type == "unanswerable":
                stats["unanswerable"] += 1
                stats["answered"] += 1
                stats[route] = stats.get(route, 0) + 1
            else:
                stats["answered"] += 1
                stats[route] = stats.get(route, 0) + 1
                stats[ans_type] = stats.get(ans_type, 0) + 1

            row = format_submission_row(qid, ans_type, ans_val, reason)
            submission_rows.append(row)

            if log_handle is not None:
                log_rec = build_question_log_record(
                    question_id=qid,
                    business_id=bid,
                    question=q_text,
                    as_of_date=as_of_date,
                    ans_type=ans_type,
                    ans_val=ans_val,
                    reason=reason,
                    route=route,
                    elapsed_seconds=q_elapsed,
                    context=ctx,
                    agent=agent,
                )
                log_handle.write(
                    json.dumps(log_rec, ensure_ascii=False, allow_nan=False) + "\n"
                )
                log_handle.flush()

            if progress_callback is not None and (
                (idx + 1) % 50 == 0 or (idx + 1) == total_q
            ):
                progress_callback(idx + 1, total_q, stats, time.monotonic() - start_time)
    finally:
        if log_handle is not None:
            log_handle.close()
        for context in business_cache.values():
            context.duckdb_conn.close()

    write_submission(output_path, submission_rows, expected_ids, overwrite=overwrite)
    total_elapsed = time.monotonic() - start_time
    submission_sha256 = hashlib.sha256(output_path.read_bytes()).hexdigest()

    if resolved_log_dir is not None:
        summary_payload = {
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "submission_path": str(output_path),
            "submission_sha256": submission_sha256,
            "offline": not config.enable_slow_path,
            "seed": 42,
            "temperature": 0.0,
            "config": asdict(config),
            "total_elapsed_seconds": round(total_elapsed, 4),
            **stats,
        }
        (resolved_log_dir / "summary.json").write_text(
            json.dumps(summary_payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    return {
        **stats,
        "total_elapsed_seconds": round(total_elapsed, 4),
        "submission_sha256": submission_sha256,
    }


def run_container(
    *,
    data_dir: str | Path = "/data",
    result_dir: str | Path = "/result",
    temp_dir: str | Path = "/tmp",
    overwrite: bool = True,
    enable_slow_path: bool = False,
    base_url: str | None = None,
    model: str = "datamate-business24",
    confidence_threshold: float = 0.95,
    limit: int | None = None,
) -> dict[str, Any]:
    """Container entrypoint reading /data (RO) and writing /result/submission.jsonl + /result/logs/."""
    paths = discover_dataset_paths(data_dir)
    result_root = Path(result_dir)
    config = production_pipeline_config(
        enable_slow_path=enable_slow_path,
        model=model,
        confidence_threshold=confidence_threshold,
    )
    return run_pipeline_to_result(
        questions_path=paths.questions_path,
        data_dir=paths.data_dir,
        calendar_path=paths.calendar_path,
        output_path=result_root / "submission.jsonl",
        log_dir=result_root / "logs",
        temp_dir=temp_dir,
        config=config,
        base_url=base_url,
        limit=limit,
        overwrite=overwrite,
        use_default_calendar=False,
    )
