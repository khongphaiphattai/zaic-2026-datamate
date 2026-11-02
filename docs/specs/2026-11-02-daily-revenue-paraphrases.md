# Daily revenue paraphrase expansion

Approved scope: expand recognition of the same single-day store-revenue intent, using questions only from the 24 SFT-train businesses. Freeze model, prompt generator, metric guard and Fast Path. No changes based on Public examples or six-business results after measurement starts.

Grammar: whole-question match with bounded polite prefixes/suffixes, optional comma, equivalent store-revenue and sales-money phrases, store possessive before/after date, today/yesterday/explicit date. Normalize only explicit word-level Telex variants observed in Train-24; do not delete arbitrary unknown words or use fuzzy matching. Reject product/customer/channel qualifiers, quantities/order counts, comparisons, thresholds, multiple dates, MTD/YTD, calendar-invalid/future dates. Preserve every old eligible form.

Validation: train-derived positive examples plus synthetic minimal-pair negatives; grammar frozen before measuring six-business/Public coverage; full tests; rerun Train/Public shadow into fresh directories without uploading or changing root submission.

To avoid repeated GPU work on unchanged prompts, optional response reuse may read previously recorded model outputs only when served checkpoint metadata, prompt-generator hash and generation settings match. Cache by full prompt SHA256, never by label or answer. Revalidate and execute generated SQL on current data/guard for every case; distinguish cached calls from network calls. Reject cache provenance mismatch. No cached execution results or ground-truth-assisted prediction.
