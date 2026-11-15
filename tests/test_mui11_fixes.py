"""TDD specifications for Mũi 11 (Slices A1–A4 and B1–B7)."""

from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd


def _make_ctx(business_id: str, tables: dict[str, pd.DataFrame]):
    from zaic_2026_datamate.data_loader import BusinessContext

    conn = duckdb.connect(":memory:")
    for name, df in tables.items():
        conn.register(name, df)
    return BusinessContext(
        business_id=business_id,
        data_dir=Path("."),
        tables=tables,
        duckdb_conn=conn,
    )


def test_slice_a1_domain_ext_no_duckdb_resource_in_use():
    from zaic_2026_datamate.domain_ext import resolve_domain_extensions
    from zaic_2026_datamate.semantic_contract import BusinessSemanticContract

    ban_hang = pd.DataFrame(
        [
            {
                "ma_don_hang": "O1",
                "ngay": "2025-07-05",
                "ten_hang": "Gạo",
                "so_luong": 1,
                "thanh_tien": 100_000.0,
                "trang_thai": "Hoàn thành",
            }
        ]
    )
    the_kho = pd.DataFrame(
        [
            {
                "ma_hang_hoa": "SP01",
                "ton_cuoi": 0.0,
                "ngay_gio_phat_sinh": "2025-07-05 18:00:00",
                "ma_giao_dich_kho": "K1",
            }
        ]
    )
    ctx = _make_ctx("priv_a1_cursor", {"ban_hang": ban_hang, "the_kho": the_kho})
    try:
        res = resolve_domain_extensions(
            "Cuối ngày 05/07/2025 có bao nhiêu mặt hàng hết hàng?",
            ctx,
            as_of_date="2025-07-10",
        )
        assert res == ("number", 1, None)
        # Subsequent semantic contract table registration must never raise ResourceInUseException
        contract = BusinessSemanticContract.from_context(ctx)
        assert contract.orders_df is not None
    finally:
        ctx.duckdb_conn.close()


def test_slice_a1_repair_cp437_mojibake_and_banner_header_sniff():
    from zaic_2026_datamate.cleaner import sniff_and_clean_dataframe
    from zaic_2026_datamate.data_loader import normalize_identifier

    assert (
        normalize_identifier("Bß║úng k├¬ phiß║┐u giao h├áng.xlsx")
        == "bang_ke_phieu_giao_hang"
    )
    assert normalize_identifier("Danh s├ích h├áng h├│a.xlsx") == "danh_sach_hang_hoa"
    assert (
        normalize_identifier("Chi tiß║┐t c├┤ng nß╗ú theo hß║ín.xlsx")
        == "chi_tiet_cong_no_theo_han"
    )

    # 3-column table preceded by a 1-cell decorative title row
    raw = pd.DataFrame(
        [
            ["CHI TIẾT CÔNG NỢ THEO HẠN THANH TOÁN", None, None],
            [None, None, None],
            ["Mã đơn hàng", "Số ngày được nợ", "Hạn thanh toán"],
            ["ORD001", 30, "03/03/2025"],
        ]
    )
    cleaned = sniff_and_clean_dataframe(raw)
    assert list(cleaned.columns) == ["mã_đơn_hàng", "số_ngày_được_nợ", "hạn_thanh_toán"]
    assert len(cleaned) == 1


def test_slice_a1_multisheet_stt_isolation_and_ma_giao_dich_order_col():
    from zaic_2026_datamate.data_loader import clean_dataframe
    from zaic_2026_datamate.intent_parser import find_primary_sales_table
    from zaic_2026_datamate.semantic_contract import BusinessSemanticContract

    raw = pd.DataFrame(
        [
            {
                "sheet_name": "01.2025",
                "STT": 1,
                "Ngày": "2025-01-05",
                "Hàng": "Món A",
                "Thành tiền": 500_000.0,
                "Trạng thái": "Hoàn thành",
            },
            {
                "sheet_name": "01.2025",
                "STT": 2,
                "Ngày": "2025-01-06",
                "Hàng": "Món B",
                "Thành tiền": 200_000.0,
                "Trạng thái": "Trả hàng",
            },
            {
                "sheet_name": "02.2025",
                "STT": 1,
                "Ngày": "2025-02-05",
                "Hàng": "Món A",
                "Thành tiền": 300_000.0,
                "Trạng thái": "Hoàn thành",
            },
            {
                "sheet_name": "02.2025",
                "STT": 2,
                "Ngày": "2025-02-06",
                "Hàng": "Món C",
                "Thành tiền": 400_000.0,
                "Trạng thái": None,
            },
        ]
    )
    cleaned, _ = clean_dataframe(raw)
    # Sheet 02.2025 STT=2 blank status must NOT inherit "Trả hàng" from Sheet 01.2025 STT=2
    assert pd.isna(cleaned.loc[3, "trang_thai"])

    ctx = _make_ctx("priv_a1_stt", {"ban_hang": cleaned})
    try:
        tbl, ord_col, _dc, _sc = find_primary_sales_table(ctx)
        assert tbl == "ban_hang"
        # Raw legacy SQL must never emit stt NOT IN across sheets
        assert ord_col != "stt"

        contract = BusinessSemanticContract.from_context(ctx)
        valid_orders = contract.orders_df[contract.orders_df["is_valid_sale"]]
        # 3 valid orders: 01.2025#1 (500k), 02.2025#1 (300k), 02.2025#2 (400k)
        assert len(valid_orders) == 3
        assert round(float(valid_orders["order_revenue"].sum())) == 1_200_000
    finally:
        ctx.duckdb_conn.close()

    # Daily-restarting stt without sheet_name
    daily_stt = pd.DataFrame(
        [
            {
                "stt": 1,
                "ngay": "2025-03-01",
                "ten_hang": "Sp 1",
                "thanh_tien": 100_000.0,
                "trang_thai": "Khách hủy",
            },
            {
                "stt": 1,
                "ngay": "2025-03-02",
                "ten_hang": "Sp 1",
                "thanh_tien": 250_000.0,
                "trang_thai": "Hoàn thành",
            },
        ]
    )
    ctx_daily = _make_ctx("priv_a1_daily_stt", {"ban_hang": daily_stt})
    try:
        contract_d = BusinessSemanticContract.from_context(ctx_daily)
        valid_d = contract_d.orders_df[contract_d.orders_df["is_valid_sale"]]
        assert len(valid_d) == 1
        assert round(float(valid_d["order_revenue"].sum())) == 250_000
    finally:
        ctx_daily.duckdb_conn.close()

    # ma_giao_dich recognized as order_col
    mgd_df = pd.DataFrame(
        [
            {
                "ma_giao_dich": "GD01",
                "ngay": "2025-04-01",
                "ten_hang": "A",
                "thanh_tien": 100_000.0,
            },
            {
                "ma_giao_dich": "GD01",
                "ngay": "2025-04-01",
                "ten_hang": "B",
                "thanh_tien": 50_000.0,
            },
        ]
    )
    ctx_mgd = _make_ctx("priv_a1_mgd", {"chi_tiet_ban_hang": mgd_df})
    try:
        _t, ord_col_mgd, _dc, _sc = find_primary_sales_table(ctx_mgd)
        assert ord_col_mgd == "ma_giao_dich"
    finally:
        ctx_mgd.duckdb_conn.close()


def test_slice_a1_unsold_items_excludes_cancelled_and_returned_orders():
    from zaic_2026_datamate.domain_ext import resolve_domain_extensions

    cat = pd.DataFrame(
        [
            {"ten_hang_hoa": "Xi măng A", "duoc_ban_truc_tiep": 1},
            {"ten_hang_hoa": "Thép B", "duoc_ban_truc_tiep": 1},
        ]
    )
    sales = pd.DataFrame(
        [
            {
                "ma_don_hang": "O1",
                "ngay": "2026-02-20 15:00:00",
                "ten_hang_hoa": "Xi măng A",
                "so_luong": 10,
                "thanh_tien": 1_000_000.0,
                "trang_thai": "Đã giao",
            },
            {
                "ma_don_hang": "O2",
                "ngay": "2026-02-25 14:30:00",
                "ten_hang_hoa": "Thép B",
                "so_luong": 5,
                "thanh_tien": 500_000.0,
                "trang_thai": "Khách hủy",
            },
        ]
    )
    ctx = _make_ctx(
        "priv_a1_unsold",
        {"danh_sach_hang_hoa": cat, "so_chi_tiet_ban_hang": sales},
    )
    try:
        res = resolve_domain_extensions(
            "Trong 14 ngày gần nhất có bao nhiêu mặt hàng không bán được cái nào?",
            ctx,
            as_of_date="2026-03-04",
        )
        # Thép B only appeared in a cancelled order -> 1 unsold
        assert res == ("number", 1, None)
        res_end = resolve_domain_extensions(
            "Trong 7 ngày gần nhất có bao nhiêu mặt hàng không bán được cái nào?",
            ctx,
            as_of_date="2026-02-20",
        )
        # Xi măng A has timestamp on end_date 2026-02-20 15:00:00 -> must NOT be truncated!
        assert res_end == ("number", 1, None)
    finally:
        ctx.duckdb_conn.close()


def test_slice_a2_valid_payment_statuses_with_tra_not_excluded_by_sql_or_contract():
    from zaic_2026_datamate.intent_parser import find_primary_sales_table, resolve_query
    from zaic_2026_datamate.semantic_contract import (
        BusinessSemanticContract,
        is_bad_status,
    )

    for valid_s in (
        "Đã trả",
        "Trả đủ",
        "Trả góp",
        "Chưa trả",
        "Trả trước",
        "Đang vận chuyển",
    ):
        assert not is_bad_status(valid_s), f"False bad status: {valid_s}"

    for bad_s in (
        "Trả hàng",
        "Đã trả hàng",
        "Đổi trả",
        "Hoàn trả",
        "Đã hoàn hàng",
        "Trả lại",
        "Hàng trả về",
        "Khách hủy",
        "Huỷ đơn",
        "Không giao được",
        "Trả",
    ):
        assert is_bad_status(bad_s), f"Missed bad status: {bad_s}"

    sales = pd.DataFrame(
        [
            {
                "so_don_hang": "D1",
                "ngay": "2025-08-05",
                "ten_hang": "SP A",
                "thanh_tien": 500_000.0,
                "trang_thai": "Trả góp",
            },
            {
                "so_don_hang": "D2",
                "ngay": "2025-08-06",
                "ten_hang": "SP B",
                "thanh_tien": 300_000.0,
                "trang_thai": "Đã trả",
            },
            {
                "so_don_hang": "D3",
                "ngay": "2025-08-07",
                "ten_hang": "SP C",
                "thanh_tien": 200_000.0,
                "trang_thai": "Chưa trả",
            },
            {
                "so_don_hang": "D4",
                "ngay": "2025-08-08",
                "ten_hang": "SP D",
                "thanh_tien": 100_000.0,
                "trang_thai": "Đang vận chuyển",
            },
            {
                "so_don_hang": "D5",
                "ngay": "2025-08-09",
                "ten_hang": "SP E",
                "thanh_tien": 999_000.0,
                "trang_thai": "Trả hàng",
            },
            {
                "so_don_hang": "D6",
                "ngay": "2025-08-10",
                "ten_hang": "SP F",
                "thanh_tien": 888_000.0,
                "trang_thai": "Khách hủy",
            },
        ]
    )
    ctx = _make_ctx("priv_a2_status", {"ban_hang": sales})
    try:
        _t, ord_col, _dc, _sc = find_primary_sales_table(ctx)
        assert ord_col == "so_don_hang"

        rev = resolve_query(
            "Doanh thu tháng 8/2025 là bao nhiêu?", ctx, as_of_date="2025-09-01"
        )
        assert rev[:2] == ("number", 1_100_000)

        cnt = resolve_query(
            "Tháng 8/2025 có bao nhiêu đơn hàng?", ctx, as_of_date="2025-09-01"
        )
        assert cnt[:2] == ("number", 4)

        contract = BusinessSemanticContract.from_context(ctx)
        valid = contract.orders_df[contract.orders_df["is_valid_sale"]]
        assert len(valid) == 4
        assert round(float(valid["order_revenue"].sum())) == 1_100_000
    finally:
        ctx.duckdb_conn.close()


def test_slice_a3_subjective_and_reason_ood_return_unanswerable():
    from zaic_2026_datamate.intent_parser import resolve_query

    ban_hang = pd.DataFrame(
        [
            {
                "ma_don_hang": "D1",
                "ngay": "2026-03-05",
                "ten_hang": "Cà phê sữa",
                "thanh_tien": 35_000.0,
                "trang_thai": "Hoàn thành",
            },
            {
                "ma_don_hang": "D2",
                "ngay": "2026-03-06",
                "ten_hang": "Trà đào",
                "thanh_tien": 45_000.0,
                "trang_thai": "Trả hàng",
            },
        ]
    )
    nhan_vien = pd.DataFrame([{"ma_nv": "NV01", "ten_nhan_vien": "Nguyễn Văn A"}])

    ctx = _make_ctx("priv_a3_ood", {"ban_hang": ban_hang, "nhan_vien": nhan_vien})
    try:
        for q in (
            "Nhân viên Nguyễn Văn A làm việc có tận tâm với khách không?",
            "Nguyễn Văn A có lễ phép, chu đáo với khách hông",
            "Ai trong cửa hàng đc khách quý mến nhất?",
            "thang 3/2026, tra hang vi loi va tra vi mua nham, ben nao nhieu don hon",
        ):
            t, v, r = resolve_query(q, ctx, as_of_date="2026-04-01")
            assert t == "unanswerable", (
                f"Expected unanswerable for {q!r}, got {(t, v, r)}"
            )
    finally:
        ctx.duckdb_conn.close()


def test_slice_a3_no_false_reason_block_on_slow_unsold_and_delegates_to_semantic(
    monkeypatch,
):
    from zaic_2026_datamate import semantic_contract
    from zaic_2026_datamate.intent_parser import resolve_query

    ban_hang = pd.DataFrame(
        [
            {
                "ma_don_hang": "D1",
                "ngay": "2026-03-05",
                "ten_hang": "Cà phê",
                "thanh_tien": 30_000.0,
                "trang_thai": "Không giao được",
            },
            {
                "ma_don_hang": "D2",
                "ngay": "2026-03-06",
                "ten_hang": "Trà",
                "thanh_tien": 40_000.0,
                "trang_thai": "Hoàn thành",
            },
        ]
    )
    ctx = _make_ctx("priv_a3_flow", {"ban_hang": ban_hang})
    try:
        # 1. Slow/unsold phrasing must never hit "Conditional / reason query abstained"
        _, _, r_slow = resolve_query(
            "đếm giúp tôi số món bán chậm đến mức trong 7 ngày gần nhất không bán được lần nào",
            ctx,
            as_of_date="2026-03-10",
        )
        assert r_slow != "Conditional / reason query abstained"

        # 2. Legacy cancel_rate / cancel_count / order_count / AOV must delegate to resolve_semantic_plan
        #    before giving up with an early abstain.
        called: list[str] = []

        def fake_sem(q_arg, c_arg, a_arg=None):
            called.append(q_arg)
            return ("number", 42, "fake_semantic_fallback")

        monkeypatch.setattr(semantic_contract, "resolve_semantic_plan", fake_sem)
        for q in (
            "tháng 3/2026 tỉ lệ đơn bị huỷ là bao nhiêu %?",
            "tháng 3/2026 có bao nhiêu đơn bị huỷ?",
        ):
            t, v, _ = resolve_query(q, ctx, as_of_date="2026-04-01")
            assert (t, v) == ("number", 42), (
                f"Early abstain blocked semantic fallback for {q!r}"
            )
        assert len(called) >= 2
    finally:
        ctx.duckdb_conn.close()


def test_slice_a4_telex_normalization_and_end_to_end():
    from zaic_2026_datamate.cleaner import normalize_query_telex
    from zaic_2026_datamate.intent_parser import resolve_query

    assert (
        normalize_query_telex(
            "7 ngày gaanf nhất cửa hangf bans đc bnhiu ddown cuar khachs"
        )
        == "7 ngày gần nhất cửa hàng bán được bao nhiêu đơn của khách"
    )
    assert (
        normalize_query_telex("tir lệ bij huyr từ đos đến nayf là bn ptram")
        == "tỉ lệ bị huỷ từ đó đến này là bn phần trăm"
    )
    assert (
        normalize_query_telex("bình quaan mỗi ddown hàng ddax xuaats trowr leen")
        == "bình quân mỗi đơn hàng đã xuất trở lên"
    )
    assert (
        normalize_query_telex("bans đc món ddoof tuwf trước")
        == "bán được món đồ từ trước"
    )

    ban_hang = pd.DataFrame(
        [
            {
                "ma_don_hang": "D1",
                "ngay": "2025-10-08",
                "ten_hang": "Bột ngọt",
                "thanh_tien": 100_000.0,
                "trang_thai": "Hoàn thành",
            },
            {
                "ma_don_hang": "D2",
                "ngay": "2025-10-10",
                "ten_hang": "Dầu ăn",
                "thanh_tien": 250_000.0,
                "trang_thai": "Hoàn thành",
            },
        ]
    )
    ctx = _make_ctx("priv_a4_telex", {"ban_hang": ban_hang})
    try:
        res_cnt = resolve_query(
            "cho hỏi số đơn 7 ngày gaanf nhất là bnhiu hả",
            ctx,
            as_of_date="2025-10-12",
        )
        assert res_cnt[:2] == ("number", 2)

        res_rev = resolve_query(
            "tuần nayf dthu được bao nhiêu",
            ctx,
            as_of_date="2025-10-12",
        )
        assert res_rev[:2] == ("number", 350_000)

        res_aov = resolve_query(
            "bình quaan mỗi đơn tháng 10/2025 là bnhiu tieenf?",
            ctx,
            as_of_date="2025-10-31",
        )
        assert res_aov[:2] == ("number", 175_000)
    finally:
        ctx.duckdb_conn.close()


def test_slice_b1_top_k_and_top_1_customers_rulebook_s3():
    from zaic_2026_datamate.intent_parser import resolve_query

    ban_hang = pd.DataFrame(
        [
            # Walk-ins with huge revenue must NEVER rank
            {
                "ma_don_hang": "W1",
                "ngay": "2025-06-05",
                "ma_khach_hang": None,
                "ten_khach_hang": "Khách lẻ",
                "ten_hang": "SP",
                "thanh_tien": 9_000_000.0,
                "trang_thai": "Hoàn thành",
            },
            {
                "ma_don_hang": "W2",
                "ngay": "2025-06-06",
                "ma_khach_hang": "",
                "ten_khach_hang": "lẻ",
                "ten_hang": "SP",
                "thanh_tien": 8_000_000.0,
                "trang_thai": "Hoàn thành",
            },
            # Cancelled order must NEVER count
            {
                "ma_don_hang": "C1",
                "ngay": "2025-06-07",
                "ma_khach_hang": "KH003",
                "ten_khach_hang": "Lê Văn C",
                "ten_hang": "SP",
                "thanh_tien": 5_000_000.0,
                "trang_thai": "Khách hủy",
            },
            # KH001: 2 orders, 500k
            {
                "ma_don_hang": "O1",
                "ngay": "2025-06-02",
                "ma_khach_hang": "KH001",
                "ten_khach_hang": "Nguyễn Văn A",
                "ten_hang": "SP",
                "thanh_tien": 300_000.0,
                "trang_thai": "Hoàn thành",
            },
            {
                "ma_don_hang": "O2",
                "ngay": "2025-06-10",
                "ma_khach_hang": "KH001",
                "ten_khach_hang": "Nguyễn Văn A",
                "ten_hang": "SP",
                "thanh_tien": 200_000.0,
                "trang_thai": "Hoàn thành",
            },
            # KH002: 1 order, 400k (ties KH004 on revenue, earlier ID KH002 < KH004)
            {
                "ma_don_hang": "O3",
                "ngay": "2025-06-11",
                "ma_khach_hang": "KH002",
                "ten_khach_hang": "Trần Thị B",
                "ten_hang": "SP",
                "thanh_tien": 400_000.0,
                "trang_thai": "Hoàn thành",
            },
            # KH004: 3 orders, 400k (most orders!)
            {
                "ma_don_hang": "O4",
                "ngay": "2025-06-12",
                "ma_khach_hang": "KH004",
                "ten_khach_hang": "Phạm Văn D",
                "ten_hang": "SP",
                "thanh_tien": 100_000.0,
                "trang_thai": "Hoàn thành",
            },
            {
                "ma_don_hang": "O5",
                "ngay": "2025-06-13",
                "ma_khach_hang": "KH004",
                "ten_khach_hang": "Phạm Văn D",
                "ten_hang": "SP",
                "thanh_tien": 150_000.0,
                "trang_thai": "Hoàn thành",
            },
            {
                "ma_don_hang": "O6",
                "ngay": "2025-06-14",
                "ma_khach_hang": "KH004",
                "ten_khach_hang": "Phạm Văn D",
                "ten_hang": "SP",
                "thanh_tien": 150_000.0,
                "trang_thai": "Hoàn thành",
            },
        ]
    )
    ctx = _make_ctx("priv_b1_cust", {"ban_hang": ban_hang})
    try:
        res_top3 = resolve_query(
            "Liệt kê 3 khách chi tiêu nhiều nhất trong tháng 6 năm 2025.",
            ctx,
            as_of_date="2025-07-01",
        )
        assert res_top3[:2] == ("list", ["Nguyễn Văn A", "Trần Thị B", "Phạm Văn D"])

        res_top3_phrase = resolve_query(
            "3 khách có tổng tiền mua cao nhất tháng 6/2025 là ai?",
            ctx,
            as_of_date="2025-07-01",
        )
        assert res_top3_phrase[:2] == (
            "list",
            ["Nguyễn Văn A", "Trần Thị B", "Phạm Văn D"],
        )

        res_top3_week = resolve_query(
            "Top 3 khách hàng mua nhiều tiền nhất tuần này là ai?",
            ctx,
            as_of_date="2025-06-15",
        )
        assert res_top3_week[:2] == (
            "list",
            ["Trần Thị B", "Phạm Văn D", "Nguyễn Văn A"],
        )

        res_top1_orders = resolve_query(
            "Tháng 6/2025 ai là khách mua nhiều lần nhất, tính theo số đơn?",
            ctx,
            as_of_date="2025-07-01",
        )
        assert res_top1_orders[:2] == ("string", "Phạm Văn D")

        res_top1_rev = resolve_query(
            "Tháng 6/2025 ai mua hàng nhiều tiền nhất?",
            ctx,
            as_of_date="2025-07-01",
        )
        assert res_top1_rev[:2] == ("string", "Nguyễn Văn A")
    finally:
        ctx.duckdb_conn.close()

    # Schema without customer columns -> unanswerable
    no_cust_df = ban_hang.drop(columns=["ma_khach_hang", "ten_khach_hang"])
    ctx_no_cust = _make_ctx("priv_b1_no_cust", {"ban_hang": no_cust_df})
    try:
        for q in (
            "Tháng 6/2025 ai là khách mua nhiều lần nhất, tính theo số đơn?",
            "Top 3 khách hàng mua nhiều tiền nhất tháng 6/2025 là ai?",
        ):
            t, _, _ = resolve_query(q, ctx_no_cust, as_of_date="2025-07-01")
            assert t == "unanswerable"
    finally:
        ctx_no_cust.duckdb_conn.close()


def test_slice_b2_unsold_items_rulebook_s5_and_den_het_date_range():
    from zaic_2026_datamate.intent_parser import parse_time_expressions, resolve_query

    p = parse_time_expressions(
        "giai đoạn từ 25/06/2026 đến hết 01/07/2026 có tổng cộng mấy mặt hàng ế?",
        as_of_date="2026-07-09",
    )
    assert p.start_date == "2026-06-25"
    assert p.end_date == "2026-07-01"

    # Rulebook §5 line 69: no catalog table needed; pre-period valid items minus in-period valid items
    sales = pd.DataFrame(
        [
            # Pre-period valid items: Xi măng A, Thép B, Gạch C
            {
                "ma_don_hang": "P1",
                "ngay": "2026-06-01",
                "ten_hang": "Xi măng A",
                "so_luong": 2,
                "thanh_tien": 200_000.0,
                "trang_thai": "Hoàn thành",
            },
            {
                "ma_don_hang": "P2",
                "ngay": "2026-06-05",
                "ten_hang": "Thép B",
                "so_luong": 1,
                "thanh_tien": 100_000.0,
                "trang_thai": "Hoàn thành",
            },
            {
                "ma_don_hang": "P3",
                "ngay": "2026-06-10",
                "ten_hang": "Gạch C",
                "so_luong": 10,
                "thanh_tien": 50_000.0,
                "trang_thai": "Hoàn thành",
            },
            # Pre-period CANCELLED ONLY item (Sơn D) -> was NEVER validly sold before period, must NOT count!
            {
                "ma_don_hang": "P4",
                "ngay": "2026-06-12",
                "ten_hang": "Sơn D",
                "so_luong": 1,
                "thanh_tien": 300_000.0,
                "trang_thai": "Đã hủy",
            },
            # Service fee before period -> never counts as an item!
            {
                "ma_don_hang": "P5",
                "ngay": "2026-06-15",
                "ten_hang": "Phí phục vụ",
                "so_luong": 1,
                "thanh_tien": 10_000.0,
                "trang_thai": "Hoàn thành",
            },
            # Inside window 2026-06-25 .. 2026-07-01:
            # Xi măng A sold validly -> active (not unsold)
            {
                "ma_don_hang": "W1",
                "ngay": "2026-06-28",
                "ten_hang": "Xi măng A",
                "so_luong": 1,
                "thanh_tien": 100_000.0,
                "trang_thai": "Hoàn thành",
            },
            # Thép B only in a returned order inside window -> still UNSOLD (count #1)
            {
                "ma_don_hang": "W2",
                "ngay": "2026-06-30",
                "ten_hang": "Thép B",
                "so_luong": 1,
                "thanh_tien": 100_000.0,
                "trang_thai": "Trả hàng",
            },
            # Gạch C has zero orders inside window -> UNSOLD (count #2)
            # Item K first introduced inside window on 2026-06-27 -> sold validly anyway
            {
                "ma_don_hang": "W3",
                "ngay": "2026-06-27",
                "ten_hang": "Cát K",
                "so_luong": 5,
                "thanh_tien": 50_000.0,
                "trang_thai": "Hoàn thành",
            },
        ]
    )
    ctx = _make_ctx("priv_b2_unsold", {"don_hang_chi_tiet": sales})
    try:
        for q in (
            "giai đoạn từ 25/06/2026 đến heets 01/07/2026 có tổng cộng mấy mặt hàng ees, bán ko dc món nào hả",
            "Đếm giúp tôi số mặt hàng bán chậm đến mức trong giai đoạn từ 25/06/2026 đến hết 01/07/2026 không bán được lần nào.",
            "Có bao nhiêu mặt hàng vẫn đang bán mà trong giai đoạn từ 25/06/2026 đến hết 01/07/2026 không bán ra đc cái nào?",
            "Trong giai đoạn từ 25/06/2026 đến hết 01/07/2026, bao nhiêu mặt hàng bị ế nhỉ?",
        ):
            res = resolve_query(q, ctx, as_of_date="2026-07-09")
            assert res[:2] == ("number", 2), f"Failed for {q!r}: got {res}"
    finally:
        ctx.duckdb_conn.close()


def test_slice_b3_cancel_count_and_rate_rulebook_s1():
    from zaic_2026_datamate.intent_parser import resolve_query
    from zaic_2026_datamate.semantic_contract import is_bad_status, is_canceled_status

    assert is_canceled_status("Không giao được") is True
    assert is_canceled_status("Khách hủy") is True
    assert is_canceled_status("cancelled") is True
    assert is_canceled_status("Đổi trả") is False
    assert is_canceled_status("Đã trả hàng") is False
    assert is_bad_status("Đổi trả") is True
    assert is_bad_status("Không giao được") is True

    sales = pd.DataFrame(
        [
            # April order with May return row -> order_date=2026-04-28 (excluded from May)
            {
                "ma_don_hang": "O_APR",
                "ngay": "2026-04-28",
                "ten_hang": "SP",
                "so_luong": 1,
                "thanh_tien": 100_000.0,
                "trang_thai": "Đổi trả",
            },
            {
                "ma_don_hang": "O_APR",
                "ngay": "2026-05-03",
                "ten_hang": "SP",
                "so_luong": -1,
                "thanh_tien": -100_000.0,
                "trang_thai": "Đổi trả",
            },
            # May 1..20: 3 canceled + 2 returned + 5 completed = 10 total orders in May MTD
            {
                "ma_don_hang": "O1",
                "ngay": "2026-05-02",
                "ten_hang": "SP",
                "so_luong": 1,
                "thanh_tien": 50_000.0,
                "trang_thai": "Không giao được",
            },
            {
                "ma_don_hang": "O2",
                "ngay": "2026-05-03",
                "ten_hang": "SP",
                "so_luong": 1,
                "thanh_tien": 50_000.0,
                "trang_thai": "Đã hủy",
            },
            {
                "ma_don_hang": "O3",
                "ngay": "2026-05-04",
                "ten_hang": "SP",
                "so_luong": 1,
                "thanh_tien": 50_000.0,
                "trang_thai": "cancelled",
            },
            {
                "ma_don_hang": "O4",
                "ngay": "2026-05-05",
                "ten_hang": "SP",
                "so_luong": 1,
                "thanh_tien": 50_000.0,
                "trang_thai": "Đổi trả",
            },
            {
                "ma_don_hang": "O5",
                "ngay": "2026-05-06",
                "ten_hang": "SP",
                "so_luong": 1,
                "thanh_tien": 50_000.0,
                "trang_thai": "Đã trả hàng",
            },
            {
                "ma_don_hang": "O6",
                "ngay": "2026-05-07",
                "ten_hang": "SP",
                "so_luong": 1,
                "thanh_tien": 50_000.0,
                "trang_thai": "Hoàn thành",
            },
            {
                "ma_don_hang": "O7",
                "ngay": "2026-05-08",
                "ten_hang": "SP",
                "so_luong": 1,
                "thanh_tien": 50_000.0,
                "trang_thai": "Hoàn thành",
            },
            {
                "ma_don_hang": "O8",
                "ngay": "2026-05-09",
                "ten_hang": "SP",
                "so_luong": 1,
                "thanh_tien": 50_000.0,
                "trang_thai": "Đang vận chuyển",
            },
            {
                "ma_don_hang": "O9",
                "ngay": "2026-05-10",
                "ten_hang": "SP",
                "so_luong": 1,
                "thanh_tien": 50_000.0,
                "trang_thai": "Hoàn thành",
            },
            {
                "ma_don_hang": "O10",
                "ngay": "2026-05-11",
                "ten_hang": "SP",
                "so_luong": 1,
                "thanh_tien": 50_000.0,
                "trang_thai": "Hoàn thành",
            },
            # Late May (after as_of_date=2026-05-20)
            {
                "ma_don_hang": "O_MAY_LATE",
                "ngay": "2026-05-25",
                "ten_hang": "SP",
                "so_luong": 1,
                "thanh_tien": 50_000.0,
                "trang_thai": "Đã hủy",
            },
            # June 2026: 1 canceled out of 3 = 33.33%
            {
                "ma_don_hang": "J1",
                "ngay": "2026-06-01",
                "ten_hang": "SP",
                "so_luong": 1,
                "thanh_tien": 50_000.0,
                "trang_thai": "khách hủy",
            },
            {
                "ma_don_hang": "J2",
                "ngay": "2026-06-02",
                "ten_hang": "SP",
                "so_luong": 1,
                "thanh_tien": 50_000.0,
                "trang_thai": "Hoàn thành",
            },
            {
                "ma_don_hang": "J3",
                "ngay": "2026-06-03",
                "ten_hang": "SP",
                "so_luong": 1,
                "thanh_tien": 50_000.0,
                "trang_thai": "Hoàn thành",
            },
        ]
    )
    ctx = _make_ctx("priv_b3_cancel", {"don_hang_chi_tiet": sales})
    try:
        # Full-month queries with as_of_date in July
        for q_cnt in (
            "thang 5/2026 co bao nhiu don hang bi huy",
            "thang 5/2026 co bn don bi huy ta",
            "thang 5/2026 co bn bill bi huy",
            "Trong tháng 5/2026, cửa hàng có mấy đơn ở trạng thái huỷ vậy?",
            "Đếm số đơn đã huỷ trong tháng 5/2026.",
        ):
            res = resolve_query(q_cnt, ctx, as_of_date="2026-07-01")
            assert res[:2] == ("number", 4), f"Failed count for {q_cnt!r}: got {res}"

        for q_pct in (
            "thang 5/2026 ti le don bi huy la bao nhieu %",
            "tỉ lệ huỷ đơn tháng 5/2026 là bn %",
            "tỉ lệ đơn huỷ tháng 6/2026 là bao nhiêu phần trăm",
        ):
            res = resolve_query(q_pct, ctx, as_of_date="2026-07-01")
            expected = 33.33 if "6/2026" in q_pct else 36.36
            assert res[:2] == ("percent", expected), (
                f"Failed pct for {q_pct!r}: got {res}"
            )

        # MTD cutoff ("tháng này" at 2026-05-20 excludes O_MAY_LATE -> 3/10 = 30.0%)
        res_mtd = resolve_query(
            "tháng này cửa hangf có bn % đơn bij huỷ",
            ctx,
            as_of_date="2026-05-20",
        )
        assert res_mtd[:2] == ("percent", 30.0)

        # Multi-month "từ đầu tháng ... đến hết tháng ..." (May + June: 5 canceled / 14 orders = 35.71%)
        res_span_cnt = resolve_query(
            "Từ đầu tháng 5/2026 đến hết tháng 6/2026 có bao nhiêu đơn bị huỷ?",
            ctx,
            as_of_date="2026-07-01",
        )
        assert res_span_cnt[:2] == ("number", 5)
        res_span_pct = resolve_query(
            "Từ đầu tháng 5/2026 đến hết tháng 6/2026 tỉ lệ đơn bị huỷ là bao nhiêu %?",
            ctx,
            as_of_date="2026-07-01",
        )
        assert res_span_pct[:2] == ("percent", 35.71)
    finally:
        ctx.duckdb_conn.close()

    # Schema without status column -> Gate 1 unanswerable
    no_status = sales.drop(columns=["trang_thai"])
    ctx_no_st = _make_ctx("priv_b3_no_st", {"ban_hang": no_status})
    try:
        t1, _, _ = resolve_query(
            "Tháng 5/2026 có mấy đơn bị huỷ?", ctx_no_st, as_of_date="2026-07-01"
        )
        t2, _, _ = resolve_query(
            "Tỉ lệ huỷ đơn tháng 5/2026 là bao nhiêu %?",
            ctx_no_st,
            as_of_date="2026-07-01",
        )
        assert t1 == "unanswerable" and t2 == "unanswerable"
    finally:
        ctx_no_st.duckdb_conn.close()


def test_slice_b3_wrong_regressions_cust_names_and_unsold_word_boundary():
    from zaic_2026_datamate.intent_parser import resolve_query

    khach = pd.DataFrame(
        [
            {"ma_khach_hang": "KH01", "ten_khach_hang": "Hứa Bích An"},
            {"ma_khach_hang": "KH02", "ten_khach_hang": "Cao Long Minh (0321806537)"},
            {
                "ma_khach_hang": "KH03",
                "ten_khach_hang": "Hoàng Trung Hương - 0971 618 698",
            },
        ]
    )
    # In Jan 2025, KH01 appears unaccented in order table, while KH01 also has accented spelling in catalog/other months
    orders = pd.DataFrame(
        [
            {
                "ma_don_hang": "D1",
                "ngay": "2025-01-05",
                "ma_kh": "KH01",
                "khach_hang": "Hua Bich An",
                "ten_hang": "Espresso (double)",
                "so_luong": 2,
                "thanh_tien": 200_000.0,
                "trang_thai": "Hoàn thành",
            },
            {
                "ma_don_hang": "D2",
                "ngay": "2025-01-06",
                "ma_kh": "KH02",
                "khach_hang": "Cao Long Minh (0321806537)",
                "ten_hang": "Espresso (single)",
                "so_luong": 1,
                "thanh_tien": 300_000.0,
                "trang_thai": "Hoàn thành",
            },
            {
                "ma_don_hang": "D3",
                "ngay": "2025-01-07",
                "ma_kh": "KH03",
                "khach_hang": "Hoàng Trung Hương - 0971 618 698",
                "ten_hang": "Espresso (double)",
                "so_luong": 3,
                "thanh_tien": 100_000.0,
                "trang_thai": "Hoàn thành",
            },
        ]
    )
    ctx = _make_ctx("priv_b3_reg", {"don_hang_chi_tiet": orders, "khach_hang": khach})
    try:
        res_top = resolve_query(
            "Tháng 1/2025, 3 khách hàng nào mua nhiều tiền nhất?",
            ctx,
            as_of_date="2025-02-01",
        )
        assert res_top[:2] == (
            "list",
            ["Cao Long Minh", "Hứa Bích An", "Hoàng Trung Hương"],
        ), res_top

        # Unsold trigger 'hang e' must NOT match 'mat hang Espresso (double)'!
        res_esp = resolve_query(
            "thang 1/2025 mat hang Espresso (double) ban duoc tong cong bao nhieu ly",
            ctx,
            as_of_date="2025-02-01",
        )
        assert res_esp[:2] == ("number", 5), res_esp
    finally:
        ctx.duckdb_conn.close()


def test_slice_b4_delivery_rounds_rulebook_s6():
    """Slice B4: VLXD delivery rounds triggers/MTD cutoff & ASCII 'tu ... den het ...' range."""
    import pandas as pd
    from zaic_2026_datamate.intent_parser import resolve_query

    catalog_df = pd.DataFrame(
        [
            {"ten_hang_hoa": "Đá 1x2", "don_vi_tinh": "m3", "trang_thai": "Đang bán"},
            {
                "ten_hang_hoa": "Xi măng Hà Tiên",
                "don_vi_tinh": "bao",
                "trang_thai": "Đang bán",
            },
            {
                "ten_hang_hoa": "Thép Hòa Phát",
                "don_vi_tinh": "cây",
                "trang_thai": "Đang bán",
            },
        ]
    )
    sales_df = pd.DataFrame(
        [
            {
                "ma_don": "ORD000",
                "ngay_ban": "2025-02-20",
                "ten_hang": "Thép Hòa Phát",
                "so_luong": 4,
                "tong_tien": 400000,
                "trang_thai": "Hoàn thành",
            },
            {
                "ma_don": "ORD001",
                "ngay_ban": "2025-03-01",
                "ten_hang": "Đá 1x2",
                "so_luong": 2,
                "tong_tien": 500000,
                "trang_thai": "Trả hàng",
            },
            {
                "ma_don": "ORD002",
                "ngay_ban": "2025-03-05",
                "ten_hang": "Xi măng Hà Tiên",
                "so_luong": 5,
                "tong_tien": 450000,
                "trang_thai": "Hoàn thành",
            },
            {
                "ma_don": "ORD010",
                "ngay_ban": "2025-09-07",
                "ten_hang": "Đá 1x2",
                "so_luong": 1,
                "tong_tien": 250000,
                "trang_thai": "Hoàn thành",
            },
            {
                "ma_don": "ORD011",
                "ngay_ban": "2025-09-19",
                "ten_hang": "Xi măng Hà Tiên",
                "so_luong": 3,
                "tong_tien": 270000,
                "trang_thai": "Hoàn thành",
            },
            {
                "ma_don": "ORD012",
                "ngay_ban": "2025-09-20",
                "ten_hang": "Thép Hòa Phát",
                "so_luong": 10,
                "tong_tien": 1000000,
                "trang_thai": "Hoàn thành",
            },
        ]
    )
    delivery_df = pd.DataFrame(
        [
            # Two item lines for ORD001 round 1 -> 1 delivery round
            {
                "ngay_giao_hang": pd.Timestamp("2025-03-01"),
                "ma_don_hang": "ORD001",
                "lan_giao": 1.0,
                "ten_hang_hoa": "Đá 1x2",
            },
            {
                "ngay_giao_hang": pd.Timestamp("2025-03-01"),
                "ma_don_hang": "ORD001",
                "lan_giao": 1.0,
                "ten_hang_hoa": "Xi măng Hà Tiên",
            },
            # Round 2 of returned ORD001 still counts per Rulebook §6
            {
                "ngay_giao_hang": pd.Timestamp("2025-03-02"),
                "ma_don_hang": "ORD001",
                "lan_giao": 2.0,
                "ten_hang_hoa": "Đá 1x2",
            },
            {
                "ngay_giao_hang": pd.Timestamp("2025-03-05"),
                "ma_don_hang": "ORD002",
                "lan_giao": 1.0,
                "ten_hang_hoa": "Xi măng Hà Tiên",
            },
            # MTD checks around as_of_date=2026-09-10
            {
                "ngay_giao_hang": pd.Timestamp("2026-09-02"),
                "ma_don_hang": "ORD003",
                "lan_giao": 1.0,
                "ten_hang_hoa": "Đá 1x2",
            },
            {
                "ngay_giao_hang": pd.Timestamp("2026-09-08"),
                "ma_don_hang": "ORD004",
                "lan_giao": 1.0,
                "ten_hang_hoa": "Đá 1x2",
            },
            {
                "ngay_giao_hang": pd.Timestamp("2026-09-12"),
                "ma_don_hang": "ORD005",
                "lan_giao": 1.0,
                "ten_hang_hoa": "Đá 1x2",
            },
        ]
    )
    ctx = _make_ctx(
        "priv_vlxd_b4",
        {
            "danh_sach_hang_hoa": catalog_df,
            "so_chi_tiet_ban_hang": sales_df,
            "bang_ke_phieu_giao_hang": delivery_df,
        },
    )
    try:
        for q in [
            "trong thang 3/2025, xe cho duoc bao nhiu dot hang",
            "tháng 3/2025 xe ddax giao total mấy đợt hàng",
            "trong tháng 3/2025, xe chở đươc bn đợt hàng",
            "check gium thang 3/2025 cua hang da cho giao bnhiu dot hang cho khach",
        ]:
            res = resolve_query(q, ctx, as_of_date="2026-09-10")
            assert res[:2] == ("number", 3), (q, res)

        # MTD cutoff at as_of_date='2026-09-10' excludes 2026-09-12
        res_mtd = resolve_query(
            "tháng này cửa hàng đã chở giao bao nhiêu đợt hàng cho khách nhỉ",
            ctx,
            as_of_date="2026-09-10",
        )
        assert res_mtd[:2] == ("number", 2), res_mtd

        # ASCII 'tu ... den het ...' date range in unsold query
        res_unsold = resolve_query(
            "e co bao nhiu mat hang van dang ban ma trong giai doan tu 06/09/2025 den het 19/09/2025 khong ban ra duoc cai nao",
            ctx,
            as_of_date="2026-09-25",
        )
        assert res_unsold[:2] == ("number", 1), res_unsold
    finally:
        ctx.duckdb_conn.close()


def test_slice_b5_cafe_size_and_paren_items():
    """Slice B5: Cafe size M/L beverage counts ('do uong' != dosage) & parenthesized '(M)/(L)' items."""
    import pandas as pd
    from zaic_2026_datamate.intent_parser import resolve_query

    sales_df = pd.DataFrame(
        [
            {
                "ma_don": "OD1",
                "ngay": "2025-02-05",
                "ten_mon": "Sữa tươi trân châu đường đen (L)",
                "nhom_hang": "trà sữa",
                "sl": 4,
                "thanh_tien": 160000,
                "trang_thai": "Hoàn thành",
            },
            {
                "ma_don": "OD2",
                "ngay": "2025-02-06",
                "ten_mon": "Cà phê sữa (L)",
                "nhom_hang": "cà phê",
                "sl": 6,
                "thanh_tien": 180000,
                "trang_thai": "Hoàn thành",
            },
            {
                "ma_don": "OD3",
                "ngay": "2025-06-10",
                "ten_mon": "Sữa tươi trân châu đường đen (L)",
                "nhom_hang": "trà sữa",
                "sl": 5,
                "thanh_tien": 200000,
                "trang_thai": "Hoàn thành",
            },
            {
                "ma_don": "OD4",
                "ngay": "2025-06-11",
                "ten_mon": "Sữa tươi trân châu đường đen (M)",
                "nhom_hang": "trà sữa",
                "sl": 3,
                "thanh_tien": 90000,
                "trang_thai": "Hoàn thành",
            },
            {
                "ma_don": "OD5",
                "ngay": "2026-01-03",
                "ten_mon": "Sữa tươi trân châu đường đen (M)",
                "nhom_hang": "trà sữa",
                "sl": 7,
                "thanh_tien": 210000,
                "trang_thai": "Hoàn thành",
            },
            {
                "ma_don": "OD6",
                "ngay": "2026-01-04",
                "ten_mon": "Bánh mì (M)",
                "nhom_hang": "bánh",
                "sl": 2,
                "thanh_tien": 50000,
                "trang_thai": "Hoàn thành",
            },
            {
                "ma_don": "OD7",
                "ngay": "2026-06-05",
                "ten_mon": "Cà phê sữa (M)",
                "nhom_hang": "cà phê",
                "sl": 8,
                "thanh_tien": 200000,
                "trang_thai": "Hoàn thành",
            },
            {
                "ma_don": "OD8",
                "ngay": "2026-06-20",
                "ten_mon": "Cà phê sữa (M)",
                "nhom_hang": "cà phê",
                "sl": 9,
                "thanh_tien": 225000,
                "trang_thai": "Hoàn thành",
            },
        ]
    )
    ctx = _make_ctx("priv_cafe_b5", {"don_hang_chi_tiet": sales_df})
    try:
        # 1. Size M / L across all drinks (must exclude 'bánh' and not trip clinical 'uong')
        r1 = resolve_query(
            "Trong tháng 1/2026, số ly size M bán được là bao nhiêu, tính mọi loại đồ uống?",
            ctx,
            as_of_date="2026-06-15",
        )
        assert r1[:2] == ("number", 7), r1

        r2 = resolve_query(
            "ê trong tháng 2/2025, soos ly size l bán dc là nhiêu, tính mọi loại ddoof uống ta",
            ctx,
            as_of_date="2026-06-15",
        )
        assert r2[:2] == ("number", 10), r2

        # MTD cutoff at as_of_date='2026-06-15' excludes OD8 on 2026-06-20
        r3 = resolve_query(
            "cho hoi trong thang nay, so ly size m ban duoc la bao nhieu, tinh moi loai do uong do",
            ctx,
            as_of_date="2026-06-15",
        )
        assert r3[:2] == ("number", 8), r3

        # 2. Specific parenthesized (M)/(L) items: revenue & quantity
        r4 = resolve_query(
            "dthu cuar Sữa tươi trân châu đường đen (L) trong thangs 6 năm 2025 là bao nhiêu",
            ctx,
            as_of_date="2026-06-15",
        )
        assert r4[:2] == ("number", 200000), r4

        r5 = resolve_query(
            "Trong tháng 6/2025 mặt hàng Sữa tươi trân châu đường đen (M) thu về bao nhiêu tiền?",
            ctx,
            as_of_date="2026-06-15",
        )
        assert r5[:2] == ("number", 90000), r5

        r6 = resolve_query(
            "Trong tháng 6 năm 2025 cửa hàng bán đc mấy ly Sữa tươi trân châu đường đen (L)?",
            ctx,
            as_of_date="2026-06-15",
        )
        assert r6[:2] == ("number", 5), r6
    finally:
        ctx.duckdb_conn.close()


def test_slice_b6_yoy_revenue_growth_pct():
    """Slice B6: YoY growth % for 'thang M nam Y1 vs thang M nam Y0' and 'cung ky nam trc'."""
    import pandas as pd
    from zaic_2026_datamate.pipeline import build_agent, production_pipeline_config

    sales_df = pd.DataFrame(
        [
            {
                "ma_don": "O1",
                "ngay_ban": "2025-03-10",
                "ten_hang": "A",
                "sl": 1,
                "tong_tien": 100000,
                "trang_thai": "Hoàn thành",
            },
            {
                "ma_don": "O2",
                "ngay_ban": "2026-03-10",
                "ten_hang": "A",
                "sl": 1,
                "tong_tien": 115000,
                "trang_thai": "Hoàn thành",
            },
            {
                "ma_don": "O3",
                "ngay_ban": "2025-04-10",
                "ten_hang": "A",
                "sl": 1,
                "tong_tien": 200000,
                "trang_thai": "Hoàn thành",
            },
            {
                "ma_don": "O4",
                "ngay_ban": "2026-04-10",
                "ten_hang": "A",
                "sl": 1,
                "tong_tien": 180000,
                "trang_thai": "Hoàn thành",
            },
            {
                "ma_don": "O5",
                "ngay_ban": "2026-04-11",
                "ten_hang": "A",
                "sl": 1,
                "tong_tien": 50000,
                "trang_thai": "Trả hàng",
            },
            {
                "ma_don": "O6",
                "ngay_ban": "2025-08-10",
                "ten_hang": "A",
                "sl": 1,
                "tong_tien": 400000,
                "trang_thai": "Hoàn thành",
            },
            {
                "ma_don": "O7",
                "ngay_ban": "2026-08-10",
                "ten_hang": "A",
                "sl": 1,
                "tong_tien": 443520,
                "trang_thai": "Hoàn thành",
            },
        ]
    )
    ctx = _make_ctx("priv_yoy_b6", {"so_ban_hang": sales_df})
    agent = build_agent(production_pipeline_config())
    try:
        r1 = agent.answer_query(
            "Doanh thu tháng 4 năm 2026 tăng trưởng bao nhiêu % so với tháng 4 năm 2025?",
            ctx,
            as_of_date="2026-09-07",
        )
        assert r1[:2] == ("percent", -10.0), r1

        r2 = agent.answer_query(
            "revenue thang 3 nam 2026 tang truong bnhiu % so vs thang 3 nam 2025",
            ctx,
            as_of_date="2026-09-07",
        )
        assert r2[:2] == ("percent", 15.0), r2

        r3 = agent.answer_query(
            "coi gium so cung ky nam trc, dthu thang 8/2026 tang/giam may phan tram vay",
            ctx,
            as_of_date="2026-09-07",
        )
        assert r3[:2] == ("percent", 10.88), r3
    finally:
        ctx.duckdb_conn.close()


def test_slice_b7_misc_unlocks():
    """Slice B7: Telex 'cuwar', order/AOV/threshold/bestseller/units, dual payment, snapshot stockout, and pre-discount single-date unit price."""
    import pandas as pd
    from zaic_2026_datamate.pipeline import build_agent, production_pipeline_config
    from zaic_2026_datamate.price_change import resolve_unit_price_on_date

    agent = build_agent(production_pipeline_config())

    # 1. Store total revenue, order count ("soos đơn" + "cho tooi" + "hqua", "bn order"), AOV ("moi order"), threshold ("co nhieu ngay"), bestseller ("5 best seller")
    sales_df = pd.DataFrame(
        [
            {
                "ma_hoa_don": "O1",
                "ngay_ban": "2025-08-10",
                "ten_hang": "Bia Tiger lon",
                "sl": 5,
                "thanh_tien": 100000,
                "trang_thai": "Hoàn thành",
            },
            {
                "ma_hoa_don": "O2",
                "ngay_ban": "2025-08-12",
                "ten_hang": "Bia Tiger lon",
                "sl": 4,
                "thanh_tien": 80000,
                "trang_thai": "Hoàn thành",
            },
            {
                "ma_hoa_don": "O3",
                "ngay_ban": "2025-08-12",
                "ten_hang": "Rau muống xào tỏi",
                "sl": 2,
                "thanh_tien": 120000,
                "trang_thai": "Hoàn thành",
            },
            {
                "ma_hoa_don": "O4",
                "ngay_ban": "2025-10-05",
                "ten_hang": "Bia Sài Gòn lon",
                "sl": 3,
                "thanh_tien": 60000,
                "trang_thai": "Hoàn thành",
            },
            {
                "ma_hoa_don": "O5",
                "ngay_ban": "2025-11-15",
                "ten_hang": "Bia Sài Gòn lon",
                "sl": 2,
                "thanh_tien": 40000,
                "trang_thai": "Hoàn thành",
            },
            {
                "ma_hoa_don": "O6",
                "ngay_ban": "2026-02-05",
                "ten_hang": "Mon A",
                "sl": 10,
                "thanh_tien": 5000000,
                "trang_thai": "Hoàn thành",
            },
            {
                "ma_hoa_don": "O7",
                "ngay_ban": "2026-02-05",
                "ten_hang": "Mon B",
                "sl": 8,
                "thanh_tien": 8000000,
                "trang_thai": "Hoàn thành",
            },
            {
                "ma_hoa_don": "O8",
                "ngay_ban": "2026-02-06",
                "ten_hang": "Mon C",
                "sl": 6,
                "thanh_tien": 13000000,
                "trang_thai": "Hoàn thành",
            },
            {
                "ma_hoa_don": "O9",
                "ngay_ban": "2026-02-07",
                "ten_hang": "Mon D",
                "sl": 4,
                "thanh_tien": 4000000,
                "trang_thai": "Hoàn thành",
            },
            {
                "ma_hoa_don": "O10",
                "ngay_ban": "2026-02-08",
                "ten_hang": "Mon E",
                "sl": 3,
                "thanh_tien": 3000000,
                "trang_thai": "Hoàn thành",
            },
            {
                "ma_hoa_don": "O11",
                "ngay_ban": "2026-02-09",
                "ten_hang": "Mon F",
                "sl": 1,
                "thanh_tien": 1000000,
                "trang_thai": "Hoàn thành",
            },
        ]
    )
    ctx_store = _make_ctx("priv_b7_store", {"so_ban_hang": sales_df})
    try:
        assert agent.answer_query(
            "tháng 8/2025 cuwar hàng bán được tổng coongj nhiêu tiền",
            ctx_store,
            as_of_date="2026-03-01",
        )[:2] == ("number", 300000)
        assert agent.answer_query(
            "tháng 8/2025 cuwar hàng bán được tổng cộng bao nhiêu tiền",
            ctx_store,
            as_of_date="2026-03-01",
        )[:2] == ("number", 300000)
        assert agent.answer_query(
            "check giùm cho tooi biết soos đơn hqua", ctx_store, as_of_date="2025-08-13"
        )[:2] == ("number", 2)
        assert agent.answer_query(
            "quý 4/2025 cuwar hàng có bn order ddos", ctx_store, as_of_date="2026-03-01"
        )[:2] == ("number", 2)
        assert agent.answer_query(
            "coi gium thang 8/2025 dthu binh quan moi order la bao nhieu ha",
            ctx_store,
            as_of_date="2026-03-01",
        )[:2] == ("number", 100000)
        assert agent.answer_query(
            "tháng 2/2026 có nhiêu ngày đạt dthu từ 12.000.000 đồng trở leen",
            ctx_store,
            as_of_date="2026-03-01",
        )[:2] == ("number", 2)
        assert agent.answer_query(
            "5 best seller thang 2/2026 la nhung mat hang nao",
            ctx_store,
            as_of_date="2026-03-01",
        )[:2] == ("list", ["Mon A", "Mon B", "Mon C", "Mon D", "Mon E"])
    finally:
        ctx_store.duckdb_conn.close()

    # 2. Units (cuc, bao, m3) & Dual payment ("trả bằng COD ... ví điện tử")
    unit_sales = pd.DataFrame(
        [
            {
                "ma_hoa_don": "U1",
                "ngay_ban": "2026-03-10",
                "ten_hang": "Xà bông Lifebuoy 90g",
                "don_vi": "cục",
                "sl": 15,
                "sl_quy_doi": 15,
                "thanh_tien": 150000,
                "phuong_thuc_thanh_toan": "COD",
                "trang_thai": "Hoàn thành",
            },
            {
                "ma_hoa_don": "U2",
                "ngay_ban": "2026-03-10",
                "ten_hang": "Keo dán gạch Weber.tai fix xám 25kg",
                "don_vi": "bao",
                "sl": 12,
                "sl_quy_doi": 12,
                "thanh_tien": 1200000,
                "phuong_thuc_thanh_toan": "Ví điện tử",
                "trang_thai": "Hoàn thành",
            },
            {
                "ma_hoa_don": "U3",
                "ngay_ban": "2026-03-11",
                "ten_hang": "Cát vàng xây tô",
                "don_vi": "m3",
                "sl": 7,
                "sl_quy_doi": 7,
                "thanh_tien": 2100000,
                "phuong_thuc_thanh_toan": "COD",
                "trang_thai": "Hoàn thành",
            },
            {
                "ma_hoa_don": "U4",
                "ngay_ban": "2026-03-12",
                "ten_hang": "Đá 1x2",
                "don_vi": "m3",
                "sl": 5,
                "sl_quy_doi": 5,
                "thanh_tien": 1500000,
                "phuong_thuc_thanh_toan": "Ví điện tử",
                "trang_thai": "Hoàn thành",
            },
        ]
    )
    ctx_unit = _make_ctx("priv_b7_units", {"so_ban_hang": unit_sales})
    try:
        assert agent.answer_query(
            "coi gium thang 3 nam 2026 ban ra bao nhiu cuc xa bong lifebuoy 90g vay",
            ctx_unit,
            as_of_date="2026-04-01",
        )[:2] == ("number", 15)
        assert agent.answer_query(
            "tháng 3/2026 bán ra bao nhiêu bao keo dán gạch weber.tai fix xám 25kg",
            ctx_unit,
            as_of_date="2026-04-01",
        )[:2] == ("number", 12)
        assert agent.answer_query(
            "Cho mình hỏi 7 ngày gần nhất cửa hàng bán ra tổng cộng mấy m3?",
            ctx_unit,
            as_of_date="2026-03-14",
        )[:2] == ("number", 12)
        assert agent.answer_query(
            "Doanh thu các đơn trả bằng COD và doanh thu các đơn trả bằng ví điện tử tháng 3/2026 lần lượt là bao nhiêu?",
            ctx_unit,
            as_of_date="2026-04-01",
        )[:2] == ("list", [2250000, 2700000])
        assert (
            agent.answer_query(
                "So dthu các đơn trả bằng COD với doanh thu các đơn trả bằng ví điện tử tháng 2/2026: mỗi bên bao nhiêu?",
                ctx_store,
                as_of_date="2026-04-01",
            )[0]
            == "unanswerable"
        )
    finally:
        ctx_unit.duckdb_conn.close()

    # 3. Inventory stockout via bao_cao_ton_kho when the_kho absent (strictly gated to as_of_date)
    ton_df = pd.DataFrame(
        [
            {
                "ma_hang": "SP1",
                "ten_hang": "Hapacol 250",
                "dvt": "gói",
                "ton_cuoi_ky": 0,
            },
            {
                "ma_hang": "SP2",
                "ten_hang": "Efferalgan",
                "dvt": "viên",
                "ton_cuoi_ky": 10,
            },
        ]
    )
    ctx_stock = _make_ctx(
        "priv_b7_stock",
        {
            "bao_cao_ton_kho": ton_df,
            "so_ban_hang": pd.DataFrame(
                [
                    {
                        "ma_hoa_don": "S1",
                        "ngay_ban": "2026-06-10",
                        "ten_hang": "Hapacol 250",
                        "sl": 1,
                        "thanh_tien": 5000,
                        "trang_thai": "Hoàn thành",
                    }
                ]
            ),
        },
    )
    try:
        assert agent.answer_query(
            "Đến cuối ngày 12/06/2026, shop có mấy mặt hàng đang hết hàng?",
            ctx_stock,
            as_of_date="2026-06-12",
        )[:2] == ("number", 1)
        assert (
            agent.answer_query(
                "Đến cuối ngày 10/06/2026, shop có mấy mặt hàng đang hết hàng?",
                ctx_stock,
                as_of_date="2026-06-12",
            )[0]
            == "abstain"
        )
    finally:
        ctx_stock.duckdb_conn.close()

    # 4. Single-date unit price: (a) noi_dung + quy_ra_dv_le; (b) mat_hang + sl_ban; (c) pre-discount unit price with line discount
    ctx_up_a = _make_ctx(
        "priv_b7_up_a",
        {
            "ban_hang": pd.DataFrame(
                [
                    {
                        "ma_hoa_don": "P1",
                        "ngay_thang": "2025-09-25",
                        "ket_qua": "Xong",
                        "noi_dung": "Kẹo Alpenliebe 100g",
                        "don_vi_tinh": "thùng",
                        "so_luong": 1,
                        "quy_ra_dv_le": 40,
                        "thanh_tien": 480000,
                    },
                    {
                        "ma_hoa_don": "P2",
                        "ngay_thang": "2025-09-25",
                        "ket_qua": "Xong",
                        "noi_dung": "Kẹo Alpenliebe 100g",
                        "don_vi_tinh": "gói",
                        "so_luong": 2,
                        "quy_ra_dv_le": 2,
                        "thanh_tien": 24000,
                    },
                ]
            ),
        },
    )
    ctx_up_b = _make_ctx(
        "priv_b7_up_b",
        {
            "so_ban_hang": pd.DataFrame(
                [
                    {
                        "ngay_mua": "2025-10-04",
                        "ghi_chu_tt": "xong",
                        "mat_hang": "Ống PVC Bình Minh D50 cây 4m",
                        "don_vi": "cây",
                        "sl_ban": 2,
                        "so_luong_le": 2,
                        "thanh_tien": 250000,
                    },
                ]
            ),
        },
    )
    ctx_up_c = _make_ctx(
        "priv_b7_up_c",
        {
            "danh_sach_hoa_don": pd.DataFrame(
                [
                    {
                        "ma_hoa_don": "D1",
                        "thoi_gian": "2025-10-10",
                        "trang_thai": "xong",
                    },
                    {
                        "ma_hoa_don": "D2",
                        "thoi_gian": "2025-10-10",
                        "trang_thai": "xong",
                    },
                ]
            ),
            "danh_sach_hoa_don_chi_tiet": pd.DataFrame(
                [
                    {
                        "ma_hoa_don": "D1",
                        "ma_hang": "SP9",
                        "ten_hang": "Trứng chiên thịt bằm",
                        "don_vi_tinh": "đĩa",
                        "so_luong": 1,
                        "so_luong_quy_doi": 1,
                        "don_gia": 45000,
                        "giam_gia": 0,
                        "thanh_tien": 45000,
                    },
                    {
                        "ma_hoa_don": "D2",
                        "ma_hang": "SP9",
                        "ten_hang": "Trứng chiên thịt bằm",
                        "don_vi_tinh": "đĩa",
                        "so_luong": 10,
                        "so_luong_quy_doi": 10,
                        "don_gia": 45000,
                        "giam_gia": 22000,
                        "thanh_tien": 428000,
                    },
                ]
            ),
        },
    )
    try:
        assert resolve_unit_price_on_date(
            "xem giúp ngày 25/09/2025 cửa hàng bán Kẹo Alpenliebe 100g giá bao nhiêu một gói",
            ctx_up_a,
            as_of_date="2026-08-24",
        )[:2] == ("number", 12000)
        assert resolve_unit_price_on_date(
            "Hôm 04/10/2025, Ống PVC Bình Minh D50 cây 4m bán ra với đơn giá bao nhiêu tiền một cây?",
            ctx_up_b,
            as_of_date="2026-08-09",
        )[:2] == ("number", 125000)
        assert resolve_unit_price_on_date(
            "Giá bán một đĩa Trứng chiên thịt bằm vào ngày 10/10/2025 là bao nhiêu?",
            ctx_up_c,
            as_of_date="2026-09-07",
        )[:2] == ("number", 45000)
    finally:
        ctx_up_a.duckdb_conn.close()
        ctx_up_b.duckdb_conn.close()
        ctx_up_c.duckdb_conn.close()


def test_mui11_public_regression_fixes():
    from zaic_2026_datamate.domain_ext import resolve_domain_extensions
    from zaic_2026_datamate.intent_parser import resolve_query

    # 1. Regression 1: "đơn trả ví điện tử" must NOT trigger asks_return_reason ("tra vi")
    ban_hang_pay = pd.DataFrame(
        [
            {
                "ma_don_hang": "DH01",
                "ngay": "2025-08-05",
                "ten_hang": "Cà phê sữa",
                "so_luong": 1,
                "thanh_tien": 62_390_000.0,
                "hinh_thuc_thanh_toan": "Tiền mặt",
                "trang_thai": "Hoàn thành",
            },
            {
                "ma_don_hang": "DH02",
                "ngay": "2025-08-06",
                "ten_hang": "Trà đào",
                "so_luong": 1,
                "thanh_tien": 7_123_000.0,
                "hinh_thuc_thanh_toan": "Ví điện tử",
                "trang_thai": "Hoàn thành",
            },
        ]
    )
    ctx_pay = _make_ctx("test_shop_pay", {"ban_hang": ban_hang_pay})
    try:
        q_pay = (
            "Tháng 8/2025 tổng doanh thu của các đơn trả tiền mặt với đơn trả ví điện tử "
            "lần lượt là bao nhiêu?"
        )
        assert (
            resolve_domain_extensions(q_pay, ctx_pay, as_of_date="2025-09-01") is None
        )
        assert resolve_query(q_pay, ctx_pay, as_of_date="2025-09-01")[:2] == (
            "list",
            [62390000, 7123000],
        )
    finally:
        ctx_pay.duckdb_conn.close()

    # 2. Regression 2: "06/2025 nhóm máy cũ bán được bnhiu tiền?" -> revenue (168,700,000), not count (21)
    so_ban_hang = pd.DataFrame(
        [
            {
                "stt": 1,
                "ngay": "2025-06-10",
                "ten_hang": "iPhone 13 cũ",
                "loai": "Máy cũ",
                "so_luong": 21,
                "thanh_tien": 168_700_000.0,
            },
            {
                "stt": 2,
                "ngay": "2025-06-11",
                "ten_hang": "iPhone 16 mới",
                "loai": "Máy mới",
                "so_luong": 5,
                "thanh_tien": 120_000_000.0,
            },
        ]
    )
    so_ban_hang_ghi_tat = pd.DataFrame(
        [
            {
                "ngay": "2025-06-10",
                "ten_hang": "iPhone 13 cũ",
                "loai": "Máy cũ",
                "so_luong": 21,
            }
        ]
    )
    ctx_phone = _make_ctx(
        "test_shop_phone",
        {
            "so_ban_hang": so_ban_hang,
            "so_ban_hang_ghi_tat": so_ban_hang_ghi_tat,
        },
    )
    try:
        q_phone = "06/2025 nhóm máy cũ bán được bnhiu tiền?"
        assert (
            resolve_domain_extensions(q_phone, ctx_phone, as_of_date="2025-07-01")
            is None
        )
        assert resolve_query(q_phone, ctx_phone, as_of_date="2025-07-01")[:2] == (
            "number",
            168700000,
        )
    finally:
        ctx_phone.duckdb_conn.close()

    # 3. Regression 3: combined table with ma_khach + ho_ten_khach resolves full customer names
    ban_hang_cust = pd.DataFrame(
        [
            {
                "ma_don_hang": f"DH{i:02d}",
                "ngay": "2025-08-12",
                "ma_khach": cid,
                "ho_ten_khach": cname,
                "ten_hang": "Sản phẩm A",
                "so_luong": 1,
                "thanh_tien": rev,
            }
            for i, (cid, cname, rev) in enumerate(
                [
                    ("KH0143", "Triệu Dương Đức", 900_000.0),
                    ("KH0144", "Ngô Hồng Duy", 800_000.0),
                    ("KH0145", "Phạm Thảo Loan", 700_000.0),
                    ("KH0146", "Diệp Thanh Quang", 600_000.0),
                    ("KH0147", "Phùng Bảo Khoa", 500_000.0),
                    ("KH0148", "Lê Văn An", 100_000.0),
                ],
                start=1,
            )
        ]
    )
    ctx_cust = _make_ctx("test_shop_cust", {"ban_hang": ban_hang_cust})
    try:
        res_cust = resolve_query(
            "Top 5 khách hàng mua nhiều tiền nhất trong tháng 08/2025 là ai?",
            ctx_cust,
            as_of_date="2025-09-01",
        )
        assert res_cust[:2] == (
            "list",
            [
                "Triệu Dương Đức",
                "Ngô Hồng Duy",
                "Phạm Thảo Loan",
                "Diệp Thanh Quang",
                "Phùng Bảo Khoa",
            ],
        )
    finally:
        ctx_cust.duckdb_conn.close()

    # 4. Regression 4: stt-only table (order_col=None in find_primary_sales_table) counts sheet/date-disambiguated orders
    ban_hang_stt = pd.DataFrame(
        [
            {
                "sheet_name": "Thang_10_2025",
                "stt": 1,
                "ngay": "2025-10-05",
                "ten_hang": "Gói A",
                "so_luong": 1,
                "thanh_tien": 100_000.0,
            },
            {
                "sheet_name": "Thang_10_2025",
                "stt": 2,
                "ngay": "2025-10-05",
                "ten_hang": "Gói B",
                "so_luong": 1,
                "thanh_tien": 200_000.0,
            },
            {
                "sheet_name": "Thang_11_2025",
                "stt": 1,
                "ngay": "2025-11-12",
                "ten_hang": "Gói C",
                "so_luong": 1,
                "thanh_tien": 300_000.0,
            },
            {
                "sheet_name": "Thang_12_2025",
                "stt": 1,
                "ngay": "2025-12-20",
                "ten_hang": "Gói D",
                "so_luong": 1,
                "thanh_tien": 400_000.0,
            },
        ]
    )
    ctx_stt = _make_ctx("test_shop_stt", {"ban_hang": ban_hang_stt})
    try:
        assert resolve_query(
            "Hôm qua tiệm bán được mấy đơn?", ctx_stt, as_of_date="2025-10-06"
        )[:2] == ("number", 2)
        assert resolve_query(
            "Quý 4/2025 tiệm bán được bao nhiêu đơn vậy?",
            ctx_stt,
            as_of_date="2026-01-05",
        )[:2] == ("number", 4)
    finally:
        ctx_stt.duckdb_conn.close()
