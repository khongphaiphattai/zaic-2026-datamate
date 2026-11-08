"""
audit_train_errors.py: Full evaluation on data/train/train/questions.jsonl (3000 questions)
matching the production pipeline (order_paraphrases, monthly_growth, mtd_growth).
Outputs wrong cases to artifacts/v35b-audit/wrong.jsonl.
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from zaic_2026_datamate.data_loader import BusinessContext, DataLoader
from zaic_2026_datamate.evaluation import evaluate_match
from zaic_2026_datamate.pipeline import PipelineConfig, build_agent

logging.basicConfig(level=logging.WARNING)


def audit_train(
    questions_path: str | Path = "data/train/train/questions.jsonl",
    answers_path: str | Path = "data/train/train/answers.jsonl",
    data_dir: str | Path = "data/train/train/data",
    calendar_path: str | Path = "data/train/train/calendar.csv",
    out_dir: str | Path = "artifacts/v35b-audit",
    limit: Optional[int] = None,
):
    questions_path = Path(questions_path)
    answers_path = Path(answers_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"📖 Đọc câu hỏi từ: {questions_path}")
    questions: List[Dict[str, Any]] = []
    with open(questions_path, "r", encoding="utf-8") as f:
        for i, line in enumerate(f):
            if limit and i >= limit:
                break
            questions.append(json.loads(line))

    print(f"📖 Đọc đáp án từ: {answers_path}")
    answers: Dict[str, Dict[str, Any]] = {}
    with open(answers_path, "r", encoding="utf-8") as f:
        for line in f:
            item = json.loads(line)
            answers[item["id"]] = item

    loader = DataLoader(data_dir, calendar_path)
    business_cache: Dict[str, BusinessContext] = {}

    config = PipelineConfig(
        enable_slow_path=False,
        order_paraphrases=True,
        monthly_growth=True,
        mtd_growth=True,
    )
    agent = build_agent(config)

    print(f"🚀 BẮT ĐẦU AUDIT TRÊN {len(questions)} CÂU HỎI TRAIN...")
    print("=" * 80)

    score_correct = 0
    score_abstain = 0
    score_wrong = 0
    wrong_cases: List[Dict[str, Any]] = []
    by_business = defaultdict(lambda: {"correct": 0, "wrong": 0, "abstain": 0})

    start_time = time.time()
    for idx, q in enumerate(questions):
        qid = q["id"]
        bid = q["business_id"]
        q_text = q["question"]
        as_of_date = q.get("as_of_date")

        if bid not in business_cache:
            business_cache[bid] = loader.load_business(bid)
        ctx = business_cache[bid]

        ans_type, ans_val, reason, route = agent.answer_query(
            question=q_text,
            context=ctx,
            as_of_date=as_of_date,
        )
        target = answers.get(qid, {})
        tgt_type = target.get("type")
        tgt_val = target.get("answer")

        if ans_type == "abstain":
            score_abstain += 1
            by_business[bid]["abstain"] += 1
        else:
            is_match = evaluate_match(ans_type, ans_val, tgt_type, tgt_val)
            if is_match:
                score_correct += 1
                by_business[bid]["correct"] += 1
            else:
                score_wrong += 1
                by_business[bid]["wrong"] += 1
                wrong_cases.append({
                    "id": qid,
                    "business_id": bid,
                    "question": q_text,
                    "as_of_date": as_of_date,
                    "pred_type": ans_type,
                    "pred_val": ans_val,
                    "tgt_type": tgt_type,
                    "tgt_val": tgt_val,
                    "reason": reason,
                    "route": route,
                })

        if (idx + 1) % 500 == 0 or (idx + 1) == len(questions):
            elapsed = time.time() - start_time
            print(
                f"[{idx+1:04d}/{len(questions)}] Correct: {score_correct} | Wrong: {score_wrong} | "
                f"Abstain: {score_abstain} | Elapsed: {elapsed:.1f}s"
            )

    total = len(questions)
    net_score = score_correct - score_wrong
    pa = net_score / total if total > 0 else 0.0
    precision = score_correct / (score_correct + score_wrong) if (score_correct + score_wrong) > 0 else 0.0

    print("\n" + "=" * 80)
    print(f"📊 KẾT QUẢ TOÀN DIỆN AUDIT TRAIN ({total} CÂU):")
    print(f"   - Đúng (+1):     {score_correct:5d} ({score_correct/total*100:6.2f}%)")
    print(f"   - Sai (-1):      {score_wrong:5d} ({score_wrong/total*100:6.2f}%)")
    print(f"   - Abstain (0):   {score_abstain:5d} ({score_abstain/total*100:6.2f}%)")
    print(f"   - Precision:     {precision*100:6.2f}%")
    print(f"   - Net Score:     {net_score:5d}")
    print(f"   - Penalized Acc: {pa:7.5f}")
    print("=" * 80)

    print("\n🏢 THỐNG KÊ CHI TIẾT THEO TỪNG BUSINESS:")
    print(f"{'Business':<10} | {'Correct':<8} | {'Wrong':<8} | {'Abstain':<8} | {'Precision':<10} | {'Net Score':<10}")
    print("-" * 65)
    for bid in sorted(by_business.keys()):
        b_corr = by_business[bid]["correct"]
        b_wrg = by_business[bid]["wrong"]
        b_abs = by_business[bid]["abstain"]
        b_prec = b_corr / (b_corr + b_wrg) if (b_corr + b_wrg) > 0 else 0.0
        b_net = b_corr - b_wrg
        print(f"{bid:<10} | {b_corr:<8} | {b_wrg:<8} | {b_abs:<8} | {b_prec*100:8.2f}% | {b_net:<10}")

    wrong_file = out_dir / "wrong.jsonl"
    with open(wrong_file, "w", encoding="utf-8") as f:
        for item in wrong_cases:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")
    print(f"\n💾 Đã lưu {len(wrong_cases)} câu sai vào: {wrong_file}")

    summary_file = out_dir / "summary.json"
    summary_data = {
        "total": total,
        "correct": score_correct,
        "wrong": score_wrong,
        "abstain": score_abstain,
        "precision": precision,
        "net_score": net_score,
        "pa": pa,
        "by_business": dict(by_business),
    }
    with open(summary_file, "w", encoding="utf-8") as f:
        json.dump(summary_data, f, ensure_ascii=False, indent=2)
    print(f"💾 Đã lưu tổng kết vào: {summary_file}")


if __name__ == "__main__":
    audit_train()
