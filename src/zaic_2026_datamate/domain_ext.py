"""Specialized Schema-Driven Domain Extensions Engine for ZAIC 2026 DataMate.

Handles deterministic, generalizable schema patterns across businesses:
1. Schema-driven Out-of-Domain (OOD) Guard:
   - Missing clinical/pregnancy columns
   - Subjective queries without rating/evaluation columns
   - Cancellation/return reason queries without reason tracking columns
2. Logistics & Delivery rounds (`luot_giao` / `lan_giao`)
3. Unsold product inventory vs catalog
4. Phone retail schema analytics (`so_ban_hang` + `so_ban_hang_ghi_tat`)
5. Warranty returns & trade-in ratio (`thu_may_cu`, `so_no_bao_hanh`)
6. Supplier ranking from purchase ledgers (`nha_cung_cap`)
7. Order count above dynamic monetary threshold
8. Inventory ledger (`the_kho`) stockout counts at dynamic target dates
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Tuple

import pandas as pd

from zaic_2026_datamate.cleaner import (
    clean_vietnamese_currency,
    normalize_query_telex,
    ws_norm_sql,
)
from zaic_2026_datamate.data_loader import BusinessContext
from zaic_2026_datamate.semantic_contract import (
    is_bad_status,
    norm_vn,
    pick_order_status_col,
)
from zaic_2026_datamate.intent_parser import (
    find_primary_sales_table,
    get_valid_sales_status_clause,
    parse_time_expressions,
)


def _fetch_one_row(conn: Any, sql: str) -> tuple[Any, ...] | None:
    """Execute SQL and drain the result set cleanly so DuckDB never keeps an open cursor."""
    rows = conn.execute(sql).fetchall()
    return rows[0] if rows else None


_REASON_COL_KEYWORDS = (
    "ly_do",
    "nguyen_nhan",
    "reason",
)
_REASON_VALUE_TOKENS = (
    "giao tre",
    "cham giao",
    "goi nham",
    "nham mon",
    "mon bi loi",
)
_NOTE_COL_CANDIDATES = (
    "ghi_chu",
    "note",
    "trang_thai",
    "tinh_trang",
    "xu_ly",
    "trang_thai_xu_ly",
    "trang_thai_don",
)
_CLINICAL_COL_TOKENS = (
    "chong_chi_dinh",
    "thai_ky",
    "ba_bau",
    "mang_thai",
    "chi_dinh",
)
_RATING_COL_TOKENS = (
    "danh_gia",
    "rating",
    "review",
    "xep_hang",
)


def strip_accents(s: str) -> str:
    """Lowercase and remove Vietnamese accents."""
    return (
        "".join(
            c
            for c in unicodedata.normalize("NFD", str(s))
            if unicodedata.category(c) != "Mn"
        )
        .lower()
        .replace("đ", "d")
    )


def extract_explicit_month(text: str, as_of_date: str | None = None) -> str | None:
    """Extract explicit month (YYYY-MM) from text even if relative expressions exist."""
    m = re.search(
        r"\b(?:th[aá]ng|th|t)\s*(\d{1,2})(?:\s*n[aăâ]m\s*|[/-])(\d{4})\b",
        text,
        re.IGNORECASE,
    )
    if m:
        return f"{m.group(2)}-{int(m.group(1)):02d}"
    m = re.search(r"\b(\d{1,2})[/-](\d{4})\b", text)
    if m:
        return f"{m.group(2)}-{int(m.group(1)):02d}"
    m = re.search(r"\b(\d{1,2})[-/](\d{2})\b", text)
    if m:
        y = 2000 + int(m.group(2))
        return f"{y}-{int(m.group(1)):02d}"
    m = re.search(r"\bth[aá]ng\s*(\d{1,2})\b", text, re.IGNORECASE)
    if m and as_of_date:
        y = int(as_of_date[:4])
        return f"{y}-{int(m.group(1)):02d}"
    if as_of_date and any(
        k in strip_accents(text)
        for k in ["thang nay", "thang ni", "th nay", "thang hien tai"]
    ):
        return as_of_date[:7]
    return None


def _extract_explicit_iso_date(text: str) -> str | None:
    """Extract an explicit dd/mm/yyyy or dd-mm-yyyy date into ISO YYYY-MM-DD."""
    m = re.search(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{4})\b", text)
    if not m:
        return None
    day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if 1 <= month <= 12 and 1 <= day <= 31 and 1990 <= year <= 2100:
        return f"{year:04d}-{month:02d}-{day:02d}"
    return None


def _extract_money_threshold(q_norm: str) -> float | None:
    """Parse a minimum order threshold in VND (e.g. 'tu 760.000', 'tu 500k', '>= 1tr')."""
    pattern = re.compile(
        r"(?:\btu\b|>=|\btoi thieu\b|\blon hon(?: hoac bang)?\b|\bdat tu\b)\s*"
        r"(\d+(?:[.,]\d+)?)\s*(?:(tr|trieu|k|nghin|ngan|dong|d)\b)?",
        re.IGNORECASE,
    )
    m = pattern.search(q_norm)
    if not m:
        return None
    raw_num, unit = m.group(1), (m.group(2) or "").lower()
    # Ignore percentage expressions like "tu 50%"
    after = q_norm[m.end() :].lstrip()
    if after.startswith("%") or after.startswith("phan tram"):
        return None

    if unit in ("tr", "trieu"):
        val = float(raw_num.replace(",", ".")) * 1_000_000.0
    elif unit in ("k", "nghin", "ngan"):
        val = float(raw_num.replace(",", ".")) * 1_000.0
    elif re.match(r"^\d{1,3}(?:\.\d{3})+$", raw_num) or re.match(
        r"^\d{1,3}(?:,\d{3})+$", raw_num
    ):
        val = float(raw_num.replace(".", "").replace(",", ""))
    else:
        val = float(raw_num.replace(",", "."))

    # Require realistic currency magnitude (>= 10,000 VND) to avoid years/counts
    return val if val >= 10_000.0 else None


def _quarter_to_bounds(quarter_str: str) -> tuple[str, str] | None:
    """Convert 'YYYY-QN' string to inclusive (start_date, end_date) bounds."""
    m = re.match(r"^(\d{4})-Q([1-4])$", quarter_str)
    if not m:
        return None
    year, q = int(m.group(1)), int(m.group(2))
    bounds = {
        1: (f"{year}-01-01", f"{year}-03-31"),
        2: (f"{year}-04-01", f"{year}-06-30"),
        3: (f"{year}-07-01", f"{year}-09-30"),
        4: (f"{year}-10-01", f"{year}-12-31"),
    }
    return bounds[q]


def _has_reason_tracking(context: BusinessContext) -> bool:
    """Return True if any non-alias table tracks cancellation/return reasons."""
    for tname, df in context.tables.items():
        if tname.endswith("_sheet1"):
            continue
        cols_lower = [c.lower() for c in df.columns]
        if any(any(tok in col for tok in _REASON_COL_KEYWORDS) for col in cols_lower):
            return True
        for col in df.columns:
            if any(cand in col.lower() for cand in _NOTE_COL_CANDIDATES):
                vals = df[col].dropna().astype(str).head(200)
                if any(
                    any(tok in strip_accents(v) for tok in _REASON_VALUE_TOKENS)
                    for v in vals
                ):
                    return True
    return False


def _has_clinical_columns(context: BusinessContext) -> bool:
    """Return True if any table exposes clinical/pregnancy guidance columns."""
    for tname, df in context.tables.items():
        if tname.endswith("_sheet1"):
            continue
        for col in df.columns:
            if any(tok in col.lower() for tok in _CLINICAL_COL_TOKENS):
                return True
    return False


def _has_rating_columns(context: BusinessContext) -> bool:
    """Return True if any table exposes subjective quality/rating columns."""
    for tname, df in context.tables.items():
        if tname.endswith("_sheet1"):
            continue
        for col in df.columns:
            if any(tok in col.lower() for tok in _RATING_COL_TOKENS):
                return True
    return False


def _check_schema_ood(
    q_norm: str, context: BusinessContext
) -> Tuple[str, Any, str | None] | None:
    """Gate 1 schema checks for unanswerable questions without business_id hardcodes."""
    if (
        "ba bau dung" in q_norm or "phu nu mang thai" in q_norm
    ) and not _has_clinical_columns(context):
        return (
            "unanswerable",
            None,
            "Cơ sở dữ liệu không chứa thông tin tư vấn y khoa/chống chỉ định cho bà bầu",
        )

    subjective_phrases = (
        "chat luong tot nhat",
        "chat luong cao nhat",
        "trung thuc nhat",
        "ngon nhat",
        "ngon nhut",
        "kho tinh nhat",
        "kho tinh nhut",
        "tan tam",
        "le phep",
        "chu dao",
        "quy men",
    )
    if any(k in q_norm for k in subjective_phrases) and not _has_rating_columns(
        context
    ):
        return (
            "unanswerable",
            None,
            "Doanh nghiệp không có tiêu chí định lượng đo lường cảm tính theo Rulebook Mục 7",
        )

    asks_delay = any(
        k in q_norm for k in ("vi giao tre", "do giao tre", "giao tre", "cham giao")
    )
    asks_wrong_dish = any(
        k in q_norm for k in ("goi nham mon", "goi nham", "mon bi loi")
    )
    asks_reason_why = any(
        k in q_norm for k in ("chu yeu vi sao", "ly do chu yeu", "vi sao z")
    ) and any(k in q_norm for k in ("huy don", "tra don", "tra mon"))
    asks_return_reason = (
        any(k in q_norm for k in ("vi loi", "mua nham", "ly do tra", "nguyen nhan tra"))
        or bool(re.search(r"\btra(?:\s+hang)?\s+vi\b(?!\s+dien\s+tu)", q_norm))
    ) and not any(k in q_norm for k in ("vi dien tu", "tien mat"))

    if (
        asks_delay or asks_wrong_dish or asks_reason_why or asks_return_reason
    ) and not _has_reason_tracking(context):
        return (
            "unanswerable",
            None,
            "Doanh nghiệp không ghi nhận lý do huỷ/trả đơn hàng cụ thể theo Rulebook Mục 7",
        )
    return None


def _resolve_delivery_rounds(
    q_norm: str, question: str, context: BusinessContext, as_of_date: str | None
) -> Tuple[str, Any, str | None] | None:
    """Count distinct order-delivery rounds across delivery tables."""
    has_delivery_round_phrase = bool(
        re.search(
            r"\b(?:"
            r"(?:dot|luot|chuyen)\s+(?:giao(?:\s+hang)?|hang)"
            r"|(?:may|bao\s+nhieu|bnhiu|bn)\s+(?:dot|luot|chuyen)\b"
            r"|tong\s+cong\s+(?:may|bao\s+nhieu|bnhiu|bn)\s+(?:dot|luot|chuyen)\b"
            r")\b",
            q_norm,
        )
    )
    has_delivery_actor = bool(re.search(r"\b(?:giao|cho|xe|van\s+chuyen)\b", q_norm))
    if not (has_delivery_round_phrase and has_delivery_actor):
        return None
    if re.search(r"\bnhap\b", q_norm):
        return None

    from zaic_2026_datamate.semantic_contract import parse_enhanced_period

    period = parse_enhanced_period(question, as_of_date)
    if period.start_date and period.end_date:
        date_filter_tpl = f"try_cast({{date_c}} as date) BETWEEN DATE '{period.start_date}' AND DATE '{period.end_date}'"
    elif period.exact_date:
        date_filter_tpl = f"try_cast({{date_c}} as date) = DATE '{period.exact_date}'"
    elif period.month_str:
        date_filter_tpl = (
            f"(strftime(try_cast({{date_c}} as date), '%Y-%m') = '{period.month_str}' "
            f"OR cast({{date_c}} as varchar) LIKE '{period.month_str}%')"
        )
    else:
        return None

    for tname, tbl in context.tables.items():
        if "giao_hang" not in tname or tname.endswith("_sheet1"):
            continue
        date_c = next(
            (
                c
                for c in ("thoi_gian_giao", "ngay_giao_hang", "ngay_cho", "ngay_giao")
                if c in tbl.columns
            ),
            None,
        )
        order_c = next(
            (
                c
                for c in ("so_don", "ma_don_hang", "ma_bill", "ma_don")
                if c in tbl.columns
            ),
            None,
        )
        round_c = next(
            (
                c
                for c in ("luot_giao", "lan_giao", "so_luot", "dot")
                if c in tbl.columns
            ),
            None,
        )
        if date_c and order_c and round_c:
            date_filter = date_filter_tpl.format(date_c=date_c)
            sql = f"""
                SELECT count(distinct concat({order_c}, '_', cast({round_c} as varchar)))
                FROM {tname}
                WHERE {date_filter} AND {order_c} IS NOT NULL AND {round_c} IS NOT NULL
            """
            try:
                res = context.query(sql)
                if not res.empty and pd.notna(res.iloc[0, 0]):
                    return "number", int(res.iloc[0, 0]), None
            except Exception:
                pass
    return None


def _resolve_delivered_item_qty(
    q_norm: str, question: str, context: BusinessContext, as_of_date: str | None
) -> Tuple[str, Any, str | None] | None:
    """Sum delivered item quantity up to cutoff date for orders placed in target period."""
    if re.search(
        r"\b(?:dot|luot|chuyen)\s*(?:giao|cho|hang)\b|\b(?:may|bao\s+nhieu|bnhiu|bn)\s+(?:dot|luot|chuyen)\b|\bnhap\b",
        q_norm,
    ):
        return None
    if not re.search(
        r"\b(?:da\s+giao|cho\s+di|da\s+cho|giao\s+duoc|xe\s+da\s+giao|xe\s+giao)\b",
        q_norm,
    ):
        return None
    if not re.search(r"\b(?:dat|don\s+dat|khach\s+dat)\b", q_norm):
        return None

    deliv_spec = None
    for tname, tbl in context.tables.items():
        if tname.endswith("_sheet1") or tname == "calendar" or "giao" not in tname:
            continue
        cols = set(tbl.columns)
        d_date = next(
            (
                c
                for c in ("thoi_gian_giao", "ngay_giao_hang", "ngay_cho", "ngay_giao")
                if c in cols
            ),
            None,
        )
        d_ord = next(
            (
                c
                for c in ("so_don", "ma_don_hang", "ma_bill", "ma_don", "ma_hd")
                if c in cols
            ),
            None,
        )
        d_item = next(
            (
                c
                for c in ("hang_hoa", "ten_hang", "mat_hang", "ten_sp", "san_pham")
                if c in cols
            ),
            None,
        )
        d_qty = next(
            (
                c
                for c in ("so_luong_giao", "sl_thuc_giao", "sl_giao", "so_luong")
                if c in cols
            ),
            None,
        )
        d_unit = next((c for c in ("dv_goc", "dvt", "don_vi") if c in cols), None)
        if d_date and d_ord and d_item and d_qty:
            deliv_spec = (tname, d_date, d_ord, d_item, d_qty, d_unit)
            break
    if not deliv_spec:
        return None
    deliv_t, d_date, d_ord, d_item, d_qty, d_unit = deliv_spec

    cutoff_date = _extract_explicit_iso_date(question) or as_of_date
    if not cutoff_date:
        return None

    from zaic_2026_datamate.intent_parser import (
        check_date_out_of_domain,
        find_primary_sales_table,
    )
    from zaic_2026_datamate.price_change import select_best_product
    from zaic_2026_datamate.semantic_contract import parse_enhanced_period

    period = parse_enhanced_period(question, as_of_date)
    if period.month_str:
        ord_date_cond_tpl = (
            f"strftime(TRY_CAST({{dc}} AS DATE), '%Y-%m') = '{period.month_str}'"
        )
    elif period.start_date and period.end_date:
        ord_date_cond_tpl = f"TRY_CAST({{dc}} AS DATE) BETWEEN DATE '{period.start_date}' AND DATE '{period.end_date}'"
    else:
        return None

    sales_tbl, order_col, date_col, status_col = find_primary_sales_table(context)
    if not (sales_tbl and order_col and date_col):
        return None
    if check_date_out_of_domain(context, period, date_col, sales_tbl):
        return (
            "unanswerable",
            None,
            "Khoảng thời gian nằm hoàn toàn ngoài dữ liệu của doanh nghiệp",
        )

    deliv_items = context.tables[deliv_t][d_item].dropna().astype(str).unique().tolist()
    picked = select_best_product(
        [(it, deliv_t, d_item) for it in deliv_items], question.lower()
    )
    if not picked:
        return None
    target_item = picked[0]
    escaped_item = target_item.replace("'", "''")

    status_clause = get_valid_sales_status_clause(
        status_col, order_col=order_col, table=sales_tbl
    )
    ord_date_cond = ord_date_cond_tpl.format(dc=date_col)
    unit_sel = f", MAX(CAST(g.{d_unit} AS VARCHAR))" if d_unit else ", NULL"
    sql = f"""
        WITH valid_orders AS (
            SELECT DISTINCT {order_col} AS ord_id
            FROM {sales_tbl}
            WHERE {ord_date_cond}
              AND {order_col} IS NOT NULL
              {status_clause}
        )
        SELECT COALESCE(SUM(TRY_CAST(g.{d_qty} AS DOUBLE)), 0) {unit_sel}
        FROM {deliv_t} g
        JOIN valid_orders o ON CAST(g.{d_ord} AS VARCHAR) = CAST(o.ord_id AS VARCHAR)
        WHERE g.{d_item} = '{escaped_item}'
          AND TRY_CAST(g.{d_date} AS DATE) <= DATE '{cutoff_date}'
    """
    try:
        row = _fetch_one_row(context.duckdb_conn, sql)
        if not row or row[0] is None:
            return None
        val = float(row[0])
        row_unit = strip_accents(str(row[1])).strip() if row[1] else ""
        # Respect catalog unit factor only if question asks a converted large unit different from dv_goc
        m_u = re.search(
            r"\b(?:may|bao\s+nhieu|bnhiu|bn)\s+([a-z0-9]+)\b",
            q_norm,
        )
        asked_unit = m_u.group(1) if m_u else ""
        if (
            asked_unit
            and row_unit
            and asked_unit != row_unit
            and asked_unit not in ("cai", "mon", "sp")
        ):
            for dm_t in ("danh_muc", "danh_sach_hang"):
                if dm_t in context.tables:
                    dm_df = context.tables[dm_t]
                    if {"hang", "he_so", "dv_goc", "loai_dv"} <= set(dm_df.columns):
                        match_dm = dm_df[dm_df["hang"].astype(str) == target_item]
                        if not match_dm.empty:
                            r0 = match_dm.iloc[0]
                            he_so = float(r0["he_so"]) if pd.notna(r0["he_so"]) else 0.0
                            dm_small = strip_accents(str(r0["dv_goc"])).strip()
                            dm_large = strip_accents(str(r0["loai_dv"])).strip()
                            if (
                                he_so > 0
                                and row_unit == dm_small
                                and asked_unit == dm_large
                            ):
                                val = val / he_so
                                break
        return (
            "number",
            int(round(val)) if abs(val - round(val)) < 1e-6 else round(val, 2),
            None,
        )
    except Exception:
        return None


def _resolve_unsold_items(
    q_norm: str, question: str, context: BusinessContext, as_of_date: str | None
) -> Tuple[str, Any, str | None] | None:
    """Count items with zero valid sales inside the requested period per Rulebook §5."""
    import calendar as _py_calendar

    from zaic_2026_datamate.semantic_contract import BusinessSemanticContract

    q_u = re.sub(r"\b(?:ko|k)\s+(?=(?:ban|ai\s+mua)\b)", "khong ", q_norm)
    q_u = re.sub(r"\b(?:dc|đc)\b", "duoc", q_u)
    if any(k in q_u for k in ("liet ke", "top ", "mat hang nao", "mon nao e")):
        return None
    unsold_patterns = (
        r"\b(?:mat hang|hang|mon)\s+e\b",
        r"\bbi\s+e\b",
        r"\be\s+han\b",
        r"\bkhong\s+ban\s+(?:ra\s+)?duoc\s+(?:cai|mon|lan)\s+nao\b",
        r"\bhok\s+ban\s+ra\s+duoc\s+(?:cai|mon|lan)\s+nao\b",
        r"\bkhong\s+ai\s+mua\b",
        r"\bchua\s+ban\s+duoc\b",
    )
    has_slow_zero = "ban cham" in q_u and any(
        k in q_u for k in ("khong ban", "chua ban", "lan nao", "cai nao", "mon nao")
    )
    if not (has_slow_zero or any(re.search(pat, q_u) for pat in unsold_patterns)):
        return None

    period = parse_time_expressions(question, as_of_date)
    start_date, end_date = period.start_date, period.end_date
    if not (start_date and end_date) and period.month_str:
        y, m = int(period.month_str[:4]), int(period.month_str[5:7])
        last_d = _py_calendar.monthrange(y, m)[1]
        start_date = f"{y:04d}-{m:02d}-01"
        end_date = f"{y:04d}-{m:02d}-{last_d:02d}"
    if not (start_date and end_date):
        return None

    # Primary Rulebook §5 path: items validly sold before start_date minus valid sales in [start_date, end_date]
    try:
        contract = BusinessSemanticContract.from_context(context)
        if contract.lines_df is not None and not contract.lines_df.empty:
            valid_l = contract.lines_df[
                contract.lines_df["is_valid_sale"]
                & (~contract.lines_df["is_service_fee"])
            ]
            dates = pd.to_datetime(valid_l["order_date"], errors="coerce")
            pre_items = set(
                valid_l.loc[dates < pd.Timestamp(start_date), "item_key"]
                .dropna()
                .astype(str)
            )
            if pre_items:
                in_items = set(
                    valid_l.loc[
                        (dates >= pd.Timestamp(start_date))
                        & (dates <= pd.Timestamp(end_date)),
                        "item_key",
                    ]
                    .dropna()
                    .astype(str)
                )
                return "number", len(pre_items - in_items), None
    except Exception:
        pass

    # Fallback when no pre-period sales exist (e.g., synthetic initial-window tests with catalog table)
    cat_table = cat_col = cat_filter = None
    for tname, df in context.tables.items():
        if tname.endswith("_sheet1"):
            continue
        if any(
            k in tname for k in ("danh_muc", "danh_sach_hang", "hang_hoa", "san_pham")
        ):
            if any(c in df.columns for c in ("he_so_qd", "ti_le_quy_doi")):
                continue
            cat_col = next(
                (
                    col
                    for col in ("ten_hang", "ten_hang_hoa", "ten_san_pham", "hang")
                    if col in df.columns
                ),
                None,
            )
            if cat_col:
                cat_table = tname
                cat_filter = (
                    "AND COALESCE(TRY_CAST(duoc_ban_truc_tiep AS INT), 1) = 1"
                    if "duoc_ban_truc_tiep" in df.columns
                    else ""
                )
                break

    sales_table = sales_col = sales_date_col = sales_status_col = sales_ord_col = None
    for tname, df in context.tables.items():
        if tname.endswith("_sheet1"):
            continue
        if any(k in tname for k in ("so_ban_hang", "ban_hang", "chi_tiet_ban_hang")):
            sales_col = next(
                (
                    col
                    for col in (
                        "ten_hang",
                        "ten_hang_hoa",
                        "ten_san_pham",
                        "mat_hang",
                        "hang_ban",
                        "hang",
                    )
                    if col in df.columns
                ),
                None,
            )
            sales_date_col = next(
                (
                    dcol
                    for dcol in ("thoi_gian", "ngay", "ngay_lap", "ngay_ban")
                    if dcol in df.columns
                ),
                None,
            )
            if sales_col and sales_date_col:
                sales_table = tname
                sales_status_col = next(
                    (
                        scol
                        for scol in (
                            "trang_thai_don_hang",
                            "trang_thai",
                            "status",
                            "ghi_chu_tt",
                            "trang_thai_xu_ly",
                            "tinh_trang_don",
                            "trang_thai_don",
                            "tinh_trang",
                            "xu_ly",
                            "ket_qua",
                        )
                        if scol in df.columns
                    ),
                    None,
                )
                sales_ord_col = next(
                    (
                        ocol
                        for ocol in (
                            "order_id",
                            "id_don",
                            "ma_don_hang",
                            "ma_hoa_don",
                            "ma_giao_dich",
                            "ma_phieu",
                            "ma_bill",
                            "so_phieu",
                            "ma_don",
                        )
                        if ocol in df.columns
                    ),
                    None,
                )
                break

    if not (cat_table and cat_col and sales_table and sales_col and sales_date_col):
        return None

    status_clause = (
        get_valid_sales_status_clause(
            sales_status_col, order_col=sales_ord_col, table=sales_table
        )
        if sales_status_col
        else ""
    )

    sql = f"""
        WITH catalog AS (
            SELECT DISTINCT regexp_replace(lower(trim(CAST({cat_col} AS VARCHAR))), '\\s+', ' ', 'g') AS item
            FROM {cat_table}
            WHERE {cat_col} IS NOT NULL
              {cat_filter}
        ),
        sold AS (
            SELECT DISTINCT regexp_replace(lower(trim(CAST({sales_col} AS VARCHAR))), '\\s+', ' ', 'g') AS item
            FROM {sales_table}
            WHERE CAST({sales_date_col} AS DATE) >= DATE '{start_date}'
              AND CAST({sales_date_col} AS DATE) <= DATE '{end_date}'
              AND {sales_col} IS NOT NULL
              {status_clause}
        )
        SELECT count(*) FROM catalog WHERE item NOT IN (SELECT item FROM sold)
    """
    try:
        res = context.query(sql)
        if not res.empty and pd.notna(res.iloc[0, 0]):
            return "number", int(res.iloc[0, 0]), None
    except Exception:
        pass
    return None


def _filter_valid_phone_orders(df: pd.DataFrame) -> pd.DataFrame:
    """Exclude cancelled/returned orders across all rows matching the same order key."""
    status_col = pick_order_status_col(df)
    if not status_col or status_col not in df.columns:
        return df.copy()
    bad_mask = df[status_col].map(is_bad_status).fillna(False)
    if not bad_mask.any():
        return df.copy()
    if "ngay" in df.columns and "stt" in df.columns:
        ngay_key = df["ngay"].astype(str).str.slice(0, 10)
        stt_notna = df["stt"].notna()
        bad_pairs = set(
            zip(ngay_key[bad_mask & stt_notna], df.loc[bad_mask & stt_notna, "stt"])
        )
        in_bad_order = pd.Series(
            [
                (nk, sv) in bad_pairs if pd.notna(sv) else False
                for nk, sv in zip(ngay_key, df["stt"])
            ],
            index=df.index,
        )
        return df[~(bad_mask | in_bad_order)].copy()
    if "stt" in df.columns:
        bad_stts = set(df.loc[bad_mask & df["stt"].notna(), "stt"])
        return df[~(bad_mask | df["stt"].isin(bad_stts))].copy()
    return df[~bad_mask].copy()


def _register_clean_phone_tables(
    context: BusinessContext,
) -> tuple[str, str, pd.DataFrame]:
    """Build and register cleaned `so_ban_hang` + canonicalized `so_ban_hang_ghi_tat` DuckDB views."""
    sbh_raw = context.tables["so_ban_hang"]
    gt_raw = context.tables.get("so_ban_hang_ghi_tat")
    sbh_clean = _filter_valid_phone_orders(sbh_raw)

    # Map both ghi_tat and full ten_hang (case/accent/whitespace-insensitive) to canonical key
    norm_to_key: dict[str, str] = {}
    gt_rows: dict[str, tuple[str, str, Any]] = {}
    if gt_raw is not None:
        for _, r in gt_raw.iterrows():
            gt_val = "" if pd.isna(r.get("ghi_tat")) else str(r.get("ghi_tat")).strip()
            full_val = (
                "" if pd.isna(r.get("ten_hang")) else str(r.get("ten_hang")).strip()
            )
            loai_val = r.get("loai")
            if not gt_val and not full_val:
                continue
            canon_key = gt_val or full_val
            canon_full = full_val or canon_key
            if canon_key not in gt_rows:
                gt_rows[canon_key] = (canon_key, canon_full, loai_val)
            for alias in (gt_val, canon_full):
                ak = norm_vn(alias)
                if ak and ak not in norm_to_key:
                    norm_to_key[ak] = canon_key

    if "hang" in sbh_clean.columns and norm_to_key:
        sbh_clean["hang"] = sbh_clean["hang"].map(
            lambda x: norm_to_key.get(norm_vn(x), x) if pd.notna(x) else x
        )
    gt_clean = pd.DataFrame(
        [{"ghi_tat": k, "ten_hang": fn, "loai": lv} for k, fn, lv in gt_rows.values()],
        columns=["ghi_tat", "ten_hang", "loai"],
    )
    clean_sbh_name = "__m12_clean_sbh"
    clean_gt_name = "__m12_clean_gt"
    try:
        context.duckdb_conn.register(clean_sbh_name, sbh_clean)
        context.duckdb_conn.register(clean_gt_name, gt_clean)
    except Exception:
        pass
    return clean_sbh_name, clean_gt_name, sbh_clean


def _compute_order_khach_tra(df: pd.DataFrame, t_month: str) -> int | None:
    """Compute monthly net customer payments across valid non-cancelled orders."""
    if "ngay" not in df.columns or "khach_tra" not in df.columns:
        return None
    valid_df = _filter_valid_phone_orders(df)
    sub = valid_df[valid_df["ngay"].astype(str).str.startswith(t_month)].copy()
    if sub.empty:
        return None
    sub["_kt"] = sub["khach_tra"].map(clean_vietnamese_currency)
    sub = sub[sub["_kt"].notna()]
    if sub.empty:
        return None
    if "stt" in sub.columns and sub["stt"].notna().all():
        order_sums = sub.groupby(["ngay", "stt"], dropna=False)["_kt"].max()
        return int(round(float(order_sums.sum())))
    return int(round(float(sub["_kt"].sum())))


def _resolve_phone_store_analytics(
    q_norm: str, question: str, context: BusinessContext, as_of_date: str | None
) -> Tuple[str, Any, str | None] | None:
    """Schema-driven phone store analytics for `so_ban_hang` + `so_ban_hang_ghi_tat`."""
    if (
        "so_ban_hang" not in context.tables
        or "so_ban_hang_ghi_tat" not in context.tables
    ):
        return None

    sbh_name, gt_name, sbh_df = _register_clean_phone_tables(context)
    gt_df = context.tables["so_ban_hang_ghi_tat"]
    sbh_cols = sbh_df.columns
    t_month = extract_explicit_month(question, as_of_date)

    # 1. Trade-in deduction value (thu_may_cu)
    if (
        "thu_may_cu" in sbh_cols
        and t_month
        and any(
            k in q_norm for k in ("thu may cu", "thu mua may cu", "tien thu may cu")
        )
    ):
        sql = f"""
            SELECT sum(TRY_CAST(thu_may_cu AS DOUBLE))
            FROM {sbh_name}
            WHERE cast(ngay as varchar) LIKE '{t_month}%'
        """
        try:
            res = context.query(sql)
            if not res.empty and pd.notna(res.iloc[0, 0]):
                return "number", int(round(float(res.iloc[0, 0]))), None
        except Exception:
            pass

    # 2. Customer net payment (khach_tra) — general schema logic with order deduplication
    if (
        "khach_tra" in sbh_cols
        and t_month
        and not any(
            k in q_norm
            for k in (
                "tra gop",
                "may khach",
                "bao nhieu khach",
                "bn khach",
                "nguoi",
                "trung binh",
                "binh quan",
                "moi may",
            )
        )
        and any(
            k in q_norm
            for k in (
                "khach phai tra",
                "khach phai tra het",
                "tien khach phai tra",
                "khach tra tong",
                "tong tien khach tra",
                "khach tra bn",
                "khach tra bao nhieu",
            )
        )
    ):
        val = _compute_order_khach_tra(sbh_df, t_month)
        if val is not None:
            return "number", val, None

    # 3. Peak sales day in month (must precede Store Total so "bữa nào ... nhiều tiền nhứt" never falls into sum)
    if (
        "thanh_tien" in sbh_cols
        and "ngay" in sbh_cols
        and t_month
        and (
            any(
                k in q_norm
                for k in (
                    "bua nao tiem ban duoc nhieu tien",
                    "ngay nao ban duoc nhieu tien nhut",
                    "ngay nao dthu cao nhat",
                )
            )
            or bool(
                re.search(
                    r"\b(?:bua|ngay|hom)\s+(?:nao|mo)\b.*?\b(?:ban|dthu|doanh\s+thu)\b.*?\b(?:cao|nhieu\s+tien)\s+(?:nhat|nhut)\b",
                    q_norm,
                )
            )
        )
    ):
        sql = f"""
            SELECT cast(ngay as varchar)
            FROM {sbh_name}
            WHERE cast(ngay as varchar) LIKE '{t_month}%'
            GROUP BY ngay
            ORDER BY sum(TRY_CAST(thanh_tien AS DOUBLE)) DESC
            LIMIT 1
        """
        try:
            res = context.query(sql)
            if not res.empty and pd.notna(res.iloc[0, 0]):
                return "date", str(res.iloc[0, 0])[:10], None
        except Exception:
            pass

    # 4. Store total revenue from `thanh_tien` when asked cleanly (guard against product-scoped & peak queries)
    if (
        "thanh_tien" in sbh_cols
        and "ngay" in sbh_cols
        and t_month
        and not any(
            k in q_norm
            for k in (
                "tra gop",
                "thu may cu",
                "khach tra",
                "khach phai tra",
                "bao hanh",
                "trung binh",
                "binh quan",
                "%",
                "phan tram",
                "bua nao",
                "ngay nao",
                "hom nao",
                "mon nao",
                "san pham nao",
                "nhut",
                "cao nhat",
                "nhieu nhat",
            )
        )
        and bool(
            re.search(
                r"\b(?:tiem\s+ban\s+(?:duoc|dc)|duoc\s+tong\s+cong|tong\s+cong\s+bao\s+nhieu\s+tien|tong\s+doanh\s+thu)\b",
                q_norm,
            )
        )
        and any(k in q_norm for k in ("tien", "doanh thu", "dthu"))
    ):
        sql = f"""
            SELECT sum(TRY_CAST(thanh_tien AS DOUBLE))
            FROM {sbh_name}
            WHERE cast(ngay as varchar) LIKE '{t_month}%'
        """
        try:
            res = context.query(sql)
            if not res.empty and pd.notna(res.iloc[0, 0]):
                return "number", int(round(float(res.iloc[0, 0]))), None
        except Exception:
            pass

    # 5. Used phone sales count
    if (
        t_month
        and any(k in q_norm for k in ("dien thoai cu", "may cu"))
        and any(k in q_norm for k in ("may may", "may chiec", "bao nhieu"))
        and not any(
            k in q_norm
            for k in (
                "gop ca may moi",
                "ca may moi",
                "khong ke phu kien",
                "tien",
                "doanh thu",
                "dthu",
                "%",
                "phan tram",
            )
        )
    ):
        sql = f"""
            SELECT sum(TRY_CAST(b.sl AS BIGINT))
            FROM {sbh_name} b
            JOIN {gt_name} g ON {ws_norm_sql("b.hang")} = {ws_norm_sql("g.ghi_tat")}
            WHERE g.loai = 'máy cũ'
              AND cast(b.ngay as varchar) LIKE '{t_month}%'
        """
        try:
            res = context.query(sql)
            if not res.empty and pd.notna(res.iloc[0, 0]):
                return "number", int(res.iloc[0, 0]), None
        except Exception:
            pass

    # 6. All phones sold (excluding accessories)
    if (
        t_month
        and not any(
            g in q_norm
            for g in (
                "%",
                "phan tram",
                "trung binh",
                "binh quan",
                "tien",
                "doanh thu",
                "dthu",
            )
        )
        and any(
            k in q_norm
            for k in (
                "khong ke phu kien",
                "khong tinh phu kien",
                "gop ca may moi voi may cu",
                "ca may moi lan may cu",
                "bao nhieu chiec dien thoai",
                "may chiec dien thoai",
            )
        )
    ):
        sql = f"""
            SELECT sum(TRY_CAST(b.sl AS BIGINT))
            FROM {sbh_name} b
            JOIN {gt_name} g ON {ws_norm_sql("b.hang")} = {ws_norm_sql("g.ghi_tat")}
            WHERE g.loai IN ('máy mới', 'máy cũ')
              AND cast(b.ngay as varchar) LIKE '{t_month}%'
        """
        try:
            res = context.query(sql)
            if not res.empty and pd.notna(res.iloc[0, 0]):
                return "number", int(res.iloc[0, 0]), None
        except Exception:
            pass

    # 7. Dynamic specific product lookup from `so_ban_hang_ghi_tat.ten_hang`
    qty_triggers = (
        "may cai",
        "may chiec",
        "bnhiu chiec",
        "bao nhieu chiec",
        "ban duoc bao nhieu",
        "may may",
        "bnhiu may",
        "bao nhieu may",
        "bao nhiu may",
        "bn may",
    )
    if (
        t_month
        and "ten_hang" in gt_df.columns
        and any(k in q_norm for k in qty_triggers)
    ):
        names = gt_df["ten_hang"].dropna().astype(str).unique().tolist()
        matched_raw_names: list[str] = []
        # Pass 1: exact normalized product name match (longest first)
        full_candidates = sorted(
            (
                (strip_accents(name), name)
                for name in names
                if len(strip_accents(name)) >= 4
            ),
            key=lambda x: len(x[0]),
            reverse=True,
        )
        for name_norm, raw_name in full_candidates:
            if name_norm in q_norm:
                matched_raw_names = [raw_name]
                break

        # Pass 2: base-model family match after stripping capacity/color/condition suffixes
        if not matched_raw_names:
            family_map: dict[str, list[str]] = {}
            for raw_name in names:
                base = strip_accents(raw_name)
                base = re.sub(
                    r"\s+(?:5g|4g|\d+gb|cu(?:\s*\d+%?)?|titan(?:\s+\w+)*|trang|den|xam|xanh(?:\s+(?:la|duong))?|tim|hong|vang|bac)\b",
                    "",
                    base,
                )
                base = re.sub(r"\s+", " ", base).strip()
                if len(base) >= 4:
                    family_map.setdefault(base, []).append(raw_name)
            for base_norm in sorted(family_map, key=len, reverse=True):
                if re.search(rf"(?<!\w){re.escape(base_norm)}(?!\w)", q_norm):
                    matched_raw_names = family_map[base_norm]
                    break

        if matched_raw_names:
            in_list = ", ".join(
                "'" + n.replace("'", "''") + "'" for n in matched_raw_names
            )
            sql = f"""
                SELECT sum(TRY_CAST(b.sl AS BIGINT))
                FROM {sbh_name} b
                JOIN {gt_name} g ON {ws_norm_sql("b.hang")} = {ws_norm_sql("g.ghi_tat")}
                WHERE g.ten_hang IN ({in_list})
                  AND cast(b.ngay as varchar) LIKE '{t_month}%'
            """
            try:
                res = context.query(sql)
                if not res.empty and pd.notna(res.iloc[0, 0]):
                    return "number", int(res.iloc[0, 0]), None
            except Exception:
                pass
    return None


def _resolve_warranty_and_tradein(
    q_norm: str, question: str, context: BusinessContext, as_of_date: str | None
) -> Tuple[str, Any, str | None] | None:
    """Resolve trade-in order share and quarterly/monthly warranty statistics."""
    if "thu_may_cu" in context.tables and "so_ban_hang" in context.tables:
        if any(k in q_norm for k in ("thu lai may cu", "thu may cu")) and any(
            k in q_norm for k in ("%", "phan tram", "chiem may")
        ):
            t_month = extract_explicit_month(question, as_of_date)
            if t_month:
                sql = f"""
                    WITH tot AS (
                        SELECT count(distinct stt) as c FROM __m12_clean_sbh WHERE cast(ngay as varchar) LIKE '{t_month}%'
                    ),
                    thu AS (
                        SELECT count(distinct tru_vao_stt_so_ban) as c FROM thu_may_cu
                        WHERE cast(ngay as varchar) LIKE '{t_month}%' AND tru_vao_stt_so_ban IS NOT NULL
                    )
                    SELECT thu.c * 100.0 / NULLIF(tot.c, 0) FROM thu, tot
                """
                try:
                    _register_clean_phone_tables(context)
                    res = context.query(sql)
                    if not res.empty and pd.notna(res.iloc[0, 0]):
                        return "percent", round(float(res.iloc[0, 0]), 2), None
                except Exception:
                    pass

    if "so_no_bao_hanh" in context.tables and "so_ban_hang" in context.tables:
        if "bao hanh" in q_norm and any(
            k in q_norm for k in ("%", "phan tram", "chiem bnhiu")
        ):
            period = parse_time_expressions(question, as_of_date)
            q_bounds = (
                _quarter_to_bounds(period.quarter_str) if period.quarter_str else None
            )
            if q_bounds and "so_ban_hang_ghi_tat" in context.tables:
                sql = f"""
                    WITH bh AS (
                        SELECT count(*) as c FROM so_no_bao_hanh
                        WHERE ngay_nhan >= '{q_bounds[0]}' AND ngay_nhan <= '{q_bounds[1]}'
                    ),
                    phones AS (
                        SELECT sum(TRY_CAST(b.sl AS BIGINT)) as c
                        FROM __m12_clean_sbh b
                        JOIN __m12_clean_gt g ON {ws_norm_sql("b.hang")} = {ws_norm_sql("g.ghi_tat")}
                        WHERE g.loai IN ('máy mới', 'máy cũ')
                          AND b.ngay >= '{q_bounds[0]}' AND b.ngay <= '{q_bounds[1]}'
                    )
                    SELECT bh.c * 100.0 / NULLIF(phones.c, 0) FROM bh, phones
                """
                try:
                    _register_clean_phone_tables(context)
                    res = context.query(sql)
                    if not res.empty and pd.notna(res.iloc[0, 0]):
                        return "percent", round(float(res.iloc[0, 0]), 2), None
                except Exception:
                    pass

        if (
            any(k in q_norm for k in ("may ban ra", "may ban thang", "ban thang"))
            and "bao hanh" in q_norm
        ):
            t_month = extract_explicit_month(question, as_of_date)
            if t_month and as_of_date:
                sql = f"""
                    SELECT count(distinct imei)
                    FROM so_no_bao_hanh
                    WHERE cast(ngay_mua as varchar) LIKE '{t_month}%'
                      AND ngay_nhan <= '{as_of_date}'
                """
                try:
                    res = context.query(sql)
                    if not res.empty and pd.notna(res.iloc[0, 0]):
                        return "number", int(res.iloc[0, 0]), None
                except Exception:
                    pass
    return None


def _resolve_top_supplier(
    q_norm: str, question: str, context: BusinessContext, as_of_date: str | None
) -> Tuple[str, Any, str | None] | None:
    """Rank top supplier by import spend from any purchase table with `nha_cung_cap`."""
    if not any(k in q_norm for k in ("nha cung cap chinh", "nha cung cap lon nhat")):
        return None
    t_month = extract_explicit_month(question, as_of_date)
    if not t_month:
        return None

    for tname, df in context.tables.items():
        if tname.endswith("_sheet1") or "nha_cung_cap" not in df.columns:
            continue
        amt_col = next(
            (
                c
                for c in ("thanh_tien", "tong_tien_phieu", "tong_tien", "tri_gia")
                if c in df.columns
            ),
            None,
        )
        date_col = next(
            (
                c
                for c in ("ngay_nhap", "ngay", "thoi_gian", "ngay_lap")
                if c in df.columns
            ),
            None,
        )
        if not (amt_col and date_col):
            continue
        sql = f"""
            SELECT nha_cung_cap, sum(TRY_CAST({amt_col} AS DOUBLE)) as tot
            FROM {tname}
            WHERE cast({date_col} as varchar) LIKE '{t_month}%'
              AND nha_cung_cap IS NOT NULL
            GROUP BY nha_cung_cap
            ORDER BY tot DESC
            LIMIT 1
        """
        try:
            row = _fetch_one_row(context.duckdb_conn, sql)
            if row and row[0]:
                return "string", str(row[0]), None
        except Exception:
            pass
    return None


def _resolve_orders_above_threshold(
    q_norm: str, question: str, context: BusinessContext, as_of_date: str | None
) -> Tuple[str, Any, str | None] | None:
    """Count distinct orders meeting a dynamic minimum VND threshold in a target month."""
    if not any(k in q_norm for k in ("bao nhieu don", "may don", "so don", "bn don")):
        return None
    threshold = _extract_money_threshold(q_norm)
    t_month = extract_explicit_month(question, as_of_date)
    if threshold is None or not t_month:
        return None

    preferred_tables = [
        t
        for t in context.tables
        if not t.endswith("_sheet1") and not re.search(r"_(?:202[4-9]|[0-9]+)$", t)
    ]
    for tname in preferred_tables:
        df = context.tables[tname]
        if not any(k in tname for k in ("ban_hang", "don_hang", "chi_tiet")):
            continue
        ord_col = next(
            (
                c
                for c in (
                    "hd",
                    "ma_don_hang",
                    "ma_hoa_don",
                    "ma_bill",
                    "ma_don",
                    "so_ct",
                )
                if c in df.columns
            ),
            None,
        )
        amt_col = next(
            (
                c
                for c in ("tt", "thanh_tien", "tong_tien", "tri_gia", "amount")
                if c in df.columns
            ),
            None,
        )
        date_col = next(
            (
                c
                for c in ("ngay", "ngay_dat_hang", "ngay_lap", "ngay_ban", "thoi_gian")
                if c in df.columns
            ),
            None,
        )
        status_col = pick_order_status_col(df)
        if not (ord_col and amt_col and date_col):
            continue
        status_clause = get_valid_sales_status_clause(
            status_col, order_col=ord_col, table=tname
        )
        adj_clause = (
            "AND (ten_hang IS NULL OR (ten_hang NOT IN ('Voucher', 'Phí ship') AND ten_hang NOT LIKE 'Dùng %'))"
            if "ten_hang" in df.columns
            else ""
        )
        sql = f"""
            SELECT count(*)
            FROM (
                SELECT {ord_col}
                FROM {tname}
                WHERE cast({date_col} as varchar) LIKE '{t_month}%'
                  AND {ord_col} IS NOT NULL
                  {status_clause}
                  {adj_clause}
                GROUP BY {ord_col}
                HAVING sum(TRY_CAST({amt_col} AS DOUBLE)) >= {threshold}
            )
        """
        try:
            row = _fetch_one_row(context.duckdb_conn, sql)
            if row and row[0] is not None:
                return "number", int(row[0]), None
        except Exception:
            pass
    return None


def _resolve_the_kho_stockouts(
    q_norm: str, question: str, context: BusinessContext, as_of_date: str | None
) -> Tuple[str, Any, str | None] | None:
    """Count out-of-stock SKUs (`ton_cuoi <= 0`) from `the_kho` ledger or `as_of_date` snapshot table."""
    if not any(k in q_norm for k in ("chay hang", "dut hang", "het hang")):
        return None

    explicit_date = _extract_explicit_iso_date(question)
    if "the_kho" in context.tables:
        cols = set(context.tables["the_kho"].columns)
        if {"ma_hang_hoa", "ton_cuoi", "ngay_gio_phat_sinh"} <= cols:
            target_date = explicit_date or as_of_date
            if not target_date:
                return None

            order_expr = (
                "ngay_gio_phat_sinh DESC, ma_giao_dich_kho DESC"
                if "ma_giao_dich_kho" in cols
                else "ngay_gio_phat_sinh DESC"
            )
            sql = f"""
                WITH ranked AS (
                    SELECT ma_hang_hoa, ton_cuoi,
                           ROW_NUMBER() OVER (PARTITION BY ma_hang_hoa ORDER BY {order_expr}) as rn
                    FROM the_kho
                    WHERE ngay_gio_phat_sinh <= '{target_date} 23:59:59'
                )
                SELECT count(CASE WHEN TRY_CAST(ton_cuoi AS DOUBLE) = 0 THEN 1 END)
                FROM ranked
                WHERE rn = 1
            """
            try:
                row = _fetch_one_row(context.duckdb_conn, sql)
                if row and row[0] is not None:
                    return "number", int(row[0]), None
            except Exception:
                pass
        return None

    # Snapshot closing-balance fallback (`bao_cao_ton_kho` / `ton_kho`): only valid at `as_of_date`
    if not as_of_date or (
        explicit_date is not None and explicit_date != as_of_date[:10]
    ):
        return None

    excluded_codes: set[str] = set()
    excluded_names: set[str] = set()
    for ctname, cdf in context.tables.items():
        if ctname.endswith("_sheet1") or ctname == "calendar":
            continue
        if not any(
            k in ctname for k in ("danh_sach", "danh_muc", "hang_hoa", "san_pham")
        ):
            continue
        c_cols = {c.lower(): c for c in cdf.columns}
        cat_type_c = next(
            (
                c_cols[k]
                for k in ("loai_hang_hoa", "loai_sp", "loai", "phan_loai")
                if k in c_cols
            ),
            None,
        )
        cid_c = next(
            (
                c_cols[k]
                for k in ("ma_hang", "ma_hang_hoa", "ma_sp", "ma_sku")
                if k in c_cols
            ),
            None,
        )
        cname_c = next(
            (
                c_cols[k]
                for k in ("ten_hang", "ten_hang_hoa", "ten_san_pham", "san_pham")
                if k in c_cols
            ),
            None,
        )
        if not (cat_type_c and (cid_c or cname_c)):
            continue
        for _, crow in cdf.iterrows():
            cval = (
                strip_accents(str(crow[cat_type_c]))
                if pd.notna(crow[cat_type_c])
                else ""
            )
            if any(
                tok in cval for tok in ("che bien", "pha che", "dich vu", "khong ton")
            ):
                if cid_c and pd.notna(crow[cid_c]):
                    excluded_codes.add(strip_accents(str(crow[cid_c]).strip()))
                if cname_c and pd.notna(crow[cname_c]):
                    excluded_names.add(strip_accents(str(crow[cname_c]).strip()))

    snap_candidates = [
        tname
        for tname in (
            ["bao_cao_ton_kho"]
            + [
                t
                for t in context.tables
                if t != "bao_cao_ton_kho"
                and "ton_kho" in t
                and not t.endswith("_sheet1")
            ]
        )
        if tname in context.tables
    ]
    for tname in snap_candidates:
        df = context.tables[tname]
        cols = {c.lower(): c for c in df.columns}
        val_c = next(
            (
                cols[k]
                for k in (
                    "ton_cuoi_ky",
                    "ton_thuc",
                    "ton_cuoi",
                    "ton_kho",
                    "so_luong_ton",
                )
                if k in cols
            ),
            None,
        )
        if not val_c:
            continue
        sku_c = next(
            (
                cols[k]
                for k in ("ma_hang", "ma_hang_hoa", "ma_sp", "ma_sku")
                if k in cols
            ),
            None,
        )
        name_c = next(
            (
                cols[k]
                for k in ("ten_hang", "ten_hang_hoa", "ten_san_pham", "hang_hoa")
                if k in cols
            ),
            None,
        )
        if not (sku_c or name_c):
            continue
        stockout_count = 0
        valid_rows = 0
        for _, row in df.iterrows():
            sku_s = (
                strip_accents(str(row[sku_c]).strip())
                if (sku_c and pd.notna(row[sku_c]))
                else ""
            )
            name_s = (
                strip_accents(str(row[name_c]).strip())
                if (name_c and pd.notna(row[name_c]))
                else ""
            )
            if sku_c and not sku_s:
                continue
            if name_s in ("tong", "tong cong", "total"):
                continue
            if (sku_s and sku_s in excluded_codes) or (
                name_s and name_s in excluded_names
            ):
                continue
            val = pd.to_numeric(row[val_c], errors="coerce")
            if pd.isna(val):
                continue
            valid_rows += 1
            if float(val) <= 0:
                stockout_count += 1
        if valid_rows > 0:
            return "number", stockout_count, None
    return None


def _is_inventory_stock_query(q_norm: str) -> bool:
    """Detect current/closing inventory stock queries cleanly without catching debt or sales-count queries."""
    if any(
        k in q_norm
        for k in (
            "chay hang",
            "dut hang",
            "het hang",
            "lo ton",
            "het han",
            "qua han",
            "sap het han",
            "gia von",
            "nha cung cap",
            "ncc",
            "bao hanh",
            "tra gop",
            "phai thu",
            "phai tra",
            "du no",
            "cong no",
            "con no",
            "khach no",
            "no cua hang",
            "tien no",
            "doanh thu",
            "bao nhieu don",
            "may don",
        )
    ):
        return False
    explicit_stock = bool(
        re.search(
            r"\b(?:ton\s+kho|trong\s+kho|ton\s+cuoi|hang\s+ton|con\s+ton|hop\s+chan|vien\s+le|cai\s+le|goi\s+le|vi\s+le|chai\s+le)\b",
            q_norm,
        )
    ) or bool(re.search(r"\bkho\b.*\bton\b|\bton\b.*\bkho\b", q_norm))
    remaining_stock = bool(
        re.search(r"\bcon\s+(?:lai\s+|ton\s+)?(?:may|bao\s+nhieu|bnhiu|bn)\b", q_norm)
    ) and any(
        k in q_norm for k in ("cuoi ngay", "het hom nay", "kho", "trong kho", "ton")
    )
    return explicit_stock or remaining_stock


def _collect_inventory_candidates(
    context: BusinessContext,
) -> list[tuple[str, str, str]]:
    """Collect (product_name_lower, table, column) candidates across catalog and inventory tables."""
    from zaic_2026_datamate.intent_parser import get_all_product_names

    candidates = list(get_all_product_names(context))
    seen = {c[0] for c in candidates}
    extra_cols = {"product", "ten_hang", "hang_hoa", "ten_san_pham", "ten_sp"}
    for tname, df in context.tables.items():
        if tname == "calendar" or tname.endswith("_sheet1") or "khach" in tname:
            continue
        for col in df.columns:
            cl = col.lower()
            if cl in extra_cols or (cl == "ten_goi" and "san_pham" in tname):
                for val in df[col].dropna().unique():
                    s_l = str(val).strip().lower()
                    if len(s_l) >= 3 and s_l not in seen:
                        seen.add(s_l)
                        candidates.append((s_l, tname, col))
    return candidates


def _lookup_sku_for_product(
    context: BusinessContext, product_name: str, norm_fn: Any
) -> str | None:
    """Resolve SKU/item ID for a matched product name across catalog tables."""
    target_norm = norm_fn(product_name)
    for tname, df in context.tables.items():
        if tname.endswith("_sheet1") or not any(
            k in tname for k in ("san_pham", "hang_hoa", "hang_h_a")
        ):
            continue
        cols = {c.lower(): c for c in df.columns}
        code_c = next(
            (
                cols[k]
                for k in (
                    "ma_sku",
                    "ma_hang_hoa",
                    "ma_san_pham_ban",
                    "ma_hang",
                    "ma_sp",
                    "id_sp",
                )
                if k in cols
            ),
            None,
        )
        name_c = next(
            (
                cols[k]
                for k in (
                    "ten_san_pham",
                    "ten_goi",
                    "ten_hang_hoa",
                    "ten_hang",
                    "ten_sp",
                    "hang_hoa",
                    "product",
                )
                if k in cols
            ),
            None,
        )
        if not (code_c and name_c):
            continue
        for _, row in df.iterrows():
            if norm_fn(row[name_c]) == target_norm:
                code_val = str(row[code_c]).strip()
                if code_val:
                    return code_val
    return None


def _extract_inventory_split_units(
    q_norm: str,
    product_name: str,
    factors: dict[str, float],
    norm_fn: Any,
    alnum_fn: Any,
) -> tuple[str | None, str | None]:
    """Extract explicit `(major_unit, minor_unit)` pair from an inventory split question."""
    if not factors:
        return None, None
    q_clean = alnum_fn(q_norm)
    p_clean = alnum_fn(norm_fn(product_name))
    if p_clean and p_clean in q_clean:
        q_clean = q_clean.replace(p_clean, " ")
    q_clean = re.sub(
        r"\b(?:hop|thung)\s+\d+\s*(?:vi|goi|ong|tuyp|lon|chai)\s*[x*]\s*\d+\s*(?:vien|goi|ong|lon|chai)\b",
        " ",
        q_clean,
    )
    q_clean = re.sub(r"\s+", " ", q_clean).strip()
    unit_choices = sorted([u for u in factors if u], key=len, reverse=True)
    if not unit_choices:
        return None, None

    u_pat = "|".join(re.escape(u) for u in unit_choices)
    minor_u: str | None = None
    major_u: str | None = None
    m_min = re.search(rf"\b({u_pat})\s+le\b", q_clean) or re.search(
        rf"\ble\s+(?:bao\s+nhieu|may|bn|bnhiu)?\s*({u_pat})\b", q_clean
    )
    if m_min:
        minor_u = m_min.group(1)
    m_maj = re.search(rf"\b({u_pat})\s+(?:chan|nguyen)\b", q_clean)
    if m_maj:
        major_u = m_maj.group(1)
    elif minor_u:
        minor_f_tmp = factors.get(minor_u, 1.0)
        for cand_u in re.findall(
            rf"\b(?:bao\s+nhieu|may|bn|bnhiu|con|duoc)\s+({u_pat})\b", q_clean
        ):
            if cand_u != minor_u and factors.get(cand_u, 0.0) > minor_f_tmp:
                major_u = cand_u
                break
    return major_u, minor_u


def _format_inventory_answer(
    rem_qty: float,
    q_norm: str,
    context: BusinessContext,
    product_name: str,
    product_code: str | None,
) -> Tuple[str, Any, str | None] | None:
    """Format scalar or [boxes, pieces] stock quantity cleanly."""
    import math

    from zaic_2026_datamate.price_change import (
        alnum_phrase,
        build_product_unit_conversions,
        norm_vn,
    )

    if not math.isfinite(rem_qty) or rem_qty < 0:
        return None
    rem_int = int(round(rem_qty))
    has_split_marker = bool(
        re.search(r"\b(?:chan|nguyen)\b", q_norm) and re.search(r"\ble\b", q_norm)
    ) or any(
        k in q_norm
        for k in (
            "hop chan",
            "vi chan",
            "thung chan",
            "hop nguyen",
            "vi nguyen",
            "vien le",
            "vi le",
            "cai le",
            "goi le",
            "chai le",
            "lon le",
        )
    )
    if not has_split_marker:
        return "number", rem_int, None

    _, factors = build_product_unit_conversions(context, product_name, product_code)
    major_u, minor_u = _extract_inventory_split_units(
        q_norm, product_name, factors, norm_vn, alnum_phrase
    )
    minor_factor = factors.get(minor_u, 1.0) if minor_u else 1.0
    major_factor = factors.get(major_u) if major_u else None
    if major_factor is not None and minor_factor > 0 and major_factor > minor_factor:
        minor_total = (
            rem_int
            if abs(minor_factor - 1.0) < 1e-9
            else int(math.floor(rem_qty / minor_factor + 1e-9))
        )
        step = int(round(major_factor / minor_factor))
        if step > 1:
            return "list", [minor_total // step, minor_total % step], None

    factor = factors.get("hop")
    if (factor is None or factor <= 1) and factors:
        large = [
            v
            for k, v in factors.items()
            if v > 1 and (k in norm_vn(q_norm) or k in ("thung", "hop", "loc"))
        ]
        factor = max(large) if large else max(factors.values())
    if factor and factor > 1:
        f_int = int(round(factor))
        return "list", [rem_int // f_int, rem_int % f_int], None
    return None


def _resolve_the_kho_item_stock(
    context: BusinessContext, sku: str, target_date: str
) -> float | None:
    """Query closing stock across all branches for one SKU from `the_kho`."""
    cols = set(context.tables["the_kho"].columns)
    if not {"ma_hang_hoa", "ton_cuoi", "ngay_gio_phat_sinh"} <= cols:
        return None
    part_cols = "ma_hang_hoa, ma_chi_nhanh" if "ma_chi_nhanh" in cols else "ma_hang_hoa"
    order_expr = (
        "ngay_gio_phat_sinh DESC, ma_giao_dich_kho DESC"
        if "ma_giao_dich_kho" in cols
        else "ngay_gio_phat_sinh DESC"
    )
    sku_esc = sku.replace("'", "''")
    sql = f"""
        WITH ranked AS (
            SELECT ton_cuoi,
                   ROW_NUMBER() OVER (PARTITION BY {part_cols} ORDER BY {order_expr}) AS rn
            FROM the_kho
            WHERE ma_hang_hoa = '{sku_esc}'
              AND ngay_gio_phat_sinh <= '{target_date} 23:59:59'
        )
        SELECT SUM(TRY_CAST(ton_cuoi AS DOUBLE)) FROM ranked WHERE rn = 1
    """
    try:
        row = _fetch_one_row(context.duckdb_conn, sql)
        if row and row[0] is not None:
            return float(row[0])
    except Exception:
        pass
    return None


def _resolve_lot_ledger_stock(
    context: BusinessContext, sku: str, target_date: str
) -> float | None:
    """Compute remaining lot-level balance (imports - net lot sales) up to target_date."""
    tbls = [t for t in context.tables if not t.endswith("_sheet1")]
    nhap_hdr = next((t for t in tbls if t.endswith("don_nhap_hang")), None)
    nhap_dtl = next((t for t in tbls if t.endswith("don_nhap_hang_chi_tiet")), None)
    lot_sales = next((t for t in tbls if t.endswith("don_hang_lo")), None)
    if not (nhap_hdr and nhap_dtl and lot_sales):
        return None

    hdr_cols = set(context.tables[nhap_hdr].columns)
    dtl_cols = set(context.tables[nhap_dtl].columns)
    lot_cols = set(context.tables[lot_sales].columns)
    if not (
        {"ma_don_nhap", "ngay_nhap"} <= hdr_cols
        and {"ma_don_nhap", "ma_sku", "ma_lo", "so_luong_nhap"} <= dtl_cols
        and {"ma_don_hang", "ma_lo", "so_luong"} <= lot_cols
    ):
        return None

    order_date_selects: list[str] = []
    for t in tbls:
        tc = set(context.tables[t].columns)
        st_c = next(
            (
                c
                for c in ("trang_thai", "tinh_trang", "tinh_trang_don", "xu_ly")
                if c in tc
            ),
            None,
        )
        if "don_hang" in t and "ma_don_hang" in tc and "ngay_dat_hang" in tc:
            st_clause = get_valid_sales_status_clause(
                st_c, order_col="ma_don_hang", table=t
            )
            order_date_selects.append(
                f"SELECT ma_don_hang, CAST(ngay_dat_hang AS DATE) AS d FROM {t} WHERE 1=1 {st_clause}"
            )
        elif "don_hang" in t and "ma_hoa_don" in tc and "ngay_lap" in tc:
            st_clause = get_valid_sales_status_clause(
                st_c, order_col="ma_hoa_don", table=t
            )
            order_date_selects.append(
                f"SELECT ma_hoa_don AS ma_don_hang, CAST(ngay_lap AS DATE) AS d FROM {t} WHERE 1=1 {st_clause}"
            )
    if not order_date_selects:
        return None

    order_cte = " UNION ".join(order_date_selects)
    ret_expr = (
        "COALESCE(TRY_CAST(ls.so_luong_tra AS DOUBLE), 0.0)"
        if "so_luong_tra" in lot_cols
        else "0.0"
    )
    sku_esc = sku.replace("'", "''")
    sql = f"""
        WITH sku_lots AS (
            SELECT d.ma_lo, SUM(TRY_CAST(d.so_luong_nhap AS DOUBLE)) AS total_in
            FROM {nhap_dtl} d
            JOIN {nhap_hdr} h ON d.ma_don_nhap = h.ma_don_nhap
            WHERE d.ma_sku = '{sku_esc}'
              AND CAST(h.ngay_nhap AS DATE) <= DATE '{target_date}'
            GROUP BY d.ma_lo
        ),
        ord_dates AS (
            SELECT ma_don_hang, MIN(d) AS order_date FROM ({order_cte}) u GROUP BY ma_don_hang
        ),
        lot_out AS (
            SELECT ls.ma_lo,
                   SUM(TRY_CAST(ls.so_luong AS DOUBLE) - {ret_expr}) AS total_out
            FROM {lot_sales} ls
            JOIN ord_dates od ON ls.ma_don_hang = od.ma_don_hang
            WHERE ls.ma_lo IN (SELECT ma_lo FROM sku_lots)
              AND od.order_date <= DATE '{target_date}'
            GROUP BY ls.ma_lo
        )
        SELECT COALESCE(SUM(sl.total_in), 0.0) - COALESCE(SUM(lo.total_out), 0.0)
        FROM sku_lots sl
        LEFT JOIN lot_out lo ON sl.ma_lo = lo.ma_lo
    """
    try:
        row = _fetch_one_row(context.duckdb_conn, sql)
        if row and row[0] is not None:
            return float(row[0])
    except Exception:
        pass
    return None


def _resolve_snapshot_ton_kho(
    context: BusinessContext, product_name: str, norm_fn: Any
) -> float | None:
    """Lookup closing balance from snapshot `ton_kho` / `bao_cao_ton_kho` table."""
    target_norm = norm_fn(product_name)
    for tname, df in context.tables.items():
        if tname.endswith("_sheet1") or "ton_kho" not in tname:
            continue
        cols = {c.lower(): c for c in df.columns}
        item_c = next(
            (
                cols[k]
                for k in ("ten_hang", "hang_hoa", "product", "ten_sp", "ten_san_pham")
                if k in cols
            ),
            None,
        )
        val_c = next(
            (
                cols[k]
                for k in ("ton_thuc", "ton_cuoi_ky", "ton_cuoi", "ton_kho")
                if k in cols
            ),
            None,
        )
        if not (item_c and val_c):
            continue
        for _, row in df.iterrows():
            if norm_fn(row[item_c]) == target_norm:
                val = pd.to_numeric(row[val_c], errors="coerce")
                if pd.notna(val):
                    return float(val)
    return None


def _resolve_inventory_stock(
    q_norm: str, question: str, context: BusinessContext, as_of_date: str | None
) -> Tuple[str, Any, str | None] | None:
    """Resolve current inventory stock or return Gate-1 unanswerable if schema lacks inventory tracking."""
    if not _is_inventory_stock_query(q_norm):
        return None

    has_stock_schema = any(
        (not tname.endswith("_sheet1") and tname != "calendar")
        and (
            any(
                k in tname
                for k in (
                    "ton_kho",
                    "the_kho",
                    "lo_hang",
                    "don_hang_lo",
                    "chi_tiet_don_lo",
                )
            )
            or any(
                k in col.lower()
                for col in df.columns
                for k in ("ton_cuoi", "ton_thuc", "ton_kho", "so_luong_ton")
            )
        )
        for tname, df in context.tables.items()
    )
    if not has_stock_schema:
        return (
            "unanswerable",
            None,
            "Doanh nghiệp không có dữ liệu theo dõi tồn kho / nhập xuất kho theo Rulebook Mục 7",
        )

    target_date = _extract_explicit_iso_date(question) or as_of_date
    if not target_date:
        return None

    from zaic_2026_datamate.price_change import norm_vn, select_best_product

    candidates = _collect_inventory_candidates(context)
    picked = select_best_product(candidates, question.lower())
    if not picked:
        return None
    matched_name = picked[0]
    sku = _lookup_sku_for_product(context, matched_name, norm_vn)

    rem_qty: float | None = None
    if sku and "the_kho" in context.tables:
        rem_qty = _resolve_the_kho_item_stock(context, sku, target_date)
    if rem_qty is None and sku:
        rem_qty = _resolve_lot_ledger_stock(context, sku, target_date)
    if rem_qty is None:
        rem_qty = _resolve_snapshot_ton_kho(context, matched_name, norm_vn)

    if rem_qty is None:
        return None
    return _format_inventory_answer(rem_qty, q_norm, context, matched_name, sku)


def _sql_date_expr(qualified_col: str) -> str:
    """Return DuckDB DATE expression parsing native dates/timestamps and DD/MM/YYYY or YYYY-MM-DD strings."""
    return (
        f"COALESCE(TRY_CAST({qualified_col} AS DATE), "
        f"TRY_STRPTIME(CAST({qualified_col} AS VARCHAR), '%d/%m/%Y')::DATE, "
        f"TRY_STRPTIME(CAST({qualified_col} AS VARCHAR), '%Y-%m-%d')::DATE)"
    )


def _resolve_credit_due_dates(
    q_norm: str, question: str, context: BusinessContext, as_of_date: str | None
) -> Tuple[str, Any, str | None] | None:
    """Count distinct active credit orders filtered by due-date relative to as_of_date or target month."""
    if any(
        k in q_norm
        for k in (
            "bao nhieu tien",
            "bnhiu tien",
            "so tien",
            "tong no",
            "du no",
            "con no",
            "tuoi no",
            "qua han",
            "tre han",
            "cham tra",
            "tra gop",
            "ky gop",
        )
    ):
        return None
    has_credit_kw = any(
        k in q_norm for k in ("ban chiu", "don chiu", "no ky han", "cong no theo han")
    )
    has_due_kw = any(
        k in q_norm
        for k in ("ngay phai tra", "han phai tra", "han thanh toan", "phai tra")
    )
    has_count_kw = any(
        k in q_norm
        for k in (
            "may don",
            "bao nhieu don",
            "bao nhiu don",
            "co bao nhieu don",
            "so don",
            "bn don",
        )
    )
    if not (has_credit_kw and has_due_kw and has_count_kw):
        return None

    after_asof = any(
        k in q_norm
        for k in (
            "sau hom nay",
            "muon hon hom nay",
            "sau hnay",
            "muon hon hnay",
            "lon hon hom nay",
        )
    )
    target_month = None if after_asof else extract_explicit_month(question, as_of_date)
    if after_asof and not as_of_date:
        return None
    if not after_asof and not target_month:
        return None

    candidate_tables: list[tuple[str, str, str]] = []
    for tname, df in context.tables.items():
        if tname.endswith("_sheet1") or re.search(r"_(?:202[4-9]|[0-9]+)$", tname):
            continue
        due_col = next(
            (
                c
                for c in ("ngay_phai_tra", "han_thanh_toan", "ngay_han", "ngay_het_han")
                if c in df.columns
            ),
            None,
        )
        ord_col = next(
            (
                c
                for c in ("ma_don_hang", "ma_don", "so_don", "ma_hoa_don", "hd")
                if c in df.columns
            ),
            None,
        )
        if due_col and ord_col:
            priority = 0 if ("han_no" in tname or "cong_no_theo_han" in tname) else 1
            candidate_tables.append((f"{priority}_{tname}", due_col, ord_col))
    if not candidate_tables:
        return None
    candidate_tables.sort(key=lambda x: x[0])

    s_tbl, s_ord, s_date, s_status = find_primary_sales_table(context)
    for tagged_name, due_col, c_ord_col in candidate_tables:
        tname = tagged_name.split("_", 1)[1]
        due_expr = _sql_date_expr(f"c.{due_col}")
        due_cond = (
            f"{due_expr} > DATE '{as_of_date}'"
            if after_asof
            else f"strftime({due_expr}, '%Y-%m') = '{target_month}'"
        )
        if s_tbl and s_ord and s_date:
            s_dt_expr = _sql_date_expr(s_date)
            cutoff = f"AND {s_dt_expr} <= DATE '{as_of_date}'" if as_of_date else ""
            status_clause = get_valid_sales_status_clause(
                s_status, order_col=s_ord, table=s_tbl
            )
            sql = f"""
                WITH active_orders AS (
                    SELECT DISTINCT CAST({s_ord} AS VARCHAR) AS ord_key
                    FROM {s_tbl}
                    WHERE {s_ord} IS NOT NULL
                      {cutoff}
                      {status_clause}
                )
                SELECT COUNT(DISTINCT CAST(c.{c_ord_col} AS VARCHAR))
                FROM {tname} c
                JOIN active_orders s ON CAST(c.{c_ord_col} AS VARCHAR) = s.ord_key
                WHERE c.{c_ord_col} IS NOT NULL
                  AND {due_cond}
            """
        else:
            sql = f"""
                SELECT COUNT(DISTINCT CAST(c.{c_ord_col} AS VARCHAR))
                FROM {tname} c
                WHERE c.{c_ord_col} IS NOT NULL
                  AND {due_cond}
            """
        try:
            row = _fetch_one_row(context.duckdb_conn, sql)
            if row and row[0] is not None:
                return "number", int(row[0]), None
        except Exception:
            pass
    return None


def _resolve_multi_round_delivered_orders(
    q_norm: str, question: str, context: BusinessContext, as_of_date: str | None
) -> Tuple[str, Any, str | None] | None:
    """Count distinct active orders placed in a period delivered in >= K rounds up to as_of_date."""
    m_k = re.search(
        r"\b(?:tu|thanh)\s*(\d+)\s*(?:dot|lan)\b|\b(\d+)\s*(?:dot|lan)\s*tro\s+len\b",
        q_norm,
    )
    if not m_k:
        return None
    if not (
        ("tro len" in q_norm or "thanh " in q_norm)
        and any(k in q_norm for k in ("giao", "cho"))
        and any(
            k in q_norm
            for k in (
                "may don",
                "bao nhieu don",
                "bao nhiu don",
                "co bao nhieu don",
                "so don",
                "bn don",
            )
        )
    ):
        return None
    min_rounds = int(m_k.group(1) or m_k.group(2))

    s_tbl, s_ord, s_date, s_status = find_primary_sales_table(context)
    if not (s_tbl and s_ord and s_date):
        return None

    s_dt_expr = _sql_date_expr(s_date)
    explicit_month = extract_explicit_month(question, as_of_date)
    if explicit_month:
        period_cond = f"strftime({s_dt_expr}, '%Y-%m') = '{explicit_month}'"
    else:
        from zaic_2026_datamate.semantic_contract import parse_enhanced_period

        p = parse_enhanced_period(question, as_of_date)
        if p.start_date and p.end_date:
            period_cond = (
                f"{s_dt_expr} BETWEEN DATE '{p.start_date}' AND DATE '{p.end_date}'"
            )
        elif p.month_str:
            period_cond = f"strftime({s_dt_expr}, '%Y-%m') = '{p.month_str}'"
        elif p.exact_date:
            period_cond = f"{s_dt_expr} = DATE '{p.exact_date}'"
        else:
            return None

    s_cutoff = f"AND {s_dt_expr} <= DATE '{as_of_date}'" if as_of_date else ""
    status_clause = get_valid_sales_status_clause(
        s_status, order_col=s_ord, table=s_tbl
    )

    for tname, tbl in context.tables.items():
        if "giao_hang" not in tname or tname.endswith("_sheet1"):
            continue
        d_date = next(
            (
                c
                for c in ("thoi_gian_giao", "ngay_giao_hang", "ngay_cho", "ngay_giao")
                if c in tbl.columns
            ),
            None,
        )
        d_ord = next(
            (
                c
                for c in ("so_don", "ma_don_hang", "ma_bill", "ma_don")
                if c in tbl.columns
            ),
            None,
        )
        d_round = next(
            (
                c
                for c in ("luot_giao", "lan_giao", "so_luot", "dot")
                if c in tbl.columns
            ),
            None,
        )
        if not (d_date and d_ord and d_round):
            continue
        d_dt_expr = _sql_date_expr(f"g.{d_date}")
        d_cutoff = f"AND {d_dt_expr} <= DATE '{as_of_date}'" if as_of_date else ""
        round_expr = (
            f"COALESCE(CAST(TRY_CAST(g.{d_round} AS DOUBLE) AS VARCHAR), "
            f"TRIM(CAST(g.{d_round} AS VARCHAR)))"
        )
        sql = f"""
            WITH valid_orders AS (
                SELECT DISTINCT CAST({s_ord} AS VARCHAR) AS ord_key
                FROM {s_tbl}
                WHERE {s_ord} IS NOT NULL
                  AND {period_cond}
                  {s_cutoff}
                  {status_clause}
            ),
            multi_deliv AS (
                SELECT CAST(g.{d_ord} AS VARCHAR) AS ord_key
                FROM {tname} g
                JOIN valid_orders v ON CAST(g.{d_ord} AS VARCHAR) = v.ord_key
                WHERE g.{d_round} IS NOT NULL
                  {d_cutoff}
                GROUP BY CAST(g.{d_ord} AS VARCHAR)
                HAVING COUNT(DISTINCT {round_expr}) >= {min_rounds}
            )
            SELECT COUNT(*) FROM multi_deliv
        """
        try:
            row = _fetch_one_row(context.duckdb_conn, sql)
            if row and row[0] is not None:
                return "number", int(row[0]), None
        except Exception:
            pass
    return None


def resolve_domain_extensions(
    question: str,
    context: BusinessContext,
    as_of_date: str | None = None,
) -> Tuple[str, Any, str | None] | None:
    """Attempt to resolve query with general, schema-driven domain rules.

    Returns (type, answer, reason) or None if not handled.
    """
    question = normalize_query_telex(question)
    q_norm = strip_accents(question).replace("–", "-").replace("—", "-")
    q_norm = re.sub(r"\btoonf\b", "ton", q_norm, flags=re.IGNORECASE)

    for resolver in (
        lambda: _check_schema_ood(q_norm, context),
        lambda: _resolve_inventory_stock(q_norm, question, context, as_of_date),
        lambda: _resolve_credit_due_dates(q_norm, question, context, as_of_date),
        lambda: _resolve_multi_round_delivered_orders(
            q_norm, question, context, as_of_date
        ),
        lambda: _resolve_delivery_rounds(q_norm, question, context, as_of_date),
        lambda: _resolve_delivered_item_qty(q_norm, question, context, as_of_date),
        lambda: _resolve_unsold_items(q_norm, question, context, as_of_date),
        lambda: _resolve_phone_store_analytics(q_norm, question, context, as_of_date),
        lambda: _resolve_warranty_and_tradein(q_norm, question, context, as_of_date),
        lambda: _resolve_top_supplier(q_norm, question, context, as_of_date),
        lambda: _resolve_orders_above_threshold(q_norm, question, context, as_of_date),
        lambda: _resolve_the_kho_stockouts(q_norm, question, context, as_of_date),
    ):
        res = resolver()
        if res is not None:
            return res

    return None
