"""
benchmark_lbo.py: Leave-Business-Out (LBO) Cross-Validation Benchmark.
Reports a fixed resolver by business group; no fold-specific training or tuning isolation.
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from zaic_2026_datamate.data_loader import BusinessContext, DataLoader
from zaic_2026_datamate.intent_parser import resolve_query

logging.basicConfig(level=logging.WARNING)


from zaic_2026_datamate.evaluation import evaluate_match


def run_lbo(n_folds: int = 5, verbose: bool = False) -> None:
    q_path = Path("data/train/train/questions.jsonl")
    a_path = Path("data/train/train/answers.jsonl")
    data_dir = Path("data/train/train/data")
    calendar_path = Path("data/train/train/calendar.csv")

    with open(q_path, "r", encoding="utf-8") as f:
        questions = [json.loads(line) for line in f]

    answers: Dict[str, Dict[str, Any]] = {}
    with open(a_path, "r", encoding="utf-8") as f:
        for line in f:
            item = json.loads(line)
            answers[item["id"]] = item

    # Group questions by business_id
    biz_to_questions: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for q in questions:
        biz_to_questions[q["business_id"]].append(q)

    all_bids = sorted(biz_to_questions.keys())
    print(f"📊 TỔNG SỐ DOANH NGHIỆP TRAIN: {len(all_bids)} ({len(questions)} câu hỏi)")

    # Partition businesses into N folds
    folds: List[List[str]] = [[] for _ in range(n_folds)]
    for idx, bid in enumerate(all_bids):
        folds[idx % n_folds].append(bid)

    loader = DataLoader(data_dir, calendar_path)
    ctx_cache: Dict[str, BusinessContext] = {}

    print(f"🚀 BẮT ĐẦU BUSINESS-GROUPED TRAIN EVALUATION ({n_folds} FOLDS)")
    print("=" * 80)

    fold_results = []
    overall_correct = 0
    overall_wrong = 0
    overall_abstain = 0
    overall_total = 0

    all_wrong_cases = []

    for fold_idx, val_bids in enumerate(folds):
        print(f"\n📁 FOLD {fold_idx + 1}/{n_folds} — Holdout businesses: {', '.join(val_bids)}")
        fold_correct = 0
        fold_wrong = 0
        fold_abstain = 0
        fold_total = 0

        t0 = time.time()
        for bid in val_bids:
            if bid not in ctx_cache:
                ctx_cache[bid] = loader.load_business(bid)
            ctx = ctx_cache[bid]

            for q in biz_to_questions[bid]:
                fold_total += 1
                qid = q["id"]
                target = answers.get(qid, {})
                tgt_type = target.get("type")
                tgt_val = target.get("answer")

                ans_type, ans_val, reason = resolve_query(
                    q["question"], ctx, as_of_date=q.get("as_of_date")
                )

                if ans_type == "abstain":
                    fold_abstain += 1
                else:
                    if evaluate_match(ans_type, ans_val, tgt_type, tgt_val):
                        fold_correct += 1
                    else:
                        fold_wrong += 1
                        all_wrong_cases.append({
                            "fold": fold_idx + 1,
                            "id": qid,
                            "business_id": bid,
                            "question": q["question"],
                            "as_of_date": q.get("as_of_date"),
                            "pred": (ans_type, ans_val),
                            "target": (tgt_type, tgt_val),
                        })

        elapsed = time.time() - t0
        fold_answered = fold_correct + fold_wrong
        fold_net = fold_correct - fold_wrong
        fold_pa = fold_net / fold_total if fold_total > 0 else 0.0
        fold_prec = (fold_correct / fold_answered * 100) if fold_answered > 0 else 0.0

        fold_results.append({
            "fold": fold_idx + 1,
            "bids": val_bids,
            "total": fold_total,
            "answered": fold_answered,
            "correct": fold_correct,
            "wrong": fold_wrong,
            "abstain": fold_abstain,
            "net": fold_net,
            "pa": fold_pa,
            "precision": fold_prec,
            "time": elapsed,
        })

        print(f"   -> Answered: {fold_answered}/{fold_total} ({fold_answered/fold_total*100:.1f}%) | "
              f"Correct: {fold_correct} | Wrong: {fold_wrong} | Abstain: {fold_abstain} | "
              f"PA: {fold_pa:+.5f} | Precision: {fold_prec:.2f}% | Elapsed: {elapsed:.1f}s")

        overall_correct += fold_correct
        overall_wrong += fold_wrong
        overall_abstain += fold_abstain
        overall_total += fold_total

    overall_answered = overall_correct + overall_wrong
    overall_net = overall_correct - overall_wrong
    overall_pa = overall_net / overall_total if overall_total > 0 else 0.0
    overall_prec = (overall_correct / overall_answered * 100) if overall_answered > 0 else 0.0

    print("\n" + "=" * 80)
    print("🏆 TỔNG KẾT TRAIN THEO NHÓM DOANH NGHIỆP:")
    print(f"   - Tổng số câu hỏi: {overall_total}")
    print(f"   - Số câu trả lời: {overall_answered} ({overall_answered/overall_total*100:.2f}%)")
    print(f"   - Đúng (+1): {overall_correct} | Sai (-1): {overall_wrong} | Abstain (0): {overall_abstain}")
    print(f"   - Điểm PA Tổng thể: {overall_pa:+.5f} (Net points: {overall_net:+d})")
    print(f"   - Precision trên Train: {overall_prec:.2f}%")
    print("   - Chi tiết từng Fold:")
    for fr in fold_results:
        print(f"     * Fold {fr['fold']} ({', '.join(fr['bids'])}): PA={fr['pa']:+.4f}, Precision={fr['precision']:.1f}% ({fr['correct']}/{fr['answered']})")
    print("=" * 80)

    if all_wrong_cases:
        print(f"\n❌ TỔNG SỐ {len(all_wrong_cases)} CÂU SAI PHÁT HIỆN TRÊN TRAIN:")
        for i, c in enumerate(all_wrong_cases):
            print(f"[{i+1}] [{c['business_id']}] {c['id']}: {c['question']}")
            print(f"    As-of-date: {c['as_of_date']}")
            print(f"    Dự đoán: {c['pred']}  <--->  Đáp án BTC: {c['target']}")
            print("-" * 60)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--folds", type=int, default=5, help="Number of folds for LBO (default: 5)")
    args = parser.parse_args()
    run_lbo(n_folds=args.folds)
