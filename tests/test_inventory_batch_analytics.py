from __future__ import annotations

from pathlib import Path

import pandas as pd

from zaic_2026_datamate.data_loader import BusinessContext
from zaic_2026_datamate.inventory_batch_analytics import resolve_inventory_batch_query


def _ctx(
    tables: dict[str, pd.DataFrame], business_id: str = "synth_inv"
) -> BusinessContext:
    return BusinessContext(
        business_id=business_id, data_dir=Path("/tmp"), tables=tables
    )


def test_pharmacy_the_kho_and_batch_hsd_analytics() -> None:
    """Verify inventory cost via the_kho + all 4 batch/HSD intents on lot-tracked pharmacy schema."""
    san_pham = pd.DataFrame(
        [
            {
                "ma_hang_hoa": "SP01",
                "ten_goi": "Siro A 100ml",
                "don_vi_co_so": "chai",
                "gia_von_hang": 40000,
            },
            {
                "ma_hang_hoa": "SP02",
                "ten_goi": "Viên B hộp 30 viên",
                "don_vi_co_so": "viên",
                "gia_von_hang": 1500,
            },
            {
                "ma_hang_hoa": "SP03",
                "ten_goi": "Kem C tuýp 5g",
                "don_vi_co_so": "tuýp",
                "gia_von_hang": 25000,
            },
        ]
    )
    # SP01: the_kho at 2026-06-28 has 10 (entry after cutoff excluded!)
    the_kho = pd.DataFrame(
        [
            {
                "ma_giao_dich_kho": "MV01",
                "ma_hang_hoa": "SP01",
                "ngay_gio_phat_sinh": pd.Timestamp("2026-06-01 08:00:00"),
                "ton_cuoi": 12,
            },
            {
                "ma_giao_dich_kho": "MV02",
                "ma_hang_hoa": "SP01",
                "ngay_gio_phat_sinh": pd.Timestamp("2026-06-28 20:00:00"),
                "ton_cuoi": 10,
            },
            {
                "ma_giao_dich_kho": "MV03",
                "ma_hang_hoa": "SP01",
                "ngay_gio_phat_sinh": pd.Timestamp("2026-06-29 09:00:00"),
                "ton_cuoi": 8,
            },
            {
                "ma_giao_dich_kho": "MV04",
                "ma_hang_hoa": "SP02",
                "ngay_gio_phat_sinh": pd.Timestamp("2026-06-15 10:00:00"),
                "ton_cuoi": 20,
            },
            {
                "ma_giao_dich_kho": "MV05",
                "ma_hang_hoa": "SP03",
                "ngay_gio_phat_sinh": pd.Timestamp("2026-06-20 11:00:00"),
                "ton_cuoi": 4,
            },
        ]
    )
    phieu_nhap = pd.DataFrame(
        [
            {
                "ma_phieu_nhap": "PN01",
                "ngay_nhap_kho": pd.Timestamp("2026-05-01"),
                "tinh_trang": "received",
            },
            {
                "ma_phieu_nhap": "PN02",
                "ngay_nhap_kho": pd.Timestamp("2026-06-29"),
                "tinh_trang": "received",
            },
        ]
    )
    lo_hang = pd.DataFrame(
        [
            # Sold out completely -> must NOT count or show up!
            {
                "ma_hang_hoa": "SP01",
                "ma_lo": "L_ZERO",
                "ma_phieu_nhap": "PN01",
                "han_su_dung": pd.Timestamp("2026-07-10"),
                "sl_nhap_quy_doi": 5,
                "don_gia_nhap": 39000,
            },
            # Active, expires in [2026-06-28, 2026-09-26]: rem = 10 - (3 - 1) = 8, cap = 8 * 41000 = 328000
            {
                "ma_hang_hoa": "SP01",
                "ma_lo": "L_A1",
                "ma_phieu_nhap": "PN01",
                "han_su_dung": pd.Timestamp("2026-08-20"),
                "sl_nhap_quy_doi": 10,
                "don_gia_nhap": 41000,
            },
            # Active, later HSD outside 90d window
            {
                "ma_hang_hoa": "SP01",
                "ma_lo": "L_A2",
                "ma_phieu_nhap": "PN01",
                "han_su_dung": pd.Timestamp("2027-01-15"),
                "sl_nhap_quy_doi": 2,
                "don_gia_nhap": 42000,
            },
            # Active, expires in [2026-06-28, 2026-09-26]: rem = 25 - 5 = 20, cap = 20 * 1400 = 28000 (sooner HSD -> 1st in list? Wait: 2026-08-15 < 2026-08-20!)
            {
                "ma_hang_hoa": "SP02",
                "ma_lo": "L_B1",
                "ma_phieu_nhap": "PN01",
                "han_su_dung": pd.Timestamp("2026-08-15"),
                "sl_nhap_quy_doi": 25,
                "don_gia_nhap": 1400,
            },
            # Active, imported AFTER cutoff 2026-06-28 -> excluded at as_of=2026-06-28!
            {
                "ma_hang_hoa": "SP02",
                "ma_lo": "L_B_FUT",
                "ma_phieu_nhap": "PN02",
                "han_su_dung": pd.Timestamp("2026-07-05"),
                "sl_nhap_quy_doi": 50,
                "don_gia_nhap": 1400,
            },
            # Active, expires 2026-10-10 -> outside 90d window, INSIDE 120d window (up to 2026-10-26)
            {
                "ma_hang_hoa": "SP03",
                "ma_lo": "L_C1",
                "ma_phieu_nhap": "PN01",
                "han_su_dung": pd.Timestamp("2026-10-10"),
                "sl_nhap_quy_doi": 4,
                "don_gia_nhap": 24000,
            },
        ]
    )
    don_hang = pd.DataFrame(
        [
            {
                "ma_hoa_don": "H01",
                "ngay_lap": pd.Timestamp("2026-06-10"),
                "tinh_trang": "completed",
            },
            {
                "ma_hoa_don": "H02",
                "ngay_lap": pd.Timestamp("2026-06-12"),
                "tinh_trang": "returned",
            },
            {
                "ma_hoa_don": "H03",
                "ngay_lap": pd.Timestamp("2026-06-14"),
                "tinh_trang": "cancelled",
            },
        ]
    )
    chi_tiet_don_lo = pd.DataFrame(
        [
            {
                "ma_hoa_don": "H01",
                "ma_lo": "L_ZERO",
                "sl_quy_doi": 5,
                "sl_tra_quy_doi": 0,
            },
            {
                "ma_hoa_don": "H01",
                "ma_lo": "L_A1",
                "sl_quy_doi": 3,
                "sl_tra_quy_doi": 0,
            },
            # Return of 1 unit adds back 1 unit to L_A1 -> rem = 10 - (3 - 1) = 8
            {
                "ma_hoa_don": "H02",
                "ma_lo": "L_A1",
                "sl_quy_doi": 0,
                "sl_tra_quy_doi": 1,
            },
            {
                "ma_hoa_don": "H01",
                "ma_lo": "L_B1",
                "sl_quy_doi": 5,
                "sl_tra_quy_doi": 0,
            },
            # Cancelled sale must NOT reduce lot stock
            {
                "ma_hoa_don": "H03",
                "ma_lo": "L_C1",
                "sl_quy_doi": 4,
                "sl_tra_quy_doi": 0,
            },
        ]
    )
    ctx = _ctx(
        {
            "san_pham": san_pham,
            "the_kho": the_kho,
            "phieu_nhap": phieu_nhap,
            "lo_hang": lo_hang,
            "don_hang": don_hang,
            "chi_tiet_don_lo": chi_tiet_don_lo,
        }
    )

    # 1. Inventory cost: 10*40000 + 20*1500 + 4*25000 = 400000 + 30000 + 100000 = 530000
    t, v, _ = resolve_inventory_batch_query(
        "Hết ngày 28/06/2026, số hàng còn trong kho đáng bao nhiêu tiền, tính theo giá vốn?",
        ctx,
        "2026-06-28",
    )
    assert (t, v) == ("number", 530000)

    # 2. Nearest-expiring batch date for Siro A (L_ZERO is depleted, so nearest active is 2026-08-20)
    t, v, _ = resolve_inventory_batch_query(
        "Lô Siro A 100ml gần hết hạn nhất có hạn dùng ngày nào?",
        ctx,
        "2026-06-28",
    )
    assert (t, v) == ("date", "2026-08-20")

    # 3. Batch capital in range [2026-06-28, 2026-09-26]: 8*41000 + 20*1400 = 328000 + 28000 = 356000
    t, v, _ = resolve_inventory_batch_query(
        "tiền vốn đang nằm ở các lô còn tồn có hạn dùng từ 28/06/2026 đến hết 26/09/2026 là bnhiu vậy",
        ctx,
        "2026-06-28",
    )
    assert (t, v) == ("number", 356000)

    # 4. Count of distinct items & count of batches expiring in range
    t, v, _ = resolve_inventory_batch_query(
        "tinh den het ngay 28/06/2026, co may mat hang con lo ton sap het han trong 90 ngay toi, tuc han dung tu 28/06/2026 den het 26/09/2026",
        ctx,
        "2026-06-28",
    )
    assert (t, v) == ("number", 2)

    t, v, _ = resolve_inventory_batch_query(
        "Theo tồn kho ngày 28/06/2026, có mấy lô hàng còn tồn sẽ hết hạn từ nay đến hết ngày 26/09/2026?",
        ctx,
        "2026-06-28",
    )
    assert (t, v) == ("number", 2)

    # 5. List of products expiring in 120 days sorted by HSD ASC
    t, v, _ = resolve_inventory_batch_query(
        "hàng nào hết hạn trong 120 ngày tới, từ 28/06/2026 đến hết 26/10/2026, mà hiện vẫn còn tồn kho",
        ctx,
        "2026-06-28",
    )
    assert (t, v) == ("list", ["Viên B hộp 30 viên", "Siro A 100ml", "Kem C tuýp 5g"])


def test_snapshot_inventory_prefixed_batch_and_box_even_odd() -> None:
    """Verify prefixed snapshot schema (ban_hang_lo_hang + ton_kho + so_tay_nhap_hang) and box/piece split."""
    ton_kho = pd.DataFrame(
        [
            {
                "id_sp": "SP1",
                "hang_hoa": "Siro Ích Nhi 100ml",
                "don_vi": "chai",
                "ton_kho": 10,
            },
            {
                "id_sp": "SP2",
                "hang_hoa": "Bánh croissant",
                "don_vi": "cái",
                "ton_kho": 80,
            },
        ]
    )
    ban_hang_hang_hoa = pd.DataFrame(
        [
            {
                "ma_sp": "SP1",
                "mon": "Siro Ích Nhi 100ml",
                "don_vi": "chai",
                "gia_von_hien_tai": 40000,
            },
            {
                "ma_sp": "SP2",
                "mon": "Bánh croissant",
                "don_vi": "cái",
                "gia_von_hien_tai": 15000,
            },
        ]
    )
    ban_hang_don_vi_quy_doi = pd.DataFrame(
        [
            {"ma_sp": "SP2", "don_vi": "hộp", "bang": 6},
        ]
    )
    ban_hang_lo_hang = pd.DataFrame(
        [
            {
                "ma_mon": "SP1",
                "lo": "LO01",
                "ma_phieu_nhap": "PN1",
                "hsd": "06/08/2028",
                "sl_nhap_dvt_co_ban": 15,
            },
            {
                "ma_mon": "SP1",
                "lo": "LO02",
                "ma_phieu_nhap": "PN2",
                "hsd": "12/09/2028",
                "sl_nhap_dvt_co_ban": 5,
            },
        ]
    )
    so_tay_nhap_hang = pd.DataFrame(
        [
            {
                "so_phieu": "PN1",
                "lo": "LO01",
                "ngay_nhap": pd.Timestamp("2026-01-10"),
                "thanh_tien": 525000.0,
            },
            {
                "so_phieu": "PN2",
                "lo": "LO02",
                "ngay_nhap": pd.Timestamp("2026-02-10"),
                "thanh_tien": 180000.0,
            },
        ]
    )
    ban_hang_hoa_don = pd.DataFrame(
        [
            {
                "order_id": "O1",
                "ngay_ban": pd.Timestamp("2026-05-25"),
                "trang_thai": "Đã giao",
            },
            {
                "order_id": "O2",
                "ngay_ban": pd.Timestamp("2026-05-25"),
                "trang_thai": "Đổi trả",
            },
        ]
    )
    ban_hang_ban_theo_lo = pd.DataFrame(
        [
            {
                "ma_hoa_don": "O1",
                "lo": "LO01",
                "sl_quy_ra_le": 10,
                "sl_tra_dvt_co_ban": 0,
            },
            # Returning 1 unit leaves LO01 = 6; note LO02 has 4 sold + 0 -> 1? Wait let's test both
            {
                "ma_hoa_don": "O2",
                "lo": "LO01",
                "sl_quy_ra_le": 1,
                "sl_tra_dvt_co_ban": 1,
            },
        ]
    )
    ctx = _ctx(
        {
            "ton_kho": ton_kho,
            "ban_hang_hang_hoa": ban_hang_hang_hoa,
            "ban_hang_don_vi_quy_doi": ban_hang_don_vi_quy_doi,
            "ban_hang_lo_hang": ban_hang_lo_hang,
            "so_tay_nhap_hang": so_tay_nhap_hang,
            "ban_hang_hoa_don": ban_hang_hoa_don,
            "ban_hang_ban_theo_lo": ban_hang_ban_theo_lo,
        }
    )

    # Box/even-odd: 80 // 6 = 13 boxes, 2 pieces
    t, v, _ = resolve_inventory_batch_query(
        "Cuối ngày hôm nay Bánh croissant còn mấy hộp chẵn, mấy cái lẻ?",
        ctx,
        "2026-05-25",
    )
    assert (t, v) == ("list", [13, 2])

    # Snapshot inventory cost: 10*40000 + 80*1500 = 400000 + 1200000 = 1600000
    t, v, _ = resolve_inventory_batch_query(
        "tính theo giá vốn, tiền hàng nằm trong kho vào cuối ngày 25/05/2026 là bao nhieeu",
        ctx,
        "2026-05-25",
    )
    assert (t, v) == ("number", 1600000)

    # Nearest HSD with noisy Telex/no-accent product query and DD/MM/YYYY hsd text
    t, v, _ = resolve_inventory_batch_query(
        "lo siro ich nhi 100ml gan het han nhat co han dung ngay nao",
        ctx,
        "2026-05-25",
    )
    assert (t, v) == ("date", "2028-08-06")

    # Expiring list with heets typo
    t, v, _ = resolve_inventory_batch_query(
        "nhung sp nao dang con hang co han dung tu 01/08/2028 den heets 15/09/2028",
        ctx,
        "2026-05-25",
    )
    assert (t, v) == ("list", ["Siro Ích Nhi 100ml"])


def test_unanswerable_and_abstain_guards() -> None:
    """Missing inventory/cost or batch/HSD schema emits unanswerable; historical rollback without the_kho abstains."""
    no_stock_ctx = _ctx(
        {
            "don_hang_chi_tiet": pd.DataFrame(
                [
                    {
                        "ma_don_hang": "ORD1",
                        "ngay_dat_hang": pd.Timestamp("2026-07-09"),
                        "ten_san_pham": "Coca",
                        "tong_tien": 10000,
                    }
                ]
            )
        }
    )
    t, v, _ = resolve_inventory_batch_query(
        "Cuối ngày 09/07/2026, tổng giá trị hàng còn trong kho tính theo giá vốn là bao nhiêu tiền?",
        no_stock_ctx,
        "2026-07-09",
    )
    assert (t, v) == ("unanswerable", None)

    no_batch_ctx = _ctx(
        {
            "bao_cao_ton_kho": pd.DataFrame(
                [
                    {
                        "ma_hang": "SP1",
                        "ten_hang": "Bánh",
                        "dvt": "cái",
                        "ton_cuoi_ky": 10,
                    }
                ]
            ),
            "danh_sach_san_pham": pd.DataFrame(
                [{"ma_hang": "SP1", "ten_hang": "Bánh", "dvt": "cái", "gia_von": 5000}]
            ),
            "danh_sach_hoa_don": pd.DataFrame(
                [{"ma_hoa_don": "H1", "thoi_gian": pd.Timestamp("2026-09-14")}]
            ),
        }
    )
    t, v, _ = resolve_inventory_batch_query(
        "Hàng nào hết hạn trong 120 ngày tới, từ 14/09/2026 đến hết 12/01/2027, mà hiện vẫn còn tồn kho vậy?",
        no_batch_ctx,
        "2026-09-14",
    )
    assert (t, v) == ("unanswerable", None)

    # Historical snapshot query (strictly before max_date without transaction stock card) must abstain safely
    t, v, _ = resolve_inventory_batch_query(
        "Cuối ngày 01/09/2026, tiền hàng nằm trong kho tính theo giá vốn là bao nhiêu?",
        no_batch_ctx,
        "2026-09-14",
    )
    assert (t, v) == ("abstain", None)

    # Credit due-date query with accented "hạn đúng" must NEVER trigger "hạn dùng" / batch unanswerable
    t, v, _ = resolve_inventory_batch_query(
        "Tới hôm nay, có mấy đơn bán chịu ghi ngày phải trả muộn hơn hôm nay (hạn đúng hôm nay không tính)?",
        no_batch_ctx,
        "2025-03-02",
    )
    assert (t, v) == ("abstain", None)

    # Store with stock + import prices (`don_gia`) but NO `gia_von` master column abstains (unverified twin combo)
    stock_nhap_no_cost_ctx = _ctx(
        {
            "bao_cao_ton_kho": pd.DataFrame(
                [{"ma_hang": "SP1", "ten_hang": "Gạo", "dvt": "kg", "ton_cuoi_ky": 10}]
            ),
            "danh_sach_san_pham": pd.DataFrame(
                [{"ma_hang": "SP1", "ten_hang": "Gạo", "dvt": "kg", "gia_ban": 20000}]
            ),
            "danh_sach_chi_tiet_nhap_hang": pd.DataFrame(
                [
                    {
                        "ma_nhap_hang": "PN1",
                        "ma_hang": "SP1",
                        "so_luong": 10,
                        "don_gia": 15000,
                    }
                ]
            ),
            "danh_sach_hoa_don": pd.DataFrame(
                [{"ma_hoa_don": "H1", "thoi_gian": pd.Timestamp("2026-09-14")}]
            ),
        }
    )
    t, v, _ = resolve_inventory_batch_query(
        "Cuối ngày 14/09/2026, hàng còn dư trong kho tính theo giá vốn đáng bao nhiêu tiền?",
        stock_nhap_no_cost_ctx,
        "2026-09-14",
    )
    assert (t, v) == ("abstain", None)
