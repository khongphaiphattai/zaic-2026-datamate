# MTD integration — full evaluation,04/11/2026

**Train adds17correct/0wrong; Public adds0answers. No new release.**

Full Train:857correct/6wrong/2137abstain, PA0.2836667, compared with monthly
baseline840/6/2154, PA0.27800. MTD contributes17correct at biz121(4),biz122(9),
biz130(4), allTrain24. SFT-excluded6 remains140correct/1wrong/334abstain;
these groups have prior development history and are not unseen validation.

Existing Fast/Slow/count/monthly predictions, routes and reasons are preserved,
as are reasons/routes for questions still abstaining.34cached raw calls,
0network calls,172.52s local evaluation. SQL reexecuted under the new execution
source; this does not measure GPU speed or live model quality.

Public completed1000questions with the same frozen source/config:204answered,
796abstain,0new answers,1cached raw call,0network. Candidate at
`../v31-mtd-public/submission.jsonl` is byte-identical to v3.0, SHA
`288ed868fde9175d36afd8995cdaf0e2c5f978a716be5c2a845f35c5e89afb9f`.
Root/release v3.0 remain unchanged. Public correctness is unknown; only the
already-submitted v3.0 has score0.13400. Do not resubmit the identical file.

## What was integrated

Shared PipelineConfig/factory and both entrypoints support `mtd_growth` /
`--mtd-growth`, default off. Fallback runs after existing paths abstain, accepts
only whole-question certified MTD, uses existing guards/compiler, and emits
`mtd_growth_path` only on success. Failure preserves the base output and records
its MTD audit separately. Model prompt/checkpoint/grammar are unchanged.

306tests passed (24.08s), including public factory/generator behavior and
number/percent/unanswerable/refusal preservation. One known pandas warning
comes from deliberate invalid-date input. Review against integration spec
found no blocker: defaults remain off, metadata delegates correctly, MTD cannot
replace an old answer, SQL identifiers/dates retain existing validation, and
offline missing cache is fatal. No new model/dependency/network path.

Independent verification rechecks3000/1000IDs, serialized output order and
predictions, Train correctness, old-answer preservation, raw calls/prompts/
responses, source hashes, config equality and releaseSHA. See each run's
`verification.json`; reproduce with `scripts/verify_mtd_evaluation.py train`
or `public_test` after following `docs/runbooks/mtd-evaluation.md`.

## Bottleneck and next experiment

Train MTD was attempted55times across11businesses:17accepted,20unsupported
status,18missing/unverified roles/columns. Public had4MTD attempts across
4businesses:1unsupported status and3unverified order roles. This distinguishes
question recognition from schema/status execution blockers. Aggregates only;
do not hardcode Public column names or questionIDs to bypass rejection.

Next local step: audit canonical sales statuses and README-scoped role linking
using Train24, including wrong-table/sheet/file mapping, sale-date versus
creation/return-time, order grain and return semantics. Treat accepted status
aliases as claims requiring evidence and counterexamples, not arbitrary
substring matches. Establish a reusable contract and test it across business
formats before rerunning Public. No more wording expansion just to improve
Train, no training/GPU requirement demonstrated by these results.

MTD is now an opt-in evaluated capability, but full Public coverage remains the
release bottleneck. There is no v3.1 score or promoted submission from this run.
