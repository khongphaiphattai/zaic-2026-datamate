# Zalo AI Challenge 2026 (ZAIC 2026) — Complete Guide & Rules Reference

> **Quick Context for AI Agents:**  
> This document summarizes the official rules, track specifications, technical requirements, scoring mechanisms, and deadlines for **Zalo AI Challenge 2026 (ZAIC 2026)**, updated directly from the official portal (`challenge.zalo.ai/portal/datamate`) and official dataset splits (`train.zip`, `public_test.zip`). Read this file first when planning, coding, or optimizing solutions in this repository.

---

## 1. Overview & Paradigm Shift

* **Official Portal:** [challenge.zalo.ai/portal/datamate](https://challenge.zalo.ai/portal/datamate)
* **Organizer:** Zalo AI (VNG Corporation)
* **Official Theme:** *"Advancing AI Agents for Real Life"*
* **Core Philosophy:** A major shift from traditional Generative AI (pure text/image output) to **Agentic AI**:
  * Multi-step planning, entity extraction, and schema linking.
  * Deterministic tool use, in-memory SQL execution, and Python sandboxing.
  * Handling real-world messy context in Vietnamese small business records.

---

## 2. Tracks & Problem Formulations

### Track 1: LifeMate — Voice Agent
> *"Agentic AI That Turns Your Voice Into Action"*
* **Problem Statement:** Build an AI voice agent that listens to conversational spoken requests in Vietnamese and carries out multi-step tasks across real-world services.
* **Benchmark Hardware:** 1× NVIDIA A30 GPU, 16-core Intel Xeon Gold 6442Y CPU, 64 GB RAM.

### Track 2: DataMate — Data Agent (Primary Focus)
> *"Talking To Your Data Through Agentic AI"*
* **Problem Statement:** Build an AI data agent that answers natural-language Vietnamese business questions directly on structured data (`.csv`, `.xlsx`, `.sqlite`) or declines to answer when data cannot support the question.
* **Input Package Structure:**
  ```text
  <split>/
  ├── data/<business_id>/
  │   ├── *.csv | *.xlsx | *.sqlite   # One or more data files
  │   └── README.md                  # Business description and notes
  ├── rulebook.md                    # Split-specific default rules and vocabulary
  ├── calendar.csv                   # Solar-lunar calendar (Tết window: 23 Chạp to mùng 10)
  ├── questions.jsonl                # Questions with as_of_date
  └── answers.jsonl                  # Ground truth (training split only)
  ```
* **Sample Question Format (`questions.jsonl`):**
  ```json
  {"id": "q_01234", "business_id": "biz_017", "question": "thang nay hang nao ban cham nhat, ton nhieu ma ban ko dc", "as_of_date": "2026-08-27"}
  ```
  * `as_of_date`: The "today" anchor against which relative time expressions (*"tháng này"*, *"tuần rồi"*, *"cùng kỳ năm ngoái"*) are resolved. Records occurring after `as_of_date` are considered not yet happened.

---

## 3. Evaluation & Scoring Metric

### 3.1. Leaderboard Metric: Penalized Accuracy (PA)
The leaderboard score is **Penalized Accuracy (PA)** across all $N$ questions:
$$PA = \frac{1}{N} \sum_{i=1}^N s_i, \quad s_i = \begin{cases} +1 & \text{answer } i \text{ is correct} \\ 0 & \text{answer } i \text{ is abstain} \\ -1 & \text{answer } i \text{ is wrong (hallucination)} \end{cases}$$

> [!CAUTION]
> Any answer given to an unanswerable question counts as **wrong (-1 point)**. A justified refusal can either be:
> - `type: "unanswerable"`: Correct if and only if the reference is unanswerable $\rightarrow$ **+1 point**.
> - `type: "abstain"`: Agent chooses not to answer $\rightarrow$ **0 points** regardless of reference.

### 3.2. Output Types & Exact Matching Rules

| Type | Content | Example | Matching Rule |
| :--- | :--- | :--- | :--- |
| **`number`** (62.8%) | Numeric value (VND, counts, quantities) | `48250000` | Exact match. Integers for VND/counts or rounded to 2 decimals. No currency symbols (`đ`, `k`, `triệu`). |
| **`percent`** (19.3%) | Percentage value | `-12.45`, `2.08` | Exact match after rounding to 2 decimal places. Correct sign. No `%` symbol. |
| **`list`** (6.4%) | Ordered list of names/numbers | `["Cà phê sữa", "Bạc xỉu"]` | Ordered array. Position-by-position match. Single element must still be a JSON array `["item"]`. |
| **`date`** (4.8%) | ISO date | `"2026-08-15"`, `"2025-09"` | Exact match. `YYYY-MM-DD` (or `YYYY-MM` for questions about a month). |
| **`string`** (2.9%) | Exact name / entity | `"Sting dâu 330ml"` | Unicode NFC, lowercase, whitespace collapsed, diacritics preserved. Matches any accepted spelling/code in data. |
| **`unanswerable`** (2.3%) | Cannot answer due to data lack | `{"type": "unanswerable", "reason": "..."}` | Correct (+1) iff reference is unanswerable. |
| **`boolean`** (0.8%) | True / False | `true` | Exact match (`true` / `false`). |
| **`table`** (0.6%) | Structured sub-table | `{"columns": [...], "rows": [[...]]}` | Columns matched by normalized name/position; rows matched as set. |
| **`abstain`** (fallback) | Voluntary abstention | `{"type": "abstain"}` | Always scores 0 points (no penalty). |

---

## 4. Technical Requirements & Verification Invariants (Rule)

1. **Model Parameter Ceiling: $\le 15$ Billion (15B):**
   - Across ALL models used at inference (LLMs, embeddings, rerankers, classifiers), the **total number of parameters must not exceed 15 billion**, counting all parameters of mixture-of-experts (MoE) models.
   - Publicly released before **1 October 2026** under permissible license.
   - Any quantization (AWQ, GPTQ, GGUF) is allowed.
2. **Offline Container (`--network none`):**
   - Zero external services, zero commercial APIs, zero network access, zero downloads during execution.
3. **Compute Benchmark Reference:**
   - **$2 \times$ NVIDIA RTX 5090 (32 GB VRAM each, 64 GB total), 512 GB RAM**.
   - Blackwell GPU architecture requires **CUDA $\ge 12.8$** and matching PyTorch/vLLM build.
   - Must process the entire private test set within **6 hours** of wall-clock time.
4. **Reproducibility Threshold:**
   - $$|PA(\text{uploaded}) - PA(\text{re-run})| \le 0.03$$
   - Disqualification if difference exceeds $0.03$ without valid explanation from logs.
5. **Read-Only `/data` Directory:**
   - Test set is mounted at `/data` as READ-ONLY. All intermediate/temporary files must be written to `/tmp` or `/result`.
6. **Logging Obligation:**
   - Must write `/result/submission.jsonl` AND detailed per-question execution logs to `/result/logs/` (question id, final answer, elapsed time, generated SQL/code, execution output).

---

## 5. Timeline & Daily Quotas

| Date & Time | Milestone | Quota / Deliverable |
| :--- | :--- | :--- |
| **Oct 26 – Nov 16** | **Round 1: Public Test** | Max **5 submissions/day** on portal. |
| **Nov 16 (23:59 GMT+7)** | **Solution Freeze Deadline** | Docker package via Google Drive + SHA256 checksum. Max 2 submissions, only last evaluated. |
| **Nov 20 (09:00) – Nov 23 (23:59)** | **Round 2: Private Test** | Private test set released (~500 questions, new business types). Run frozen Docker, submit `submission.jsonl`. |
| **Dec 02, 2026** | **Finalists Announced** | Top 2 teams per track announced for verification. |
| **Dec 12, 2026** | **Final Standings** | Champion ($3,500) & Runner-up ($2,500) awarded. |


---

## 6. Official Regulations & Legal Invariants (PDF & FAQ Reference)

Synthesized directly from the 14-page official document `zalo-ai-challenge-regulations-vi.pdf` and portal FAQ:

### 6.1. Team Management & Merging Rules (Mục 2 & FAQ)
* **Team Limit:** Up to **5 members** (including team leader). Each participant can only join one team.
* **Team Changes:** Team leader can add/remove members on the portal. Previous submissions by removed members remain credited to the team.
* **Hard Lock Deadline:** Starting from **November 17, 2026**, the organizing committee will **disable all add/remove member functions** in Team Management.
* **Team Merging Formula:** Permitted if total submissions of the merged team do not exceed:
  $$\text{Max Allowed Submissions} = (\text{Daily Quota: 5}) \times (\text{Days Elapsed Until Merge})$$
* **Disqualification for VNG Personnel:** Employees, contractors, or interns of VNG Corporation and its affiliates are strictly prohibited from competing.

### 6.2. Data Privacy & External Data Compliance (Mục 4)
* **Vietnamese Data Protection Law:** Must strictly comply with Vietnam's Personal Data Protection Law No. 91/2025/QH15 and Decree No. 356/2025/ND-CP.
* **Post-Competition Obligation:** When the competition concludes, teams are legally obligated to delete/destroy all personal data derived from or associated with the competition dataset.
* **Prohibited Labeling:** Adding sub-labels or notes to the training set is permitted; **manual human labeling or prediction on validation/test sets is strictly forbidden**.
* **Code & Data Sharing:** Private sharing of code or data outside the team is strictly prohibited. Any code sharing must be publicly posted on the official competition forum under an OSI-approved open-source license.

### 6.3. Prize Claiming, Support & Dispute Window (Mục 7, 8, 12)
* **Official Support Channel:** Email to `aichallenge@zalo.me`.
* **Prize Claiming Protocol:** Potential winners must confirm within **1 week**, submit complete dossiers within **2 weeks**, and prizes are disbursed within **30 days**.
* **Dispute & Appeal Window:** Any complaints regarding evaluation or results must be submitted within **7 working days** following the official standings announcement. Decisions of the Organizing Committee are final and binding.
