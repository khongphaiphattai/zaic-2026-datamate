"""
prepare_sft_dataset.py: Generates high-quality Supervised Fine-Tuning (SFT) dataset
from ZAIC 2026 Train questions and answers for Qwen2.5-Coder-7B LoRA training on Colab A100.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from zaic_2026_datamate.data_loader import DataLoader
from zaic_2026_datamate.prompt_generator import SYSTEM_PROMPT, generate_text_to_sql_prompt

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def prepare_dataset(
    questions_path: str = "data/train/train/questions.jsonl",
    answers_path: str = "data/train/train/answers.jsonl",
    data_dir: str = "data/train/train/data",
    calendar_path: str = "data/train/train/calendar.csv",
    output_path: str = "train_sft.jsonl",
) -> None:
    q_file = Path(questions_path)
    a_file = Path(answers_path)
    loader = DataLoader(data_dir=data_dir, calendar_path=calendar_path)

    # Load ground-truth answers
    answers: Dict[str, Dict[str, Any]] = {}
    with open(a_file, "r", encoding="utf-8") as f:
        for line in f:
            row = json.loads(line)
            answers[row["id"]] = row

    with open(q_file, "r", encoding="utf-8") as f:
        questions = [json.loads(line) for line in f]

    print(f"📦 Đang chuẩn bị tập dữ liệu SFT từ {len(questions)} câu hỏi Train...")

    sft_records: List[Dict[str, Any]] = []
    biz_cache: Dict[str, Any] = {}

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

        # Generate schema-compressed user prompt
        user_prompt = generate_text_to_sql_prompt(
            question=q_text,
            context=ctx,
            as_of_date=as_of_date,
            business_id=biz_id,
        )

        # Build target assistant response
        if tgt_type == "unanswerable":
            assistant_content = json.dumps({
                "thought": "Câu hỏi hỏi dữ liệu không có trong hệ thống",
                "type": "unanswerable",
                "sql": None,
                "confidence": 1.0,
                "reason": "Dữ liệu của doanh nghiệp không hỗ trợ thông tin này",
            }, ensure_ascii=False)
        else:
            assistant_content = json.dumps({
                "thought": f"Truy vấn dữ liệu cho doanh nghiệp {biz_id}",
                "type": tgt_type,
                "sql": None,  # Model learns output typing and refusal contracts
                "confidence": 0.95,
                "expected_answer": tgt_val,
            }, ensure_ascii=False)

        # Standard OpenAI Chat format for SFTTrainer
        sft_records.append({
            "id": qid,
            "business_id": biz_id,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
                {"role": "assistant", "content": assistant_content},
            ],
        })

        if (idx + 1) % 500 == 0 or (idx + 1) == len(questions):
            print(f"[{idx+1}/{len(questions)}] Đã xử lý {len(sft_records)} bản ghi SFT...")

    with open(output_path, "w", encoding="utf-8") as f:
        for r in sft_records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    print(f"🎉 Hoàn thành! Đã lưu {len(sft_records)} bản ghi vào {output_path} (Dung lượng: {Path(output_path).stat().st_size / 1024 / 1024:.2f} MB)")


if __name__ == "__main__":
    prepare_dataset()
