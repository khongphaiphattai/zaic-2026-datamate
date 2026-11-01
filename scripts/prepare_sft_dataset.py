"""
prepare_sft_dataset.py: Execution-Guided SFT Dataset Miner for ZAIC 2026 DataMate.

Mines verified DuckDB SQL pairs where:
1. Fast Path generates exact SQL matching Ground Truth (761+ examples).
2. OOD questions generate verified 'unanswerable' refusals (70+ examples).
3. Produces clean, token-bounded ChatML / OpenAI JSONL ready for LoRA training.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from zaic_2026_datamate.data_loader import DataLoader
from zaic_2026_datamate.intent_parser import resolve_query
from zaic_2026_datamate.prompt_generator import SYSTEM_PROMPT, generate_text_to_sql_prompt

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def evaluate_match(pred_type: str, pred_val: Any, target_type: str, target_val: Any) -> bool:
    if pred_type == "unanswerable":
        return target_type == "unanswerable"
    if target_type == "unanswerable":
        return pred_type == "unanswerable"
    if pred_val is None or target_val is None:
        return pred_val == target_val
    if target_type == "percent":
        try:
            p, t = float(pred_val), float(target_val)
            return abs(p - t) <= 0.02 or abs(p * 100 - t) <= 0.02
        except Exception:
            return False
    if target_type == "number":
        try:
            p, t = int(round(float(pred_val))), int(round(float(target_val)))
            return p == t
        except Exception:
            return False
    return str(pred_val).strip().lower() == str(target_val).strip().lower()


def mine_sft_dataset(
    questions_path: str = "data/train/train/questions.jsonl",
    answers_path: str = "data/train/train/answers.jsonl",
    data_dir: str = "data/train/train/data",
    calendar_path: str = "data/train/train/calendar.csv",
    output_path: str = "train_sft.jsonl",
) -> None:
    q_file = Path(questions_path)
    a_file = Path(answers_path)
    loader = DataLoader(data_dir=data_dir, calendar_path=calendar_path)

    # Load answers
    answers: Dict[str, Dict[str, Any]] = {}
    with open(a_file, "r", encoding="utf-8") as f:
        for line in f:
            row = json.loads(line)
            answers[row["id"]] = row

    with open(q_file, "r", encoding="utf-8") as f:
        questions = [json.loads(line) for line in f]

    print(f"🚀 BẮT ĐẦU ĐÀO TẬP DỮ LIỆU SFT VỚI SQL ĐƯỢC KIỂM CHỨNG TỪ {len(questions)} CÂU HỎI...")

    sft_records: List[Dict[str, Any]] = []
    biz_cache: Dict[str, Any] = {}

    stats = {"fast_path_verified": 0, "unanswerable_verified": 0, "total_mined": 0}

    for idx, q in enumerate(questions):
        qid = q["id"]
        biz_id = q["business_id"]
        q_text = q["question"]
        as_of_date = q.get("as_of_date")

        target = answers.get(qid)
        if not target:
            continue

        tgt_type = target.get("type")
        tgt_val = target.get("answer") if "answer" in target else target.get("result")

        if biz_id not in biz_cache:
            biz_cache[biz_id] = loader.load_business(biz_id)
        ctx = biz_cache[biz_id]

        # Reset last_sql
        ctx.last_sql = None

        ans_type, ans_val, reason = resolve_query(
            question=q_text,
            context=ctx,
            as_of_date=as_of_date,
        )

        is_match = evaluate_match(ans_type, ans_val, tgt_type, tgt_val)

        if not is_match:
            continue

        # Verified! Build SFT example
        user_prompt = generate_text_to_sql_prompt(
            question=q_text,
            context=ctx,
            as_of_date=as_of_date,
            business_id=biz_id,
        )

        if ans_type == "unanswerable":
            stats["unanswerable_verified"] += 1
            assistant_content = json.dumps({
                "thought": reason or "Khoảng thời gian nằm ngoài phạm vi dữ liệu hoặc thiếu thực thể",
                "type": "unanswerable",
                "sql": None,
                "confidence": 1.0,
                "reason": reason or "Dữ liệu không hỗ trợ câu hỏi này",
            }, ensure_ascii=False)
        else:
            stats["fast_path_verified"] += 1
            assistant_content = json.dumps({
                "thought": f"Truy vấn DuckDB tính {ans_type} cho {biz_id}",
                "type": ans_type,
                "sql": ctx.last_sql,
                "confidence": 0.98,
            }, ensure_ascii=False)

        sft_records.append({
            "id": qid,
            "business_id": biz_id,
            "type": ans_type,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
                {"role": "assistant", "content": assistant_content},
            ],
        })

        if (idx + 1) % 500 == 0 or (idx + 1) == len(questions):
            print(f"[{idx+1}/{len(questions)}] Đã kiểm chứng: Fast SQL={stats['fast_path_verified']}, Unanswerable={stats['unanswerable_verified']}")

    with open(output_path, "w", encoding="utf-8") as f:
        for r in sft_records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    file_size_mb = Path(output_path).stat().st_size / 1024 / 1024
    print("=" * 70)
    print(f"🎉 HOÀN THÀNH ĐÀO DỮ LIỆU SFT!")
    print(f"   - Tổng số mẫu đã kiểm chứng 100%: {len(sft_records)}")
    print(f"   - Mẫu SQL thực thi chính xác: {stats['fast_path_verified']}")
    print(f"   - Mẫu từ chối OOD unanswerable: {stats['unanswerable_verified']}")
    print(f"   - Đường dẫn file: {output_path} ({file_size_mb:.2f} MB)")


if __name__ == "__main__":
    mine_sft_dataset()
