# Broad Hybrid diagnostics and Public shadow candidate

Approved scope: evaluate all 3,000 Train questions with fixed business24 checkpoint/prompt/metric guard; report 24 SFT-seen vs 6 SFT-excluded business partitions and all per-business counts. Then run all 1,000 Public questions, with no labels or manual prediction, into a separate artifact directory. Preserve root submission and v2.8 snapshot; do not upload to portal.

Runner must verify endpoint provenance before calls, serialize requests to the single GPU server, close business contexts after each business, record trace/rejection reasons and model attempts, checkpoint/policy/source hashes and generation settings. Public correctness/PA must be unknown, not zero. Candidate publication requires complete unique IDs, valid formats, and Fast-only rows matching saved v2.8 answers. If Fast rows differ, report differences and withhold candidate. Candidate retains the exact basename submission.jsonl inside its own directory.

Train metrics are descriptive development diagnostics: the 24 businesses saw SFT and the other 6 have already informed guard analysis. They are not an untouched generalization estimate. No changes to routing/model/prompt during measurement. Existing output directories are refused to protect past results.
