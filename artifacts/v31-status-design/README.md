# Exact status contract —04/11/2026

**Train adds14correct/0wrong. Public adds1answer; correctness awaits portal.**
Train total871correct/6wrong/2123abstain, PA0.2883333. New answers:3Train24
atbiz112,11SFT-excluded atbiz103. Excluded businesses have been used in earlier
development, so this is evidence across businesses, not untouched evaluation.
All old non-abstain answers/routes/reasons and raw model prompts/responses are
preserved. No old errors were patched by numeric label or ID.

Public candidate:205answered/795abstain, exactly1previous abstain changed to
percent, all204old answers preserved.1000IDs/order/format valid. SHA256:
`8c2c06b47b0033a92376a2c01da04e9a92b1a38decb02a494dd5ae6dcadca7f7`.
File: `../v31-status-public/submission.jsonl`. Candidate is ready for manual
review/upload; not uploaded/promoted, no score assigned. Root/release v3.0
retains SHA `288ed868fde9175d36afd8995cdaf0e2c5f978a716be5c2a845f35c5e89afb9f`
and its historical0.13400score. Do not infer a new score from one changed row.

## Evidence and implementation

Train rulebook§1/§8 defines four status groups, excludes whole cancelled/returned
orders, includes shipping. Every newly supported exact alias is observed in
frozen Train24 profiles; manifest records rulebook/profile/source hashes. No
Public label or schema-specific alias was used. Completion/shipping/cancel/return
classification is finite; unknown labels and ambiguous ok/failed-delivery or
negation remain refused. The legacy six accepted labels are retained.

Normalization and SQL now use the same inclusion policy. SQL builds an exact
raw-value exclusion set for cancelled/returned order IDs, quoting literals,
instead of recognizing unaccented huy in a whitelist and then missing it in an
accented substring filter. Null/order consistency/metric/date/positive-revenue
checks remain. No changes to Fast/Slow, question grammar or README role mapping.

Tests first reproduced alias rejection, then verified inclusion, unaccented
cancellation/return exclusion, multi-line returns, unknown/negated labels.
**317tests passed (24.98s)**, with one known pandas invalid-date fixture warning.
Review of diff against spec: finite vocabulary, whole-order exclusion, raw value
escaping, unchanged public interfaces and source freeze; no blocking finding.

Both full runs use34Train/1Publiccached raw calls,0network calls, SQL rerun.
`verification.json` in each run checks IDs/order/correctness/preservation/source
hashes/config equality. `independent-totals.json` independently checks14Train
and1Publicnew outputs via Pandas masks/Decimal arithmetic and raw status sets;
shared loader remains a common dependency. Public correctness is still unknown.

## Role audit and follow-up

`role-audit.json` records Train evidence: creation/return-time selected instead
of documented sale date, file+sheet headings not resolved, and some shops with
no scoped definitions. These are different problems. The status fix intentionally
does not guess roles from missing README or hardcode Public names. Next role
linking design should use file/sheet provenance and unique documented meanings,
refuse conflicting definitions, and retain metric/grain/return checks.

Reproduce with fresh output dirs using the prior MTD commands but prefixes
`v31-status-*` and cache sources `v31-mtd-*`. Verify with:

```bash
.venv/bin/python scripts/verify_mtd_evaluation.py train --run-prefix v31-status --baseline-prefix v31-mtd --allow-new-monthly --allow-refusal-reason-changes
.venv/bin/python scripts/verify_mtd_evaluation.py public_test --run-prefix v31-status --baseline-prefix v31-mtd --allow-new-monthly --allow-refusal-reason-changes
```

No Colab/training needed. Numeric execution has improved; score confirmation
requires the user to submit this exact candidate and return the portal result.
