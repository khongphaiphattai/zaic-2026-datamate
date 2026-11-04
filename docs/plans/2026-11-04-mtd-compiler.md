# MTD compiler offline

Implements the user's approved next step. Public test seams are
`compile_growth_windows(GrowthIntent, as_of_date)` and
`resolve_growth_intent(GrowthIntent, context, as_of_date, audit)`.
The first returns explicit inclusive previous/current date bounds; the second
returns percent/value/reason or abstain through the existing SQL/data guards.
An intent is trusted question-derived diagnostic input, NOT a claim that a model
has preserved all question conditions. No raw model wiring or new language gate.

`GrowthIntent` contains period full_month/month_to_date, integer year/month.
Full-month must be complete by as_of. MTD year/month must match as_of; previous
window ends on min(as_of.day, last day of previous month). Reject missing/bad
anchor, unsupported period, bool/noninteger year/month or years outside1901..2099.

Factor current execution behind these windows; retain exact schema/metric,
status, null/nonfinite/negative, order consistency and boundary guards. Both
revenues must remain positive under this conservative contract, including
current=0 rejection; no new zero policy. Signed growth rounded to2decimals.
Full-month grammar and caller behavior remain unchanged, with additive audit
previous_end/period evidence. No schema synonyms/JOIN/returns changes.

TDD vertically: ordinary MTD bounds, then cap/leap/year boundaries and invalid
intents; numeric hand-worked fixture with large distractors outside each window;
rejection cases and full-month regression. Test failure must precede code.
Evaluate all39 frozen MTD annotations directly, without prefiltering21blocked
cases; score only after calculation, record18previously-ready separately.
Reexecute every baseline full-month invocation and compare type/value/rejection
reason, plus public wrapper tests; do not infer Public gain.

Freeze source before labelled evaluation. If MTD results are wrong, diagnose
semantics before changing code/labels, save the original run, and do not promote.
Run full local tests once after code completion, review diff, verify submissionSHA,
update ROADMAP and commit. No network inference/training/Colab required.
