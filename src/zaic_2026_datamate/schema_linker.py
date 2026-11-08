"""
schema_linker.py: Schema Linking, Entity/Value Alignment, and Ambiguity Detection.
Inspired by BIRD / SOTA Data Agent architectures (HKUSTDial/awesome-data-agents).
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field

import pandas as pd
from rapidfuzz import fuzz


def remove_vietnamese_accents(text: str) -> str:
    """Normalize Vietnamese text to ASCII lowercase without accents for robust fuzzy matching."""
    text = unicodedata.normalize("NFD", text.lower())
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    return text.replace("đ", "d")


# Vietnamese commercial business terms mapped to candidate schema column patterns
VIETNAMESE_BUSINESS_SYNONYMS: dict[str, list[str]] = {
    "doanh thu": [
        "doanh_thu",
        "thanh_tien",
        "so_luong",
        "don_gia",
        "gia_ban",
        "tien_hang",
        "tong_tien",
    ],
    "doanh so": [
        "doanh_so",
        "doanh_thu",
        "so_luong",
        "don_gia",
        "gia_ban",
        "tien_hang",
    ],
    "tien hang": ["tien_hang", "thanh_tien", "so_luong", "don_gia", "gia_ban"],
    "gia von": ["gia_von", "gia_nhap", "cogs", "tien_von", "von"],
    "gia ban": ["gia_ban", "don_gia", "gia"],
    "loi nhuan": [
        "loi_nhuan",
        "lai",
        "bien_lai",
        "gia_ban",
        "gia_nhap",
        "gia_von",
        "cogs",
    ],
    "khach hang": ["khach_hang", "khach", "ten_kh", "nguoi_mua", "ma_kh"],
    "san pham": ["san_pham", "ten_hang", "mat_hang", "hang_hoa", "ten_sp", "sp"],
    "so luong": ["so_luong", "sl", "so_luong_ban", "so_luong_nhap"],
    "cong no": ["cong_no", "no", "chua_tra", "con_lai", "con_no", "no_cu"],
    "chiet khau": ["chiet_khau", "giam_gia", "ck", "khuyen_mai"],
}


@dataclass
class LinkedSchema:
    matched_columns: dict[str, float] = field(default_factory=dict)
    matched_values: dict[str, list[tuple[str, float]]] = field(default_factory=dict)
    coverage_score: float = 0.0
    is_ambiguous: bool = False
    refusal_reason: str | None = None


@dataclass
class MultiTableLinkedSchema:
    matched_tables: dict[str, float] = field(default_factory=dict)
    matched_columns: dict[str, dict[str, float]] = field(default_factory=dict)
    matched_values: dict[str, dict[str, list[tuple[str, float]]]] = field(
        default_factory=dict
    )
    is_ambiguous: bool = False
    refusal_reason: str | None = None


class MultiTableSchemaLinker:
    """Connects user queries to multiple tables, columns, and cell values across a business.

    Implements Gate 1 across multi-table business contexts.
    """

    def __init__(self, tables: dict[str, pd.DataFrame]):
        self.tables = tables
        self.linkers = {
            tname: SchemaLinker(df, table_name=tname)
            for tname, df in tables.items()
            if not df.empty
        }

    def link(self, query: str) -> MultiTableLinkedSchema:
        norm_query = remove_vietnamese_accents(query)
        matched_tables: dict[str, float] = {}
        matched_cols: dict[str, dict[str, float]] = {}
        matched_vals: dict[str, dict[str, list[tuple[str, float]]]] = {}

        for tname, linker in self.linkers.items():
            norm_tname = remove_vietnamese_accents(tname).replace("_", " ")
            t_ratio = fuzz.token_set_ratio(norm_tname, norm_query)
            if t_ratio >= 75.0 or norm_tname in norm_query:
                matched_tables[tname] = t_ratio

            res = linker.link(query)
            if not res.is_ambiguous and res.matched_columns:
                matched_tables[tname] = max(matched_tables.get(tname, 0.0), 80.0)
                matched_cols[tname] = res.matched_columns
                if res.matched_values:
                    matched_vals[tname] = res.matched_values

        # If question has relative temporal terms, calendar table is linked
        if any(
            w in norm_query
            for w in ["thang", "ngay", "nam", "hom nay", "tuan", "tet", "le"]
        ):
            if "calendar" in self.linkers:
                matched_tables["calendar"] = max(
                    matched_tables.get("calendar", 0.0), 80.0
                )

        # Gate 1 check: if no table matched at all, refuse
        if not matched_tables:
            return MultiTableLinkedSchema(
                is_ambiguous=True,
                refusal_reason="Không tìm thấy bảng dữ liệu, cột hoặc thực thể nào khớp với câu hỏi.",
            )

        return MultiTableLinkedSchema(
            matched_tables=matched_tables,
            matched_columns=matched_cols,
            matched_values=matched_vals,
            is_ambiguous=False,
        )


class SchemaLinker:
    """Connects user queries in Vietnamese natural language to table schemas and cell values.

    Implements Gate 1 of strict-refusal-eval (Data Presence & Ambiguity Check).
    """

    def __init__(self, df: pd.DataFrame, table_name: str = "dataset"):
        self.df = df
        self.table_name = table_name
        self.columns = list(df.columns)
        self.normalized_columns = {
            col: remove_vietnamese_accents(col) for col in self.columns
        }

        # Cache top distinct values for text/category columns (for value linking)
        self.column_value_cache: dict[str, list[str]] = {}
        for col in self.columns:
            if pd.api.types.is_string_dtype(self.df[col]) or self.df[col].dtype in (
                "object",
                "string",
                "str",
            ):
                unique_vals = [str(v) for v in self.df[col].dropna().unique()[:100]]
                self.column_value_cache[col] = unique_vals

    def link(self, query: str, fuzzy_threshold: float = 65.0) -> LinkedSchema:
        """Link query terms to columns and categorical cell values."""
        norm_query = remove_vietnamese_accents(query)
        matched_cols: dict[str, float] = {}
        matched_vals: dict[str, list[tuple[str, float]]] = {}

        # 1. Column matching using token_set_ratio and whole token presence
        for orig_col, norm_col in self.normalized_columns.items():
            col_phrase = norm_col.replace("_", " ")
            ratio = fuzz.token_set_ratio(col_phrase, norm_query)
            col_words = col_phrase.split()
            all_words_present = (
                all(w in norm_query.split() for w in col_words) if col_words else False
            )

            if ratio >= 80.0 or all_words_present or col_phrase in norm_query:
                matched_cols[orig_col] = max(
                    ratio, 95.0 if all_words_present else ratio
                )

        # 1.1 Expand via Vietnamese commercial business synonyms (e.g. 'doanh thu' -> 'so_luong', 'gia_ban')
        for term, candidate_cols in VIETNAMESE_BUSINESS_SYNONYMS.items():
            if term in norm_query:
                for cand in candidate_cols:
                    for orig_col, norm_col in self.normalized_columns.items():
                        if cand == norm_col or cand in norm_col:
                            matched_cols[orig_col] = max(
                                matched_cols.get(orig_col, 0.0), 90.0
                            )

        # 2. Value matching (Entity linking into cell contents)
        for col, val_list in self.column_value_cache.items():
            col_matches = []
            for val in val_list:
                norm_val = remove_vietnamese_accents(val)
                if len(norm_val) > 2:
                    # Use token_set_ratio to match product variations (e.g. omitting brand)
                    score = fuzz.token_set_ratio(norm_val, norm_query)
                    if score >= 70.0 or (len(norm_val) >= 4 and norm_val in norm_query):
                        col_matches.append(
                            (val, max(score, 95.0 if norm_val in norm_query else score))
                        )
            if col_matches:
                col_matches.sort(key=lambda x: x[1], reverse=True)
                matched_vals[col] = col_matches[:5]
                # If a value in this column matched, the column itself is relevant
                matched_cols[col] = max(matched_cols.get(col, 0.0), 85.0)

        # 3. Ambiguity & Coverage Evaluation (Gate 1 of Strict-Refusal-Eval)
        coverage = len(matched_cols) / max(1, len(self.columns))
        is_ambiguous = False
        refusal_reason = None

        if not matched_cols:
            is_ambiguous = True
            refusal_reason = "Không tìm thấy cột dữ liệu hoặc thực thể nào trong bảng khớp với câu hỏi."

        return LinkedSchema(
            matched_columns=matched_cols,
            matched_values=matched_vals,
            coverage_score=coverage,
            is_ambiguous=is_ambiguous,
            refusal_reason=refusal_reason,
        )
