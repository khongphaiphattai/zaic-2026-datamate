"""
audit_train_errors.py: Multi-process evaluation on data/train/train/questions.jsonl (3000 questions)
matching the production pipeline (order_paraphrases, monthly_growth, mtd_growth).
Includes built-in Gate 2 (Holdout 710 Qs) summary and --businesses slice filtering.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any

from zaic_2026_datamate.data_loader import BusinessContext, DataLoader
from zaic_2026_datamate.evaluation import evaluate_match
from zaic_2026_datamate.pipeline import build_agent, production_pipeline_config

logging.basicConfig(level=logging.WARNING)


def _business_dir_weight(data_dir: Path, bid: str) -> int:
    """Estimate business load weight (LPT scheduling: weight Excel workbooks 25x higher than SQLite/CSV)."""
    bdir = data_dir / bid
    if not bdir.is_dir():
        return 0
    total = 0
    for f in bdir.iterdir():
        if not f.is_file():
            continue
        sz = f.stat().st_size
        if f.suffix.lower() == ".xlsx":
            total += sz * 25
        else:
            total += sz
    return total


def _evaluate_business_worker(
    task: tuple[
        str,
        list[tuple[int, dict[str, Any]]],
        dict[str, dict[str, Any]],
        str,
        str,
        bool,
    ],
) -> tuple[
    str,
    dict[str, int],
    list[tuple[int, dict[str, Any]]],
    list[tuple[int, dict[str, Any]]],
    float,
]:
    """Worker function to load and evaluate all questions for a single business_id."""
    (
        bid,
        indexed_questions,
        answers_subset,
        data_dir_str,
        calendar_path_str,
        save_abstains,
    ) = task
    t0 = time.time()
    loader = DataLoader(data_dir_str, calendar_path_str)
    ctx: BusinessContext = loader.load_business(bid)
    agent = build_agent(production_pipeline_config())

    b_stats = {"correct": 0, "wrong": 0, "abstain": 0}
    wrong_cases: list[tuple[int, dict[str, Any]]] = []
    abstain_cases: list[tuple[int, dict[str, Any]]] = []

    for idx, q in indexed_questions:
        qid = q["id"]
        q_text = q["question"]
        as_of_date = q.get("as_of_date")
        ctx.reset_trace()

        ans_type, ans_val, reason, route = agent.answer_query(
            question=q_text,
            context=ctx,
            as_of_date=as_of_date,
        )
        target = answers_subset.get(qid, {})
        tgt_type = target.get("type")
        tgt_val = target.get("answer")

        if ans_type == "abstain":
            b_stats["abstain"] += 1
            if save_abstains:
                abstain_cases.append(
                    (
                        idx,
                        {
                            "id": qid,
                            "business_id": bid,
                            "question": q_text,
                            "as_of_date": as_of_date,
                            "tgt_type": tgt_type,
                            "tgt_val": tgt_val,
                            "reason": reason,
                            "route": route,
                        },
                    )
                )
        else:
            is_match = evaluate_match(ans_type, ans_val, tgt_type, tgt_val)
            if is_match:
                b_stats["correct"] += 1
            else:
                b_stats["wrong"] += 1
                wrong_cases.append(
                    (
                        idx,
                        {
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
                        },
                    )
                )

    elapsed = time.time() - t0
    return bid, b_stats, wrong_cases, abstain_cases, elapsed


def audit_train(
    questions_path: str | Path = "data/train/train/questions.jsonl",
    answers_path: str | Path = "data/train/train/answers.jsonl",
    data_dir: str | Path = "data/train/train/data",
    calendar_path: str | Path = "data/train/train/calendar.csv",
    out_dir: str | Path = "artifacts/v35b-audit",
    limit: int | None = None,
    max_wrong: int | None = None,
    save_abstains: bool = False,
    workers: int = 8,
    businesses: list[str] | None = None,
    holdout_config_path: str | Path = "artifacts/v34-holdout/holdout.json",
) -> dict[str, Any]:
    questions_path = Path(questions_path)
    answers_path = Path(answers_path)
    data_dir_path = Path(data_dir)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    biz_filter = set(businesses) if businesses else None

    print(f"📖 Đọc câu hỏi từ: {questions_path}")
    questions: list[dict[str, Any]] = []
    with open(questions_path, encoding="utf-8") as f:
        for i, line in enumerate(f):
            if limit and i >= limit:
                break
            q = json.loads(line)
            if biz_filter and q["business_id"] not in biz_filter:
                continue
            questions.append(q)

    print(f"📖 Đọc đáp án từ: {answers_path}")
    answers: dict[str, dict[str, Any]] = {}
    with open(answers_path, encoding="utf-8") as f:
        for line in f:
            item = json.loads(line)
            answers[item["id"]] = item

    questions_by_biz: dict[str, list[tuple[int, dict[str, Any]]]] = defaultdict(list)
    for idx, q in enumerate(questions):
        questions_by_biz[q["business_id"]].append((idx, q))

    # Schedule largest businesses first (Longest Processing Time first) to minimize tail latency
    sorted_bids = sorted(
        questions_by_biz.keys(),
        key=lambda b: (-_business_dir_weight(data_dir_path, b), b),
    )

    tasks = []
    for bid in sorted_bids:
        b_qs = questions_by_biz[bid]
        b_ans = {q["id"]: answers[q["id"]] for _, q in b_qs if q["id"] in answers}
        tasks.append(
            (
                bid,
                b_qs,
                b_ans,
                str(data_dir_path),
                str(calendar_path),
                save_abstains,
            )
        )

    eff_workers = max(1, min(workers, len(tasks)))
    print(
        f"🚀 BẮT ĐẦU AUDIT TRÊN {len(questions)} CÂU HỎI "
        f"({len(tasks)} businesses, workers={eff_workers})..."
    )
    print("=" * 80)

    start_time = time.time()
    by_business: dict[str, dict[str, int]] = {}
    all_wrong_indexed: list[tuple[int, dict[str, Any]]] = []
    all_abstain_indexed: list[tuple[int, dict[str, Any]]] = []

    if eff_workers == 1:
        for done_cnt, task in enumerate(tasks, 1):
            bid, b_stats, w_cases, a_cases, b_elapsed = _evaluate_business_worker(task)
            by_business[bid] = b_stats
            all_wrong_indexed.extend(w_cases)
            all_abstain_indexed.extend(a_cases)
            elapsed = time.time() - start_time
            print(
                f"[{done_cnt:02d}/{len(tasks):02d}] {bid}: "
                f"C={b_stats['correct']} W={b_stats['wrong']} A={b_stats['abstain']} "
                f"({b_elapsed:.1f}s | total {elapsed:.1f}s)",
                flush=True,
            )
    else:
        with ProcessPoolExecutor(max_workers=eff_workers) as executor:
            fut_to_bid = {
                executor.submit(_evaluate_business_worker, task): task[0]
                for task in tasks
            }
            done_cnt = 0
            for fut in as_completed(fut_to_bid):
                done_cnt += 1
                bid, b_stats, w_cases, a_cases, b_elapsed = fut.result()
                by_business[bid] = b_stats
                all_wrong_indexed.extend(w_cases)
                all_abstain_indexed.extend(a_cases)
                elapsed = time.time() - start_time
                print(
                    f"[{done_cnt:02d}/{len(tasks):02d}] {bid}: "
                    f"C={b_stats['correct']} W={b_stats['wrong']} A={b_stats['abstain']} "
                    f"({b_elapsed:.1f}s | wall {elapsed:.1f}s)",
                    flush=True,
                )

    all_wrong_indexed.sort(key=lambda x: x[0])
    all_abstain_indexed.sort(key=lambda x: x[0])
    wrong_cases = [item for _, item in all_wrong_indexed]
    abstain_cases = [item for _, item in all_abstain_indexed]

    score_correct = sum(b["correct"] for b in by_business.values())
    score_wrong = sum(b["wrong"] for b in by_business.values())
    score_abstain = sum(b["abstain"] for b in by_business.values())

    total = len(questions)
    net_score = score_correct - score_wrong
    pa = net_score / total if total > 0 else 0.0
    precision = (
        score_correct / (score_correct + score_wrong)
        if (score_correct + score_wrong) > 0
        else 0.0
    )
    total_wall = time.time() - start_time

    print("\n" + "=" * 80)
    print(f"📊 KẾT QUẢ TOÀN DIỆN AUDIT TRAIN ({total} CÂU — {total_wall:.1f}s):")
    print(
        f"   - Đúng (+1):     {score_correct:5d} ({score_correct / total * 100 if total else 0:6.2f}%)"
    )
    print(
        f"   - Sai (-1):      {score_wrong:5d} ({score_wrong / total * 100 if total else 0:6.2f}%)"
    )
    print(
        f"   - Abstain (0):   {score_abstain:5d} ({score_abstain / total * 100 if total else 0:6.2f}%)"
    )
    print(f"   - Precision:     {precision * 100:6.2f}%")
    print(f"   - Net Score:     {net_score:5d}")
    print(f"   - Penalized Acc: {pa:7.5f}")
    print("=" * 80)

    holdout_summary: dict[str, Any] = {}
    holdout_path = Path(holdout_config_path)
    if holdout_path.is_file():
        holdout_cfg = json.loads(holdout_path.read_text(encoding="utf-8"))
        holdout_bids = [
            b for b in holdout_cfg.get("holdout_businesses", []) if b in by_business
        ]
        if holdout_bids:
            h_corr = sum(by_business[b]["correct"] for b in holdout_bids)
            h_wrg = sum(by_business[b]["wrong"] for b in holdout_bids)
            h_abs = sum(by_business[b]["abstain"] for b in holdout_bids)
            h_tot = h_corr + h_wrg + h_abs
            h_prec = h_corr / (h_corr + h_wrg) if (h_corr + h_wrg) > 0 else 0.0
            h_pa = (h_corr - h_wrg) / h_tot if h_tot > 0 else 0.0
            holdout_summary = {
                "businesses": holdout_bids,
                "total": h_tot,
                "correct": h_corr,
                "wrong": h_wrg,
                "abstain": h_abs,
                "precision": h_prec,
                "net_score": h_corr - h_wrg,
                "pa": h_pa,
            }
            print(
                f"\n🛡️ KẾT QUẢ GATE 2 — HOLDOUT ({h_tot} CÂU / {len(holdout_bids)} BUSINESSES):"
            )
            print(
                f"   - Correct (+1): {h_corr} | Wrong (-1): {h_wrg} | Abstain (0): {h_abs}"
            )
            print(f"   - Precision:    {h_prec * 100:.2f}% | Penalized Acc: {h_pa:.5f}")
            print("=" * 80)

    print("\n🏢 THỐNG KÊ CHI TIẾT THEO TỪNG BUSINESS:")
    print(
        f"{'Business':<10} | {'Correct':<8} | {'Wrong':<8} | {'Abstain':<8} | {'Precision':<10} | {'Net Score':<10}"
    )
    print("-" * 65)
    ordered_by_business: dict[str, dict[str, int]] = {}
    for bid in sorted(by_business.keys()):
        ordered_by_business[bid] = by_business[bid]
        b_corr = by_business[bid]["correct"]
        b_wrg = by_business[bid]["wrong"]
        b_abs = by_business[bid]["abstain"]
        b_prec = b_corr / (b_corr + b_wrg) if (b_corr + b_wrg) > 0 else 0.0
        b_net = b_corr - b_wrg
        print(
            f"{bid:<10} | {b_corr:<8} | {b_wrg:<8} | {b_abs:<8} | {b_prec * 100:8.2f}% | {b_net:<10}"
        )

    wrong_file = out_dir / "wrong.jsonl"
    with open(wrong_file, "w", encoding="utf-8") as f:
        for item in wrong_cases:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")
    print(f"\n💾 Đã lưu {len(wrong_cases)} câu sai vào: {wrong_file}")

    if save_abstains:
        abstain_file = out_dir / "abstain.jsonl"
        with open(abstain_file, "w", encoding="utf-8") as f:
            for item in abstain_cases:
                f.write(json.dumps(item, ensure_ascii=False) + "\n")
        print(f"💾 Đã lưu {len(abstain_cases)} câu abstain vào: {abstain_file}")

    summary_file = out_dir / "summary.json"
    summary_data = {
        "total": total,
        "correct": score_correct,
        "wrong": score_wrong,
        "abstain": score_abstain,
        "precision": precision,
        "net_score": net_score,
        "pa": pa,
        "elapsed_seconds": round(total_wall, 2),
        "holdout": holdout_summary,
        "by_business": ordered_by_business,
    }
    with open(summary_file, "w", encoding="utf-8") as f:
        json.dump(summary_data, f, ensure_ascii=False, indent=2)
    print(f"💾 Đã lưu tổng kết vào: {summary_file}")
    if max_wrong is not None and score_wrong > max_wrong:
        print(
            f"❌ Audit gate failed: score_wrong={score_wrong} > max_wrong={max_wrong}"
        )
        sys.exit(1)
    return summary_data


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Audit train questions against ground truth (multi-process + Gate 2 Holdout)"
    )
    parser.add_argument(
        "--limit", type=int, default=None, help="Limit number of questions"
    )
    parser.add_argument(
        "--out-dir", type=str, default="artifacts/v35b-audit", help="Output directory"
    )
    parser.add_argument(
        "--max-wrong",
        type=int,
        default=None,
        help="Fail with exit(1) when score_wrong > max_wrong",
    )
    parser.add_argument(
        "--save-abstains",
        action="store_true",
        help="Also write abstain.jsonl for P1 twin profiling",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=8,
        help="Number of parallel worker processes across businesses (default: 8)",
    )
    parser.add_argument(
        "--businesses",
        type=str,
        default=None,
        help="Comma-separated list of business_ids for fast slice check (e.g. biz_117,biz_118)",
    )
    args = parser.parse_args()
    biz_list = (
        [b.strip() for b in args.businesses.split(",") if b.strip()]
        if args.businesses
        else None
    )
    audit_train(
        limit=args.limit,
        out_dir=args.out_dir,
        max_wrong=args.max_wrong,
        save_abstains=args.save_abstains,
        workers=args.workers,
        businesses=biz_list,
    )
