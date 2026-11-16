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
from typing import Any, Tuple

from zaic_2026_datamate.cleaner import normalize_query_telex, ws_norm_sql
from zaic_2026_datamate.data_loader import BusinessContext
from zaic_2026_datamate.intent_parser import (
    check_date_out_of_domain,
    find_header_detail_revenue_spec,
    find_primary_sales_table,
    find_revenue_column,
    get_all_product_names,
    get_valid_sales_status_clause,
    build_date_where_clause,
    parse_time_expressions,
)


def find_item_name_col(context: BusinessContext, table: str) -> str | None:
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


def find_qty_col(context: BusinessContext, table: str) -> str | None:
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
        "so_luong_quy_doi",
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


_ws_norm_sql = ws_norm_sql


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

    question_clean = normalize_query_telex(question)
    q = question_clean.lower()

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

    # Guard: Submissions asking about debt, loans, warranty, returns, or current inventory
    if (
        any(
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
                "tồn kho",
                "ton kho",
                "trong kho",
                "tồn cuối",
                "ton cuoi",
                "hộp chẵn",
                "hop chan",
                "viên lẻ",
                "vien le",
                "cái lẻ",
                "cai le",
            ]
        )
        or bool(re.search(r"\b(?:hàng\s+tồn|hang\s+ton|còn\s+tồn|con\s+ton)\b", q))
        or (
            bool(
                re.search(
                    r"\b(?:còn|con)\s+(?:lại|lai|tồn|ton)?\s*(?:mấy|may|bao nhiêu|bao nhieu|bnhiu|bn)\b",
                    q,
                )
            )
            and not any(
                k in q
                for k in [
                    "nợ",
                    "no",
                    "phải thu",
                    "phai thu",
                    "phải trả",
                    "phai tra",
                    "đơn",
                    "don",
                    "ngày",
                    "ngay",
                ]
            )
        )
        or (bool(re.search(r"\bkho\b.*\b(?:tồn|ton)\b|\b(?:tồn|ton)\b.*\bkho\b", q)))
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
            "từ đầu nawm",
            "đầu năm",
            "dau nam",
            "đầu nawm",
            "dau nawm",
        ]
    ):
        return None

    period = parse_time_expressions(question_clean, as_of_date)
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
    # CASE A: Top K / Top 1 Items Sold in Period -> delegate to semantic_contract
    # (which computes base_quantity with NaN recovery, danh_muc conversions, and tie safety)
    # -------------------------------------------------------------------------
    is_top_k = bool(
        re.search(
            r"\b(?:top\s*\d+|tóp\s*\d+|\d+\s*(?:mặt\s*hàng\w*|mat\s*hang\w*|món|mon|sp|sản\s*phẩm\w*))\s+(?:bán\s+)?(?:chạy|nhiều)\s+nhất\b",
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
        and any(
            k in q
            for k in [
                "mặt hàng",
                "mat hang",
                "sp",
                "món",
                "san pham",
                "sản phẩm",
                "mặt hangf",
            ]
        )
    )
    if is_top_k or is_top_1:
        from zaic_2026_datamate.semantic_contract import resolve_semantic_plan

        sem_type, sem_val, sem_reason = resolve_semantic_plan(
            question_clean, context, as_of_date
        )
        if sem_type != "abstain":
            return sem_type, sem_val, sem_reason
        return None

    # -------------------------------------------------------------------------
    # CASE B: Unit Quantity (Tổng số đĩa, lon, gói, tô, ly, chai, bình, cái, chiếc)
    # -------------------------------------------------------------------------
    _UNIT_VARIANTS: dict[str, tuple[str, ...]] = {
        "đĩa": ("đĩa", "dia"),
        "lon": ("lon",),
        "gói": ("gói", "goi"),
        "tô": ("tô", "to"),
        "bát": ("bát", "bat"),
        "chai": ("chai",),
        "ly": ("ly", "cốc", "coc"),
        "bình": ("bình", "binh"),
        "hộp": ("hộp", "hop"),
        "bịch": ("bịch", "bich"),
        "thùng": ("thùng", "thung"),
        "chiếc": ("chiếc", "chiec"),
        "cái": ("cái", "cai"),
        "nồi": ("nồi", "noi"),
        "con": ("con",),
        "viên": ("viên", "vien"),
        "vỉ": ("vỉ", "vi"),
    }
    _RAW_TO_CANON_UNIT = {
        v: canon for canon, variants in _UNIT_VARIANTS.items() for v in variants
    }

    unit_m = re.search(
        r"\b(?:mấy|may|bao nhiêu|bao nhieu|bnhiu|bn|nhiêu|nhieu|tổng\s+bnhiu|tổng\s+bao\s+nhiêu|tổng cộng bao nhiêu)\s+(đĩa|dia|lon|gói|goi|tô|to|bát|bat|chai|ly|cốc|coc|bình|binh|hộp|hop|bịch|bich|thùng|thung|chiếc|chiec|cái|cai|nồi|noi|con|viên|vien|vỉ|vi)\b",
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
        canon_u = _RAW_TO_CANON_UNIT.get(unit_m.group(1), unit_m.group(1))
        u_patterns = _UNIT_VARIANTS.get(canon_u, (canon_u,))

        def _unit_like_sql(expr: str) -> str:
            clauses = " OR ".join(
                f"lower(CAST({expr} AS VARCHAR)) LIKE '%{u}%'" for u in u_patterns
            )
            return f"({clauses})"

        def _pick_base_qty_col(cols: list[str]) -> str | None:
            return next(
                (
                    c
                    for c in (
                        "sl_quy_doi",
                        "so_luong_quy_doi",
                        "so_luong_le",
                        "sl_le",
                        "quy_ra_dv_le",
                        "sl_dv_co_ban",
                    )
                    if c in cols
                ),
                None,
            )

        tbl_cols = list(context.tables[tbl].columns)
        qty_col = find_qty_col(context, tbl)
        base_qty_col = _pick_base_qty_col(tbl_cols)
        unit_col = _find_unit_col(context, tbl)
        item_col = find_item_name_col(context, tbl)

        # Flat table unit count: prefer precomputed base_qty_col when matching catalog base unit, or raw qty when row unit directly matches
        if (qty_col or base_qty_col) and unit_col:
            raw_first = (
                f"COALESCE(NULLIF(TRY_CAST(t.{qty_col} AS DOUBLE), 'NaN'::DOUBLE), NULLIF(TRY_CAST(t.{base_qty_col} AS DOUBLE), 'NaN'::DOUBLE))"
                if (base_qty_col and qty_col)
                else f"NULLIF(TRY_CAST(t.{qty_col or base_qty_col} AS DOUBLE), 'NaN'::DOUBLE)"
            )
            base_first = (
                f"COALESCE(NULLIF(TRY_CAST(t.{base_qty_col} AS DOUBLE), 'NaN'::DOUBLE), NULLIF(TRY_CAST(t.{qty_col} AS DOUBLE), 'NaN'::DOUBLE))"
                if (base_qty_col and qty_col)
                else f"NULLIF(TRY_CAST(t.{base_qty_col or qty_col} AS DOUBLE), 'NaN'::DOUBLE)"
            )
            flat_date_where = build_date_where_clause(f"t.{date_col}", period)
            flat_status_clause = get_valid_sales_status_clause(
                status_col, order_col=order_col, table=tbl, table_alias="t"
            )
            cat_join_sql = ""
            if base_qty_col and item_col:
                for cat_t, cat_df in context.tables.items():
                    if cat_t.endswith("_sheet1") or not any(
                        k in cat_t
                        for k in (
                            "danh_sach_hang",
                            "danh_s_ich_h_ang",
                            "danh_sach_san_pham",
                            "san_pham",
                        )
                    ):
                        continue
                    cat_cols = list(cat_df.columns)
                    cat_name = next(
                        (
                            c
                            for c in (
                                "ten_hang_hoa",
                                "ten_hang",
                                "ten_san_pham",
                                "ten_sp",
                                "ten_goi",
                            )
                            if c in cat_cols
                        ),
                        None,
                    )
                    cat_u = next(
                        (
                            c
                            for c in (
                                "don_vi_co_ban",
                                "don_vi_co_so",
                                "don_vi_tinh",
                                "don_vi",
                                "dvt",
                            )
                            if c in cat_cols
                        ),
                        None,
                    )
                    if cat_name and cat_u:
                        cat_join_sql = (
                            f"LEFT JOIN (SELECT {_ws_norm_sql(cat_name)} AS k, max({cat_u}) AS u "
                            f"FROM {cat_t} GROUP BY 1) cat ON {_ws_norm_sql(f't.{item_col}')} = cat.k"
                        )
                        break
            if cat_join_sql and base_qty_col:
                eff_qty_expr = f"CASE WHEN {_unit_like_sql(f't.{unit_col}')} THEN {raw_first} ELSE {base_first} END"
                unit_filter_sql = f"({_unit_like_sql(f't.{unit_col}')} OR (cat.u IS NOT NULL AND {_unit_like_sql('cat.u')}))"
            else:
                eff_qty_expr = (
                    f"CASE WHEN {_unit_like_sql(f't.{unit_col}')} THEN {raw_first} ELSE {base_first} END"
                    if base_qty_col
                    else raw_first
                )
                unit_filter_sql = _unit_like_sql(f"t.{unit_col}")
            sql = f"""
                SELECT sum({eff_qty_expr}) as total_qty
                FROM {tbl} t
                {cat_join_sql}
                WHERE {flat_date_where}
                  AND {unit_filter_sql}
                  {flat_status_clause}
            """
            try:
                res = context.query(sql)
                val = res["total_qty"][0]
                if val is not None:
                    return "number", int(round(val)), None
            except Exception:
                pass

        # If unit is in danh_muc_hang (join sales table as c)
        if "danh_muc_hang" in context.tables and (base_qty_col or qty_col):
            dm_cols = list(context.tables["danh_muc_hang"].columns)
            item_col_dm = item_col or "mat_hang"
            dm_item = next(
                (c for c in ["hang_ban", "ten_hang", "ten_sp"] if c in dm_cols), None
            )
            dm_unit = next((c for c in ["dvt", "don_vi_tinh"] if c in dm_cols), None)
            if item_col_dm and dm_item and dm_unit:
                status_clause_c = get_valid_sales_status_clause(
                    status_col, order_col=order_col, table=tbl, table_alias="c"
                )
                eff_dm_qty = (
                    f"COALESCE(NULLIF(TRY_CAST(c.{base_qty_col} AS DOUBLE), 'NaN'::DOUBLE), NULLIF(TRY_CAST(c.{qty_col} AS DOUBLE), 'NaN'::DOUBLE))"
                    if (base_qty_col and qty_col)
                    else f"NULLIF(TRY_CAST(c.{base_qty_col or qty_col} AS DOUBLE), 'NaN'::DOUBLE)"
                )
                sql = f"""
                    SELECT sum({eff_dm_qty}) as total_qty
                    FROM {tbl} c
                    JOIN danh_muc_hang d ON {_ws_norm_sql(f"c.{item_col_dm}")} = {_ws_norm_sql(f"d.{dm_item}")}
                    WHERE {build_date_where_clause(f"c.{date_col}", period)}
                      AND {_unit_like_sql(f"d.{dm_unit}")}
                      {status_clause_c}
                """
                try:
                    res = context.query(sql)
                    val = res["total_qty"][0]
                    if val is not None:
                        return "number", int(round(val)), None
                except Exception:
                    pass

        # Header + detail schema (don_hang+chi_tiet_don or danh_sach_hoa_don+danh_sach_hoa_don_chi_tiet)
        if order_col:
            detail_candidates = [
                t for t in ("chi_tiet_don", f"{tbl}_chi_tiet") if t in context.tables
            ] + [
                t
                for t in context.tables
                if "chi_tiet" in t and "nhap" not in t and not t.endswith("_sheet1")
            ]
            for ct_tbl in detail_candidates:
                ct_cols = list(context.tables[ct_tbl].columns)
                ct_join = next(
                    (
                        c
                        for c in (
                            order_col,
                            "ma_bill",
                            "ma_don_hang",
                            "ma_hoa_don",
                            "ma_don",
                            "so_ct",
                        )
                        if c in ct_cols
                    ),
                    None,
                )
                ct_raw_qty = next(
                    (
                        c
                        for c in (
                            "so_luong",
                            "sl",
                            "s",
                            "so_luong_san_pham",
                            "so_luong_ban",
                            "sl_ban",
                        )
                        if c in ct_cols
                    ),
                    None,
                )
                ct_base_qty = _pick_base_qty_col(ct_cols)
                ct_unit = next(
                    (c for c in ("dvt", "don_vi_tinh", "dv_tinh") if c in ct_cols), None
                )
                if not (ct_join and (ct_raw_qty or ct_base_qty) and ct_unit):
                    continue

                raw_first_expr = (
                    f"COALESCE(NULLIF(TRY_CAST(c.{ct_raw_qty} AS DOUBLE), 'NaN'::DOUBLE), NULLIF(TRY_CAST(c.{ct_base_qty} AS DOUBLE), 'NaN'::DOUBLE))"
                    if (ct_base_qty and ct_raw_qty)
                    else f"NULLIF(TRY_CAST(c.{ct_raw_qty or ct_base_qty} AS DOUBLE), 'NaN'::DOUBLE)"
                )
                base_first_expr = (
                    f"COALESCE(NULLIF(TRY_CAST(c.{ct_base_qty} AS DOUBLE), 'NaN'::DOUBLE), NULLIF(TRY_CAST(c.{ct_raw_qty} AS DOUBLE), 'NaN'::DOUBLE))"
                    if (ct_base_qty and ct_raw_qty)
                    else f"NULLIF(TRY_CAST(c.{ct_base_qty or ct_raw_qty} AS DOUBLE), 'NaN'::DOUBLE)"
                )
                sp_join_sql = ""
                unit_where_sql = _unit_like_sql(f"c.{ct_unit}")
                qty_expr = raw_first_expr
                ct_sku = next(
                    (
                        c
                        for c in (
                            "ma_hang_hoa",
                            "ma_mat_hang",
                            "ma_hang_ban",
                            "ma_hang",
                            "ma_sp",
                            "ma_sku",
                        )
                        if c in ct_cols
                    ),
                    None,
                )
                if ct_base_qty and ct_sku:
                    for sp_t in (
                        "san_pham",
                        "danh_sach_san_pham",
                        "danh_sach_hang_hoa",
                    ):
                        if sp_t in context.tables:
                            sp_cols = list(context.tables[sp_t].columns)
                            sp_sku = next(
                                (
                                    c
                                    for c in (
                                        "ma_san_pham_ban",
                                        "ma_hang_hoa",
                                        "ma_hh",
                                        "ma_hang",
                                        "ma_sku",
                                        "ma_sp",
                                    )
                                    if c in sp_cols
                                ),
                                None,
                            )
                            sp_u = next(
                                (
                                    c
                                    for c in (
                                        "don_vi_co_ban",
                                        "don_vi_co_so",
                                        "don_vi_tinh",
                                        "don_vi",
                                        "dvt",
                                    )
                                    if c in sp_cols
                                ),
                                None,
                            )
                            if sp_sku and sp_u:
                                sp_join_sql = f"LEFT JOIN (SELECT {sp_sku} AS sku, max({sp_u}) AS u FROM {sp_t} GROUP BY 1) sp ON c.{ct_sku} = sp.sku"
                                unit_where_sql = f"({_unit_like_sql(f'c.{ct_unit}')} OR {_unit_like_sql('sp.u')})"
                                qty_expr = f"CASE WHEN {_unit_like_sql(f'c.{ct_unit}')} THEN {raw_first_expr} ELSE {base_first_expr} END"
                                break
                status_clause_d = get_valid_sales_status_clause(
                    status_col, order_col=order_col, table=tbl, table_alias="d"
                )
                sql = f"""
                    SELECT sum({qty_expr}) as total_qty
                    FROM {ct_tbl} c
                    JOIN {tbl} d ON c.{ct_join} = d.{order_col}
                    {sp_join_sql}
                    WHERE {build_date_where_clause(f"d.{date_col}", period)}
                      AND {unit_where_sql}
                      {status_clause_d}
                """
                try:
                    res = context.query(sql)
                    val = res["total_qty"][0]
                    if val is not None:
                        return "number", int(round(val)), None
                except Exception:
                    pass
            return None

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
    has_rev_kw = (
        any(
            k in q
            for k in [
                "doanh thu",
                "doanhthu",
                "dthu",
                "revenue",
                "tiền bán",
                "tien ban",
                "bán được bao nhiêu tiền",
                "thu về bao nhiêu",
            ]
        )
        or bool(re.search(r"\b(?:bán|ban)\s+(?:được|duoc)\b.*?\b(?:tiền|tien)\b", q))
        or bool(
            re.search(
                r"\b(?:bán|ban)\s+(?:được|duoc)\s+(?:tổng\s+cộng\s+)?bao\s+nhi[êe]u\s*(?:[?!.,]|$)",
                q,
            )
        )
    )
    has_non_store_rev_guard = (
        bool(
            re.search(
                r"\b(?:đơn|don|đh|dh|bill|hóa đơn|hoa don|order|số lượng|so luong|món|mon|sp|nhiều nhất|nhieu nhat|cao nhất|cao nhat|bán chạy|ban chay|là gì|la gi|là chi|la chi|mấy đĩa|mấy lon|mấy gói|ly|cốc|gói|goi|chai|lon|hộp|hop|viên|vien|đĩa|dia|tô|bát|chiếc|chiec|bình|binh|thùng|thung|bịch|bich|kg|lít|lit|m3|cục|cuc|tuýp|tuyp|cái|cai|lượt|luot|chuyến|chuyen|đợt|dot)\b",
                q,
            )
        )
        or any(
            k in q
            for k in [
                "trung bình",
                "bình quân",
                "mặt hàng",
                "mat hang",
                "sản phẩm",
                "san pham",
                "hoạt chất",
                "hoat chat",
                "thuốc",
                "thuoc",
                "khách",
                "khach",
                "nhân viên",
                "nhan vien",
                "kênh",
                "kenh",
                "chi nhánh",
                "chi nhanh",
                "giá",
                "gia",
                "nhóm",
                "nhom",
                "danh mục",
                "danh muc",
                "nếu",
                "neu",
            ]
        )
        or any(p_name in q for p_name, _, _ in get_all_product_names(context))
    )
    if (
        has_rev_kw
        and not unit_m
        and not has_non_store_rev_guard
        and not check_date_out_of_domain(context, period, date_col, tbl)
    ):
        hd_spec = find_header_detail_revenue_spec(context, tbl, order_col)
        if hd_spec:
            ct_order_col, ct_rev_col = hd_spec
            d_date_where = build_date_where_clause(f"d.{date_col}", period)
            d_status_clause = get_valid_sales_status_clause(
                status_col, order_col=order_col, table=tbl, table_alias="d"
            )
            sql = f"""
                SELECT sum(c.{ct_rev_col}) as rev
                FROM chi_tiet_don c
                JOIN {tbl} d ON c.{ct_order_col} = d.{order_col}
                WHERE {d_date_where}
                  {d_status_clause}
            """
            try:
                res = context.query(sql)
                val = res["rev"][0]
                if val is not None and float(val) > 0:
                    return "number", int(round(val)), None
            except Exception:
                pass

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
                if val is not None and float(val) > 0:
                    return "number", int(round(val)), None
            except Exception:
                pass

    return None
