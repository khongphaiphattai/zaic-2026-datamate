from __future__ import annotations

from pathlib import Path

import pandas as pd

from zaic_2026_datamate.data_loader import BusinessContext
from zaic_2026_datamate.in_out_ledger import resolve_in_minus_out_query


def _ctx(
    tables: dict[str, pd.DataFrame], business_id: str = "synth_inout"
) -> BusinessContext:
    return BusinessContext(
        business_id=business_id, data_dir=Path("/tmp"), tables=tables
    )


def test_pharmacy_header_detail_in_minus_out_positive_negative_and_sub_unit() -> None:
    """Verify net in-minus-out calculation with unit conversions, cancelled/returned exclusions, and negative diff."""
    san_pham = pd.DataFrame(
        [
            {
                "ma_hang_hoa": "SP01",
                "ten_goi": "Salonpas cao dán hộp 24 gói",
                "don_vi_co_so": "gói",
            },
            {
                "ma_hang_hoa": "SP02",
                "ten_goi": "Telfast HD 180mg hộp 3 vỉ x 10 viên",
                "don_vi_co_so": "viên",
            },
        ]
    )
    don_vi_sp = pd.DataFrame(
        [
            {"ma_hang_hoa": "SP01", "don_vi_tinh": "hộp", "he_so_quy_doi": 24},
            {"ma_hang_hoa": "SP02", "don_vi_tinh": "hộp", "he_so_quy_doi": 30},
            {"ma_hang_hoa": "SP02", "don_vi_tinh": "vỉ", "he_so_quy_doi": 10},
        ]
    )
    phieu_nhap = pd.DataFrame(
        [
            {
                "ma_phieu_nhap": "PN01",
                "ngay_nhap_kho": pd.Timestamp("2026-01-05"),
                "tinh_trang": "Đã nhập hàng",
            },
            {
                "ma_phieu_nhap": "PN02",
                "ngay_nhap_kho": pd.Timestamp("2026-01-12"),
                "tinh_trang": "cancelled",
            },
            {
                "ma_phieu_nhap": "PN04",
                "ngay_nhap_kho": pd.Timestamp("2026-01-14"),
                "tinh_trang": "Nháp",
            },
            {
                "ma_phieu_nhap": "PN03",
                "ngay_nhap_kho": pd.Timestamp("2025-03-10"),
                "tinh_trang": "received",
            },
        ]
    )
    chi_tiet_nhap = pd.DataFrame(
        [
            # 2 hộp = 48 gói in 2026-01 ("Đã nhập hàng" MUST count, never confused with "Nháp")
            {
                "ma_phieu_nhap": "PN01",
                "ma_hang_hoa": "SP01",
                "don_vi_tinh": "hộp",
                "sl": 2,
            },
            # Cancelled & draft receipts must be ignored
            {
                "ma_phieu_nhap": "PN02",
                "ma_hang_hoa": "SP01",
                "don_vi_tinh": "hộp",
                "sl": 5,
            },
            {
                "ma_phieu_nhap": "PN04",
                "ma_hang_hoa": "SP01",
                "don_vi_tinh": "hộp",
                "sl": 3,
            },
            # 1 hộp = 30 viên in 2025-03
            {
                "ma_phieu_nhap": "PN03",
                "ma_hang_hoa": "SP02",
                "don_vi_tinh": "hộp",
                "sl": 1,
            },
        ]
    )
    don_hang = pd.DataFrame(
        [
            {
                "ma_hoa_don": "H01",
                "ngay_lap": pd.Timestamp("2026-01-20"),
                "tinh_trang": "completed",
            },
            {
                "ma_hoa_don": "H02",
                "ngay_lap": pd.Timestamp("2025-03-12"),
                "tinh_trang": "completed",
            },
            {
                "ma_hoa_don": "H03",
                "ngay_lap": pd.Timestamp("2025-03-15"),
                "tinh_trang": "returned",
            },
            {
                "ma_hoa_don": "H04",
                "ngay_lap": pd.Timestamp("2025-03-18"),
                "tinh_trang": "cancelled",
            },
        ]
    )
    chi_tiet_don = pd.DataFrame(
        [
            # 1 hộp + 14 gói = 38 gói sold -> 48 - 38 = 10 gói
            {
                "ma_hoa_don": "H01",
                "ma_hang_hoa": "SP01",
                "don_vi_tinh": "hộp",
                "sl": 1,
                "sl_quy_doi": 24,
            },
            {
                "ma_hoa_don": "H01",
                "ma_hang_hoa": "SP01",
                "don_vi_tinh": "gói",
                "sl": 14,
                "sl_quy_doi": 14,
            },
            # 50 viên completed sold; returned (5) and cancelled (20) excluded -> 30 - 50 = -20 viên (-2 vỉ)
            {
                "ma_hoa_don": "H02",
                "ma_hang_hoa": "SP02",
                "don_vi_tinh": "viên",
                "sl": 50,
                "sl_quy_doi": 50,
            },
            {
                "ma_hoa_don": "H03",
                "ma_hang_hoa": "SP02",
                "don_vi_tinh": "viên",
                "sl": 5,
                "sl_quy_doi": 5,
            },
            {
                "ma_hoa_don": "H04",
                "ma_hang_hoa": "SP02",
                "don_vi_tinh": "viên",
                "sl": 20,
                "sl_quy_doi": 20,
            },
        ]
    )
    ctx = _ctx(
        {
            "san_pham": san_pham,
            "don_vi_sp": don_vi_sp,
            "phieu_nhap": phieu_nhap,
            "chi_tiet_nhap": chi_tiet_nhap,
            "don_hang": don_hang,
            "chi_tiet_don": chi_tiet_don,
        }
    )

    t, v, _ = resolve_in_minus_out_query(
        "lay so nhap tru so ban cua salonpas cao dan hop 24 goi trong thang 1/2026 thi dc bnhiu goi",
        ctx,
        "2026-06-28",
    )
    assert (t, v) == ("number", 10)

    t, v, _ = resolve_in_minus_out_query(
        "Tháng 3/2025, số nhập trừ số bán của Telfast HD 180mg hộp 3 vỉ x 10 viên là bao nhiêu viên?",
        ctx,
        "2026-06-28",
    )
    assert (t, v) == ("number", -20)

    # Non-base target unit ("vỉ" = 10 viên -> -20 / 10 = -2)
    t, v, _ = resolve_in_minus_out_query(
        "Tháng 3/2025, số nhập trừ số bán của Telfast HD 180mg hộp 3 vỉ x 10 viên là bao nhiêu vỉ?",
        ctx,
        "2026-06-28",
    )
    assert (t, v) == ("number", -2)


def test_combined_import_notebook_and_cashbook_decoy_sales_schema() -> None:
    """Verify combined `so_tay_nhap_hang` import table + decoy `du_lieu_ban_hang` guard and `Đổi trả` exclusion."""
    ban_hang_hang_hoa = pd.DataFrame(
        [{"ma_sp": "SP12", "mon": "Smecta hộp 30 gói", "don_vi": "gói"}]
    )
    ban_hang_don_vi_quy_doi = pd.DataFrame(
        [{"ma_sp": "SP12", "don_vi": "hộp", "bang": 30}]
    )
    so_tay_nhap_hang = pd.DataFrame(
        [
            {
                "so_phieu": "PN1",
                "ngay_nhap": pd.Timestamp("2026-04-02"),
                "product": "Smecta hộp 30 gói",
                "dvt_nhap": "hộp",
                "sl_nhap": 2,
            },
            {
                "so_phieu": "PN2",
                "ngay_nhap": pd.Timestamp("2026-04-15"),
                "product": "Smecta hộp 30 gói",
                "dvt_nhap": "gói",
                "sl_nhap": 11,
            },
        ]
    )
    # Decoy cashbook mirror table with `ma_phieu`, `thoi_gian`, `ma_hoa_don`, `gia_tri` (must NOT pre-empt order header!)
    du_lieu_ban_hang = pd.DataFrame(
        [
            {
                "ma_phieu": "PAY1",
                "thoi_gian": pd.Timestamp("2026-04-05"),
                "ma_hoa_don": "ORD1",
                "gia_tri": 100000,
            },
            {
                "ma_phieu": "PAY2",
                "thoi_gian": pd.Timestamp("2026-04-06"),
                "ma_hoa_don": "ORD2",
                "gia_tri": 50000,
            },
        ]
    )
    ban_hang_hoa_don = pd.DataFrame(
        [
            {
                "order_id": "ORD1",
                "ngay_ban": pd.Timestamp("2026-04-05"),
                "trang_thai": "Đã giao",
            },
            {
                "order_id": "ORD2",
                "ngay_ban": pd.Timestamp("2026-04-06"),
                "trang_thai": "Đổi trả",
            },
        ]
    )
    ban_hang_chi_tiet_hoa_don = pd.DataFrame(
        [
            {
                "ma_giao_dich": "ORD1",
                "ma_hang": "SP12",
                "d": "gói",
                "slg": 30,
                "sl_co_ban": 30,
            },
            {
                "ma_giao_dich": "ORD2",
                "ma_hang": "SP12",
                "d": "gói",
                "slg": 10,
                "sl_co_ban": 10,
            },
        ]
    )
    ctx = _ctx(
        {
            "ban_hang_hang_hoa": ban_hang_hang_hoa,
            "ban_hang_don_vi_quy_doi": ban_hang_don_vi_quy_doi,
            "so_tay_nhap_hang": so_tay_nhap_hang,
            "du_lieu_ban_hang": du_lieu_ban_hang,
            "ban_hang_hoa_don": ban_hang_hoa_don,
            "ban_hang_chi_tiet_hoa_don": ban_hang_chi_tiet_hoa_don,
        }
    )

    # Imports = 2*30 + 11 = 71 gói; Valid sales (`Đã giao`) = 30 gói -> 71 - 30 = 41
    t, v, _ = resolve_in_minus_out_query(
        "Chênh lệch nhập trừ bán của Smecta hộp 30 gói trong tháng 4 năm 2026 là bao nhiêu gói?",
        ctx,
        "2026-05-25",
    )
    assert (t, v) == ("number", 41)


def test_in_minus_out_unanswerable_and_abstain_guards() -> None:
    """Verify unanswerable on missing import ledger or future month, and abstain on regular item qty / unknown units."""
    no_import_ctx = _ctx(
        {
            "danh_sach_san_pham": pd.DataFrame(
                [{"ma_hang": "SP1", "ten_hang": "Bánh", "dvt": "cái"}]
            ),
            "danh_sach_hoa_don": pd.DataFrame(
                [
                    {
                        "ma_hoa_don": "H1",
                        "thoi_gian": pd.Timestamp("2026-05-01"),
                        "trang_thai": "xong",
                    }
                ]
            ),
            "danh_sach_hoa_don_chi_tiet": pd.DataFrame(
                [{"ma_hoa_don": "H1", "ma_hang": "SP1", "so_luong_quy_doi": 5}]
            ),
        }
    )
    t, v, _ = resolve_in_minus_out_query(
        "Lấy số nhập trừ số bán của Bánh trong tháng 5/2026 là bao nhiêu cái?",
        no_import_ctx,
        "2026-05-25",
    )
    assert (t, v) == ("unanswerable", None)

    has_import_ctx = _ctx(
        {
            "danh_sach_san_pham": pd.DataFrame(
                [{"ma_hang": "SP1", "ten_hang": "Bánh", "dvt": "cái"}]
            ),
            "danh_sach_nhap_hang": pd.DataFrame(
                [
                    {
                        "ma_nhap_hang": "PN1",
                        "thoi_gian": pd.Timestamp("2026-05-01"),
                        "trang_thai": "Đã nhập hàng",
                    }
                ]
            ),
            "danh_sach_chi_tiet_nhap_hang": pd.DataFrame(
                [
                    {
                        "ma_nhap_hang": "PN1",
                        "ma_hang": "SP1",
                        "dvt": "cái",
                        "so_luong": 10,
                    }
                ]
            ),
            "danh_sach_hoa_don": pd.DataFrame(
                [
                    {
                        "ma_hoa_don": "H1",
                        "thoi_gian": pd.Timestamp("2026-05-02"),
                        "trang_thai": "xong",
                    }
                ]
            ),
            "danh_sach_hoa_don_chi_tiet": pd.DataFrame(
                [{"ma_hoa_don": "H1", "ma_hang": "SP1", "so_luong_quy_doi": 4}]
            ),
        }
    )
    # Future month strictly beyond as_of_date -> unanswerable
    t, v, _ = resolve_in_minus_out_query(
        "Lấy số nhập trừ số bán của Bánh trong tháng 7/2026 là bao nhiêu cái?",
        has_import_ctx,
        "2026-05-25",
    )
    assert (t, v) == ("unanswerable", None)

    # Regular sales quantity question must NOT trigger in-minus-out
    t, v, _ = resolve_in_minus_out_query(
        "Tháng 5/2026 bán được bao nhiêu cái Bánh?",
        has_import_ctx,
        "2026-05-25",
    )
    assert (t, v) == ("abstain", None)

    # Unknown target unit abstains cleanly
    t, v, _ = resolve_in_minus_out_query(
        "Lấy số nhập trừ số bán của Bánh trong tháng 5/2026 là bao nhiêu thùng?",
        has_import_ctx,
        "2026-05-25",
    )
    assert (t, v) == ("abstain", None)
