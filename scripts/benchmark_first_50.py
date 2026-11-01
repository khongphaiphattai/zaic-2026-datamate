"""
benchmark_first_50.py: Benchmark the DataMate pipeline on the first 50 questions
of data/train/train/questions.jsonl.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from zaic_2026_datamate.data_loader import BusinessContext, DataLoader
from zaic_2026_datamate.intent_parser import resolve_query

logging.basicConfig(level=logging.WARNING)


def load_dataset(n: int = 50) -> Tuple[List[Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    q_path = Path("data/train/train/questions.jsonl")
    a_path = Path("data/train/train/answers.jsonl")

    questions: List[Dict[str, Any]] = []
    with open(q_path, "r", encoding="utf-8") as f:
        for i, line in enumerate(f):
            if i >= n:
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


def main():
    questions, answers = load_dataset(50)
    loader = DataLoader("data/train/train/data", "data/train/train/calendar.csv")

    business_cache: Dict[str, BusinessContext] = {}

    print(f"🚀 BẮT ĐẦU BENCHMARK TRÊN {len(questions)} CÂU HỎI ĐẦU TIÊN...")
    print("=" * 80)

    score_correct = 0
    score_abstain = 0
    score_wrong = 0

    results = []
    total_start_time = time.time()

    for idx, q in enumerate(questions):
        qid = q["id"]
        biz_id = q["business_id"]
        question_text = q["question"]
        as_of_date = q["as_of_date"]
        target_info = answers.get(qid, {})
        target_val = target_info.get("answer")
        target_type = target_info.get("type", "number")

        # Load business context with cache
        t0 = time.time()
        if biz_id not in business_cache:
            business_cache[biz_id] = loader.load_business(biz_id)
        ctx = business_cache[biz_id]
        load_time = time.time() - t0

        # Query Resolution via Intent Parser
        t_query = time.time()
        pred_type, pred_val, reason = resolve_query(question_text, ctx, as_of_date=as_of_date)
        query_time = time.time() - t_query

        # Evaluate Penalized Accuracy
        if pred_type == "abstain":
            status = "⚪ ABSTAIN"
            score_abstain += 1
            points = 0
        else:
            is_correct = evaluate_match(pred_type, pred_val, target_type, target_val)
            if is_correct:
                status = "✅ CORRECT"
                score_correct += 1
                points = 1
            else:
                status = "❌ WRONG"
                score_wrong += 1
                points = -1

        print(
            f"[{idx + 1:02d}/50] {status} ({points:+d}đ) | Biz: {biz_id} | Type: {target_type} | Time: {query_time * 1000:.1f}ms\n"
            f"   Q: {question_text[:70]}...\n"
            f"   Target: {target_val} | Pred ({pred_type}): {pred_val} {f'(Lý do: {reason})' if reason and pred_type == 'abstain' else ''}"
        )

        results.append({
            "idx": idx + 1,
            "id": qid,
            "biz_id": biz_id,
            "status": status,
            "points": points,
            "pred_type": pred_type,
            "pred_val": pred_val,
            "target_type": target_type,
            "target_val": target_val,
            "time_ms": query_time * 1000,
        })

    total_time = time.time() - total_start_time
    total_q = len(questions)
    net_points = score_correct - score_wrong
    pa = net_points / total_q

    print("\n" + "=" * 80)
    print("📊 TỔNG KẾT BENCHMARK 50 CÂU HỎI ĐẦU TIÊN (ZAIC 2026 DATAMATE)")
    print("=" * 80)
    print(f"Tổng số câu hỏi:    {total_q}")
    print(f"✅ Đúng (+1):         {score_correct} ({score_correct / total_q * 100:.1f}%)")
    print(f"⚪ Từ chối an toàn:   {score_abstain} ({score_abstain / total_q * 100:.1f}%) [0 điểm - không bị phạt]")
    print(f"❌ Sai / Ảo giác:     {score_wrong} ({score_wrong / total_q * 100:.1f}%) [-1 điểm]")
    print(f"🎯 Điểm số thuần:     {net_points:+d} / {total_q}")
    print(f"🏆 Penalized Accuracy (PA): {pa:.4f} ({pa * 100:.2f}%)")
    print(f"⏱️ Tổng thời gian:     {total_time:.2f}s (Trung bình: {total_time / total_q * 1000:.1f}ms/câu)")
    print("=" * 80)


if __name__ == "__main__":
    main()
