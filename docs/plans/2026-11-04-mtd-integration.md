# MTD opt-in integration and offline evaluation

Approved continuation of the capability-first plan. Add PipelineConfig.mtd_growth
default False and --mtd-growth in both entrypoints. Wrap the existing configured
agent after full-month fallback. Preserve any existing non-abstain answer. Only
whole-question certified month_to_date plans may reach the compiled resolver;
successful answers have mtd_growth_path and a separate audit/counter. Failure
keeps the base prediction/route/reason and records the attempted MTD rejection.

No grammar/model/prompt/schema changes. Existing model calls use provenance-
checked offline raw-response replay with explicit execution-source migration;
missing cache must fail. Freeze source before full Train, retain it through
Public. Check all old non-abstain predictions/routes, IDs/format, per-partition
new correct/wrong/abstain, network/cache calls and Public labels=null. Keep root
v3.0 untouched. Candidate diff is evidence of changed answers, not accuracy.

TDD seams: shared factory on/off, wrapper preservation/refusal, generator route
and output. Full suite after implementation, independent verification after
3000Train/1000Public. Update ROADMAP after milestones, commit reviewed results.
