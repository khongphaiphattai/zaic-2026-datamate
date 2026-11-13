"""Phase 1 Slice 2 TDD specs: zero dev_* across src/, schema parity, & 5 SME rules."""

import ast
from datetime import date
from pathlib import Path
import re

import duckdb
import pandas as pd
import pytest

from zaic_2026_datamate.data_loader import BusinessContext, clean_dataframe, parse_mixed_excel_dates
from zaic_2026_datamate.intent_parser import find_revenue_column
from zaic_2026_datamate.rolling_window import resolve_rolling_window_query


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


class _StringLiteralFinder(ast.NodeVisitor):
    """Collect runtime string literals, properly skipping module/func/class docstrings."""

    def __init__(self) -> None:
        self.literals: list[tuple[int, str]] = []

    def _visit_body(self, body: list[ast.stmt]) -> None:
        for i, stmt in enumerate(body):
            if (
                i == 0
                and isinstance(stmt, ast.Expr)
                and isinstance(stmt.value, ast.Constant)
                and isinstance(stmt.value.value, str)
            ):
                continue
            self.visit(stmt)

    def visit_Module(self, node: ast.Module) -> None:
        self._visit_body(node.body)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_body(node.body)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_body(node.body)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._visit_body(node.body)

    def visit_Constant(self, node: ast.Constant) -> None:
        if isinstance(node.value, str):
            self.literals.append((getattr(node, "lineno", 0), node.value))


def test_zero_dev_ids_across_src():
    """Static AST guard: zero dev_*/biz_*/priv_* literals or business_id branches across src."""
    offending: dict[str, list[tuple[int, str]]] = {}
    bad_branching: dict[str, list[tuple[int, str]]] = {}
    id_pat = re.compile(r"\b(?:dev|biz|priv)_\d+\b")

    def _is_business_id_expr(node: ast.AST) -> bool:
        return (isinstance(node, ast.Name) and node.id == "business_id") or (
            isinstance(node, ast.Attribute) and node.attr == "business_id"
        )

    for py_file in sorted(Path("src/zaic_2026_datamate").glob("*.py")):
        src_text = py_file.read_text(encoding="utf-8")
        tree = ast.parse(src_text)
        finder = _StringLiteralFinder()
        finder.visit(tree)
        hits = [(ln, val) for ln, val in finder.literals if id_pat.search(val)]
        if hits:
            offending[py_file.name] = hits

        for node in ast.walk(tree):
            if isinstance(node, ast.Compare):
                exprs = [node.left, *node.comparators]
                if any(_is_business_id_expr(e) for e in exprs):
                    other_consts = [
                        c.value
                        for e in exprs
                        if not _is_business_id_expr(e)
                        for c in ast.walk(e)
                        if isinstance(c, ast.Constant) and isinstance(c.value, str)
                    ]
                    disallowed = [v for v in other_consts if v not in ("test", "fixture")]
                    if disallowed:
                        bad_branching.setdefault(py_file.name, []).append(
                            (getattr(node, "lineno", 0), str(disallowed))
                        )

    assert not offending, f"Runtime dev_*/biz_*/priv_* literals remain in src: {offending}"
    assert not bad_branching, f"Disallowed business_id branches in src: {bad_branching}"


def test_schema_driven_revenue_column_without_dev_ids():
    ctx_tt = _make_ctx(
        "priv_mb_01",
        {"ban_hang": pd.DataFrame([{"hd": "O1", "dg": 1000, "tt": 1000, "ten_hang": "Sữa"}])},
    )
    rev_expr = find_revenue_column(ctx_tt, "ban_hang")
    assert rev_expr is not None and "Voucher" in rev_expr and "tt" in rev_expr

    ctx_dh = _make_ctx(
        "priv_ecom_01",
        {"don_hang": pd.DataFrame([{"thanh_tien": 50000, "tien_giam": 5000, "phi_van_chuyen": 15000}])},
    )
    rev_expr2 = find_revenue_column(ctx_dh, "don_hang")
    assert rev_expr2 == "(thanh_tien + COALESCE(tien_giam, 0) - COALESCE(phi_van_chuyen, 0))"


def test_unified_debt_and_installment_merge():
    from zaic_2026_datamate.mobile_installment import unify_debt_and_installment_ledgers

    so_no_df = pd.DataFrame(
        [
            {"ngay": "2025-07-02", "khach": "An", "sdt": "0901", "no": 300000.0, "da_tra": "100k", "ngay_tra": "2025-07-10", "ghi_chu": "STT 1"},
            {"ngay": "2025-07-03", "khach": "Binh", "sdt": "0902", "no": 200000.0, "da_tra": "200k", "ngay_tra": "2025-07-12", "ghi_chu": "STT 2"},
        ]
    )
    tra_gop_df = pd.DataFrame(
        [
            {"ngay": "2025-07-03", "stt": 10, "khach": "An", "sdt": "0901", "may": "IP16", "gia": 10000000.0, "tra_truoc": "2tr", "so_ky": 4, "gop_thang": "2tr", "han_gop_ngay": 3, "ngay_gop": "2025-08-03", "da_gop": "2tr"},
        ]
    )
    unified = unify_debt_and_installment_ledgers(so_no_df, tra_gop_df, as_of_date="2025-08-10")
    # An owes 200k in so_no + 6M remaining in tra_gop = 6.2M; Binh paid off = 0
    by_name = unified.groupby("khach_norm")["remaining_debt"].sum().to_dict()
    assert round(by_name.get("an", 0.0)) == 6_200_000
    assert round(by_name.get("binh", 0.0)) == 0


def test_shared_inline_topping_split():
    from zaic_2026_datamate.cleaner import split_inline_toppings

    base, tops = split_inline_toppings(
        "Trà đào cam sả (M) + Trân châu đen (7.000đ)",
        line_revenue=42000.0,
        line_qty=1.0,
        line_base_qty=1.0,
    )
    assert base["name"] == "Trà đào cam sả (M)"
    assert base["amount"] == 35000.0
    assert base["qty"] == 1.0
    assert len(tops) == 1 and tops[0]["name"] == "Topping Trân châu đen" and tops[0]["amount"] == 7000.0

    base2, tops2 = split_inline_toppings(
        "Trà sữa (L) + 2 Trân châu trắng (10.000đ) + Pudding trứng (8.000đ)",
        line_revenue=53000.0,
        line_qty=1.0,
        line_base_qty=1.0,
    )
    assert base2["name"] == "Trà sữa (L)"
    assert base2["amount"] == 35000.0
    assert len(tops2) == 2
    assert tops2[0]["qty"] == 2.0 and tops2[0]["amount"] == 10000.0
    assert tops2[1]["name"] == "Topping Pudding trứng" and tops2[1]["amount"] == 8000.0


def test_parse_mixed_excel_dates_with_python_date_and_serials():
    s = pd.Series([45809, "45689.0", date(2025, 3, 15), "15/07/2025 14:30", "2025-08-01"])
    parsed = parse_mixed_excel_dates(s)
    assert parsed.notna().all()
    assert parsed.dt.strftime("%Y-%m-%d").tolist() == [
        "2025-06-01",
        "2025-02-01",
        "2025-03-15",
        "2025-07-15",
        "2025-08-01",
    ]
    raw_df = pd.DataFrame([{"thoi_diem": 45809, "so_luong": 2}])
    cleaned, _ = clean_dataframe(raw_df)
    assert pd.api.types.is_datetime64_any_dtype(cleaned["thoi_diem"])


def test_rolling_window_joins_respect_status_clause_and_base_qty():
    don_hang = pd.DataFrame(
        [
            {"ma_bill": "D1", "ngay_lap": "2025-08-05", "xu_ly": "Hoàn thành", "thanh_tien": 100000},
            {"ma_bill": "D2", "ngay_lap": "2025-08-06", "xu_ly": "Đã hủy", "thanh_tien": 500000},
        ]
    )
    chi_tiet_don = pd.DataFrame(
        [
            {"ma_bill": "D1", "dvt": "gói", "so_luong": 3.0},
            {"ma_bill": "D2", "dvt": "gói", "so_luong": 50.0},
        ]
    )
    ctx_join = _make_ctx("priv_join_01", {"don_hang": don_hang, "chi_tiet_don": chi_tiet_don})
    res = resolve_rolling_window_query(
        "7 ngày gần nhất bán được bao nhiêu gói?", ctx_join, "2025-08-07"
    )
    assert res == ("number", 3, None)


def test_repair_shop_dynamic_danh_sach_tho_no_hardcodes():
    from zaic_2026_datamate import repair_shop

    assert not hasattr(repair_shop, "HOA_HONG_RATES")
    so_sua_xe = pd.DataFrame(
        [
            {"ngay": "2026-01-10", "tho": "Khánh", "xe": "59A1-12345 Vision", "lam_gi": "Thay nhớt", "tien": 1000000},
            {"ngay": "2026-01-12", "tho": "Tuấn", "xe": "59B1-67890 Lead", "lam_gi": "Sửa điện", "tien": 2000000},
        ]
    )
    danh_sach_tho = pd.DataFrame(
        [
            {"ho_ten": "Nguyễn Bảo Khánh", "ty_le_hoa_hong": "30%"},
            {"ho_ten": "Trần Minh Tuấn", "ty_le_hoa_hong": 0.25},
        ]
    )
    ctx = _make_ctx("priv_repair_01", {"so_sua_xe": so_sua_xe, "danh_sach_tho": danh_sach_tho})
    assert repair_shop.resolve_repair_shop_query(
        "Tháng 1/2026 tiền hoa hồng của thợ Khánh là bao nhiêu?", ctx, "2026-02-01"
    ) == ("number", 300000, None)
    assert repair_shop.resolve_repair_shop_query(
        "Tháng 1/2026 bảng hoa hồng từng thợ xếp giảm dần?", ctx, "2026-02-01"
    ) == (
        "table",
        {
            "columns": ["tho", "hoa_hong"],
            "rows": [["Trần Minh Tuấn", 500000], ["Nguyễn Bảo Khánh", 300000]],
        },
        None,
    )

    # Without rate table, commission queries must abstain instead of fabricating rates
    ctx_no_rates = _make_ctx("priv_repair_02", {"so_sua_xe": so_sua_xe})
    res_no_rate = repair_shop.resolve_repair_shop_query(
        "Tháng 1/2026 tiền hoa hồng của thợ Khánh là bao nhiêu?", ctx_no_rates, "2026-02-01"
    )
    assert res_no_rate[0] == "abstain"


def test_header_detail_shift_revenue_filters_by_shift():
    from zaic_2026_datamate.intent_parser import resolve_query

    don_hang = pd.DataFrame(
        [
            {"ma_don_hang": "O1", "ngay": "2025-05-10", "thoi_gian_tao": "2025-05-10 08:30:00", "trang_thai": "Hoàn thành", "tong_don": 100000},
            {"ma_don_hang": "O2", "ngay": "2025-05-10", "thoi_gian_tao": "2025-05-10 19:00:00", "trang_thai": "Hoàn thành", "tong_don": 500000},
        ]
    )
    chi_tiet_don = pd.DataFrame(
        [
            {"ma_don_hang": "O1", "ten_hang": "Cà phê", "so_luong": 1, "amount": 100000},
            {"ma_don_hang": "O2", "ten_hang": "Trà", "so_luong": 2, "amount": 500000},
        ]
    )
    ctx = _make_ctx("priv_hd_shift", {"don_hang": don_hang, "chi_tiet_don": chi_tiet_don})
    res = resolve_query("Ngày 10/05/2025 doanh thu ca sáng là bao nhiêu?", ctx, "2025-05-15")
    assert res[:2] == ("number", 100000)


def test_rolling_flat_pack_unit_prefers_raw_qty_over_base_qty():
    danh_sach_hang = pd.DataFrame(
        [{"ten_hang_hoa": "Vitamin C", "don_vi_co_ban": "viên"}]
    )
    ban_hang = pd.DataFrame(
        [
            {"ngay": "2025-08-05", "ma_don": "B1", "ten_hang_hoa": "Vitamin C", "don_vi_tinh": "hộp", "so_luong": 2.0, "so_luong_quy_doi": 20.0, "trang_thai": "Hoàn thành"},
            {"ngay": "2025-08-06", "ma_don": "B2", "ten_hang_hoa": "Vitamin C", "don_vi_tinh": "viên", "so_luong": 5.0, "so_luong_quy_doi": 5.0, "trang_thai": "Hoàn thành"},
        ]
    )
    ctx = _make_ctx("priv_pack_flat", {"danh_sach_hang": danh_sach_hang, "ban_hang": ban_hang})
    assert resolve_rolling_window_query(
        "7 ngày gần nhất bán được bao nhiêu hộp?", ctx, "2025-08-07"
    ) == ("number", 2, None)
    assert resolve_rolling_window_query(
        "7 ngày gần nhất bán được bao nhiêu viên?", ctx, "2025-08-07"
    ) == ("number", 25, None)


def test_repair_shop_roster_ignores_multiword_informal_aliases():
    from zaic_2026_datamate import repair_shop

    so_sua_xe = pd.DataFrame(
        [
            {"ngay": "2026-01-10", "tho": "a Phúc", "xe": "59A1-11111 Wave", "lam_gi": "Thay nhớt", "tien": 1000000},
            {"ngay": "2026-01-12", "tho": "chú An", "xe": "59B1-22222 Future", "lam_gi": "Thay lốp", "tien": 2000000},
        ]
    )
    danh_sach_tho = pd.DataFrame(
        [
            {"ho_ten": "Nguyễn Văn Phúc", "ty_le_hoa_hong": "30%"},
            {"ho_ten": "Trần An", "ty_le_hoa_hong": "20%"},
        ]
    )
    ctx = _make_ctx("priv_repair_alias", {"so_sua_xe": so_sua_xe, "danh_sach_tho": danh_sach_tho})
    assert repair_shop.resolve_repair_shop_query(
        "Tháng 1/2026 tiền hoa hồng của thợ Phúc là bao nhiêu?", ctx, "2026-02-01"
    ) == ("number", 300000, None)
    assert repair_shop.resolve_repair_shop_query(
        "Tháng 1/2026 bảng hoa hồng từng thợ xếp giảm dần?", ctx, "2026-02-01"
    ) == (
        "table",
        {
            "columns": ["tho", "hoa_hong"],
            "rows": [["Trần An", 400000], ["Nguyễn Văn Phúc", 300000]],
        },
        None,
    )


def test_orders_above_threshold_excludes_trang_thai_don_hang_cancels_and_returns():
    from zaic_2026_datamate.domain_ext import resolve_domain_extensions

    don_hang_chi_tiet = pd.DataFrame(
        [
            {"ma_don_hang": "O1", "ngay_dat_hang": "2025-05-05", "trang_thai_don_hang": "Đã hoàn thành", "tong_tien": 100000},
            {"ma_don_hang": "O2", "ngay_dat_hang": "2025-05-06", "trang_thai_don_hang": "Đã hoàn thành", "tong_tien": 30000},
            {"ma_don_hang": "O3", "ngay_dat_hang": "2025-05-07", "trang_thai_don_hang": "Đã hủy", "tong_tien": 200000},
            {"ma_don_hang": "O4", "ngay_dat_hang": "2025-05-08", "trang_thai_don_hang": "Đã hoàn hàng", "tong_tien": 150000},
        ]
    )
    ctx = _make_ctx("priv_thresh_status", {"don_hang_chi_tiet": don_hang_chi_tiet})
    res = resolve_domain_extensions(
        "Tháng 5/2025 có bao nhiêu đơn hàng có doanh thu từ 50.000 đồng trở lên?",
        ctx,
        "2025-06-01",
    )
    assert res == ("number", 1, None)


def test_inventory_split_unit_pairs_vi_vien_and_hop_vi():
    from zaic_2026_datamate.domain_ext import resolve_domain_extensions

    san_pham = pd.DataFrame(
        [
            {"ma_sku": "SKU_A", "ten_san_pham": "Thuốc A hộp 10 vỉ x 8 viên", "don_vi": "viên"},
            {"ma_sku": "SKU_B", "ten_san_pham": "Thuốc B hộp 10 vỉ x 20 viên", "don_vi": "vỉ"},
        ]
    )
    quy_doi = pd.DataFrame(
        [
            {"ten_san_pham": "Thuốc A hộp 10 vỉ x 8 viên", "don_vi_quy_doi": "vỉ", "ti_le_quy_doi": 8, "don_vi_co_ban": "viên"},
            {"ten_san_pham": "Thuốc A hộp 10 vỉ x 8 viên", "don_vi_quy_doi": "hộp", "ti_le_quy_doi": 80, "don_vi_co_ban": "viên"},
            {"ten_san_pham": "Thuốc B hộp 10 vỉ x 20 viên", "don_vi_quy_doi": "hộp", "ti_le_quy_doi": 10, "don_vi_co_ban": "vỉ"},
        ]
    )
    nhap_hdr = pd.DataFrame([{"ma_don_nhap": "NH1", "ngay_nhap": "2026-01-01"}])
    nhap_dtl = pd.DataFrame(
        [
            {"ma_don_nhap": "NH1", "ma_sku": "SKU_A", "ma_lo": "LO_A", "so_luong_nhap": 100},
            {"ma_don_nhap": "NH1", "ma_sku": "SKU_B", "ma_lo": "LO_B", "so_luong_nhap": 200},
        ]
    )
    don_hang = pd.DataFrame([{"ma_don_hang": "DH1", "ngay_dat_hang": "2026-01-02", "trang_thai_don_hang": "Đã hoàn thành"}])
    don_lo = pd.DataFrame(
        [
            {"ma_don_hang": "DH1", "ma_lo": "LO_A", "so_luong": 30, "so_luong_tra": 0},
            {"ma_don_hang": "DH1", "ma_lo": "LO_B", "so_luong": 39, "so_luong_tra": 0},
        ]
    )
    ctx = _make_ctx(
        "priv_inv_split",
        {
            "du_lieu_xuat_danh_sach_san_pham": san_pham,
            "du_lieu_xuat_danh_sach_quy_doi": quy_doi,
            "du_lieu_xuat_don_nhap_hang": nhap_hdr,
            "du_lieu_xuat_don_nhap_hang_chi_tiet": nhap_dtl,
            "don_hang": don_hang,
            "du_lieu_xuat_don_hang_lo": don_lo,
        },
    )
    res_a = resolve_domain_extensions(
        "cuoi ngay hom nay thuoc a hop 10 vi x 8 vien trong kho con bao nhieu vi chan, le bao nhieu vien",
        ctx,
        "2026-01-05",
    )
    assert res_a == ("list", [8, 6], None)

    res_b = resolve_domain_extensions(
        "Xem giúp hết hôm nay trong kho còn bao nhiêu hộp Thuốc B hộp 10 vỉ x 20 viên nguyên và bao nhiêu vỉ lẻ?",
        ctx,
        "2026-01-05",
    )
    assert res_b == ("list", [16, 1], None)


def test_inventory_telex_toonf_kho_routes_to_snapshot_stock():
    from zaic_2026_datamate.domain_ext import resolve_domain_extensions

    ton_kho = pd.DataFrame(
        [{"ma_hang": "H1", "ten_hang": "Trà xanh 0 độ 455ml", "dvt": "chai", "ton_cuoi_ky": 102}]
    )
    ctx = _make_ctx("priv_telex_inv", {"bao_cao_ton_kho": ton_kho})
    res = resolve_domain_extensions(
        "check giùm toonf kho trà xanh 0 độ 455ml cuối ngày hôm nay là bao nhiêu chai",
        ctx,
        "2026-04-01",
    )
    assert res == ("number", 102, None)


def test_unit_conversion_map_picks_numeric_ti_le_quy_doi_over_don_vi_col():
    from zaic_2026_datamate.intent_parser import resolve_query

    quy_doi = pd.DataFrame(
        [
            {
                "ten_san_pham": "Nước tăng lực A",
                "don_vi_quy_doi": "thùng",
                "ti_le_quy_doi": 24,
                "don_vi_co_ban": "lon",
            },
            {
                "ten_san_pham": "Sữa hộp B",
                "don_vi_quy_doi": "lốc",
                "ti_le_quy_doi": 4,
                "don_vi_co_ban": "hộp",
            },
        ]
    )
    don_hang_chi_tiet = pd.DataFrame(
        [
            {
                "ma_don_hang": "DH1",
                "ngay_dat_hang": "2025-09-05",
                "trang_thai_don_hang": "Đã hoàn thành",
                "ten_san_pham": "Nước tăng lực A",
                "don_vi_tinh": "thùng",
                "so_luong_san_pham": 2.0,
                "gia_san_pham": 240000.0,
                "tong_tien": 480000.0,
            },
            {
                "ma_don_hang": "DH2",
                "ngay_dat_hang": "2025-09-06",
                "trang_thai_don_hang": "Đã hoàn thành",
                "ten_san_pham": "Nước tăng lực A",
                "don_vi_tinh": "thùng",
                "so_luong_san_pham": None,
                "gia_san_pham": 240000.0,
                "tong_tien": 240000.0,
            },
            {
                "ma_don_hang": "DH1",
                "ngay_dat_hang": "2025-09-05",
                "trang_thai_don_hang": "Đã hoàn thành",
                "ten_san_pham": "Sữa hộp B",
                "don_vi_tinh": "hộp",
                "so_luong_san_pham": 10.0,
                "gia_san_pham": 10000.0,
                "tong_tien": 100000.0,
            },
        ]
    )
    ctx = _make_ctx(
        "priv_unit_conv_topk",
        {
            "danh_sach_quy_doi": quy_doi,
            "don_hang_chi_tiet": don_hang_chi_tiet,
        },
    )
    res_topk = resolve_query(
        "Tháng 9/2025 top 2 sản phẩm bán chạy nhất theo số lượng là gì?",
        ctx,
        as_of_date="2025-10-01",
    )
    assert res_topk[:2] == ("list", ["Nước tăng lực A", "Sữa hộp B"])

    res_qty = resolve_query(
        "Tháng 9/2025 tổng số lượng Nước tăng lực A bán ra tính theo đơn vị cơ bản là bao nhiêu?",
        ctx,
        as_of_date="2025-10-01",
    )
    assert res_qty[:2] == ("number", 72)


def test_canonical_item_grouping_handles_id_and_case_space_variants():
    from zaic_2026_datamate.intent_parser import resolve_query

    # Case A: Header + Detail with ma_hang having mixed shortened/accentless/spaced names
    dm = pd.DataFrame(
        [
            {"ma_hang": "SP01", "ten_hang": "Snack Oishi tôm cay 40g"},
            {"ma_hang": "SP02", "ten_hang": "Pepsi 330ml"},
            {"ma_hang": "SP03", "ten_hang": "Bánh quy bơ"},
        ]
    )
    hd = pd.DataFrame(
        [
            {"ma_hd": "HD1", "ngay_lap": "2025-11-10", "trang_thai": "Hoàn thành"},
            {"ma_hd": "HD2", "ngay_lap": "2025-11-11", "trang_thai": "Hoàn thành"},
        ]
    )
    ct = pd.DataFrame(
        [
            {"ma_hd": "HD1", "ma_hang": "SP01", "ten_hang": "Snack Oishi tôm cay 40g", "so_luong": 10, "thanh_tien": 50000},
            {"ma_hd": "HD2", "ma_hang": "SP01", "ten_hang": "tom cay", "so_luong": 15, "thanh_tien": 75000},
            {"ma_hd": "HD1", "ma_hang": "SP02", "ten_hang": "Pepsi  330ml", "so_luong": 12, "thanh_tien": 120000},
            {"ma_hd": "HD2", "ma_hang": "SP02", "ten_hang": "Pepsi 330 ml", "so_luong": 8, "thanh_tien": 80000},
            {"ma_hd": "HD1", "ma_hang": "SP03", "ten_hang": "Bánh quy bơ", "so_luong": 18, "thanh_tien": 180000},
        ]
    )
    ctx_a = _make_ctx(
        "priv_item_canon_id",
        {
            "danh_muc_hang_hoa": dm,
            "danh_sach_hoa_don": hd,
            "danh_sach_hoa_don_chi_tiet": ct,
        },
    )
    res_a = resolve_query(
        "Tháng 11/2025 top 2 mặt hàng bán chạy nhất theo số lượng là những mặt hàng nào?",
        ctx_a,
        as_of_date="2025-12-01",
    )
    assert res_a[:2] == ("list", ["Snack Oishi tôm cay 40g", "Pepsi 330ml"])

    # Case B: Combined table without ma_hang, case variants for coffee/tea
    so_bh = pd.DataFrame(
        [
            {"ma_don": "M1", "ngay": "2026-03-01", "ten_mon": "Cà phê sữa (M)", "so_luong": 10, "thanh_tien": 300000},
            {"ma_don": "M2", "ngay": "2026-03-02", "ten_mon": "CÀ PHÊ SỮA (M)", "so_luong": 5, "thanh_tien": 150000},
            {"ma_don": "M3", "ngay": "2026-03-03", "ten_mon": "Cà Phê Sữa (m)", "so_luong": 5, "thanh_tien": 150000},
            {"ma_don": "M4", "ngay": "2026-03-04", "ten_mon": "Cà phê đen (M)", "so_luong": 18, "thanh_tien": 450000},
            {"ma_don": "M5", "ngay": "2026-03-05", "ten_mon": "Trà đào cam sả (M)", "so_luong": 12, "thanh_tien": 420000},
        ]
    )
    ctx_b = _make_ctx("priv_item_canon_combined", {"so_chi_tiet_ban_hang": so_bh})
    res_b = resolve_query(
        "Tháng 3/2026 top 2 món bán chạy nhất theo số lượng là gì?",
        ctx_b,
        as_of_date="2026-04-01",
    )
    assert res_b[:2] == ("list", ["Cà phê sữa (M)", "Cà phê đen (M)"])


def test_telex_soos_kg_and_thangs_returns_quantity_not_revenue():
    from zaic_2026_datamate.intent_parser import resolve_query

    df = pd.DataFrame(
        [
            {
                "ma_don_hang": "D1",
                "ngay": "2025-09-10",
                "ten_hang": "Cam sành",
                "don_vi": "kg",
                "so_luong": 32,
                "don_gia": 30000,
                "thanh_tien": 945000,
            }
        ]
    )
    ctx = _make_ctx("priv_telex_soos", {"so_ban_hang": df})
    res = resolve_query(
        "soos kg Cam sành bán được trong thangs 9/2025 là bn?",
        ctx,
        as_of_date="2025-10-01",
    )
    assert res[:2] == ("number", 32)


def test_bua_nao_peak_day_and_product_dien_thoai_not_channel_phone():
    from zaic_2026_datamate.intent_parser import resolve_query

    df = pd.DataFrame(
        [
            {
                "ma_don": "P1",
                "ngay": "2026-01-10",
                "ten_san_pham": "Kệ điện thoại để bàn",
                "kenh_ban": "Tại cửa hàng",
                "so_luong": 2,
                "thanh_tien": 250000,
            },
            {
                "ma_don": "P2",
                "ngay": "2026-01-11",
                "ten_san_pham": "Dây sạc",
                "kenh_ban": "Tại cửa hàng",
                "so_luong": 4,
                "thanh_tien": 400000,
            },
            {
                "ma_don": "P3",
                "ngay": "2026-04-02",
                "ten_san_pham": "Kệ điện thoại để bàn",
                "kenh_ban": "Tại cửa hàng",
                "so_luong": 1,
                "thanh_tien": 100000,
            },
            {
                "ma_don": "P4",
                "ngay": "2026-04-05",
                "ten_san_pham": "Ốp lưng",
                "kenh_ban": "Tại cửa hàng",
                "so_luong": 5,
                "thanh_tien": 500000,
            },
        ]
    )
    ctx = _make_ctx("priv_bua_nao_product", {"so_ban_hang": df})
    res_peak = resolve_query(
        "Tháng 4/2026 bữa nào tiệm bán được nhiều tiền nhứt?",
        ctx,
        as_of_date="2026-05-01",
    )
    assert res_peak[:2] == ("date", "2026-04-05")

    res_prod = resolve_query(
        "Tháng 1/2026 tiệm bán Kệ điện thoại để bàn được bao nhiêu tiền?",
        ctx,
        as_of_date="2026-02-01",
    )
    assert res_prod[:2] == ("number", 250000)


def test_phone_store_analytics_bua_nao_peak_day_and_product_not_store_total():
    from zaic_2026_datamate.domain_ext import resolve_domain_extensions

    sbh = pd.DataFrame(
        [
            {"ma_don": "D1", "ngay": "2026-01-10", "khach_hang": "A", "ten_hang": "Kệ điện thoại để bàn", "sl": 2, "thanh_tien": 250000, "khach_tra": 250000},
            {"ma_don": "D2", "ngay": "2026-01-11", "khach_hang": "B", "ten_hang": "Dây sạc", "sl": 4, "thanh_tien": 400000, "khach_tra": 400000},
            {"ma_don": "D3", "ngay": "2026-04-02", "khach_hang": "C", "ten_hang": "Ốp lưng", "sl": 1, "thanh_tien": 100000, "khach_tra": 100000},
            {"ma_don": "D4", "ngay": "2026-04-05", "khach_hang": "D", "ten_hang": "Dây sạc", "sl": 5, "thanh_tien": 500000, "khach_tra": 500000},
        ]
    )
    gt = pd.DataFrame([{"ma_don": "D1", "ngay": "2026-01-10", "ten": "ke dt", "thanh_tien": 250000}])
    ctx = _make_ctx("priv_phone_store", {"so_ban_hang": sbh, "so_ban_hang_ghi_tat": gt})

    res_peak = resolve_domain_extensions(
        "Tháng 4/2026 bữa nào tiệm bán được nhiều tiền nhứt?",
        ctx,
        "2026-05-01",
    )
    assert res_peak == ("date", "2026-04-05", None)

    res_prod = resolve_domain_extensions(
        "Tháng 1/2026 tiệm bán Kệ điện thoại để bàn được bao nhiêu tiền?",
        ctx,
        "2026-02-01",
    )
    assert res_prod is None

    res_total = resolve_domain_extensions(
        "Tháng 1/2026 tiệm bán được tổng cộng bao nhiêu tiền?",
        ctx,
        "2026-02-01",
    )
    assert res_total == ("number", 650000, None)


def test_technical_abstain_gate_blocks_number_and_list_for_peak_period_bua_nao():
    from zaic_2026_datamate.hybrid_agent import technical_abstain_gate

    q_bua = "Tháng 4/2026 bữa nào tiệm bán được nhiều tiền nhứt?"
    assert technical_abstain_gate(q_bua, "number", 1169052000, "ok", "rule")[0] == "abstain"
    assert technical_abstain_gate(q_bua, "list", [100, 200], "ok", "rule")[0] == "abstain"
    assert technical_abstain_gate(q_bua, "date", "2026-04-05", "ok", "rule")[:2] == ("date", "2026-04-05")
    assert (
        technical_abstain_gate(
            "Tháng 4/2026 giờ nào bán nhiều đơn nhất?", "number", 14, "ok", "rule"
        )[:2]
        == ("number", 14)
    )
    assert technical_abstain_gate("Bảng top", "table", {"columns": ["a"], "rows": []}, "ok", "rule")[0] == "abstain"
    assert (
        technical_abstain_gate("Bảng top", "table", {"columns": ["a"], "rows": [[float("nan")]]}, "ok", "rule")[0]
        == "abstain"
    )


def test_repair_shop_general_month_range_not_hardcoded_th9_th11():
    from types import SimpleNamespace
    from zaic_2026_datamate.repair_shop import filter_df_by_period

    df = pd.DataFrame(
        [
            {"month": "2026-01", "date_str": "2026-01-10"},
            {"month": "2026-02", "date_str": "2026-02-15"},
            {"month": "2026-03", "date_str": "2026-03-20"},
            {"month": "2026-04", "date_str": "2026-04-05"},
            {"month": "2026-05", "date_str": "2026-05-01"},
        ]
    )
    p_none = SimpleNamespace(month_str=None, quarter_str=None, exact_date=None, start_date=None, end_date=None)
    res = filter_df_by_period(df, p_none, "tu th2/2026 den th4/2026 doanh thu bn", as_of_date="2026-06-01")
    assert res is not None
    assert list(res["month"]) == ["2026-02", "2026-03", "2026-04"]

    df2 = pd.DataFrame(
        [
            {"month": "2025-08", "date_str": "2025-08-10"},
            {"month": "2025-09", "date_str": "2025-09-10"},
            {"month": "2025-10", "date_str": "2025-10-10"},
            {"month": "2025-11", "date_str": "2025-11-10"},
            {"month": "2025-12", "date_str": "2025-12-10"},
        ]
    )
    res2 = filter_df_by_period(df2, p_none, "th9/2025 den het th11/2025 co bnhiu xe", as_of_date="2026-06-01")
    assert res2 is not None
    assert list(res2["month"]) == ["2025-09", "2025-10", "2025-11"]


def test_price_change_ignores_arbitrary_month_vs_month_comparison():
    from zaic_2026_datamate.price_change import resolve_price_change_query

    df = pd.DataFrame(
        [{"ma_don": "1", "ngay": "2026-06-01", "san_pham": "Trà sữa", "so_luong": 1, "don_gia": 30000, "thanh_tien": 30000}]
    )
    ctx = _make_ctx("priv_cmp_guard", {"ban_hang": df})
    assert (
        resolve_price_change_query(
            "Th7/2026 so với tháng trước lên giá hay giảm giá bn %?",
            ctx,
            as_of_date="2026-08-01",
        )
        is None
    )


def _iter_non_docstring_string_literals(path: "Path") -> list[tuple[int, str]]:
    import ast

    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, str(path))
    doc_nodes: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            if (
                node.body
                and isinstance(node.body[0], ast.Expr)
                and isinstance(node.body[0].value, ast.Constant)
                and isinstance(node.body[0].value.value, str)
            ):
                doc_nodes.add(id(node.body[0].value))

    hits: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in doc_nodes
        ):
            hits.append((node.lineno, node.value))
    return hits


def test_zero_hardcoded_month_date_literals_in_src():
    import re
    from pathlib import Path

    date_lit_re = re.compile(
        r"\bth\d{1,2}/20\d{2}\b|\b20\d{2}-\d{2}(?:-\d{2})?\b|\b\d{1,2}/\d{1,2}/20\d{2}\b",
        re.IGNORECASE,
    )
    offenders: list[str] = []
    for path in sorted(Path("src/zaic_2026_datamate").glob("*.py")):
        for lineno, value in _iter_non_docstring_string_literals(path):
            if date_lit_re.search(value):
                offenders.append(f"{path}:{lineno}: {value!r}")
    assert not offenders, f"Hardcoded month/date literals in src/: {offenders}"


def test_zero_public_test_paths_in_src_and_tests():
    forbidden = "public_" + "test"
    offenders: list[str] = []
    for folder in ("src/zaic_2026_datamate", "tests"):
        for path in sorted(Path(folder).glob("*.py")):
            for lineno, value in _iter_non_docstring_string_literals(path):
                if forbidden in value:
                    offenders.append(f"{path}:{lineno}: {value!r}")
    assert not offenders, f"Unexpected {forbidden} path reference: {offenders}"


def test_no_cross_module_private_imports_or_dead_private_helpers_in_src():
    offenders_resolved = []
    src_files = sorted(Path("src/zaic_2026_datamate").glob("*.py"))
    cross_priv_imports: list[str] = offenders_resolved
    priv_defs: list[tuple[Path, int, str]] = []
    all_refs: set[str] = set()

    for root in ("src/zaic_2026_datamate", "tests", "scripts"):
        for path in sorted(Path(root).glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Name):
                    all_refs.add(node.id)
                elif isinstance(node, ast.Attribute):
                    all_refs.add(node.attr)
                elif isinstance(node, ast.ImportFrom):
                    for alias in node.names:
                        all_refs.add(alias.name)

    for path in src_files:
        tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
        mod_stem = path.stem
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.ImportFrom)
                and node.module
                and node.module.startswith("zaic_2026_datamate.")
            ):
                target_mod = node.module.split(".", 1)[1]
                if target_mod != mod_stem:
                    for alias in node.names:
                        if alias.name.startswith("_") and not alias.name.startswith("__"):
                            cross_priv_imports.append(
                                f"{path}:{node.lineno}: from {node.module} import {alias.name}"
                            )
            elif (
                isinstance(node, ast.FunctionDef)
                and node.name.startswith("_")
                and not node.name.startswith("__")
            ):
                priv_defs.append((path, node.lineno, node.name))

    assert not cross_priv_imports, f"Cross-module private imports found: {cross_priv_imports}"
    dead_priv = [
        f"{path}:{lineno}: {name}"
        for path, lineno, name in priv_defs
        if name not in all_refs
    ]
    assert not dead_priv, f"Dead unreferenced private helpers in src/: {dead_priv}"


@pytest.mark.parametrize(
    ("question", "ans_type", "ans_val"),
    [
        ("any", "number", float("nan")),
        ("any", "number", float("-inf")),
        ("any", "percent", float("inf")),
        ("any", "number", False),
        ("any", "percent", True),
        ("any", "number", "abc"),
        ("any", "boolean", 1),
        ("any", "boolean", "true"),
        ("any", "string", None),
        ("any", "string", "NaT"),
        ("any", "string", "null"),
        ("any", "date", "2025-02-29"),
        ("any", "date", "2019-01"),
        ("any", "date", "05/06/2025"),
        ("any", "list", []),
        ("any", "list", [1, None]),
        ("any", "list", [1.0, float("nan")]),
        ("any", "table", {}),
        ("any", "table", {"columns": [], "rows": [[1]]}),
        ("any", "table", {"columns": ["a", "b"], "rows": [[1]]}),
        ("any", "table", {"columns": ["a"], "rows": [[float("inf")]]}),
        ("Doanh thu tháng 5 có tăng liên tục không?", "number", 10),
        ("Tháng nào doanh thu cao nhất?", "list", [100, 200]),
        ("Bữa nào bán được nhiều nhất?", "number", 500000),
    ],
)
def test_technical_abstain_gate_contract_matrix(question, ans_type, ans_val):
    from zaic_2026_datamate.hybrid_agent import technical_abstain_gate

    gated = technical_abstain_gate(question, ans_type, ans_val, "reason", "fast_path")
    assert gated[:2] == ("abstain", None)


def test_hybrid_agent_enforces_technical_abstain_gate_on_fast_path(monkeypatch):
    from zaic_2026_datamate import hybrid_agent

    monkeypatch.setattr(hybrid_agent, "resolve_query", lambda *args, **kwargs: ("number", float("nan"), None))
    agent = hybrid_agent.HybridAgent(enable_slow_path=False)
    res = agent.answer_query("Doanh thu bao nhiêu?", _make_ctx("priv_gate_noop", {}), as_of_date="2026-06-01")
    assert res[:2] == ("abstain", None)
