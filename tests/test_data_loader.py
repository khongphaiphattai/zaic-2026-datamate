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


@pytest.mark.slow
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


@pytest.mark.slow
def test_load_business_biz_106_split():
    train_dir = Path("data/train/train/data")
    if not train_dir.exists():
        pytest.skip("Train data directory not available")

    loader = DataLoader("data/train/train/data", "data/train/train/calendar.csv")
    ctx = loader.load_business("biz_106")

    assert "danh_sach_hoa_don_chi_tiet" in ctx.tables
    # Check that parts 1 and 2 were concatenated cleanly (deduplicated per Rulebook Sec 8: 97504 + 28682 = 126186)
    assert len(ctx.tables["danh_sach_hoa_don_chi_tiet"]) == 126186


@pytest.mark.slow
def test_load_business_biz_123_sqlite():
    train_dir = Path("data/train/train/data")
    if not train_dir.exists():
        pytest.skip("Train data directory not available")

    loader = DataLoader("data/train/train/data", "data/train/train/calendar.csv")
    ctx = loader.load_business("biz_123")

    assert "khach_hang" in ctx.tables
    assert "don_hang" in ctx.tables
    assert len(ctx.tables["khach_hang"]) == 300


def test_header_fill_stays_within_order():
    raw = pd.DataFrame({
        'Mã đơn hàng': ['A', None, 'B', None, 'A'],
        'Khách hàng': ['Lan', None, None, None, None],
        'Trạng thái': ['Đã hủy', None, 'Hoàn thành', None, None],
        'Thành tiền': [10, 20, 30, 40, 50],
    })
    cleaned, _ = clean_dataframe(raw)
    assert cleaned['ma_don_hang'].tolist() == ['A', 'A', 'B', 'B', 'A']
    assert cleaned.loc[1, 'khach_hang'] == 'Lan'
    assert pd.isna(cleaned.loc[2, 'khach_hang'])
    assert pd.isna(cleaned.loc[3, 'khach_hang'])
    assert cleaned.loc[4, 'khach_hang'] == 'Lan'
    assert cleaned.loc[4, 'trang_thai'] == 'Đã hủy'


def test_missing_status_on_new_order_does_not_inherit_previous_order():
    raw = pd.DataFrame({
        'Mã đơn hàng': ['A', 'B', None],
        'Trạng thái': ['Đã hủy', None, None],
        'Thành tiền': [10, 20, 30],
    })
    cleaned, _ = clean_dataframe(raw)
    assert cleaned.loc[0, 'trang_thai'] == 'Đã hủy'
    assert cleaned.loc[1:, 'trang_thai'].isna().all()


def test_header_without_order_id_keeps_its_own_value():
    raw = pd.DataFrame({
        'Mã đơn hàng': [None, 'A'],
        'Khách hàng': ['Unknown order customer', None],
        'Thành tiền': [10, 20],
    })
    cleaned, _ = clean_dataframe(raw)
    assert cleaned.loc[0, 'khach_hang'] == 'Unknown order customer'
    assert pd.isna(cleaned.loc[1, 'khach_hang'])


def test_shared_day_across_orders_keeps_date_but_not_customer():
    raw = pd.DataFrame({
        'Mã đơn hàng': ['A', 'B'],
        'Ngày': ['2026-01-02', None],
        'Khách hàng': ['Lan', None],
        'Thành tiền': [10, 20],
    })
    cleaned, _ = clean_dataframe(raw)
    assert cleaned.loc[1, 'ngay'] == cleaned.loc[0, 'ngay']
    assert pd.isna(cleaned.loc[1, 'khach_hang'])


def test_noi_dung_product_column_is_not_coerced_to_currency_or_deduplicated():
    """Regression for biz_101: 'Nội dung' ('noi_dung') contains 'no' substring
    and must NOT be coerced to currency or cause false duplicate row removal.
    """
    raw = pd.DataFrame({
        "Mã hóa đơn": ["ORD001", "ORD001", "ORD002", "ORD002"],
        "Ngày tháng": ["2025-08-10", "2025-08-10", "2025-08-11", "2025-08-11"],
        "Nội dung": ["Pepsi 330ml", "Coca Cola 330ml", "Bánh mì thịt", "Trà đào cam sả"],
        "Số lượng": [1, 1, 1, 1],
        "Đơn giá": [10000, 10000, 20000, 20000],
        "Thành tiền": [10000, 10000, 20000, 20000],
    })
    cleaned, _ = clean_dataframe(raw)
    assert len(cleaned) == 4
    assert cleaned["noi_dung"].tolist() == [
        "Pepsi 330ml",
        "Coca Cola 330ml",
        "Bánh mì thịt",
        "Trà đào cam sả",
    ]
    assert cleaned["thanh_tien"].sum() == 60000


def test_priority1_order_grain_cancel_rate_and_line_revenue():
    """Regression for:
    1. Order-grain date filtering on cancel rate when return rows have next-month 'gio' (biz_105/biz_117/dev_004).
    2. Line-item revenue selection ('thanh_tien_d') over repeated order-header ('tien_hang_truoc_giam') on multi-item tables (dev_003).
    3. Dated sales table selection when undated detail table is also present (biz_109).
    """
    from pathlib import Path
    import duckdb
    from zaic_2026_datamate.data_loader import BusinessContext
    from zaic_2026_datamate.intent_parser import (
        find_primary_sales_table,
        find_revenue_column,
        resolve_query,
    )

    # 1. Cancel rate order-grain date test:
    # Order ORD_APR was placed in April (2026-04-28), with a return row in May (2026-05-03).
    # May has 2 orders placed in May (ORD_MAY1 cancelled, ORD_MAY2 completed).
    # True May cancel rate = 1 / 2 = 50.0% (NOT 1 / 3 = 33.33%).
    df_sales = pd.DataFrame({
        "ma_hoa_don": ["ORD_APR", "ORD_APR", "ORD_MAY1", "ORD_MAY2"],
        "gio": ["2026-04-28", "2026-05-03", "2026-05-10", "2026-05-12"],
        "status": ["Đổi trả", "Đổi trả", "Đã hủy", "Hoàn thành"],
        "tri_gia": [100000.0, -100000.0, 50000.0, 200000.0],
    })
    conn1 = duckdb.connect(":memory:")
    conn1.register("so_ban_hang", df_sales)
    ctx1 = BusinessContext(
        business_id="test_cancel",
        data_dir=Path("."),
        tables={"so_ban_hang": df_sales},
        duckdb_conn=conn1,
    )
    kind, val, _ = resolve_query(
        "Tháng 5/2026 cửa hàng có bao nhiêu % đơn bị huỷ?",
        ctx1,
        as_of_date="2026-06-01",
    )
    assert kind == "percent"
    assert val == 50.0

    # 2. Line-item vs order-header revenue column selection (dev_003 pattern):
    df_dev003 = pd.DataFrame({
        "don_so": ["D1", "D1", "D2"],
        "ngay_hd": ["2026-02-01", "2026-02-01", "2026-02-02"],
        "trang_thai_giao": ["Hoàn thành", "Hoàn thành", "Hoàn thành"],
        "dong": [1, 2, 1],
        "tien_hang_truoc_giam": [100000.0, 100000.0, 50000.0],
        "thanh_tien_d": [60000.0, 40000.0, 50000.0],
    })
    conn2 = duckdb.connect(":memory:")
    conn2.register("ban_hang_tong_hop", df_dev003)
    ctx2 = BusinessContext(
        business_id="test_dev003",
        data_dir=Path("."),
        tables={"ban_hang_tong_hop": df_dev003},
        duckdb_conn=conn2,
    )
    assert find_revenue_column(ctx2, "ban_hang_tong_hop") == "thanh_tien_d"

    # 3. Dated primary sales table selection over undated large detail table (biz_109 pattern):
    df_detail = pd.DataFrame({
        "ma_hoa_don": [f"HD{i}" for i in range(250)],
        "thanh_tien": [10000.0] * 250,
    })
    df_header = pd.DataFrame({
        "ma_hoa_don": ["HD1", "HD2"],
        "thoi_gian": ["2026-05-01", "2026-05-02"],
        "trang_thai": ["Hoàn thành", "Hoàn thành"],
        "tong_tien_hang": [100000.0, 200000.0],
    })
    conn3 = duckdb.connect(":memory:")
    conn3.register("danh_sach_hoa_don_chi_tiet", df_detail)
    conn3.register("du_lieu_ban_hang_danh_sach_hoa_don", df_header)
    ctx3 = BusinessContext(
        business_id="test_biz109",
        data_dir=Path("."),
        tables={
            "danh_sach_hoa_don_chi_tiet": df_detail,
            "du_lieu_ban_hang_danh_sach_hoa_don": df_header,
        },
        duckdb_conn=conn3,
    )
    t_sel, o_sel, d_sel, s_sel = find_primary_sales_table(ctx3)
    assert t_sel == "du_lieu_ban_hang_danh_sach_hoa_don"
    assert d_sel == "thoi_gian"


def test_so_phieu_and_phieu_thu_are_preserved_as_text_ids():
    """Regression for biz_129/biz_124: 'so_phieu' and 'phieu_thu' contain 'phi' substring
    and must NEVER be coerced to numeric currency via clean_vietnamese_currency.
    """
    raw = pd.DataFrame({
        "Số phiếu": ["ORD000001", "ORD000002", "ORD000003"],
        "Phiếu thu": ["PT2026-001", "PT2026-002", "PT2026-003"],
        "Số CT": ["CT001", "CT002", "CT003"],
        "Phí ship": ["15.000 đ", "20.000 đ", "0 đ"],
        "Thành tiền": ["100.000 đ", "250.000 đ", "180.000 đ"],
    })
    cleaned, _ = clean_dataframe(raw)
    assert cleaned["so_phieu"].tolist() == ["ORD000001", "ORD000002", "ORD000003"]
    assert cleaned["phieu_thu"].tolist() == ["PT2026-001", "PT2026-002", "PT2026-003"]
    assert cleaned["so_ct"].tolist() == ["CT001", "CT002", "CT003"]
    assert cleaned["phi_ship"].tolist() == [15000.0, 20000.0, 0.0]


def test_ma_giao_dich_order_deduplication():
    """Regression for biz_124.ban_hang_chi_tiet_hoa_don: 'ma_giao_dich' is an order ID column,
    so exact duplicate rows within the same order must be deduplicated once per Rulebook §8.
    """
    raw = pd.DataFrame({
        "Mã giao dịch": ["ORD045004", "ORD045004", "ORD045004"],
        "TT dòng": [1, 2, 2],
        "Mã hàng": ["SP0010", "SP0015", "SP0015"],
        "Số lượng": [1, 2, 2],
        "Thành tiền": [50000, 120000, 120000],
    })
    cleaned, _ = clean_dataframe(raw)
    assert len(cleaned) == 2
    assert cleaned["thanh_tien"].sum() == 170000


def test_drop_footer_notes_row():
    """Bug 2 regression: footer rows like 'Ghi chú: số liệu ghi tay' must be dropped."""
    raw = pd.DataFrame({
        "Mã đơn hàng": ["ORD01", "ORD02", "Ghi chú: số liệu ghi tay"],
        "Ngày": ["2026-01-01", "2026-01-02", None],
        "Thành tiền": [100000, 200000, None],
    })
    cleaned, _ = clean_dataframe(raw)
    assert len(cleaned) == 2
    assert cleaned["ma_don_hang"].tolist() == ["ORD01", "ORD02"]


def test_clean_dataframe_multisheet_years():
    """Bug 1 regression: sheets with 2025 and 2026 in sheet_name must parse yearless dates with their own year."""
    raw = pd.DataFrame({
        "sheet_name": ["T12-2025", "T12-2025", "T1-2026", "T1-2026"],
        "Mã đơn hàng": ["ORD01", "ORD02", "ORD03", "ORD04"],
        "Ngày": ["15/12", "20/12", "05/01", "10/01"],
        "Thành tiền": [100, 200, 300, 400],
    })
    cleaned, _ = clean_dataframe(raw)
    years = cleaned["ngay"].dt.year.tolist()
    assert years == [2025, 2025, 2026, 2026]


def test_clean_dataframe_stt_and_order_scoped_ffill():
    """Bug 3 regression: STT must act as order ID when no other order ID exists,
    and ghi_chu / kenh must be forward filled within the order.
    """
    raw = pd.DataFrame({
        "STT": [1.0, None, 2.0, None],
        "Ngày": ["2026-01-01", None, "2026-01-01", None],
        "Hàng": ["IP16", "Ốp lưng", "Samsung S24", "Cáp sạc"],
        "Ghi chú": ["Đổi trả", None, None, None],
        "Kênh": ["Zalo OA", None, "Tại tiệm", None],
        "Thành tiền": [20000000, 200000, 18000000, 150000],
    })
    cleaned, _ = clean_dataframe(raw)
    assert cleaned["stt"].tolist() == [1.0, 1.0, 2.0, 2.0]
    assert cleaned.loc[0, "ghi_chu"] == "Đổi trả"
    assert cleaned.loc[1, "ghi_chu"] == "Đổi trả"
    assert pd.isna(cleaned.loc[2, "ghi_chu"])
    assert pd.isna(cleaned.loc[3, "ghi_chu"])
    assert cleaned["kenh"].tolist() == ["Zalo OA", "Zalo OA", "Tại tiệm", "Tại tiệm"]



