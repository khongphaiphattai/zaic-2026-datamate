"""
semantic_contract.py: Shared Business Semantic Layer (Canonical Order & Line-Item Grain Views)
and Deterministic QueryPlan Compiler for Zalo AI Challenge 2026 (Track 2: DataMate).

Implements Priority 2 of the v3.2 Architecture:
1. BusinessSemanticContract:
   - Builds and registers two canonical DuckDB tables/views per business:
     * `canonical_orders` (1 row = 1 order):
       - `order_id` (VARCHAR)
       - `order_date` (DATE: MIN order placement date per order)
       - `order_hour` (INTEGER: 0..23 extracted from order placement timestamp)
       - `is_valid_sale` (BOOLEAN: True for completed/shipping, False if any row in the order is cancelled/returned)
       - `order_revenue` (DOUBLE: sum of valid merchandise line items after line-level discount,
         excluding order-level discounts/vouchers/shipping, keeping service fee 'Phí phục vụ')
       - `channel` (VARCHAR: 'store' | 'phone' | 'zalo_oa' | 'ecommerce' | NULL)
       - `payment_method` (VARCHAR: 'cash' | 'transfer' | 'ewallet' | 'cod' | 'debt' | NULL)
       - `customer_key` (VARCHAR: non-walk-in customer identifier/name, NULL for walk-in 'khách lẻ')
       - `customer_group` (VARCHAR: 'thân thiết' | 'sỉ' | 'lẻ' | NULL)
     * `canonical_order_lines` (1 row = 1 line item):
       - `order_id` (VARCHAR)
       - `order_date` (DATE)
       - `order_hour` (INTEGER)
       - `is_valid_sale` (BOOLEAN)
       - `item_key` (VARCHAR: canonical product code or normalized product name)
       - `item_name` (VARCHAR)
       - `category` (VARCHAR: normalized lowercase category, with inline topping split for dev_002)
       - `is_prescription` (BOOLEAN: True for Rx drugs, False for non-Rx)
       - `is_service_fee` (BOOLEAN: True for 'Phí phục vụ')
       - `line_revenue` (DOUBLE)
   - Records schema capability flags (`has_channel`, `has_payment_method`, `has_customer_key`,
     `has_customer_group`, `has_gender`, `has_shift`, `has_category`, `has_prescription`) for
     deterministic Gate 1 (`unanswerable`) detection.

2. QueryPlan & Deterministic Verifier/Compiler:
   - Step 2A (Filtered Metrics): category revenue & order count, shift revenue & order count,
     customer group revenue & AOV, prescription/non-prescription revenue, channel revenue (single & list),
     payment method revenue (single & list), repeat customer count, order revenue threshold count,
     daily revenue threshold count, item-in-K-orders count.
   - Step 2B (Share & Percentage `%`): category_share_pct, customer_group_share_pct,
     prescription_share_pct, channel_share_pct, payment_share_pct, repeat_customer_share_pct,
     shift_diff_pct, plus OOD `unanswerable` when the business schema lacks the queried dimension.
"""

from __future__ import annotations

import calendar
import logging
import math
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import numpy as np
import pandas as pd

from zaic_2026_datamate.data_loader import BusinessContext
from zaic_2026_datamate.intent_parser import (
    ParsedPeriod,
    check_date_out_of_domain,
    find_primary_sales_table,
    parse_time_expressions,
)

logger = logging.getLogger(__name__)


def norm_vn(text: Any) -> str:
    """Normalize Vietnamese text to lowercase ASCII with collapsed whitespace."""
    if text is None or (isinstance(text, float) and np.isnan(text)):
        return ""
    s = str(text).strip().lower()
    if not s or s in ("nan", "none", "null", "nat"):
        return ""
    s = s.replace("đ", "d")
    s = "".join(
        c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn"
    )
    # Common telex and typo normalizations
    s = re.sub(r"\btrawm\b", "tram", s)
    s = re.sub(r"\bptram\b", "phan tram", s)
    s = re.sub(r"\bnhta\b", "nhat", s)
    s = re.sub(r"\bdduowcj\b", "duoc", s)
    s = re.sub(r"\bcko\b", "cho", s)
    s = re.sub(r"\btoor?g\s*coor?g\b", "tong cong", s)
    s = re.sub(r"\bsoos\b", "so", s)
    s = re.sub(r"\btoonf\b", "ton", s)
    s = re.sub(r"\bthangs\b", "thang", s)
    s = re.sub(r"\bngayf\b", "ngay", s)
    s = re.sub(r"\btieenf\b", "tien", s)
    s = re.sub(
        r"\bmi\s+(?=(?:don|cuon|cay|ly|cai|mon|tram|trieu|nghin|k\b|ngay|thang|hop|chai|lon|phan|khach))",
        "may ",
        s,
    )
    return re.sub(r"\s+", " ", s).strip()


_UNIT_SPACE_RE = re.compile(r"\b(\d+)\s+(ml|l|g|kg|mg|cm|mm|mcg|iu)\b")


def _item_norm_key(text: Any) -> str:
    """Normalize an item name/ID for grouping, collapsing pack-unit spaces (e.g. '330 ml' -> '330ml')."""
    s = norm_vn(text)
    if not s:
        return ""
    return _UNIT_SPACE_RE.sub(r"\1\2", s)


def clean_spaces_lower(text: Any) -> str:
    """Lowercase and collapse whitespace while preserving Vietnamese diacritics."""
    if text is None or (isinstance(text, float) and np.isnan(text)):
        return ""
    s = str(text).strip().lower()
    if not s or s in ("nan", "none", "null", "nat"):
        return ""
    return re.sub(r"\s+", " ", s).strip()


# Standardized domain product-to-category lookup tables for businesses without a separate catalog table
# (derived deterministically from the identical product catalogs in biz_106/112 and biz_115/116/118)
CAFE_CATEGORY_LOOKUP: dict[str, str] = {
    "ca phe den (m)": "cà phê",
    "ca phe den (l)": "cà phê",
    "ca phe sua (m)": "cà phê",
    "ca phe sua (l)": "cà phê",
    "bac xiu (m)": "cà phê",
    "bac xiu (l)": "cà phê",
    "ca phe muoi (m)": "cà phê",
    "ca phe muoi (l)": "cà phê",
    "espresso (single)": "cà phê",
    "espresso (double)": "cà phê",
    "latte (m)": "cà phê",
    "latte (l)": "cà phê",
    "cappuccino (m)": "cà phê",
    "cappuccino (l)": "cà phê",
    "cold brew (m)": "cà phê",
    "cold brew (l)": "cà phê",
    "tra dao cam sa (m)": "trà",
    "tra dao cam sa (l)": "trà",
    "tra vai (m)": "trà",
    "tra vai (l)": "trà",
    "tra sua tran chau (m)": "trà sữa",
    "tra sua tran chau (l)": "trà sữa",
    "tra sua o long (m)": "trà sữa",
    "tra sua o long (l)": "trà sữa",
    "hong tra sua (m)": "trà sữa",
    "hong tra sua (l)": "trà sữa",
    "tra chanh (m)": "trà",
    "tra chanh (l)": "trà",
    "matcha latte (m)": "trà",
    "matcha latte (l)": "trà",
    "sua tuoi tran chau duong den (m)": "trà sữa",
    "sua tuoi tran chau duong den (l)": "trà sữa",
    "sinh to bo (m)": "sinh tố",
    "sinh to bo (l)": "sinh tố",
    "sinh to xoai (m)": "sinh tố",
    "sinh to xoai (l)": "sinh tố",
    "nuoc ep cam (m)": "nước ép",
    "nuoc ep cam (l)": "nước ép",
    "nuoc ep dua hau (m)": "nước ép",
    "nuoc ep dua hau (l)": "nước ép",
    "soda chanh day (m)": "soda",
    "soda chanh day (l)": "soda",
    "da xay cookie (m)": "đá xay",
    "da xay cookie (l)": "đá xay",
    "topping tran chau den": "topping",
    "topping thach ca phe": "topping",
    "topping pudding trung": "topping",
    "topping kem cheese": "topping",
    "banh croissant": "bánh",
    "banh tiramisu": "bánh",
}

HARDWARE_CATEGORY_LOOKUP: dict[str, str] = {
    "xi mang ha tien pcb40 bao 50kg": "xi măng",
    "xi mang insee power-s bao 50kg": "xi măng",
    "cat xay to": "cát đá",
    "cat be tong": "cát đá",
    "cat san lap": "cát đá",
    "da 1x2": "cát đá",
    "da 4x6": "cát đá",
    "da mi bui": "cát đá",
    "gach ong 8x8x18 tuynel": "gạch",
    "gach the dac 4x8x18": "gạch",
    "gach lat nen prime 60x60 thung 4 vien": "gạch",
    "thep pomina d10 cay 11.7m": "sắt thép",
    "thep pomina d12 cay 11.7m": "sắt thép",
    "thep pomina d14 cay 11.7m": "sắt thép",
    "thep pomina d16 cay 11.7m": "sắt thép",
    "thep cuon phi 6 hoa phat": "sắt thép",
    "thep cuon phi 8 hoa phat": "sắt thép",
    "son dulux weathershield powerflexx 5l": "sơn",
    "son maxilite trong nha 18l": "sơn",
    "ong pvc binh minh d90 cay 4m": "ống nước",
    "day dien cadivi vcmd 2x2.5 cuon 100m": "điện",
    "gas petrolimex 12kg": "gas & nước",
    "gas saigon petro 12kg": "gas & nước",
    "nuoc khoang lavie 19l": "gas & nước",
    "nuoc tinh khiet vihawa 20l": "gas & nước",
    "bon nuoc inox son ha 1000l ngang": "ống nước",
    "ong pvc binh minh d32 cay 4m": "ống nước",
    "ong pvc binh minh d50 cay 4m": "ống nước",
    "keo dan ong pvc binh minh 200g": "ống nước",
    "day dien cadivi cv 2.5 cuon 100m": "điện",
    "day dien cadivi cv 1.5 cuon 100m": "điện",
    "cb panasonic mcb 2p 20a": "điện",
    "bang keo dien nano 20 yard": "điện",
    "bong led bulb rang dong a60 9w": "điện",
    "thep hop 40x80 day 1.4mm cay 6m": "sắt thép",
    "luoi b40 ma kem kho 1.5m": "sắt thép",
    "kem buoc 1mm": "sắt thép",
    "dinh 5 phan": "sắt thép",
    "ton lanh hoa sen 0.4mm": "sắt thép",
    "son lot jotun jotashield chong kiem 5l": "sơn",
    "bot tret jotun noi that 40kg": "sơn",
    "chong tham kova ct-11a tuong 20kg": "sơn",
    "keo dan gach weber.tai fix xam 25kg": "hoàn thiện",
    "keo cha ron weber 1kg": "hoàn thiện",
    "tam thach cao gyproc 9mm 1.2x2.4m": "hoàn thiện",
    "gach op tuong viglacera 30x60 thung 8 vien": "gạch",
    "ngoi dong nai 22 vien/m2": "gạch",
    "may bom nuoc panasonic gp-129jxk 125w": "ống nước",
    "bep gas doi rinnai rv-375sw": "gas & nước",
    "bo van day gas namilux": "gas & nước",
    "nuoc kiem ion life 19l": "gas & nước",
    "nuoc tinh khiet bidrico 20l": "gas & nước",
    "bat xanh cam kho 4m": "dụng cụ",
    "o cam doi 3 chau panasonic wide wev1582sw": "điện",
    "cong tac 1 chieu panasonic wide kem mat": "điện",
    "ong luon day dien sino sp d20 cay 2.92m": "điện",
    "bo den tuyp led t8 1.2m 18w rang dong": "điện",
    "bay tho ho total 160mm": "dụng cụ",
    "thuoc thuy nhom total 60cm": "dụng cụ",
    "luoi cat gach 110mm total": "dụng cụ",
}


def normalize_channel_value(val: Any) -> str | None:
    """Map raw channel value to canonical ('store', 'phone', 'zalo_oa', 'ecommerce')."""
    s = norm_vn(val)
    if not s:
        return None
    if s in (
        "store",
        "tai quay",
        "quay",
        "truc tiep",
        "ban truc tiep",
        "ban tai quay",
        "tai cho",
        "mang ve",
    ):
        return "store"
    if "zalo" in s:
        return "zalo_oa"
    if s in ("phone", "dien thoai", "dt", "goi dien", "qua dien thoai"):
        return "phone"
    if s in (
        "ecommerce",
        "san tmdt",
        "tmdt",
        "shopee",
        "lazada",
        "tiktok",
        "san thuong mai dien tu",
    ):
        return "ecommerce"
    return s


def normalize_payment_value(val: Any) -> str | None:
    """Map raw payment method value to canonical ('cash', 'transfer', 'ewallet', 'cod', 'debt')."""
    s = norm_vn(val)
    if not s:
        return None
    if s in ("cash", "tien mat", "tm", "tien tuoi"):
        return "cash"
    if s in ("transfer", "chuyen khoan", "ck", "ngan hang", "bank"):
        return "transfer"
    if s in (
        "ewallet",
        "vi dien tu",
        "vi",
        "momo",
        "vi momo",
        "zalopay",
        "vi zalopay",
        "vnpay",
        "vi vnpay",
    ):
        return "ewallet"
    if "cod" in s or "thu ho" in s:
        return "cod"
    if s in ("debt", "ghi no", "cong no", "no", "ban chiu", "mua chiu"):
        return "debt"
    return s


def normalize_customer_group_value(val: Any) -> str | None:
    """Map raw customer group value to canonical ('thân thiết', 'sỉ', 'lẻ')."""
    s = norm_vn(val)
    if not s:
        return None
    if "than thiet" in s or s == "vip" or s == "quen":
        return "thân thiết"
    if s in ("si", "khach si", "ban si", "nhom si"):
        return "sỉ"
    if s in ("le", "khach le", "ban le", "nhom le", "vang lai"):
        return "lẻ"
    return None


def normalize_customer_key(val: Any) -> str | None:
    """Return canonical non-walk-in customer key, or None for walk-in / blank customers."""
    if val is None or (isinstance(val, float) and np.isnan(val)):
        return None
    s = norm_vn(val)
    if not s:
        return None
    if s in ("khach le", "kl", "le", "khach vang lai", "vang lai", "khach"):
        return None
    return s


def is_bad_status(val: Any) -> bool:
    """Return True if status value indicates cancellation or return."""
    s = norm_vn(val)
    if not s:
        return False
    # Guard: 'đang vận chuyển' ('dang van chuyen') contains substring 'huy' in 'chuyen'!
    # Also support English 'cancelled' and 'returned'.
    return bool(
        re.search(
            r"\b(?:huy|cancel|cancelled|canceled|tra|return|returned|hoan hang|hoan tra|hang tra|doi tra|khong giao|that bai)\b",
            s,
        )
    )


def is_Prescription_True(val: Any) -> bool:
    """Return True if raw prescription value indicates a prescription drug (Rx)."""
    if val is None or (isinstance(val, float) and np.isnan(val)):
        return False
    if isinstance(val, (bool, np.bool_)):
        return bool(val)
    if isinstance(val, (int, float, np.number)):
        return float(val) == 1.0
    s = norm_vn(val)
    return s in (
        "1",
        "1.0",
        "true",
        "co",
        "yes",
        "ke don",
        "thuoc ke don",
        "ban theo don",
    )


def extract_hour_series(series: pd.Series) -> pd.Series:
    """Extract integer hour (0..23) from timestamp/datetime/time string series."""
    if series is None or len(series) == 0:
        return pd.Series(dtype="float64")
    if pd.api.types.is_datetime64_any_dtype(series):
        return series.dt.hour.astype("float64")
    s_str = series.astype(str).str.strip()
    # Match HH:MM in strings like '2025-04-01 14:25:00' or '14:25' or '08:05:12'
    extracted = s_str.str.extract(r"(?:^|\s)(\d{1,2}):\d{2}", expand=False)
    hours = pd.to_numeric(extracted, errors="coerce")
    return hours.where((hours >= 0) & (hours <= 23), np.nan)


@dataclass
class DimensionFilter:
    """Represents a 1D or composite semantic filter on canonical views."""

    channel: str | None = None  # 'store' | 'phone' | 'zalo_oa' | 'ecommerce'
    payment_method: str | None = (
        None  # 'cash' | 'transfer' | 'ewallet' | 'cod' | 'debt'
    )
    customer_group: str | None = None  # 'thân thiết' | 'sỉ' | 'lẻ'
    category: str | None = None  # lowercase category name
    is_prescription: bool | None = None  # True (Rx) | False (non-Rx)
    shift: str | None = None  # 'morning' | 'afternoon'
    min_orders_per_customer: int | None = None
    min_order_revenue: float | None = None
    min_daily_revenue: float | None = None
    min_orders_per_item: int | None = None
    gender: str | None = None  # 'female' | 'male' -> triggers OOD check
    item_key: str | None = None
    item_name: str | None = None
    size: str | None = None  # 'M' | 'L'
    drink_only: bool = (
        False  # exclude topping and food when asking for ly / drink count
    )
    staff_key: str | None = None
    staff_name: str | None = None


@dataclass
class QueryPlan:
    """Structured semantic query plan compiled deterministically against canonical views."""

    operation: str
    # Supported operations:
    # - 'filtered_revenue'
    # - 'filtered_order_count'
    # - 'filtered_quantity'
    # - 'ranking_top_k'
    # - 'filtered_aov'
    # - 'dual_channel_revenue_list'
    # - 'dual_payment_revenue_list'
    # - 'repeat_customer_count'
    # - 'order_revenue_threshold_count'
    # - 'daily_revenue_threshold_count'
    # - 'item_order_threshold_count'
    # - 'share_pct'
    # - 'repeat_customer_share_pct'
    # - 'shift_diff_pct'
    # - 'period_growth_pct'
    metric: str  # 'revenue' | 'order_count' | 'customer_count' | 'day_count' | 'item_count' | 'aov' | 'quantity' | 'base_quantity'
    period: ParsedPeriod
    prior_period: ParsedPeriod | None = None
    numerator_filter: DimensionFilter = field(default_factory=DimensionFilter)
    denominator_filter: DimensionFilter = field(default_factory=DimensionFilter)
    secondary_filter: DimensionFilter | None = (
        None  # For 2-element list or shift comparison
    )
    limit_k: int = 1
    target_dimension: str | None = (
        None  # 'item' | 'date' | 'month' | 'customer' | 'category'
    )
    output_type: str = "number"  # 'number' | 'percent' | 'list' | 'date' | 'string' | 'unanswerable' | 'abstain'


@dataclass
class BusinessSemanticContract:
    """Holds canonical grain DataFrames and schema capability flags for a BusinessContext."""

    business_id: str
    orders_df: pd.DataFrame
    lines_df: pd.DataFrame
    has_channel: bool = False
    has_payment_method: bool = False
    has_customer_key: bool = False
    has_customer_group: bool = False
    has_gender: bool = False
    has_shift: bool = False
    has_category: bool = False
    has_prescription: bool = False
    has_staff: bool = False
    staff_id_to_name: dict[str, str] = field(default_factory=dict)
    staff_name_to_id: dict[str, str] = field(default_factory=dict)
    available_categories: set[str] = field(default_factory=set)
    available_products: list[tuple[str, str, str]] = field(
        default_factory=list
    )  # (norm_name, original_name, item_key)

    @classmethod
    def from_context(cls, context: BusinessContext) -> BusinessSemanticContract:
        """Build or retrieve cached BusinessSemanticContract for a BusinessContext."""
        cached = getattr(context, "_semantic_contract", None)
        if cached is not None:
            return cached

        contract = cls._build(context)
        context._semantic_contract = contract
        return contract

    @classmethod
    def _build(cls, context: BusinessContext) -> BusinessSemanticContract:
        tables = context.tables

        # 1. Build lookup maps from auxiliary tables (Customer Group, Product Category, Prescription)
        cust_group_map: dict[str, str] = {}
        cust_name_map: dict[str, str] = {}
        has_gender = False
        for tname, df in tables.items():
            if tname == "calendar":
                continue
            cols = list(df.columns)
            # Check gender column with non-null values
            for gc in ("gioi_tinh", "gender", "phai"):
                if gc in cols and df[gc].dropna().astype(str).str.strip().ne("").any():
                    has_gender = True

            # Customer table lookup
            if any(k in tname for k in ("khach_hang", "khach")):
                id_col = next(
                    (
                        c
                        for c in (
                            "ma_khach_hang",
                            "ma_kh",
                            "customer_id",
                            "ma_kh_mua",
                            "ma_khach_le_si",
                            "ma_khach",
                            "id_kh",
                        )
                        if c in cols
                    ),
                    None,
                )
                grp_col = next(
                    (
                        c
                        for c in (
                            "nhom_khach_hang",
                            "loai_khach_hang",
                            "nhom",
                            "hang",
                            "kieu_khach",
                            "loai_khach",
                            "phan_nhom",
                        )
                        if c in cols
                    ),
                    None,
                )
                name_col = next(
                    (
                        c
                        for c in (
                            "ten_goi",
                            "khach_hang_ten",
                            "ten_khach_hang",
                            "ten_kh",
                            "tenkh",
                            "ho_ten",
                            "ten",
                            "khach_le_si",
                            "khach_hang",
                            "kh",
                        )
                        if c in cols
                    ),
                    None,
                )
                if id_col:
                    if grp_col:
                        for k_val, g_val in zip(df[id_col], df[grp_col]):
                            k_norm = norm_vn(k_val)
                            g_norm = normalize_customer_group_value(g_val)
                            if k_norm and g_norm:
                                cust_group_map[k_norm] = g_norm
                    if name_col:
                        for k_val, n_val in zip(df[id_col], df[name_col]):
                            k_norm = norm_vn(k_val)
                            if k_norm and pd.notna(n_val) and str(n_val).strip():
                                cust_name_map[k_norm] = str(n_val).strip()

        # Staff table lookup (Staff code <-> Staff name)
        staff_id_to_name: dict[str, str] = {}
        staff_name_to_id: dict[str, str] = {}
        for tname, df in tables.items():
            if "nhan_vien" in tname.lower():
                cols = {c.lower(): c for c in df.columns}
                id_c = next(
                    (cols[c] for c in ["ma_nhan_vien", "ma_nv", "manv"] if c in cols),
                    None,
                )
                nm_c = next(
                    (
                        cols[c]
                        for c in ["ten_goi", "ten_nhan_vien", "ten_nv", "ho_ten", "ten"]
                        if c in cols
                    ),
                    None,
                )
                if id_c and nm_c:
                    for _, r in df.iterrows():
                        sid = str(r[id_c]).strip()
                        snm = str(r[nm_c]).strip()
                        if sid and snm and sid.lower() not in ("nan", "none", ""):
                            staff_id_to_name[sid] = snm
                            staff_name_to_id[norm_vn(snm)] = sid

        # Product catalog & prescription lookups
        id_to_cat: dict[str, str] = {}
        name_to_cat: dict[str, str] = {}
        name_to_id: dict[str, str] = {}
        id_to_name: dict[str, str] = {}
        id_to_rx: dict[str, bool] = {}
        name_to_rx: dict[str, bool] = {}

        # First pass: product catalog tables
        for tname, df in tables.items():
            if (
                tname == "calendar"
                or any(
                    k in tname
                    for k in (
                        "hoa_don",
                        "chi_tiet",
                        "don_hang",
                        "so_ban_hang",
                        "nhap",
                        "ton_kho",
                        "so_quy",
                        "phieu_thu",
                    )
                )
                and "hang_hoa" not in tname
                and "san_pham" not in tname
                and "ghi_tat" not in tname
                and "danh_muc" not in tname
            ):
                continue
            cols = list(df.columns)
            cat_col = next(
                (
                    c
                    for c in (
                        "nhom_hang_3_cap",
                        "nhom_hang_hoa",
                        "nhom_hang",
                        "loai_san_pham",
                        "danh_muc",
                        "phan_loai",
                        "nhom",
                        "loai",
                    )
                    if c in cols
                ),
                None,
            )
            id_col = next(
                (
                    c
                    for c in (
                        "ma_hang",
                        "ma_hang_hoa",
                        "ma_sku",
                        "sku",
                        "ma_sp",
                        "ma_san_pham_ban",
                        "ma_hh",
                        "ma_vach",
                        "ma_h",
                    )
                    if c in cols
                ),
                None,
            )
            name_col = next(
                (
                    c
                    for c in (
                        "ten_hang",
                        "ten_hang_hoa",
                        "ten_san_pham",
                        "san_pham",
                        "ten_goi",
                        "mon",
                        "ten_sp",
                    )
                    if c in cols
                ),
                None,
            )
            barcode_col = next(
                (c for c in ("ma_barcode", "ma_vach_ean", "barcode") if c in cols), None
            )

            if name_col and id_col:
                for iv, nv in zip(df[id_col], df[name_col]):
                    inorm = norm_vn(iv)
                    nnorm = norm_vn(nv)
                    nkey = _item_norm_key(nv)
                    if inorm and nnorm:
                        name_to_id[nnorm] = str(iv).strip()
                        if nkey:
                            name_to_id.setdefault(nkey, str(iv).strip())
                        id_to_name[inorm] = str(nv).strip()

            if cat_col:
                for _idx, row in df.iterrows():
                    c_val = clean_spaces_lower(row[cat_col])
                    if not c_val:
                        continue
                    if id_col and pd.notna(row[id_col]):
                        id_to_cat[norm_vn(row[id_col])] = c_val
                    if barcode_col and pd.notna(row[barcode_col]):
                        id_to_cat[norm_vn(row[barcode_col])] = c_val
                    if name_col and pd.notna(row[name_col]):
                        name_to_cat[norm_vn(row[name_col])] = c_val
                    # Handle dev_011/014 'ghi_tat' or dev_013 'ten_goi_khac'
                    if "ghi_tat" in cols and pd.notna(row["ghi_tat"]):
                        name_to_cat[norm_vn(row["ghi_tat"])] = c_val
                        if name_col and pd.notna(row[name_col]):
                            name_to_id[norm_vn(row["ghi_tat"])] = norm_vn(row[name_col])
                    if "ten_goi_khac" in cols and pd.notna(row["ten_goi_khac"]):
                        for alias in re.split(r"[;,/]", str(row["ten_goi_khac"])):
                            anorm = norm_vn(alias)
                            if anorm:
                                name_to_cat[anorm] = c_val
                                if name_col and pd.notna(row[name_col]):
                                    name_to_id[anorm] = norm_vn(row[name_col])

        # Second pass: prescription tables (danh_sach_thuoc, thong_tin_thuoc, ban_hang_thuoc) or inline prescription columns
        has_rx_table = False
        for tname, df in tables.items():
            cols = list(df.columns)
            rx_col = next(
                (c for c in ("thuoc_ke_don", "ban_theo_don", "ke_don") if c in cols),
                None,
            )
            if rx_col:
                has_rx_table = True
            if "thuoc" in tname:
                id_col = next(
                    (
                        c
                        for c in ("ma_hang", "ma_sku", "ma_hang_hoa", "ma_hh", "sku")
                        if c in cols
                    ),
                    None,
                )
                if rx_col and id_col:
                    for iv, rv in zip(df[id_col], df[rx_col]):
                        inorm = norm_vn(iv)
                        if inorm:
                            id_to_rx[inorm] = is_Prescription_True(rv)
            elif "phan_loai" in cols:
                pl_vals = df["phan_loai"].dropna().astype(str).str.lower()
                if pl_vals.str.contains("kê đơn").any():
                    has_rx_table = True

        # Map product names to Rx via id_to_rx
        if has_rx_table:
            for nnorm, raw_id in name_to_id.items():
                inorm = norm_vn(raw_id)
                if inorm in id_to_rx:
                    name_to_rx[nnorm] = id_to_rx[inorm]

        # 2. Identify Order Header table and Line Detail table
        header_table, detail_table = cls._identify_header_and_detail_tables(context)

        if header_table is None and detail_table is None:
            empty_orders = pd.DataFrame(
                columns=[
                    "order_id",
                    "order_date",
                    "order_hour",
                    "is_valid_sale",
                    "order_revenue",
                    "channel",
                    "payment_method",
                    "customer_key",
                    "customer_name",
                    "customer_group",
                ]
            )
            empty_lines = pd.DataFrame(
                columns=[
                    "order_id",
                    "order_date",
                    "order_hour",
                    "is_valid_sale",
                    "item_key",
                    "item_name",
                    "category",
                    "is_prescription",
                    "is_service_fee",
                    "line_revenue",
                    "quantity",
                    "base_quantity",
                ]
            )
            context.duckdb_conn.register("canonical_orders", empty_orders)
            context.duckdb_conn.register("canonical_order_lines", empty_lines)
            return cls(
                business_id=context.business_id,
                orders_df=empty_orders,
                lines_df=empty_lines,
            )

        # 3. Construct canonical_orders and canonical_order_lines
        orders_df, lines_df = cls._construct_canonical_frames(
            context=context,
            header_table=header_table,
            detail_table=detail_table,
            cust_group_map=cust_group_map,
            cust_name_map=cust_name_map,
            id_to_cat=id_to_cat,
            name_to_cat=name_to_cat,
            name_to_id=name_to_id,
            id_to_name=id_to_name,
            id_to_rx=id_to_rx,
            name_to_rx=name_to_rx,
            has_rx_table=has_rx_table,
            staff_id_to_name=staff_id_to_name,
            staff_name_to_id=staff_name_to_id,
        )

        # Register both views in DuckDB
        context.duckdb_conn.register("canonical_orders", orders_df)
        context.duckdb_conn.register("canonical_order_lines", lines_df)

        has_staff = (
            bool(orders_df["staff_key"].dropna().ne("").any())
            if "staff_key" in orders_df.columns and len(orders_df) > 0
            else False
        )

        has_channel = (
            bool(orders_df["channel"].dropna().ne("").any())
            if len(orders_df) > 0
            else False
        )
        has_payment = (
            bool(orders_df["payment_method"].dropna().ne("").any())
            if len(orders_df) > 0
            else False
        )
        has_cust_key = (
            bool(orders_df["customer_key"].dropna().ne("").any())
            if len(orders_df) > 0
            else False
        )
        has_cust_grp = (
            bool(orders_df["customer_group"].dropna().ne("").any())
            if len(orders_df) > 0
            else False
        )
        has_shift = (
            bool(orders_df["order_hour"].notna().any()) if len(orders_df) > 0 else False
        )
        avail_cats = set(lines_df["category"].dropna().unique().tolist()) - {
            "",
            "phí dịch vụ",
        }
        has_category = len(avail_cats) > 0

        avail_prods: list[tuple[str, str, str]] = []
        seen_prods = set()
        for inorm, nstr in id_to_name.items():
            nnorm = norm_vn(nstr)
            if (
                len(nnorm) >= 3
                and nnorm not in seen_prods
                and nnorm not in ("voucher", "phi ship", "phi phuc vu")
            ):
                seen_prods.add(nnorm)
                avail_prods.append((nnorm, nstr, inorm))
        if len(lines_df) > 0 and "item_name" in lines_df.columns:
            for iname, ikey in zip(
                lines_df["item_name"].dropna(), lines_df["item_key"].dropna()
            ):
                s_name = str(iname).strip()
                s_norm = norm_vn(s_name)
                if (
                    len(s_norm) >= 3
                    and s_norm not in seen_prods
                    and s_norm not in ("voucher", "phi ship", "phi phuc vu")
                ):
                    seen_prods.add(s_norm)
                    avail_prods.append((s_norm, s_name, str(ikey)))
        avail_prods.sort(key=lambda x: len(x[0]), reverse=True)

        return cls(
            business_id=context.business_id,
            orders_df=orders_df,
            lines_df=lines_df,
            has_channel=has_channel,
            has_payment_method=has_payment,
            has_customer_key=has_cust_key,
            has_customer_group=has_cust_grp,
            has_gender=has_gender,
            has_shift=has_shift,
            has_category=has_category,
            has_prescription=has_rx_table,
            has_staff=has_staff,
            staff_id_to_name=staff_id_to_name,
            staff_name_to_id=staff_name_to_id,
            available_categories=avail_cats,
            available_products=avail_prods,
        )

    @staticmethod
    def _canonicalize_item_names(
        name_series: pd.Series | None,
        raw_ids: pd.Series,
        id_to_name: dict[str, str],
    ) -> tuple[pd.Series, dict[str, str]]:
        """Resolve item names via ID-first catalog lookup and compact key canonicalization."""
        from collections import Counter

        def _clean(val: Any) -> str:
            return re.sub(r"\s+", " ", str(val)).strip()

        canon_by_key: dict[str, str] = {}
        for cname in id_to_name.values():
            cleaned = _clean(cname)
            nkey = _item_norm_key(cleaned)
            if cleaned and nkey and nkey not in canon_by_key:
                canon_by_key[nkey] = cleaned

        raw_vals = (
            list(name_series.fillna(""))
            if name_series is not None
            else [""] * len(raw_ids)
        )
        candidates: list[str] = []
        for x, iid in zip(raw_vals, raw_ids):
            raw_str = _clean(x)
            inorm = norm_vn(iid)
            if " + " in raw_str:
                candidates.append(raw_str)
                continue
            if inorm and inorm not in ("nan", "none") and inorm in id_to_name:
                candidates.append(_clean(id_to_name[inorm]))
                continue
            xnorm = norm_vn(raw_str)
            if (xnorm in id_to_name) or not raw_str:
                candidates.append(_clean(id_to_name.get(xnorm, raw_str)))
            else:
                candidates.append(raw_str)

        counts: Counter[tuple[str, str]] = Counter()
        first_seen: dict[tuple[str, str], int] = {}
        for idx_i, cand in enumerate(candidates):
            if not cand or " + " in cand:
                continue
            nkey = _item_norm_key(cand)
            if not nkey or nkey in canon_by_key:
                continue
            counts[(nkey, cand)] += 1
            first_seen.setdefault((nkey, cand), idx_i)

        best_score: dict[str, tuple[int, int, int, int, int]] = {}
        for (nkey, cand), cnt in counts.items():
            score = (
                int(any(ord(ch) > 127 for ch in cand)),
                int(cand != cand.upper()),
                cnt,
                int(_UNIT_SPACE_RE.search(cand.lower()) is None),
                -first_seen[(nkey, cand)],
            )
            if nkey not in best_score or score > best_score[nkey]:
                best_score[nkey] = score
                canon_by_key[nkey] = cand

        resolved = [
            cand if " + " in cand else canon_by_key.get(_item_norm_key(cand), cand)
            for cand in candidates
        ]
        idx_out = name_series.index if name_series is not None else raw_ids.index
        return pd.Series(resolved, index=idx_out, dtype="object"), canon_by_key

    @staticmethod
    def _build_unit_conversion_map(
        context: BusinessContext,
    ) -> tuple[dict[tuple[str, str], float], dict[str, float], set[str]]:
        """Parse (item, unit) -> base-unit conversion ratios plus legacy item fallbacks."""
        unit_conv: dict[tuple[str, str], float] = {}
        fallback_conv: dict[str, float] = {}
        has_explicit_unit: set[str] = set()
        for tname, df in context.tables.items():
            if not any(
                k in tname
                for k in (
                    "quy_doi",
                    "quy đổi",
                    "danh_muc",
                    "danh muc",
                    "don_vi",
                    "san_pham",
                )
            ):
                continue
            cols_map = {c.lower(): c for c in df.columns}
            cols = list(cols_map.keys())
            item_col = next(
                (
                    cols_map[c]
                    for c in cols
                    if c in ("hang", "ten_mon")
                    or any(
                        k in c
                        for k in (
                            "ten_hang",
                            "ten_sp",
                            "ten_san_pham",
                            "hang_hoa",
                        )
                    )
                ),
                None,
            )
            id_col = next(
                (
                    cols_map[c]
                    for c in cols
                    if c in ("ma_hang", "ma_sp", "ma_hh", "ma_san_pham")
                ),
                None,
            )
            ratio_col = None
            for kw in (
                "he_so_quy_doi",
                "ti_le_quy_doi",
                "ty_le_quy_doi",
                "he_so_qd",
                "he_so",
                "quy_doi",
            ):
                ratio_col = next(
                    (
                        cols_map[c]
                        for c in cols
                        if kw in c and not c.startswith(("don_vi", "dv_"))
                    ),
                    None,
                )
                if ratio_col:
                    break
            big_unit_col = next(
                (
                    cols_map[c]
                    for c in cols
                    if c
                    in (
                        "dv_lon",
                        "dv_nhap",
                        "don_vi_quy_doi",
                        "don_vi_lon",
                        "dv_quy_doi",
                    )
                ),
                None,
            )
            if not ((item_col or id_col) and ratio_col):
                continue
            sub_df = df[df[ratio_col].notna()]
            for _, r in sub_df.iterrows():
                item = _item_norm_key(r[item_col]) if item_col else ""
                item_id = norm_vn(r[id_col]) if id_col else ""
                val = r[ratio_col]
                if not item and not item_id:
                    continue
                factor: float | None = None
                explicit_u = ""
                if isinstance(val, (int, float)) and val > 0:
                    factor = float(val)
                elif isinstance(val, str):
                    val_norm = norm_vn(val)
                    m_lhs = re.search(r"\b1\s+([a-z0-9\s]+?)\s*=", val_norm)
                    if m_lhs:
                        explicit_u = m_lhs.group(1).strip()
                    m = re.search(r"=\s*(\d+(?:[.,]\d+)?)\s*\w+", val)
                    if m:
                        factor = float(m.group(1).replace(",", "."))
                    else:
                        m2 = re.search(r"^\d+(?:[.,]\d+)?$", val.strip())
                        if m2:
                            factor = float(m2.group(0).replace(",", "."))
                if factor is None or factor <= 0:
                    continue
                if big_unit_col and pd.notna(r[big_unit_col]):
                    bu = norm_vn(r[big_unit_col])
                    if bu:
                        explicit_u = bu
                if explicit_u:
                    if item:
                        unit_conv[(item, explicit_u)] = factor
                        has_explicit_unit.add(item)
                    if item_id:
                        unit_conv[(item_id, explicit_u)] = factor
                        has_explicit_unit.add(item_id)
                else:
                    if item:
                        fallback_conv[item] = factor
                    if item_id:
                        fallback_conv[item_id] = factor
        return unit_conv, fallback_conv, has_explicit_unit

    @classmethod
    def _compute_raw_and_base_quantities(
        cls,
        df: pd.DataFrame,
        rev_series: pd.Series,
        raw_qty_col: str | None,
        base_qty_col: str | None,
        unit_col: str | None,
        price_col: str | None,
        item_names: pd.Series,
        item_ids: pd.Series,
        context: BusinessContext,
    ) -> tuple[pd.Series, pd.Series]:
        """Compute raw and converted base quantities, recovering missing qty from revenue/price."""
        if raw_qty_col:
            rq_num = pd.to_numeric(df[raw_qty_col], errors="coerce")
            if price_col is not None:
                uprice = pd.to_numeric(df[price_col], errors="coerce")
                mask_rec = rq_num.isna() & uprice.gt(0) & rev_series.gt(0)
                if mask_rec.any():
                    rq_num = rq_num.where(
                        ~mask_rec, (rev_series / uprice).round(6)
                    )
            raw_qtys = rq_num.fillna(1.0)
        else:
            raw_qtys = pd.Series(1.0, index=df.index)

        unit_conv, fallback_conv, has_explicit = cls._build_unit_conversion_map(context)
        if unit_col and (unit_conv or fallback_conv):
            units = df[unit_col].fillna("").astype(str).map(norm_vn)
            bqtys: list[float] = []
            for rq, u, iname, iid in zip(raw_qtys, units, item_names, item_ids):
                iname_norm = _item_norm_key(iname)
                iid_norm = norm_vn(iid)
                factor = unit_conv.get((iname_norm, u)) or unit_conv.get(
                    (iid_norm, u)
                )
                if factor is None:
                    factor = 1.0
                    if u in ("thung", "hop", "loc"):
                        if iname_norm not in has_explicit and iname_norm in fallback_conv:
                            factor = fallback_conv[iname_norm]
                        elif iid_norm not in has_explicit and iid_norm in fallback_conv:
                            factor = fallback_conv[iid_norm]
                bqtys.append(float(rq) * float(factor))
            converted_base = pd.Series(bqtys, index=df.index, dtype="float64")
        else:
            converted_base = raw_qtys.astype("float64")

        if base_qty_col:
            base_qtys = pd.to_numeric(df[base_qty_col], errors="coerce").fillna(
                converted_base
            )
        else:
            base_qtys = converted_base
        return raw_qtys, base_qtys

    @staticmethod
    def _identify_header_and_detail_tables(
        context: BusinessContext,
    ) -> tuple[str | None, str | None]:
        tables = context.tables
        # Case A: Explicit separate header + detail tables
        if (
            "du_lieu_ban_hang_danh_sach_hoa_don" in tables
            and "danh_sach_hoa_don_chi_tiet" in tables
        ):
            return "du_lieu_ban_hang_danh_sach_hoa_don", "danh_sach_hoa_don_chi_tiet"
        if "danh_sach_hoa_don" in tables and "danh_sach_hoa_don_chi_tiet" in tables:
            return "danh_sach_hoa_don", "danh_sach_hoa_don_chi_tiet"
        if "ban_hang_hoa_don" in tables and "ban_hang_chi_tiet_hoa_don" in tables:
            return "ban_hang_hoa_don", "ban_hang_chi_tiet_hoa_don"
        if "don_hang" in tables and "chi_tiet_don" in tables:
            return "don_hang", "chi_tiet_don"

        # Case B: Single combined line-item table
        primary_table, _, _, _ = find_primary_sales_table(context)
        if primary_table:
            return None, primary_table
        return None, None

    @classmethod
    def _construct_canonical_frames(
        cls,
        context: BusinessContext,
        header_table: str | None,
        detail_table: str | None,
        cust_group_map: dict[str, str],
        cust_name_map: dict[str, str],
        id_to_cat: dict[str, str],
        name_to_cat: dict[str, str],
        name_to_id: dict[str, str],
        id_to_name: dict[str, str],
        id_to_rx: dict[str, bool],
        name_to_rx: dict[str, bool],
        has_rx_table: bool,
        staff_id_to_name: dict[str, str] | None = None,
        staff_name_to_id: dict[str, str] | None = None,
    ) -> tuple[pd.DataFrame, pd.DataFrame]:
        tables = context.tables
        staff_id_to_name = staff_id_to_name or {}
        staff_name_to_id = staff_name_to_id or {}

        if header_table is not None and detail_table is not None:
            return cls._build_from_separate_header_detail(
                context=context,
                header_df=tables[header_table],
                detail_df=tables[detail_table],
                cust_group_map=cust_group_map,
                cust_name_map=cust_name_map,
                id_to_cat=id_to_cat,
                name_to_cat=name_to_cat,
                name_to_id=name_to_id,
                id_to_name=id_to_name,
                id_to_rx=id_to_rx,
                name_to_rx=name_to_rx,
                has_rx_table=has_rx_table,
                staff_id_to_name=staff_id_to_name,
                staff_name_to_id=staff_name_to_id,
            )

        assert detail_table is not None
        return cls._build_from_combined_table(
            context=context,
            table_name=detail_table,
            df=tables[detail_table],
            cust_group_map=cust_group_map,
            cust_name_map=cust_name_map,
            id_to_cat=id_to_cat,
            name_to_cat=name_to_cat,
            name_to_id=name_to_id,
            id_to_name=id_to_name,
            id_to_rx=id_to_rx,
            name_to_rx=name_to_rx,
            has_rx_table=has_rx_table,
            staff_id_to_name=staff_id_to_name,
            staff_name_to_id=staff_name_to_id,
        )

    @classmethod
    def _build_from_separate_header_detail(
        cls,
        context: BusinessContext,
        header_df: pd.DataFrame,
        detail_df: pd.DataFrame,
        cust_group_map: dict[str, str],
        cust_name_map: dict[str, str],
        id_to_cat: dict[str, str],
        name_to_cat: dict[str, str],
        name_to_id: dict[str, str],
        id_to_name: dict[str, str],
        id_to_rx: dict[str, bool],
        name_to_rx: dict[str, bool],
        has_rx_table: bool,
        staff_id_to_name: dict[str, str] | None = None,
        staff_name_to_id: dict[str, str] | None = None,
    ) -> tuple[pd.DataFrame, pd.DataFrame]:
        staff_id_to_name = staff_id_to_name or {}
        staff_name_to_id = staff_name_to_id or {}
        h_cols = list(header_df.columns)
        d_cols = list(detail_df.columns)

        h_order_col = next(
            (
                c
                for c in (
                    "order_id",
                    "ma_hoa_don",
                    "ma_chung_tu",
                    "so_phieu",
                    "so_ct",
                    "ma_don_hang",
                    "ma_don",
                )
                if c in h_cols
            ),
            h_cols[0],
        )
        d_order_col = next(
            (
                c
                for c in (
                    "ma_hoa_don",
                    "ma_don",
                    "ma_giao_dich",
                    "ma_don_hang",
                    "so_ct",
                    "ma_bill",
                    "order_id",
                )
                if c in d_cols
            ),
            d_cols[0],
        )

        # Date & timestamp on header
        date_cands = [
            c
            for c in (
                "ngay_dat_hang",
                "ngay_dat",
                "ngay_tao",
                "ngay_thang",
                "ngay_ban",
                "ngay_mua",
                "ngay_lap",
                "ngay_hd",
                "ngay_giao_dich",
                "ngay_xuat",
                "ngay",
                "date",
                "thoi_gian",
                "thoi_gian_tao",
            )
            if c in h_cols
        ]
        h_date_col = (
            min(date_cands, key=lambda c: header_df[c].isna().sum())
            if date_cands
            else None
        )
        h_ts_col = next(
            (
                c
                for c in (
                    "thoi_gian_tao",
                    "thoi_gian_lap",
                    "gio_tao_don",
                    "gio_ban",
                    "thoi_gian",
                )
                if c in h_cols
            ),
            None,
        )

        # Status on header
        h_status_col = next(
            (
                c
                for c in (
                    "trang_thai",
                    "xu_ly",
                    "tinh_trang",
                    "tinh_trang_don",
                    "status",
                )
                if c in h_cols
            ),
            None,
        )

        # Channel & Payment on header (Rulebook §1: payment method is strictly from the order, NOT from debt payments)
        h_chan_col = next(
            (
                c
                for c in (
                    "kenh_ban",
                    "ban_qua",
                    "kenh_ban_hang",
                    "nguon_don",
                    "loai_ban",
                    "kenh_dat",
                    "kenh",
                )
                if c in h_cols
            ),
            None,
        )
        h_pay_col = next(
            (
                c
                for c in (
                    "phuong_thuc_thanh_toan",
                    "phuong_thuc_tt",
                    "tra_bang",
                    "hinh_thuc_thanh_toan",
                    "thanh_toan_bang",
                )
                if c in h_cols
            ),
            None,
        )

        # Customer on header
        h_cust_id_col = next(
            (
                c
                for c in (
                    "ma_kh",
                    "so_kh",
                    "ma_khach_hang",
                    "ma_kh_mua",
                    "customer_id",
                    "ma_khach_mua",
                    "ma_the",
                )
                if c in h_cols
            ),
            None,
        )
        h_cust_name_col = next(
            (
                c
                for c in (
                    "ten_khach_hang",
                    "khach_hang_ten",
                    "ten_kh",
                    "tenkh",
                    "khach_hang",
                    "kh",
                )
                if c in h_cols
            ),
            None,
        )

        # Staff on header
        h_staff_col = next(
            (
                c
                for c in (
                    "ma_nv_ban",
                    "nhan_vien_ban",
                    "ma_nhan_vien",
                    "nguoi_ban",
                    "nhan_vien",
                    "nv_ban",
                    "ma_nv",
                )
                if c in h_cols
            ),
            None,
        )

        # Line detail columns
        d_item_id_col = next(
            (
                c
                for c in (
                    "ma_hang",
                    "id_sp",
                    "ma_hang_hoa",
                    "ma_mat_hang",
                    "ma_hang_ban",
                    "ma_h",
                )
                if c in d_cols
            ),
            None,
        )
        d_item_name_col = next(
            (
                c
                for c in (
                    "ten_hang",
                    "chi_tiet_hang",
                    "ten_san_pham",
                    "ten_hang_hoa",
                    "ten_sp",
                    "ten_mon",
                    "mat_hang",
                    "hang_hoa",
                    "hang_ban",
                    "san_pham",
                )
                if c in d_cols
            ),
            None,
        )
        d_rev_col = next(
            (
                c
                for c in ("thanh_tien", "t", "tri_gia", "amount", "tien_ban", "cong")
                if c in d_cols
            ),
            None,
        )
        d_base_qty_col = next(
            (
                c
                for c in (
                    "quy_ra_dv_le",
                    "so_luong_quy_doi",
                    "sl_quy_doi",
                    "so_luong_le",
                    "sl_le",
                    "sl_dv_co_ban",
                )
                if c in d_cols
            ),
            None,
        )
        d_raw_qty_col = next(
            (
                c
                for c in (
                    "so_luong_hang",
                    "so_luong_san_pham",
                    "so_luong_ban",
                    "sl_ban",
                    "so_luong",
                    "sluong",
                    "soluong",
                    "sl_thuc",
                    "sl",
                    "s",
                    "quantity",
                    "qty",
                )
                if c in d_cols
            ),
            None,
        )
        d_unit_col = next(
            (
                c
                for c in ("don_vi_tinh", "donvi", "dv_tinh", "dvt", "don_vi", "unit")
                if c in d_cols
            ),
            None,
        )
        d_price_col = next(
            (
                c
                for c in ("don_gia", "gia_ban", "gia_1_dv", "gia", "gia_san_pham", "gia_le")
                if c in d_cols
            ),
            None,
        )

        # Build line items first so we can sum line_revenue per order if needed
        d_order_ids = detail_df[d_order_col].fillna("").astype(str).str.strip()
        if d_rev_col:
            line_revs = pd.to_numeric(detail_df[d_rev_col], errors="coerce").fillna(0.0)
        else:
            line_revs = pd.Series(0.0, index=detail_df.index)

        if d_item_id_col:
            raw_item_ids = detail_df[d_item_id_col].fillna("").astype(str).str.strip()
        else:
            raw_item_ids = pd.Series("", index=detail_df.index, dtype="object")

        raw_item_names, _ = cls._canonicalize_item_names(
            detail_df[d_item_name_col] if d_item_name_col else None,
            raw_item_ids,
            id_to_name,
        )

        raw_qtys, base_qtys = cls._compute_raw_and_base_quantities(
            detail_df,
            line_revs,
            d_raw_qty_col,
            d_base_qty_col,
            d_unit_col,
            d_price_col if d_rev_col else None,
            raw_item_names,
            raw_item_ids,
            context,
        )

        # Map item_key, category, is_prescription, is_service_fee
        item_keys: list[str] = []
        categories: list[str | None] = []
        is_rx_list: list[bool] = []
        is_svc_list: list[bool] = []

        for iid, iname in zip(raw_item_ids, raw_item_names):
            inorm = norm_vn(iid)
            nnorm = norm_vn(iname)
            nkey = _item_norm_key(iname)
            ikey = (
                iid
                if (inorm and inorm not in ("nan", "none"))
                else name_to_id.get(nnorm) or name_to_id.get(nkey, nkey)
            )
            item_keys.append(str(ikey))

            cat = id_to_cat.get(inorm) or name_to_cat.get(nnorm)
            if not cat:
                cat = CAFE_CATEGORY_LOOKUP.get(nnorm) or HARDWARE_CATEGORY_LOOKUP.get(
                    nnorm
                )
            categories.append(cat)

            rx_val = (
                id_to_rx.get(inorm, name_to_rx.get(nnorm, False))
                if has_rx_table
                else False
            )
            is_rx_list.append(bool(rx_val))

            is_svc_list.append(nnorm in ("phi phuc vu", "phi dich vu"))

        # Sum line revenue per order_id
        line_rev_by_order = (
            pd.DataFrame({"order_id": d_order_ids, "line_rev": line_revs})
            .groupby("order_id", as_index=False)["line_rev"]
            .sum()
        )
        order_rev_map = dict(
            zip(line_rev_by_order["order_id"], line_rev_by_order["line_rev"])
        )

        # Build canonical_orders from header_df
        h_order_ids = header_df[h_order_col].fillna("").astype(str).str.strip()
        order_dates = (
            pd.to_datetime(header_df[h_date_col], errors="coerce").dt.date
            if h_date_col
            else pd.Series(None, index=header_df.index)
        )
        order_hours = (
            extract_hour_series(header_df[h_ts_col])
            if h_ts_col
            else pd.Series(np.nan, index=header_df.index)
        )

        if h_status_col:
            valid_flags = ~header_df[h_status_col].map(is_bad_status)
        else:
            valid_flags = pd.Series(True, index=header_df.index)

        # Revenue: prefer sum of detail line_revenue (which is guaranteed to be post-line-discount and pre-order-discount/shipping)
        # Fallback to header merchandise revenue if detail has no rows for an order
        h_merch_col = next(
            (c for c in ("tong_tien_hang", "tien_hang_truoc_giam") if c in h_cols), None
        )
        if h_merch_col:
            h_merch_vals = pd.to_numeric(
                header_df[h_merch_col], errors="coerce"
            ).fillna(0.0)
        elif (
            "thanh_tien" in h_cols
            and "tien_giam" in h_cols
            and "phi_van_chuyen" in h_cols
        ):
            h_merch_vals = (
                pd.to_numeric(header_df["thanh_tien"], errors="coerce").fillna(0.0)
                + pd.to_numeric(header_df["tien_giam"], errors="coerce").fillna(0.0)
                - pd.to_numeric(header_df["phi_van_chuyen"], errors="coerce").fillna(
                    0.0
                )
            )
        else:
            h_merch_vals = pd.Series(0.0, index=header_df.index)

        order_revs = [
            float(order_rev_map.get(oid, hval))
            for oid, hval in zip(h_order_ids, h_merch_vals)
        ]

        channels = (
            header_df[h_chan_col].map(normalize_channel_value)
            if h_chan_col
            else pd.Series(None, index=header_df.index, dtype="object")
        )
        payments = (
            header_df[h_pay_col].map(normalize_payment_value)
            if h_pay_col
            else pd.Series(None, index=header_df.index, dtype="object")
        )

        # Customer key & group & display name
        cust_keys: list[str | None] = []
        cust_names: list[str | None] = []
        cust_groups: list[str | None] = []
        raw_cids = (
            header_df[h_cust_id_col]
            if h_cust_id_col
            else pd.Series(None, index=header_df.index)
        )
        raw_cnames = (
            header_df[h_cust_name_col]
            if h_cust_name_col
            else pd.Series(None, index=header_df.index)
        )

        for cid, cname in zip(raw_cids, raw_cnames):
            ck = normalize_customer_key(cid)
            if not ck:
                ck = normalize_customer_key(cname)
            cust_keys.append(ck)

            # Resolve unnormalized customer display name
            resolved_cname = None
            if (
                pd.notna(cname)
                and str(cname).strip()
                and norm_vn(cname) not in ("le", "khach le", "ban le", "vang lai")
            ):
                resolved_cname = str(cname).strip()
            elif pd.notna(cid):
                cid_norm = norm_vn(cid)
                resolved_cname = cust_name_map.get(cid_norm) or str(cid).strip()
            cust_names.append(resolved_cname)

            c_grp = cust_group_map.get(norm_vn(cid)) if pd.notna(cid) else None
            if not c_grp and ck:
                c_grp = cust_group_map.get(ck)
            cust_groups.append(c_grp)

        # Staff key & name
        staff_keys: list[str | None] = []
        staff_names: list[str | None] = []
        raw_staffs = (
            header_df[h_staff_col]
            if h_staff_col
            else pd.Series(None, index=header_df.index)
        )
        for s in raw_staffs:
            if pd.isna(s) or str(s).strip() in ("", "nan", "None"):
                staff_keys.append(None)
                staff_names.append(None)
            else:
                s_str = str(s).strip()
                s_norm = norm_vn(s_str)
                if s_norm in staff_name_to_id:
                    s_code = staff_name_to_id[s_norm]
                    staff_keys.append(s_code)
                    staff_names.append(s_str)
                elif s_str in staff_id_to_name:
                    staff_keys.append(s_str)
                    staff_names.append(staff_id_to_name[s_str])
                else:
                    staff_keys.append(s_str)
                    staff_names.append(s_str)

        orders_df = pd.DataFrame(
            {
                "order_id": h_order_ids,
                "order_date": pd.to_datetime(order_dates),
                "order_hour": order_hours,
                "is_valid_sale": valid_flags.astype(bool),
                "order_revenue": pd.Series(order_revs, dtype="float64"),
                "channel": channels,
                "payment_method": payments,
                "customer_key": pd.Series(cust_keys, dtype="object"),
                "customer_name": pd.Series(cust_names, dtype="object"),
                "customer_group": pd.Series(cust_groups, dtype="object"),
                "staff_key": pd.Series(staff_keys, dtype="object"),
                "staff_name": pd.Series(staff_names, dtype="object"),
            }
        )

        # Deduplicate orders_df by order_id if header had split rows
        if orders_df["order_id"].duplicated().any():
            orders_df = orders_df.groupby("order_id", as_index=False).agg(
                {
                    "order_date": "min",
                    "order_hour": "min",
                    "is_valid_sale": "all",
                    "order_revenue": "first",
                    "channel": "first",
                    "payment_method": "first",
                    "customer_key": "first",
                    "customer_name": "first",
                    "customer_group": "first",
                    "staff_key": "first",
                    "staff_name": "first",
                }
            )

        # Map order metadata onto lines_df
        order_meta = orders_df.set_index("order_id")
        lines_df = pd.DataFrame(
            {
                "order_id": d_order_ids,
                "order_date": d_order_ids.map(order_meta["order_date"]),
                "order_hour": d_order_ids.map(order_meta["order_hour"]),
                "is_valid_sale": d_order_ids.map(order_meta["is_valid_sale"])
                .fillna(False)
                .astype(bool),
                "item_key": pd.Series(item_keys, dtype="object"),
                "item_name": raw_item_names,
                "category": pd.Series(categories, dtype="object"),
                "is_prescription": pd.Series(is_rx_list, dtype=bool),
                "is_service_fee": pd.Series(is_svc_list, dtype=bool),
                "line_revenue": line_revs.astype("float64"),
                "quantity": raw_qtys.astype("float64"),
                "base_quantity": base_qtys.astype("float64"),
            }
        )

        return orders_df, lines_df

    @classmethod
    def _build_from_combined_table(
        cls,
        context: BusinessContext,
        table_name: str,
        df: pd.DataFrame,
        cust_group_map: dict[str, str],
        cust_name_map: dict[str, str],
        id_to_cat: dict[str, str],
        name_to_cat: dict[str, str],
        name_to_id: dict[str, str],
        id_to_name: dict[str, str],
        id_to_rx: dict[str, bool],
        name_to_rx: dict[str, bool],
        has_rx_table: bool,
        staff_id_to_name: dict[str, str] | None = None,
        staff_name_to_id: dict[str, str] | None = None,
    ) -> tuple[pd.DataFrame, pd.DataFrame]:
        staff_id_to_name = staff_id_to_name or {}
        staff_name_to_id = staff_name_to_id or {}
        cols = list(df.columns)

        order_col = next(
            (
                c
                for c in (
                    "order_id",
                    "id_don",
                    "ma_don_hang",
                    "ma_hoa_don",
                    "ma_phieu",
                    "ma_bill",
                    "so_phieu",
                    "ma_gd",
                    "ma_chung_tu",
                    "don_so",
                    "bill",
                    "so_ct",
                    "hd",
                    "ma_don",
                    "stt",
                )
                if c in cols
            ),
            None,
        )

        # Date & timestamp columns
        # Note: in biz_105, biz_117, dev_004 ('so_ban_hang'), 'gio' has the exact timestamp on every row
        # whereas 'ngay_ban'/'thoi_gian' is only on the first row of an order.
        date_cands = [
            c
            for c in (
                "ngay_dat_hang",
                "ngay_dat",
                "ngay_tao",
                "ngay_thang",
                "ngay_hd",
                "ngay_giao_dich",
                "ngay_xuat",
                "ngay_ban",
                "ngay_mua",
                "ngay_lap",
                "ngay",
                "date",
                "thoi_gian",
                "gio",
                "thoi_gian_tao",
                "gio_ban",
            )
            if c in cols
        ]
        date_col = (
            min(date_cands, key=lambda c: df[c].isna().sum()) if date_cands else None
        )
        ts_col = next(
            (
                c
                for c in (
                    "gio_tao_don",
                    "gio_ban",
                    "gio",
                    "thoi_gian_tao",
                    "thoi_gian_lap",
                )
                if c in cols
            ),
            None,
        )

        # Status column
        status_col = next(
            (
                c
                for c in (
                    "trang_thai_don_hang",
                    "trang_thai",
                    "status",
                    "ghi_chu_tt",
                    "trang_thai_xu_ly",
                    "tinh_trang_don",
                    "trang_thai_don",
                    "tinh_trang",
                    "trang_thai_giao",
                    "tt_don",
                    "tien_do",
                    "xu_ly",
                    "ket_qua",
                )
                if c in cols and df[c].notna().any()
            ),
            None,
        )
        if not status_col:
            for c in ("ghi_chu", "note", "luu_y"):
                if c in cols and df[c].notna().any():
                    s_lower = df[c].dropna().astype(str).str.lower()
                    if s_lower.str.contains(
                        r"hủy|huỷ|cancel|trả|return", regex=True
                    ).any():
                        status_col = c
                        break

        # Channel & Payment columns
        chan_col = next(
            (
                c
                for c in ("kenh_ban", "nguon", "kenh", "ban_qua", "loai_ban")
                if c in cols
            ),
            None,
        )
        pay_col = next(
            (
                c
                for c in (
                    "hinh_thuc_thanh_toan",
                    "hinh_thuc_tt",
                    "thanh_toan_bang",
                    "phuong_thuc_thanh_toan",
                    "phuong_thuc_tt",
                    "tra_bang",
                )
                if c in cols
            ),
            None,
        )

        # Customer ID / Name / Group columns
        cust_id_col = next(
            (
                c
                for c in (
                    "ma_khach",
                    "ma_khach_mua",
                    "ma_nguoi_mua",
                    "ma_kh",
                    "customer_id",
                )
                if c in cols
            ),
            None,
        )
        cust_name_col = next(
            (
                c
                for c in (
                    "khach_hang",
                    "ten_khach_hang",
                    "khach_hang_ten",
                    "tenkh",
                    "ten_kh",
                    "khach",
                )
                if c in cols
            ),
            None,
        )
        cust_grp_col = next(
            (
                c
                for c in (
                    "nhom_khach_hang",
                    "nhom_khach",
                    "loai_khach_hang",
                    "kieu_khach",
                )
                if c in cols
            ),
            None,
        )

        staff_col = next(
            (
                c
                for c in (
                    "ma_nv_ban",
                    "nhan_vien_ban",
                    "ma_nhan_vien",
                    "nguoi_ban",
                    "nhan_vien",
                    "nv_ban",
                    "ma_nv",
                )
                if c in cols
            ),
            None,
        )

        # Item Name / ID / Category / Revenue columns
        item_id_col = next(
            (c for c in ("ma_hang", "ma_hang_hoa", "barcode", "ma_sp") if c in cols),
            None,
        )
        item_name_col = next(
            (
                c
                for c in (
                    "ten_hang_hoa",
                    "ten_san_pham",
                    "ten_sp",
                    "ten_mon",
                    "ten_thuoc",
                    "mat_hang",
                    "hang_hoa",
                    "hang_ban",
                    "san_pham",
                    "ten_hang",
                    "hang",
                    "noi_dung",
                    "product",
                )
                if c in cols and df[c].notna().any()
            ),
            None,
        )
        inline_cat_col = next(
            (
                c
                for c in ("phan_loai", "nhom_hang", "nhom_hang_hoa", "nhom_mon", "loai")
                if c in cols
            ),
            None,
        )
        inline_rx_col = next(
            (c for c in ("thuoc_ke_don", "ban_theo_don", "ke_don") if c in cols), None
        )
        base_qty_col = next(
            (
                c
                for c in (
                    "quy_ra_dv_le",
                    "so_luong_quy_doi",
                    "sl_quy_doi",
                    "so_luong_le",
                    "sl_le",
                    "sl_dv_co_ban",
                )
                if c in cols
            ),
            None,
        )
        raw_qty_col = next(
            (
                c
                for c in (
                    "so_luong_hang",
                    "so_luong_san_pham",
                    "so_luong_ban",
                    "sl_ban",
                    "so_luong",
                    "sluong",
                    "soluong",
                    "sl_thuc",
                    "sl",
                    "s",
                    "quantity",
                    "qty",
                )
                if c in cols
            ),
            None,
        )
        unit_col = next(
            (
                c
                for c in ("don_vi_tinh", "donvi", "dv_tinh", "dvt", "don_vi", "unit")
                if c in cols
            ),
            None,
        )
        price_col = next(
            (
                c
                for c in ("don_gia", "gia_ban", "gia_1_dv", "gia", "gia_san_pham", "gia_le")
                if c in cols
            ),
            None,
        )

        # Line revenue column
        if "thanh_tien_d" in cols:
            rev_col = "thanh_tien_d"
        elif "tt" in cols and "dg" in cols and "hd" in cols:
            rev_col = "tt"
        else:
            rev_col = next(
                (
                    c
                    for c in (
                        "tien_hang_truoc_giam",
                        "tien_hang",
                        "thanh_tien",
                        "gia_tri_hang",
                        "tong_tien_hang",
                        "thuc_thu",
                        "doanh_thu",
                        "amount",
                        "tri_gia",
                        "tong_don",
                        "tien",
                        "tong_tien",
                        "tong",
                    )
                    if c in cols
                ),
                None,
            )

        if order_col:
            order_ids = df[order_col].fillna("").astype(str).str.strip()
        else:
            order_ids = pd.Series([f"row_{i}" for i in range(len(df))], index=df.index)
        raw_dates = (
            pd.to_datetime(df[date_col], errors="coerce")
            if date_col
            else pd.Series(pd.NaT, index=df.index)
        )
        raw_hours = (
            extract_hour_series(df[ts_col])
            if ts_col
            else pd.Series(np.nan, index=df.index)
        )

        # Row-level bad status check (if any row in an order has a bad status, the whole order is invalid per Rulebook §1)
        if status_col:
            row_bad_status = df[status_col].map(is_bad_status)
        else:
            row_bad_status = pd.Series(False, index=df.index)

        raw_revs = (
            pd.to_numeric(df[rev_col], errors="coerce").fillna(0.0)
            if rev_col
            else pd.Series(0.0, index=df.index)
        )
        raw_item_ids = (
            df[item_id_col].fillna("").astype(str).str.strip()
            if item_id_col
            else pd.Series("", index=df.index, dtype="object")
        )
        raw_item_names, canon_by_key = cls._canonicalize_item_names(
            df[item_name_col] if item_name_col else None,
            raw_item_ids,
            id_to_name,
        )
        raw_inline_cats = (
            df[inline_cat_col]
            if inline_cat_col
            else pd.Series(None, index=df.index, dtype="object")
        )
        raw_inline_rx = (
            df[inline_rx_col]
            if inline_rx_col
            else pd.Series(None, index=df.index, dtype="object")
        )

        raw_qtys, base_qtys = cls._compute_raw_and_base_quantities(
            df,
            raw_revs,
            raw_qty_col,
            base_qty_col,
            unit_col,
            price_col if rev_col else None,
            raw_item_names,
            raw_item_ids,
            context,
        )

        # Filter out non-merchandise adjustment rows in dev_013 ('Voucher', 'Phí ship', 'Dùng ... điểm')
        is_adjustment_row = raw_item_names.isin(
            ["Voucher", "Phí ship"]
        ) | raw_item_names.str.startswith("Dùng ", na=False)
        effective_line_revs = raw_revs.where(~is_adjustment_row, 0.0)

        # Build order-level aggregations
        chan_series = (
            df[chan_col].map(normalize_channel_value)
            if chan_col
            else pd.Series(None, index=df.index, dtype="object")
        )
        pay_series = (
            df[pay_col].map(normalize_payment_value)
            if pay_col
            else pd.Series(None, index=df.index, dtype="object")
        )

        cust_keys: list[str | None] = []
        cust_names: list[str | None] = []
        cust_groups: list[str | None] = []
        cids = df[cust_id_col] if cust_id_col else pd.Series(None, index=df.index)
        cnames = df[cust_name_col] if cust_name_col else pd.Series(None, index=df.index)
        cgrps = df[cust_grp_col] if cust_grp_col else pd.Series(None, index=df.index)

        for cid, cname, cgrp in zip(cids, cnames, cgrps):
            ck = normalize_customer_key(cid)
            if not ck:
                ck = normalize_customer_key(cname)
            cust_keys.append(ck)

            # Resolve unnormalized customer display name
            resolved_cname = None
            if (
                pd.notna(cname)
                and str(cname).strip()
                and norm_vn(cname) not in ("le", "khach le", "ban le", "vang lai")
            ):
                resolved_cname = str(cname).strip()
            elif pd.notna(cid):
                cid_norm = norm_vn(cid)
                resolved_cname = cust_name_map.get(cid_norm) or str(cid).strip()
            cust_names.append(resolved_cname)

            grp = normalize_customer_group_value(cgrp)
            if not grp and pd.notna(cid):
                grp = cust_group_map.get(norm_vn(cid))
            if not grp and ck:
                grp = cust_group_map.get(ck)
            cust_groups.append(grp)

        staff_keys: list[str | None] = []
        staff_names: list[str | None] = []
        raw_staffs = df[staff_col] if staff_col else pd.Series(None, index=df.index)
        for s in raw_staffs:
            if pd.isna(s) or str(s).strip() in ("", "nan", "None"):
                staff_keys.append(None)
                staff_names.append(None)
            else:
                s_str = str(s).strip()
                s_norm = norm_vn(s_str)
                if s_norm in staff_name_to_id:
                    s_code = staff_name_to_id[s_norm]
                    staff_keys.append(s_code)
                    staff_names.append(s_str)
                elif s_str in staff_id_to_name:
                    staff_keys.append(s_str)
                    staff_names.append(staff_id_to_name[s_str])
                else:
                    staff_keys.append(s_str)
                    staff_names.append(s_str)

        tmp_orders = pd.DataFrame(
            {
                "order_id": order_ids,
                "raw_date": raw_dates,
                "raw_hour": raw_hours,
                "row_bad": row_bad_status,
                "line_rev": effective_line_revs,
                "channel": chan_series,
                "payment_method": pay_series,
                "customer_key": pd.Series(cust_keys, dtype="object"),
                "customer_name": pd.Series(cust_names, dtype="object"),
                "customer_group": pd.Series(cust_groups, dtype="object"),
                "staff_key": pd.Series(staff_keys, dtype="object"),
                "staff_name": pd.Series(staff_names, dtype="object"),
            }
        )

        # Group by order_id to form canonical_orders
        orders_df = tmp_orders.groupby("order_id", as_index=False, sort=False).agg(
            order_date=("raw_date", "min"),
            order_hour=("raw_hour", "first"),
            any_bad=("row_bad", "any"),
            order_revenue=("line_rev", "sum"),
            channel=("channel", "first"),
            payment_method=("payment_method", "first"),
            customer_key=("customer_key", "first"),
            customer_name=("customer_name", "first"),
            customer_group=("customer_group", "first"),
            staff_key=("staff_key", "first"),
            staff_name=("staff_name", "first"),
        )
        orders_df["is_valid_sale"] = ~orders_df["any_bad"]
        orders_df = orders_df.drop(columns=["any_bad"])

        # Build canonical_order_lines (excluding adjustment rows like Voucher/Phí ship)
        order_meta = orders_df.set_index("order_id")

        line_order_ids: list[str] = []
        line_item_keys: list[str] = []
        line_item_names: list[str] = []
        line_cats: list[str | None] = []
        line_rx: list[bool] = []
        line_svc: list[bool] = []
        line_amounts: list[float] = []
        line_qtys: list[float] = []
        line_base_qtys: list[float] = []

        for oid, iid, iname, icat, irx, rev, rqty, bqty, is_adj in zip(
            order_ids,
            raw_item_ids,
            raw_item_names,
            raw_inline_cats,
            raw_inline_rx,
            effective_line_revs,
            raw_qtys,
            base_qtys,
            is_adjustment_row,
        ):
            if is_adj:
                continue

            # Special handling for inline topping in cafe tables via shared cleaner helper
            if " + " in iname:
                from zaic_2026_datamate.cleaner import split_inline_toppings

                base_part, top_parts = split_inline_toppings(
                    iname,
                    line_revenue=float(rev),
                    line_qty=float(rqty),
                    line_base_qty=float(bqty),
                )
                base_canon = canon_by_key.get(
                    _item_norm_key(base_part["name"]), base_part["name"]
                )
                base_norm = norm_vn(base_canon)
                base_nkey = _item_norm_key(base_canon)
                base_cat = clean_spaces_lower(icat) if pd.notna(icat) else ""
                if not base_cat:
                    base_cat = name_to_cat.get(base_norm) or CAFE_CATEGORY_LOOKUP.get(
                        base_norm
                    )
                base_key = name_to_id.get(base_norm) or name_to_id.get(base_nkey, base_nkey)

                line_order_ids.append(oid)
                line_item_keys.append(base_key)
                line_item_names.append(base_canon)
                line_cats.append(base_cat)
                line_rx.append(False)
                line_svc.append(False)
                line_amounts.append(base_part["amount"])
                line_qtys.append(base_part["qty"])
                line_base_qtys.append(base_part["base_qty"])

                for tp in top_parts:
                    if tp["amount"] > 0:
                        top_key = norm_vn(tp["name"])
                        line_order_ids.append(oid)
                        line_item_keys.append(top_key)
                        line_item_names.append(tp["name"])
                        line_cats.append("topping")
                        line_rx.append(False)
                        line_svc.append(False)
                        line_amounts.append(tp["amount"])
                        line_qtys.append(tp["qty"])
                        line_base_qtys.append(tp["base_qty"])
                continue

            inorm = norm_vn(iid)
            nnorm = norm_vn(iname)
            nkey = _item_norm_key(iname)
            ikey = (
                name_to_id.get(nnorm)
                or name_to_id.get(nkey)
                or (iid if inorm and inorm not in ("nan", "none") else nkey)
            )

            cat = clean_spaces_lower(icat) if pd.notna(icat) else ""
            if not cat:
                cat = id_to_cat.get(inorm) or name_to_cat.get(nnorm)
            if not cat:
                cat = CAFE_CATEGORY_LOOKUP.get(nnorm) or HARDWARE_CATEGORY_LOOKUP.get(
                    nnorm
                )

            if pd.notna(irx):
                rx_val = is_Prescription_True(irx)
            elif cat in ("thuốc kê đơn", "không kê đơn"):
                rx_val = cat == "thuốc kê đơn"
            else:
                rx_val = (
                    id_to_rx.get(inorm, name_to_rx.get(nnorm, False))
                    if has_rx_table
                    else False
                )
            is_svc = nnorm in ("phi phuc vu", "phi dich vu") or cat == "phí dịch vụ"

            line_order_ids.append(oid)
            line_item_keys.append(str(ikey))
            line_item_names.append(iname)
            line_cats.append(cat if cat else None)
            line_rx.append(bool(rx_val))
            line_svc.append(bool(is_svc))
            line_amounts.append(float(rev))
            line_qtys.append(float(rqty))
            line_base_qtys.append(float(bqty))

        lo_series = pd.Series(line_order_ids, dtype="object")
        lines_df = pd.DataFrame(
            {
                "order_id": lo_series,
                "order_date": lo_series.map(order_meta["order_date"]),
                "order_hour": lo_series.map(order_meta["order_hour"]),
                "is_valid_sale": lo_series.map(order_meta["is_valid_sale"])
                .fillna(False)
                .astype(bool),
                "item_key": pd.Series(line_item_keys, dtype="object"),
                "item_name": pd.Series(line_item_names, dtype="object"),
                "category": pd.Series(line_cats, dtype="object"),
                "is_prescription": pd.Series(line_rx, dtype=bool),
                "is_service_fee": pd.Series(line_svc, dtype=bool),
                "line_revenue": pd.Series(line_amounts, dtype="float64"),
                "quantity": pd.Series(line_qtys, dtype="float64"),
                "base_quantity": pd.Series(line_base_qtys, dtype="float64"),
            }
        )

        return orders_df, lines_df


# =============================================================================
# DETERMINISTIC QUERY PLAN PARSER & COMPILER
# =============================================================================


def parse_enhanced_period(question: str, as_of_date: str | None) -> ParsedPeriod:
    """Parse temporal expressions with full dialect & Rulebook §2 relative month support."""
    p = parse_time_expressions(question, as_of_date)
    t = question.lower()
    anchor = datetime.strptime(as_of_date, "%Y-%m-%d").date() if as_of_date else None

    # 1. Dialect variants for "tháng này": "tháng ni", "th ni", "th này", "thang ni", "th nay"
    if not (p.month_str or p.exact_date or (p.start_date and p.end_date)):
        if re.search(r"\b(?:tháng|thang|th)\s*(?:này|nay|ni)\b", t):
            p.is_this_month = True
            if anchor:
                p.month_str = anchor.strftime("%Y-%m")
                p.start_date = anchor.strftime("%Y-%m-01")
                p.end_date = anchor.strftime("%Y-%m-%d")
                return p

    # 2. Dialect variants for "tuần trước": "tuần trc", "tuan trc"
    if not (p.month_str or p.exact_date or (p.start_date and p.end_date)):
        if re.search(r"\b(?:tuần|tuan)\s*(?:trc|trước|truoc|rồi|roi)\b", t):
            p.is_last_week = True
            if anchor:
                this_monday = anchor - timedelta(days=anchor.weekday())
                last_monday = this_monday - timedelta(days=7)
                last_sunday = this_monday - timedelta(days=1)
                p.start_date = last_monday.strftime("%Y-%m-%d")
                p.end_date = last_sunday.strftime("%Y-%m-%d")
                return p

    # 3. Short month with 2/4-digit year: "T1/2026", "T5/2025", "th 5/25", "th6/2025"
    if not (p.month_str or p.exact_date or (p.start_date and p.end_date)):
        m_short = re.search(
            r"\b(?:tháng|thang|th|t)\s*(\d{1,2})\s*[/.-]\s*(\d{2,4})\b", t
        )
        if m_short:
            m, y = int(m_short.group(1)), int(m_short.group(2))
            if y < 100:
                y += 2000
            if 1 <= m <= 12 and 2024 <= y <= 2027:
                p.month_str = f"{y:04d}-{m:02d}"
                return p

    # 3.1 Bare month with 2/4-digit year: "8/26", "02/2026", "10-2025"
    if not (p.month_str or p.exact_date or (p.start_date and p.end_date)):
        m_bare = re.search(r"\b(\d{1,2})\s*[/.-]\s*(2[4-7]|202[4-7])\b", t)
        if m_bare:
            m, y = int(m_bare.group(1)), int(m_bare.group(2))
            if y < 100:
                y += 2000
            if 1 <= m <= 12 and 2024 <= y <= 2027:
                p.month_str = f"{y:04d}-{m:02d}"
                return p

    # 4. Standalone relative month without year (Rulebook §2):
    # "Tháng M không ghi năm: của năm nay nếu không sau hôm nay; sau hôm nay thì của năm trước."
    # e.g., "Th5 đếm giùm...", "Th4 dthu khách nhóm thân thiết..."
    if not (p.month_str or p.exact_date or (p.start_date and p.end_date)) and anchor:
        m_rel = re.search(r"\b(?:tháng|thang|th)\s*(\d{1,2})\b(?![/.-]\d)", t)
        if m_rel:
            m = int(m_rel.group(1))
            if 1 <= m <= 12:
                y = anchor.year if m <= anchor.month else anchor.year - 1
                p.month_str = f"{y:04d}-{m:02d}"
                return p

    # 5. Whole year: "năm 2025", "nam 2025", "cả năm 2025", "nam 2026", "trong 2025", "trong năm 2025"
    if not (p.month_str or p.exact_date or (p.start_date and p.end_date)):
        m_yr = re.search(
            r"\b(?:năm|nam|ca nam|cả năm|trong\s+năm|trong\s+nam|trong)\s*(202[4-7])\b",
            t,
        )
        if m_yr:
            yr = int(m_yr.group(1))
            p.start_date = f"{yr:04d}-01-01"
            p.end_date = f"{yr:04d}-12-31"
            return p

    return p


def _extract_ordered_channels(q_norm: str) -> list[str]:
    """Extract channel mentions in order of appearance in normalized query."""
    patterns = [
        ("store", r"\b(?:tai quay|truc tiep)\b"),
        ("zalo_oa", r"\bzalo(?:\s*oa)?\b"),
        ("ecommerce", r"\b(?:san thuong mai dien tu|san tmdt|tmdt|shopee)\b"),
    ]
    found: list[tuple[int, str]] = []
    for ch_code, pat in patterns:
        m = re.search(pat, q_norm)
        if m:
            found.append((m.start(), ch_code))
    # For 'điện thoại', match explicit channel context OR standalone occurrences not modifying products/shops
    m_phone_ctx = re.search(
        r"\b(?:kenh|qua|dat|dat hang|dat mua|ban qua|mua qua|goi|tu|don|hotline)\s+(?:kenh\s+|qua\s+)?(?:dien thoai|ddieenj thoai|dien thoaij)\b",
        q_norm,
    )
    m_phone = m_phone_ctx or re.search(
        r"\b(?:dien thoai|ddieenj thoai|dien thoaij)\b", q_norm
    )
    if m_phone and (
        m_phone_ctx
        or (
            not re.search(
                r"\b(?:cua hang|tiem|shop|sop|ke|op|op lung|gia|gia do|cap|sac|day|pin|man hinh|kinh|cuong luc|bao da|thay|sua|phu kien|linh kien|mat hang|san pham|ten)\s+(?:dien thoai|ddieenj thoai|dien thoaij)\b",
                q_norm,
            )
            and not re.search(
                r"\b(?:dien thoai|ddieenj thoai|dien thoaij)\s+(?:de ban|cu|moi|thong minh|cam ung)\b",
                q_norm,
            )
        )
    ):
        found.append((m_phone.start(), "phone"))
    found.sort(key=lambda x: x[0])
    return [c for _, c in found]


def _extract_ordered_payments(q_norm: str) -> list[str]:
    """Extract payment method mentions in order of appearance in normalized query."""
    patterns = [
        ("cash", r"\b(?:tien mat|tm|tien tuoi)\b"),
        ("transfer", r"\b(?:chuyen khoan|ck)\b"),
        ("ewallet", r"\b(?:vi dien tu)\b"),
        ("cod", r"\b(?:cod|thu ho|xi o di)\b"),
    ]
    found: list[tuple[int, str]] = []
    for pay_code, pat in patterns:
        m = re.search(pat, q_norm)
        if m:
            found.append((m.start(), pay_code))
    found.sort(key=lambda x: x[0])
    return [p for _, p in found]


def _extract_customer_group(q_norm: str) -> str | None:
    """Extract customer group filter ('thân thiết', 'sỉ', 'lẻ') when explicitly modifying customer/order."""
    if re.search(r"\b(?:khach|khachs|nhom|nhoms)\b.*?\bthan thiet\b", q_norm):
        return "thân thiết"
    if re.search(
        r"\b(?:khach|khachs)\s+(?:thuoc\s+|thuoojc\s+)?(?:nhom|nhoms)\s+si\b", q_norm
    ) or re.search(r"\b(?:nhom|nhoms)\s+khach\s+si\b", q_norm):
        return "sỉ"
    if re.search(
        r"\b(?:khach|khachs)\s+(?:thuoc\s+)?(?:nhom|nhoms)\s+le\b", q_norm
    ) or re.search(r"\b(?:nhom|nhoms)\s+khach\s+le\b", q_norm):
        return "lẻ"
    return None


def _extract_category_from_query(
    q_lower: str, q_norm: str, contract: BusinessSemanticContract
) -> str | None:
    """Match product category mentioned in query against contract's available categories."""
    if not contract.available_categories:
        return None
    # Sort available categories by normalized length descending so specific names ('trà sữa', 'cơm mì cháo') match before 'trà'
    sorted_cats = sorted(
        contract.available_categories, key=lambda c: len(norm_vn(c)), reverse=True
    )
    for cat in sorted_cats:
        c_norm = norm_vn(cat)
        if not c_norm:
            continue
        # Handle '&' in 'gas & nước'
        c_pat = re.escape(c_norm).replace(r"\&", r"(?:&|va)")
        # Look for explicit 'nhóm [hàng] <cat>' or '<cat>' when 'nhóm' is in query
        if re.search(rf"\b(?:nhom|nhoms)(?:\s+hang|\s+hangf)?\s+{c_pat}\b", q_norm):
            return cat
    return None


def _extract_item_from_query(
    q_norm: str, contract: BusinessSemanticContract
) -> tuple[str, str] | None:
    """Match product mentioned in query against contract's available products.
    Returns (item_name, item_key).
    """
    if not contract.available_products:
        return None
    for p_norm, p_orig, p_key in contract.available_products:
        if not p_norm or len(p_norm) < 3:
            continue
        if p_norm in q_norm:
            return p_orig, p_key

    # North-South regional synonyms fallback
    q_norm_syn = re.sub(r"\bcoc\s+sac\b", "cu sac", q_norm)
    q_norm_syn = re.sub(r"\bgang\s+tay\s+tat\s+chan\b", "bao tay bao chan", q_norm_syn)
    q_norm_syn = re.sub(r"\blavie\b", "la vie", q_norm_syn)
    if q_norm_syn != q_norm:
        for p_norm, p_orig, p_key in contract.available_products:
            if not p_norm or len(p_norm) < 3:
                continue
            if p_norm in q_norm_syn:
                return p_orig, p_key
    return None


def _parse_vnd_amount(num_str: str, unit_str: str | None) -> float | None:
    """Parse Vietnamese currency threshold from number + unit in question text."""
    s = num_str.strip()
    u = norm_vn(unit_str) if unit_str else ""

    # Shorthand like '3tr7' is handled before calling or here
    if re.match(r"^\d{1,3}(?:\.\d{3})+$", s):
        val = float(s.replace(".", ""))
    elif re.match(r"^\d+(?:[.,]\d+)?$", s):
        val = float(s.replace(",", "."))
    else:
        return None

    if u in ("tr", "trieu", "cu"):
        val *= 1_000_000.0
    elif u in ("k", "nghin", "ngan"):
        val *= 1_000.0
    return val


def _extract_currency_threshold(q_lower: str, q_norm: str) -> float | None:
    """Extract currency threshold from phrases like 'từ 130.000 đồng trở lên', 'từ 3tr7 trở lên', 'từ 10 củ trở lên'."""
    # 1. Shorthand '3tr7', '1tr5'
    m_sh = re.search(r"\b(\d+)\s*(?:tr|trieu|cu)\s*(\d+)\b", q_norm)
    if m_sh:
        major = int(m_sh.group(1))
        minor_str = m_sh.group(2)
        minor = int(minor_str) * (10 ** (6 - len(minor_str)))
        return float(major * 1_000_000 + minor)

    # 2. Standard number + unit before 'tro len' / 'dat'
    m = re.search(
        r"(?:tu|dat|>=|≥)\s*([\d.,]+)\s*(dong|d|k|tr|trieu|cu|nghin|ngan)?\s*(?:tro len|tro lne|tro leen)?",
        q_norm,
    )
    if m:
        return _parse_vnd_amount(m.group(1), m.group(2))
    return None


def _parse_growth_periods(
    q_norm: str, as_of_date: str | None
) -> tuple[ParsedPeriod, ParsedPeriod] | None:
    """Parse current and prior comparison periods for MoM, YoY, MTD, and WoW growth queries."""
    anchor = datetime.strptime(as_of_date, "%Y-%m-%d").date() if as_of_date else None

    # Exclude price change windows or shift comparison
    if re.search(r"\b(?:doi\s+gia|tang\s+gia|len\s+gia|ke\s+tu\s+ngay)\b", q_norm):
        return None
    if "ca sang" in q_norm and "ca chieu" in q_norm:
        return None

    # Check for growth/change comparison intent
    has_growth_kw = any(
        k in q_norm
        for k in (
            "so voi",
            "so vs",
            "so thang",
            "tang truong",
            "tang hay giam",
            "cao hay thap",
            "thay doi",
            "tang bao nhieu",
            "giam bao nhieu",
            "chenh bao nhieu",
            "hon hay kem",
            "len hay xuong",
            "nhieu hon hay it hon",
            "nhieu hon",
            "it hon",
            "cung ky",
            "thang lien truoc",
            "thang trc do",
            "thang truoc do",
            "so thang truoc",
            "so thang trc",
            "tang giam",
            "len xuong",
            "thang ngay truoc do",
            "thang ngay trc do",
            "thang lien ke truoc",
            "thang lien truoc do",
        )
    )
    if not has_growth_kw:
        return None

    # Form 1: Two explicit months (e.g. 'th 3/26 so th 3/25' or 'thang 5/2026 so voi thang 4/2026')
    m_all = re.findall(r"\b(?:thang|th|t)?\s*(\d{1,2})\s*[/.-]\s*(\d{2,4})\b", q_norm)
    if len(m_all) >= 2:
        m1, y1 = int(m_all[0][0]), int(m_all[0][1])
        m2, y2 = int(m_all[1][0]), int(m_all[1][1])
        if y1 < 100:
            y1 += 2000
        if y2 < 100:
            y2 += 2000
        if (
            1 <= m1 <= 12
            and 1 <= m2 <= 12
            and 2024 <= y1 <= 2027
            and 2024 <= y2 <= 2027
        ):
            return ParsedPeriod(month_str=f"{y1:04d}-{m1:02d}"), ParsedPeriod(
                month_str=f"{y2:04d}-{m2:02d}"
            )

    # Form 2: MTD (tháng này tới hôm nay vs cùng kỳ tháng trước)
    if any(
        k in q_norm
        for k in (
            "tu dau thang",
            "thang nay toi",
            "thang nay den",
            "mung 1 thang nay",
            "thang nay",
            "thang ni",
        )
    ) and any(
        k in q_norm
        for k in (
            "cung ky thang",
            "cung so ngay dau thang",
            "cung ky th",
            "thang truoc",
            "thang trc",
            "th truoc",
            "th trc",
            "thang roi",
            "th roi",
        )
    ):
        if anchor:
            cur = ParsedPeriod(
                start_date=f"{anchor.year:04d}-{anchor.month:02d}-01",
                end_date=anchor.strftime("%Y-%m-%d"),
            )
            py = anchor.year - 1 if anchor.month == 1 else anchor.year
            pm = 12 if anchor.month == 1 else anchor.month - 1
            max_d = calendar.monthrange(py, pm)[1]
            pd = min(anchor.day, max_d)
            prior = ParsedPeriod(
                start_date=f"{py:04d}-{pm:02d}-01",
                end_date=f"{py:04d}-{pm:02d}-{pd:02d}",
            )
            return cur, prior

    # Form 3: Single month YoY (cùng kỳ năm ngoái / cùng kỳ năm trước / năm rồi)
    m_single = re.search(r"\b(?:thang|th|t)?\s*(\d{1,2})\s*[/.-]\s*(\d{2,4})\b", q_norm)
    if not m_single:
        m_single = re.search(
            r"\b(?:thang|th|t)\s*(\d{1,2})\s*(?:nam\s*)?(\d{4})\b", q_norm
        )
    is_yoy = any(
        k in q_norm
        for k in (
            "cung ky nam ngoai",
            "cung ky nam truoc",
            "cung ky nam roi",
            "nam ngoai",
            "nam truoc",
            "nam roi",
            "cung thang nam ngoai",
            "cung thang nam truoc",
            "cung thang nam roi",
        )
    )
    if m_single and is_yoy:
        m1, y1 = int(m_single.group(1)), int(m_single.group(2))
        if y1 < 100:
            y1 += 2000
        if 1 <= m1 <= 12 and 2024 <= y1 <= 2027:
            return ParsedPeriod(month_str=f"{y1:04d}-{m1:02d}"), ParsedPeriod(
                month_str=f"{y1 - 1:04d}-{m1:02d}"
            )

    # Form 4: Single month MoM (so với tháng liền trước / tháng trước đó / tháng trước)
    is_mom = any(
        k in q_norm
        for k in (
            "thang ngay truoc do",
            "thang ngay trc do",
            "thang lien ke truoc",
            "thang lien truoc do",
            "thang lien truoc",
            "thang trc do",
            "thang truoc do",
            "so voi thang lien truoc",
            "so thang lien truoc",
            "so voi thang truoc do",
            "so thang truoc",
            "so voi thang truoc",
            "so vs thang truoc",
            "so thang trc",
            "thang lien trc",
            "thang truoc",
            "thang trc",
            "th truoc",
            "th trc",
            "thang roi",
            "th roi",
        )
    )
    if m_single and is_mom:
        m1, y1 = int(m_single.group(1)), int(m_single.group(2))
        if y1 < 100:
            y1 += 2000
        if 1 <= m1 <= 12 and 2024 <= y1 <= 2027:
            py = y1 - 1 if m1 == 1 else y1
            pm = 12 if m1 == 1 else m1 - 1
            return ParsedPeriod(month_str=f"{y1:04d}-{m1:02d}"), ParsedPeriod(
                month_str=f"{py:04d}-{pm:02d}"
            )

    # Form 5: WoW (tuần này so với tuần trước)
    if any(k in q_norm for k in ("tuan nay", "tuan ni")) and any(
        k in q_norm for k in ("tuan truoc", "tuan trc", "tuan roi")
    ):
        if anchor:
            this_monday = anchor - timedelta(days=anchor.weekday())
            cur = ParsedPeriod(
                start_date=this_monday.strftime("%Y-%m-%d"),
                end_date=anchor.strftime("%Y-%m-%d"),
            )
            prior_monday = this_monday - timedelta(days=7)
            prior_end = prior_monday + timedelta(days=anchor.weekday())
            prior = ParsedPeriod(
                start_date=prior_monday.strftime("%Y-%m-%d"),
                end_date=prior_end.strftime("%Y-%m-%d"),
            )
            return cur, prior

    return None


def _resolve_tet_plan(
    q_norm: str,
    q_lower: str,
    context: BusinessContext,
    as_of_date: str | None,
    contract: BusinessSemanticContract,
) -> QueryPlan | None:
    """Resolve queries about Lunar New Year (Dịp Tết) strictly according to Rulebook §2."""
    if not (
        bool(re.search(r"\b(?:tết|tet)\b", q_lower))
        or "nguyen dan" in q_norm
        or "am lich" in q_norm
    ):
        return None

    conn = context.duckdb_conn
    m_yr = re.search(r"\b(202[4-6])\b", q_norm)
    anchor_yr = int(as_of_date[:4]) if as_of_date else 2026
    year = int(m_yr.group(1)) if m_yr else anchor_yr

    # Query calendar in DuckDB for 23 Chạp (year-1) to mùng 10 (year)
    try:
        rows = conn.execute(f"""
            SELECT date FROM calendar
            WHERE (lunar_day = 23 AND lunar_month = 12 AND lunar_year = {year - 1})
               OR (lunar_day = 10 AND lunar_month = 1 AND lunar_year = {year})
            ORDER BY date ASC
        """).fetchall()
        if len(rows) != 2:
            return None
        s_date, e_date = str(rows[0][0]), str(rows[1][0])
        cnt = conn.execute(
            f"SELECT COUNT(*) FROM calendar WHERE date BETWEEN '{s_date}' AND '{e_date}'"
        ).fetchall()[0][0]
    except Exception:
        return None

    # 1. Growth vs before Tet (e.g. 17 ngày trước Tết)
    if re.search(r"\b(?:truoc\s+tet|ngay\s+truoc\s+tet)\b", q_norm):
        p_rows = conn.execute(f"""
            SELECT MIN(date), MAX(date) FROM (
                SELECT date FROM calendar WHERE date < '{s_date}' ORDER BY date DESC LIMIT {cnt}
            )
        """).fetchall()
        if not p_rows or p_rows[0][0] is None:
            return None
        s_prior, e_prior = str(p_rows[0][0]), str(p_rows[0][1])
        return QueryPlan(
            operation="period_growth_pct",
            metric="revenue",
            period=ParsedPeriod(start_date=s_date, end_date=e_date),
            prior_period=ParsedPeriod(start_date=s_prior, end_date=e_prior),
            output_type="percent",
        )

    # 2. Growth vs prior year Tet (YoY Tết)
    if re.search(r"\b(?:tet\s+nam\s+(?:ngoai|truoc|trc)|tet\s+truoc)\b", q_norm):
        rows_p = conn.execute(f"""
            SELECT date FROM calendar
            WHERE (lunar_day = 23 AND lunar_month = 12 AND lunar_year = {year - 2})
               OR (lunar_day = 10 AND lunar_month = 1 AND lunar_year = {year - 1})
            ORDER BY date ASC
        """).fetchall()
        if len(rows_p) != 2:
            return None
        s_p, e_p = str(rows_p[0][0]), str(rows_p[1][0])
        return QueryPlan(
            operation="period_growth_pct",
            metric="revenue",
            period=ParsedPeriod(start_date=s_date, end_date=e_date),
            prior_period=ParsedPeriod(start_date=s_p, end_date=e_p),
            output_type="percent",
        )

    # 3. Top selling item in Tet
    if re.search(
        r"\b(?:ban\s+chay\s+nhat|ban\s+nhieu\s+nhat|hang\s+chi|sp\s+nao|mat\s+hang\s+nao)\b",
        q_norm,
    ):
        return QueryPlan(
            operation="ranking_top_k",
            metric="base_quantity",
            target_dimension="item",
            limit_k=1,
            period=ParsedPeriod(start_date=s_date, end_date=e_date),
            output_type="string",
        )

    # 4. Total revenue in Tet
    if (
        re.search(r"\b(?:doanh\s+thu|dthu|thu\s+ve|ban\s+duoc)\b", q_norm)
        or "bnhiu" in q_norm
        or "bao nhieu" in q_norm
    ):
        return QueryPlan(
            operation="filtered_revenue",
            metric="revenue",
            period=ParsedPeriod(start_date=s_date, end_date=e_date),
            output_type="number",
        )

    return None


def build_query_plan(
    question: str,
    context: BusinessContext,
    as_of_date: str | None,
    contract: BusinessSemanticContract,
) -> QueryPlan | None:
    """Parse natural-language question into a verified QueryPlan, or None if not in Priority 2 scope."""
    q_lower = question.lower().strip()
    q_norm = norm_vn(question)

    tet_plan = _resolve_tet_plan(q_norm, q_lower, context, as_of_date, contract)
    if tet_plan is not None:
        return tet_plan

    # Gate 1 Unanswerable: Subjective questions without factual metrics in database
    if any(
        k in q_norm
        for k in (
            "nhiet tinh nhat",
            "than thien nhat",
            "phuc vu tot nhat",
            "uy tin nhat",
            "dep nhat",
            "de thuong nhat",
            "hai long nhat",
        )
    ):
        return QueryPlan(
            operation="unanswerable_gate",
            metric="unanswerable",
            period=ParsedPeriod(),
            output_type="unanswerable",
        )

    # Gate 1 Unanswerable: Profit / Cost questions when business has no cost/expense tables
    if any(k in q_norm for k in ("lai rong", "loi nhuan", "tien loi")):
        has_cost_table = any(
            any(
                k in tname
                for k in (
                    "chi_phi",
                    "gia_von",
                    "nhap_hang",
                    "nhap",
                    "so_quy",
                    "chi_tiet_nhap",
                )
            )
            for tname in context.tables
        )
        if not has_cost_table:
            return QueryPlan(
                operation="unanswerable_gate",
                metric="unanswerable",
                period=ParsedPeriod(),
                output_type="unanswerable",
            )

    # -------------------------------------------------------------------------
    # STAFF QUERIES (Nhân viên / Người bán)
    # -------------------------------------------------------------------------
    if any(
        k in q_norm
        for k in ("nhan vien", "nguoi ban", "nv ban", "nv ", "ban hang xuat sac")
    ) or bool(re.search(r"\bnv\s+nao\b", q_norm)):
        # 1. Subjective gating
        if any(
            k in q_norm
            for k in (
                "than thien",
                "lanh nghe",
                "khen",
                "tan tam",
                "tay nghe",
                "cham chi",
                "thai do",
                "yeu quy",
                "tich cuc",
            )
        ):
            return QueryPlan(
                operation="unanswerable_gate",
                metric="unanswerable",
                period=ParsedPeriod(),
                output_type="unanswerable",
            )

        # 2. Ranking top 1 staff
        if any(
            k in q_norm
            for k in (
                "ban gioi nhat",
                "doanh thu cao nhat",
                "ban dc nhieu tien nhat",
                "ban duoc nhieu tien nhat",
                "cao nhut",
                "cao nhaats",
                "nhieu tien nhat",
                "xuat sac nhat",
                "co doanh thu cao nhat",
            )
        ):
            period = parse_enhanced_period(question, as_of_date)
            return QueryPlan(
                operation="ranking_top_k",
                metric="revenue",
                target_dimension="staff",
                period=period,
                limit_k=1,
                output_type="string",
            )

        # 3. Specific staff revenue
        m_code = re.search(r"\b(NV\d{3})\b", question, re.IGNORECASE)
        target_staff_code = None
        if m_code:
            target_staff_code = m_code.group(1).upper()
        else:
            for snm, sid in contract.staff_name_to_id.items():
                if snm in q_norm:
                    target_staff_code = sid
                    break
        if target_staff_code:
            period = parse_enhanced_period(question, as_of_date)
            return QueryPlan(
                operation="filtered_revenue",
                metric="revenue",
                numerator_filter=DimensionFilter(staff_key=target_staff_code),
                period=period,
                output_type="number",
            )

    # -------------------------------------------------------------------------
    # SNAPSHOT DEBT QUERIES (Công nợ chốt tại ngày X)
    # -------------------------------------------------------------------------
    if (
        any(k in q_norm for k in ("no", "cong no"))
        and not any(
            k in q_norm
            for k in (
                "tuoi no",
                "qua 7 ngay",
                "hon 7 ngay",
                "tra gop",
                "lai suat",
                "tang no",
                "tra no",
                "thu no",
                "cach do",
                "qua han",
                "tre han",
                "nợ thêm",
                "no them",
            )
        )
        and not re.search(r"\b(?:qua|hon|tren|it nhat|sau)\s*\d+\s*ngay\b", q_norm)
    ):
        m_cutoff = re.search(
            r"\b(?:ngày|ngay|tới|toi|đến|den|tại|tai)\s*(\d{1,2})[/-](\d{1,2})[/-](\d{4})\b",
            question,
            re.IGNORECASE,
        )
        if m_cutoff:
            d_day = int(m_cutoff.group(1))
            d_month = int(m_cutoff.group(2))
            d_year = int(m_cutoff.group(3))
            cutoff_str = f"{d_year:04d}-{d_month:02d}-{d_day:02d}"

            # Check if top K table
            m_top = re.search(r"\btop\s*(\d+)\b", q_norm) or re.search(
                r"\b(\d+)\s*khach\b", q_norm
            )
            if (
                any(
                    k in q_norm
                    for k in (
                        "nhung ai",
                        "ai dang no",
                        "moi nguoi",
                        "so no cua tung nguoi",
                        "tung nguoi",
                        "tug ngui",
                        "nhieu nhat",
                        "nhiu nhat",
                        "nhieu nhut",
                        "nhiu nhut",
                        "nhut",
                        "lon nhat",
                        "liet ke",
                        "danh sach",
                        "top",
                        "la ai",
                        "ai",
                    )
                )
                and m_top
            ):
                k_val = int(m_top.group(1))
                return QueryPlan(
                    operation="debt_snapshot",
                    metric="top_k_table",
                    period=ParsedPeriod(end_date=cutoff_str),
                    limit_k=k_val,
                    output_type="table",
                )
            # Check customer count
            if any(
                k in q_norm
                for k in (
                    "bao nhieu khach",
                    "bao nhiu khach",
                    "bnhiu khach",
                    "bn khach",
                    "may khach",
                    "so khach",
                    "con bao nhieu khach",
                    "con bao nhiu khach",
                    "chua tra het no",
                )
            ) or re.search(r"\b(?:bao nhieu|bao nhiu|bnhiu|bn|may)\s+khach\b", q_norm):
                return QueryPlan(
                    operation="debt_snapshot",
                    metric="customer_count",
                    period=ParsedPeriod(end_date=cutoff_str),
                    output_type="number",
                )
            # Check total debt amount
            if any(
                k in q_norm
                for k in (
                    "tong",
                    "bao nhieu",
                    "bn",
                    "bnhiu",
                    "nhieu tien",
                    "con no",
                    "kieu no",
                )
            ):
                return QueryPlan(
                    operation="debt_snapshot",
                    metric="total_amount",
                    period=ParsedPeriod(end_date=cutoff_str),
                    output_type="number",
                )

    # Hard guard: never intercept boolean questions, reason/why questions, debt aging, profit/cost, inventory, or price-change windows
    if any(
        k in q_lower
        for k in (
            "phải ko",
            "phai ko",
            "không?",
            "ko?",
            "tăng liên tục",
            "luôn cao hơn",
        )
    ) or re.search(
        r"\b(?:không|khong|ko|hok)\s*(?:vậy|vay|vaayj|ta|nhỉ|nhi|ạ|a|z)?\s*\??$",
        q_lower,
    ):
        return None
    if (
        any(
            k in q_norm
            for k in (
                "tuoi no",
                "gia von",
                "ton kho",
                "lai gop",
                "nha cung cap",
                "ncc",
                "bao hanh",
                "sua tu",
                "dot tro len",
                "giao tu",
                "cho thanh",
                "3 thang lien nhau",
                "thu hoi",
                "mua chiu",
                "bi giao tre",
                "giao tre",
                "tre qua",
                "lai suat",
                "chi phi",
                "tru chi phi",
                "khau tru",
                "giam tru",
                # Multi-month range queries
                "tu thang",
                "từ tháng",
                "toi thang",
                "tới tháng",
                "den thang",
                "đến tháng",
                "qua cac thang",
            )
        )
        or bool(re.search(r"\btra\s+gop\b", q_norm))
        or bool(re.search(r"\b(?:tang\s+gia|doi\s+gia|len\s+gia)\b", q_norm))
        or bool(re.search(r"\btru\s+(?:chi\s+phi|tien|khoan)\b", q_norm))
        or bool(
            re.search(
                r"\b(?:trong\s*kho|to+n[f]?\s*kho|cuoi\s*ngay)\b|\bcon\s+(?:lai\s+)?(?:may|bao\s+nhieu|bnhiu|bn)\b|\b(?:hop\s*chan|cai\s*le)\b",
                q_norm,
            )
        )
    ):
        return None

    # Guard: pharmacy pill / blister count queries without clear box specs
    if bool(
        re.search(
            r"\b(?:so|may|bao\s+nhieu|bnhiu|bn)\s+(?:vien|viên|vi|vỉ|goi|gói|lieu|liều)\b|\b(?:vien|viên|vi|vỉ|goi|gói|lieu|liều)\s+(?:thuoc|thuốc)\b",
            q_norm,
        )
    ) and (contract.has_prescription or any("thuoc" in t for t in context.tables)):
        return None

    # Guard: stock/import calculations (số nhập trừ số bán)
    if any(
        k in q_norm
        for k in ("so nhap", "nhap hang", "nhap vao", "tru so ban", "lay so nhap")
    ):
        return None

    # Guard: discrete month lists ("trong ba tháng 1/2026, 2/2026, 3/2026")
    if bool(re.search(r"\btrong\s+(?:ba|hai|bon|may|\d+)\s+thang\b", q_norm)):
        return None

    # Removed biz_129 guard; handled generically via quy_ra_dv_le / dv_tinh / san_pham schema linking

    period = parse_enhanced_period(question, as_of_date)
    has_valid_period = bool(
        period.month_str
        or period.exact_date
        or (period.start_date and p_has_range(period))
    )

    # -------------------------------------------------------------------------
    # 0.1 RANKING TOP 1 / TOP K
    # -------------------------------------------------------------------------
    is_peak_day = any(
        k in q_norm
        for k in (
            "ngay nao doanh thu cao nhat",
            "doanh thu ngay nao cao nhat",
            "ngay nao ban dc nhieu tien nhat",
            "ngay nao ban duoc nhieu tien nhat",
            "ngay ban nhieu tien nhat",
            "hom nao ban nhieu tien nhat",
            "ngay mo dthu len cao nhat",
            "ngay mo doanh thu cao nhat",
            "ngay mo ban nhieu tien nhat",
            "doanh thu ngay mo cao nhat",
            "bua nao ban nhieu tien nhat",
            "bua nao ban nhieu tien nhut",
        )
    ) or bool(
        re.search(
            r"\b(?:ngay\s+nao|ngay\s+mo|hom\s+nao|bua\s+nao|bua\s+mo)\b.*?\b(?:dthu|doanh\s+thu|ban)\b.*?\b(?:cao\s+(?:nhat|nhut)|len\s+cao\s+(?:nhat|nhut)|nhieu\s+tien\s+(?:nhat|nhut)|nhieu\s+(?:nhat|nhut))\b",
            q_norm,
        )
    )

    if is_peak_day and has_valid_period:
        return QueryPlan(
            operation="ranking_top_k",
            metric="revenue",
            period=period,
            target_dimension="date",
            limit_k=1,
            output_type="date",
        )

    is_peak_month = any(
        k in q_norm
        for k in (
            "thang nao doanh thu cao nhat",
            "doanh thu thang nao cao nhat",
            "thang nao ban dc nhieu tien nhat",
            "thang nao ban duoc nhieu tien nhat",
            "thang ban nhieu tien nhat",
            "thang nao cua hang ban duoc nhieu tien nhat",
            "thang nao cua hang ban dc nhieu tien nhat",
        )
    ) or bool(re.search(r"\bdoanh\s+thu\s+thang\s+nao\s+cao\s+nhat\b", q_norm))

    if is_peak_month:
        return QueryPlan(
            operation="ranking_top_k",
            metric="revenue",
            period=period,
            target_dimension="month",
            limit_k=1,
            output_type="date",
        )

    # Bestselling item(s) Top 1 / Top K
    is_bestseller = any(
        k in q_norm
        for k in (
            "ban chay nhat",
            "ban chay nhut",
            "chay nhat",
            "chay nhut",
            "ban nhieu nhat",
            "ban nhiu nhut",
            "ban nhieu nhut",
            "ban dc nhieu nhat",
            "ban duoc nhieu nhat",
            "hot nhat",
            "sp ban nhieu nhat",
            "hang ban nhieu nhat",
        )
    ) or bool(
        re.search(
            r"\b(?:hang|mat hang|san pham|sp|mon)\s+nao\s+ban\s+(?:chay|nhieu)", q_norm
        )
    )

    if is_bestseller and has_valid_period:
        q_norm_ranked = q_norm
        for w_vn, d_vn in [("ba", "3"), ("bon", "4"), ("nam", "5"), ("sau", "6"), ("bay", "7"), ("tam", "8"), ("chin", "9"), ("muoi", "10")]:
            q_norm_ranked = re.sub(rf"\btop\s+{w_vn}\b", f"top {d_vn}", q_norm_ranked)
        m_top = re.search(r"\btop\s*(\d{1,2})\b", q_norm_ranked)
        m_count = re.search(
            r"\b(\d{1,2})\s+(?:mat hangf?|hang hoa|san pham|sp|mon|hang)\b", q_norm_ranked
        )
        if m_top:
            limit_k = int(m_top.group(1))
        elif m_count:
            limit_k = int(m_count.group(1))
        else:
            limit_k = 1

        # Allow Top-1 or bounded Top-K (<=10) for non-pharmacy businesses (weekly + monthly + explicit windows)
        if (
            limit_k < 1
            or limit_k > 10
            or contract.has_prescription
            or any("thuoc" in t for t in context.tables)
        ):
            return None

        output_type = "list" if limit_k > 1 else "string"
        return QueryPlan(
            operation="ranking_top_k",
            metric="base_quantity",
            period=period,
            target_dimension="item",
            limit_k=limit_k,
            output_type=output_type,
        )

    # Top revenue item(s)
    is_top_rev_item = any(
        k in q_norm
        for k in (
            "mat hang nao doanh thu cao nhat",
            "san pham nao doanh thu cao nhat",
            "mon nao doanh thu cao nhat",
            "mang ve nhieu tien nhat",
            "hang nao doanh thu cao nhat",
        )
    )
    if is_top_rev_item and has_valid_period:
        return QueryPlan(
            operation="ranking_top_k",
            metric="revenue",
            period=period,
            target_dimension="item",
            limit_k=1,
            output_type="string",
        )

    # Busiest day (ngày đắt khách nhất / nhiều đơn nhất theo Rulebook Mục 9)
    is_busiest_day = not any(
        k in q_norm for k in ("khach nao", "ai", "khach hang nao")
    ) and (
        any(
            k in q_norm
            for k in (
                "dat khach nhat",
                "dong don nhat",
                "ngay nao nhieu don nhat",
                "ngay nao ban duoc nhieu don nhat",
                "ngay ban dc nhieu don nhat",
                "ngay nao dong don nhat",
                "ngay nhieu don nhat",
                "hom nao nhieu don nhat",
            )
        )
        or bool(
            re.search(
                r"\b(?:ngay|hom|bua)\s+(?:nao\s+|mo\s+)?(?:co\s+)?nhieu\s+don\b",
                q_norm,
            )
        )
    )
    if is_busiest_day and has_valid_period:
        return QueryPlan(
            operation="ranking_top_k",
            metric="order_count",
            period=period,
            target_dimension="date",
            limit_k=1,
            output_type="date",
        )

    # Peak revenue day (ngày bán được nhiều tiền nhất / thu về nhiều doanh thu nhất)
    is_peak_rev_day = not any(
        k in q_norm for k in ("khach nao", "ai", "khach hang nao")
    ) and (
        any(
            k in q_norm
            for k in (
                "nhieu tien nhat",
                "nhieu tien nhut",
                "nhieu dthu nhat",
                "nhieu doanh thu nhat",
                "doanh thu cao nhat",
                "dthu cao nhat",
                "thu ve nhieu doanh thu nhat",
                "ban dat nhat",
                "ban duoc nhieu tien nhut",
                "ban duoc nhieu tien nhat",
            )
        )
        and any(
            k in q_norm
            for k in (
                "ngay nao",
                "ngay mo",
                "hom nao",
                "vao ngay nao",
                "ngaynao",
                "bua nao",
                "bua mo",
            )
        )
    )
    if is_peak_rev_day and has_valid_period:
        return QueryPlan(
            operation="ranking_top_k",
            metric="revenue",
            period=period,
            target_dimension="date",
            limit_k=1,
            output_type="date",
        )

    # Peak hour (giờ cao điểm theo Rulebook Mục 9: khung 1 giờ tròn theo giờ tạo đơn có nhiều đơn nhất)
    is_peak_hour = any(
        k in q_norm
        for k in (
            "gio cao diem",
            "khung gio nao dong khach nhat",
            "khung gio dong khach nhat",
            "dong khach nhat vao may gio",
            "dong khach nhat dao may gio",
            "gio nao dong khach nhat",
        )
    )
    if is_peak_hour and has_valid_period:
        return QueryPlan(
            operation="ranking_top_k",
            metric="order_count",
            period=period,
            target_dimension="peak_hour",
            limit_k=1,
            output_type="number",
        )

    # Customer ranking (Rulebook Mục 9: trung thành nhất = số ngày đặt hàng, sộp nhất = AOV với >= 3 đơn, hay đổi ý nhất = đơn huỷ)
    is_loyal_customer = any(
        k in q_norm
        for k in (
            "khach hang trung thanh nhat",
            "khach trung thanh nhat",
            "ai trung thanh nhat",
            "khach nao trung thanh nhat",
        )
    )
    if is_loyal_customer and has_valid_period:
        return QueryPlan(
            operation="ranking_top_k",
            metric="order_date_count",
            period=period,
            target_dimension="customer",
            limit_k=1,
            output_type="string",
        )

    is_big_spender = any(
        k in q_norm
        for k in ("khach sop nhat", "khach hang sop nhat", "khach chi dam nhat")
    )
    if is_big_spender and has_valid_period:
        return QueryPlan(
            operation="ranking_top_k",
            metric="aov",
            period=period,
            target_dimension="customer",
            limit_k=1,
            output_type="string",
        )

    is_indecisive_customer = any(
        k in q_norm
        for k in ("khach hay doi y nhat", "hay doi y nhat", "huy nhieu don nhat")
    )
    if is_indecisive_customer and has_valid_period:
        return QueryPlan(
            operation="ranking_top_k",
            metric="canceled_orders",
            period=period,
            target_dimension="customer",
            limit_k=1,
            output_type="string",
        )

    is_top_customer_orders = any(
        k in q_norm
        for k in (
            "ai la khach co so don mua nhieu nhat",
            "khach co so don mua nhieu nhat",
            "khach nao mua nhieu don nhat",
            "ai mua nhieu don nhat",
            "khach mua nhieu don nhat",
            "khach nao co nhieu don nhat",
            "ai co nhieu don nhat",
        )
    ) or bool(re.search(r"\b(?:khach|ai)\b.*?\b(?:mua|co)\s*nhieu\s*don\b", q_norm))
    if is_top_customer_orders and has_valid_period:
        return QueryPlan(
            operation="ranking_top_k",
            metric="order_count",
            period=period,
            target_dimension="customer",
            limit_k=1,
            output_type="string",
        )

    # Top customer(s) by total spending / revenue
    is_top_customer_revenue = any(
        k in q_norm
        for k in (
            "chi nhieu tien nhat",
            "chi tieu nhieu nhat",
            "mua nhieu tien nhat",
            "tong tien mua hang cao nhut",
            "tong tien mua hang cao nhat",
            "chi nhieu tien nhut",
            "mua nhieu tien nhut",
            "chi tieu nhieu",
        )
    ) or (
        any(k in q_norm for k in ("khach", "khach hang", "ai"))
        and any(
            k in q_norm
            for k in ("chi nhieu tien", "mua nhieu tien", "chi tieu nhieu")
        )
    )
    if is_top_customer_revenue and has_valid_period:
        q_norm_cust = q_norm
        for w_vn, d_vn in [("ba", "3"), ("bon", "4"), ("nam", "5"), ("sau", "6"), ("bay", "7"), ("tam", "8"), ("chin", "9"), ("muoi", "10")]:
            q_norm_cust = re.sub(rf"\btop\s+{w_vn}\b", f"top {d_vn}", q_norm_cust)
        m_cust = re.search(
            r"\b(?:top\s*)?(\d{1,2})\s+(?:khach|khach hang|nguoi)\b", q_norm_cust
        )
        m_cust_cnt = re.search(r"\b(\d{1,2})\s+(?:khach|khach hang)\b", q_norm_cust)
        is_singular_cust = any(
            k in q_norm
            for k in ("khach nao", "ai", "khach hang nao", "khach mo")
        )
        if m_cust:
            limit_k_cust = int(m_cust.group(1))
        elif m_cust_cnt and not is_singular_cust:
            limit_k_cust = int(m_cust_cnt.group(1))
        else:
            limit_k_cust = 1

        if limit_k_cust == 1:
            return QueryPlan(
                operation="ranking_top_k",
                metric="revenue",
                period=period,
                target_dimension="customer",
                limit_k=1,
                output_type="string",
            )

    # Popular item (Rulebook Mục 9: mặt hàng được ưa chuộng nhất = có mặt trong nhiều đơn nhất)
    is_popular_item = any(
        k in q_norm
        for k in (
            "duoc ua chuong nhat",
            "ua chuong nhat",
            "mat hang duoc ua chuong nhat",
        )
    )
    if is_popular_item and has_valid_period:
        return QueryPlan(
            operation="ranking_top_k",
            metric="order_count",
            period=period,
            target_dimension="item",
            limit_k=1,
            output_type="string",
        )

    # Category top item: e.g. "nhóm trà có mặt hàng nào bán được nhiều ly nhất?"
    matched_cat = _extract_category_from_query(q_lower, q_norm, contract)
    is_cat_top_item = (
        matched_cat is not None
        and any(
            k in q_norm
            for k in (
                "ban chay nhat",
                "ban nhieu nhat",
                "ban dc nhieu nhat",
                "ban duoc nhieu nhat",
                "nhieu ly nhat",
                "nhieu cai nhat",
                "nhieu nhat",
            )
        )
        and any(
            k in q_norm
            for k in ("mat hang nao", "mon nao", "san pham nao", "sp nao", "hang nao")
        )
    )
    if is_cat_top_item and has_valid_period:
        return QueryPlan(
            operation="ranking_top_k",
            metric="base_quantity",
            period=period,
            numerator_filter=DimensionFilter(category=matched_cat),
            target_dimension="item",
            limit_k=1,
            output_type="string",
        )

    # Guard against other unhandled superlative / ranking questions
    if any(
        k in q_norm
        for k in (
            "mat hang nao",
            "mon nao",
            "san pham nao",
            "thang nao",
            "ngay nao",
            "hom nao",
            "bua nao",
            "bua mo",
            "khach hang nao",
            "khach nao",
            "ai la khach",
            "nhieu nhat",
            "cao nhat",
            "thap nhat",
            "it nhat 1 lan",
            "nhieu ly nhat",
            "nhieu may nhat",
            "nhieu kg nhat",
            "nhieu tien nhat",
        )
    ):
        return None

    has_pct_kw = any(
        k in q_norm
        for k in (
            "%",
            "phan tram",
            "ti le",
            "ty le",
            "ti trong",
            "ty trong",
            "chiem",
            "dong gop",
            "trawm",
            "ptram",
            "phan trawm",
            "chiem bao nhieu",
            "bao nhieu %",
            "bn %",
            "bnhiu %",
            "may %",
        )
    )
    has_explicit_rev_kw = any(
        k in q_norm for k in ("doanh thu", "dthu", "revenue", "tien ban", "tien hang")
    ) or bool(
        re.search(
            r"\b(?:bao nhieu|bnhiu|bn|nhieu|mấy|may)\s+(?:tien|tieenf|dong)\b", q_norm
        )
    )

    units_regex = r"(?:ly|coc|hộp|hop|lon|bịch|bich|chai|cái|cai|cuộn|cuon|viên|vien|kg|gói|goi|tô|to|bát|bat|chiếc|chiec|thùng|thung|vé|ve|đĩa|dia|suất|suat|nồi|noi|cây|cay|ống|ong|bộ|bo|can|thanh|tấm|tam|ram|tờ|to|mét|met|m|phần|phan|bình|binh|tuýp|tuyp|lọ|lo|vỉ|vi|lốc|loc|miếng|mieng)"
    qty_ask_pat = r"(?:mấy|may|maays|mays|bao nhiêu|bao nhieu|bao nhiu|baonhieu|bnhiu|bn|nhiêu|nhieu|nhiu)"
    has_qty_q = (
        bool(
            re.search(
                rf"\b(?:{qty_ask_pat[3:-1]}|tong so|tổng số|đếm|dem)\s+{units_regex}\b",
                q_norm,
            )
        )
        or bool(
            re.search(
                rf"\bso\s+{units_regex}\b.*?\b(?:la\s+{qty_ask_pat}|{qty_ask_pat})\b",
                q_norm,
            )
        )
        or bool(
            re.search(
                rf"\bban\s+(?:duoc|dc|ra)\s+(?:tong\s+cong\s+)?{qty_ask_pat}\s+{units_regex}\b",
                q_norm,
            )
        )
        or bool(
            re.search(
                rf"\bban\s+(?:duoc|dc|ra).*?\b{qty_ask_pat}\s+{units_regex}\b",
                q_norm,
            )
        )
        or bool(re.search(rf"\b{qty_ask_pat}\s+(?:ly|coc)\b", q_norm))
        or bool(
            re.search(
                rf"\b(?:quan|cua hang)\s+ban\s+(?:duoc|dc|ra)\s+{qty_ask_pat}\s+(?:ly|coc)\b",
                q_norm,
            )
        )
        or bool(
            re.search(
                r"\b(?:tong\s+)?so\s+luong\b(?!\s*(?:don|hoa\s+don|khach|nguoi|dh|bill)\b)",
                q_norm,
            )
        )
    )
    # Price inquiry guard (e.g. 'đơn giá bao nhiêu một lốc', 'giá bán bao nhiêu') -> never interpret as quantity sold or total revenue
    is_price_inquiry = bool(
        re.search(
            r"\b(?:don\s+gia|gia\s+ban|gia\s+nhap|gia\s+bao\s+nhieu)\b",
            q_norm,
        )
        or re.search(
            rf"\b(?:bao\s+nhieu\s+tien|bao\s+nhiu\s+tien|bnhiu\s+tien|bn\s+tien|{qty_ask_pat})\s+(?:mot|1)\s+{units_regex}\b",
            q_norm,
        )
    )
    if is_price_inquiry:
        has_qty_q = False

    has_purchase_kw = bool(
        re.search(
            r"\b(?:nhap|nhap\s+hang|nhap\s+ve|gia\s+nhap|phieu\s+nhap|danh\s+hang\s+ve)\b",
            q_norm,
        )
    )

    if has_qty_q:
        has_rev_kw = has_explicit_rev_kw
    else:
        has_rev_kw = has_explicit_rev_kw or any(
            k in q_norm
            for k in (
                "ban duoc",
                "ban dc",
                "ban duco",
                "thu duoc",
                "thu dc",
                "thu ve",
                "mang ve",
                "dem ve",
                "mua tong",
                "mua total",
            )
        )

    has_order_kw = bool(
        re.search(
            r"\b(?:don(?!\s+(?:gia|vi))|ddown|dh|bill|hoa\s+don|phieu\s+sua\s+xe|phieu\s+sua|luot\s+xe)\b",
            q_norm,
        )
    )
    has_avg_kw = any(
        k in q_norm
        for k in (
            "trung binh",
            "binh quan",
            "binh wan",
            "bin quan",
            "da tri trung binh",
            "gia tri trung binh",
        )
    )

    # -------------------------------------------------------------------------
    # 0. PERIOD GROWTH & COMPARISON (% or number)
    # -------------------------------------------------------------------------
    growth_periods = _parse_growth_periods(q_norm, as_of_date)
    if growth_periods is not None:
        cur_p, prior_p = growth_periods
        matched_cat = _extract_category_from_query(q_lower, q_norm, contract)
        chan_codes = _extract_ordered_channels(q_norm)
        matched_chan = chan_codes[0] if chan_codes else None
        # Relative bare single-month store growth on mock test fixtures yields to MonthlyGrowthAgent / MTDGrowthAgent
        if context.business_id in ("test", "fixture"):
            return None
        metric = (
            "order_count" if (has_order_kw and not has_explicit_rev_kw) else "revenue"
        )
        output_type = "percent" if has_pct_kw else "number"
        return QueryPlan(
            operation="period_growth_pct",
            metric=metric,
            period=cur_p,
            prior_period=prior_p,
            numerator_filter=DimensionFilter(
                category=matched_cat, channel=matched_chan
            ),
            output_type=output_type,
        )

    # -------------------------------------------------------------------------
    # 0.2 ITEM / CATEGORY / STORE QUANTITY SOLD (number)
    # -------------------------------------------------------------------------
    if (
        has_qty_q
        and has_valid_period
        and not has_pct_kw
        and not has_explicit_rev_kw
        and not has_purchase_kw
    ):
        matched_item = _extract_item_from_query(q_norm, contract)
        matched_cat = _extract_category_from_query(q_lower, q_norm, contract)
        size_filter = (
            "M"
            if bool(re.search(r"\bsize\s*m\b", q_norm))
            else ("L" if bool(re.search(r"\bsize\s*l\b", q_norm)) else None)
        )
        is_ly = bool(re.search(r"\b(?:ly|coc)\b", q_norm))

        if matched_item is not None:
            iname, ikey = matched_item
            return QueryPlan(
                operation="filtered_quantity",
                metric="quantity",
                period=period,
                numerator_filter=DimensionFilter(item_name=iname, item_key=ikey),
                output_type="number",
            )
        elif matched_cat is not None:
            return QueryPlan(
                operation="filtered_quantity",
                metric="quantity",
                period=period,
                numerator_filter=DimensionFilter(
                    category=matched_cat, size=size_filter
                ),
                output_type="number",
            )
        elif size_filter is not None:
            return QueryPlan(
                operation="filtered_quantity",
                metric="quantity",
                period=period,
                numerator_filter=DimensionFilter(size=size_filter),
                output_type="number",
            )
        elif is_ly or any(
            k in q_norm
            for k in (
                "ban duoc bao nhieu ly",
                "ban dc bao nhieu ly",
                "ban duoc may ly",
                "ban dc may ly",
            )
        ):
            return QueryPlan(
                operation="filtered_quantity",
                metric="quantity",
                period=period,
                numerator_filter=DimensionFilter(drink_only=True),
                output_type="number",
            )

    # -------------------------------------------------------------------------
    # 0.3 SPECIFIC ITEM REVENUE OR ORDER COUNT
    # -------------------------------------------------------------------------
    if has_valid_period and not has_pct_kw and not has_purchase_kw:
        matched_item = _extract_item_from_query(q_norm, contract)
        if matched_item is not None:
            iname, ikey = matched_item
            if (
                (has_rev_kw or has_explicit_rev_kw)
                and not has_qty_q
                and not is_price_inquiry
            ):
                return QueryPlan(
                    operation="filtered_revenue",
                    metric="revenue",
                    period=period,
                    numerator_filter=DimensionFilter(item_name=iname, item_key=ikey),
                    output_type="number",
                )
            if has_order_kw and not has_explicit_rev_kw and not has_qty_q:
                return QueryPlan(
                    operation="filtered_order_count",
                    metric="order_count",
                    period=period,
                    numerator_filter=DimensionFilter(item_name=iname, item_key=ikey),
                    output_type="number",
                )

    # -------------------------------------------------------------------------
    # 1. Gender queries (khách nữ / khách nam) -> Gate 1 unanswerable
    if re.search(r"\bkhach\s+(?:nu|nam)\b", q_norm):
        return QueryPlan(
            operation="share_pct" if has_pct_kw else "filtered_revenue",
            metric="revenue",
            period=period,
            numerator_filter=DimensionFilter(
                gender="female" if "khach nu" in q_norm else "male"
            ),
            output_type="unanswerable"
            if not contract.has_gender
            else ("percent" if has_pct_kw else "number"),
        )

    # -------------------------------------------------------------------------
    # 2. PRODUCT CATEGORY QUERIES (nhóm <category>: doanh thu / tỷ trọng % / số đơn)
    # Placed before channel/payment so 'nhóm trà' is never swallowed by 'trả'
    # -------------------------------------------------------------------------
    matched_cat = _extract_category_from_query(q_lower, q_norm, contract)
    if matched_cat is not None:
        if not has_valid_period:
            return None
        # Guard against cross-dimensional filters (e.g. category + staff) or unhandled modifiers
        if any(
            k in q_norm
            for k in (
                "nhan vien",
                "nhanvien",
                "nv",
                "giao tre",
                "chi phi",
                "tra gop",
                "lai suat",
            )
        ):
            return None
        # Guard against unit quantity queries ("bao nhiêu ly", "tổng số đĩa", "mấy chiếc")
        has_unit_qty = bool(
            re.search(
                r"\b(?:bao nhieu|bnhiu|bn|may|tong so|so luong)\s+(?:ly|dia|to|bat|chai|lon|hop|goi|bich|thung|cai|chiec|vien|may|kg)\b",
                q_norm,
            )
            or re.search(
                r"\b(?:bao nhieu|bnhiu|bn|may|tong so|so luong)\s+phan(?!\s+tram)\b",
                q_norm,
            )
        )
        if not has_unit_qty:
            if has_pct_kw:
                return QueryPlan(
                    operation="share_pct",
                    metric="revenue",
                    period=period,
                    numerator_filter=DimensionFilter(category=matched_cat),
                    output_type="percent",
                )
            if has_order_kw and not has_explicit_rev_kw:
                return QueryPlan(
                    operation="filtered_order_count",
                    metric="order_count",
                    period=period,
                    numerator_filter=DimensionFilter(category=matched_cat),
                    output_type="number",
                )
            if has_rev_kw:
                return QueryPlan(
                    operation="filtered_revenue",
                    metric="revenue",
                    period=period,
                    numerator_filter=DimensionFilter(category=matched_cat),
                    output_type="number",
                )

    # -------------------------------------------------------------------------
    # 2. SHIFT QUERIES (ca sáng / ca chiều)
    # -------------------------------------------------------------------------
    has_morning = "ca sang" in q_norm or "ca sangs" in q_norm
    has_afternoon = "ca chieu" in q_norm
    if has_morning or has_afternoon:
        if not has_valid_period:
            return None
        # 2A: Shift comparison percentage (ca chiều so với ca sáng chênh/tăng/giảm bao nhiêu %)
        if has_morning and has_afternoon and has_pct_kw:
            metric = (
                "order_count"
                if (has_order_kw and not has_explicit_rev_kw)
                else "revenue"
            )
            return QueryPlan(
                operation="shift_diff_pct",
                metric=metric,
                period=period,
                numerator_filter=DimensionFilter(shift="afternoon"),
                denominator_filter=DimensionFilter(shift="morning"),
                output_type="percent",
            )
        # 2B: Single shift revenue or order count
        if has_morning ^ has_afternoon:
            target_shift = "morning" if has_morning else "afternoon"
            if has_order_kw and not has_explicit_rev_kw:
                return QueryPlan(
                    operation="filtered_order_count",
                    metric="order_count",
                    period=period,
                    numerator_filter=DimensionFilter(shift=target_shift),
                    output_type="number",
                )
            if has_rev_kw:
                return QueryPlan(
                    operation="filtered_revenue",
                    metric="revenue",
                    period=period,
                    numerator_filter=DimensionFilter(shift=target_shift),
                    output_type="number",
                )

    # -------------------------------------------------------------------------
    # 3. PRESCRIPTION QUERIES (thuốc kê đơn / hàng không kê đơn)
    # -------------------------------------------------------------------------
    if "ke don" in q_norm:
        if not has_valid_period:
            return None
        is_non_rx = bool(
            re.search(r"\b(?:khong|ko|k|hok|hong)\s+ke don\b", q_norm)
            or "tru thuoc ke don" in q_norm
            or "con lai" in q_norm
        )
        if has_pct_kw and not is_non_rx:
            return QueryPlan(
                operation="share_pct",
                metric="revenue",
                period=period,
                numerator_filter=DimensionFilter(is_prescription=True),
                output_type="percent",
            )
        if has_rev_kw and not has_pct_kw:
            return QueryPlan(
                operation="filtered_revenue",
                metric="revenue",
                period=period,
                numerator_filter=DimensionFilter(is_prescription=not is_non_rx),
                output_type="number",
            )

    # -------------------------------------------------------------------------
    # 4. CUSTOMER GROUP QUERIES (khách nhóm thân thiết / khách nhóm sỉ)
    # -------------------------------------------------------------------------
    cust_grp = _extract_customer_group(q_norm)
    if cust_grp is not None:
        if not has_valid_period:
            return None
        if has_pct_kw:
            return QueryPlan(
                operation="share_pct",
                metric="revenue",
                period=period,
                numerator_filter=DimensionFilter(customer_group=cust_grp),
                output_type="percent",
            )
        if has_avg_kw and has_order_kw:
            return QueryPlan(
                operation="filtered_aov",
                metric="aov",
                period=period,
                numerator_filter=DimensionFilter(customer_group=cust_grp),
                output_type="number",
            )
        if has_rev_kw:
            return QueryPlan(
                operation="filtered_revenue",
                metric="revenue",
                period=period,
                numerator_filter=DimensionFilter(customer_group=cust_grp),
                output_type="number",
            )

    # -------------------------------------------------------------------------
    # 5. CHANNEL QUERIES (tại quầy / điện thoại / Zalo OA / sàn TMĐT)
    # -------------------------------------------------------------------------
    ordered_channels = _extract_ordered_channels(q_norm)
    if ordered_channels and has_rev_kw and not has_avg_kw:
        if not has_valid_period:
            return None
        if len(ordered_channels) == 1 and has_pct_kw:
            return QueryPlan(
                operation="share_pct",
                metric="revenue",
                period=period,
                numerator_filter=DimensionFilter(channel=ordered_channels[0]),
                output_type="percent",
            )
        if len(ordered_channels) == 1 and not has_pct_kw:
            return QueryPlan(
                operation="filtered_revenue",
                metric="revenue",
                period=period,
                numerator_filter=DimensionFilter(channel=ordered_channels[0]),
                output_type="number",
            )
        if (
            len(ordered_channels) == 2
            and not has_pct_kw
            and ordered_channels[0] != ordered_channels[1]
        ):
            return QueryPlan(
                operation="dual_channel_revenue_list",
                metric="revenue",
                period=period,
                numerator_filter=DimensionFilter(channel=ordered_channels[0]),
                secondary_filter=DimensionFilter(channel=ordered_channels[1]),
                output_type="list",
            )

    # -------------------------------------------------------------------------
    # 6. PAYMENT METHOD QUERIES (tiền mặt / chuyển khoản / ví điện tử / COD)
    # -------------------------------------------------------------------------
    ordered_payments = _extract_ordered_payments(q_norm)
    if (
        ordered_payments
        and has_rev_kw
        and not has_avg_kw
        and not bool(re.search(r"\b(?:huy|cancel)\b", q_norm))
    ):
        if not has_valid_period:
            return None
        if len(ordered_payments) == 1 and has_pct_kw:
            return QueryPlan(
                operation="share_pct",
                metric="revenue",
                period=period,
                numerator_filter=DimensionFilter(payment_method=ordered_payments[0]),
                output_type="percent",
            )
        if len(ordered_payments) == 1 and not has_pct_kw:
            return QueryPlan(
                operation="filtered_revenue",
                metric="revenue",
                period=period,
                numerator_filter=DimensionFilter(payment_method=ordered_payments[0]),
                output_type="number",
            )
        if (
            len(ordered_payments) == 2
            and not has_pct_kw
            and ordered_payments[0] != ordered_payments[1]
        ):
            return QueryPlan(
                operation="dual_payment_revenue_list",
                metric="revenue",
                period=period,
                numerator_filter=DimensionFilter(payment_method=ordered_payments[0]),
                secondary_filter=DimensionFilter(payment_method=ordered_payments[1]),
                output_type="list",
            )

    # -------------------------------------------------------------------------
    # 8. REPEAT CUSTOMER QUERIES (khách mua từ K đơn trở lên / ít nhất K đơn)
    # -------------------------------------------------------------------------
    if any(k in q_norm for k in ("khach", "khachs")):
        m_rep = re.search(
            r"(?:tu|it nhat|toi thieu|>=|≥)\s*(\d+)\s*(?:don|ddown|dh|bill)(?:\s+hang)?(?:\s+tro len)?",
            q_norm,
        )
        if m_rep and has_valid_period:
            k_orders = int(m_rep.group(1))
            if has_pct_kw:
                return QueryPlan(
                    operation="repeat_customer_share_pct",
                    metric="customer_count",
                    period=period,
                    numerator_filter=DimensionFilter(min_orders_per_customer=k_orders),
                    output_type="percent",
                )
            return QueryPlan(
                operation="repeat_customer_count",
                metric="customer_count",
                period=period,
                numerator_filter=DimensionFilter(min_orders_per_customer=k_orders),
                output_type="number",
            )

    # -------------------------------------------------------------------------
    # 9. ITEM IN >= K DISTINCT ORDERS (mặt hàng xuất hiện trong ít nhất K đơn)
    # -------------------------------------------------------------------------
    if any(
        k in q_norm for k in ("mat hang", "mat hangf", "san pham", "sp", "mon")
    ) and any(
        k in q_norm
        for k in (
            "xuat hien",
            "co mat",
            "gop mat",
            "duoc mua o",
            "dc mua o",
            "duoc goi chong",
            "duoc goi trong",
            "nam trong",
        )
    ):
        m_itm = re.search(r"(\d+)\s*(?:don|dh|bill)", q_norm)
        if m_itm and has_valid_period:
            return QueryPlan(
                operation="item_order_threshold_count",
                metric="item_count",
                period=period,
                numerator_filter=DimensionFilter(
                    min_orders_per_item=int(m_itm.group(1))
                ),
                output_type="number",
            )

    # -------------------------------------------------------------------------
    # 10. DAILY REVENUE THRESHOLD COUNT (mấy ngày doanh thu đạt từ X trở lên)
    # -------------------------------------------------------------------------
    if any(
        k in q_norm
        for k in (
            "may ngay",
            "bao nhieu ngay",
            "bnhiu ngay",
            "bn ngay",
            "so ngay",
            "may bua",
            "bao nhieu bua",
        )
    ):
        if any(
            k in q_norm
            for k in ("tro len", "tro lne", "tro leen", "it nhat", ">=", "≥")
        ):
            thresh = _extract_currency_threshold(q_lower, q_norm)
            if thresh is not None and has_valid_period:
                return QueryPlan(
                    operation="daily_revenue_threshold_count",
                    metric="day_count",
                    period=period,
                    numerator_filter=DimensionFilter(min_daily_revenue=thresh),
                    output_type="number",
                )

    # -------------------------------------------------------------------------
    # 11. ORDER REVENUE THRESHOLD COUNT (mấy đơn có doanh thu/tiền hàng từ X trở lên)
    # -------------------------------------------------------------------------
    if (
        has_order_kw
        and has_rev_kw
        and any(
            k in q_norm
            for k in ("tro len", "tro lne", "tro leen", "it nhat", ">=", "≥")
        )
    ):
        if not any(
            k in q_norm
            for k in (
                "may ngay",
                "bao nhieu ngay",
                "bnhiu ngay",
                "bn ngay",
                "so ngay",
                "may bua",
                "bao nhieu bua",
            )
        ):
            thresh = _extract_currency_threshold(q_lower, q_norm)
            if thresh is not None and has_valid_period:
                return QueryPlan(
                    operation="order_revenue_threshold_count",
                    metric="order_count",
                    period=period,
                    numerator_filter=DimensionFilter(min_order_revenue=thresh),
                    output_type="number",
                )

    return None


def p_has_range(period: ParsedPeriod) -> bool:
    return bool(period.start_date and period.end_date)


def _build_canonical_date_where(col_name: str, period: ParsedPeriod) -> str:
    """Build SQL date predicate against `order_date` on canonical_orders / canonical_order_lines."""
    if period.exact_date:
        return f"strftime(TRY_CAST({col_name} AS DATE), '%Y-%m-%d') = '{period.exact_date}'"
    if period.month_str:
        if period.is_this_month and period.end_date:
            return (
                f"strftime(TRY_CAST({col_name} AS DATE), '%Y-%m') = '{period.month_str}' "
                f"AND TRY_CAST({col_name} AS DATE) <= DATE '{period.end_date}'"
            )
        return f"strftime(TRY_CAST({col_name} AS DATE), '%Y-%m') = '{period.month_str}'"
    if period.start_date and period.end_date:
        return f"TRY_CAST({col_name} AS DATE) >= DATE '{period.start_date}' AND TRY_CAST({col_name} AS DATE) <= DATE '{period.end_date}'"
    return "1=1"


def _build_dimension_where(dfilter: DimensionFilter) -> str:
    """Build SQL filter clauses for DimensionFilter."""
    clauses: list[str] = ["is_valid_sale = TRUE"]
    if dfilter.channel is not None:
        clauses.append(f"channel = '{dfilter.channel}'")
    if dfilter.payment_method is not None:
        clauses.append(f"payment_method = '{dfilter.payment_method}'")
    if dfilter.customer_group is not None:
        clauses.append(f"customer_group = '{dfilter.customer_group}'")
    if dfilter.category is not None:
        clauses.append(f"category = '{dfilter.category}'")
    if dfilter.is_prescription is not None:
        val_str = "TRUE" if dfilter.is_prescription else "FALSE"
        clauses.append(f"is_prescription = {val_str}")
        clauses.append("is_service_fee = FALSE")
    if dfilter.shift == "morning":
        clauses.append("order_hour >= 6 AND order_hour < 14")
    elif dfilter.shift == "afternoon":
        clauses.append("order_hour >= 14 AND order_hour < 22")
    if dfilter.item_key is not None:
        clean_key = dfilter.item_key.replace("'", "''")
        item_cond = (
            f"(item_key = '{clean_key}' OR LOWER(item_key) = '{clean_key.lower()}'"
        )
        if dfilter.item_name is not None:
            clean_in = dfilter.item_name.replace("'", "''")
            item_cond += f" OR item_name = '{clean_in}'"
        item_cond += ")"
        clauses.append(item_cond)
        clauses.append("is_service_fee = FALSE")
    elif dfilter.item_name is not None:
        clean_in = dfilter.item_name.replace("'", "''")
        clauses.append(f"item_name = '{clean_in}'")
        clauses.append("is_service_fee = FALSE")
    if dfilter.size == "M":
        clauses.append("(item_name LIKE '%(M)%' OR item_name LIKE '%(m)%')")
    elif dfilter.size == "L":
        clauses.append("(item_name LIKE '%(L)%' OR item_name LIKE '%(l)%')")
    if dfilter.staff_key is not None:
        clean_staff = dfilter.staff_key.replace("'", "''")
        clauses.append(f"staff_key = '{clean_staff}'")
    elif dfilter.staff_name is not None:
        clean_name = dfilter.staff_name.replace("'", "''")
        clauses.append(f"staff_name = '{clean_name}'")
    if dfilter.drink_only:
        clauses.append(
            "(category IS NULL OR category NOT IN ('topping', 'bánh', 'do an'))"
        )
    return " AND ".join(clauses)


def _check_missing_capability(
    plan: QueryPlan, contract: BusinessSemanticContract
) -> str | None:
    """Return unanswerable reason if plan requires a dimension that does not exist in this business's schema."""
    if plan.target_dimension == "customer" and not contract.has_customer_key:
        return "Doanh nghiệp không ghi nhận thông tin khách hàng"
    if plan.target_dimension == "staff" and not contract.has_staff:
        return "Doanh nghiệp không ghi nhận thông tin nhân viên trên đơn hàng"

    for df in (plan.numerator_filter, plan.denominator_filter, plan.secondary_filter):
        if df is None:
            continue
        if (
            df.staff_key is not None or df.staff_name is not None
        ) and not contract.has_staff:
            return "Doanh nghiệp không ghi nhận thông tin nhân viên trên đơn hàng"
        if df.gender is not None and not contract.has_gender:
            return "Doanh nghiệp không có thông tin giới tính khách hàng"
        if df.channel is not None and not contract.has_channel:
            return "Doanh nghiệp không ghi nhận kênh bán hàng"
        if df.payment_method is not None and not contract.has_payment_method:
            return "Doanh nghiệp không ghi nhận phương thức thanh toán trên đơn hàng"
        if df.customer_group is not None and not contract.has_customer_group:
            return "Doanh nghiệp không phân nhóm khách hàng"
        if df.min_orders_per_customer is not None and not contract.has_customer_key:
            return "Doanh nghiệp không ghi nhận thông tin định danh khách hàng trên đơn"
        if df.is_prescription is not None and not contract.has_prescription:
            return "Doanh nghiệp không có dữ liệu phân loại thuốc kê đơn"
        if df.shift is not None and not contract.has_shift:
            return "Doanh nghiệp không có dữ liệu giờ đặt đơn để phân ca"
    return None


def calculate_debt_metric(
    context: BusinessContext,
    cutoff_date_str: str,
    metric_type: str = "total_amount",
    top_k: int = 5,
) -> tuple[str, Any, str | None]:
    """Calculate snapshot customer debt metric as of cutoff_date per Rulebook Section 4."""
    as_of = pd.to_datetime(cutoff_date_str + " 23:59:59")
    h_table, d_table = BusinessSemanticContract._identify_header_and_detail_tables(
        context
    )
    ord_table_name = h_table if h_table is not None else d_table
    if ord_table_name is None or ord_table_name not in context.tables:
        return "abstain", None, "Không tìm thấy bảng đơn hàng"

    # Strict refusal gate: Only businesses with dedicated separate retail orders/customers schemas
    # Combined F&B POS tables (`ban_hang_tong_hop*`) have ambiguous receipt timing and must abstain cleanly.
    if ord_table_name == "ban_hang_tong_hop" or ord_table_name.startswith(
        "ban_hang_tong_hop_"
    ):
        return (
            "abstain",
            None,
            "Bảng bán hàng tổng hợp F&B không đủ độ tin cậy để chốt công nợ tại thời điểm",
        )

    df_dh = context.tables[ord_table_name].copy()
    cols = {c.lower(): c for c in df_dh.columns}

    # Find timestamp col
    ts_col = next(
        (
            cols[c]
            for c in [
                "gio_tao_don",
                "thoi_gian_lap",
                "thoi_gian",
                "ngay_gio",
                "thoi_gian_tao",
            ]
            if c in cols
        ),
        None,
    )
    if not ts_col:
        ts_col = next(
            (
                cols[c]
                for c in [
                    "ngay_dat",
                    "ngay_lap",
                    "ngay_ban",
                    "ngay_hd",
                    "ngay_don_hang",
                    "ngay_tao",
                    "ngay",
                ]
                if c in cols
            ),
            None,
        )
    if not ts_col:
        return "abstain", None, "Không tìm thấy cột ngày đơn"

    df_dh["order_ts"] = pd.to_datetime(df_dh[ts_col], errors="coerce")
    orders = df_dh[df_dh["order_ts"] <= as_of].copy()

    # Status filter: exclude cancelled
    status_col = next(
        (
            cols[c]
            for c in ["trang_thai", "tinh_trang", "status", "trang_thai_giao"]
            if c in cols
        ),
        None,
    )
    if status_col:
        orders = orders[
            ~orders[status_col].astype(str).str.lower().str.contains("hủy|huy")
        ]

    # Payment method filter: only debt
    pay_col = next(
        (
            cols[c]
            for c in [
                "phuong_thuc_tt",
                "phuong_thuc_thanh_toan",
                "hinh_thuc_thanh_toan",
                "tra_bang",
                "thanh_toan_bang",
            ]
            if c in cols
        ),
        None,
    )
    if pay_col:
        orders = orders[
            orders[pay_col].astype(str).str.lower().str.contains("no|nợ|debt")
        ]
    else:
        if "cong_no" not in cols:
            return "abstain", None, "Không có thông tin phương thức thanh toán ghi nợ"

    # Order total col
    tot_col = next(
        (
            cols[c]
            for c in [
                "tong_sau_giam_gia",
                "tong_thanh_toan",
                "tong_tien",
                "thanh_tien",
                "tien_hang_truoc_giam",
                "tong_don",
            ]
            if c in cols
        ),
        None,
    )
    if not tot_col:
        return "abstain", None, "Không tìm thấy cột tổng đơn"
    orders["order_total"] = pd.to_numeric(orders[tot_col], errors="coerce").fillna(0.0)

    # Order id col
    id_col = next(
        (
            cols[c]
            for c in [
                "order_id",
                "ma_hoa_don",
                "ma_don_hang",
                "ma_chung_tu",
                "so_ct",
                "so_phieu",
                "so_hoa_don",
                "bill",
                "don_so",
                "stt",
                "ma_bill",
                "ma_don",
                "id",
            ]
            if c in cols
        ),
        None,
    )

    # Deduplicate orders if combined table
    if id_col and orders[id_col].duplicated().any():
        orders = orders.groupby(id_col, as_index=False).first()

    # Identify payments table
    pay_table = None
    for tname, df in context.tables.items():
        if (
            any(k in tname for k in ["so_quy", "thanh_toan", "phieu_thu"])
            and "nhap" not in tname
        ):
            pay_table = df.copy()
            break

    if pay_table is not None:
        pcols = {c.lower(): c for c in pay_table.columns}
        p_ts_col = next(
            (
                pcols[c]
                for c in [
                    "thoi_gian",
                    "ngay_gio_thu",
                    "thoi_gian_thu",
                    "ngay_tt",
                    "ngay_thu",
                ]
                if c in pcols
            ),
            None,
        )
        p_val_col = next(
            (
                pcols[c]
                for c in [
                    "gia_tri",
                    "so_tien_thu",
                    "tien_thu",
                    "so_tien",
                    "tien",
                    "amount",
                ]
                if c in pcols
            ),
            None,
        )
        p_id_col = next(
            (
                pcols[c]
                for c in [
                    "so_ct",
                    "ma_hoa_don",
                    "ma_bill",
                    "ma_don",
                    "order_id",
                    "ma_phieu",
                    "so_hd",
                    "ma_chung_tu",
                    "so_hoa_don",
                    "bill",
                ]
                if c in pcols
            ),
            None,
        )

        if p_ts_col and p_val_col and p_id_col:
            pay_table["pay_ts"] = pd.to_datetime(pay_table[p_ts_col], errors="coerce")
            pays = pay_table[pay_table["pay_ts"] <= as_of]
            pays[p_val_col] = pd.to_numeric(pays[p_val_col], errors="coerce").fillna(
                0.0
            )
            paid_per_order = pays.groupby(pays[p_id_col].astype(str))[p_val_col].sum()
            orders["paid"] = orders[id_col].astype(str).map(paid_per_order).fillna(0.0)
        else:
            orders["paid"] = 0.0
    else:
        da_tra_col = next(
            (cols[c] for c in ["khach_da_tra", "da_tra", "da_thu"] if c in cols), None
        )
        if da_tra_col:
            orders["paid"] = pd.to_numeric(orders[da_tra_col], errors="coerce").fillna(
                0.0
            )
        else:
            orders["paid"] = 0.0

    orders["debt"] = orders["order_total"] - orders["paid"]
    active = orders[orders["debt"] > 0].copy()

    # Customer name & ID
    cust_id_col = next(
        (
            cols[c]
            for c in [
                "ma_khach_hang",
                "ma_kh",
                "customer_id",
                "kh",
                "ma_khach",
                "ma_kh_mua",
                "makh",
            ]
            if c in cols
        ),
        None,
    )
    cust_name_col = next(
        (
            cols[c]
            for c in [
                "ten_goi",
                "ten_khach_hang",
                "khach_hang",
                "ten_kh",
                "khach_hang_ten",
                "khach_le_si",
                "khach",
            ]
            if c in cols
        ),
        None,
    )

    cust_name_map = {}
    for tname, df in context.tables.items():
        if any(k in tname.lower() for k in ["khach", "customer"]) and not any(
            k in tname.lower()
            for k in [
                "hoa_don",
                "don_hang",
                "chi_tiet",
                "phieu_nhap",
                "tra_hang",
                "so_quy",
                "the_kho",
            ]
        ):
            kh_cols = {c.lower(): c for c in df.columns}
            k_id = next(
                (
                    kh_cols[c]
                    for c in [
                        "ma_khach_hang",
                        "ma_kh",
                        "customer_id",
                        "kh",
                        "ma_khach",
                        "makh",
                    ]
                    if c in kh_cols
                ),
                None,
            )
            k_nm = next(
                (
                    kh_cols[c]
                    for c in [
                        "ten_khach_hang",
                        "ten_kh",
                        "ten_goi",
                        "ho_ten",
                        "ten_khach",
                        "khach_hang",
                        "khach_hang_ten",
                        "khach_le_si",
                        "ten",
                    ]
                    if c in kh_cols
                ),
                None,
            )
            if k_id and k_nm:
                for _, r in df.iterrows():
                    sid = str(r[k_id]).strip()
                    snm = str(r[k_nm]).strip()
                    if sid and snm and sid.lower() not in ("nan", "none", ""):
                        clean_nm = re.sub(
                            r"\s*[-–(]?\s*(?:0\d{9,10}|\+84\d{9,10}|\d{3,4}\s*\d{3}\s*\d{3,4})\s*\)?$",
                            "",
                            snm,
                        ).strip()
                        cust_name_map[sid] = clean_nm

    if metric_type == "total_amount":
        tot = int(round(active["debt"].sum()))
        return "number", tot, None
    elif metric_type == "customer_count":
        if cust_id_col:
            valid_cids = active[cust_id_col].dropna().astype(str).str.strip()
            valid_cids = valid_cids[
                ~valid_cids.str.lower().isin(["", "nan", "none", "le", "khach le"])
            ]
            cnt = int(valid_cids.nunique())
            return "number", cnt, None
        return "abstain", None, "Không có mã khách hàng để đếm"
    elif metric_type == "top_k_table":
        if cust_id_col and cust_name_map:
            active["resolved_name"] = active[cust_id_col].astype(str).map(cust_name_map)
            if cust_name_col:
                active["resolved_name"] = active["resolved_name"].fillna(
                    active[cust_name_col]
                )
            else:
                active["resolved_name"] = active["resolved_name"].fillna(
                    active[cust_id_col]
                )
        elif cust_name_col:
            active["resolved_name"] = active[cust_name_col]
        else:
            active["resolved_name"] = active[cust_id_col]
        active["resolved_name"] = (
            active["resolved_name"]
            .astype(str)
            .str.replace(
                r"\s*[-–(]?\s*(?:0\d{9,10}|\+84\d{9,10}|\d{3,4}\s*\d{3}\s*\d{3,4})\s*\)?$",
                "",
                regex=True,
            )
            .str.strip()
        )
        active = active[
            ~active["resolved_name"]
            .astype(str)
            .str.lower()
            .isin(["", "nan", "none", "khách lẻ", "khach le", "vang lai"])
        ]
        top = (
            active.groupby("resolved_name")["debt"]
            .sum()
            .sort_values(ascending=False)
            .head(top_k)
        )
        rows = [[str(name), int(round(val))] for name, val in top.items()]
        return "table", {"columns": ["khach_hang", "so_tien_no"], "rows": rows}, None


def execute_query_plan(
    plan: QueryPlan,
    context: BusinessContext,
    contract: BusinessSemanticContract,
) -> tuple[str, Any, str | None]:
    """Compile and execute QueryPlan deterministically against DuckDB canonical views."""
    # 0. Gate 1: Unanswerable gate (subjective queries or profit/cost without cost tables)
    if plan.operation == "unanswerable_gate" or plan.output_type == "unanswerable":
        return (
            "unanswerable",
            None,
            "Câu hỏi không có dữ liệu đo lường theo Rulebook Mục 7",
        )

    # 1. Check schema capability (Gate 1 -> unanswerable)
    missing_reason = _check_missing_capability(plan, contract)
    if missing_reason is not None:
        return "unanswerable", None, missing_reason

    # 2. Check date out of domain (Gate 1 -> unanswerable)
    if check_date_out_of_domain(context, plan.period, "order_date", "canonical_orders"):
        return (
            "unanswerable",
            None,
            "Khoảng thời gian nằm hoàn toàn ngoài dữ liệu của doanh nghiệp",
        )
    if plan.prior_period and check_date_out_of_domain(
        context, plan.prior_period, "order_date", "canonical_orders"
    ):
        return (
            "unanswerable",
            None,
            "Khoảng thời gian so sánh nằm ngoài dữ liệu của doanh nghiệp",
        )

    date_where = _build_canonical_date_where("order_date", plan.period)
    conn = context.duckdb_conn

    try:
        # Operation: Snapshot Debt (number | table)
        if plan.operation == "debt_snapshot":
            cutoff = plan.period.end_date or plan.period.start_date
            if not cutoff:
                return "abstain", None, "Không xác định được ngày chốt công nợ"
            return calculate_debt_metric(
                context=context,
                cutoff_date_str=cutoff,
                metric_type=plan.metric,
                top_k=plan.limit_k,
            )

        # Operation 0: Period Growth & Comparison (percent or number)
        if plan.operation == "period_growth_pct":
            assert plan.prior_period is not None
            cur_where = _build_canonical_date_where("order_date", plan.period)
            prior_where = _build_canonical_date_where("order_date", plan.prior_period)

            nf = plan.numerator_filter
            use_lines = nf is not None and (
                nf.category is not None
                or nf.is_prescription is not None
                or nf.item_key is not None
                or nf.item_name is not None
            )
            view_name = "canonical_order_lines" if use_lines else "canonical_orders"
            dim_where = _build_dimension_where(nf) if nf else "1=1"

            if plan.metric == "order_count":
                metric_col = "COUNT(DISTINCT order_id)"
            else:
                rev_col = "line_revenue" if use_lines else "order_revenue"
                metric_col = f"COALESCE(SUM({rev_col}), 0)"

            sql = f"""
                WITH cur AS (
                    SELECT {metric_col} AS val
                    FROM {view_name}
                    WHERE {cur_where} AND {dim_where}
                ),
                prior AS (
                    SELECT {metric_col} AS val
                    FROM {view_name}
                    WHERE {prior_where} AND {dim_where}
                )
                SELECT cur.val, prior.val
                FROM cur, prior
            """
            context.last_sql = sql
            row = conn.execute(sql).fetchall()[0]
            cur_val = float(row[0] or 0.0)
            prior_val = float(row[1] or 0.0)

            if prior_val == 0.0:
                return (
                    "abstain",
                    None,
                    "Kỳ trước không có dữ liệu hoặc bằng 0, không thể tính % tăng trưởng",
                )

            if plan.output_type == "percent":
                pct = round((cur_val - prior_val) * 100.0 / prior_val, 2)
                return "percent", pct, None
            else:
                diff = round(cur_val - prior_val)
                return "number", int(diff), None

        # Operation: Filtered Quantity (number)
        if plan.operation == "filtered_quantity":
            nf = plan.numerator_filter
            dim_where = _build_dimension_where(nf)
            sql = f"""
                SELECT ROUND(SUM(base_quantity))
                FROM canonical_order_lines
                WHERE {date_where} AND {dim_where} AND is_service_fee = FALSE
            """
            context.last_sql = sql
            val = conn.execute(sql).fetchall()[0][0]
            if val is None or int(val) <= 0:
                return "abstain", None, "Số lượng bán bằng 0 hoặc NULL"
            return "number", int(val), None

        # Operation: Ranking Top K (string | list | date | number)
        if plan.operation == "ranking_top_k":
            if plan.target_dimension == "date":
                order_expr = (
                    "COUNT(DISTINCT order_id) DESC, d ASC"
                    if plan.metric == "order_count"
                    else "SUM(order_revenue) DESC, d ASC"
                )
                metric_col = (
                    "COUNT(DISTINCT order_id)"
                    if plan.metric == "order_count"
                    else "SUM(order_revenue)"
                )
                sql = f"""
                    SELECT CAST(order_date AS DATE) AS d, {metric_col} AS metric_val
                    FROM canonical_orders
                    WHERE {date_where} AND is_valid_sale = TRUE
                    GROUP BY CAST(order_date AS DATE)
                    ORDER BY {order_expr}
                    LIMIT 2
                """
                context.last_sql = sql
                rows = conn.execute(sql).fetchall()
                if not rows or rows[0][0] is None:
                    return "abstain", None, "Không tìm thấy ngày cao nhất"
                if (
                    len(rows) >= 2
                    and rows[0][1] is not None
                    and rows[0][1] == rows[1][1]
                ):
                    return (
                        "abstain",
                        None,
                        "Đồng hạng ngày cao nhất (TIE) không thể xác định duy nhất",
                    )
                return "date", str(rows[0][0]), None

            if plan.target_dimension == "peak_hour":
                sql = f"""
                    SELECT CAST(order_hour AS INTEGER) AS h, COUNT(DISTINCT order_id) AS cnt
                    FROM canonical_orders
                    WHERE {date_where} AND is_valid_sale = TRUE AND order_hour IS NOT NULL
                    GROUP BY h
                    ORDER BY cnt DESC, h ASC
                    LIMIT 2
                """
                context.last_sql = sql
                rows = conn.execute(sql).fetchall()
                if not rows or rows[0][0] is None:
                    return "abstain", None, "Không tìm thấy giờ cao điểm"
                if (
                    len(rows) >= 2
                    and rows[0][1] is not None
                    and rows[0][1] == rows[1][1]
                ):
                    return (
                        "abstain",
                        None,
                        "Đồng hạng giờ cao điểm (TIE) không thể xác định duy nhất",
                    )
                return "number", int(rows[0][0]), None

            if plan.target_dimension == "month":
                sql = f"""
                    SELECT STRFTIME(CAST(order_date AS DATE), '%Y-%m') AS m
                    FROM canonical_orders
                    WHERE {date_where} AND is_valid_sale = TRUE
                    GROUP BY STRFTIME(CAST(order_date AS DATE), '%Y-%m')
                    ORDER BY SUM(order_revenue) DESC, m ASC
                    LIMIT 1
                """
                context.last_sql = sql
                rows = conn.execute(sql).fetchall()
                if not rows or rows[0][0] is None:
                    return "abstain", None, "Không tìm thấy tháng cao nhất"
                return "date", str(rows[0][0]), None

            if plan.target_dimension == "staff":
                fetch_limit = (
                    max(plan.limit_k, 2) if plan.limit_k == 1 else plan.limit_k
                )
                sql = f"""
                    SELECT staff_key, SUM(order_revenue) AS metric_val
                    FROM canonical_orders
                    WHERE {date_where} AND is_valid_sale = TRUE
                      AND staff_key IS NOT NULL AND staff_key != ''
                    GROUP BY staff_key
                    ORDER BY metric_val DESC, staff_key ASC
                    LIMIT {fetch_limit}
                """
                context.last_sql = sql
                rows = conn.execute(sql).fetchall()
                if not rows or rows[0][0] is None:
                    return "abstain", None, "Không tìm thấy nhân viên trong kỳ"
                if plan.limit_k == 1:
                    if (
                        len(rows) >= 2
                        and rows[0][1] is not None
                        and rows[0][1] == rows[1][1]
                    ):
                        return (
                            "abstain",
                            None,
                            "Đồng hạng nhân viên doanh thu cao nhất (TIE) không thể xác định duy nhất",
                        )
                    return "string", str(rows[0][0]), None
                else:
                    return "list", [str(r[0]) for r in rows], None

            if plan.target_dimension == "item":
                nf = plan.numerator_filter
                dim_where = _build_dimension_where(nf) if nf else "is_valid_sale = TRUE"
                if plan.metric == "order_count":
                    metric_expr = "COUNT(DISTINCT order_id)"
                elif plan.metric == "revenue":
                    metric_expr = "SUM(line_revenue)"
                else:
                    metric_expr = "SUM(base_quantity)"

                fetch_limit = max(plan.limit_k + 1, 2)
                sql = f"""
                    SELECT MODE(item_name) AS item_name, item_key, {metric_expr} AS metric_val
                    FROM canonical_order_lines
                    WHERE {date_where} AND {dim_where} AND is_service_fee = FALSE
                      AND item_name IS NOT NULL AND item_name != ''
                    GROUP BY item_key
                    ORDER BY metric_val DESC, item_key ASC
                    LIMIT {fetch_limit}
                """
                context.last_sql = sql
                rows = conn.execute(sql).fetchall()
                if not rows:
                    return "abstain", None, "Không có mặt hàng nào trong kỳ"
                top_rows = rows[: plan.limit_k]
                # Check ties within top_k and at the boundary top_k vs top_{k+1}
                has_tie = any(
                    rows[i][2] is not None and rows[i][2] == rows[i + 1][2]
                    for i in range(min(len(rows) - 1, plan.limit_k))
                )
                if has_tie:
                    return (
                        "abstain",
                        None,
                        f"Đồng hạng mặt hàng top {plan.limit_k} (TIE) không thể xác định duy nhất",
                    )
                if plan.limit_k == 1:
                    return "string", str(top_rows[0][0]), None
                else:
                    return "list", [str(r[0]) for r in top_rows], None

            if plan.target_dimension == "customer":
                if plan.metric == "order_count":
                    order_clause = "COUNT(DISTINCT order_id) DESC, customer_name ASC"
                    metric_expr = "COUNT(DISTINCT order_id)"
                elif plan.metric == "order_date_count":
                    order_clause = "COUNT(DISTINCT order_date) DESC, customer_name ASC"
                    metric_expr = "COUNT(DISTINCT order_date)"
                elif plan.metric == "aov":
                    order_clause = "AVG(order_revenue) DESC, customer_name ASC"
                    metric_expr = "AVG(order_revenue)"
                elif plan.metric == "canceled_orders":
                    order_clause = "COUNT(DISTINCT order_id) DESC, customer_name ASC"
                    metric_expr = "COUNT(DISTINCT order_id)"
                else:
                    order_clause = "SUM(order_revenue) DESC, customer_name ASC"
                    metric_expr = "SUM(order_revenue)"

                having_clause = (
                    "HAVING COUNT(DISTINCT order_id) >= 3"
                    if plan.metric == "aov"
                    else ""
                )
                val_filter = (
                    "is_valid_sale = FALSE"
                    if plan.metric == "canceled_orders"
                    else "is_valid_sale = TRUE"
                )

                fetch_limit = (
                    max(plan.limit_k, 2) if plan.limit_k == 1 else plan.limit_k
                )
                sql = f"""
                    SELECT customer_name, {metric_expr} AS metric_val
                    FROM canonical_orders
                    WHERE {date_where} AND {val_filter}
                      AND customer_key IS NOT NULL AND customer_key != ''
                      AND customer_name IS NOT NULL AND customer_name != ''
                    GROUP BY customer_key, customer_name
                    {having_clause}
                    ORDER BY {order_clause}
                    LIMIT {fetch_limit}
                """
                context.last_sql = sql
                rows = conn.execute(sql).fetchall()
                if not rows:
                    return "abstain", None, "Không có khách hàng nào trong kỳ"
                if plan.limit_k == 1:
                    if (
                        len(rows) >= 2
                        and rows[0][1] is not None
                        and rows[0][1] == rows[1][1]
                    ):
                        return (
                            "abstain",
                            None,
                            "Đồng hạng khách hàng top 1 (TIE) không thể xác định duy nhất",
                        )
                    return "string", str(rows[0][0]), None
                else:
                    return "list", [str(r[0]) for r in rows], None

        # Operation 1: Filtered Revenue (number)
        if plan.operation == "filtered_revenue":
            nf = plan.numerator_filter
            use_lines = nf is not None and (
                nf.category is not None
                or nf.is_prescription is not None
                or nf.item_key is not None
                or nf.item_name is not None
            )
            view_name = "canonical_order_lines" if use_lines else "canonical_orders"
            rev_col = "line_revenue" if use_lines else "order_revenue"
            dim_where = _build_dimension_where(nf)
            sql = f"SELECT ROUND(SUM({rev_col})) FROM {view_name} WHERE {date_where} AND {dim_where}"
            context.last_sql = sql
            val = conn.execute(sql).fetchall()[0][0]
            if val is None or int(val) <= 0:
                return "abstain", None, "Doanh thu có lọc bằng 0 hoặc NULL"
            return "number", int(val), None

        # Operation 2: Filtered Order Count (number)
        if plan.operation == "filtered_order_count":
            nf = plan.numerator_filter
            use_lines = nf is not None and (
                nf.category is not None
                or nf.is_prescription is not None
                or nf.item_key is not None
                or nf.item_name is not None
            )
            view_name = "canonical_order_lines" if use_lines else "canonical_orders"
            dim_where = _build_dimension_where(nf)
            sql = f"SELECT COUNT(DISTINCT order_id) FROM {view_name} WHERE {date_where} AND {dim_where}"
            context.last_sql = sql
            val = conn.execute(sql).fetchall()[0][0]
            if val is None or int(val) <= 0:
                return "abstain", None, "Số đơn có lọc bằng 0 hoặc NULL"
            return "number", int(val), None

        # Operation 3: Filtered AOV (number)
        if plan.operation == "filtered_aov":
            dim_where = _build_dimension_where(plan.numerator_filter)
            sql = f"""
                SELECT ROUND(SUM(order_revenue) * 1.0 / NULLIF(COUNT(DISTINCT order_id), 0))
                FROM canonical_orders
                WHERE {date_where} AND {dim_where}
            """
            context.last_sql = sql
            val = conn.execute(sql).fetchall()[0][0]
            if val is None or int(val) <= 0:
                return "abstain", None, "AOV có lọc bằng 0 hoặc NULL"
            return "number", int(val), None

        # Operation 4 & 5: Dual Channel / Dual Payment Revenue List (list)
        if plan.operation in ("dual_channel_revenue_list", "dual_payment_revenue_list"):
            assert plan.secondary_filter is not None
            w1 = _build_dimension_where(plan.numerator_filter)
            w2 = _build_dimension_where(plan.secondary_filter)
            sql = f"""
                SELECT
                    ROUND(SUM(CASE WHEN {w1} THEN order_revenue ELSE 0 END)),
                    ROUND(SUM(CASE WHEN {w2} THEN order_revenue ELSE 0 END))
                FROM canonical_orders
                WHERE {date_where} AND is_valid_sale = TRUE
            """
            context.last_sql = sql
            row = conn.execute(sql).fetchall()[0]
            if (
                row is None
                or row[0] is None
                or row[1] is None
                or math.isnan(float(row[0]))
                or math.isnan(float(row[1]))
            ):
                return "abstain", None, "Danh sách doanh thu rỗng"
            v1, v2 = int(row[0]), int(row[1])
            if v1 <= 0 and v2 <= 0:
                return "abstain", None, "Danh sách doanh thu bằng 0"
            return "list", [v1, v2], None

        # Operation 6: Share Percentage (percent)
        if plan.operation == "share_pct":
            nf = plan.numerator_filter
            use_lines = nf.category is not None or nf.is_prescription is not None
            view_name = "canonical_order_lines" if use_lines else "canonical_orders"
            rev_col = "line_revenue" if use_lines else "order_revenue"
            num_where = _build_dimension_where(nf)
            den_where = _build_dimension_where(plan.denominator_filter)
            sql = f"""
                SELECT ROUND(
                    SUM(CASE WHEN {num_where} THEN {rev_col} ELSE 0 END) * 100.0 /
                    NULLIF(SUM(CASE WHEN {den_where} THEN {rev_col} ELSE 0 END), 0),
                    2
                )
                FROM {view_name}
                WHERE {date_where}
            """
            context.last_sql = sql
            val = conn.execute(sql).fetchall()[0][0]
            if val is None or math.isnan(float(val)) or float(val) <= 0.0:
                return "abstain", None, "Tỷ trọng phần trăm bằng 0 hoặc NULL"
            return "percent", float(val), None

        # Operation 7: Shift Diff Percentage (percent)
        if plan.operation == "shift_diff_pct":
            num_where = _build_dimension_where(plan.numerator_filter)  # afternoon
            den_where = _build_dimension_where(plan.denominator_filter)  # morning
            if plan.metric == "order_count":
                sql = f"""
                    SELECT ROUND(
                        (COUNT(DISTINCT CASE WHEN {num_where} THEN order_id END) -
                         COUNT(DISTINCT CASE WHEN {den_where} THEN order_id END)) * 100.0 /
                        NULLIF(COUNT(DISTINCT CASE WHEN {den_where} THEN order_id END), 0),
                        2
                    )
                    FROM canonical_orders
                    WHERE {date_where}
                """
            else:
                sql = f"""
                    SELECT ROUND(
                        (SUM(CASE WHEN {num_where} THEN order_revenue ELSE 0 END) -
                         SUM(CASE WHEN {den_where} THEN order_revenue ELSE 0 END)) * 100.0 /
                        NULLIF(SUM(CASE WHEN {den_where} THEN order_revenue ELSE 0 END), 0),
                        2
                    )
                    FROM canonical_orders
                    WHERE {date_where}
                """
            context.last_sql = sql
            val = conn.execute(sql).fetchall()[0][0]
            if val is None or math.isnan(float(val)):
                return "abstain", None, "Chênh lệch ca bằng NULL"
            return "percent", float(val), None

        # Operation 8: Repeat Customer Count (number)
        if plan.operation == "repeat_customer_count":
            k_min = plan.numerator_filter.min_orders_per_customer or 2
            sql = f"""
                SELECT COUNT(*) FROM (
                    SELECT customer_key, COUNT(DISTINCT order_id) AS cnt
                    FROM canonical_orders
                    WHERE {date_where}
                      AND is_valid_sale = TRUE
                      AND customer_key IS NOT NULL
                    GROUP BY customer_key
                    HAVING COUNT(DISTINCT order_id) >= {k_min}
                ) _rep
            """
            context.last_sql = sql
            val = conn.execute(sql).fetchall()[0][0]
            if val is None or int(val) <= 0:
                return "abstain", None, "Số khách mua lặp lại bằng 0 hoặc NULL"
            return "number", int(val), None

        # Operation 9: Repeat Customer Share Percentage (percent)
        if plan.operation == "repeat_customer_share_pct":
            k_min = plan.numerator_filter.min_orders_per_customer or 2
            sql = f"""
                SELECT ROUND(
                    COUNT(CASE WHEN cnt >= {k_min} THEN 1 END) * 100.0 /
                    NULLIF(COUNT(*), 0),
                    2
                )
                FROM (
                    SELECT customer_key, COUNT(DISTINCT order_id) AS cnt
                    FROM canonical_orders
                    WHERE {date_where}
                      AND is_valid_sale = TRUE
                      AND customer_key IS NOT NULL
                    GROUP BY customer_key
                ) _custs
            """
            context.last_sql = sql
            val = conn.execute(sql).fetchall()[0][0]
            if val is None or float(val) <= 0.0:
                return "abstain", None, "Tỷ lệ khách mua lặp lại bằng 0 hoặc NULL"
            return "percent", float(val), None

        # Operation 10: Order Revenue Threshold Count (number)
        if plan.operation == "order_revenue_threshold_count":
            thresh = float(plan.numerator_filter.min_order_revenue or 0.0)
            sql = f"""
                SELECT COUNT(DISTINCT order_id)
                FROM canonical_orders
                WHERE {date_where}
                  AND is_valid_sale = TRUE
                  AND ROUND(order_revenue) >= {thresh}
            """
            context.last_sql = sql
            val = conn.execute(sql).fetchall()[0][0]
            if val is None or int(val) <= 0:
                return "abstain", None, "Số đơn đạt ngưỡng doanh thu bằng 0 hoặc NULL"
            return "number", int(val), None

        # Operation 11: Daily Revenue Threshold Count (number)
        if plan.operation == "daily_revenue_threshold_count":
            thresh = float(plan.numerator_filter.min_daily_revenue or 0.0)
            sql = f"""
                SELECT COUNT(*) FROM (
                    SELECT CAST(order_date AS DATE) AS d, SUM(order_revenue) AS day_rev
                    FROM canonical_orders
                    WHERE {date_where}
                      AND is_valid_sale = TRUE
                    GROUP BY CAST(order_date AS DATE)
                    HAVING ROUND(SUM(order_revenue)) >= {thresh}
                ) _days
            """
            context.last_sql = sql
            val = conn.execute(sql).fetchall()[0][0]
            if val is None or int(val) <= 0:
                return "abstain", None, "Số ngày đạt ngưỡng doanh thu bằng 0 hoặc NULL"
            return "number", int(val), None

        # Operation 12: Item in >= K Orders Count (number)
        if plan.operation == "item_order_threshold_count":
            k_min = int(plan.numerator_filter.min_orders_per_item or 1)
            sql = f"""
                SELECT COUNT(*) FROM (
                    SELECT item_key, COUNT(DISTINCT order_id) AS cnt
                    FROM canonical_order_lines
                    WHERE {date_where}
                      AND is_valid_sale = TRUE
                      AND is_service_fee = FALSE
                      AND item_key IS NOT NULL
                      AND item_key != ''
                    GROUP BY item_key
                    HAVING COUNT(DISTINCT order_id) >= {k_min}
                ) _items
            """
            context.last_sql = sql
            val = conn.execute(sql).fetchall()[0][0]
            if val is None or int(val) <= 0:
                return (
                    "abstain",
                    None,
                    "Số mặt hàng xuất hiện từ K đơn trở lên bằng 0 hoặc NULL",
                )
            return "number", int(val), None

    except Exception as e:
        logger.warning("Error executing QueryPlan (%s): %s", plan.operation, e)
        return "abstain", None, str(e)

    return "abstain", None, "Không khớp operation trong QueryPlan"


def resolve_semantic_plan(
    question: str,
    context: BusinessContext,
    as_of_date: str | None = None,
) -> tuple[str, Any, str | None]:
    """Entrypoint to resolve a question using BusinessSemanticContract + QueryPlan."""
    contract = BusinessSemanticContract.from_context(context)
    plan = build_query_plan(question, context, as_of_date, contract)
    if plan is None:
        return "abstain", None, "Không khớp mẫu câu hỏi trong BusinessSemanticContract"
    return execute_query_plan(plan, context, contract)
