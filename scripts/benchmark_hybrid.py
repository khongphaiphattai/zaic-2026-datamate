"""
benchmark_hybrid.py: Evaluates the Hybrid Fast-Slow Architecture on Train or Public Test.

Measures:
1. Fast Path contribution (answers, accuracy, latency)
2. Slow Path contribution (answers, accuracy, SQL execution success, net gain)
3. Penalized Accuracy (PA) comparison before and after Slow Path.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from zaic_2026_datamate.data_loader import BusinessContext, DataLoader
from zaic_2026_datamate.hybrid_agent import HybridAgent
from zaic_2026_datamate.llm_client import MockLLMClient, OpenAICompatibleClient

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


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
            p = int(round(float(pred_val)))
            t = int(round(float(target_val)))
            return p == t
        except Exception:
            return False

    return str(pred_val).strip().lower() == str(target_val).strip().lower()


def run_benchmark(
    questions_path: str = "data/train/train/questions.jsonl",
    answers_path: Optional[str] = "data/train/train/answers.jsonl",
    data_dir: str = "data/train/train/data",
    calendar_path: str = "data/train/train/calendar.csv",
    enable_slow_path: bool = False,
    base_url: Optional[str] = None,
    model: Optional[str] = None,
    limit: Optional[int] = None,
    business_filter: Optional[List[str]] = None,
) -> None:
    q_file = Path(questions_path)
    a_file = Path(answers_path) if answers_path else None
    loader = DataLoader(data_dir=data_dir, calendar_path=calendar_path)

    with open(q_file, "r", encoding="utf-8") as f:
        questions = [json.loads(line) for line in f]

    answers: Dict[str, Dict[str, Any]] = {}
    if a_file and a_file.exists():
        with open(a_file, "r", encoding="utf-8") as f:
            for line in f:
                row = json.loads(line)
                answers[row["id"]] = row

    if business_filter:
        questions = [q for q in questions if q.get("business_id") in business_filter]

    if limit:
        questions = questions[:limit]

    # Initialize LLM client if slow path enabled
    llm_client = None
    if enable_slow_path:
        llm_client = OpenAICompatibleClient(base_url=base_url, model=model)

    agent = HybridAgent(enable_slow_path=enable_slow_path, llm_client=llm_client)

    print(f"🚀 BẮT ĐẦU BENCHMARK HYBRID: {len(questions)} câu hỏi", flush=True)
    print(f"   - Enable Slow Path: {enable_slow_path}", flush=True)
    if enable_slow_path:
        print(f"   - LLM Endpoint: {llm_client.base_url} (Model: {llm_client.model})", flush=True)
    print("=" * 70, flush=True)

    stats = {
        "total": len(questions),
        "fast_path": {"answered": 0, "correct": 0, "wrong": 0},
        "slow_path": {"answered": 0, "correct": 0, "wrong": 0},
        "abstain": 0,
        "unanswerable": 0,
    }

    business_cache: Dict[str, BusinessContext] = {}
    t0 = time.time()

    for idx, q in enumerate(questions):
        qid = q["id"]
        biz_id = q["business_id"]
        q_text = q["question"]
        as_of_date = q.get("as_of_date")

        if biz_id not in business_cache:
            business_cache[biz_id] = loader.load_business(biz_id)
        ctx = business_cache[biz_id]

        ans_type, ans_val, reason, route = agent.answer_query(
            question=q_text,
            context=ctx,
            as_of_date=as_of_date,
        )

        has_ground_truth = qid in answers
        target = answers[qid] if has_ground_truth else {}
        target_type = target.get("type")
        target_val = target.get("answer") if "answer" in target else target.get("result")

        if route == "fast_path":
            stats["fast_path"]["answered"] += 1
            if has_ground_truth:
                is_correct = evaluate_match(ans_type, ans_val, target_type, target_val)
                if is_correct:
                    stats["fast_path"]["correct"] += 1
                else:
                    stats["fast_path"]["wrong"] += 1

        elif route == "slow_path":
            stats["slow_path"]["answered"] += 1
            if has_ground_truth:
                is_correct = evaluate_match(ans_type, ans_val, target_type, target_val)
                if is_correct:
                    stats["slow_path"]["correct"] += 1
                else:
                    stats["slow_path"]["wrong"] += 1

        elif route == "abstain":
            stats["abstain"] += 1

        if (idx + 1) % 10 == 0 or (idx + 1) == len(questions):
            print(f"[{idx+1}/{len(questions)}] Fast={stats['fast_path']['answered']}, Slow={stats['slow_path']['answered']}, Abstain={stats['abstain']}", flush=True)

    elapsed = time.time() - t0
    print("\n" + "=" * 70)
    print("🏁 KẾT QUẢ BENCHMARK HYBRID:")
    print(f"⏱️ Tổng thời gian: {elapsed:.2f}s (Trung bình: {elapsed/max(len(questions),1):.3f}s/câu)")
    print(f"⚡ Fast Path: {stats['fast_path']['answered']} câu trả lời")
    if a_file:
        c_f = stats["fast_path"]["correct"]
        w_f = stats["fast_path"]["wrong"]
        prec_f = (c_f / max(c_f + w_f, 1)) * 100
        print(f"   -> Đúng: {c_f}, Sai: {w_f}, Precision: {prec_f:.2f}%")

    print(f"🧠 Slow Path: {stats['slow_path']['answered']} câu trả lời")
    if a_file:
        c_s = stats["slow_path"]["correct"]
        w_s = stats["slow_path"]["wrong"]
        prec_s = (c_s / max(c_s + w_s, 1)) * 100
        print(f"   -> Đúng: {c_s}, Sai: {w_s}, Precision: {prec_s:.2f}%")

    print(f"🛡️ Abstain an toàn: {stats['abstain']} câu")

    if a_file:
        total_correct = stats["fast_path"]["correct"] + stats["slow_path"]["correct"]
        total_wrong = stats["fast_path"]["wrong"] + stats["slow_path"]["wrong"]
        net_points = total_correct - total_wrong
        pa = net_points / len(questions)
        print(f"\n🎯 TỔNG ĐIỂM PENALIZED ACCURACY (PA): {pa:+.5f} (Điểm thuần: {net_points:+d} / {len(questions)})")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--questions", default="data/train/train/questions.jsonl")
    parser.add_argument("--answers", default="data/train/train/answers.jsonl")
    parser.add_argument("--enable-slow-path", action="store_true")
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--model", default="Qwen/Qwen2.5-Coder-7B-Instruct")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    run_benchmark(
        questions_path=args.questions,
        answers_path=args.answers,
        enable_slow_path=args.enable_slow_path,
        base_url=args.base_url,
        model=args.model,
        limit=args.limit,
    )
