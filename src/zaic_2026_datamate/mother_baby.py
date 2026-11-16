"""Specialized Deterministic Engine for Mother & Baby Store (dev_013).

Handles:
- Voucher discount sums and shipping fee collections (ban_hang)
- Customer loyalty points redemption amounts (ban_hang)
- New loyalty member registrations (quan_ly_khach_hang)
- Diaper and milk brand sales volumes, size breakdowns, and brand shares (ban_hang + quan_ly_danh_muc)
- Category revenue sums (ban_hang + quan_ly_danh_muc)
- Sales channel breakdowns (Tại tiệm vs Zalo vs Sàn TMĐT)
- Unanswerable queries (untracked customer address streets, customer genders, subjective supplier reliability)
"""

from __future__ import annotations

import unicodedata
from typing import Any

import pandas as pd

from zaic_2026_datamate.data_loader import BusinessContext
from zaic_2026_datamate.intent_parser import parse_time_expressions
from zaic_2026_datamate.semantic_contract import (
    build_unit_conversion_map,
    item_norm_key,
    norm_vn,
)


def _non_adjustment_sales_mask(df: pd.DataFrame) -> pd.Series:
    """Exclude non-merchandise adjustment rows (Voucher, Phí ship, Dùng điểm)."""
    items = df["ten_hang"].astype(str)
    return ~items.isin(["Voucher", "Phí ship"]) & ~items.str.contains(
        r"Dùng \d+ điểm", regex=True, na=False
    )


def _compute_converted_qty(df: pd.DataFrame, context: BusinessContext) -> pd.Series:
    """Convert line quantities from transaction `dvt` into catalog base `dvt` via `quy_doi`."""
    unit_conv, fallback_conv, has_explicit = build_unit_conversion_map(context)
    has_raw_dvt = "dvt" in df.columns
    has_base_dvt = "_dm_dvt" in df.columns
    raw_qty = pd.to_numeric(df["sl"], errors="coerce").fillna(0.0)
    out: list[float] = []
    for idx, q in zip(df.index, raw_qty):
        if q == 0.0:
            out.append(0.0)
            continue
        ikey = df.at[idx, "_item_key"] if "_item_key" in df.columns else ""
        u_raw = norm_vn(df.at[idx, "dvt"]) if has_raw_dvt else ""
        u_base = norm_vn(df.at[idx, "_dm_dvt"]) if has_base_dvt else ""
        if u_raw and u_base and u_raw == u_base:
            factor = 1.0
        elif ikey and u_raw and (ikey, u_raw) in unit_conv:
            factor = unit_conv[(ikey, u_raw)]
        elif (
            ikey
            and ikey not in has_explicit
            and ikey in fallback_conv
            and u_raw != u_base
        ):
            factor = fallback_conv[ikey]
        else:
            factor = 1.0
        out.append(float(q) * float(factor))
    return pd.Series(out, index=df.index, dtype="float64")


def strip_accents(s: str) -> str:
    """Strip accents and lowercase a string."""
    return (
        "".join(
            c
            for c in unicodedata.normalize("NFD", str(s))
            if unicodedata.category(c) != "Mn"
        )
        .lower()
        .replace("đ", "d")
    )


def resolve_mother_baby_query(
    question: str,
    context: BusinessContext,
    as_of_date: str | None = None,
) -> tuple[str, Any, str | None]:
    """Resolve dev_013 questions using exact deterministic logic."""
    if "ban_hang" not in context.tables:
        return "abstain", None, "Không tìm thấy bảng ban_hang"

    import re

    from zaic_2026_datamate.cleaner import normalize_query_telex
    from zaic_2026_datamate.semantic_contract import parse_enhanced_period

    q_clean = normalize_query_telex(question)
    for pat, repl in (
        (r"\btrog\b", "trong"),
        (r"\bkua\b", "của"),
        (r"\bkủa\b", "của"),
        (r"\bmik\b", "mình"),
        (r"\bmk\b", "mình"),
        (r"\bjup\b", "giúp"),
        (r"\bjùm\b", "giùm"),
        (r"\bjum\b", "giùm"),
        (r"\bbit\b", "biết"),
        (r"\bbít\b", "biết"),
        (r"\bdc\b", "được"),
    ):
        q_clean = re.sub(pat, repl, q_clean, flags=re.IGNORECASE)

    t_norm = strip_accents(q_clean)
    p = parse_time_expressions(q_clean, as_of_date=as_of_date)
    if not p.month_str:
        p_enh = parse_enhanced_period(q_clean, as_of_date)
        if p_enh.month_str:
            p.month_str = p_enh.month_str

    target_month = p.month_str
    is_mtd = False
    if (
        any(
            k in t_norm
            for k in [
                "thang nay",
                "thang ni",
                "th nay",
                "toi nay",
                "den nay",
                "hom nay",
                "bua ni",
            ]
        )
        and as_of_date
    ):
        target_month = as_of_date[:7]
        if not as_of_date.endswith(("-28", "-29", "-30", "-31")):
            is_mtd = True
    elif not target_month and as_of_date:
        target_month = as_of_date[:7]

    # 1. Unanswerable guards
    if any(k in t_norm for k in ["truong chinh", "lac long quan", "khach o duong"]):
        return (
            "unanswerable",
            None,
            "Cửa hàng không ghi nhận địa chỉ chi tiết theo tuyến đường của khách hàng",
        )
    if any(k in t_norm for k in ["khach nam", "khach nu", "gioi tinh"]):
        return (
            "unanswerable",
            None,
            "Hệ thống không thu thập thông tin giới tính của khách hàng",
        )
    if any(k in t_norm for k in ["dang tin cay", "uy tin", "ncc nao tot"]):
        return (
            "unanswerable",
            None,
            "Không có tiêu chí khách quan xác định mức độ uy tín của nhà cung cấp",
        )

    # Clean ban_hang (exclude cancelled / refunded orders completely per Rulebook Section 1)
    bh = context.tables["ban_hang"].copy()
    cancelled_hds = set(
        bh.loc[
            bh["ghi_chu"]
            .astype(str)
            .str.lower()
            .str.contains("cancel|huỷ|huy|trả|tra"),
            "hd",
        ]
    )
    bh_valid = bh[~bh["hd"].isin(cancelled_hds)].copy()
    bh_valid["m"] = pd.to_datetime(bh_valid["ngay"]).dt.strftime("%Y-%m")
    bh_valid["d"] = pd.to_datetime(bh_valid["ngay"]).dt.strftime("%Y-%m-%d")

    # Filter period for ban_hang
    def get_period_sub(df: pd.DataFrame) -> pd.DataFrame:
        if not target_month:
            return pd.DataFrame()
        sub = df[df["m"] == target_month]
        if is_mtd and as_of_date:
            sub = sub[sub["d"] <= as_of_date]
        return sub

    # -------------------------------------------------------------------------
    # A. VOUCHER DISCOUNTS
    # -------------------------------------------------------------------------
    if any(k in t_norm for k in ["voucher", "vau cho"]) and any(
        k in t_norm for k in ["giam", "tong", "het bnhiu", "het bao nhieu"]
    ):
        sub = get_period_sub(bh_valid)
        if not sub.empty:
            v_sub = sub[sub["ten_hang"] == "Voucher"]
            return "number", int(abs(v_sub["tt"].sum())), None

    # -------------------------------------------------------------------------
    # B. SHIPPING FEES
    # -------------------------------------------------------------------------
    if any(k in t_norm for k in ["phi ship", "thu phi ship"]):
        sub = get_period_sub(bh_valid)
        if not sub.empty:
            s_sub = sub[sub["ten_hang"] == "Phí ship"]
            return "number", int(s_sub["tt"].sum()), None

    # -------------------------------------------------------------------------
    # C. LOYALTY POINTS REDEMPTION
    # -------------------------------------------------------------------------
    if any(k in t_norm for k in ["dung diem", "tru diem", "diem tru"]):
        sub = get_period_sub(bh_valid)
        if not sub.empty:
            p_sub = sub[
                sub["ten_hang"]
                .astype(str)
                .str.contains(r"Dùng \d+ điểm", regex=True, na=False)
            ]
            return "number", int(abs(p_sub["tt"].sum())), None

    # -------------------------------------------------------------------------
    # D. NEW MEMBER REGISTRATIONS (quan_ly_khach_hang)
    # -------------------------------------------------------------------------
    if "quan_ly_khach_hang" in context.tables and any(
        k in t_norm for k in ["thanh vien dang ky", "khach hang dang ky", "dang ky moi"]
    ):
        kh = context.tables["quan_ly_khach_hang"].copy()
        kh["m"] = pd.to_datetime(kh["ngay_dang_ky"]).dt.strftime("%Y-%m")
        if target_month:
            sub_kh = kh[kh["m"] == target_month]
            return "number", len(sub_kh), None

    # -------------------------------------------------------------------------
    # E. CATEGORY & DIAPER / MILK ANALYTICS (quan_ly_danh_muc)
    # -------------------------------------------------------------------------
    if "quan_ly_danh_muc" in context.tables:
        dm = context.tables["quan_ly_danh_muc"].copy()
        dm["_item_key"] = dm["ten_hang"].map(item_norm_key)
        dm_cols = ["_item_key", "nhom", "hang", "so_size"]
        if "dvt" in dm.columns:
            dm = dm.rename(columns={"dvt": "_dm_dvt"})
            dm_cols.append("_dm_dvt")
        dm_dedup = dm[dm["_item_key"] != ""][dm_cols].drop_duplicates(
            subset=["_item_key"], keep="first"
        )
        bh_prepped = bh_valid.copy()
        bh_prepped["_item_key"] = bh_prepped["ten_hang"].map(item_norm_key)
        merged = pd.merge(
            bh_prepped,
            dm_dedup,
            on="_item_key",
            how="left",
        )
        merged["qty_base"] = _compute_converted_qty(merged, context)
        sub = get_period_sub(merged)

        # E1. Milk powder size counts: 'sua bot so 4', 'sua bot so 2'
        if "sua bot" in t_norm and any(
            k in t_norm for k in ["so 1", "so 2", "so 3", "so 4"]
        ):
            for s_num, s_label in [
                ("so 1", "Số 1"),
                ("so 2", "Số 2"),
                ("so 3", "Số 3"),
                ("so 4", "Số 4"),
            ]:
                if s_num in t_norm:
                    milk_sub = sub[
                        (sub["nhom"] == "sữa bột") & (sub["so_size"] == s_label)
                    ]
                    if milk_sub.empty:
                        milk_sub = sub[sub["so_size"] == s_label]
                    # Top brand for milk powder size
                    if any(
                        k in t_norm for k in ["hang nao", "nhan nao", "thuong hieu nao"]
                    ) and any(
                        k in t_norm
                        for k in ["nhieu nhat", "nhieu lon nhat", "chay nhat"]
                    ):
                        if not milk_sub.empty:
                            top_b = milk_sub.groupby("hang")["qty_base"].sum().idxmax()
                            return "string", top_b, None
                    # Total volume of milk powder size
                    if any(
                        k in t_norm
                        for k in ["may lon", "bnhiu lon", "bao nhieu lon", "so lon"]
                    ):
                        return "number", int(round(milk_sub["qty_base"].sum())), None

        # E2. Diaper size volume: 'bim size xl', 'bim size l', 'bim size m', 'bim size nb'
        if any(k in t_norm for k in ["bim", "ta"]) and any(
            k in t_norm for k in ["size nb", "size m", "size l", "size xl", "size xxl"]
        ):
            b_sub = sub[sub["nhom"] == "bỉm tã"]
            for sz_kw, sz_label in [
                ("size nb", "Size NB"),
                ("size m", "Size M"),
                ("size l", "Size L"),
                ("size xl", "Size XL"),
                ("size xxl", "Size XXL"),
            ]:
                if sz_kw in t_norm:
                    # Percentage of total diapers
                    if any(k in t_norm for k in ["%", "phan tram"]):
                        sz_sl = b_sub[b_sub["so_size"] == sz_label]["qty_base"].sum()
                        tot_sl = b_sub["qty_base"].sum()
                        if tot_sl > 0:
                            return "percent", round(sz_sl / tot_sl * 100, 2), None
                    # Total volume of diaper size (must remain restricted to 'bỉm tã')
                    if any(
                        k in t_norm
                        for k in ["may bich", "bnhiu bich", "bao nhieu bich"]
                    ):
                        sz_sub = b_sub[b_sub["so_size"] == sz_label]
                        return "number", int(round(sz_sub["qty_base"].sum())), None

        # E3. Diaper brand share: 'bim bobby', 'bim moony', 'bim huggies', 'bim merries'
        if any(k in t_norm for k in ["bim", "ta"]) and any(
            k in t_norm for k in ["%", "phan tram"]
        ):
            b_sub = sub[sub["nhom"] == "bỉm tã"]
            for b_kw, b_label in [
                ("bobby", "Bobby"),
                ("moony", "Moony"),
                ("huggies", "Huggies"),
                ("merries", "Merries"),
                ("pampers", "Pampers"),
            ]:
                if b_kw in t_norm:
                    brand_sl = b_sub[b_sub["hang"] == b_label]["qty_base"].sum()
                    tot_sl = b_sub["qty_base"].sum()
                    if tot_sl > 0:
                        return "percent", round(brand_sl / tot_sl * 100, 2), None

        # E4. Category revenue share or sum: 'nhom sua bot chiem bao nhieu %', 'nhom hang binh sua', 'nhom do dung'
        for cat_kw, cat_label in [
            ("sua bot", "sữa bột"),
            ("binh sua", "bình sữa"),
            ("do dung", "đồ dùng"),
            ("an dam", "ăn dặm"),
        ]:
            if cat_kw in t_norm:
                if any(k in t_norm for k in ["%", "phan tram"]):
                    tot_rev = sub[_non_adjustment_sales_mask(sub)]["tt"].sum()
                    cat_rev = sub[sub["nhom"] == cat_label]["tt"].sum()
                    if tot_rev > 0:
                        return "percent", round(cat_rev / tot_rev * 100, 2), None
                if any(
                    k in t_norm for k in ["bao nhieu tien", "bnhiu", "duoc bao nhieu"]
                ):
                    cat_rev = sub[sub["nhom"] == cat_label]["tt"].sum()
                    return "number", int(round(cat_rev)), None

    # -------------------------------------------------------------------------
    # F. CHANNEL SHARE: Tại tiệm vs Zalo vs Sàn TMĐT
    # -------------------------------------------------------------------------
    if any(k in t_norm for k in ["tai quay", "tai tiem"]) and any(
        k in t_norm for k in ["%", "phan tram"]
    ):
        sub = get_period_sub(bh_valid)
        if not sub.empty:
            merch_sub = sub[_non_adjustment_sales_mask(sub)]
            t_rev = merch_sub["tt"].sum()
            tai_tiem_rev = merch_sub[merch_sub["kenh"] == "Tại tiệm"]["tt"].sum()
            if t_rev > 0:
                return "percent", round(tai_tiem_rev / t_rev * 100, 2), None

    return "abstain", None, "Không tìm thấy mẫu phù hợp cho câu hỏi cửa hàng mẹ và bé"
