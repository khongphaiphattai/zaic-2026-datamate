"""
compare_submissions.py: Compare candidate v3.3 submission with baseline v3.2 submission.
Generates:
- artifacts/v33-replay/summary.json
- artifacts/v33-replay/public-diffs.json
"""

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List


def compare(
    v32_path: str = "submission.jsonl",
    v33_path: str = "artifacts/v33-replay/submission.jsonl",
    questions_path: str = "data/public_test/public_test/questions.jsonl",
    out_diff_path: str = "artifacts/v33-replay/public-diffs.json",
    out_summary_path: str = "artifacts/v33-replay/summary.json",
):
    with open(v32_path, "r", encoding="utf-8") as f:
        v32_rows = {r["id"]: r for r in (json.loads(line) for line in f if line.strip())}

    with open(v33_path, "r", encoding="utf-8") as f:
        v33_rows = {r["id"]: r for r in (json.loads(line) for line in f if line.strip())}

    questions = {}
    with open(questions_path, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                q = json.loads(line)
                questions[q["id"]] = q

    diffs: List[Dict[str, Any]] = []
    v32_answered = 0
    v33_answered = 0
    new_unlocked = 0
    value_changed = 0
    new_abstained = 0

    by_biz: Dict[str, Dict[str, int]] = {}

    for qid, q in questions.items():
        bid = q.get("business_id", "unknown")
        if bid not in by_biz:
            by_biz[bid] = {"v32_ans": 0, "v33_ans": 0, "new_ans": 0, "val_changed": 0}

        r32 = v32_rows.get(qid, {"type": "abstain"})
        r33 = v33_rows.get(qid, {"type": "abstain"})

        is_32_ans = r32.get("type") != "abstain"
        is_33_ans = r33.get("type") != "abstain"

        if is_32_ans:
            v32_answered += 1
            by_biz[bid]["v32_ans"] += 1
        if is_33_ans:
            v33_answered += 1
            by_biz[bid]["v33_ans"] += 1

        if not is_32_ans and is_33_ans:
            new_unlocked += 1
            by_biz[bid]["new_ans"] += 1
            diffs.append({
                "id": qid,
                "business_id": bid,
                "question": q["question"],
                "as_of_date": q.get("as_of_date"),
                "change_type": "new_unlocked",
                "v32": r32,
                "v33": r33,
            })
        elif is_32_ans and not is_33_ans:
            new_abstained += 1
            diffs.append({
                "id": qid,
                "business_id": bid,
                "question": q["question"],
                "as_of_date": q.get("as_of_date"),
                "change_type": "new_abstained",
                "v32": r32,
                "v33": r33,
            })
        elif is_32_ans and is_33_ans:
            if r32.get("type") != r33.get("type") or r32.get("answer") != r33.get("answer"):
                value_changed += 1
                by_biz[bid]["val_changed"] += 1
                diffs.append({
                    "id": qid,
                    "business_id": bid,
                    "question": q["question"],
                    "as_of_date": q.get("as_of_date"),
                    "change_type": "value_changed",
                    "v32": r32,
                    "v33": r33,
                })

    summary = {
        "v32_answered": v32_answered,
        "v33_answered": v33_answered,
        "new_unlocked_answers": new_unlocked,
        "value_changed_answers": value_changed,
        "new_abstained_answers": new_abstained,
        "total_diff_count": len(diffs),
        "by_business": by_biz,
    }

    Path(out_diff_path).parent.mkdir(parents=True, exist_ok=True)
    with open(out_diff_path, "w", encoding="utf-8") as f:
        json.dump(diffs, f, ensure_ascii=False, indent=2)

    with open(out_summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print("=" * 60)
    print("📊 SUBMISSION COMPARISON SUMMARY:")
    print(f"  - v3.2 Answered: {v32_answered} / 1000")
    print(f"  - v3.3 Answered: {v33_answered} / 1000")
    print(f"  - New Unlocked:  +{new_unlocked}")
    print(f"  - Value Changed: {value_changed} (Forensic bug fixes)")
    print(f"  - Total Diffs:   {len(diffs)}")
    print("=" * 60)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--v32", default="submission.jsonl")
    parser.add_argument("--v33", default="artifacts/v33-replay/submission.jsonl")
    parser.add_argument("--questions", default="data/public_test/public_test/questions.jsonl")
    parser.add_argument("--out-diff", default="artifacts/v33-replay/public-diffs.json")
    parser.add_argument("--out-summary", default="artifacts/v33-replay/summary.json")
    args = parser.parse_args()
    compare(args.v32, args.v33, args.questions, args.out_diff, args.out_summary)
