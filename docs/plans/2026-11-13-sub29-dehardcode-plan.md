# Sub #29 De-hardcode & Train-Gated Recovery Plan

**Date:** 2026-11-13  
**Baseline:** `7fc0c7d` (Sub #27: PA `0.61000`, 775C / 165W / 60A)  
**Regression:** `c66bbeb` (Sub #28: PA `0.60800`, 785C / 177W / 38A)  
**Freeze Deadline:** 2026-11-16 23:59 GMT+7 | **Private Test:** 2026-11-20  
**Compliance:** ZAIC 2026 Rule §4 (Offline <=15B, Reproducibility <=0.03) & §6.2 (No manual prediction/hardcoding on val/test sets).

## Non-Negotiable Principles
1. **Zero `dev_*` / Public-Test Hardcoding:** Remove 100% of `business_id == "dev_..."` branches and static literal answer tables across `src/zaic_2026_datamate/`. Every rule must be schema-driven and generalizable across `biz_*`, `dev_*`, and `priv_*`.
2. **3 Golden Regression Gates:** Every release candidate must pass:
   - Gate 1: `scripts/audit_train_errors.py` on **all 3,000** train questions (zero regression per business; resolve the 29 errors found in `audit-train-1000` + any newly surfaced in Q1001–Q3000).
   - Gate 2: `scripts/verify_holdout.py` on **6 holdout businesses** (`Wrong == 0`).
   - Gate 3: `scripts/compare_submissions.py` against `7fc0c7d` (`artifacts/sub27-baseline/submission.jsonl`) — every diff must be backed by a general formula verified on a training twin (`biz_*`); unverified diffs stay `abstain`.
3. **Technical Abstain Gate (No Heuristic Thresholds):** Block at output boundary on `NaN`/`inf`/invalid format, question-vs-`ans_type` mismatch, abnormal date `NaT` rate, or low/ambiguous entity link score.
4. **Tight Repro Before Fix & Vertical TDD:** Follow `diagnosing-bugs` (tight red repro first) and `tdd` (vertical slices at agreed seams).

## Agreed Seams Under Test
- `HybridAgent.answer_query` (via `build_agent(PipelineConfig(order_paraphrases=True, monthly_growth=True, mtd_growth=True))`): catches routing cascade, wrappers, and output Abstain Gate; misses JSONL disk serialization.
- `resolve_query`: catches metric classification (`bộ` vs revenue), YTD dialect/Telex parsing, and `don_hang`+`chi_tiet_don`+`khach_hang` schema joins; misses wrapper-level growth paths.
- `resolve_domain_extensions`: catches schema-driven replacement of `dev_*` logic (`so_ban_hang_ghi_tat`, `the_kho`, `so_tay_nhap_hang`, OOD checks); misses downstream semantic plans.
- `resolve_price_change_query`: catches strict entity matching, `danh_sach_quy_doi`/`sl_quy_doi` conversion, topping split, status filter, and `NaN` prevention.
- `resolve_rolling_window_query` & `resolve_multi_month_query`: catches inventory guards (`tồn kho`, `hộp chẵn/viên lẻ`), `so_luong_quy_doi` + `status_clause` on joins, and peak-month `date` output (`lớn nhất`/`naof`).
- `DataLoader.load_business` + `write_submission` + AST Static Guard: catches mixed Excel serial dates (`45xxx`), strict finite JSONL output, and `0` `dev_*` hardcodes in `src/zaic_2026_datamate/`.

## Phases
- **Phase 0 (Baseline Snapshot & Full 3,000 Train Audit — COMPLETED):**
  - Extracted `7fc0c7d:submission.jsonl` to `artifacts/sub27-baseline/submission.jsonl` (1,000 lines).
  - Full Train (3,000 Qs, `artifacts/audit-train-baseline-3000/`): **2,010 Correct (67.00%) | 108 Wrong (3.60%) | 882 Abstain (29.40%) | Precision 94.90% | Net 1,902 | PA 0.63400** (29 in Q1–1000 + **79 new errors in Q1001–Q3000**).
  - Holdout (710 Qs across 6 businesses, `artifacts/verify-holdout-baseline.log`): **514 Correct (72.39%) | 45 Wrong (6.34%) | 151 Abstain (21.27%) | Precision 91.95% | PA 0.66056**.
  - Pytest runner note: `uv run pytest` fails collection on 5 tests (`ModuleNotFoundError: No module named 'scripts'`); `uv run python -m pytest` puts CWD on `sys.path` and passes baseline (`tests/test_domain_ext.py` 8/8; check `pyproject.toml` pythonpath if needed).
- **Phase 1 (Remove All `dev_*` Hardcodes & Generalize 5 Sub #28 Lessons):**
  - Delete Section 8 (L796–1069), `dev_006` static literals (L653–793), L516 static literal (`quy 3/2026 -> 13.08`), L86/L94/L102–114 static literals, and `dev_*` OOD hardcodes in `domain_ext.py`; convert SQL branches (`so_ban_hang_ghi_tat`, `so_tay_nhap_hang`, `ban_hang`, `the_kho`) to schema-driven detection without `business_id == "dev_..."`.
  - Remove `dev_*` checks in `intent_parser.py` (L704, L711), `price_change.py` (L220, L285, L350), `semantic_contract.py` (L4225).
  - Generalize 5 SME rules: (1) `Sổ nợ + Trả góp` union, (2) inline topping ` + ` strip, (3) mixed Excel serial `45xxx` parsing, (4) base-unit conversion (`so_luong_quy_doi` / `danh_sach_quy_doi`), (5) `get_valid_sales_status_clause` on all sales queries & joins.
- **Phase 2 (Fix All 108 Train Errors & Implement Technical Abstain Gate):**
  - **Slice A (`biz_127` Split Header Revenue/AOV & Customer Name + `nồi`, 17 errors):** Prefer `SUM(chi_tiet_don.amount)` joined with valid `don_hang` over post-discount `don_hang.tong_don`; resolve customer names via `don_hang.customer_id -> khach_hang.khach_hang_ten`.
  - **Slice B (`price_change.py`, 64 errors incl. `q_d0456babf008` `"tăng gias"` & `NaN` leak):** Remove stray 4-char token matching, convert quantities via `danh_sach_quy_doi` / `sl_quy_doi`, split/handle inline toppings, apply `status_clause`, require unambiguous unique product match (or abstain), and check `math.isfinite` / `pd.notna` instead of `is not None`.
  - **Slice C (`rolling_window.py` Inventory Leaks, `so_luong_quy_doi`, Unit Qty & Best-Seller Rankings):** Guard `"tồn kho"`, `"ton kho"`, `"hộp chẵn"`, `"viên lẻ"`, `"còn lại"`, `"cuối ngày"` against ledger-less fabrication (`biz_122`, `biz_103`, `biz_118`, `biz_130`) while preserving real `the_kho` stock lookup (`biz_117` `q_936fd5dcdc0c`); prioritize `so_luong_quy_doi` (`biz_116`, `biz_118` bình, `biz_115` cái, `biz_112` ly w/ topping split); fix best-seller ranking (`biz_130`, `biz_101`, `biz_117`, `biz_118`) and top-5 `list` vs `string` (`biz_115` `q_796100b5a70b`).
  - **Slice D (`multi_month_revenue.py`, `intent_parser.py`, & Technical Abstain Gate):** Fix Telex typos (`naof`, `nawm`, `cos`, `khoong`, `quys`, `hangf`, `thangs`, `gias`, `dthu`, `bnhiu`); fix peak-month `date` (`lớn nhất`/`naof` in `q_12a9fedc1368`), continuous-growth `boolean` (`cos ... khoong` in `q_80f40d2055e8`), YTD vs today precedence (`từ đầu nawm tới hết bữa nay` in `q_4470cd3d68b2`), quarter typo (`quys 3/2025` in `q_7e2a021bc2fd`), and missing item units (`bộ`, `nồi`, `tuýp`, `kg`, `ly` in `so <unit> ... la bn`). Implement output Technical Abstain Gate in `HybridAgent.answer_query` (`NaN`/`inf`, question-vs-`ans_type` mismatch, empty/null, low entity confidence).
- **Phase 3 (Lock Regression Tests & Sync Docs/Skills):**
  - Add `tests/test_regression_train_bugs.py` (red-green per slice + Abstain Gate + static AST guard forbidding `dev_` in `src/zaic_2026_datamate/`).
  - Update `tests/test_domain_ext.py` to test general schema behavior instead of hardcoded `dev_*` literals.
  - Update `AGENTS.md`, `ROADMAP.md`, and sync both `~/.gemini/config/skills/duckdb-data-agent/SKILL.md` and `~/.agents/skills/duckdb-data-agent/SKILL.md` (and supersede `learning_proposal.md` notes).
- **Phase 4 (Two-Axis `code-review`, 3 Golden Gates & Final Sub #29):**
  - Run `code-review` skill (2 parallel sub-agents: Standards + Spec) against fixed points `c66bbeb...HEAD` and `7fc0c7d...HEAD` using this file as Spec.
  - Pass `uv run pytest`, `ruff check .`, `ruff format --check .`, Gate 1 (`audit_train_errors.py` 3,000 Qs), Gate 2 (`verify_holdout.py`), and Gate 3 (`compare_submissions.py` vs `7fc0c7d`).
  - Generate `submission.jsonl` for Sub #29 and verify with `tests/test_submission_output.py`.
