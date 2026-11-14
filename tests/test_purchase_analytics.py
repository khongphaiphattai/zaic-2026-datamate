"""TDD tests for P1.8 schema-general purchase analytics."""

from pathlib import Path
import sys

import duckdb
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from zaic_2026_datamate.data_loader import BusinessContext, DataLoader
from zaic_2026_datamate.intent_parser import resolve_query
from zaic_2026_datamate.purchase_analytics import resolve_purchase_query

TRAIN_DATA_DIR = (
    Path(__file__).resolve().parents[1] / "data" / "train" / "train" / "data"
)


def _make_ctx(
    tables: dict[str, pd.DataFrame], business_id: str = "test_biz"
) -> BusinessContext:
    conn = duckdb.connect()
    for name, df in tables.items():
        conn.register(name, df)
    return BusinessContext(
        business_id=business_id,
        data_dir=Path("."),
        tables=tables,
        duckdb_conn=conn,
    )


def _calendar_df(start: str = "2025-01-01", end: str = "2026-08-31") -> pd.DataFrame:
    dates = pd.date_range(start, end, freq="D")
    return pd.DataFrame({"date": dates.strftime("%Y-%m-%d")})


def test_relational_prefixed_purchase_weighted_avg_mtd_and_status() -> None:
    ctx = _make_ctx(
        {
            "calendar": _calendar_df(),
            "du_lieu_ban_hang_danh_sach_san_pham": pd.DataFrame(
                [
                    {
                        "ma_hang": "SP001",
                        "ten_hang": "Trà xanh 0 độ 455ml",
                        "gia_von": 150000,
                    },
                    {
                        "ma_hang": "SP002",
                        "ten_hang": "Nước suối 1.5L",
                        "gia_von": 80000,
                    },
                ]
            ),
            "du_lieu_ban_hang_bao_cao_ton_kho": pd.DataFrame(
                [
                    {
                        "ma_hang": "SP001",
                        "ten_hang": "Trà xanh 0 độ 455 ml",
                        "dvt": "Thùng",
                    },
                    {"ma_hang": "SP002", "ten_hang": "Nước suối 1.5L", "dvt": "Thùng"},
                ]
            ),
            "du_lieu_ban_hang_danh_sach_nhap_hang": pd.DataFrame(
                [
                    {
                        "ma_nhap_hang": "NH1",
                        "thoi_gian": "2025-03-05",
                        "ma_ncc": "NCC1",
                        "can_tra_ncc": 1000000,
                        "trang_thai": "Đã nhập hàng",
                    },
                    {
                        "ma_nhap_hang": "NH2",
                        "thoi_gian": "2025-03-10",
                        "ma_ncc": "NCC2",
                        "can_tra_ncc": 2600000,
                        "trang_thai": "Đã nhập hàng",
                    },
                    {
                        "ma_nhap_hang": "NH3",
                        "thoi_gian": "2025-03-12",
                        "ma_ncc": "NCC3",
                        "can_tra_ncc": 9999999,
                        "trang_thai": "Đã hủy",
                    },
                    {
                        "ma_nhap_hang": "NH4",
                        "thoi_gian": "2025-03-20",
                        "ma_ncc": "NCC1",
                        "can_tra_ncc": 500000,
                        "trang_thai": "Đã nhập hàng",
                    },
                ]
            ),
            "du_lieu_ban_hang_danh_sach_chi_tiet_nhap_hang": pd.DataFrame(
                [
                    {
                        "ma_nhap_hang": "NH1",
                        "ma_hang": "SP001",
                        "dvt": "Thùng",
                        "so_luong": 10,
                        "don_gia": 100000,
                    },
                    {
                        "ma_nhap_hang": "NH2",
                        "ma_hang": "SP001",
                        "dvt": "Thùng",
                        "so_luong": 20,
                        "don_gia": 130000,
                    },
                    {
                        "ma_nhap_hang": "NH3",
                        "ma_hang": "SP001",
                        "dvt": "Thùng",
                        "so_luong": 50,
                        "don_gia": 199999,
                    },
                    {
                        "ma_nhap_hang": "NH4",
                        "ma_hang": "SP002",
                        "dvt": "Thùng",
                        "so_luong": 5,
                        "don_gia": 100000,
                    },
                ]
            ),
        }
    )
    # Weighted avg for SP001 in March 2025 (excludes cancelled NH3): (10*100k + 20*130k)/30 = 120000 (simple mean would be 115000)
    ans_type, ans_val, _ = resolve_query(
        "Tháng 3/2025 cửa hàng nhập Trà xanh 0 độ 455ml bình quân bao nhiêu tiền một thùng?",
        ctx,
        as_of_date="2025-04-30",
    )
    assert (ans_type, ans_val) == ("number", 120000)

    # Total purchase spend MTD as_of_date=2025-03-15 excludes 2025-03-20 (500k) and cancelled NH3 -> 3,600,000
    ans_type2, ans_val2, _ = resolve_query(
        "tháng này tổng giá trị các phiếu nhập hàng là bao nhiu",
        ctx,
        as_of_date="2025-03-15",
    )
    assert (ans_type2, ans_val2) == ("number", 3600000)

    # Explicit full month includes 2025-03-20 -> 4,100,000
    ans_type3, ans_val3, _ = resolve_query(
        "tong tien hang nhap vao thang 3/2025 la bao nhieu ta",
        ctx,
        as_of_date="2025-04-30",
    )
    assert (ans_type3, ans_val3) == ("number", 4100000)

    # Dialect phrasing "thang 3/2025 nhap hang het bao nhiu tien" and "toi nay"
    assert resolve_query(
        "thang 3/2025 nhap hang het bao nhiu tien",
        ctx,
        as_of_date="2025-04-30",
    )[:2] == ("number", 4100000)
    assert resolve_query(
        "nhap hang toi nay het bnhiu",
        ctx,
        as_of_date="2025-03-15",
    )[:2] == ("number", 3600000)

    # Denormalized flat detail table with repeated ticket total `tong_tien_phieu`
    ctx_denorm = _make_ctx(
        {
            "calendar": _calendar_df(),
            "chi_tiet_nhap_hang": pd.DataFrame(
                [
                    {
                        "ma_phieu_nhap": "PN1",
                        "ngay_nhap": "2025-04-02",
                        "tong_tien_phieu": "500.000đ",
                        "sl_nhap": 2,
                        "gia_nhap": 100000,
                    },
                    {
                        "ma_phieu_nhap": "PN1",
                        "ngay_nhap": "2025-04-02",
                        "tong_tien_phieu": "500.000đ",
                        "sl_nhap": 3,
                        "gia_nhap": 100000,
                    },
                    {
                        "ma_phieu_nhap": "PN2",
                        "ngay_nhap": "2025-04-10",
                        "tong_tien_phieu": "300k",
                        "sl_nhap": 1,
                        "gia_nhap": 300000,
                    },
                ]
            ),
        }
    )
    assert resolve_query(
        "thang 4/2025 nhap hang het bao nhieu tien",
        ctx_denorm,
        as_of_date="2025-05-01",
    )[:2] == ("number", 800000)


def test_relational_phieu_nhap_top_supplier_tie_and_missing_name_abstain() -> None:
    tables = {
        "calendar": _calendar_df(),
        "nha_cung_cap": pd.DataFrame(
            [
                {"ma_nha_cung_cap": "S1", "ten_goi": "Cty Chung Khoa"},
                {"ma_nha_cung_cap": "S2", "ten_goi": "Cty Ánh Dương"},
            ]
        ),
        "san_pham": pd.DataFrame([{"ma_hang_hoa": "M1", "ten_goi": "Hapacol 250"}]),
        "phieu_nhap": pd.DataFrame(
            [
                {
                    "ma_phieu_nhap": "PN1",
                    "ma_nha_cung_cap": "S1",
                    "ngay_nhap_kho": "2025-01-10",
                    "tinh_trang": "received",
                    "tong_gia_tri_nhap": 5000000,
                },
                {
                    "ma_phieu_nhap": "PN2",
                    "ma_nha_cung_cap": "S2",
                    "ngay_nhap_kho": "2025-01-11",
                    "tinh_trang": "received",
                    "tong_gia_tri_nhap": 2000000,
                },
                {
                    "ma_phieu_nhap": "PN3",
                    "ma_nha_cung_cap": "S1",
                    "ngay_nhap_kho": "2025-02-05",
                    "tinh_trang": "received",
                    "tong_gia_tri_nhap": 3000000,
                },
                {
                    "ma_phieu_nhap": "PN4",
                    "ma_nha_cung_cap": "S2",
                    "ngay_nhap_kho": "2025-02-06",
                    "tinh_trang": "received",
                    "tong_gia_tri_nhap": 3000000,
                },
            ]
        ),
        "chi_tiet_nhap": pd.DataFrame(
            [
                {
                    "ma_phieu_nhap": "PN1",
                    "ma_hang_hoa": "M1",
                    "don_vi_tinh": "Hộp",
                    "sl": 1,
                    "don_gia_nhap": 100000,
                },
                {
                    "ma_phieu_nhap": "PN2",
                    "ma_hang_hoa": "M1",
                    "don_vi_tinh": "Hộp",
                    "sl": 1,
                    "don_gia_nhap": 100001,
                },
            ]
        ),
    }
    ctx = _make_ctx(tables)
    assert resolve_query(
        "Tháng 1/2025 cửa hàng nhập nhiều tiền hàng nhất từ nhà cung cấp nào?",
        ctx,
        as_of_date="2025-05-01",
    )[:2] == ("string", "Cty Chung Khoa")

    # Tie in Feb 2025 -> abstain cleanly
    assert (
        resolve_query(
            "Tháng 2/2025 cửa hàng nhập nhiều tiền hàng nhất từ nhà cung cấp nào?",
            ctx,
            as_of_date="2025-05-01",
        )[0]
        == "abstain"
    )

    # Weighted avg rounds half-up: (100000 + 100001)/2 = 100000.5 -> 100001
    assert resolve_query(
        "Tháng 1/2025 giá nhập bình quân một hộp Hapacol 250 là bao nhiêu?",
        ctx,
        as_of_date="2025-05-01",
    )[:2] == ("number", 100001)

    # Without supplier lookup table, ma_nha_cung_cap code only -> abstain
    tables_no_ncc = {k: v for k, v in tables.items() if k != "nha_cung_cap"}
    assert (
        resolve_purchase_query(
            "Tháng 1/2025 cửa hàng nhập nhiều tiền hàng nhất từ nhà cung cấp nào?",
            _make_ctx(tables_no_ncc),
            as_of_date="2025-05-01",
        )[0]
        == "abstain"
    )

    # Guardrail: open-ended "tu truoc toi nay" must NOT fall back to MTD ("thang nay")
    assert (
        resolve_purchase_query(
            "Từ trước tới nay cửa hàng nhập hàng hết bao nhiêu tiền?",
            ctx,
            as_of_date="2025-02-20",
        )[0]
        == "abstain"
    )

    # Top-1 supplier on header_detail without header spend column + flat dedup ticket spend
    hd_no_header_spend = _make_ctx(
        {
            "calendar": _calendar_df(),
            "phieu_nhap": pd.DataFrame(
                [
                    {
                        "ma_phieu_nhap": "PN1",
                        "ten_ncc": "Cty A",
                        "ngay_nhap_kho": "2025-01-10",
                        "tinh_trang": "received",
                    },
                    {
                        "ma_phieu_nhap": "PN2",
                        "ten_ncc": "Cty B",
                        "ngay_nhap_kho": "2025-01-11",
                        "tinh_trang": "received",
                    },
                ]
            ),
            "chi_tiet_nhap": pd.DataFrame(
                [
                    {"ma_phieu_nhap": "PN1", "sl": 10, "don_gia_nhap": 100000},
                    {"ma_phieu_nhap": "PN2", "sl": 15, "don_gia_nhap": 100000},
                ]
            ),
        }
    )
    assert resolve_purchase_query(
        "Tháng 1/2025 nhập nhiều tiền nhất từ nhà cung cấp nào?",
        hd_no_header_spend,
        as_of_date="2025-05-01",
    )[:2] == ("string", "Cty B")

    flat_dedup_sup = _make_ctx(
        {
            "calendar": _calendar_df(),
            "chi_tiet_nhap_hang": pd.DataFrame(
                [
                    {
                        "ma_phieu_nhap": "PN1",
                        "ngay_nhap": "2025-04-02",
                        "nha_cung_cap": "Cty Alpha",
                        "tong_tien_phieu": 500000,
                        "sl_nhap": 2,
                    },
                    {
                        "ma_phieu_nhap": "PN1",
                        "ngay_nhap": "2025-04-02",
                        "nha_cung_cap": "Cty Alpha",
                        "tong_tien_phieu": 500000,
                        "sl_nhap": 3,
                    },
                    {
                        "ma_phieu_nhap": "PN2",
                        "ngay_nhap": "2025-04-03",
                        "nha_cung_cap": "Cty Beta",
                        "tong_tien_phieu": 700000,
                        "sl_nhap": 7,
                    },
                ]
            ),
        }
    )
    assert resolve_purchase_query(
        "Tháng 4/2025 cửa hàng nhập nhiều tiền hàng nhất từ nhà cung cấp nào?",
        flat_dedup_sup,
        as_of_date="2025-05-01",
    )[:2] == ("string", "Cty Beta")


def test_flat_so_nhap_hang_telex_units_and_unit_guards() -> None:
    ctx = _make_ctx(
        {
            "calendar": _calendar_df(),
            "so_tay_nhap_hang": pd.DataFrame(
                [
                    {
                        "so_phieu": "1",
                        "ngay_nhap": "2026-04-03",
                        "nha_cung_cap": "Cty Thân Ngọc",
                        "product": "Bộ van dây gas Namilux",
                        "dvt_nhap": "Bộ",
                        "sl_nhap": 3,
                        "gia_nhap": 200000,
                        "thanh_tien": 600000,
                    },
                    {
                        "so_phieu": "2",
                        "ngay_nhap": "2026-04-10",
                        "nha_cung_cap": "Cty B",
                        "product": "Bộ van dây gas Namilux",
                        "dvt_nhap": "Bộ",
                        "sl_nhap": 7,
                        "gia_nhap": 205000,
                        "thanh_tien": 1435000,
                    },
                    {
                        "so_phieu": "3",
                        "ngay_nhap": "2026-01-05",
                        "nha_cung_cap": "Cty Thân Ngọc",
                        "product": "Cát xây tô",
                        "dvt_nhap": "Xe 4m3",
                        "sl_nhap": 2,
                        "gia_nhap": 980000,
                        "thanh_tien": 1960000,
                    },
                    {
                        "so_phieu": "4",
                        "ngay_nhap": "2026-01-12",
                        "nha_cung_cap": "Cty Thân Ngọc",
                        "product": "Cát xây tô",
                        "dvt_nhap": "Xe 4m3",
                        "sl_nhap": 2,
                        "gia_nhap": 1000000,
                        "thanh_tien": 2000000,
                    },
                    {
                        "so_phieu": "5",
                        "ngay_nhap": "2026-05-02",
                        "nha_cung_cap": "Cty Thân Ngọc",
                        "product": "Acemuc kids 200mg",
                        "dvt_nhap": "Hộp",
                        "sl_nhap": 4,
                        "gia_nhap": 70000,
                        "thanh_tien": 280000,
                    },
                    {
                        "so_phieu": "6",
                        "ngay_nhap": "2026-06-01",
                        "nha_cung_cap": "Cty B",
                        "product": "Thuốc đa đơn vị",
                        "dvt_nhap": "Hộp",
                        "sl_nhap": 2,
                        "gia_nhap": 100000,
                        "thanh_tien": 200000,
                    },
                    {
                        "so_phieu": "7",
                        "ngay_nhap": "2026-06-02",
                        "nha_cung_cap": "Cty B",
                        "product": "Thuốc đa đơn vị",
                        "dvt_nhap": "Vỉ",
                        "sl_nhap": 10,
                        "gia_nhap": 12000,
                        "thanh_tien": 120000,
                    },
                ]
            ),
        }
    )
    # Telex supplier query
    assert resolve_query(
        "coi giùm tháng 5/2026 cuwar hàng nhập nhiều tiền hàng nhất từ nhaf cung cấp nào",
        ctx,
        as_of_date="2026-06-30",
    )[:2] == ("string", "Cty Thân Ngọc")

    # Unit starts product phrase: "mot Bo van day gas Namilux" -> 2035000 / 10 = 203500
    assert resolve_query(
        "Tháng 4/2026 giá nhập bình quân một Bộ van dây gas Namilux là bao nhiêu?",
        ctx,
        as_of_date="2026-06-30",
    )[:2] == ("number", 203500)

    # Composite unit "xe 4m3"
    assert resolve_query(
        "gia nhap binh quan mot xe 4m3 cat xay to trong thang 1 nam 2026 la bn",
        ctx,
        as_of_date="2026-06-30",
    )[:2] == ("number", 990000)

    # Telex "tổng tieenf nhập hàng"
    assert resolve_query(
        "tổng tieenf nhập hàng tháng 5/2026 laf bnhiu",
        ctx,
        as_of_date="2026-06-30",
    )[:2] == ("number", 280000)

    # Explicit unit conflict ("một viên" vs table dvt_nhap "Hộp") -> abstain
    assert (
        resolve_purchase_query(
            "Tháng 5/2026 giá nhập bình quân một viên Acemuc kids 200mg là bao nhiêu?",
            ctx,
            as_of_date="2026-06-30",
        )[0]
        == "abstain"
    )

    # Mixed units without unambiguous filter -> abstain
    assert (
        resolve_purchase_query(
            "Tháng 6/2026 giá nhập bình quân một gói Thuốc đa đơn vị là bao nhiêu?",
            ctx,
            as_of_date="2026-06-30",
        )[0]
        == "abstain"
    )


def test_purchase_gate1_unanswerable_and_unknown_schema_abstain() -> None:
    # Case A: No purchase tables at all (even though catalog has static gia_von)
    ctx_no_purchase = _make_ctx(
        {
            "calendar": _calendar_df(),
            "danh_sach_san_pham": pd.DataFrame(
                [{"ma_hang": "B1", "ten_hang": "Bánh tiramisu", "gia_von": 50000}]
            ),
            "don_hang_chi_tiet": pd.DataFrame(
                [
                    {
                        "ma_don_hang": "D1",
                        "ngay_dat_hang": "2026-04-01",
                        "ten_san_pham": "Bánh tiramisu",
                        "tong_tien": 90000,
                    }
                ]
            ),
        }
    )
    assert resolve_query(
        "Cho hỏi tổng tiền nhập hàng tháng này là bao nhiêu?",
        ctx_no_purchase,
        as_of_date="2026-04-20",
    )[:2] == ("unanswerable", None)
    assert resolve_query(
        "Tháng 4/2026 cửa hàng nhập Bánh tiramisu bình quân bao nhiêu tiền một hộp vậy?",
        ctx_no_purchase,
        as_of_date="2026-04-20",
    )[:2] == ("unanswerable", None)

    # Case B: Purchase header+detail exist, but only quantities (zero price/spend columns)
    ctx_no_price = _make_ctx(
        {
            "calendar": _calendar_df(),
            "danh_sach_san_pham": pd.DataFrame(
                [{"ma_sku": "H1", "ten_san_pham": "Hapacol 150 hộp 24 gói"}]
            ),
            "don_nhap_hang": pd.DataFrame(
                [
                    {
                        "ma_don_nhap": "DN1",
                        "ngay_nhap": "2025-02-04",
                        "ma_nha_cung_cap": "NCC1",
                    }
                ]
            ),
            "don_nhap_hang_chi_tiet": pd.DataFrame(
                [
                    {
                        "ma_don_nhap": "DN1",
                        "ma_sku": "H1",
                        "ma_lo": "L1",
                        "so_luong_nhap": 10,
                    }
                ]
            ),
        }
    )
    assert resolve_query(
        "Cho hỏi tháng 2/2025 cửa hàng nhập Hapacol 150 hộp 24 gói bình quân bao nhiêu tiền một hộp?",
        ctx_no_price,
        as_of_date="2026-04-20",
    )[:2] == ("unanswerable", None)
    assert resolve_query(
        "Tổng tiền nhập hàng tháng 2/2025 là bao nhiêu?",
        ctx_no_price,
        as_of_date="2026-04-20",
    )[:2] == ("unanswerable", None)

    # Case C: Unknown purchase table with numeric cost/price column we don't map -> abstain (NEVER unanswerable)
    ctx_unmapped = _make_ctx(
        {
            "calendar": _calendar_df(),
            "nhap_hang_tu_ncc": pd.DataFrame(
                [{"ngay": "2026-03-01", "chi_phi_mua": 500000}]
            ),
        }
    )
    assert (
        resolve_purchase_query(
            "Tổng tiền nhập hàng tháng 3/2026 là bao nhiêu?",
            ctx_unmapped,
            as_of_date="2026-04-20",
        )[0]
        == "abstain"
    )


def test_purchase_guards_do_not_hijack_other_intents() -> None:
    ctx = _make_ctx(
        {
            "calendar": _calendar_df(),
            "so_nhap_hang": pd.DataFrame(
                [
                    {
                        "so_phieu": "1",
                        "ngay_nhap": "2026-03-01",
                        "nha_cung_cap": "Cty A",
                        "ten_mat_hang": "Sữa tươi",
                        "dvt": "Hộp",
                        "sl_nhap": 10,
                        "gia_nhap": 20000,
                        "thanh_tien": 200000,
                    }
                ]
            ),
        }
    )
    for q in [
        "Tháng 3/2026 số nhập trừ số bán của Sữa tươi là bao nhiêu hộp?",
        "Thu nhập tháng 3/2026 là bao nhiêu?",
        "Tổng tiền nhập hàng tháng 3/2026 so với tháng 2/2026 tăng bao nhiêu?",
        "Tháng 3/2026 còn nợ nhà cung cấp bao nhiêu tiền?",
        "Tháng 3/2026 cửa hàng nhập bao nhiêu hộp Sữa tươi?",
        "Nhà cung cấp nào đáng tin cậy nhất tháng 3/2026?",
    ]:
        ans_type, _, _ = resolve_purchase_query(q, ctx, as_of_date="2026-04-01")
        if "đáng tin cậy" in q:
            assert ans_type == "unanswerable"
        else:
            assert ans_type == "abstain", (
                f"Unexpectedly handled non-P1.8 query: {q} -> {ans_type}"
            )


@pytest.mark.slow
def test_p18_real_train_purchase_twins() -> None:
    """P1.8: verify total spend, weighted-avg unit price, Top-1 NCC, and missing-price unanswerable on real twins."""
    from zaic_2026_datamate.pipeline import build_agent, production_pipeline_config

    if not TRAIN_DATA_DIR.exists():
        pytest.skip("Train data not present")
    loader = DataLoader(TRAIN_DATA_DIR)
    agent = build_agent(production_pipeline_config())

    cases = [
        (
            "biz_103",
            "Cho hỏi tổng tiền nhập hàng tháng này là bao nhiêu?",
            "2026-07-09",
            "unanswerable",
            None,
        ),
        (
            "biz_105",
            "coi giùm tháng 5/2026 cuwar hàng nhập nhiều tiền hàng nhất từ nhaf cung cấp nào",
            "2026-06-24",
            "string",
            "Cty Thân Ngọc",
        ),
        (
            "biz_105",
            "tổng tiền hàng nhập vào tháng 3/2026 là bnhiu",
            "2026-06-24",
            "number",
            116822200,
        ),
        (
            "biz_109",
            "Tháng 3/2025 cửa hàng nhập Trà xanh 0 độ 455ml bình quân bao nhiêu tiền một thùng?",
            "2026-08-05",
            "number",
            195733,
        ),
        (
            "biz_112",
            "Tháng 4/2026 cửa hàng nhập Bánh tiramisu bình quân bao nhiêu tiền một hộp vậy?",
            "2026-07-31",
            "unanswerable",
            None,
        ),
        (
            "biz_117",
            "Tháng 4/2026 giá nhập bình quân một Bộ van dây gas Namilux là bao nhiêu?",
            "2026-05-20",
            "number",
            203470,
        ),
        (
            "biz_117",
            "gia nhap binh quan mot xe 4m3 cat xay to trong thang 1 nam 2026 la bn",
            "2026-05-20",
            "number",
            988475,
        ),
        (
            "biz_118",
            "Cho mình hỏi giá nhập bình quân một cây Thép Pomina D12 cây 11.7m trong tháng 6 năm 2025 là bao nhiêu?",
            "2026-09-10",
            "unanswerable",
            None,
        ),
        (
            "biz_119",
            "trong tháng 11/2025, acemuc kids 200mg hộp 30 gói nhập vào giá bình quân một hộp laf bao nhiêu",
            "2026-07-13",
            "number",
            71143,
        ),
        (
            "biz_119",
            "tổng tieenf nhập hàng tháng này là bnhiu",
            "2026-07-13",
            "number",
            37843200,
        ),
        (
            "biz_120",
            "thang 4/2025 nhap hang het bao nhiu tien",
            "2026-06-12",
            "number",
            220195300,
        ),
        (
            "biz_122",
            "Cho hỏi tháng 2/2025 cửa hàng nhập Hapacol 150 hộp 24 gói bình quân bao nhiêu tiền một hộp?",
            "2026-07-17",
            "unanswerable",
            None,
        ),
        (
            "biz_123",
            "Tháng 1/2025 cửa hàng nhập nhiều tiền hàng nhất từ nhà cung cấp nào?",
            "2026-06-28",
            "string",
            "Cty Chung Khoa",
        ),
        (
            "biz_124",
            "Tháng 8/2025 giá nhập bình quân một hộp Oresol 245 DHG hộp 20 gói là bao nhiêu?",
            "2026-05-25",
            "number",
            20455,
        ),
        (
            "biz_128",
            "tháng này tổng giá trị các phiếu nhập hàng là bao nhiu",
            "2026-09-07",
            "number",
            89526400,
        ),
    ]
    ctx_cache = {}
    for biz_id, q, as_of, exp_type, exp_val in cases:
        ctx = ctx_cache.setdefault(biz_id, loader.load_business(biz_id))
        ans_type, ans_val = agent.answer_query(q, ctx, as_of_date=as_of)[:2]
        assert (ans_type, ans_val) == (exp_type, exp_val), (
            f"{biz_id} | {q}: got {(ans_type, ans_val)}"
        )
