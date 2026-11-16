from __future__ import annotations

import re

import pandas as pd

from zaic_2026_datamate.cleaner import normalize_query_telex
from zaic_2026_datamate.data_loader import BusinessContext, parse_mixed_excel_dates
from zaic_2026_datamate.debt_analytics import (
    extract_month_ym,
    month_cutoff_eod,
    strip_accents_norm,
    to_numeric_series,
)
from zaic_2026_datamate.inventory_batch_analytics import (
    ProductMasterSchema,
    extract_product_master,
    find_df_col,
    get_unit_factor,
    inactive_status_mask,
    match_product_row,
    normalize_inventory_text,
)

_DUP_TABLE_RE = re.compile(r"(?:_sheet\d+|_\d+)$")


def _norm_id_series(s: pd.Series) -> pd.Series:
    return s.fillna("").astype(str).str.strip()


def _norm_name_series(s: pd.Series) -> pd.Series:
    return s.fillna("").astype(str).map(strip_accents_norm)


def _filter_product_rows(
    df: pd.DataFrame,
    pid_col: str | None,
    pname_col: str | None,
    pid: str,
    pname_norm: str,
) -> pd.DataFrame:
    """Filter DataFrame rows belonging to matched product by ID first, else normalized name."""
    mask = pd.Series(False, index=df.index)
    pid_s = str(pid).strip()
    if pid_col and pid_s:
        mask = mask | (_norm_id_series(df[pid_col]) == pid_s)
    if not mask.any() and pname_col and pname_norm:
        mask = mask | (_norm_name_series(df[pname_col]) == pname_norm)
    return df.loc[mask].copy()


def _sum_lines_in_base_units(
    df: pd.DataFrame,
    base_qty_col: str | None,
    qty_col: str | None,
    unit_col: str | None,
    master: ProductMasterSchema,
    pid: str,
    pname: str,
) -> float | None:
    """Convert matched rows to base units cleanly; return None if an unknown conversion factor blocks precision."""
    if df.empty:
        return 0.0
    if base_qty_col:
        return float(to_numeric_series(df[base_qty_col]).astype(float).sum())
    if not qty_col:
        return None
    qtys = to_numeric_series(df[qty_col]).astype(float)
    if not unit_col:
        return float(qtys.sum())
    total = 0.0
    for q_val, u_val in zip(qtys, df[unit_col], strict=False):
        if pd.isna(q_val) or q_val == 0.0:
            continue
        u_str = "" if pd.isna(u_val) else str(u_val).strip()
        if not u_str:
            total += float(q_val)
            continue
        factor = get_unit_factor(master, pid, pname, u_str)
        if factor is None:
            return None
        total += float(q_val) * factor
    return total


def _compute_monthly_import_base(
    context: BusinessContext,
    ym: tuple[int, int],
    cutoff_eod: pd.Timestamp,
    master: ProductMasterSchema,
    pid: str,
    pname: str,
) -> tuple[bool, float | None]:
    """Return `(has_import_ledger, monthly_imported_base_qty)`. Returns `(False, None)` if store lacks imports."""
    year, month = ym
    pname_norm = strip_accents_norm(pname)

    # 1. Try Header + Detail import schema
    hdr_cands: list[tuple[int, str, pd.DataFrame, str, str, str | None]] = []
    det_cands: list[tuple[int, str, pd.DataFrame]] = []
    comb_cands: list[tuple[int, str, pd.DataFrame]] = []

    for tname, df in sorted(context.tables.items()):
        tl = tname.lower()
        if df.empty or _DUP_TABLE_RE.search(tl):
            continue
        if "nhap" not in tl:
            continue
        dt_col = find_df_col(df, ["ngay_nhap_kho", "ngay_nhap", "thoi_gian"])
        doc_col = find_df_col(
            df, ["ma_phieu_nhap", "ma_nhap_hang", "ma_don_nhap", "so_phieu"]
        )
        qty_col = find_df_col(
            df,
            [
                "sl_nhap_quy_doi",
                "so_luong_quy_doi",
                "sl_nhap_dvt_co_ban",
                "so_luong",
                "sl_nhap",
                "sl",
            ],
        )
        p_ref = find_df_col(
            df,
            [
                "ma_hang_hoa",
                "ma_hang",
                "ma_sp",
                "id_sp",
                "ma_h",
                "ma_sku",
                "ma_vach",
                "ten_hang",
                "ten_hang_hoa",
                "ten_mat_hang",
                "product",
            ],
        )
        if dt_col and qty_col and p_ref and "chi_tiet" not in tl:
            comb_cands.append((20, tname, df))
        elif doc_col and dt_col and "chi_tiet" not in tl and "lo_hang" not in tl:
            st_col = find_df_col(df, ["tinh_trang", "trang_thai", "trang_thai_phieu"])
            hdr_cands.append((10 if st_col else 5, tname, df, doc_col, dt_col, st_col))
        elif doc_col and qty_col and p_ref:
            det_cands.append((10, tname, df))

    if hdr_cands and det_cands:
        hdr_cands.sort(key=lambda x: (-x[0], x[1]))
        det_cands.sort(key=lambda x: (-x[0], x[1]))
        _, _, hdr_df, h_doc, h_dt, h_st = hdr_cands[0]
        _, _, det_df = det_cands[0]

        h_work = pd.DataFrame(
            {
                "_doc": _norm_id_series(hdr_df[h_doc]),
                "_dt": parse_mixed_excel_dates(hdr_df[h_dt]),
                "_inactive": inactive_status_mask(hdr_df[h_st]) if h_st else False,
            }
        )
        h_work = h_work[
            (~h_work["_inactive"].astype(bool))
            & h_work["_dt"].notna()
            & (h_work["_dt"].dt.year == year)
            & (h_work["_dt"].dt.month == month)
            & (h_work["_dt"] <= cutoff_eod)
        ].drop_duplicates("_doc")

        d_doc = find_df_col(
            det_df, ["ma_phieu_nhap", "ma_nhap_hang", "ma_don_nhap", "so_phieu"]
        )
        d_pid = find_df_col(
            det_df,
            ["ma_hang_hoa", "ma_hang", "ma_sp", "id_sp", "ma_mon", "ma_sku", "ma_vach"],
        )
        d_pname = find_df_col(
            det_df,
            [
                "ten_hang",
                "ten_hang_hoa",
                "ten_san_pham",
                "ten_mat_hang",
                "product",
                "hang_hoa",
            ],
        )
        d_base = find_df_col(
            det_df, ["sl_nhap_quy_doi", "so_luong_quy_doi", "sl_nhap_dvt_co_ban"]
        )
        d_qty = find_df_col(det_df, ["so_luong", "sl_nhap", "sl"])
        d_unit = find_df_col(det_df, ["don_vi_tinh", "dvt", "dvt_nhap", "don_vi"])
        if not d_doc:
            return False, None

        sub_det = _filter_product_rows(det_df, d_pid, d_pname, pid, pname_norm)
        if sub_det.empty or h_work.empty:
            return True, 0.0
        sub_det["_doc"] = _norm_id_series(sub_det[d_doc])
        joined = sub_det.merge(h_work[["_doc"]], on="_doc", how="inner")
        return True, _sum_lines_in_base_units(
            joined, d_base, d_qty, d_unit, master, pid, pname
        )

    # 2. Combined import table (`so_tay_nhap_hang` / `so_nhap_hang`)
    if comb_cands:
        comb_cands.sort(key=lambda x: (-x[0], x[1]))
        _, _, c_df = comb_cands[0]
        c_dt = find_df_col(c_df, ["ngay_nhap_kho", "ngay_nhap", "thoi_gian"])
        c_st = find_df_col(c_df, ["tinh_trang", "trang_thai", "trang_thai_phieu"])
        c_pid = find_df_col(
            c_df, ["ma_hang_hoa", "ma_hang", "ma_sp", "id_sp", "ma_sku", "ma_vach"]
        )
        c_pname = find_df_col(
            c_df,
            [
                "product",
                "ten_hang",
                "ten_hang_hoa",
                "ten_san_pham",
                "ten_mat_hang",
                "hang_hoa",
            ],
        )
        c_base = find_df_col(
            c_df, ["sl_nhap_quy_doi", "so_luong_quy_doi", "sl_nhap_dvt_co_ban"]
        )
        c_qty = find_df_col(c_df, ["sl_nhap", "so_luong", "sl"])
        c_unit = find_df_col(c_df, ["dvt_nhap", "don_vi_tinh", "dvt", "don_vi"])
        if not c_dt:
            return False, None
        sub = _filter_product_rows(c_df, c_pid, c_pname, pid, pname_norm)
        if sub.empty:
            return True, 0.0
        dts = parse_mixed_excel_dates(sub[c_dt])
        mask = (
            dts.notna()
            & (dts.dt.year == year)
            & (dts.dt.month == month)
            & (dts <= cutoff_eod)
        )
        if c_st:
            mask = mask & (~inactive_status_mask(sub[c_st]))
        return True, _sum_lines_in_base_units(
            sub.loc[mask].copy(), c_base, c_qty, c_unit, master, pid, pname
        )

    return False, None


def _compute_monthly_sale_base(
    context: BusinessContext,
    ym: tuple[int, int],
    cutoff_eod: pd.Timestamp,
    master: ProductMasterSchema,
    pid: str,
    pname: str,
) -> float | None:
    """Compute completed monthly sales in base units, excluding cancelled and returned orders."""
    year, month = ym
    pname_norm = strip_accents_norm(pname)

    # 1. Rank true order headers (reject cashbook mirrors like `du_lieu_ban_hang` / `so_quy` lacking status)
    hdr_cands: list[tuple[int, str, pd.DataFrame, str, str, str]] = []
    det_cands: list[tuple[int, str, pd.DataFrame]] = []
    comb_cands: list[tuple[int, str, pd.DataFrame]] = []

    for tname, df in sorted(context.tables.items()):
        tl = tname.lower()
        if df.empty or _DUP_TABLE_RE.search(tl):
            continue
        is_order_doc_name = bool(re.search(r"(?:^|_)(?:hoa_don|don_hang)(?:$|_)", tl))
        if any(
            bad in tl
            for bad in (
                "nhap",
                "so_quy",
                "phieu_thu",
                "thanh_toan",
                "tra_hang",
                "the_kho",
                "lo_hang",
                "chi_tiet_don_lo",
                "chi_tiet_lo",
                "ban_theo_lo",
            )
        ) or (
            not is_order_doc_name
            and bool(
                re.search(
                    r"(?:^|_)(?:ton_kho|san_pham|hang_hoa|khach_hang)(?:$|_)",
                    tl,
                )
            )
        ):
            continue
        oid_col = find_df_col(
            df,
            [
                "ma_hoa_don",
                "order_id",
                "ma_don_hang",
                "ma_giao_dich",
                "ma_phieu",
                "don_so",
                "ma_bill",
                "bill",
                "ma_don",
            ],
        )
        dt_col = find_df_col(
            df,
            [
                "thoi_gian",
                "ngay_ban",
                "ngay_lap",
                "ngay_dat_hang",
                "ngay_hd",
                "ngay_xuat",
                "ngay",
            ],
        )
        st_col = find_df_col(
            df,
            [
                "trang_thai",
                "tinh_trang",
                "trang_thai_don_hang",
                "trang_thai_giao",
                "tinh_trang_giao",
                "tt_don",
                "ghi_chu_tt",
            ],
        )
        pid_col = find_df_col(
            df,
            [
                "ma_hang_hoa",
                "ma_hang",
                "ma_sp",
                "id_sp",
                "ma_h",
                "ma_sku",
                "ma_san_pham_ban",
                "barcode",
                "ma_vach",
            ],
        )
        pname_col = find_df_col(
            df,
            [
                "ten_hang",
                "ten_hang_hoa",
                "ten_san_pham",
                "ten_sp",
                "chi_tiet_hang",
                "hang_hoa",
                "hang_ban",
                "hang",
                "mat_hang",
            ],
        )
        qty_col = find_df_col(
            df,
            [
                "sl_quy_doi",
                "so_luong_quy_doi",
                "sl_co_ban",
                "sl_dv_co_ban",
                "so_luong",
                "sl",
                "slg",
                "so_luong_san_pham",
                "so_luong_hang",
            ],
        )

        is_header_name = bool(
            re.search(r"(?:hoa_don|don_hang|so_ban_hang)(?!.*chi_tiet)", tl)
        )
        if (
            is_header_name
            and oid_col
            and dt_col
            and st_col
            and not (pid_col or pname_col)
        ):
            hdr_cands.append(
                (
                    30 if "hoa_don" in tl or "don_hang" in tl else 15,
                    tname,
                    df,
                    oid_col,
                    dt_col,
                    st_col,
                )
            )
        elif "chi_tiet" in tl and oid_col and (pid_col or pname_col) and qty_col:
            det_cands.append((25 if "hoa_don" in tl or "don" in tl else 10, tname, df))
        elif oid_col and dt_col and st_col and (pid_col or pname_col) and qty_col:
            comb_score = (
                30
                if tl == "ban_hang_tong_hop"
                else (15 if tl.startswith("ban_hang_tong_hop_") else 20)
            )
            comb_cands.append((comb_score, tname, df))

    if hdr_cands and det_cands:
        hdr_cands.sort(key=lambda x: (-x[0], x[1]))
        det_cands.sort(key=lambda x: (-x[0], x[1]))
        _, _, hdr_df, h_oid, h_dt, h_st = hdr_cands[0]
        _, _, det_df = det_cands[0]

        oh = pd.DataFrame(
            {
                "_oid": _norm_id_series(hdr_df[h_oid]),
                "_dt": parse_mixed_excel_dates(hdr_df[h_dt]),
                "_inactive": inactive_status_mask(hdr_df[h_st], include_returns=True)
                if h_st
                else False,
            }
        )
        oh = oh[
            (~oh["_inactive"].astype(bool))
            & oh["_dt"].notna()
            & (oh["_dt"].dt.year == year)
            & (oh["_dt"].dt.month == month)
            & (oh["_dt"] <= cutoff_eod)
        ].drop_duplicates("_oid")

        d_oid = find_df_col(
            det_df, ["ma_hoa_don", "order_id", "ma_don_hang", "ma_giao_dich"]
        )
        d_pid = find_df_col(det_df, ["ma_hang_hoa", "ma_hang", "ma_sp", "id_sp"])
        d_pname = find_df_col(
            det_df,
            ["ten_hang", "ten_hang_hoa", "ten_san_pham", "ten_sp", "chi_tiet_hang"],
        )
        d_base = find_df_col(
            det_df,
            [
                "sl_quy_doi",
                "so_luong_quy_doi",
                "sl_co_ban",
                "so_luong_co_ban",
                "so_luong_dvt_co_ban",
            ],
        )
        d_qty = find_df_col(
            det_df, ["so_luong", "sl", "slg", "so_luong_san_pham", "so_luong_hang"]
        )
        d_unit = find_df_col(det_df, ["don_vi_tinh", "dvt", "d", "donvi"])
        if not d_oid:
            return None

        sub_det = _filter_product_rows(det_df, d_pid, d_pname, pid, pname_norm)
        if sub_det.empty or oh.empty:
            return 0.0
        sub_det["_oid"] = _norm_id_series(sub_det[d_oid])
        joined = sub_det.merge(oh[["_oid"]], on="_oid", how="inner")
        return _sum_lines_in_base_units(
            joined, d_base, d_qty, d_unit, master, pid, pname
        )

    if comb_cands:
        comb_cands.sort(key=lambda x: (-x[0], len(x[1]), x[1]))
        _, _, c_df = comb_cands[0]
        c_dt = find_df_col(
            c_df,
            ["ngay_dat_hang", "ngay_lap", "ngay_hd", "ngay_xuat", "thoi_gian", "ngay"],
        )
        c_st = find_df_col(
            c_df,
            [
                "trang_thai",
                "tinh_trang",
                "trang_thai_don_hang",
                "trang_thai_giao",
                "tinh_trang_giao",
                "tt_don",
                "ghi_chu_tt",
            ],
        )
        c_pid = find_df_col(
            c_df,
            [
                "ma_hang_hoa",
                "ma_hang",
                "ma_sp",
                "id_sp",
                "ma_h",
                "ma_sku",
                "ma_san_pham_ban",
                "barcode",
                "ma_vach",
            ],
        )
        c_pname = find_df_col(
            c_df,
            [
                "ten_hang",
                "ten_hang_hoa",
                "ten_san_pham",
                "ten_sp",
                "hang_hoa",
                "hang_ban",
                "hang",
                "mat_hang",
            ],
        )
        c_base = find_df_col(
            c_df,
            [
                "sl_quy_doi",
                "so_luong_quy_doi",
                "sl_co_ban",
                "sl_dv_co_ban",
                "so_luong_dvt_co_ban",
            ],
        )
        c_qty = find_df_col(
            c_df, ["so_luong", "sl", "slg", "so_luong_san_pham", "so_luong_hang"]
        )
        c_unit = find_df_col(c_df, ["don_vi_tinh", "dvt", "donvi", "dv"])
        if not c_dt:
            return None
        sub = _filter_product_rows(c_df, c_pid, c_pname, pid, pname_norm)
        if sub.empty:
            return 0.0
        dts = parse_mixed_excel_dates(sub[c_dt])
        mask = (
            dts.notna()
            & (dts.dt.year == year)
            & (dts.dt.month == month)
            & (dts <= cutoff_eod)
        )
        if c_st:
            mask = mask & (~inactive_status_mask(sub[c_st], include_returns=True))
        return _sum_lines_in_base_units(
            sub.loc[mask].copy(), c_base, c_qty, c_unit, master, pid, pname
        )

    return None


_NON_UNIT_WORDS = frozenset(
    {
        "tien",
        "phan",
        "thang",
        "nam",
        "ngay",
        "don",
        "phieu",
        "lan",
        "mat",
        "loai",
        "lo",
        "luot",
        "hang",
        "dong",
        "vnd",
        "d",
        "nghin",
        "trieu",
        "luong",
        "nguoi",
        "khach",
        "san",
    }
)

_FILLER_TOKENS = frozenset(
    {
        "nho",
        "xiu",
        "chut",
        "nha",
        "nhe",
        "nhi",
        "ne",
        "ad",
        "shop",
        "quan",
        "tiem",
        "thuoc",
        "mik",
        "minh",
        "mk",
        "em",
        "e",
        "anh",
        "chi",
        "cho",
        "hoi",
        "oi",
        "tinh",
        "giup",
        "dum",
        "xem",
        "check",
        "di",
        "ban",
        "cua",
        "ben",
        "hang",
        "alo",
    }
)

_MONTH_SPAN_RE = re.compile(
    r"\b(?:trong|o|vao|tu|tinh)?\s*(?:thang|thg|th|t)\s*\d{1,2}\s*(?:[/\-]|nam\s*)\s*\d{2,4}\b"
    r"|\b(?:trong|o|vao|tu|tinh)?\s*thang\s+\d{1,2}\b"
    r"|\b(?:trong|o|vao)?\s*\d{1,2}\s*[/\-]\s*\d{4}\b"
    r"|\bnam\s+\d{4}\b"
)

_LEADING_FILLER_RE = re.compile(
    r"^(?:"
    r"nho\s+xiu(?:\s+(?:nha|nhe))?"
    r"|cho\s+(?:em|e|minh|mik|mk)?\s*hoi(?:\s+xiu)?"
    r"|hoi\s+xiu"
    r"|(?:tinh|xem|check|lay)\s+(?:giup|dum|cho)?(?:\s+(?:em|e|minh|mik|mk))?"
    r"|doi\s+voi|ve\s+mat\s+hang|mat\s+hang|san\s+pham|\bsp\b"
    r"|(?:cua\s+)?(?:shop|cua\s+hang|quan|tiem|nha\s+thuoc|ben)(?:\s+(?:minh|mik|mk|em|e))?"
    r"|minh|mik|mk"
    r")\b[\s,:\-]*"
)

_TRAILING_FILLER_RE = re.compile(
    r"[\s,:\-]*(?:trong\s+ky(?:\s+nay)?|luon\s+di(?:\s+e)?|di\s+e|dum(?:\s+(?:em|e))?|giup(?:\s+(?:em|e))?|(?:duoc\s+)?(?:khong|ko|kg)|nha|nhe|nhi|voi|vay|zay|z|ta|a)\s*$"
)


def _clean_product_fragment(raw: str) -> str:
    """Strip month references, store nouns, and polite conversational wrappers from a product fragment."""
    frag = _MONTH_SPAN_RE.sub(" ", raw)
    frag = re.sub(r"\s+", " ", frag).strip(" ,.?:;-")
    m_sep = re.match(r"^([^,:;]{1,40})[,:;]\s*(.+)$", frag)
    if m_sep:
        head_tokens = [tok for tok in m_sep.group(1).strip().split() if tok]
        if head_tokens and all(tok in _FILLER_TOKENS for tok in head_tokens):
            frag = m_sep.group(2).strip()
    while True:
        updated = _LEADING_FILLER_RE.sub("", frag).strip(" ,.?:;-")
        if updated == frag:
            break
        frag = updated
    while True:
        updated = _TRAILING_FILLER_RE.sub("", frag).strip(" ,.?:;-")
        if updated == frag:
            break
        frag = updated
    return frag


def _extract_product_and_unit(q_norm: str) -> tuple[str, str]:
    """Extract product query fragment and requested output unit from an in-minus-out question."""
    target_unit = ""
    unit_matches = list(re.finditer(r"\b(?:bao\s+nhieu|may)\s+([a-z0-9]+)\b", q_norm))
    for m in reversed(unit_matches):
        cand = m.group(1)
        if cand not in _NON_UNIT_WORDS:
            target_unit = cand
            break

    if not target_unit:
        for m in re.finditer(
            r"\b(?:so|tong)\s+([a-z0-9]+)\s+(?:nhap(?:\s+(?:ve|vo|vao|kho))?|ban)\b",
            q_norm,
        ):
            cand = m.group(1)
            if cand not in _NON_UNIT_WORDS:
                target_unit = cand
                break

    op_match = re.search(
        r"\b(?:lay\s+)?(?:(?:so|tong)\s+(?:luong\s+)?(?:[a-z0-9]+\s+)?)?(?:chenh\s+lech\s+)?nhap(?:\s+(?:ve|vo|vao|kho))?\s+tru\b",
        q_norm,
    )
    if op_match:
        post_op = q_norm[op_match.end() :]
        m_cua = re.search(
            r"\bcua\s+(?!(?:shop|quan|cua\s+hang|tiem|nha\s+thuoc)\b)(.+)$",
            post_op,
        )
        if m_cua:
            tail = re.split(
                r"\b(?:la\s+bao\s+nhieu|thi\s+duoc\s+bao\s+nhieu|bao\s+nhieu|chenh\s+bao\s+nhieu)\b",
                m_cua.group(1),
            )[0]
            cleaned = _clean_product_fragment(tail)
            if cleaned:
                return cleaned, target_unit
        prefix = q_norm[: op_match.start()]
        cleaned_prefix = _clean_product_fragment(prefix)
        if cleaned_prefix:
            return cleaned_prefix, target_unit

    frag = q_norm
    m_cua = re.search(
        r"\bcua\s+(?!(?:shop|quan|cua\s+hang|tiem|nha\s+thuoc)\b)(.+)$",
        frag,
    )
    if m_cua:
        frag = m_cua.group(1)
    frag = re.split(
        r"\b(?:trong\s+thang|o\s+thang|thang\s+\d|la\s+bao\s+nhieu|thi\s+duoc\s+bao\s+nhieu|bao\s+nhieu)\b",
        frag,
    )[0]
    return _clean_product_fragment(frag), target_unit


_PURE_IMPORT_EXCLUDE_RE = re.compile(
    r"\b(?:tien|chi\s+phi|gia\s+nhap|gia\s+mua|don\s+gia|thanh\s+tien|von|phieu|hoa\s+don|don\s+nhap|lan\s+nhap|dot\s+nhap|nha\s+cung\s+cap|ncc|mat\s+hang|loai|ton\s+kho|ban\s+ra|lai|loi\s+nhuan|cong\s+no|khach\s+no|tru)\b"
)


def _extract_pure_import_product_and_unit(q_norm: str) -> tuple[str, str] | None:
    """Extract (product_fragment, target_unit) for monthly pure import quantity queries."""
    if _PURE_IMPORT_EXCLUDE_RE.search(q_norm):
        return None
    m = re.search(
        r"\bnhap(?:\s+(?:ve|vo|vao|kho|hang|them))?\s+(?:het\s+)?(?:duoc\s+|tong\s+(?:cong\s+)?)?(?:bao\s+nhieu|may)\s+([a-z0-9]+)\b(.*)$",
        q_norm,
    )
    if not m:
        return None
    target_unit = m.group(1)
    if target_unit in _NON_UNIT_WORDS:
        return None
    tail = re.sub(r"^\s*cua\s+", "", m.group(2))
    prod_frag = _clean_product_fragment(tail)
    if not prod_frag:
        prod_frag = _clean_product_fragment(q_norm[: m.start()])
    if not prod_frag:
        return None
    return prod_frag, target_unit


def resolve_in_minus_out_query(
    question: str,
    context: BusinessContext,
    as_of_date: str | None = None,
) -> tuple[str, object, str]:
    """Resolve monthly import-minus-sale or monthly import product quantity in the requested unit."""
    q_norm = normalize_inventory_text(normalize_query_telex(question))
    q_norm = re.sub(r"\bdc\b", "duoc", q_norm)

    has_diff_intent = bool(
        re.search(
            r"\b(?:so\s+(?:luong\s+)?nhap(?:\s+(?:ve|vo|vao|kho))?\s+tru\s+(?:so\s+(?:luong\s+)?)?ban|chenh\s+lech\s+nhap(?:\s+(?:ve|vo|vao|kho))?\s+tru\s+ban|nhap(?:\s+(?:ve|vo|vao|kho))?\s+tru\s+(?:so\s+)?ban|(?:so|tong)\s+(?:luong\s+)?[a-z0-9]+\s+nhap(?:\s+(?:ve|vo|vao|kho))?\s+tru\s+(?:(?:so|tong)\s+(?:luong\s+)?(?:[a-z0-9]+\s+)?)?ban)\b",
            q_norm,
        )
    )
    pure_import_parsed = (
        None if has_diff_intent else _extract_pure_import_product_and_unit(q_norm)
    )
    if not has_diff_intent and pure_import_parsed is None:
        return "abstain", None, "Not an in-minus-out query"

    # Guard out multi-month spans or financial margin/debt questions
    if re.search(
        r"\b(?:tu\s+thang|den\s+thang|toi\s+thang|loi\s+nhuan|lai\s+gop|lai\s+rong|cong\s+no|khach\s+no)\b",
        q_norm,
    ):
        return (
            "abstain",
            None,
            "Non-monthly or financial query outside in-minus-out scope",
        )

    month_ym = extract_month_ym(q_norm, as_of_date)
    if not month_ym:
        return "abstain", None, "Missing target month in in-minus-out query"

    ym = (int(month_ym[:4]), int(month_ym[5:7]))
    if as_of_date:
        try:
            as_of_dt = pd.Timestamp(as_of_date)
            if ym > (as_of_dt.year, as_of_dt.month):
                return "unanswerable", None, "Requested month is after as_of_date"
        except Exception:
            return "abstain", None, "Invalid as_of_date"

    cutoff_eod = month_cutoff_eod(month_ym, as_of_date)

    master = extract_product_master(context)
    if master is None:
        return "unanswerable", None, "Product master unavailable for in-minus-out query"

    if has_diff_intent:
        prod_frag, target_unit = _extract_product_and_unit(q_norm)
    else:
        assert pure_import_parsed is not None
        prod_frag, target_unit = pure_import_parsed
    if not prod_frag or not target_unit:
        return "abstain", None, "Product or target unit not parsed cleanly"

    prod_row = match_product_row(prod_frag, master.products_df, master.name_col)
    if prod_row is None:
        return "abstain", None, "Ambiguous or unmatched product for in-minus-out query"

    pid = str(prod_row[master.id_col]).strip() if master.id_col else ""
    pname = str(prod_row[master.name_col]).strip()
    target_factor = get_unit_factor(master, pid, pname, target_unit)
    if not target_factor or target_factor <= 0:
        return "abstain", None, "Target unit conversion factor unknown"

    has_imports, import_base = _compute_monthly_import_base(
        context, ym, cutoff_eod, master, pid, pname
    )
    if not has_imports:
        return "unanswerable", None, "Import ledger tables unavailable"
    if import_base is None:
        return "abstain", None, "Import unit conversion unresolved"

    if not has_diff_intent:
        ans = import_base / target_factor
        rounded = round(float(ans), 2)
        val: int | float = (
            int(round(rounded)) if abs(rounded - round(rounded)) < 1e-6 else rounded
        )
        return "number", val, "Computed monthly import product quantity"

    sale_base = _compute_monthly_sale_base(context, ym, cutoff_eod, master, pid, pname)
    if sale_base is None:
        return "abstain", None, "Sales ledger or unit conversion unresolved"

    ans = (import_base - sale_base) / target_factor
    rounded = round(float(ans), 2)
    val = int(round(rounded)) if abs(rounded - round(rounded)) < 1e-6 else rounded
    return "number", val, "Computed monthly import minus sale difference"
