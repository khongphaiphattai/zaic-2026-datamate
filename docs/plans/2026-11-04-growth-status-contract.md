# Exact sales-status contract

Atomic next step after schema/status audit, using Train rulebook sections1/8 and
Train24 observed vocabulary. Four canonical states: completed/shipping included,
cancelled/returned excluded for the entire order. Unknown, blank and negated
labels refuse. No substring success/failure guesses, no default completed.

Retain existing six supported labels, add exact Train24 aliases for completion,
shipping, cancellation and returns. Explicit status classification drives SQL
exclusion using the actual raw values, safely quoted, so unaccented huy/tra hang
cannot be admitted by a normalized whitelist but missed by an accented SQL filter.
Preserve existing per-order raw status consistency guard and all schema/metric/
date/value rules. Do not broaden ambiguous ok/failed-delivery labels or README
roles in this atomic change. Rulebook/observed vocabulary do not certify every
possible status; unknown remains abstain.

TDD: xong and shipping accepted; cancel/return unaccented labels excluded from
both periods; whole-order exclusion; unknown/negation refused. Full suite, source
freeze, full Train then Public offline replay. Preserve all old non-abstain
predictions/routes; rejection reasons may become more specific after a guard
advances. No new model calls, grammar edits, Public-specific aliases or root
submission overwrite. Audit role candidates stays a separate follow-up.
