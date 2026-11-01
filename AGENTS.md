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
   - **`type: "abstain"`:** When the agent's internal confidence is $< 95\%$ or query execution fails $\rightarrow$ Abstain cleanly to secure **0 points** and avoid negative penalty.
3. **No Human-In-The-Loop:** All parsing, cleaning, SQL generation, execution, and output formatting must run 100% autonomously in the automated benchmark container.

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
- **Location:** `~/.gemini/config/skills/duckdb-data-agent/SKILL.md`
- **Responsibilities:**
  1. *Physical unmerge:* Broadcast top-left values across merged ranges before any dataframe conversion.
  2. *Header Sniffing:* Identify the true header row among decorative invoice headers and merchant metadata.
  3. *Vietnamese Normalization:* Strip currency symbols, parse shorthand notations, conform date orders.
  4. *In-Memory DuckDB Querying:* Generate clean ANSI SQL and execute within the sandbox.

### 3.3. Production Tri-Module Architecture (Code-Level Implementation)
To eliminate hallucination and maximize Penalized Accuracy, the architecture is decoupled into 4 distinct components in `src/zaic_2026_datamate/`:

1. **`cleaner.py` (Physical Unmerge & Normalizer):**
   - Bẻ khóa `merged_cells` bằng `openpyxl`, broadcast giá trị ô góc trái.
   - Sniffing header tự động, chuẩn hóa tiền tệ Việt Nam (`100k`, `1.5tr`, `100.000 đ`, `(50.000)`).
2. **`schema_linker.py` (Schema Linking & Gate 1):**
   - Sử dụng `rapidfuzz` để liên kết từ khóa câu hỏi tiếng Việt với tên cột và giá trị ô trong bảng.
   - Tích hợp từ điển nghiệp vụ bán hàng (`VIETNAMESE_BUSINESS_SYNONYMS`: "doanh thu" $\rightarrow$ `so_luong * gia_ban`).
   - Tự động kích hoạt Cổng 1 (`unanswerable`) nếu câu hỏi nằm ngoài phạm vi bảng (OOD).
3. **`engine.py` (In-Memory DuckDB & Gate 2):**
   - Nạp DataFrame vào RAM DuckDB view và thực thi câu lệnh SQL siêu tốc.
   - Vòng lặp tự sửa lỗi (**Self-Correction Loop**): Tự động bắt lỗi cú pháp SQL và gửi phản hồi cho agent sửa trong tối đa 2-3 lượt retry.
   - Cổng 2 (Sanity Check): Kích hoạt `abstain` an toàn nếu kết quả trả về `NULL`, rỗng hoặc chia cho 0.
4. **`agent.py` (`DataMateAgent`):**
   - Đóng gói toàn bộ quy trình trên thành một entrypoint thống nhất: `agent.answer_query(query)`.
   - Xuất dữ liệu chuẩn 8 format: `number`, `percent`, `string`, `date`, `boolean`, `list`, `table`, `unanswerable`, `abstain`.

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

### 5.1. Adding New Data Pipelines or Cleaners
1. Write a minimal unit test first (TDD) using real/synthetic messy Excel fixtures.
2. Implement parsing logic using `openpyxl` $\rightarrow$ `pandas` $\rightarrow$ `duckdb`.
3. Verify with `uv run pytest`.

### 5.2. Tool & MCP Orchestration
- **`context7`:** Luôn tra cứu tài liệu mới nhất của DuckDB, Polars, và LangGraph khi cần viết các hàm phức tạp hoặc nâng cấp cú pháp.
- **`serena` & `codebase-memory-mcp`:** Dùng để rà soát cấu trúc mã nguồn, refactor hàm dùng chung và truy vết dependency giữa các module.
