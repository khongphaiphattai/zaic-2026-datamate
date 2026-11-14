"""Regression tests for Phase 2 bug slices (schema-generalized, zero dev_* hardcoding)."""

from pathlib import Path

import pandas as pd
import pytest

from zaic_2026_datamate.data_loader import BusinessContext, DataLoader
from zaic_2026_datamate.hybrid_agent import HybridAgent

TRAIN_DATA_DIR = (
    Path(__file__).resolve().parents[1] / "data" / "train" / "train" / "data"
)


def _make_context(business_id: str, tables: dict[str, pd.DataFrame]) -> BusinessContext:
    ctx = BusinessContext(business_id=business_id, data_dir=Path("."))
    for name, df in tables.items():
        ctx.tables[name] = df
        ctx.duckdb_conn.register(name, df)
    return ctx


def test_header_detail_monthly_aov_uses_line_items_and_half_up() -> None:
    """Monthly AOV sums chi_tiet_don line revenue (pre-order-discount) and rounds half up."""
    don_hang = pd.DataFrame(
        {
            "ma_chung_tu": ["DH1", "DH2", "DH3", "DH4"],
            "ngay": ["2025-03-10", "2025-03-11", "2025-03-12", "2025-03-13"],
            # Post-order-discount tong_don differs from line-item sum (100000 + 100001)
            "tong_don": [90000.0, 90001.0, 50000.0, 80000.0],
            "tinh_trang": ["completed", "shipping", "cancelled", "returned"],
        }
    )
    chi_tiet_don = pd.DataFrame(
        {
            "ma_don_hang": ["DH1", "DH1", "DH2", "DH3", "DH4"],
            # Valid line sum = 60000 + 40000 + 100001 = 200001 across 2 valid orders -> 100000.5 -> 100001
            "amount": [60000.0, 40000.0, 100001.0, 50000.0, 80000.0],
            "so_luong": [1, 1, 1, 1, 1],
            "dvt": ["cai", "cai", "cai", "cai", "cai"],
        }
    )
    ctx = _make_context(
        "priv_aov", {"don_hang": don_hang, "chi_tiet_don": chi_tiet_don}
    )
    agent = HybridAgent(enable_slow_path=False)

    res = agent.answer_query(
        "Doanh thu trung bình trên một đơn tháng 3/2025 là bao nhiêu ạ?",
        ctx,
        as_of_date="2026-07-28",
    )
    assert res[:2] == ("number", 100001)


def test_header_detail_rolling_daily_revenue_uses_line_items() -> None:
    """Rolling daily store revenue on header+detail schemas uses chi_tiet_don line sums."""
    don_hang = pd.DataFrame(
        {
            "ma_chung_tu": ["DH1", "DH2", "DH3"],
            "ngay": ["2025-12-30", "2025-12-30", "2025-12-30"],
            "tong_don": [95000.0, 50000.0, 30000.0],
            "tinh_trang": ["completed", "shipping", "cancelled"],
        }
    )
    chi_tiet_don = pd.DataFrame(
        {
            "ma_don_hang": ["DH1", "DH2", "DH3"],
            "amount": [100000.0, 55000.0, 30000.0],
            "so_luong": [1, 1, 1],
            "dvt": ["cai", "cai", "cai"],
        }
    )
    ctx = _make_context(
        "priv_roll_rev", {"don_hang": don_hang, "chi_tiet_don": chi_tiet_don}
    )
    agent = HybridAgent(enable_slow_path=False)

    res = agent.answer_query(
        "Hôm qua cửa hàng bán được bao nhiêu tiền?",
        ctx,
        as_of_date="2025-12-31",
    )
    assert res[:2] == ("number", 155000)


def test_header_detail_rolling_unit_falls_back_to_base_qty_when_raw_null() -> None:
    """When raw qty is NULL, fall back to base qty; keep raw qty when counting packs."""
    don_hang = pd.DataFrame(
        {
            "ma_chung_tu": ["DH1", "DH2", "DH3"],
            "ngay_ban": ["2025-06-13", "2025-06-13", "2025-06-13"],
            "xu_ly": ["completed", "completed", "returned"],
        }
    )
    chi_tiet_don = pd.DataFrame(
        {
            "ma_don": ["DH1", "DH2", "DH3"],
            "don_vi_tinh": ["ly", "hộp", "ly"],
            "sl": [float("nan"), 2.0, 5.0],
            "sl_quy_doi": [4.0, 48.0, 5.0],
            "t": [120000.0, 240000.0, 150000.0],
        }
    )
    ctx = _make_context(
        "priv_roll_unit", {"don_hang": don_hang, "chi_tiet_don": chi_tiet_don}
    )
    agent = HybridAgent(enable_slow_path=False)

    assert agent.answer_query(
        "Hôm qua bán được bao nhiêu ly?", ctx, as_of_date="2025-06-14"
    )[:2] == (
        "number",
        4,
    )
    assert agent.answer_query(
        "Hôm qua bán được bao nhiêu hộp?", ctx, as_of_date="2025-06-14"
    )[:2] == (
        "number",
        2,
    )


@pytest.mark.parametrize(
    "name_col,expected_name",
    [("khach_hang_ten", "Nguyễn Minh An"), ("kh", "Trần Bảo Ngọc")],
)
def test_customer_ranking_resolves_khach_hang_ten_and_kh(
    name_col: str, expected_name: str
) -> None:
    """Customer ranking resolves names from khach_hang_ten and kh instead of returning raw IDs."""
    don_hang = pd.DataFrame(
        {
            "ma_chung_tu": ["DH1", "DH2"],
            "ngay": ["2025-06-05", "2025-06-06"],
            "customer_id": ["KH007", None],
            "tong_don": [500000.0, 9000000.0],
            "tinh_trang": ["completed", "completed"],
        }
    )
    chi_tiet_don = pd.DataFrame(
        {
            "ma_don_hang": ["DH1", "DH2"],
            "amount": [500000.0, 9000000.0],
            "so_luong": [1, 5],
            "dvt": ["nồi", "nồi"],
        }
    )
    khach_hang = pd.DataFrame({"ma_kh": ["KH007"], name_col: [expected_name]})
    ctx = _make_context(
        f"priv_cust_{name_col}",
        {"don_hang": don_hang, "chi_tiet_don": chi_tiet_don, "khach_hang": khach_hang},
    )
    agent = HybridAgent(enable_slow_path=False)

    res = agent.answer_query(
        "Tháng 6/2025 khách nào mua nhiều tiền nhất?",
        ctx,
        as_of_date="2026-07-28",
    )
    assert res[:2] == ("string", expected_name)


@pytest.mark.slow
@pytest.mark.skipif(
    not TRAIN_DATA_DIR.exists(), reason="Train data directory unavailable"
)
def test_slice_a_real_train_biz127_and_biz111() -> None:
    """Real training-twin verification for biz_127 and biz_111 Slice A fixes."""
    loader = DataLoader(TRAIN_DATA_DIR)
    agent = HybridAgent(enable_slow_path=False)

    ctx127 = loader.load_business("biz_127")
    assert agent.answer_query(
        "Doanh thu trung bình trên một đơn tháng 3/2025 là bao nhiêu ạ?",
        ctx127,
        as_of_date="2026-07-28",
    )[:2] == ("number", 522915)
    assert agent.answer_query(
        "Hôm qua cửa hàng bán được bao nhiêu tiền?",
        ctx127,
        as_of_date="2025-12-31",
    )[:2] == ("number", 25465500)
    assert agent.answer_query(
        "Hôm qua tổng cộng bán được bao nhiêu nồi?",
        ctx127,
        as_of_date="2025-12-02",
    )[:2] == ("number", 25)
    assert agent.answer_query(
        "Tháng 6/2025 khách nào mua nhiều tiền nhất?",
        ctx127,
        as_of_date="2026-07-28",
    )[:2] == ("string", "Thân Hồng Nhung")

    ctx111 = loader.load_business("biz_111")
    assert agent.answer_query(
        "Hôm qua bán được bao nhiêu ly?",
        ctx111,
        as_of_date="2025-06-14",
    )[:2] == ("number", 201)


def test_price_change_unambiguous_product_no_token_collision() -> None:
    """Product matcher distinguishes size (M) vs (L) and avoids 4-char token collisions."""
    so_ban_hang = pd.DataFrame(
        {
            "ngay_giao_dich": [
                # Cappuccino (M) goes DOWN 50% (10 -> 5), while Cappuccino (L) goes UP 100% (4 -> 8)
                "2025-09-18",
                "2025-09-25",
                "2025-09-19",
                "2025-09-26",
                # Topping Tran chau den vs Pudding trung
                "2026-07-14",
                "2026-07-21",
                "2026-07-15",
                "2026-07-22",
            ],
            "ma_hoa_don": ["O1", "O2", "O3", "O4", "O5", "O6", "O7", "O8"],
            "hang": [
                "Cappuccino (M)",
                "Cappuccino (M)",
                "Cappuccino (L)",
                "Cappuccino (L)",
                "Topping Trân châu đen",
                "Topping Trân châu đen",
                "Topping Pudding trứng",
                "Topping Pudding trứng",
            ],
            "don_vi_tinh": ["ly", "ly", "ly", "ly", "phần", "phần", "phần", "phần"],
            "so_luong": [10.0, 5.0, 4.0, 8.0, 20.0, 10.0, 8.0, 12.0],
            "status": ["xong"] * 8,
        }
    )
    ctx = _make_context("priv_pc_collision", {"so_ban_hang": so_ban_hang})
    agent = HybridAgent(enable_slow_path=False)

    assert agent.answer_query(
        "ngay 24/09/2025 cappuccino (l) tang gia tu 51.000đ len 53.500đ. so ly ban dc trong 7 ngay ke tu ngay do so voi 7 ngay trc do thay doi bao nhieu %",
        ctx,
        as_of_date="2026-08-01",
    )[:2] == ("percent", 100.0)
    assert agent.answer_query(
        "Topping Pudding trứng đổi giá từ 10.000đ lên 10.500đ vào ngày 20/07/2026. 7 ngày kể từ ngày đó bán được nhiều hay ít hơn 7 ngày liền trước bao nhiêu %, tính theo phần?",
        ctx,
        as_of_date="2026-08-01",
    )[:2] == ("percent", 50.0)


def test_price_change_nan_recovery_and_unit_conversion() -> None:
    """Recover NaN raw qty from revenue/price and convert packs to base unit via quy_doi."""
    don_hang_chi_tiet = pd.DataFrame(
        {
            "ma_don_hang": ["D1", "D2", "D3", "D4", "D5"],
            "ngay_dat_hang": [
                "2026-01-18",
                "2026-01-25",
                "2026-06-10",
                "2026-06-18",
                "2026-06-19",
            ],
            "trang_thai_don_hang": ["đã lấy", "đã lấy", "đã lấy", "đã lấy", "hủy"],
            "ten_san_pham": [
                "Nhang Quảng Trí bó",
                "Nhang Quảng Trí bó",
                "Clarityne 10mg hộp 1 vỉ x 10 viên",
                "Clarityne 10mg hộp 1 vỉ x 10 viên",
                "Clarityne 10mg hộp 1 vỉ x 10 viên",
            ],
            "don_vi_tinh": ["bó", "bó", "viên", "hộp", "hộp"],
            # Row 0 has NaN with 5% discount (95000 / 25000 -> rounds cleanly to 4)
            "so_luong_san_pham": [float("nan"), 6.0, 10.0, 2.0, 5.0],
            "gia_san_pham": [25000.0, 25000.0, 7800.0, 78000.0, 78000.0],
            "tong_tien": [95000.0, 150000.0, 78000.0, 156000.0, 390000.0],
        }
    )
    danh_sach_quy_doi = pd.DataFrame(
        {
            "ten_san_pham": ["Clarityne 10mg hộp 1 vỉ x 10 viên"],
            "don_vi_quy_doi": ["hộp"],
            "ti_le_quy_doi": [10.0],
            "don_vi_co_ban": ["viên"],
        }
    )
    ctx = _make_context(
        "priv_pc_nan_conv",
        {
            "don_hang_chi_tiet": don_hang_chi_tiet,
            "danh_sach_quy_doi": danh_sach_quy_doi,
        },
    )
    agent = HybridAgent(enable_slow_path=False)

    # Nhang: 4 -> 6 => +50.0% (must never return NaN)
    assert agent.answer_query(
        "nhang quảng trí bó đổi giá từ 26.000đ lên 27.500đ vào ngày 24/01/2026. 7 ngày kể từ ngày đó bán được nhiều hay ít hơn 7 ngày liền trước bao nhiêu %, tính theo bó",
        ctx,
        as_of_date="2026-07-01",
    )[:2] == ("percent", 50.0)
    # Clarityne: 10 vien -> 2 hop * 10 = 20 vien (ignore cancelled D5) => +100.0%
    assert agent.answer_query(
        "Từ ngày 17/06/2026, Clarityne 10mg hộp 1 vỉ x 10 viên tăng giá lên 7.800đ. So 7 ngày kể từ ngày 17/06/2026 với 7 ngày ngay trước đó, số viên bán ra thay đổi bao nhiêu phần trăm nhỉ?",
        ctx,
        as_of_date="2026-07-01",
    )[:2] == ("percent", 100.0)


def test_price_change_relational_pos_respects_status_and_asked_unit() -> None:
    """Relational don_hang + chi_tiet_don + san_pham filters bad statuses and uses sl_quy_doi."""
    don_hang = pd.DataFrame(
        {
            "ma_hoa_don": ["H1", "H2", "H3"],
            "ngay_lap": ["2025-09-10", "2025-09-24", "2025-09-25"],
            "tinh_trang": ["completed", "completed", "cancelled"],
        }
    )
    san_pham = pd.DataFrame(
        {
            "ma_hang_hoa": ["SP1", "SP2"],
            "ten_goi": [
                "Panadol Cảm Cúm hộp 15 vỉ x 12 viên",
                "Kẹo ngậm bổ phế hộp 10 vỉ",
            ],
            "don_vi_co_so": ["viên", "viên"],
        }
    )
    chi_tiet_don = pd.DataFrame(
        {
            "ma_hoa_don": ["H1", "H2", "H3", "H1", "H2"],
            "ma_hang_hoa": ["SP1", "SP1", "SP1", "SP2", "SP2"],
            "don_vi_tinh": ["hộp", "vỉ", "hộp", "hộp", "hộp"],
            "sl": [1.0, 3.0, 10.0, 1.0, 5.0],
            "sl_quy_doi": [180.0, 36.0, 1800.0, 100.0, 500.0],
            "tri_gia": [100000.0, 30000.0, 1000000.0, 50000.0, 250000.0],
        }
    )
    ctx = _make_context(
        "priv_pc_rel",
        {"don_hang": don_hang, "san_pham": san_pham, "chi_tiet_don": chi_tiet_don},
    )
    agent = HybridAgent(enable_slow_path=False)

    # Valid Panadol qty: 180 -> 36 (-80.0%). Cancelled H3 ignored, SP2 ignored.
    assert agent.answer_query(
        "từ ngày 24/09/2025, panadol cam cum hop 15 vi x 12 vien tăng giá lên 1.300đ. so 14 ngày kể từ ngày 24/09/2025 với 14 ngày ngay trước đó, số viên bán ra thay đổi bao nhiêu phần trăm",
        ctx,
        as_of_date="2026-06-28",
    )[:2] == ("percent", -80.0)


@pytest.mark.slow
@pytest.mark.skipif(
    not TRAIN_DATA_DIR.exists(), reason="Train data directory unavailable"
)
def test_slice_b_real_train_price_change_9_businesses() -> None:
    """Real training-twin verification across Slice B price-change failures."""
    loader = DataLoader(TRAIN_DATA_DIR)
    agent = HybridAgent(enable_slow_path=False)
    cases = [
        (
            "biz_103",
            "nhang quảng trí bó đổi giá từ 26.000đ lên 27.500đ vào ngày 24/01/2026. 7 ngày kể từ ngày đó bán được nhiều hay ít hơn 7 ngày liền trước bao nhiu %, tính theo bó",
            "2026-07-09",
            57.14,
        ),
        (
            "biz_105",
            "cho hoi ngay 24/09/2025 cappuccino (l) tang gia tu 51.000đ len 53.500đ. so ly ban dc trong 7 ngay ke tu ngay do so voi 7 ngay trc do thay doi bao nhieu %",
            "2026-08-01",
            35.71,
        ),
        (
            "biz_112",
            "Topping Pudding trứng đổi giá từ 10.000đ lên 10.500đ vào ngày 20/07/2026. 7 ngày kể từ ngày đó bán được nhiều hay ít hơn 7 ngày liền trước bao nhiêu %, tính theo phần?",
            "2026-07-31",
            12.5,
        ),
        (
            "biz_112",
            "từ ngày 11/12/2025, Nước ép cam (M) tăng gias lên 41.000đ. So 30 ngày kể từ ngày 11/12/2025 với 30 ngày ngay trc đó, soos ly bán ra thay đổi bnhiu % vậy",
            "2026-07-31",
            7.57,
        ),
        (
            "biz_117",
            "Xem giúp Xi măng INSEE Power-S bao 50kg lên giá 105.000đ từ ngày 30/01/2026; so 14 ngày kể từ ngày tăng giá với 14 ngày ngay trước, lượng bán tính theo bao tăng hay giảm bao nhiêu phần trăm?",
            "2026-05-20",
            -46.63,
        ),
        (
            "biz_121",
            "Từ ngày 17/06/2026, Clarityne 10mg hộp 1 vỉ x 10 viên tăng giá lên 7.800đ. So 7 ngày kể từ ngày 17/06/2026 với 7 ngày ngay trước đó, số viên bán ra thay đổi bao nhiêu phần trăm nhỉ?",
            "2026-09-20",
            50.0,
        ),
        (
            "biz_123",
            "tu ngay 24/09/2025, panadol cam cum hop 15 vi x 12 vien tang gia len 1.300đ. so 14 ngay ke tu ngay 24/09/2025 vs 14 ngay ngay trc do, so vien ban ra thay doi bao nhiu phan tram",
            "2026-06-28",
            -82.86,
        ),
        (
            "biz_123",
            "Plusssz Max Multivitamin viên sủi tuýp 20 viên lên giá 44.000đ từ ngày 20/11/2025; so 7 ngày kể từ ngày tăng giá vs 7 ngày ngay truwowcs, lượng bán tính theo tuýp tăng hay giảm bnhiu % z",
            "2026-06-28",
            78.57,
        ),
        (
            "biz_130",
            "so so dia Dau hu chien gion ban ra trong 30 ngay ke tu ngay 01/12/2025, ngay tang gia len 51.500đ, vs 30 ngay ngay truoc ngay do: tang hay giam bao nhiu % nhi",
            "2026-08-29",
            -12.74,
        ),
        (
            "biz_105",
            "Cho hỏi Bánh croissant lên giá 37.000đ từ ngày 28/07/2025; so 7 ngày kể từ ngày tăng giá với 7 ngày ngay trước, lượng bán tính theo cái tăng hay giảm bao nhiêu phần trăm?",
            "2026-06-24",
            21.62,
        ),
        (
            "biz_122",
            "Cho mình hỏi so số tuýp Plusssz Max Multivitamin viên sủi tuýp 20 viên bán ra trong 30 ngày kể từ ngày 29/10/2025, ngày tăng giá lên 42.000đ, với 30 ngày ngay trước ngày đó: tăng hay giảm bao nhiêu %?",
            "2026-07-17",
            -2.3,
        ),
    ]
    ctx_cache: dict[str, BusinessContext] = {}
    for bid, q, as_of, expected in cases:
        if bid not in ctx_cache:
            ctx_cache[bid] = loader.load_business(bid)
        res = agent.answer_query(q, ctx_cache[bid], as_of_date=as_of)
        assert res[:2] == ("percent", expected), (
            f"{bid}: got {res[:2]} expected {expected} for {q}"
        )


def test_price_change_danh_muc_ten_mon_and_multispace_collapse():
    """Slice B: danh_muc with ten_mon/don_vi_goc/dv_nhap/he_so AND multispace item names must match uniformly."""
    ctx = _make_context(
        "priv_pc_schema_variants",
        {
            "danh_muc": pd.DataFrame(
                [
                    {
                        "ten_mon": "Bánh sừng trâu",
                        "don_vi_goc": "cái",
                        "dv_nhap": "hộp",
                        "he_so": 6,
                    },
                ]
            ),
            "so_ban_hang": pd.DataFrame(
                [
                    # Before (2025-07-21..2025-07-27): 4 + 6 (via double space) = 10 cái
                    {
                        "ngay_giao_dich": "2025-07-22",
                        "hang": "Bánh sừng trâu",
                        "so_luong": 4,
                        "unit": "cái",
                        "status": "Hoàn thành",
                    },
                    {
                        "ngay_giao_dich": "2025-07-25",
                        "hang": "Bánh  sừng   trâu",
                        "so_luong": 1,
                        "unit": "hộp",
                        "status": "Hoàn thành",
                    },
                    # After (2025-07-28..2025-08-03): 6 + 6 = 12 cái (+20.0%)
                    {
                        "ngay_giao_dich": "2025-07-29",
                        "hang": "Bánh  sừng trâu",
                        "so_luong": 1,
                        "unit": "hộp",
                        "status": "Hoàn thành",
                    },
                    {
                        "ngay_giao_dich": "2025-08-01",
                        "hang": "Bánh sừng trâu",
                        "so_luong": 6,
                        "unit": "cái",
                        "status": "Hoàn thành",
                    },
                ]
            ),
        },
    )
    agent = HybridAgent(enable_slow_path=False)
    res = agent.answer_query(
        "Cho hỏi Bánh sừng trâu lên giá 37.000đ từ ngày 28/07/2025; so 7 ngày kể từ ngày tăng giá với 7 ngày ngay trước, lượng bán tính theo cái tăng hay giảm bao nhiêu phần trăm?",
        ctx,
        as_of_date="2026-06-24",
    )
    assert res[:2] == ("percent", 20.0)


def test_inventory_lot_ledger_boxes_and_pieces_respects_cutoff() -> None:
    """Slice C1: Prefixed lot-ledger import minus sales up to as_of_date returns [boxes, pieces]."""
    ctx = _make_context(
        "priv_inv_lot",
        {
            "du_lieu_xuat_danh_sach_san_pham": pd.DataFrame(
                [
                    {
                        "ma_sku": "SP1",
                        "ten_san_pham": "Thuốc Demo hộp 3 vỉ x 10 viên",
                        "don_vi": "viên",
                    }
                ]
            ),
            "du_lieu_xuat_danh_sach_quy_doi": pd.DataFrame(
                [
                    {
                        "ten_san_pham": "Thuốc Demo hộp 3 vỉ x 10 viên",
                        "don_vi_quy_doi": "vỉ",
                        "ti_le_quy_doi": 10,
                        "don_vi_co_ban": "viên",
                    },
                    {
                        "ten_san_pham": "Thuốc Demo hộp 3 vỉ x 10 viên",
                        "don_vi_quy_doi": "hộp",
                        "ti_le_quy_doi": 30,
                        "don_vi_co_ban": "viên",
                    },
                ]
            ),
            "du_lieu_xuat_don_nhap_hang": pd.DataFrame(
                [
                    {"ma_don_nhap": "PN1", "ngay_nhap": "2026-06-01"},
                    {
                        "ma_don_nhap": "PN2",
                        "ngay_nhap": "2026-06-20",
                    },  # Future import ignored
                ]
            ),
            "du_lieu_xuat_don_nhap_hang_chi_tiet": pd.DataFrame(
                [
                    {
                        "ma_don_nhap": "PN1",
                        "ma_sku": "SP1",
                        "ma_lo": "L1",
                        "so_luong_nhap": 100,
                    },
                    {
                        "ma_don_nhap": "PN2",
                        "ma_sku": "SP1",
                        "ma_lo": "L2",
                        "so_luong_nhap": 300,
                    },
                ]
            ),
            "du_lieu_xuat_don_hang_chi_tiet": pd.DataFrame(
                [
                    {
                        "ma_don_hang": "O1",
                        "ngay_dat_hang": "2026-06-10",
                        "ten_san_pham": "Thuốc Demo hộp 3 vỉ x 10 viên",
                    },
                    {
                        "ma_don_hang": "O2",
                        "ngay_dat_hang": "2026-06-15",
                        "ten_san_pham": "Thuốc Demo hộp 3 vỉ x 10 viên",
                    },
                    {
                        "ma_don_hang": "O3",
                        "ngay_dat_hang": "2026-06-18",
                        "ten_san_pham": "Thuốc Demo hộp 3 vỉ x 10 viên",
                    },
                ]
            ),
            "du_lieu_xuat_don_hang_lo": pd.DataFrame(
                [
                    {
                        "ma_don_hang": "O1",
                        "ma_lo": "L1",
                        "so_luong": 25,
                        "so_luong_tra": 2,
                    },
                    # Sale ON as_of_date counts toward closing stock
                    {
                        "ma_don_hang": "O2",
                        "ma_lo": "L1",
                        "so_luong": 10,
                        "so_luong_tra": 0,
                    },
                    # Future sale AFTER as_of_date must not reduce closing stock
                    {
                        "ma_don_hang": "O3",
                        "ma_lo": "L1",
                        "so_luong": 50,
                        "so_luong_tra": 0,
                    },
                ]
            ),
        },
    )
    # 100 - (25-2) - 10 = 67 -> 2 hộp (60), 7 viên lẻ
    agent = HybridAgent(enable_slow_path=False)
    res = agent.answer_query(
        "tồn kho Thuốc Demo hộp 3 vỉ x 10 viên cuối ngày hôm nay dc mấy hộp chẵn và mấy viên lẻ?",
        ctx,
        as_of_date="2026-06-15",
    )
    assert res[:2] == ("list", [2, 7])


def test_inventory_the_kho_multibranch_and_missing_schema_ood() -> None:
    """Slice C1: the_kho sums latest ton_cuoi per branch; sales-only business returns unanswerable."""
    ctx_tk = _make_context(
        "priv_inv_the_kho",
        {
            "san_pham": pd.DataFrame(
                [
                    {
                        "ma_hang_hoa": "SP09",
                        "ten_goi": "Siro Ho 100ml",
                        "don_vi_co_so": "chai",
                    }
                ]
            ),
            "the_kho": pd.DataFrame(
                [
                    {
                        "ma_giao_dich_kho": "M1",
                        "ma_hang_hoa": "SP09",
                        "ma_chi_nhanh": "CN1",
                        "ngay_gio_phat_sinh": "2025-12-09 10:00:00",
                        "ton_cuoi": 40,
                    },
                    {
                        "ma_giao_dich_kho": "M2",
                        "ma_hang_hoa": "SP09",
                        "ma_chi_nhanh": "CN1",
                        "ngay_gio_phat_sinh": "2025-12-10 18:00:00",
                        "ton_cuoi": 35,
                    },
                    {
                        "ma_giao_dich_kho": "M3",
                        "ma_hang_hoa": "SP09",
                        "ma_chi_nhanh": "CN2",
                        "ngay_gio_phat_sinh": "2025-12-10 09:00:00",
                        "ton_cuoi": 20,
                    },
                    # Future ledger movement ignored
                    {
                        "ma_giao_dich_kho": "M4",
                        "ma_hang_hoa": "SP09",
                        "ma_chi_nhanh": "CN1",
                        "ngay_gio_phat_sinh": "2025-12-11 08:00:00",
                        "ton_cuoi": 1,
                    },
                ]
            ),
        },
    )
    agent = HybridAgent(enable_slow_path=False)
    assert agent.answer_query(
        "cuối ngày hôm nay Siro Ho 100ml còn bao nhiêu chai?",
        ctx_tk,
        as_of_date="2025-12-10",
    )[:2] == ("number", 55)

    ctx_ood = _make_context(
        "priv_inv_ood",
        {
            "don_hang_chi_tiet": pd.DataFrame(
                [
                    {
                        "ma_don_hang": "O1",
                        "ngay_dat_hang": "2026-07-09",
                        "trang_thai_don_hang": "Đã hoàn thành",
                        "ten_san_pham": "Nước ngọt 330ml",
                        "so_luong_san_pham": 24,
                        "don_vi_tinh": "lon",
                        "tong_tien": 240000.0,
                    }
                ]
            )
        },
    )
    assert agent.answer_query(
        "tồn kho Nước ngọt 330ml cuối ngày hôm nay là bao nhiêu lon?",
        ctx_ood,
        as_of_date="2026-07-09",
    )[:2] == ("unanswerable", None)


def test_rolling_unit_count_prefers_base_qty_and_header_detail_never_returns_revenue() -> (
    None
):
    """Slice C2: Rolling unit count uses base_qty over NULL/pack raw_qty and never leaks VND revenue."""
    # 1. Flat table with NULL raw_qty + populated so_luong_quy_doi
    ctx_flat = _make_context(
        "priv_unit_flat",
        {
            "danh_sach_hang_hoa": pd.DataFrame(
                [{"ten_hang_hoa": "Nước suối 19L", "don_vi_tinh": "bình"}]
            ),
            "so_chi_tiet_ban_hang": pd.DataFrame(
                [
                    {
                        "ngay": "2025-10-17",
                        "ma_don_hang": "O1",
                        "ten_hang_hoa": "Nước suối 19L",
                        "don_vi_tinh": "bình",
                        "so_luong": 5.0,
                        "so_luong_quy_doi": 5,
                        "trang_thai": "ok",
                    },
                    {
                        "ngay": "2025-10-17",
                        "ma_don_hang": "O2",
                        "ten_hang_hoa": "Nước suối 19L",
                        "don_vi_tinh": "bình",
                        "so_luong": float("nan"),
                        "so_luong_quy_doi": 8,
                        "trang_thai": "ok",
                    },
                    {
                        "ngay": "2025-10-17",
                        "ma_don_hang": "O3",
                        "ten_hang_hoa": "Nước suối 19L",
                        "don_vi_tinh": "bình",
                        "so_luong": 2.0,
                        "so_luong_quy_doi": 2,
                        "trang_thai": "hủy",
                    },
                ]
            ),
        },
    )
    agent = HybridAgent(enable_slow_path=False)
    assert agent.answer_query(
        "Cộng mọi mặt hàng tính theo bình, hôm qua bán ra đc mấy bình?",
        ctx_flat,
        as_of_date="2025-10-18",
    )[:2] == ("number", 13)

    # 2. Header + Detail danh_sach_hoa_don + danh_sach_hoa_don_chi_tiet
    ctx_hd = _make_context(
        "priv_unit_hd",
        {
            "danh_sach_hoa_don": pd.DataFrame(
                [
                    {
                        "ma_hoa_don": "H1",
                        "thoi_gian": "2026-03-18",
                        "trang_thai": "xong",
                    },
                    {
                        "ma_hoa_don": "H2",
                        "thoi_gian": "2026-03-25",
                        "trang_thai": "xong",
                    },  # outside last week (Mon 16..Sun 22)
                ]
            ),
            "danh_sach_hoa_don_chi_tiet": pd.DataFrame(
                [
                    {
                        "ma_hoa_don": "H1",
                        "ten_hang": "Trà sữa",
                        "don_vi_tinh": "ly",
                        "so_luong": 3,
                        "so_luong_quy_doi": 3,
                        "thanh_tien": 135000.0,
                    },
                    {
                        "ma_hoa_don": "H2",
                        "ten_hang": "Trà sữa",
                        "don_vi_tinh": "ly",
                        "so_luong": 5,
                        "so_luong_quy_doi": 5,
                        "thanh_tien": 225000.0,
                    },
                ]
            ),
        },
    )
    assert agent.answer_query(
        "Tuần trước tổng cộng bán được bao nhiêu ly?",
        ctx_hd,
        as_of_date="2026-03-28",
    )[:2] == ("number", 3)

    # 3. Pack unit conversion via san_pham.don_vi_co_ban
    ctx_pack = _make_context(
        "priv_unit_pack",
        {
            "san_pham": pd.DataFrame(
                [
                    {
                        "ma_san_pham_ban": "SP1",
                        "ten_sp": "Bia Lager",
                        "don_vi_co_ban": "lon",
                    }
                ]
            ),
            "don_hang": pd.DataFrame(
                [{"ma_chung_tu": "D1", "ngay": "2025-12-27", "tinh_trang": "completed"}]
            ),
            "chi_tiet_don": pd.DataFrame(
                [
                    {
                        "ma_don_hang": "D1",
                        "ma_mat_hang": "SP1",
                        "dvt": "thùng",
                        "so_luong": 1,
                        "so_luong_le": 24,
                        "amount": 300000.0,
                    },
                    {
                        "ma_don_hang": "D1",
                        "ma_mat_hang": "SP1",
                        "dvt": "lon",
                        "so_luong": 6,
                        "so_luong_le": 6,
                        "amount": 90000.0,
                    },
                ]
            ),
        },
    )
    assert agent.answer_query(
        "cộng mọi mặt hàng tính theo lon, bữa qua bán ra được mấy lon",
        ctx_pack,
        as_of_date="2025-12-28",
    )[:2] == ("number", 30)


def test_bestseller_uses_base_qty_nan_recovery_and_topk_tie_safety() -> None:
    """Slice C3: Best-seller rankings recover NaNs, convert danh_muc units, handle quy_ra_dv_le, and check Top-K ties."""
    agent = HybridAgent(enable_slow_path=False)

    # 1. NaN recovery + danh_muc dv_lon -> dv_nho conversion in weekly ranking
    ctx_dm = _make_context(
        "priv_rank_dm",
        {
            "danh_muc": pd.DataFrame(
                [
                    {
                        "hang": "Băng keo",
                        "dv_lon": "cây",
                        "dv_nho": "cuộn",
                        "he_so_qd": 10,
                    }
                ]
            ),
            "so_ban_hang": pd.DataFrame(
                [
                    # Competitor: 15 đơn vị
                    {
                        "ma_phieu": "O1",
                        "ngay_lap": "2026-05-05",
                        "ghi_chu_tt": "Thành công",
                        "ten_sp": "Thép D12",
                        "donvi": "cây",
                        "so_luong_hang": 15.0,
                        "gia": 100000.0,
                        "tien": 1500000.0,
                    },
                    # Băng keo: 2 cây (NaN qty recovered via 180k/90k) = 20 cuộn -> WINNER
                    {
                        "ma_phieu": "O2",
                        "ngay_lap": "2026-05-06",
                        "ghi_chu_tt": "đã lấy",
                        "ten_sp": "Băng keo",
                        "donvi": "cây",
                        "so_luong_hang": float("nan"),
                        "gia": 90000.0,
                        "tien": 180000.0,
                    },
                ]
            ),
        },
    )
    assert agent.answer_query(
        "Cho mình hỏi mặt hàng bán chạy nhất tuần rồi là mặt hàng nào?",
        ctx_dm,
        as_of_date="2026-05-11",
    )[:2] == ("string", "Băng keo")

    # 2. quy_ra_dv_le ranking & Top-3 strict list vs tie abstain
    ctx_topk = _make_context(
        "priv_rank_topk",
        {
            "ban_hang": pd.DataFrame(
                [
                    {
                        "ma_hoa_don": "B1",
                        "ngay_thang": "2025-02-05",
                        "ket_qua": "Xong",
                        "noi_dung": "Snack A",
                        "so_luong": 2,
                        "quy_ra_dv_le": 50,
                        "thanh_tien": 500000.0,
                    },
                    {
                        "ma_hoa_don": "B2",
                        "ngay_thang": "2025-02-06",
                        "ket_qua": "Xong",
                        "noi_dung": "Mì B",
                        "so_luong": 10,
                        "quy_ra_dv_le": 30,
                        "thanh_tien": 300000.0,
                    },
                    {
                        "ma_hoa_don": "B3",
                        "ngay_thang": "2025-02-07",
                        "ket_qua": "Xong",
                        "noi_dung": "Nước C",
                        "so_luong": 5,
                        "quy_ra_dv_le": 20,
                        "thanh_tien": 200000.0,
                    },
                    {
                        "ma_hoa_don": "B4",
                        "ngay_thang": "2025-02-08",
                        "ket_qua": "Xong",
                        "noi_dung": "Kẹo D",
                        "so_luong": 1,
                        "quy_ra_dv_le": 10,
                        "thanh_tien": 100000.0,
                    },
                ]
            )
        },
    )
    assert agent.answer_query(
        "xem giúp tháng 2/2025 hàng nào chạy nhất", ctx_topk, as_of_date="2026-01-01"
    )[:2] == ("string", "Snack A")
    assert agent.answer_query(
        "liệt kê 3 mặt hangf chạy nhất thangs 2/2025", ctx_topk, as_of_date="2026-01-01"
    )[:2] == ("list", ["Snack A", "Mì B", "Nước C"])

    # Tie at cutoff rank 3 vs 4 must abstain cleanly
    ctx_tie = _make_context(
        "priv_rank_tie",
        {
            "ban_hang": pd.DataFrame(
                [
                    {
                        "ma_hoa_don": "B1",
                        "ngay_thang": "2025-02-05",
                        "ket_qua": "Xong",
                        "noi_dung": "Snack A",
                        "quy_ra_dv_le": 50,
                        "thanh_tien": 500000.0,
                    },
                    {
                        "ma_hoa_don": "B2",
                        "ngay_thang": "2025-02-06",
                        "ket_qua": "Xong",
                        "noi_dung": "Mì B",
                        "quy_ra_dv_le": 30,
                        "thanh_tien": 300000.0,
                    },
                    {
                        "ma_hoa_don": "B3",
                        "ngay_thang": "2025-02-07",
                        "ket_qua": "Xong",
                        "noi_dung": "Nước C",
                        "quy_ra_dv_le": 20,
                        "thanh_tien": 200000.0,
                    },
                    {
                        "ma_hoa_don": "B4",
                        "ngay_thang": "2025-02-08",
                        "ket_qua": "Xong",
                        "noi_dung": "Kẹo D",
                        "quy_ra_dv_le": 20,
                        "thanh_tien": 200000.0,
                    },
                ]
            )
        },
    )
    assert (
        agent.answer_query(
            "liệt kê 3 mặt hàng chạy nhất tháng 2/2025",
            ctx_tie,
            as_of_date="2026-01-01",
        )[0]
        == "abstain"
    )


@pytest.mark.slow
@pytest.mark.skipif(
    not TRAIN_DATA_DIR.exists(), reason="Train data directory unavailable"
)
def test_slice_c_real_train_inventory_units_rankings() -> None:
    """Real training-twin verification for Slice C (inventory, rolling units, rankings)."""
    loader = DataLoader(TRAIN_DATA_DIR)
    agent = HybridAgent(enable_slow_path=False)
    cases: list[tuple[str, str, str, str, object]] = [
        # C1 Inventory & OOD
        (
            "biz_122",
            "tồn kho Telfast HD 180mg hộp 3 vỉ x 10 viên cuoois ngày hôm nay dc mấy hộp chẵn và mấy viên lẻ vậy",
            "2026-07-17",
            "list",
            [5, 13],
        ),
        (
            "biz_122",
            "Cho hỏi cuối ngày hôm nay Savi Losartan 50mg hộp 3 vỉ x 10 viên còn mấy hộp chẵn, mấy viên lẻ?",
            "2026-07-17",
            "list",
            [2, 22],
        ),
        (
            "biz_117",
            "Hết hôm nay, Gas Saigon Petro 12kg còn lại mấy bình trong kho?",
            "2026-05-20",
            "number",
            75,
        ),
        (
            "biz_123",
            "cuoi ngay hom nay Yumangel hop 20 goi con bao nhieu goi",
            "2025-12-10",
            "number",
            162,
        ),
        (
            "biz_103",
            "het hom nay, bia tiger 330ml con lai may lon trong kho ha",
            "2026-07-09",
            "unanswerable",
            None,
        ),
        (
            "biz_103",
            "tồn kho red bull 250ml cuối ngày hôm nay là bao nhiêu lon",
            "2026-07-09",
            "unanswerable",
            None,
        ),
        (
            "biz_130",
            "hôm nay kho còn tồn bao nhiêu lon coca-cola lon lúc cuối ngày",
            "2026-08-29",
            "unanswerable",
            None,
        ),
        (
            "biz_130",
            "Cho hỏi tồn kho Bia Tiger lon cuối ngày hôm nay là bao nhiêu lon?",
            "2026-08-29",
            "unanswerable",
            None,
        ),
        (
            "biz_118",
            "Cuối ngày hôm nay CB Panasonic MCB 2P 20A còn bao nhiêu cái?",
            "2026-09-10",
            "unanswerable",
            None,
        ),
        # C2 Rolling Unit Quantity
        (
            "biz_116",
            "Cộng mọi mặt hàng tính theo bình, hôm qua bán ra đc mấy bình?",
            "2025-10-18",
            "number",
            13,
        ),
        (
            "biz_115",
            "hqua tổng cộng bán được bao nhiêu cái",
            "2025-03-11",
            "number",
            23,
        ),
        (
            "biz_118",
            "Cộng mọi mặt hàng tính theo bình, 7 ngày gần nhất bán ra đc mấy bình?",
            "2025-08-24",
            "number",
            148,
        ),
        (
            "biz_112",
            "Tuần trước cửa hàng bán ra tổng cộng mấy ly?",
            "2026-06-25",
            "number",
            1751,
        ),
        ("biz_112", "tuan nay ban duoc bn ly ha", "2025-12-11", "number", 771),
        (
            "biz_106",
            "Tuần trước tổng cộng bán được bao nhiêu ly?",
            "2026-03-28",
            "number",
            1521,
        ),
        (
            "biz_127",
            "cộng mọi mặt hàng tính theo lon, bữa qua bán ra được mấy lon",
            "2025-12-28",
            "number",
            140,
        ),
        (
            "biz_129",
            "bữa qua toongr cộng bán dduwowcj bnhiu lon",
            "2025-07-09",
            "number",
            59,
        ),
        # C3 Best-seller Top-1 & Top-K
        (
            "biz_115",
            "liệt kê 5 mặt hangf chạy nhất thangs 2/2025",
            "2026-05-16",
            "list",
            [
                "Gạch ống 8x8x18 Tuynel",
                "Ngói Đồng Nai 22 viên/m2",
                "Xi măng Hà Tiên PCB40 bao 50kg",
                "Xi măng INSEE Power-S bao 50kg",
                "Thép Pomina D10 cây 11.7m",
            ],
        ),
        (
            "biz_130",
            "Cho hỏi tuần này mặt hàng nào bán chạy nhất?",
            "2026-03-18",
            "string",
            "Bia Sài Gòn Lager chai",
        ),
        (
            "biz_101",
            "xem giúp tháng 9/2025 hàng nào chạy nhất",
            "2026-08-24",
            "string",
            "Snack Oishi tôm cay 40g",
        ),
        (
            "biz_117",
            "Cho mình hỏi mặt hàng bán chạy nhất tuần rồi là mặt hàng nào?",
            "2026-05-11",
            "string",
            "Băng keo điện Nano 20 yard",
        ),
        (
            "biz_118",
            "Mặt hàng bán chạy nhất tuần rồi là mặt hàng nào?",
            "2026-02-16",
            "string",
            "Xi măng INSEE Power-S bao 50kg",
        ),
    ]
    ctx_cache: dict[str, BusinessContext] = {}
    for bid, q, as_of, exp_t, exp_v in cases:
        if bid not in ctx_cache:
            ctx_cache[bid] = loader.load_business(bid)
        res = agent.answer_query(q, ctx_cache[bid], as_of_date=as_of)
        assert res[:2] == (exp_t, exp_v), (
            f"{bid}: got {res[:2]} expected {(exp_t, exp_v)} for {q}"
        )


def test_slice_d_multi_month_typos_and_technical_abstain_gate() -> None:
    """Slice D: Telex typos in peak-month/monotonic growth, YTD/quarter, item-unit vs revenue, and Abstain Gate."""
    from zaic_2026_datamate.hybrid_agent import technical_abstain_gate

    # 1. Technical Abstain Gate unit checks (NaN, Inf, bad dates, empty list, type mismatch)
    assert technical_abstain_gate("any", "percent", float("nan"), None, "fast_path")[
        :2
    ] == ("abstain", None)
    assert technical_abstain_gate("any", "number", float("inf"), None, "fast_path")[
        :2
    ] == ("abstain", None)
    assert technical_abstain_gate("any", "number", True, None, "fast_path")[:2] == (
        "abstain",
        None,
    )
    assert technical_abstain_gate("any", "date", "2025-13-01", None, "fast_path")[
        :2
    ] == ("abstain", None)
    assert technical_abstain_gate("any", "string", "", None, "fast_path")[:2] == (
        "abstain",
        None,
    )
    assert technical_abstain_gate("any", "list", [], None, "fast_path")[:2] == (
        "abstain",
        None,
    )
    assert technical_abstain_gate(
        "any", "list", [1.0, float("nan")], None, "fast_path"
    )[:2] == ("abstain", None)
    assert technical_abstain_gate(
        "doanh thu 3 tháng có tăng liên tục không",
        "list",
        [10, 20, 30],
        None,
        "fast_path",
    )[:2] == ("abstain", None)
    assert technical_abstain_gate(
        "doanh thu tháng naof lớn nhất", "list", [10, 20, 30], None, "fast_path"
    )[:2] == ("abstain", None)
    # Valid list listing even when ending with polite 'được không' must NOT be blocked
    assert technical_abstain_gate(
        "liệt kê 5 mặt hàng chạy nhất được không", "list", ["A", "B"], None, "fast_path"
    )[:2] == ("list", ["A", "B"])

    # 2. Synthetic priv_* end-to-end tests: multi-month typos, YTD nawm, quys, ngay_mua, kg/san_pham, header+detail qty
    ctx_mm = _make_context(
        "priv_slice_d",
        {
            "so_ban_hang": pd.DataFrame(
                [
                    {
                        "ma_gd": "O1",
                        "ngay_mua": "2025-03-10",
                        "san_pham": "Đinh 5 phân",
                        "don_vi_tinh": "kg",
                        "sluong": float("nan"),
                        "quy_ra_dv_le": 12,
                        "tong": 240000,
                        "trang_thai": "xong",
                    },
                    {
                        "ma_gd": "O2",
                        "ngay_mua": "2025-04-10",
                        "san_pham": "Đinh 5 phân",
                        "don_vi_tinh": "kg",
                        "sluong": 5.0,
                        "quy_ra_dv_le": 5,
                        "tong": 100000,
                        "trang_thai": "xong",
                    },
                    {
                        "ma_gd": "O3",
                        "ngay_mua": "2025-05-10",
                        "san_pham": "Đinh 5 phân",
                        "don_vi_tinh": "kg",
                        "sluong": 15.0,
                        "quy_ra_dv_le": 15,
                        "tong": 300000,
                        "trang_thai": "xong",
                    },
                    {
                        "ma_gd": "O4",
                        "ngay_mua": "2025-07-05",
                        "san_pham": "Đinh 5 phân",
                        "don_vi_tinh": "kg",
                        "sluong": 4.0,
                        "quy_ra_dv_le": 4,
                        "tong": 80000,
                        "trang_thai": "xong",
                    },
                    {
                        "ma_gd": "O5",
                        "ngay_mua": "2025-05-11",
                        "san_pham": "Bộ  van dây gas",
                        "don_vi_tinh": "bộ",
                        "sluong": 3.0,
                        "quy_ra_dv_le": 3,
                        "tong": 600000,
                        "trang_thai": "xong",
                    },
                    {
                        "ma_gd": "O6",
                        "ngay_mua": "2025-05-12",
                        "san_pham": "Bộ van dây gas",
                        "don_vi_tinh": "bộ",
                        "sluong": 2.0,
                        "quy_ra_dv_le": 2,
                        "tong": 400000,
                        "trang_thai": "xong",
                    },
                ]
            )
        },
    )
    agent = HybridAgent(enable_slow_path=False)
    assert agent.answer_query(
        "xét 3 tháng 3/2025, 4/2025 và 5/2025, doanh thu tháng naof lớn nhất",
        ctx_mm,
        as_of_date="2025-08-01",
    )[:2] == ("date", "2025-05")
    assert agent.answer_query(
        "doanh thu 3 tháng 3/2025, 4/2025, 5/2025 cos tăng liên tục khoong",
        ctx_mm,
        as_of_date="2025-08-01",
    )[:2] == ("boolean", False)
    assert agent.answer_query(
        "số đơn từ đầu nawm tới hết bữa nay là bao nhiêu",
        ctx_mm,
        as_of_date="2025-05-12",
    )[:2] == ("number", 5)
    assert agent.answer_query(
        "số đơn quys 3/2025 là nhiêu",
        ctx_mm,
        as_of_date="2025-08-01",
    )[:2] == ("number", 1)
    assert agent.answer_query(
        "Cho mình hỏi số kg Đinh 5 phân bán được trong tháng 3/2025 là bao nhiêu?",
        ctx_mm,
        as_of_date="2025-08-01",
    )[:2] == ("number", 12)
    assert agent.answer_query(
        "so bo van day gas ban duoc trong thang 5/2025 la bn",
        ctx_mm,
        as_of_date="2025-08-01",
    )[:2] == ("number", 5)


@pytest.mark.slow
@pytest.mark.skipif(
    not TRAIN_DATA_DIR.exists(), reason="Train data directory unavailable"
)
def test_slice_d_real_train_typos_item_qty_and_biz129() -> None:
    """Real training-twin verification for Slice D (8 Train Qs + biz_129 debt non-regression)."""
    loader = DataLoader(TRAIN_DATA_DIR)
    agent = HybridAgent(enable_slow_path=False)
    cases: list[tuple[str, str, str, str, object]] = [
        (
            "biz_118",
            "xét 3 tháng 3/2025, 4/2025 và 5/2025, doanh thu tháng naof lớn nhất",
            "2026-09-10",
            "date",
            "2025-05",
        ),
        (
            "biz_112",
            "doanh thu 3 tháng 4/2026, 5/2026, 6/2026 cos tăng liên tục khoong",
            "2026-07-31",
            "boolean",
            False,
        ),
        (
            "biz_124",
            "số đơn từ đầu nawm tới hết bữa nay là bao nhiêu",
            "2025-05-08",
            "number",
            11527,
        ),
        ("biz_102", "số đơn quys 3/2025 là nhiêu", "2026-06-16", "number", 5204),
        (
            "biz_113",
            "so bo van day gas namilux ban duoc trong thang 12/2025 la bn",
            "2026-08-09",
            "number",
            17,
        ),
        (
            "biz_121",
            "cho hoi so tuyp kudos vitamin c 1000mg vien sui tuyp 20 vien ban duoc trong thang 6/2026 la bn",
            "2026-09-20",
            "number",
            61,
        ),
        (
            "biz_114",
            "Cho mình hỏi số kg Đinh 5 phân bán được trong tháng 9/2025 là bao nhiêu?",
            "2026-06-21",
            "number",
            84,
        ),
        (
            "biz_129",
            "thang 9/2025 mat hang Chan ga ngam sa tac ban duoc tong cong bao nhiu dia",
            "2025-07-09",
            "number",
            182,
        ),
    ]
    ctx_cache: dict[str, BusinessContext] = {}
    for bid, q, as_of, exp_t, exp_v in cases:
        if bid not in ctx_cache:
            ctx_cache[bid] = loader.load_business(bid)
        res = agent.answer_query(q, ctx_cache[bid], as_of_date=as_of)
        assert res[:2] == (exp_t, exp_v), (
            f"{bid}: got {res[:2]} expected {(exp_t, exp_v)} for {q}"
        )

    # Verify customer debt question on biz_129 is never hijacked into inventory OOD unanswerable by 'hang ton' substring
    debt_res = agent.answer_query(
        "Tính đến hết ngày 14/08/2026, khách hàng còn nợ cửa hàng tổng cộng bao nhiêu tiền?",
        ctx_cache["biz_129"],
        as_of_date="2026-08-14",
    )
    assert debt_res[:2] in {("abstain", None), ("number", 2854584)}


def test_p15_telex_header_detail_peak_day_and_multi_month_unit() -> None:
    """P1.5 synthetic unit tests: Header+Detail rev fallback, 3m ending, peak day, Telex/English, guards."""
    from zaic_2026_datamate.cleaner import normalize_query_telex
    from zaic_2026_datamate.pipeline import build_agent, production_pipeline_config

    # 1. Ambiguous English/hardware tokens like 'DC 12V' must NEVER be corrupted by Telex normalization
    assert "dc 12v" in normalize_query_telex("Adapter DC 12V bán được mấy cái").lower()
    assert normalize_query_telex("bán dc total nhiu tiền") != "bán dc total nhiu tiền"

    don_hang = pd.DataFrame(
        [
            {"so_ct": "D1", "ngay_ban": "2025-01-10", "trang_thai": "Hoàn thành"},
            {"so_ct": "D2", "ngay_ban": "2025-02-10", "trang_thai": "Hoàn thành"},
            {"so_ct": "D3", "ngay_ban": "2025-03-05", "trang_thai": "Hoàn thành"},
            {"so_ct": "D4", "ngay_ban": "2025-03-06", "trang_thai": "Hoàn thành"},
            {"so_ct": "D5", "ngay_ban": "2025-03-06", "trang_thai": "Đã hủy"},
            {"so_ct": "D6", "ngay_ban": "2025-03-14", "trang_thai": "Hoàn thành"},
            {"so_ct": "D7", "ngay_ban": "2025-03-15", "trang_thai": "Hoàn thành"},
            {"so_ct": "D8", "ngay_ban": "2025-03-15", "trang_thai": "Hoàn thành"},
        ]
    )
    chi_tiet_don = pd.DataFrame(
        [
            {
                "so_ct": "D1",
                "ma_hang_ban": "M1",
                "ten_hang": "Gà rán",
                "sl": 1,
                "tien_ban": 100000,
            },
            {
                "so_ct": "D2",
                "ma_hang_ban": "M1",
                "ten_hang": "Gà rán",
                "sl": 2,
                "tien_ban": 200000,
            },
            {
                "so_ct": "D3",
                "ma_hang_ban": "M1",
                "ten_hang": "Gà rán",
                "sl": 1,
                "tien_ban": 150000,
            },
            {
                "so_ct": "D4",
                "ma_hang_ban": "M2",
                "ten_hang": "Trà đá",
                "sl": 3,
                "tien_ban": 300000,
            },
            {
                "so_ct": "D5",
                "ma_hang_ban": "M2",
                "ten_hang": "Trà đá",
                "sl": 5,
                "tien_ban": 999000,
            },
            {
                "so_ct": "D6",
                "ma_hang_ban": "M1",
                "ten_hang": "Gà rán",
                "sl": 1,
                "tien_ban": 50000,
            },
            {
                "so_ct": "D7",
                "ma_hang_ban": "M1",
                "ten_hang": "Gà rán",
                "sl": 1,
                "tien_ban": 40000,
            },
            {
                "so_ct": "D8",
                "ma_hang_ban": "M2",
                "ten_hang": "Trà đá",
                "sl": 1,
                "tien_ban": 60000,
            },
        ]
    )
    ctx = _make_context(
        "priv_p15_hd", {"don_hang": don_hang, "chi_tiet_don": chi_tiet_don}
    )
    ctx.readme = (
        "# Tài liệu\n"
        "### Cột trong `don_hang.xlsx`\n"
        "- **so_ct**: mã đơn\n"
        "- **ngay_ban**: ngày bán\n"
        "- **trang_thai**: trạng thái đơn\n"
    )
    agent = build_agent(production_pipeline_config())

    # Header+Detail overall rev fallback (rev_col on don_hang is None)
    assert agent.answer_query(
        "Cho tôi biết doanh thu tháng 3/2025.", ctx, "2025-04-01"
    )[:2] == ("number", 600000)
    assert agent.answer_query(
        "Tổng doanh thu quý 1/2025 là bao nhiêu?", ctx, "2025-04-01"
    )[:2] == ("number", 900000)
    assert agent.answer_query(
        "thang 3/2025 cua hang ban dc total bao nhieu tien", ctx, "2025-04-01"
    )[:2] == ("number", 600000)
    assert agent.answer_query(
        "tháng 3/2025 cửa hàng bán đươc tổng cộng bn tiền", ctx, "2025-04-01"
    )[:2] == ("number", 600000)

    # 3-month consecutive list ending at target month
    assert agent.answer_query(
        "dthu 3 tháng liền nhau kết thúc ở tháng 3/2025 là bao nhiêu mỗi tháng, từ tháng cũ đến tháng mới",
        ctx,
        "2025-04-01",
    )[:2] == ("list", [100000, 200000, 600000])

    # Rolling window + relative day revenue & bill/order counts
    assert agent.answer_query(
        "tong revenue trong ngay hom nay la bao nhiu", ctx, "2025-03-15"
    )[:2] == ("number", 100000)
    assert agent.answer_query(
        "hnay cua hang ban dc bao nhiu tien vay", ctx, "2025-03-15"
    )[:2] == ("number", 100000)
    assert agent.answer_query(
        "bua qua cuwar hàng bán đươc bn tiền vaayj", ctx, "2025-03-15"
    )[:2] == ("number", 50000)
    assert agent.answer_query(
        "xem giup 7 ngay gan nhat cua hang ban dc bnhiu tien", ctx, "2025-03-15"
    )[:2] == ("number", 150000)
    assert agent.answer_query("so bill hnay la bnhiu", ctx, "2025-03-15")[:2] == (
        "number",
        2,
    )
    assert agent.answer_query(
        "check giùm bữa qua cuwar hàng có bao nhieeu bill", ctx, "2025-03-15"
    )[:2] == ("number", 1)
    assert agent.answer_query("bua qua co tong cong bn don ha", ctx, "2025-03-15")[
        :2
    ] == ("number", 1)
    assert agent.answer_query("hnay cửa hangf có nhiêu ddown", ctx, "2025-03-15")[
        :2
    ] == ("number", 2)

    # Peak revenue day variants (incl. dthu/revenue before ngay nao + nhiu + Telex)
    assert agent.answer_query(
        "dthu ngày nào cao nhất trong tháng 3 năm 2025", ctx, "2025-04-01"
    )[:2] == ("date", "2025-03-06")
    assert agent.answer_query(
        "revenue ngay nao cao nhat trong thang 3 nam 2025", ctx, "2025-04-01"
    )[:2] == ("date", "2025-03-06")
    assert agent.answer_query(
        "thang 3/2025 ngay nao cua hang ban duoc nhiu tien nhat", ctx, "2025-04-01"
    )[:2] == ("date", "2025-03-06")
    assert agent.answer_query(
        "ngày naof trong tháng 3/2025 dthu cao nhaats", ctx, "2025-04-01"
    )[:2] == ("date", "2025-03-06")

    # AOV with bill & Telex mooix
    assert agent.answer_query(
        "tháng 3/2025 doanh thu bình quân mỗi bill là bnhiu", ctx, "2025-04-01"
    )[:2] == ("number", 120000)
    assert agent.answer_query(
        "trung bình mooix đơn tháng 3/2025 mang về bao nhiu dthu", ctx, "2025-04-01"
    )[:2] == ("number", 120000)

    # Negative / regression guards against misrouting non-store queries into store total revenue
    sp_rank = agent.answer_query(
        "check giùm sp bán dc nhieeuf nhất trong tháng 3 năm 2025 là chi",
        ctx,
        "2025-04-01",
    )
    assert sp_rank[:2] in {("abstain", None), ("string", "Trà đá")}
    unrec_item = agent.answer_query(
        "thang 3/2025 mon tom nuong moi thu ve bao nhieu tien",
        ctx,
        "2025-04-01",
    )
    assert unrec_item[:2] == ("abstain", None)
    bill_today = agent.answer_query(
        "tính riêng ngày hôm nay, cửa hàng bán được bao nhiu bill",
        ctx,
        "2025-03-15",
    )
    assert bill_today[:2] in {("abstain", None), ("number", 2)}
    order_today = agent.answer_query(
        "tinh rieng ngay hom nay, cua hang ban dc bao nhiu don hang",
        ctx,
        "2025-03-15",
    )
    assert order_today[:2] in {("abstain", None), ("number", 2)}

    # Flat single-table MTD growth with Telex hown/cungf
    flat_sales = pd.DataFrame(
        [
            {
                "ma_don": "A1",
                "ngay": "2025-07-05",
                "trang_thai": "Hoàn thành",
                "thanh_tien": 100000,
            },
            {
                "ma_don": "A2",
                "ngay": "2025-07-25",
                "trang_thai": "Hoàn thành",
                "thanh_tien": 900000,
            },
            {
                "ma_don": "A3",
                "ngay": "2025-08-05",
                "trang_thai": "Hoàn thành",
                "thanh_tien": 150000,
            },
        ]
    )
    ctx_mtd = _make_context("priv_p15_mtd", {"so_ban_hang": flat_sales})
    ctx_mtd.readme = (
        "### Cột trong `so_ban_hang.xlsx`\n"
        "- **ma_don**: mã đơn\n"
        "- **ngay**: ngày bán\n"
        "- **trang_thai**: trạng thái đơn\n"
    )
    assert agent.answer_query(
        "từ đầu tháng này đến hết bữa nay, dthu hown hay kém cungf kỳ th trước mấy %",
        ctx_mtd,
        "2025-08-10",
    )[:2] == ("percent", 50.0)


@pytest.mark.slow
@pytest.mark.skipif(
    not TRAIN_DATA_DIR.exists(), reason="Train data directory unavailable"
)
def test_p15_real_train_twins() -> None:
    """Real training-twin verification for P1.5."""
    from zaic_2026_datamate.pipeline import build_agent, production_pipeline_config

    loader = DataLoader(TRAIN_DATA_DIR)
    agent = build_agent(production_pipeline_config())
    cases: list[tuple[str, str, str, str, object]] = [
        (
            "biz_129",
            "Cho tôi biết doanh thu tháng 3/2026.",
            "2026-08-14",
            "number",
            1016912500,
        ),
        (
            "biz_111",
            "Tháng 9/2025 cửa hàng bán được tổng cộng bao nhiêu tiền?",
            "2026-08-19",
            "number",
            229934500,
        ),
        (
            "biz_124",
            "dthu 3 tháng liền nhau kết thúc ở tháng 6/2025 là bao nhiêu mỗi tháng, từ tháng cũ đến tháng mới",
            "2026-05-25",
            "list",
            [287435200, 346554700, 309693800],
        ),
        (
            "biz_103",
            "dthu 3 tháng liền nhau kết thúc ở tháng 12/2025 là bao nhiêu mỗi tháng, từ tháng cũ đến tháng mới?",
            "2026-07-09",
            "list",
            [173866200, 156289700, 158205600],
        ),
        (
            "biz_113",
            "tong revenue trong ngay hom nay la bao nhiu",
            "2025-12-29",
            "number",
            30226400,
        ),
        (
            "biz_113",
            "revenue ngay nao cao nhat trong thang 10 nam 2025",
            "2026-08-09",
            "date",
            "2025-10-04",
        ),
        (
            "biz_114",
            "thang 2/2026 ngay nao cua hang ban dc nhiu tien nhat",
            "2026-06-21",
            "date",
            "2026-02-05",
        ),
        (
            "biz_107",
            "dthu ngày nào cao nhất trong tháng 6 năm 2025",
            "2026-07-22",
            "date",
            "2025-06-23",
        ),
        (
            "biz_130",
            "từ đầu tháng này đến hết bữa nay, dthu hown hay kém cungf kỳ th trước mấy %",
            "2025-08-20",
            "percent",
            -0.8,
        ),
        (
            "biz_111",
            "tháng 8/2025 doanh thu bình quân mỗi bill là bnhiu",
            "2026-08-19",
            "number",
            69726,
        ),
        (
            "biz_111",
            "trung bình mooix đơn tháng 6/2026 mang về bao nhiu dthu",
            "2026-08-19",
            "number",
            72752,
        ),
        (
            "biz_102",
            "check giùm bữa qua cuwar hàng có bao nhieeu bill",
            "2025-06-26",
            "number",
            46,
        ),
        ("biz_116", "bua qua co tong cong bn don ha", "2025-10-08", "number", 31),
        (
            "biz_125",
            "thang 4/2025 cua hang ban dc tong cong bn tien",
            "2026-05-11",
            "number",
            629820000,
        ),
        (
            "biz_116",
            "thang 2/2026 cua hang ban dc total bao nhieu tien",
            "2026-09-03",
            "number",
            1069913200,
        ),
    ]
    ctx_cache: dict[str, BusinessContext] = {}
    for bid, q, as_of, exp_t, exp_v in cases:
        if bid not in ctx_cache:
            ctx_cache[bid] = loader.load_business(bid)
        res = agent.answer_query(q, ctx_cache[bid], as_of_date=as_of)
        assert res[:2] == (exp_t, exp_v), (
            f"{bid}: got {res[:2]} expected {(exp_t, exp_v)} for {q}"
        )


def test_p16_ban_hang_columns_pharmacy_bestseller_and_exact_unit_qty() -> None:
    """Slice P1.6: ban_hang_* unit aliases, percentage drug names, tie-safe bestseller, and storewide base_unit qty."""
    from zaic_2026_datamate.pipeline import build_agent, production_pipeline_config

    agent = build_agent(production_pipeline_config())

    def _build_pharma_ctx(include_base_qty_col: bool) -> BusinessContext:
        hoa_don = pd.DataFrame(
            {
                "order_id": [
                    "O1",
                    "O2",
                    "O3",
                    "O4",
                    "O5",
                    "O_TIE1",
                    "O_TIE2",
                    "O_CANC",
                ],
                "ngay_ban": [
                    "2025-10-05",
                    "2025-10-06",
                    "2025-10-14",
                    "2025-10-14",
                    "2025-10-14",
                    "2025-11-01",
                    "2025-11-02",
                    "2025-10-07",
                ],
                "trang_thai": [
                    "Hoàn thành",
                    "Hoàn thành",
                    "Hoàn thành",
                    "Hoàn thành",
                    "Hoàn thành",
                    "Hoàn thành",
                    "Hoàn thành",
                    "Đã hủy",
                ],
                "tong_tien": [
                    150000,
                    35000,
                    2900000,
                    120000,
                    80000,
                    50000,
                    50000,
                    999000,
                ],
                "da_thu": [150000, 35000, 2900000, 120000, 80000, 50000, 50000, 0],
                "con_no": [0, 0, 0, 0, 0, 0, 0, 0],
            }
        )
        chi_tiet_data: dict[str, list[object]] = {
            "ma_giao_dich": [
                "O1",
                "O2",
                "O3",
                "O4",
                "O5",
                "O_TIE1",
                "O_TIE2",
                "O_CANC",
            ],
            "ma_hang": [
                "SP0047",
                "SP0047",
                "SP0038",
                "SP0002",
                "SP0010",
                "SP0038",
                "SP0002",
                "SP0047",
            ],
            "chi_tiet_hang": [
                "Zyrtec 10mg hộp 1 vỉ x 10 viên",
                "Zyrtec 10mg hộp 1 vỉ x 10 viên",
                "Otrivin 0,1% xịt mũi chai 10ml",
                "Hapacol 250 hộp 24 gói",
                "Smecta hộp 30 gói",
                "Otrivin 0,1% xịt mũi chai 10ml",
                "Hapacol 250 hộp 24 gói",
                "Zyrtec 10mg hộp 1 vỉ x 10 viên",
            ],
            "slg": [2, 5, 30, 1, 10, 5, 5, 9],
            "d": ["hộp", "viên", "chai", "hộp", "gói", "chai", "gói", "hộp"],
            "gia_niem_yet": [75000, 7500, 58000, 120000, 8000, 58000, 8000, 75000],
            "thanh_tien": [
                150000,
                35000,
                1740000,
                120000,
                80000,
                290000,
                40000,
                675000,
            ],
        }
        if include_base_qty_col:
            chi_tiet_data["sl_co_ban"] = [20, 5, 30, 24, 10, 5, 5, 90]
        chi_tiet = pd.DataFrame(chi_tiet_data)
        quy_doi = pd.DataFrame(
            {
                "ma_sp": ["SP0047", "SP0002"],
                "don_vi": ["hộp", "hộp"],
                "bang": [10, 24],
                "gia_ban_theo_don_vi_quy_doi": [75000, 120000],
            }
        )
        hang_hoa = pd.DataFrame(
            {
                "ma_sp": ["SP0047", "SP0038", "SP0002", "SP0010"],
                "mon": [
                    "Zyrtec 10mg hộp 1 vỉ x 10 viên",
                    "Otrivin 0,1% xịt mũi chai 10ml",
                    "Hapacol 250 hộp 24 gói",
                    "Smecta hộp 30 gói",
                ],
                "don_vi": ["viên", "chai", "gói", "gói"],
            }
        )
        thuoc = pd.DataFrame(
            {
                "ma_hh": ["SP0047", "SP0038", "SP0002", "SP0010"],
                "hoat_chat": [
                    "Cetirizin",
                    "Xylometazolin",
                    "Paracetamol",
                    "Diosmectit",
                ],
                "ke_don": [False, False, False, False],
            }
        )
        return _make_context(
            "test_pharma",
            {
                "ban_hang_hoa_don": hoa_don,
                "ban_hang_chi_tiet_hoa_don": chi_tiet,
                "ban_hang_don_vi_quy_doi": quy_doi,
                "ban_hang_hang_hoa": hang_hoa,
                "ban_hang_thuoc": thuoc,
            },
        )

    for has_col in (True, False):
        ctx = _build_pharma_ctx(include_base_qty_col=has_col)
        # 1. Exact base-unit item quantity (sl_co_ban & don_vi_quy_doi fallback, excluding cancelled O_CANC)
        assert agent.answer_query(
            "Tháng 10/2025 cửa hàng bán được tổng cộng bao nhiêu viên Zyrtec 10mg hộp 1 vỉ x 10 viên?",
            ctx,
            as_of_date="2025-11-10",
        )[:2] == ("number", 25)
        # 2. Item with '%' in name (0,1%) must NOT be blocked by has_pct_kw
        assert agent.answer_query(
            "Tháng 10/2025 mặt hàng Otrivin 0,1% xịt mũi chai 10ml bán được tổng cộng bao nhiêu chai?",
            ctx,
            as_of_date="2025-11-10",
        )[:2] == ("number", 30)
        # 3. Non-base conversion unit requested for multi-unit item must NOT emit base count 25
        res_box = agent.answer_query(
            "Tháng 10/2025 bán được tổng cộng bao nhiêu hộp Zyrtec 10mg hộp 1 vỉ x 10 viên?",
            ctx,
            as_of_date="2025-11-10",
        )
        assert res_box[0] == "abstain"
        # 4. Storewide base-unit sum ("cộng mọi mặt hàng tính theo gói") across multi-unit Hapacol (24) + Smecta (10)
        assert agent.answer_query(
            "Số gói bán được hôm qua là bao nhiêu, cộng mọi mặt hàng tính theo gói?",
            ctx,
            as_of_date="2025-10-15",
        )[:2] == ("number", 34)
        # 5. Pharmacy bestseller Top-1 & Top-3 using converted base_quantity (Otrivin 30 > Zyrtec 25 > Hapacol 24)
        assert agent.answer_query(
            "Tháng 10/2025 mặt hàng nào bán chạy nhất?",
            ctx,
            as_of_date="2025-11-10",
        )[:2] == ("string", "Otrivin 0,1% xịt mũi chai 10ml")
        assert agent.answer_query(
            "Top 3 sản phẩm bán nhiều nhất tháng 10/2025 là những gì?",
            ctx,
            as_of_date="2025-11-10",
        )[:2] == (
            "list",
            [
                "Otrivin 0,1% xịt mũi chai 10ml",
                "Zyrtec 10mg hộp 1 vỉ x 10 viên",
                "Hapacol 250 hộp 24 gói",
            ],
        )
        # 5b. Telex K-item bestseller and plural-without-K abstain guard
        assert agent.answer_query(
            "cho hỏi 3 mawtj hangf chạy nhất tháng 10/2025 là những mặt hàng nào",
            ctx,
            as_of_date="2025-11-10",
        )[:2] == (
            "list",
            [
                "Otrivin 0,1% xịt mũi chai 10ml",
                "Zyrtec 10mg hộp 1 vỉ x 10 viên",
                "Hapacol 250 hộp 24 gói",
            ],
        )
        assert (
            agent.answer_query(
                "liệt kê các mặt hàng bán chạy nhất tháng 10/2025 là những mặt hàng nào",
                ctx,
                as_of_date="2025-11-10",
            )[0]
            == "abstain"
        )
        # 6. Tie in November (Otrivin 5 vs Hapacol 5) MUST abstain safely
        assert (
            agent.answer_query(
                "Tháng 11/2025 mặt hàng nào bán chạy nhất?",
                ctx,
                as_of_date="2025-12-01",
            )[0]
            == "abstain"
        )
        # 7. Dosage / OOD trap MUST NOT trigger storewide pill count
        res_dosage = agent.answer_query(
            "thuốc có hoạt chất Paracetamol người lớn nên uống mỗi lần mấy viên",
            ctx,
            as_of_date="2025-11-10",
        )
        assert res_dosage[0] != "number"
        # 8. OOD item name must NOT fall back to storewide unit sum
        res_ood_item = agent.answer_query(
            "Tháng 10/2025 cửa hàng bán được tổng cộng bao nhiêu gói Thuốc Lạ Không Tồn Tại?",
            ctx,
            as_of_date="2025-11-10",
        )
        assert res_ood_item[0] != "number"


@pytest.mark.slow
@pytest.mark.skipif(
    not TRAIN_DATA_DIR.exists(), reason="Train data directory unavailable"
)
def test_p16_real_pharmacy_twins() -> None:
    """Real training-twin verification for P1.6."""
    from zaic_2026_datamate.pipeline import build_agent, production_pipeline_config

    loader = DataLoader(TRAIN_DATA_DIR)
    agent = build_agent(production_pipeline_config())
    cases: list[tuple[str, str, str, str, object]] = [
        (
            "biz_119",
            "Tháng 7/2025 mặt hàng Otrivin 0,1% xịt mũi chai 10ml bán được tổng cộng bao nhiêu chai?",
            "2026-07-13",
            "number",
            77,
        ),
        (
            "biz_119",
            "trong thang 4 nam 2025 cua hang ban dc may tuyp voltaren emulgel 1% tuyp 20g",
            "2026-07-13",
            "number",
            55,
        ),
        (
            "biz_119",
            "hqua cửa hàng bán ra tổng cộng mấy gói?",
            "2026-07-02",
            "number",
            220,
        ),
        (
            "biz_119",
            "top 3 sp ban nhieu nhat thang 3 nam 2026 la nhung j z",
            "2026-07-13",
            "list",
            [
                "Khẩu trang y tế 4 lớp Khánh An hộp 50 cái",
                "Prednisolon 5mg Boston chai 500 viên",
                "Hapacol 250 hộp 24 gói",
            ],
        ),
        (
            "biz_124",
            "Tháng 10/2025 cửa hàng bán được tổng cộng bao nhiêu viên Zyrtec 10mg hộp 1 vỉ x 10 viên?",
            "2026-05-25",
            "number",
            389,
        ),
        (
            "biz_124",
            "Tháng 11/2025 mặt hàng nào bán chạy nhất?",
            "2026-05-25",
            "string",
            "Panadol Extra hộp 15 vỉ x 12 viên",
        ),
        (
            "biz_124",
            "Tuần rồi top 5 hàng bán chạy nhất là gì?",
            "2026-01-08",
            "list",
            [
                "Panadol 500mg hộp 10 vỉ x 12 viên",
                "Berberin 100mg Mekophar chai 100 viên",
                "Hapacol 250 hộp 24 gói",
                "Panadol Extra hộp 15 vỉ x 12 viên",
                "Khẩu trang y tế 4 lớp Khánh An hộp 50 cái",
            ],
        ),
        (
            "biz_123",
            "Tháng 1/2025 cửa hàng bán được tổng cộng bao nhiêu viên Gofen 400 hộp 5 vỉ x 10 viên nang?",
            "2026-06-28",
            "number",
            588,
        ),
        (
            "biz_123",
            "5 mặt hàng bán chạy nhất tháng 6/2025 là những mặt hàng nào",
            "2026-06-28",
            "list",
            [
                "Panadol Extra hộp 15 vỉ x 12 viên",
                "Enervon hộp 10 vỉ x 10 viên",
                "Panadol 500mg hộp 10 vỉ x 12 viên",
                "Tiffy Dey hộp 25 vỉ x 4 viên",
                "Amoxicillin 500mg Domesco hộp 10 vỉ x 10 viên",
            ],
        ),
        (
            "biz_120",
            "Số gói bán được hqua là bao nhiêu, cộng mọi mặt hàng tính theo gói?",
            "2025-10-24",
            "number",
            327,
        ),
        (
            "biz_120",
            "cho hỏi 5 mawtj hangf chạy nhất tháng 12/2025 là những mặt hàng nào",
            "2026-06-12",
            "list",
            [
                "Panadol 500mg hộp 10 vỉ x 12 viên",
                "Panadol Extra hộp 15 vỉ x 12 viên",
                "Khẩu trang y tế 4 lớp Khánh An hộp 50 cái",
                "Smecta hộp 30 gói",
                "Hapacol 250 hộp 24 gói",
            ],
        ),
        (
            "biz_116",
            "Cộng mọi mặt hàng tính theo cây, hôm qua bán ra được mấy cây ạ?",
            "2025-09-05",
            "number",
            55,
        ),
        (
            "biz_128",
            "Số lon bán được hôm qua là bao nhiêu, cộng mọi mặt hàng tính theo lon?",
            "2026-03-27",
            "number",
            95,
        ),
        (
            "biz_129",
            "Số lon bán được hôm qua là bao nhiêu, cộng mọi mặt hàng tính theo lon?",
            "2025-02-21",
            "number",
            53,
        ),
    ]
    ctx_cache: dict[str, BusinessContext] = {}
    for bid, q, as_of, exp_t, exp_v in cases:
        if bid not in ctx_cache:
            ctx_cache[bid] = loader.load_business(bid)
        res = agent.answer_query(q, ctx_cache[bid], as_of_date=as_of)
        assert res[:2] == (exp_t, exp_v), (
            f"{bid}: got {res[:2]} expected {(exp_t, exp_v)} for {q}"
        )
    assert (
        agent.answer_query(
            "thuốc có hoạt chất nhôm phosphat người lớn nên uống mỗi lần mấy viên",
            ctx_cache["biz_123"],
            as_of_date="2026-06-28",
        )[0]
        != "number"
    )


def test_p17_active_ingredient_exact_non_rx_and_dosage_unanswerable() -> None:
    """P1.7: exact active_ingredient match (no combo bleed), non-Rx 'khong can don bac si', and dosage OOD."""
    agent = HybridAgent()

    # Schema variant 1: thanh_phan_chinh + ban_theo_don (0/1)
    orders = pd.DataFrame(
        {
            "order_id": ["DH1", "DH2", "DH3", "DH4"],
            "ngay_ban": ["2025-09-05", "2025-09-06", "2025-09-07", "2025-09-08"],
            "tong_tien": [50000.0, 120000.0, 80000.0, 40000.0],
            "trang_thai": ["completed", "completed", "completed", "cancelled"],
        }
    )
    details = pd.DataFrame(
        {
            "order_id": ["DH1", "DH2", "DH3", "DH4"],
            "ma_sp": ["SP1", "SP2", "SP3", "SP1"],
            "mon": ["Hapacol 500", "Panadol Extra", "Amox 500", "Hapacol 500"],
            "sl_co_ban": [1, 2, 1, 1],
            "thanh_tien": [50000.0, 120000.0, 80000.0, 40000.0],
        }
    )
    items = pd.DataFrame(
        {
            "ma_sp": ["SP1", "SP2", "SP3"],
            "mon": ["Hapacol 500", "Panadol Extra", "Amox 500"],
        }
    )
    drugs = pd.DataFrame(
        {
            "ma_hh": ["SP1", "SP2", "SP3"],
            "thanh_phan_chinh": ["Paracetamol", "Paracetamol + Cafein", "Amoxicillin"],
            "ban_theo_don": [0, 0, 1],
        }
    )
    pharma_ctx = _make_context(
        "synth_pharma_p17",
        {
            "ban_hang_hoa_don": orders,
            "ban_hang_chi_tiet_hoa_don": details,
            "ban_hang_hang_hoa": items,
            "ban_hang_thuoc": drugs,
        },
    )

    # Exact Paracetamol excludes 'Paracetamol + Cafein' and cancelled DH4 (50,000 not 170,000 or 210,000)
    assert agent.answer_query(
        "Thuốc có hoạt chất Paracetamol tháng 9/2025 bán được tổng cộng bao nhiêu tiền?",
        pharma_ctx,
        as_of_date="2025-09-30",
    )[:2] == ("number", 50000)

    # Exact combo active ingredient matches only combo SKU
    assert agent.answer_query(
        "Doanh thu thuốc hoạt chất Paracetamol + Cafein tháng 9/2025 là bao nhiêu?",
        pharma_ctx,
        as_of_date="2025-09-30",
    )[:2] == ("number", 120000)

    # Non-Rx 'khong can don bac si' excludes ban_theo_don=1 (50k + 120k = 170k)
    assert agent.answer_query(
        "Tháng 9/2025 bán hàng không cần đơn bác sĩ được bao nhiêu tiền?",
        pharma_ctx,
        as_of_date="2025-09-30",
    )[:2] == ("number", 170000)

    # Unknown active ingredient -> safe abstain (never guess 0)
    assert (
        agent.answer_query(
            "Thuốc có hoạt chất Cetirizin tháng 9/2025 bán được bao nhiêu tiền?",
            pharma_ctx,
            as_of_date="2025-09-30",
        )[0]
        == "abstain"
    )

    # Dosage / clinical usage question -> Gate 1 unanswerable
    assert agent.answer_query(
        "thuốc có hoạt chất paracetamol người lớn nên uống mỗi lần mấy viên",
        pharma_ctx,
        as_of_date="2025-09-30",
    )[:2] == ("unanswerable", None)

    # Guard against 'tang truong bao nhieu' matching 'uong bao nhieu' without word boundaries
    from zaic_2026_datamate.semantic_contract import (
        BusinessSemanticContract,
        build_query_plan,
    )

    pharma_contract = BusinessSemanticContract.from_context(pharma_ctx)
    yoy_plan = build_query_plan(
        "Doanh thu tháng 3 năm 2026 tăng trưởng bao nhiêu % so với tháng 3 năm 2025?",
        pharma_ctx,
        "2026-07-13",
        pharma_contract,
    )
    assert yoy_plan is None or yoy_plan.operation != "unanswerable_gate"

    # Non-pharmacy store asked about active ingredient -> Gate 1 unanswerable
    cafe_ctx = _make_context(
        "synth_cafe_p17",
        {
            "hoa_don": pd.DataFrame(
                {
                    "ma_hd": ["H1"],
                    "ngay": ["2025-09-05"],
                    "tong_tien": [30000.0],
                    "trang_thai": ["completed"],
                }
            ),
            "chi_tiet": pd.DataFrame(
                {
                    "ma_hd": ["H1"],
                    "ten_mon": ["Trà đào"],
                    "so_luong": [1],
                    "thanh_tien": [30000.0],
                }
            ),
        },
    )
    assert agent.answer_query(
        "Thuốc có hoạt chất Paracetamol tháng 9/2025 bán được bao nhiêu tiền?",
        cafe_ctx,
        as_of_date="2025-09-30",
    )[:2] == ("unanswerable", None)


@pytest.mark.slow
def test_p17_real_pharmacy_active_ingredient_twins() -> None:
    """P1.7: verify active_ingredient, non-Rx 'khong can don bac si', and dosage unanswerable across 6 pharmacies."""
    from zaic_2026_datamate.pipeline import build_agent, production_pipeline_config

    if not TRAIN_DATA_DIR.exists():
        pytest.skip("Train data not present")
    loader = DataLoader(TRAIN_DATA_DIR)
    agent = build_agent(production_pipeline_config())

    cases = [
        (
            "biz_119",
            "Doanh thu thuốc hoạt chất Paracetamol tháng 9/2025 là bao nhiêu?",
            "2026-06-30",
            "number",
            4444000,
        ),
        (
            "biz_119",
            "thang 4/2025 cac thuoc co hoat chat paracetamol + phenylephrin + clorpheniramin ban duoc bnhiu tien",
            "2026-06-30",
            "number",
            3013600,
        ),
        (
            "biz_120",
            "Thuốc có hoạt chất Ibuprofen tháng 9/2025 bán được tổng cộng bao nhiêu tiền?",
            "2026-06-30",
            "number",
            10351400,
        ),
        (
            "biz_121",
            "thang 3/2026 ban hang khong can don bac si duoc bao nhieu tien z",
            "2026-08-18",
            "number",
            252133800,
        ),
        (
            "biz_122",
            "dthu thuốc hoạt chất Paracetamol tháng 10/2025 là bao nhiêu?",
            "2026-06-30",
            "number",
            7485600,
        ),
        (
            "biz_123",
            "thuoc co hoat chat ibuprofen thang 12/2025 ban dc tong cong bn tien",
            "2026-06-30",
            "number",
            6720000,
        ),
        (
            "biz_123",
            "thuốc có hoạt chất nhôm phosphat người lớn nên uống mỗi lần mấy viên",
            "2026-06-28",
            "unanswerable",
            None,
        ),
        (
            "biz_124",
            "Tháng 9/2025 bán hàng không cần đơn bác sĩ được bao nhiêu tiền?",
            "2026-06-30",
            "number",
            292217500,
        ),
        (
            "biz_124",
            "thuốc có hoạt chất loratadin thangs 6/2025 bán được tổng cộng bnhiu tiền",
            "2026-06-30",
            "number",
            3175400,
        ),
    ]
    ctx_cache: dict[str, BusinessContext] = {}
    for bid, q, as_of, exp_t, exp_v in cases:
        if bid not in ctx_cache:
            ctx_cache[bid] = loader.load_business(bid)
        res = agent.answer_query(q, ctx_cache[bid], as_of_date=as_of)
        assert res[:2] == (exp_t, exp_v), (
            f"{bid}: got {res[:2]} expected {(exp_t, exp_v)} for {q}"
        )
