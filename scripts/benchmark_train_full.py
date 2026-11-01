"""
benchmark_train_full.py: Full or partial evaluation on data/train/train/questions.jsonl
with detailed breakdown of precision, accuracy, and error logs.
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from zaic_2026_datamate.data_loader import BusinessContext, DataLoader
from zaic_2026_datamate.intent_parser import resolve_query

logging.basicConfig(level=logging.WARNING)


def load_dataset(limit: Optional[int] = None) -> Tuple[List[Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    q_path = Path("data/train/train/questions.jsonl")
    a_path = Path("data/train/train/answers.jsonl")

    questions: List[Dict[str, Any]] = []
    with open(q_path, "r", encoding="utf-8") as f:
        for i, line in enumerate(f):
            if limit and i >= limit:
                break
            questions.append(json.loads(line))

    answers: Dict[str, Dict[str, Any]] = {}
    with open(a_path, "r", encoding="utf-8") as f:
        for line in f:
            item = json.loads(line)
            answers[item["id"]] = item

    return questions, answers


def evaluate_match(pred_type: str, pred_val: Any, target_type: str, target_val: Any) -> bool:
    """Check if predicted answer matches target under ZAIC rulebook."""
    if pred_type == "unanswerable":
        return target_type == "unanswerable"

    if target_type == "unanswerable":
        return pred_type == "unanswerable"

    if pred_val is None or target_val is None:
        return pred_val == target_val

    if target_type == "percent":
        try:
            p = float(pred_val)
            t = float(target_val)
            return abs(p - t) <= 0.02 or abs(p * 100 - t) <= 0.02
        except Exception:
            return False

    if target_type == "number":
        try:
            p = float(pred_val)
            t = float(target_val)
            if t == 0:
                return abs(p) <= 1e-4
            return abs(p - t) / abs(t) <= 0.005 or abs(p - t) <= 1.0
        except Exception:
            return False

    if target_type == "string":
        return str(pred_val).strip().lower() == str(target_val).strip().lower()

    if target_type == "list":
        if isinstance(pred_val, list) and isinstance(target_val, list):
            if len(pred_val) != len(target_val):
                return False
            for pv, tv in zip(pred_val, target_val):
                if str(pv).strip().lower() != str(tv).strip().lower():
                    return False
            return True
        return False

    return str(pred_val).strip().lower() == str(target_val).strip().lower()


def run_benchmark(limit: Optional[int] = None, show_wrong: int = 15):
    questions, answers = load_dataset(limit)
    loader = DataLoader("data/train/train/data", "data/train/train/calendar.csv")
    business_cache: Dict[str, BusinessContext] = {}

    print(f"🚀 BẮT ĐẦU BENCHMARK TRÊN {len(questions)} CÂU HỎI TRAIN...")
    print("=" * 80)

    score_correct = 0
    score_abstain = 0
    score_wrong = 0
    wrong_cases = []

    start_time = time.time()
    for idx, q in enumerate(questions):
        qid = q["id"]
        bid = q["business_id"]
        q_text = q["question"]
        as_of_date = q.get("as_of_date")

        if bid not in business_cache:
            business_cache[bid] = loader.load_business(bid)
        ctx = business_cache[bid]

        ans_type, ans_val, reason = resolve_query(q_text, ctx, as_of_date=as_of_date)
        target = answers.get(qid, {})
        tgt_type = target.get("type")
        tgt_val = target.get("answer")

        if ans_type == "abstain":
            score_abstain += 1
        else:
            is_match = evaluate_match(ans_type, ans_val, tgt_type, tgt_val)
            if is_match:
                score_correct += 1
            else:
                score_wrong += 1
                wrong_cases.append({
                    "id": qid,
                    "business_id": bid,
                    "question": q_text,
                    "as_of_date": as_of_date,
                    "pred_type": ans_type,
                    "pred_val": ans_val,
                    "tgt_type": tgt_type,
                    "tgt_val": tgt_val,
                    "reason": reason
                })

        if (idx + 1) % 500 == 0 or (idx + 1) == len(questions):
            elapsed = time.time() - start_time
            print(f"[{idx+1:04d}/{len(questions)}] Correct: {score_correct} | Wrong: {score_wrong} | Abstain: {score_abstain} | Time: {elapsed:.1f}s")

    total = len(questions)
    answered = score_correct + score_wrong
    net_score = score_correct - score_wrong
    pa = net_score / total if total > 0 else 0.0
    precision = (score_correct / answered * 100) if answered > 0 else 0.0

    print("=" * 80)
    print("📊 KẾT QUẢ BENCHMARK:")
    print(f"   - Tổng số câu hỏi: {total}")
    print(f"   - Số câu trả lời (Answered): {answered} ({answered/total*100:.2f}%)")
    print(f"   - Số câu Đúng (+1): {score_correct}")
    print(f"   - Số câu Sai (-1): {score_wrong}")
    print(f"   - Số câu Từ chối / Abstain (0): {score_abstain}")
    print(f"   - Điểm Penalized Accuracy (PA): {pa:+.5f} (Net points: {net_score:+d})")
    print(f"   - Độ chuẩn xác thực nghiệm (Precision): {precision:.2f}%")
    print("=" * 80)

    if wrong_cases and show_wrong > 0:
        print(f"\n❌ CHI TIẾT {min(len(wrong_cases), show_wrong)} TRƯỜNG HỢP SAI ĐIỂN HÌNH:")
        for i, c in enumerate(wrong_cases[:show_wrong]):
            print(f"[{i+1}] ID: {c['id']} ({c['business_id']})")
            print(f"    Q: {c['question']}")
            print(f"    As-of-date: {c['as_of_date']}")
            print(f"    Dự đoán: {c['pred_type']} -> {c['pred_val']}")
            print(f"    Thực tế: {c['tgt_type']} -> {c['tgt_val']}")
            print("-" * 60)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None, help="Limit number of questions to evaluate")
    parser.add_argument("--show-wrong", type=int, default=15, help="Number of wrong cases to print")
    args = parser.parse_args()
    run_benchmark(limit=args.limit, show_wrong=args.show_wrong)
