"""
price_change.py: Deterministic evaluation of price change impact queries
(Thay đổi lượng bán trước và sau khi đổi giá / tăng giá / lên giá / xuống giá).

Formula verified against Ground Truth on train datasets:
- Price change date: D
- Window: N days (30, 14, 7)
- Period before: [D - N days, D - 1 day]
- Period after: [D, min(D + N - 1 days, as_of_date)]
- Percentage change: round((Q_after - Q_before) / Q_before * 100.0, 2)
"""

from __future__ import annotations

import math
import re
import unicodedata
from datetime import datetime, timedelta
from typing import Any, Tuple

import pandas as pd

from zaic_2026_datamate.cleaner import split_inline_toppings
from zaic_2026_datamate.data_loader import BusinessContext
from zaic_2026_datamate.intent_parser import (
    find_primary_sales_table,
    get_all_product_names,
    get_valid_sales_status_clause,
)
from zaic_2026_datamate.rolling_window import find_item_name_col, find_qty_col


def strip_accents(s: str) -> str:
    """Normalize and strip Vietnamese diacritics for resilient keyword matching."""
    s = s.replace("đ", "d").replace("Đ", "D")
    return "".join(
        c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn"
    )


def norm_vn(val: Any) -> str:
    """Normalize scalar value to stripped lowercase Vietnamese ASCII."""
    if val is None or (isinstance(val, float) and math.isnan(val)):
        return ""
    s = str(val).strip()
    if s.lower() in ("nan", "none", "nat", ""):
        return ""
    return re.sub(r"\s+", " ", strip_accents(s.lower())).strip()


# Common Vietnamese retail & restaurant synonyms across dialects
REGIONAL_SYNONYMS = {
    "lạc": "đậu phộng",
    "lac": "dau phong",
    "má lợn": "má heo",
    "ma lon": "ma heo",
    "thịt lợn": "thịt heo",
    "thit lon": "thit heo",
    "mực rán": "mực chiên",
    "muc ran": "muc chien",
    "bo luc lac": "bò lúc lắc",
}

_KNOWN_UNITS = {
    "vien",
    "vi",
    "hop",
    "goi",
    "ong",
    "chai",
    "tuyp",
    "lo",
    "binh",
    "bao",
    "cay",
    "cuon",
    "bo",
    "ly",
    "coc",
    "phan",
    "dia",
    "to",
    "con",
    "cai",
    "chiec",
    "lon",
    "thung",
    "loc",
    "kg",
    "lit",
    "met",
    "m",
    "m2",
    "m3",
    "tui",
    "o",
    "mieng",
    "cap",
    "doi",
}

_CONTINUOUS_UNITS = {
    "kg",
    "g",
    "gr",
    "gam",
    "l",
    "lit",
    "ml",
    "m",
    "met",
    "cm",
    "mm",
    "m2",
    "m3",
}

_MATCH_STOPWORDS = {"cho", "va", "voi", "cua", "la", "cac", "trong", "loai"}


def alnum_phrase(text: str) -> str:
    """Collapse non-alphanumeric separators into single spaces on unaccented lowercase text."""
    return re.sub(r"[^a-z0-9]+", " ", strip_accents(text.lower())).strip()


def _extract_size_tags(text: str) -> set[str]:
    """Extract parenthetical cup/apparel size markers such as (m), (l), (xl)."""
    return set(re.findall(r"\(\s*(s|m|l|xl|xxl)\s*\)", strip_accents(text.lower())))


def _extract_spec_tokens(alnum_text: str) -> set[str]:
    """Extract alphanumeric model/dosage specs (e.g. 10mg, 12kg, 2p, 20a, b6)."""
    return {
        tok
        for tok in alnum_text.split()
        if any(ch.isdigit() for ch in tok) and any(ch.isalpha() for ch in tok)
    }


def _extract_asked_unit(q_lower: str) -> str | None:
    """Extract target quantity unit from price-change question text."""
    q_u = strip_accents(q_lower)
    patterns = (
        r"\btinh\s+theo\s+([a-z0-9]+)\b",
        r"\bban\s+ra\s+tinh\s+theo\s+([a-z0-9]+)\b",
        r"\b(?:so|soos)\s+([a-z0-9]+)\b",
        r"\bluong\s+([a-z0-9]+)\s+(?:ban|thay\s+doi)\b",
    )
    for pat in patterns:
        for m in re.finditer(pat, q_u):
            cand = m.group(1).strip()
            if cand in _KNOWN_UNITS:
                return cand
    return None


def select_best_product(
    candidates: list[tuple[str, str, str]],
    q_lower: str,
) -> tuple[str, str, str] | None:
    """Select the unambiguous best-matching product candidate (never collide on 1 shared word)."""
    if not candidates:
        return None

    q_syn = q_lower
    for k, v in REGIONAL_SYNONYMS.items():
        if k in q_syn:
            q_syn = q_syn.replace(k, v)

    q_u = strip_accents(q_lower)
    q_syn_u = strip_accents(q_syn)
    q_alnum = alnum_phrase(q_u)
    q_syn_alnum = alnum_phrase(q_syn_u)
    q_variants = (q_u, q_syn_u, q_alnum, q_syn_alnum)
    q_toks = set(q_syn_alnum.split()) | set(q_alnum.split())
    q_sizes = _extract_size_tags(q_syn_u)

    best_score: tuple[int, int, float, int] | None = None
    best_cand: tuple[str, str, str] | None = None
    is_tied = False

    seen_names: set[str] = set()
    for p_name, p_table, p_col in candidates:
        p_clean = str(p_name).strip()
        if not p_clean:
            continue
        p_u = strip_accents(p_clean.lower())
        p_alnum = alnum_phrase(p_u)
        if not p_alnum or p_alnum in seen_names:
            continue
        seen_names.add(p_alnum)

        p_sizes = _extract_size_tags(p_u)
        if q_sizes and p_sizes and not (p_sizes & q_sizes):
            continue

        is_exact = 0
        for p_form in (p_u, p_alnum):
            pat = rf"(?<![a-z0-9]){re.escape(p_form)}(?![a-z0-9])"
            if any(re.search(pat, qv) for qv in q_variants):
                is_exact = 1
                break

        p_toks = [t for t in p_alnum.split() if t not in _MATCH_STOPWORDS]
        matched = sum(1 for t in p_toks if t in q_toks)
        coverage = matched / len(p_toks) if p_toks else 0.0

        if not is_exact:
            if not p_toks or coverage < 0.80:
                continue
            if len(p_toks) >= 2 and matched < 2:
                continue
            if len(p_toks) == 1 and len(p_toks[0]) < 4:
                continue
            p_specs = _extract_spec_tokens(p_alnum)
            if p_specs and not (p_specs <= q_toks):
                continue

        score = (is_exact, matched, round(coverage, 4), len(p_alnum))
        if best_score is None or score > best_score:
            best_score = score
            best_cand = (p_name, p_table, p_col)
            is_tied = False
        elif score == best_score:
            is_tied = True

    return None if is_tied else best_cand


def build_product_unit_conversions(
    context: BusinessContext,
    product_name: str,
    product_code: str | None = None,
) -> tuple[str | None, dict[str, float]]:
    """Return (base_unit_norm, {unit_norm: factor_to_base}) for a matched product."""
    base_unit: str | None = None
    factors: dict[str, float] = {}
    p_norm = norm_vn(product_name)
    code_norm = norm_vn(product_code) if product_code else ""

    # 1. danh_sach_quy_doi / du_lieu_xuat_danh_sach_quy_doi
    for tname, df in context.tables.items():
        tl = tname.lower()
        if "quy_doi" not in tl:
            continue
        cols = {c.lower(): c for c in df.columns}
        item_c = next(
            (cols[k] for k in ("ten_san_pham", "ten_hang", "hang", "ten_sp") if k in cols),
            None,
        )
        unit_c = next((cols[k] for k in ("don_vi_quy_doi", "don_vi_tinh", "dvt") if k in cols), None)
        ratio_c = next((cols[k] for k in ("ti_le_quy_doi", "he_so_quy_doi", "he_so") if k in cols), None)
        base_c = next((cols[k] for k in ("don_vi_co_ban", "don_vi_co_so") if k in cols), None)
        if not (item_c and unit_c and ratio_c):
            continue
        for _, r in df.iterrows():
            if norm_vn(r[item_c]) != p_norm:
                continue
            u = norm_vn(r[unit_c])
            ratio = pd.to_numeric(r[ratio_c], errors="coerce")
            if u and pd.notna(ratio) and float(ratio) > 0:
                factors[u] = float(ratio)
            if base_c and pd.notna(r[base_c]):
                bu = norm_vn(r[base_c])
                if bu:
                    base_unit = bu
                    factors.setdefault(bu, 1.0)

    # 2. danh_muc / danh_muc_hang (hang, dv_lon, dv_nho, he_so_qd)
    for tname, df in context.tables.items():
        tl = tname.lower()
        if "danh_muc" not in tl:
            continue
        cols = {c.lower(): c for c in df.columns}
        item_c = next(
            (cols[k] for k in ("hang", "hang_ban", "ten_hang", "ten_sp", "ten_san_pham", "ten_mon") if k in cols),
            None,
        )
        big_c = cols.get("dv_lon") or cols.get("dv_nhap")
        small_c = cols.get("dv_nho") or cols.get("don_vi_goc")
        ratio_c = cols.get("he_so_qd") or cols.get("he_so_quy_doi") or cols.get("he_so")
        if not (item_c and big_c and small_c and ratio_c):
            continue
        for _, r in df.iterrows():
            if norm_vn(r[item_c]) != p_norm:
                continue
            bu = norm_vn(r[small_c])
            big_u = norm_vn(r[big_c])
            ratio = pd.to_numeric(r[ratio_c], errors="coerce")
            if bu:
                base_unit = base_unit or bu
                factors.setdefault(bu, 1.0)
            if big_u and pd.notna(ratio) and float(ratio) > 0:
                factors[big_u] = float(ratio)

    # 3. Relational don_vi_sp + san_pham.don_vi_co_so
    if "san_pham" in context.tables and (product_code or p_norm):
        sp = context.tables["san_pham"]
        sp_cols = {c.lower(): c for c in sp.columns}
        sp_code_c = next(
            (sp_cols[k] for k in ("ma_hang_hoa", "sku", "ma_san_pham_ban", "ma_hh", "ma_sp") if k in sp_cols),
            None,
        )
        sp_name_c = next(
            (sp_cols[k] for k in ("ten_goi", "ten_sp", "ten_san_pham", "san_pham", "ten_hang") if k in sp_cols),
            None,
        )
        sp_bu_c = next(
            (sp_cols[k] for k in ("don_vi_co_so", "don_vi_co_ban", "don_vi", "don_vi_tinh", "dvt") if k in sp_cols),
            None,
        )
        resolved_code = code_norm
        if sp_bu_c and (sp_code_c or sp_name_c):
            for _, r in sp.iterrows():
                row_code = norm_vn(r[sp_code_c]) if sp_code_c else ""
                row_name = norm_vn(r[sp_name_c]) if sp_name_c else ""
                if (resolved_code and row_code == resolved_code) or (p_norm and row_name == p_norm):
                    bu = norm_vn(r[sp_bu_c])
                    if bu:
                        base_unit = base_unit or bu
                        factors.setdefault(bu, 1.0)
                    resolved_code = resolved_code or row_code
                    break
        if "don_vi_sp" in context.tables and resolved_code:
            dv_df = context.tables["don_vi_sp"]
            dv_cols = {c.lower(): c for c in dv_df.columns}
            dv_code = dv_cols.get("ma_hang_hoa") or dv_cols.get("ma_sp")
            dv_unit = dv_cols.get("don_vi_tinh") or dv_cols.get("dvt")
            dv_ratio = dv_cols.get("he_so_quy_doi") or dv_cols.get("ti_le_quy_doi")
            if dv_code and dv_unit and dv_ratio:
                for _, r in dv_df.iterrows():
                    if norm_vn(r[dv_code]) != resolved_code:
                        continue
                    u = norm_vn(r[dv_unit])
                    ratio = pd.to_numeric(r[dv_ratio], errors="coerce")
                    if u and pd.notna(ratio) and float(ratio) > 0:
                        factors[u] = float(ratio)

    # 4. Pack-text fallback only when no table rule defined factors
    if not factors:
        m_multi = re.search(
            r"\b(hop|thung)\s+(\d+)\s*(vi|goi|ong|tuyp|lon|chai)\s*[xX*]\s*(\d+)\s*(vien|goi|ong|lon|chai)\b",
            alnum_phrase(product_name),
        )
        if m_multi:
            pack_u, n1, inner_u, n2, bu = (
                m_multi.group(1),
                int(m_multi.group(2)),
                m_multi.group(3),
                int(m_multi.group(4)),
                m_multi.group(5),
            )
            base_unit = base_unit or bu
            factors[bu] = 1.0
            factors[inner_u] = float(n2)
            factors[pack_u] = float(n1 * n2)
        else:
            m_single = re.search(
                r"\b(hop|vi|tuyp|loc|thung|bao)\s+(\d+)\s*(vien|goi|ong|lon|chai)\b",
                alnum_phrase(product_name),
            )
            if m_single:
                pack_u, n_u, bu = m_single.group(1), int(m_single.group(2)), m_single.group(3)
                base_unit = base_unit or bu
                factors[bu] = 1.0
                factors[pack_u] = float(n_u)

    return base_unit, factors


def _compute_window_bounds(
    change_date: str,
    n_days: int,
    as_of_date: str | None = None,
) -> tuple[str, str, str, str] | None:
    """Compute [start_b, end_b, start_a, end_a] bounded cleanly by as_of_date."""
    try:
        d_obj = datetime.strptime(change_date, "%Y-%m-%d").date()
    except Exception:
        return None
    start_b = (d_obj - timedelta(days=n_days)).strftime("%Y-%m-%d")
    end_b = (d_obj - timedelta(days=1)).strftime("%Y-%m-%d")
    start_a = d_obj.strftime("%Y-%m-%d")
    end_a_dt = d_obj + timedelta(days=n_days - 1)
    if as_of_date:
        try:
            as_of_dt = datetime.strptime(as_of_date[:10], "%Y-%m-%d").date()
            if end_a_dt > as_of_dt:
                end_a_dt = as_of_dt
        except Exception:
            pass
    if end_a_dt < d_obj:
        return None
    end_a = end_a_dt.strftime("%Y-%m-%d")
    return start_b, end_b, start_a, end_a


def _finalize_pct(q_before: Any, q_after: Any) -> Tuple[str, Any, str | None] | None:
    """Validate finite quantities and compute 2-decimal percent delta."""
    if q_before is None or q_after is None:
        return None
    try:
        qb = float(q_before)
        qa = float(q_after)
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(qb) and math.isfinite(qa)) or qb <= 0.0:
        return None
    return "percent", round((qa - qb) / qb * 100.0, 2), None


def _lookup_price_change_date(
    context: BusinessContext,
    target_price: float | None = None,
    product_code: str | None = None,
    product_name: str | None = None,
) -> str | None:
    """Find the effective date of a price change from price history tables."""
    for tname in [
        "ban_hang_tong_hop_ban_hang_lich_su_gia",
        "lich_su_gia",
        "bang_gia",
        "lich_su_thay_doi_gia",
    ]:
        if tname in context.tables:
            df = context.tables[tname]
            date_col = next(
                (c for c in df.columns if "ngay" in c.lower() or "date" in c.lower()),
                None,
            )
            price_col = next(
                (
                    c
                    for c in df.columns
                    if "gia_moi" in c.lower()
                    or "gia_ban_le" in c.lower()
                    or "gia" in c.lower()
                ),
                None,
            )
            code_col = next((c for c in df.columns if "ma" in c.lower()), None)
            item_col = next(
                (
                    c
                    for c in df.columns
                    if "item" in c.lower()
                    or "san_pham" in c.lower()
                    or "ten" in c.lower()
                ),
                None,
            )

            sub = df.copy()
            if product_code and code_col:
                sub = sub[sub[code_col].astype(str) == str(product_code)]
            elif product_name and item_col:
                p_unaccent = strip_accents(product_name.lower())
                sub = sub[
                    sub[item_col]
                    .astype(str)
                    .apply(lambda x, p_un=p_unaccent: p_un in strip_accents(x.lower()))
                ]

            if target_price is not None and price_col and not sub.empty:
                prices_num = pd.to_numeric(sub[price_col], errors="coerce")
                match_p = sub[prices_num == float(target_price)]
                if not match_p.empty and date_col:
                    return str(match_p[date_col].iloc[0])[:10]

            if not sub.empty and date_col:
                sub_non_init = sub.dropna(subset=[date_col])
                if len(sub_non_init) > 1:
                    return str(sub_non_init[date_col].iloc[-1])[:10]
                if len(sub_non_init) == 1:
                    return str(sub_non_init[date_col].iloc[0])[:10]
    return None


def _resolve_relational_price_change(
    context: BusinessContext,
    q_lower: str,
    asked_unit: str | None,
    change_date: str | None,
    target_price: float | None,
    n_days: int,
    as_of_date: str | None,
) -> Tuple[str, Any, str | None] | None:
    """Resolve price change on relational chi_tiet_don + don_hang + san_pham schemas."""
    sp_df = context.tables["san_pham"]
    sp_cols = list(sp_df.columns)
    sp_name_col = next(
        (c for c in ("ten_goi", "ten_sp", "ten_san_pham", "san_pham", "ten_hang") if c in sp_cols),
        None,
    )
    sp_code_col = next(
        (c for c in ("ma_hang_hoa", "sku", "ma_san_pham_ban", "ma_hh", "ma_sp") if c in sp_cols),
        None,
    )
    if not (sp_name_col and sp_code_col):
        return None

    sp_cands = [
        (str(r[sp_name_col]), "san_pham", sp_name_col)
        for _, r in sp_df.iterrows()
        if pd.notna(r[sp_name_col]) and str(r[sp_name_col]).strip()
    ]
    picked = select_best_product(sp_cands, q_lower)
    if not picked:
        return None
    matched_name = picked[0]
    code_rows = sp_df[sp_df[sp_name_col].astype(str) == matched_name]
    if code_rows.empty:
        return None
    target_sku = str(code_rows[sp_code_col].iloc[0])

    if not change_date:
        change_date = _lookup_price_change_date(
            context,
            target_price=target_price,
            product_code=target_sku,
            product_name=matched_name,
        )
    if not change_date:
        return None

    bounds = _compute_window_bounds(change_date, n_days, as_of_date)
    if not bounds:
        return None
    start_b, end_b, start_a, end_a = bounds

    tbl, order_col, date_col, status_col = find_primary_sales_table(context)
    dh_tbl = "don_hang"
    dh_cols = list(context.tables[dh_tbl].columns)
    dh_order_col = (
        order_col
        if (tbl == dh_tbl and order_col)
        else next(
            (c for c in ("ma_hoa_don", "ma_chung_tu", "so_phieu", "ma_don") if c in dh_cols),
            None,
        )
    )
    dh_date_col = (
        date_col
        if (tbl == dh_tbl and date_col)
        else next(
            (c for c in ("ngay_lap", "thoi_gian_lap", "ngay_ban", "ngay") if c in dh_cols),
            None,
        )
    )
    dh_status_col = (
        status_col
        if (tbl == dh_tbl and status_col)
        else next(
            (c for c in ("tinh_trang", "xu_ly", "tinh_trang_don", "trang_thai") if c in dh_cols),
            None,
        )
    )

    ct_cols = list(context.tables["chi_tiet_don"].columns)
    ct_order_col = next(
        (
            c
            for c in (dh_order_col, "ma_hoa_don", "ma_don_hang", "ma_don", "so_ct")
            if c and c in ct_cols
        ),
        None,
    )
    ct_item_col = next(
        (c for c in ("ma_hang_hoa", "id_sp", "ma_mat_hang", "ma_hang_ban", "ma_sp") if c in ct_cols),
        None,
    )
    ct_base_qty = next(
        (
            c
            for c in ("sl_quy_doi", "so_luong_quy_doi", "so_luong_le", "quy_ra_dv_le", "sl_dv_co_ban")
            if c in ct_cols
        ),
        None,
    )
    ct_raw_qty = next(
        (c for c in ("so_luong", "sl", "s", "so_luong_san_pham", "so_luong_ban") if c in ct_cols),
        None,
    )
    if not (dh_order_col and dh_date_col and ct_order_col and ct_item_col and (ct_base_qty or ct_raw_qty)):
        return None

    qty_expr = (
        f"COALESCE(NULLIF(TRY_CAST(ctd.{ct_base_qty} AS DOUBLE), 'NaN'::DOUBLE), NULLIF(TRY_CAST(ctd.{ct_raw_qty} AS DOUBLE), 'NaN'::DOUBLE))"
        if (ct_base_qty and ct_raw_qty)
        else f"NULLIF(TRY_CAST(ctd.{ct_base_qty or ct_raw_qty} AS DOUBLE), 'NaN'::DOUBLE)"
    )
    status_clause_dh = get_valid_sales_status_clause(
        dh_status_col, order_col=dh_order_col, table=dh_tbl, table_alias="dh"
    )
    sku_esc = target_sku.replace("'", "''")

    sql_b = f"""
        SELECT COALESCE(SUM({qty_expr}), 0.0) as total_sl
        FROM chi_tiet_don ctd
        JOIN {dh_tbl} dh ON ctd.{ct_order_col} = dh.{dh_order_col}
        WHERE ctd.{ct_item_col} = '{sku_esc}'
          AND strftime('%Y-%m-%d', dh.{dh_date_col}::TIMESTAMP) BETWEEN '{start_b}' AND '{end_b}'
          {status_clause_dh}
    """
    sql_a = f"""
        SELECT COALESCE(SUM({qty_expr}), 0.0) as total_sl
        FROM chi_tiet_don ctd
        JOIN {dh_tbl} dh ON ctd.{ct_order_col} = dh.{dh_order_col}
        WHERE ctd.{ct_item_col} = '{sku_esc}'
          AND strftime('%Y-%m-%d', dh.{dh_date_col}::TIMESTAMP) BETWEEN '{start_a}' AND '{end_a}'
          {status_clause_dh}
    """
    try:
        qb = float(context.scalar(sql_b) or 0.0)
        qa = float(context.scalar(sql_a) or 0.0)
        base_unit, factors = build_product_unit_conversions(context, matched_name, target_sku)
        if asked_unit and base_unit and asked_unit != base_unit and asked_unit in factors:
            div = factors[asked_unit]
            if div > 0:
                qb /= div
                qa /= div
        return _finalize_pct(qb, qa)
    except Exception:
        return None


def _resolve_abbreviated_price_change(
    context: BusinessContext,
    q_lower: str,
    change_date: str,
    n_days: int,
    as_of_date: str | None,
) -> Tuple[str, Any, str | None] | None:
    """Resolve price change when ledger uses abbreviation dictionary so_ban_hang_ghi_tat."""
    gt_df = context.tables["so_ban_hang_ghi_tat"]
    if not ({"ghi_tat", "ten_hang"} <= set(gt_df.columns)):
        return None
    cands = [
        (str(r["ten_hang"]), "so_ban_hang_ghi_tat", "ten_hang")
        for _, r in gt_df.iterrows()
        if str(r.get("ten_hang", "")).strip()
    ]
    picked = select_best_product(cands, q_lower)
    matched_codes: list[str] = []
    if picked:
        matched_codes = [
            str(r["ghi_tat"])
            for _, r in gt_df.iterrows()
            if str(r.get("ten_hang", "")).strip() == picked[0]
        ]
    else:
        q_u = strip_accents(q_lower)
        for _, r in gt_df.iterrows():
            code_raw = str(r.get("ghi_tat") or "").strip()
            if len(code_raw) >= 3 and strip_accents(code_raw.lower()) in q_u:
                matched_codes.append(code_raw)
    if not matched_codes:
        return None

    bounds = _compute_window_bounds(change_date, n_days, as_of_date)
    if not bounds:
        return None
    start_b, end_b, start_a, end_a = bounds

    s_df = context.tables["so_ban_hang"]
    s_cols = list(s_df.columns)
    qty_col = find_qty_col(context, "so_ban_hang") or next(
        (c for c in ("sl", "so_luong") if c in s_cols), None
    )
    date_col = next((c for c in ("ngay", "ngay_ban", "date") if c in s_cols), None)
    status_col = next(
        (c for c in ("trang_thai", "trang_thai_xu_ly", "ghi_chu") if c in s_cols), None
    )
    order_col = next(
        (c for c in ("ma_don_hang", "ma_hoa_don", "ma_don", "stt") if c in s_cols), None
    )
    if not (qty_col and date_col):
        return None

    in_codes = ", ".join("'" + c.replace("'", "''") + "'" for c in sorted(set(matched_codes)))
    sc = (
        get_valid_sales_status_clause(status_col, order_col=order_col, table="so_ban_hang")
        if status_col
        else ""
    )
    sql_b = f"""
        SELECT COALESCE(SUM(NULLIF(TRY_CAST({qty_col} AS DOUBLE), 'NaN'::DOUBLE)), 0.0)
        FROM so_ban_hang
        WHERE hang IN ({in_codes})
          AND strftime('%Y-%m-%d', {date_col}::TIMESTAMP) BETWEEN '{start_b}' AND '{end_b}'
          {sc}
    """
    sql_a = f"""
        SELECT COALESCE(SUM(NULLIF(TRY_CAST({qty_col} AS DOUBLE), 'NaN'::DOUBLE)), 0.0)
        FROM so_ban_hang
        WHERE hang IN ({in_codes})
          AND strftime('%Y-%m-%d', {date_col}::TIMESTAMP) BETWEEN '{start_a}' AND '{end_a}'
          {sc}
    """
    try:
        return _finalize_pct(context.scalar(sql_b), context.scalar(sql_a))
    except Exception:
        return None


def _compute_row_quantity(
    row: pd.Series,
    row_unit: str,
    asked_unit: str | None,
    base_unit: str | None,
    factors: dict[str, float],
    raw_qty_col: str | None,
    base_qty_col: str | None,
    rev_col: str | None,
    price_col: str | None,
) -> float:
    """Compute effective quantity for a filtered product row in the requested unit."""
    raw_val = pd.to_numeric(row.get(raw_qty_col), errors="coerce") if raw_qty_col else float("nan")
    base_val = pd.to_numeric(row.get(base_qty_col), errors="coerce") if base_qty_col else float("nan")

    # Fast path when table precomputes base quantity and no different pack output unit is requested
    if pd.notna(base_val) and math.isfinite(float(base_val)):
        if asked_unit and base_unit and asked_unit != base_unit and asked_unit in factors:
            return float(base_val) / factors[asked_unit]
        if not asked_unit or not base_unit or asked_unit == base_unit:
            return float(base_val)
        if row_unit == asked_unit and pd.notna(raw_val) and math.isfinite(float(raw_val)):
            return float(raw_val)
        return float(base_val)

    # Recover NaN raw_val via revenue / price if raw quantity was blank
    qty_in_row_unit: float | None = (
        float(raw_val) if (pd.notna(raw_val) and math.isfinite(float(raw_val))) else None
    )
    if qty_in_row_unit is None and rev_col and price_col:
        rv = pd.to_numeric(row.get(rev_col), errors="coerce")
        pv = pd.to_numeric(row.get(price_col), errors="coerce")
        if pd.notna(rv) and pd.notna(pv) and float(pv) > 0:
            ratio = float(rv) / float(pv)
            qty_in_row_unit = float(round(ratio)) if row_unit not in _CONTINUOUS_UNITS else ratio

    if qty_in_row_unit is None:
        return 0.0

    # Convert row unit -> base unit -> asked unit
    if factors:
        mult = factors.get(row_unit, 1.0)
        qty_base = qty_in_row_unit * mult
        if asked_unit and base_unit and asked_unit != base_unit and asked_unit in factors:
            return qty_base / factors[asked_unit]
        return qty_base

    return qty_in_row_unit


def resolve_price_change_query(
    question: str,
    context: BusinessContext,
    as_of_date: str | None = None,
) -> Tuple[str, Any, str | None] | None:
    """Evaluate price change impact percentage on product quantity sold."""
    q = question.lower()
    q_unaccent = strip_accents(q)

    is_price_change = (
        bool(
            re.search(
                r"\b(?:đổi giá|doi gia|lên giá|len gia|tăng giá|tang gia|xuống giá|xuong gia|ngày tăng giá|ngay tang gia|ngày đổi giá|ngay doi gia)\b",
                q,
            )
        )
        or bool(
            re.search(r"\b(?:lên|len)\s+(\d+)\s*(?:k|nghìn|nghin|đ|d|đồng|dong)?\b", q)
        )
        or bool(re.search(r"\bsau lần tăng giá\b", q))
        or bool(re.search(r"\btang gia len\b", q_unaccent) and not re.search(r"\btang giam\b", q_unaccent))
    )
    if not is_price_change:
        return None

    if any(k in q for k in ("doanh thu thang nay", "cung ky thang truoc")) or re.search(
        r"\b(?:th(?:ang)?\s*\d{1,2}(?:/\d{2,4})?|t\d{1,2}/\d{2,4})\s+so\s+(?:voi|vs)\s+(?:th|t\d|cung\s+ky)\b",
        q_unaccent,
    ):
        return None

    has_window_signal = (
        any(
            k in q
            for k in (
                "kể từ",
                "ke tu",
                "từ khi",
                "tu khi",
                "sau khi",
                "sau lần tăng",
                "sau lan tang",
                "kể từ ngày",
                "ke tu ngay",
                "liền trước",
                "lien truoc",
                "trước đó",
                "truoc do",
                "ngay trước",
                "ngay truoc",
                "từ bữa đó",
                "tu bua do",
                "từ hôm đó",
                "tu hom do",
            )
        )
        or bool(re.search(r"\bso\s+\d+\s+ngay\b", q_unaccent))
        or bool(re.search(r"\b30\s+ngay\s+dau\b", q_unaccent))
    )
    if not has_window_signal:
        return None

    n_m = re.search(r"(\d+)\s*(?:ngày|ngay)", q)
    n_days = int(n_m.group(1)) if n_m else 30
    asked_unit = _extract_asked_unit(q)

    date_m = re.search(
        r"(?:ngày|ngay|từ\s+ngày|tu\s+ngay|bữa|bua|hôm|hom|kể từ|ke tu)?\s*(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})",
        q,
    )
    change_date: str | None = None
    if date_m:
        d, m, y = int(date_m.group(1)), int(date_m.group(2)), int(date_m.group(3))
        if 1 <= d <= 31 and 1 <= m <= 12 and 2024 <= y <= 2027:
            change_date = f"{y:04d}-{m:02d}-{d:02d}"

    price_m = re.search(
        r"(?:lên|len|từ|tu|lên\s+giá|len\s+gia|giá|gia)?\s*([\d.]+)\s*(?:đ|d|đồng|dong|k)\b",
        q,
    )
    target_price = None
    if price_m:
        raw_p = price_m.group(1).replace(".", "")
        try:
            target_price = float(raw_p)
            if target_price < 1000:
                target_price *= 1000
        except Exception:
            pass

    # 1. Relational POS schema: chi_tiet_don JOIN don_hang JOIN san_pham
    if {"chi_tiet_don", "don_hang", "san_pham"} <= set(context.tables):
        rel_res = _resolve_relational_price_change(
            context, q, asked_unit, change_date, target_price, n_days, as_of_date
        )
        if rel_res is not None:
            return rel_res

    # 2. Abbreviation-mapped ledger: so_ban_hang + so_ban_hang_ghi_tat
    if {"so_ban_hang", "so_ban_hang_ghi_tat"} <= set(context.tables) and change_date:
        abbr_res = _resolve_abbreviated_price_change(context, q, change_date, n_days, as_of_date)
        if abbr_res is not None:
            return abbr_res

    # 3. General flat / single-table sales schema
    tbl, order_col, date_col, status_col = find_primary_sales_table(context)
    if not tbl or not date_col:
        return None
    item_col = find_item_name_col(context, tbl)
    raw_qty_col = find_qty_col(context, tbl)
    t_cols = list(context.tables[tbl].columns)
    base_qty_col = next(
        (
            c
            for c in ("so_luong_quy_doi", "sl_quy_doi", "so_luong_le", "sl_le", "quy_ra_dv_le")
            if c in t_cols
        ),
        None,
    )
    if not item_col or not (raw_qty_col or base_qty_col):
        return None

    candidates = list(get_all_product_names(context))
    distinct_items = (
        context.tables[tbl][item_col].dropna().astype(str).str.strip().unique().tolist()
    )
    candidates.extend((item, tbl, item_col) for item in distinct_items if item)

    picked = select_best_product(candidates, q)
    if not picked:
        return None
    matched_product_name, p_table, p_col = picked

    matched_code = None
    matched_p_norm = norm_vn(matched_product_name)
    for c_code in ("ma_hang_hoa", "ma_san_pham_ban", "ma_h", "ma_sp"):
        if c_code in context.tables.get(p_table, {}):
            sub_c = context.tables[p_table][
                context.tables[p_table][p_col].astype(str).map(norm_vn) == matched_p_norm
            ]
            if not sub_c.empty:
                matched_code = str(sub_c[c_code].iloc[0])
                break

    if not change_date:
        change_date = _lookup_price_change_date(
            context,
            target_price=target_price,
            product_code=matched_code,
            product_name=matched_product_name,
        )
    if not change_date:
        return None

    bounds = _compute_window_bounds(change_date, n_days, as_of_date)
    if not bounds:
        return None
    start_before, end_before, start_after, end_after = bounds

    unit_col = next((c for c in ("don_vi_tinh", "donvi", "dvt", "unit") if c in t_cols), None)
    rev_col = next(
        (c for c in ("thanh_tien", "tong_tien", "tri_gia", "amount", "tien_ban", "tien", "t") if c in t_cols),
        None,
    )
    price_col = next(
        (c for c in ("gia_san_pham", "gia_don_vi", "don_gia", "gia_ban", "gia", "gia_le") if c in t_cols),
        None,
    )
    status_clause = get_valid_sales_status_clause(status_col, order_col=order_col, table=tbl)

    norm_p_sql = " ".join(matched_product_name.strip().lower().split()).replace("'", "''")
    ws_re = r"\s+"
    norm_col_expr = f"regexp_replace(lower(trim({item_col})), '{ws_re}', ' ', 'g')"
    is_topping_query = norm_p_sql.startswith("topping") or "topping" in alnum_phrase(norm_p_sql)
    item_filter = (
        f"({norm_col_expr} = '{norm_p_sql}' "
        f"OR {norm_col_expr} LIKE '{norm_p_sql} + %'"
        + (f" OR {norm_col_expr} LIKE '%+ {norm_p_sql}%')" if is_topping_query else ")")
    )

    sql_rows = f"""
        SELECT *, strftime('%Y-%m-%d', {date_col}::TIMESTAMP) AS _pc_date
        FROM {tbl}
        WHERE {item_filter}
          AND strftime('%Y-%m-%d', {date_col}::TIMESTAMP) BETWEEN '{start_before}' AND '{end_after}'
          {status_clause}
    """
    try:
        rows_df = context.query(sql_rows)
    except Exception:
        return None

    if rows_df.empty:
        return None

    base_unit, factors = build_product_unit_conversions(context, matched_product_name, matched_code)
    p_norm_target = norm_vn(matched_product_name)
    q_before = 0.0
    q_after = 0.0

    for _, row in rows_df.iterrows():
        d_str = str(row.get("_pc_date") or "")[:10]
        if start_before <= d_str <= end_before:
            bucket = "before"
        elif start_after <= d_str <= end_after:
            bucket = "after"
        else:
            continue

        item_str = str(row.get(item_col) or "").strip()
        row_unit = norm_vn(row.get(unit_col)) if unit_col else ""

        if " + " in item_str and is_topping_query and norm_vn(item_str) != p_norm_target:
            base_q = _compute_row_quantity(
                row, row_unit, None, base_unit, factors, raw_qty_col, base_qty_col, rev_col, price_col
            )
            _, tops = split_inline_toppings(item_str, line_qty=base_q, line_base_qty=base_q)
            row_qty = sum(
                float(t["qty"])
                for t in tops
                if norm_vn(t.get("name")) == p_norm_target or norm_vn(t.get("raw_name")) == p_norm_target
            )
        else:
            row_qty = _compute_row_quantity(
                row, row_unit, asked_unit, base_unit, factors, raw_qty_col, base_qty_col, rev_col, price_col
            )

        if bucket == "before":
            q_before += row_qty
        else:
            q_after += row_qty

    return _finalize_pct(q_before, q_after)
