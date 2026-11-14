"""Schema-general purchase analytics (total spend, Top-1 supplier, weighted-avg unit price)."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

import pandas as pd

from zaic_2026_datamate.cleaner import (
    clean_vietnamese_currency,
    compact_digit_unit_spaces,
    normalize_query_telex,
    round_half_up,
)
from zaic_2026_datamate.data_loader import BusinessContext, parse_mixed_excel_dates
from zaic_2026_datamate.semantic_contract import (
    is_bad_status,
    item_norm_key,
    norm_vn,
    parse_enhanced_period,
)

_SHEET_SUFFIX_RE = re.compile(r"_sheet\d*$")
_NON_PURCHASE_TABLE_RE = re.compile(
    r"(?:bao_cao_ton_kho|^ton_kho$|_ton_kho$|the_kho|xuat_nhap_ton|lo_hang$)"
)
_PURCHASE_TABLE_RE = re.compile(
    r"(?:^|_)(?:nhap_hang|chi_tiet_nhap_hang|phieu_nhap|chi_tiet_nhap|don_nhap_hang|don_nhap_hang_chi_tiet|so_nhap_hang|so_tay_nhap_hang)$"
)
_ANY_PURCHASE_HINT_TABLE_RE = re.compile(
    r"(?:nhap_hang|phieu_nhap|don_nhap|mua_hang|chi_tiet_nhap|nhap_kho)"
)
_MONEY_COL_HINT_RE = re.compile(r"(?:gia|tien|tra|chi|phi|tri|amount|price|cost|value)")

_SUBJECTIVE_SUPPLIER_KWS = (
    "dang tin cay",
    "uy tin",
    "nha cung cap tot nhat",
    "ncc tot nhat",
)
_EXCLUSION_KWS = (
    "thu nhap",
    "dang nhap",
    "nhap vien",
    "nhap khau",
    "sap nhap",
    "tru so ban",
    "nhap tru ban",
    "tru ban",
    "ton kho",
    "ton cuoi",
    "ton thuc",
    "the kho",
    "het han",
    "sap het",
    "can date",
    "lo hang",
    "khach hang",
    "nhan vien",
    "doanh thu",
    "cong no",
    "con no",
    "no ncc",
    "no nha cung cap",
    "phai tra",
    "han no",
    "chua tra",
    "tang truong",
    "so voi",
    "bien dong",
    "chenh lech",
    "bao nhieu don",
    "bao nhieu phieu",
    "may phieu",
    "may don",
    "bao nhieu lan",
    "bao nhieu ncc",
    "bao nhieu nha cung cap",
    "danh sach",
    "nhung nha cung cap",
    "cac nha cung cap",
    "nhung mat hang",
    "cac mat hang",
    "ngay nao",
    "bua nao",
    "hom nao",
    "thang nao",
    "san pham nao",
    "mat hang nao",
    "hang nao",
    "binh quan ngay",
    "trung binh ngay",
    "moi ngay",
    "hang ngay",
    "binh quan thang",
    "binh quan moi phieu",
    "binh quan phieu",
    "binh quan don",
    "it nhat",
    "thap nhat",
    "nho nhat",
    "nhieu lan",
    "nhieu phieu",
    "nhieu don",
    "nhieu loai",
    "nhieu mat hang",
)
_KNOWN_UNIT_TOKENS = {
    "hop",
    "vien",
    "vi",
    "goi",
    "tuyp",
    "ong",
    "chai",
    "lo",
    "thung",
    "cay",
    "cuon",
    "kg",
    "g",
    "gram",
    "lit",
    "l",
    "ml",
    "met",
    "m",
    "m3",
    "xe",
    "bao",
    "tui",
    "binh",
    "lon",
    "loc",
    "bo",
    "cai",
    "chiec",
    "cap",
    "doi",
    "to",
    "tap",
    "quyen",
    "hu",
    "ly",
    "coc",
    "chen",
    "bat",
    "dia",
    "suat",
    "phan",
}


@dataclass(frozen=True)
class _PurchaseSchema:
    status: str  # "ready" | "missing_schema" | "unmapped_schema"
    mode: str | None = None  # "header_detail" | "flat"
    header_name: str | None = None
    detail_name: str | None = None
    join_col: str | None = None
    date_col: str | None = None
    status_col: str | None = None
    spend_col: str | None = None
    dedup_spend_col: str | None = None
    supplier_id_col: str | None = None
    supplier_name_col: str | None = None
    supplier_table: str | None = None
    supplier_tbl_id_col: str | None = None
    supplier_tbl_name_col: str | None = None
    qty_col: str | None = None
    price_col: str | None = None
    amount_col: str | None = None
    unit_col: str | None = None
    item_id_col: str | None = None
    item_name_col: str | None = None


def _select_base_tables(context: BusinessContext) -> dict[str, pd.DataFrame]:
    """Return non-calendar base tables, avoiding redundant _sheet1 duplicates."""
    base: dict[str, pd.DataFrame] = {}
    sheet_only: dict[str, pd.DataFrame] = {}
    for name, df in context.tables.items():
        if name == "calendar":
            continue
        if _SHEET_SUFFIX_RE.search(name):
            sheet_only[name] = df
        else:
            base[name] = df
    for name, df in sheet_only.items():
        stem = _SHEET_SUFFIX_RE.sub("", name)
        if stem not in base:
            base[name] = df
    return base


def _first_col(df: pd.DataFrame, candidates: tuple[str, ...]) -> str | None:
    cols = set(df.columns)
    return next((c for c in candidates if c in cols), None)


def _to_numeric_series(s: pd.Series) -> pd.Series:
    num = pd.to_numeric(s, errors="coerce")
    if num.isna().any() and s.dtype == object:
        parsed = s.map(lambda v: clean_vietnamese_currency(v) if pd.notna(v) else None)
        num = num.fillna(pd.to_numeric(parsed, errors="coerce"))
    return num


def _has_any_money_hint_col(
    tables: dict[str, pd.DataFrame], table_names: list[str]
) -> bool:
    for t in table_names:
        df = tables[t]
        for col in df.columns:
            c_norm = norm_vn(col).replace(" ", "_")
            if c_norm in {
                "don_vi_tinh",
                "don_vi",
                "chi_nhanh",
                "ma_chi_nhanh",
                "trang_thai",
                "tinh_trang",
                "dien_giai",
                "ghi_chu",
            }:
                continue
            if _MONEY_COL_HINT_RE.search(c_norm):
                return True
    return False


def _discover_purchase_schema(tables: dict[str, pd.DataFrame]) -> _PurchaseSchema:
    purchase_tables = [
        t
        for t in sorted(tables)
        if _PURCHASE_TABLE_RE.search(norm_vn(t).replace(" ", "_"))
        and not _NON_PURCHASE_TABLE_RE.search(norm_vn(t).replace(" ", "_"))
    ]
    hint_tables = [
        t
        for t in sorted(tables)
        if _ANY_PURCHASE_HINT_TABLE_RE.search(norm_vn(t).replace(" ", "_"))
        and not _NON_PURCHASE_TABLE_RE.search(norm_vn(t).replace(" ", "_"))
    ]

    if not purchase_tables and not hint_tables:
        # Check if any non-catalog table contains purchase transaction columns
        for t, df in tables.items():
            t_norm = norm_vn(t).replace(" ", "_")
            if any(
                k in t_norm for k in ("san_pham", "hang_hoa", "ton_kho", "danh_muc")
            ):
                continue
            if any(
                c in df.columns
                for c in (
                    "ngay_nhap",
                    "ngay_nhap_kho",
                    "ma_nhap_hang",
                    "ma_phieu_nhap",
                    "can_tra_ncc",
                    "tong_gia_tri_nhap",
                )
            ):
                return _PurchaseSchema(status="unmapped_schema")
        return _PurchaseSchema(status="missing_schema")

    # Try flat table first if single consolidated flat ledger exists (e.g. so_nhap_hang, so_tay_nhap_hang, denormalized chi_tiet_nhap_hang)
    for t in purchase_tables:
        df = tables[t]
        date_col = _first_col(df, ("ngay_nhap_kho", "ngay_nhap", "thoi_gian", "ngay"))
        qty_col = _first_col(df, ("sl_nhap", "so_luong_nhap", "so_luong", "sl"))
        price_col = _first_col(df, ("don_gia_nhap", "gia_nhap", "don_gia"))
        amount_col = _first_col(df, ("thanh_tien",))
        dedup_spend_col = _first_col(
            df,
            ("tong_tien_phieu", "tong_gia_tri_nhap", "can_tra_ncc", "tong_tien_nhap"),
        )
        jcol = _first_col(
            df, ("ma_nhap_hang", "ma_phieu_nhap", "ma_don_nhap", "so_phieu")
        )
        item_name_col = _first_col(
            df,
            (
                "ten_mat_hang",
                "ten_hang_hoa",
                "ten_hang",
                "ten_san_pham",
                "product",
                "ten_goi",
            ),
        )
        item_id_col = _first_col(df, ("ma_hang_hoa", "ma_hang", "ma_sku", "ma_sp"))
        if date_col and (
            (qty_col or item_name_col or item_id_col)
            and (price_col or amount_col or dedup_spend_col)
        ):
            use_dedup = (
                dedup_spend_col
                if (dedup_spend_col and jcol and not amount_col)
                or (dedup_spend_col == "tong_tien_phieu" and jcol)
                else None
            )
            return _PurchaseSchema(
                status="ready",
                mode="flat",
                header_name=t,
                detail_name=t,
                join_col=jcol,
                date_col=date_col,
                status_col=_first_col(df, ("trang_thai", "tinh_trang")),
                spend_col=amount_col or dedup_spend_col,
                dedup_spend_col=use_dedup,
                supplier_name_col=_first_col(
                    df, ("nha_cung_cap", "ten_ncc", "ten_nha_cung_cap")
                ),
                supplier_id_col=_first_col(df, ("ma_nha_cung_cap", "ma_ncc")),
                qty_col=qty_col,
                price_col=price_col,
                amount_col=amount_col,
                unit_col=_first_col(df, ("dvt_nhap", "dvt", "don_vi_tinh", "don_vi")),
                item_id_col=item_id_col,
                item_name_col=item_name_col,
            )

    # Try relational header + detail pair
    header_cands = []
    detail_cands = []
    for t in purchase_tables:
        df = tables[t]
        jcol = _first_col(
            df, ("ma_nhap_hang", "ma_phieu_nhap", "ma_don_nhap", "so_phieu")
        )
        dcol = _first_col(df, ("ngay_nhap_kho", "ngay_nhap", "thoi_gian", "ngay"))
        qcol = _first_col(df, ("sl_nhap", "so_luong_nhap", "so_luong", "sl"))
        pcol = _first_col(df, ("don_gia_nhap", "gia_nhap", "don_gia"))
        scol = _first_col(
            df,
            (
                "tong_gia_tri_nhap",
                "can_tra_ncc",
                "tong_tien_nhap",
                "tong_tien",
                "thanh_tien",
            ),
        )
        icol = _first_col(df, ("ma_hang_hoa", "ma_hang", "ma_sku", "ma_sp"))
        if jcol and dcol and not qcol and not icol:
            header_cands.append((t, jcol, dcol, scol))
        elif jcol and (qcol or pcol or icol):
            detail_cands.append((t, jcol))

    for ht, hj, dcol, scol in header_cands:
        matching_details = [dt for dt, dj in detail_cands if dj == hj]
        if not matching_details:
            continue
        # Prefer detail table sharing longest prefix with header table
        dt = max(
            matching_details,
            key=lambda cand: len(
                "".join(a for a, b in zip(ht, cand, strict=False) if a == b)
            ),
        )
        hdf = tables[ht]
        ddf = tables[dt]
        pcol = _first_col(ddf, ("don_gia_nhap", "gia_nhap", "don_gia"))
        acol = _first_col(ddf, ("thanh_tien",))
        qcol = _first_col(ddf, ("sl_nhap", "so_luong_nhap", "so_luong", "sl"))

        if not scol and not pcol and not acol:
            if not _has_any_money_hint_col(
                tables, list(dict.fromkeys(purchase_tables + hint_tables))
            ):
                return _PurchaseSchema(status="missing_schema")
            return _PurchaseSchema(status="unmapped_schema")

        sup_id_col = _first_col(hdf, ("ma_nha_cung_cap", "ma_ncc"))
        sup_name_col = _first_col(hdf, ("nha_cung_cap", "ten_ncc", "ten_nha_cung_cap"))
        sup_tbl = sup_tbl_id = sup_tbl_name = None
        if sup_id_col and not sup_name_col:
            for st_name in sorted(tables):
                st_norm = norm_vn(st_name).replace(" ", "_")
                if (
                    "nha_cung_cap" in st_norm
                    or st_norm == "ncc"
                    or st_norm.endswith("_ncc")
                ):
                    sdf = tables[st_name]
                    c_id = _first_col(sdf, ("ma_nha_cung_cap", "ma_ncc"))
                    c_nm = _first_col(
                        sdf,
                        (
                            "ten_goi",
                            "ten_nha_cung_cap",
                            "ten_ncc",
                            "nha_cung_cap",
                            "ten",
                        ),
                    )
                    if c_id and c_nm:
                        sup_tbl, sup_tbl_id, sup_tbl_name = st_name, c_id, c_nm
                        break

        return _PurchaseSchema(
            status="ready",
            mode="header_detail",
            header_name=ht,
            detail_name=dt,
            join_col=hj,
            date_col=dcol,
            status_col=_first_col(hdf, ("trang_thai", "tinh_trang")),
            spend_col=scol,
            supplier_id_col=sup_id_col,
            supplier_name_col=sup_name_col,
            supplier_table=sup_tbl,
            supplier_tbl_id_col=sup_tbl_id,
            supplier_tbl_name_col=sup_tbl_name,
            qty_col=qcol,
            price_col=pcol,
            amount_col=acol,
            unit_col=_first_col(ddf, ("dvt_nhap", "dvt", "don_vi_tinh", "don_vi")),
            item_id_col=_first_col(ddf, ("ma_hang_hoa", "ma_hang", "ma_sku", "ma_sp")),
            item_name_col=_first_col(
                ddf,
                (
                    "ten_mat_hang",
                    "ten_hang_hoa",
                    "ten_hang",
                    "ten_san_pham",
                    "product",
                    "ten_goi",
                ),
            ),
        )

    all_p_tables = list(dict.fromkeys(purchase_tables + hint_tables))
    if not _has_any_money_hint_col(tables, all_p_tables):
        return _PurchaseSchema(status="missing_schema")
    return _PurchaseSchema(status="unmapped_schema")


def _build_item_catalog(
    tables: dict[str, pd.DataFrame],
) -> tuple[dict[str, str], list[str]]:
    """Build item_id -> canonical name map and unique item names, preferring product masters over ton_kho."""

    def _priority(tname: str) -> int:
        tn = norm_vn(tname).replace(" ", "_")
        if "san_pham" in tn or "hang_hoa" in tn:
            return 0
        if _PURCHASE_TABLE_RE.search(tn):
            return 1
        if "ton_kho" in tn:
            return 3
        return 2

    id_to_name: dict[str, str] = {}
    canon_by_key: dict[str, str] = {}
    for tname in sorted(tables, key=lambda n: (_priority(n), n)):
        df = tables[tname]
        ic = _first_col(df, ("ma_hang_hoa", "ma_hang", "ma_sku", "ma_sp", "ma_mon"))
        nc = _first_col(
            df,
            (
                "ten_hang_hoa",
                "ten_hang",
                "ten_san_pham",
                "ten_mat_hang",
                "ten_goi",
                "product",
                "hang_hoa",
                "ten_mon",
                "mon",
            ),
        )
        if not nc:
            continue
        if ic:
            for iv, nv in zip(df[ic], df[nc], strict=False):
                if pd.notna(iv) and pd.notna(nv):
                    n_str = str(nv).strip()
                    if n_str:
                        id_to_name.setdefault(norm_vn(iv), n_str)
                        canon_by_key.setdefault(item_norm_key(n_str), n_str)
        else:
            for nv in df[nc].dropna():
                n_str = str(nv).strip()
                if n_str:
                    canon_by_key.setdefault(item_norm_key(n_str), n_str)

    return id_to_name, list(canon_by_key.values())


def _match_catalog_item(qn: str, catalog_names: list[str]) -> tuple[str | None, bool]:
    """Return (matched_name, ambiguous_flag) using word-bounded longest match."""
    q_comp = compact_digit_unit_spaces(qn)
    ranked: list[tuple[int, str, str]] = []
    for name in catalog_names:
        n_norm = norm_vn(name)
        if len(n_norm) < 2:
            continue
        n_comp = compact_digit_unit_spaces(n_norm)
        matched = False
        for target_q, target_n in ((qn, n_norm), (q_comp, n_comp)):
            if re.search(rf"(?<![a-z0-9]){re.escape(target_n)}(?![a-z0-9])", target_q):
                matched = True
                break
        if matched:
            ranked.append((len(n_comp), item_norm_key(name), name))
    if not ranked:
        return None, False
    ranked.sort(key=lambda r: (-r[0], r[1]))
    top_len, top_key, top_name = ranked[0]
    ties = {key for length, key, _ in ranked if length == top_len}
    if len(ties) > 1:
        return None, True
    return top_name, False


def _filter_period_df(
    df: pd.DataFrame,
    date_col: str,
    status_col: str | None,
    period: Any,
) -> pd.DataFrame | None:
    if df.empty or date_col not in df.columns:
        return pd.DataFrame(columns=df.columns)
    work = df.copy()
    if status_col and status_col in work.columns:
        work = work[~work[status_col].map(is_bad_status)]
    work["_date_iso"] = parse_mixed_excel_dates(work[date_col]).fillna("").astype(str)
    work = work[work["_date_iso"].str.len() == 10]
    if period.exact_date:
        return work[work["_date_iso"] == period.exact_date]
    if period.start_date and period.end_date:
        return work[
            (work["_date_iso"] >= period.start_date)
            & (work["_date_iso"] <= period.end_date)
        ]
    if period.month_str:
        return work[work["_date_iso"].str.slice(0, 7) == period.month_str]
    return None


def _classify_purchase_intent(qn: str, catalog_names: list[str]) -> str | None:
    if any(k in qn for k in _SUBJECTIVE_SUPPLIER_KWS):
        return "subjective_unanswerable"
    if not re.search(r"\b(?:nhap|ncc|nha cung cap)\b", qn):
        return None
    if any(k in qn for k in _EXCLUSION_KWS):
        return None
    if re.search(r"\btop\s*\d+\b", qn):
        return None

    # Intent 1: weighted-average unit purchase price
    if (
        re.search(r"\b(?:binh quan|trung binh)\b", qn)
        and re.search(r"\bnhap\b", qn)
        and re.search(r"\b(?:gia|tien|bn|bao nhieu|nhieu|mot|moi)\b", qn)
    ):
        return "avg_unit_price"

    # Intent 2: Top-1 supplier by purchase spend
    if (
        re.search(r"\b(?:ncc|nha cung cap)\b", qn)
        and re.search(r"\b(?:nhieu|cao|lon)\b.*\bnhat\b", qn)
        and (
            re.search(r"\b(?:tien|gia tri|tien hang)\b", qn)
            or re.search(r"\b(?:lon|cao)\b.*\bnhat\b", qn)
        )
    ):
        return "top_supplier"

    # Intent 3: Total purchase spend
    if re.search(r"\bnhap\b", qn) and (
        re.search(r"\btong\b.*\b(?:tien|gia tri|chi)\b", qn)
        or re.search(
            r"\b(?:tien hang nhap|tien nhap hang|gia tri(?: cac)? phieu nhap|chi phi nhap hang)\b",
            qn,
        )
        or re.search(r"\bchi\b.*\b(?:bao nhieu|bn|bnhiu|nhieu)\b.*\bnhap\b", qn)
        or re.search(
            r"\b(?:nhap hang|tien nhap)\b.*\b(?:het|bao nhieu|bn|bnhiu|nhieu)\b.*\btien\b|\b(?:nhap hang|tien nhap)\b.*\bhet\s+(?:bao nhieu|bn|bnhiu|nhieu)\b|\b(?:bao nhieu|bn|bnhiu|nhieu)\s+tien\b.*\bnhap hang\b",
            qn,
        )
    ):
        if re.search(
            r"\b(?:binh quan|trung binh|don gia|mot hop|mot thung|mot cay|mot binh|mot tuyp|mot chai|mot vien|mot goi|mot bo|mot cai|mot kg|mot lit)\b",
            qn,
        ):
            return None
        matched_item, _ = _match_catalog_item(qn, catalog_names)
        if matched_item is not None:
            return None
        return "total_spend"

    return None


def _extract_requested_unit(qn: str) -> tuple[str | None, str | None]:
    """Extract requested unit (full phrase + head token) around 'mot/moi/1 <unit>' outside dates."""
    q_clean = re.sub(
        r"\b(?:ngay|thang|nam|quy|tuan|lan)\s+\d+(?:\s*/\s*\d+)?\b", " ", qn
    )
    anchor_pat = (
        r"\b(?:binh quan|trung binh|gia nhap|nhap(?: vao)?|bao nhieu tien|bn tien)\b"
    )
    unit_pat = (
        r"\b(?:mot|moi|(?<![0-9/.])1(?![0-9/.a-z]))\s+([a-z0-9]+(?:\s+[0-9]+m3)?)\b"
    )
    matches = list(re.finditer(unit_pat, q_clean))
    if not matches:
        return None, None
    # Prefer match closest after average/price keywords if multiple exist
    anchor = re.search(anchor_pat, q_clean)
    chosen = matches[-1]
    if anchor:
        after_anchor = [m for m in matches if m.start() >= anchor.start()]
        if after_anchor:
            chosen = after_anchor[0]
    phrase = chosen.group(1).strip()
    head = phrase.split()[0]
    if head in _KNOWN_UNIT_TOKENS:
        return phrase, head
    return None, None


def _unit_compatible(
    req_phrase: str | None, req_head: str | None, table_unit: str
) -> bool:
    if not req_head:
        return True
    u_norm = norm_vn(table_unit)
    if not u_norm:
        return True
    if req_phrase and u_norm == req_phrase:
        return True
    return req_head in u_norm.split()


def _resolve_avg_unit_price(
    schema: _PurchaseSchema,
    tables: dict[str, pd.DataFrame],
    id_to_name: dict[str, str],
    catalog_names: list[str],
    qn: str,
    period: Any,
) -> tuple[str, Any, str | None]:
    matched_item, ambiguous = _match_catalog_item(qn, catalog_names)
    if ambiguous or not matched_item:
        return (
            "abstain",
            None,
            "Không xác định được mặt hàng cần tính giá nhập bình quân",
        )
    if not schema.qty_col or (not schema.price_col and not schema.amount_col):
        return "abstain", None, "Thiếu cột số lượng hoặc đơn giá nhập"

    hdf = tables[schema.header_name]
    if schema.mode == "header_detail":
        assert schema.join_col is not None and schema.detail_name is not None
        if hdf[schema.join_col].duplicated().any():
            return "abstain", None, "Khóa phiếu nhập không duy nhất"
        h_filtered = _filter_period_df(hdf, schema.date_col, schema.status_col, period)
        if h_filtered is None or h_filtered.empty:
            return "abstain", None, "Không có phiếu nhập trong kỳ"
        ddf = tables[schema.detail_name]
        merged = ddf.merge(
            h_filtered[[schema.join_col]], on=schema.join_col, how="inner"
        )
    else:
        merged = _filter_period_df(hdf, schema.date_col, schema.status_col, period)
        if merged is None or merged.empty:
            return "abstain", None, "Không có dữ liệu nhập trong kỳ"

    if merged.empty:
        return "abstain", None, "Không có chi tiết nhập trong kỳ"

    if schema.item_name_col and schema.item_name_col in merged.columns:
        item_series = merged[schema.item_name_col].fillna("").astype(str)
    elif schema.item_id_col and schema.item_id_col in merged.columns:
        item_series = merged[schema.item_id_col].map(
            lambda v: id_to_name.get(norm_vn(v), "")
        )
    else:
        return "abstain", None, "Thiếu cột định danh mặt hàng nhập"

    target_key = item_norm_key(matched_item)
    sub = merged[item_series.map(item_norm_key) == target_key].copy()
    if sub.empty:
        return "abstain", None, "Không có giao dịch nhập mặt hàng này trong kỳ"

    req_phrase, req_head = _extract_requested_unit(qn)
    if schema.unit_col and schema.unit_col in sub.columns:
        units_raw = sub[schema.unit_col].dropna().astype(str).map(str.strip)
        units_norm = sorted({norm_vn(u) for u in units_raw if norm_vn(u)})
        if len(units_norm) > 1:
            if not req_head:
                return "abstain", None, "Mặt hàng có nhiều đơn vị nhập khác nhau"
            sub = sub[
                sub[schema.unit_col]
                .fillna("")
                .astype(str)
                .map(lambda u: _unit_compatible(req_phrase, req_head, u))
            ]
            if sub.empty:
                return "abstain", None, "Không có đơn vị nhập khớp câu hỏi"
            units_norm = sorted(
                {
                    norm_vn(u)
                    for u in sub[schema.unit_col].dropna().astype(str)
                    if norm_vn(u)
                }
            )
            if len(units_norm) > 1:
                return "abstain", None, "Còn nhiều đơn vị nhập sau khi lọc"
        elif len(units_norm) == 1 and not _unit_compatible(
            req_phrase, req_head, units_norm[0]
        ):
            return "abstain", None, "Đơn vị nhập không khớp câu hỏi"

    qty = _to_numeric_series(sub[schema.qty_col])
    if schema.amount_col and schema.amount_col in sub.columns:
        amt = _to_numeric_series(sub[schema.amount_col])
        if schema.price_col and schema.price_col in sub.columns:
            price = _to_numeric_series(sub[schema.price_col])
            amt = amt.fillna(qty * price)
    else:
        assert schema.price_col is not None
        price = _to_numeric_series(sub[schema.price_col])
        amt = qty * price

    valid = qty.notna() & amt.notna() & (qty > 0)
    if not valid.any():
        return "abstain", None, "Dữ liệu số lượng hoặc đơn giá nhập không hợp lệ"
    total_qty = float(qty[valid].sum())
    if total_qty <= 0:
        return "abstain", None, "Tổng số lượng nhập bằng 0"
    weighted_avg = float(amt[valid].sum()) / total_qty
    return "number", round_half_up(weighted_avg), None


def _compute_spend_series(
    df: pd.DataFrame, schema: _PurchaseSchema
) -> pd.Series | None:
    if schema.spend_col and schema.spend_col in df.columns:
        return _to_numeric_series(df[schema.spend_col])
    if schema.amount_col and schema.amount_col in df.columns:
        return _to_numeric_series(df[schema.amount_col])
    if (
        schema.qty_col
        and schema.price_col
        and schema.qty_col in df.columns
        and schema.price_col in df.columns
    ):
        return _to_numeric_series(df[schema.qty_col]) * _to_numeric_series(
            df[schema.price_col]
        )
    return None


def _compute_filtered_spend(
    schema: _PurchaseSchema,
    tables: dict[str, pd.DataFrame],
    h_filtered: pd.DataFrame,
    group_key: pd.Series | None = None,
) -> tuple[pd.Series | None, pd.Series | None, str | None]:
    """Compute filtered spend values (and aligned group keys if given) across flat/header_detail schemas."""
    hdf = tables[schema.header_name]
    if (
        schema.mode == "flat"
        and schema.dedup_spend_col
        and schema.join_col
        and schema.dedup_spend_col in h_filtered.columns
    ):
        work_df = pd.DataFrame(
            {
                "_join": h_filtered[schema.join_col],
                "_sp": _to_numeric_series(h_filtered[schema.dedup_spend_col]),
            }
        )
        if group_key is not None:
            work_df["_grp"] = group_key.values
        work_df = work_df.dropna(subset=["_sp"])
        deduped = work_df.groupby("_join", dropna=True, as_index=False).first()
        return (
            deduped["_sp"],
            (deduped["_grp"] if group_key is not None else None),
            None,
        )

    if schema.mode == "header_detail" and not schema.spend_col:
        assert schema.join_col is not None and schema.detail_name is not None
        if hdf[schema.join_col].duplicated().any():
            return None, None, "Khóa phiếu nhập không duy nhất"
        ddf = tables[schema.detail_name]
        h_join = pd.DataFrame({schema.join_col: h_filtered[schema.join_col]})
        if group_key is not None:
            h_join["_grp"] = group_key.values
        merged = ddf.merge(h_join, on=schema.join_col, how="inner")
        spend = _compute_spend_series(merged, schema)
        return spend, (merged["_grp"] if group_key is not None else None), None

    spend = _compute_spend_series(h_filtered, schema)
    return spend, group_key, None


def _resolve_top_supplier(
    schema: _PurchaseSchema,
    tables: dict[str, pd.DataFrame],
    period: Any,
) -> tuple[str, Any, str | None]:
    hdf = tables[schema.header_name]
    h_filtered = _filter_period_df(hdf, schema.date_col, schema.status_col, period)
    if h_filtered is None or h_filtered.empty:
        return "abstain", None, "Không có dữ liệu nhập hàng trong kỳ"

    if schema.supplier_name_col and schema.supplier_name_col in h_filtered.columns:
        sup_key = (
            h_filtered[schema.supplier_name_col].fillna("").astype(str).str.strip()
        )
        id_to_supplier: dict[str, str] | None = None
    elif (
        schema.supplier_id_col
        and schema.supplier_id_col in h_filtered.columns
        and schema.supplier_table
        and schema.supplier_tbl_id_col
        and schema.supplier_tbl_name_col
    ):
        sup_key = h_filtered[schema.supplier_id_col].fillna("").astype(str).str.strip()
        sdf = tables[schema.supplier_table]
        id_to_supplier = {
            str(sid).strip(): str(sname).strip()
            for sid, sname in zip(
                sdf[schema.supplier_tbl_id_col],
                sdf[schema.supplier_tbl_name_col],
                strict=False,
            )
            if pd.notna(sid) and pd.notna(sname) and str(sname).strip()
        }
    else:
        return "abstain", None, "Thiếu tên nhà cung cấp để trả lời"

    spend, aligned_sup, err = _compute_filtered_spend(
        schema, tables, h_filtered, group_key=sup_key
    )
    if err is not None:
        return "abstain", None, err
    if spend is None or aligned_sup is None:
        return "abstain", None, "Thiếu cột tổng tiền nhập hàng"

    work = pd.DataFrame(
        {
            "_sup": aligned_sup.fillna("").astype(str).str.strip().values,
            "_spend": spend.values,
        }
    )
    work = work[(work["_sup"] != "") & work["_spend"].notna()]
    if work.empty:
        return "abstain", None, "Không có dữ liệu nhà cung cấp hợp lệ"

    grouped = (
        work.groupby("_sup", dropna=True)["_spend"].sum().sort_values(ascending=False)
    )
    if grouped.empty:
        return "abstain", None, "Không có nhà cung cấp trong kỳ"
    if len(grouped) > 1 and float(grouped.iloc[0]) - float(grouped.iloc[1]) <= 1e-6:
        return (
            "abstain",
            None,
            "Có đồng hạng giá trị nhập cao nhất giữa nhiều nhà cung cấp",
        )

    winner_raw = str(grouped.index[0]).strip()
    if id_to_supplier is not None:
        winner = id_to_supplier.get(winner_raw)
        if not winner:
            return "abstain", None, "Không ánh xạ được tên nhà cung cấp"
        return "string", winner, None
    return "string", winner_raw, None


def _resolve_total_spend(
    schema: _PurchaseSchema,
    tables: dict[str, pd.DataFrame],
    period: Any,
) -> tuple[str, Any, str | None]:
    hdf = tables[schema.header_name]
    h_filtered = _filter_period_df(hdf, schema.date_col, schema.status_col, period)
    if h_filtered is None or h_filtered.empty:
        return "abstain", None, "Không có phiếu nhập hàng trong kỳ"

    spend, _, err = _compute_filtered_spend(schema, tables, h_filtered)
    if err is not None:
        return "abstain", None, err
    if spend is None or not spend.notna().any():
        return "abstain", None, "Thiếu cột giá trị nhập hàng"

    total = float(spend.dropna().sum())
    if total <= 0:
        return "abstain", None, "Tổng giá trị nhập bằng 0 hoặc không hợp lệ"
    return "number", round_half_up(total), None


def _normalize_purchase_dialect(qn: str) -> str:
    repls = {
        "nhut": "nhat",
        "hagn": "hang",
        "nhiu": "nhieu",
        "nha cc": "nha cung cap",
        "hom ni": "hom nay",
        "bua ni": "hom nay",
        "thang ni": "thang nay",
        "th nay": "thang nay",
    }
    for src, dst in repls.items():
        qn = re.sub(rf"\b{re.escape(src)}\b", dst, qn)
    return qn


def resolve_purchase_query(
    question: str, context: BusinessContext, as_of_date: str | None = None
) -> tuple[str, Any, str | None]:
    """Resolve schema-general purchase analytics and deterministic unanswerable checks."""
    q_telex = normalize_query_telex(question)
    qn = _normalize_purchase_dialect(norm_vn(q_telex))

    tables = _select_base_tables(context)
    _, catalog_names = _build_item_catalog(tables)
    intent = _classify_purchase_intent(qn, catalog_names)
    if intent is None:
        return "abstain", None, "Không phải câu hỏi nhập hàng thuộc phạm vi xác định"
    if intent == "subjective_unanswerable":
        return (
            "unanswerable",
            None,
            "Dữ liệu chỉ chứa thông tin nhập hàng, không có tiêu chí đánh giá độ uy tín hay nhà cung cấp tốt nhất.",
        )

    period = parse_enhanced_period(q_telex, as_of_date)
    if period.is_this_month and not as_of_date:
        return "abstain", None, "Thiếu as_of_date cho tháng này"
    if (
        not (
            period.exact_date
            or (period.start_date and period.end_date)
            or period.month_str
        )
        and as_of_date
        and not re.search(
            r"\b(?:tu\s+(?:truoc|xua|luc|dau|nam|thang|t\d)|truoc\s+(?:toi|den)\s+nay|luy\s+ke|tat\s+ca)\b",
            qn,
        )
        and re.search(
            r"\b(?:thang nay|toi nay|den nay|hom nay|den bay gio|den gio)\b", qn
        )
    ):
        period = parse_enhanced_period("tháng này", as_of_date)
    if not (
        period.exact_date or (period.start_date and period.end_date) or period.month_str
    ):
        return "abstain", None, "Không xác định được kỳ thời gian nhập hàng"

    schema = _discover_purchase_schema(tables)
    if schema.status == "missing_schema":
        return (
            "unanswerable",
            None,
            "Dữ liệu cửa hàng không có bảng hoặc cột giá trị nhập hàng.",
        )
    if schema.status != "ready":
        return "abstain", None, "Cấu trúc bảng nhập hàng chưa được hỗ trợ"

    id_to_name, catalog_names = _build_item_catalog(tables)

    if intent == "avg_unit_price":
        return _resolve_avg_unit_price(
            schema, tables, id_to_name, catalog_names, qn, period
        )
    if intent == "top_supplier":
        return _resolve_top_supplier(schema, tables, period)
    if intent == "total_spend":
        return _resolve_total_spend(schema, tables, period)
    return "abstain", None, "Không khớp mẫu câu hỏi nhập hàng"
