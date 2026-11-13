"""TDD suite for Slice P1.1: unified debt ledgers, aging, repayments, and safety gates."""

from __future__ import annotations

import duckdb
import pandas as pd

from zaic_2026_datamate.data_loader import BusinessContext
from zaic_2026_datamate.pipeline import build_agent, production_pipeline_config


def _build_unified_ledger_ctx(include_sales: bool = False) -> BusinessContext:
    """Create a synthetic mobile store context with both so_no_so_no and so_no_tra_gop."""
    # Nguyễn Văn An owes 3,000,000 in so_no_so_no (5,000,000 - 2,000,000 as of 2025-08-10;
    # 1,000,000 receipt on 2025-08-15 is after as_of_date).
    # Trần Thị Bình is fully paid in so_no_so_no (1,000,000 - 1,000,000 = 0).
    # Lê Văn Cường owes in 2025-08-20 (future after as_of_date).
    so_no_df = pd.DataFrame(
        [
            {
                "ngay": "2025-07-10",
                "khach": "Nguyễn Văn An",
                "no": 5_000_000,
                "da_tra": None,
                "ngay_tra": None,
            },
            {
                "ngay": None,
                "khach": None,
                "no": None,
                "da_tra": 2_000_000,
                "ngay_tra": "2025-08-05",
            },
            {
                "ngay": None,
                "khach": None,
                "no": None,
                "da_tra": 1_000_000,
                "ngay_tra": "2025-08-15",
            },
            {
                "ngay": "2025-07-15",
                "khach": "Trần Thị Bình",
                "no": 1_000_000,
                "da_tra": 1_000_000,
                "ngay_tra": "2025-07-20",
            },
            {
                "ngay": "2025-08-20",
                "khach": "Lê Văn Cường",
                "no": 4_000_000,
                "da_tra": None,
                "ngay_tra": None,
            },
        ]
    )
    # Nguyễn Văn An ALSO has an installment contract: 4 * 2,000,000 = 8,000,000 total, 2,000,000 paid -> 6,000,000 left.
    # Trần Thị An has an installment contract: 3 * 1,500,000 = 4,500,000 total, 1,500,000 paid -> 3,000,000 left.
    # Phạm Quốc Dũng opened an installment contract on 2025-08-15 (after as_of_date).
    tra_gop_df = pd.DataFrame(
        [
            {
                "ngay": "2025-07-03",
                "stt": 1,
                "khach": "Nguyễn Văn An",
                "sdt": "0901",
                "may": "Phone A",
                "gia": 10_000_000.0,
                "tra_truoc": "2tr",
                "so_ky": 4,
                "gop_thang": "2tr",
                "han_gop_ngay": 3,
                "ngay_gop": "2025-08-03",
                "da_gop": "2tr",
            },
            {
                "ngay": "2025-07-12",
                "stt": 2,
                "khach": "Trần Thị An",
                "sdt": "0902",
                "may": "Phone B",
                "gia": 6_000_000.0,
                "tra_truoc": "1.5tr",
                "so_ky": 3,
                "gop_thang": "1.5tr",
                "han_gop_ngay": 12,
                "ngay_gop": "2025-08-01",
                "da_gop": "1.5tr",
            },
            {
                "ngay": "2025-08-15",
                "stt": 3,
                "khach": "Phạm Quốc Dũng",
                "sdt": "0903",
                "may": "Phone C",
                "gia": 5_000_000.0,
                "tra_truoc": "1tr",
                "so_ky": 4,
                "gop_thang": "1tr",
                "han_gop_ngay": 15,
                "ngay_gop": None,
                "da_gop": None,
            },
        ]
    )

    conn = duckdb.connect(":memory:")
    conn.register("so_no_so_no", so_no_df)
    conn.register("so_no_tra_gop", tra_gop_df)
    tables: dict[str, pd.DataFrame] = {
        "so_no_so_no": so_no_df,
        "so_no_tra_gop": tra_gop_df,
    }
    if include_sales:
        sbh_df = pd.DataFrame(
            [
                {
                    "ngay": "2025-07-03",
                    "stt": 1,
                    "sheet_name": "07.2025",
                    "hang": "Phone A",
                    "sl": 1,
                    "thanh_tien": 10_000_000.0,
                    "ghi_chu": "",
                }
            ]
        )
        conn.register("so_ban_hang", sbh_df)
        tables["so_ban_hang"] = sbh_df

    return BusinessContext(
        business_id="synthetic_mobile_ledger",
        data_dir=".",
        tables=tables,
        duckdb_conn=conn,
    )


def test_unified_so_no_and_tra_gop_total_and_active_debtor_count() -> None:
    """P1.1a: Combined ledger runtime resolves total debt, deduplicated debtor count, and customer balance."""
    ctx = _build_unified_ledger_ctx(include_sales=False)
    agent = build_agent(production_pipeline_config())
    try:
        # 2 active unique customers: Nguyễn Văn An (3M + 6M = 9M) and Trần Thị An (3M).
        t_cnt, v_cnt, _, _ = agent.answer_query(
            "Tính đến hết ngày 10/08/2025, có bao nhiêu khách đang còn nợ cửa hàng?",
            ctx,
            as_of_date="2025-08-10",
        )
        assert (t_cnt, v_cnt) == ("number", 2)

        t_tot, v_tot, _, _ = agent.answer_query(
            "Tính đến hết ngày 10/08/2025, khách còn nợ tổng cộng bao nhiêu tiền?",
            ctx,
            as_of_date="2025-08-10",
        )
        assert (t_tot, v_tot) == ("number", 12_000_000)

        # Customer balance combines both general debt ledger (3M) and installment ledger (6M).
        t_cust, v_cust, _, _ = agent.answer_query(
            "Hết ngày 10/08/2025, khách Nguyễn Văn An còn nợ bao nhiêu tiền?",
            ctx,
            as_of_date="2025-08-10",
        )
        assert (t_cust, v_cust) == ("number", 9_000_000)
    finally:
        ctx.duckdb_conn.close()


def test_unified_debt_abstains_on_ambiguous_customer_or_fragile_installment_schedule() -> (
    None
):
    """P1.1a: Ambiguous customer tokens and fragile storewide installment queries abstain safely."""
    ctx = _build_unified_ledger_ctx(include_sales=True)
    agent = build_agent(production_pipeline_config())
    try:
        # 'An' matches both 'Nguyễn Văn An' and 'Trần Thị An' -> must abstain.
        t_amb, _, _, _ = agent.answer_query(
            "Hết ngày 10/08/2025, khách An còn nợ bao nhiêu tiền?",
            ctx,
            as_of_date="2025-08-10",
        )
        assert t_amb == "abstain"

        # Existing installment-specific customer query must still return installment-only balance (6M).
        t_inst, v_inst, _, _ = agent.answer_query(
            "Hết ngày 10/08/2025, khách Nguyễn Văn An còn thiếu bao nhiêu tiền trả góp?",
            ctx,
            as_of_date="2025-08-10",
        )
        assert (t_inst, v_inst) == ("number", 6_000_000)

        # Complex unmatured/overdue storewide installment questions keep Silence-is-Free abstain.
        for q in (
            "Tính đến 10/08/2025, tổng số tiền góp kể cả chưa tới hạn là bao nhiêu?",
            "Hết ngày 10/08/2025, khách trả góp quá hạn còn thiếu bao nhiêu tiền?",
        ):
            t_frag, _, _, _ = agent.answer_query(q, ctx, as_of_date="2025-08-10")
            assert t_frag == "abstain"
    finally:
        ctx.duckdb_conn.close()


def _build_retail_credit_ledger_ctx(
    schema_variant: str = "standard",
) -> BusinessContext:
    """Create a realistic synthetic multi-table retail ledger with credit orders and payment receipts."""
    conn = duckdb.connect(":memory:")
    if schema_variant == "variant_alt_cols":
        # Variant testing alternate column names: don_hang (so_phieu, tinh_trang_don, ma_khach_mua, phai_thu),
        # thanh_toan (ma_gd=PAY*, ma_giao_dich=ORD*, thoi_diem, tien), khach_hang (ma_khach_le_si, kh with spaced phone).
        dh_df = pd.DataFrame(
            [
                {
                    "so_phieu": "ORD01",
                    "ngay_ban": "2025-07-01",
                    "tinh_trang_don": "completed",
                    "hinh_thuc_thanh_toan": "debt",
                    "ma_khach_mua": "KH01",
                    "phai_thu": 100_000,
                },
                {
                    "so_phieu": "ORD02",
                    "ngay_ban": "2025-07-12",
                    "tinh_trang_don": "completed",
                    "hinh_thuc_thanh_toan": "debt",
                    "ma_khach_mua": "KH01",
                    "phai_thu": 50_000,
                },
                {
                    "so_phieu": "ORD03",
                    "ngay_ban": "2025-07-22",
                    "tinh_trang_don": "completed",
                    "hinh_thuc_thanh_toan": "debt",
                    "ma_khach_mua": "KH02",
                    "phai_thu": 80_000,
                },
                # Cancelled credit order must be excluded
                {
                    "so_phieu": "ORD04",
                    "ngay_ban": "2025-07-22",
                    "tinh_trang_don": "cancelled",
                    "hinh_thuc_thanh_toan": "debt",
                    "ma_khach_mua": "KH02",
                    "phai_thu": 90_000,
                },
                # Cash order payments must NOT count as credit debt repayment
                {
                    "so_phieu": "ORD05",
                    "ngay_ban": "2025-07-15",
                    "tinh_trang_don": "completed",
                    "hinh_thuc_thanh_toan": "cash",
                    "ma_khach_mua": "KH01",
                    "phai_thu": 200_000,
                },
            ]
        )
        tt_df = pd.DataFrame(
            [
                # Same-day initial down-payment on ORD01 (30k -> ORD01 nợ thêm = 70k)
                {
                    "ma_gd": "PAY01",
                    "ma_giao_dich": "ORD01",
                    "thoi_diem": "2025-07-01 10:00:00",
                    "tien": 30_000,
                    "pttt": "cash",
                },
                # Subsequent July repayment on ORD01 (20k on 2025-07-10 -> ORD01 rem = 50k as of 2025-07-25)
                {
                    "ma_gd": "PAY02",
                    "ma_giao_dich": "ORD01",
                    "thoi_diem": "2025-07-10 11:00:00",
                    "tien": 20_000,
                    "pttt": "transfer",
                },
                # Cash payment on ORD05 (excluded from credit repayments)
                {
                    "ma_gd": "PAY03",
                    "ma_giao_dich": "ORD05",
                    "thoi_diem": "2025-07-15 09:00:00",
                    "tien": 200_000,
                    "pttt": "cash",
                },
                # Same-day down-payment on ORD03 (10k -> ORD03 rem = 70k, nợ thêm = 70k)
                {
                    "ma_gd": "PAY04",
                    "ma_giao_dich": "ORD03",
                    "thoi_diem": "2025-07-22 14:00:00",
                    "tien": 10_000,
                    "pttt": "cash",
                },
                # August repayment (must be excluded when as_of = 2025-07-25!)
                {
                    "ma_gd": "PAY05",
                    "ma_giao_dich": "ORD01",
                    "thoi_diem": "2025-08-02 09:00:00",
                    "tien": 50_000,
                    "pttt": "cash",
                },
            ]
        )
        kh_df = pd.DataFrame(
            [
                {"ma_khach_le_si": "KH01", "kh": "Vũ Cường Quân - 0986 827 988"},
                {"ma_khach_le_si": "KH02", "kh": "Tô Phúc Huy 0901234567"},
            ]
        )
        tables = {"don_hang": dh_df, "thanh_toan": tt_df, "khach_hang": kh_df}
    else:
        dh_df = pd.DataFrame(
            [
                {
                    "order_id": "ORD01",
                    "ngay_ban": "2025-07-01",
                    "trang_thai": "Hoàn thành",
                    "phuong_thuc_tt": "Ghi nợ",
                    "ma_kh_mua": "KH01",
                    "ten_kh": "Alias mismatch",
                    "tong_tien": 100_000,
                },
                {
                    "order_id": "ORD02",
                    "ngay_ban": "2025-07-12",
                    "trang_thai": "Đã giao",
                    "phuong_thuc_tt": "Ghi nợ",
                    "ma_kh_mua": "KH01",
                    "ten_kh": "Vũ Cường Quân",
                    "tong_tien": 50_000,
                },
                {
                    "order_id": "ORD03",
                    "ngay_ban": "2025-07-22",
                    "trang_thai": "Đã giao",
                    "phuong_thuc_tt": "Ghi nợ",
                    "ma_kh_mua": "KH02",
                    "ten_kh": "Tô Phúc Huy",
                    "tong_tien": 80_000,
                },
                {
                    "order_id": "ORD04",
                    "ngay_ban": "2025-07-20",
                    "trang_thai": "Đã giao",
                    "phuong_thuc_tt": "Tiền mặt",
                    "ma_kh_mua": "KH01",
                    "ten_kh": "Vũ Cường Quân",
                    "tong_tien": 120_000,
                },
            ]
        )
        tt_df = pd.DataFrame(
            [
                {
                    "phieu_thu": "PAY01",
                    "ma_bill": "ORD01",
                    "thoi_gian_thu": "2025-07-01 09:00:00",
                    "so_tien_thu": 30_000,
                    "pttt": "Tiền mặt",
                },
                {
                    "phieu_thu": "PAY02",
                    "ma_bill": "ORD01",
                    "thoi_gian_thu": "2025-07-10 15:00:00",
                    "so_tien_thu": 20_000,
                    "pttt": "Chuyển khoản",
                },
                {
                    "phieu_thu": "PAY03",
                    "ma_bill": "ORD03",
                    "thoi_gian_thu": "2025-07-22 10:00:00",
                    "so_tien_thu": 10_000,
                    "pttt": "Tiền mặt",
                },
                {
                    "phieu_thu": "PAY04",
                    "ma_bill": "ORD04",
                    "thoi_gian_thu": "2025-07-20 10:00:00",
                    "so_tien_thu": 120_000,
                    "pttt": "Tiền mặt",
                },
            ]
        )
        kh_df = pd.DataFrame(
            [
                {"ma_kh": "KH01", "ten_kh": "Vũ Cường Quân - 0986 827 988"},
                {"ma_kh": "KH02", "ten_kh": "Tô Phúc Huy"},
            ]
        )
        tables = {
            "ban_hang_hoa_don": dh_df,
            "ban_hang_phieu_thu": tt_df,
            "ban_hang_khach_hang": kh_df,
        }

    for tname, df in tables.items():
        conn.register(tname, df)
    return BusinessContext(
        business_id=f"synthetic_{schema_variant}",
        data_dir=".",
        tables=tables,
        duckdb_conn=conn,
    )


def test_debt_aging_repayment_customer_delta_and_alt_schema() -> None:
    """P1.1b: Verify aging [0-7, 8-15, >15], >K days, monthly repayment (incl. Telex), [nợ thêm, trả được], and alt schemas."""
    agent = build_agent(production_pipeline_config())
    for variant in ("standard", "variant_alt_cols"):
        ctx = _build_retail_credit_ledger_ctx(variant)
        try:
            # As of 2025-07-25:
            # ORD01 (2025-07-01 -> age 24 > 15): 100k - 30k - 20k = 50k
            # ORD02 (2025-07-12 -> age 13 in [8, 15]): 50k - 0 = 50k
            # ORD03 (2025-07-22 -> age 3 in [0, 7]): 80k - 10k = 70k
            assert agent.answer_query(
                "tính đến hết ngày 25/07/2025, khách còn nợ bao nhiêu ở nhóm tuổi nợ 0–7 ngày, 8–15 ngày và trên 15 ngày",
                ctx,
                as_of_date="2025-07-25",
            )[:2] == ("list", [70_000, 50_000, 50_000])

            assert agent.answer_query(
                "Tính đến hết ngày 25/07/2025, tổng số tiền khách còn nợ ở các đơn đã đặt quá 7 ngày là bao nhiêu?",
                ctx,
                as_of_date="2025-07-25",
            )[:2] == ("number", 100_000)

            assert agent.answer_query(
                "den het ngay 25/07/2025, nhung don dat cach do hon 15 ngay ma chua tra het con no tong cong bn",
                ctx,
                as_of_date="2025-07-25",
            )[:2] == ("number", 50_000)

            # Monthly credit repayments strictly after order creation day (only PAY02 = 20k in July; same-day PAY01/PAY04 excluded)
            assert agent.answer_query(
                "trong tháng này, tiền khách trar bowts nợ các đơn mua chịu được bao nhiêu",
                ctx,
                as_of_date="2025-07-25",
            )[:2] == ("number", 20_000)

            # Customer monthly delta even when order table denormalized name mismatches (`KH01` via phone-stripped khach_hang)
            # Vũ Cường Quân (KH01) in 07/2025: ORD01 (100k - 30k same day = 70k) + ORD02 (50k) = 120k nợ thêm; PAY02 = 20k trả được.
            assert agent.answer_query(
                "Tháng 7/2025 khách Vũ Cường Quân ghi nợ thêm bao nhiêu và trả nợ đc bao nhiêu?",
                ctx,
                as_of_date="2025-07-25",
            )[:2] == ("list", [120_000, 20_000])

            # Snapshot with 'còn thiếu' & top-k table & count
            assert agent.answer_query(
                "Đến hết ngày 25/07/2025, số tiền khách còn thiếu là bao nhiêu?",
                ctx,
                as_of_date="2025-07-25",
            )[:2] == ("number", 170_000)
            assert agent.answer_query(
                "có bao nhiêu khách đang còn thiếu tiền cửa hàng tính tới hết ngày 25/07/2025",
                ctx,
                as_of_date="2025-07-25",
            )[:2] == ("number", 2)
            assert agent.answer_query(
                "xem giúp top 2 khách có công nợ lớn nhất đến hết ngày 25/07/2025 và số nợ của từng người",
                ctx,
                as_of_date="2025-07-25",
            )[:2] == (
                "table",
                {
                    "columns": ["khach_hang", "so_tien_no"],
                    "rows": [["Vũ Cường Quân", 100_000], ["Tô Phúc Huy", 70_000]],
                },
            )
        finally:
            ctx.duckdb_conn.close()


def test_debt_unanswerable_when_no_credit_tracking_and_abstain_guards() -> None:
    """P1.1b: Unanswerable on due-date-only/sales-only schemas; abstain on F&B combined POS or 'knorr' false trigger."""
    agent = build_agent(production_pipeline_config())

    # 1. Business with so_han_no (due dates) + so_ban_hang (no pay_method/receipts/debt columns) -> unanswerable
    conn1 = duckdb.connect(":memory:")
    sbh = pd.DataFrame(
        [
            {
                "ngay_lap": "2025-05-01",
                "tenkh": "An",
                "tien": 100_000,
                "ghi_chu_tt": "Thành công",
                "ma_phieu": "ORD1",
            }
        ]
    )
    han = pd.DataFrame(
        [{"so_hoa_don": "ORD1", "han_no_ngay": None, "han_tra": "31/05/2025"}]
    )
    conn1.register("so_ban_hang", sbh)
    conn1.register("so_han_no", han)
    ctx1 = BusinessContext(
        business_id="synthetic_no_debt_ledger",
        data_dir=".",
        tables={"so_ban_hang": sbh, "so_han_no": han},
        duckdb_conn=conn1,
    )
    try:
        assert (
            agent.answer_query(
                "cho bảng 3 khách còn nợ nhìu nhất tính đến hết ngày 20/05/2026, kèm số tiền còn nợ",
                ctx1,
                as_of_date="2026-05-20",
            )[0]
            == "unanswerable"
        )
        assert (
            agent.answer_query(
                "Tháng này thu hồi được bao nhiêu tiền công nợ từ khách mua chịu?",
                ctx1,
                as_of_date="2025-05-20",
            )[0]
            == "unanswerable"
        )
    finally:
        conn1.close()

    # 2. Combined F&B POS table `ban_hang_tong_hop` must remain abstain (Silence is Free)
    conn2 = duckdb.connect(":memory:")
    fnb = pd.DataFrame(
        [
            {
                "ngay": "2025-07-01",
                "phuong_thuc_thanh_toan": "Ghi nợ",
                "tong_tien": 50_000,
                "trang_thai": "Hoàn thành",
                "khach": "An",
            }
        ]
    )
    conn2.register("ban_hang_tong_hop", fnb)
    ctx2 = BusinessContext(
        business_id="synthetic_fnb",
        data_dir=".",
        tables={"ban_hang_tong_hop": fnb},
        duckdb_conn=conn2,
    )
    try:
        assert (
            agent.answer_query(
                "Tính đến hết ngày 25/07/2025, khách hàng còn nợ cửa hàng tổng cộng bao nhiêu tiền?",
                ctx2,
                as_of_date="2025-07-25",
            )[0]
            == "abstain"
        )
    finally:
        conn2.close()

    # 3. Non-debt question containing substring 'no' ('Knorr') must NOT misfire into debt analytics
    ctx3 = _build_retail_credit_ledger_ctx("standard")
    try:
        t_knorr, _, _, _ = agent.answer_query(
            "tháng 7/2025 cửa hàng nhập Hạt nêm Knorr 400g bình quân bn tiền một thùng",
            ctx3,
            as_of_date="2025-07-25",
        )
        assert t_knorr in ("abstain", "unanswerable")

        # 4. Counting phrasing 'Đếm ... khách ... bao nhiêu người' must return headcount, not sum
        assert agent.answer_query(
            "Đếm dúp các khách còn thiếu tiền đến hết 25/07/2025 được bao nhiêu người nhé?",
            ctx3,
            as_of_date="2025-07-25",
        )[:2] == ("number", 2)
    finally:
        ctx3.duckdb_conn.close()

    # 5. 'nội thất' ('noi that') in a non-debt shop price query must NOT misfire into debt 'no'
    conn4 = duckdb.connect(":memory:")
    orders_price = pd.DataFrame(
        [
            {
                "ma_don": "HD01",
                "ngay_ban": "2026-05-01",
                "tong_tien": 300_000,
                "ten_hang": "Bột trét Jotun nội thất 40kg",
                "so_luong": 1,
                "don_gia": 300_000,
            },
            {
                "ma_don": "HD02",
                "ngay_ban": "2026-05-05",
                "tong_tien": 300_000,
                "ten_hang": "Bột trét Jotun nội thất 40kg",
                "so_luong": 1,
                "don_gia": 300_000,
            },
        ]
    )
    san_pham_price = pd.DataFrame(
        [
            {
                "ma_hang": "SP01",
                "ten_san_pham": "Bột trét Jotun nội thất 40kg",
                "don_vi": "bao",
                "gia_ban": 300_000,
            }
        ]
    )
    conn4.register("hoa_don_ban_hang", orders_price)
    conn4.register("san_pham", san_pham_price)
    ctx4 = BusinessContext(
        business_id="synthetic_paint_price",
        data_dir=".",
        tables={"hoa_don_ban_hang": orders_price, "san_pham": san_pham_price},
        duckdb_conn=conn4,
    )
    try:
        from zaic_2026_datamate.semantic_contract import (
            BusinessSemanticContract,
            build_query_plan,
        )

        plan = build_query_plan(
            "Giá bán một bao Bột trét Jotun nội thất 40kg vào ngày 03/05/2026 là bao nhiêu?",
            ctx4,
            "2026-05-03",
            BusinessSemanticContract.from_context(ctx4),
        )
        assert plan is None or plan.operation != "debt_snapshot"
        ans_type, ans_val, _, _ = agent.answer_query(
            "Giá bán một bao Bột trét Jotun nội thất 40kg vào ngày 03/05/2026 là bao nhiêu?",
            ctx4,
            as_of_date="2026-05-03",
        )
        assert ans_type != "unanswerable"
        if ans_type == "number":
            assert ans_val == 300_000
    finally:
        conn4.close()
