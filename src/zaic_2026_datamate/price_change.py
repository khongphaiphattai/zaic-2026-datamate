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

from zaic_2026_datamate.cleaner import (
    clean_vietnamese_currency,
    compact_digit_unit_spaces,
    normalize_query_telex,
    round_half_up,
    split_inline_toppings,
)
from zaic_2026_datamate.data_loader import BusinessContext, parse_mixed_excel_dates
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
            (
                cols[k]
                for k in ("ten_san_pham", "ten_hang", "hang", "ten_sp")
                if k in cols
            ),
            None,
        )
        unit_c = next(
            (cols[k] for k in ("don_vi_quy_doi", "don_vi_tinh", "dvt") if k in cols),
            None,
        )
        ratio_c = next(
            (cols[k] for k in ("ti_le_quy_doi", "he_so_quy_doi", "he_so") if k in cols),
            None,
        )
        base_c = next(
            (cols[k] for k in ("don_vi_co_ban", "don_vi_co_so") if k in cols), None
        )
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
            (
                cols[k]
                for k in (
                    "hang",
                    "hang_ban",
                    "ten_hang",
                    "ten_sp",
                    "ten_san_pham",
                    "ten_mon",
                )
                if k in cols
            ),
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
            (
                sp_cols[k]
                for k in ("ma_hang_hoa", "sku", "ma_san_pham_ban", "ma_hh", "ma_sp")
                if k in sp_cols
            ),
            None,
        )
        sp_name_c = next(
            (
                sp_cols[k]
                for k in ("ten_goi", "ten_sp", "ten_san_pham", "san_pham", "ten_hang")
                if k in sp_cols
            ),
            None,
        )
        sp_bu_c = next(
            (
                sp_cols[k]
                for k in (
                    "don_vi_co_so",
                    "don_vi_co_ban",
                    "don_vi",
                    "don_vi_tinh",
                    "dvt",
                )
                if k in sp_cols
            ),
            None,
        )
        resolved_code = code_norm
        if sp_bu_c and (sp_code_c or sp_name_c):
            for _, r in sp.iterrows():
                row_code = norm_vn(r[sp_code_c]) if sp_code_c else ""
                row_name = norm_vn(r[sp_name_c]) if sp_name_c else ""
                if (resolved_code and row_code == resolved_code) or (
                    p_norm and row_name == p_norm
                ):
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
                pack_u, n_u, bu = (
                    m_single.group(1),
                    int(m_single.group(2)),
                    m_single.group(3),
                )
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
    except TypeError, ValueError:
        return None
    if not (math.isfinite(qb) and math.isfinite(qa)) or qb <= 0.0:
        return None
    return "percent", round((qa - qb) / qb * 100.0, 2), None


def _find_product_code_col(df: pd.DataFrame) -> str | None:
    """Select the product/SKU code column while skipping order or customer IDs."""
    preferred = {
        "ma_sp",
        "ma_hang",
        "ma_hh",
        "ma_san_pham",
        "sku",
        "ma_hang_hoa",
        "ma_thuoc",
    }
    hit = next((c for c in df.columns if c.lower() in preferred), None)
    if hit:
        return hit
    return next(
        (
            c
            for c in df.columns
            if "ma" in re.split(r"[^a-z0-9]+", strip_accents(c.lower()))
            and not any(
                bad in c.lower()
                for bad in ("don", "giao_dich", "hoa_don", "phieu", "khach", "ncc")
            )
        ),
        None,
    )


def _find_product_name_col(df: pd.DataFrame) -> str | None:
    """Select the product name column while skipping customer, supplier, and unit columns."""
    excluded = (
        "khach",
        "tenkh",
        "ten_kh",
        "ncc",
        "nha_cc",
        "cung_cap",
        "nhan_vien",
        "ten_nv",
        "don_vi",
        "donvi",
        "nguoi",
        "ma_",
        "ma ",
    )
    valid_cols = [
        c for c in df.columns if not any(bad in c.lower() for bad in excluded)
    ]
    tier1 = (
        "ten_sp",
        "ten_san_pham",
        "ten_hang",
        "ten_mon",
        "ten_thuoc",
        "hang_ban",
        "san_pham",
        "hang_hoa",
        "item",
        "mon",
    )
    hit = next((c for c in valid_cols if any(k in c.lower() for k in tier1)), None)
    if hit:
        return hit
    exact_tier2 = {"hang", "ten", "name", "product"}
    hit = next((c for c in valid_cols if c.lower() in exact_tier2), None)
    if hit:
        return hit
    return next(
        (
            c
            for c in valid_cols
            if "ten" in re.split(r"[^a-z0-9]+", strip_accents(c.lower()))
        ),
        None,
    )


def _iso_date_scalar(val: Any) -> str | None:
    """Parse a scalar cell value into an ISO YYYY-MM-DD date string."""
    if val is None or (isinstance(val, float) and math.isnan(val)):
        return None
    s = str(val).strip()
    if not s or s.lower() in {"nan", "nat", "none"}:
        return None
    m_iso = re.match(r"^(\d{4})-(\d{2})-(\d{2})\b", s)
    if m_iso:
        try:
            datetime(int(m_iso.group(1)), int(m_iso.group(2)), int(m_iso.group(3)))
            return f"{m_iso.group(1)}-{m_iso.group(2)}-{m_iso.group(3)}"
        except ValueError:
            pass
    parsed = parse_mixed_excel_dates(pd.Series([val]))
    if not parsed.empty and pd.notna(parsed.iloc[0]):
        return parsed.iloc[0].strftime("%Y-%m-%d")
    return None


def _lookup_price_change_date(
    context: BusinessContext,
    target_price: float | None = None,
    product_code: str | None = None,
    product_name: str | None = None,
) -> str | None:
    """Find the effective date of a price change from price history tables."""
    from zaic_2026_datamate.semantic_contract import item_norm_key

    preferred = [
        "ban_hang_tong_hop_ban_hang_lich_su_gia",
        "lich_su_gia",
        "bang_gia",
        "lich_su_thay_doi_gia",
    ]
    cand_tables: list[str] = [t for t in preferred if t in context.tables]
    for tname, df in context.tables.items():
        if tname in cand_tables or "_sheet" in tname.lower() or df.empty:
            continue
        tlow = tname.lower()
        if any(
            k in tlow
            for k in ("lich_su_gia", "lich_su_thay_doi_gia", "thay_doi_gia", "bang_gia")
        ):
            cand_tables.append(tname)

    # Resolve candidate codes from catalog tables when only product_name is known
    catalog_codes: set[str] = set()
    if product_code:
        catalog_codes.add(str(product_code).strip().lower())
    if product_name:
        target_norm = item_norm_key(product_name)
        for tname, df in context.tables.items():
            if "_sheet" in tname.lower() or df.empty:
                continue
            tlow = tname.lower()
            if not any(
                k in tlow
                for k in ("san_pham", "hang_hoa", "danh_muc", "thuoc", "mat_hang")
            ):
                continue
            c_col = _find_product_code_col(df)
            n_col = _find_product_name_col(df)
            if c_col and n_col:
                for _, r in df.iterrows():
                    if item_norm_key(r[n_col]) == target_norm and pd.notna(r[c_col]):
                        c_val = str(r[c_col]).strip().lower()
                        if c_val:
                            catalog_codes.add(c_val)

    for tname in cand_tables:
        df = context.tables[tname]
        if df.empty:
            continue
        date_col = next(
            (
                c
                for c in df.columns
                if any(
                    k in c.lower()
                    for k in (
                        "ngay_ap_dung",
                        "ngay_thay_doi",
                        "ngay_doi_gia",
                        "ngay_hieu_luc",
                    )
                )
            ),
            None,
        ) or next(
            (c for c in df.columns if "ngay" in c.lower() or "date" in c.lower()),
            None,
        )
        price_col = (
            next(
                (
                    c
                    for c in df.columns
                    if any(k in c.lower() for k in ("gia_moi", "gia_sau"))
                ),
                None,
            )
            or next(
                (
                    c
                    for c in df.columns
                    if any(k in c.lower() for k in ("gia_ban_le", "gia_ban", "don_gia"))
                ),
                None,
            )
            or next(
                (
                    c
                    for c in df.columns
                    if "gia" in c.lower()
                    and not any(
                        bad in c.lower()
                        for bad in ("gia_cu", "gia_truoc", "gia_nhap", "gia_von")
                    )
                ),
                None,
            )
        )
        code_col = _find_product_code_col(df)
        item_col = _find_product_name_col(df)
        if not date_col:
            continue

        sub = df.copy()
        filtered = False
        if code_col and catalog_codes:
            code_mask = (
                sub[code_col].astype(str).str.strip().str.lower().isin(catalog_codes)
            )
            if code_mask.any():
                sub = sub[code_mask]
                filtered = True
        if not filtered and product_name and item_col:
            p_key = item_norm_key(product_name)
            norm_mask = sub[item_col].apply(
                lambda x, target=p_key: item_norm_key(x) == target
            )
            if norm_mask.any():
                sub = sub[norm_mask]
                filtered = True
            else:
                p_unaccent = strip_accents(product_name.lower())
                sub_mask = (
                    sub[item_col]
                    .astype(str)
                    .apply(lambda x, p_un=p_unaccent: p_un in strip_accents(x.lower()))
                )
                sub = sub[sub_mask]
                filtered = True

        if not filtered or sub.empty:
            continue

        if target_price is not None and price_col:
            prices_num = pd.to_numeric(
                sub[price_col].apply(clean_vietnamese_currency), errors="coerce"
            )
            match_p = sub[(prices_num - float(target_price)).abs() <= 1e-6]
            if not match_p.empty:
                iso = _iso_date_scalar(match_p[date_col].iloc[0])
                if iso:
                    return iso
            continue

        sub_non_init = sub.dropna(subset=[date_col])
        if len(sub_non_init) > 1:
            iso = _iso_date_scalar(sub_non_init[date_col].iloc[-1])
            if iso:
                return iso
        if len(sub_non_init) == 1:
            iso = _iso_date_scalar(sub_non_init[date_col].iloc[0])
            if iso:
                return iso
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
        (
            c
            for c in ("ten_goi", "ten_sp", "ten_san_pham", "san_pham", "ten_hang")
            if c in sp_cols
        ),
        None,
    )
    sp_code_col = next(
        (
            c
            for c in ("ma_hang_hoa", "sku", "ma_san_pham_ban", "ma_hh", "ma_sp")
            if c in sp_cols
        ),
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
            (
                c
                for c in ("ma_hoa_don", "ma_chung_tu", "so_phieu", "ma_don")
                if c in dh_cols
            ),
            None,
        )
    )
    dh_date_col = (
        date_col
        if (tbl == dh_tbl and date_col)
        else next(
            (
                c
                for c in ("ngay_lap", "thoi_gian_lap", "ngay_ban", "ngay")
                if c in dh_cols
            ),
            None,
        )
    )
    dh_status_col = (
        status_col
        if (tbl == dh_tbl and status_col)
        else next(
            (
                c
                for c in ("tinh_trang", "xu_ly", "tinh_trang_don", "trang_thai")
                if c in dh_cols
            ),
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
        (
            c
            for c in ("ma_hang_hoa", "id_sp", "ma_mat_hang", "ma_hang_ban", "ma_sp")
            if c in ct_cols
        ),
        None,
    )
    ct_base_qty = next(
        (
            c
            for c in (
                "sl_quy_doi",
                "so_luong_quy_doi",
                "so_luong_le",
                "quy_ra_dv_le",
                "sl_dv_co_ban",
            )
            if c in ct_cols
        ),
        None,
    )
    ct_raw_qty = next(
        (
            c
            for c in ("so_luong", "sl", "s", "so_luong_san_pham", "so_luong_ban")
            if c in ct_cols
        ),
        None,
    )
    if not (
        dh_order_col
        and dh_date_col
        and ct_order_col
        and ct_item_col
        and (ct_base_qty or ct_raw_qty)
    ):
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
        base_unit, factors = build_product_unit_conversions(
            context, matched_name, target_sku
        )
        if (
            asked_unit
            and base_unit
            and asked_unit != base_unit
            and asked_unit in factors
        ):
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

    in_codes = ", ".join(
        "'" + c.replace("'", "''") + "'" for c in sorted(set(matched_codes))
    )
    sc = (
        get_valid_sales_status_clause(
            status_col, order_col=order_col, table="so_ban_hang"
        )
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
    raw_val = (
        pd.to_numeric(row.get(raw_qty_col), errors="coerce")
        if raw_qty_col
        else float("nan")
    )
    base_val = (
        pd.to_numeric(row.get(base_qty_col), errors="coerce")
        if base_qty_col
        else float("nan")
    )

    # Fast path when table precomputes base quantity and no different pack output unit is requested
    if pd.notna(base_val) and math.isfinite(float(base_val)):
        if (
            asked_unit
            and base_unit
            and asked_unit != base_unit
            and asked_unit in factors
        ):
            return float(base_val) / factors[asked_unit]
        if not asked_unit or not base_unit or asked_unit == base_unit:
            return float(base_val)
        if (
            row_unit == asked_unit
            and pd.notna(raw_val)
            and math.isfinite(float(raw_val))
        ):
            return float(raw_val)
        return float(base_val)

    # Recover NaN raw_val via revenue / price if raw quantity was blank
    qty_in_row_unit: float | None = (
        float(raw_val)
        if (pd.notna(raw_val) and math.isfinite(float(raw_val)))
        else None
    )
    if qty_in_row_unit is None and rev_col and price_col:
        rv = pd.to_numeric(row.get(rev_col), errors="coerce")
        pv = pd.to_numeric(row.get(price_col), errors="coerce")
        if pd.notna(rv) and pd.notna(pv) and float(pv) > 0:
            ratio = float(rv) / float(pv)
            qty_in_row_unit = (
                float(round(ratio)) if row_unit not in _CONTINUOUS_UNITS else ratio
            )

    if qty_in_row_unit is None:
        return 0.0

    # Convert row unit -> base unit -> asked unit
    if factors:
        mult = factors.get(row_unit, 1.0)
        qty_base = qty_in_row_unit * mult
        if (
            asked_unit
            and base_unit
            and asked_unit != base_unit
            and asked_unit in factors
        ):
            return qty_base / factors[asked_unit]
        return qty_base

    return qty_in_row_unit


def _resolve_legacy_price_change_query(
    question: str,
    context: BusinessContext,
    as_of_date: str | None = None,
) -> Tuple[str, Any, str | None] | None:
    """Evaluate price change impact percentage on legacy schemas."""
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
        or bool(
            re.search(r"\btang gia len\b", q_unaccent)
            and not re.search(r"\btang giam\b", q_unaccent)
        )
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
        abbr_res = _resolve_abbreviated_price_change(
            context, q, change_date, n_days, as_of_date
        )
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
            for c in (
                "so_luong_quy_doi",
                "sl_quy_doi",
                "so_luong_le",
                "sl_le",
                "quy_ra_dv_le",
            )
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
                context.tables[p_table][p_col].astype(str).map(norm_vn)
                == matched_p_norm
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

    unit_col = next(
        (c for c in ("don_vi_tinh", "donvi", "dvt", "unit") if c in t_cols), None
    )
    rev_col = next(
        (
            c
            for c in (
                "thanh_tien",
                "tong_tien",
                "tri_gia",
                "amount",
                "tien_ban",
                "tien",
                "t",
            )
            if c in t_cols
        ),
        None,
    )
    price_col = next(
        (
            c
            for c in (
                "gia_san_pham",
                "gia_don_vi",
                "don_gia",
                "gia_ban",
                "gia",
                "gia_le",
            )
            if c in t_cols
        ),
        None,
    )
    status_clause = get_valid_sales_status_clause(
        status_col, order_col=order_col, table=tbl
    )

    norm_p_sql = " ".join(matched_product_name.strip().lower().split()).replace(
        "'", "''"
    )
    ws_re = r"\s+"
    norm_col_expr = f"regexp_replace(lower(trim({item_col})), '{ws_re}', ' ', 'g')"
    is_topping_query = norm_p_sql.startswith("topping") or "topping" in alnum_phrase(
        norm_p_sql
    )
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

    base_unit, factors = build_product_unit_conversions(
        context, matched_product_name, matched_code
    )
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

        if (
            " + " in item_str
            and is_topping_query
            and norm_vn(item_str) != p_norm_target
        ):
            base_q = _compute_row_quantity(
                row,
                row_unit,
                None,
                base_unit,
                factors,
                raw_qty_col,
                base_qty_col,
                rev_col,
                price_col,
            )
            _, tops = split_inline_toppings(
                item_str, line_qty=base_q, line_base_qty=base_q
            )
            row_qty = sum(
                float(t["qty"])
                for t in tops
                if norm_vn(t.get("name")) == p_norm_target
                or norm_vn(t.get("raw_name")) == p_norm_target
            )
        else:
            row_qty = _compute_row_quantity(
                row,
                row_unit,
                asked_unit,
                base_unit,
                factors,
                raw_qty_col,
                base_qty_col,
                rev_col,
                price_col,
            )

        if bucket == "before":
            q_before += row_qty
        else:
            q_after += row_qty

    return _finalize_pct(q_before, q_after)


def _extract_all_dates(q_u: str) -> list[str]:
    """Extract all valid calendar DD/MM/YYYY dates in unaccented query text."""
    dates: list[str] = []
    for m in re.finditer(r"\b(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})\b", q_u):
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if not (2000 <= y <= 2035):
            continue
        try:
            datetime(y, mo, d)
        except ValueError:
            continue
        iso = f"{y:04d}-{mo:02d}-{d:02d}"
        if iso not in dates:
            dates.append(iso)
    return dates


def _load_canonical_item_candidates(
    context: BusinessContext, contract: Any
) -> list[tuple[str, str]]:
    """Collect deduplicated (item_key, item_name) candidates from DuckDB & catalog tables."""
    from zaic_2026_datamate.semantic_contract import item_norm_key

    seen_norm: dict[str, tuple[str, str]] = {}
    rows = context.duckdb_conn.execute(
        """
        SELECT DISTINCT item_key, item_name
        FROM canonical_order_lines
        WHERE is_valid_sale = TRUE
          AND item_name IS NOT NULL
          AND trim(item_name) <> ''
        """
    ).fetchall()
    for ikey, iname in rows:
        name_s = str(iname).strip()
        nkey = item_norm_key(name_s)
        if nkey and nkey not in seen_norm:
            seen_norm[nkey] = (str(ikey) if ikey is not None else "", name_s)

    for cat in getattr(contract, "catalog_items", ()):
        name_s = str(getattr(cat, "item_name", "") or "").strip()
        code_s = str(
            getattr(cat, "item_code", "") or getattr(cat, "item_key", "") or ""
        ).strip()
        nkey = item_norm_key(name_s)
        if name_s and nkey and nkey not in seen_norm:
            seen_norm[nkey] = (code_s, name_s)

    for tname, df in context.tables.items():
        if "_sheet" in tname.lower() or df.empty:
            continue
        if not any(
            k in tname.lower()
            for k in ("san_pham", "hang_hoa", "danh_muc", "thuoc", "mat_hang")
        ):
            continue
        c_col = _find_product_code_col(df)
        n_col = _find_product_name_col(df)
        if not n_col:
            continue
        for _, r in df.iterrows():
            val = r[n_col]
            if pd.isna(val):
                continue
            name_s = str(val).strip()
            nkey = item_norm_key(name_s)
            if name_s and nkey and nkey not in seen_norm:
                code_s = str(r[c_col]).strip() if c_col and pd.notna(r[c_col]) else ""
                seen_norm[nkey] = (code_s, name_s)

    return list(seen_norm.values())


def _match_canonical_item(
    q_text: str, candidates: list[tuple[str, str]]
) -> tuple[str, str] | None:
    """Unambiguously match the longest canonical product mention in the query."""
    if not candidates:
        return None
    q_alnum = alnum_phrase(q_text)
    q_compact = compact_digit_unit_spaces(q_alnum)
    q_sizes = _extract_size_tags(q_text)
    q_tokens = set(q_compact.split())

    scored: list[tuple[tuple[int, int, int], str, str]] = []
    for ikey, iname in candidates:
        n_alnum = alnum_phrase(iname)
        n_compact = compact_digit_unit_spaces(n_alnum)
        if not n_compact:
            continue
        i_sizes = _extract_size_tags(iname)
        if q_sizes and i_sizes and not (q_sizes & i_sizes):
            continue

        exact = bool(
            re.search(rf"\b{re.escape(n_alnum)}\b", q_alnum)
            or re.search(rf"\b{re.escape(n_compact)}\b", q_compact)
        )
        if exact:
            scored.append(((2, len(n_compact), len(n_compact.split())), ikey, iname))
            continue

        content_tokens = [
            tok for tok in n_compact.split() if tok not in _MATCH_STOPWORDS
        ]
        if len(content_tokens) >= 2 and all(tok in q_tokens for tok in content_tokens):
            scored.append(((1, len(content_tokens), len(n_compact)), ikey, iname))

    if not scored:
        return None
    best_tier = max(s[0][0] for s in scored)
    tier_items = [s for s in scored if s[0][0] == best_tier]
    top_score = max(s[0] for s in tier_items)
    top_matches = [(s[1], s[2]) for s in tier_items if s[0] == top_score]
    if len(top_matches) != 1:
        return None
    return top_matches[0]


def _build_canonical_unit_factors(
    context: BusinessContext, item_name: str, item_key: str
) -> dict[str, float]:
    """Map normalized unit names to conversion factors relative to canonical base_unit."""
    from zaic_2026_datamate.semantic_contract import item_norm_key

    factors: dict[str, float] = {}
    target_norm = item_norm_key(item_name)

    rows = context.duckdb_conn.execute(
        """
        SELECT DISTINCT item_key, item_name, base_unit
        FROM canonical_order_lines
        WHERE is_valid_sale = TRUE
          AND base_unit IS NOT NULL
          AND trim(base_unit) <> ''
        """
    ).fetchall()
    for r_key, r_name, r_bu in rows:
        if str(r_key or "") == item_key or item_norm_key(r_name) == target_norm:
            u_norm = strip_accents(str(r_bu).lower().strip())
            if u_norm:
                factors[u_norm] = 1.0

    # Collect matching product codes across catalog tables
    codes: set[str] = set()
    if item_key:
        codes.add(item_key.strip().lower())
    for tname, df in context.tables.items():
        if "_sheet" in tname.lower() or df.empty:
            continue
        tlow = tname.lower()
        if not any(
            k in tlow
            for k in (
                "san_pham",
                "hang_hoa",
                "danh_muc",
                "thuoc",
                "mat_hang",
                "don_vi",
                "quy_doi",
            )
        ):
            continue
        c_col = _find_product_code_col(df)
        n_col = _find_product_name_col(df)
        if c_col and n_col:
            for _, r in df.iterrows():
                if item_norm_key(r[n_col]) == target_norm and pd.notna(r[c_col]):
                    cv = str(r[c_col]).strip().lower()
                    if cv:
                        codes.add(cv)

    base_units: set[str] = set(factors.keys())
    for tname, df in context.tables.items():
        if "_sheet" in tname.lower() or df.empty:
            continue
        tlow = tname.lower()
        if not any(
            k in tlow
            for k in (
                "don_vi_sp",
                "don_vi_quy_doi",
                "quy_doi",
                "danh_sach_don_vi",
                "san_pham",
                "hang_hoa",
                "danh_muc",
                "thuoc",
            )
        ):
            continue
        c_col = _find_product_code_col(df)
        n_col = _find_product_name_col(df)
        base_col_markers = ("co_ban", "co_so", "goc", "dv_le", "dv_nho")
        bu_col = next(
            (c for c in df.columns if any(m in c.lower() for m in base_col_markers)),
            None,
        )
        u_col = next(
            (
                c
                for c in df.columns
                if c != bu_col
                and not any(m in c.lower() for m in base_col_markers)
                and (
                    c.lower()
                    in {
                        "don_vi_quy_doi",
                        "don_vi_ban",
                        "don_vi_tinh",
                        "don_vi",
                        "dvt",
                        "unit",
                        "dv_lon",
                        "dv_nhap",
                    }
                    or "don_vi" in c.lower()
                )
            ),
            None,
        )
        factor_exact = {
            "bang",
            "he_so",
            "he_so_qd",
            "he_so_quy_doi",
            "ty_le_quy_doi",
            "ti_le_quy_doi",
            "ty_le",
            "ti_le",
            "so_luong_quy_doi",
            "sl_quy_doi",
            "quy_doi",
            "factor",
            "ratio",
        }
        f_col = next(
            (
                c
                for c in df.columns
                if (
                    c.lower() in factor_exact
                    or "he_so" in c.lower()
                    or "quy_doi" in c.lower()
                )
                and not any(
                    bad in c.lower()
                    for bad in ("thue", "vat", "chiet_khau", "giam_gia")
                )
            ),
            None,
        )
        for _, r in df.iterrows():
            matched_row = False
            if c_col and pd.notna(r[c_col]) and str(r[c_col]).strip().lower() in codes:
                matched_row = True
            elif n_col and item_norm_key(r[n_col]) == target_norm:
                matched_row = True
            if not matched_row:
                continue
            if bu_col and pd.notna(r[bu_col]):
                bu_u = strip_accents(str(r[bu_col]).lower().strip())
                if bu_u:
                    base_units.add(bu_u)
                    factors[bu_u] = 1.0
            if u_col and pd.notna(r[u_col]):
                u_u = strip_accents(str(r[u_col]).lower().strip())
                if not u_u or u_u in base_units:
                    continue
                if f_col and pd.notna(r[f_col]):
                    f_val = pd.to_numeric(pd.Series([r[f_col]]), errors="coerce").iloc[
                        0
                    ]
                    if pd.notna(f_val) and float(f_val) > 0:
                        factors[u_u] = float(f_val)
                elif "don_vi" in tlow or "quy_doi" in tlow:
                    continue
                elif u_u not in factors:
                    factors[u_u] = 1.0

    legacy_code = next(iter(codes), None)
    legacy_bu, legacy_factors = build_product_unit_conversions(
        context, item_name, product_code=legacy_code
    )
    if legacy_bu:
        bu_norm = strip_accents(legacy_bu.lower().strip())
        if bu_norm:
            base_units.add(bu_norm)
            factors[bu_norm] = 1.0
    for k, v in legacy_factors.items():
        k_norm = strip_accents(k.lower().strip())
        if k_norm and v > 0 and k_norm not in base_units and k_norm not in factors:
            factors[k_norm] = float(v)
    for bu_u in base_units:
        factors[bu_u] = 1.0

    if not factors:
        sale_units: set[str] = set()
        unit_ratios: dict[str, list[float]] = {}
        for tname, df in context.tables.items():
            if "_sheet" in tname.lower() or df.empty:
                continue
            tlow = tname.lower()
            if not any(
                k in tlow
                for k in ("ban_hang", "don_hang", "hoa_don", "chi_tiet", "giao_dich")
            ):
                continue
            u_col = next(
                (
                    c
                    for c in df.columns
                    if c.lower()
                    in {
                        "don_vi_tinh",
                        "don_vi_ban",
                        "don_vi",
                        "donvi",
                        "dvt",
                        "unit",
                        "d",
                    }
                ),
                None,
            )
            if not u_col:
                continue
            c_col = _find_product_code_col(df)
            n_col = _find_product_name_col(df)
            if not (c_col or n_col):
                continue
            mask = pd.Series(False, index=df.index)
            if c_col and codes:
                mask = mask | df[c_col].astype(str).str.strip().str.lower().isin(codes)
            if n_col:
                mask = mask | df[n_col].apply(lambda x: item_norm_key(x) == target_norm)
            sub = df[mask]
            if sub.empty:
                continue
            q_col = next(
                (
                    c
                    for c in df.columns
                    if c.lower()
                    in {
                        "so_luong_san_pham",
                        "so_luong_hang",
                        "so_luong",
                        "sl_ban",
                        "slg",
                        "sl",
                        "qty",
                    }
                ),
                None,
            )
            bq_col = next(
                (
                    c
                    for c in df.columns
                    if c.lower()
                    in {
                        "so_luong_quy_doi",
                        "sl_quy_doi",
                        "so_luong_le",
                        "sl_le",
                        "so_luong_co_ban",
                        "sl_co_ban",
                    }
                ),
                None,
            )
            for _, r in sub.iterrows():
                u_val = r[u_col]
                if pd.isna(u_val):
                    continue
                u_u = strip_accents(str(u_val).lower().strip())
                if not u_u:
                    continue
                sale_units.add(u_u)
                if q_col and bq_col:
                    qv = pd.to_numeric(pd.Series([r[q_col]]), errors="coerce").iloc[0]
                    bqv = pd.to_numeric(pd.Series([r[bq_col]]), errors="coerce").iloc[0]
                    if (
                        pd.notna(qv)
                        and pd.notna(bqv)
                        and float(qv) > 0
                        and float(bqv) > 0
                    ):
                        unit_ratios.setdefault(u_u, []).append(float(bqv) / float(qv))

        for u_u, ratios in unit_ratios.items():
            if ratios and (max(ratios) - min(ratios)) <= 1e-6:
                factors[u_u] = float(ratios[0])
        if not factors and len(sale_units) == 1:
            factors[next(iter(sale_units))] = 1.0

    return factors


def _extract_target_price(q_u: str) -> float | None:
    """Extract target new unit price cleanly from 'tu X len Y' or 'len gia Y' phrases."""
    m_pair = re.search(
        r"\b(?:tu|from)\s+[0-9][0-9.,]*(?:\s*(?:k|nghin|ngan|tr|trieu|d|dong))?\s+(?:len|xuong|thanh|toi)\s+([0-9][0-9.,]*(?:\s*(?:k|nghin|ngan|tr|trieu|d|dong))?)",
        q_u,
    )
    if m_pair:
        val = clean_vietnamese_currency(m_pair.group(1))
        if val is not None and val > 0:
            return float(val)

    m_single = re.search(
        r"\b(?:len\s+gia|tang\s+gia(?:\s+len)?|giam\s+gia(?:\s+xuong)?|xuong\s+gia|doi\s+gia(?:\s+thanh)?|len|thanh|gia\s+moi)\s+(?:la\s+)?([0-9][0-9.,]*(?:\s*(?:k|nghin|ngan|tr|trieu|d|dong))?)",
        q_u,
    )
    if m_single:
        val = clean_vietnamese_currency(m_single.group(1))
        if val is not None and val > 0:
            return float(val)
    return None


def _resolve_canonical_price_change(
    question: str,
    context: BusinessContext,
    as_of_date: str | None = None,
) -> Tuple[str, Any, str | None] | None:
    """Schema-general price change resolution backed by canonical_order_lines."""
    from zaic_2026_datamate.semantic_contract import (
        BusinessSemanticContract,
        item_norm_key,
    )

    q_telex = normalize_query_telex(question)
    q_lower = q_telex.lower()
    q_u = strip_accents(q_lower)

    has_price_change_event = bool(
        re.search(
            r"\b(?:doi gia|len gia|tang gia|xuong gia|thay doi gia|sau lan tang gia|tu khi\s+.+\s+len\s+\d+|len\s+\d+(?:[.,]\d+)?\s*(?:k|nghin|ngan|d|dong))\b",
            q_u,
        )
    )
    if not has_price_change_event:
        return None
    if any(
        k in q_u for k in ("doanh thu thang nay", "cung ky thang truoc")
    ) or re.search(
        r"\b(?:th(?:ang)?\s*\d{1,2}(?:/\d{2,4})?|t\d{1,2}/\d{2,4})\s+so\s+(?:voi|vs)\s+(?:th|t\d|cung\s+ky)\b",
        q_u,
    ):
        return None

    has_window_signal = any(
        k in q_u
        for k in (
            "ke tu",
            "tu khi",
            "sau khi",
            "sau lan tang",
            "lien truoc",
            "truoc do",
            "ngay truoc",
            "tu bua do",
            "tu hom do",
        )
    ) or bool(re.search(r"\b(?:so\s+\d+\s+ngay|\d+\s+ngay\s+dau)\b", q_u))
    has_pct_compare_cue = bool(
        re.search(
            r"(%|phan\s+tram|nhieu\s+hay\s+it|it\s+hay\s+nhieu|tang\s+hay\s+giam|thay\s+doi\s+bao\s+nhieu)",
            q_u,
        )
    )
    if not (has_window_signal and has_pct_compare_cue):
        return None

    dates = _extract_all_dates(q_u)
    if len(dates) > 1:
        return None
    change_date = dates[0] if len(dates) == 1 else None

    n_m = re.search(r"\b(\d+)\s*ngay\b", q_u)
    n_days = int(n_m.group(1)) if n_m else 30
    if not (1 <= n_days <= 365):
        return None

    contract = BusinessSemanticContract.from_context(context)
    candidates = _load_canonical_item_candidates(context, contract)
    picked = _match_canonical_item(q_telex, candidates)
    if picked is None:
        return None
    matched_key, matched_name = picked
    matched_norm = item_norm_key(matched_name)
    factors = _build_canonical_unit_factors(context, matched_name, matched_key)

    target_price = _extract_target_price(q_u)
    if not change_date:
        change_date = _lookup_price_change_date(
            context,
            target_price=target_price,
            product_code=matched_key or None,
            product_name=matched_name,
        )
        if not change_date and target_price is not None:
            alt_dates: list[str] = []
            for f in factors.values():
                if abs(f - 1.0) <= 1e-6 or f <= 0:
                    continue
                for scaled in (target_price / f, target_price * f):
                    d_alt = _lookup_price_change_date(
                        context,
                        target_price=scaled,
                        product_code=matched_key or None,
                        product_name=matched_name,
                    )
                    if d_alt and d_alt not in alt_dates:
                        alt_dates.append(d_alt)
            if len(alt_dates) == 1:
                change_date = alt_dates[0]
    if not change_date:
        return None

    bounds = _compute_window_bounds(change_date, n_days, as_of_date)
    if not bounds:
        return None
    start_b, end_b, start_a, end_a = bounds

    masked_q_u = re.sub(
        rf"\b{re.escape(alnum_phrase(matched_name))}\b",
        " ",
        alnum_phrase(q_telex),
    )
    asked_unit = _extract_asked_unit(masked_q_u) or _extract_asked_unit(q_lower)
    if asked_unit is not None and factors and asked_unit not in factors:
        return None

    rows = context.duckdb_conn.execute(
        """
        SELECT item_key, item_name, order_date, base_quantity
        FROM canonical_order_lines
        WHERE is_valid_sale = TRUE
          AND order_date IS NOT NULL
          AND order_date >= ?
          AND order_date <= ?
        """,
        [start_b, end_a],
    ).fetchall()

    q_before = 0.0
    q_after = 0.0
    for r_key, r_name, r_date, r_bqty in rows:
        if not (
            str(r_key or "") == matched_key or item_norm_key(r_name) == matched_norm
        ):
            continue
        if r_bqty is None:
            return None
        bqty = float(r_bqty)
        if not math.isfinite(bqty):
            return None
        od = str(r_date)[:10]
        if start_b <= od <= end_b:
            q_before += bqty
        elif start_a <= od <= end_a:
            q_after += bqty

    is_daily_rate = bool(
        re.search(r"\b(?:trung\s+binh|binh\s+quan|tb)\s+(?:moi|1)\s+ngay\b", q_u)
    )
    before_days = (
        datetime.strptime(end_b, "%Y-%m-%d").date()
        - datetime.strptime(start_b, "%Y-%m-%d").date()
    ).days + 1
    after_days = (
        datetime.strptime(end_a, "%Y-%m-%d").date()
        - datetime.strptime(start_a, "%Y-%m-%d").date()
    ).days + 1
    if is_daily_rate and before_days > 0 and after_days > 0:
        return _finalize_pct(q_before / before_days, q_after / after_days)
    return _finalize_pct(q_before, q_after)


def resolve_price_change_query(
    question: str,
    context: BusinessContext,
    as_of_date: str | None = None,
) -> Tuple[str, Any, str | None] | None:
    """Evaluate price change impact percentage on product quantity sold."""
    legacy_res = _resolve_legacy_price_change_query(question, context, as_of_date)
    if legacy_res is not None and legacy_res[0] != "abstain":
        return legacy_res
    return _resolve_canonical_price_change(question, context, as_of_date)


def resolve_unit_price_on_date(
    question: str,
    context: BusinessContext,
    as_of_date: str | None = None,
) -> Tuple[str, Any, str | None] | None:
    """Resolve exact single-date unit selling price from valid canonical_order_lines."""
    from zaic_2026_datamate.semantic_contract import (
        BusinessSemanticContract,
        item_norm_key,
    )

    q_telex = normalize_query_telex(question)
    q_u = strip_accents(q_telex.lower())

    # Reject non-unit-price intents first
    if any(
        bad in q_u
        for bad in (
            "tang gia",
            "doi gia",
            "len gia",
            "xuong gia",
            "thay doi gia",
            "doanh thu",
            "tien hang",
            "tien ban",
            "thanh tien",
            "tong tien",
            "tong thu",
            "loi nhuan",
            "bien loi nhuan",
            "gia von",
            "nhap hang",
            "gia nhap",
            "hoa don nhap",
            "phieu nhap",
            "nha cung cap",
            "nha cc",
            "ncc",
            "trung binh",
            "binh quan",
            "cao nhat",
            "thap nhat",
            "dat nhat",
            "re nhat",
            "lon nhat",
            "nho nhat",
            "nhieu nhat",
            "it nhat",
            "danh sach",
            "liet ke",
            "bao nhieu don",
            "tong cong",
            "chenh lech",
            "chiet khau",
            "ban duoc bao nhieu",
            "ban ra bao nhieu",
            "so luong",
            "phan tram",
        )
    ) or bool(re.search(r"(?:^|[^0-9,])%", q_u)):
        return None
    if re.search(r"\bso\s+\d+\s+ngay\b", q_u) or re.search(r"\btop\s+\d+\b", q_u):
        return None

    has_price_cue = bool(
        re.search(
            r"\b(?:gia\s+ban|don\s+gia|ban\s+(?:voi\s+)?gia|gia\s+(?:la\s+)?(?:bao\s+nhieu|bnhiu|bn|nhiu|nhieu)|voi\s+gia|co\s+gia)\b",
            q_u,
        )
    )
    has_ask_cue = bool(
        re.search(r"\b(?:bao\s+nhieu|bnhiu|bn|nhiu|nhieu|may|la\s+gi)\b", q_u)
    )
    if not (has_price_cue and has_ask_cue):
        return None

    dates = _extract_all_dates(q_u)
    if len(dates) != 1:
        return None
    if re.search(
        r"\b(?:tu\s+ngay|den\s+ngay|trong\s+thang|ca\s+thang|thang\s+\d{1,2}/\d{4})\b",
        q_u,
    ):
        return None
    target_date = dates[0]
    if as_of_date and target_date > as_of_date[:10]:
        return None

    contract = BusinessSemanticContract.from_context(context)
    candidates = _load_canonical_item_candidates(context, contract)
    picked = _match_canonical_item(q_telex, candidates)
    if picked is None:
        return None
    matched_key, matched_name = picked
    matched_norm = item_norm_key(matched_name)
    factors = _build_canonical_unit_factors(context, matched_name, matched_key)
    if not factors:
        return None

    # Strip full dates and mask matched product name before extracting asked unit
    q_nodates = re.sub(r"\b\d{1,2}[/.-]\d{1,2}[/.-]\d{4}\b", " ", q_u)
    item_alnum = alnum_phrase(matched_name)
    q_masked = " ".join(
        re.sub(
            rf"\b{re.escape(item_alnum)}\b",
            " ",
            alnum_phrase(q_nodates),
        ).split()
    )
    q_nodates_norm = " ".join(q_nodates.split())

    unit_vocab = _KNOWN_UNITS | set(factors.keys())
    asked_unit: str | None = None
    explicit_unknown_unit = False

    # Priority 1: word-quantity unit ("mot chai", "moi goi")
    for m in re.finditer(r"\b(?:mot|moi)\s+([a-z0-9]+)\b", q_masked):
        cand = m.group(1).strip()
        if cand in factors:
            asked_unit = cand
            break
        if cand in _KNOWN_UNITS:
            explicit_unknown_unit = True
            break

    # Priority 2: slash unit ("/chai", "theo hop")
    if asked_unit is None and not explicit_unknown_unit:
        for m in re.finditer(r"(?:/|\btheo\s+)([a-z0-9]+)\b", q_nodates_norm):
            cand = m.group(1).strip()
            if cand in unit_vocab:
                if cand in factors:
                    asked_unit = cand
                else:
                    explicit_unknown_unit = True
                break

    # Priority 3: numeric "1 <unit>"
    if asked_unit is None and not explicit_unknown_unit:
        for m in re.finditer(r"\b1\s+([a-z0-9]+)\b", q_masked):
            cand = m.group(1).strip()
            if cand in factors:
                asked_unit = cand
                break
            if cand in _KNOWN_UNITS:
                explicit_unknown_unit = True
                break

    if explicit_unknown_unit:
        return None

    if asked_unit is None:
        if len(factors) == 1:
            factor = next(iter(factors.values()))
        else:
            return None
    else:
        factor = factors[asked_unit]

    rows = context.duckdb_conn.execute(
        """
        SELECT item_key, item_name, line_revenue, base_quantity, quantity
        FROM canonical_order_lines
        WHERE is_valid_sale = TRUE
          AND order_date = ?
        """,
        [target_date],
    ).fetchall()

    bu_prices: list[float] = []
    for r_key, r_name, r_rev, r_bqty, r_qty in rows:
        if not (
            str(r_key or "") == matched_key or item_norm_key(r_name) == matched_norm
        ):
            continue
        if r_rev is None:
            continue
        rev = float(r_rev)
        denom_raw = (
            r_bqty if r_bqty is not None else (r_qty if len(factors) <= 1 else None)
        )
        if denom_raw is None:
            return None
        denom = float(denom_raw)
        if not (math.isfinite(rev) and math.isfinite(denom) and rev > 0 and denom > 0):
            return None
        bu_prices.append(rev / denom)

    if not bu_prices or (max(bu_prices) - min(bu_prices)) > 1.0:
        return None

    avg_bu_price = sum(bu_prices) / len(bu_prices)
    unit_price = avg_bu_price * factor
    if not math.isfinite(unit_price) or unit_price <= 0:
        return None

    rounded_int = round_half_up(unit_price)
    if abs(unit_price - rounded_int) < 0.05:
        return "number", rounded_int, None
    return "number", round(unit_price, 2), None
