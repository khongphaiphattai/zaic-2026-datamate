"""triage_abstain.py: Diagnose exact reasons why questions abstained in v3.3.

Analyzes the 527 abstained questions from submission.jsonl against
data/public_test/public_test/questions.jsonl using BusinessSemanticContract and build_query_plan.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

from zaic_2026_datamate.data_loader import DataLoader
from zaic_2026_datamate.semantic_contract import (
    BusinessSemanticContract,
    build_query_plan,
    _check_missing_capability,
)


def run_triage():
    sub_path = Path("submission.jsonl")
    questions_path = Path("data/public_test/public_test/questions.jsonl")
    data_dir = Path("data/public_test/public_test/data")
    calendar_path = Path("data/public_test/public_test/calendar.csv")

    sub_rows = [json.loads(line) for line in sub_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    q_rows = {
        json.loads(line)["id"]: json.loads(line)
        for line in questions_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }

    abstained_ids = [row["id"] for row in sub_rows if row.get("type") == "abstain"]
    print(f"Total abstained questions in submission.jsonl: {len(abstained_ids)}")

    loader = DataLoader(data_dir=data_dir, calendar_path=calendar_path)

    # Cache business contexts and contracts
    contexts = {}
    contracts = {}

    failure_reasons = Counter()
    plan_operations = Counter()
    missing_capabilities = Counter()
    by_biz = defaultdict(Counter)
    samples_by_reason = defaultdict(list)

    for qid in abstained_ids:
        q_item = q_rows[qid]
        biz_id = q_item["business_id"]
        question = q_item["question"]
        as_of_date = q_item.get("as_of_date")

        if biz_id not in contexts:
            contexts[biz_id] = loader.load_business(biz_id)
            contracts[biz_id] = BusinessSemanticContract.from_context(contexts[biz_id])

        ctx = contexts[biz_id]
        contract = contracts[biz_id]

        plan = build_query_plan(question, ctx, as_of_date, contract)
        if plan is None:
            reason = "plan_is_none (no pattern matched in build_query_plan)"
            failure_reasons[reason] += 1
            by_biz[biz_id][reason] += 1
            if len(samples_by_reason[reason]) < 5:
                samples_by_reason[reason].append((biz_id, question))
        else:
            plan_operations[plan.operation] += 1
            missing = _check_missing_capability(plan, contract)
            if missing:
                reason = f"missing_capability: {missing}"
                missing_capabilities[missing] += 1
            else:
                reason = f"plan_compiled ({plan.operation}) but rejected in execution or Gate"
            failure_reasons[reason] += 1
            by_biz[biz_id][reason] += 1
            if len(samples_by_reason[reason]) < 5:
                samples_by_reason[reason].append((biz_id, question, plan.operation))

    print("\n--- TOP FAILURE REASONS ---")
    for r, cnt in failure_reasons.most_common(20):
        print(f"{cnt:4d} | {r}")
        for s in samples_by_reason[r][:3]:
            print(f"       -> {s[0]}: {s[1]}")

    print("\n--- COMPILED OPERATIONS THAT FAILED / ABSTAINED ---")
    for op, cnt in plan_operations.most_common():
        print(f"{cnt:4d} | {op}")

    print("\n--- MISSING CAPABILITIES ---")
    for cap, cnt in missing_capabilities.most_common():
        print(f"{cnt:4d} | {cap}")


if __name__ == "__main__":
    run_triage()
