"""
test_data_loader.py: Unit tests for DataLoader, multi-sheet concatenation,
split file merging, and DuckDB registration.
"""

from pathlib import Path
import pandas as pd
import pytest

from zaic_2026_datamate.data_loader import (
    DataLoader,
    clean_dataframe,
    get_base_table_stem,
    normalize_identifier,
)


def test_normalize_identifier():
    assert normalize_identifier("Mã nhập hàng") == "ma_nhap_hang"
    assert normalize_identifier("Cần trả NCC") == "can_tra_ncc"
    assert normalize_identifier("Nhóm hàng(3 Cấp)") == "nhom_hang_3_cap"
    assert normalize_identifier("DanhSachHoaDon.xlsx") == "danh_sach_hoa_don"
    assert normalize_identifier("DanhSachHoaDonChiTiet_1") == "danh_sach_hoa_don_chi_tiet_1"
    assert normalize_identifier("DonHang_ChiTiet_2026") == "don_hang_chi_tiet_2026"
    assert normalize_identifier("Đơn giá") == "don_gia"


def test_get_base_table_stem():
    assert get_base_table_stem("DanhSachHoaDonChiTiet_1") == "DanhSachHoaDonChiTiet"
    assert get_base_table_stem("DanhSachHoaDonChiTiet_2") == "DanhSachHoaDonChiTiet"
    assert get_base_table_stem("So_chi_tiet_ban_hang_2026") == "So_chi_tiet_ban_hang"
    assert get_base_table_stem("DonHang_ChiTiet_1") == "DonHang_ChiTiet"
    assert get_base_table_stem("BaoCaoTonKho") == "BaoCaoTonKho"


def test_clean_dataframe():
    raw_df = pd.DataFrame({
        "Mã hóa đơn": ["HD001", "HD002"],
        "Thời gian": ["01/04/2025", "15/04/2025"],
        "Cần trả NCC": ["227,262,100", "2,880,000"],
    })
    cleaned_df, mapping = clean_dataframe(raw_df)

    assert "ma_hoa_don" in cleaned_df.columns
    assert "thoi_gian" in cleaned_df.columns
    assert "can_tra_ncc" in cleaned_df.columns

    # Verify numeric conversion
    assert cleaned_df["can_tra_ncc"].iloc[0] == 227262100.0
    assert cleaned_df["can_tra_ncc"].iloc[1] == 2880000.0

    # Verify datetime conversion
    assert pd.api.types.is_datetime64_any_dtype(cleaned_df["thoi_gian"])


def test_load_business_biz_128():
    train_dir = Path("data/train/train/data")
    if not train_dir.exists():
        pytest.skip("Train data directory not available")

    loader = DataLoader("data/train/train/data", "data/train/train/calendar.csv")
    ctx = loader.load_business("biz_128")

    assert "calendar" in ctx.tables
    assert "danh_sach_hoa_don" in ctx.tables
    assert "danh_sach_nhap_hang" in ctx.tables

    # Verify multi-sheet concatenation across 18 monthly sheets
    assert len(ctx.tables["danh_sach_hoa_don"]) == 26505

    # Verify SQL execution on DuckDB
    res = ctx.scalar("""
        SELECT SUM(can_tra_ncc)
        FROM danh_sach_nhap_hang
        WHERE strftime(thoi_gian, '%Y-%m') = '2026-09'
          AND trang_thai = 'Đã nhập hàng'
    """)
    assert res == 89526400.0


def test_load_business_biz_106_split():
    train_dir = Path("data/train/train/data")
    if not train_dir.exists():
        pytest.skip("Train data directory not available")

    loader = DataLoader("data/train/train/data", "data/train/train/calendar.csv")
    ctx = loader.load_business("biz_106")

    assert "danh_sach_hoa_don_chi_tiet" in ctx.tables
    # Check that parts 1 and 2 were concatenated cleanly (deduplicated per Rulebook Sec 8: 97504 + 28682 = 126186)
    assert len(ctx.tables["danh_sach_hoa_don_chi_tiet"]) == 126186


def test_load_business_biz_123_sqlite():
    train_dir = Path("data/train/train/data")
    if not train_dir.exists():
        pytest.skip("Train data directory not available")

    loader = DataLoader("data/train/train/data", "data/train/train/calendar.csv")
    ctx = loader.load_business("biz_123")

    assert "khach_hang" in ctx.tables
    assert "don_hang" in ctx.tables
    assert len(ctx.tables["khach_hang"]) == 300
