"""
data_loader.py: Production Data Loader for ZAIC 2026 DataMate.

Handles multi-sheet Excel files, split year/part files (_1, _2, _2025, _2026),
CSV, SQLite databases, and calendar integration into high-performance in-memory DuckDB.
"""

from __future__ import annotations

import re
import sqlite3
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import duckdb
import pandas as pd


def normalize_identifier(text: str) -> str:
    """Convert arbitrary Vietnamese text / PascalCase / CamelCase to clean ASCII snake_case.

    Examples:
        'DanhSachHoaDon' -> 'danh_sach_hoa_don'
        'Mã nhập hàng' -> 'ma_nhap_hang'
        'Cần trả NCC' -> 'can_tra_ncc'
        'Nhóm hàng(3 Cấp)' -> 'nhom_hang_3_cap'
        'DanhSachHoaDon.xlsx' -> 'danh_sach_hoa_don'
    """
    stem = Path(text).stem if ("." in text and not text.startswith(".")) else text
    # Convert PascalCase / CamelCase to snake_case before accent stripping
    s = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", stem)
    s = re.sub(r"([a-z\d])([A-Z])", r"\1_\2", s)
    nfkd = unicodedata.normalize("NFKD", s)
    no_accent = "".join([c for c in nfkd if not unicodedata.combining(c)])
    no_accent = no_accent.replace("đ", "d").replace("Đ", "d")
    clean = re.sub(r"[^a-zA-Z0-9]+", "_", no_accent.strip().lower()).strip("_")
    return clean or "col"


def get_base_table_stem(stem: str) -> str:
    """Extract logical base table stem by stripping split suffixes.

    Examples:
        'DanhSachHoaDonChiTiet_1' -> 'DanhSachHoaDonChiTiet'
        'DanhSachHoaDonChiTiet_2' -> 'DanhSachHoaDonChiTiet'
        'SoBanHang_2026' -> 'SoBanHang'
        'BanHang_T10_2025' -> 'BanHang'
        'BanHang_Thang01_2025' -> 'BanHang'
    """
    s = re.sub(r"_(?:t|thang|tháng)\d{1,2}_\d{2,4}$", "", stem, flags=re.IGNORECASE)
    s = re.sub(r"_(?:[0-9]{1,2}|202[4-9])$", "", s)
    return s


def is_monthly_sheet(sheet_name: str) -> bool:
    """Detect if sheet name represents a monthly segment (e.g. '04.2025', '12/2026', 'Thang 02 2025', 'T01_2025', 'T02-2025')."""
    s = str(sheet_name).strip().lower()
    if re.match(r"^(?:thang|tháng|t)?\s*[0-9]{1,2}[./_\s-][0-9]{2,4}$", s):
        return True
    if re.match(r"^(?:thang|tháng|t)\s*[0-9]{1,2}$", s):
        return True
    return False


def _parse_single_date(val: Any, sheet_year: Optional[int] = None) -> Optional[str]:
    """Deterministically parse a date string or serial into ISO YYYY-MM-DD format."""
    if pd.isna(val):
        return None
    val_str = str(val).strip()
    if not val_str or val_str.lower() in ("nan", "nat", "none", "null"):
        return None

    # 0. Vietnamese text month format: '1 thg 5 2025', '15 tháng 04 2025', '1 thg 1 2026'
    m_thg = re.search(r"(\d{1,2})\s*(?:thg|tháng|thang)\s*(\d{1,2})\s*(\d{4})", val_str, re.IGNORECASE)
    if m_thg:
        d, m, y = int(m_thg.group(1)), int(m_thg.group(2)), int(m_thg.group(3))
        if 1990 <= y <= 2100 and 1 <= m <= 12 and 1 <= d <= 31:
            return f"{y:04d}-{m:02d}-{d:02d}"

    # Handle time if attached, take date part
    date_part = val_str.split(" ")[0].strip()

    # 1. Slash format: DD/MM/YYYY or DD/MM
    if "/" in date_part:
        parts = date_part.split("/")
        if len(parts) == 3:
            try:
                d, m, y = int(parts[0]), int(parts[1]), int(parts[2])
                if y < 100:
                    y += 2000
                if 1990 <= y <= 2100 and 1 <= m <= 12 and 1 <= d <= 31:
                    return f"{y:04d}-{m:02d}-{d:02d}"
            except (ValueError, TypeError):
                pass
        elif len(parts) == 2 and sheet_year:
            try:
                d, m = int(parts[0]), int(parts[1])
                if 1 <= m <= 12 and 1 <= d <= 31:
                    return f"{sheet_year:04d}-{m:02d}-{d:02d}"
            except (ValueError, TypeError):
                pass

    # 2. Hyphen / ISO format: YYYY-MM-DD or DD-MM-YYYY or DD-MM
    elif "-" in date_part:
        parts = date_part.split("-")
        if len(parts) == 3:
            try:
                if len(parts[0]) == 4:  # YYYY-MM-DD
                    y, m, d = int(parts[0]), int(parts[1]), int(parts[2])
                else:  # DD-MM-YYYY
                    d, m, y = int(parts[0]), int(parts[1]), int(parts[2])
                    if y < 100:
                        y += 2000
                if 1990 <= y <= 2100 and 1 <= m <= 12 and 1 <= d <= 31:
                    return f"{y:04d}-{m:02d}-{d:02d}"
            except (ValueError, TypeError):
                pass
        elif len(parts) == 2 and sheet_year:
            try:
                d, m = int(parts[0]), int(parts[1])
                if 1 <= m <= 12 and 1 <= d <= 31:
                    return f"{sheet_year:04d}-{m:02d}-{d:02d}"
            except (ValueError, TypeError):
                pass

    return None


def parse_mixed_excel_dates(s: pd.Series, sheet_year: Optional[int] = None) -> pd.Series:
    """Parse a date Series that may contain mixed Excel serial numbers and string dates."""
    # 1. Handle numeric / Excel serial dates (e.g. 45809 = 2025-06-01)
    num_s = pd.to_numeric(s, errors="coerce")
    is_serial = (num_s >= 35000) & (num_s <= 65000)
    serials_converted = pd.to_datetime(num_s[is_serial], unit="D", origin="1899-12-30", errors="coerce")

    # 2. Convert remaining string dates deterministically
    str_s = s[~is_serial]
    parsed_iso = str_s.apply(lambda v: _parse_single_date(v, sheet_year=sheet_year))
    strs_converted = pd.to_datetime(parsed_iso, format="%Y-%m-%d", errors="coerce")

    # Filter out wild/invalid years (< 1990 or > 2100) to prevent OutOfBoundsDatetime
    serials_converted = serials_converted.where((serials_converted.dt.year >= 1990) & (serials_converted.dt.year <= 2100))
    strs_converted = strs_converted.where((strs_converted.dt.year >= 1990) & (strs_converted.dt.year <= 2100))

    result = pd.Series(pd.NaT, index=s.index, dtype="datetime64[us]")
    result.update(serials_converted)
    result.update(strs_converted)
    return result


SUBTOTAL_ROW_PATTERN = re.compile(
    r"^(?:cộng|cong|tổng|tong)\s*(?:ngày|ngay|tháng|thang|quý|quy|năm|nam|\d{1,2}[/-]\d{1,2}|cộng|cong|$|\s*:)",
    re.IGNORECASE,
)


def drop_subtotal_rows(df: pd.DataFrame) -> pd.DataFrame:
    """Drop accounting/POS daily subtotal and summary rows embedded in detail tables.

    Examples:
        'Cộng ngày 01/01/2025' (dev_001)
        'TỔNG NGÀY 01/10' (dev_002, dev_007)
        'Tong cong' (dev_004)
        'Tổng ngày 01/01' (dev_008, dev_009)
        'tổng 01/05' (dev_010)
        'TỔNG NGÀY 01/04' (biz_130)
    """
    if df.empty:
        return df

    drop_mask = pd.Series(False, index=df.index)
    for col in df.columns:
        if not pd.api.types.is_numeric_dtype(df[col]) and not pd.api.types.is_datetime64_any_dtype(df[col]):
            s_str = df[col].astype(str).str.strip()
            matches = s_str.str.contains(SUBTOTAL_ROW_PATTERN)
            if matches.sum() > 0 and matches.sum() < len(df) * 0.5:
                drop_mask = drop_mask | matches

    if drop_mask.any():
        return df[~drop_mask].copy()
    return df


def clean_dataframe(df: pd.DataFrame) -> Tuple[pd.DataFrame, Dict[str, str]]:
    """Clean column names to ASCII snake_case while preserving original column mapping.

    Also normalizes numeric strings and date formats.
    """
    if df.empty:
        return df.copy(), {}

    orig_cols = df.columns.tolist()
    col_mapping: Dict[str, str] = {}
    clean_cols: List[str] = []
    seen_cols: set[str] = set()

    for col in orig_cols:
        clean = normalize_identifier(str(col))
        # Deduplicate column names
        base_clean = clean
        counter = 1
        while clean in seen_cols:
            clean = f"{base_clean}_{counter}"
            counter += 1
        seen_cols.add(clean)
        clean_cols.append(clean)
        col_mapping[str(col)] = clean

    cleaned_df = df.copy()
    cleaned_df.columns = clean_cols

    # Drop accounting/POS subtotal rows before data type normalization and ffill
    cleaned_df = drop_subtotal_rows(cleaned_df)

    # Identify sheet year if available from _sheet_name or table name
    sheet_year = None
    if "_sheet_name" in cleaned_df.columns:
        first_sheet = str(cleaned_df["_sheet_name"].dropna().iloc[0]) if cleaned_df["_sheet_name"].notna().any() else ""
        m_yr = re.search(r"202[4-9]", first_sheet)
        if m_yr:
            sheet_year = int(m_yr.group(0))

    # Normalize data types
    for orig_col, clean_col in col_mapping.items():
        s = cleaned_df[clean_col]
        if s.dropna().empty:
            continue

        # 1. Date Detection (checked first so date columns like "thoi_gian" are never misclassified)
        is_date_col = any(k in clean_col for k in [
            "thoi_gian", "ngay", "date", "time", "gio",
            "han_dung", "han_su_dung", "het_han"
        ]) or bool(re.search(r"\bhan\b", clean_col)) or pd.api.types.is_datetime64_any_dtype(s)

        if is_date_col:
            if not pd.api.types.is_datetime64_any_dtype(s):
                cleaned_df[clean_col] = parse_mixed_excel_dates(s, sheet_year=sheet_year)
            continue

        # 2. Text exclusion: if column name suggests name/text/ID, never force convert to numeric
        is_text_name_col = any(k in clean_col for k in [
            "ten", "mon", "mo_ta", "ma", "ghi_chu", "loai", "kenh",
            "trang_thai", "khach", "nhan_vien", "don_vi", "dvt",
            "hoat_chat", "thuong_hieu"
        ])
        if is_text_name_col:
            continue

        # 3. Numeric Detection
        is_num_col = any(k in clean_col for k in [
            "tien", "gia", "don_gia", "luong", "no",
            "tong", "thue", "giam", "stt", "ton",
            "amount", "sl", "qty", "so_luong", "tt", "dg"
        ])
        if not pd.api.types.is_numeric_dtype(s):
            is_money_col = any(k in clean_col for k in [
                "tien", "gia", "amount", "no", "tong", "thue", "giam", "phi", "tt", "dg"
            ])
            if is_money_col:
                from zaic_2026_datamate.cleaner import clean_vietnamese_currency
                converted = s.apply(clean_vietnamese_currency)
            else:
                clean_str = s.astype(str).str.strip().str.replace("đ", "", case=False).str.replace("vnd", "", case=False).str.replace(",", "")
                converted = pd.to_numeric(clean_str, errors="coerce")
                # If low ratio and column name implies quantity/price/amount, extract leading number from strings like "1 ly", "10 bao", "3.5 kg"
                if is_num_col and (converted.notna().sum() / len(s.dropna()) if len(s.dropna()) > 0 else 0) < 0.5:
                    extracted = clean_str.str.extract(r"^([-+]?\d*\.?\d+)", expand=False)
                    converted = pd.to_numeric(extracted, errors="coerce")

            valid_ratio = converted.notna().sum() / len(s.dropna()) if len(s.dropna()) > 0 else 0
            if (is_num_col and valid_ratio >= 0.5) or valid_ratio >= 0.8:
                cleaned_df[clean_col] = converted

    # Filter out repeated header rows (e.g. where ma_don_hang == 'Mã đơn hàng' or trang_thai == 'Trạng thái đơn hàng')
    for c in ["ma_don_hang", "ma_hoa_don", "so_ct", "trang_thai_don_hang", "trang_thai"]:
        if c in cleaned_df.columns:
            cleaned_df = cleaned_df[~cleaned_df[c].astype(str).str.lower().str.contains(r"^mã\s+đơn|^trạng\s+thái", regex=True, na=False)]

    # Rulebook Section 8: Forward fill header fields for multi-item order groups FIRST
    # ("Ô để trống ở các dòng sau của cùng một nhóm dòng: giống ô đó ở dòng đầu nhóm: khách, ghi chú, trạng thái của đơn áp cho mọi dòng của đơn.")
    order_header_cols = [
        "ma_don_hang", "ma_hoa_don", "ma_phieu", "ma_bill", "bill", "so_phieu", "ma_gd", "so_hd", "ma_dh", "so_ct", "hd", "don_so", "ma_don",
        "ngay_dat_hang", "thoi_gian", "ngay", "ngay_ban", "ngay_lap", "gio", "thoi_gian_tao",
        "trang_thai_don_hang", "trang_thai", "status", "ghi_chu_tt", "trang_thai_xu_ly",
        "tien_do", "xu_ly",
        "ten_khach_hang", "khach_hang", "khach", "tenkh", "ma_kh", "ma_khach_hang"
    ]
    for c in order_header_cols:
        if c in cleaned_df.columns:
            cleaned_df[c] = cleaned_df[c].ffill()

    # Rulebook Section 8:
    # "Hai dòng của cùng một đơn giống nhau ở mọi ô: lỗi xuất file, tính một lần.
    #  Hai dòng giống nhau của hai đơn khác nhau là hai lần bán, tính cả hai."
    # "Bảng không ghi theo đơn: mỗi dòng là một lần ghi; hai dòng giống nhau trong cùng file vẫn tính cả hai."
    order_id_cols = [
        "ma_don_hang", "ma_hoa_don", "ma_phieu", "ma_bill", "bill", "so_phieu", "ma_gd", "so_hd", "ma_dh", "so_ct", "hd", "don_so", "ma_don"
    ]
    has_order_col = any(c in cleaned_df.columns for c in order_id_cols)
    if has_order_col:
        cleaned_df = cleaned_df.drop_duplicates().reset_index(drop=True)

    return cleaned_df, col_mapping


@dataclass
class BusinessContext:
    """Encapsulates all datasets, metadata, and DuckDB sandbox for a single business."""

    business_id: str
    data_dir: Path
    tables: Dict[str, pd.DataFrame] = field(default_factory=dict)
    table_metadata: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    readme: str = ""
    duckdb_conn: duckdb.DuckDBPyConnection = field(default_factory=duckdb.connect)
    last_sql: Optional[str] = None

    def query(self, sql: str) -> pd.DataFrame:
        """Execute SQL query directly against the in-memory DuckDB connection."""
        self.last_sql = sql.strip()
        return self.duckdb_conn.execute(sql).fetchdf()

    def scalar(self, sql: str) -> Any:
        """Execute SQL query and return a single scalar value."""
        self.last_sql = sql.strip()
        res = self.duckdb_conn.execute(sql).fetchone()
        return res[0] if res is not None else None


class DataLoader:
    """Production DataLoader for Zalo AI Challenge 2026.

    Loads and registers multi-sheet Excel files, split tables, CSVs, SQLite,
    and calendar data into in-memory DuckDB views.
    """

    def __init__(self, data_dir: str | Path, calendar_path: Optional[str | Path] = None) -> None:
        self.data_dir = Path(data_dir)
        self.calendar_path = Path(calendar_path) if calendar_path else self._find_calendar()
        self.calendar_df: Optional[pd.DataFrame] = None
        if self.calendar_path and self.calendar_path.is_file():
            self._load_calendar()

    def _find_calendar(self) -> Optional[Path]:
        """Auto-locate calendar.csv in parent or current data directories."""
        candidates = [
            self.data_dir / "calendar.csv",
            self.data_dir.parent / "calendar.csv",
            self.data_dir.parent.parent / "calendar.csv",
            Path("data/train/train/calendar.csv"),
            Path("data/public_test/public_test/calendar.csv"),
        ]
        for c in candidates:
            if c.is_file():
                return c
        return None

    def _load_calendar(self) -> None:
        """Load and clean calendar.csv."""
        if not self.calendar_path or not self.calendar_path.is_file():
            return
        df = pd.read_csv(self.calendar_path)
        if "date" in df.columns:
            df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.strftime("%Y-%m-%d")
        self.calendar_df = df

    def load_business(self, business_id: str) -> BusinessContext:
        """Load all files for a specific business ID and register them in DuckDB."""
        biz_dir = self.data_dir / business_id if not (self.data_dir.name == business_id) else self.data_dir
        if not biz_dir.is_dir():
            raise FileNotFoundError(f"Business directory not found: {biz_dir}")

        context = BusinessContext(
            business_id=business_id,
            data_dir=biz_dir,
            duckdb_conn=duckdb.connect(":memory:"),
        )

        # 1. Load README.md if available
        readme_path = biz_dir / "README.md"
        if readme_path.is_file():
            try:
                context.readme = readme_path.read_text(encoding="utf-8")
            except Exception:
                context.readme = readme_path.read_text(encoding="latin1")

        # 2. Register Calendar in DuckDB
        if self.calendar_df is not None:
            context.duckdb_conn.register("calendar", self.calendar_df)
            context.tables["calendar"] = self.calendar_df

        # 3. Discover and group files (handle split files _1, _2, _2025, _2026)
        raw_tables: Dict[str, List[pd.DataFrame]] = {}
        split_tables: Dict[str, pd.DataFrame] = {}

        # 3.1. Process SQLite databases
        for sqlite_file in biz_dir.glob("*.sqlite"):
            self._load_sqlite(sqlite_file, raw_tables)

        # 3.2. Process CSV files
        for csv_file in biz_dir.glob("*.csv"):
            if csv_file.name == "calendar.csv":
                continue
            self._load_csv(csv_file, raw_tables, split_tables)

        # 3.3. Process Excel files (.xlsx)
        for xlsx_file in sorted(biz_dir.glob("*.xlsx")):
            self._load_excel(xlsx_file, raw_tables, split_tables)

        # 4. Clean, concatenate, and register all tables into DuckDB
        for base_name, df_list in raw_tables.items():
            if not df_list:
                continue
            # Concatenate split parts or multi-sheets
            merged_df = pd.concat(df_list, ignore_index=True) if len(df_list) > 1 else df_list[0]
            cleaned_df, col_mapping = clean_dataframe(merged_df)

            norm_name = normalize_identifier(base_name)
            context.tables[norm_name] = cleaned_df
            context.table_metadata[norm_name] = {
                "original_name": base_name,
                "columns": cleaned_df.columns.tolist(),
                "col_mapping": col_mapping,
                "rows": len(cleaned_df),
            }

            # Register in DuckDB with multiple aliases for robust SQL resolution
            context.duckdb_conn.register(norm_name, cleaned_df)
            if base_name != norm_name:
                context.duckdb_conn.register(base_name, cleaned_df)

        # Also register split individual tables if needed
        for split_name, df in split_tables.items():
            norm_split = normalize_identifier(split_name)
            if norm_split not in context.tables:
                cleaned_split, _ = clean_dataframe(df)
                context.tables[norm_split] = cleaned_split
                context.duckdb_conn.register(norm_split, cleaned_split)
                if split_name != norm_split:
                    context.duckdb_conn.register(split_name, cleaned_split)

        return context

    def _read_sheet_with_sniff(self, xls: pd.ExcelFile, sheet_name: str) -> pd.DataFrame:
        """Read a single sheet, auto-sniffing header if row 0 has NaNs."""
        try:
            raw_df = pd.read_excel(xls, sheet_name=sheet_name, header=None)
            if raw_df.empty:
                return raw_df
            row0_nans = raw_df.iloc[0].isna().sum()
            if row0_nans / raw_df.shape[1] >= 0.3:
                from zaic_2026_datamate.cleaner import sniff_and_clean_dataframe
                return sniff_and_clean_dataframe(raw_df)
            else:
                df = raw_df.iloc[1:].copy()
                df.columns = raw_df.iloc[0].tolist()
                return df.dropna(how="all").reset_index(drop=True)
        except Exception:
            return pd.read_excel(xls, sheet_name=sheet_name)

    def _load_excel(
        self,
        xlsx_path: Path,
        raw_tables: Dict[str, List[pd.DataFrame]],
        split_tables: Dict[str, pd.DataFrame],
    ) -> None:
        """Load an Excel workbook, concatenating monthly sheets or split files."""
        stem = xlsx_path.stem
        base_stem = get_base_table_stem(stem)

        try:
            xls = pd.ExcelFile(xlsx_path, engine="calamine")
            sheet_names = xls.sheet_names

            monthly_sheets = [s for s in sheet_names if is_monthly_sheet(s)]
            non_monthly_sheets = [s for s in sheet_names if not is_monthly_sheet(s)]

            if len(monthly_sheets) >= 2:
                sheet_dfs = []
                for s in monthly_sheets:
                    sdf = self._read_sheet_with_sniff(xls, s)
                    if not sdf.empty:
                        sdf["_sheet_name"] = s
                        sheet_dfs.append(sdf)
                if sheet_dfs:
                    combined = pd.concat(sheet_dfs, ignore_index=True)
                    raw_tables.setdefault(base_stem, []).append(combined)
                    split_tables[stem] = combined
                for s in non_monthly_sheets:
                    sdf = self._read_sheet_with_sniff(xls, s)
                    table_name = f"{base_stem}_{s}"
                    raw_tables.setdefault(table_name, []).append(sdf)
                    split_tables[table_name] = sdf
            elif len(sheet_names) == 1:
                s = sheet_names[0]
                sdf = self._read_sheet_with_sniff(xls, s)
                raw_tables.setdefault(base_stem, []).append(sdf)
                table_name = f"{base_stem}_{s}"
                if table_name != base_stem:
                    raw_tables.setdefault(table_name, []).append(sdf)
                split_tables[stem] = sdf
            else:
                for s in sheet_names:
                    sdf = self._read_sheet_with_sniff(xls, s)
                    table_name = f"{base_stem}_{s}"
                    raw_tables.setdefault(table_name, []).append(sdf)
                    # If this is the primary sales/invoice sheet, also register under base_stem
                    is_primary = (
                        s.lower() in ["banhang", "ban_hang", "sobanhang", "so_ban_hang", "hoadon", "hoa_don"]
                        or s == sheet_names[0]
                    )
                    if is_primary:
                        raw_tables.setdefault(base_stem, []).append(sdf)
                    split_tables[table_name] = sdf
        except Exception:
            xls = pd.ExcelFile(xlsx_path, engine="openpyxl")
            for s in xls.sheet_names:
                sdf = self._read_sheet_with_sniff(xls, s)
                raw_tables.setdefault(base_stem, []).append(sdf)
                split_tables[stem] = sdf

    def _load_csv(
        self,
        csv_path: Path,
        raw_tables: Dict[str, List[pd.DataFrame]],
        split_tables: Dict[str, pd.DataFrame],
    ) -> None:
        """Load a CSV file."""
        stem = csv_path.stem
        base_stem = get_base_table_stem(stem)
        try:
            df = pd.read_csv(csv_path, low_memory=False)
            raw_tables.setdefault(base_stem, []).append(df)
            split_tables[stem] = df
        except Exception:
            df = pd.read_csv(csv_path, encoding="latin1", low_memory=False)
            raw_tables.setdefault(base_stem, []).append(df)
            split_tables[stem] = df

    def _load_sqlite(
        self,
        sqlite_path: Path,
        raw_tables: Dict[str, List[pd.DataFrame]],
    ) -> None:
        """Read all tables from a SQLite database."""
        conn = sqlite3.connect(sqlite_path)
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = [row[0] for row in cursor.fetchall()]

        for table in tables:
            try:
                df = pd.read_sql_query(f"SELECT * FROM \"{table}\"", conn)
                raw_tables.setdefault(table, []).append(df)
            except Exception:
                continue
        conn.close()
