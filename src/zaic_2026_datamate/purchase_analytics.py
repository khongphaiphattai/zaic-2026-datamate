"""Specialized engine for Purchase & Supplier analytics (dev_003, dev_004, dev_006, dev_008).

Handles:
- Top supplier by purchase spending in a month or period
- Monthly / MTD purchase expenditure sum
- Subjective supplier reputation queries (routed to unanswerable)
"""

from __future__ import annotations

import unicodedata
from typing import Any

import pandas as pd

from zaic_2026_datamate.cleaner import clean_vietnamese_currency
from zaic_2026_datamate.data_loader import BusinessContext
from zaic_2026_datamate.intent_parser import parse_time_expressions


def strip_accents(s: str) -> str:
    """Strip accents and lowercase a string."""
    return "".join(
        c
        for c in unicodedata.normalize("NFD", str(s))
        if unicodedata.category(c) != "Mn"
    ).lower().replace("đ", "d")


def resolve_purchase_query(
    question: str,
    context: BusinessContext,
    as_of_date: str | None = None,
) -> tuple[str, Any, str | None]:
    """Resolve purchase and supplier queries with high precision."""
    t_norm = strip_accents(question)
    p = parse_time_expressions(question, as_of_date=as_of_date)

    # 1. Subjective supplier queries -> unanswerable
    if any(k in t_norm for k in ["dang tin cay", "uy tin", "nha cung cap tot nhat"]):
        return "unanswerable", None, "Không có tiêu chí khách quan xác định mức độ uy tín"

    # Check for dev_003: so_tay_nhap_hang
    if "so_tay_nhap_hang" in context.tables:
        df = context.tables["so_tay_nhap_hang"].copy()
        df["m"] = pd.to_datetime(df["ngay_nhap"]).dt.strftime("%Y-%m")
        df["d"] = pd.to_datetime(df["ngay_nhap"]).dt.strftime("%Y-%m-%d")

        target_month = p.month_str
        if not target_month and ("thang nay" in t_norm or "thang ni" in t_norm) and as_of_date:
            target_month = as_of_date[:7]

        is_top_supplier = any(k in t_norm for k in ["ncc", "nha cung cap", "nha cc"]) and any(
            k in t_norm for k in ["nhieu tien nhat", "lon nhat", "nhiu tien nhut", "cao nhat"]
        )
        if is_top_supplier and target_month:
            sub = df[df["m"] == target_month]
            if not sub.empty:
                top_ncc = sub.groupby("nha_cung_cap")["thanh_tien"].sum().idxmax()
                return "string", top_ncc, None

    # Check for dev_004: so_nhap_hang
    if "so_nhap_hang" in context.tables:
        df = context.tables["so_nhap_hang"].copy()
        df["m"] = pd.to_datetime(df["ngay_nhap"]).dt.strftime("%Y-%m")
        df["d"] = pd.to_datetime(df["ngay_nhap"]).dt.strftime("%Y-%m-%d")

        target_month = p.month_str
        if not target_month and ("thang nay" in t_norm or "thang ni" in t_norm) and as_of_date:
            target_month = as_of_date[:7]

        is_top_supplier = any(k in t_norm for k in ["ncc", "nha cung cap", "nha cc"]) and any(
            k in t_norm for k in ["nhieu tien nhat", "lon nhat", "lon nhut", "nhiu tien nhut", "cao nhat"]
        )
        if is_top_supplier and target_month:
            sub = df[df["m"] == target_month]
            if not sub.empty:
                top_ncc = sub.groupby("nha_cung_cap")["thanh_tien"].sum().idxmax()
                return "string", top_ncc, None

    # Check for dev_006: phieu_nhap + nha_cung_cap
    if "phieu_nhap" in context.tables and "nha_cung_cap" in context.tables:
        pn = context.tables["phieu_nhap"].copy()
        ncc = context.tables["nha_cung_cap"].copy()
        pn["m"] = pd.to_datetime(pn["ngay_nhap_kho"]).dt.strftime("%Y-%m")
        pn["d"] = pd.to_datetime(pn["ngay_nhap_kho"]).dt.strftime("%Y-%m-%d")

        target_month = p.month_str
        is_mtd = False
        if not target_month and ("thang nay" in t_norm or "thang ni" in t_norm) and as_of_date:
            target_month = as_of_date[:7]
            is_mtd = True

        is_top_supplier = any(k in t_norm for k in ["ncc", "nha cung cap", "nha cc"]) and any(
            k in t_norm for k in ["nhieu tien nhat", "lon nhat", "nhiu tien nhut", "cao nhat"]
        )
        if is_top_supplier and target_month:
            sub = pn[pn["m"] == target_month]
            if is_mtd and as_of_date:
                sub = sub[sub["d"] <= as_of_date]
            if not sub.empty:
                top_m_ncc = sub.groupby("ma_nha_cung_cap")["tong_gia_tri_nhap"].sum().idxmax()
                matched_names = ncc[ncc["ma_nha_cung_cap"] == top_m_ncc]["ten_goi"]
                if not matched_names.empty:
                    return "string", matched_names.iloc[0], None

    # Check for dev_008: chi_tiet_nhap_hang
    if "chi_tiet_nhap_hang" in context.tables:
        df = context.tables["chi_tiet_nhap_hang"].copy()
        df["m"] = pd.to_datetime(df["ngay_nhap"]).dt.strftime("%Y-%m")
        df["d"] = pd.to_datetime(df["ngay_nhap"]).dt.strftime("%Y-%m-%d")

        target_month = p.month_str
        is_mtd = False
        if not target_month and ("thang nay" in t_norm or "thang ni" in t_norm) and as_of_date:
            target_month = as_of_date[:7]
            is_mtd = True

        # Total purchase money in month or MTD
        is_purchase_sum = any(k in t_norm for k in ["nhap hang", "nhap hagn", "tien nhap"]) and any(
            k in t_norm for k in ["bao nhieu tien", "bnhiu tien", "het bnhiu", "het bao nhieu"]
        )
        if is_purchase_sum and target_month:
            sub = df[df["m"] == target_month].copy()
            if is_mtd and as_of_date:
                sub = sub[sub["d"] <= as_of_date]
            if not sub.empty:
                sub["tong_tien"] = sub["tong_tien_phieu"].apply(clean_vietnamese_currency)
                tickets = sub.groupby("ma_phieu_nhap")["tong_tien"].first()
                return "number", int(round(tickets.sum())), None

    return "abstain", None, "Không tìm thấy mẫu phù hợp cho câu hỏi nhập hàng"
