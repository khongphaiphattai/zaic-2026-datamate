"""TDD specs for Mũi 9 [P0]: per-question logging, read-only /data container entrypoint, and reproducibility."""

from __future__ import annotations

import json
import time

import duckdb
import pandas as pd

from zaic_2026_datamate.data_loader import BusinessContext
from zaic_2026_datamate.pipeline import build_agent, production_pipeline_config


def _make_sales_ctx(business_id: str = "test") -> BusinessContext:
    df = pd.DataFrame(
        {
            "ma_don_hang": ["D1", "D2", "D3"],
            "ngay_dat_hang": ["2025-06-02", "2025-06-02", "2025-06-03"],
            "trang_thai": ["Hoàn thành", "Hoàn thành", "Đã hủy"],
            "tong_tien": [100_000, 250_000, 999_000],
        }
    )
    conn = duckdb.connect(":memory:")
    conn.register("don_hang", df)
    return BusinessContext(
        business_id=business_id,
        data_dir=".",
        tables={"don_hang": df},
        duckdb_conn=conn,
    )


def test_p0_1_execution_log_captures_sql_pandas_and_json_safe_outputs():
    from zaic_2026_datamate.container_runner import build_question_log_record

    ctx = _make_sales_ctx()
    try:
        agent = build_agent(production_pipeline_config())

        # 1. SQL-backed / paraphrase route
        t0 = time.monotonic()
        ans_type, ans_val, reason, route = agent.answer_query(
            "Doanh thu ngày 02/06/2025 là bao nhiêu?",
            ctx,
            as_of_date="2025-06-05",
        )
        elapsed = time.monotonic() - t0
        assert ans_type == "number" and ans_val == 350000

        record = build_question_log_record(
            question_id="q_sql_1",
            business_id=ctx.business_id,
            question="Doanh thu ngày 02/06/2025 là bao nhiêu?",
            as_of_date="2025-06-05",
            ans_type=ans_type,
            ans_val=ans_val,
            reason=reason,
            route=route,
            elapsed_seconds=elapsed,
            context=ctx,
            agent=agent,
        )
        for key in (
            "question_id",
            "final_answer",
            "elapsed_time",
            "generated_sql_or_code",
            "execution_output",
        ):
            assert key in record
        assert record["question_id"] == "q_sql_1"
        assert record["final_answer"] == {"id": "q_sql_1", "type": "number", "answer": 350000}
        assert isinstance(record["elapsed_time"], float) and record["elapsed_time"] >= 0.0
        assert "SELECT" in record["generated_sql_or_code"].upper()
        json.dumps(record, ensure_ascii=False, allow_nan=False)

        # 2. Trace resets cleanly on subsequent abstain / non-SQL call and sanitizes NaN / Timestamp
        ctx.last_execution_output = {
            "bad_float": float("nan"),
            "ts": pd.Timestamp("2025-06-02"),
        }
        t1 = time.monotonic()
        ans_type2, ans_val2, reason2, route2 = agent.answer_query(
            "Câu hỏi không tồn tại trong schema xyz?",
            ctx,
            as_of_date="2025-06-05",
        )
        elapsed2 = time.monotonic() - t1
        assert ans_type2 == "abstain"
        record2 = build_question_log_record(
            question_id="q_abs_2",
            business_id=ctx.business_id,
            question="Câu hỏi không tồn tại trong schema xyz?",
            as_of_date="2025-06-05",
            ans_type=ans_type2,
            ans_val=ans_val2,
            reason=reason2,
            route=route2,
            elapsed_seconds=elapsed2,
            context=ctx,
            agent=agent,
        )
        assert record2["final_answer"] == {"id": "q_abs_2", "type": "abstain"}
        assert "SELECT" not in record2["generated_sql_or_code"].upper()
        assert record2["generated_sql_or_code"]
        json.dumps(record2, ensure_ascii=False, allow_nan=False)
    finally:
        ctx.duckdb_conn.close()


def test_p0_1_pandas_vertical_emits_non_empty_code_path():
    from zaic_2026_datamate.container_runner import build_question_log_record

    tg_df = pd.DataFrame(
        [
            {
                "ngay": "2025-07-03",
                "stt": 1,
                "khach": "An",
                "sdt": "0901",
                "may": "Phone A",
                "gia": 10_000_000.0,
                "tra_truoc": "2tr",
                "so_ky": 4,
                "gop_thang": "2tr",
                "han_gop_ngay": 3,
                "ngay_gop": "2025-08-03",
                "da_gop": "2tr",
            }
        ]
    )
    sbh_df = pd.DataFrame(
        [{"ngay": "2025-07-03", "stt": 1, "sheet_name": "07.2025", "hang": "Phone A", "sl": 1, "thanh_tien": 10_000_000.0, "ghi_chu": ""}]
    )
    conn = duckdb.connect(":memory:")
    conn.register("so_no_tra_gop", tg_df)
    conn.register("so_ban_hang", sbh_df)
    ctx = BusinessContext(
        business_id="test",
        data_dir=".",
        tables={"so_no_tra_gop": tg_df, "so_ban_hang": sbh_df},
        duckdb_conn=conn,
    )
    try:
        agent = build_agent(production_pipeline_config())
        ans_type, ans_val, reason, route = agent.answer_query(
            "Hết ngày 2025-08-10, khách An còn thiếu bao nhiêu tiền trả góp?",
            ctx,
            as_of_date="2025-08-10",
        )
        assert ans_type == "number" and ans_val == 6_000_000
        record = build_question_log_record(
            question_id="q_pd_1",
            business_id=ctx.business_id,
            question="Hết ngày 2025-08-10, khách An còn thiếu bao nhiêu tiền trả góp?",
            as_of_date="2025-08-10",
            ans_type=ans_type,
            ans_val=ans_val,
            reason=reason,
            route=route,
            elapsed_seconds=0.01,
            context=ctx,
            agent=agent,
        )
        assert "mobile_installment" in record["generated_sql_or_code"]
        json.dumps(record, ensure_ascii=False, allow_nan=False)
    finally:
        conn.close()


def test_p0_2_read_only_data_sqlite_logs_and_sha256_reproducibility(tmp_path):
    import hashlib
    import os
    import sqlite3
    import stat

    from zaic_2026_datamate.container_runner import (
        discover_dataset_paths,
        run_container,
    )

    # Nested split layout under read-only /data mount
    data_mount = tmp_path / "data_ro"
    split_dir = data_mount / "eval_split"
    biz1 = split_dir / "data" / "fixture_shop_1"
    biz2 = split_dir / "data" / "fixture_shop_2"
    biz1.mkdir(parents=True)
    biz2.mkdir(parents=True)

    # CSV business
    pd.DataFrame(
        [
            {
                "ma_don_hang": "A1",
                "ngay_dat_hang": "2025-07-01",
                "trang_thai": "Hoàn thành",
                "tong_tien": 120_000,
            },
            {
                "ma_don_hang": "A2",
                "ngay_dat_hang": "2025-07-01",
                "trang_thai": "Đã hủy",
                "tong_tien": 50_000,
            },
        ]
    ).to_csv(biz1 / "don_hang.csv", index=False)

    # SQLite business (must open read-only safely even when directory is chmod a-w)
    sq_conn = sqlite3.connect(biz2 / "pos.sqlite")
    sq_conn.execute(
        "CREATE TABLE don_hang (ma_don_hang TEXT, ngay_dat_hang TEXT, trang_thai TEXT, tong_tien REAL)"
    )
    sq_conn.execute(
        "INSERT INTO don_hang VALUES ('S1', '2025-07-02', 'Hoàn thành', 300000)"
    )
    sq_conn.commit()
    sq_conn.close()

    pd.DataFrame([{"date": "2025-07-02"}]).to_csv(split_dir / "calendar.csv", index=False)
    questions = [
        {
            "id": "q_ro_1",
            "business_id": "fixture_shop_1",
            "question": "Doanh thu ngày 01/07/2025 là bao nhiêu?",
            "as_of_date": "2025-07-05",
        },
        {
            "id": "q_ro_2",
            "business_id": "fixture_shop_2",
            "question": "Doanh thu ngày 02/07/2025 là bao nhiêu?",
            "as_of_date": "2025-07-05",
        },
        {
            "id": "q_ro_3",
            "business_id": "fixture_shop_1",
            "question": "Chỉ số không tồn tại xyz?",
            "as_of_date": "2025-07-05",
        },
    ]
    (split_dir / "questions.jsonl").write_text(
        "\n".join(json.dumps(q, ensure_ascii=False) for q in questions) + "\n",
        encoding="utf-8",
    )

    # Lock down /data strictly read-only before running
    all_paths = sorted(data_mount.rglob("*"), reverse=True)
    for p in all_paths:
        mode = stat.S_IMODE(p.stat().st_mode)
        os.chmod(p, mode & ~0o222)
    os.chmod(data_mount, stat.S_IMODE(data_mount.stat().st_mode) & ~0o222)

    try:
        discovered = discover_dataset_paths(data_mount)
        assert discovered.questions_path == split_dir / "questions.jsonl"
        assert discovered.data_dir == split_dir / "data"
        assert discovered.calendar_path == split_dir / "calendar.csv"

        res1 = tmp_path / "result_1"
        res2 = tmp_path / "result_2"
        tmp_work = tmp_path / "tmp_work"
        run_container(data_dir=data_mount, result_dir=res1, temp_dir=tmp_work)
        run_container(data_dir=data_mount, result_dir=res2, temp_dir=tmp_work)

        sub1 = res1 / "submission.jsonl"
        sub2 = res2 / "submission.jsonl"
        assert sub1.is_file() and sub2.is_file()
        sha1 = hashlib.sha256(sub1.read_bytes()).hexdigest()
        sha2 = hashlib.sha256(sub2.read_bytes()).hexdigest()
        assert sha1 == sha2

        rows = [json.loads(line) for line in sub1.read_text(encoding="utf-8").splitlines()]
        assert rows == [
            {"id": "q_ro_1", "type": "number", "answer": 120000},
            {"id": "q_ro_2", "type": "number", "answer": 300000},
            {"id": "q_ro_3", "type": "abstain"},
        ]

        q_logs_path = res1 / "logs" / "questions.jsonl"
        summary_path = res1 / "logs" / "summary.json"
        assert q_logs_path.is_file() and summary_path.is_file()
        log_rows = [json.loads(line) for line in q_logs_path.read_text(encoding="utf-8").splitlines()]
        assert len(log_rows) == 3
        assert [r["question_id"] for r in log_rows] == ["q_ro_1", "q_ro_2", "q_ro_3"]
        for lr in log_rows:
            assert {"question_id", "final_answer", "elapsed_time", "generated_sql_or_code", "execution_output"} <= set(lr)
            assert lr["generated_sql_or_code"]
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        assert summary["submission_sha256"] == sha1
        assert summary["total"] == 3 and summary["answered"] == 2 and summary["abstain"] == 1
        assert summary["seed"] == 42 and summary["temperature"] == 0.0
    finally:
        # Restore write permissions so pytest tmp_path cleanup succeeds
        os.chmod(data_mount, stat.S_IMODE(data_mount.stat().st_mode) | 0o700)
        for p in sorted(data_mount.rglob("*")):
            os.chmod(p, stat.S_IMODE(p.stat().st_mode) | (0o700 if p.is_dir() else 0o600))


def test_p0_3_direct_layout_cli_and_docker_packaging_contract(tmp_path):
    import subprocess
    import sys
    from pathlib import Path

    from zaic_2026_datamate.container_runner import discover_dataset_paths

    # Direct /data layout without calendar.csv
    data_mount = tmp_path / "data_direct"
    biz_dir = data_mount / "data" / "fixture_direct"
    biz_dir.mkdir(parents=True)
    pd.DataFrame(
        [
            {
                "ma_don_hang": "D1",
                "ngay_dat_hang": "2025-08-01",
                "trang_thai": "Hoàn thành",
                "tong_tien": 450_000,
            }
        ]
    ).to_csv(biz_dir / "don_hang.csv", index=False)
    (data_mount / "questions.jsonl").write_text(
        json.dumps(
            {
                "id": "q_cli_1",
                "business_id": "fixture_direct",
                "question": "Doanh thu ngày 01/08/2025 là bao nhiêu?",
                "as_of_date": "2025-08-05",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    paths = discover_dataset_paths(data_mount)
    assert paths.questions_path == data_mount / "questions.jsonl"
    assert paths.data_dir == data_mount / "data"
    assert paths.calendar_path is None

    res_dir = tmp_path / "result_cli"
    tmp_dir = tmp_path / "tmp_cli"
    proc = subprocess.run(
        [
            sys.executable,
            "scripts/run_container.py",
            "--data-dir",
            str(data_mount),
            "--result-dir",
            str(res_dir),
            "--temp-dir",
            str(tmp_dir),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "SHA256=" in proc.stdout
    assert (res_dir / "submission.jsonl").is_file()
    assert (res_dir / "logs" / "questions.jsonl").is_file()
    assert (res_dir / "logs" / "summary.json").is_file()

    # Validate Dockerfile & .dockerignore contract
    dockerfile = Path("Dockerfile")
    dockerignore = Path(".dockerignore")
    assert dockerfile.is_file(), "Missing Dockerfile at repo root"
    assert dockerignore.is_file(), "Missing .dockerignore at repo root"

    df_text = dockerfile.read_text(encoding="utf-8")
    assert "uv sync --frozen --no-dev" in df_text
    assert "TMPDIR=/tmp" in df_text
    assert "DUCKDB_TEMP_DIRECTORY=/tmp" in df_text
    assert "PYTHONHASHSEED=42" in df_text
    assert "scripts/run_container.py" in df_text

    di_lines = {line.strip() for line in dockerignore.read_text(encoding="utf-8").splitlines()}
    for ignored in ("data", ".venv", ".git", "artifacts", "submission.jsonl", "*.zip"):
        assert any(ignored in ln for ln in di_lines), f".dockerignore missing {ignored}"


