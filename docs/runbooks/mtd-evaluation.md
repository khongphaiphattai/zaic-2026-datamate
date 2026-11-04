# MTD pipeline evaluation

`--mtd-growth` is opt-in in both generation and diagnosis. It uses the shared
factory and adds `mtd_growth_path` only after existing paths abstain. It needs
neither a model call nor the monthly-growth flag; production defaults remain
off. Its certifier/metric/compiler are frozen before Public measurement.

From repo root, use fresh output directories:

```bash
.venv/bin/python scripts/diagnose_hybrid.py --dataset train --output-dir artifacts/v31-mtd-train --reuse-run artifacts/v31-growth-train --order-paraphrases --monthly-growth --mtd-growth --offline-replay --allow-execution-changes
.venv/bin/python scripts/diagnose_hybrid.py --dataset public_test --output-dir artifacts/v31-mtd-public --reuse-run artifacts/v31-growth-public --order-paraphrases --monthly-growth --mtd-growth --offline-replay --allow-execution-changes
```

The existing daily Slow Path is retained through verified cached raw responses;
all SQL reruns. Offline mode fails on missing cache and never falls back to the
network. The migration flag explicitly permits execution changes, while model,
generation and full prompt identity checks remain required. Do not call it a
fresh live model measurement. Reused provenance is declared checkpoint metadata.

Check sufficient IDs/order/format; all baseline non-abstain predictions and
routes/reasons; delta correct/wrong by Train24/excluded6; Public targets/correct
null; new MTD audit windows, network/cache calls; Train/Public frozen sources
and pipeline config identical. Root submission/release v3.0 must retain its
published SHA until a separate release decision. A different Public file has
unknown accuracy until scored; an identical file is not a new scoring experiment.

Tests include shared factory default/opt-in, preservation of existing answers,
data refusal and generator output/route. Full suite passed306tests before
evaluation, with one known pandas warning in an invalid-date fixture. Full
dataset results and next decision are recorded at the end of ROADMAP.md.
