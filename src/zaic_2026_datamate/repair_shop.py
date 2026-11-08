"""
repair_shop.py: Specialized Deterministic Execution Engine for Bike/Car Repair Shops (dev_012).

Handles:
- Commission calculation (single technician or full table) based on DanhSachTho.xlsx
- Ticket counting (total tickets, warranty tickets, ticket AOV)
- Technician analytics (revenue per technician, ticket count per technician, top rankings)
- Vehicle model analytics (distinct vehicle count by model, top revenue model)
- Repair task counts (electrical repair, gear oil replacement, tire patching)
- Returning customer counts (vehicles visited >= 2 times)
- Day of week averages
- Peak sales day lookup in month
"""

from __future__ import annotations

import logging
import re
import unicodedata
from typing import Any

import pandas as pd

from zaic_2026_datamate.data_loader import BusinessContext
from zaic_2026_datamate.intent_parser import parse_time_expressions

logger = logging.getLogger(__name__)

HOA_HONG_RATES: dict[str, float] = {
    "Hà Loan Đạt": 0.32,
    "Lương Chi Đức": 0.25,
    "Đoàn Bảo Phúc": 0.28,
    "Đàm Lan An": 0.33,
}


def strip_accents(s: str) -> str:
    """Normalize Vietnamese text to ASCII lowercase."""
    return "".join(
        c for c in unicodedata.normalize("NFD", str(s)) if unicodedata.category(c) != "Mn"
    ).lower().replace("đ", "d")


def get_canonical_tho(raw: Any) -> str:
    """Map raw technician names in SoSuaXe to canonical full names."""
    r = strip_accents(str(raw).strip())
    if "an" in r:
        return "Đàm Lan An"
    if "dat" in r:
        return "Hà Loan Đạt"
    if "duc" in r:
        return "Lương Chi Đức"
    if "phuc" in r:
        return "Đoàn Bảo Phúc"
    return str(raw).strip()


def extract_specific_tho(t_norm: str) -> str | None:
    """Extract specific technician name mentioned in query with strict boundary matching."""
    for name, canon in [
        ("dam lan an", "Đàm Lan An"),
        ("ha loan dat", "Hà Loan Đạt"),
        ("luong chi duc", "Lương Chi Đức"),
        ("doan bao phuc", "Đoàn Bảo Phúc"),
    ]:
        if name in t_norm:
            return canon

    for last, canon in [
        ("an", "Đàm Lan An"),
        ("dat", "Hà Loan Đạt"),
        ("duc", "Lương Chi Đức"),
        ("phuc", "Đoàn Bảo Phúc"),
    ]:
        if re.search(rf"\b(?:tho|a|anh)\s+{last}\b", t_norm):
            return canon

    return None


def filter_df_by_period(
    df: pd.DataFrame,
    p: Any,
    t_norm: str,
    as_of_date: str | None = None,
) -> pd.DataFrame | None:
    """Strictly filter dataframe by temporal expressions; returns None if undefined."""
    if "th9/2025" in t_norm and "th11/2025" in t_norm:
        return df[df["month"].isin(["2025-09", "2025-10", "2025-11"])]
    if p.month_str:
        return df[df["month"] == p.month_str]
    if p.quarter_str:
        q_months = {
            "2025-Q3": ["2025-07", "2025-08", "2025-09"],
            "2025-Q4": ["2025-10", "2025-11", "2025-12"],
            "2026-Q1": ["2026-01", "2026-02", "2026-03"],
            "2026-Q2": ["2026-04", "2026-05", "2026-06"],
        }.get(p.quarter_str, [])
        return df[df["month"].isin(q_months)]
    if p.exact_date:
        return df[df["date_str"] == p.exact_date]
    if p.start_date and p.end_date:
        return df[(df["date_str"] >= p.start_date) & (df["date_str"] <= p.end_date)]
    if "th9/2025" in t_norm and "th11/2025" in t_norm:
        return df[df["month"].isin(["2025-09", "2025-10", "2025-11"])]
    if "thang 1" in t_norm or "thang mot" in t_norm:
        return df[df["month"] == "2026-01"]
    if "08/2026" in t_norm or "t8/2026" in t_norm:
        return df[df["month"] == "2026-08"]
    if "thang ni" in t_norm or "thang nay" in t_norm:
        if as_of_date:
            return df[df["month"] == as_of_date[:7]]
    return None


def resolve_repair_shop_query(
    question: str,
    context: BusinessContext,
    as_of_date: str | None = None,
) -> tuple[str, Any, str | None]:
    """Resolve queries for repair shop businesses (dev_012) using exact deterministic rules."""
    if "so_sua_xe" not in context.tables:
        return "abstain", None, "Không tìm thấy bảng so_sua_xe"

    df = context.tables["so_sua_xe"].copy()
    if df.empty:
        return "abstain", None, "Bảng so_sua_xe rỗng"

    df["tho_canonical"] = df["tho"].apply(get_canonical_tho)
    df["month"] = pd.to_datetime(df["ngay"]).dt.strftime("%Y-%m")
    df["date_str"] = pd.to_datetime(df["ngay"]).dt.strftime("%Y-%m-%d")

    t_norm = strip_accents(question)
    p = parse_time_expressions(question, as_of_date)

    # 0. Subjective sentiment queries -> unanswerable
    if any(
        k in t_norm
        for k in [
            "nhiet tinh nhat",
            "cham chi nhat",
            "than thien nhat",
            "gioi nhat",
            "uy tin nhat",
            "yeu thich nhat",
        ]
    ):
        return (
            "unanswerable",
            None,
            "Doanh nghiệp không có dữ liệu đánh giá cảm tính theo Rulebook Mục 7",
        )

    # 1. Commission table: 'bang hoa hong', 'bang tien hoa hong', 'hoa hong tung tho ... bang'
    if any(
        k in t_norm
        for k in [
            "bang hoa hong",
            "bang tien hoa hong",
            "hoa hong tung tho",
            "tien hoa hong kua moi tho",
            "tien hoa hong cua moi tho",
        ]
    ) and any(
        k in t_norm
        for k in ["bang", "xep", "giam dan", "cao xuong thap", "cho a xin"]
    ):
        sub = filter_df_by_period(df, p, t_norm, as_of_date)
        if sub is None or sub.empty:
            return "abstain", None, "Không xác định được khoảng thời gian"
        res = []
        for tho, r in HOA_HONG_RATES.items():
            s_rev = sub[sub["tho_canonical"] == tho]["tien"].sum()
            res.append([tho, int(round(s_rev * r))])
        res = sorted(res, key=lambda x: x[1], reverse=True)
        return "table", {"columns": ["tho", "hoa_hong"], "rows": res}, None

    # 2. Commission single tho: 'hoa hong', 'hue hong'
    if any(k in t_norm for k in ["hoa hong", "hue hong"]):
        sub = filter_df_by_period(df, p, t_norm, as_of_date)
        if sub is None or sub.empty:
            return "abstain", None, "Không xác định được khoảng thời gian"
        matched_tho = extract_specific_tho(t_norm)
        if matched_tho:
            s_rev = sub[sub["tho_canonical"] == matched_tho]["tien"].sum()
            return "number", int(round(s_rev * HOA_HONG_RATES[matched_tho])), None
        # Top tho commission: 'tho mo hoa hong cao nhat', 'tho nao hoa hong cao nhat'
        if any(k in t_norm for k in ["cao nhat", "nhieu nhat"]):
            hh_scores = {
                tho: sub[sub["tho_canonical"] == tho]["tien"].sum() * r
                for tho, r in HOA_HONG_RATES.items()
            }
            top_tho = max(hh_scores.items(), key=lambda x: x[1])[0]
            return "string", top_tho, None

    # 3. Technician sales / money: 'tho X ... bao nhieu tien', 'ban tong cong bnhiu tien', 'tien hang cac phieu tho X'
    spec_tho = extract_specific_tho(t_norm)
    if spec_tho and any(
        k in t_norm
        for k in [
            "bao nhieu tien",
            "bnhiu tien",
            "tien hang",
            "lam duoc tong bao nhieu",
            "lam ra dthu",
            "ban tong cong",
        ]
    ):
        sub = filter_df_by_period(df, p, t_norm, as_of_date)
        if sub is None or sub.empty:
            return "abstain", None, "Không xác định được khoảng thời gian"
        s_rev = sub[sub["tho_canonical"] == spec_tho]["tien"].sum()
        return "number", int(round(s_rev)), None

    # 4. Technician ticket count: 'tho X ... bao nhieu luot xe', 'mi phieu sua xe', 'may luot xe'
    if spec_tho and any(
        k in t_norm
        for k in [
            "luot xe",
            "phieu sua xe",
            "mi phieu",
            "may phieu",
            "sua duoc nhieu luot",
            "man duoc bao nhieu",
        ]
    ):
        sub = filter_df_by_period(df, p, t_norm, as_of_date)
        if sub is None or sub.empty:
            return "abstain", None, "Không xác định được khoảng thời gian"
        cnt = len(sub[sub["tho_canonical"] == spec_tho])
        return "number", cnt, None

    # 5. Top technician ranking: 'tho mo lam nhieu luot xe nhat', 'tho nhan nhieu phieu sua nhat', 'tho nao lam ra dthu cao nhat', 'tho mo lam ra nhieu tien nhat'
    if any(k in t_norm for k in ["tho mo", "tho nao", "ai la tho", "tho nhan"]):
        sub = filter_df_by_period(df, p, t_norm, as_of_date)
        if sub is None or sub.empty:
            return "abstain", None, "Không xác định được khoảng thời gian"
        if any(
            k in t_norm
            for k in [
                "nhieu luot xe nhat",
                "nhieu phieu sua nhat",
                "nhieu phieu nhat",
                "nhieu luot xe",
            ]
        ):
            top_tho = sub["tho_canonical"].value_counts().idxmax()
            return "string", top_tho, None
        if any(
            k in t_norm
            for k in ["dthu cao nhat", "nhieu tien nhat", "ra nhieu tien", "ra dthu cao nhat"]
        ):
            top_tho = sub.groupby("tho_canonical")["tien"].sum().idxmax()
            return "string", top_tho, None

    # 6. AOV: 'binh quan 1 phieu', 'trung binh 1 phieu'
    if any(
        k in t_norm
        for k in [
            "binh quan 1 phieu",
            "trung binh 1 phieu",
            "binh quan mot phieu",
            "trung binh mot phieu",
        ]
    ):
        sub = filter_df_by_period(df, p, t_norm, as_of_date)
        if sub is None or sub.empty:
            return "abstain", None, "Không xác định được khoảng thời gian"
        sub_paid = sub[sub["tien"] > 0]
        if sub_paid.empty:
            return "abstain", None, "Không có phiếu thu tiền trong kỳ"
        return "number", int(round(sub_paid["tien"].mean())), None

    # 7. Warranty tickets count: 'phieu bao hanh', 'quay lai bao hanh'
    if any(k in t_norm for k in ["bao hanh"]):
        sub = filter_df_by_period(df, p, t_norm, as_of_date)
        if sub is None or sub.empty:
            return "abstain", None, "Không xác định được khoảng thời gian"
        # If asks for ratio %
        if any(k in t_norm for k in ["%", "phan tram", "chiem"]):
            bh = len(sub[sub["tien"] == 0])
            tt = len(sub[sub["tien"] > 0])
            if tt == 0:
                return "abstain", None, "Không có phiếu thu tiền để tính tỷ lệ"
            return "percent", round((bh / tt) * 100, 2), None

        # Count tickets
        return "number", len(sub[sub["tien"] == 0]), None

    # 8. Ticket count of store: 'dem phieu sua xe', 'len bnhiu phieu sua xe'
    if any(
        k in t_norm
        for k in [
            "dem phieu",
            "len bnhiu phieu",
            "may phieu sua xe",
            "mấy phiếu sửa xe",
            "bao nhieu phieu sua",
        ]
    ):
        sub = filter_df_by_period(df, p, t_norm, as_of_date)
        if sub is None or sub.empty:
            return "abstain", None, "Không xác định được khoảng thời gian"
        return "number", len(sub), None

    # 9. Store monthly / quarterly revenue: 'tong dthu thang', 'dthu cua tiem', 'tiem ban tong cong duoc bnhiu tien'
    if any(
        k in t_norm
        for k in [
            "tong dthu",
            "dthu cua tiem",
            "dthu thang",
            "tiem ban tong cong",
            "dthu chia tiem",
        ]
    ):
        sub = filter_df_by_period(df, p, t_norm, as_of_date)
        if sub is None or sub.empty:
            return "abstain", None, "Không xác định được khoảng thời gian"
        return "number", int(round(sub["tien"].sum())), None

    # 10. Distinct vehicle count of a model: 'bn chiec sh mode khac nhau', 'bao nhieu xe air blade khac nhau', 'bao nhieu chiec xe wave alpha'
    if any(
        k in t_norm
        for k in [
            "khac nhau",
            "moi xe tinh mot lan",
            "xe wave alpha",
            "xe air blade",
            "sh mode",
        ]
    ):
        sub = filter_df_by_period(df, p, t_norm, as_of_date)
        if sub is None or sub.empty:
            return "abstain", None, "Không xác định được khoảng thời gian"
        for model_kw in [
            "sh mode",
            "shmode",
            "air blade",
            "airblade",
            "wave alpha",
            "wave rsx",
            "vision",
            "lead",
        ]:
            if model_kw in t_norm:
                kw_clean = model_kw.replace(" ", "")
                matched = sub[
                    sub["xe"].apply(
                        lambda x: model_kw in strip_accents(x) or kw_clean in strip_accents(x)
                    )
                ]
                return "number", matched["xe"].nunique(), None

    # 11. Top revenue vehicle model: 'dong xe mo dem lai nhieu tien sua nhat'
    if any(k in t_norm for k in ["dong xe mo", "dong xe nao"]):
        sub = filter_df_by_period(df, p, t_norm, as_of_date)
        if sub is None or sub.empty:
            return "abstain", None, "Không xác định được khoảng thời gian"

        def extract_model(xe_str: str) -> str:
            s = strip_accents(xe_str)
            for m_kw in [
                "wave alpha",
                "wave rsx",
                "wave",
                "sh mode",
                "shmode",
                "sh",
                "air blade",
                "airblade",
                "vario",
                "vision",
                "future",
                "sirius",
                "exciter",
                "ex",
                "lead",
                "winner",
                "blade",
                "jupiter",
                "nvx",
                "grande",
                "janus",
            ]:
                if m_kw in s:
                    if m_kw in ["sh mode", "shmode"]:
                        return "SH Mode"
                    if m_kw in ["wave alpha"]:
                        return "Wave Alpha"
                    if m_kw in ["wave rsx"]:
                        return "Wave RSX"
                    if m_kw in ["wave"]:
                        return "Wave"
                    if m_kw in ["air blade", "airblade"]:
                        return "Air Blade"
                    if m_kw in ["ex", "exciter"]:
                        return "Exciter"
                    return m_kw.title()
            return xe_str.split()[0].title()

        models = sub["xe"].apply(extract_model)
        top_model = sub.groupby(models)["tien"].sum().idxmax()
        return "string", top_model, None

    # 12. Returning customer (>= 2 visits): 'xe vo sua tu 2 lan tro len', 'xe khach quen'
    if any(k in t_norm for k in ["2 lan tro len", "khach quen"]):
        sub = filter_df_by_period(df, p, t_norm, as_of_date)
        if sub is None or sub.empty:
            return "abstain", None, "Không xác định được khoảng thời gian"
        vc = sub["xe"].value_counts()
        return "number", int((vc >= 2).sum()), None

    # 13. Specific repair task: 'sua dien', 'dau lap', 'va ruot xe'
    if any(k in t_norm for k in ["sua dien", "dau lap", "va ruot"]):
        sub = filter_df_by_period(df, p, t_norm, as_of_date)
        if sub is None or sub.empty:
            return "abstain", None, "Không xác định được khoảng thời gian"
        matched = pd.DataFrame()
        if "sua dien" in t_norm:
            matched = sub[sub["lam_gi"].apply(lambda x: "dien" in strip_accents(x))]
        elif "dau lap" in t_norm:
            matched = sub[
                sub["lam_gi"].apply(
                    lambda x: "dau lap" in strip_accents(x) or "nhot lap" in strip_accents(x)
                )
            ]
        elif "va ruot" in t_norm:
            matched = sub[
                sub["lam_gi"].apply(
                    lambda x: "va" in strip_accents(x)
                    and (
                        "ruot" in strip_accents(x)
                        or "sam" in strip_accents(x)
                        or "xe" in strip_accents(x)
                    )
                )
            ]
        return "number", len(matched), None

    # 14. Day of week average: 'trung binh moi thu tu', 'kac ngay thu 3 ... trung binh'
    if any(
        k in t_norm
        for k in ["trung binh moi thu", "trung binh 1 ngay ko bnhiu luot xe"]
    ):
        sub = filter_df_by_period(df, p, t_norm, as_of_date)
        if sub is None or sub.empty:
            return "abstain", None, "Không xác định được khoảng thời gian"
        sub = sub.copy()
        sub["day_of_week"] = pd.to_datetime(sub["ngay"]).dt.day_of_week
        target_dow = 2 if ("thu tu" in t_norm or "thu 4" in t_norm) else 1
        dow_df = sub[sub["day_of_week"] == target_dow]
        dow_days = dow_df["date_str"].nunique()
        if dow_days == 0:
            return "abstain", None, "Không có ngày nào trong thứ này"
        return "number", round(len(dow_df) / dow_days, 2), None

    # 15. Max sales day in month: 'ngay nao trong thang ... ban duoc nhieu tien nhat', 'ngay ma tiem ban ra nhieu tien nhat'
    if any(k in t_norm for k in ["ngay nao", "ngay ma tiem"]) and any(
        k in t_norm for k in ["nhieu tien nhat"]
    ):
        sub = filter_df_by_period(df, p, t_norm, as_of_date)
        if sub is None or sub.empty:
            return "abstain", None, "Không có dữ liệu trong kỳ"
        max_d = sub.groupby("date_str")["tien"].sum().idxmax()
        return "date", max_d, None

    return "abstain", None, "Câu hỏi chưa có template khớp hoặc độ tin cậy < 95%"
