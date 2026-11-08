"""
cleaner.py: Physical unmerging and Vietnamese commercial data normalization.
"""

from __future__ import annotations

import re
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
    non_null_counts = raw_df.notna().sum(axis=1)
    threshold = int(raw_df.shape[1] * non_null_ratio)
    header_candidates = non_null_counts[non_null_counts >= threshold]

    header_idx = header_candidates.index[0] if not header_candidates.empty else 0

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
