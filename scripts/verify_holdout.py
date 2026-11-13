"""verify_holdout.py: Production-parity benchmark on the 6 Holdout businesses."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from zaic_2026_datamate.data_loader import DataLoader
from zaic_2026_datamate.evaluation import evaluate_match
from zaic_2026_datamate.pipeline import build_agent, production_pipeline_config


def run_holdout_check(max_wrong: int = 0):
    holdout_cfg = json.loads(Path("artifacts/v34-holdout/holdout.json").read_text())
    holdout_biz = set(holdout_cfg["holdout_businesses"])

    print(f"Loading holdout businesses: {sorted(holdout_biz)}...", flush=True)
    loader = DataLoader("data/train/train/data", "data/train/train/calendar.csv")
    contexts = {b: loader.load_business(b) for b in holdout_biz}
    print("Pre-loaded all 6 contexts successfully!", flush=True)

    with open("data/train/train/questions.jsonl", encoding="utf-8") as qf:
        questions = [q for line in qf if (q := json.loads(line))["business_id"] in holdout_biz]
    with open("data/train/train/answers.jsonl", encoding="utf-8") as af:
        answers = {a["id"]: a for line in af if (a := json.loads(line))}

    agent = build_agent(production_pipeline_config())

    correct, wrong, abstain = 0, 0, 0
    wrong_cases = []

    for i, q in enumerate(questions, 1):
        ctx = contexts[q["business_id"]]
        pred_type, pred_val, _, _ = agent.answer_query(q["question"], ctx, q.get("as_of_date"))
        gt = answers[q["id"]]
        gt_type = gt["type"]
        gt_ans = gt.get("answer")

        if pred_type == "abstain":
            abstain += 1
        elif evaluate_match(pred_type, pred_val, gt_type, gt_ans):
            correct += 1
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

    if wrong > max_wrong:
        sys.exit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Verify zero holdout regressions under production config")
    parser.add_argument("--max-wrong", type=int, default=0, help="Maximum allowed wrong answers before exit(1)")
    args = parser.parse_args()
    run_holdout_check(max_wrong=args.max_wrong)
