# Growth semantic boundary, local only

Step3 of the approved capability-first plan. Known risk: a valid JSON model
proposal can omit a shift/channel or change YoY to MoM. Merely adding empty
filters or quoted spans cannot prove that all source conditions were retained.

Use a bounded independent certifier, not a denylist: match the entire original
question against the existing full-month grammar or a small MTD grammar built
from reviewed Train-24 wording. No fuzzy deletion or arbitrary ignored suffixes.
Unknown wording refuses. This is a deterministic comparator with limited
coverage, not a general natural-language proof or a new claim about AI quality.

Public seams:
- `certify_growth_question(question, as_of_date)` returns None or an explicit
  plan: typed GrowthIntent, comparison kind, empty supported filters, and both
  date windows. It inspects only question/date, never ID/business/target.
- `validate_growth_proposal(raw_json, question, as_of_date)` checks exact keys,
  duplicate keys, integer types, comparison, filters, dates and agreement with
  the independently certified original question. Valid JSON alone never admits
  a proposal. Strict abstain returns None. It does not invoke SQL.

Proposal shape: intent, period, year, month, comparison, filters. Only empty
filters are supported, and only after whole-question certification. Comparisons
are previous_full_month or previous_month_same_days, explicitly agreed with the
question and period. Do not accept the legacy four-field shape by filling defaults.

Freeze24synthetic controls (8positive,16negative) before implementation. Use
minimal pairs based on accepted wording so refusal cannot be explained solely
by an unsupported base phrase. Check fabricated valid proposals that claim no
filters against filtered questions, YoY, negation, appended instructions and
different date windows. Old14probe cases remain development regression; refusal
of unsupported positive wording is a coverage loss, never scored as a correct
positive. No assertion that zero sampled false accepts proves universal safety.

Keep production pipeline and existing model prompt untouched. Measure offline
on frozen281Train-24 diagnostic cases using raw question→certifier→compiler,
with IDs used only for reporting/scoring after execution. Record recall gaps,
false acceptance of outside-capability questions, final numeric correct/wrong/
abstain and preservation of releaseSHA. A model that merely agrees with this
certifier adds no demonstrated coverage; broader model use needs separate
validation. No Colab/training/Public question tuning or new submission.
