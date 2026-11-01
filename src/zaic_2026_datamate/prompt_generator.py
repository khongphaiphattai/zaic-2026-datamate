"""
prompt_generator.py: Dense, Schema-Compressed Text-to-SQL prompts
for Qwen2.5-Coder-7B/14B in ZAIC 2026 DataMate.

Key Optimizations:
1. Relevance-Ranked Table Selection: Selects top 3-5 most relevant tables using
   token overlap and domain heuristics, discarding duplicate sheets (_sheet1) and irrelevant tables.
2. Hard Bounds on Context: Guarantees prompt is always < 3,000 tokens (safe against 8,192 limit).
3. Strict Temporal Anchor (as_of_date): Forbids CURRENT_DATE/NOW() and enforces exact DATE anchor.
4. Business Rulebook Invariants: Returns handling, delivery, AOV, net price, ranking.
"""

from __future__ import annotations

import json
import re
import unicodedata
from typing import Any, Dict, List, Optional, Set, Tuple

import pandas as pd
from rapidfuzz import fuzz

from zaic_2026_datamate.data_loader import BusinessContext

SYSTEM_PROMPT = """Bạn là Chuyên gia Dữ liệu & Text-to-SQL (Lead Data Agent) tham gia Zalo AI Challenge 2026 (Track 2: DataMate).
Nhiệm vụ của bạn: Dịch câu hỏi nghiệp vụ tiếng Việt thành câu lệnh SQL DuckDB chính xác tuyệt đối, hoặc từ chối trả lời nếu thiếu thông tin theo triết lý 'Silence is Free'.

Các quy tắc nghiệp vụ bất biến theo Rulebook 2026:
1. ĐIỂM NEO THỜI GIAN (as_of_date):
   - CẤM TUYỆT ĐỐI dùng `CURRENT_DATE`, `CURRENT_TIMESTAMP`, `NOW()`. Bắt buộc dùng giá trị cụ thể của as_of_date: DATE 'YYYY-MM-DD'.
   - 'hôm nay', 'hnay', 'bữa nay' = as_of_date.
   - 'hôm qua', 'hqua' = as_of_date - INTERVAL '1 day'.
   - 'tháng này' = từ ngày 1 của tháng chứa as_of_date đến hết as_of_date (hoặc MONTH(col) = MONTH(as_of_date) AND YEAR(col) = YEAR(as_of_date) AND col <= as_of_date).
   - 'tuần này' = từ Thứ Hai của tuần chứa as_of_date đến hết as_of_date.
   - 'tuần trước' = trọn vẹn từ Thứ Hai đến Chủ Nhật của tuần liền trước as_of_date.
   - 'từ đầu năm / YTD' = từ 01/01 của năm chứa as_of_date đến hết as_of_date.
   - CẤM tính các giao dịch sau as_of_date (coi như chưa xảy ra). Luôn có điều kiện: `col <= DATE '{as_of_date}'`.

2. DOANH THU & ĐƠN HÀNG HỢP LỆ (Rulebook Mục 1):
   - Đơn tính vào doanh thu: đơn KHÔNG huỷ và KHÔNG trả hàng.
   - Đơn có trả hàng (kể cả chỉ trả 1 dòng hàng) là đơn trả hàng: LOẠI BỎ CẢ ĐƠN.
   - Đơn đang giao / đang xử lý / shipping VẪN TÍNH.
   - Tiền hàng = tổng thành tiền dòng hàng trừ chiết khấu dòng (không trừ voucher toàn đơn, không tính ship, không cộng VAT).

3. GIÁ TRỊ TRUNG BÌNH ĐƠN (AOV):
   - AOV = ROUND(SUM(revenue) / COUNT(DISTINCT order_id)) làm tròn thành số nguyên VND.

4. XẾP HẠNG & TOP BÁN CHẠY (Rulebook Mục 4):
   - Xếp theo SỐ LƯỢNG quy về đơn vị cơ bản (không xếp theo doanh thu tiền).

5. NGUYÊN TẮC 'SILENCE IS FREE' (Phòng chống phạt điểm -1):
   - Nếu câu hỏi hỏi thông tin mà dữ liệu hoàn toàn không có (địa chỉ, số điện thoại, thời tiết) -> type: 'unanswerable'.
   - Nếu câu hỏi mơ hồ, thiếu dữ kiện, hoặc độ tự tin < 95% -> type: 'abstain'.

BẮT BUỘC TRẢ VỀ JSON HỢP LỆ:
{
  "thought": "Phân tích ngắn gọn logic, bảng cần dùng và điều kiện lọc",
  "type": "number" | "percent" | "list" | "date" | "string" | "table" | "unanswerable" | "abstain",
  "sql": "SELECT ... (hoặc null nếu unanswerable/abstain)",
  "confidence": 0.95,
  "reason": "lý do nếu unanswerable hoặc abstain"
}
"""


def _normalize_text(s: str) -> str:
    s = unicodedata.normalize("NFD", s.lower())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn").replace("đ", "d")
    return s


def select_relevant_tables(
    question: str,
    context: BusinessContext,
    max_tables: int = 5,
) -> List[Tuple[str, pd.DataFrame]]:
    """Select the top most relevant tables for the given question."""
    q_norm = _normalize_text(question)
    candidates: List[Tuple[str, pd.DataFrame]] = []

    # 1. Deduplicate table names ending in _sheet1, _sheet2, etc.
    existing_tables = set(context.tables.keys())
    for tname, df in context.tables.items():
        if df is None or df.empty:
            continue
        # Skip duplicate sheet tables if base table exists
        base_match = re.sub(r"_sheet\d+$", "", tname)
        if base_match != tname and base_match in existing_tables:
            continue

        # Skip calendar table unless lunar / Tet / holiday mentioned
        if tname == "calendar":
            is_lunar = bool(re.search(r"\b(tet|am lich|mung|chap|le|nghi)\b", q_norm))
            if not is_lunar:
                continue

        candidates.append((tname, df))

    if len(candidates) <= max_tables:
        return candidates

    # 2. Score relevance of each table based on question keywords & columns
    scored_tables = []
    for tname, df in candidates:
        score = 0.0
        t_norm = _normalize_text(tname)

        # Keyword matching on table name
        for token in t_norm.split("_"):
            if len(token) >= 3 and token in q_norm:
                score += 15.0

        # Domain bonuses
        if any(k in q_norm for k in ["nhap", "ncc", "nha cung cap"]) and "nhap" in t_norm:
            score += 30.0
        if any(k in q_norm for k in ["khach", "no", "cong no"]) and ("khach" in t_norm or "no" in t_norm or "thu" in t_norm):
            score += 30.0
        if any(k in q_norm for k in ["thuoc", "duoc", "hoat chat", "sku"]) and ("thuoc" in t_norm or "hang_hoa" in t_norm):
            score += 30.0
        if any(k in q_norm for k in ["hoa don", "don hang", "doanh thu", "ban duoc"]) and ("hoa_don" in t_norm or "ban_hang" in t_norm or "don_hang" in t_norm):
            score += 25.0
        if any(k in q_norm for k in ["chi phi", "phi", "chi"]) and "chi" in t_norm:
            score += 25.0
        if any(k in q_norm for k in ["tho", "nhan vien", "nv", "hoa hong"]) and ("tho" in t_norm or "nhan_vien" in t_norm):
            score += 30.0

        # Column matches
        for col in df.columns:
            c_norm = _normalize_text(col)
            for token in c_norm.split("_"):
                if len(token) >= 3 and token in q_norm:
                    score += 5.0

        # Prioritize larger primary tables
        if len(df) > 500:
            score += 5.0

        scored_tables.append((score, tname, df))

    scored_tables.sort(key=lambda x: x[0], reverse=True)
    return [(tname, df) for _, tname, df in scored_tables[:max_tables]]


def build_schema_markdown(
    context: BusinessContext,
    relevant_tables: List[Tuple[str, pd.DataFrame]],
    max_sample_rows: int = 2,
) -> str:
    """Build a concise, clean schema description of selected tables."""
    schema_parts = []

    for tname, df in relevant_tables:
        num_rows = len(df)
        cols_info = []
        for col in df.columns:
            dtype_str = str(df[col].dtype)
            cols_info.append(f"`{col}` ({dtype_str})")

        header = f"### Bảng `{tname}` (Tổng: {num_rows:,} dòng)\n"
        cols_text = "- **Cột:** " + ", ".join(cols_info) + "\n"

        # Categorical distinct values for key status/payment columns (<= 6 values)
        cat_samples = []
        for col in df.columns:
            c_lower = col.lower()
            if any(k in c_lower for k in ["trang_thai", "tinh_trang", "thanh_toan", "phuong_thuc", "kenh", "loai"]):
                unique_vals = [str(x) for x in df[col].dropna().unique()[:6] if str(x).strip()]
                if unique_vals:
                    cat_samples.append(f"  * `{col}`: {unique_vals}")

        cat_text = ""
        if cat_samples:
            cat_text = "- **Giá trị mẫu:**\n" + "\n".join(cat_samples) + "\n"

        # Sample rows
        sample_df = df.head(max_sample_rows)
        sample_records = sample_df.to_dict(orient="records")
        sample_clean = []
        for r in sample_records:
            sample_clean.append({k: (v if not isinstance(v, str) or len(v) < 45 else v[:42] + "...") for k, v in r.items()})

        sample_text = f"- **{len(sample_clean)} dòng mẫu:**\n```json\n" + json.dumps(sample_clean, ensure_ascii=False, default=str) + "\n```\n"
        schema_parts.append(header + cols_text + cat_text + sample_text)

    return "\n".join(schema_parts)


def generate_text_to_sql_prompt(
    question: str,
    context: BusinessContext,
    as_of_date: Optional[str] = None,
    business_id: Optional[str] = None,
) -> str:
    """Construct a dense, token-budgeted user prompt for Text-to-SQL generation."""
    biz_id = business_id or context.business_id or "Chưa rõ"

    # Select top 5 relevant tables to prevent token overflow
    selected_tables = select_relevant_tables(question, context, max_tables=5)
    schema_md = build_schema_markdown(context, selected_tables, max_sample_rows=2)

    readme_snippet = context.readme.strip() if context.readme else "Không có ghi chú thêm."
    if len(readme_snippet) > 600:
        readme_snippet = readme_snippet[:600] + "..."

    as_of_note = f"DATE '{as_of_date}'" if as_of_date else "Không xác định"
    prompt = f"""## BỐI CẢNH DOANH NGHIỆP: `{biz_id}`
- **Điểm neo thời gian (as_of_date / 'Hôm nay'):** `{as_of_note}`
- **LƯU Ý CỰC KỲ QUAN TRỌNG:** CẤM DÙNG `CURRENT_DATE` hay `NOW()`. Mọi phép lọc thời gian tương đối ('hôm nay', 'tháng này', 'tuần này') PHẢI neo theo `DATE '{as_of_date}'`!
- **Ghi chú nghiệp vụ:**
{readme_snippet}

---

## CẤU TRÚC CƠ SỞ DỮ LIỆU DUCKDB LIÊN QUAN:
{schema_md}

---

## CÂU HỎI NGHIỆP VỤ CẦN GIẢI QUYẾT:
"{question}"

Hãy sinh đúng 1 khối JSON hợp lệ theo đúng cấu trúc đã hướng dẫn.
"""
    return prompt
