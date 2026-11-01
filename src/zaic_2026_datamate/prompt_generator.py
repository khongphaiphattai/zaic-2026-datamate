"""
prompt_generator.py: Generates dense, schema-grounded Text-to-SQL prompts
for Qwen2.5-Coder-7B/14B in ZAIC 2026 DataMate.

Injects:
1. Business domain notes from README.md
2. Full DuckDB table schemas with data types, row counts, and sample records
3. Distinct categorical values for status, payment, and channel columns
4. Core ZAIC 2026 business invariants (as_of_date, returns, delivery, AOV)
5. Strict JSON output contract.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

import pandas as pd

from zaic_2026_datamate.data_loader import BusinessContext

SYSTEM_PROMPT = """Bạn là Chuyên gia Dữ liệu & Text-to-SQL (Lead Data Agent) tham gia Zalo AI Challenge 2026 (Track 2: DataMate).
Nhiệm vụ của bạn: Dịch câu hỏi nghiệp vụ tiếng Việt thành câu lệnh SQL DuckDB chính xác tuyệt đối, hoặc từ chối trả lời nếu thiếu thông tin theo triết lý 'Silence is Free'.

Các quy tắc nghiệp vụ bất biến theo Rulebook 2026:
1. ĐIỂM NEO THỜI GIAN (as_of_date):
   - 'hôm nay', 'hnay', 'bữa nay' = as_of_date.
   - 'hôm qua', 'hqua' = as_of_date - 1 ngày.
   - 'tháng này' = từ ngày 1 của tháng chứa as_of_date đến hết as_of_date.
   - 'tuần này' = từ Thứ Hai của tuần chứa as_of_date đến hết as_of_date.
   - 'tuần trước' = trọn vẹn từ Thứ Hai đến Chủ Nhật của tuần liền trước as_of_date.
   - 'từ đầu năm / YTD' = từ 01/01 của năm chứa as_of_date đến hết as_of_date.
   - CẤM tính các giao dịch sau as_of_date (coi như chưa xảy ra).

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
   - Nếu câu hỏi mơ hồ, thiếu dữ kiện, hoặc độ tự tin < 90% -> type: 'abstain'.

BẮT BUỘC TRẢ VỀ JSON HỢP LỆ:
{
  "thought": "Phân tích ngắn gọn logic, bảng cần dùng và điều kiện lọc",
  "type": "number" | "percent" | "list" | "date" | "string" | "table" | "unanswerable" | "abstain",
  "sql": "SELECT ... (hoặc null nếu unanswerable/abstain)",
  "confidence": 0.95,
  "reason": "lý do nếu unanswerable hoặc abstain"
}
"""


def build_schema_markdown(context: BusinessContext, max_sample_rows: int = 2) -> str:
    """Build a concise, clean schema description of all tables in DuckDB."""
    schema_parts = []

    for tname, df in context.tables.items():
        if df is None or df.empty:
            continue

        num_rows = len(df)
        cols_info = []
        for col in df.columns:
            dtype_str = str(df[col].dtype)
            cols_info.append(f"`{col}` ({dtype_str})")

        header = f"### Bảng `{tname}` (Tổng cộng: {num_rows:,} dòng)\n"
        cols_text = "- **Các cột:** " + ", ".join(cols_info) + "\n"

        # Categorical distinct values for key status/payment columns (<= 8 unique values)
        cat_samples = []
        for col in df.columns:
            c_lower = col.lower()
            if any(k in c_lower for k in ["trang_thai", "tinh_trang", "thanh_toan", "phuong_thuc", "kenh", "loai"]):
                unique_vals = [str(x) for x in df[col].dropna().unique()[:8] if str(x).strip()]
                if unique_vals:
                    cat_samples.append(f"  * `{col}` có các giá trị: {unique_vals}")

        cat_text = ""
        if cat_samples:
            cat_text = "- **Giá trị mẫu cột phân loại:**\n" + "\n".join(cat_samples) + "\n"

        # Sample rows (compact Markdown or JSON-like records)
        sample_df = df.head(max_sample_rows)
        sample_records = sample_df.to_dict(orient="records")
        sample_clean = []
        for r in sample_records:
            # Shorten any massive text
            sample_clean.append({k: (v if not isinstance(v, str) or len(v) < 60 else v[:57] + "...") for k, v in r.items()})

        sample_text = f"- **{len(sample_clean)} dòng mẫu đầu tiên:**\n```json\n" + json.dumps(sample_clean, ensure_ascii=False, default=str) + "\n```\n"

        schema_parts.append(header + cols_text + cat_text + sample_text)

    return "\n".join(schema_parts)


def generate_text_to_sql_prompt(
    question: str,
    context: BusinessContext,
    as_of_date: Optional[str] = None,
    business_id: Optional[str] = None,
) -> str:
    """Construct the full user prompt for Text-to-SQL generation."""
    biz_id = business_id or context.business_id or "Chưa rõ"
    readme_snippet = context.readme.strip() if context.readme else "Không có ghi chú thêm."
    if len(readme_snippet) > 800:
        readme_snippet = readme_snippet[:800] + "..."

    schema_md = build_schema_markdown(context, max_sample_rows=2)

    prompt = f"""## BỐI CẢNH DOANH NGHIỆP: `{biz_id}`
- **Điểm neo thời gian (as_of_date / 'Hôm nay'):** `{as_of_date or 'Không xác định'}`
- **Ghi chú nghiệp vụ (từ README của doanh nghiệp):**
{readme_snippet}

---

## CẤU TRÚC CƠ SỞ DỮ LIỆU DUCKDB HIỆN CÓ:
{schema_md}

---

## CÂU HỎI NGHIỆP VỤ CẦN GIẢI QUYẾT:
"{question}"

Hãy sinh đúng 1 khối JSON hợp lệ theo đúng cấu trúc đã hướng dẫn.
"""
    return prompt
