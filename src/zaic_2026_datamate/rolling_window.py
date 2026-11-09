"""
rolling_window.py: Deterministic resolution of rolling window queries
(7 ngày gần nhất, 30 ngày gần nhất, tuần này, tuần trước, hôm qua, hôm nay).

Strictly grounded in verified DuckDB SQL formulas against Ground Truth (diff = 0.0):
- 7 ngày gần nhất: [as_of_date - 6 days, as_of_date]
- 30 ngày gần nhất: [as_of_date - 29 days, as_of_date]
- Tuần trước: [Monday of previous week, Sunday of previous week]
- Tuần này: [Monday of current week, as_of_date]
- Hôm qua: as_of_date - 1 day
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any, Tuple

from zaic_2026_datamate.data_loader import BusinessContext
from zaic_2026_datamate.intent_parser import (
    find_primary_sales_table,
    find_revenue_column,
    get_valid_sales_status_clause,
    build_date_where_clause,
    parse_time_expressions,
)


def _find_item_name_col(context: BusinessContext, table: str) -> str | None:
    cols = list(context.tables[table].columns)
    for c in [
        "ten_hang",
        "ten_hang_hoa",
        "mat_hang",
        "hang_ban",
        "ten_sp",
        "product",
        "hang_hoa",
        "item",
        "ten_san_pham",
        "hang",
        "ten_mon",
        "ten_goi",
    ]:
        if c in cols and context.tables[table][c].notna().any():
            return c
    return None


def _find_qty_col(context: BusinessContext, table: str) -> str | None:
    cols = list(context.tables[table].columns)
    for c in [
        "s_lg",
        "so_luong",
        "sl_ban",
        "sl",
        "so_luong_hang",
        "luong",
        "soluong",
        "sl_quy_doi",
        "so_luong_san_pham",
        "sl_dv_co_ban",
        "s",
        "sluong",
    ]:
        if c in cols:
            return c
    return None


def _find_unit_col(context: BusinessContext, table: str) -> str | None:
    cols = list(context.tables[table].columns)
    for c in ["don_vi_tinh", "dvt", "donvi", "don_vi", "dv_goc"]:
        if c in cols:
            return c
    return None


def resolve_rolling_window_query(
    question: str,
    context: BusinessContext,
    as_of_date: str | None = None,
) -> Tuple[str, Any, str | None] | None:
    """Resolve rolling window queries deterministically.

    Returns (type, val, reason) if handled, or None if not a rolling window query.
    """
    if not as_of_date:
        return None

    q = question.lower()

    # 1. Detect rolling window / relative temporal signals
    has_rolling_signal = any(
        k in q
        for k in [
            "7 ngày",
            "7 ngay",
            "30 ngày",
            "30 ngay",
            "ngày gần nhất",
            "ngay gan nhat",
            "ngày qua",
            "ngay qua",
            "ngày vừa qua",
            "ngay vua qua",
            "tuần này",
            "tuan nay",
            "tuần rồi",
            "tuan roi",
            "tuần trước",
            "tuan truoc",
            "tuần trc",
            "tuan trc",
            "hôm qua",
            "hom qua",
            "hqua",
            "hqa",
            "bữa qua",
            "bua qua",
            "hôm nay",
            "hom nay",
            "hnay",
            "bữa nay",
            "bua nay",
        ]
    )
    if not has_rolling_signal:
        return None

    # Guard: Price change comparisons (handled by price change module)
    if any(
        k in q
        for k in [
            "đổi giá",
            "doi gia",
            "lên giá",
            "len gia",
            "xuống giá",
            "xuong gia",
            "tăng giá",
            "tang gia",
            "thay đổi bao nhiêu %",
            "thay doi bn %",
            "so với 30 ngày liền trước",
            "so với 7 ngày",
        ]
    ):
        return None

    # Guard: Submissions asking about debt, loans, warranty, returns
    if any(
        k in q
        for k in [
            "công nợ",
            "cong no",
            "trả nợ",
            "tra no",
            "bảo hành",
            "bao hanh",
            "lô tồn",
            "hết hạn",
            "tuổi nợ",
        ]
    ):
        return None

    # Guard: Percentage, growth, comparison, and shift queries
    if any(
        k in q
        for k in [
            "%",
            "phần trăm",
            "phan tram",
            "so với",
            "so voi",
            "hơn hay kém",
            "hon hay kem",
            "tăng hay giảm",
            "tang hay giam",
            "lên hay xuống",
            "len hay xuong",
            "ca sáng",
            "ca sang",
            "ca chiều",
            "ca chieu",
            "cùng kỳ",
            "cung ky",
        ]
    ):
        return None

    # Guard: Average order value (AOV) queries
    if any(
        k in q
        for k in [
            "trung bình",
            "trung binh",
            "bình quân",
            "binh quan",
            "mỗi đơn",
            "moi don",
            "1 đơn",
            "1 don",
            "mot don",
            "bq",
        ]
    ):
        return None

    # Guard: MTD / YTD queries (e.g. từ đầu tháng này đến hết hôm nay)
    if any(
        k in q
        for k in [
            "từ đầu tháng",
            "tu dau thang",
            "đầu tháng",
            "dau thang",
            "từ mùng 1",
            "tu mung 1",
            "từ đầu năm",
            "tu dau nam",
            "đầu năm",
            "dau nam",
        ]
    ):
        return None

    period = parse_time_expressions(question, as_of_date)
    has_valid_window = bool(
        period.exact_date or (period.start_date and period.end_date)
    )
    if not has_valid_window:
        return None

    tbl, order_col, date_col, status_col = find_primary_sales_table(context)
    if not tbl or not date_col:
        return None

    date_where = build_date_where_clause(date_col, period)
    status_clause = get_valid_sales_status_clause(
        status_col, order_col=order_col, table=tbl
    )

    # -------------------------------------------------------------------------
    # CASE A: Top K / Top 1 Items Sold in Period
    # -------------------------------------------------------------------------
    is_top_k = bool(
        re.search(
            r"\b(?:top\s*\d+|tóp\s*\d+|\d+\s*mặt hàng|\d+\s*món|\d+\s*sp)\s+bán\s+(?:chạy|nhiều)\s+nhất\b",
            q,
        )
    )
    is_top_1 = (
        any(
            k in q
            for k in [
                "bán chạy nhất",
                "bán nhiều nhất",
                "ban chay nhat",
                "ban nhieu nhat",
                "dc nhiều nhất",
            ]
        )
        and not is_top_k
        and any(k in q for k in ["mặt hàng", "sp", "món", "san pham", "mặt hangf"])
    )
    if is_top_k or is_top_1:
        item_col = _find_item_name_col(context, tbl)
        qty_col = _find_qty_col(context, tbl)
        if item_col:
            k_val = 1
            if is_top_k:
                k_m = re.search(
                    r"\b(?:top|tóp)\s*(\d+)|(\d+)\s*(?:mặt hàng|món|sp)\b", q
                )
                if k_m:
                    k_val = int(k_m.group(1) or k_m.group(2))

            qty_expr = f"sum(TRY_CAST({qty_col} AS DOUBLE))" if qty_col else "count(*)"
            sql = f"""
                SELECT {item_col} as item, {qty_expr} as q
                FROM {tbl}
                WHERE {date_where}
                  {status_clause}
                GROUP BY {item_col}
                ORDER BY q DESC
                LIMIT {k_val}
            """
            try:
                res = context.query(sql)
                if not res.empty:
                    items = res["item"].dropna().tolist()
                    if is_top_1:
                        return "string", str(items[0]), None
                    else:
                        return "list", items, None
            except Exception:
                pass

    # -------------------------------------------------------------------------
    # CASE B: Unit Quantity (Tổng số đĩa, lon, gói, tô, ly, chai, chiếc)
    # -------------------------------------------------------------------------
    unit_m = re.search(
        r"\b(?:mấy|may|bao nhiêu|bao nhieu|bnhiu|bn|nhiêu|nhieu|tổng\s+bnhiu|tổng\s+bao\s+nhiêu|tổng cộng bao nhiêu)\s+(đĩa|dia|lon|gói|goi|tô|to|bát|bat|chai|ly|cốc|coc|bình|binh|hộp|hop|bịch|bich|thùng|thung|chiếc|chiec|cái|cai|nồi|noi|con)\b",
        q,
    )
    if (
        unit_m
        and not any(
            k in q for k in ["đơn", "don", "đh", "dh", "khách", "khach", "tiền", "tien"]
        )
        and not re.search(
            r"\b(?:mấy|may|bao nhiêu|bao nhieu|bnhiu)\s+(?:ngày|ngay)\b", q
        )
    ):
        raw_u = unit_m.group(1)
        norm_u = (
            "đĩa"
            if raw_u in ["đĩa", "dia"]
            else (
                "lon"
                if raw_u == "lon"
                else (
                    "gói"
                    if raw_u in ["gói", "goi"]
                    else (
                        "tô"
                        if raw_u in ["tô", "to"]
                        else (
                            "chai"
                            if raw_u == "chai"
                            else ("ly" if raw_u in ["ly", "cốc", "coc"] else raw_u)
                        )
                    )
                )
            )
        )
        qty_col = _find_qty_col(context, tbl)
        unit_col = _find_unit_col(context, tbl)

        # If unit column is present directly in table
        if qty_col and unit_col:
            sql = f"""
                SELECT sum(TRY_CAST({qty_col} AS DOUBLE)) as total_qty
                FROM {tbl}
                WHERE {date_where}
                  AND lower({unit_col}) LIKE '%{norm_u}%'
                  {status_clause}
            """
            try:
                res = context.query(sql)
                val = res["total_qty"][0]
                if val is not None:
                    return "number", int(round(val)), None
            except Exception:
                pass

        # If dev_007 where unit is in danh_muc_hang
        if "danh_muc_hang" in context.tables and qty_col:
            dm_cols = list(context.tables["danh_muc_hang"].columns)
            item_col = _find_item_name_col(context, tbl) or "mat_hang"
            dm_item = next(
                (c for c in ["hang_ban", "ten_hang", "ten_sp"] if c in dm_cols), None
            )
            dm_unit = next((c for c in ["dvt", "don_vi_tinh"] if c in dm_cols), None)
            if item_col and dm_item and dm_unit:
                sql = f"""
                    SELECT sum(TRY_CAST(c.{qty_col} AS DOUBLE)) as total_qty
                    FROM {tbl} c
                    JOIN danh_muc_hang d ON lower(trim(c.{item_col})) = lower(trim(d.{dm_item}))
                    WHERE {build_date_where_clause(f"c.{date_col}", period)}
                      AND lower(d.{dm_unit}) LIKE '%{norm_u}%'
                """
                try:
                    res = context.query(sql)
                    val = res["total_qty"][0]
                    if val is not None:
                        return "number", int(round(val)), None
                except Exception:
                    pass

        # If dev_010 where unit is in chi_tiet_don and date is in don_hang
        if "chi_tiet_don" in context.tables and tbl == "don_hang":
            ct_cols = list(context.tables["chi_tiet_don"].columns)
            ct_qty = next((c for c in ["so_luong", "sl"] if c in ct_cols), None)
            ct_unit = next((c for c in ["dvt", "don_vi_tinh"] if c in ct_cols), None)
            if ct_qty and ct_unit:
                sql = f"""
                    SELECT sum(TRY_CAST(c.{ct_qty} AS DOUBLE)) as total_qty
                    FROM chi_tiet_don c
                    JOIN {tbl} d ON c.ma_bill = d.{order_col}
                    WHERE {build_date_where_clause(f"d.{date_col}", period)}
                      AND lower(c.{ct_unit}) LIKE '%{norm_u}%'
                """
                try:
                    res = context.query(sql)
                    val = res["total_qty"][0]
                    if val is not None:
                        return "number", int(round(val)), None
                except Exception:
                    pass

    # -------------------------------------------------------------------------
    # CASE C: Pure Order Count (Số đơn hàng thành công)
    # -------------------------------------------------------------------------
    has_order_kw = (
        bool(
            re.search(
                r"(?:bao nhiêu|bao nhieu|bao nhiu|bnhiu|mấy|may|co bao nhieu|có bao nhiêu|được mấy|duoc may|được bao nhiêu|duoc bao nhieu)\s+(?:đơn|don|đh|dh|bill|hóa đơn|hoa don|o\s*đờ|o\s*do|order)\b",
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
                r"\b(?:số\s+đh|so\s+dh|số\s+đơn|so\s+don|số\s+o\s*đờ|so\s+o\s*do)\b", q
            )
        )
    )
    if (
        has_order_kw
        and order_col
        and not any(k in q for k in ["hủy", "huỷ", "tiền", "doanh thu", "dthu", "lãi"])
        and not period.is_today
        and not period.is_yesterday
    ):
        sql = f"""
            SELECT COUNT(DISTINCT {order_col}) as cnt
            FROM {tbl}
            WHERE {date_where}
              {status_clause}
        """
        try:
            res = context.query(sql)
            val = res["cnt"][0]
            if val is not None and 0 < int(val) < 500000:
                return "number", int(val), None
        except Exception:
            pass

    # -------------------------------------------------------------------------
    # CASE D: Total Store Revenue in Rolling Window
    # -------------------------------------------------------------------------
    has_rev_kw = any(
        k in q
        for k in [
            "doanh thu",
            "doanhthu",
            "dthu",
            "tiền bán",
            "tien ban",
            "bán được bao nhiêu tiền",
            "thu về bao nhiêu",
            "bán được bao nhiêu",
            "ban duoc bao nhieu",
        ]
    )
    if has_rev_kw and not any(
        k in q
        for k in [
            "mấy đơn",
            "bao nhiêu đơn",
            "bao nhiêu đh",
            "mấy đĩa",
            "mấy lon",
            "mấy gói",
            "trung bình",
            "bình quân",
        ]
    ):
        rev_col = find_revenue_column(context, tbl)
        if rev_col:
            sql = f"""
                SELECT sum({rev_col}) as rev
                FROM {tbl}
                WHERE {date_where}
                  {status_clause}
            """
            try:
                res = context.query(sql)
                val = res["rev"][0]
                if val is not None:
                    return "number", int(round(val)), None
            except Exception:
                pass

    return None
