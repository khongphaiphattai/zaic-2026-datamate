# Revenue metric verification — approved implementation scope

Intent: prevent daily-revenue Slow Path answers from summing customer payable instead of rulebook merchandise revenue. Fast Path, default off switch, model checkpoint and Public submission remain unchanged.

Design: inspect DuckDB's parsed SQL JSON before execution. Accept only one direct SELECT aggregate SUM(column), optionally ROUND, from one registered base table. Reject unverified shapes (CTE/subquery in FROM, DISTINCT, arithmetic, filtered aggregate, casts, other functions), rather than treating unobserved lineage as safe. Existing WHERE/status/date logic is not certified by this metric check.

Resolve column meaning from README column definitions scoped to the matching source file/table. Recognize tightly defined merchandise-revenue descriptions and after-discount line amounts only when README states line-item grain. Column names alone, another table's descriptions, or ambiguous/conflicting definitions are insufficient. Missing evidence means abstain, never unanswerable.

Integrate only into Hybrid's selective daily revenue Slow Path, before SQL execution and on correction retries. Record matched table/column/evidence and rejection reason for auditing. Do not rewrite SQL, choose by ground-truth values, add business-ID exceptions, alter prompts or retrain in this increment.

Validation: synthetic adversarial tests (aliases/comments/CTE/arithmetic/customer payable, same-value wrong column, ambiguous/no README, line-grain vs order-grain); replay seven saved outputs without model calls to isolate guard effect; full suite; if endpoint remains live, rerun fixed 475-question development split with provenance and report separately. Validation failures already inspected imply development/regression evidence, not untouched-test accuracy.
