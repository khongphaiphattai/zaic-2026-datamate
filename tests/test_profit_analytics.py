"""Tests for schema-general gross profit, net profit, top-profit item, and refusal guards (Slice P1.2)."""

from __future__ import annotations

import duckdb
import pandas as pd

from zaic_2026_datamate.data_loader import BusinessContext
from zaic_2026_datamate.pipeline import build_agent, production_pipeline_config


def _build_retail_supermarket_profit_ctx() -> BusinessContext:
    """Synthetic retail supermarket schema: header + detail (line `gia_von`), broken-accent detail names, no expense table."""
    conn = duckdb.connect(":memory:")
    orders = pd.DataFrame(
        [
            {
                "ma_hoa_don": "ORD01",
                "thoi_gian": "2025-10-02",
                "trang_thai": "đã lấy",
                "tong_tien_hang": 300_000,
                "giam_gia": 10_000,
                "tong_sau_giam_gia": 290_000,
            },
            {
                "ma_hoa_don": "ORD02",
                "thoi_gian": "2025-10-05",
                "trang_thai": "Hoàn thành",
                "tong_tien_hang": 150_000,
                "giam_gia": 0,
                "tong_sau_giam_gia": 150_000,
            },
            {
                "ma_hoa_don": "ORD03",
                "thoi_gian": "2025-10-08",
                "trang_thai": "Đã huỷ",
                "tong_tien_hang": 500_000,
                "giam_gia": 0,
                "tong_sau_giam_gia": 500_000,
            },
            {
                "ma_hoa_don": "ORD04",
                "thoi_gian": "2025-10-09",
                "trang_thai": "Đã trả hàng",
                "tong_tien_hang": 400_000,
                "giam_gia": 0,
                "tong_sau_giam_gia": 400_000,
            },
            {
                "ma_hoa_don": "ORD05",
                "thoi_gian": "2025-11-01",
                "trang_thai": "Hoàn thành",
                "tong_tien_hang": 100_000,
                "giam_gia": 0,
                "tong_sau_giam_gia": 100_000,
            },
        ]
    )
    # SP01 sold across mixed-accent `ten_hang` rows:
    # ORD01 SP01: rev=200k, cogs=2*70k=140k -> gp=60k
    # ORD02 SP01: rev=150k, cogs=1*105k=105k -> gp=45k (Total SP01 gp=105k)
    # ORD01 SP02: rev=100k, cogs=1*20k=20k -> gp=80k
    # Store 2025-10 GP = (200k+100k+150k) - (140k+20k+105k) = 450k - 265k = 185k
    details = pd.DataFrame(
        [
            {
                "ma_hoa_don": "ORD01",
                "stt": 1,
                "ma_hang": "SP01",
                "ten_hang": "Giay ve sinh Pulppy 10 cuon",
                "don_vi_tinh": "lốc",
                "so_luong": 2,
                "so_luong_quy_doi": 2,
                "don_gia": 100_000,
                "giam_gia": 0,
                "thanh_tien": 200_000,
                "gia_von": 70_000,
            },
            {
                "ma_hoa_don": "ORD01",
                "stt": 2,
                "ma_hang": "SP02",
                "ten_hang": "Dầu ăn Simply 2L",
                "don_vi_tinh": "chai",
                "so_luong": 1,
                "so_luong_quy_doi": 1,
                "don_gia": 100_000,
                "giam_gia": 0,
                "thanh_tien": 100_000,
                "gia_von": 20_000,
            },
            {
                "ma_hoa_don": "ORD02",
                "stt": 1,
                "ma_hang": "SP01",
                "ten_hang": "Giấy vệ sinh Pulppy 10 cuộn",
                "don_vi_tinh": "lốc",
                "so_luong": 1,
                "so_luong_quy_doi": 6,
                "don_gia": 150_000,
                "giam_gia": 0,
                "thanh_tien": 150_000,
                "gia_von": 105_000,
            },
            {
                "ma_hoa_don": "ORD03",
                "stt": 1,
                "ma_hang": "SP02",
                "ten_hang": "Dầu ăn Simply 2L",
                "don_vi_tinh": "chai",
                "so_luong": 5,
                "so_luong_quy_doi": 5,
                "don_gia": 100_000,
                "giam_gia": 0,
                "thanh_tien": 500_000,
                "gia_von": 20_000,
            },
            {
                "ma_hoa_don": "ORD04",
                "stt": 1,
                "ma_hang": "SP02",
                "ten_hang": "Dầu ăn Simply 2L",
                "don_vi_tinh": "chai",
                "so_luong": 4,
                "so_luong_quy_doi": 4,
                "don_gia": 100_000,
                "giam_gia": 0,
                "thanh_tien": 400_000,
                "gia_von": 20_000,
            },
            {
                "ma_hoa_don": "ORD05",
                "stt": 1,
                "ma_hang": "SP01",
                "ten_hang": "Giấy vệ sinh Pulppy 10 cuộn",
                "don_vi_tinh": "lốc",
                "so_luong": 1,
                "so_luong_quy_doi": 1,
                "don_gia": 100_000,
                "giam_gia": 0,
                "thanh_tien": 100_000,
                "gia_von": 60_000,
            },
        ]
    )
    products = pd.DataFrame(
        [
            {
                "ma_hang": "SP01",
                "ten_hang": "Giấy vệ sinh Pulppy 10 cuộn",
                "gia_von": 50_000,
            },
            {"ma_hang": "SP02", "ten_hang": "Dầu ăn Simply 2L", "gia_von": 20_000},
        ]
    )
    # Decoy cashbook mirror table named `du_lieu_ban_hang` (must NEVER preempt order header)
    cashbook_mirror = pd.DataFrame(
        [
            {
                "ma_phieu": "PAY01",
                "thoi_gian": "2025-10-02",
                "ma_hoa_don": "ORD01",
                "phuong_thuc_thanh_toan": "Tiền mặt",
                "gia_tri": 290_000,
            },
            {
                "ma_phieu": "PAY02",
                "thoi_gian": "2025-10-05",
                "ma_hoa_don": "ORD02",
                "phuong_thuc_thanh_toan": "Tiền mặt",
                "gia_tri": 150_000,
            },
            {
                "ma_phieu": "PAY03",
                "thoi_gian": "2025-10-08",
                "ma_hoa_don": "ORD03",
                "phuong_thuc_thanh_toan": "Tiền mặt",
                "gia_tri": 500_000,
            },
            {
                "ma_phieu": "PAY04",
                "thoi_gian": "2025-10-09",
                "ma_hoa_don": "ORD04",
                "phuong_thuc_thanh_toan": "Tiền mặt",
                "gia_tri": 400_000,
            },
        ]
    )
    conn.register("du_lieu_ban_hang", cashbook_mirror)
    conn.register("du_lieu_ban_hang_danh_sach_hoa_don", orders)
    conn.register("danh_sach_hoa_don_chi_tiet", details)
    conn.register("du_lieu_ban_hang_danh_sach_san_pham", products)
    return BusinessContext(
        business_id="synthetic_retail_profit",
        data_dir=".",
        tables={
            "du_lieu_ban_hang": cashbook_mirror,
            "du_lieu_ban_hang_danh_sach_hoa_don": orders,
            "danh_sach_hoa_don_chi_tiet": details,
            "du_lieu_ban_hang_danh_sach_san_pham": products,
        },
        duckdb_conn=conn,
    )


def _build_pharmacy_net_profit_ctx() -> BusinessContext:
    """Synthetic pharmacy schema: `ban_hang_hoa_don` + `ban_hang_chi_tiet_hoa_don` (`gia_von_luc_ban`, `slg`, `sl_co_ban`) + `so_tay_chi_phi`."""
    conn = duckdb.connect(":memory:")
    orders = pd.DataFrame(
        [
            {
                "order_id": "ORD1",
                "ngay_ban": "2025-01-10",
                "trang_thai": "Đã giao",
                "tong_tien": 200_000,
                "ck": 10_000,
            },
            {
                "order_id": "ORD2",
                "ngay_ban": "2025-01-15",
                "trang_thai": "Không giao được",
                "tong_tien": 500_000,
                "ck": 0,
            },
            {
                "order_id": "ORD3",
                "ngay_ban": "2025-01-18",
                "trang_thai": "Đổi trả",
                "tong_tien": 300_000,
                "ck": 0,
            },
            {
                "order_id": "ORD4",
                "ngay_ban": "2025-06-12",
                "trang_thai": "Đang vận chuyển",
                "tong_tien": 400_000,
                "ck": 0,
            },
        ]
    )
    details = pd.DataFrame(
        [
            {
                "ma_giao_dich": "ORD1",
                "ma_hang": "SP10",
                "chi_tiet_hang": "omega 3",
                "slg": 2,
                "sl_co_ban": 200,
                "thanh_tien": 210_000,
                "gia_von_luc_ban": 60_000,  # 2 * 60k = 120k -> gp = 90k
            },
            {
                "ma_giao_dich": "ORD2",
                "ma_hang": "SP20",
                "chi_tiet_hang": "Thuoc B",
                "slg": 5,
                "sl_co_ban": 5,
                "thanh_tien": 500_000,
                "gia_von_luc_ban": 50_000,
            },
            {
                "ma_giao_dich": "ORD3",
                "ma_hang": "SP20",
                "chi_tiet_hang": "Thuoc B",
                "slg": 3,
                "sl_co_ban": 3,
                "thanh_tien": 300_000,
                "gia_von_luc_ban": 50_000,
            },
            {
                "ma_giao_dich": "ORD4",
                "ma_hang": "SP10",
                "chi_tiet_hang": "Costar Omega 3",
                "slg": 2,
                "sl_co_ban": 200,
                "thanh_tien": 400_000,
                "gia_von_luc_ban": 100_000,  # 2 * 100k = 200k -> gp = 200k
            },
        ]
    )
    products = pd.DataFrame(
        [
            {
                "ma_sp": "SP10",
                "mon": "Costar Omega 3 1000mg lọ 100 viên",
                "gia_von_hien_tai": 500,
            },
            {"ma_sp": "SP20", "mon": "Thuốc B", "gia_von_hien_tai": 40_000},
        ]
    )
    expenses = pd.DataFrame(
        [
            {
                "ngay_chi": "2025-01-01",
                "muc": "thuê",
                "so_tien": 200_000,
            },  # net Jan = 90k - 200k = -110k
            {
                "ngay_chi": "2025-06-05",
                "muc": "điện",
                "so_tien": 50_000,
            },  # net Jun = 200k - 50k = 150k
        ]
    )
    conn.register("ban_hang_hoa_don", orders)
    conn.register("ban_hang_chi_tiet_hoa_don", details)
    conn.register("ban_hang_hang_hoa", products)
    conn.register("so_tay_chi_phi", expenses)
    return BusinessContext(
        business_id="synthetic_pharmacy_profit",
        data_dir=".",
        tables={
            "ban_hang_hoa_don": orders,
            "ban_hang_chi_tiet_hoa_don": details,
            "ban_hang_hang_hoa": products,
            "so_tay_chi_phi": expenses,
        },
        duckdb_conn=conn,
    )


def test_gross_net_profit_and_top_profitable_product() -> None:
    """Verify monthly gross profit, negative/positive net profit, Telex typos, and canonical product ID ranking."""
    agent = build_agent(production_pipeline_config())

    ctx_retail = _build_retail_supermarket_profit_ctx()
    try:
        # Monthly gross profit with Telex typos ('nawm', 'nhiu') and excluding cancelled/returned orders
        assert agent.answer_query(
            "xem giúp cho tôi biết lời gộp tháng 10 nawm 2025",
            ctx_retail,
            as_of_date="2025-11-30",
        )[:2] == ("number", 185_000)

        # Canonical product grouping must merge 'Giay ve sinh...' + 'Giấy vệ sinh...' into top GP item SP01 (105k > 80k)
        assert agent.answer_query(
            "cho hỏi thangs 10/2025 hàng nào lời nhất",
            ctx_retail,
            as_of_date="2025-11-30",
        )[:2] == ("string", "Giấy vệ sinh Pulppy 10 cuộn")

        # Retail shop without operating expense table must refuse net profit as unanswerable
        assert (
            agent.answer_query(
                "Lãi ròng tháng 10/2025 của cửa hàng là bao nhiêu?",
                ctx_retail,
                as_of_date="2025-11-30",
            )[0]
            == "unanswerable"
        )
    finally:
        ctx_retail.duckdb_conn.close()

    ctx_pharma = _build_pharmacy_net_profit_ctx()
    try:
        # Negative net profit after deducting expenses and ignoring 'Không giao được' & 'Đổi trả'
        assert agent.answer_query(
            "Xem giúp trừ hết chi phí rồi thì tháng 1/2025 còn lãi ròng bao nhiêu?",
            ctx_pharma,
            as_of_date="2025-07-01",
        )[:2] == ("number", -110_000)

        # Positive net profit including 'Đang vận chuyển' & 'naof' typo on top-profit query
        assert agent.answer_query(
            "Lợi nhuận ròng tháng 6 năm 2025 là bao nhiêu?",
            ctx_pharma,
            as_of_date="2025-07-01",
        )[:2] == ("number", 150_000)

        assert agent.answer_query(
            "tháng 6/2025 mặt hàng naof lời nhất",
            ctx_pharma,
            as_of_date="2025-07-01",
        )[:2] == ("string", "Costar Omega 3 1000mg lọ 100 viên")
    finally:
        ctx_pharma.duckdb_conn.close()


def test_profit_unanswerable_and_abstain_guards() -> None:
    """Verify unanswerable when schema lacks cost prices vs abstain on margins, inventory cost, and ambiguous ratios."""
    agent = build_agent(production_pipeline_config())

    # 1. Business with `don_nhap_hang_chi_tiet` WITHOUT price/cost column -> gross profit MUST be unanswerable
    conn1 = duckdb.connect(":memory:")
    orders = pd.DataFrame(
        [
            {
                "ma_don_hang": "D1",
                "ngay_dat_hang": "2025-03-10",
                "trang_thai_don_hang": "Hoàn thành",
                "ten_san_pham": "Thuốc A",
                "so_luong_san_pham": 2,
                "gia_san_pham": 50_000,
                "tong_tien": 100_000,
            }
        ]
    )
    nhap = pd.DataFrame(
        [{"ma_don_nhap": "N1", "ma_sku": "SKU1", "ma_lo": "L1", "so_luong_nhap": 10}]
    )
    conn1.register("don_hang_chi_tiet", orders)
    conn1.register("don_nhap_hang_chi_tiet", nhap)
    ctx_no_cost = BusinessContext(
        business_id="synthetic_no_cost_nhap",
        data_dir=".",
        tables={"don_hang_chi_tiet": orders, "don_nhap_hang_chi_tiet": nhap},
        duckdb_conn=conn1,
    )
    try:
        assert (
            agent.answer_query(
                "Cho hỏi tháng 3/2025 cửa hàng lãi gộp bao nhiêu tiền?",
                ctx_no_cost,
                as_of_date="2025-06-01",
            )[0]
            == "unanswerable"
        )
    finally:
        conn1.close()

    # 2. Margin/percentage, static-master-only cost, and multi-month range stay safely abstain
    ctx_pharma = _build_pharmacy_net_profit_ctx()
    try:
        assert (
            agent.answer_query(
                "Biên lợi nhuận gộp tháng 6/2025 là bao nhiêu phần trăm?",
                ctx_pharma,
                as_of_date="2025-07-01",
            )[0]
            == "abstain"
        )
        assert (
            agent.answer_query(
                "Từ tháng 1/2025 đến tháng 6/2025 lãi ròng bao nhiêu?",
                ctx_pharma,
                as_of_date="2025-07-01",
            )[0]
            == "abstain"
        )
        # Inventory cost valuation belongs to Slice P1.3, not profit_analytics
        assert (
            agent.answer_query(
                "Hết ngày 15/06/2025, số hàng còn trong kho đáng bao nhiêu tiền, tính theo giá vốn?",
                ctx_pharma,
                as_of_date="2025-06-15",
            )[0]
            == "abstain"
        )
    finally:
        ctx_pharma.duckdb_conn.close()

    # 3. Business with ONLY static master `san_pham.gia_von` (no historical line COGS) must abstain safely
    conn3 = duckdb.connect(":memory:")
    orders_static = pd.DataFrame(
        [
            {
                "ngay": "2025-03-10",
                "ma_don_hang": "D1",
                "ten_hang_hoa": "Mặt hàng A",
                "so_luong": 2,
                "don_gia": 50_000,
                "thanh_tien": 100_000,
                "trang_thai": "Hoàn thành",
            }
        ]
    )
    master_static = pd.DataFrame([{"ten_hang": "Mặt hàng A", "gia_von": 30_000}])
    conn3.register("so_chi_tiet_ban_hang", orders_static)
    conn3.register("danh_sach_hang_hoa", master_static)
    ctx_static = BusinessContext(
        business_id="synthetic_static_master_cost",
        data_dir=".",
        tables={
            "so_chi_tiet_ban_hang": orders_static,
            "danh_sach_hang_hoa": master_static,
        },
        duckdb_conn=conn3,
    )
    try:
        assert (
            agent.answer_query(
                "Tháng 3/2025 cửa hàng lãi gộp bao nhiêu tiền?",
                ctx_static,
                as_of_date="2025-04-01",
            )[0]
            == "abstain"
        )
    finally:
        conn3.close()
