"""
multi_month_revenue.py: Specialized Deterministic Engine for Multi-Month Sales & Growth
for Zalo AI Challenge 2026 (Track 2: DataMate).

Handles:
1. Ordered 3-month revenue lists (type: 'list', e.g. [233818700, 248568700, 237185200])
2. Peak / Highest revenue month among 3 consecutive months (type: 'date', e.g. '2025-07')
3. Strict monotonic continuous growth check across 3 months (type: 'boolean', True / False)

Uses ANSI SQL execution on DuckDB with whole-order cancellation filtering per Rulebook §1.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from typing import Any

from zaic_2026_datamate.cleaner import normalize_query_telex
from zaic_2026_datamate.data_loader import BusinessContext
from zaic_2026_datamate.intent_parser import (
    find_header_detail_revenue_spec,
    find_primary_sales_table,
    find_revenue_column,
    get_valid_sales_status_clause,
)

logger = logging.getLogger(__name__)


def extract_consecutive_months(
    text: str, as_of_date: str | None = None
) -> list[str] | None:
    """Extract exactly 3 consecutive months from query text."""
    t = normalize_query_telex(text).lower()

    # 1. Range: 'từ th12/2025 đến th2/2026' or 'từ T4/2025 tới T6/2025' or 'T3/2026 tới T5/2026'
    m_range = re.search(
        r"(?:t(?:ừ|u)\s+)?(?:tháng|thang|th|t)?\s*(\d{1,2})[/.-](\d{2,4})\s*(?:tới|toi|đến|den)\s*(?:tháng|thang|th|t)?\s*(\d{1,2})[/.-](\d{2,4})",
        t,
    )
    if m_range:
        m_start, y_start = int(m_range.group(1)), int(m_range.group(2))
        m_end, y_end = int(m_range.group(3)), int(m_range.group(4))
        if y_start < 100:
            y_start += 2000
        if y_end < 100:
            y_end += 2000
        months = []
        cur_y, cur_m = y_start, m_start
        while (cur_y, cur_m) <= (y_end, m_end):
            months.append(f"{cur_y:04d}-{cur_m:02d}")
            cur_m += 1
            if cur_m > 12:
                cur_m = 1
                cur_y += 1
        if len(months) == 3:
            return months

    # 2. Ending: 'tính tới 3-2026', 'kết thúc ở tháng 6/2025', etc.
    m_ending = re.search(
        r"(?:t(?:ính|inh)\s*t(?:ới|oi)|(?:kết|ket)\s*th[úu]c\s*(?:ở|o|tại|tai|vào|vao)?)\s*(?:tháng|thang|th|t)?\s*(\d{1,2})[/.-](\d{2,4})",
        t,
    )
    if m_ending:
        end_m, end_y = int(m_ending.group(1)), int(m_ending.group(2))
        if end_y < 100:
            end_y += 2000
        months = []
        for i in range(2, -1, -1):
            m = end_m - i
            y = end_y
            if m <= 0:
                m += 12
                y -= 1
            months.append(f"{y:04d}-{m:02d}")
        return months

    # 3. Explicit month strings (unique set check for comparison questions with multiple month mentions)
    m_list = re.findall(r"(?:tháng|thang|th|t)?\s*(\d{1,2})[/.-](\d{2,4})", t)
    if m_list:
        m_set: list[str] = []
        for m, y in m_list:
            m, y = int(m), int(y)
            if not (1 <= m <= 12):
                continue
            if y < 100:
                y += 2000
            if not (2024 <= y <= 2027):
                continue
            m_str = f"{y:04d}-{m:02d}"
            if m_str not in m_set:
                m_set.append(m_str)
        if len(m_set) == 3:
            return sorted(m_set)

    # 4. Same year list: 'các tháng 10, 11, 12 năm 2025' or 'tháng 3, tháng 4, tháng 5 năm 2025' or 'th9, th10, th11/2025'
    m_same = re.search(
        r"(?:các\s+tháng|tháng|th|t)?\s*(\d{1,2})\s*,\s*(?:tháng|thang|th|t)?\s*(\d{1,2})\s*(?:,|và|va|rồi|roi)?\s*(?:tháng|thang|th|t)?\s*(\d{1,2})\s*(?:[/.-]|năm|nam)?\s*(\d{2,4})",
        t,
    )
    if m_same:
        m1, m2, m3, y = (
            int(m_same.group(1)),
            int(m_same.group(2)),
            int(m_same.group(3)),
            int(m_same.group(4)),
        )
        if y < 100:
            y += 2000
        return [f"{y:04d}-{m1:02d}", f"{y:04d}-{m2:02d}", f"{y:04d}-{m3:02d}"]

    # 5. Month numbers with as_of_date: 'th4, th5, th6'
    m_short = re.search(
        r"(?:tháng|thang|th|t)\s*(\d{1,2})\s*,\s*(?:tháng|thang|th|t)?\s*(\d{1,2})\s*(?:,|và|va|rồi|roi)?\s*(?:tháng|thang|th|t)?\s*(\d{1,2})",
        t,
    )
    if m_short and as_of_date:
        y = int(as_of_date[:4])
        m1, m2, m3 = int(m_short.group(1)), int(m_short.group(2)), int(m_short.group(3))
        return [f"{y:04d}-{m1:02d}", f"{y:04d}-{m2:02d}", f"{y:04d}-{m3:02d}"]

    return None


def get_month_clean_revenue(context: BusinessContext, month_str: str) -> int | None:
    """Execute clean store revenue for a specific month on DuckDB."""
    table, order_col, date_col, status_col = find_primary_sales_table(context)
    if not table or not date_col:
        return None
    hd_spec = find_header_detail_revenue_spec(context, table, order_col)
    if hd_spec:
        ct_order_col, ct_rev = hd_spec
        d_status_clause = get_valid_sales_status_clause(
            status_col, order_col=order_col, table=table, table_alias="d"
        )
        sql = f"""
            SELECT SUM(c.{ct_rev})
            FROM chi_tiet_don c
            JOIN {table} d ON c.{ct_order_col} = d.{order_col}
            WHERE strftime(TRY_CAST(d.{date_col} AS DATE), '%Y-%m') = '{month_str}'
              {d_status_clause}
        """
    else:
        rev_col = find_revenue_column(context, table)
        if not rev_col:
            return None

        status_clause = get_valid_sales_status_clause(
            status_col, order_col=order_col, table=table
        )
        sql = f"""
            SELECT SUM({rev_col})
            FROM {table}
            WHERE strftime(TRY_CAST({date_col} AS DATE), '%Y-%m') = '{month_str}'
              {status_clause}
        """
    try:
        val = context.scalar(sql)
        return int(round(val)) if val is not None else 0
    except Exception as e:
        logger.warning(f"Failed to query month revenue for {month_str}: {e}")
        return None


def resolve_multi_month_query(
    question: str,
    context: BusinessContext,
    as_of_date: str | None = None,
) -> tuple[str, Any, str | None]:
    """Resolve queries asking about 3 consecutive months revenue."""
    question_clean = normalize_query_telex(question)
    q_lower = question_clean.lower()

    # Guard: must be asking for revenue / sales
    has_rev = any(
        k in q_lower
        for k in [
            "dthu",
            "doanh thu",
            "revenue",
            "thu về",
            "bán được",
            "ban duoc",
            "tiền",
        ]
    )
    if not has_rev:
        return "abstain", None, "Not a multi-month revenue query"

    # Avoid specific item/category filters
    if any(
        k in q_lower
        for k in [
            "mặt hàng",
            "mat hang",
            "món",
            "mon",
            "sản phẩm",
            "san pham",
            "thuốc",
            "thuoc",
            "khách",
            "khach",
            "nhân viên",
            "nhan vien",
        ]
    ):
        return "abstain", None, "Filtered multi-month query abstained"

    # Guard: Price change / event impact questions
    if any(
        k in q_lower
        for k in [
            "đổi giá",
            "doi gia",
            "lên giá",
            "len gia",
            "tăng giá",
            "tang gia",
            "hạ giá",
            "ha gia",
            "giảm giá",
            "giam gia",
        ]
    ):
        return "abstain", None, "Price change question abstained"

    months = extract_consecutive_months(question, as_of_date)
    if not months or len(months) != 3:
        return "abstain", None, "Could not extract 3 consecutive months"

    # Calculate revenues for all 3 months
    revs = [get_month_clean_revenue(context, m) for m in months]
    if any(r is None or r <= 0 for r in revs):
        return "abstain", None, "Could not compute positive revenue for all 3 months"

    r1, r2, r3 = revs[0], revs[1], revs[2]

    q_norm = re.sub(
        r"\s+",
        " ",
        "".join(
            ch
            for ch in unicodedata.normalize("NFD", q_lower.replace("đ", "d"))
            if unicodedata.category(ch) != "Mn"
        ),
    ).strip()

    # 1. Boolean continuous growth
    has_monotone_ask = bool(
        re.search(
            r"\b(?:co|cos)?\s*(?:tang|giam)\s+lien\s+tuc\b|\blien\s+tuc\s+(?:tang|giam)\b|\b(?:luon|deu)\s+(?:cao\s+hon|tang|giam)\b",
            q_norm,
        )
    )
    if has_monotone_ask or any(
        k in q_lower
        for k in [
            "phải ko",
            "phai ko",
            "phải không",
            "phai khong",
            "đúng vậy không",
            "dung vay khong",
            "ko ta",
            "hả",
            "hông",
            "hong",
            "khoong",
            "ko ha",
            "co tang lien tuc",
            "có tăng liên tục",
            "luôn hơn",
            "luon hon",
            "đều cao hơn",
            "deu cao hon",
        ]
    ):
        is_decreasing = bool(
            re.search(r"\bgiam\s+lien\s+tuc\b|\blien\s+tuc\s+giam\b", q_norm)
        )
        return "boolean", (r1 > r2 > r3) if is_decreasing else (r1 < r2 < r3), None

    # 2. Highest / Peak month
    if bool(
        re.search(
            r"\bthang\s+naof?\b|\b(?:cao|lon|nhieu)\s+nh(?:at|aast|ut|atf)f?\b",
            q_norm,
        )
    ) or any(
        k in q_lower
        for k in [
            "cao nhất",
            "cao nhat",
            "cao nhứt",
            "cao nhut",
            "caonhất",
            "lớn nhất",
            "lon nhat",
            "nhiều nhất",
            "nhieu nhat",
            "tháng nào",
            "thang nao",
        ]
    ):
        pairs = list(zip(months, revs))
        # Check tie
        sorted_pairs = sorted(pairs, key=lambda x: x[1], reverse=True)
        if len(sorted_pairs) >= 2 and sorted_pairs[0][1] == sorted_pairs[1][1]:
            return "abstain", None, "Tie in multi-month peak revenue"
        return "date", sorted_pairs[0][0], None

    # 3. Default: Ordered List
    return "list", [r1, r2, r3], None
