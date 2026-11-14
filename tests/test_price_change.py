"""Unit tests for price_change.py deterministic engine (schema-driven, Rulebook §1)."""

from pathlib import Path
import sys

import duckdb
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from zaic_2026_datamate.data_loader import BusinessContext, DataLoader
from zaic_2026_datamate.intent_parser import resolve_query
from zaic_2026_datamate.price_change import (
    resolve_price_change_query,
    resolve_unit_price_on_date,
)

TRAIN_DATA_DIR = (
    Path(__file__).resolve().parents[1] / "data" / "train" / "train" / "data"
)


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
            {
                "ngay": "2026-04-01",
                "ma_don": "D1",
                "ten_hang": "Má heo nướng nước mắm",
                "dvt": "đĩa",
                "so_luong": 5,
                "trang_thai": "Hoàn thành",
            },
            {
                "ngay": "2026-04-10",
                "ma_don": "D2",
                "ten_hang": "Má heo nướng nước mắm",
                "dvt": "đĩa",
                "so_luong": 5,
                "trang_thai": "Hoàn thành",
            },
            {
                "ngay": "2026-04-15",
                "ma_don": "D3",
                "ten_hang": "Má heo nướng nước mắm",
                "dvt": "đĩa",
                "so_luong": 12,
                "trang_thai": "Hoàn thành",
            },
            {
                "ngay": "2026-04-20",
                "ma_don": "D4",
                "ten_hang": "Má heo nướng nước mắm",
                "dvt": "đĩa",
                "so_luong": 2,
                "trang_thai": "Đã hủy",
            },
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
            {
                "ngay": "2025-10-10",
                "ten_hang": "Topping trân châu đen",
                "don_vi": "phần",
                "so_luong": 2,
                "don_gia": 5000,
                "thanh_tien": 10000,
                "trang_thai": "Hoàn thành",
            },
            {
                "ngay": "2025-10-11",
                "ten_hang": "Trà sữa (M) + Topping trân châu đen",
                "don_vi": "ly",
                "so_luong": 1,
                "don_gia": 35000,
                "thanh_tien": 35000,
                "trang_thai": "Hoàn thành",
            },
            {
                "ngay": "2025-10-20",
                "ten_hang": "Topping trân châu đen",
                "don_vi": "phần",
                "so_luong": 5,
                "don_gia": 8000,
                "thanh_tien": 40000,
                "trang_thai": "Hoàn thành",
            },
            {
                "ngay": "2025-10-21",
                "ten_hang": "Trà sữa (M) + Topping trân châu đen",
                "don_vi": "ly",
                "so_luong": 1,
                "don_gia": 38000,
                "thanh_tien": 38000,
                "trang_thai": "Hoàn thành",
            },
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
            {
                "ngay": "2025-11-25",
                "ten_hang": "Bơ sữa",
                "don_vi": "hộp",
                "so_luong": 4,
                "don_gia": 10000,
                "thanh_tien": 40000,
            },
            {
                "ngay": "2025-11-25",
                "ten_hang": "Sữa bơ",
                "don_vi": "hộp",
                "so_luong": 9,
                "don_gia": 10000,
                "thanh_tien": 90000,
            },
            {
                "ngay": "2025-12-05",
                "ten_hang": "Bơ sữa",
                "don_vi": "hộp",
                "so_luong": 8,
                "don_gia": 12000,
                "thanh_tien": 96000,
            },
            {
                "ngay": "2025-12-05",
                "ten_hang": "Sữa bơ",
                "don_vi": "hộp",
                "so_luong": 3,
                "don_gia": 12000,
                "thanh_tien": 36000,
            },
        ]
    )
    ctx = _make_ctx("priv_pc_tie", {"ban_hang": df})
    q = (
        "Ngày 01/12/2025 bơ và sữa tăng giá từ 10.000đ lên 12.000đ. "
        "Số lượng bán được trong 10 ngày kể từ ngày đó so với 10 ngày trước đó thay đổi bao nhiêu %?"
    )
    assert resolve_price_change_query(q, ctx, "2025-12-30") is None


def test_unit_price_on_date_prefixed_telex_and_ambiguity_guards():
    """P1.9: resolve exact-date unit selling price via canonical_order_lines with strict uniqueness & unit guards."""
    ctx = _make_ctx(
        "priv_unit_price_pos",
        {
            "ban_hang_hang_hoa": pd.DataFrame(
                [
                    {
                        "ma_sp": "M1",
                        "mon": "Dầu gió xanh con ó chai 12ml",
                        "don_vi": "Chai",
                        "gia": 52000,
                    },
                    {
                        "ma_sp": "M2",
                        "mon": "Siro Ích Nhi 100ml",
                        "don_vi": "Chai",
                        "gia": 65000,
                    },
                    {
                        "ma_sp": "M3",
                        "mon": "Acemuc 200mg hộp 30 gói",
                        "don_vi": "Gói",
                        "gia": 3000,
                    },
                ]
            ),
            "ban_hang_don_vi_quy_doi": pd.DataFrame(
                [{"ma_sp": "M3", "don_vi": "Hộp", "bang": 30}]
            ),
            "ban_hang_hoa_don": pd.DataFrame(
                [
                    {
                        "order_id": "O1",
                        "ngay_ban": "2026-01-16",
                        "trang_thai": "Hoàn thành",
                    },
                    {
                        "order_id": "O2",
                        "ngay_ban": "2026-01-16",
                        "trang_thai": "Hoàn thành",
                    },
                    {
                        "order_id": "O3",
                        "ngay_ban": "2026-01-16",
                        "trang_thai": "Đã hủy",
                    },
                    {
                        "order_id": "O4",
                        "ngay_ban": "2026-01-17",
                        "trang_thai": "Hoàn thành",
                    },
                    {
                        "order_id": "O5",
                        "ngay_ban": "2026-01-17",
                        "trang_thai": "Hoàn thành",
                    },
                ]
            ),
            "ban_hang_chi_tiet_hoa_don": pd.DataFrame(
                [
                    {
                        "ma_giao_dich": "O1",
                        "ma_hang": "M1",
                        "d": "Chai",
                        "slg": 2,
                        "sl_co_ban": 2,
                        "thanh_tien": 98000,
                    },
                    {
                        "ma_giao_dich": "O2",
                        "ma_hang": "M1",
                        "d": "Chai",
                        "slg": 1,
                        "sl_co_ban": 1,
                        "thanh_tien": 49000,
                    },
                    # Cancelled order with stale/higher price must be ignored
                    {
                        "ma_giao_dich": "O3",
                        "ma_hang": "M1",
                        "d": "Chai",
                        "slg": 1,
                        "sl_co_ban": 1,
                        "thanh_tien": 60000,
                    },
                    # Two conflicting valid prices for M2 on 2026-01-17 -> must abstain
                    {
                        "ma_giao_dich": "O4",
                        "ma_hang": "M2",
                        "d": "Chai",
                        "slg": 1,
                        "sl_co_ban": 1,
                        "thanh_tien": 65000,
                    },
                    {
                        "ma_giao_dich": "O5",
                        "ma_hang": "M2",
                        "d": "Chai",
                        "slg": 3,
                        "sl_co_ban": 3,
                        "thanh_tien": 185000,
                    },
                    # M3 sold in converted Hộp (30 gói) on 2026-01-16 at 90k/hộp = 3k/gói
                    {
                        "ma_giao_dich": "O1",
                        "ma_hang": "M3",
                        "d": "Hộp",
                        "slg": 1,
                        "sl_co_ban": 30,
                        "thanh_tien": 90000,
                    },
                ]
            ),
        },
    )
    # Telex + valid orders -> 49000 (not catalog 52000 or cancelled 60000)
    assert resolve_query(
        "check giùm giá bán mootj chai dầu gió xanh con ó chai 12ml vaof ngày 16/01/2026 là bnhiu",
        ctx,
        as_of_date="2026-06-30",
    )[:2] == ("number", 49000)

    # Converted unit: hỏi theo gói -> 3000; hỏi theo hộp -> 90000
    assert resolve_unit_price_on_date(
        "Ngày 16/01/2026 cửa hàng bán Acemuc 200mg hộp 30 gói với đơn giá bao nhiêu một gói?",
        ctx,
    )[:2] == ("number", 3000)
    assert resolve_unit_price_on_date(
        "Ngày 16/01/2026 cửa hàng bán Acemuc 200mg hộp 30 gói với đơn giá bao nhiêu một hộp?",
        ctx,
    )[:2] == ("number", 90000)

    # Conflicting discounted/tiered prices on same date -> abstain (None)
    assert (
        resolve_unit_price_on_date(
            "Ngày 17/01/2026 cửa hàng bán Siro Ích Nhi 100ml giá bao nhiêu một chai?",
            ctx,
        )
        is None
    )

    # Date with zero valid sales -> abstain (None)
    assert (
        resolve_unit_price_on_date(
            "Ngày 20/01/2026 cửa hàng bán Dầu gió xanh con ó chai 12ml giá bao nhiêu một chai?",
            ctx,
        )
        is None
    )

    # Incompatible asked unit -> abstain (None)
    assert (
        resolve_unit_price_on_date(
            "Ngày 16/01/2026 cửa hàng bán Dầu gió xanh con ó chai 12ml giá bao nhiêu một thùng?",
            ctx,
        )
        is None
    )

    # Guardrails: must NOT intercept avg/purchase/revenue/price-change questions
    for q in [
        "Ngày 16/01/2026 tổng doanh thu bán Dầu gió xanh con ó chai 12ml là bao nhiêu?",
        "Tháng 1/2026 giá nhập bình quân một chai Dầu gió xanh con ó chai 12ml là bao nhiêu?",
        "Từ ngày 16/01/2026 Dầu gió tăng giá lên 52.000đ, so 7 ngày với 7 ngày trước thay đổi bao nhiêu %?",
    ]:
        assert resolve_unit_price_on_date(q, ctx) is None


def test_canonical_price_change_prefixed_14_days_and_price_history_lookup():
    """P1.9: handle ban_hang_lich_su_gia, 14-day comparisons, and prefixed POS tables."""
    ctx = _make_ctx(
        "priv_pc_prefixed",
        {
            "ban_hang_hang_hoa": pd.DataFrame(
                [
                    {
                        "ma_sp": "A1",
                        "mon": "Acemuc Kids 200mg hộp 30 gói",
                        "don_vi": "Gói",
                        "gia": 3200,
                    },
                    {
                        "ma_sp": "P1",
                        "mon": "Prospan siro ho chai 100ml",
                        "don_vi": "Chai",
                        "gia": 97500,
                    },
                ]
            ),
            "ban_hang_don_vi_quy_doi": pd.DataFrame(
                [{"ma_sp": "A1", "don_vi": "Hộp", "bang": 30}]
            ),
            "ban_hang_lich_su_gia": pd.DataFrame(
                [
                    {
                        "ma_san_pham": "A1",
                        "san_pham": "Acemuc Kids 200mg hộp 30 gói",
                        "ngay_ap_dung": "2025-07-09",
                        "gia_cu": 3000,
                        "gia_moi": 3200,
                    },
                    {
                        "ma_san_pham": "P1",
                        "san_pham": "Prospan siro ho chai 100ml",
                        "ngay_ap_dung": "2026-02-05",
                        "gia_cu": 92000,
                        "gia_moi": 97500,
                    },
                ]
            ),
            "ban_hang_hoa_don": pd.DataFrame(
                [
                    {
                        "order_id": "H1",
                        "ngay_ban": "2025-07-05",
                        "trang_thai": "Hoàn thành",
                    },
                    {
                        "order_id": "H2",
                        "ngay_ban": "2025-07-10",
                        "trang_thai": "Hoàn thành",
                    },
                    {
                        "order_id": "H3",
                        "ngay_ban": "2025-07-12",
                        "trang_thai": "Đã hủy",
                    },
                    {
                        "order_id": "H4",
                        "ngay_ban": "2026-01-25",
                        "trang_thai": "Hoàn thành",
                    },
                    {
                        "order_id": "H5",
                        "ngay_ban": "2026-02-08",
                        "trang_thai": "Hoàn thành",
                    },
                ]
            ),
            "ban_hang_chi_tiet_hoa_don": pd.DataFrame(
                [
                    # 7-day before [2025-07-02..07-08]: 1 hộp = 30 gói + 10 gói = 40 gói
                    {
                        "ma_giao_dich": "H1",
                        "ma_hang": "A1",
                        "d": "Hộp",
                        "slg": 1,
                        "sl_co_ban": 30,
                        "thanh_tien": 90000,
                    },
                    {
                        "ma_giao_dich": "H1",
                        "ma_hang": "A1",
                        "d": "Gói",
                        "slg": 10,
                        "sl_co_ban": 10,
                        "thanh_tien": 30000,
                    },
                    # 7-day after [2025-07-09..07-15]: 2 hộp = 60 gói (excludes cancelled H3) -> +50.0%
                    {
                        "ma_giao_dich": "H2",
                        "ma_hang": "A1",
                        "d": "Hộp",
                        "slg": 2,
                        "sl_co_ban": 60,
                        "thanh_tien": 192000,
                    },
                    {
                        "ma_giao_dich": "H3",
                        "ma_hang": "A1",
                        "d": "Gói",
                        "slg": 100,
                        "sl_co_ban": 100,
                        "thanh_tien": 320000,
                    },
                    # 14-day before [2026-01-22..02-04]: 8; after [2026-02-05..02-18]: 12 -> +50.0%
                    {
                        "ma_giao_dich": "H4",
                        "ma_hang": "P1",
                        "d": "Chai",
                        "slg": 8,
                        "sl_co_ban": 8,
                        "thanh_tien": 736000,
                    },
                    {
                        "ma_giao_dich": "H5",
                        "ma_hang": "P1",
                        "d": "Chai",
                        "slg": 12,
                        "sl_co_ban": 12,
                        "thanh_tien": 1170000,
                    },
                ]
            ),
        },
    )
    assert resolve_query(
        "Acemuc Kids 200mg hộp 30 gói tăng giá lên 3.200đ: so 7 ngày kể từ ngày tăng với 7 ngày ngay trước thì số gói bán ra tăng hay giảm bao nhiêu % nhỉ?",
        ctx,
        as_of_date="2026-05-25",
    )[:2] == ("percent", 50.0)

    assert resolve_query(
        "Kể từ khi Prospan siro ho chai 100ml lên giá 97.500 đồng/chai, 14 ngày đầu bán đc nhiều hay ít hơn 14 ngày liền trước đó bao nhiêu phần trăm, tính theo số chai?",
        ctx,
        as_of_date="2026-05-25",
    )[:2] == ("percent", 50.0)


def test_unit_price_flat_sales_table_with_tenkh_before_ten_sp() -> None:
    """P1.9: fallback unit detection must skip customer column 'tenkh' before 'ten_sp'."""
    ctx = _make_ctx(
        "priv_unit_flat_tenkh",
        {
            "danh_muc": pd.DataFrame(
                [
                    {
                        "hang": "Xi măng PCB40",
                        "dv_lon": "tấn",
                        "dv_nho": "bao",
                        "he_so_qd": 20,
                    }
                ]
            ),
            "so_ban_hang": pd.DataFrame(
                [
                    {
                        "ma_phieu": "O1",
                        "tenkh": "Khách lẻ",
                        "ngay_lap": "2026-03-19",
                        "tien": 75500,
                        "ghi_chu_tt": "Thành công",
                        "gia": 75500,
                        "donvi": "bình",
                        "so_luong_hang": 1,
                        "ten_sp": "Nước kiềm Ion Life 19L",
                    },
                    {
                        "ma_phieu": "O2",
                        "tenkh": "Công ty ABC",
                        "ngay_lap": "2026-03-19",
                        "tien": 377500,
                        "ghi_chu_tt": "Đã hoàn thành",
                        "gia": 75500,
                        "donvi": "bình",
                        "so_luong_hang": 5,
                        "ten_sp": "Nước kiềm Ion Life 19L",
                    },
                ]
            ),
        },
    )
    assert resolve_query(
        "Cho mình hỏi ngày 19/03/2026 cửa hàng bán Nước kiềm Ion Life 19L với đơn giá bao nhiêu một bình?",
        ctx,
        as_of_date="2026-05-20",
    )[:2] == ("number", 75500)


def test_unit_price_conversion_ignores_base_unit_col_and_tax_rate() -> None:
    """P1.9: don_vi_goc/don_vi_co_so stay 1.0 and ty_le_thue must never act as unit factor."""
    # Pattern 1: danh_muc with don_vi_goc + dv_nhap + he_so (e.g. Bánh croissant)
    ctx_dm = _make_ctx(
        "priv_unit_don_vi_goc",
        {
            "danh_muc": pd.DataFrame(
                [
                    {
                        "ten_mon": "Bánh croissant",
                        "don_vi_goc": "cái",
                        "dv_nhap": "hộp",
                        "he_so": 6,
                    }
                ]
            ),
            "so_ban_hang": pd.DataFrame(
                [
                    {
                        "ma_hoa_don": "ORD1",
                        "ngay_giao_dich": "2026-05-21",
                        "hang": "Bánh croissant",
                        "unit": "cái",
                        "so_luong": 2,
                        "tri_gia": 74000,
                        "status": "đã xong",
                    }
                ]
            ),
        },
    )
    assert resolve_query(
        "hom 21/05/2026, Banh croissant ban ra voi don gia bao nhiu tien mot cai",
        ctx_dm,
        as_of_date="2026-06-30",
    )[:2] == ("number", 37000)

    # Pattern 2: san_pham with ty_le_thue=10 + don_vi_co_so='viên' + don_vi_sp
    ctx_tax = _make_ctx(
        "priv_unit_ty_le_thue",
        {
            "san_pham": pd.DataFrame(
                [
                    {
                        "ma_hang_hoa": "SP0051",
                        "ten_goi": "Enervon hộp 10 vỉ x 10 viên",
                        "don_vi_co_so": "viên",
                        "ty_le_thue": 10,
                        "gia_ban_le": 2400,
                    }
                ]
            ),
            "don_vi_sp": pd.DataFrame(
                [
                    {
                        "ma_hang_hoa": "SP0051",
                        "don_vi_tinh": "hộp",
                        "he_so_quy_doi": 100,
                    },
                    {"ma_hang_hoa": "SP0051", "don_vi_tinh": "vỉ", "he_so_quy_doi": 10},
                ]
            ),
            "don_hang": pd.DataFrame(
                [
                    {
                        "ma_don": "ORD003221",
                        "ngay_ban": "2025-02-18",
                        "tinh_trang": "Hoàn thành",
                    }
                ]
            ),
            "chi_tiet_don": pd.DataFrame(
                [
                    {
                        "ma_don": "ORD003221",
                        "ma_hang_hoa": "SP0051",
                        "don_vi_tinh": "viên",
                        "so_luong": 1,
                        "so_luong_quy_doi": 1,
                        "thanh_tien": 2300,
                    }
                ]
            ),
        },
    )
    assert resolve_query(
        "Ngày 18/02/2025 cửa hàng bán Enervon hộp 10 vỉ x 10 viên với đơn giá bao nhiêu một viên?",
        ctx_tax,
        as_of_date="2025-12-31",
    )[:2] == ("number", 2300)
    assert resolve_query(
        "Ngày 18/02/2025 cửa hàng bán Enervon hộp 10 vỉ x 10 viên với đơn giá bao nhiêu một vỉ?",
        ctx_tax,
        as_of_date="2025-12-31",
    )[:2] == ("number", 23000)


def test_unit_price_pct_named_item_and_lookup_price_mismatch_guard() -> None:
    """Lock %-item unit price & strict target_price matching in _lookup_price_change_date."""
    from zaic_2026_datamate.price_change import (
        _lookup_price_change_date,
        resolve_unit_price_on_date,
    )

    ctx = _make_ctx(
        "priv_pct_unit_guard",
        {
            "ban_hang_hang_hoa": pd.DataFrame(
                [
                    {
                        "ma_sp": "O1",
                        "mon": "Otrivin 0,1% chai 10ml",
                        "don_vi": "Chai",
                        "gia": 45000,
                    }
                ]
            ),
            "ban_hang_lich_su_gia": pd.DataFrame(
                [
                    {
                        "ma_san_pham": "O1",
                        "ngay_ap_dung": "2026-01-01",
                        "gia_cu": 40000,
                        "gia_moi": 45000,
                    }
                ]
            ),
            "ban_hang_hoa_don": pd.DataFrame(
                [
                    {
                        "order_id": "H1",
                        "ngay_ban": "2026-02-10",
                        "trang_thai": "Hoàn thành",
                    }
                ]
            ),
            "ban_hang_chi_tiet_hoa_don": pd.DataFrame(
                [
                    {
                        "ma_giao_dich": "H1",
                        "ma_hang": "O1",
                        "d": "Chai",
                        "slg": 2,
                        "sl_co_ban": 2,
                        "thanh_tien": 90000,
                    }
                ]
            ),
        },
    )
    assert resolve_unit_price_on_date(
        "Ngày 10/02/2026 cửa hàng bán Otrivin 0,1% chai 10ml với đơn giá bao nhiêu một chai?",
        ctx,
        as_of_date="2026-06-30",
    )[:2] == ("number", 45000)
    assert (
        _lookup_price_change_date(ctx, target_price=45000, product_code="O1")
        == "2026-01-01"
    )
    assert _lookup_price_change_date(ctx, target_price=99999, product_code="O1") is None


@pytest.mark.slow
def test_p19_real_train_price_twins() -> None:
    """P1.9: verify exact-date unit price and prefixed/14-day price change across real Train twins."""
    from zaic_2026_datamate.pipeline import build_agent, production_pipeline_config

    if not TRAIN_DATA_DIR.exists():
        pytest.skip("Train data not present")
    loader = DataLoader(TRAIN_DATA_DIR)
    agent = build_agent(production_pipeline_config())

    cases = [
        (
            "biz_104",
            "Ngày 14/07/2025 cửa hàng bán Cà phê muối (L) với đơn giá bao nhiêu một ly?",
            "2026-06-02",
            "number",
            41000,
        ),
        (
            "biz_117",
            "Cho mình hỏi ngày 19/03/2026 cửa hàng bán Nước kiềm Ion Life 19L với đơn giá bao nhiêu một bình?",
            "2026-05-20",
            "number",
            75500,
        ),
        (
            "biz_122",
            "gia ban mot vien strepsils cool hop 2 vi x 12 vien vao ngay 11/11/2025 la bn",
            "2026-07-17",
            "number",
            1700,
        ),
        (
            "biz_119",
            "check giùm giá bán mootj chai dầu gió xanh con ó chai 12ml vaof ngày 16/01/2026 là bnhiu",
            "2026-07-13",
            "number",
            49000,
        ),
        (
            "biz_125",
            "ngay 27/04/2026 cua hang ban Tom nuong moi gia bao nhieu mot dia",
            "2026-05-11",
            "number",
            104000,
        ),
        (
            "biz_105",
            "hom 21/05/2026, Banh croissant ban ra voi don gia bao nhiu tien mot cai",
            "2026-07-01",
            "number",
            37000,
        ),
        (
            "biz_123",
            "Ngày 18/02/2025 cửa hàng bán Enervon hộp 10 vỉ x 10 viên với đơn giá bao nhiêu một viên?",
            "2026-07-01",
            "number",
            2300,
        ),
        (
            "biz_124",
            "Từ ngày 16/03/2025, Fugacar 500mg hộp 1 viên tăng giá lên 23.000đ. So 7 ngày kể từ ngày 16/03/2025 với 7 ngày ngay trước đó, số viên bán ra thay đổi bao nhiêu phần trăm nhỉ?",
            "2026-05-25",
            "percent",
            69.57,
        ),
        (
            "biz_109",
            "pin panasonic aa đổi giá từ 22.000đ lên 23.000đ vào ngày 02/06/2026. 30 ngày kể từ ngày đó bán được nhìu hay ít hơn 30 ngày liền trước bao nhiêu %, tính theo vỉ hả",
            "2026-08-05",
            "percent",
            -53.52,
        ),
        (
            "biz_106",
            "So số ly Latte (L) bán ra trong 30 ngày kể từ ngày 05/02/2026, ngày tăng giá lên 53.500đ, với 30 ngày ngay trước ngày đó: tăng hay giảm bao nhiêu %?",
            "2026-09-14",
            "percent",
            5.33,
        ),
        (
            "biz_124",
            "Acemuc Kids 200mg hộp 30 gói tăng giá lên 3.200đ: so 7 ngày kể từ ngày tăng với 7 ngày ngay trước thì số gói bán ra tăng hay giảm bao nhiêu % nhỉ?",
            "2026-05-25",
            "percent",
            391.3,
        ),
        (
            "biz_124",
            "Kể từ khi Prospan siro ho chai 100ml lên giá 97.500 đồng/chai, 14 ngày đầu bán đc nhiều hay ít hơn 14 ngày liền trước đó bao nhiêu phần trăm, tính theo số chai?",
            "2026-05-25",
            "percent",
            35.48,
        ),
        (
            "biz_124",
            "Từ hồi Prospan siro ho chai 100ml lên giá 97.500đ, so 30 ngày kể từ ngày đổi giá với 30 ngày trước đó, trung bình mỗi ngày số chai bán ra tăng hay giảm bao nhiêu %?",
            "2026-05-25",
            "percent",
            28.48,
        ),
        (
            "biz_109",
            "tu ngay 20/06/2025, bia tiger 330ml tang gia len 19.000đ. so 14 ngay ke tu ngay 20/06/2025 vs 14 ngay ngay truoc do, so lon ban ra thay doi bao nhieu % ha",
            "2026-08-05",
            "percent",
            -0.85,
        ),
    ]
    ctx_cache = {}
    for biz_id, q, as_of, exp_type, exp_val in cases:
        ctx = ctx_cache.setdefault(biz_id, loader.load_business(biz_id))
        ans_type, ans_val = agent.answer_query(q, ctx, as_of_date=as_of)[:2]
        assert (ans_type, ans_val) == (exp_type, exp_val), (
            f"{biz_id} | {q}: got {(ans_type, ans_val)}"
        )
