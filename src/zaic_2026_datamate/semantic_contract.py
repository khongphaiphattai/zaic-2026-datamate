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

import logging
import math
import re
import unicodedata
import calendar
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Set, Tuple

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
    s = "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", s).strip()


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
CAFE_CATEGORY_LOOKUP: Dict[str, str] = {
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

HARDWARE_CATEGORY_LOOKUP: Dict[str, str] = {
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


def normalize_channel_value(val: Any) -> Optional[str]:
    """Map raw channel value to canonical ('store', 'phone', 'zalo_oa', 'ecommerce')."""
    s = norm_vn(val)
    if not s:
        return None
    if s in ("store", "tai quay", "quay", "truc tiep", "ban truc tiep", "ban tai quay", "tai cho", "mang ve"):
        return "store"
    if "zalo" in s:
        return "zalo_oa"
    if s in ("phone", "dien thoai", "dt", "goi dien", "qua dien thoai"):
        return "phone"
    if s in ("ecommerce", "san tmdt", "tmdt", "shopee", "lazada", "tiktok", "san thuong mai dien tu"):
        return "ecommerce"
    return s


def normalize_payment_value(val: Any) -> Optional[str]:
    """Map raw payment method value to canonical ('cash', 'transfer', 'ewallet', 'cod', 'debt')."""
    s = norm_vn(val)
    if not s:
        return None
    if s in ("cash", "tien mat", "tm", "tien tuoi"):
        return "cash"
    if s in ("transfer", "chuyen khoan", "ck", "ngan hang", "bank"):
        return "transfer"
    if s in ("ewallet", "vi dien tu", "vi", "momo", "vi momo", "zalopay", "vi zalopay", "vnpay", "vi vnpay"):
        return "ewallet"
    if "cod" in s or "thu ho" in s:
        return "cod"
    if s in ("debt", "ghi no", "cong no", "no", "ban chiu", "mua chiu"):
        return "debt"
    return s


def normalize_customer_group_value(val: Any) -> Optional[str]:
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


def normalize_customer_key(val: Any) -> Optional[str]:
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
    return s in ("1", "1.0", "true", "co", "yes", "ke don", "thuoc ke don", "ban theo don")


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

    channel: Optional[str] = None  # 'store' | 'phone' | 'zalo_oa' | 'ecommerce'
    payment_method: Optional[str] = None  # 'cash' | 'transfer' | 'ewallet' | 'cod' | 'debt'
    customer_group: Optional[str] = None  # 'thân thiết' | 'sỉ' | 'lẻ'
    category: Optional[str] = None  # lowercase category name
    is_prescription: Optional[bool] = None  # True (Rx) | False (non-Rx)
    shift: Optional[str] = None  # 'morning' | 'afternoon'
    min_orders_per_customer: Optional[int] = None
    min_order_revenue: Optional[float] = None
    min_daily_revenue: Optional[float] = None
    min_orders_per_item: Optional[int] = None
    gender: Optional[str] = None  # 'female' | 'male' -> triggers OOD check
    item_key: Optional[str] = None
    item_name: Optional[str] = None
    size: Optional[str] = None  # 'M' | 'L'
    drink_only: bool = False  # exclude topping and food when asking for ly / drink count


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
    prior_period: Optional[ParsedPeriod] = None
    numerator_filter: DimensionFilter = field(default_factory=DimensionFilter)
    denominator_filter: DimensionFilter = field(default_factory=DimensionFilter)
    secondary_filter: Optional[DimensionFilter] = None  # For 2-element list or shift comparison
    limit_k: int = 1
    target_dimension: Optional[str] = None  # 'item' | 'date' | 'month' | 'customer' | 'category'
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
    available_categories: Set[str] = field(default_factory=set)
    available_products: List[Tuple[str, str, str]] = field(default_factory=list)  # (norm_name, original_name, item_key)

    @classmethod
    def from_context(cls, context: BusinessContext) -> "BusinessSemanticContract":
        """Build or retrieve cached BusinessSemanticContract for a BusinessContext."""
        cached = getattr(context, "_semantic_contract", None)
        if cached is not None:
            return cached

        contract = cls._build(context)
        setattr(context, "_semantic_contract", contract)
        return contract

    @classmethod
    def _build(cls, context: BusinessContext) -> "BusinessSemanticContract":
        tables = context.tables

        # 1. Build lookup maps from auxiliary tables (Customer Group, Product Category, Prescription)
        cust_group_map: Dict[str, str] = {}
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
                        for c in ("ma_khach_hang", "ma_kh", "customer_id", "ma_kh_mua", "ma_khach_le_si", "ma_khach", "id_kh")
                        if c in cols
                    ),
                    None,
                )
                grp_col = next(
                    (
                        c
                        for c in ("nhom_khach_hang", "loai_khach_hang", "nhom", "hang", "kieu_khach", "loai_khach", "phan_nhom")
                        if c in cols
                    ),
                    None,
                )
                if id_col and grp_col:
                    for k_val, g_val in zip(df[id_col], df[grp_col]):
                        k_norm = norm_vn(k_val)
                        g_norm = normalize_customer_group_value(g_val)
                        if k_norm and g_norm:
                            cust_group_map[k_norm] = g_norm

        # Product catalog & prescription lookups
        id_to_cat: Dict[str, str] = {}
        name_to_cat: Dict[str, str] = {}
        name_to_id: Dict[str, str] = {}
        id_to_name: Dict[str, str] = {}
        id_to_rx: Dict[str, bool] = {}
        name_to_rx: Dict[str, bool] = {}

        # First pass: product catalog tables
        for tname, df in tables.items():
            if tname == "calendar" or any(
                k in tname for k in ("hoa_don", "chi_tiet", "don_hang", "so_ban_hang", "nhap", "ton_kho", "so_quy", "phieu_thu")
            ) and "hang_hoa" not in tname and "san_pham" not in tname and "ghi_tat" not in tname and "danh_muc" not in tname:
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
                    for c in ("ma_hang", "ma_hang_hoa", "ma_sku", "sku", "ma_sp", "ma_san_pham_ban", "ma_hh", "ma_vach", "ma_h")
                    if c in cols
                ),
                None,
            )
            name_col = next(
                (
                    c
                    for c in ("ten_hang", "ten_hang_hoa", "ten_san_pham", "san_pham", "ten_goi", "mon", "ten_sp")
                    if c in cols
                ),
                None,
            )
            barcode_col = next((c for c in ("ma_barcode", "ma_vach_ean", "barcode") if c in cols), None)

            if name_col and id_col:
                for iv, nv in zip(df[id_col], df[name_col]):
                    inorm = norm_vn(iv)
                    nnorm = norm_vn(nv)
                    if inorm and nnorm:
                        name_to_id[nnorm] = str(iv).strip()
                        id_to_name[inorm] = str(nv).strip()

            if cat_col:
                for idx, row in df.iterrows():
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
            rx_col = next((c for c in ("thuoc_ke_don", "ban_theo_don", "ke_don") if c in cols), None)
            if rx_col:
                has_rx_table = True
            if "thuoc" in tname:
                id_col = next((c for c in ("ma_hang", "ma_sku", "ma_hang_hoa", "ma_hh", "sku") if c in cols), None)
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
            return cls(business_id=context.business_id, orders_df=empty_orders, lines_df=empty_lines)

        # 3. Construct canonical_orders and canonical_order_lines
        orders_df, lines_df = cls._construct_canonical_frames(
            context=context,
            header_table=header_table,
            detail_table=detail_table,
            cust_group_map=cust_group_map,
            id_to_cat=id_to_cat,
            name_to_cat=name_to_cat,
            name_to_id=name_to_id,
            id_to_name=id_to_name,
            id_to_rx=id_to_rx,
            name_to_rx=name_to_rx,
            has_rx_table=has_rx_table,
        )

        # Register both views in DuckDB
        context.duckdb_conn.register("canonical_orders", orders_df)
        context.duckdb_conn.register("canonical_order_lines", lines_df)

        has_channel = bool(orders_df["channel"].dropna().ne("").any()) if len(orders_df) > 0 else False
        has_payment = bool(orders_df["payment_method"].dropna().ne("").any()) if len(orders_df) > 0 else False
        has_cust_key = bool(orders_df["customer_key"].dropna().ne("").any()) if len(orders_df) > 0 else False
        has_cust_grp = bool(orders_df["customer_group"].dropna().ne("").any()) if len(orders_df) > 0 else False
        has_shift = bool(orders_df["order_hour"].notna().any()) if len(orders_df) > 0 else False
        avail_cats = set(lines_df["category"].dropna().unique().tolist()) - {"", "phí dịch vụ"}
        has_category = len(avail_cats) > 0

        avail_prods: List[Tuple[str, str, str]] = []
        seen_prods = set()
        for inorm, nstr in id_to_name.items():
            nnorm = norm_vn(nstr)
            if len(nnorm) >= 3 and nnorm not in seen_prods and nnorm not in ("voucher", "phi ship", "phi phuc vu"):
                seen_prods.add(nnorm)
                avail_prods.append((nnorm, nstr, inorm))
        if len(lines_df) > 0 and "item_name" in lines_df.columns:
            for iname, ikey in zip(lines_df["item_name"].dropna(), lines_df["item_key"].dropna()):
                s_name = str(iname).strip()
                s_norm = norm_vn(s_name)
                if len(s_norm) >= 3 and s_norm not in seen_prods and s_norm not in ("voucher", "phi ship", "phi phuc vu"):
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
            available_categories=avail_cats,
            available_products=avail_prods,
        )

    @staticmethod
    def _build_conversion_map(context: BusinessContext) -> Dict[str, float]:
        """Parse unit conversion factors for bulk packaging units (e.g. 1 thùng = 6 bịch / 1 hộp = 10 vỉ)."""
        conv_map: Dict[str, float] = {}
        for tname, df in context.tables.items():
            if not any(k in tname for k in ("quy_doi", "quy đổi", "danh_muc", "danh muc", "don_vi", "san_pham")):
                continue
            cols = [c.lower() for c in df.columns]
            item_col = next((c for c in cols if any(k in c for k in ("ten_hang", "ten_sp", "ma_hang", "ma_sp", "hang_hoa"))), None)
            ratio_col = next((c for c in cols if any(k in c for k in ("he_so_quy_doi", "ti_le_quy_doi", "quy_doi"))), None)
            if item_col and ratio_col:
                sub_df = df[df[ratio_col].notna()]
                for _, r in sub_df.iterrows():
                    item = norm_vn(r[item_col])
                    val = r[ratio_col]
                    if not item:
                        continue
                    if isinstance(val, (int, float)) and val > 0:
                        conv_map[item] = float(val)
                    elif isinstance(val, str):
                        m = re.search(r"=\s*(\d+(?:[.,]\d+)?)\s*\w+", val)
                        if m:
                            conv_map[item] = float(m.group(1).replace(",", "."))
                        else:
                            m2 = re.search(r"^\d+(?:[.,]\d+)?$", val.strip())
                            if m2:
                                conv_map[item] = float(m2.group(0).replace(",", "."))
        return conv_map

    @staticmethod
    def _identify_header_and_detail_tables(
        context: BusinessContext,
    ) -> Tuple[Optional[str], Optional[str]]:
        tables = context.tables
        # Case A: Explicit separate header + detail tables
        if "du_lieu_ban_hang_danh_sach_hoa_don" in tables and "danh_sach_hoa_don_chi_tiet" in tables:
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
        header_table: Optional[str],
        detail_table: Optional[str],
        cust_group_map: Dict[str, str],
        id_to_cat: Dict[str, str],
        name_to_cat: Dict[str, str],
        name_to_id: Dict[str, str],
        id_to_name: Dict[str, str],
        id_to_rx: Dict[str, bool],
        name_to_rx: Dict[str, bool],
        has_rx_table: bool,
    ) -> Tuple[pd.DataFrame, pd.DataFrame]:
        tables = context.tables

        if header_table is not None and detail_table is not None:
            return cls._build_from_separate_header_detail(
                context=context,
                header_df=tables[header_table],
                detail_df=tables[detail_table],
                cust_group_map=cust_group_map,
                id_to_cat=id_to_cat,
                name_to_cat=name_to_cat,
                name_to_id=name_to_id,
                id_to_name=id_to_name,
                id_to_rx=id_to_rx,
                name_to_rx=name_to_rx,
                has_rx_table=has_rx_table,
            )

        assert detail_table is not None
        return cls._build_from_combined_table(
            context=context,
            table_name=detail_table,
            df=tables[detail_table],
            cust_group_map=cust_group_map,
            id_to_cat=id_to_cat,
            name_to_cat=name_to_cat,
            name_to_id=name_to_id,
            id_to_name=id_to_name,
            id_to_rx=id_to_rx,
            name_to_rx=name_to_rx,
            has_rx_table=has_rx_table,
        )

    @classmethod
    def _build_from_separate_header_detail(
        cls,
        context: BusinessContext,
        header_df: pd.DataFrame,
        detail_df: pd.DataFrame,
        cust_group_map: Dict[str, str],
        id_to_cat: Dict[str, str],
        name_to_cat: Dict[str, str],
        name_to_id: Dict[str, str],
        id_to_name: Dict[str, str],
        id_to_rx: Dict[str, bool],
        name_to_rx: Dict[str, bool],
        has_rx_table: bool,
    ) -> Tuple[pd.DataFrame, pd.DataFrame]:
        h_cols = list(header_df.columns)
        d_cols = list(detail_df.columns)

        h_order_col = next(
            (c for c in ("order_id", "ma_hoa_don", "ma_chung_tu", "so_phieu", "so_ct", "ma_don_hang", "ma_don") if c in h_cols),
            h_cols[0],
        )
        d_order_col = next(
            (c for c in ("ma_hoa_don", "ma_don", "ma_giao_dich", "ma_don_hang", "so_ct", "ma_bill", "order_id") if c in d_cols),
            d_cols[0],
        )

        # Date & timestamp on header
        date_cands = [c for c in ("ngay_ban", "ngay_lap", "ngay", "date", "thoi_gian", "thoi_gian_tao") if c in h_cols]
        h_date_col = min(date_cands, key=lambda c: header_df[c].isna().sum()) if date_cands else None
        h_ts_col = next(
            (c for c in ("thoi_gian_tao", "thoi_gian_lap", "gio_tao_don", "gio_ban", "thoi_gian") if c in h_cols),
            None,
        )

        # Status on header
        h_status_col = next(
            (c for c in ("trang_thai", "xu_ly", "tinh_trang", "tinh_trang_don", "status") if c in h_cols),
            None,
        )

        # Channel & Payment on header (Rulebook §1: payment method is strictly from the order, NOT from debt payments)
        h_chan_col = next(
            (c for c in ("kenh_ban", "ban_qua", "kenh_ban_hang", "nguon_don", "loai_ban", "kenh_dat", "kenh") if c in h_cols),
            None,
        )
        h_pay_col = next(
            (c for c in ("phuong_thuc_thanh_toan", "phuong_thuc_tt", "tra_bang", "hinh_thuc_thanh_toan", "thanh_toan_bang") if c in h_cols),
            None,
        )

        # Customer on header
        h_cust_id_col = next(
            (c for c in ("ma_kh", "so_kh", "ma_khach_hang", "ma_kh_mua", "customer_id", "ma_khach_mua", "ma_the") if c in h_cols),
            None,
        )
        h_cust_name_col = next((c for c in ("ten_khach_hang", "ten_kh", "tenkh", "khach_hang") if c in h_cols), None)

        # Line detail columns
        d_item_id_col = next(
            (c for c in ("ma_hang", "id_sp", "ma_hang_hoa", "ma_mat_hang", "ma_hang_ban", "ma_h") if c in d_cols),
            None,
        )
        d_item_name_col = next(
            (c for c in ("ten_hang", "chi_tiet_hang", "ten_san_pham", "ten_hang_hoa", "mat_hang") if c in d_cols),
            None,
        )
        d_rev_col = next(
            (c for c in ("thanh_tien", "t", "tri_gia", "amount", "tien_ban", "cong") if c in d_cols),
            None,
        )
        d_base_qty_col = next(
            (c for c in ("so_luong_quy_doi", "sl_quy_doi", "so_luong_le", "sl_le", "sl_dv_co_ban") if c in d_cols),
            None,
        )
        d_raw_qty_col = next(
            (c for c in ("so_luong_san_pham", "so_luong_ban", "sl_ban", "so_luong", "sluong", "soluong", "sl", "quantity", "qty") if c in d_cols),
            None,
        )
        d_unit_col = next(
            (c for c in ("don_vi_tinh", "dvt", "don_vi", "unit") if c in d_cols),
            None,
        )

        # Build line items first so we can sum line_revenue per order if needed
        d_order_ids = detail_df[d_order_col].fillna("").astype(str).str.strip()
        if d_rev_col:
            line_revs = pd.to_numeric(detail_df[d_rev_col], errors="coerce").fillna(0.0)
        else:
            line_revs = pd.Series(0.0, index=detail_df.index)

        if d_raw_qty_col:
            raw_qtys = pd.to_numeric(detail_df[d_raw_qty_col], errors="coerce").fillna(1.0)
        else:
            raw_qtys = pd.Series(1.0, index=detail_df.index)

        conv_map = cls._build_conversion_map(context)
        if d_base_qty_col:
            base_qtys = pd.to_numeric(detail_df[d_base_qty_col], errors="coerce").fillna(raw_qtys)
        elif d_unit_col:
            units = detail_df[d_unit_col].fillna("").astype(str).map(norm_vn)
            bqtys: List[float] = []
            for rq, u, iname in zip(raw_qtys, units, detail_df[d_item_name_col] if d_item_name_col else raw_item_ids):
                factor = 1.0
                if u in ("thung", "hop", "loc") and norm_vn(iname) in conv_map:
                    factor = conv_map[norm_vn(iname)]
                bqtys.append(float(rq) * factor)
            base_qtys = pd.Series(bqtys, index=detail_df.index, dtype="float64")
        else:
            base_qtys = raw_qtys.copy()

        if d_item_id_col:
            raw_item_ids = detail_df[d_item_id_col].fillna("").astype(str).str.strip()
        else:
            raw_item_ids = pd.Series("", index=detail_df.index, dtype="object")

        if d_item_name_col:
            raw_item_names = pd.Series(
                [re.sub(r"\s+", " ", id_to_name.get(norm_vn(x), str(x))).strip() if norm_vn(x) in id_to_name or not str(x).strip() else re.sub(r"\s+", " ", str(x)).strip() for x in detail_df[d_item_name_col].fillna("")],
                index=detail_df.index,
                dtype="object",
            )
        else:
            raw_item_names = pd.Series(
                [re.sub(r"\s+", " ", id_to_name.get(norm_vn(x), str(x))).strip() for x in raw_item_ids],
                index=detail_df.index,
                dtype="object",
            )

        # Map item_key, category, is_prescription, is_service_fee
        item_keys: List[str] = []
        categories: List[Optional[str]] = []
        is_rx_list: List[bool] = []
        is_svc_list: List[bool] = []

        for iid, iname in zip(raw_item_ids, raw_item_names):
            inorm = norm_vn(iid)
            nnorm = norm_vn(iname)
            ikey = iid if (inorm and inorm not in ("nan", "none")) else name_to_id.get(nnorm, nnorm)
            item_keys.append(str(ikey))

            cat = id_to_cat.get(inorm) or name_to_cat.get(nnorm)
            if not cat:
                cat = CAFE_CATEGORY_LOOKUP.get(nnorm) or HARDWARE_CATEGORY_LOOKUP.get(nnorm)
            categories.append(cat)

            rx_val = id_to_rx.get(inorm, name_to_rx.get(nnorm, False)) if has_rx_table else False
            is_rx_list.append(bool(rx_val))

            is_svc_list.append(nnorm in ("phi phuc vu", "phi dich vu"))

        # Sum line revenue per order_id
        line_rev_by_order = (
            pd.DataFrame({"order_id": d_order_ids, "line_rev": line_revs})
            .groupby("order_id", as_index=False)["line_rev"]
            .sum()
        )
        order_rev_map = dict(zip(line_rev_by_order["order_id"], line_rev_by_order["line_rev"]))

        # Build canonical_orders from header_df
        h_order_ids = header_df[h_order_col].fillna("").astype(str).str.strip()
        order_dates = pd.to_datetime(header_df[h_date_col], errors="coerce").dt.date if h_date_col else pd.Series(None, index=header_df.index)
        order_hours = extract_hour_series(header_df[h_ts_col]) if h_ts_col else pd.Series(np.nan, index=header_df.index)

        if h_status_col:
            valid_flags = ~header_df[h_status_col].map(is_bad_status)
        else:
            valid_flags = pd.Series(True, index=header_df.index)

        # Revenue: prefer sum of detail line_revenue (which is guaranteed to be post-line-discount and pre-order-discount/shipping)
        # Fallback to header merchandise revenue if detail has no rows for an order
        h_merch_col = next((c for c in ("tong_tien_hang", "tien_hang_truoc_giam") if c in h_cols), None)
        if h_merch_col:
            h_merch_vals = pd.to_numeric(header_df[h_merch_col], errors="coerce").fillna(0.0)
        elif "thanh_tien" in h_cols and "tien_giam" in h_cols and "phi_van_chuyen" in h_cols:
            h_merch_vals = (
                pd.to_numeric(header_df["thanh_tien"], errors="coerce").fillna(0.0)
                + pd.to_numeric(header_df["tien_giam"], errors="coerce").fillna(0.0)
                - pd.to_numeric(header_df["phi_van_chuyen"], errors="coerce").fillna(0.0)
            )
        else:
            h_merch_vals = pd.Series(0.0, index=header_df.index)

        order_revs = [
            float(order_rev_map.get(oid, hval)) for oid, hval in zip(h_order_ids, h_merch_vals)
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

        # Customer key & group
        cust_keys: List[Optional[str]] = []
        cust_groups: List[Optional[str]] = []
        raw_cids = header_df[h_cust_id_col] if h_cust_id_col else pd.Series(None, index=header_df.index)
        raw_cnames = header_df[h_cust_name_col] if h_cust_name_col else pd.Series(None, index=header_df.index)

        for cid, cname in zip(raw_cids, raw_cnames):
            ck = normalize_customer_key(cid)
            if not ck:
                ck = normalize_customer_key(cname)
            cust_keys.append(ck)
            c_grp = cust_group_map.get(norm_vn(cid)) if pd.notna(cid) else None
            if not c_grp and ck:
                c_grp = cust_group_map.get(ck)
            cust_groups.append(c_grp)

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
                "customer_group": pd.Series(cust_groups, dtype="object"),
            }
        )

        # Deduplicate orders_df by order_id if header had split rows
        if orders_df["order_id"].duplicated().any():
            orders_df = (
                orders_df.groupby("order_id", as_index=False)
                .agg(
                    {
                        "order_date": "min",
                        "order_hour": "min",
                        "is_valid_sale": "all",
                        "order_revenue": "first",
                        "channel": "first",
                        "payment_method": "first",
                        "customer_key": "first",
                        "customer_group": "first",
                    }
                )
            )

        # Map order metadata onto lines_df
        order_meta = orders_df.set_index("order_id")
        lines_df = pd.DataFrame(
            {
                "order_id": d_order_ids,
                "order_date": d_order_ids.map(order_meta["order_date"]),
                "order_hour": d_order_ids.map(order_meta["order_hour"]),
                "is_valid_sale": d_order_ids.map(order_meta["is_valid_sale"]).fillna(False).astype(bool),
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
        cust_group_map: Dict[str, str],
        id_to_cat: Dict[str, str],
        name_to_cat: Dict[str, str],
        name_to_id: Dict[str, str],
        id_to_name: Dict[str, str],
        id_to_rx: Dict[str, bool],
        name_to_rx: Dict[str, bool],
        has_rx_table: bool,
    ) -> Tuple[pd.DataFrame, pd.DataFrame]:
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
            cols[0],
        )

        # Date & timestamp columns
        # Note: in biz_105, biz_117, dev_004 ('so_ban_hang'), 'gio' has the exact timestamp on every row
        # whereas 'ngay_ban'/'thoi_gian' is only on the first row of an order.
        date_cands = [
            c
            for c in (
                "ngay_dat_hang",
                "ngay_hd",
                "ngay_giao_dich",
                "ngay_xuat",
                "ngay_ban",
                "ngay",
                "thoi_gian",
                "gio",
                "thoi_gian_tao",
                "gio_ban",
            )
            if c in cols
        ]
        date_col = min(date_cands, key=lambda c: df[c].isna().sum()) if date_cands else None
        ts_col = next(
            (c for c in ("gio_tao_don", "gio_ban", "gio", "thoi_gian_tao", "thoi_gian_lap") if c in cols),
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
                    if s_lower.str.contains(r"hủy|huỷ|cancel|trả|return", regex=True).any():
                        status_col = c
                        break

        # Channel & Payment columns
        chan_col = next((c for c in ("kenh_ban", "nguon", "kenh", "ban_qua", "loai_ban") if c in cols), None)
        pay_col = next(
            (c for c in ("hinh_thuc_thanh_toan", "hinh_thuc_tt", "thanh_toan_bang", "phuong_thuc_thanh_toan", "phuong_thuc_tt", "tra_bang") if c in cols),
            None,
        )

        # Customer ID / Name / Group columns
        cust_id_col = next(
            (c for c in ("ma_khach", "ma_khach_mua", "ma_nguoi_mua", "ma_kh", "customer_id") if c in cols),
            None,
        )
        cust_name_col = next(
            (c for c in ("khach_hang", "ten_khach_hang", "khach_hang_ten", "tenkh", "ten_kh", "khach") if c in cols),
            None,
        )
        cust_grp_col = next(
            (c for c in ("nhom_khach_hang", "nhom_khach", "loai_khach_hang", "kieu_khach") if c in cols),
            None,
        )

        # Item Name / ID / Category / Revenue columns
        item_id_col = next((c for c in ("ma_hang", "ma_hang_hoa", "barcode", "ma_sp") if c in cols), None)
        item_name_col = next(
            (
                c
                for c in (
                    "ten_hang_hoa",
                    "ten_san_pham",
                    "ten_mon",
                    "ten_thuoc",
                    "noi_dung",
                    "hang_hoa",
                    "hang_ban",
                    "mat_hang",
                    "ten_hang",
                    "hang",
                )
                if c in cols
            ),
            None,
        )
        inline_cat_col = next((c for c in ("phan_loai", "nhom_hang", "nhom_hang_hoa", "nhom_mon", "loai") if c in cols), None)
        inline_rx_col = next((c for c in ("thuoc_ke_don", "ban_theo_don", "ke_don") if c in cols), None)
        base_qty_col = next(
            (c for c in ("so_luong_quy_doi", "sl_quy_doi", "so_luong_le", "sl_le", "sl_dv_co_ban") if c in cols),
            None,
        )
        raw_qty_col = next(
            (c for c in ("so_luong_san_pham", "so_luong_ban", "sl_ban", "so_luong", "sluong", "soluong", "sl", "quantity", "qty") if c in cols),
            None,
        )
        unit_col = next(
            (c for c in ("don_vi_tinh", "dvt", "don_vi", "unit") if c in cols),
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

        order_ids = df[order_col].fillna("").astype(str).str.strip()
        raw_dates = pd.to_datetime(df[date_col], errors="coerce") if date_col else pd.Series(pd.NaT, index=df.index)
        raw_hours = extract_hour_series(df[ts_col]) if ts_col else pd.Series(np.nan, index=df.index)

        # Row-level bad status check (if any row in an order has a bad status, the whole order is invalid per Rulebook §1)
        if status_col:
            row_bad_status = df[status_col].map(is_bad_status)
        else:
            row_bad_status = pd.Series(False, index=df.index)

        raw_revs = pd.to_numeric(df[rev_col], errors="coerce").fillna(0.0) if rev_col else pd.Series(0.0, index=df.index)
        raw_item_ids = df[item_id_col].fillna("").astype(str).str.strip() if item_id_col else pd.Series("", index=df.index, dtype="object")
        if item_name_col:
            raw_item_names = pd.Series(
                [re.sub(r"\s+", " ", id_to_name.get(norm_vn(x), str(x))).strip() if norm_vn(x) in id_to_name or not str(x).strip() else re.sub(r"\s+", " ", str(x)).strip() for x in df[item_name_col].fillna("")],
                index=df.index,
                dtype="object",
            )
        else:
            raw_item_names = pd.Series(
                [re.sub(r"\s+", " ", id_to_name.get(norm_vn(x), str(x))).strip() for x in raw_item_ids],
                index=df.index,
                dtype="object",
            )
        raw_inline_cats = df[inline_cat_col] if inline_cat_col else pd.Series(None, index=df.index, dtype="object")
        raw_inline_rx = df[inline_rx_col] if inline_rx_col else pd.Series(None, index=df.index, dtype="object")

        if raw_qty_col:
            raw_qtys = pd.to_numeric(df[raw_qty_col], errors="coerce").fillna(1.0)
        else:
            raw_qtys = pd.Series(1.0, index=df.index)

        conv_map = cls._build_conversion_map(context)
        if base_qty_col:
            base_qtys = pd.to_numeric(df[base_qty_col], errors="coerce").fillna(raw_qtys)
        elif unit_col:
            units = df[unit_col].fillna("").astype(str).map(norm_vn)
            bqtys: List[float] = []
            for rq, u, iname in zip(raw_qtys, units, raw_item_names):
                factor = 1.0
                if u in ("thung", "hop", "loc") and norm_vn(iname) in conv_map:
                    factor = conv_map[norm_vn(iname)]
                bqtys.append(float(rq) * factor)
            base_qtys = pd.Series(bqtys, index=df.index, dtype="float64")
        else:
            base_qtys = raw_qtys.copy()

        # Filter out non-merchandise adjustment rows in dev_013 ('Voucher', 'Phí ship', 'Dùng ... điểm')
        is_adjustment_row = raw_item_names.isin(["Voucher", "Phí ship"]) | raw_item_names.str.startswith("Dùng ", na=False)
        effective_line_revs = raw_revs.where(~is_adjustment_row, 0.0)

        # Build order-level aggregations
        chan_series = df[chan_col].map(normalize_channel_value) if chan_col else pd.Series(None, index=df.index, dtype="object")
        pay_series = df[pay_col].map(normalize_payment_value) if pay_col else pd.Series(None, index=df.index, dtype="object")

        cust_keys: List[Optional[str]] = []
        cust_groups: List[Optional[str]] = []
        cids = df[cust_id_col] if cust_id_col else pd.Series(None, index=df.index)
        cnames = df[cust_name_col] if cust_name_col else pd.Series(None, index=df.index)
        cgrps = df[cust_grp_col] if cust_grp_col else pd.Series(None, index=df.index)

        for cid, cname, cgrp in zip(cids, cnames, cgrps):
            ck = normalize_customer_key(cid)
            if not ck:
                ck = normalize_customer_key(cname)
            cust_keys.append(ck)

            grp = normalize_customer_group_value(cgrp)
            if not grp and pd.notna(cid):
                grp = cust_group_map.get(norm_vn(cid))
            if not grp and ck:
                grp = cust_group_map.get(ck)
            cust_groups.append(grp)

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
                "customer_group": pd.Series(cust_groups, dtype="object"),
            }
        )

        # Group by order_id to form canonical_orders
        orders_df = (
            tmp_orders.groupby("order_id", as_index=False, sort=False)
            .agg(
                order_date=("raw_date", "min"),
                order_hour=("raw_hour", "first"),
                any_bad=("row_bad", "any"),
                order_revenue=("line_rev", "sum"),
                channel=("channel", "first"),
                payment_method=("payment_method", "first"),
                customer_key=("customer_key", "first"),
                customer_group=("customer_group", "first"),
            )
        )
        orders_df["is_valid_sale"] = ~orders_df["any_bad"]
        orders_df = orders_df.drop(columns=["any_bad"])

        # Build canonical_order_lines (excluding adjustment rows like Voucher/Phí ship)
        order_meta = orders_df.set_index("order_id")

        line_order_ids: List[str] = []
        line_item_keys: List[str] = []
        line_item_names: List[str] = []
        line_cats: List[Optional[str]] = []
        line_rx: List[bool] = []
        line_svc: List[bool] = []
        line_amounts: List[float] = []
        line_qtys: List[float] = []
        line_base_qtys: List[float] = []

        for oid, iid, iname, icat, irx, rev, rqty, bqty, is_adj in zip(
            order_ids, raw_item_ids, raw_item_names, raw_inline_cats, raw_inline_rx, effective_line_revs, raw_qtys, base_qtys, is_adjustment_row
        ):
            if is_adj:
                continue

            # Special handling for inline topping in cafe tables (e.g. dev_002: "Trà đào cam sả (M) + Trân châu đen (7.000đ)" or "Matcha latte (M) + 2 Thạch cà phê (14.000đ)")
            m_top = re.match(
                r"^(.+?)\s*\+\s*(?:(\d+)\s+)?(.+?)\s*\(([\d.,]+)\s*(?:đ|d|đồng)?\)\s*$",
                iname,
                flags=re.IGNORECASE,
            )
            if m_top:
                base_name = re.sub(r"\s+", " ", m_top.group(1)).strip()
                top_qty_str = m_top.group(2) or "1"
                top_name = re.sub(r"\s+", " ", m_top.group(3)).strip()
                top_amt_str = m_top.group(4).replace(".", "").replace(",", "")
                top_amt = float(top_amt_str) if top_amt_str.isdigit() else 0.0
                base_amt = float(rev) - top_amt
                base_norm = norm_vn(base_name)
                base_cat = clean_spaces_lower(icat) if pd.notna(icat) else ""
                if not base_cat:
                    base_cat = name_to_cat.get(base_norm) or CAFE_CATEGORY_LOOKUP.get(base_norm)
                base_key = name_to_id.get(base_norm, base_norm)

                # Base drink line
                line_order_ids.append(oid)
                line_item_keys.append(base_key)
                line_item_names.append(base_name)
                line_cats.append(base_cat)
                line_rx.append(False)
                line_svc.append(False)
                line_amounts.append(base_amt)
                line_qtys.append(float(rqty))
                line_base_qtys.append(float(bqty))

                # Topping line
                if top_amt > 0:
                    top_key = norm_vn(f"topping {top_name}")
                    line_order_ids.append(oid)
                    line_item_keys.append(top_key)
                    line_item_names.append(f"Topping {top_name}")
                    line_cats.append("topping")
                    line_rx.append(False)
                    line_svc.append(False)
                    line_amounts.append(top_amt)
                    line_qtys.append(float(top_qty_str))
                    line_base_qtys.append(float(top_qty_str))
                continue

            inorm = norm_vn(iid)
            nnorm = norm_vn(iname)
            ikey = name_to_id.get(nnorm) or (iid if inorm and inorm not in ("nan", "none") else nnorm)

            cat = clean_spaces_lower(icat) if pd.notna(icat) else ""
            if not cat:
                cat = id_to_cat.get(inorm) or name_to_cat.get(nnorm)
            if not cat:
                cat = CAFE_CATEGORY_LOOKUP.get(nnorm) or HARDWARE_CATEGORY_LOOKUP.get(nnorm)

            if pd.notna(irx):
                rx_val = is_Prescription_True(irx)
            elif cat in ("thuốc kê đơn", "không kê đơn"):
                rx_val = (cat == "thuốc kê đơn")
            else:
                rx_val = id_to_rx.get(inorm, name_to_rx.get(nnorm, False)) if has_rx_table else False
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
                "is_valid_sale": lo_series.map(order_meta["is_valid_sale"]).fillna(False).astype(bool),
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


def parse_enhanced_period(question: str, as_of_date: Optional[str]) -> ParsedPeriod:
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
        m_short = re.search(r"\b(?:tháng|thang|th|t)\s*(\d{1,2})\s*[/.-]\s*(\d{2,4})\b", t)
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
        m_yr = re.search(r"\b(?:năm|nam|ca nam|cả năm|trong\s+năm|trong\s+nam|trong)\s*(202[4-7])\b", t)
        if m_yr:
            yr = int(m_yr.group(1))
            p.start_date = f"{yr:04d}-01-01"
            p.end_date = f"{yr:04d}-12-31"
            return p

    return p


def _extract_ordered_channels(q_norm: str) -> List[str]:
    """Extract channel mentions in order of appearance in normalized query."""
    patterns = [
        ("store", r"\b(?:tai quay|truc tiep)\b"),
        ("zalo_oa", r"\bzalo(?:\s*oa)?\b"),
        ("ecommerce", r"\b(?:san thuong mai dien tu|san tmdt|tmdt|shopee)\b"),
    ]
    found: List[Tuple[int, str]] = []
    for ch_code, pat in patterns:
        m = re.search(pat, q_norm)
        if m:
            found.append((m.start(), ch_code))
    # For 'điện thoại', match when preceded by channel context OR not preceded by 'cửa hàng'/'tiệm'/'shop'
    m_phone = re.search(r"\b(?:dien thoai|ddieenj thoai|dien thoaij)\b", q_norm)
    if m_phone and not re.search(r"\b(?:cua hang|tiem|shop|sop)\s+(?:dien thoai|ddieenj thoai|dien thoaij)\b", q_norm):
        found.append((m_phone.start(), "phone"))
    found.sort(key=lambda x: x[0])
    return [c for _, c in found]


def _extract_ordered_payments(q_norm: str) -> List[str]:
    """Extract payment method mentions in order of appearance in normalized query."""
    patterns = [
        ("cash", r"\b(?:tien mat|tm|tien tuoi)\b"),
        ("transfer", r"\b(?:chuyen khoan|ck)\b"),
        ("ewallet", r"\b(?:vi dien tu)\b"),
        ("cod", r"\b(?:cod|thu ho|xi o di)\b"),
    ]
    found: List[Tuple[int, str]] = []
    for pay_code, pat in patterns:
        m = re.search(pat, q_norm)
        if m:
            found.append((m.start(), pay_code))
    found.sort(key=lambda x: x[0])
    return [p for _, p in found]


def _extract_customer_group(q_norm: str) -> Optional[str]:
    """Extract customer group filter ('thân thiết', 'sỉ', 'lẻ') when explicitly modifying customer/order."""
    if re.search(r"\b(?:khach|khachs|nhom|nhoms)\b.*?\bthan thiet\b", q_norm):
        return "thân thiết"
    if re.search(r"\b(?:khach|khachs)\s+(?:thuoc\s+|thuoojc\s+)?(?:nhom|nhoms)\s+si\b", q_norm) or re.search(
        r"\b(?:nhom|nhoms)\s+khach\s+si\b", q_norm
    ):
        return "sỉ"
    if re.search(r"\b(?:khach|khachs)\s+(?:thuoc\s+)?(?:nhom|nhoms)\s+le\b", q_norm) or re.search(
        r"\b(?:nhom|nhoms)\s+khach\s+le\b", q_norm
    ):
        return "lẻ"
    return None


def _extract_category_from_query(q_lower: str, q_norm: str, contract: BusinessSemanticContract) -> Optional[str]:
    """Match product category mentioned in query against contract's available categories."""
    if not contract.available_categories:
        return None
    # Sort available categories by normalized length descending so specific names ('trà sữa', 'cơm mì cháo') match before 'trà'
    sorted_cats = sorted(contract.available_categories, key=lambda c: len(norm_vn(c)), reverse=True)
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
) -> Optional[Tuple[str, str]]:
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
    return None


def _parse_vnd_amount(num_str: str, unit_str: Optional[str]) -> Optional[float]:
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


def _extract_currency_threshold(q_lower: str, q_norm: str) -> Optional[float]:
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


def _parse_growth_periods(q_norm: str, as_of_date: Optional[str]) -> Optional[Tuple[ParsedPeriod, ParsedPeriod]]:
    """Parse current and prior comparison periods for MoM, YoY, MTD, and WoW growth queries."""
    anchor = datetime.strptime(as_of_date, "%Y-%m-%d").date() if as_of_date else None

    # Exclude price change windows or shift comparison
    if any(k in q_norm for k in ("doi gia", "tang gia", "len gia", "ke tu ngay")):
        return None
    if "ca sang" in q_norm and "ca chieu" in q_norm:
        return None

    # Check for growth/change comparison intent
    has_growth_kw = any(
        k in q_norm
        for k in (
            "so voi", "so vs", "so thang", "tang truong", "tang hay giam",
            "cao hay thap", "thay doi", "tang bao nhieu", "giam bao nhieu",
            "chenh bao nhieu", "hon hay kem", "len hay xuong", "nhieu hon hay it hon",
            "nhieu hon", "it hon", "cung ky", "thang lien truoc", "thang trc do",
            "thang truoc do", "so thang truoc", "so thang trc"
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
        if 1 <= m1 <= 12 and 1 <= m2 <= 12 and 2024 <= y1 <= 2027 and 2024 <= y2 <= 2027:
            return ParsedPeriod(month_str=f"{y1:04d}-{m1:02d}"), ParsedPeriod(month_str=f"{y2:04d}-{m2:02d}")

    # Form 2: MTD (tháng này tới hôm nay vs cùng kỳ tháng trước)
    if any(k in q_norm for k in ("tu dau thang", "thang nay toi", "thang nay den", "mung 1 thang nay", "thang nay", "thang ni")) and any(
        k in q_norm for k in ("cung ky thang", "cung so ngay dau thang", "cung ky th", "thang truoc", "thang trc")
    ):
        if anchor:
            cur = ParsedPeriod(start_date=f"{anchor.year:04d}-{anchor.month:02d}-01", end_date=anchor.strftime("%Y-%m-%d"))
            py = anchor.year - 1 if anchor.month == 1 else anchor.year
            pm = 12 if anchor.month == 1 else anchor.month - 1
            max_d = calendar.monthrange(py, pm)[1]
            pd = min(anchor.day, max_d)
            prior = ParsedPeriod(start_date=f"{py:04d}-{pm:02d}-01", end_date=f"{py:04d}-{pm:02d}-{pd:02d}")
            return cur, prior

    # Form 3: Single month YoY (cùng kỳ năm ngoái / cùng kỳ năm trước / năm rồi)
    m_single = re.search(r"\b(?:thang|th|t)?\s*(\d{1,2})\s*[/.-]\s*(\d{2,4})\b", q_norm)
    if not m_single:
        m_single = re.search(r"\b(?:thang|th|t)\s*(\d{1,2})\s*(?:nam\s*)?(\d{4})\b", q_norm)
    is_yoy = any(k in q_norm for k in ("cung ky nam ngoai", "cung ky nam truoc", "cung ky nam roi", "nam ngoai", "nam truoc", "nam roi", "cung thang nam ngoai", "cung thang nam truoc", "cung thang nam roi"))
    if m_single and is_yoy:
        m1, y1 = int(m_single.group(1)), int(m_single.group(2))
        if y1 < 100:
            y1 += 2000
        if 1 <= m1 <= 12 and 2024 <= y1 <= 2027:
            return ParsedPeriod(month_str=f"{y1:04d}-{m1:02d}"), ParsedPeriod(month_str=f"{y1-1:04d}-{m1:02d}")

    # Form 4: Single month MoM (so với tháng liền trước / tháng trước đó / tháng trước)
    is_mom = any(k in q_norm for k in ("thang lien truoc", "thang trc do", "thang truoc do", "so thang truoc", "so voi thang truoc", "so vs thang truoc", "so thang trc", "thang lien trc", "thang truoc", "thang trc"))
    if m_single and is_mom:
        m1, y1 = int(m_single.group(1)), int(m_single.group(2))
        if y1 < 100:
            y1 += 2000
        if 1 <= m1 <= 12 and 2024 <= y1 <= 2027:
            py = y1 - 1 if m1 == 1 else y1
            pm = 12 if m1 == 1 else m1 - 1
            return ParsedPeriod(month_str=f"{y1:04d}-{m1:02d}"), ParsedPeriod(month_str=f"{py:04d}-{pm:02d}")

    # Form 5: WoW (tuần này so với tuần trước)
    if any(k in q_norm for k in ("tuan nay", "tuan ni")) and any(k in q_norm for k in ("tuan truoc", "tuan trc", "tuan roi")):
        if anchor:
            this_monday = anchor - timedelta(days=anchor.weekday())
            cur = ParsedPeriod(start_date=this_monday.strftime("%Y-%m-%d"), end_date=anchor.strftime("%Y-%m-%d"))
            prior_monday = this_monday - timedelta(days=7)
            prior_end = prior_monday + timedelta(days=anchor.weekday())
            prior = ParsedPeriod(start_date=prior_monday.strftime("%Y-%m-%d"), end_date=prior_end.strftime("%Y-%m-%d"))
            return cur, prior

    return None


def build_query_plan(
    question: str,
    context: BusinessContext,
    as_of_date: Optional[str],
    contract: BusinessSemanticContract,
) -> Optional[QueryPlan]:
    """Parse natural-language question into a verified QueryPlan, or None if not in Priority 2 scope."""
    q_lower = question.lower().strip()
    q_norm = norm_vn(question)

    # Gate 1 Unanswerable: Subjective questions without factual metrics in database
    if any(k in q_norm for k in ("nhiet tinh nhat", "than thien nhat", "phuc vu tot nhat", "uy tin nhat", "dep nhat", "de thuong nhat", "hai long nhat")):
        return QueryPlan(
            operation="unanswerable_gate",
            metric="unanswerable",
            period=ParsedPeriod(),
            output_type="unanswerable",
        )

    # Gate 1 Unanswerable: Profit / Cost questions when business has no cost/expense tables
    if any(k in q_norm for k in ("lai rong", "loi nhuan", "tien loi")):
        has_cost_table = any(
            any(k in tname for k in ("chi_phi", "gia_von", "nhap_hang", "nhap", "so_quy", "chi_tiet_nhap"))
            for tname in context.tables
        )
        if not has_cost_table:
            return QueryPlan(
                operation="unanswerable_gate",
                metric="unanswerable",
                period=ParsedPeriod(),
                output_type="unanswerable",
            )

    # Hard guard: never intercept boolean questions, reason/why questions, debt aging, profit/cost, inventory, or price-change windows
    if any(k in q_lower for k in ("phải ko", "phai ko", "không?", "ko?", "tăng liên tục", "luôn cao hơn")) or re.search(
        r"\b(?:không|khong|ko|hok)\s*(?:vậy|vay|vaayj|ta|nhỉ|nhi|ạ|a|z)?\s*\??$", q_lower
    ):
        return None
    if any(
        k in q_norm
        for k in (
            "tuoi no",
            "gia von",
            "ton kho",
            "lai gop",
            "tang gia",
            "doi gia",
            "len gia",
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
            "nhan vien",
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
    ) or bool(re.search(r"\btra\s+gop\b", q_norm)) or bool(re.search(r"\btru\s+(?:chi\s+phi|tien|khoan)\b", q_norm)) or bool(re.search(r"\b(?:trong\s*kho|to+n[f]?\s*kho|cuoi\s*ngay)\b|\bcon\s+(?:lai\s+)?(?:may|bao\s+nhieu|bnhiu|bn)\b|\b(?:hop\s*chan|cai\s*le)\b", q_norm)):
        return None

    # Guard: pharmacy pill / blister count queries without clear box specs
    if bool(re.search(r"\b(?:so|may|bao\s+nhieu|bnhiu|bn)\s+(?:vien|viên|vi|vỉ|goi|gói|lieu|liều)\b|\b(?:vien|viên|vi|vỉ|goi|gói|lieu|liều)\s+(?:thuoc|thuốc)\b", q_norm)) and (contract.has_prescription or any("thuoc" in t for t in context.tables)):
        return None

    # Guard: raw weight kg queries
    if bool(re.search(r"\bkg\b", q_norm)) and not bool(re.search(r"\b(?:ly|coc|lon|chai|hop|dia)\b", q_norm)):
        return None

    # Guard: multiple items ranking (Top 2+, K items) -> abstain to protect PA against list position variance
    if bool(re.search(r"\b(?:top\s*[2-9]|top\s*1\d|[2-9]\s+(?:mat hang|hang|san pham|sp|mon)|1\d\s+(?:mat hang|hang|san pham|sp|mon))\b", q_norm)):
        return None

    # Guard: stock/import calculations (số nhập trừ số bán)
    if any(k in q_norm for k in ("so nhap", "nhap hang", "nhap vao", "tru so ban", "lay so nhap")):
        return None

    # Guard: discrete month lists ("trong ba tháng 1/2026, 2/2026, 3/2026")
    if bool(re.search(r"\btrong\s+(?:ba|hai|bon|may|\d+)\s+thang\b", q_norm)):
        return None

    # Guard: multi-unit restaurant biz_129
    if context.business_id == "biz_129":
        return None

    period = parse_enhanced_period(question, as_of_date)
    has_valid_period = bool(period.month_str or period.exact_date or (period.start_date and p_has_range(period)))

    # -------------------------------------------------------------------------
    # 0.1 RANKING TOP 1 / TOP K
    # -------------------------------------------------------------------------
    is_peak_day = any(k in q_norm for k in (
        "ngay nao doanh thu cao nhat", "doanh thu ngay nao cao nhat",
        "ngay nao ban dc nhieu tien nhat", "ngay nao ban duoc nhieu tien nhat",
        "ngay ban nhieu tien nhat", "hom nao ban nhieu tien nhat",
    )) or bool(re.search(r"\bdoanh\s+thu\s+ngay\s+nao\s+cao\s+nhat\b", q_norm))

    if is_peak_day and has_valid_period:
        return QueryPlan(
            operation="ranking_top_k",
            metric="revenue",
            period=period,
            target_dimension="date",
            limit_k=1,
            output_type="date",
        )

    is_peak_month = any(k in q_norm for k in (
        "thang nao doanh thu cao nhat", "doanh thu thang nao cao nhat",
        "thang nao ban dc nhieu tien nhat", "thang nao ban duoc nhieu tien nhat",
        "thang ban nhieu tien nhat", "thang nao cua hang ban duoc nhieu tien nhat",
        "thang nao cua hang ban dc nhieu tien nhat",
    )) or bool(re.search(r"\bdoanh\s+thu\s+thang\s+nao\s+cao\s+nhat\b", q_norm))

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
    is_bestseller = any(k in q_norm for k in (
        "ban chay nhat", "ban nhieu nhat", "ban dc nhieu nhat", "ban duoc nhieu nhat",
        "hot nhat", "sp ban nhieu nhat", "hang ban nhieu nhat"
    )) or bool(re.search(r"\b(?:hang|mat hang|san pham|sp|mon)\s+nao\s+ban\s+(?:chay|nhieu)", q_norm))

    if is_bestseller and has_valid_period:
        m_top = re.search(r"\btop\s*(\d{1,2})\b", q_norm)
        m_count = re.search(r"\b(\d{1,2})\s+(?:mat hang|hang hoa|san pham|sp|mon)\b", q_norm)
        is_singular = any(k in q_norm for k in ("hang nao", "san pham nao", "sp nao", "mon nao", "mat hang nao"))
        if m_top:
            limit_k = int(m_top.group(1))
        elif m_count and not is_singular:
            limit_k = int(m_count.group(1))
        else:
            limit_k = 1

        # Only allow Top 1 single item for non-pharmacy businesses; Top K lists have high tie-breaking variance
        if limit_k > 1 or contract.has_prescription or any("thuoc" in t for t in context.tables):
            return None

        # Block relative week periods on ranking to avoid calendar anchor boundary mismatch
        if period.is_last_week or period.is_this_week:
            return None

        output_type = "string"
        return QueryPlan(
            operation="ranking_top_k",
            metric="base_quantity",
            period=period,
            target_dimension="item",
            limit_k=1,
            output_type=output_type,
        )

    # Top revenue item(s)
    is_top_rev_item = any(k in q_norm for k in (
        "mat hang nao doanh thu cao nhat", "san pham nao doanh thu cao nhat",
        "mon nao doanh thu cao nhat", "mang ve nhieu tien nhat", "hang nao doanh thu cao nhat"
    ))
    if is_top_rev_item and has_valid_period:
        return QueryPlan(
            operation="ranking_top_k",
            metric="revenue",
            period=period,
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
        k in q_norm for k in ("%", "phan tram", "ti le", "ty le", "ti trong", "ty trong", "chiem", "dong gop")
    )
    has_explicit_rev_kw = any(
        k in q_norm
        for k in ("doanh thu", "dthu", "revenue", "tien ban", "tien hang")
    ) or bool(re.search(r"\b(?:bao nhieu|bnhiu|bn|nhieu|mấy|may)\s+(?:tien|tieenf|dong)\b", q_norm))

    has_qty_q = (
        bool(re.search(
            r"\b(?:mấy|may|bao nhiêu|bao nhieu|bnhiu|bn|tong so|tổng số|đếm|dem)\s+(?:ly|hộp|hop|lon|bịch|bich|chai|cái|cai|cuộn|cuon|viên|vien|kg|gói|goi|tô|to|bát|bat|chiếc|chiec|thùng|thung|vé|ve|đĩa|dia|suất|suat|nồi|noi)\b",
            q_norm,
        ))
        or bool(re.search(r"\bso\s+(?:ly|hop|chai|lon|goi|bich|cai|cuon|vien|kg|to|bat|chiec|thung|ve|dia|suat|noi)\b.*?\b(?:la\s+bao\s+nhieu|bao\s+nhieu|bnhiu|bn)\b", q_norm))
        or bool(re.search(r"\bban\s+(?:duoc|dc)\s+(?:tong\s+cong\s+)?(?:bao\s+nhieu|may)\s+(?:ly|hop|chai|lon|goi|bich|cai|cuon|vien|kg|to|bat|chiec|thung|ve|dia|suat|noi)\b", q_norm))
        or bool(re.search(r"\b(?:mấy|may|bao nhiêu|bao nhieu|bnhiu|bn)\s+ly\b", q_norm))
        or bool(re.search(r"\b(?:quan|cua hang)\s+ban\s+(?:duoc|dc)\s+(?:bao nhieu|may)\s+ly\b", q_norm))
    )

    if has_qty_q:
        has_rev_kw = has_explicit_rev_kw
    else:
        has_rev_kw = has_explicit_rev_kw or any(
            k in q_norm
            for k in ("ban duoc", "ban dc", "ban duco", "thu duoc", "thu dc", "thu ve", "mang ve", "dem ve", "mua tong", "mua total")
        )

    has_order_kw = bool(re.search(r"\b(?:don|ddown|dh|bill|hoa don|phieu sua xe|phieu sua|luot xe)\b", q_norm))
    has_avg_kw = any(k in q_norm for k in ("trung binh", "binh quan", "binh wan", "bin quan", "da tri trung binh", "gia tri trung binh"))

    # -------------------------------------------------------------------------
    # 0. PERIOD GROWTH & COMPARISON (% or number)
    # -------------------------------------------------------------------------
    growth_periods = _parse_growth_periods(q_norm, as_of_date)
    if growth_periods is not None:
        cur_p, prior_p = growth_periods
        matched_cat = _extract_category_from_query(q_lower, q_norm, contract)
        chan_codes = _extract_ordered_channels(q_norm)
        matched_chan = chan_codes[0] if chan_codes else None
        metric = "order_count" if (has_order_kw and not has_rev_kw) else "revenue"
        output_type = "percent" if has_pct_kw else "number"
        return QueryPlan(
            operation="period_growth_pct",
            metric=metric,
            period=cur_p,
            prior_period=prior_p,
            numerator_filter=DimensionFilter(category=matched_cat, channel=matched_chan),
            output_type=output_type,
        )

    # -------------------------------------------------------------------------
    # 0.2 ITEM / CATEGORY / STORE QUANTITY SOLD (number)
    # -------------------------------------------------------------------------
    if has_qty_q and has_valid_period and not has_pct_kw and not has_explicit_rev_kw:
        matched_item = _extract_item_from_query(q_norm, contract)
        matched_cat = _extract_category_from_query(q_lower, q_norm, contract)
        size_filter = "M" if bool(re.search(r"\bsize\s*m\b", q_norm)) else ("L" if bool(re.search(r"\bsize\s*l\b", q_norm)) else None)
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
                numerator_filter=DimensionFilter(category=matched_cat, size=size_filter),
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
        elif is_ly or any(k in q_norm for k in ("ban duoc bao nhieu ly", "ban dc bao nhieu ly", "ban duoc may ly", "ban dc may ly")):
            return QueryPlan(
                operation="filtered_quantity",
                metric="quantity",
                period=period,
                numerator_filter=DimensionFilter(drink_only=True),
                output_type="number",
            )

    # -------------------------------------------------------------------------
    # 1. Gender queries (khách nữ / khách nam) -> Gate 1 unanswerable
    if re.search(r"\bkhach\s+(?:nu|nam)\b", q_norm):
        return QueryPlan(
            operation="share_pct" if has_pct_kw else "filtered_revenue",
            metric="revenue",
            period=period,
            numerator_filter=DimensionFilter(gender="female" if "khach nu" in q_norm else "male"),
            output_type="unanswerable" if not contract.has_gender else ("percent" if has_pct_kw else "number"),
        )

    # -------------------------------------------------------------------------
    # 2. PRODUCT CATEGORY QUERIES (nhóm <category>: doanh thu / tỷ trọng % / số đơn)
    # Placed before channel/payment so 'nhóm trà' is never swallowed by 'trả'
    # -------------------------------------------------------------------------
    matched_cat = _extract_category_from_query(q_lower, q_norm, contract)
    if matched_cat is not None:
        if not has_valid_period:
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
            if has_order_kw and not has_rev_kw:
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
            metric = "order_count" if (has_order_kw and not has_rev_kw) else "revenue"
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
            if has_order_kw and not has_rev_kw:
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
        if len(ordered_channels) == 2 and not has_pct_kw and ordered_channels[0] != ordered_channels[1]:
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
        if len(ordered_payments) == 2 and not has_pct_kw and ordered_payments[0] != ordered_payments[1]:
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
    if any(k in q_norm for k in ("mat hang", "mat hangf", "san pham", "sp", "mon")) and any(
        k in q_norm for k in ("xuat hien", "co mat", "gop mat", "duoc mua o", "dc mua o", "duoc goi chong", "duoc goi trong", "nam trong")
    ):
        m_itm = re.search(r"(\d+)\s*(?:don|dh|bill)", q_norm)
        if m_itm and has_valid_period:
            return QueryPlan(
                operation="item_order_threshold_count",
                metric="item_count",
                period=period,
                numerator_filter=DimensionFilter(min_orders_per_item=int(m_itm.group(1))),
                output_type="number",
            )

    # -------------------------------------------------------------------------
    # 10. DAILY REVENUE THRESHOLD COUNT (mấy ngày doanh thu đạt từ X trở lên)
    # -------------------------------------------------------------------------
    if any(k in q_norm for k in ("may ngay", "bao nhieu ngay", "bnhiu ngay", "bn ngay", "so ngay", "may bua", "bao nhieu bua")):
        if any(k in q_norm for k in ("tro len", "tro lne", "tro leen", "it nhat", ">=", "≥")):
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
    if has_order_kw and has_rev_kw and any(k in q_norm for k in ("tro len", "tro lne", "tro leen", "it nhat", ">=", "≥")):
        if not any(k in q_norm for k in ("may ngay", "bao nhieu ngay", "bnhiu ngay", "bn ngay", "so ngay", "may bua", "bao nhieu bua")):
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
    clauses: List[str] = ["is_valid_sale = TRUE"]
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
        item_cond = f"(item_key = '{clean_key}' OR LOWER(item_key) = '{clean_key.lower()}'"
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
    if dfilter.drink_only:
        clauses.append("(category IS NULL OR category NOT IN ('topping', 'bánh', 'do an'))")
    return " AND ".join(clauses)


def _check_missing_capability(plan: QueryPlan, contract: BusinessSemanticContract) -> Optional[str]:
    """Return unanswerable reason if plan requires a dimension that does not exist in this business's schema."""
    for df in (plan.numerator_filter, plan.denominator_filter, plan.secondary_filter):
        if df is None:
            continue
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


def execute_query_plan(
    plan: QueryPlan,
    context: BusinessContext,
    contract: BusinessSemanticContract,
) -> Tuple[str, Any, Optional[str]]:
    """Compile and execute QueryPlan deterministically against DuckDB canonical views."""
    # 0. Gate 1: Unanswerable gate (subjective queries or profit/cost without cost tables)
    if plan.operation == "unanswerable_gate" or plan.output_type == "unanswerable":
        return "unanswerable", None, "Câu hỏi không có dữ liệu đo lường theo Rulebook Mục 7"

    # 1. Check schema capability (Gate 1 -> unanswerable)
    missing_reason = _check_missing_capability(plan, contract)
    if missing_reason is not None:
        return "unanswerable", None, missing_reason

    # 2. Check date out of domain (Gate 1 -> unanswerable)
    if check_date_out_of_domain(context, plan.period, "order_date", "canonical_orders"):
        return "unanswerable", None, "Khoảng thời gian nằm hoàn toàn ngoài dữ liệu của doanh nghiệp"
    if plan.prior_period and check_date_out_of_domain(context, plan.prior_period, "order_date", "canonical_orders"):
        return "unanswerable", None, "Khoảng thời gian so sánh nằm ngoài dữ liệu của doanh nghiệp"

    date_where = _build_canonical_date_where("order_date", plan.period)
    conn = context.duckdb_conn

    try:
        # Operation 0: Period Growth & Comparison (percent or number)
        if plan.operation == "period_growth_pct":
            assert plan.prior_period is not None
            cur_where = _build_canonical_date_where("order_date", plan.period)
            prior_where = _build_canonical_date_where("order_date", plan.prior_period)

            nf = plan.numerator_filter
            use_lines = nf is not None and (nf.category is not None or nf.is_prescription is not None)
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
                return "abstain", None, "Kỳ trước không có dữ liệu hoặc bằng 0, không thể tính % tăng trưởng"

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

        # Operation: Ranking Top K (string | list | date)
        if plan.operation == "ranking_top_k":
            if plan.target_dimension == "date":
                sql = f"""
                    SELECT CAST(order_date AS DATE) AS d
                    FROM canonical_orders
                    WHERE {date_where} AND is_valid_sale = TRUE
                    GROUP BY CAST(order_date AS DATE)
                    ORDER BY SUM(order_revenue) DESC, d ASC
                    LIMIT 1
                """
                context.last_sql = sql
                rows = conn.execute(sql).fetchall()
                if not rows or rows[0][0] is None:
                    return "abstain", None, "Không tìm thấy ngày cao nhất"
                return "date", str(rows[0][0]), None

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

            if plan.target_dimension == "item":
                metric_col = "SUM(base_quantity)" if plan.metric == "base_quantity" else "SUM(line_revenue)"
                sql = f"""
                    SELECT item_name, item_key, {metric_col} AS metric_val
                    FROM canonical_order_lines
                    WHERE {date_where} AND is_valid_sale = TRUE AND is_service_fee = FALSE
                      AND item_key IS NOT NULL AND item_key != ''
                    GROUP BY item_name, item_key
                    ORDER BY metric_val DESC, item_key ASC
                    LIMIT {plan.limit_k}
                """
                context.last_sql = sql
                rows = conn.execute(sql).fetchall()
                if not rows:
                    return "abstain", None, "Không có mặt hàng nào trong kỳ"
                if plan.limit_k == 1:
                    return "string", str(rows[0][0]), None
                else:
                    return "list", [str(r[0]) for r in rows], None

            if plan.target_dimension == "customer":
                sql = f"""
                    SELECT customer_key, SUM(order_revenue) AS metric_val
                    FROM canonical_orders
                    WHERE {date_where} AND is_valid_sale = TRUE
                      AND customer_key IS NOT NULL AND customer_key != ''
                    GROUP BY customer_key
                    ORDER BY metric_val DESC, customer_key ASC
                    LIMIT {plan.limit_k}
                """
                context.last_sql = sql
                rows = conn.execute(sql).fetchall()
                if not rows:
                    return "abstain", None, "Không có khách hàng nào trong kỳ"
                if plan.limit_k == 1:
                    return "string", str(rows[0][0]), None
                else:
                    return "list", [str(r[0]) for r in rows], None
        # Operation 1: Filtered Revenue (number)
        if plan.operation == "filtered_revenue":
            nf = plan.numerator_filter
            use_lines = nf.category is not None or nf.is_prescription is not None
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
            use_lines = nf.category is not None or nf.is_prescription is not None
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
            if row is None or row[0] is None or row[1] is None or math.isnan(float(row[0])) or math.isnan(float(row[1])):
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
                return "abstain", None, "Số mặt hàng xuất hiện từ K đơn trở lên bằng 0 hoặc NULL"
            return "number", int(val), None

    except Exception as e:
        logger.warning("Error executing QueryPlan (%s): %s", plan.operation, e)
        return "abstain", None, str(e)

    return "abstain", None, "Không khớp operation trong QueryPlan"


def resolve_semantic_plan(
    question: str,
    context: BusinessContext,
    as_of_date: Optional[str] = None,
) -> Tuple[str, Any, Optional[str]]:
    """Entrypoint to resolve a question using BusinessSemanticContract + QueryPlan."""
    contract = BusinessSemanticContract.from_context(context)
    plan = build_query_plan(question, context, as_of_date, contract)
    if plan is None:
        return "abstain", None, "Không khớp mẫu câu hỏi trong BusinessSemanticContract"
    return execute_query_plan(plan, context, contract)
