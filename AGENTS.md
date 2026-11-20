# Zalo AI Challenge 2026 (ZAIC 2026) — Repository Agent Constitution

> **Scope:** This document governs all autonomous AI agents, assistants, and human-agent pair programming workflows in this repository.  
> **Target Track:** Primarily optimized for **Track 2: DataMate (Data Agent)**, with extensible foundations for **Track 1: LifeMate (Voice Agent)**.  
> **Official Guide Reference:** [ZAIC_2026_GUIDE.md](file:///home/pt/AI%20Challenge/ZAIC_2026_GUIDE.md)

---

## 🏛️ 1. Core Philosophy & Game Theory: "Silence is Free"

In ZAIC 2026 DataMate, the scoring metric is **Penalized Accuracy (PA)**:
$$PA = \frac{1}{N} \sum_{i=1}^N s_i, \quad s_i = \begin{cases} +1 & \text{answer } i \text{ is correct} \\ 0 & \text{answer } i \text{ is abstain} \\ -1 & \text{answer } i \text{ is wrong (hallucination)} \end{cases}$$

### Invariant Axioms:
1. **"Build a system that is right when it speaks."** Never guess, extrapolate, or invent business logic.
2. **Two Types of Refusals (Critical Distinction):**
   - **`type: "unanswerable"`:** When data genuinely lacks the required metric or lies outside the date range. Correct answer gets **+1 point**. Guessing a number for an unanswerable question incurs a **-1 penalty**!
   - **`type: "abstain"`:** When query execution fails or fails technical validation $\rightarrow$ Abstain cleanly to secure **0 points** and avoid negative penalty.
3. **Zero Hardcoding & General Schema Logic (ZAIC Rule §6.2):**
   - Derive every business rule (merged `Sổ nợ + Trả góp`, ` + ` topping split, Excel serial date `45xxx`, base-unit conversion, cancelled/returned order filter, `"đến hết"`/`"tới cuối"` month-range grammar) generically from schema and calendar bounds. Never branch on `business_id` (`dev_*`/`biz_*`/`priv_*`), hardcode specific month/date routing literals (`"th9/2025"`, `"th4/2026"`), import cross-module `_private` helpers, leave dead helpers, or read `public_test` paths in core logic (locked via `tests/test_phase1_dehardcode_and_rules.py`).
4. **Three Golden Regression Gates (Mandatory Pre-Submission Checklist — 15x Speedup Protocol):**
   - Run all gates with **identical production config** via `build_agent(production_pipeline_config())` and `evaluate_match` (`scripts/generate_public_submission.py` defaults to production parity unless `--no-production` is passed):
   - **Between Slices (Fast Gate ~25s):** Run `uv run ruff check` + `uv run python -m pytest -m "not slow"` (~10s) + targeted business check `uv run python scripts/audit_train_errors.py --businesses biz_117,biz_118 --max-wrong 0` (~10–15s).
   - **Gate 1 + Gate 2 Combined (Train 3,000 Qs + Holdout 710 Qs in ~2 min):** `uv run python scripts/audit_train_errors.py --workers 8 --max-wrong 0` runs multi-process across all 30 businesses and automatically prints/validates **Gate 2 (Holdout 710 Qs across 6 businesses)** from `artifacts/v34-holdout/holdout.json` without needing a separate `scripts/verify_holdout.py` run! Run after each batch of slices.
   - **Slow Pytest Suite (`pytest -m slow`):** Uses session-cached `BusinessContext` (`tests/conftest.py`); run **once at the end of the session** before packaging.
   - **Gate 3 (Twin Verification vs Baseline `7fc0c7d`):** `scripts/compare_submissions.py` — every `abstain -> answer` or value change must map to schema-general logic verified on training twins (`biz_*`); otherwise abstain (*Break-Even Precision* $P \ge 50\%$).
5. **Technical Abstain Gate (Real Output Signals Only):**
   - `HybridAgent.answer_query` enforces finite, schema-valid outputs via `_technical_abstain_gate`: coerce `NaN`/`Inf`, `bool` in `number`/`percent`, empty strings/lists/tables, invalid calendar dates, or question-vs-`ans_type` mismatches (`list` on `boolean`/peak period) to `abstain`. Rely on deterministic execution signals rather than heuristic confidence thresholds.
6. **No Human-In-The-Loop:** All parsing, cleaning, SQL generation, execution, and output formatting must run 100% autonomously in the automated benchmark container.

---

## ⚙️ 2. Production Technology Stack & Invariants

All dependencies are pinned and managed via `uv` in `.venv`:

| Layer | Technology | Primary Responsibility & Invariants |
| :--- | :--- | :--- |
| **Package & Env Manager** | `uv` (Fast Rust-based packaging) | Environment isolation, deterministic lockfile (`uv.lock`), rapid container builds. |
| **Physical Excel & DB Parser** | `openpyxl`, `python-calamine`, `sqlite3` | Unmerging merged cells, unhiding sheets, querying `.sqlite` / `.csv` / `.xlsx`. |
| **Data Wrangling & Heuristics** | `pandas` (with `pyarrow` engine) | Header sniffing, footer removal, Vietnamese currency regex (`100k`, `1.5tr`, `100.000 đ`), date parsing (`dd/mm/yyyy`). |
| **OLAP SQL Execution Engine** | `duckdb` (In-memory C++ engine) | Zero-copy SQL queries directly on Pandas DataFrames, CTEs, Window functions, fast sub-second aggregations. |
| **Entity & Schema Linking** | `rapidfuzz` (C++ engine) | Fuzzy string matching, token-set ratio, Value Linking into table cells. |
| **Foundation Model Ceiling** | **$\le 15$ Billion Parameters** | Total parameters across all inference models $\le 15B$. Primary: `Qwen2.5-Coder-7B/14B-Instruct`. |
| **Hardware Stratification** | Local + Colab Pro + Benchmark | **Local:** Ryzen 7 5700X, 32GB RAM, abundant SSD, RX 6600 (DuckDB & Wrangling).<br>**Cloud:** Colab Pro A100/L4, 200 units (LoRA Fine-tuning).<br>**Benchmark:** $2 \times$ RTX 5090, 64GB VRAM, 512GB RAM (Final offline re-run). |
| **Container Environment** | Offline Docker (`--network none`) | `/data` directory is mounted **READ-ONLY**. Write all temporary files to `/tmp` and output to `/result`. |
| **Output & Logging** | Streaming `.jsonl` | File nộp bài luôn ĐẶT TÊN CHÍNH XÁC LÀ `submission.jsonl` tại root repo (hoặc `/result/submission.jsonl` trong container). TUYỆT ĐỐI KHÔNG thêm hậu tố phiên bản như `_v2`, `_v3` vì portal thi đấu sẽ báo lỗi không nhận diện file! Per-question detailed audit logs to `/result/logs/`. |

---

## 🎯 3. Specialized Skills & Core Architecture

### 3.1. Persona: `Lead Data Agent Engineer`
When operating in this repository, autonomously adopt the mindset of a Senior Data Agent Engineer:
- Prioritize **deterministic execution** (SQL/Code Interpreter) over loose generative reasoning.
- Deeply inspect edge cases in Vietnamese commercial records (tiểu thương, tiệm tạp hóa, phòng khám, nhà xe).

### 3.2. Skill: `duckdb-data-agent`
- **Mirrored Paths:** `~/.gemini/config/skills/duckdb-data-agent/SKILL.md` and `~/.agents/skills/duckdb-data-agent/SKILL.md` (keep byte-identical).
- Covers Excel unmerging, header sniffing, Vietnamese normalization, In-Memory DuckDB patterns, and refusal guardrails.

### 3.3. Production Pipeline (`src/zaic_2026_datamate/`)
Inspect live modules directly rather than caching signatures; core orchestration flows through:
- **`pipeline.py` (`PipelineConfig`, `build_agent`):** Composes `HybridAgent` / `DailyOrderParaphraseAgent` wrapped by `MonthlyGrowthAgent` and `MTDGrowthAgent`.
- **`data_loader.py` & `cleaner.py`:** Unmerges `.xlsx`, detects multi-row headers, normalizes VN currencies/dates, builds `BusinessContext`.
- **`intent_parser.py` & `hybrid_agent.py`:** Runs deterministic Fast Path (`resolve_query`), optional bounded Slow Path (`slow_path.py`), and `_technical_abstain_gate` before emitting benchmark output types (`number`, `percent`, `string`, `date`, `boolean`, `list`, `table`, `unanswerable`, `abstain`).
- **Shared & Vertical Modules:** Reuse public helpers across `domain_ext.py`, `semantic_contract.py`, `growth_semantics.py`, `revenue_metric.py`, `price_change.py`, `rolling_window.py`, `repair_shop.py`, `mobile_installment.py`, `multi_month_revenue.py`, `mother_baby.py`, `purchase_analytics.py`, and `sales_status.py`.

---

## 📋 4. Solution Freeze & Verification Invariants (Rule 4.3 & 4.4)

- **Deadline:** November 16, 2026 (23:59 GMT+7).
- **Mã nguồn mở chuẩn OSI:** Mọi mô hình nền (LLM) và thư viện sử dụng phải có giấy phép OSI-approved (Apache 2.0, MIT, BSD) không có điều khoản hạn chế thương mại.
- **Zero Proprietary API Dependency in Final Freeze:** Bài nộp cuối cùng phải chạy độc lập trên máy chủ benchmark của BTC ($2 \times$ RTX 5090, 64GB VRAM, 512GB RAM).
- **Tái lập 100% (Reproducibility):**
  - $$|PA(\text{uploaded}) - PA(\text{re-run})| \le 0.03$$
  - Tất cả lời gọi model phải đặt `temperature = 0.0`, `seed = 42`.
  - Môi trường định nghĩa rõ ràng qua `pyproject.toml`, `uv.lock` và Dockerfile.

---

## 🛠️ 5. Standard Operating Workflows

### 5.1. Adding New Data Pipelines, Fixes or Regression Locks
1. Follow TDD (red-before-green): add a minimal failing test in `tests/` using synthetic schema fixtures or verified training bugs (`tests/test_regression_train_bugs.py`), never `data/public_test`.
2. Promote reusable normalization/matching logic to public functions; remove dead `_private` helpers in the same edit.
3. Between individual slices, run only the Fast Gate (`./.git/hooks/pre-commit`: `uv run ruff check` + `uv run python -m pytest -m "not slow"`, ~10s, plus `uv run python scripts/audit_train_errors.py --businesses ... --max-wrong 0`, ~15s). Run full `uv run python scripts/audit_train_errors.py --workers 8 --max-wrong 0` (covering both Gate 1 Train 3,000 Qs and Gate 2 Holdout 710 Qs in ~2 min) after each slice cluster, and run `uv run python -m pytest -m slow` (session-cached via `tests/conftest.py`) once at the end of the session.
4. Keep both `duckdb-data-agent/SKILL.md` mirrors byte-identical after discovering new data patterns.
5. **Portal "Test Solution" & Freeze Packaging:** Before 23:59 GMT+7 on Nov 15, 2026 (max 1 test run/day), use `scripts/package_freeze_solution.sh` to build `zaic2026-datamate:freeze`, verify offline parity (`--network none`), export `.tar.gz`, and output both **MD5** (for Portal `Test Solution`) and **SHA256** (for Nov 16 Freeze).

### 5.2. Tool & MCP Orchestration
- **`context7`:** Luôn tra cứu tài liệu mới nhất của DuckDB, Polars, và LangGraph khi cần viết các hàm phức tạp hoặc nâng cấp cú pháp.
- **`serena` & `codebase-memory-mcp`:** Dùng để rà soát cấu trúc mã nguồn, refactor hàm dùng chung và truy vết dependency giữa các module.

---

## WSL2 / Linux Execution Guardrails

- **Linux binaries only:** Always use native Linux tools on `PATH` (`docker`, `uv`, `python3`, `skillspector`). NEVER invoke Windows `.exe` binaries (`powershell.exe`, `cmd.exe`, `docker.exe`, `*.exe`) from WSL2 background commands (causes ConPTY deadlock / `Empty log`).
- **Anti-hang pattern:** Wrap network/scan/build commands (`git`, `docker`, `curl`, `npx`, `skillspector`) with `timeout <sec>s <cmd> < /dev/null` and `PYTHONUNBUFFERED=1` for Python scripts.
- **Avoid `/mnt/c` heavy scans:** Never recursively scan or run bulk I/O directly on `/mnt/c/...` (9P bottleneck); copy to `/tmp` or Linux home first if needed.
