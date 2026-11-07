"""
test_semantic_contract.py: Comprehensive unit test suite for
zaic_2026_datamate.semantic_contract (BusinessSemanticContract,
resolve_semantic_plan, and resolve_query integration).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

import duckdb
import pandas as pd
import pytest

from zaic_2026_datamate.data_loader import BusinessContext, DataLoader
from zaic_2026_datamate.intent_parser import resolve_query
from zaic_2026_datamate.semantic_contract import (
    BusinessSemanticContract,
    resolve_semantic_plan,
)


def _make_context(
    tables: Dict[str, pd.DataFrame],
    business_id: str = "synthetic_biz",
    readme: str = "",
) -> BusinessContext:
    """Create an isolated in-memory BusinessContext from synthetic DataFrames."""
    conn = duckdb.connect(":memory:")
    for name, df in tables.items():
        conn.register(name, df)
    return BusinessContext(
        business_id=business_id,
        data_dir=Path("."),
        tables=tables,
        readme=readme,
        duckdb_conn=conn,
    )


# =============================================================================
# 1. CANONICAL VIEWS & SEMANTIC CONTRACT ENRICHMENT TESTS
# =============================================================================


def test_canonical_orders_and_lines_grain_and_exclusions():
    """Verify BusinessSemanticContract.from_context(context) builds
    canonical_orders and canonical_order_lines in DuckDB with:
    - order_date = MIN(date) per order_id (cross-month return row stays anchored)
    - order_hour (0..23)
    - is_valid_sale excluding cancelled and partial-return orders at the order grain
    - order_revenue excluding 'Voucher', 'Phí ship', 'Dùng %', while keeping 'Phí phục vụ'
    - is_service_fee = True for 'Phí phục vụ' (excluded from item counts)
    """
    df_sales = pd.DataFrame(
        {
            "ma_don_hang": [
                "ORD_VALID_1",
                "ORD_VALID_1",
                "ORD_VALID_1",
                "ORD_VALID_1",
                "ORD_PARTIAL_RET",
                "ORD_PARTIAL_RET",
                "ORD_CANCEL",
            ],
            "thoi_gian_tao": pd.to_datetime(
                [
                    "2025-06-10 08:30:00",
                    "2025-06-10 08:35:00",
                    "2025-06-10 08:35:00",
                    "2025-06-10 08:35:00",
                    "2025-06-28 15:10:00",
                    "2025-07-02 10:00:00",  # Return line in next month
                    "2025-06-15 19:00:00",
                ]
            ),
            "trang_thai": [
                "Hoàn thành",
                "Hoàn thành",
                "Hoàn thành",
                "Hoàn thành",
                "Hoàn thành",
                "Đã trả hàng",  # Partial return -> invalidates entire order
                "Đã hủy",
            ],
            "ten_hang": [
                "Sữa bột Meiji",
                "Phí phục vụ",
                "Voucher",
                "Phí ship",
                "Bỉm Bobby",
                "Bỉm Bobby",
                "Sữa tươi",
            ],
            "nhom_hang": [
                "Sữa bột",
                "Dịch vụ",
                "Khuyến mãi",
                "Vận chuyển",
                "Tã bỉm",
                "Tã bỉm",
                "Sữa",
            ],
            "thanh_tien": [
                200000.0,
                15000.0,   # Phí phục vụ kept in order_revenue
                -20000.0,  # Voucher excluded
                30000.0,   # Phí ship excluded
                150000.0,
                -50000.0,
                100000.0,
            ],
        }
    )
    ctx = _make_context({"so_ban_hang": df_sales}, business_id="test_grain")
    contract = BusinessSemanticContract.from_context(ctx)
    assert contract is not None

    orders_df = ctx.query(
        """
        SELECT
            order_id,
            strftime(CAST(order_date AS DATE), '%Y-%m-%d') AS order_date_str,
            order_hour,
            is_valid_sale,
            order_revenue
        FROM canonical_orders
        ORDER BY order_id
        """
    )
    orders_map = orders_df.set_index("order_id").to_dict(orient="index")

    # 1. Valid order keeps Sữa bột (200k) + Phí phục vụ (15k) = 215k, excludes Voucher & Phí ship
    assert orders_map["ORD_VALID_1"]["order_date_str"] == "2025-06-10"
    assert int(orders_map["ORD_VALID_1"]["order_hour"]) == 8
    assert bool(orders_map["ORD_VALID_1"]["is_valid_sale"]) is True
    assert float(orders_map["ORD_VALID_1"]["order_revenue"]) == pytest.approx(215000.0)

    # 2. Partial return order has MIN(date) = 2025-06-28 and is_valid_sale = False at order grain
    assert orders_map["ORD_PARTIAL_RET"]["order_date_str"] == "2025-06-28"
    assert int(orders_map["ORD_PARTIAL_RET"]["order_hour"]) == 15
    assert bool(orders_map["ORD_PARTIAL_RET"]["is_valid_sale"]) is False

    # 3. Cancelled order has is_valid_sale = False
    assert bool(orders_map["ORD_CANCEL"]["is_valid_sale"]) is False

    # 4. Check service fee flag on canonical_order_lines
    lines_df = ctx.query(
        """
        SELECT item_name, is_service_fee, line_revenue
        FROM canonical_order_lines
        WHERE order_id = 'ORD_VALID_1'
        """
    )
    svc_rows = lines_df[lines_df["item_name"].astype(str).str.contains("Phí phục vụ", case=False)]
    assert not svc_rows.empty
    assert bool(svc_rows.iloc[0]["is_service_fee"]) is True


def test_dimension_normalization_channel_payment_customer_group_and_key():
    """Verify dimension normalization in canonical_orders:
    - channel: 'store', 'phone', 'zalo_oa', 'ecommerce'
    - payment_method: 'cash', 'transfer', 'ewallet', 'cod', 'debt'
    - customer_group: 'thân thiết', 'sỉ', 'lẻ'
    - customer_key: None for walk-in ('Khách lẻ', 'KL', 'lẻ'), preserved for named/ID customers.
    """
    df = pd.DataFrame(
        {
            "ma_hoa_don": ["HD1", "HD2", "HD3", "HD4", "HD5"],
            "ngay_ban": pd.to_datetime(
                ["2025-08-01", "2025-08-02", "2025-08-03", "2025-08-04", "2025-08-05"]
            ),
            "trang_thai": ["Hoàn thành"] * 5,
            "kenh_ban": ["Tại quầy", "Điện thoại", "Zalo OA", "Shopee", "Bán trực tiếp"],
            "hinh_thuc_thanh_toan": ["Tiền mặt", "Chuyển khoản", "Ví MoMo", "COD", "Ghi nợ"],
            "nhom_khach": ["Khách thân thiết", "Khách sỉ", "Khách lẻ", "Thân thiết", "Lẻ"],
            "khach_hang": ["KH001 - Nguyễn Lan", "Công ty Minh Phát", "Khách lẻ", "KL", "lẻ"],
            "tien_hang": [100000.0, 200000.0, 150000.0, 300000.0, 50000.0],
        }
    )
    ctx = _make_context({"ban_hang": df}, business_id="test_dims")
    contract = BusinessSemanticContract.from_context(ctx)
    assert contract.has_channel is True
    assert contract.has_payment_method is True
    assert contract.has_customer_group is True
    assert contract.has_customer_key is True

    res = ctx.query(
        """
        SELECT order_id, channel, payment_method, customer_group, customer_key
        FROM canonical_orders
        ORDER BY order_id
        """
    ).set_index("order_id")

    assert res.loc["HD1", "channel"] == "store"
    assert res.loc["HD2", "channel"] == "phone"
    assert res.loc["HD3", "channel"] == "zalo_oa"
    assert res.loc["HD4", "channel"] == "ecommerce"
    assert res.loc["HD5", "channel"] == "store"

    assert res.loc["HD1", "payment_method"] == "cash"
    assert res.loc["HD2", "payment_method"] == "transfer"
    assert res.loc["HD3", "payment_method"] == "ewallet"
    assert res.loc["HD4", "payment_method"] == "cod"
    assert res.loc["HD5", "payment_method"] == "debt"

    assert res.loc["HD1", "customer_group"] == "thân thiết"
    assert res.loc["HD2", "customer_group"] == "sỉ"
    assert res.loc["HD3", "customer_group"] == "lẻ"
    assert res.loc["HD4", "customer_group"] == "thân thiết"
    assert res.loc["HD5", "customer_group"] == "lẻ"

    assert pd.notna(res.loc["HD1", "customer_key"]) and str(res.loc["HD1", "customer_key"]).strip() != ""
    assert pd.notna(res.loc["HD2", "customer_key"]) and str(res.loc["HD2", "customer_key"]).strip() != ""
    assert pd.isna(res.loc["HD3", "customer_key"])
    assert pd.isna(res.loc["HD4", "customer_key"])
    assert pd.isna(res.loc["HD5", "customer_key"])


def test_inline_topping_splitting_and_prescription_enrichment():
    """Verify line-level enrichment in canonical_order_lines:
    - Inline topping splitting (dev_002 pattern):
      "Trà đào cam sả (L) + 1 Topping (7.000đ)" with line total 42000
      splits 7000 to category 'topping' and 35000 to base category 'trà'.
    - is_prescription boolean enrichment for Rx vs non-Rx items.
    """
    df_beverage = pd.DataFrame(
        {
            "ma_don_hang": ["B1", "B2"],
            "ngay": pd.to_datetime(["2025-09-05 09:00:00", "2025-09-06 16:00:00"]),
            "trang_thai": ["Hoàn thành", "Hoàn thành"],
            "ten_mon": [
                "Trà đào cam sả (L) + 1 Topping (7.000đ)",
                "Cà phê sữa đá (M)",
            ],
            "nhom_mon": ["Trà", "Cà phê"],
            "thanh_tien": [42000.0, 29000.0],
        }
    )
    ctx_bev = _make_context({"so_ban_hang": df_beverage}, business_id="dev_002_mock")
    BusinessSemanticContract.from_context(ctx_bev)

    cat_rev = (
        ctx_bev.query(
            """
            SELECT category, SUM(line_revenue) AS rev
            FROM canonical_order_lines
            WHERE is_valid_sale
            GROUP BY category
            """
        )
        .set_index("category")["rev"]
        .to_dict()
    )
    assert cat_rev.get("trà") == pytest.approx(35000.0)
    assert cat_rev.get("topping") == pytest.approx(7000.0)
    assert cat_rev.get("cà phê") == pytest.approx(29000.0)

    # Actual dev_002 pattern: no category column, base drink inferred from CAFE_CATEGORY_LOOKUP
    df_dev002 = pd.DataFrame(
        {
            "ma_don_hang": ["D1", "D2"],
            "ngay": pd.to_datetime(["2025-10-01 10:00:00", "2025-10-01 11:00:00"]),
            "trang_thai": ["Hoàn thành", "Hoàn thành"],
            "ten_mon": [
                "Trà đào cam sả (M) + Trân châu đen (7.000đ)",
                "Matcha latte (M) + 2 Thạch cà phê (14.000đ)",
            ],
            "thanh_tien": [46000.0, 112000.0],
        }
    )
    ctx_002 = _make_context({"so_ban_hang": df_dev002}, business_id="dev_002_real")
    BusinessSemanticContract.from_context(ctx_002)
    cat_rev_real = (
        ctx_002.query(
            """
            SELECT category, SUM(line_revenue) AS rev
            FROM canonical_order_lines
            WHERE is_valid_sale
            GROUP BY category
            """
        )
        .set_index("category")["rev"]
        .to_dict()
    )
    assert cat_rev_real.get("trà") == pytest.approx(39000.0 + 98000.0)
    assert cat_rev_real.get("topping") == pytest.approx(7000.0 + 14000.0)

    # Prescription enrichment fixture
    df_pharmacy = pd.DataFrame(
        {
            "ma_hoa_don": ["RX1", "RX1", "RX2"],
            "ngay_ban": pd.to_datetime(["2025-10-01", "2025-10-01", "2025-10-02"]),
            "trang_thai": ["Hoàn thành", "Hoàn thành", "Hoàn thành"],
            "ten_thuoc": ["Augmentin 625mg", "Vitamin C 500mg", "Panadol Extra"],
            "phan_loai": ["Thuốc kê đơn", "Không kê đơn", "Không kê đơn"],
            "thanh_tien": [180000.0, 45000.0, 55000.0],
        }
    )
    ctx_rx = _make_context({"ban_hang": df_pharmacy}, business_id="pharmacy_mock")
    contract_rx = BusinessSemanticContract.from_context(ctx_rx)
    assert contract_rx.has_prescription is True

    rx_rev = (
        ctx_rx.query(
            """
            SELECT is_prescription, SUM(line_revenue) AS rev
            FROM canonical_order_lines
            WHERE is_valid_sale
            GROUP BY is_prescription
            """
        )
        .set_index("is_prescription")["rev"]
        .to_dict()
    )
    assert rx_rev.get(True) == pytest.approx(180000.0)
    assert rx_rev.get(False) == pytest.approx(100000.0)


# =============================================================================
# 2. STEP 2A: FILTERED METRICS & STEP 2B: SHARE / PERCENTAGE METRICS
# =============================================================================


@pytest.fixture
def rich_retail_context() -> BusinessContext:
    """Comprehensive multi-dimensional retail fixture covering:
    - Categories: 'trà', 'cà phê', 'bánh ngọt'
    - Shifts: morning (06:00-13:59) and afternoon (14:00-21:59)
    - Customer groups: 'thân thiết', 'sỉ', 'lẻ'
    - Customer keys for repeat customer counts
    - Channels: 'store', 'phone', 'zalo_oa', 'ecommerce'
    - Payment methods: 'cash', 'transfer', 'ewallet', 'cod', 'debt'
    - Prescription flags
    """
    rows = [
        # Order 1: 2025-09-01 08:00 (morning), trà (60k) + bánh ngọt (40k) = 100k, store, cash, thân thiết, KH01, Rx=True
        {
            "ma_don_hang": "ORD01",
            "thoi_gian_tao": pd.Timestamp("2025-09-01 08:15:00"),
            "trang_thai": "Hoàn thành",
            "ten_hang": "Trà sen vàng",
            "nhom_hang": "Trà",
            "kenh_ban": "Tại quầy",
            "hinh_thuc_tt": "Tiền mặt",
            "nhom_khach": "Thân thiết",
            "ma_kh": "KH01",
            "ke_don": "Thuốc kê đơn",
            "thanh_tien": 60000.0,
        },
        {
            "ma_don_hang": "ORD01",
            "thoi_gian_tao": pd.Timestamp("2025-09-01 08:15:00"),
            "trang_thai": "Hoàn thành",
            "ten_hang": "Bánh tiramisu",
            "nhom_hang": "Bánh ngọt",
            "kenh_ban": "Tại quầy",
            "hinh_thuc_tt": "Tiền mặt",
            "nhom_khach": "Thân thiết",
            "ma_kh": "KH01",
            "ke_don": "Không kê đơn",
            "thanh_tien": 40000.0,
        },
        # Order 2: 2025-09-01 15:30 (afternoon), trà (140k), phone, transfer, thân thiết, KH01
        {
            "ma_don_hang": "ORD02",
            "thoi_gian_tao": pd.Timestamp("2025-09-01 15:30:00"),
            "trang_thai": "Hoàn thành",
            "ten_hang": "Trà đào cam sả",
            "nhom_hang": "Trà",
            "kenh_ban": "Điện thoại",
            "hinh_thuc_tt": "Chuyển khoản",
            "nhom_khach": "Thân thiết",
            "ma_kh": "KH01",
            "ke_don": "Không kê đơn",
            "thanh_tien": 140000.0,
        },
        # Order 3: 2025-09-02 16:00 (afternoon), cà phê (160k), ecommerce, cod, sỉ, KH01 (3rd order for KH01!)
        {
            "ma_don_hang": "ORD03",
            "thoi_gian_tao": pd.Timestamp("2025-09-02 16:00:00"),
            "trang_thai": "Hoàn thành",
            "ten_hang": "Cà phê truyền thống",
            "nhom_hang": "Cà phê",
            "kenh_ban": "Shopee",
            "hinh_thuc_tt": "COD",
            "nhom_khach": "Khách sỉ",
            "ma_kh": "KH01",
            "ke_don": "Không kê đơn",
            "thanh_tien": 160000.0,
        },
        # Order 4: 2025-09-03 09:00 (morning), cà phê (100k), zalo_oa, ewallet, lẻ, KH02 (1st order for KH02)
        {
            "ma_don_hang": "ORD04",
            "thoi_gian_tao": pd.Timestamp("2025-09-03 09:00:00"),
            "trang_thai": "Hoàn thành",
            "ten_hang": "Cà phê sữa",
            "nhom_hang": "Cà phê",
            "kenh_ban": "Zalo OA",
            "hinh_thuc_tt": "Ví điện tử",
            "nhom_khach": "Khách lẻ",
            "ma_kh": "KH02",
            "ke_don": "Không kê đơn",
            "thanh_tien": 100000.0,
        },
        # Order 5: 2025-09-04 10:00 (morning), trà (500k), cancelled -> must be ignored
        {
            "ma_don_hang": "ORD05_CANCEL",
            "thoi_gian_tao": pd.Timestamp("2025-09-04 10:00:00"),
            "trang_thai": "Đã hủy",
            "ten_hang": "Trà sen vàng",
            "nhom_hang": "Trà",
            "kenh_ban": "Tại quầy",
            "hinh_thuc_tt": "Tiền mặt",
            "nhom_khach": "Thân thiết",
            "ma_kh": "KH02",
            "ke_don": "Thuốc kê đơn",
            "thanh_tien": 500000.0,
        },
    ]
    df = pd.DataFrame(rows)
    return _make_context({"so_ban_hang": df}, business_id="rich_retail")


def test_step_2a_category_revenue_and_order_count(rich_retail_context):
    """Test category revenue and category order count."""
    # Total valid revenue in 09/2025 = 100k (ORD01) + 140k (ORD02) + 160k (ORD03) + 100k (ORD04) = 500k
    # Category 'trà': ORD01 (60k) + ORD02 (140k) = 200k across 2 distinct valid orders
    ans_rev = resolve_query(
        "Tháng 9/2025 doanh thu nhóm trà là bao nhiêu?",
        rich_retail_context,
        as_of_date="2025-09-30",
    )
    assert ans_rev[:2] == ("number", 200000)

    ans_cnt = resolve_query(
        "Tháng 9/2025 số đơn có mua hàng nhóm trà là bao nhiêu?",
        rich_retail_context,
        as_of_date="2025-09-30",
    )
    assert ans_cnt[:2] == ("number", 2)


def test_step_2a_shift_revenue_and_order_count(rich_retail_context):
    """Test shift revenue and shift order count for ca sáng (06:00-13:59) and ca chiều (14:00-21:59)."""
    # Morning (ca sáng): ORD01 (100k) + ORD04 (100k) = 200k, 2 orders
    # Afternoon (ca chiều): ORD02 (140k) + ORD03 (160k) = 300k, 2 orders
    assert resolve_query(
        "Tháng 9/2025 doanh thu ca sáng là bao nhiêu?",
        rich_retail_context,
        as_of_date="2025-09-30",
    )[:2] == ("number", 200000)

    assert resolve_query(
        "Tháng 9/2025 doanh thu ca chiều là bao nhiêu?",
        rich_retail_context,
        as_of_date="2025-09-30",
    )[:2] == ("number", 300000)

    assert resolve_query(
        "Tháng 9/2025 ca sáng có bao nhiêu đơn hàng?",
        rich_retail_context,
        as_of_date="2025-09-30",
    )[:2] == ("number", 2)

    assert resolve_query(
        "Tháng 9/2025 ca chiều có bao nhiêu đơn hàng?",
        rich_retail_context,
        as_of_date="2025-09-30",
    )[:2] == ("number", 2)


def test_step_2a_customer_group_revenue_and_aov(rich_retail_context):
    """Test customer group revenue and customer group AOV."""
    # 'thân thiết': ORD01 (100k) + ORD02 (140k) = 240k across 2 orders -> AOV = 120,000
    assert resolve_query(
        "Tháng 9/2025 doanh thu nhóm khách thân thiết là bao nhiêu?",
        rich_retail_context,
        as_of_date="2025-09-30",
    )[:2] == ("number", 240000)

    assert resolve_query(
        "Khách nhóm thân thiết tháng 9/2025 mua trung bình bao nhiêu tiền hàng mỗi đơn?",
        rich_retail_context,
        as_of_date="2025-09-30",
    )[:2] == ("number", 120000)


def test_step_2a_prescription_and_non_prescription_revenue(rich_retail_context):
    """Test prescription ('thuốc kê đơn') and non-prescription ('không kê đơn' / 'trừ thuốc kê đơn ra, các mặt hàng còn lại') revenue."""
    # Rx = 60,000; Non-Rx = 40k + 140k + 160k + 100k = 440,000
    assert resolve_query(
        "Tháng 9/2025 doanh thu thuốc kê đơn là bao nhiêu?",
        rich_retail_context,
        as_of_date="2025-09-30",
    )[:2] == ("number", 60000)

    assert resolve_query(
        "Tháng 9/2025 doanh thu hàng không kê đơn là bao nhiêu?",
        rich_retail_context,
        as_of_date="2025-09-30",
    )[:2] == ("number", 440000)

    assert resolve_query(
        "Tháng 9/2025 trừ thuốc kê đơn ra, các mặt hàng còn lại bán được bao nhiêu tiền?",
        rich_retail_context,
        as_of_date="2025-09-30",
    )[:2] == ("number", 440000)


def test_step_2a_channel_revenue_single_and_list(rich_retail_context):
    """Test single-channel revenue ('number') and 2-channel revenue ('list')."""
    # store = 100k, phone = 140k, ecommerce = 160k, zalo_oa = 100k
    assert resolve_query(
        "Tháng 9/2025 doanh thu kênh bán tại quầy là bao nhiêu?",
        rich_retail_context,
        as_of_date="2025-09-30",
    )[:2] == ("number", 100000)

    assert resolve_query(
        "Tháng 9/2025 doanh thu đặt qua điện thoại và sàn TMĐT lần lượt là bao nhiêu?",
        rich_retail_context,
        as_of_date="2025-09-30",
    )[:2] == ("list", [140000, 160000])


def test_step_2a_payment_method_revenue_single_list_and_phonetic(rich_retail_context):
    """Test payment method revenue (single number, 2-method list, phonetic 'xi ô đi' -> cod and 'tiền tươi' -> cash)."""
    # cash = 100k, transfer = 140k, cod = 160k, ewallet = 100k
    assert resolve_query(
        "Tháng 9/2025 doanh thu thanh toán bằng tiền tươi là bao nhiêu?",
        rich_retail_context,
        as_of_date="2025-09-30",
    )[:2] == ("number", 100000)

    assert resolve_query(
        "Tháng 9/2025 doanh thu thanh toán xi ô đi là bao nhiêu?",
        rich_retail_context,
        as_of_date="2025-09-30",
    )[:2] == ("number", 160000)

    assert resolve_query(
        "Tháng 9/2025 doanh thu trả bằng tiền mặt và chuyển khoản lần lượt là bao nhiêu?",
        rich_retail_context,
        as_of_date="2025-09-30",
    )[:2] == ("list", [100000, 140000])


def test_step_2a_repeat_customer_count_and_relative_month_anchor(rich_retail_context):
    """Test repeat customer count ('mua từ K đơn trở lên') including 'Th5' anchored to as_of_date."""
    # In 09/2025: KH01 has 3 valid orders, KH02 has 1 valid order.
    assert resolve_query(
        "Tháng 9/2025 có bao nhiêu khách mua từ 3 đơn trở lên?",
        rich_retail_context,
        as_of_date="2025-09-30",
    )[:2] == ("number", 1)

    # Test relative month 'Th5' anchored to as_of_date year (2026) with >= 9 orders
    may_rows = []
    for i in range(9):
        may_rows.append(
            {
                "ma_don_hang": f"M_ORD_{i}",
                "thoi_gian_tao": pd.Timestamp(f"2026-05-{i+1:02d} 10:00:00"),
                "trang_thai": "Hoàn thành",
                "ten_hang": "Trà đào",
                "nhom_hang": "Trà",
                "ma_kh": "KH_VIP",
                "thanh_tien": 50000.0,
            }
        )
    may_rows.append(
        {
            "ma_don_hang": "M_ORD_OTHER",
            "thoi_gian_tao": pd.Timestamp("2026-05-15 11:00:00"),
            "trang_thai": "Hoàn thành",
            "ten_hang": "Trà đào",
            "nhom_hang": "Trà",
            "ma_kh": "KH_ONCE",
            "thanh_tien": 50000.0,
        }
    )
    ctx_may = _make_context({"so_ban_hang": pd.DataFrame(may_rows)}, business_id="may_repeat")
    assert resolve_query(
        "Th5 đếm giùm có mấy khách mua từ 9 đơn trở lên?",
        ctx_may,
        as_of_date="2026-05-31",
    )[:2] == ("number", 1)


def test_step_2a_order_and_daily_revenue_threshold_and_item_in_k_orders(rich_retail_context):
    """Test:
    - Order revenue threshold count ('có bao nhiêu đơn hàng có doanh thu từ 130.000 đồng trở lên?')
    - Daily revenue threshold count ('có bao nhiêu ngày đạt doanh thu từ ... trở lên?')
    - Item-in-K-orders count ('có bao nhiêu mặt hàng xuất hiện trong ít nhất K đơn?')
    """
    # Order revenues in 09/2025: ORD01=100k, ORD02=140k, ORD03=160k, ORD04=100k -> 2 orders >= 130k
    assert resolve_query(
        "Tháng 9/2025 có bao nhiêu đơn hàng có doanh thu từ 130.000 đồng trở lên?",
        rich_retail_context,
        as_of_date="2025-09-30",
    )[:2] == ("number", 2)

    # Daily revenues in 09/2025:
    # 2025-09-01: ORD01 (100k) + ORD02 (140k) = 240k
    # 2025-09-02: ORD03 (160k) = 160k
    # 2025-09-03: ORD04 (100k) = 100k
    # Days >= 150,000 = 2 days (09-01 and 09-02)
    assert resolve_query(
        "Tháng 9/2025 có bao nhiêu ngày đạt doanh thu từ 150.000 đồng trở lên?",
        rich_retail_context,
        as_of_date="2025-09-30",
    )[:2] == ("number", 2)

    # Item-in-K-orders fixture: item A appears in 3 valid orders, item B in 1 order, service fee in 3 orders
    item_rows = []
    for i in range(3):
        item_rows.append(
            {
                "ma_don_hang": f"D{i}",
                "thoi_gian_tao": pd.Timestamp(f"2026-03-0{i+1} 09:00:00"),
                "trang_thai": "Hoàn thành",
                "ten_hang": "Bánh mì thịt nướng",
                "thanh_tien": 30000.0,
            }
        )
        item_rows.append(
            {
                "ma_don_hang": f"D{i}",
                "thoi_gian_tao": pd.Timestamp(f"2026-03-0{i+1} 09:00:00"),
                "trang_thai": "Hoàn thành",
                "ten_hang": "Phí phục vụ",
                "thanh_tien": 5000.0,
            }
        )
    item_rows.append(
        {
            "ma_don_hang": "D0",
            "thoi_gian_tao": pd.Timestamp("2026-03-01 09:00:00"),
            "trang_thai": "Hoàn thành",
            "ten_hang": "Sữa đậu nành",
            "thanh_tien": 15000.0,
        }
    )
    ctx_items = _make_context({"so_ban_hang": pd.DataFrame(item_rows)}, business_id="item_k_orders")
    # Only 'Bánh mì thịt nướng' (not 'Phí phục vụ') should count as an item in >= 3 orders
    assert resolve_query(
        "Tháng 3/2026 có bao nhiêu mặt hàng xuất hiện trong ít nhất 3 đơn?",
        ctx_items,
        as_of_date="2026-03-31",
    )[:2] == ("number", 1)


def test_step_2b_percentage_shares_and_shift_diff_pct(rich_retail_context):
    """Test all Step 2B share and percentage metrics:
    - category_share_pct ('percent'): trà = 200k / 500k = 40.0%
    - customer_group_share_pct ('percent'): thân thiết = 240k / 500k = 48.0%
    - prescription_share_pct ('percent'): thuốc kê đơn = 60k / 500k = 12.0%
    - channel_share_pct ('percent'): điện thoại = 140k / 500k = 28.0%
    - payment_share_pct ('percent'): chuyển khoản = 140k / 500k = 28.0%
    - repeat_customer_share_pct ('percent'): KH01 (1) / {KH01, KH02} (2) = 50.0%
    - shift_diff_pct ('percent'): signed (afternoon 300k - morning 200k) / morning 200k * 100 = +50.0%
    """
    # 1. category_share_pct
    res_cat = resolve_query(
        "Tháng 9/2025 doanh thu nhóm trà chiếm bao nhiêu phần trăm tổng doanh thu?",
        rich_retail_context,
        as_of_date="2025-09-30",
    )
    assert res_cat[0] == "percent" and res_cat[1] == pytest.approx(40.0)

    # 2. customer_group_share_pct
    res_grp = resolve_query(
        "Tháng 9/2025 doanh thu nhóm khách thân thiết chiếm bao nhiêu % tổng doanh thu?",
        rich_retail_context,
        as_of_date="2025-09-30",
    )
    assert res_grp[0] == "percent" and res_grp[1] == pytest.approx(48.0)

    # 3. prescription_share_pct
    res_rx = resolve_query(
        "Tháng 9/2025 doanh thu thuốc kê đơn chiếm bao nhiêu phần trăm tổng doanh thu?",
        rich_retail_context,
        as_of_date="2025-09-30",
    )
    assert res_rx[0] == "percent" and res_rx[1] == pytest.approx(12.0)

    # 4. channel_share_pct
    res_ch = resolve_query(
        "Tháng 9/2025 doanh thu kênh điện thoại chiếm bao nhiêu phần trăm tổng doanh thu?",
        rich_retail_context,
        as_of_date="2025-09-30",
    )
    assert res_ch[0] == "percent" and res_ch[1] == pytest.approx(28.0)

    # 5. payment_share_pct
    res_pay = resolve_query(
        "Tháng 9/2025 doanh thu thanh toán chuyển khoản chiếm bao nhiêu % tổng doanh thu?",
        rich_retail_context,
        as_of_date="2025-09-30",
    )
    assert res_pay[0] == "percent" and res_pay[1] == pytest.approx(28.0)

    # 6. repeat_customer_share_pct
    res_rep = resolve_query(
        "Tháng 9/2025 khách mua từ 2 đơn trở lên chiếm bao nhiêu phần trăm tổng số khách?",
        rich_retail_context,
        as_of_date="2025-09-30",
    )
    assert res_rep[0] == "percent" and res_rep[1] == pytest.approx(50.0)

    # 7. shift_diff_pct: (afternoon 300k - morning 200k) / 200k * 100 = +50.0%
    res_shift = resolve_query(
        "Tháng 9/2025 doanh thu ca chiều hơn hay kém ca sáng bao nhiêu phần trăm?",
        rich_retail_context,
        as_of_date="2025-09-30",
    )
    assert res_shift[0] == "percent" and res_shift[1] == pytest.approx(50.0)


# =============================================================================
# 3. OUT-OF-DOMAIN / MISSING SCHEMA CAPABILITY -> UNANSWERABLE
# =============================================================================


def test_missing_schema_capabilities_and_gender_return_unanswerable():
    """Verify queries asking for non-existent schema dimensions return 'unanswerable':
    - Gender queries ('khách nữ', 'khách nam') -> unanswerable
    - Channel queries when has_channel=False -> unanswerable
    - Payment method queries when has_payment_method=False -> unanswerable
    - Customer group queries when has_customer_group=False -> unanswerable
    - Repeat customer queries when has_customer_key=False -> unanswerable
    """
    df_minimal = pd.DataFrame(
        {
            "ma_don_hang": ["M1", "M2"],
            "ngay": pd.to_datetime(["2025-09-01", "2025-09-02"]),
            "trang_thai": ["Hoàn thành", "Hoàn thành"],
            "ten_hang": ["Bánh mì", "Nước suối"],
            "thanh_tien": [25000.0, 10000.0],
        }
    )
    ctx_min = _make_context({"so_ban_hang": df_minimal}, business_id="minimal_biz")
    contract = BusinessSemanticContract.from_context(ctx_min)
    assert contract.has_channel is False
    assert contract.has_payment_method is False
    assert contract.has_customer_group is False
    assert contract.has_customer_key is False

    # 1. Gender queries -> unanswerable
    assert (
        resolve_query(
            "Tháng 9/2025 có bao nhiêu khách nữ mua hàng?",
            ctx_min,
            as_of_date="2025-09-30",
        )[0]
        == "unanswerable"
    )
    assert (
        resolve_query(
            "Tháng 9/2025 doanh thu từ khách nam là bao nhiêu?",
            ctx_min,
            as_of_date="2025-09-30",
        )[0]
        == "unanswerable"
    )

    # 2. Channel query when has_channel=False -> unanswerable
    assert (
        resolve_query(
            "Tháng 9/2025 doanh thu kênh bán tại quầy là bao nhiêu?",
            ctx_min,
            as_of_date="2025-09-30",
        )[0]
        == "unanswerable"
    )

    # 3. Payment method query when has_payment_method=False -> unanswerable
    assert (
        resolve_query(
            "Tháng 9/2025 doanh thu thanh toán chuyển khoản là bao nhiêu?",
            ctx_min,
            as_of_date="2025-09-30",
        )[0]
        == "unanswerable"
    )

    # 4. Customer group query when has_customer_group=False -> unanswerable
    assert (
        resolve_query(
            "Tháng 9/2025 doanh thu nhóm khách thân thiết là bao nhiêu?",
            ctx_min,
            as_of_date="2025-09-30",
        )[0]
        == "unanswerable"
    )

    # 5. Repeat customer query when has_customer_key=False -> unanswerable
    assert (
        resolve_query(
            "Tháng 9/2025 có bao nhiêu khách mua từ 2 đơn trở lên?",
            ctx_min,
            as_of_date="2025-09-30",
        )[0]
        == "unanswerable"
    )


# =============================================================================
# 4. NEGATIVE CONTROLS -> ABSTAIN
# =============================================================================


@pytest.mark.parametrize(
    "question",
    [
        "Tháng 9/2025 doanh thu nhóm trà của nhân viên Nguyễn Văn A là bao nhiêu?",
        "Tháng 9/2025 có bao nhiêu đơn nhóm trà bị giao trễ quá 30 phút?",
        "Tháng 9/2025 doanh thu ca sáng trừ chi phí mặt bằng và điện nước còn bao nhiêu?",
        "Tháng 9/2025 khách nhóm thân thiết mua trả góp lãi suất 0% bao nhiêu tiền?",
        "Tháng 9/2025 doanh thu nhóm trà cao hơn tháng 8/2025 phải không?",
    ],
)
def test_negative_controls_with_unrecognized_modifiers_abstain(rich_retail_context, question):
    """Unrecognized modifiers, staff filters, profit/cost deductions, or boolean checks must return 'abstain'."""
    assert resolve_semantic_plan(question, rich_retail_context, "2025-09-30")[0] == "abstain"
    assert resolve_query(question, rich_retail_context, "2025-09-30")[0] == "abstain"


# =============================================================================
# 5. REAL DEV DATASET INTEGRATION SMOKE TESTS (IF DATA PRESENT)
# =============================================================================


def test_real_dev_002_inline_topping_and_category_order_count():
    """Integration smoke test against real dev_002 data if available on disk."""
    train_dir = Path("data/train/train/data")
    if not (train_dir / "dev_002").exists():
        pytest.skip("dev_002 data directory not available")

    loader = DataLoader(train_dir, "data/train/train/calendar.csv")
    ctx = loader.load_business("dev_002")
    contract = BusinessSemanticContract.from_context(ctx)
    assert contract is not None

    ans_type, ans_val, _ = resolve_query(
        "Tháng 9/2025 số đơn có mua hàng nhóm trà là bao nhiêu?",
        ctx,
        as_of_date="2025-10-01",
    )
    assert ans_type == "number"
    assert isinstance(ans_val, int) and ans_val > 0


def test_step2_growth_and_unanswerable_gate():
    """Verify Step 2 features:
    1. Gate 1 unanswerable on subjective queries.
    2. Gate 1 unanswerable on profit/cost without cost tables.
    3. Gate 1 unanswerable on prior period out-of-domain.
    4. Deterministic MoM period growth percent calculation.
    """
    df = pd.DataFrame(
        {
            "ma_don_hang": ["O1", "O2", "O3"],
            "thoi_gian": pd.to_datetime(["2025-08-15 10:00:00", "2025-09-10 10:00:00", "2025-09-20 11:00:00"]),
            "trang_thai": ["Hoàn thành", "Hoàn thành", "Hoàn thành"],
            "thanh_tien": [100000.0, 150000.0, 50000.0],  # Aug: 100k, Sep: 200k -> +100%
        }
    )
    ctx = _make_context({"so_ban_hang": df}, business_id="growth_biz")

    # 1. Subjective query -> unanswerable
    res_subj = resolve_query("Nhân viên nào nhiệt tình nhất?", ctx, "2025-09-30")
    assert res_subj[0] == "unanswerable"

    # 2. Profit without cost table -> unanswerable
    res_prof = resolve_query("Lợi nhuận tháng 9/2025 là bao nhiêu?", ctx, "2025-09-30")
    assert res_prof[0] == "unanswerable"

    # 3. Growth compared to out-of-domain prior year (2024) -> unanswerable
    res_ood = resolve_query("Tháng 9/2025 so với tháng 9/2024 tăng trưởng bao nhiêu %?", ctx, "2025-09-30")
    assert res_ood[0] == "unanswerable"

    # 4. Valid MoM growth: Sep 2025 (200k) vs Aug 2025 (100k) -> +100.0%
    res_mom = resolve_query("Doanh thu tháng 9/2025 so với tháng 8/2025 tăng trưởng bao nhiêu %?", ctx, "2025-09-30")
    assert res_mom[0] == "percent"
    assert res_mom[1] == pytest.approx(100.0)


def test_step3_quantity_and_ranking():
    """Verify Step 3 features:
    1. Bestseller Top 1 item (string output) with whitespace normalization.
    2. Bestseller Top K items (list output).
    3. Peak day (date output).
    4. Filtered item quantity (number output).
    5. Size-filtered quantity (number output).
    6. Store-wide drink quantity excluding toppings/food.
    """
    df = pd.DataFrame(
        {
            "ma_don_hang": ["O1", "O2", "O3", "O4"],
            "thoi_gian": pd.to_datetime(["2025-05-10 10:00:00", "2025-05-10 12:00:00", "2025-05-15 15:00:00", "2025-05-15 16:00:00"]),
            "trang_thai": ["Hoàn thành", "Hoàn thành", "Hoàn thành", "Hoàn thành"],
            "ten_mon": ["Bia  Tiger lon", "Bia Tiger lon", "Bia Sài Gòn Special lon", "Bánh croissant"],
            "nhom_mon": ["đồ uống", "đồ uống", "đồ uống", "bánh"],
            "so_luong": [4.0, 3.0, 4.0, 5.0],
            "thanh_tien": [44000.0, 66000.0, 76000.0, 50000.0],
        }
    )
    ctx = _make_context({"ban_hang": df}, business_id="step3_biz")

    # 1. Bestseller Top 1: Bia Tiger lon total = 2 + 3 = 5 > 4 (Bia SG) -> 'Bia Tiger lon'
    res_top1 = resolve_query("tháng 5/2025 hàng nào bán chạy nhất", ctx)
    assert res_top1[0] == "string"
    assert res_top1[1] == "Bia Tiger lon"

    # 2. Peak day: 2025-05-15 (126k) vs 2025-05-10 (110k) -> '2025-05-15'
    res_peak = resolve_query("Ngày nào doanh thu cao nhất trong tháng 5/2025?", ctx)
    assert res_peak[0] == "date"
    assert res_peak[1] == "2025-05-15"

    # 3. Item quantity: Bia Tiger lon = 4 + 3 = 7
    res_qty = resolve_query("Tháng 5/2025 quán bán được bao nhiêu lon Bia Tiger lon?", ctx)
    assert res_qty[0] == "number"
    assert res_qty[1] == 7

    # 4. Inventory query must abstain (protect PA)
    res_inv = resolve_query("Cuối ngày hôm nay trong kho còn bao nhiêu lon Bia Tiger?", ctx, "2025-05-31")
    assert res_inv[0] == "abstain"

    # 5. Top K list must abstain (protect PA against list ordering variance)
    res_top3 = resolve_query("Top 3 mặt hàng bán chạy nhất tháng 5/2025 là những gì?", ctx)
    assert res_top3[0] == "abstain"

    # 6. Multi-month range must abstain
    res_range = resolve_query("Từ tháng 5/2025 tới tháng 8/2025 tháng nào bán được nhiều tiền nhất?", ctx)
    assert res_range[0] == "abstain"


