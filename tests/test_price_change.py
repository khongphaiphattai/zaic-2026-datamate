"""Unit tests for price_change.py deterministic engine (schema-driven, Rulebook §1)."""

from pathlib import Path
import duckdb
import pandas as pd

from zaic_2026_datamate.data_loader import BusinessContext
from zaic_2026_datamate.price_change import resolve_price_change_query


def _make_ctx(business_id: str, tables: dict[str, pd.DataFrame]) -> BusinessContext:
    conn = duckdb.connect()
    for name, df in tables.items():
        conn.register(name, df)
    return BusinessContext(
        business_id=business_id,
        data_dir=Path("."),
        tables=tables,
        duckdb_conn=conn,
    )


def test_price_change_synonym_and_unit_conversion_flat():
    """Verify synonym matching (Má lợn -> Má heo) and flat table sales comparison."""
    sales = pd.DataFrame(
        [
            {"ngay": "2026-04-01", "ma_don": "D1", "ten_hang": "Má heo nướng nước mắm", "dvt": "đĩa", "so_luong": 5, "trang_thai": "Hoàn thành"},
            {"ngay": "2026-04-10", "ma_don": "D2", "ten_hang": "Má heo nướng nước mắm", "dvt": "đĩa", "so_luong": 5, "trang_thai": "Hoàn thành"},
            {"ngay": "2026-04-15", "ma_don": "D3", "ten_hang": "Má heo nướng nước mắm", "dvt": "đĩa", "so_luong": 12, "trang_thai": "Hoàn thành"},
            {"ngay": "2026-04-20", "ma_don": "D4", "ten_hang": "Má heo nướng nước mắm", "dvt": "đĩa", "so_luong": 2, "trang_thai": "Đã hủy"},
        ]
    )
    ctx = _make_ctx("priv_pc_flat", {"so_chi_tiet_ban_hang": sales})
    q = (
        "Má lợn nướng nước mắm đổi giá từ 99.000đ lên 104.000đ vào ngày 15/04/2026. "
        "30 ngày kể từ ngày đó bán được nhiều hay ít hơn 30 ngày liền trước bao nhiêu %, tính theo đĩa?"
    )
    # Before [2026-03-16..2026-04-14]: 10; After [2026-04-15..2026-05-14]: 12 -> +20.0%
    res = resolve_price_change_query(q, ctx, "2026-07-22")
    assert res == ("percent", 20.0, None)


def test_price_change_relational_pos_whole_order_returns_rulebook_1():
    """Verify relational POS tables exclude whole-order returns per Rulebook §1 (-37.5% case)."""
    san_pham = pd.DataFrame(
        [{"ma_sp": "SP1", "ten_sp": "Cáp Type-C sang Lightning 1m", "don_vi": "cái"}]
    )
    don_hang = pd.DataFrame(
        [
            {"ma_hoa_don": "HD1", "ngay_lap": "2025-11-20", "trang_thai": "Hoàn thành"},
            {"ma_hoa_don": "HD2", "ngay_lap": "2025-11-25", "trang_thai": "Hoàn thành"},
            {"ma_hoa_don": "HD3", "ngay_lap": "2025-12-03", "trang_thai": "Hoàn thành"},
            # Whole order partially/fully returned on separate negative line -> exclude entire HD4
            {"ma_hoa_don": "HD4", "ngay_lap": "2025-12-06", "trang_thai": "Hoàn thành"},
            {"ma_hoa_don": "HD4", "ngay_lap": "2025-12-07", "trang_thai": "Hoàn trả"},
        ]
    )
    chi_tiet = pd.DataFrame(
        [
            {"ma_hoa_don": "HD1", "ma_sp": "SP1", "so_luong": 5},
            {"ma_hoa_don": "HD2", "ma_sp": "SP1", "so_luong": 3},
            {"ma_hoa_don": "HD3", "ma_sp": "SP1", "so_luong": 5},
            {"ma_hoa_don": "HD4", "ma_sp": "SP1", "so_luong": 4},
        ]
    )
    ctx = _make_ctx(
        "priv_pc_pos",
        {
            "san_pham": san_pham,
            "don_hang": don_hang,
            "chi_tiet_don": chi_tiet,
        },
    )
    q = (
        "Ngày 01/12/2025 Cáp Type-C sang Lightning 1m tăng giá từ 150.000đ lên 157.500đ. "
        "Số cái bán được trong 14 ngày kể từ ngày đó so với 14 ngày trước đó thay đổi bao nhiêu %?"
    )
    # Before: 5 + 3 = 8; After (excluding HD4 return): 5 -> (5 - 8) / 8 = -37.5%
    res = resolve_price_change_query(q, ctx, "2026-03-31")
    assert res == ("percent", -37.5, None)


def test_price_change_inline_topping_split_uses_topping_name_and_raw_name():
    """Verify inline topping extraction works via split_inline_toppings keys ('name'/'raw_name')."""
    df = pd.DataFrame(
        [
            {"ngay": "2025-10-10", "ten_hang": "Topping trân châu đen", "don_vi": "phần", "so_luong": 2, "don_gia": 5000, "thanh_tien": 10000, "trang_thai": "Hoàn thành"},
            {"ngay": "2025-10-11", "ten_hang": "Trà sữa (M) + Topping trân châu đen", "don_vi": "ly", "so_luong": 1, "don_gia": 35000, "thanh_tien": 35000, "trang_thai": "Hoàn thành"},
            {"ngay": "2025-10-20", "ten_hang": "Topping trân châu đen", "don_vi": "phần", "so_luong": 5, "don_gia": 8000, "thanh_tien": 40000, "trang_thai": "Hoàn thành"},
            {"ngay": "2025-10-21", "ten_hang": "Trà sữa (M) + Topping trân châu đen", "don_vi": "ly", "so_luong": 1, "don_gia": 38000, "thanh_tien": 38000, "trang_thai": "Hoàn thành"},
        ]
    )
    ctx = _make_ctx("priv_pc_top", {"ban_hang": df})
    q = (
        "Ngày 15/10/2025 Topping trân châu đen tăng giá lên 8.000đ. "
        "Số lượng bán được trong 10 ngày kể từ ngày đó so với 10 ngày trước đó thay đổi bao nhiêu %?"
    )
    # Before: 2 + 1 = 3; After: 5 + 1 = 6 -> +100.0%
    res = resolve_price_change_query(q, ctx, "2025-11-01")
    assert res == ("percent", 100.0, None)


def test_price_change_tied_product_candidates_abstain():
    """Verify exact product match score ties return None rather than guessing."""
    df = pd.DataFrame(
        [
            {"ngay": "2025-11-25", "ten_hang": "Bơ sữa", "don_vi": "hộp", "so_luong": 4, "don_gia": 10000, "thanh_tien": 40000},
            {"ngay": "2025-11-25", "ten_hang": "Sữa bơ", "don_vi": "hộp", "so_luong": 9, "don_gia": 10000, "thanh_tien": 90000},
            {"ngay": "2025-12-05", "ten_hang": "Bơ sữa", "don_vi": "hộp", "so_luong": 8, "don_gia": 12000, "thanh_tien": 96000},
            {"ngay": "2025-12-05", "ten_hang": "Sữa bơ", "don_vi": "hộp", "so_luong": 3, "don_gia": 12000, "thanh_tien": 36000},
        ]
    )
    ctx = _make_ctx("priv_pc_tie", {"ban_hang": df})
    q = (
        "Ngày 01/12/2025 bơ và sữa tăng giá từ 10.000đ lên 12.000đ. "
        "Số lượng bán được trong 10 ngày kể từ ngày đó so với 10 ngày trước đó thay đổi bao nhiêu %?"
    )
    assert resolve_price_change_query(q, ctx, "2025-12-30") is None
