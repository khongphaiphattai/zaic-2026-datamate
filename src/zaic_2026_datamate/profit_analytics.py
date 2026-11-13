"""Schema-general gross profit, net profit, and top-profit product analytics (Slice P1.2)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import pandas as pd

from zaic_2026_datamate.data_loader import BusinessContext, parse_mixed_excel_dates
from zaic_2026_datamate.debt_analytics import (
    extract_month_ym,
    find_col,
    month_cutoff_eod,
    normalize_debt_text,
    strip_accents_norm,
    to_numeric_series,
)

_NON_ACTIVE_ORDER_STATUS_RE = re.compile(
    r"\b(?:huy|cancel|returned|return|tra\s+hang|tra\s+lai|da\s+tra|"
    r"hoan\s+tra|doi\s+tra|khong\s+giao\s+duoc|that\s+bai|failed)\b"
)

_COST_COL_NAMES = frozenset(
    {
        "gia_von",
        "gia_von_luc_ban",
        "gia_von_hang",
        "gia_von_hien_tai",
        "don_gia_nhap",
        "gia_nhap",
        "cogs",
        "don_gia_von",
        "gia_von_sp",
    }
)

_EXCLUDED_PROFIT_TERMS = (
    "trong kho",
    "ton kho",
    "cuoi ngay",
    "het han",
    "han dung",
    "han su dung",
    "phan tram",
    "ty le",
    "ti le",
    "%",
    "bien lai",
    "bien loi nhuan",
    "lai suat",
    "tra gop",
    "tien gop",
    "tu thang",
    "toi thang",
    "den thang",
    "tung thang",
    "cac thang",
    "so voi",
    "tang hay giam",
    "dong may",
    "may cu",
    "chi nhanh",
    "tai quay",
    "online",
    "kenh",
    "thuong hieu",
    "nhom hang",
)


@dataclass
class ProfitSchema:
    """Resolved sales-cost lines, operating expenses, and product directory."""

    lines_df: pd.DataFrame | None
    pid_col: str | None
    line_name_col: str | None
    prod_name_map: dict[str, str]
    exp_df: pd.DataFrame | None
    has_any_cost: bool
    has_line_cost: bool
    has_ambiguous_expense: bool
    min_ym: str | None
    max_ym: str | None


def _build_product_name_map(context: BusinessContext) -> dict[str, str]:
    """Map product ID to canonical display name from product master tables."""
    pid_to_name: dict[str, str] = {}
    id_cands = ("ma_hang_hoa", "ma_hang", "ma_sp", "ma_sku", "product_id", "id_sp")
    name_cands = (
        "ten_goi",
        "ten_hang_hoa",
        "ten_hang",
        "ten_san_pham",
        "mon",
        "ten_sp",
        "san_pham",
        "hang_hoa",
    )
    skip_kw = (
        "hoa_don",
        "don_hang",
        "chi_tiet",
        "nhap",
        "ton_kho",
        "the_kho",
        "don_vi",
        "quy_doi",
        "khach",
        "so_quy",
        "thanh_toan",
        "chi_phi",
        "lich_su",
    )
    for tname, df in context.tables.items():
        tl = tname.lower()
        if tl.endswith("_sheet1") and tl[:-7] in context.tables:
            continue
        if any(k in tl for k in skip_kw):
            continue
        if not any(k in tl for k in ("san_pham", "hang_hoa", "product", "danh_s_ich")):
            continue
        cols = {c.lower(): c for c in df.columns}
        pid_col = find_col(cols, id_cands)
        rem_name_cands = tuple(c for c in name_cands if cols.get(c) != pid_col)
        nm_col = find_col(cols, rem_name_cands)
        if not pid_col or not nm_col:
            continue
        for pid_raw, nm_raw in zip(df[pid_col], df[nm_col], strict=False):
            pid = str(pid_raw).strip()
            nm = str(nm_raw).strip()
            if (
                pid
                and nm
                and pid.lower() not in ("nan", "none")
                and nm.lower() not in ("nan", "none")
            ):
                pid_to_name.setdefault(pid, nm)
    return pid_to_name


def _schema_has_any_cost(context: BusinessContext) -> bool:
    """Return True if any non-duplicate table carries populated cost/import price data."""
    for tname, df in context.tables.items():
        tl = tname.lower()
        if tl.endswith("_sheet1") and tl[:-7] in context.tables:
            continue
        for col in df.columns:
            if col.lower() in _COST_COL_NAMES:
                vals = to_numeric_series(df[col])
                if bool((vals > 0).any()):
                    return True
    return False


def _select_expense_table(context: BusinessContext) -> tuple[pd.DataFrame | None, bool]:
    """Locate dedicated operating expense table (date + amount) and check for ambiguous cashbook expenses."""
    date_cands = ("ngay_chi", "ngay", "thoi_gian", "ngay_lap", "date", "thoi_diem")
    val_cands = ("so_tien", "gia_tri", "tien_chi", "tien", "amount", "thanh_tien")
    for tname, df in context.tables.items():
        tl = tname.lower()
        if tl.endswith("_sheet1") and tl[:-7] in context.tables:
            continue
        if any(k in tl for k in ("chi_tiet", "san_pham", "hang_hoa")):
            continue
        if not any(
            k in tl
            for k in (
                "chi_phi",
                "so_tay_chi_phi",
                "phieu_chi",
                "bang_chi_phi",
                "chi_tieu_van_hanh",
                "chi_khac",
            )
        ):
            continue
        cols = {c.lower(): c for c in df.columns}
        d_col = find_col(cols, date_cands)
        v_col = find_col(cols, val_cands)
        if not d_col or not v_col:
            continue
        out = pd.DataFrame(
            {
                "_exp_ts": parse_mixed_excel_dates(df[d_col]),
                "_exp_val": to_numeric_series(df[v_col]),
            }
        )
        out["_exp_ym"] = out["_exp_ts"].dt.strftime("%Y-%m")
        return out, False

    # Guard: check if `so_quy` carries cash-out type columns without dedicated expense table
    has_ambig_cashbook = False
    for tname, df in context.tables.items():
        tl = tname.lower()
        if "so_quy" in tl:
            cols = {c.lower(): c for c in df.columns}
            tcol = find_col(cols, ("loai_phieu", "loai_giao_dich", "thu_chi", "loai"))
            if tcol is not None:
                vals = df[tcol].astype(str).map(strip_accents_norm)
                if bool(vals.str.contains(r"\bchi\b").any()):
                    has_ambig_cashbook = True
    return None, has_ambig_cashbook


def _select_header_and_detail_tables(
    context: BusinessContext,
) -> tuple[str | None, str | None]:
    """Identify matching sales header and detail tables dynamically from column roles."""
    non_sales_doc_kw = (
        "nhap",
        "tra_hang",
        "lo_hang",
        "the_kho",
        "lich_su",
        "bao_cao",
        "so_quy",
        "thanh_toan",
        "chi_phi",
        "phieu_chi",
        "phieu_thu",
        "han_no",
    )
    entity_master_kw = (
        "san_pham",
        "hang_hoa",
        "khach_hang",
        "nhan_vien",
        "chi_nhanh",
        "nha_cung_cap",
        "don_vi",
        "quy_doi",
        "ton_kho",
        "thuoc",
    )
    date_cands = (
        "ngay_ban",
        "ngay_lap",
        "ngay_dat_hang",
        "ngay_dat",
        "ngay_hd",
        "ngay_tao",
        "ngay",
        "thoi_gian",
        "thoi_gian_lap",
        "gio_tao_don",
        "date",
    )
    detail_cands: list[str] = []
    header_cands: list[str] = []
    for tname, df in context.tables.items():
        tl = tname.lower()
        if tl.endswith("_sheet1") and tl[:-7] in context.tables:
            continue
        # Skip sharded child partitions `_1`, `_2` when unified parent table exists
        if re.search(r"_\d+$", tl) and re.sub(r"_\d+$", "", tl) in context.tables:
            continue
        if tl == "ban_hang_tong_hop" or tl.startswith("ban_hang_tong_hop_"):
            continue
        if any(k in tl for k in non_sales_doc_kw):
            continue
        if re.search(r"(?:^|_)lo(?:$|_)", tl):
            continue
        has_order_doc = bool(re.search(r"(?:^|_)(?:hoa_don|don_hang)(?:$|_)", tl))
        if not has_order_doc and any(k in tl for k in entity_master_kw):
            continue
        cols = {c.lower(): c for c in df.columns}
        is_detail = "chi_tiet" in tl
        if is_detail:
            detail_cands.append(tname)
        else:
            is_cashbook_voucher = (
                ("ma_phieu" in cols or "phieu_thu" in cols)
                and any(c in cols for c in ("gia_tri", "so_tien_thu", "tien_thu"))
                and not any(
                    c in cols
                    for c in (
                        "tong_tien",
                        "tong_sau_giam_gia",
                        "tong_thanh_toan",
                        "trang_thai",
                        "tinh_trang",
                        "tinh_trang_don",
                    )
                )
            )
            if (
                not is_cashbook_voucher
                and find_col(cols, date_cands)
                and any(
                    k in tl for k in ("hoa_don", "don_hang", "ban_hang", "so_ban_hang")
                )
            ):
                header_cands.append(tname)

    def _detail_sort_key(t: str) -> tuple[int, int, str]:
        dcols = {c.lower() for c in context.tables[t].columns}
        has_cost = int(
            bool(dcols & {"gia_von_luc_ban", "gia_von", "don_gia_von", "gia_von_sp"})
        )
        return (-has_cost, len(t), t)

    if header_cands and detail_cands:
        header_cands.sort(
            key=lambda t: (
                0
                if any(k in t.lower() for k in ("hoa_don", "don_hang", "so_ban_hang"))
                else 1,
                int("tong_hop" in t.lower()),
                t,
            )
        )
        detail_cands.sort(key=_detail_sort_key)
        return header_cands[0], detail_cands[0]
    if detail_cands:
        detail_cands.sort(key=_detail_sort_key)
        return None, detail_cands[0]
    return None, None


def extract_profit_schema(context: BusinessContext) -> ProfitSchema:
    """Resolve sales-line COGS frames, product name directory, and operating expenses."""
    has_any_cost = _schema_has_any_cost(context)
    exp_df, has_ambig_exp = _select_expense_table(context)
    prod_name_map = _build_product_name_map(context)

    h_name, d_name = _select_header_and_detail_tables(context)
    if not d_name or d_name not in context.tables:
        return ProfitSchema(
            lines_df=None,
            pid_col=None,
            line_name_col=None,
            prod_name_map=prod_name_map,
            exp_df=exp_df,
            has_any_cost=has_any_cost,
            has_line_cost=False,
            has_ambiguous_expense=has_ambig_exp,
            min_ym=None,
            max_ym=None,
        )

    d_df = context.tables[d_name]
    dcols = {c.lower(): c for c in d_df.columns}
    line_cost_col = find_col(
        dcols, ("gia_von_luc_ban", "gia_von", "don_gia_von", "gia_von_sp")
    )
    line_rev_col = find_col(dcols, ("thanh_tien", "tri_gia", "tong_tien"))
    line_qty_col = find_col(dcols, ("so_luong", "slg", "sl", "qty", "quantity"))
    pid_col = find_col(
        dcols, ("ma_hang_hoa", "ma_hang", "ma_sp", "ma_sku", "product_id", "id_sp")
    )
    line_name_col = find_col(
        dcols,
        (
            "ten_hang_hoa",
            "ten_hang",
            "ten_san_pham",
            "chi_tiet_hang",
            "ten_sp",
            "mon",
            "ten_goi",
        ),
    )

    if not line_cost_col or not line_rev_col or not line_qty_col:
        return ProfitSchema(
            lines_df=None,
            pid_col=pid_col,
            line_name_col=line_name_col,
            prod_name_map=prod_name_map,
            exp_df=exp_df,
            has_any_cost=has_any_cost,
            has_line_cost=False,
            has_ambiguous_expense=has_ambig_exp,
            min_ym=None,
            max_ym=None,
        )

    date_cands = (
        "ngay_ban",
        "ngay_lap",
        "ngay_dat_hang",
        "ngay_dat",
        "ngay_hd",
        "ngay_tao",
        "ngay",
        "thoi_gian",
        "thoi_gian_lap",
        "gio_tao_don",
        "date",
    )
    status_cands = (
        "trang_thai",
        "tinh_trang",
        "tinh_trang_don",
        "trang_thai_don_hang",
        "xu_ly",
        "status",
    )
    oid_cands = (
        "order_id",
        "ma_hoa_don",
        "ma_giao_dich",
        "ma_don_hang",
        "ma_don",
        "ma_chung_tu",
        "so_phieu",
        "so_ct",
        "ma_bill",
    )

    if h_name and h_name in context.tables:
        h_df = context.tables[h_name]
        hcols = {c.lower(): c for c in h_df.columns}
        h_date = find_col(hcols, date_cands)
        h_status = find_col(hcols, status_cands)
        avail_h_oids = [hcols[c] for c in oid_cands if c in hcols]
        avail_d_oids = [dcols[c] for c in oid_cands if c in dcols]
        h_oid: str | None = None
        d_oid: str | None = None
        best_overlap = 0
        for hc in avail_h_oids:
            h_vals = set(h_df[hc].dropna().astype(str).str.strip())
            for dc in avail_d_oids:
                overlap = int(d_df[dc].astype(str).str.strip().isin(h_vals).sum())
                if overlap > best_overlap:
                    best_overlap = overlap
                    h_oid, d_oid = hc, dc
        if not h_date or not h_oid or not d_oid:
            return ProfitSchema(
                lines_df=None,
                pid_col=pid_col,
                line_name_col=line_name_col,
                prod_name_map=prod_name_map,
                exp_df=exp_df,
                has_any_cost=has_any_cost,
                has_line_cost=False,
                has_ambiguous_expense=has_ambig_exp,
                min_ym=None,
                max_ym=None,
            )
        h_work = pd.DataFrame(
            {
                "_oid": h_df[h_oid].astype(str).str.strip(),
                "_order_ts": parse_mixed_excel_dates(h_df[h_date]),
                "_status_norm": (
                    h_df[h_status].astype(str).map(strip_accents_norm)
                    if h_status
                    else ""
                ),
            }
        ).drop_duplicates(subset=["_oid"], keep="first")

        all_days = h_work["_order_ts"].dropna()
        min_ym = all_days.min().strftime("%Y-%m") if not all_days.empty else None
        max_ym = all_days.max().strftime("%Y-%m") if not all_days.empty else None

        active_h = h_work[
            ~h_work["_status_norm"]
            .astype(str)
            .str.contains(_NON_ACTIVE_ORDER_STATUS_RE, regex=True)
        ]
        d_work = pd.DataFrame(
            {
                "_oid": d_df[d_oid].astype(str).str.strip(),
                "_rev": to_numeric_series(d_df[line_rev_col]),
                "_cogs": to_numeric_series(d_df[line_cost_col])
                * to_numeric_series(d_df[line_qty_col]),
                "_pid": d_df[pid_col].astype(str).str.strip() if pid_col else "",
                "_dname": d_df[line_name_col].astype(str).str.strip()
                if line_name_col
                else "",
            }
        )
        merged = d_work.merge(active_h[["_oid", "_order_ts"]], on="_oid", how="inner")
    else:
        d_date = find_col(dcols, date_cands)
        d_status = find_col(dcols, status_cands)
        if not d_date:
            return ProfitSchema(
                lines_df=None,
                pid_col=pid_col,
                line_name_col=line_name_col,
                prod_name_map=prod_name_map,
                exp_df=exp_df,
                has_any_cost=has_any_cost,
                has_line_cost=False,
                has_ambiguous_expense=has_ambig_exp,
                min_ym=None,
                max_ym=None,
            )
        ts_all = parse_mixed_excel_dates(d_df[d_date])
        all_days = ts_all.dropna()
        min_ym = all_days.min().strftime("%Y-%m") if not all_days.empty else None
        max_ym = all_days.max().strftime("%Y-%m") if not all_days.empty else None
        status_norm = (
            d_df[d_status].astype(str).map(strip_accents_norm)
            if d_status
            else pd.Series("", index=d_df.index)
        )
        active_mask = ~status_norm.str.contains(_NON_ACTIVE_ORDER_STATUS_RE, regex=True)
        merged = pd.DataFrame(
            {
                "_order_ts": ts_all[active_mask],
                "_rev": to_numeric_series(d_df.loc[active_mask, line_rev_col]),
                "_cogs": (
                    to_numeric_series(d_df.loc[active_mask, line_cost_col])
                    * to_numeric_series(d_df.loc[active_mask, line_qty_col])
                ),
                "_pid": d_df.loc[active_mask, pid_col].astype(str).str.strip()
                if pid_col
                else "",
                "_dname": d_df.loc[active_mask, line_name_col].astype(str).str.strip()
                if line_name_col
                else "",
            }
        )

    merged["_gp"] = merged["_rev"] - merged["_cogs"]
    merged["_ym"] = merged["_order_ts"].dt.strftime("%Y-%m")
    return ProfitSchema(
        lines_df=merged,
        pid_col=pid_col,
        line_name_col=line_name_col,
        prod_name_map=prod_name_map,
        exp_df=exp_df,
        has_any_cost=True,
        has_line_cost=True,
        has_ambiguous_expense=has_ambig_exp,
        min_ym=min_ym,
        max_ym=max_ym,
    )


def resolve_profit_query(
    question: str,
    context: BusinessContext,
    as_of_date: str | None = None,
) -> tuple[str, Any, str | None]:
    """Deterministically resolve monthly gross profit, net profit, and top-profit product queries."""
    q_norm = normalize_debt_text(question)

    if any(k in q_norm for k in _EXCLUDED_PROFIT_TERMS):
        return "abstain", None, "Ngoài phạm vi lợi nhuận gộp/ròng theo tháng"

    is_top_product = bool(
        re.search(r"\b(?:mat\s+hang|san\s+pham|hang\s+hoa|hang|mon)\b", q_norm)
        and (
            ("loi nhat" in q_norm)
            or (
                any(
                    k in q_norm
                    for k in ("lai gop", "loi gop", "loi nhuan gop", "loi nhuan")
                )
                and any(
                    w in q_norm for w in ("cao nhat", "lon nhat", "nhieu nhat", "nhut")
                )
            )
        )
    )
    is_net = any(
        k in q_norm
        for k in (
            "lai rong",
            "loi nhuan rong",
            "tru het chi phi",
            "sau chi phi",
            "sau khi tru chi phi",
        )
    )
    is_gross = any(k in q_norm for k in ("lai gop", "loi gop", "loi nhuan gop"))

    if not (is_top_product or is_net or is_gross):
        return "abstain", None, "Không khớp mẫu câu hỏi lợi nhuận"

    month_ym = extract_month_ym(q_norm, as_of_date)
    if not month_ym:
        return "abstain", None, "Không xác định được tháng cho câu hỏi lợi nhuận"

    schema = extract_profit_schema(context)
    if not schema.has_any_cost:
        return (
            "unanswerable",
            None,
            "Doanh nghiệp không ghi nhận giá vốn / chi phí để tính lợi nhuận",
        )

    if is_net and schema.exp_df is None:
        if schema.has_ambiguous_expense:
            return (
                "abstain",
                None,
                "Chi phí nằm trong sổ quỹ hỗn hợp cần kiểm chứng thêm",
            )
        return (
            "unanswerable",
            None,
            "Doanh nghiệp không có sổ chi phí vận hành để tính lãi ròng",
        )

    if not schema.has_line_cost or schema.lines_df is None:
        return (
            "abstain",
            None,
            "Thiếu cột giá vốn lịch sử trên chi tiết đơn để chốt lợi nhuận tháng",
        )

    if (schema.min_ym and month_ym < schema.min_ym) or (
        schema.max_ym and month_ym > schema.max_ym
    ):
        return (
            "unanswerable",
            None,
            "Tháng yêu cầu nằm hoàn toàn ngoài dữ liệu bán hàng của doanh nghiệp",
        )

    cutoff_eod = month_cutoff_eod(month_ym, as_of_date)
    lines = schema.lines_df
    sub = lines[(lines["_ym"] == month_ym) & (lines["_order_ts"] <= cutoff_eod)]
    if sub.empty:
        return "unanswerable", None, "Không có dữ liệu bán hàng trong tháng yêu cầu"

    if is_top_product:
        if schema.pid_col and sub["_pid"].astype(str).str.strip().ne("").any():
            grouped = sub.groupby("_pid")["_gp"].sum().sort_values(ascending=False)
            top_pid = str(grouped.index[0])
            top_name = schema.prod_name_map.get(top_pid)
            if not top_name:
                names = sub.loc[sub["_pid"] == top_pid, "_dname"].dropna()
                top_name = str(names.iloc[0]) if not names.empty else top_pid
        elif schema.line_name_col:
            grouped = sub.groupby("_dname")["_gp"].sum().sort_values(ascending=False)
            top_name = str(grouped.index[0])
        else:
            return "abstain", None, "Không xác định được tên sản phẩm"
        context.last_code_path = "profit_analytics.top_profit_product"
        return "string", top_name, None

    gross_total = float(sub["_gp"].sum())
    if is_net:
        assert schema.exp_df is not None
        exp = schema.exp_df
        exp_sub = exp[(exp["_exp_ym"] == month_ym) & (exp["_exp_ts"] <= cutoff_eod)]
        net_total = gross_total - float(exp_sub["_exp_val"].sum())
        context.last_code_path = "profit_analytics.net_profit"
        return "number", int(round(net_total)), None

    context.last_code_path = "profit_analytics.gross_profit"
    return "number", int(round(gross_total)), None
