"""
generate_public_submission.py: Run the DataMate pipeline on data/public_test/public_test/questions.jsonl
and generate a strictly compliant submission.jsonl for evaluation.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from zaic_2026_datamate.data_loader import BusinessContext, DataLoader
from zaic_2026_datamate.intent_parser import resolve_query

logging.basicConfig(level=logging.WARNING)


def generate_submission(
    questions_path: str | Path = "data/public_test/public_test/questions.jsonl",
    data_dir: str | Path = "data/public_test/public_test/data",
    calendar_path: str | Path = "data/public_test/public_test/calendar.csv",
    output_path: str | Path = "submission.jsonl",
) -> None:
    questions_path = Path(questions_path)
    data_dir = Path(data_dir)
    calendar_path = Path(calendar_path)
    output_path = Path(output_path)

    loader = DataLoader(data_dir, calendar_path)
    business_cache: Dict[str, BusinessContext] = {}

    print(f"🚀 BẮT ĐẦU CHẠY PIPELINE TRÊN TẬP PUBLIC TEST: {questions_path}")
    print("=" * 75)

    stats = {
        "total": 0,
        "answered": 0,
        "abstain": 0,
        "unanswerable": 0,
        "number": 0,
        "percent": 0,
        "string": 0,
        "list": 0,
    }

    start_time = time.time()
    submission_rows: List[Dict[str, Any]] = []

    with open(questions_path, "r", encoding="utf-8") as f:
        for idx, line in enumerate(f):
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

            # Resolve query
            ans_type, ans_val, reason = resolve_query(q_text, ctx, as_of_date=as_of_date)

            if ans_type == "abstain":
                stats["abstain"] += 1
                row = {
                    "id": qid,
                    "type": "abstain",
                }
            elif ans_type == "unanswerable":
                stats["unanswerable"] += 1
                stats["answered"] += 1
                row = {
                    "id": qid,
                    "type": "unanswerable",
                    "reason": reason or "Không đủ dữ liệu để trả lời",
                }
            else:
                stats["answered"] += 1
                stats[ans_type] = stats.get(ans_type, 0) + 1
                row = {
                    "id": qid,
                    "type": ans_type,
                    "answer": ans_val,
                }

            submission_rows.append(row)

            if (idx + 1) % 100 == 0 or (idx + 1) == 1000:
                elapsed = time.time() - start_time
                print(
                    f"[{idx + 1:04d}/1000] Đã xử lý {idx + 1} câu | "
                    f"Đã trả lời: {stats['answered']} | Từ chối (Abstain): {stats['abstain']} | "
                    f"Thời gian: {elapsed:.1f}s"
                )

    # Write out submission.jsonl
    with open(output_path, "w", encoding="utf-8") as f_out:
        for row in submission_rows:
            f_out.write(json.dumps(row, ensure_ascii=False) + "\n")

    total_time = time.time() - start_time
    print("\n" + "=" * 75)
    print("✅ ĐÃ XUẤT FILE NỘP BÀI THÀNH CÔNG:")
    print(f"📁 Đường dẫn: {output_path.resolve()}")
    print(f"Tổng số câu:       {stats['total']}")
    print(f"🎯 Số câu trả lời:  {stats['answered']} ({stats['answered'] / stats['total'] * 100:.1f}%)")
    print(f"   - number:        {stats['number']}")
    print(f"   - percent:       {stats['percent']}")
    print(f"   - string:        {stats['string']}")
    print(f"   - list:          {stats['list']}")
    print(f"   - unanswerable:  {stats['unanswerable']}")
    print(f"⚪ Số câu từ chối:  {stats['abstain']} ({stats['abstain'] / stats['total'] * 100:.1f}%) [0 điểm - không phạt]")
    print(f"⏱️ Tổng thời gian:   {total_time:.2f}s (Trung bình: {total_time / stats['total'] * 1000:.1f}ms/câu)")
    print("=" * 75)


if __name__ == "__main__":
    generate_submission()
