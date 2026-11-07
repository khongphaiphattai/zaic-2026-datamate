# Artifacts: v3.3 Replay Package

> **Release Date:** 07/11/2026  
> **Target:** ZAIC 2026 DataMate (Track 2)  
> **Candidate File:** `artifacts/v33-replay/submission.jsonl`  
> **Candidate SHA256:** `36ecdf160767ac8376afe553ab31152d878a3f7a7d594cb2618caae019840e49`  

---

## 📋 Overview of Changes in v3.3

1. **Step 1 (Forensic Bug Fixes — Phase 1):**
   - Fixed `sheet_year` extraction per sheet in `clean_dataframe()`.
   - Filtered `"Ghi chú: ..."` footer rows across `dev_004`, `dev_013`, `dev_014`.
   - Included `"stt"`, `"ghi_chu"`, `"kenh"`, `"kenh_ban"` in order scoping.
   - Accurately split topping lines and mapped categories in `dev_002` (100% matched).
   - Fixed phone store revenue logic in `dev_011`, `dev_014` to use line totals (`thanh_tien`) without subtracting order-level discounts or `thu_may_cu`.
   - Corrected 28 previously miscalculated answers on Public Test.

2. **Step 2 (Language Expansion & Growth Metrics — Phase 2A):**
   - Added two-digit months (`"8/26"`, `"th 3/26"`), dialects (`"tháng ni"`), rolling N-days (`"30 ngày gần nhất"`), synonyms (`"dthu"`, `"tiền bán"`, `"đh"`, `"phiếu sửa xe"`).
   - Implemented Gate 1 `unanswerable_gate` for subjective sentiment questions and profit queries lacking cost data.
   - Implemented `period_growth_pct` (MoM, YoY, MTD, WoW) across orders and order lines.

3. **Step 3 (Quantity & Ranking Top 1 — Phase 2B):**
   - Added `quantity` and `base_quantity` to `canonical_order_lines`.
   - Implemented `filtered_quantity` for items, categories, sizes, and store-wide drink counts.
   - Implemented `ranking_top_k` for peak day (`date`), peak month (`date`), and bestseller Top 1 (`string`).
   - Hardened with 7 epistemic guards (blocking inventory stock questions, multi-month ranges, pill unit conversions, rice weight conversions, and Top K lists).

4. **Step 4 (Packaging & Verification):**
   - Train 3,000 benchmark: 1,391 correct, 0 wrong, PA = `+0.46367` (100.00% precision).
   - Public Test: 473 answered (94 newly unlocked, 28 value-fixed, 0 regressed).

---

## 📂 Package Contents

- `submission.jsonl`: Candidate submission for Public Test (1,000 rows).
- `summary.json`: Complete quantitative comparison between v3.2 and v3.3.
- `public-diffs.json`: Line-by-line diff between v3.2 and v3.3 answers on Public Test.
- `train-wrong.json`: `[]` (Zero errors across all 3,000 Train questions).
