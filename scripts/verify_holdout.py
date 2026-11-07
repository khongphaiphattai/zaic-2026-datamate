"""verify_holdout.py: Fast cached benchmark on the 6 Holdout businesses for v3.4."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from zaic_2026_datamate.data_loader import DataLoader
from zaic_2026_datamate.intent_parser import resolve_query


def run_holdout_check():
    holdout_cfg = json.loads(Path("artifacts/v34-holdout/holdout.json").read_text())
    holdout_biz = set(holdout_cfg["holdout_businesses"])

    print(f"Loading holdout businesses: {sorted(holdout_biz)}...", flush=True)
    loader = DataLoader("data/train/train/data", "data/train/train/calendar.csv")
    contexts = {b: loader.load_business(b) for b in holdout_biz}
    print("Pre-loaded all 6 contexts successfully!", flush=True)

    questions = [
        json.loads(l)
        for l in open("data/train/train/questions.jsonl")
        if json.loads(l)["business_id"] in holdout_biz
    ]
    answers = {json.loads(l)["id"]: json.loads(l) for l in open("data/train/train/answers.jsonl")}

    correct, wrong, abstain = 0, 0, 0
    wrong_cases = []

    for i, q in enumerate(questions, 1):
        ctx = contexts[q["business_id"]]
        pred_type, pred_val, _ = resolve_query(q["question"], ctx, q.get("as_of_date"))
        gt = answers[q["id"]]
        gt_type = gt["type"]
        gt_ans = gt.get("answer")

        if pred_type == "abstain":
            abstain += 1
        elif pred_type == gt_type:
            if pred_type in ("unanswerable", "abstain"):
                correct += 1
            elif isinstance(gt_ans, (int, float)) and isinstance(pred_val, (int, float)):
                try:
                    if abs(float(pred_val) - float(gt_ans)) < 1e-4:
                        correct += 1
                    else:
                        wrong += 1
                        wrong_cases.append((q, pred_type, pred_val, gt_ans))
                except Exception:
                    wrong += 1
                    wrong_cases.append((q, pred_type, pred_val, gt_ans))
            elif pred_val == gt_ans:
                correct += 1
            else:
                wrong += 1
                wrong_cases.append((q, pred_type, pred_val, gt_ans))
        else:
            wrong += 1
            wrong_cases.append((q, pred_type, pred_val, gt_ans))

        if i % 100 == 0 or i == len(questions):
            print(f"[{i}/{len(questions)}] Correct: {correct} | Wrong: {wrong} | Abstain: {abstain}", flush=True)

    print("\n" + "=" * 60, flush=True)
    print(f"📊 HOLDOUT EVALUATION ({len(questions)} questions across 6 businesses):", flush=True)
    print(f"   - Correct (+1): {correct}", flush=True)
    print(f"   - Wrong (-1): {wrong}", flush=True)
    print(f"   - Abstain (0): {abstain}", flush=True)
    total_answered = correct + wrong
    prec = (correct / total_answered * 100) if total_answered > 0 else 0.0
    pa = (correct - wrong) / len(questions)
    print(f"   - Precision: {prec:.2f}%", flush=True)
    print(f"   - Penalized Accuracy: {pa:.5f}", flush=True)
    print("=" * 60, flush=True)

    if wrong_cases:
        print("\n❌ WRONG CASES ON HOLDOUT:", flush=True)
        for q, p_type, p_val, gt_val in wrong_cases[:10]:
            print(f"[{q['business_id']}] {q['question']}")
            print(f"   Pred: {p_type} -> {p_val} | GroundTruth: {gt_val}\n")


if __name__ == "__main__":
    run_holdout_check()
