"""
generate_public_submission.py: Run the DataMate pipeline on data/public_test/public_test/questions.jsonl
and generate a strictly compliant submission.jsonl for evaluation.

Supports:
- Pure Fast Path (deterministic rule-based)
- Hybrid Fast-Slow Path (Fast Path -> LoRA SLM -> Safe Abstain)
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from zaic_2026_datamate.data_loader import BusinessContext, DataLoader
from zaic_2026_datamate.pipeline import PipelineConfig, build_agent
from zaic_2026_datamate.provenance import fetch_model_provenance
from zaic_2026_datamate.submission import write_submission
from zaic_2026_datamate.llm_client import OpenAICompatibleClient

logging.basicConfig(level=logging.WARNING)


def generate_submission(
    questions_path: str | Path = "data/public_test/public_test/questions.jsonl",
    data_dir: str | Path = "data/public_test/public_test/data",
    calendar_path: str | Path = "data/public_test/public_test/calendar.csv",
    output_path: str | Path = "submission.jsonl",
    enable_slow_path: bool = False,
    base_url: Optional[str] = None,
    model: str = "datamate-business24",
    confidence_threshold: float = 0.95,
    limit: Optional[int] = None,
    overwrite: bool = False,
    order_paraphrases: bool = False,
    monthly_growth: bool = False,
) -> Dict[str, int]:
    questions_path = Path(questions_path)
    data_dir = Path(data_dir)
    calendar_path = Path(calendar_path)
    output_path = Path(output_path)

    if limit is not None and (limit <= 0 or output_path.name == "submission.jsonl"):
        raise ValueError("--limit requires a positive limit and a separate preview output filename")
    if output_path.exists() and not overwrite:
        raise FileExistsError(f"Output exists: {output_path}; use --overwrite explicitly")
    expected_ids = [json.loads(line)["id"] for line in questions_path.read_text().splitlines() if line.strip()]
    if limit is not None:
        expected_ids = expected_ids[:limit]
    loader = DataLoader(data_dir, calendar_path)
    business_cache: Dict[str, BusinessContext] = {}

    config = PipelineConfig(enable_slow_path=enable_slow_path, order_paraphrases=order_paraphrases,
                            model=model, confidence_threshold=confidence_threshold, monthly_growth=monthly_growth)
    llm_client = None
    if enable_slow_path:
        url = base_url or os.getenv("LLM_BASE_URL", "http://localhost:8000/v1")
        fetch_model_provenance(url, model)
        print(f"🧠 Kích hoạt SLOW PATH: Endpoint={url}, Model={model}, Confidence Threshold={confidence_threshold}")
        llm_client = OpenAICompatibleClient(base_url=url, model=model)

    agent = build_agent(config, llm_client)

    print(f"🚀 BẮT ĐẦU CHẠY PIPELINE TRÊN TẬP PUBLIC TEST: {questions_path}")
    print("=" * 75)

    stats = {
        "total": 0,
        "answered": 0,
        "fast_path": 0,
        "slow_path": 0,
        "paraphrase_fast_path": 0,
        "monthly_growth_path": 0,
        "abstain": 0,
        "unanswerable": 0,
        "number": 0,
        "percent": 0,
        "string": 0,
        "list": 0,
    }

    start_time = time.time()
    submission_rows: List[Dict[str, Any]] = []

    try:
        with open(questions_path, "r", encoding="utf-8") as f:
            for idx, line in enumerate(f):
                if limit and idx >= limit:
                    break
                if not line.strip():
                    continue
                q = json.loads(line)
                qid = q["id"]
                biz_id = q["business_id"]
                q_text = q["question"]
                as_of_date = q.get("as_of_date")
                stats["total"] += 1

                # Cache business context
                if biz_id not in business_cache:
                    business_cache[biz_id] = loader.load_business(biz_id)
                ctx = business_cache[biz_id]

                # Resolve query via Hybrid cascade
                ans_type, ans_val, reason, route = agent.answer_query(
                    question=q_text,
                    context=ctx,
                    as_of_date=as_of_date,
                )

                if ans_type == "abstain":
                    stats["abstain"] += 1
                    row = {
                        "id": qid,
                        "type": "abstain",
                    }
                elif ans_type == "unanswerable":
                    stats["unanswerable"] += 1
                    stats["answered"] += 1
                    stats[route] += 1
                    row = {
                        "id": qid,
                        "type": "unanswerable",
                        "reason": reason or "Không đủ dữ liệu để trả lời",
                    }
                else:
                    stats["answered"] += 1
                    stats[route] += 1
                    stats[ans_type] = stats.get(ans_type, 0) + 1
                    row = {
                        "id": qid,
                        "type": ans_type,
                        "answer": ans_val,
                    }

                submission_rows.append(row)

                if (idx + 1) % 50 == 0 or (idx + 1) == 1000 or (limit and (idx + 1) == limit):
                    elapsed = time.time() - start_time
                    print(
                        f"[{idx + 1:04d}/{limit or 1000}] Đã xử lý {idx + 1} câu | "
                        f"Fast: {stats['fast_path']} | Slow: {stats['slow_path']} | "
                        f"Abstain: {stats['abstain']} | Thời gian: {elapsed:.1f}s",
                        flush=True,
                    )

    finally:
        for context in business_cache.values():
            context.duckdb_conn.close()

    write_submission(output_path, submission_rows, expected_ids, overwrite=overwrite)

    total_time = time.time() - start_time
    print("\n" + "=" * 75)
    print("✅ ĐÃ XUẤT FILE NỘP BÀI THÀNH CÔNG:")
    print(f"📁 Đường dẫn: {output_path.resolve()}")
    print(f"Tổng số câu:       {stats['total']}")
    print(f"🎯 Số câu trả lời:  {stats['answered']} ({stats['answered'] / stats['total'] * 100:.1f}%)")
    print(f"   ⚡ Fast Path:    {stats['fast_path']}")
    print(f"   🧠 Slow Path:    {stats['slow_path']}")
    print(f"   Count fallback: {stats['paraphrase_fast_path']}")
    print(f"   Monthly growth: {stats['monthly_growth_path']}")
    print(f"   - number:        {stats['number']}")
    print(f"   - percent:       {stats['percent']}")
    print(f"   - string:        {stats['string']}")
    print(f"   - list:          {stats['list']}")
    print(f"   - unanswerable:  {stats['unanswerable']}")
    print(f"⚪ Số câu từ chối:  {stats['abstain']} ({stats['abstain'] / stats['total'] * 100:.1f}%) [0 điểm - không phạt]")
    print(f"⏱️ Tổng thời gian:   {total_time:.2f}s (Trung bình: {total_time / stats['total'] * 1000:.1f}ms/câu)")
    print("=" * 75)
    return stats


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--questions", default="data/public_test/public_test/questions.jsonl")
    parser.add_argument("--data-dir", default="data/public_test/public_test/data")
    parser.add_argument("--calendar", default="data/public_test/public_test/calendar.csv")
    parser.add_argument("--output", default="submission.jsonl")
    parser.add_argument("--enable-slow-path", action="store_true")
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--model", default="datamate-business24")
    parser.add_argument("--order-paraphrases", "--daily-order-paraphrases", action="store_true")
    parser.add_argument("--monthly-growth", action="store_true")
    parser.add_argument("--confidence-threshold", type=float, default=0.95)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    generate_submission(
        questions_path=args.questions,
        data_dir=args.data_dir,
        calendar_path=args.calendar,
        output_path=args.output,
        enable_slow_path=args.enable_slow_path,
        base_url=args.base_url,
        model=args.model,
        confidence_threshold=args.confidence_threshold,
        limit=args.limit,
        overwrite=args.overwrite,
        order_paraphrases=args.order_paraphrases,
        monthly_growth=args.monthly_growth,
    )
