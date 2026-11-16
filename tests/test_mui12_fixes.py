"""Mũi 12 TDD regression specs (Slice 1+: schema-general fixes, synthetic fixtures only)."""

from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd


def _make_ctx(business_id: str, tables: dict[str, pd.DataFrame]):
    from zaic_2026_datamate.data_loader import BusinessContext

    conn = duckdb.connect()
    for name, df in tables.items():
        conn.register(name, df)
    return BusinessContext(
        business_id=business_id,
        data_dir=Path("."),
        tables=tables,
        duckdb_conn=conn,
    )


def test_slice1_return_vocabulary_recovery_and_false_positive_safety():
    from zaic_2026_datamate.semantic_contract import is_bad_status, is_canceled_status

    missed_or_required_returns = (
        "khách mang trả",
        "Khách mang trả",
        "khach mang tra",
        "mang trả",
        "mang tra",
        "đã mang trả",
        "trả về",
        "tra ve",
        "trả lại hàng",
        "khách trả lại",
        "Khách trả",
        "Trả hàng",
        "Đã trả hàng",
        "Hàng trả về",
        "Đã hoàn hàng",
        "Hoàn đơn",
        "hàng hoàn",
    )
    for bad_s in missed_or_required_returns:
        assert is_bad_status(bad_s), f"Missed return status: {bad_s!r}"
        assert not is_canceled_status(bad_s), (
            f"Return misclassified as cancel: {bad_s!r}"
        )

    safe_non_bad_values = (
        "đã trả",
        "trả đủ",
        "trả góp",
        "chưa trả",
        "trả trước",
        "nợ, đã trả đủ",
        "ghi nợ 1.038.500đ, đã trả đủ",
        "trả 259k, nợ 449.500đ",
        "khách đưa trước 366k, còn thiếu 853 nghìn",
        "kiểm tra lại tuần sau",
        "kiểm tra hàng trước khi nhận",
        "bảo hành, phiếu 9/6",
        "đổi máy khác",
        "Đã nhập hàng",
        "Đang giao",
        "đang ship",
        "ok",
        "Đã giao",
        "Đã hoàn thành",
    )
    for ok_s in safe_non_bad_values:
        assert not is_bad_status(ok_s), f"False-positive bad status: {ok_s!r}"


def test_slice1_contract_and_sql_status_parity_with_mang_tra():
    from zaic_2026_datamate.intent_parser import (
        find_primary_sales_table,
        get_valid_sales_status_clause,
        resolve_query,
    )
    from zaic_2026_datamate.semantic_contract import BusinessSemanticContract

    # Case A: Line-level notebook schema (no order_id, return markers in ghi_chu)
    sbh = pd.DataFrame(
        [
            {
                "ngay": "2025-08-05",
                "ten_hang": "SP 1",
                "thanh_tien": 500_000.0,
                "ghi_chu": None,
            },
            {
                "ngay": "2025-08-06",
                "ten_hang": "SP 2",
                "thanh_tien": 300_000.0,
                "ghi_chu": "ghi nợ 300k, đã trả đủ",
            },
            {
                "ngay": "2025-08-07",
                "ten_hang": "SP 3",
                "thanh_tien": 200_000.0,
                "ghi_chu": "kiểm tra lại tuần sau",
            },
            {
                "ngay": "2025-08-08",
                "ten_hang": "SP 4",
                "thanh_tien": 450_000.0,
                "ghi_chu": "khách mang trả",
            },
            {
                "ngay": "2025-08-09",
                "ten_hang": "SP 5",
                "thanh_tien": 150_000.0,
                "ghi_chu": "trả về",
            },
            {
                "ngay": "2025-08-10",
                "ten_hang": "SP 6",
                "thanh_tien": 250_000.0,
                "ghi_chu": "trả lại hàng",
            },
        ]
    )
    ctx_a = _make_ctx("synth_s1_notebook", {"so_ban_hang": sbh})
    try:
        tbl, ord_col, _dc, sc = find_primary_sales_table(ctx_a)
        assert tbl == "so_ban_hang"
        assert ord_col is None
        assert sc == "ghi_chu"

        contract = BusinessSemanticContract.from_context(ctx_a)
        valid_mask = contract.lines_df["is_valid_sale"].tolist()
        assert valid_mask == [True, True, True, False, False, False]

        clause = get_valid_sales_status_clause(sc, order_col=ord_col, table=tbl)
        sql_rev = ctx_a.scalar(f"SELECT SUM(thanh_tien) FROM {tbl} WHERE 1=1 {clause}")
        assert float(sql_rev) == 1_000_000.0

        rev_ans = resolve_query(
            "Doanh thu tháng 8/2025 là bao nhiêu?", ctx_a, as_of_date="2025-09-01"
        )
        assert rev_ans[:2] == ("number", 1_000_000)

        q3_ans = resolve_query(
            "Tổng doanh thu quý 3/2025 là bao nhiêu?", ctx_a, as_of_date="2025-10-01"
        )
        assert q3_ans[:2] == ("number", 1_000_000)
    finally:
        ctx_a.duckdb_conn.close()

    # Case B: Multi-line order with partial return row -> whole order excluded per Rulebook §1
    orders = pd.DataFrame(
        [
            {
                "ma_don": "ORD_1",
                "ngay": "2025-08-01",
                "ten_hang": "SP A",
                "thanh_tien": 700_000.0,
                "trang_thai": "Hoàn thành",
            },
            {
                "ma_don": "ORD_2",
                "ngay": "2025-08-02",
                "ten_hang": "SP B1",
                "thanh_tien": 400_000.0,
                "trang_thai": "Hoàn thành",
            },
            {
                "ma_don": "ORD_2",
                "ngay": "2025-08-02",
                "ten_hang": "SP B2",
                "thanh_tien": 200_000.0,
                "trang_thai": "khách mang trả",
            },
            {
                "ma_don": "ORD_3",
                "ngay": "2025-08-03",
                "ten_hang": "SP C",
                "thanh_tien": 300_000.0,
                "trang_thai": "Hoàn thành",
            },
        ]
    )
    ctx_b = _make_ctx("synth_s1_multiline", {"ban_hang": orders})
    try:
        contract = BusinessSemanticContract.from_context(ctx_b)
        assert contract.orders_df.set_index("order_id")["is_valid_sale"].to_dict() == {
            "ORD_1": True,
            "ORD_2": False,
            "ORD_3": True,
        }

        clause = get_valid_sales_status_clause(
            "trang_thai", order_col="ma_don", table="ban_hang"
        )
        sql_rev = ctx_b.scalar(
            f"SELECT SUM(thanh_tien) FROM ban_hang WHERE 1=1 {clause}"
        )
        assert float(sql_rev) == 1_000_000.0
    finally:
        ctx_b.duckdb_conn.close()


def test_slice1_sales_status_aliases_return_vocabulary():
    from zaic_2026_datamate.sales_status import (
        canonical_status_clause,
        classify_sales_status,
    )

    for ret_s in (
        "khách mang trả",
        "mang trả",
        "đã mang trả",
        "trả lại hàng",
        "khách trả",
        "khách trả lại",
        "trả về",
    ):
        assert classify_sales_status(ret_s) == "returned", f"Unmapped return: {ret_s!r}"

    assert classify_sales_status("Chờ đối soát") is None
    assert classify_sales_status("kiểm tra lại tuần sau") is None

    clause = canonical_status_clause(
        '"trang_thai"', '"ma_don"', '"ban_hang"', ["Hoàn thành", "khách mang trả"]
    )
    assert "'khách mang trả'" in clause


def test_slice2_today_cutoff_does_not_overwrite_rolling_or_order_cohort():
    from zaic_2026_datamate.cleaner import normalize_query_telex
    from zaic_2026_datamate.intent_parser import parse_time_expressions, resolve_query

    assert "bao nhiêu" in normalize_query_telex(
        "30 ngay gan nhat quan ban duoc tat ca bao nheiu ly?"
    )

    p1 = parse_time_expressions(
        "7 ngay qua tinh ca hom nay ban duoc may don?", as_of_date="2025-10-09"
    )
    assert not p1.is_today
    assert p1.start_date == "2025-10-03" and p1.end_date == "2025-10-09"

    p2 = parse_time_expressions(
        "Tính luôn hôm nay thì 7 ngày gần nhất quán bán ra được bao nhiêu ly z?",
        as_of_date="2025-06-23",
    )
    assert not p2.is_today
    assert p2.start_date == "2025-06-17" and p2.end_date == "2025-06-23"

    p3 = parse_time_expressions(
        "Tuần này tính tới hôm nay doanh thu cửa hàng là bao nhiêu?",
        as_of_date="2026-05-08",
    )
    assert not p3.is_today
    assert p3.is_this_week
    assert p3.start_date == "2026-05-04" and p3.end_date == "2026-05-08"

    p4 = parse_time_expressions(
        "Các đơn đặt tháng 3/2026, tới hôm nay đã chở đi bao nhiêu thùng Sơn A?",
        as_of_date="2026-04-01",
    )
    assert not p4.is_today
    assert p4.month_str == "2026-03"

    # Comparison query comparing today vs another period MUST still preserve is_today=True
    p_cmp = parse_time_expressions(
        "Doanh thu hôm nay so với 7 ngày qua chênh bao nhiêu?",
        as_of_date="2025-10-09",
    )
    assert p_cmp.is_today and p_cmp.exact_date == "2025-10-09"

    # End-to-end delivery item qty + "bao nheiu ly" unit qty on synthetic data
    sbh = pd.DataFrame(
        [
            {
                "ma_don": "D1",
                "thoi_gian": "2026-03-10",
                "hang_ban": "Sơn A 5L",
                "dvt": "thùng",
                "sl_ban": 5,
                "amount": 5_000_000.0,
                "trang_thai_xu_ly": "Hoàn thành",
            },
            {
                "ma_don": "D2",
                "thoi_gian": "2026-03-29",
                "hang_ban": "Sơn A 5L",
                "dvt": "thùng",
                "sl_ban": 4,
                "amount": 4_000_000.0,
                "trang_thai_xu_ly": "Đang giao",
            },
            {
                "ma_don": "D3",
                "thoi_gian": "2026-03-15",
                "hang_ban": "Sơn A 5L",
                "dvt": "thùng",
                "sl_ban": 10,
                "amount": 10_000_000.0,
                "trang_thai_xu_ly": "Huỷ đơn",
            },
        ]
    )
    sgh = pd.DataFrame(
        [
            {
                "so_don": "D1",
                "thoi_gian_giao": "2026-03-12",
                "hang_hoa": "Sơn A 5L",
                "so_luong_giao": 3,
                "luot_giao": 1,
                "dv_goc": "thùng",
            },
            {
                "so_don": "D1",
                "thoi_gian_giao": "2026-04-05",
                "hang_hoa": "Sơn A 5L",
                "so_luong_giao": 2,
                "luot_giao": 2,
                "dv_goc": "thùng",
            },
            {
                "so_don": "D2",
                "thoi_gian_giao": "2026-04-01",
                "hang_hoa": "Sơn A 5L",
                "so_luong_giao": 2,
                "luot_giao": 1,
                "dv_goc": "thùng",
            },
            {
                "so_don": "D3",
                "thoi_gian_giao": "2026-03-16",
                "hang_hoa": "Sơn A 5L",
                "so_luong_giao": 10,
                "luot_giao": 1,
                "dv_goc": "thùng",
            },
        ]
    )
    ctx_deliv = _make_ctx("synth_s2_deliv", {"so_ban_hang": sbh, "so_giao_hang": sgh})
    try:
        ans = resolve_query(
            "Các đơn đặt tháng 3/2026, tới hôm nay đã chở đi bao nhiêu thùng Sơn A 5L?",
            ctx_deliv,
            as_of_date="2026-04-01",
        )
        # Valid placed 2026-03 = D1 (3 delivered <= 2026-04-01) + D2 (2 delivered on 2026-04-01) = 5; D3 is cancelled
        assert ans[:2] == ("number", 5)
    finally:
        ctx_deliv.duckdb_conn.close()

    cafe = pd.DataFrame(
        [
            {
                "don_so": "C1",
                "ngay_hd": "2025-12-20",
                "hang_hoa": "Trà sữa",
                "dv": "ly",
                "s": 3,
                "sl_dv_co_ban": 3,
                "thanh_tien_d": 150_000.0,
                "trang_thai_giao": "Hoàn thành",
            },
            {
                "don_so": "C1",
                "ngay_hd": "2025-12-20",
                "hang_hoa": "Trân châu",
                "dv": "phần",
                "s": 3,
                "sl_dv_co_ban": 3,
                "thanh_tien_d": 30_000.0,
                "trang_thai_giao": "Hoàn thành",
            },
            {
                "don_so": "C2",
                "ngay_hd": "2025-12-01",
                "hang_hoa": "Cà phê",
                "dv": "ly",
                "s": 4,
                "sl_dv_co_ban": 4,
                "thanh_tien_d": 120_000.0,
                "trang_thai_giao": "Hoàn thành",
            },
        ]
    )
    cafe_catalog = pd.DataFrame(
        [
            {"ten_hang_hoa": "Trà sữa", "nhom_hang": "trà sữa", "dvt": "ly"},
            {"ten_hang_hoa": "Trân châu", "nhom_hang": "topping", "dvt": "phần"},
            {"ten_hang_hoa": "Cà phê", "nhom_hang": "cà phê", "dvt": "ly"},
        ]
    )
    ctx_cafe = _make_ctx(
        "synth_s2_nheiu",
        {
            "ban_hang_tong_hop": cafe,
            "ban_hang_tong_hop_ban_hang_hang_hoa": cafe_catalog,
        },
    )
    try:
        ans_ly = resolve_query(
            "30 ngay gan nhat quan ban duoc tat ca bao nheiu ly?",
            ctx_cafe,
            as_of_date="2025-12-27",
        )
        assert ans_ly[:2] == ("number", 7)
    finally:
        ctx_cafe.duckdb_conn.close()


def test_slice3_phone_store_ghi_tat_and_bad_status_and_1_26_bestseller():
    from zaic_2026_datamate.intent_parser import resolve_query

    gt = pd.DataFrame(
        [
            {"ghi_tat": "CL 9D", "ten_hang": "Kính cường lực 9D", "loai": "phụ kiện"},
            {"ghi_tat": "Ốp IP", "ten_hang": "Ốp silicon iPhone", "loai": "phụ kiện"},
            {"ghi_tat": "IP14 cũ", "ten_hang": "iPhone 14 128GB Cũ", "loai": "máy cũ"},
            {"ghi_tat": "IP16 mới", "ten_hang": "iPhone 16 128GB", "loai": "máy mới"},
        ]
    )
    sbh = pd.DataFrame(
        [
            # Abbreviated + full + noisy case/space forms (total Kính 9D valid = 4, Ốp valid = 3)
            {
                "ngay": "2026-01-05",
                "stt": 1,
                "khach": "A",
                "hang": "CL 9D",
                "sl": 2,
                "gia": 100_000,
                "thanh_tien": 200_000,
                "thu_may_cu": None,
                "khach_tra": 200_000,
                "ghi_chu": None,
            },
            {
                "ngay": "2026-01-06",
                "stt": 2,
                "khach": "B",
                "hang": "Kính cường lực 9D",
                "sl": 2,
                "gia": 100_000,
                "thanh_tien": 200_000,
                "thu_may_cu": None,
                "khach_tra": 200_000,
                "ghi_chu": None,
            },
            {
                "ngay": "2026-01-07",
                "stt": 3,
                "khach": "C",
                "hang": "Ốp IP",
                "sl": 3,
                "gia": 50_000,
                "thanh_tien": 150_000,
                "thu_may_cu": None,
                "khach_tra": 150_000,
                "ghi_chu": None,
            },
            # Returned order with mixed rows: one row full name + one row ghi_chu return -> whole order 4 excluded
            {
                "ngay": "2026-01-08",
                "stt": 4,
                "khach": "D",
                "hang": "iPhone 14 128GB Cũ",
                "sl": 1,
                "gia": 8_000_000,
                "thanh_tien": 8_000_000,
                "thu_may_cu": 2_000_000,
                "khach_tra": 6_000_000,
                "ghi_chu": "khách mang trả",
            },
            {
                "ngay": "2026-01-08",
                "stt": 4,
                "khach": "D",
                "hang": "CL 9D",
                "sl": 5,
                "gia": 100_000,
                "thanh_tien": 500_000,
                "thu_may_cu": None,
                "khach_tra": None,
                "ghi_chu": None,
            },
            # Valid full-name used phone + noisy case new phone
            {
                "ngay": "2026-01-10",
                "stt": 5,
                "khach": "E",
                "hang": "iPhone 14 128GB Cũ",
                "sl": 2,
                "gia": 8_000_000,
                "thanh_tien": 16_000_000,
                "thu_may_cu": 1_500_000,
                "khach_tra": 14_500_000,
                "ghi_chu": None,
            },
            {
                "ngay": "2026-01-12",
                "stt": 6,
                "khach": "F",
                "hang": "IPHONE 16 128GB",
                "sl": 1,
                "gia": 20_000_000,
                "thanh_tien": 20_000_000,
                "thu_may_cu": None,
                "khach_tra": 20_000_000,
                "ghi_chu": None,
            },
        ]
    )
    ctx = _make_ctx("synth_s3_phone", {"so_ban_hang": sbh, "so_ban_hang_ghi_tat": gt})
    try:
        # 1/26 sp must not trigger m_count=26 -> returns merged full canonical name 'Kính cường lực 9D' (4 > 3)
        ans_top = resolve_query(
            "Tháng 1/26 sp nào bán chạy nhất?", ctx, as_of_date="2026-02-01"
        )
        assert ans_top[:2] == ("string", "Kính cường lực 9D")

        # Phone analytics must include full names + noisy case AND exclude stt=4 return
        ans_old = resolve_query(
            "Máy cũ 1/2026 bán được mấy chiếc?", ctx, as_of_date="2026-02-01"
        )
        assert ans_old[:2] == ("number", 2)

        ans_all_phones = resolve_query(
            "1-2026 tiệm bán được mấy chiếc điện thoại, không tính phụ kiện?",
            ctx,
            as_of_date="2026-02-01",
        )
        assert ans_all_phones[:2] == ("number", 3)

        ans_trade = resolve_query(
            "Tổng tiền thu máy cũ của khách 1-2026 là bao nhiêu?",
            ctx,
            as_of_date="2026-02-01",
        )
        assert ans_trade[:2] == ("number", 1_500_000)

        ans_paid = resolve_query(
            "1/2026 khách phải trả tổng cộng bao nhiêu?", ctx, as_of_date="2026-02-01"
        )
        assert ans_paid[:2] == (
            "number",
            200_000 + 200_000 + 150_000 + 14_500_000 + 20_000_000,
        )
    finally:
        ctx.duckdb_conn.close()


def test_slice3_orders_above_threshold_excludes_bad_ghi_chu_and_adjustment_rows():
    from zaic_2026_datamate.intent_parser import resolve_query

    ban_hang = pd.DataFrame(
        [
            # Order H1: 800k valid merchandise - 100k Voucher + 30k Phí ship (pre-adj = 800k >= 750k!)
            {
                "hd": "H1",
                "ngay": "2025-05-02",
                "ten_hang": "Sữa A",
                "sl": 2,
                "dg": 400_000,
                "tt": 800_000,
                "ghi_chu": None,
            },
            {
                "hd": "H1",
                "ngay": "2025-05-02",
                "ten_hang": "Voucher",
                "sl": 1,
                "dg": -100_000,
                "tt": -100_000,
                "ghi_chu": None,
            },
            {
                "hd": "H1",
                "ngay": "2025-05-02",
                "ten_hang": "Phí ship",
                "sl": 1,
                "dg": 30_000,
                "tt": 30_000,
                "ghi_chu": None,
            },
            # Order H2: 740k merchandise + 30k Phí ship -> raw sum 770k >= 750k, clean 740k < 750k -> excluded
            {
                "hd": "H2",
                "ngay": "2025-05-03",
                "ten_hang": "Tã B",
                "sl": 1,
                "dg": 740_000,
                "tt": 740_000,
                "ghi_chu": None,
            },
            {
                "hd": "H2",
                "ngay": "2025-05-03",
                "ten_hang": "Phí ship",
                "sl": 1,
                "dg": 30_000,
                "tt": 30_000,
                "ghi_chu": None,
            },
            # Order H3: 900k merchandise but status Huỷ -> excluded
            {
                "hd": "H3",
                "ngay": "2025-05-04",
                "ten_hang": "Sữa A",
                "sl": 2,
                "dg": 450_000,
                "tt": 900_000,
                "ghi_chu": "Huỷ",
            },
            # Order H4: 800k merchandise + Dùng 100 điểm (-50k) -> clean 800k >= 750k!
            {
                "hd": "H4",
                "ngay": "2025-05-05",
                "ten_hang": "Sữa A",
                "sl": 2,
                "dg": 400_000,
                "tt": 800_000,
                "ghi_chu": None,
            },
            {
                "hd": "H4",
                "ngay": "2025-05-05",
                "ten_hang": "Dùng 100 điểm",
                "sl": 1,
                "dg": -50_000,
                "tt": -50_000,
                "ghi_chu": None,
            },
        ]
    )
    ctx = _make_ctx("synth_s3_thresh", {"ban_hang": ban_hang})
    try:
        ans = resolve_query(
            "Tháng 5/2025 có mấy đơn tiền hàng từ 750.000đ trở lên?",
            ctx,
            as_of_date="2025-06-01",
        )
        assert ans[:2] == ("number", 2)
    finally:
        ctx.duckdb_conn.close()


def test_slice4_readme_unit_conversion_and_example_guard():
    from zaic_2026_datamate.intent_parser import resolve_query
    from zaic_2026_datamate.semantic_contract import parse_readme_unit_rules

    readme = (
        "# Dữ liệu\n"
        '- **Quy đổi**: quy đổi đơn vị lớn ra ĐVT, vd "1 thùng = 4 bịch"; trống nếu chỉ bán theo ĐVT\n'
        "- **Hàng theo bó**: Ống PVC Bình Minh D32 cây 4m, Ống PVC Bình Minh D50 cây 4m bán theo bó, "
        "1 bó = 10 cây; khách lấy nguyên bó thì sổ ghi số bó.\n"
    )
    rules = parse_readme_unit_rules(readme)
    assert ("ong pvc binh minh d32 cay 4m", "bo", 10.0, "cay") in rules
    assert ("ong pvc binh minh d50 cay 4m", "bo", 10.0, "cay") in rules
    assert all(r[1] != "thung" for r in rules)

    sbh = pd.DataFrame(
        [
            {
                "ma_don": "H1",
                "thoi_gian": "2026-03-02",
                "hang_ban": "Ống PVC Bình Minh D32 cây 4m",
                "dvt": "bó",
                "sl_ban": 2,
                "gia_1_dv": 450_000,
                "amount": 900_000,
                "trang_thai_xu_ly": "Hoàn thành",
            },
            {
                "ma_don": "H2",
                "thoi_gian": "2026-03-05",
                "hang_ban": "Ống PVC Bình Minh D32 cây 4m",
                "dvt": "cây",
                "sl_ban": 5,
                "gia_1_dv": 50_000,
                "amount": 250_000,
                "trang_thai_xu_ly": "đã xong",
            },
            {
                "ma_don": "H3",
                "thoi_gian": "2026-03-08",
                "hang_ban": "Ống PVC Bình Minh D32 cây 4m",
                "dvt": "bó",
                "sl_ban": 1,
                "gia_1_dv": 450_000,
                "amount": 450_000,
                "trang_thai_xu_ly": "Đã trả hàng",
            },
        ]
    )
    ctx = _make_ctx("synth_s4_pvc", {"so_ban_hang": sbh})
    ctx.readme = readme
    try:
        ans = resolve_query(
            "Tháng 3/2026 bán ra bao nhiêu cây Ống PVC Bình Minh D32 cây 4m?",
            ctx,
            as_of_date="2026-04-01",
        )
        assert ans[:2] == ("number", 25)
    finally:
        ctx.duckdb_conn.close()


def test_slice4_repair_shop_returning_customer_normalizes_plate():
    from zaic_2026_datamate.repair_shop import resolve_repair_shop_query

    so_sua_xe = pd.DataFrame(
        [
            {
                "ngay": "2025-10-02",
                "xe": "Wave RSX 51D4-252.44",
                "tho": "An",
                "dich_vu": "Thay nhớt",
                "tien": 120_000,
            },
            {
                "ngay": "2025-10-18",
                "xe": "RSX 51D4-252.44",
                "tho": "An",
                "dich_vu": "Tăng sên",
                "tien": 50_000,
            },
            {
                "ngay": "2025-10-05",
                "xe": "Vision 51T6-865.41",
                "tho": "Bình",
                "dich_vu": "Vệ sinh nồi",
                "tien": 150_000,
            },
            {
                "ngay": "2025-10-24",
                "xe": "Vision 110 51T6-865.41",
                "tho": "Bình",
                "dich_vu": "Thay bugi",
                "tien": 90_000,
            },
            {
                "ngay": "2025-10-11",
                "xe": "AirBlade 59A1-111.11",
                "tho": "An",
                "dich_vu": "Rửa xe",
                "tien": 40_000,
            },
        ]
    )
    ctx = _make_ctx("synth_s4_repair", {"so_sua_xe": so_sua_xe})
    try:
        ans = resolve_repair_shop_query(
            "Tháng 10 năm 2025 có mấy chiếc xe vô sửa từ 2 lần trở lên?",
            ctx,
            as_of_date="2025-11-01",
        )
        assert ans[:2] == ("number", 2)
    finally:
        ctx.duckdb_conn.close()


def test_slice4_abbreviated_price_change_merges_variants_and_excludes_returns():
    from zaic_2026_datamate.price_change import resolve_price_change_query

    gt = pd.DataFrame(
        [
            {
                "ghi_tat": "Cáp C-L",
                "ten_hang": "Cáp Type-C sang Lightning 1m",
                "loai": "phụ kiện",
            }
        ]
    )
    sbh = pd.DataFrame(
        [
            # Before (2025-11-17..2025-11-30): 4 valid variants + 1 full-row return + 1 multi-line order return
            {
                "ngay": "2025-11-18",
                "stt": 1,
                "hang": "Cáp C-L",
                "sl": 4,
                "gia": 150_000,
                "ghi_chu": None,
                "sheet_name": "T11",
            },
            {
                "ngay": "2025-11-20",
                "stt": 2,
                "hang": "Cap C-L",
                "sl": 3,
                "gia": 150_000,
                "ghi_chu": None,
                "sheet_name": "T11",
            },
            {
                "ngay": "2025-11-22",
                "stt": 3,
                "hang": "Cáp  C-L",
                "sl": 2,
                "gia": 150_000,
                "ghi_chu": None,
                "sheet_name": "T11",
            },
            {
                "ngay": "2025-11-25",
                "stt": 4,
                "hang": "Cáp Type-C sang Lightning 1m",
                "sl": 1,
                "gia": 150_000,
                "ghi_chu": None,
                "sheet_name": "T11",
            },
            {
                "ngay": "2025-11-26",
                "stt": 5,
                "hang": "Cap C-L",
                "sl": 5,
                "gia": 150_000,
                "ghi_chu": "trả lại hàng",
                "sheet_name": "T11",
            },
            {
                "ngay": "2025-11-27",
                "stt": 6,
                "hang": "Cáp Type-C sang Lightning 1m",
                "sl": 2,
                "gia": 150_000,
                "ghi_chu": None,
                "sheet_name": "T11",
            },
            {
                "ngay": "2025-11-27",
                "stt": 6,
                "hang": "Ốp lưng",
                "sl": 1,
                "gia": 50_000,
                "ghi_chu": "khách mang trả",
                "sheet_name": "T11",
            },
            # After (2025-12-01..2025-12-14): 3 + 2 + 2 = 7 valid, plus 4 returned
            {
                "ngay": "2025-12-02",
                "stt": 1,
                "hang": "Cap C-L",
                "sl": 3,
                "gia": 157_500,
                "ghi_chu": None,
                "sheet_name": "T12",
            },
            {
                "ngay": "2025-12-05",
                "stt": 2,
                "hang": "Cáp Type-C sang Lightning 1m",
                "sl": 2,
                "gia": 157_500,
                "ghi_chu": None,
                "sheet_name": "T12",
            },
            {
                "ngay": "2025-12-08",
                "stt": 3,
                "hang": "Cáp  C-L",
                "sl": 2,
                "gia": 157_500,
                "ghi_chu": None,
                "sheet_name": "T12",
            },
            {
                "ngay": "2025-12-10",
                "stt": 4,
                "hang": "Cáp C-L",
                "sl": 4,
                "gia": 157_500,
                "ghi_chu": "trả lại hàng",
                "sheet_name": "T12",
            },
        ]
    )
    ctx = _make_ctx("synth_s4_pc", {"so_ban_hang": sbh, "so_ban_hang_ghi_tat": gt})
    try:
        ans = resolve_price_change_query(
            "Ngày 01/12/2025 Cáp Type-C sang Lightning 1m tăng giá từ 150.000đ lên 157.500đ. "
            "Số cái bán được trong 14 ngày kể từ ngày đó so với 14 ngày trước đó thay đổi bao nhiêu %?",
            ctx,
            as_of_date="2026-01-01",
        )
        assert ans[:2] == ("percent", -30.0)
    finally:
        ctx.duckdb_conn.close()


def test_slice5a_clean_vietnamese_currency_nghin_ngan_trieu():
    from zaic_2026_datamate.cleaner import clean_vietnamese_currency

    assert clean_vietnamese_currency("304 nghìn") == 304_000.0
    assert clean_vietnamese_currency("853 nghin") == 853_000.0
    assert clean_vietnamese_currency("45 ngàn") == 45_000.0
    assert clean_vietnamese_currency("1,5 triệu") == 1_500_000.0
    assert clean_vietnamese_currency("2.25 trieu") == 2_250_000.0
    assert clean_vietnamese_currency("khách trả") is None


def test_slice5a_debt_combined_ban_hang_tong_hop_delta_repay_and_cod_filter():
    from zaic_2026_datamate.debt_analytics import extract_month_ym, resolve_debt_query

    # Verify generalized extract_month_ym formats + Rulebook §2 yearless months
    assert extract_month_ym("le son trung t7/2026 no them", "2026-08-21") == "2026-07"
    assert extract_month_ym("thang 7/25 khach tra no", "2026-08-21") == "2025-07"
    assert extract_month_ym("tong tien 12-2025", "2026-08-21") == "2025-12"
    assert extract_month_ym("den ngay 06/07/2025 no", "2026-08-21") is None
    assert extract_month_ym("trog thang 6 lai gop", "2026-08-21") == "2026-06"
    assert extract_month_ym("trong thang 10 doanh thu", "2026-08-21") == "2025-10"

    bhth = pd.DataFrame(
        [
            {
                "don_so": "ORD001",
                "ngay_hd": "2025-09-05",
                "ma_khach": "KH01",
                "khach_hang_ten": "Hà Duy Trung",
                "hinh_thuc_thanh_toan": "Ghi nợ",
                "trang_thai_giao": "Xong",
                "tong_thanh_toan": 500_000,
                "cong_no": 300_000,
            },
            {
                "don_so": "ORD001",
                "ngay_hd": "2025-09-05",
                "ma_khach": "KH01",
                "khach_hang_ten": "Hà Duy Trung",
                "hinh_thuc_thanh_toan": "Ghi nợ",
                "trang_thai_giao": "Xong",
                "tong_thanh_toan": 500_000,
                "cong_no": 300_000,
            },
            # COD order must NEVER enter debt or aging even if cong_no > 0
            {
                "don_so": "ORD002",
                "ngay_hd": "2025-09-01",
                "ma_khach": "KH02",
                "khach_hang_ten": "Phạm Cod",
                "hinh_thuc_thanh_toan": "Thu hộ (COD)",
                "trang_thai_giao": "Đang vận chuyển",
                "tong_thanh_toan": 900_000,
                "cong_no": 900_000,
            },
            # Cancelled credit order excluded
            {
                "don_so": "ORD003",
                "ngay_hd": "2025-09-02",
                "ma_khach": "KH01",
                "khach_hang_ten": "Hà Duy Trung",
                "hinh_thuc_thanh_toan": "Ghi nợ",
                "trang_thai_giao": "Đã hủy đơn",
                "tong_thanh_toan": 400_000,
                "cong_no": 0,
            },
        ]
    )
    pt = pd.DataFrame(
        [
            # Leading space in ma_don must match cleanly
            {"ma_don": " ORD001", "ngay_tt": "2025-09-12", "tien_thu": 200_000},
        ]
    )
    kh = pd.DataFrame(
        [
            {"ma_kh_mua": "KH01", "khach_hang_mua": "Hà Duy Trung - 0938 711 545"},
            {"ma_kh_mua": "KH02", "khach_hang_mua": "Phạm Cod"},
        ]
    )
    ctx = _make_ctx(
        "synth_s5_bhth",
        {
            "ban_hang_tong_hop": bhth,
            "ban_hang_phieu_thu": pt,
            "ban_hang_tong_hop_ban_hang_khach_hang": kh,
        },
    )
    try:
        delta = resolve_debt_query(
            "9/2025 khach Ha Duy Trung no them bao nhieu tien, tra bot duoc bao nhieu?",
            ctx,
            as_of_date="2025-10-01",
        )
        assert delta[:2] == ("list", [500_000, 200_000])

        repay = resolve_debt_query(
            "Tháng 9 năm 2025 tiệm đòi được bnhiu tiền nợ từ khách mua chịu?",
            ctx,
            as_of_date="2025-10-01",
        )
        assert repay[:2] == ("number", 200_000)

        cnt = resolve_debt_query(
            "Tính đến hết hôm nay, có bao nhiêu khách còn nợ cửa hàng?",
            ctx,
            as_of_date="2025-10-01",
        )
        assert cnt[:2] == ("number", 1)
    finally:
        ctx.duckdb_conn.close()


def test_slice5a_debt_date_ma_the_and_telex_topk():
    from zaic_2026_datamate.debt_analytics import resolve_debt_query

    dh = pd.DataFrame(
        [
            {
                "so_ct": "D1",
                "date": "2026-08-20",
                "ma_the": "KH01",
                "phuong_thuc_tt": "debt",
                "xu_ly": "completed",
                "thanh_tien": 800_000,
                "con_no": 500_000,
            },
            {
                "so_ct": "D2",
                "date": "2026-09-01",
                "ma_the": "KH02",
                "phuong_thuc_tt": "debt",
                "xu_ly": "completed",
                "thanh_tien": 300_000,
                "con_no": 300_000,
            },
        ]
    )
    tt = pd.DataFrame([{"ma_don": "D1", "ngay_thu": "2026-08-25", "so_tien": 300_000}])
    kh = pd.DataFrame(
        [
            {"ma_khach": "KH01", "khach_le_si": "Nguyễn Văn An"},
            {"ma_khach": "KH02", "khach_le_si": "Trần Thị Bình"},
        ]
    )
    ctx = _make_ctx(
        "synth_s5_mathe",
        {"don_hang": dh, "thanh_toan": tt, "khach_hang": kh},
    )
    try:
        top = resolve_debt_query(
            "tíh tới ngày 6/9/2026, 2 khách nào đag nợ nhìu nhứt, mỗi ngừi kòn nợ bnhiu đó nghen",
            ctx,
            as_of_date="2026-09-06",
        )
        assert top[0] == "table"
        assert top[1]["rows"] == [
            ["Nguyễn Văn An", 500_000],
            ["Trần Thị Bình", 300_000],
        ]
    finally:
        ctx.duckdb_conn.close()


def test_slice5a_debt_note_ledger_end_of_data_and_fail_closed():
    from zaic_2026_datamate.debt_analytics import resolve_debt_query

    orders = pd.DataFrame(
        [
            {
                "bill": "B1",
                "ngay_xuat": "2026-07-01",
                "ma_nguoi_mua": "KH01",
                "khach": "Lý Tâm Khánh",
                "thanh_toan_bang": "debt",
                "tt_don": "Thành công",
                "tong_tien": 725_000,
                "note": "nợ 725 nghìn",
            },
            {
                "bill": "B2",
                "ngay_xuat": "2026-07-10",
                "ma_nguoi_mua": "KH02",
                "khach": "Phùng Châu Phong - 0971 746 686",
                "thanh_toan_bang": "debt",
                "tt_don": "đã xong",
                "tong_tien": 286_000,
                "note": "đưa trước 236k, còn lại ghi sổ",
            },
            {
                "bill": "B3",
                "ngay_xuat": "2026-07-20",
                "ma_nguoi_mua": "KH03",
                "khach": "Hoàng Đức Hải 0798860862",
                "thanh_toan_bang": "debt",
                "tt_don": "đã lấy",
                "tong_tien": 611_000,
                "note": "nợ 611 nghìn, khách ck đủ rồi",
            },
            {
                "bill": "B4",
                "ngay_xuat": "2026-07-21",
                "ma_nguoi_mua": "KH04",
                "khach": "Vương Hạnh Tâm",
                "thanh_toan_bang": "debt",
                "tt_don": "đã xong",
                "tong_tien": 228_000,
                "note": "còn thiếu 99.334đ",
            },
        ]
    )
    ctx = _make_ctx("synth_s5_note", {"chi_tiet_hoa_don": orders})
    try:
        # At end-of-data date 2026-07-22 (>= max order day 2026-07-21): exact match
        tot = resolve_debt_query(
            "Tính đến hết ngày 22/07/2026, khách hàng còn nợ cửa hàng tổng cộng bao nhiêu tiền?",
            ctx,
            as_of_date="2026-07-22",
        )
        assert tot[:2] == ("number", 725_000 + 50_000 + 99_334)

        cnt = resolve_debt_query(
            "Đến hết ngày 22/07/2026, số khách còn công nợ với cửa hàng là bao nhiêu?",
            ctx,
            as_of_date="2026-07-22",
        )
        assert cnt[:2] == ("number", 3)

        ag = resolve_debt_query(
            "Đến hết ngày 22/07/2026, các khoản nợ có tuổi quá 10 ngày còn bao nhiêu tiền?",
            ctx,
            as_of_date="2026-07-22",
        )
        # B1 age=21 (>10d -> 725_000), B2 age=12 (>10d -> 50_000), B4 age=1 (<=10d)
        assert ag[:2] == ("number", 775_000)

        # Historical snapshot before max_order_day MUST abstain (Note only holds EOD status)
        hist = resolve_debt_query(
            "Tính đến hết ngày 15/07/2026, khách hàng còn nợ cửa hàng tổng cộng bao nhiêu tiền?",
            ctx,
            as_of_date="2026-07-22",
        )
        assert hist[0] == "abstain"
    finally:
        ctx.duckdb_conn.close()


def _make_synth_installment_ctx(name: str):
    so_no = pd.DataFrame(
        [
            {
                "ngay": "2026-08-10",
                "khach": "Trần Văn C",
                "sdt": "0900000003",
                "no": 500_000.0,
                "da_tra": 200_000.0,
                "ngay_tra": "2026-08-20",
            }
        ]
    )
    tra_gop = pd.DataFrame(
        [
            # Single-contract customer A: 6 months x 1,000,000, due day 30, 1st due 2026-07-30, paid 2
            {
                "stt": 1,
                "khach": "Nguyễn Văn A",
                "ngay": "2026-06-30",
                "so_ky": 6,
                "gop_thang": 1_000_000.0,
                "han_gop_ngay": 30,
                "ngay_gop": "2026-07-30",
                "da_gop": 1_000_000.0,
            },
            {
                "stt": None,
                "khach": None,
                "ngay": None,
                "so_ky": None,
                "gop_thang": None,
                "han_gop_ngay": None,
                "ngay_gop": "2026-08-30",
                "da_gop": 1_000_000.0,
            },
            # Multi-contract customer B (2 contracts), due day 15 -> 2026-08-15 & 2026-09-15
            {
                "stt": 2,
                "khach": "Lê Thị B",
                "ngay": "2026-07-15",
                "so_ky": 3,
                "gop_thang": 500_000.0,
                "han_gop_ngay": 15,
                "ngay_gop": None,
                "da_gop": None,
            },
            {
                "stt": 3,
                "khach": "Lê Thị B",
                "ngay": "2026-08-15",
                "so_ky": 2,
                "gop_thang": 400_000.0,
                "han_gop_ngay": 15,
                "ngay_gop": None,
                "da_gop": None,
            },
            # Distinct customer D whose 1st due date falls exactly on 2026-09-15
            {
                "stt": 4,
                "khach": "Phạm Thị D",
                "ngay": "2026-08-15",
                "so_ky": 2,
                "gop_thang": 300_000.0,
                "han_gop_ngay": 15,
                "ngay_gop": None,
                "da_gop": None,
            },
        ]
    )
    return _make_ctx(name, {"so_no_so_no": so_no, "so_no_tra_gop": tra_gop})


def test_slice5b_unified_debt_telex_accented_and_false_cust_token():
    from zaic_2026_datamate.mobile_installment import resolve_mobile_installment_query

    ctx = _make_synth_installment_ctx("synth_s5b_unified")
    try:
        ans = resolve_mobile_installment_query(
            "chị ơi, tíh tới hết ngày 27/9/2026 tong tien khach kòn nợ shop mik bnhiu đó ha chị",
            ctx,
            as_of_date="2026-09-30",
        )
        # so_no remaining = 300k; tra_gop remaining = (6M - 2M) + 1.5M + 0.8M + 0.6M = 6.9M -> 7.2M
        assert ans[:2] == ("number", 7_200_000)

        ag = resolve_mobile_installment_query(
            "Đến ngày 23/09/2026 tiền khách còn nợ chia theo tuổi nợ 0–7, 8–15 và trên 15 ngày, mỗi nhóm bnhiu?",
            ctx,
            as_of_date="2026-09-30",
        )
        assert ag[0] == "abstain"
    finally:
        ctx.duckdb_conn.close()


def test_slice5b_installment_c1_due_count_and_multi_contract_guard():
    from zaic_2026_datamate.mobile_installment import resolve_mobile_installment_query

    ctx = _make_synth_installment_ctx("synth_s5b_c1")
    try:
        # Customer A single contract: due 07-30, 08-30, 09-30, 10-30 -> 4 periods by 2026-10-30
        c1 = resolve_mobile_installment_query(
            "Hết hnay, khách Nguyễn Văn A góp tới hạn mấy kỳ rồi, tính luôn kỳ hạn hôm nay nha?",
            ctx,
            as_of_date="2026-10-30",
        )
        assert c1[:2] == ("number", 4)

        # Customer B has 2 contracts -> must abstain for safety
        c1_multi = resolve_mobile_installment_query(
            "Hết hnay, khách Lê Thị B góp tới hạn mấy kỳ rồi?",
            ctx,
            as_of_date="2026-10-30",
        )
        assert c1_multi[0] == "abstain"
    finally:
        ctx.duckdb_conn.close()


def test_slice5b_installment_c3_storewide_remaining_balance():
    from zaic_2026_datamate.mobile_installment import resolve_mobile_installment_query

    ctx = _make_synth_installment_ctx("synth_s5b_c3")
    try:
        q1 = resolve_mobile_installment_query(
            "Đến hết hôm nay, các hợp đồng trả góp còn lại bao nhiêu tiền chưa góp, tính cả kỳ chưa tới hạn?",
            ctx,
            as_of_date="2026-09-30",
        )
        # 4M + 1.5M + 0.8M + 0.6M = 6.9M
        assert q1[:2] == ("number", 6_900_000)

        q2 = resolve_mobile_installment_query(
            "Hết ngày hôm nay, tổng số tiền góp khách chưa trả, kể cả các kỳ chưa tới hạn, là bao nhiêu?",
            ctx,
            as_of_date="2026-09-30",
        )
        assert q2[:2] == ("number", 6_900_000)
    finally:
        ctx.duckdb_conn.close()


def test_slice5b_installment_c4_c5_boundary_agreement_guard():
    from zaic_2026_datamate.mobile_installment import resolve_mobile_installment_query

    ctx = _make_synth_installment_ctx("synth_s5b_c45")
    try:
        # On 2026-09-15, due dates land exactly on cutoff (due < vs <= diverge) -> MUST abstain
        c4_ambig = resolve_mobile_installment_query(
            "Den ngay 15/09/2026 co may khach tra gop con no ky gop qua han?",
            ctx,
            as_of_date="2026-09-30",
        )
        assert c4_ambig[0] == "abstain"

        c5_ambig = resolve_mobile_installment_query(
            "Den het ngay 15/09/2026, khach con chua dong bao nhieu tien thuoc cac ky gop da qua han?",
            ctx,
            as_of_date="2026-09-30",
        )
        assert c5_ambig[0] == "abstain"

        # On 2026-09-20, no unpaid installment has due_date == 2026-09-20 (< and <= agree!)
        # A: due 07-30(paid), 08-30(paid) -> 0 overdue
        # B c2: 1M; B c3: 400k; D c4: 300k -> 2 custs (B, D), 1.7M
        c4_ok = resolve_mobile_installment_query(
            "nè, hết ngày 20/9/2026 shop mik kó mí ngừi trả góp kòn kỳ quá hạn chưa đóng",
            ctx,
            as_of_date="2026-09-30",
        )
        assert c4_ok[:2] == ("number", 2)

        c5_ok = resolve_mobile_installment_query(
            "Tính tới ngày 20/09/2026, tiền góp quá hạn khách chưa trả là bao nhiêu?",
            ctx,
            as_of_date="2026-09-30",
        )
        assert c5_ok[:2] == ("number", 1_700_000)
    finally:
        ctx.duckdb_conn.close()


def test_slice6a_profit_combined_bhth_net_expense_and_top_sp():
    from zaic_2026_datamate.profit_analytics import resolve_profit_query

    bhth = pd.DataFrame(
        [
            # 2025-05 gross profit: 39_000*2 - 11_000*2 + 210_000*1 - 90_000*1 = 56_000 + 120_000 = 176_000
            # Order-level tien_giam=10_000 and phi_ship=15_000 MUST NOT adjust gross profit per Rulebook §1
            {
                "don_so": "ORD1",
                "ngay_hd": "2025-05-10",
                "trang_thai_giao": "Xong",
                "barcode": "SP01",
                "hang_hoa": "Trà Đào (m)",
                "dv": "ly",
                "s": "2 ly",
                "sl_dv_co_ban": 2,
                "thanh_tien_d": 78_000,
                "gia_von_luc_ban": 11_000,
                "tien_giam": 10_000,
                "phi_ship": 15_000,
            },
            {
                "don_so": "ORD1",
                "ngay_hd": "2025-05-10",
                "trang_thai_giao": "Xong",
                "barcode": "SP02",
                "hang_hoa": "Bánh croissant",
                "dv": "hộp",
                "s": 1,
                "sl_dv_co_ban": 6,
                "thanh_tien_d": 210_000,
                "gia_von_luc_ban": 90_000,
                "tien_giam": 10_000,
                "phi_ship": 15_000,
            },
            # Cancelled / returned MUST be excluded
            {
                "don_so": "ORD2",
                "ngay_hd": "2025-05-11",
                "trang_thai_giao": "Đã hủy đơn",
                "barcode": "SP02",
                "hang_hoa": "Bánh croissant",
                "dv": "hộp",
                "s": 1,
                "sl_dv_co_ban": 6,
                "thanh_tien_d": 210_000,
                "gia_von_luc_ban": 90_000,
                "tien_giam": 0,
                "phi_ship": 0,
            },
            # 2025-07: Trà đào (merged mixed-case via SP01: 40_000 + 30_000 = 70_000) beats Bánh (50_000)
            {
                "don_so": "ORD3",
                "ngay_hd": "2025-07-02",
                "trang_thai_giao": "Xong",
                "barcode": "SP01",
                "hang_hoa": "Trà đào (M)",
                "dv": "ly",
                "s": 1,
                "sl_dv_co_ban": 1,
                "thanh_tien_d": 51_000,
                "gia_von_luc_ban": 11_000,
                "tien_giam": 0,
                "phi_ship": 0,
            },
            {
                "don_so": "ORD4",
                "ngay_hd": "2025-07-05",
                "trang_thai_giao": "Xong",
                "barcode": "SP01",
                "hang_hoa": "Trà Đào (m)",
                "dv": "ly",
                "s": "1 ly",
                "sl_dv_co_ban": 1,
                "thanh_tien_d": 41_000,
                "gia_von_luc_ban": 11_000,
                "tien_giam": 0,
                "phi_ship": 0,
            },
            {
                "don_so": "ORD5",
                "ngay_hd": "2025-07-06",
                "trang_thai_giao": "Xong",
                "barcode": "SP02",
                "hang_hoa": "Bánh croissant",
                "dv": "cái",
                "s": 2,
                "sl_dv_co_ban": 2,
                "thanh_tien_d": 80_000,
                "gia_von_luc_ban": 15_000,
                "tien_giam": 0,
                "phi_ship": 0,
            },
            # 2026-01: Gross 300_000 - 100_000 = 200_000; Expense = 50_000 -> Net = 150_000
            {
                "don_so": "ORD6",
                "ngay_hd": "2026-01-12",
                "trang_thai_giao": "Xong",
                "barcode": "SP02",
                "hang_hoa": "Bánh croissant",
                "dv": "hộp",
                "s": 1,
                "sl_dv_co_ban": 6,
                "thanh_tien_d": 300_000,
                "gia_von_luc_ban": 100_000,
                "tien_giam": 0,
                "phi_ship": 0,
            },
        ]
    )
    hh = pd.DataFrame(
        [
            {"ma_h": "SP01", "ten_hang_hoa": "Trà đào (M)", "dvt": "ly"},
            {"ma_h": "SP02", "ten_hang_hoa": "Bánh croissant", "dvt": "cái"},
        ]
    )
    cp = pd.DataFrame(
        [
            {"ngay_mua": "2026-01-05", "nhom": "thuê", "tien_chi": 50_000},
            {"ngay_mua": "2025-05-05", "nhom": "điện", "tien_chi": 26_000},
        ]
    )
    ctx = _make_ctx(
        "synth_s6a_profit",
        {
            "ban_hang_tong_hop": bhth,
            "ban_hang_tong_hop_2026": bhth[
                bhth["ngay_hd"].str.startswith("2026")
            ].copy(),
            "ban_hang_tong_hop_ban_hang": bhth.copy(),
            "ban_hang_tong_hop_ban_hang_hang_hoa": hh,
            "so_tay_chi_phi": cp,
        },
    )
    try:
        r_gross = resolve_profit_query(
            "Laix gộp 5/2025 bnhiu?", ctx, as_of_date="2026-08-21"
        )
        assert r_gross[:2] == ("number", 176_000)

        r_net = resolve_profit_query(
            "1-2026 tru chi phi xong quan loi that duoc bao nhieu?",
            ctx,
            as_of_date="2026-08-21",
        )
        assert r_net[:2] == ("number", 150_000)

        r_top1 = resolve_profit_query(
            "th7/2025 sp nao lãi gộp cao nhut z ta",
            ctx,
            as_of_date="2026-08-21",
        )
        assert r_top1[:2] == ("string", "Trà đào (M)")

        r_top2 = resolve_profit_query(
            "T7/2025 mặt hàng mô cho lời nhiều nhất?",
            ctx,
            as_of_date="2026-08-21",
        )
        assert r_top2[:2] == ("string", "Trà đào (M)")

        r_top3 = resolve_profit_query(
            "1-2026 món nào đem về lãi gộp cao nhất?",
            ctx,
            as_of_date="2026-08-21",
        )
        assert r_top3[:2] == ("string", "Bánh croissant")
    finally:
        ctx.duckdb_conn.close()


def test_slice6a_in_out_ledger_wording_units_and_pure_import():
    from zaic_2026_datamate.in_out_ledger import resolve_in_minus_out_query

    # 1. Store with ma_h + don_vi_quy_doi(ma_vach, quy_cach, bang) + so_tay_nhap_hang
    hh = pd.DataFrame(
        [{"ma_h": "SP49", "ten_hang_hoa": "Bánh croissant", "dvt": "cái"}]
    )
    dvqd = pd.DataFrame([{"ma_vach": "SP49", "quy_cach": "hộp", "bang": 6}])
    nhap = pd.DataFrame(
        [
            {
                "so_phieu": "PN1",
                "ngay_nhap": "2026-03-05",
                "product": "Bánh croissant",
                "sl_nhap": 10,
                "dvt_nhap": "hộp",
            },
        ]
    )
    ban = pd.DataFrame(
        [
            {
                "don_so": "H1",
                "ngay_hd": "2026-03-08",
                "trang_thai_giao": "Xong",
                "barcode": "SP49",
                "hang_hoa": "Bánh croissant",
                "dv": "cái",
                "sl_dv_co_ban": 15,
            },
            {
                "don_so": "H2",
                "ngay_hd": "2026-03-09",
                "trang_thai_giao": "Đã hủy",
                "barcode": "SP49",
                "hang_hoa": "Bánh croissant",
                "dv": "cái",
                "sl_dv_co_ban": 5,
            },
        ]
    )
    ctx_bakery = _make_ctx(
        "synth_s6a_io_bakery",
        {
            "ban_hang_tong_hop_ban_hang_hang_hoa": hh,
            "ban_hang_tong_hop_ban_hang_don_vi_quy_doi": dvqd,
            "so_tay_nhap_hang": nhap,
            "ban_hang_tong_hop_ban_hang": ban,
        },
    )
    try:
        # Import 10*6 = 60 cai, sold = 15 cai -> diff = 45
        ans = resolve_in_minus_out_query(
            "nhờ xíu nha, trog th3/2026 shop mik banh croissant nhập zìa tru so ban ra thi dc bnhiu cai luôn đi e",
            ctx_bakery,
            as_of_date="2026-08-21",
        )
        assert ans[:2] == ("number", 45)
    finally:
        ctx_bakery.duckdb_conn.close()

    # 2. Pharma header/detail import + order + unit-before-month + pure import qty
    sp = pd.DataFrame(
        [
            {
                "ma_hang_hoa": "SP01",
                "ten_goi": "Hagifen 400mg DHG hộp 10 vỉ x 10 viên",
                "don_vi_co_so": "viên",
            },
            {
                "ma_hang_hoa": "SP02",
                "ten_goi": "Biosubtyl-II hộp 25 gói",
                "don_vi_co_so": "gói",
            },
        ]
    )
    dv = pd.DataFrame(
        [
            {"ma_hang_hoa": "SP01", "don_vi_tinh": "hộp", "he_so_quy_doi": 100},
            {"ma_hang_hoa": "SP02", "don_vi_tinh": "hộp", "he_so_quy_doi": 25},
        ]
    )
    ph_nhap = pd.DataFrame(
        [
            {
                "ma_phieu_nhap": "PN1",
                "ngay_nhap_kho": "2025-06-10",
                "tinh_trang": "received",
            },
            {
                "ma_phieu_nhap": "PN_CANCEL",
                "ngay_nhap_kho": "2025-06-15",
                "tinh_trang": "Đã hủy",
            },
            {
                "ma_phieu_nhap": "PN2",
                "ngay_nhap_kho": "2025-09-05",
                "tinh_trang": "received",
            },
            {
                "ma_phieu_nhap": "PN3",
                "ngay_nhap_kho": "2025-11-05",
                "tinh_trang": "received",
            },
        ]
    )
    ct_nhap = pd.DataFrame(
        [
            {
                "ma_phieu_nhap": "PN1",
                "ma_hang_hoa": "SP01",
                "don_vi_tinh": "hộp",
                "sl": 3,
            },
            {
                "ma_phieu_nhap": "PN_CANCEL",
                "ma_hang_hoa": "SP01",
                "don_vi_tinh": "hộp",
                "sl": 9,
            },
            {
                "ma_phieu_nhap": "PN2",
                "ma_hang_hoa": "SP01",
                "don_vi_tinh": "hộp",
                "sl": 5,
            },
            {
                "ma_phieu_nhap": "PN3",
                "ma_hang_hoa": "SP02",
                "don_vi_tinh": "hộp",
                "sl": 4,
            },
        ]
    )
    dh = pd.DataFrame(
        [
            {"ma_hoa_don": "HD1", "ngay_lap": "2025-09-10", "tinh_trang": "completed"},
            {"ma_hoa_don": "HD2", "ngay_lap": "2025-11-12", "tinh_trang": "completed"},
        ]
    )
    ct_don = pd.DataFrame(
        [
            {
                "ma_hoa_don": "HD1",
                "ma_hang_hoa": "SP01",
                "don_vi_tinh": "viên",
                "sl": 120,
                "sl_quy_doi": 120,
            },
            {
                "ma_hoa_don": "HD2",
                "ma_hang_hoa": "SP02",
                "don_vi_tinh": "gói",
                "sl": 30,
                "sl_quy_doi": 30,
            },
        ]
    )
    ctx_ph = _make_ctx(
        "synth_s6a_io_pharma",
        {
            "san_pham": sp,
            "don_vi_sp": dv,
            "phieu_nhap": ph_nhap,
            "chi_tiet_nhap": ct_nhap,
            "don_hang": dh,
            "chi_tiet_don": ct_don,
        },
    )
    try:
        r1 = resolve_in_minus_out_query(
            "Th9/2025 Hagifen 400mg DHG hộp 10 vỉ x 10 viên, số viên nhập trừ số viên bán ra chênh bnhiu?",
            ctx_ph,
            as_of_date="2026-08-08",
        )
        assert r1[:2] == ("number", 380)

        r2 = resolve_in_minus_out_query(
            "Hagifen 400mg DHG hop 10 vi x 10 vien thang 9 nam 2025: lay so vien nhap vo tru so vien ban ra thi duoc bao nhieu?",
            ctx_ph,
            as_of_date="2026-08-08",
        )
        assert r2[:2] == ("number", 380)

        r3 = resolve_in_minus_out_query(
            "Biosubtyl-II hộp 25 gói T11/2025: tổng gói nhập trừ tổng gói bán?",
            ctx_ph,
            as_of_date="2026-08-08",
        )
        assert r3[:2] == ("number", 70)

        r_imp = resolve_in_minus_out_query(
            "Thang 6 nam 2025 nhap ve bnhiu vien Hagifen 400mg DHG hop 10 vi x 10 vien?",
            ctx_ph,
            as_of_date="2026-08-08",
        )
        assert r_imp[:2] == ("number", 300)

        r_money = resolve_in_minus_out_query(
            "Thang 6 nam 2025 nhap ve het bao nhieu tien?",
            ctx_ph,
            as_of_date="2026-08-08",
        )
        assert r_money[0] == "abstain"
    finally:
        ctx_ph.duckdb_conn.close()


def test_slice6a_inventory_batch_hsd_and_cost_aliases():
    from zaic_2026_datamate.inventory_batch_analytics import (
        resolve_inventory_batch_query,
    )

    # 1. Batch HSD: "các sp còn lô tồn", "rơi zô N ngày tới", "cận date nhứt"
    sp = pd.DataFrame(
        [
            {
                "ma_hang_hoa": "SP01",
                "ten_goi": "Augmentin 625mg hộp 2 vỉ x 7 viên",
                "don_vi_co_so": "viên",
                "gia_von_hang": 10_000,
            },
            {
                "ma_hang_hoa": "SP02",
                "ten_goi": "Panadol Extra",
                "don_vi_co_so": "viên",
                "gia_von_hang": 2_000,
            },
        ]
    )
    lo = pd.DataFrame(
        [
            {
                "ma_hang_hoa": "SP01",
                "ma_lo": "L1",
                "ma_phieu_nhap": "PN1",
                "han_su_dung": "2026-09-15",
                "sl_nhap_quy_doi": 20,
                "don_gia_nhap": 9_000,
            },
            {
                "ma_hang_hoa": "SP01",
                "ma_lo": "L2",
                "ma_phieu_nhap": "PN1",
                "han_su_dung": "2026-10-05",
                "sl_nhap_quy_doi": 20,
                "don_gia_nhap": 9_000,
            },
            {
                "ma_hang_hoa": "SP02",
                "ma_lo": "L3",
                "ma_phieu_nhap": "PN1",
                "han_su_dung": "2026-11-25",
                "sl_nhap_quy_doi": 10,
                "don_gia_nhap": 1_800,
            },
        ]
    )
    pn = pd.DataFrame(
        [
            {
                "ma_phieu_nhap": "PN1",
                "ngay_nhap_kho": "2026-06-01",
                "tinh_trang": "received",
            }
        ]
    )
    ctx_b = _make_ctx(
        "synth_s6a_batch", {"san_pham": sp, "lo_hang": lo, "phieu_nhap": pn}
    )
    try:
        r_list = resolve_inventory_batch_query(
            "Kể giúp các sp còn lô tồn hết hạn trong 90 ngày tới.",
            ctx_b,
            as_of_date="2026-08-08",
        )
        assert r_list[:2] == ("list", ["Augmentin 625mg hộp 2 vỉ x 7 viên"])

        r_cnt = resolve_inventory_batch_query(
            "nè mik hỏi xíu, hiện kòn bn lô chua ban het mà hạn dùng rơi zô 120 ngay toi",
            ctx_b,
            as_of_date="2026-08-08",
        )
        assert r_cnt[:2] == ("number", 3)

        r_near = resolve_inventory_batch_query(
            "Tính tại ngày 08/08/2026, lô Augmentin 625mg hộp 2 vỉ x 7 viên cận date nhứt hết hạn ngày nào?",
            ctx_b,
            as_of_date="2026-08-08",
        )
        assert r_near[:2] == ("date", "2026-09-15")
    finally:
        ctx_b.duckdb_conn.close()

    # 2. Inventory cost snapshot aliases: ma_san_pham_ban/ma_sku, hang/hang_ban, ton_thuc/ton_cuoi, ngay_xuat
    dmh = pd.DataFrame(
        [
            {
                "ma_san_pham_ban": "SP01",
                "hang": "Rau muống xào tỏi",
                "dvt": "đĩa",
                "ton_thuc": 10,
                "gia_von": 15_000,
            },
            {
                "ma_san_pham_ban": "SP02",
                "hang": "Bia Tiger",
                "dvt": "lon",
                "ton_thuc": 5,
                "gia_von": 16_000,
            },
        ]
    )
    cthd = pd.DataFrame([{"ngay_xuat": "2026-07-22", "bill": "B1"}])
    ctx_snap = _make_ctx(
        "synth_s6a_snap", {"danh_muc_hang": dmh, "chi_tiet_hoa_don": cthd}
    )
    try:
        r_ok = resolve_inventory_batch_query(
            "Hết ngày 22/07/2026, hàng còn dư trong kho của quán tính theo giá vốn là bao nhiêu tiền?",
            ctx_snap,
            as_of_date="2026-07-22",
        )
        assert r_ok[:2] == ("number", 230_000)

        r_hist = resolve_inventory_batch_query(
            "Hết ngày 10/07/2026, hàng còn dư trong kho của quán tính theo giá vốn là bao nhiêu tiền?",
            ctx_snap,
            as_of_date="2026-07-22",
        )
        assert r_hist[0] == "abstain"
    finally:
        ctx_snap.duckdb_conn.close()


def test_slice6a_purchase_cat_hang_and_string_dtype_spend():
    from zaic_2026_datamate.intent_parser import resolve_query

    df = pd.DataFrame(
        {
            "ma_phieu_nhap": ["PN1", "PN1", "PN2", "PN3"],
            "ngay_nhap": ["2026-07-01", "2026-07-01", "2026-07-05", "2026-07-06"],
            "trang_thai_phieu": [
                "Đã nhập hàng",
                "Đã nhập hàng",
                "Đã nhập hàng",
                "Đã hủy",
            ],
            "ten_mat_hang": ["Cơm chiên", "Chân gà", "Thịt kho", "Cơm chiên"],
            "ma_vach": ["SP07", "SP37", "SP31", "SP07"],
            "sl_nhap": [10, 5, 4, 20],
            "gia_nhap": [100_000, 100_000, 150_000, 100_000],
            "thanh_tien": [1_000_000, 500_000, 600_000, 2_000_000],
            "tong_tien_phieu": pd.Series(
                ["1,5tr", "1.500.000đ", "0,6tr", "2tr"], dtype="string"
            ),
        }
    )
    ctx = _make_ctx("synth_s6a_pur", {"chi_tiet_nhap_hang": df})
    try:
        r1 = resolve_query(
            "Thang nay nhap hagn het bnhiu tien roi?",
            ctx,
            as_of_date="2026-07-22",
        )
        assert r1[:2] == ("number", 2_100_000)

        r2 = resolve_query(
            "Tháng 7/2026 quán cất hàng hết bao nhiêu tiền?",
            ctx,
            as_of_date="2026-07-22",
        )
        assert r2[:2] == ("number", 2_100_000)

        r_cat = resolve_query(
            "Tháng 7/2026 bán được bao nhiêu cát vàng?",
            ctx,
            as_of_date="2026-07-22",
        )
        assert r_cat[0] in ("abstain", "unanswerable")
    finally:
        ctx.duckdb_conn.close()


def test_slice6b_telex_and_regional_synonyms():
    from zaic_2026_datamate.cleaner import (
        canonicalize_regional_product,
        normalize_query_telex,
    )
    from zaic_2026_datamate.intent_parser import resolve_query
    from zaic_2026_datamate.price_change import _resolve_legacy_price_change_query

    # 1. Dialect / Telex words from Slice 6B
    assert "nhiều" in normalize_query_telex("nhiêuf")
    assert "được" in normalize_query_telex("ban duco")
    assert "bán" in normalize_query_telex("hom nay bna duoc")
    assert "nhất" in normalize_query_telex("gan nhât")
    assert "bình" in normalize_query_telex("trung bìh 1 don")
    assert "nhân viên" in normalize_query_telex("nhân diên nào")
    assert "tiền bán" in normalize_query_telex("tiềng báng nhiêu z")
    assert canonicalize_regional_product(
        "đậu phụ rán giòn"
    ) == canonicalize_regional_product("đậu hũ chiên giòn")
    assert canonicalize_regional_product(
        "canh chua cá tràu"
    ) == canonicalize_regional_product("canh chua cá lóc")
    assert canonicalize_regional_product(
        "hoa quả nghiền hippis organic 100g"
    ) == canonicalize_regional_product("trái cây nghiền hippis organic 100g")
    assert canonicalize_regional_product(
        "kệ điện thoại để bàn"
    ) == canonicalize_regional_product("giá đỡ điện thoại để bàn")

    # 2. Regional product matching in semantic_contract + Telex revenue
    sales_df = pd.DataFrame(
        [
            {
                "ma_don": "D1",
                "ngay": "2025-10-05",
                "trang_thai": "Hoàn thành",
                "ten_hang": "Đậu hũ chiên giòn",
                "dvt": "đĩa",
                "so_luong": 2,
                "thanh_tien": 70_000,
            },
            {
                "ma_don": "D2",
                "ngay": "2025-10-06",
                "trang_thai": "Hoàn thành",
                "ten_hang": "Canh chua cá lóc",
                "dvt": "tô",
                "so_luong": 1,
                "thanh_tien": 90_000,
            },
            {
                "ma_don": "D3",
                "ngay": "2026-01-10",
                "trang_thai": "Hoàn thành",
                "ten_hang": "Trái cây nghiền HiPPiS Organic 100g",
                "dvt": "túi",
                "so_luong": 3,
                "thanh_tien": 150_000,
            },
            {
                "ma_don": "D4",
                "ngay": "2026-01-11",
                "trang_thai": "Hoàn thành",
                "ten_hang": "Giá đỡ điện thoại để bàn",
                "dvt": "cái",
                "so_luong": 2,
                "thanh_tien": 120_000,
            },
            {
                "ma_don": "D5",
                "ngay": "2025-09-12",
                "trang_thai": "Hoàn thành",
                "ten_hang": "Nước giặt xả D-nee 3000ml",
                "dvt": "can",
                "so_luong": 4,
                "thanh_tien": 520_000,
            },
        ]
    )
    ctx = _make_ctx("synth_s6b_syn", {"so_ban_hang": sales_df})
    try:
        assert resolve_query(
            "Tiền bán Đậu phụ rán giòn tháng 10/2025 là bao nhiêu?", ctx, "2026-07-22"
        )[:2] == ("number", 70_000)
        assert resolve_query(
            "Tháng 10/2025 bán Canh chua cá tràu được bao nhiêu tiền?",
            ctx,
            "2026-07-22",
        )[:2] == ("number", 90_000)
        assert resolve_query(
            "Tháng 1/2026 bán Hoa quả nghiền HiPPiS Organic 100g được bao nhiêu tiền?",
            ctx,
            "2026-09-06",
        )[:2] == ("number", 150_000)
        assert resolve_query(
            "Tháng 1/2026 tiệm bán Kệ điện thoại để bàn được bao nhiêu tiền?",
            ctx,
            "2026-07-31",
        )[:2] == ("number", 120_000)
        assert resolve_query(
            "Nước giặt xả D-nee 3000ml tháng 9/2025, tiềng báng nhiêu z?",
            ctx,
            "2026-09-06",
        )[:2] == ("number", 520_000)
    finally:
        ctx.duckdb_conn.close()

    # 3. Price change regional synonym ("Cát xây trát" -> "Cát xây tô")
    pc_rows = []
    for day_offset in range(14):
        d_pre = (pd.Timestamp("2025-06-08") + pd.Timedelta(days=day_offset)).strftime(
            "%Y-%m-%d"
        )
        d_post = (pd.Timestamp("2025-06-22") + pd.Timedelta(days=day_offset)).strftime(
            "%Y-%m-%d"
        )
        pc_rows.append(
            {
                "ma_don": f"PRE{day_offset}",
                "thoi_gian": d_pre,
                "hang_ban": "Cát xây tô",
                "dvt": "m3",
                "sl_ban": 1,
                "gia_1_dv": 300_000,
                "amount": 300_000,
                "trang_thai_xu_ly": "Hoàn thành",
            }
        )
        pc_rows.append(
            {
                "ma_don": f"POST{day_offset}",
                "thoi_gian": d_post,
                "hang_ban": "Cát xây tô",
                "dvt": "m3",
                "sl_ban": 2,
                "gia_1_dv": 315_000,
                "amount": 630_000,
                "trang_thai_xu_ly": "Hoàn thành",
            }
        )
    bang_gia = pd.DataFrame(
        [
            {"gia_cu": 300_000, "ngay_ap_dung": "2025-06-22", "gia_moi": 315_000},
        ]
    )
    danh_muc = pd.DataFrame(
        [
            {"hang": "Cát xây tô", "he_so": 4, "dv_goc": "m3", "loai_dv": "xe 4m3"},
        ]
    )
    ctx_pc = _make_ctx(
        "synth_s6b_pc",
        {
            "so_ban_hang": pd.DataFrame(pc_rows),
            "bang_gia": bang_gia,
            "danh_muc": danh_muc,
        },
    )
    try:
        res_pc = _resolve_legacy_price_change_query(
            "Cát xây trát lên 315.000đ từ ngày 22/06/2025, số m3 bán trong 14 ngày kể từ hôm đó tăng hay giảm mấy % so với 14 ngày trước đó?",
            ctx_pc,
            "2026-06-10",
        )
        assert res_pc[:2] == ("percent", 100.0)
    finally:
        ctx_pc.duckdb_conn.close()


def test_slice6b_category_top_item_aov_mtd_text_month_staff():
    from zaic_2026_datamate.intent_parser import resolve_query
    from zaic_2026_datamate.semantic_contract import (
        parse_enhanced_period,
        resolve_semantic_plan,
    )

    # 1. Text month parsing with anti-ambiguity guards
    assert (
        parse_enhanced_period(
            "Tháng ba quán có tất cả bao nhiêu đơn bị huỷ thế?", "2026-08-21"
        ).month_str
        == "2026-03"
    )
    assert (
        parse_enhanced_period(
            "Tháng ba quán có tất cả bao nhiêu đơn bị huỷ thế?", "2026-02-10"
        ).month_str
        == "2025-03"
    )
    assert (
        parse_enhanced_period("Tháng sau bán được bao nhiêu?", "2026-04-10").month_str
        is None
    )
    assert (
        parse_enhanced_period(
            "Tháng tư vấn khách hàng bán được bao nhiêu?", "2026-04-10"
        ).month_str
        is None
    )

    # 2. Store sales fixture with categories, staff, cancelled orders, AOV, MTD & 30d
    sales_df = pd.DataFrame(
        [
            # 2025-11 categories (gia vị, gạo)
            {
                "ma_don": "O1",
                "ngay": "2025-11-05",
                "trang_thai": "Hoàn thành",
                "ten_hang": "Muối I-ốt",
                "nhom_hang": "Gia vị",
                "dvt": "gói",
                "so_luong": 10,
                "thanh_tien": 50_000,
                "khach_hang": "An",
                "nhan_vien": "NV001",
            },
            {
                "ma_don": "O2",
                "ngay": "2025-11-06",
                "trang_thai": "Hoàn thành",
                "ten_hang": "Nước mắm Nam Ngư",
                "nhom_hang": "Gia vị",
                "dvt": "chai",
                "so_luong": 3,
                "thanh_tien": 120_000,
                "khach_hang": "Bình",
                "nhan_vien": "NV002",
            },
            {
                "ma_don": "O3",
                "ngay": "2025-11-07",
                "trang_thai": "Hoàn thành",
                "ten_hang": "Gạo ST25",
                "nhom_hang": "Gạo",
                "dvt": "kg",
                "so_luong": 25,
                "thanh_tien": 750_000,
                "khach_hang": "An",
                "nhan_vien": "NV001",
            },
            # 2026-01 & 2025-12 MTD test (as_of=2026-01-28)
            {
                "ma_don": "O4",
                "ngay": "2025-12-10",
                "trang_thai": "Hoàn thành",
                "ten_hang": "Gạo ST25",
                "nhom_hang": "Gạo",
                "dvt": "kg",
                "so_luong": 10,
                "thanh_tien": 200_000,
                "khach_hang": "An",
                "nhan_vien": "NV001",
            },
            {
                "ma_don": "O5",
                "ngay": "2026-01-10",
                "trang_thai": "Hoàn thành",
                "ten_hang": "Gạo ST25",
                "nhom_hang": "Gạo",
                "dvt": "kg",
                "so_luong": 15,
                "thanh_tien": 300_000,
                "khach_hang": "Cường",
                "nhan_vien": "NV001",
            },
            # 2026-01-26..2026-02-01 (week before 2026-02-03) -> 2 orders avg = 150k
            {
                "ma_don": "O6",
                "ngay": "2026-01-27",
                "trang_thai": "Hoàn thành",
                "ten_hang": "Gạo ST25",
                "nhom_hang": "Gạo",
                "dvt": "kg",
                "so_luong": 5,
                "thanh_tien": 100_000,
                "khach_hang": "Dũng",
                "nhan_vien": "NV002",
            },
            {
                "ma_don": "O7",
                "ngay": "2026-01-29",
                "trang_thai": "Hoàn thành",
                "ten_hang": "Gạo ST25",
                "nhom_hang": "Gạo",
                "dvt": "kg",
                "so_luong": 10,
                "thanh_tien": 200_000,
                "khach_hang": "Dũng",
                "nhan_vien": "NV001",
            },
            # 2026-03 cancelled + valid order
            {
                "ma_don": "O8",
                "ngay": "2026-03-04",
                "trang_thai": "Đã hủy",
                "ten_hang": "Gạo ST25",
                "nhom_hang": "Gạo",
                "dvt": "kg",
                "so_luong": 5,
                "thanh_tien": 100_000,
                "khach_hang": "An",
                "nhan_vien": "NV001",
            },
            {
                "ma_don": "O9",
                "ngay": "2026-03-05",
                "trang_thai": "Hoàn thành",
                "ten_hang": "Gạo ST25",
                "nhom_hang": "Gạo",
                "dvt": "kg",
                "so_luong": 5,
                "thanh_tien": 250_000,
                "khach_hang": "An",
                "nhan_vien": "NV001",
            },
            # 2026-07 NV001 + staff ranking + last 30 days
            {
                "ma_don": "O10",
                "ngay": "2026-07-05",
                "trang_thai": "Hoàn thành",
                "ten_hang": "Gạo ST25",
                "nhom_hang": "Gạo",
                "dvt": "kg",
                "so_luong": 10,
                "thanh_tien": 400_000,
                "khach_hang": "An",
                "nhan_vien": "NV001",
            },
            {
                "ma_don": "O11",
                "ngay": "2026-07-10",
                "trang_thai": "Hoàn thành",
                "ten_hang": "Gạo ST25",
                "nhom_hang": "Gạo",
                "dvt": "kg",
                "so_luong": 5,
                "thanh_tien": 100_000,
                "khach_hang": "Bình",
                "nhan_vien": "NV002",
            },
        ]
    )
    nv_df = pd.DataFrame(
        [
            {"ma_nhan_vien": "NV001", "ten_nhan_vien": "Nguyễn Văn Một"},
            {"ma_nhan_vien": "NV002", "ten_nhan_vien": "Trần Thị Hai"},
        ]
    )
    ctx = _make_ctx("synth_s6b_core", {"so_ban_hang": sales_df, "nhan_vien": nv_df})
    try:
        assert resolve_semantic_plan(
            "Trong nhóm gia vị, mặt hàng nào có doanh thu cao nhất tháng 11/2025?",
            ctx,
            "2026-07-08",
        )[:2] == ("string", "Nước mắm Nam Ngư")
        assert resolve_semantic_plan(
            "Tháng 11 năm 2025, nhóm gạo có mặt hàng nào bán được nhiều kg nhất?",
            ctx,
            "2026-07-08",
        )[:2] == ("string", "Gạo ST25")
        assert resolve_query(
            "Tuần trước khách mua trung bình mội đơn hết bao nhiêu tiền?",
            ctx,
            "2026-02-03",
        )[:2] == ("number", 150_000)
        assert resolve_query(
            "Tuan truoc dthu binh quan tren 1 dh la bnhiu z?", ctx, "2026-02-03"
        )[:2] == ("number", 150_000)
        assert resolve_query(
            "Binh quan don hang tuan truoc gia tri bnhiu z?", ctx, "2026-02-03"
        )[:2] == ("number", 150_000)
        assert resolve_query(
            "Th3 trung bình mỗi đơn được bao nhiêu doanh thu?", ctx, "2026-08-21"
        )[:2] == ("number", 250_000)
        assert resolve_query(
            "1-2026 trung binh 1 don dem ve bao nhieu doanh thu?", ctx, "2026-05-24"
        )[:2] == ("number", 200_000)
        assert resolve_query(
            "Tháng ba quán có tất cả bao nhiêu đơn bị huỷ thế?", ctx, "2026-08-21"
        )[:2] == ("number", 1)
        assert resolve_query(
            "Dthu th này so với cùng số ngày đầu th trước hơn hay kém mấy % z?",
            ctx,
            "2026-01-28",
        )[:2] == ("percent", 100.0)
        assert resolve_query(
            "xem jup mik, dthu shop mk tu mung 1 toi het hom nay so vs cung ky thang trc cao hay thap hon bnhiu %",
            ctx,
            "2026-01-28",
        )[:2] == ("percent", 100.0)
        assert resolve_query(
            "Thangs nay NV001 ban duoc bao nhieeu tien roi?", ctx, "2026-07-22"
        )[:2] == ("number", 400_000)
        assert resolve_query(
            "Th7/2026 nhân diên nào bán giỏi nhất?", ctx, "2026-09-19"
        )[:2] == ("string", "NV001")
        assert resolve_query(
            "trog th7/2026 nv nào bán đc dthu cao nhứt nhỉ", ctx, "2026-09-19"
        )[:2] == ("string", "NV001")
        assert resolve_query(
            "Dthu 30 ngày gần nhât được nhiêu vậy?", ctx, "2026-07-22"
        )[:2] == ("number", 500_000)
        assert resolve_query(
            "Tu dau nam den hom nay quan ban duco bao nhieu tien roi z?",
            ctx,
            "2026-02-18",
        )[:2] == ("number", 600_000)
        assert resolve_query(
            "Tu dau nam den hom nay bna duoc bnhiu tien roi?", ctx, "2026-02-18"
        )[:2] == ("number", 600_000)
    finally:
        ctx.duckdb_conn.close()


def test_slice6b_mother_baby_han_no_and_multi_delivery():
    from zaic_2026_datamate.domain_ext import resolve_domain_extensions
    from zaic_2026_datamate.mother_baby import resolve_mother_baby_query

    # 1. mother_baby Telex + colloquial phrasing
    mb_bh = pd.DataFrame(
        [
            {
                "hd": "M1",
                "ngay": "2026-03-05",
                "ghi_chu": "",
                "ten_hang": "Sữa bột Meiji số 2 800g",
                "dvt": "lon",
                "sl": 5,
                "dg": 500_000,
                "tt": 2_500_000,
                "khach": "Lẻ",
                "kenh": "Tại quầy",
            },
            {
                "hd": "M2",
                "ngay": "2026-03-08",
                "ghi_chu": "",
                "ten_hang": "Sữa bột Friso Gold số 2 800g",
                "dvt": "lon",
                "sl": 2,
                "dg": 500_000,
                "tt": 1_000_000,
                "khach": "Lẻ",
                "kenh": "Tại quầy",
            },
        ]
    )
    mb_dm = pd.DataFrame(
        [
            {
                "ten_hang": "Sữa bột Meiji số 2 800g",
                "ten_goi_khac": "",
                "nhom": "Sữa bột",
                "hang": "Meiji",
                "so_size": "Số 2",
                "dvt": "lon",
                "quy_doi": 1,
            },
            {
                "ten_hang": "Sữa bột Friso Gold số 2 800g",
                "ten_goi_khac": "",
                "nhom": "Sữa bột",
                "hang": "Friso",
                "so_size": "Số 2",
                "dvt": "lon",
                "quy_doi": 1,
            },
        ]
    )
    ctx_mb = _make_ctx("synth_s6b_mb", {"ban_hang": mb_bh, "quan_ly_danh_muc": mb_dm})
    try:
        assert resolve_mother_baby_query(
            "cko mik bít xíu, trog th3/2026 sữa bột số 2 kủa hãng nào bán đc nhìu lon nhứt jùm nha",
            ctx_mb,
            as_of_date="2026-06-30",
        )[:2] == ("string", "Meiji")
    finally:
        ctx_mb.duckdb_conn.close()

    # 2. so_han_no + so_giao_hang (>= 2 rounds) with causal as_of_date & status guards
    sbh = pd.DataFrame(
        [
            {
                "ma_don": "ORD1",
                "thoi_gian": "2025-11-01",
                "trang_thai_xu_ly": "Đã hoàn thành",
                "hang_ban": "Xi măng",
                "sl_ban": 10,
                "amount": 1_000_000,
            },
            {
                "ma_don": "ORD2",
                "thoi_gian": "2025-11-10",
                "trang_thai_xu_ly": "Đang vận chuyển",
                "hang_ban": "Cát",
                "sl_ban": 2,
                "amount": 600_000,
            },
            {
                "ma_don": "ORD3",
                "thoi_gian": "2025-11-15",
                "trang_thai_xu_ly": "hàng trả về",
                "hang_ban": "Gạch",
                "sl_ban": 100,
                "amount": 500_000,
            },
            {
                "ma_don": "ORD4",
                "thoi_gian": "2025-12-20",
                "trang_thai_xu_ly": "Hoàn thành",
                "hang_ban": "Thép",
                "sl_ban": 5,
                "amount": 800_000,
            },
        ]
    )
    shn = pd.DataFrame(
        [
            {"ma_don_hang": "ORD1", "ky_han": 30, "ngay_phai_tra": "2025-12-01"},
            {"ma_don_hang": "ORD2", "ky_han": 45, "ngay_phai_tra": "2025-12-25"},
            # Exact as_of_date (2025-12-10) must NOT count as > hôm nay; future order ORD4 excluded
            {"ma_don_hang": "ORD3", "ky_han": 30, "ngay_phai_tra": "2025-12-10"},
            {"ma_don_hang": "ORD4", "ky_han": 30, "ngay_phai_tra": "2026-01-20"},
        ]
    )
    sgh = pd.DataFrame(
        [
            # ORD1 delivered in 2 rounds before 2025-12-01 -> counts
            {"so_don": "ORD1", "thoi_gian_giao": "2025-11-02", "luot_giao": 1.0},
            {"so_don": "ORD1", "thoi_gian_giao": "2025-11-04", "luot_giao": 2.0},
            # ORD2 round 1 before cutoff, round 2 after 2025-12-01 -> NOT >=2 by 2025-12-01
            {"so_don": "ORD2", "thoi_gian_giao": "2025-11-11", "luot_giao": 1.0},
            {"so_don": "ORD2", "thoi_gian_giao": "2025-12-05", "luot_giao": 2.0},
            # ORD3 returned -> excluded even with 2 rounds
            {"so_don": "ORD3", "thoi_gian_giao": "2025-11-16", "luot_giao": 1.0},
            {"so_don": "ORD3", "thoi_gian_giao": "2025-11-18", "luot_giao": 2.0},
        ]
    )
    ctx_d4 = _make_ctx(
        "synth_s6b_d4", {"so_ban_hang": sbh, "so_han_no": shn, "so_giao_hang": sgh}
    )
    try:
        assert resolve_domain_extensions(
            "Tới hnay, mấy đơn bán chịu ghi ngày phải trả còn sau hôm nay vậy?",
            ctx_d4,
            "2025-12-10",
        )[:2] == ("number", 1)
        assert resolve_domain_extensions(
            "Tính đến hôm nay, trong mọi đơn bán chịu, có mấy đơn ghi ngày phải trả trong tháng 12/2025?",
            ctx_d4,
            "2025-12-10",
        )[:2] == ("number", 2)
        assert resolve_domain_extensions(
            "Trong các đơn đặt tháng 11/2025, tính tới hôm nay có bao nhiêu đơn đã được giao từ 2 đợt trở lên?",
            ctx_d4,
            "2025-12-01",
        )[:2] == ("number", 1)
        assert resolve_domain_extensions(
            "Trong các đơn đặt tháng 11/2025, tính tới hôm nay có bao nhiêu đơn đã được giao từ 2 đợt trở lên?",
            ctx_d4,
            "2025-12-10",
        )[:2] == ("number", 2)
    finally:
        ctx_d4.duckdb_conn.close()

    # 3. DD/MM/YYYY string credit due date variant (chi_tiet_cong_no_theo_han)
    cn_str = pd.DataFrame(
        [
            {
                "ma_don_hang": "ORD1",
                "so_ngay_duoc_no": 30,
                "han_thanh_toan": "05/01/2026",
            },
            {
                "ma_don_hang": "ORD2",
                "so_ngay_duoc_no": 15,
                "han_thanh_toan": "10/12/2025",
            },
        ]
    )
    ctx_cn = _make_ctx(
        "synth_s6b_cn", {"so_ban_hang": sbh, "chi_tiet_cong_no_theo_han": cn_str}
    )
    try:
        assert resolve_domain_extensions(
            "Tới hôm nay, có mấy đơn bán chịu ghi ngày phải trả trong tháng 1/2026?",
            ctx_cn,
            "2025-12-10",
        )[:2] == ("number", 1)
    finally:
        ctx_cn.duckdb_conn.close()


def test_slice7_runtime_consistency_guards(monkeypatch) -> None:
    """Slice 7: G1 (single-day SQL on multi-day period) and G3 (money SQL on unit count)."""
    from zaic_2026_datamate.hybrid_agent import HybridAgent, technical_abstain_gate

    single_day_sql = (
        "SELECT COUNT(DISTINCT don_so) FROM ban_hang_tong_hop "
        "WHERE strftime(TRY_CAST(ngay_hd AS DATE), '%Y-%m-%d') = '2025-10-09' "
        "AND (don_so IS NULL OR don_so NOT IN (SELECT don_so FROM ban_hang_tong_hop))"
    )
    # 1. G1 catches single-day equality on multi-day rolling / week / month / prior-month questions.
    assert (
        technical_abstain_gate(
            "7 ngay qua tinh ca hom nay ban duoc may don?",
            "number",
            107,
            "ok",
            "fast_path",
            sql_history=[single_day_sql],
            as_of_date="2025-10-09",
        )[0]
        == "abstain"
    )
    assert (
        technical_abstain_gate(
            "30 ngày qua kể cả hôm nay dthu đc bnhiu?",
            "number",
            9556500,
            "ok",
            "fast_path",
            sql_history=[
                "SELECT SUM(thanh_tien) FROM ban_hang WHERE TRY_CAST(ngay AS DATE) = DATE '2026-03-20'"
            ],
            as_of_date="2026-03-20",
        )[0]
        == "abstain"
    )
    # Friday 2026-05-08: "Tuần này tính tới hôm nay" is 5 days, NOT 1 day.
    assert (
        technical_abstain_gate(
            "Tuần này tính tới hôm nay doanh thu cửa hàng là bao nhiêu?",
            "number",
            500000,
            "ok",
            "fast_path",
            sql_history=[
                "SELECT SUM(tong_tien) FROM ban_hang WHERE strftime(TRY_CAST(ngay AS DATE), '%Y-%m-%d') = '2026-05-08'"
            ],
            as_of_date="2026-05-08",
        )[0]
        == "abstain"
    )
    # Day 8 of month: "Tháng này" is 8 days, NOT 1 day.
    assert (
        technical_abstain_gate(
            "Tháng này tính tới hôm nay có bao nhiêu đơn hàng?",
            "number",
            12,
            "ok",
            "fast_path",
            sql_history=[
                "SELECT COUNT(*) FROM ban_hang WHERE strftime(TRY_CAST(ngay AS DATE), '%Y-%m-%d') = '2026-05-08'"
            ],
            as_of_date="2026-05-08",
        )[0]
        == "abstain"
    )
    # Orders placed in prior month ("đặt tháng 4/2025") checked against single 2025-05-01 day.
    assert (
        technical_abstain_gate(
            "Tới hôm nay, xe đã giao tổng cộng mấy cuộn dây điện cho khách đặt tháng 4/2025?",
            "number",
            5,
            "ok",
            "fast_path",
            sql_history=[
                "SELECT ROUND(SUM(base_quantity)) FROM canonical_order_lines WHERE strftime(TRY_CAST(order_date AS DATE), '%Y-%m-%d') = '2025-05-01'"
            ],
            as_of_date="2025-05-01",
        )[0]
        == "abstain"
    )

    # 2. G1 zero false positives: Monday for "tuần này", 1st for "tháng này", genuine single-day, range SQL, multi-SQL recovery, 1 ngày qua, Python path.
    assert (
        technical_abstain_gate(
            "Tuần này tính tới hôm nay doanh thu cửa hàng là bao nhiêu?",
            "number",
            500000,
            "ok",
            "fast_path",
            sql_history=[
                "SELECT SUM(tong_tien) FROM ban_hang WHERE strftime(TRY_CAST(ngay AS DATE), '%Y-%m-%d') = '2026-05-04'"
            ],
            as_of_date="2026-05-04",  # Monday!
        )[:2]
        == ("number", 500000)
    )
    assert (
        technical_abstain_gate(
            "Tháng này tính tới hôm nay doanh thu là bao nhiêu?",
            "number",
            500000,
            "ok",
            "fast_path",
            sql_history=[
                "SELECT SUM(tong_tien) FROM ban_hang WHERE strftime(TRY_CAST(ngay AS DATE), '%Y-%m-%d') = '2026-05-01'"
            ],
            as_of_date="2026-05-01",  # 1st of month!
        )[:2]
        == ("number", 500000)
    )
    assert technical_abstain_gate(
        "Hôm nay bán được bao nhiêu đơn?",
        "number",
        10,
        "ok",
        "fast_path",
        sql_history=[single_day_sql],
        as_of_date="2025-10-09",
    )[:2] == ("number", 10)
    assert technical_abstain_gate(
        "7 ngày qua bán được mấy đơn?",
        "number",
        80,
        "ok",
        "fast_path",
        sql_history=[
            "SELECT COUNT(*) FROM ban_hang WHERE TRY_CAST(ngay AS DATE) >= DATE '2025-10-03' AND TRY_CAST(ngay AS DATE) <= DATE '2025-10-09'"
        ],
        as_of_date="2025-10-09",
    )[:2] == ("number", 80)
    # Multi-SQL history where only the final SQL succeeded with a valid range:
    assert technical_abstain_gate(
        "30 ngày qua bán được bao nhiêu đơn?",
        "number",
        320,
        "ok",
        "fast_path",
        sql_history=[
            single_day_sql,
            "SELECT COUNT(*) FROM ban_hang WHERE TRY_CAST(ngay AS DATE) >= DATE '2025-09-10' AND TRY_CAST(ngay AS DATE) <= DATE '2025-10-09'",
        ],
        as_of_date="2025-10-09",
    )[:2] == ("number", 320)
    assert technical_abstain_gate(
        "1 ngày qua bán được mấy đơn?",
        "number",
        12,
        "ok",
        "fast_path",
        sql_history=[single_day_sql],
        as_of_date="2025-10-09",
    )[:2] == ("number", 12)
    assert technical_abstain_gate(
        "7 ngay qua tinh ca hom nay ban duoc may don?",
        "number",
        107,
        "ok",
        "fast_path",
        sql_history=[],
        as_of_date="2025-10-09",
    )[:2] == ("number", 107)

    # 3. G3 catches pure unit-count questions whose SQL sums only money columns.
    assert (
        technical_abstain_gate(
            "30 ngay gan nhat quan ban duoc tat ca bao nheiu ly?",
            "number",
            248768500,
            "ok",
            "fast_path",
            sql_history=[
                "SELECT ROUND(SUM(thanh_tien_d)) FROM ban_hang_tong_hop WHERE TRY_CAST(ngay_hd AS DATE) >= DATE '2025-11-28' AND TRY_CAST(ngay_hd AS DATE) <= DATE '2025-12-27'"
            ],
            as_of_date="2025-12-27",
        )[0]
        == "abstain"
    )
    assert (
        technical_abstain_gate(
            "Tháng 3/2026 bán được bao nhiêu thùng gạch?",
            "number",
            45000000,
            "ok",
            "fast_path",
            sql_history=[
                "SELECT SUM(tong_tien) FROM ban_hang WHERE ngay LIKE '2026-03%'"
            ],
            as_of_date="2026-04-01",
        )[0]
        == "abstain"
    )

    # 4. G3 zero false positives: money keyword in question, quantity column in SQL, or order count question.
    assert technical_abstain_gate(
        "30 ngay gan nhat quan ban duoc tat ca bao nheiu ly va thu duoc bao nhieu tien?",
        "number",
        248768500,
        "ok",
        "fast_path",
        sql_history=["SELECT ROUND(SUM(thanh_tien_d)) FROM ban_hang_tong_hop"],
        as_of_date="2025-12-27",
    )[:2] == ("number", 248768500)
    assert technical_abstain_gate(
        "30 ngay gan nhat quan ban duoc tat ca bao nheiu ly?",
        "number",
        5868,
        "ok",
        "fast_path",
        sql_history=["SELECT ROUND(SUM(so_luong)) FROM ban_hang_tong_hop"],
        as_of_date="2025-12-27",
    )[:2] == ("number", 5868)
    assert technical_abstain_gate(
        "30 ngày qua bán được mấy cuộn dây?",
        "number",
        40,
        "ok",
        "fast_path",
        sql_history=["SELECT ROUND(SUM(base_quantity)) FROM canonical_order_lines"],
        as_of_date="2025-12-27",
    )[:2] == ("number", 40)
    assert technical_abstain_gate(
        "30 ngày qua bán được bao nhiêu đơn?",
        "number",
        250,
        "ok",
        "fast_path",
        sql_history=["SELECT SUM(thanh_tien_d) FROM ban_hang_tong_hop"],
        as_of_date="2025-12-27",
    )[:2] == ("number", 250)

    # 5. End-to-end HybridAgent.answer_query passes context.sql_history & as_of_date to technical_abstain_gate.
    import zaic_2026_datamate.hybrid_agent as ha_mod

    ctx = _make_ctx(
        "synth_s7",
        {
            "ban_hang": pd.DataFrame(
                [{"ngay": "2025-10-09", "thanh_tien": 100000, "so_luong": 2}]
            )
        },
    )
    try:
        agent = HybridAgent(enable_slow_path=False)

        def fake_resolve_bad(question: str, context, as_of_date: str | None = None):
            context.record_sql(single_day_sql)
            return ("number", 107, "mocked single day")

        monkeypatch.setattr(ha_mod, "resolve_query", fake_resolve_bad)
        res_bad = agent.answer_query(
            "7 ngay qua tinh ca hom nay ban duoc may don?", ctx, "2025-10-09"
        )
        assert res_bad[0] == "abstain"

        def fake_resolve_good(question: str, context, as_of_date: str | None = None):
            context.record_sql(
                "SELECT ROUND(SUM(so_luong)) FROM ban_hang WHERE TRY_CAST(ngay AS DATE) >= DATE '2025-10-03'"
            )
            return ("number", 55, "mocked valid qty")

        monkeypatch.setattr(ha_mod, "resolve_query", fake_resolve_good)
        res_good = agent.answer_query(
            "7 ngay qua ban duoc bao nhieu ly?", ctx, "2025-10-09"
        )
        assert res_good[:2] == ("number", 55)
    finally:
        ctx.duckdb_conn.close()
