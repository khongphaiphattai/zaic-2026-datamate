"""
cleaner.py: Physical unmerging and Vietnamese commercial data normalization.
"""

from __future__ import annotations

import math
import re
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

import openpyxl
import pandas as pd


def physical_unmerge_and_broadcast(
    wb_path: str | Path, sheet_name: str | None = None
) -> openpyxl.Workbook:
    """Load an Excel workbook, physically unmerge all merged ranges and broadcast

    the top-left value across all constituent cells.
    """
    wb = openpyxl.load_workbook(wb_path, data_only=True)
    target_sheets = [wb[sheet_name]] if sheet_name else wb.worksheets

    for sheet in target_sheets:
        merged_ranges = list(sheet.merged_cells.ranges)
        for merge_range in merged_ranges:
            min_col, min_row, max_col, max_row = merge_range.bounds
            top_left_value = sheet.cell(row=min_row, column=min_col).value
            sheet.unmerge_cells(str(merge_range))
            for r in range(min_row, max_row + 1):
                for c in range(min_col, max_col + 1):
                    sheet.cell(row=r, column=c).value = top_left_value
    return wb


def clean_vietnamese_currency(val: Any) -> float | None:
    """Parse Vietnamese currency strings, shorthand notation, and accounting negatives.

    Examples:
        '100.000 đ' -> 100000.0
        '1.5tr' / '1,5 triệu' -> 1500000.0
        '120k' -> 120000.0
        '(50.000)' -> -50000.0
    """
    if pd.isna(val) or val is None:
        return None
    if isinstance(val, (int, float)):
        return float(val)

    s = str(val).lower().strip().replace("đ", "").replace("vnd", "").strip()
    if not s:
        return None

    is_negative = (s.startswith("(") and s.endswith(")")) or s.startswith("-")
    s = s.strip("() -")

    # 1. Compound Vietnamese patterns: 5tr520 -> 5,520,000; 1tr5 -> 1,500,000; 1tr580 -> 1,580,000
    m_tr_comp = re.match(r"^(\d+)\s*(?:tr|triệu)\s*(\d+)$", s)
    if m_tr_comp:
        m1 = int(m_tr_comp.group(1))
        g2 = m_tr_comp.group(2)
        m2 = int(g2.ljust(6, "0")[:6])
        res = float(m1 * 1_000_000 + m2)
        return -res if is_negative else res

    # 2. Compound thousands patterns: 2k5 -> 2,500
    m_k_comp = re.match(r"^(\d+)\s*k\s*(\d+)$", s)
    if m_k_comp:
        m1 = int(m_k_comp.group(1))
        g2 = m_k_comp.group(2)
        m2 = int(g2.ljust(3, "0")[:3])
        res = float(m1 * 1_000 + m2)
        return -res if is_negative else res

    if "tr" in s or "triệu" in s:
        nums = re.findall(r"[-+]?(?:\d*\.\d+|\d+)", s.replace(",", "."))
        res = float(nums[0]) * 1_000_000 if nums else None
    elif "k" in s:
        nums = re.findall(r"[-+]?(?:\d*\.\d+|\d+)", s.replace(",", "."))
        res = float(nums[0]) * 1_000 if nums else None
    else:
        # Standard Vietnamese thousands dot separator
        cleaned = re.sub(r"[^\d.]", "", s)
        if cleaned.count(".") > 1:
            cleaned = cleaned.replace(".", "")
        elif cleaned.count(".") == 1:
            parts = cleaned.split(".")
            if len(parts[1]) == 3:  # 150.000 -> 150000
                cleaned = parts[0] + parts[1]
        try:
            res = (
                float(cleaned)
                if cleaned and any(c.isdigit() for c in cleaned)
                else None
            )
        except ValueError:
            res = None

    if res is not None and is_negative:
        return -res
    return res


def sniff_and_clean_dataframe(
    raw_df: pd.DataFrame, non_null_ratio: float = 0.5
) -> pd.DataFrame:
    """Identify the true header row among decorative metadata and clean columns."""
    if raw_df.empty:
        return raw_df

    # 1. Detect header row by finding the row with high non-null density
    # Require >= 2 cells on multi-column sheets so a 1-cell banner title is never picked.
    non_null_counts = raw_df.notna().sum(axis=1)
    threshold = (
        max(2, math.ceil(raw_df.shape[1] * non_null_ratio))
        if raw_df.shape[1] >= 2
        else 1
    )
    header_candidates = non_null_counts[non_null_counts >= threshold]

    header_idx = (
        header_candidates.index[0]
        if not header_candidates.empty
        else int(non_null_counts.idxmax())
    )

    df = raw_df.iloc[header_idx + 1 :].copy()
    raw_headers = raw_df.iloc[header_idx].tolist()

    # Clean headers
    clean_cols = []
    for i, col in enumerate(raw_headers):
        if pd.isna(col) or str(col).strip() == "":
            clean_cols.append(f"col_{i}")
        else:
            c = str(col).strip().lower()
            c = re.sub(r"[^\w\s]", "", c)
            c = re.sub(r"\s+", "_", c)
            clean_cols.append(c)
    df.columns = clean_cols

    # Drop completely empty rows or footer noise (e.g. 'Tổng cộng', 'Tổng tháng', 'Ký tên')
    df = df.dropna(how="all")
    first_col = df.columns[0]
    df = df[
        ~df[first_col]
        .astype(str)
        .str.lower()
        .str.contains(
            r"tổng cộng|tổng tháng|^tổng\b|^cộng\b|ký tên|người lập",
            regex=True,
            na=False,
        )
    ]

    return df.reset_index(drop=True)


def split_inline_toppings(
    item_name: str,
    line_revenue: float | None = None,
    line_qty: float = 1.0,
    line_base_qty: float | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Split F&B items with inline `+ Topping (price)` suffixes into base and topping lines.

    Handles single or multiple inline toppings, e.g.:
    'Trà đào cam sả (M) + Trân châu đen (7.000đ)'
    'Trà sữa (L) + 2 Trân châu trắng (10.000đ) + Pudding trứng (8.000đ)'
    """
    raw_name = str(item_name or "").strip()
    if line_base_qty is None:
        line_base_qty = line_qty

    if " + " not in raw_name:
        return {
            "name": re.sub(r"\s+", " ", raw_name).strip(),
            "amount": float(line_revenue) if line_revenue is not None else 0.0,
            "qty": float(line_qty),
            "base_qty": float(line_base_qty),
        }, []

    parts = [re.sub(r"\s+", " ", p).strip() for p in raw_name.split(" + ")]
    base_name = parts[0]
    toppings: list[dict[str, Any]] = []
    topping_total_amt = 0.0

    for seg in parts[1:]:
        if not seg:
            continue
        m_seg = re.match(
            r"^(?:(\d+(?:\.\d+)?)\s+)?(.+?)(?:\s*\(([\d.,]+)\s*(?:đ|d|đồng)?\))?\s*$",
            seg,
            flags=re.IGNORECASE,
        )
        if not m_seg:
            continue
        top_qty = float(m_seg.group(1)) if m_seg.group(1) else 1.0
        top_name = re.sub(r"\s+", " ", m_seg.group(2)).strip()
        top_amt = 0.0
        if m_seg.group(3):
            amt_digits = m_seg.group(3).replace(".", "").replace(",", "")
            if amt_digits.isdigit():
                top_amt = float(amt_digits)
        topping_total_amt += top_amt
        toppings.append(
            {
                "name": f"Topping {top_name}",
                "raw_name": top_name,
                "amount": top_amt,
                "qty": top_qty,
                "base_qty": top_qty,
            }
        )

    base_amt = (
        max(0.0, float(line_revenue) - topping_total_amt)
        if line_revenue is not None
        else 0.0
    )
    return {
        "name": base_name,
        "amount": base_amt,
        "qty": float(line_qty),
        "base_qty": float(line_base_qty),
    }, toppings


_QUERY_TELEX_WORDS: dict[str, str] = {
    "bnhiu": "bao nhiêu",
    "nhiu": "nhiều",
    "nhìu": "nhiều",
    "nhieeu": "nhiều",
    "nhieeuf": "nhiều",
    "nhieuf": "nhiều",
    "nhiuf": "nhiều",
    "nhìuf": "nhiều",
    "dduwowcj": "được",
    "dduowcj": "được",
    "dduowjc": "được",
    "duwowcj": "được",
    "duocj": "được",
    "đươc": "được",
    "đươcj": "được",
    "đc": "được",
    "cuwar": "cửa",
    "naof": "nào",
    "nhaats": "nhất",
    "nhaat": "nhất",
    "nhta": "nhất",
    "ddown": "đơn",
    "mooix": "mỗi",
    "mổi": "mỗi",
    "mội": "mỗi",
    "hown": "hơn",
    "cungf": "cùng",
    "thangs": "tháng",
    "ngayf": "ngày",
    "hangf": "hàng",
    "mawtj": "mặt",
    "chayj": "chạy",
    "nhuwngx": "những",
    "sanr": "sản",
    "phaamr": "phẩm",
    "mootj": "một",
    "hoom": "hôm",
    "nawm": "năm",
    "quys": "quý",
    "khoong": "không",
    "nhaf": "nhà",
    "nhaapj": "nhập",
    "toongr": "tổng",
    "coongj": "cộng",
    "vaof": "vào",
    "heets": "hết",
    "bieets": "biết",
    "bieet": "biết",
    "vaayj": "vậy",
    "laf": "là",
    "tooi": "tôi",
    "hoir": "hỏi",
    "trawm": "trăm",
    "ptram": "phần trăm",
    "tieenf": "tiền",
    "soos": "số",
    "toonf": "tồn",
    "cko": "cho",
    "ees": "ế",
    "cuar": "của",
    "gaanf": "gần",
    "ganf": "gần",
    "nayf": "này",
    "nafy": "này",
    "quaan": "quân",
    "xuaats": "xuất",
    "its": "ít",
    "trowr": "trở",
    "leen": "lên",
    "tuwf": "từ",
    "ddos": "đó",
    "đos": "đó",
    "ddax": "đã",
    "đax": "đã",
    "ddoof": "đồ",
    "đoof": "đồ",
    "ddoor": "đồ",
    "đown": "đơn",
    "bij": "bị",
    "huyr": "huỷ",
    "huyx": "huỷ",
    "tir": "tỉ",
    "giups": "giúp",
    "giumf": "giùm",
    "muooi": "mười",
    "bans": "bán",
    "khachs": "khách",
    "nhoms": "nhóm",
    "gias": "giá",
    "maays": "mấy",
    "giamr": "giảm",
    "sangs": "sáng",
    "nowj": "nợ",
    "veef": "về",
    "binhf": "bình",
    "leej": "lệ",
    "vowis": "với",
    "bawngf": "bằng",
    "nhir": "nhỉ",
    "ddieenj": "điện",
    "vaf": "và",
    "luwowtj": "lượt",
    "cuoois": "cuối",
    "thuoocs": "thuốc",
    "trar": "trả",
    "thoaij": "thoại",
    "nguwowif": "người",
    "ddeems": "đếm",
    "thuoocj": "thuộc",
    "tieeu": "tiêu",
}
_QUERY_TELEX_RE = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in _QUERY_TELEX_WORDS) + r")\b",
    re.IGNORECASE,
)


def round_half_up(value: float) -> int:
    """Round half up to the nearest integer per benchmark rounding rules."""
    return int(Decimal(str(value)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def normalize_query_telex(text: Any) -> str:
    """Repair unambiguous Vietnamese Telex typos and colloquial variants on query text only."""
    if text is None:
        return ""
    s = re.sub(r"\bbans\b", "bán", str(text), flags=re.IGNORECASE)
    s = re.sub(
        r"\b(?:bán|ban)\s+(?:dc|đc|đươc)\b",
        "bán được",
        s,
        flags=re.IGNORECASE,
    )
    s = re.sub(
        r"\b(giao|nhập|nhap|mua|thu|làm|lam|đặt|dat|chốt|chot|ko|không|khong)\s+dc\b(?!\s*\d+\s*(?:v|w|a)\b)",
        r"\1 được",
        s,
        flags=re.IGNORECASE,
    )
    s = re.sub(
        r"\b(?:bao|b\.?)\s*nh(?:[iì]u|ieeu|ieeuf|ieuf|iuf|ìuf)\b",
        "bao nhiêu",
        s,
        flags=re.IGNORECASE,
    )
    s = re.sub(
        r"\bcos\s+(?=(?:tăng|tang|giảm|giam|tổng|tong|bao|bnhiu|bn|mấy|may|không|khong|ko|phải|phai|đúng|dung|ai)\b)",
        "có ",
        s,
        flags=re.IGNORECASE,
    )
    s = re.sub(r"\btoor?g\s+coor?g\b", "tổng cộng", s, flags=re.IGNORECASE)
    s = re.sub(r"\b(?:toor?g|toongr)\b", "tổng", s, flags=re.IGNORECASE)
    s = re.sub(r"\bcoor?g\b", "cộng", s, flags=re.IGNORECASE)
    s = re.sub(
        r"\bbans\s+(?=(?:được|duoc|dc|đc|ra|trong|tháng|thang|ngày|ngay)\b)",
        "bán ",
        s,
        flags=re.IGNORECASE,
    )
    s = re.sub(
        r"\bmi\s+(?=(?:đơn|don|cuốn|cuon|cây|cay|ly|cái|cai|món|mon|trăm|tram|triệu|trieu|nghìn|nghin|k\b|ngày|ngay|tháng|thang|hộp|hop|chai|lon|phần|phan|khách|khach)\b)",
        "mấy ",
        s,
        flags=re.IGNORECASE,
    )
    return _QUERY_TELEX_RE.sub(lambda m: _QUERY_TELEX_WORDS[m.group(0).lower()], s)


def compact_digit_unit_spaces(text: str) -> str:
    """Compact spaces between numbers and measurement units (e.g. '100 ml' -> '100ml')."""
    return re.sub(
        r"\b(\d+(?:\s+\d+)?)\s+(ml|mg|mcg|g|kg|l|lit|v|w|a|mah|cm|mm|m|gb|tb|oz)\b",
        lambda m: m.group(1).replace(" ", "") + m.group(2),
        text,
    )


PHONE_SUFFIX_RE = re.compile(
    r"\s*[-–(]?\s*(?:(?:\+84|0)[\d\s.-]{8,14}|\d{3,4}[\s.-]*\d{3}[\s.-]*\d{3,4})\s*\)?$"
)


def strip_customer_phone(val: Any) -> str:
    """Strip trailing phone number suffixes from customer display names."""
    if val is None:
        return ""
    raw = str(val).strip()
    if not raw or raw.lower() in ("nan", "none", "null", "nat"):
        return ""
    cleaned = PHONE_SUFFIX_RE.sub("", raw).strip()
    return cleaned if cleaned else raw
