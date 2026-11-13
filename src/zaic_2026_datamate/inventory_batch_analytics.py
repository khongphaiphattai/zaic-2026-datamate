from __future__ import annotations

import re
from dataclasses import dataclass

import pandas as pd
from rapidfuzz import fuzz

from zaic_2026_datamate.data_loader import BusinessContext, parse_mixed_excel_dates
from zaic_2026_datamate.debt_analytics import (
    find_col,
    normalize_debt_text,
    strip_accents_norm,
    to_numeric_series,
)

_DUP_TABLE_RE = re.compile(r"(?:_sheet\d+|_\d+)$")
_CANCELLED_ORDER_RE = re.compile(r"huy|cancel|nhap\b|draft", re.IGNORECASE)
_INACTIVE_IMPORT_RE = re.compile(r"huy|cancel|nhap\b|draft", re.IGNORECASE)


def find_df_col(
    df: pd.DataFrame, candidates: list[str] | tuple[str, ...]
) -> str | None:
    """Resolve the first matching column name on a DataFrame via `debt_analytics.find_col`."""
    cols_map = {str(c).lower(): str(c) for c in df.columns}
    return find_col(cols_map, tuple(candidates))


def normalize_inventory_text(text: str) -> str:
    """Normalize Vietnamese text and common Telex/repeated-character typos for inventory queries."""
    base = normalize_debt_text(text)
    base = re.sub(r"\bheets?\b", "het", base)
    base = re.sub(r"\bnhieeu\b", "nhieu", base)
    base = re.sub(r"\bbnhiu\b", "bao nhieu", base)
    base = re.sub(r"\bdem\s+het\b", "den het", base)
    return base


def _clean_match_str(text: str) -> str:
    """Normalize string to lowercase accent-stripped alphanumeric tokens for product matching."""
    norm = strip_accents_norm(str(text))
    norm = re.sub(r"(\d),(\d)", r"\1.\2", norm)
    norm = re.sub(r"[^a-z0-9.%]+", " ", norm)
    return re.sub(r"\s+", " ", norm).strip()


def _format_num(val: float) -> int | float:
    rounded = round(float(val), 2)
    if abs(rounded - round(rounded)) < 1e-6:
        return int(round(rounded))
    return rounded


def parse_inventory_cutoff_date(
    q_norm: str, as_of_date: str | None
) -> tuple[pd.Timestamp | None, pd.Timestamp | None]:
    """Parse target cutoff date and its end-of-day timestamp from normalized query text."""
    # Prefer an explicit date paired with "cuối ngày" / "hết ngày" / "tồn kho ngày" / "tính đến hết ngày"
    patterns = [
        r"(?:cuoi\s+ngay|het\s+ngay|ton\s+kho\s+ngay|den\s+het\s+ngay|vao\s+ngay)\s+(\d{1,2})[/-](\d{1,2})[/-](\d{4})",
        r"(?:cuoi\s+ngay|het\s+ngay|ton\s+kho\s+ngay|den\s+het\s+ngay|vao\s+ngay)\s+(\d{4})[/-](\d{1,2})[/-](\d{1,2})",
    ]
    for pat in patterns:
        m = re.search(pat, q_norm)
        if m:
            nums = [int(g) for g in m.groups()]
            try:
                if len(m.group(1)) == 4:
                    dt = pd.Timestamp(year=nums[0], month=nums[1], day=nums[2])
                else:
                    dt = pd.Timestamp(year=nums[2], month=nums[1], day=nums[0])
                return dt, dt + pd.Timedelta(hours=23, minutes=59, seconds=59)
            except ValueError:
                return None, None

    # Generic single DD/MM/YYYY date when not inside a "tu D1 den D2" expiry span
    if not re.search(r"\btu\s+(?:ngay\s+)?\d{1,2}[/-]\d{1,2}[/-]\d{4}", q_norm):
        m_any = re.search(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{4})\b", q_norm)
        if m_any:
            d, mo, y = int(m_any.group(1)), int(m_any.group(2)), int(m_any.group(3))
            try:
                dt = pd.Timestamp(year=y, month=mo, day=d)
                return dt, dt + pd.Timedelta(hours=23, minutes=59, seconds=59)
            except ValueError:
                return None, None

    if as_of_date:
        try:
            dt = pd.Timestamp(as_of_date).normalize()
            return dt, dt + pd.Timedelta(hours=23, minutes=59, seconds=59)
        except Exception:
            return None, None
    return None, None


def parse_hsd_date_range(
    q_norm: str, as_of_date: str | None
) -> tuple[pd.Timestamp, pd.Timestamp] | None:
    """Parse expiry date range [D1, D2] from explicit range or 'từ nay đến hết D2' / 'N ngày tới'."""
    m = re.search(
        r"\btu\s+(?:ngay\s+)?(\d{1,2})[/-](\d{1,2})[/-](\d{4})\s+(?:den|toi)\s+(?:het\s+)?(?:ngay\s+)?(\d{1,2})[/-](\d{1,2})[/-](\d{4})\b",
        q_norm,
    )
    if m:
        try:
            d1 = pd.Timestamp(
                year=int(m.group(3)), month=int(m.group(2)), day=int(m.group(1))
            )
            d2 = pd.Timestamp(
                year=int(m.group(6)), month=int(m.group(5)), day=int(m.group(4))
            )
            return (d1, d2) if d1 <= d2 else None
        except ValueError:
            return None

    m_now = re.search(
        r"\btu\s+nay\s+(?:den|toi)\s+(?:het\s+)?(?:ngay\s+)?(\d{1,2})[/-](\d{1,2})[/-](\d{4})\b",
        q_norm,
    )
    if m_now and as_of_date:
        try:
            d1 = pd.Timestamp(as_of_date).normalize()
            d2 = pd.Timestamp(
                year=int(m_now.group(3)),
                month=int(m_now.group(2)),
                day=int(m_now.group(1)),
            )
            return (d1, d2) if d1 <= d2 else None
        except ValueError:
            return None

    m_days = re.search(r"\btrong\s+(\d+)\s+ngay\s+toi\b", q_norm)
    if m_days and as_of_date:
        try:
            d1 = pd.Timestamp(as_of_date).normalize()
            d2 = d1 + pd.Timedelta(days=int(m_days.group(1)))
            return d1, d2
        except Exception:
            return None
    return None


@dataclass(slots=True)
class ProductMasterSchema:
    table_name: str
    products_df: pd.DataFrame
    id_col: str | None
    name_col: str
    base_unit_col: str | None
    cost_col: str | None
    unit_df: pd.DataFrame | None = None
    unit_pid_col: str | None = None
    unit_pname_col: str | None = None
    unit_name_col: str | None = None
    unit_factor_col: str | None = None
    unit_base_col: str | None = None


def extract_product_master(context: BusinessContext) -> ProductMasterSchema | None:
    """Detect canonical product master table and unit-conversion table generically from column structure."""
    candidates: list[
        tuple[int, str, pd.DataFrame, str | None, str, str | None, str | None]
    ] = []
    for tname, df in sorted(context.tables.items()):
        tl = tname.lower()
        if df.empty or _DUP_TABLE_RE.search(tl):
            continue
        if any(
            bad in tl
            for bad in (
                "hoa_don",
                "don_hang",
                "chi_tiet",
                "so_quy",
                "phieu_",
                "nhap_hang",
                "ton_kho",
                "the_kho",
                "lo_hang",
                "don_vi",
                "quy_doi",
                "bang_gia",
                "lich_su_gia",
                "khach_hang",
                "nha_cung_cap",
                "nhan_vien",
                "calendar",
                "chi_phi",
                "thuoc",
                "cong_no",
            )
        ):
            continue
        id_col = find_df_col(
            df, ["ma_hang_hoa", "ma_hang", "ma_sp", "id_sp", "ma_san_pham", "ma_hh"]
        )
        name_col = find_df_col(
            df,
            [
                "ten_hang_hoa",
                "ten_hang",
                "ten_goi",
                "ten_san_pham",
                "ten_sp",
                "mon",
                "hang_hoa",
                "hang",
                "product",
            ],
        )
        cost_col = find_df_col(
            df,
            ["gia_von_hien_tai", "gia_von_hang", "gia_von", "gia_von_co_ban"],
        )
        unit_col = find_df_col(df, ["don_vi_co_so", "don_vi_tinh", "dvt", "don_vi"])
        price_col = find_df_col(df, ["gia_ban_le", "gia_ban", "gia"])
        if not name_col:
            continue
        if not (cost_col or unit_col or price_col):
            continue
        score = 0
        if id_col:
            score += 30
        if cost_col:
            score += 40
        if unit_col:
            score += 15
        if any(k in tl for k in ("san_pham", "hang_hoa", "hang_h_a", "danh_muc")):
            score += 20
        candidates.append((score, tname, df, id_col, name_col, unit_col, cost_col))

    unit_df: pd.DataFrame | None = None
    unit_pid_col = unit_pname_col = unit_name_col = unit_factor_col = unit_base_col = (
        None
    )
    for tname, df in sorted(context.tables.items()):
        tl = tname.lower()
        if df.empty or _DUP_TABLE_RE.search(tl):
            continue
        factor_col = find_df_col(
            df, ["he_so_quy_doi", "quy_doi", "ti_le_quy_doi", "he_so_qd", "bang"]
        )
        uname_col = find_df_col(
            df, ["don_vi_tinh", "dvt", "don_vi_quy_doi", "don_vi", "dv_lon"]
        )
        pid_col = find_df_col(df, ["ma_hang_hoa", "ma_hang", "ma_sp", "id_sp"])
        pname_col = find_df_col(df, ["ten_san_pham", "ten_hang", "hang", "product"])
        if factor_col and uname_col and (pid_col or pname_col):
            unit_df = df.copy()
            unit_pid_col = pid_col
            unit_pname_col = pname_col
            unit_name_col = uname_col
            unit_factor_col = factor_col
            unit_base_col = find_df_col(df, ["don_vi_co_ban", "dv_nho", "dvt_co_ban"])
            break

    if not candidates:
        return None
    candidates.sort(key=lambda x: (-x[0], x[1]))
    _, tname, df, id_col, name_col, unit_col, cost_col = candidates[0]
    return ProductMasterSchema(
        table_name=tname,
        products_df=df.copy(),
        id_col=id_col,
        name_col=name_col,
        base_unit_col=unit_col,
        cost_col=cost_col,
        unit_df=unit_df,
        unit_pid_col=unit_pid_col,
        unit_pname_col=unit_pname_col,
        unit_name_col=unit_name_col,
        unit_factor_col=unit_factor_col,
        unit_base_col=unit_base_col,
    )


def match_product_row(
    query_fragment: str,
    df: pd.DataFrame,
    name_col: str,
) -> pd.Series | None:
    """Match a single product row deterministically using exact accentless normalization then guarded fuzzy scoring."""
    target = _clean_match_str(query_fragment)
    if not target or df.empty or name_col not in df.columns:
        return None

    clean_names = df[name_col].fillna("").astype(str).map(_clean_match_str)
    exact_mask = clean_names == target
    if exact_mask.sum() == 1:
        return df.loc[exact_mask].iloc[0]

    # Prefer whole-name containment when unique
    contains_mask = clean_names.map(
        lambda cand: (
            bool(cand)
            and (
                target == cand
                or (len(target) >= 6 and (target in cand or cand in target))
            )
        )
    )
    if contains_mask.sum() == 1:
        return df.loc[contains_mask].iloc[0]

    target_tokens = set(target.split())
    scored: list[tuple[float, int, pd.Series]] = []
    for idx, cand in clean_names.items():
        if not cand:
            continue
        cand_tokens = set(cand.split())
        if target_tokens and target_tokens.issubset(cand_tokens):
            score = 95.0 - max(0, len(cand_tokens) - len(target_tokens)) * 1.5
        else:
            score = (
                float(fuzz.token_set_ratio(target, cand)) * 0.7
                + float(fuzz.ratio(target, cand)) * 0.3
            )
        scored.append((score, -len(cand), df.loc[idx]))

    if not scored:
        return None
    scored.sort(key=lambda x: (-x[0], -x[1]))
    best_score, _, best_row = scored[0]
    if best_score < 78.0:
        return None
    if len(scored) > 1 and (best_score - scored[1][0]) < 4.0:
        return None
    return best_row


def get_unit_factor(
    master: ProductMasterSchema | None,
    product_id: str | None,
    product_name: str | None,
    unit_name: str,
) -> float | None:
    """Return multiplier converting `unit_name` into base product unit (e.g. 'hộp' -> 24, 'gói' -> 1)."""
    u_norm = strip_accents_norm(unit_name)
    if not u_norm or master is None:
        return None

    # Check if matches base unit directly
    prod_row: pd.Series | None = None
    if product_id and master.id_col and master.id_col in master.products_df.columns:
        m = master.products_df[master.id_col].astype(str) == str(product_id)
        if m.any():
            prod_row = master.products_df.loc[m].iloc[0]
    elif product_name:
        prod_row = match_product_row(product_name, master.products_df, master.name_col)

    if prod_row is not None and master.base_unit_col:
        base_u = strip_accents_norm(str(prod_row[master.base_unit_col]))
        if base_u == u_norm:
            return 1.0

    if master.unit_df is not None and master.unit_name_col and master.unit_factor_col:
        sub = master.unit_df
        matched = pd.DataFrame()
        if product_id and master.unit_pid_col and master.unit_pid_col in sub.columns:
            matched = sub[sub[master.unit_pid_col].astype(str) == str(product_id)]
        elif (
            product_name
            and master.unit_pname_col
            and master.unit_pname_col in sub.columns
        ):
            p_clean = _clean_match_str(product_name)
            matched = sub[
                sub[master.unit_pname_col].fillna("").astype(str).map(_clean_match_str)
                == p_clean
            ]
        if not matched.empty:
            for _, row in matched.iterrows():
                if strip_accents_norm(str(row[master.unit_name_col])) == u_norm:
                    val = float(
                        to_numeric_series(
                            pd.Series([row[master.unit_factor_col]])
                        ).iloc[0]
                    )
                    if pd.notna(val) and val > 0:
                        return val
                if (
                    master.unit_base_col
                    and strip_accents_norm(str(row[master.unit_base_col])) == u_norm
                ):
                    return 1.0
    return None


def _max_transaction_date(context: BusinessContext) -> pd.Timestamp | None:
    max_dt: pd.Timestamp | None = None
    for tname, df in context.tables.items():
        tl = tname.lower()
        if df.empty or _DUP_TABLE_RE.search(tl):
            continue
        if not any(
            k in tl
            for k in (
                "hoa_don",
                "don_hang",
                "ban_hang",
                "nhap_hang",
                "phieu_nhap",
                "the_kho",
            )
        ):
            continue
        dt_col = find_df_col(
            df,
            [
                "thoi_gian",
                "ngay_ban",
                "ngay_lap",
                "ngay_dat_hang",
                "ngay_nhap_kho",
                "ngay_nhap",
                "ngay_gio_phat_sinh",
                "ngay",
            ],
        )
        if not dt_col:
            continue
        parsed = parse_mixed_excel_dates(df[dt_col]).dropna()
        if parsed.empty:
            continue
        cand = parsed.max().normalize()
        if max_dt is None or cand > max_dt:
            max_dt = cand
    return max_dt


def _extract_stock_df_at_cutoff(
    context: BusinessContext,
    cutoff_dt: pd.Timestamp,
    cutoff_eod: pd.Timestamp,
    as_of_date: str | None,
) -> pd.DataFrame | None:
    """Return DataFrame with columns ['_pid', '_pname', '_unit', '_qty'] at cutoff date, or None if unavailable/stale."""
    # 1. Prefer transaction-level stock card (`the_kho`) for exact point-in-time balance
    for tname, df in sorted(context.tables.items()):
        tl = tname.lower()
        if df.empty or _DUP_TABLE_RE.search(tl):
            continue
        qty_col = find_df_col(df, ["ton_cuoi"])
        dt_col = find_df_col(df, ["ngay_gio_phat_sinh", "ngay_phat_sinh", "thoi_gian"])
        pid_col = find_df_col(df, ["ma_hang_hoa", "ma_hang", "ma_sp", "id_sp"])
        if qty_col and dt_col and pid_col and "the_kho" in tl:
            work = df.copy()
            work["_dt"] = parse_mixed_excel_dates(work[dt_col])
            work = work[work["_dt"].notna() & (work["_dt"] <= cutoff_eod)].copy()
            if work.empty:
                return None
            tx_col = find_df_col(work, ["ma_giao_dich_kho", "ma_giao_dich"])
            work["_row_ord"] = range(len(work))
            sort_cols = ["_dt", tx_col, "_row_ord"] if tx_col else ["_dt", "_row_ord"]
            last = (
                work.sort_values(sort_cols, kind="mergesort")
                .groupby(work[pid_col].astype(str), as_index=False, sort=False)
                .tail(1)
            )
            return pd.DataFrame(
                {
                    "_pid": last[pid_col].astype(str),
                    "_pname": pd.NA,
                    "_unit": pd.NA,
                    "_qty": to_numeric_series(last[qty_col]).astype(float),
                }
            ).reset_index(drop=True)

    # 2. Fallback to snapshot table (`bao_cao_ton_kho` / `ton_kho`) when cutoff aligns with dataset snapshot
    snap_candidates: list[
        tuple[int, str, pd.DataFrame, str | None, str | None, str | None, str]
    ] = []
    for tname, df in sorted(context.tables.items()):
        tl = tname.lower()
        if df.empty or _DUP_TABLE_RE.search(tl):
            continue
        if any(
            bad in tl
            for bad in ("the_kho", "lo_hang", "nhap", "ban_hang_hoa_don", "chi_tiet")
        ):
            continue
        qty_col = find_df_col(
            df, ["ton_cuoi_ky", "ton_kho", "ton_thuc", "so_luong_ton"]
        )
        pid_col = find_df_col(df, ["ma_hang_hoa", "ma_hang", "ma_sp", "id_sp"])
        pname_col = find_df_col(
            df, ["ten_hang", "ten_hang_hoa", "hang_hoa", "product", "ten_san_pham"]
        )
        unit_col = find_df_col(df, ["dvt", "don_vi", "don_vi_tinh"])
        if qty_col and (pid_col or pname_col):
            score = 20 if "ton_kho" in tl else 10
            snap_candidates.append(
                (score, tname, df, pid_col, pname_col, unit_col, qty_col)
            )

    if not snap_candidates:
        return None
    snap_candidates.sort(key=lambda x: (-x[0], x[1]))
    _, _, df, pid_col, pname_col, unit_col, qty_col = snap_candidates[0]

    max_tx_dt = _max_transaction_date(context)
    if max_tx_dt is not None and cutoff_dt < (max_tx_dt - pd.Timedelta(days=1)):
        # Historical cutoff strictly before snapshot end without transaction stock card -> unsafe
        return None

    return pd.DataFrame(
        {
            "_pid": df[pid_col].astype(str) if pid_col else pd.NA,
            "_pname": df[pname_col].astype(str) if pname_col else pd.NA,
            "_unit": df[unit_col].astype(str) if unit_col else pd.NA,
            "_qty": to_numeric_series(df[qty_col]).astype(float),
        }
    ).reset_index(drop=True)


def _find_import_tables(
    context: BusinessContext,
) -> tuple[pd.DataFrame | None, pd.DataFrame | None]:
    """Find (import_header_df, import_detail_df) supporting batch receipt dates and lot unit costs."""
    hdr_df: pd.DataFrame | None = None
    det_df: pd.DataFrame | None = None
    for tname, df in sorted(context.tables.items()):
        tl = tname.lower()
        if df.empty or _DUP_TABLE_RE.search(tl):
            continue
        if (
            any(k in tl for k in ("phieu_nhap", "danh_sach_nhap_hang", "nhap_hang"))
            and "chi_tiet" not in tl
        ):
            if find_df_col(
                df, ["ma_phieu_nhap", "ma_nhap_hang", "so_phieu"]
            ) and find_df_col(df, ["ngay_nhap_kho", "ngay_nhap", "thoi_gian"]):
                if "so_tay" not in tl or hdr_df is None:
                    hdr_df = df
        if any(k in tl for k in ("chi_tiet_nhap", "so_tay_nhap_hang")) and find_df_col(
            df, ["ma_phieu_nhap", "ma_nhap_hang", "so_phieu"]
        ):
            det_df = df
    return hdr_df, det_df


def _extract_active_lots(
    context: BusinessContext,
    cutoff_eod: pd.Timestamp,
    master: ProductMasterSchema | None,
) -> tuple[bool, pd.DataFrame | None]:
    """Return `(has_batch_schema, active_lots_df)`.

    `active_lots_df` contains: `_lot_id`, `_pid`, `_pname`, `_hsd_dt`, `_rem_qty`, `_lot_unit_cost`.
    """
    lot_candidates: list[tuple[int, str, pd.DataFrame]] = []
    for tname, df in sorted(context.tables.items()):
        tl = tname.lower()
        if df.empty or _DUP_TABLE_RE.search(tl):
            continue
        lot_col = find_df_col(df, ["ma_lo", "lo"])
        hsd_col = find_df_col(df, ["han_su_dung", "hsd"])
        qty_col = find_df_col(
            df,
            ["sl_nhap_quy_doi", "so_luong_dvt_co_ban", "sl_nhap_dvt_co_ban"],
        )
        if lot_col and hsd_col and qty_col:
            score = 30 if "lo_hang" in tl else 10
            lot_candidates.append((score, tname, df))

    if not lot_candidates:
        return False, None

    lot_candidates.sort(key=lambda x: (-x[0], x[1]))
    _, _, lot_raw = lot_candidates[0]
    lot_col = find_df_col(lot_raw, ["ma_lo", "lo"])
    hsd_col = find_df_col(lot_raw, ["han_su_dung", "hsd"])
    in_qty_col = find_df_col(
        lot_raw, ["sl_nhap_quy_doi", "so_luong_dvt_co_ban", "sl_nhap_dvt_co_ban"]
    )
    pid_col = find_df_col(
        lot_raw, ["ma_hang_hoa", "ma_hang", "ma_mon", "ma_sp", "id_sp"]
    )
    doc_col = find_df_col(lot_raw, ["ma_phieu_nhap", "ma_nhap_hang", "so_phieu"])
    cost_col = find_df_col(lot_raw, ["don_gia_nhap"])
    if not (lot_col and hsd_col and in_qty_col):
        return False, None

    lots = pd.DataFrame(
        {
            "_lot_id": lot_raw[lot_col].astype(str),
            "_pid": lot_raw[pid_col].astype(str) if pid_col else pd.NA,
            "_doc_id": lot_raw[doc_col].astype(str) if doc_col else pd.NA,
            "_hsd_dt": parse_mixed_excel_dates(lot_raw[hsd_col]).dt.normalize(),
            "_in_qty": to_numeric_series(lot_raw[in_qty_col]).astype(float),
            "_lot_unit_cost": (
                to_numeric_series(lot_raw[cost_col]).astype(float)
                if cost_col
                else pd.NA
            ),
        }
    )

    # Filter import date <= cutoff_eod & enrich lot cost if stored in import detail table
    imp_hdr, imp_det = _find_import_tables(context)
    if imp_hdr is not None:
        h_doc = find_df_col(imp_hdr, ["ma_phieu_nhap", "ma_nhap_hang", "so_phieu"])
        h_dt = find_df_col(imp_hdr, ["ngay_nhap_kho", "ngay_nhap", "thoi_gian"])
        h_st = find_df_col(imp_hdr, ["tinh_trang", "trang_thai"])
        if h_doc and h_dt:
            h_df = pd.DataFrame(
                {
                    "_doc_id": imp_hdr[h_doc].astype(str),
                    "_imp_dt": parse_mixed_excel_dates(imp_hdr[h_dt]),
                    "_imp_st": imp_hdr[h_st].fillna("").astype(str) if h_st else "",
                }
            )
            h_df = h_df[~h_df["_imp_st"].str.contains(_INACTIVE_IMPORT_RE, na=False)]
            h_df = h_df.drop_duplicates("_doc_id")
            lots = lots.merge(h_df[["_doc_id", "_imp_dt"]], on="_doc_id", how="inner")
            lots = lots[lots["_imp_dt"].isna() | (lots["_imp_dt"] <= cutoff_eod)].copy()

    if imp_det is not None and (lots["_lot_unit_cost"].isna().all() or imp_hdr is None):
        d_doc = find_df_col(imp_det, ["ma_phieu_nhap", "ma_nhap_hang", "so_phieu"])
        d_lot = find_df_col(imp_det, ["ma_lo", "lo"])
        d_dt = find_df_col(imp_det, ["ngay_nhap", "ngay_nhap_kho"])
        d_total = find_df_col(imp_det, ["thanh_tien"])
        d_qty = find_df_col(imp_det, ["so_luong", "sl_nhap", "sl"])
        d_price = find_df_col(imp_det, ["don_gia", "don_gia_nhap", "gia_nhap"])
        if d_lot and d_doc:
            det = pd.DataFrame(
                {
                    "_doc_id": imp_det[d_doc].astype(str),
                    "_lot_id": imp_det[d_lot].astype(str),
                }
            )
            if d_dt:
                det["_imp_dt"] = parse_mixed_excel_dates(imp_det[d_dt])
            if d_total:
                det["_line_total"] = to_numeric_series(imp_det[d_total]).astype(float)
            elif d_qty and d_price:
                det["_line_total"] = to_numeric_series(imp_det[d_qty]).astype(
                    float
                ) * to_numeric_series(imp_det[d_price]).astype(float)
            det = det.drop_duplicates(["_doc_id", "_lot_id"])
            lots = lots.merge(det, on=["_doc_id", "_lot_id"], how="left")
            if "_imp_dt" in lots.columns and imp_hdr is None:
                lots = lots[
                    lots["_imp_dt"].isna() | (lots["_imp_dt"] <= cutoff_eod)
                ].copy()
            if "_line_total" in lots.columns and lots["_lot_unit_cost"].isna().all():
                lots["_lot_unit_cost"] = lots["_line_total"] / lots["_in_qty"].where(
                    lots["_in_qty"] > 0
                )

    # Aggregate lot imports cleanly
    lots_agg = (
        lots.groupby("_lot_id", as_index=False, sort=False)
        .agg(
            _pid=("_pid", "first"),
            _hsd_dt=("_hsd_dt", "min"),
            _in_qty=("_in_qty", "sum"),
            _lot_unit_cost=("_lot_unit_cost", "first"),
        )
        .copy()
    )

    # Find lot sales table + join order header for cutoff and active statuses
    sale_candidates: list[tuple[int, str, pd.DataFrame]] = []
    for tname, df in sorted(context.tables.items()):
        tl = tname.lower()
        if df.empty or _DUP_TABLE_RE.search(tl):
            continue
        lot_c = find_df_col(df, ["ma_lo", "lo"])
        ord_c = find_df_col(df, ["ma_hoa_don", "order_id", "ma_don_hang"])
        sold_c = find_df_col(
            df, ["sl_quy_doi", "sl_quy_ra_le", "so_luong_dvt_co_ban", "sl_co_ban"]
        )
        if lot_c and ord_c and sold_c and "nhap" not in tl:
            score = 30 if "lo" in tl else 10
            sale_candidates.append((score, tname, df))

    sold_agg = pd.DataFrame(columns=["_lot_id", "_sold_qty", "_ret_qty"])
    if sale_candidates:
        sale_candidates.sort(key=lambda x: (-x[0], x[1]))
        _, _, sale_raw = sale_candidates[0]
        s_lot = find_df_col(sale_raw, ["ma_lo", "lo"])
        s_ord = find_df_col(sale_raw, ["ma_hoa_don", "order_id", "ma_don_hang"])
        s_qty = find_df_col(
            sale_raw, ["sl_quy_doi", "sl_quy_ra_le", "so_luong_dvt_co_ban", "sl_co_ban"]
        )
        r_qty = find_df_col(
            sale_raw,
            ["sl_tra_quy_doi", "sl_tra_dvt_co_ban", "so_luong_tra_dvt_co_ban"],
        )
        if s_lot and s_ord and s_qty:
            sales = pd.DataFrame(
                {
                    "_lot_id": sale_raw[s_lot].astype(str),
                    "_ord_id": sale_raw[s_ord].astype(str),
                    "_sold": to_numeric_series(sale_raw[s_qty])
                    .astype(float)
                    .fillna(0.0),
                    "_ret": (
                        to_numeric_series(sale_raw[r_qty]).astype(float).fillna(0.0)
                        if r_qty
                        else 0.0
                    ),
                }
            )
            # Find order header table to apply date <= cutoff_eod & exclude cancelled orders
            hdr_cands: list[tuple[int, str, pd.DataFrame, str, str, str | None]] = []
            for tname, df in sorted(context.tables.items()):
                tl = tname.lower()
                if (
                    df.empty
                    or _DUP_TABLE_RE.search(tl)
                    or "chi_tiet" in tl
                    or "so_quy" in tl
                ):
                    continue
                if any(k in tl for k in ("hoa_don", "don_hang", "ban_hang")):
                    oid = find_df_col(df, ["ma_hoa_don", "order_id", "ma_don_hang"])
                    odt = find_df_col(
                        df, ["thoi_gian", "ngay_ban", "ngay_lap", "ngay_dat_hang"]
                    )
                    ost = find_df_col(df, ["trang_thai", "tinh_trang"])
                    if oid and odt and ost:
                        hdr_cands.append((10, tname, df, oid, odt, ost))
                    elif oid and odt:
                        hdr_cands.append((5, tname, df, oid, odt, None))
            if hdr_cands:
                hdr_cands.sort(key=lambda x: (-x[0], x[1]))
                _, _, oh_df, oid, odt, ost = hdr_cands[0]
                oh = pd.DataFrame(
                    {
                        "_ord_id": oh_df[oid].astype(str),
                        "_ord_dt": parse_mixed_excel_dates(oh_df[odt]),
                        "_ord_st": oh_df[ost].fillna("").astype(str) if ost else "",
                    }
                )
                oh = oh[
                    (~oh["_ord_st"].str.contains(_CANCELLED_ORDER_RE, na=False))
                    & oh["_ord_dt"].notna()
                    & (oh["_ord_dt"] <= cutoff_eod)
                ].drop_duplicates("_ord_id")
                sales = sales.merge(oh[["_ord_id"]], on="_ord_id", how="inner")

            sold_agg = sales.groupby("_lot_id", as_index=False, sort=False).agg(
                _sold_qty=("_sold", "sum"), _ret_qty=("_ret", "sum")
            )

    merged = lots_agg.merge(sold_agg, on="_lot_id", how="left")
    merged["_sold_qty"] = merged["_sold_qty"].fillna(0.0)
    merged["_ret_qty"] = merged["_ret_qty"].fillna(0.0)
    merged["_rem_qty"] = merged["_in_qty"] - (merged["_sold_qty"] - merged["_ret_qty"])
    merged = merged[(merged["_rem_qty"] > 1e-9) & merged["_hsd_dt"].notna()].copy()

    # Map canonical product names from master when available
    if master is not None and master.id_col:
        pmap = master.products_df[[master.id_col, master.name_col]].copy()
        pmap.columns = ["_pid", "_pname"]
        pmap["_pid"] = pmap["_pid"].astype(str)
        pmap = pmap.drop_duplicates("_pid")
        merged["_pid"] = merged["_pid"].astype(str)
        merged = merged.merge(pmap, on="_pid", how="left")
    else:
        merged["_pname"] = merged["_pid"].astype(str)

    return True, merged


def _resolve_box_even_odd(
    q_norm: str,
    context: BusinessContext,
    cutoff_dt: pd.Timestamp,
    cutoff_eod: pd.Timestamp,
    as_of_date: str | None,
    master: ProductMasterSchema | None,
) -> tuple[str, object, str]:
    m = re.search(
        r"\b(?:may|bao\s+nhieu|bn)\s+([a-z0-9]+)\s+chan\b.*?\b(?:may|bao\s+nhieu|bn)\s+([a-z0-9]+)\s+le\b",
        q_norm,
    )
    if not m:
        return "abstain", None, "Box/unit split pattern incomplete"
    big_unit, small_unit = m.group(1), m.group(2)

    stock_df = _extract_stock_df_at_cutoff(context, cutoff_dt, cutoff_eod, as_of_date)
    if stock_df is None or stock_df.empty or master is None or master.unit_df is None:
        return "unanswerable", None, "Missing inventory stock or unit conversion table"

    # Extract product phrase cleanly between date/opening marker and "con may ..."
    frag = q_norm
    frag = re.split(r"\bcon\s+(?:lai\s+)?(?:may|bao\s+nhieu|bn)\b", frag)[0]
    frag = re.sub(
        r"^(?:cuoi\s+ngay|het\s+ngay|vao\s+cuoi\s+ngay|tinh\s+den\s+het\s+ngay|cho\s+(?:minh|toi)\s+hoi)?\s*(?:hom\s+nay|ngay\s+\d{1,2}[/-]\d{1,2}[/-]\d{4}|\d{1,2}[/-]\d{1,2}[/-]\d{4})?\s*",
        "",
        frag,
    ).strip(" ,?:-")
    prod_row = match_product_row(frag, master.products_df, master.name_col)
    if prod_row is None:
        return "abstain", None, "Product ambiguous or unmatched for box split"

    pid = str(prod_row[master.id_col]) if master.id_col else ""
    pname = str(prod_row[master.name_col])
    big_factor = get_unit_factor(master, pid, pname, big_unit)
    small_factor = get_unit_factor(master, pid, pname, small_unit)
    if not big_factor or not small_factor or big_factor <= small_factor:
        return (
            "unanswerable",
            None,
            "Unit conversion factor not found for requested units",
        )

    # Find stock qty for matched product
    qty_row = pd.DataFrame()
    if pid and stock_df["_pid"].notna().any():
        qty_row = stock_df[stock_df["_pid"].astype(str) == pid]
    if qty_row.empty and stock_df["_pname"].notna().any():
        pname_clean = _clean_match_str(pname)
        qty_row = stock_df[
            stock_df["_pname"].fillna("").astype(str).map(_clean_match_str)
            == pname_clean
        ]
    if qty_row.empty:
        qty_base = 0.0
        stock_unit = ""
    else:
        qty_base = float(qty_row["_qty"].sum())
        stock_unit = (
            str(qty_row.iloc[0]["_unit"]) if pd.notna(qty_row.iloc[0]["_unit"]) else ""
        )

    if stock_unit:
        stock_factor = get_unit_factor(master, pid, pname, stock_unit)
        if stock_factor is None:
            return "abstain", None, "Stock unit conversion unknown"
        qty_base = qty_base * stock_factor

    qty_in_small = int(round(qty_base / small_factor))
    ratio = int(round(big_factor / small_factor))
    if ratio <= 1 or abs((big_factor / small_factor) - ratio) > 1e-4:
        return "abstain", None, "Non-integer unit ratio"
    return (
        "list",
        [qty_in_small // ratio, qty_in_small % ratio],
        "Computed even/odd unit inventory split",
    )


def _resolve_inventory_cost(
    context: BusinessContext,
    cutoff_dt: pd.Timestamp,
    cutoff_eod: pd.Timestamp,
    as_of_date: str | None,
    master: ProductMasterSchema | None,
) -> tuple[str, object, str]:
    has_any_stock_table = any(
        not df.empty
        and not _DUP_TABLE_RE.search(t.lower())
        and find_df_col(df, ["ton_cuoi_ky", "ton_kho", "ton_thuc", "ton_cuoi"])
        is not None
        for t, df in context.tables.items()
    )
    if master is None or not master.cost_col:
        _, imp_det = _find_import_tables(context)
        has_import_prices = (
            imp_det is not None
            and find_df_col(imp_det, ["don_gia", "don_gia_nhap", "gia_nhap"])
            is not None
        )
        if has_any_stock_table and has_import_prices:
            return (
                "abstain",
                None,
                "Inventory stock and import prices exist without product gia_von column",
            )
        return (
            "unanswerable",
            None,
            "Inventory cost valuation requires product cost column",
        )

    stock_df = _extract_stock_df_at_cutoff(context, cutoff_dt, cutoff_eod, as_of_date)
    if stock_df is None:
        # Distinguish genuinely missing stock tables vs historical rollback abstention
        if not has_any_stock_table:
            return "unanswerable", None, "Inventory stock table unavailable"
        return (
            "abstain",
            None,
            "Historical inventory cutoff cannot be resolved cleanly from snapshot",
        )

    costs = master.products_df.copy()
    costs["_cost"] = to_numeric_series(costs[master.cost_col]).astype(float)
    costs["_pname_clean"] = (
        costs[master.name_col].fillna("").astype(str).map(_clean_match_str)
    )

    if master.id_col and stock_df["_pid"].notna().all():
        cmap = costs[[master.id_col, "_cost"]].drop_duplicates(master.id_col)
        cmap.columns = ["_pid", "_cost"]
        cmap["_pid"] = cmap["_pid"].astype(str)
        joined = stock_df.merge(cmap, on="_pid", how="left")
    else:
        cmap = costs[["_pname_clean", "_cost"]].drop_duplicates("_pname_clean")
        stock_df = stock_df.copy()
        stock_df["_pname_clean"] = (
            stock_df["_pname"].fillna("").astype(str).map(_clean_match_str)
        )
        joined = stock_df.merge(cmap, on="_pname_clean", how="left")

    positive = joined[joined["_qty"].notna() & (joined["_qty"] > 0)]
    if positive.empty:
        return "number", 0, "Zero positive inventory at cutoff"
    if positive["_cost"].isna().any():
        return "unanswerable", None, "Missing unit cost for stocked items"

    total_val = float((joined["_qty"].fillna(0.0) * joined["_cost"].fillna(0.0)).sum())
    return "number", _format_num(total_val), "Computed inventory valuation at cost"


def _resolve_batch_hsd(
    q_norm: str,
    context: BusinessContext,
    cutoff_dt: pd.Timestamp,
    cutoff_eod: pd.Timestamp,
    as_of_date: str | None,
    master: ProductMasterSchema | None,
) -> tuple[str, object, str]:
    has_batch, active_lots = _extract_active_lots(context, cutoff_eod, master)
    if not has_batch or active_lots is None:
        return "unanswerable", None, "Batch/expiry tracking tables not available"

    date_range = parse_hsd_date_range(q_norm, as_of_date)

    # 1. Nearest-expiring batch date for a single product
    if date_range is None and re.search(
        r"\b(?:gan\s+het\s+han\s+nhat|sap\s+het\s+han\s+nhat|het\s+han\s+som\s+nhat)\b",
        q_norm,
    ):
        if master is None:
            return "abstain", None, "No product master for batch product lookup"
        frag = re.split(
            r"\b(?:gan\s+het\s+han\s+nhat|sap\s+het\s+han\s+nhat|het\s+han\s+som\s+nhat)\b",
            q_norm,
        )[0]
        m_lo = re.search(r"\blo\s+(.+)$", frag)
        prod_frag = m_lo.group(1) if m_lo else frag
        prod_row = match_product_row(prod_frag, master.products_df, master.name_col)
        if prod_row is None:
            return "abstain", None, "Product ambiguous or unmatched for nearest HSD"
        pid = str(prod_row[master.id_col]) if master.id_col else ""
        pname = str(prod_row[master.name_col])
        sub = active_lots[
            (active_lots["_pid"].astype(str) == pid)
            | (active_lots["_pname"].astype(str) == pname)
        ]
        if sub.empty:
            return "unanswerable", None, "No remaining lots for product"
        unexpired = sub[sub["_hsd_dt"] >= cutoff_dt]
        target_lots = unexpired if not unexpired.empty else sub
        min_dt = target_lots["_hsd_dt"].min()
        return (
            "date",
            min_dt.strftime("%Y-%m-%d"),
            "Computed nearest-expiring batch HSD",
        )

    if date_range is None:
        return "abstain", None, "Unsupported batch expiry query without date range"

    d1, d2 = date_range
    sub = active_lots[
        (active_lots["_hsd_dt"] >= d1) & (active_lots["_hsd_dt"] <= d2)
    ].copy()

    # 2. Capital tied up in expiring lots
    if re.search(
        r"\b(?:tien\s+von|gia\s+von|von\b|bao\s+nhieu\s+tien|tri\s+gia)\b", q_norm
    ):
        if sub.empty:
            return "number", 0, "No expiring lots in range"
        if sub["_lot_unit_cost"].isna().any():
            # Fallback to master product cost only if lot import cost column does not exist
            if master is not None and master.id_col and master.cost_col:
                cmap = master.products_df[
                    [master.id_col, master.cost_col]
                ].drop_duplicates(master.id_col)
                cmap.columns = ["_pid", "_master_cost"]
                cmap["_pid"] = cmap["_pid"].astype(str)
                cmap["_master_cost"] = to_numeric_series(cmap["_master_cost"]).astype(
                    float
                )
                sub = sub.merge(cmap, on="_pid", how="left")
                sub["_eff_cost"] = sub["_lot_unit_cost"].where(
                    sub["_lot_unit_cost"].notna(), sub["_master_cost"]
                )
            else:
                sub["_eff_cost"] = sub["_lot_unit_cost"]
        else:
            sub["_eff_cost"] = sub["_lot_unit_cost"]

        if sub["_eff_cost"].isna().any():
            return "unanswerable", None, "Batch purchase cost unavailable"
        total = float((sub["_rem_qty"] * sub["_eff_cost"].astype(float)).sum())
        return "number", _format_num(total), "Computed expiring batch capital"

    # 3. Count lots or distinct products
    if re.search(r"\b(?:co\s+may|bao\s+nhieu)\s+(?:lo\b|lo\s+hang)", q_norm):
        return "number", int(len(sub)), "Counted expiring lots in range"

    if re.search(
        r"\b(?:co\s+may|bao\s+nhieu)\s+(?:mat\s+hang|san\s+pham|sp\b|hang\s+hoa)",
        q_norm,
    ):
        key_col = "_pid" if sub["_pid"].notna().all() else "_pname"
        return (
            "number",
            int(sub[key_col].nunique()),
            "Counted distinct products expiring in range",
        )

    # 4. List of expiring products sorted by HSD ascending
    if re.search(
        r"\b(?:hang\s+nao|mat\s+hang\s+nao|san\s+pham\s+nao|sp\s+nao|nhung\s+(?:sp|san\s+pham|mat\s+hang|hang)\s+nao)\b",
        q_norm,
    ):
        if sub.empty:
            return "list", [], "No expiring products in range"
        sort_cols = (
            ["_hsd_dt", "_pname"]
            if sub["_pname"].notna().all()
            else ["_hsd_dt", "_pid"]
        )
        sub_sorted = sub.sort_values(sort_cols, kind="mergesort")
        names = [
            str(n)
            for n in dict.fromkeys(sub_sorted["_pname"].dropna().astype(str))
            if n.strip()
        ]
        if not names:
            return "abstain", None, "Product master names unavailable for expiring lots"
        return "list", names, "Listed expiring products ordered by HSD"

    return "abstain", None, "Unsupported batch/HSD question structure"


def resolve_inventory_batch_query(
    question: str,
    context: BusinessContext,
    as_of_date: str | None = None,
) -> tuple[str, object, str]:
    """Resolve inventory cost valuation, box/unit split, and batch/expiry queries schema-generically."""
    q_norm = normalize_inventory_text(question)
    # Prevent accented credit-term phrase "hạn đúng" from colliding with accentless "han dung" ("hạn dùng")
    q_exp_norm = normalize_inventory_text(
        re.sub(r"\bhạn\s+đúng\b", " ", question.lower())
    )
    is_credit_context = bool(
        re.search(
            r"\b(?:ban\s+chiu|ghi\s+no|cong\s+no|phai\s+tra|han\s+tra|tra\s+no|tra\s+gop)\b",
            q_norm,
        )
    )

    is_box_split = bool(
        re.search(
            r"\b(?:may|bao\s+nhieu|bn)\s+[a-z0-9]+\s+chan\b.*?\b(?:may|bao\s+nhieu|bn)\s+[a-z0-9]+\s+le\b",
            q_norm,
        )
    )
    is_batch_hsd = (not is_credit_context) and bool(
        re.search(r"\b(?:han\s*(?:su\s*)?dung|hsd|het\s+han)\b", q_exp_norm)
    )
    is_inv_cost = (
        not is_batch_hsd
        and bool(re.search(r"\b(?:trong\s+kho|ton\s+kho)\b", q_norm))
        and bool(re.search(r"\b(?:gia\s+von|theo\s+gia\s+von)\b", q_norm))
        and not bool(
            re.search(
                r"\b(?:loi\s+nhuan|lai\s+gop|lai\s+rong|doanh\s+thu|chi\s+phi)\b",
                q_norm,
            )
        )
    )

    if not (is_box_split or is_batch_hsd or is_inv_cost):
        return "abstain", None, "Not an inventory/batch/box-split query"

    cutoff_dt, cutoff_eod = parse_inventory_cutoff_date(q_norm, as_of_date)
    if cutoff_dt is None or cutoff_eod is None:
        return "abstain", None, "Invalid or unparsed inventory cutoff date"

    master = extract_product_master(context)

    if is_box_split:
        return _resolve_box_even_odd(
            q_norm, context, cutoff_dt, cutoff_eod, as_of_date, master
        )
    if is_batch_hsd:
        return _resolve_batch_hsd(
            q_norm, context, cutoff_dt, cutoff_eod, as_of_date, master
        )
    return _resolve_inventory_cost(context, cutoff_dt, cutoff_eod, as_of_date, master)
