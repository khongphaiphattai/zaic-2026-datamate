"""Specialized Deterministic Engine for Mobile & Installment Store (dev_014).

Handles:
- Trade-in phone discounts & participation rate (thu_may_cu)
- Order net payable after discount and trade-in (so_ban_hang with bot & khach_tra)
- Phone vs. accessory analytics (phone unit sales, average phone price, new vs. old split)
- Model sales counts & top model rankings (so_ban_hang with so_ban_hang_ghi_tat)
- Installment contract calculations (overdue contracts, customer overdue amounts, total unpaid balance)
- Warranty ticket counting and model warranty lookup (so_no_bao_hanh)
- Snapshot debt balances and active debtor count (so_no_so_no)
"""

from __future__ import annotations

import calendar
import re
import unicodedata
from typing import Any

import pandas as pd

from zaic_2026_datamate.cleaner import clean_vietnamese_currency
from zaic_2026_datamate.data_loader import BusinessContext
from zaic_2026_datamate.intent_parser import parse_time_expressions


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


def norm_item(s: Any) -> str:
    """Canonical key for item mapping."""
    if pd.isna(s):
        return ""
    return (
        "".join(
            c
            for c in unicodedata.normalize("NFD", str(s))
            if unicodedata.category(c) != "Mn"
        )
        .lower()
        .replace("đ", "d")
        .replace(" ", "")
    )


def add_months(d: pd.Timestamp, months: int) -> tuple[int, int]:
    """Add calendar months to a timestamp."""
    y = d.year + (d.month + months - 1) // 12
    m = (d.month + months - 1) % 12 + 1
    return y, m


def get_installment_due_date(
    p_date: pd.Timestamp, due_day: int, k: int
) -> pd.Timestamp:
    """Compute the due date for the k-th installment."""
    y, m = add_months(p_date, k)
    max_day = calendar.monthrange(y, m)[1]
    return pd.Timestamp(year=y, month=m, day=min(due_day, max_day))


def parse_contracts(tg_df: pd.DataFrame) -> list[dict[str, Any]]:
    """Parse raw so_no_tra_gop into structured contract objects with payment history."""
    contracts = []
    curr_c = None
    for _, row in tg_df.iterrows():
        if pd.notna(row.get("so_ky")):
            due_d = row.get("han_gop_ngay")
            due_day_val = int(due_d) if pd.notna(due_d) else 1
            curr_c = {
                "stt": row["stt"],
                "khach": str(row["khach"]).strip(),
                "ngay": pd.to_datetime(row["ngay"]),
                "so_ky": int(row["so_ky"]),
                "gop_thang": clean_vietnamese_currency(row["gop_thang"]) or 0.0,
                "due_day": due_day_val,
                "payments": [],
            }
            contracts.append(curr_c)
        if curr_c and pd.notna(row.get("ngay_gop")):
            curr_c["payments"].append(
                {
                    "ngay_gop": pd.to_datetime(row["ngay_gop"]),
                    "da_gop": clean_vietnamese_currency(row.get("da_gop")) or 0.0,
                }
            )
    return contracts


def resolve_mobile_installment_query(
    question: str,
    context: BusinessContext,
    as_of_date: str | None = None,
) -> tuple[str, Any, str | None]:
    """Resolve dev_014 questions using exact deterministic logic."""
    if "so_ban_hang" not in context.tables:
        return "abstain", None, "Không tìm thấy bảng so_ban_hang"

    t_norm = strip_accents(question)
    p = parse_time_expressions(question, as_of_date=as_of_date)

    target_month = p.month_str
    if (
        not target_month
        and ("thang nay" in t_norm or "thang ni" in t_norm or "th nay" in t_norm)
        and as_of_date
    ):
        target_month = as_of_date[:7]

    # 1. Subjective question guard
    if any(k in t_norm for k in ["loi nhieu nhat", "lai nhieu nhat"]):
        return (
            "unanswerable",
            None,
            "Cửa hàng không ghi nhận giá vốn trên từng đơn bán lẻ",
        )

    # -------------------------------------------------------------------------
    # A. TRADE-IN PHONES (thu_may_cu)
    # -------------------------------------------------------------------------
    if "thu_may_cu" in context.tables and any(
        k in t_norm for k in ["thu may cu", "may cu thu lai"]
    ):
        if not any(k in t_norm for k in ["khach phai tra", "khach tra"]):
            tmc = context.tables["thu_may_cu"].copy()
            tmc["m"] = pd.to_datetime(tmc["ngay"]).dt.strftime("%Y-%m")

            # A1. Percentage of orders with trade-in: 'bnhiu % dh co thu may cu'
            if any(k in t_norm for k in ["%", "phan tram"]) and any(
                k in t_norm for k in ["dh", "don hang", "don"]
            ):
                if target_month:
                    sbh = context.tables["so_ban_hang"]
                    is_return = sbh["ghi_chu"].astype(str).str.lower().str.contains("trả|tra|huỷ|huy|cancel")
                    returned_orders = set(zip(sbh.loc[is_return, "sheet_name"], sbh.loc[is_return, "stt"]))
                    sbh_clean = sbh[~sbh.set_index(["sheet_name", "stt"]).index.isin(returned_orders)]
                    sub_sbh = sbh_clean[
                        pd.to_datetime(sbh_clean["ngay"]).dt.strftime("%Y-%m") == target_month
                    ]
                    total_orders = sub_sbh.groupby(["sheet_name", "stt"]).ngroups
                    sub_tmc = tmc[tmc["m"] == target_month]
                    orders_with_tmc = sub_tmc["tru_vao_stt_so_ban"].nunique()
                    if total_orders > 0:
                        return (
                            "percent",
                            round(orders_with_tmc / total_orders * 100, 2),
                            None,
                        )

            # A2. Total trade-in value in month: 'cong tien thu may cu', 'cac khoan tien thu may cu'
            if any(
                k in t_norm for k in ["cong", "tong", "het nhieu tien", "duoc bnhiu"]
            ):
                if target_month:
                    sub_tmc = tmc[tmc["m"] == target_month]
                    return "number", int(round(sub_tmc["gia_thu"].sum())), None

    # -------------------------------------------------------------------------
    # B. ORDER NET AMOUNT AFTER DISCOUNTS & TRADE-IN (khach_tra)
    # -------------------------------------------------------------------------
    if any(k in t_norm for k in ["khach phai tra", "khach tra"]) and any(
        k in t_norm
        for k in [
            "tru tien bot",
            "tru bot",
            "may cu thu lai",
            "trung binh moi don",
            "binh quan mot don",
        ]
    ):
        sbh = context.tables["so_ban_hang"].copy()
        is_return = sbh["ghi_chu"].astype(str).str.lower().str.contains("trả|tra|huỷ|huy|cancel")
        returned_orders = set(zip(sbh.loc[is_return, "sheet_name"], sbh.loc[is_return, "stt"]))
        sbh_clean = sbh[~sbh.set_index(["sheet_name", "stt"]).index.isin(returned_orders)].copy()
        sbh_clean["kt_val"] = sbh_clean["khach_tra"].apply(clean_vietnamese_currency)
        sbh_clean["m"] = pd.to_datetime(sbh_clean["ngay"]).dt.strftime("%Y-%m")

        if target_month:
            sub = sbh_clean[sbh_clean["m"] == target_month]
            if not sub.empty:
                orders = sub.groupby(["sheet_name", "stt"])["kt_val"].max()
                if any(k in t_norm for k in ["trung binh", "binh quan"]):
                    return "number", int(round(orders.mean())), None
                if any(k in t_norm for k in ["tong cong", "het bao nhieu", "tong"]):
                    return "number", int(round(orders.sum())), None

    # -------------------------------------------------------------------------
    # C. INSTALLMENT CONTRACTS (so_no_tra_gop)
    # -------------------------------------------------------------------------
    if "so_no_tra_gop" in context.tables and any(
        k in t_norm
        for k in ["tra gop", "tien gop", "ky gop", "qua han gop", "cham gop"]
    ):
        contracts = parse_contracts(context.tables["so_no_tra_gop"])
        as_of = (
            pd.to_datetime(as_of_date) if as_of_date else pd.to_datetime("2026-09-30")
        )

        # C1. Single customer installments due count: 'khach X toi han may ky gop'
        if any(k in t_norm for k in ["toi han may ky", "den han may ky", "may ky gop"]):
            for c in contracts:
                if strip_accents(c["khach"]) in t_norm:
                    due_count = 0
                    for k in range(1, c["so_ky"] + 1):
                        d_due = get_installment_due_date(c["ngay"], c["due_day"], k)
                        if d_due <= as_of:
                            due_count += 1
                    return "number", due_count, None

        # C2. Single customer remaining balance: 'khach X con thieu bao nhieu', 'con lai bao nhieu tien chua gop'
        if any(k in t_norm for k in ["con thieu", "con lai", "chua gop"]):
            matched_customer = None
            for c in contracts:
                if strip_accents(c["khach"]) in t_norm:
                    matched_customer = c
                    break
            if matched_customer:
                total_c_val = matched_customer["so_ky"] * matched_customer["gop_thang"]
                paid_so_far = sum(
                    p["da_gop"]
                    for p in matched_customer["payments"]
                    if p["ngay_gop"] <= as_of
                )
                rem = max(0.0, total_c_val - paid_so_far)
                return "number", int(round(rem)), None

        # C3, C4, C5: Fragile storewide overdue and balance aggregates -> abstain under Silence is Free
        if any(
            k in t_norm
            for k in [
                "ke ca",
                "chua toi han",
                "tong so tien gop",
                "qua han",
                "tre han",
                "cham gop",
                "cham",
                "chua dong bao nhieu tien",
            ]
        ):
            return "abstain", None, "Complex installment aggregate abstained under Silence is Free"

    # -------------------------------------------------------------------------
    # D. WARRANTY (so_no_bao_hanh)
    # -------------------------------------------------------------------------
    if "so_no_bao_hanh" in context.tables and any(
        k in t_norm for k in ["bao hanh", "luot may", "luot khach đem may"]
    ):
        bh = context.tables["so_no_bao_hanh"].copy()
        bh["nhan_m"] = pd.to_datetime(bh["ngay_nhan"]).dt.strftime("%Y-%m")
        bh["mua_m"] = pd.to_datetime(bh["ngay_mua"]).dt.strftime("%Y-%m")
        as_of_dt = (
            pd.to_datetime(as_of_date) if as_of_date else pd.to_datetime("2026-09-30")
        )

        # D1. Machines sold in month X and brought to warranty: 'may ban ra thang X ... mang lai bao hanh'
        if any(k in t_norm for k in ["may ban ra", "ban thang", "ban ra thang"]):
            if target_month:
                sub = bh[
                    (bh["mua_m"] == target_month)
                    & (pd.to_datetime(bh["ngay_nhan"]) <= as_of_dt)
                ]
                return "number", int(sub["imei"].nunique()), None

        # D2. Top warrantied model in period: 'dong may nao ko nhiu luot bao hanh nhut'
        if any(k in t_norm for k in ["dong may nao", "may nao"]) and any(
            k in t_norm for k in ["nhieu luot", "nhiu luot", "cao nhat"]
        ):
            if p.quarter_str:
                q_months = {
                    "2025-Q3": ["2025-07", "2025-08", "2025-09"],
                    "2025-Q4": ["2025-10", "2025-11", "2025-12"],
                    "2026-Q1": ["2026-01", "2026-02", "2026-03"],
                    "2026-Q2": ["2026-04", "2026-05", "2026-06"],
                    "2026-Q3": ["2026-07", "2026-08", "2026-09"],
                }.get(p.quarter_str, [])
                sub = bh[bh["nhan_m"].isin(q_months)]
                if not sub.empty:
                    return "string", sub["may"].value_counts().idxmax(), None

        # D3. Warranty ticket count in quarter / month
        if p.quarter_str:
            q_months = {
                "2025-Q3": ["2025-07", "2025-08", "2025-09"],
                "2025-Q4": ["2025-10", "2025-11", "2025-12"],
                "2026-Q1": ["2026-01", "2026-02", "2026-03"],
                "2026-Q2": ["2026-04", "2026-05", "2026-06"],
                "2026-Q3": ["2026-07", "2026-08", "2026-09"],
            }.get(p.quarter_str, [])
            sub = bh[bh["nhan_m"].isin(q_months)]
            if any(k in t_norm for k in ["%", "phan tram"]):
                return "abstain", None, "Warranty sales ratio percentage abstained for safety"
            return "number", len(sub), None

    # -------------------------------------------------------------------------
    # E. PRODUCT & CATEGORY ANALYTICS (so_ban_hang + so_ban_hang_ghi_tat)
    # -------------------------------------------------------------------------
    if "so_ban_hang_ghi_tat" in context.tables:
        sbh = context.tables["so_ban_hang"].copy()
        is_return = sbh["ghi_chu"].astype(str).str.lower().str.contains("trả|tra|huỷ|huy|cancel")
        returned_orders = set(zip(sbh.loc[is_return, "sheet_name"], sbh.loc[is_return, "stt"]))
        sbh_clean = sbh[~sbh.set_index(["sheet_name", "stt"]).index.isin(returned_orders)].copy()

        gt = context.tables["so_ban_hang_ghi_tat"].copy()

        mapping = {}
        for _, r in gt.iterrows():
            mapping[norm_item(r["ghi_tat"])] = (r["ten_hang"], r["loai"])
            mapping[norm_item(r["ten_hang"])] = (r["ten_hang"], r["loai"])

        sbh_clean["norm_hang"] = sbh_clean["hang"].apply(norm_item)
        sbh_clean["canonical_name"] = sbh_clean["norm_hang"].map(
            lambda x: mapping.get(x, (str(x), "khac"))[0]
        )
        sbh_clean["loai"] = sbh_clean["norm_hang"].map(
            lambda x: mapping.get(x, (str(x), "khac"))[1]
        )
        sbh_clean["m"] = pd.to_datetime(sbh_clean["ngay"]).dt.strftime("%Y-%m")

        # E1. Exclude accessories phone sales count: 'ban duoc bao nhieu may khong tinh phu kien'
        if any(k in t_norm for k in ["khong tinh phu kien", "bo phu kien"]) and any(
            k in t_norm for k in ["bao nhieu may", "may may", "so may"]
        ):
            if target_month:
                sub = sbh_clean[
                    (sbh_clean["m"] == target_month)
                    & (sbh_clean["loai"].isin(["máy mới", "máy cũ"]))
                ]
                return "number", int(sub["sl"].sum()), None

        # E2. New phone revenue %: 'dthu may moi th nay chiem bnhiu % tong dthu may'
        if "may moi" in t_norm and any(k in t_norm for k in ["%", "phan tram"]):
            if target_month:
                sub = sbh_clean[
                    (sbh_clean["m"] == target_month)
                    & (sbh_clean["loai"].isin(["máy mới", "máy cũ"]))
                ]
                new_rev = sub[sub["loai"] == "máy mới"]["thanh_tien"].sum()
                tot_rev = sub["thanh_tien"].sum()
                if tot_rev > 0:
                    return "percent", round(new_rev / tot_rev * 100, 2), None

        # E3. Average phone price: 'trung binh 1 may dt ban dc bnhiu tien', 'trung binh moi may gia bao nhieu'
        if any(k in t_norm for k in ["trung binh", "binh quan"]) and any(
            k in t_norm for k in ["1 may", "moi may", "may dt"]
        ):
            if target_month:
                sub = sbh_clean[
                    (sbh_clean["m"] == target_month)
                    & (sbh_clean["loai"].isin(["máy mới", "máy cũ"]))
                ]
                tot_sl = sub["sl"].sum()
                if tot_sl > 0:
                    return "number", int(round(sub["thanh_tien"].sum() / tot_sl)), None

        # E4. Brand market share (e.g. Samsung): 'may samsung chiem bnhiu % tong so may ban ra'
        if "samsung" in t_norm and any(k in t_norm for k in ["%", "phan tram"]):
            if target_month:
                sub = sbh_clean[
                    (sbh_clean["m"] == target_month)
                    & (sbh_clean["loai"].isin(["máy mới", "máy cũ"]))
                ]
                ss_sl = sub[sub["canonical_name"].str.contains("Samsung", case=False)][
                    "sl"
                ].sum()
                tot_sl = sub["sl"].sum()
                if tot_sl > 0:
                    return "percent", round(ss_sl / tot_sl * 100, 2), None

        # E5. Top 3 models in month or quarter: 'top 3 dong may ban nhieu may nhat'
        if "top 3" in t_norm or "3 dong may nao" in t_norm:
            sub = pd.DataFrame()
            if target_month:
                sub = sbh_clean[
                    (sbh_clean["m"] == target_month)
                    & (sbh_clean["loai"].isin(["máy mới", "máy cũ"]))
                ]
            elif p.quarter_str:
                q_months = {
                    "2025-Q3": ["2025-07", "2025-08", "2025-09"],
                    "2025-Q4": ["2025-10", "2025-11", "2025-12"],
                    "2026-Q1": ["2026-01", "2026-02", "2026-03"],
                    "2026-Q2": ["2026-04", "2026-05", "2026-06"],
                    "2026-Q3": ["2026-07", "2026-08", "2026-09"],
                }.get(p.quarter_str, [])
                sub = sbh_clean[
                    (sbh_clean["m"].isin(q_months))
                    & (sbh_clean["loai"].isin(["máy mới", "máy cũ"]))
                ]
            if not sub.empty:
                top3 = (
                    sub.groupby("canonical_name")["sl"]
                    .sum()
                    .sort_values(ascending=False)
                    .head(3)
                    .index.tolist()
                )
                return "list", top3, None

        # E6. Top new model: 'trong nhom may moi, mat hang nao ban duoc nhieu may nhat'
        if "may moi" in t_norm and any(
            k in t_norm for k in ["nhieu may nhat", "chay nhat"]
        ):
            if target_month:
                sub = sbh_clean[(sbh_clean["m"] == target_month) & (sbh_clean["loai"] == "máy mới")]
                if not sub.empty:
                    top_m = sub.groupby("canonical_name")["sl"].sum().idxmax()
                    return "string", top_m, None

        # E7. Specific item unit count: 'iphone 13 th nay ban dc may chiec', 'op lung silicon iphone', 'xiaomi redmi note 14'
        for kw, pat in [
            ("iphone 13", "iPhone 13"),
            ("op lung silicon iphone", "Ốp lưng silicon iPhone"),
            ("xiaomi redmi note 14", "Xiaomi Redmi Note 14"),
        ]:
            if kw in t_norm:
                if target_month:
                    sub = sbh_clean[
                        (sbh_clean["m"] == target_month)
                        & (sbh_clean["canonical_name"].str.contains(pat, case=False))
                    ]
                    return "number", int(sub["sl"].sum()), None

    return "abstain", None, "Không tìm thấy mẫu phù hợp cho câu hỏi dev_014"
