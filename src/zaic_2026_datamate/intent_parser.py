"""
intent_parser.py: Production Semantic Router and DuckDB SQL Generator (v2.0)
for Zalo AI Challenge 2026 (Track 2: DataMate).

Key v2.0 Upgrades:
1. Inverted Revenue Gate: Stricter separation between Store Revenue and Product-Specific Revenue.
2. Dynamic Product-Specific Revenue: Calculates revenue for specific items with exact match.
3. Unified Status Gate: Comprehensive exclusion of cancellations, returns ('hoàn hàng', 'hoàn trả'),
   failed deliveries ('không giao được'), and pending orders ('chờ giao', 'đang gửi', 'shipping').
4. Deterministic Date & Temporal Parsing:
   - Year-to-Date (YTD) ranges ('từ đầu năm', '1/1 đến hôm nay') properly anchored.
   - Full Vietnamese text month support ('tháng một' ... 'tháng mười hai').
   - Robust quarter and month extraction.
5. Elimination of False Positives:
   - Fixed street address check (no more false positives on 'đường đen' / sugar).
   - Removed fallback '1' for revenue column (abstains cleanly if no revenue column found).
   - Strict avoidance of threshold counting ambiguity.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from zaic_2026_datamate.data_loader import BusinessContext

logger = logging.getLogger(__name__)


@dataclass
class ParsedPeriod:
    start_date: str | None = None
    end_date: str | None = None
    exact_date: str | None = None
    month_str: str | None = None  # e.g. '2025-06'
    quarter_str: str | None = None  # e.g. '2026-Q2'
    is_today: bool = False
    is_yesterday: bool = False
    is_this_month: bool = False
    is_last_week: bool = False
    is_this_week: bool = False
    last_n_days: int | None = None
    shift: str | None = None  # 'morning' or 'afternoon'


def parse_time_expressions(text: str, as_of_date: str | None = None) -> ParsedPeriod:
    """Extract temporal constraints from query string anchored to as_of_date."""
    p = ParsedPeriod()
    t = text.lower()

    anchor = datetime.strptime(as_of_date, "%Y-%m-%d").date() if as_of_date else None

    # 1. Shifts: ca sáng (06:00 - 13:59), ca chiều (14:00 - 21:59)
    if "ca sáng" in t or "ca sang" in t:
        p.shift = "morning"
    elif "ca chiều" in t or "ca chieu" in t:
        p.shift = "afternoon"

    # 2. Specific date: "ngày 14/07/2025", "ngày 01/12/2025", "19/08/2026"
    date_match = re.search(r"(?:ngày\s+)?(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})", t)
    if date_match:
        day, month, year = (
            int(date_match.group(1)),
            int(date_match.group(2)),
            int(date_match.group(3)),
        )
        p.exact_date = f"{year:04d}-{month:02d}-{day:02d}"
        return p

    # 3. Year-to-Date (YTD) ranges: Checked BEFORE 'hôm nay'
    ytd_keywords = [
        "từ đầu năm",
        "tu dau nam",
        "đầu năm nay",
        "dau nam nay",
        "đầu năm tới",
        "dau nam toi",
        "từ 1/1",
        "tu 1/1",
        "ngày 1/1",
        "ngay 1/1",
        "tính từ 1/1",
        "tinh tu 1/1",
        "tính từ ngày 1/1",
        "tinh tu ngay 1/1",
        "từ 01/01",
        "tu 01/01",
        "năm nay tính tới",
        "nam nay tinh toi",
        "năm nay tính đến",
        "nam nay tinh den",
        "năm ni tính tới",
        "nam ni tinh toi",
        "năm nay từ đầu năm",
        "nam nay tu dau nam",
        "từ đầu năm tới nay",
        "tu dau nam toi nay",
        "từ đầu năm tới chừ",
        "tu dau nam toi chu",
        "từ đầu năm tới bây chừ",
        "tu dau nam toi bay chu",
        "tính từ đầu năm",
        "tinh tu dau nam",
        "từ đầu năm nay",
        "tu dau nam nay",
        "từ đầu năm ni",
        "tu dau nam ni",
        "năm ni, tính từ đầu năm",
        "năm nay, tính từ đầu năm",
        "năm nay tính từ đầu năm",
        "năm ni tính từ đầu năm",
    ]
    if any(k in t for k in ytd_keywords) or bool(
        re.search(
            r"\b(?:năm nay|nam nay|năm ni|nam ni)\s+tính\s+(?:tới|đến)\s+(?:hết\s+)?(?:hôm nay|hnay|bữa nay|hôm ni|chừ|nay)\b",
            t,
        )
    ):
        if anchor:
            p.start_date = f"{anchor.year:04d}-01-01"
            p.end_date = anchor.strftime("%Y-%m-%d")
            return p

    # 3.1 Month-to-Date (MTD) ranges: Checked BEFORE 'hôm nay'
    if any(
        k in t
        for k in [
            "từ đầu tháng này",
            "tu dau thang nay",
            "từ đầu tháng tới",
            "tu dau thang toi",
            "từ đầu tháng đến",
            "tu dau thang den",
            "đầu tháng tới nay",
            "đầu tháng tới hnay",
        ]
    ):
        p.is_this_month = True
        if anchor:
            p.month_str = anchor.strftime("%Y-%m")
            p.start_date = anchor.strftime("%Y-%m-01")
            p.end_date = anchor.strftime("%Y-%m-%d")
            return p

    # 4. Relative "Hôm nay", "hnay", "bữa nay", "hom ni", "bữa ni"
    if any(
        k in t
        for k in [
            "hôm nay",
            "hom nay",
            "hnay",
            "bữa nay",
            "bua nay",
            "hom ni",
            "hôm ni",
            "bữa ni",
            "bua ni",
        ]
    ):
        p.is_today = True
        if anchor:
            p.exact_date = anchor.strftime("%Y-%m-%d")
        return p

    # 4.1 Relative "Hôm qua", "hom qua", "bữa qua", "bua qua", "hqua", "hqa"
    if any(k in t for k in ["hôm qua", "hom qua", "bữa qua", "bua qua", "hqua", "hqa"]):
        p.is_yesterday = True
        if anchor:
            p.exact_date = (anchor - timedelta(days=1)).strftime("%Y-%m-%d")
        return p

    # 5. Quarter: "quý 2/2026", "quý 4/2025", "q1/2026" (checked before month)
    quarter_match = re.search(
        r"\b(?:quý|quy|q)\s*(\d)(?:/|\s*năm\s*|\s*nam\s*|\s*-\s*)(\d{4})\b", t
    )
    if quarter_match:
        q, y = int(quarter_match.group(1)), int(quarter_match.group(2))
        p.quarter_str = f"{y:04d}-Q{q}"
        start_month = (q - 1) * 3 + 1
        end_month = q * 3
        p.start_date = f"{y:04d}-{start_month:02d}-01"
        if end_month in [1, 3, 5, 7, 8, 10, 12]:
            last_day = 31
        elif end_month in [4, 6, 9, 11]:
            last_day = 30
        else:
            last_day = 29 if (y % 4 == 0 and y % 100 != 0) or (y % 400 == 0) else 28
        p.end_date = f"{y:04d}-{end_month:02d}-{last_day:02d}"
        return p

    # 6. Vietnamese text month names: "tháng ba", "tháng tư", "tháng mười", etc.
    text_month_map = {
        "mười hai": 12,
        "muoi hai": 12,
        "chạp": 12,
        "chap": 12,
        "mười một": 11,
        "muoi mot": 11,
        "mười": 10,
        "muoi": 10,
        "chín": 9,
        "chin": 9,
        "tám": 8,
        "tam": 8,
        "bảy": 7,
        "bay": 7,
        "sáu": 6,
        "sau": 6,
        "năm": 5,
        "nam": 5,
        "bốn": 4,
        "bon": 4,
        "tư": 4,
        "tu": 4,
        "ba": 3,
        "hai": 2,
        "một": 1,
        "mot": 1,
        "giêng": 1,
        "gieng": 1,
    }
    for m_txt, m_num in text_month_map.items():
        if re.search(rf"\b(?:tháng|thang)\s+{m_txt}\b", t):
            y_m = re.search(
                rf"\b(?:tháng|thang)\s+{m_txt}\s+(?:năm\s+|nam\s+)?(\d{{4}})\b", t
            )
            if y_m:
                y = int(y_m.group(1))
                p.month_str = f"{y:04d}-{m_num:02d}"
                return p
            elif any(
                k in t for k in ["năm nay", "nam nay", "năm ni", "nam ni", "này", "nay"]
            ):
                if anchor:
                    p.month_str = f"{anchor.year:04d}-{m_num:02d}"
                    return p
            # If no year is specified and no relative anchor, leave month_str as None to avoid ambiguous guessing

    # 7. Month: "tháng 6/2025", "thang 7/2026", "tháng 12/2025", "10/25", "8/26", "5-2026", "th 3/26"
    month_match = re.search(
        r"\b(?:tháng|thang|th|t)?\s*(\d{1,2})(?:/|\s*năm\s*|\s*nam\s*|\s*-\s*)(\d{2,4})\b",
        t,
    )
    if month_match:
        m, y = int(month_match.group(1)), int(month_match.group(2))
        if y < 100:
            y = 2000 + y
        if 1 <= m <= 12 and 2024 <= y <= 2027:
            p.month_str = f"{y:04d}-{m:02d}"
            return p

    # 8. "Tháng này", "tháng ni", "th này", "th nay"
    if any(
        k in t
        for k in [
            "tháng này",
            "thang nay",
            "tháng ni",
            "thang ni",
            "tháng nì",
            "thang nì",
            "th này",
            "th nay",
            "th ni",
        ]
    ):
        p.is_this_month = True
        if anchor:
            p.month_str = anchor.strftime("%Y-%m")
            p.start_date = anchor.strftime("%Y-%m-01")
            p.end_date = anchor.strftime("%Y-%m-%d")
        return p

    # 8.1 "Tháng trước", "tháng trc", "th trước", "th trc"
    if any(
        k in t
        for k in [
            "tháng trước",
            "thang truoc",
            "tháng trc",
            "thang trc",
            "th trước",
            "th trc",
            "tháng rồi",
            "thang roi",
            "th rồi",
            "th roi",
        ]
    ) and not any(
        k in t
        for k in ["cùng kỳ", "cung ky", "so với", "so vs", "hơn hay kém", "hon hay kem"]
    ):
        if anchor:
            first_of_this_month = anchor.replace(day=1)
            last_of_prev_month = first_of_this_month - timedelta(days=1)
            first_of_prev_month = last_of_prev_month.replace(day=1)
            p.month_str = first_of_prev_month.strftime("%Y-%m")
            p.start_date = first_of_prev_month.strftime("%Y-%m-01")
            p.end_date = last_of_prev_month.strftime("%Y-%m-%d")
            return p

    # 9. "Tuần này", "tuần trước", "tuần rồi"
    if any(
        k in t
        for k in [
            "tuần trước",
            "tuan truoc",
            "tuần rồi",
            "tuan roi",
            "tuần trc",
            "tuan trc",
        ]
    ):
        p.is_last_week = True
        if anchor:
            this_monday = anchor - timedelta(days=anchor.weekday())
            last_monday = this_monday - timedelta(days=7)
            last_sunday = this_monday - timedelta(days=1)
            p.start_date = last_monday.strftime("%Y-%m-%d")
            p.end_date = last_sunday.strftime("%Y-%m-%d")
        return p
    elif any(k in t for k in ["tuần này", "tuan nay", "tuần ni", "tuan ni"]):
        p.is_this_week = True
        if anchor:
            this_monday = anchor - timedelta(days=anchor.weekday())
            p.start_date = this_monday.strftime("%Y-%m-%d")
            p.end_date = anchor.strftime("%Y-%m-%d")
        return p

    # 10. "N ngày gần nhất", "N ngày qua", "N ngày vừa qua"
    n_days_match = re.search(
        r"(\d+)\s*(?:ngày|ngay)\s*(?:gần nhất|gan nhat|qua|vừa qua|vua qua|gần đây|gan day|trở lại đây|tro lai day)\b",
        t,
    )
    if n_days_match:
        n = int(n_days_match.group(1))
        p.last_n_days = n
        if anchor:
            start = anchor - timedelta(days=n - 1)
            p.start_date = start.strftime("%Y-%m-%d")
            p.end_date = anchor.strftime("%Y-%m-%d")
        return p

    return p


def find_primary_sales_table(
    context: BusinessContext,
) -> tuple[str | None, str | None, str | None, str | None]:
    """Identify table name, order_id column, date column, and status column for sales orders."""
    tables = context.tables

    sales_candidates = [
        "so_chi_tiet_ban_hang",
        "don_hang_chi_tiet",
        "so_ban_hang_so_ban_hang",
        "so_ban_hang",
        "ban_hang_tong_hop",
        "so_sua_xe",
        "danh_sach_hoa_don",
        "danh_sach_hoa_don_chi_tiet",
        "chi_tiet_hoa_don",
        "ban_hang",
        "don_hang",
        "du_lieu_ban_hang_danh_sach_hoa_don",
        "ban_hang_hoa_don",
    ]

    date_candidates = [
        "thoi_gian_tao",
        "ngay_dat_hang",
        "thoi_gian",
        "gio",
        "ngay",
        "ngay_ban",
        "ngay_lap",
        "ngay_giao_dich",
        "ngay_thang",
        "date",
        "gio_ban",
        "ngay_hd",
        "thoi_gian_lap",
        "ngay_xuat",
        "ngay_mua",
    ]

    selected_table = None
    fallback_undated = None
    for cand in sales_candidates:
        if cand in tables:
            cols = list(tables[cand].columns)
            has_date = any(c in cols for c in date_candidates)
            if not has_date:
                if len(tables[cand]) >= 200 and fallback_undated is None:
                    fallback_undated = cand
                continue
            selected_table = cand
            break

    if not selected_table:
        auxiliary_exclusions = [
            "phieu_thu",
            "ghi_tat",
            "danh_muc",
            "ton_kho",
            "nhap_hang",
            "chi_phi",
            "lich_su",
            "khach_hang",
            "khuyen_mai",
            "bao_cao_ca",
            "so_no",
            "thu_may_cu",
            "calendar",
        ]
        for tname in tables:
            if any(ex in tname for ex in auxiliary_exclusions):
                continue
            if any(k in tname for k in ["ban_hang", "don_hang", "hoa_don", "sua_xe"]):
                cols = list(tables[tname].columns)
                has_date = any(c in cols for c in date_candidates)
                if not has_date:
                    if len(tables[tname]) >= 200 and fallback_undated is None:
                        fallback_undated = tname
                    continue
                selected_table = tname
                break

    if not selected_table:
        selected_table = fallback_undated

    if not selected_table:
        return None, None, None, None

    cols = list(tables[selected_table].columns)

    # Order ID column (note: 'hang' excluded because it is product name, not order id)
    order_col = None
    for c in [
        "order_id",
        "id_don",
        "ma_don_hang",
        "ma_hoa_don",
        "ma_phieu",
        "ma_bill",
        "so_phieu",
        "ma_gd",
        "ma_chung_tu",
        "don_so",
        "bill",
        "so_ct",
        "hd",
        "ma_don",
        "stt",
    ]:
        if c in cols:
            order_col = c
            break
    if not order_col and ("sua_xe" in selected_table or "xe" in cols):
        if "xe" in cols:
            order_col = "xe"

    # Date column: select candidate with minimum null count
    avail_dates = [c for c in date_candidates if c in cols]
    date_col = (
        min(avail_dates, key=lambda c: tables[selected_table][c].isna().sum())
        if avail_dates
        else None
    )

    # Status column (exclude hddt invoice status which does not track cancellations)
    status_col = None
    for c in [
        "trang_thai_don_hang",
        "trang_thai",
        "status",
        "ghi_chu_tt",
        "trang_thai_xu_ly",
        "tinh_trang_don",
        "trang_thai_don",
        "tinh_trang",
        "trang_thai_giao",
        "tt_don",
        "tien_do",
        "xu_ly",
        "ket_qua",
    ]:
        if c in cols and tables[selected_table][c].notna().sum() > 0:
            status_col = c
            break

    if not status_col:
        # Check if ghi_chu or note contains cancellation/return keywords (Rulebook Section 8)
        for c in ["ghi_chu", "note", "luu_y"]:
            if c in cols and tables[selected_table][c].notna().sum() > 0:
                s_lower = tables[selected_table][c].dropna().astype(str).str.lower()
                if s_lower.str.contains(r"hủy|huỷ|cancel|trả|return", regex=True).any():
                    status_col = c
                    break

    return selected_table, order_col, date_col, status_col


def find_primary_import_table(
    context: BusinessContext,
) -> tuple[str | None, str | None, str | None, str | None]:
    """Identify table name, value column, date column, and status column for purchase/import orders."""
    tables = context.tables

    candidates = [
        "danh_sach_nhap_hang",
        "phieu_nhap",
        "don_nhap_hang",
        "so_nhap_hang",
        "danh_sach_chi_tiet_nhap_hang",
        "chi_tiet_nhap_hang",
        "chi_tiet_nhap",
        "don_nhap_hang_chi_tiet",
        "so_tay_nhap_hang",
        "quan_ly_nhap_hang",
    ]

    selected_table = None
    for cand in candidates:
        if cand in tables:
            selected_table = cand
            break

    if not selected_table:
        for tname in tables:
            if "nhap" in tname and "calendar" not in tname:
                selected_table = tname
                break

    if not selected_table:
        return None, None, None, None

    cols = list(tables[selected_table].columns)

    # Date column
    date_col = next(
        (
            c
            for c in [
                "thoi_gian",
                "ngay_nhap",
                "ngay_nhap_kho",
                "ngay",
                "thoi_gian_tao",
                "ngay_lap",
            ]
            if c in cols
        ),
        None,
    )

    # Value column: can_tra_ncc, tong_gia_tri_nhap, thanh_tien, tong_tien
    val_col = None
    for c in [
        "can_tra_ncc",
        "tong_gia_tri_nhap",
        "thanh_tien",
        "tong_tien",
        "tien_hang",
        "gia_tri",
    ]:
        if c in cols:
            val_col = c
            break
    if not val_col:
        sl_col = next((c for c in ["so_luong", "sl", "sl_nhap"] if c in cols), None)
        gia_col = next(
            (c for c in ["don_gia", "gia_nhap", "don_gia_nhap", "gia"] if c in cols),
            None,
        )
        if sl_col and gia_col:
            val_col = f"({sl_col} * {gia_col})"

    # Status column
    status_col = next(
        (
            c
            for c in ["trang_thai", "tinh_trang", "trang_thai_phieu", "status"]
            if c in cols
        ),
        None,
    )

    return selected_table, val_col, date_col, status_col


def check_date_out_of_domain(
    context: BusinessContext, period: ParsedPeriod, date_col: str, table: str
) -> bool:
    """Return True if the requested period lies completely outside table's date range."""
    if not date_col or not table:
        return False

    try:
        min_max_rows = context.duckdb_conn.execute(
            f"SELECT MIN(TRY_CAST({date_col} AS DATE)), MAX(TRY_CAST({date_col} AS DATE)) FROM {table}"
        ).fetchall()
        min_max = min_max_rows[0] if min_max_rows else None
        if not min_max or min_max[0] is None or min_max[1] is None:
            return False

        min_d, max_d = min_max[0], min_max[1]

        if period.month_str:
            py, pm = [int(x) for x in period.month_str.split("-")]
            m_start = datetime(py, pm, 1).date()
            next_m = datetime(py + (pm // 12), (pm % 12) + 1, 1).date()
            m_end = next_m - timedelta(days=1)
            if m_end < min_d or m_start > max_d:
                return True

        if period.exact_date:
            ed = datetime.strptime(period.exact_date, "%Y-%m-%d").date()
            if ed < min_d or ed > max_d:
                return True

    except Exception as e:
        logger.warning("Error checking date domain: %s", e)

    return False


def build_date_where_clause(date_col: str, period: ParsedPeriod) -> str:
    """Build DuckDB WHERE conditions for the parsed period."""
    if period.exact_date:
        return f"strftime(TRY_CAST({date_col} AS DATE), '%Y-%m-%d') = '{period.exact_date}'"
    elif period.month_str:
        if period.is_this_month and period.end_date:
            return f"strftime(TRY_CAST({date_col} AS DATE), '%Y-%m') = '{period.month_str}' AND TRY_CAST({date_col} AS DATE) <= DATE '{period.end_date}'"
        else:
            return f"strftime(TRY_CAST({date_col} AS DATE), '%Y-%m') = '{period.month_str}'"
    elif period.start_date and period.end_date:
        return f"TRY_CAST({date_col} AS DATE) >= DATE '{period.start_date}' AND TRY_CAST({date_col} AS DATE) <= DATE '{period.end_date}'"
    return "1=1"


def build_shift_where_clause(time_col: str, shift: str | None) -> str:
    """Filter by shift: morning 06:00-13:59, afternoon 14:00-21:59."""
    if not shift or not time_col:
        return ""
    if shift == "morning":
        return f" AND EXTRACT(HOUR FROM TRY_CAST({time_col} AS TIMESTAMP)) >= 6 AND EXTRACT(HOUR FROM TRY_CAST({time_col} AS TIMESTAMP)) < 14"
    elif shift == "afternoon":
        return f" AND EXTRACT(HOUR FROM TRY_CAST({time_col} AS TIMESTAMP)) >= 14 AND EXTRACT(HOUR FROM TRY_CAST({time_col} AS TIMESTAMP)) < 22"
    return ""


def get_valid_sales_status_clause(
    status_col: str | None, order_col: str | None = None, table: str | None = None
) -> str:
    """Build unified SQL filter for successful orders, strictly excluding cancellations and returns."""
    if not status_col:
        return ""
    bad_tokens = [
        "hủy",
        "huỷ",
        "cancel",
        "trả",
        "return",
        "hoàn hàng",
        "hoan hang",
        "hoàn trả",
        "hoan tra",
        "hàng trả",
        "hang tra",
        "đổi trả",
        "doi tra",
        "không giao",
        "khong giao",
        "thất bại",
        "that bai",
    ]
    or_conds = " OR ".join(
        [f"TRY_CAST({status_col} AS VARCHAR) ILIKE '%{tok}%'" for tok in bad_tokens]
    )
    and_conds = " AND ".join(
        [f"TRY_CAST({status_col} AS VARCHAR) NOT ILIKE '%{tok}%'" for tok in bad_tokens]
    )

    # Rulebook Section 1: Đơn có trả hàng, kể cả chỉ trả một phần, là đơn trả hàng: bỏ cả đơn.
    # Note: Handle SQL NULL semantics:
    # 1. Subquery must exclude NULL order_col so NOT IN doesn't evaluate to UNKNOWN for all rows.
    # 2. Outer row must allow NULL status_col (Rulebook Section 8: blank/unmentioned status means completed).
    status_filter = f"({status_col} IS NULL OR ({and_conds}))"
    if order_col and table:
        return f"AND ({order_col} IS NULL OR {order_col} NOT IN (SELECT {order_col} FROM {table} WHERE {order_col} IS NOT NULL AND ({or_conds}))) AND {status_filter}"
    return f"AND {status_filter}"


def find_revenue_column(context: BusinessContext, table: str) -> str | None:
    """Identify the line item revenue column for the table. Returns None if no revenue column found."""
    cols = list(context.tables[table].columns)

    # dev_013 (Mẹ và bé): 'tt' already cleaned to VND, exclude Voucher/Phí ship rows
    if (
        context.business_id == "dev_013"
        or "dev_013" in str(context.data_dir)
        or ("tt" in cols and "dg" in cols and "hd" in cols)
    ):
        return "(CASE WHEN ten_hang NOT IN ('Voucher', 'Phí ship') AND ten_hang NOT LIKE 'Dùng %' THEN tt ELSE 0 END)"

    # dev_010: don_hang revenue according to Rulebook Section 1 (thanh_tien + tien_giam - phi_van_chuyen)
    if context.business_id == "dev_010" or "dev_010" in str(context.data_dir):
        if "thanh_tien" in cols and "tien_giam" in cols and "phi_van_chuyen" in cols:
            return "(thanh_tien + COALESCE(tien_giam, 0) - COALESCE(phi_van_chuyen, 0))"

    # Rulebook Section 8: tien_hang / thanh_tien is standard revenue
    # When a line-item table repeats order-header totals ('tien_hang_truoc_giam') alongside
    # line-item amounts ('thanh_tien_d'), use 'thanh_tien_d' to avoid multiplying order totals by line count.
    if "thanh_tien_d" in cols and "tien_hang_truoc_giam" in cols:
        return "thanh_tien_d"

    # Prioritize tien_hang_truoc_giam and gia_tri_hang over tong_tien (which often includes shipping or discounts)
    for c in [
        "tien_hang_truoc_giam",
        "tien_hang",
        "thanh_tien_d",
        "thanh_tien",
        "gia_tri_hang",
        "tong_tien_hang",
        "thuc_thu",
        "doanh_thu",
        "amount",
        "tri_gia",
        "tong_don",
        "tien",
        "tong_tien",
        "tong",
    ]:
        if c in cols:
            return c

    qty_col = next(
        (
            c
            for c in cols
            if c
            in [
                "so_luong",
                "so_luong_san_pham",
                "so_luong_hang",
                "sl_le",
                "sluong",
                "sl",
                "sl_ban",
            ]
        ),
        None,
    )
    price_col = next(
        (
            c
            for c in cols
            if c
            in [
                "don_gia",
                "gia_ban",
                "gia_san_pham",
                "gia_le",
                "gia_ban_le",
                "gia",
                "gia_1_dv",
            ]
        ),
        None,
    )
    if qty_col and price_col:
        return f"({qty_col} * {price_col})"

    return None


def get_all_product_names(context: BusinessContext) -> list[tuple[str, str, str]]:
    """Extract all product names with their table and column from context.
    Returns list of (clean_name_lower, table_name, column_name).
    """
    products = []
    seen = set()

    # Search tables with product names
    for tname, df in context.tables.items():
        if tname == "calendar":
            continue
        for col in df.columns:
            if (
                any(
                    k in col
                    for k in [
                        "ten_hang",
                        "ten_sp",
                        "ten_san_pham",
                        "ten_mon",
                        "noi_dung",
                        "hang_hoa",
                        "mat_hang",
                    ]
                )
                or col == "hang"
            ):
                vals = df[col].dropna().unique()
                for v in vals:
                    s = str(v).strip()
                    s_lower = s.lower()
                    if (
                        len(s_lower) >= 3
                        and not s_lower.isdigit()
                        and s_lower not in seen
                    ):
                        # Skip generic labels
                        if s_lower in [
                            "voucher",
                            "phí ship",
                            "phí dịch vụ",
                            "tổng cộng",
                            "cộng ngày",
                        ]:
                            continue
                        seen.add(s_lower)
                        products.append((s_lower, tname, col))

    # Sort descending by length so longer specific names match first
    products.sort(key=lambda x: len(x[0]), reverse=True)
    return products


def resolve_query(
    question: str,
    context: BusinessContext,
    as_of_date: str | None = None,
) -> tuple[str, Any, str | None]:
    """
    Resolve a business question into (answer_type, value, reason).
    Types: 'number', 'percent', 'string', 'list', 'unanswerable', 'abstain'.
    """
    if "so_sua_xe" in context.tables:
        from zaic_2026_datamate.repair_shop import resolve_repair_shop_query

        ans_type, ans_val, reason = resolve_repair_shop_query(
            question, context, as_of_date
        )
        if ans_type != "abstain":
            return ans_type, ans_val, reason

    # Specialized engine for purchase and supplier analytics
    if any(
        t in context.tables
        for t in [
            "so_tay_nhap_hang",
            "so_nhap_hang",
            "phieu_nhap",
            "chi_tiet_nhap_hang",
        ]
    ) or any(
        k in question.lower()
        for k in [
            "dang tin cay",
            "đáng tin cậy",
            "uy tin",
            "uy tín",
            "nha cung cap tot nhat",
            "nhà cung cấp tốt nhất",
        ]
    ):
        from zaic_2026_datamate.purchase_analytics import resolve_purchase_query

        ans_type, ans_val, reason = resolve_purchase_query(
            question, context, as_of_date
        )
        if ans_type != "abstain":
            return ans_type, ans_val, reason

    # Specialized engine for mobile store & installments (dev_014)
    if "so_no_tra_gop" in context.tables:
        from zaic_2026_datamate.mobile_installment import (
            resolve_mobile_installment_query,
        )

        ans_type, ans_val, reason = resolve_mobile_installment_query(
            question, context, as_of_date
        )
        if ans_type != "abstain":
            return ans_type, ans_val, reason

    # Specialized engine for mother and baby store (dev_013)
    if "quan_ly_danh_muc" in context.tables:
        from zaic_2026_datamate.mother_baby import resolve_mother_baby_query

        ans_type, ans_val, reason = resolve_mother_baby_query(
            question, context, as_of_date
        )
        if ans_type != "abstain":
            return ans_type, ans_val, reason

    # Specialized engine for multi-month sales and monotonic comparisons
    from zaic_2026_datamate.multi_month_revenue import resolve_multi_month_query

    mm_type, mm_val, mm_reason = resolve_multi_month_query(
        question, context, as_of_date
    )
    if mm_type != "abstain":
        return mm_type, mm_val, mm_reason

    # Specialized engine for rolling windows (7 days, 30 days, last week, this week, yesterday)
    from zaic_2026_datamate.rolling_window import resolve_rolling_window_query

    rw_res = resolve_rolling_window_query(question, context, as_of_date)
    if rw_res is not None:
        rw_type, rw_val, rw_reason = rw_res
        if rw_type != "abstain":
            return rw_type, rw_val, rw_reason

    # Specialized engine for price change impact comparisons
    from zaic_2026_datamate.price_change import resolve_price_change_query

    pc_res = resolve_price_change_query(question, context, as_of_date)
    if pc_res is not None:
        pc_type, pc_val, pc_reason = pc_res
        if pc_type != "abstain":
            return pc_type, pc_val, pc_reason

    q = question.lower()
    period = parse_time_expressions(question, as_of_date)
    table, order_col, date_col, status_col = find_primary_sales_table(context)

    # Detect if query mentions a specific product name in this business
    product_list = get_all_product_names(context)
    matched_product: tuple[str, str, str] | None = None
    for p_name, p_table, p_col in product_list:
        if p_name in q:
            matched_product = (p_name, p_table, p_col)
            break

    # -------------------------------------------------------------------------
    # 0. Strict Anti-Negative Protection Gate (Silence is Free Axiom)
    # -------------------------------------------------------------------------
    # 1. Block boolean questions
    if any(k in q for k in ["phải ko", "phai ko", "không?", "ko?"]) or re.search(
        r"\b(?:không|khong|ko)\s*\??$", q.strip()
    ):
        return "abstain", None, "Boolean question abstained"

    # 2. Relative time guard: if relative keywords exist but no as_of_date is provided, abstain cleanly
    is_relative_time = any(
        k in q
        for k in [
            "hôm nay",
            "hom nay",
            "hnay",
            "hôm ni",
            "hom ni",
            "bữa nay",
            "bua nay",
            "hôm qua",
            "hom qua",
            "bữa qua",
            "bua qua",
            "tuần",
            "tuan",
            "đầu năm",
            "dau nam",
            "1/1",
            "01/01",
            "năm nay",
            "nam nay",
            "năm ni",
            "nam ni",
            "30 ngày",
            "ngày qua",
            "ngay qua",
            "tháng này",
            "thang nay",
        ]
    )
    if is_relative_time and not as_of_date:
        return "abstain", None, "Relative time query without as_of_date abstained"

    # Rolling N days ("30 ngày", "ngày qua")
    if any(
        k in q
        for k in [
            "30 ngày",
            "30 ngay",
            "ngày qua",
            "ngay qua",
            "ngày gần nhất",
            "ngay gan nhat",
        ]
    ):
        from zaic_2026_datamate.semantic_contract import resolve_semantic_plan

        sem_type, sem_val, sem_reason = resolve_semantic_plan(
            question, context, as_of_date
        )
        if sem_type != "abstain":
            return sem_type, sem_val, sem_reason
        # Fall through to deterministic handlers below (Order Count, Store Revenue, AOV)

    # 3. Block conditions / reasons (late delivery, reasons for cancellation/return, etc.)
    q_no_ewallet = re.sub(r"\b(?:vi\s+dien\s+tu|ví\s+điện\s+tử|cadivi)\b", "", q)
    q_no_blister = re.sub(
        r"\b\d+\s*(?:vỉ|vi)\b|\b(?:vỉ|vi)\s*(?:x|\*)\s*\d+\b|\b(?:hộp|hop)\s+\d+\s*(?:vỉ|vi)\b",
        "",
        q_no_ewallet,
    )
    is_reason_q = bool(
        re.search(
            r"\b(?:vì\s+sao|vi\s+sao|lý\s+do|ly\s+do|tại\s+sao|tai\s+sao|nguyên\s+nhân|nguyen\s+nhan)\b",
            q_no_blister,
        )
        or re.search(
            r"\b(?:huỷ|huy|trả\s+món|tra\s+mon|trả\s+hàng|tra\s+hang|trả\s+lại|tra\s+lai|bị|bi)\s+.*?\b(?:vì|vi)\b",
            q_no_blister,
        )
        or re.search(r"\b(?:trễ|chậm|tre|cham)\b", q_no_blister)
        or re.search(
            r"\b(?:vì|vi)\s+(?:món|mon|lỗi|loi|sao|lý|ly|kẹt|giao)\b",
            q_no_blister,
        )
    )
    if is_reason_q:
        return "abstain", None, "Conditional / reason query abstained"

    # 4. Block inventory queries ("còn", "trong kho", "tồn kho", "cuối ngày")
    if bool(
        re.search(
            r"\b(?:trong\s*kho|to+n[f]?\s*kho|cuoi\s*ngay)\b|\bcon\s+(?:lai\s+)?(?:may|bao\s+nhieu|bnhiu|bn)\b|\b(?:hop\s*chan|cai\s*le)\b",
            q,
        )
    ):
        return "abstain", None, "Inventory query abstained"

    # Block multi-month ranges ("từ tháng", "tu thang", "tới tháng", "toi thang", "đến tháng", "den thang")
    if any(
        k in q
        for k in [
            "từ tháng",
            "tu thang",
            "tới tháng",
            "toi thang",
            "đến tháng",
            "den thang",
        ]
    ):
        return "abstain", None, "Multi-month range query abstained"

    # 5. Item quantities (units sold, bowl, roll, package count)
    has_item_qty = (
        bool(
            re.search(
                r"\b(?:mấy|may|bao nhiêu|bao nhieu|bnhiu|bn|tong so|tổng số|đếm|dem)\s+(?:hộp|chai|lon|gói|cái|cuộn|bịch|thùng|vé|đĩa|ly|tô|bát|suất|chiếc|viên|kg|nồi|noi)\b",
                q,
            )
        )
        or bool(
            re.search(
                r"\bso\s+(?:ly|hop|chai|lon|goi|bich|cai|cuon|vien|kg|to|bat|chiec|thung|ve|dia|suat|noi)\b.*?\b(?:la\s+bao\s+nhieu|bao\s+nhieu|bnhiu|bn)\b",
                q,
            )
        )
        or bool(
            re.search(
                r"\bban\s+(?:duoc|dc)\s+(?:tong\s+cong\s+)?(?:bao\s+nhieu|may)\s+(?:ly|hop|chai|lon|goi|bich|cai|cuon|vien|kg|to|bat|chiec|thung|ve|dia|suat|noi)\b",
                q,
            )
        )
        or bool(re.search(r"\b(?:mấy|may|bao nhiêu|bao nhieu|bnhiu|bn)\s+ly\b", q))
        or bool(
            re.search(
                r"\b(?:quan|cua hang)\s+ban\s+(?:duoc|dc)\s+(?:bao nhieu|may)\s+ly\b", q
            )
        )
    )
    if has_item_qty:
        from zaic_2026_datamate.semantic_contract import resolve_semantic_plan

        sem_type, sem_val, sem_reason = resolve_semantic_plan(
            question, context, as_of_date
        )
        if sem_type != "abstain":
            return sem_type, sem_val, sem_reason
        return "abstain", None, "Item quantity query abstained"

    # -------------------------------------------------------------------------
    # 0.1 Out of Domain / Missing Entity Check -> Unanswerable
    # -------------------------------------------------------------------------
    if (
        date_col
        and table
        and check_date_out_of_domain(context, period, date_col, table)
    ):
        return (
            "unanswerable",
            None,
            "Khoảng thời gian nằm hoàn toàn ngoài dữ liệu của doanh nghiệp",
        )

    # Gate 1: Subjective queries without objective metrics in database
    if any(
        k in q
        for k in [
            "nhiệt tình nhất",
            "nhiet tinh nhat",
            "thân thiện nhất",
            "than thien nhat",
            "phục vụ tốt nhất",
            "phuc vu tot nhat",
            "uy tín nhất",
            "uy tin nhat",
            "đẹp nhất",
            "dep nhat",
            "dễ thương nhất",
            "de thuong nhat",
            "hài lòng nhất",
            "hai long nhat",
            "chăm chỉ nhất",
            "cham chi nhat",
            "chu đáo nhất",
            "chu dao nhat",
            "vui vẻ nhất",
            "vui ve nhat",
        ]
    ):
        return (
            "unanswerable",
            None,
            "Doanh nghiệp không có dữ liệu đánh giá cảm tính theo Rulebook Mục 7",
        )

    # Gate 1: Cancellation reason when business does not record specific cancel reasons
    if any(
        k in q
        for k in [
            "giao trễ",
            "giao tre",
            "vì giao trễ",
            "vi giao tre",
            "do giao trễ",
            "do giao tre",
        ]
    ):
        has_cancel_reason = any(
            any(k in c.lower() for k in ["ly_do", "nguyen_nhan", "ghi_chu_huy"])
            for tname in context.tables
            for c in context.tables[tname].columns
        )
        if not has_cancel_reason:
            return (
                "unanswerable",
                None,
                "Doanh nghiệp không ghi nhận lý do huỷ đơn hàng cụ thể theo Rulebook Mục 7",
            )

    # Gate 1: Hour/peak traffic when business has no hour/time tracking
    if any(
        k in q
        for k in [
            "mấy giờ",
            "may gio",
            "giờ nào",
            "gio nao",
            "khung giờ",
            "khung gio",
            "vào giờ nào",
            "vao gio nao",
        ]
    ):
        has_hour = any(
            any(k in c.lower() for k in ["gio", "hour", "time", "thoi_gian"])
            for tname in context.tables
            for c in context.tables[tname].columns
            if "ngay" not in c.lower()
        )
        if not has_hour:
            return (
                "unanswerable",
                None,
                "Dữ liệu doanh nghiệp không lưu vết thời gian chi tiết theo giờ giao dịch theo Rulebook Mục 7",
            )

    # Gate 1: Profit / cost queries when business lacks cost/expense tables
    if any(
        k in q
        for k in [
            "lãi ròng",
            "lai rong",
            "lợi nhuận",
            "loi nhuan",
            "tiền lời",
            "tien loi",
        ]
    ):
        has_cost_table = any(
            any(
                k in tname
                for k in (
                    "chi_phi",
                    "gia_von",
                    "nhap_hang",
                    "nhap",
                    "so_quy",
                    "chi_tiet_nhap",
                )
            )
            for tname in context.tables
        )
        if not has_cost_table:
            return (
                "unanswerable",
                None,
                "Doanh nghiệp không có bảng chi phí / giá vốn để tính lợi nhuận",
            )

    # Only match true customer address queries (e.g. 'khách ở đường X', 'khách đường Y', 'địa chỉ'), NEVER food 'đường'
    has_explicit_address_query = any(
        k in q
        for k in [
            "khách ở đường",
            "khach o duong",
            "khách đường",
            "khach duong",
            "nhà ở đường",
            "nha o duong",
            "khách hàng ở đường",
            "khach hang o duong",
            "địa chỉ",
            "dia chi",
            "thời tiết",
            "nhiệt độ",
        ]
    )
    if has_explicit_address_query:
        has_address_col = any(
            "dia_chi" in c or "duong" in c
            for t in context.tables.values()
            for c in t.columns
        )
        if not has_address_col:
            return (
                "unanswerable",
                None,
                "Doanh nghiệp không có thông tin địa chỉ trong dữ liệu",
            )

    # -------------------------------------------------------------------------
    # 1. INTENT: CANCEL RATE (Tỉ lệ huỷ đơn)
    # -------------------------------------------------------------------------
    has_cancel_token = bool(re.search(r"\b(?:hủy|huỷ|huy|huỹ|cancel)\b", q))
    has_rate_token = any(
        k in q
        for k in [
            "%",
            "tỉ lệ",
            "ti le",
            "tỉ nệ",
            "ti ne",
            "phần trăm",
            "phan tram",
            "chiếm",
            "chiem",
        ]
    )
    has_order_token = any(
        k in q for k in ["đơn", "don", "đh", "dh", "bill", "hoá đơn", "hóa đơn"]
    )
    has_cancel_rate_guards = any(
        k in q
        for k in [
            "chuyển khoản",
            "tiền mặt",
            "tra bang",
            "trả bằng",
            "dthu",
            "doanh thu",
            "tiền bán",
            "tien ban",
            "mặt hàng",
            "mat hang",
            "sản phẩm",
            "san pham",
            "thuốc",
            "loại",
            "nhóm",
            "vì sao",
            "vi sao",
            "lý do",
            "ly do",
            "trễ",
            "chậm",
        ]
    ) or (matched_product is not None)

    is_cancel_rate_query = (
        has_cancel_token
        and has_rate_token
        and has_order_token
        and not has_cancel_rate_guards
    )

    if is_cancel_rate_query:
        if not table or not order_col or not date_col or not status_col:
            return "abstain", None, "Thiếu cột trạng thái hoặc mã đơn để tính tỉ lệ huỷ"

        date_where = build_date_where_clause("_order_date", period)
        cancel_check = f"(TRY_CAST({status_col} AS VARCHAR) ILIKE '%hủy%' OR TRY_CAST({status_col} AS VARCHAR) ILIKE '%huỷ%' OR TRY_CAST({status_col} AS VARCHAR) ILIKE '%cancel%')"

        # Guard: verify if status column actually contains cancellation events
        has_cancel_events = context.scalar(
            f"SELECT COUNT(*) FROM {table} WHERE {cancel_check}"
        )
        if not has_cancel_events or has_cancel_events == 0:
            unique_status = context.tables[table][status_col].dropna().unique()
            if not any(
                k in str(v).lower()
                for v in unique_status
                for k in ["hủy", "huỷ", "cancel"]
            ):
                return "abstain", None, "Cột trạng thái không chứa thông tin huỷ đơn"

        sql = f"""
            SELECT ROUND(
                COUNT(DISTINCT CASE WHEN _is_cancel = 1 THEN {order_col} END) * 100.0 /
                NULLIF(COUNT(DISTINCT {order_col}), 0), 2
            )
            FROM (
                SELECT
                    {order_col},
                    MIN(TRY_CAST({date_col} AS DATE)) AS _order_date,
                    MAX(CASE WHEN {cancel_check} THEN 1 ELSE 0 END) AS _is_cancel
                FROM {table}
                WHERE {order_col} IS NOT NULL
                GROUP BY {order_col}
            ) _orders
            WHERE {date_where}
        """
        try:
            val = context.scalar(sql)
            if val is None or float(val) == 0.0:
                return "abstain", None, "Tỉ lệ huỷ là 0.0% hoặc không có đơn hàng"
            return "percent", float(val), None
        except Exception as e:
            logger.warning("Error computing cancel rate: %s", e)
            return "abstain", None, str(e)

    # -------------------------------------------------------------------------
    # 3. INTENT: CANCEL COUNT (Số đơn đã huỷ)
    # -------------------------------------------------------------------------
    has_cancel_count_phrases = (
        any(
            k in q
            for k in [
                "số đơn",
                "so don",
                "mấy đơn",
                "may don",
                "mấy đơng",
                "may dong",
                "mấy đh",
                "may dh",
                "bao nhiêu đơn",
                "bao nhieu don",
                "bao nhiu don",
                "bnhiu don",
                "bnhiu đơn",
                "bao nhiêu đh",
                "bao nhieu dh",
                "bnhiu đh",
                "bnhiu dh",
                "mấy bill",
                "bao nhiêu bill",
                "bao nhieu bill",
                "đếm đh",
                "đếm đơn",
                "mấy hoá đơn",
                "mấy hóa đơn",
            ]
        )
        or bool(
            re.search(
                r"\b(?:huỷ|hủy|huy|huỹ|cancel)\s+(?:mất\s+)?(?:mấy|may|bao nhiêu|bao nhieu|bnhiu)\s+(?:đơn|don|đh|dh|bill)",
                q,
            )
        )
        or bool(
            re.search(
                r"\b(?:bị|bi)\s+(?:huỷ|hủy|huy|huỹ|cancel)\s+(?:mấy|may|bao nhiêu|bao nhieu|bnhiu)\s+(?:đơn|don|đh|dh|bill)",
                q,
            )
        )
    )

    has_cancel_count_guards = (
        has_rate_token
        or has_cancel_rate_guards
        or any(k in q for k in ["ca sáng", "ca sang", "ca chiều", "ca chieu"])
    )

    is_cancel_count_query = (
        has_cancel_token
        and has_cancel_count_phrases
        and has_order_token
        and not has_cancel_count_guards
    )

    if is_cancel_count_query:
        if not table or not order_col or not date_col or not status_col:
            return "abstain", None, "Thiếu bảng bán hàng"

        has_valid_period = bool(
            period.month_str
            or period.exact_date
            or (period.start_date and period.end_date)
        )
        if not has_valid_period:
            return "abstain", None, "Không xác định được khoảng thời gian rõ ràng"

        date_where = build_date_where_clause(date_col, period)
        cancel_check = f"(TRY_CAST({status_col} AS VARCHAR) ILIKE '%hủy%' OR TRY_CAST({status_col} AS VARCHAR) ILIKE '%huỷ%' OR TRY_CAST({status_col} AS VARCHAR) ILIKE '%cancel%')"
        sql = f"""
            SELECT COUNT(DISTINCT {order_col})
            FROM {table}
            WHERE {date_where}
              AND {cancel_check}
        """
        try:
            val = context.scalar(sql)
            if val is None or int(val) == 0:
                return "abstain", None, "Số đơn huỷ là 0 hoặc NULL"
            return "number", int(val), None
        except Exception as e:
            return "abstain", None, str(e)

    # -------------------------------------------------------------------------
    # 4. INTENT: PURE ORDER COUNT (Số đơn hàng thành công)
    # -------------------------------------------------------------------------
    has_order_count_kw = (
        bool(
            re.search(
                r"(?:bao nhiêu|bao nhieu|bao nhiu|bnhiu|mấy|may|co bao nhieu|có bao nhiêu|co bao nhiu|có bao nhiu|tổng cộng mấy|tổng mấy|được mấy|duoc may|được bao nhiêu|duoc bao nhieu)\s+(?:đơn|don|đh|dh|bill|hóa đơn|hoa don|o\s*đờ|o\s*do|order)\b",
                q,
            )
        )
        or bool(
            re.search(
                r"\b(?:được|chốt được|lên được|co|có)\s+(?:mấy|may|bao nhiêu|bao nhieu|bnhiu)\s+(?:đơn|don|đh|dh|bill|o\s*đờ|o\s*do|order)\b",
                q,
            )
        )
        or bool(
            re.search(
                r"\b(?:số\s+đh|so\s+dh|số\s+đơn|so\s+don|số\s+hóa\s+đơn|so\s+hoa\s+don|số\s+o\s*đờ|so\s+o\s*do)\b",
                q,
            )
        )
        or any(
            k in q
            for k in [
                "bao nhiêu đơn hàng",
                "bao nhieu don hang",
                "mấy đơn hàng",
                "may don hang",
            ]
        )
    )

    has_order_threshold = bool(
        re.search(
            r"(?:từ|tu|đạt|dat|trên|tren|dưới|duoi|>=|<=|>|<)\s*[\d.]+\s*(?:đ|d|đồng|dong|k|tr|triệu|trieu|nghìn|nghin)?\s*(?:trở lên|tro len|tro lne|trở leen|tro leen)?",
            q,
        )
    ) or any(
        k in q
        for k in [
            "trở lên",
            "tro len",
            "tro lne",
            "trở leen",
            "tro leen",
            "≥",
            ">=",
            "≤",
            "<=",
        ]
    )
    has_order_items = (matched_product is not None) or any(
        k in q
        for k in [
            "mặt hàng",
            "mat hang",
            "sản phẩm",
            "san pham",
            "nhóm",
            "nhom",
            "món",
            "loại",
            "hộp",
            "chai",
            "ly",
            "tô",
            "phần",
            "cái",
            "viên",
        ]
    )
    has_order_cust = any(
        k in q
        for k in [
            "khách",
            "khach",
            "bán chịu",
            "ban chiu",
            "nợ",
            "no",
            "phải trả",
            "phai tra",
        ]
    )
    has_order_day_count = any(
        k in q
        for k in [
            "mấy ngày",
            "may ngay",
            "bao nhiêu ngày",
            "bao nhiu ngay",
            "mấy bữa",
            "may bua",
            "bao nhiêu bữa",
            "bao nhiu bua",
            "số ngày",
            "so ngay",
        ]
    )
    has_order_rev = any(
        k in q
        for k in [
            "dthu",
            "doanh thu",
            "tiền bán",
            "tien ban",
            "lãi",
            "lời",
            "chiết khấu",
            "chiet khau",
            "lần lượt",
            "lan luot",
            "được bao nhiêu tiền",
            "bao nhiêu tiền",
        ]
    )
    has_order_channel = any(
        k in q
        for k in [
            "shopee",
            "zalo",
            "grab",
            "tại quầy",
            "mang về",
            "tiền mặt",
            "chuyển khoản",
        ]
    )

    is_pure_order_count = (
        has_order_count_kw
        and not any(k in q for k in ["huỷ", "hủy", "huy", "cancel"])
        and not any(k in q for k in ["ca sáng", "ca sang", "ca chiều", "ca chieu"])
        and not has_order_threshold
        and not has_order_items
        and not has_order_cust
        and not has_order_day_count
        and not has_order_rev
        and not has_order_channel
    )
    if is_pure_order_count and table and order_col and date_col:
        has_valid_period = bool(
            period.month_str
            or period.exact_date
            or (period.start_date and period.end_date)
        )
        if not has_valid_period:
            return (
                "abstain",
                None,
                "Không xác định được khoảng thời gian rõ ràng để đếm số đơn",
            )

        cols = list(context.tables[table].columns)
        effective_date_col = (
            "thoi_gian_tao" if period.shift and "thoi_gian_tao" in cols else date_col
        )
        date_where = build_date_where_clause(effective_date_col, period)
        shift_where = build_shift_where_clause(effective_date_col, period.shift)
        status_clause = get_valid_sales_status_clause(
            status_col, order_col=order_col, table=table
        )

        sql = f"""
            SELECT COUNT(DISTINCT {order_col})
            FROM {table}
            WHERE {date_where}
              {shift_where}
              {status_clause}
        """
        try:
            val = context.scalar(sql)
            if val is None or int(val) == 0 or int(val) > 500000:
                return "abstain", None, "Số lượng đơn hàng bất thường (0 hoặc > 500k)"
            return "number", int(val), None
        except Exception as e:
            return "abstain", None, str(e)

    # -------------------------------------------------------------------------
    # 5. INTENT: PRODUCT-SPECIFIC REVENUE (Doanh thu của 1 mặt hàng cụ thể)
    # -------------------------------------------------------------------------
    has_rev_keyword = any(
        k in q
        for k in [
            "tổng doanh thu",
            "tổng dthu",
            "doanh thu",
            "dthu",
            "tiền bán",
            "tien ban",
            "thu về",
            "bán đc",
            "bán được",
            "ban duoc",
        ]
    )
    has_valid_period = bool(
        period.month_str or period.exact_date or (period.start_date and period.end_date)
    )

    # (matched_product is already extracted at top of resolve_query)

    # Block item quantity queries (e.g. "bán được mấy hộp X", "bao nhiêu chai Y", "total bn ly", "nhiêu bình", "số tuýp", "số kg")
    has_item_qty_kw = (not has_rev_keyword) and (
        bool(
            re.search(
                r"\b(?:bn|bnhiu|mấy|may|bao nhiêu|bao nhieu|nhiêu|nhieu|số lượng|so luong|số|so|đếm|dem)\s+(?:ly|kg|cây|cay|cuộn|cuon|mét|m\b|bao|phần(?!\s+trăm)|phan(?!\s+tram)|chai|hộp|hop|gói|goi|lon|bịch|bich|thùng|thung|cái|cai|chiếc|chiec|viên|vien|bình|binh|tuýp|tuyp|đĩa|dia|tô|bát|bộ|bo)\b",
                q,
            )
        )
        or (
            any(k in q for k in ["mấy", "may", "bao nhiêu", "bao nhieu", "bnhiu"])
            and any(
                re.search(rf"\b{k}\b", q)
                for k in [
                    "hộp",
                    "chai",
                    "lon",
                    "gói",
                    "bịch",
                    "thùng",
                    "vé",
                    "đĩa",
                    "tô",
                    "bát",
                    "cuốn",
                    "suất",
                    "viên",
                    "tuýp",
                    "bộ",
                    "bo",
                    "kg",
                ]
            )
        )
    )
    if has_item_qty_kw:
        from zaic_2026_datamate.semantic_contract import resolve_semantic_plan

        sem_type, sem_val, sem_reason = resolve_semantic_plan(
            question, context, as_of_date
        )
        if sem_type != "abstain":
            return sem_type, sem_val, sem_reason
        return (
            "abstain",
            None,
            "Câu hỏi đếm số lượng mặt hàng - từ chối an toàn để tránh -1 điểm",
        )

    # If product matched and query asks for its revenue — ưu tiên resolve_semantic_plan trước khi abstain
    if (
        matched_product
        and has_rev_keyword
        and not any(
            k in q
            for k in [
                "đơn",
                "don",
                "%",
                "phần trăm",
                "phan tram",
                "chiếm",
                "chiem",
                "tỉ lệ",
                "ti le",
                "tỷ lệ",
                "ty le",
                "tỉ trọng",
                "ti trong",
                "tỷ trọng",
                "ty trong",
                "nhóm",
                "nhom",
                "kênh",
                "kenh",
                "tại quầy",
                "tai quay",
                "điện thoại",
                "dien thoai",
                "zalo",
                "shopee",
                "tmđt",
                "tmdt",
                "tiền mặt",
                "tien mat",
                "tiền tươi",
                "tien tuoi",
                "chuyển khoản",
                "chuyen khoan",
                "ví điện tử",
                "vi dien tu",
                "cod",
                "xi ô đi",
                "xi o di",
                "ca sáng",
                "ca sang",
                "ca chiều",
                "ca chieu",
                "kê đơn",
                "ke don",
                "lãi",
                "lời",
            ]
        )
    ):
        from zaic_2026_datamate.semantic_contract import resolve_semantic_plan

        sem_type, sem_val, sem_reason = resolve_semantic_plan(
            question, context, as_of_date
        )
        if sem_type != "abstain":
            return sem_type, sem_val, sem_reason
        return "abstain", None, "Doanh thu mặt hàng cụ thể chưa đạt ngưỡng tin cậy 95%"

    # -------------------------------------------------------------------------
    # 5.1 INTENT: AVERAGE ORDER VALUE (AOV - Doanh thu bình quân mỗi đơn)
    # -------------------------------------------------------------------------
    has_aov_avg = any(
        k in q
        for k in [
            "bình quân",
            "binh quan",
            "trung bình",
            "trung binh",
            "binh wan",
            "bình wan",
            "bìn quân",
            "bin quan",
            "bìn wan",
            "bq",
            "tb",
        ]
    )
    has_aov_order = any(
        k in q
        for k in [
            "mỗi đơn",
            "moi don",
            "mổi đơn",
            "mổi don",
            "mội đơn",
            "moi don hang",
            "mỗi đơn hàng",
            "mội đơn hàng",
            "1 đơn",
            "1 don",
            "một đơn",
            "mot don",
            "chên mỗi đơn",
            "trên mỗi đơn",
            "trên 1 đơn",
            "trên một đơn",
            "mỗi đh",
            "mổi đh",
            "mội đh",
            "1 đh",
            "một đh",
            "mot dh",
            "moi dh",
            "mỗi hoá đơn",
            "mỗi hóa đơn",
        ]
    )
    has_aov_guards = (
        any(
            k in q
            for k in [
                "nhóm khách",
                "khach nhom",
                "thân thiết",
                "than thiet",
                "sỉ",
                "lẻ",
                "ly",
                "chai",
                "hộp",
                "gói",
                "cái",
                "viên",
                "khách",
                "ca sáng",
                "ca chiều",
                "tăng",
                "giảm",
                "%",
                "so với",
                "hơn",
                "kém",
                "máy cũ",
                "may cu",
                "thu lại",
                "thu lai",
                "trừ bớt",
                "tru bot",
                "cấn trừ",
                "can tru",
                "trả góp",
                "tra gop",
                "bảo hành",
                "bao hanh",
                "lãi",
                "lai",
                "lời",
                "loi",
            ]
        )
        or (matched_product is not None)
        or ("thu_may_cu" in context.tables)
    )

    if (
        has_aov_avg
        and has_aov_order
        and not has_aov_guards
        and table
        and order_col
        and date_col
    ):
        has_valid_period = bool(
            period.month_str
            or period.exact_date
            or (period.start_date and period.end_date)
        )
        if not has_valid_period:
            return (
                "abstain",
                None,
                "Không xác định được khoảng thời gian rõ ràng để tính AOV",
            )

        rev_c = find_revenue_column(context, table)
        if rev_c:
            date_where = build_date_where_clause(date_col, period)
            status_clause = get_valid_sales_status_clause(
                status_col, order_col=order_col, table=table
            )
            sql = f"""
                SELECT SUM({rev_c}) * 1.0 / NULLIF(COUNT(DISTINCT {order_col}), 0)
                FROM {table}
                WHERE {date_where}
                  {status_clause}
            """
            try:
                val = context.scalar(sql)
                if val is not None and val > 0:
                    val_float = float(val)
                    res = int(round(val_float))
                    return "number", res, None
            except Exception as e:
                return "abstain", None, str(e)

    # -------------------------------------------------------------------------
    # 6. INTENT: OVERALL STORE REVENUE (Tổng doanh thu toàn cửa hàng)
    # STRICT GUARDS to prevent hallucination (-1 penalty)
    # -------------------------------------------------------------------------
    has_ratio = any(
        k in q
        for k in [
            "%",
            "phần trăm",
            "phan tram",
            "chiếm",
            "tỉ lệ",
            "ti le",
            "tỉ trọng",
            "ti trong",
        ]
    )
    has_avg = any(
        k in q
        for k in [
            "trung bình",
            "trung binh",
            "bình quân",
            "binh quan",
            "bình wan",
            "binh wan",
            "mỗi đơn",
            "moi don",
            "1 đơn",
            "mỗi ngày",
            "moi ngay",
        ]
    )
    has_count_qty = any(
        k in q
        for k in [
            "bao nhiêu đơn",
            "bao nhieu don",
            "mấy đơn",
            "may don",
            "mấy đơng",
            "may dong",
            "bnhiu đơn",
            "bnhiu don",
            "bao nhiêu đh",
            "bao nhieu dh",
            "mấy đh",
            "may dh",
            "bnhiu đh",
            "bnhiu dh",
            "bao nhiêu đơn hàng",
            "bao nhieu don hang",
            "mấy đơn hàng",
            "may don hang",
            "bao nhiêu ngày",
            "bao nhieu ngay",
            "mấy ngày",
            "may ngay",
            "mấy bữa",
            "may bua",
            "bao nhiêu bữa",
            "bao nhieu bua",
            "bao nhiêu gói",
            "mấy gói",
            "bao nhiêu ly",
            "mấy ly",
            "bao nhiêu lon",
            "mấy lon",
            "bao nhiêu bịch",
            "mấy bịch",
            "bao nhiêu chai",
            "mấy chai",
            "bao nhiêu hộp",
            "mấy hộp",
            "bao nhiêu cái",
            "mấy cái",
            "bao nhiêu xe",
            "mấy xe",
            "mấy chiếc",
            "mấy khách",
            "bao nhiêu khách",
            "đếm",
            "dem",
            "số ngày",
            "so ngay",
        ]
    ) or bool(
        re.search(
            r"(?:bao nhiêu|bao nhiu|bao nhieu|bn|bnhiu|mấy|may|nhiêu)\s*(?:đơn|don|đh|dh|ly|kg|cây|cay|cuộn|cuon|chai|bịch|bich|lon|gói|goi|hộp|hop|thùng|thung|mét|m\b|bao|viên|vien|vé|ve|cái|cai|bữa|bua|ngày|ngay|suất|dia|dĩa|tô|bát|máy|may|miếng|mieng|đôi|doi|quyển|cuốn|tập|bình|binh|tuýp|tuyp|bill|phần|phan|bộ|bo)",
            q,
        )
    )

    has_choice_rank = any(
        k in q
        for k in [
            "tháng nào",
            "thang nao",
            "tháng naof",
            "thang naof",
            "ngày nào",
            "ngay nao",
            "ngày naof",
            "ngay naof",
            "ngayf nào",
            "ngayf naof",
            "hôm nào",
            "hom nao",
            "hôm naof",
            "hom naof",
            "bữa nào",
            "bua nao",
            "bữa naof",
            "mặt hàng nào",
            "mat hang nao",
            "món nào",
            "mon nao",
            "ai",
            "nv nào",
            "nhân viên nào",
            "nhiều nhất",
            "nhieu nhat",
            "cao nhất",
            "cao nhat",
            "cao nhaats",
            "cao nhứt",
            "thấp nhất",
            "thap nhat",
            "thấp nhaats",
            "thấp nhứt",
            "hơn hay kém",
            "tăng hay giảm",
            "so với",
            "so sánh",
            "có tăng",
            "co tang",
            "đều cao hơn",
            "tăng liên tục",
            "dòng máy",
            "dong may",
            "hãng nào",
            "hang nao",
            "lời nhiều nhất",
            "lời nhất",
            "loi nhat",
        ]
    )
    has_list_periods = any(
        k in q
        for k in [
            "lần lượt",
            "lan luot",
            "kể giúp",
            "ke giup",
            "từng tháng",
            "tung thang",
            "các tháng",
            "cac thang",
            "mỗi tháng",
            "moi thang",
            "mỗi ngày",
            "moi ngay",
            "theo thứ tự",
            "thứ tự thời gian",
        ]
    ) or bool(
        re.search(
            r"(?:\d{1,2}[/.-]\d{2,4}.*?,\s*\d{1,2}[/.-]\d{2,4})|(?:tháng\s*\d+,\s*\d+)|(?:t(?:h)?\d+/\d+.*?,.*?t(?:h)?\d+/\d+)",
            q,
        )
    )
    has_channel_split = any(
        k in q
        for k in [
            "tại quầy",
            "tai quay",
            "kênh điện thoại",
            "kenh dien thoai",
            "qua điện thoại",
            "qua dien thoai",
            "bán qua điện thoại",
            "ban qua dien thoai",
            "shopee",
            "sàn tmđt",
            "san tmdt",
            "tiền mặt",
            "tien mat",
            "tiền tươi",
            "tien tuoi",
            "chuyển khoản",
            "chuyen khoan",
            "ví điện tử",
            "vi dien tu",
            "cod",
            "xi ô đi",
            "xi o di",
            "ghi nợ",
            "trả góp",
            "tra gop",
            "mang về",
            "mang ve",
            "tại chỗ",
            "tai cho",
            "giao hàng",
            "giao hang",
            "ship",
            "trực tiếp",
            "truc tiep",
            "online",
            "onl",
            "grab",
            "bán lẻ",
            "ban le",
            "bán sỉ",
            "ban si",
            "khách lẻ",
            "khach le",
            "khách sỉ",
            "khach si",
            "khách thân thiết",
            "khach than thiet",
            "zalo",
            "zalo oa",
            "ca sáng",
            "ca sang",
            "ca chiều",
            "ca chieu",
            "khách nữ",
            "khach nu",
            "khách nam",
            "khach nam",
        ]
    )
    has_category_filter = any(
        k in q
        for k in [
            "nhóm",
            "nhom",
            "thuốc kê đơn",
            "thuoc ke don",
            "không kê đơn",
            "khong ke don",
            "hoạt chất",
            "hoat chat",
            "voucher",
            "vâu chơ",
            "giảm giá",
            "giam gia",
            "phí ship",
            "phi ship",
            "bảo hành",
            "bao hanh",
            "máy cũ",
            "may cu",
            "lời",
            "loi",
            "lãi",
            "lai",
            "lãi gộp",
            "lãi ròng",
            "chi phí",
            "chi phi",
            "lãi suất",
            "lai suat",
            "giao trễ",
            "giao tre",
        ]
    )
    has_store_possessive = bool(
        re.search(
            r"\b(?:của|kủa)\s+(?:cửa hàng|cua hang|quán|quan|shop|tiệm|tiem|sốp|sop|mik|mình|minh|tôi|toi|chúng tôi|chung toi|cả quán|cả tiệm|toàn bộ|toan bo)\b",
            q,
        )
    )
    has_other_possessive = ("của" in q or "kủa" in q) and not has_store_possessive
    has_product_filter = (
        (matched_product is not None)
        or has_other_possessive
        or any(
            k in q
            for k in [
                "mặt hàng",
                "mat hang",
                "sản phẩm",
                "san pham",
                "heineken",
                "nước ép",
                "sữa bột",
                "bỉm",
                "ống pvc",
                "gạch ống",
                "size m",
                "size l",
                "thép",
                "thep",
            ]
        )
    )

    has_threshold_kw = bool(
        re.search(
            r"(?:từ|tu|đạt|dat)\s*[\d.]+\s*(?:đ|d|đồng|dong|k|tr|triệu|trieu|nghìn|nghin)?\s*(?:trở lên|tro len|tro lne|trở leen|tro leen)",
            q,
        )
    ) or any(
        k in q
        for k in [
            "trở lên",
            "tro len",
            "tro lne",
            "trở leen",
            "tro leen",
            "≥",
            ">=",
            "≤",
            "<=",
        ]
    )
    has_comparison = any(
        k in q
        for k in [
            "hơn",
            "hon",
            "kém",
            "kem",
            "tăng",
            "tang",
            "giảm",
            "giam",
            "so với",
            "so voi",
            "phải ko",
            "phải không",
            "hông",
            "khong?",
            "không?",
            "luôn cao hơn",
            "luon cao hon",
            "luôn hơn",
        ]
    )
    has_debt_kw = any(
        k in q
        for k in [
            "bán chịu",
            "ban chiu",
            "sổ nợ",
            "so no",
            "công nợ",
            "cong no",
            "nợ",
            "no",
            "phải trả",
            "phai tra",
        ]
    )
    has_day_count = any(
        k in q
        for k in [
            "mấy ngày",
            "may ngay",
            "bao nhiêu ngày",
            "bao nhiu ngay",
            "mấy bữa",
            "may bua",
            "bao nhiêu bữa",
            "bao nhiu bua",
            "số ngày",
            "so ngay",
        ]
    )

    SPECIFIC_PRODUCT_WORDS = [
        "nhân viên",
        "nhan vien",
        "nv",
        "mã nv",
        "ma nv",
        "đinh đạt đức",
        "nhóm",
        "nhom",
        "loại",
        "loai",
        "kê đơn",
        "ke don",
        "không kê đơn",
        "khong ke don",
        "ko kê đơn",
        "ko ke don",
        "đơn bác sĩ",
        "bác sĩ",
        "bac si",
        "thuốc",
        "thuoc",
        "bỉm",
        "bim",
        "sữa bột",
        "sua bot",
        "thép",
        "thep",
        "xi măng",
        "xi mang",
        "gạch",
        "gach",
        "ống pvc",
        "ong pvc",
        "cà phê",
        "ca phe",
        "trà",
        "nước ép",
        "nuoc ep",
        "bánh",
        "banh",
        "mì",
        "bia",
        "heineken",
        "tiger",
        "sài gòn",
        "má heo",
        "ma heo",
        "nướng",
        "savi",
        "losartan",
        "costar",
        "omega",
        "redmi",
        "xiaomi",
        "dòng máy",
        "dong may",
        "chiếc",
        "chiec",
        "đồ ăn vặt",
        "do an vat",
        "dùng ngoài",
        "dung ngoai",
        "ly size",
        "size m",
        "size l",
        "đĩa",
        "mấy đĩa",
        "may dia",
        "tết nguyên đán",
    ]
    has_specific_product_or_group = any(
        bool(re.search(rf"\b{re.escape(k)}\b", q)) for k in SPECIFIC_PRODUCT_WORDS
    )

    # Check if the query asks for revenue of a specific subject rather than the whole store
    m_subject = re.search(
        r"(?:tiền bán|tien ban|doanh thu|dthu)\s+(?:của\s+)?([^?]+?)(?:\s+trong|\s+tháng|\s+thang|\s+quý|\s+quy|\s+t\d|\s+năm|\s+nam|\s+đc|\s+được|\s+đạt|\s+dat|\s+là|\s+bn|\s+bao nhiêu)",
        q,
        re.IGNORECASE,
    )
    has_unrecognized_product_subject = False
    if m_subject:
        subj = m_subject.group(1).strip()
        # Clean temporal tokens (e.g. th3/2025, 7/26, 7 ngay gan nhat)
        clean_subj = re.sub(
            r"\b(?:th|t|tháng|thang|quý|quy|năm|nam)?\s*\d{1,2}(?:[/.-]\d{2,4})?\b",
            "",
            subj,
        )
        clean_subj = re.sub(
            r"\b\d+\s*(?:ngày|ngay|tuần|tuan|tháng|thang)\s*(?:gần\s+nhất|gan\s+nhat|qua)?\b",
            "",
            clean_subj,
        )
        # If temporal like 't10/2025', '5/2026', '2025', 'q1' or stop words
        if not re.match(
            r"^(?:t\d{1,2}[/.-]\d{2,4}|\d{1,2}[/.-]\d{2,4}|\d{4}|q\d)$", subj
        ):
            words = set(re.findall(r"[a-zA-Z_À-ỹ]+", clean_subj.lower()))
            STORE_STOP_WORDS = {
                "cua",
                "hang",
                "quan",
                "tiem",
                "shop",
                "sop",
                "tat",
                "ca",
                "tong",
                "cong",
                "mik",
                "minh",
                "duoc",
                "dc",
                "dat",
                "trong",
                "la",
                "t",
                "th",
                "thang",
                "quy",
                "nam",
                "cửa",
                "hàng",
                "quán",
                "tiệm",
                "sốp",
                "tất",
                "cả",
                "tổng",
                "cộng",
                "mình",
                "được",
                "đc",
                "đạt",
                "tháng",
                "quý",
                "năm",
                "ở",
                "tại",
                "vào",
                "kủa",
                "của",
                "là",
                "chi nhánh",
                "toàn bộ",
                "toan bo",
                "cả quán",
                "ca quan",
                "cả tiệm",
                "ca tiem",
                "nay",
                "này",
                "ni",
                "hom",
                "hôm",
                "bua",
                "bữa",
                "tuan",
                "tuần",
                "qua",
                "chừ",
                "chu",
                "đầu",
                "dau",
                "hết",
                "het",
                "hnay",
                "hqua",
                "ngay",
                "ngày",
                "ngayf",
                "từ",
                "tu",
                "tới",
                "toi",
                "đến",
                "den",
                "tính",
                "tinh",
                "bây",
                "bay",
                "giờ",
                "gio",
                "gan",
                "gần",
                "nhat",
                "nhất",
                "nhứt",
                "nhaats",
                "moi",
                "mỗi",
                "wán",
                "trog",
            }
            if not words.issubset(STORE_STOP_WORDS) and not matched_product:
                has_unrecognized_product_subject = True

    is_overall_rev_query = (
        has_rev_keyword
        and not has_ratio
        and not has_avg
        and not has_count_qty
        and not has_choice_rank
        and not has_list_periods
        and not has_threshold_kw
        and not has_comparison
        and not has_debt_kw
        and not has_day_count
        and not has_specific_product_or_group
        and not has_channel_split
        and not has_category_filter
        and not has_product_filter
        and not has_unrecognized_product_subject
    )

    if is_overall_rev_query:
        if not has_valid_period:
            from zaic_2026_datamate.semantic_contract import resolve_semantic_plan

            sem_type, sem_val, sem_reason = resolve_semantic_plan(
                question, context, as_of_date
            )
            if sem_type != "abstain":
                return sem_type, sem_val, sem_reason
            return (
                "abstain",
                None,
                "Không xác định được khoảng thời gian rõ ràng để tính tổng doanh thu",
            )
        if not table or not date_col:
            return "abstain", None, "Thiếu bảng bán hàng"

        rev_col = find_revenue_column(context, table)
        if not rev_col:
            return "abstain", None, "Không tìm thấy cột doanh thu hợp lệ"

        cols = list(context.tables[table].columns)
        effective_date_col = (
            "thoi_gian_tao" if period.shift and "thoi_gian_tao" in cols else date_col
        )
        date_where = build_date_where_clause(effective_date_col, period)
        shift_where = build_shift_where_clause(effective_date_col, period.shift)
        status_clause = get_valid_sales_status_clause(
            status_col, order_col=order_col, table=table
        )

        # If chi_tiet_don exists alongside don_hang (e.g. biz_127, biz_129), compute line items for exact revenue
        if "chi_tiet_don" in context.tables and table == "don_hang":
            ct_cols = list(context.tables["chi_tiet_don"].columns)
            ct_order_col = next(
                (
                    c
                    for c in [
                        "ma_don_hang",
                        "ma_hoa_don",
                        "ma_phieu",
                        "so_phieu",
                        "ma_chung_tu",
                    ]
                    if c in ct_cols
                ),
                None,
            )
            ct_rev = next((c for c in ["amount", "tien_ban"] if c in ct_cols), None)
            if ct_order_col and ct_rev and order_col:
                d_date_where = build_date_where_clause(
                    f"d.{effective_date_col}", period
                )
                d_status_cond = (
                    f"AND d.{status_col} = 'completed'"
                    if (
                        status_col
                        and "completed"
                        in context.tables[table][status_col].dropna().values
                    )
                    else ""
                )
                sql = f"""
                    SELECT ROUND(SUM(c.{ct_rev}))
                    FROM chi_tiet_don c
                    JOIN {table} d ON c.{ct_order_col} = d.{order_col}
                    WHERE {d_date_where}
                      {d_status_cond}
                """
            else:
                sql = f"""
                    SELECT ROUND(SUM({rev_col}))
                    FROM {table}
                    WHERE {date_where}
                      {shift_where}
                      {status_clause}
                """
        else:
            sql = f"""
                SELECT ROUND(SUM({rev_col}))
                FROM {table}
                WHERE {date_where}
                  {shift_where}
                  {status_clause}
            """
        try:
            val = context.scalar(sql)
            if val is None or int(val) == 0:
                return "abstain", None, "Kết quả doanh thu là NULL hoặc 0"
            return "number", int(val), None
        except Exception as e:
            return "abstain", None, str(e)

    # -------------------------------------------------------------------------
    # 7. INTENT: PURCHASE TOTAL VALUE (Tổng giá trị phiếu nhập hàng)
    # -------------------------------------------------------------------------
    has_import_kw = any(
        k in q
        for k in [
            "nhập hàng",
            "nhap hang",
            "nhap hagn",
            "phiếu nhập",
            "phieu nhap",
            "tiền nhập",
            "tien nhap",
            "nhập vô",
            "nhap vo",
            "nhập về",
            "nhap ve",
            "đánh hàng về",
            "danh hang ve",
            "đánh hàng",
            "danh hang",
        ]
    )
    has_import_val_kw = any(
        k in q
        for k in [
            "bao nhiêu tiền",
            "bao nhieu tien",
            "bnhiu tiền",
            "bn tiền",
            "tốn nhiêu tiền",
            "ton nhieu tien",
            "hết bao nhiêu",
            "het bao nhieu",
            "tổng giá trị",
            "tong gia tri",
            "tổng là bao nhiêu",
            "tong la bao nhieu",
            "tổng bnhiu",
            "tong bnhiu",
            "được bao nhiêu tiền",
            "duc bnhiu tien",
            "tổng bn",
            "tổng bao nhiêu",
            "tong bao nhieu",
            "tổng bnhiu",
            "tong bnhiu",
            "tổng bn",
            "tong bn",
            "chi nhap hang bao nhieu tien",
            "nhập hàng bao nhiêu tiền",
            "nhap hang bao nhieu tien",
        ]
    )
    has_import_guards = any(
        k in q
        for k in [
            "bình quân",
            "binh quan",
            "ncc nào",
            "nha cung cap nào",
            "nhà cung cấp nào",
            "trừ",
            "tru",
            "chênh lệch",
            "chenh lech",
            "mấy",
            "may",
            "hộp",
            "hop",
            "chai",
            "thùng",
            "thung",
            "kg",
            "viên",
            "vien",
            "cái",
            "cai",
            "nào nhiều nhất",
            "nào lớn nhất",
        ]
    )

    if has_import_kw and has_import_val_kw and not has_import_guards:
        nhap_t, val_c, date_c, status_c = find_primary_import_table(context)
        if nhap_t and val_c and date_c:
            has_valid_period = bool(
                period.month_str
                or period.exact_date
                or (period.start_date and period.end_date)
            )
            if not has_valid_period:
                return "abstain", None, "Không xác định được khoảng thời gian nhập hàng"

            date_where = build_date_where_clause(date_c, period)
            status_clause = (
                f"AND TRY_CAST({status_c} AS VARCHAR) NOT ILIKE '%hủy%' AND TRY_CAST({status_c} AS VARCHAR) NOT ILIKE '%huỷ%'"
                if status_c
                else ""
            )
            sql = f"""
                SELECT ROUND(SUM({val_c}))
                FROM {nhap_t}
                WHERE {date_where}
                  {status_clause}
            """
            try:
                val = context.scalar(sql)
                if val is None or int(val) == 0:
                    return "abstain", None, "Giá trị nhập hàng là 0 hoặc NULL"
                return "number", int(val), None
            except Exception as e:
                return "abstain", None, str(e)

    # -------------------------------------------------------------------------
    # 8. INTENT: DEBT (Khách nợ)
    # -------------------------------------------------------------------------
    if "còn nợ" in q or "con no" in q:
        debt_table = next(
            (
                t
                for t in context.tables
                if "cong_no" in t or "han_no" in t or "don_hang" in t or "hoa_don" in t
            ),
            None,
        )
        if "bao nhiêu khách" in q or "may khach" in q or "bao nhiu khach" in q:
            if debt_table:
                kh_col = next(
                    (
                        c
                        for c in context.tables[debt_table].columns
                        if "khach" in c or "tenkh" in c or "ma_kh" in c
                    ),
                    None,
                )
                no_col = next(
                    (
                        c
                        for c in context.tables[debt_table].columns
                        if "con_no" in c or "no" in c or "du_no" in c
                    ),
                    None,
                )
                if kh_col and no_col:
                    sql = f"SELECT COUNT(DISTINCT {kh_col}) FROM {debt_table} WHERE {no_col} > 0"
                    try:
                        val = context.scalar(sql)
                        return "number", int(val) if val is not None else 0, None
                    except Exception as e:
                        return "abstain", None, str(e)

    # -------------------------------------------------------------------------
    # 9. PRIORITY 2 SEMANTIC CONTRACT & QUERYPLAN COMPILER
    # -------------------------------------------------------------------------
    from zaic_2026_datamate.semantic_contract import resolve_semantic_plan

    sem_type, sem_val, sem_reason = resolve_semantic_plan(question, context, as_of_date)
    if sem_type != "abstain":
        return sem_type, sem_val, sem_reason

    # -------------------------------------------------------------------------
    # Default: Abstain cleanly (0 penalty) to protect Penalized Accuracy
    # -------------------------------------------------------------------------
    return "abstain", None, "Câu hỏi chưa có template khớp hoặc độ tin cậy < 95%"
