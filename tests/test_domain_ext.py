"""Unit tests for general schema-driven domain extensions (zero dev_* hardcodes)."""

from pathlib import Path
import ast
import re

import duckdb
import pandas as pd
import pytest

from zaic_2026_datamate.data_loader import BusinessContext, DataLoader
from zaic_2026_datamate.domain_ext import resolve_domain_extensions
from zaic_2026_datamate.rolling_window import resolve_rolling_window_query


@pytest.fixture(scope="module")
def train_loader():
    return DataLoader("data/train/train/data")


def _make_synthetic_ctx(
    business_id: str, tables: dict[str, pd.DataFrame]
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


def test_no_hardcoded_dev_ids_or_dates_in_domain_ext():
    """Static AST guard: domain_ext.py must contain zero dev_* identifiers."""
    source_path = Path("src/zaic_2026_datamate/domain_ext.py")
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    offending: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        # Skip module/function docstrings
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            continue
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if re.search(r"\bdev_\d+", node.value):
                offending.append((getattr(node, "lineno", 0), node.value))
    assert not offending, f"Found hardcoded dev_* string constants: {offending}"


def test_ood_clinical_advice():
    ctx_priv = _make_synthetic_ctx(
        "priv_pharm_01",
        {
            "ban_hang": pd.DataFrame(
                [{"ngay": "2026-08-01", "ten_hang": "Acemuc Kids 200mg", "so_luong": 1, "thanh_tien": 50000}]
            )
        },
    )
    res = resolve_domain_extensions(
        "Bà bầu dùng Acemuc Kids 200mg hộp 30 gói được không?",
        ctx_priv,
        "2026-08-08",
    )
    assert res is not None
    assert res[0] == "unanswerable"


@pytest.mark.slow
def test_schema_driven_cancellation_return_ood(train_loader):
    # biz_115 lacks reason/cause columns -> cancellation/return reason is unanswerable
    ctx115 = train_loader.load_business("biz_115")
    res_cancel = resolve_domain_extensions(
        "Tháng 6/2025 có bao nhiêu đơn bị huỷ vì giao trễ?",
        ctx115,
        "2026-07-01",
    )
    assert res_cancel is not None and res_cancel[0] == "unanswerable"

    res_ret = resolve_domain_extensions(
        "Khách trả món do gọi nhầm món bao nhiêu lần?",
        ctx115,
        "2026-07-01",
    )
    assert res_ret is not None and res_ret[0] == "unanswerable"

    # biz_123 HAS tra_hang.nguyen_nhan -> gate must NOT blindly reject reason queries
    ctx123 = train_loader.load_business("biz_123")
    res_has_reason = resolve_domain_extensions(
        "Khách trả món do món bị lỗi bao nhiêu lần?",
        ctx123,
        "2026-06-28",
    )
    assert res_has_reason is None or res_has_reason[0] != "unanswerable"


def test_delivery_rounds():
    ctx_priv = _make_synthetic_ctx(
        "priv_log_01",
        {
            "giao_hang": pd.DataFrame(
                [
                    {"ngay_giao_hang": "2025-11-01", "ma_don_hang": "D1", "luot_giao": 1},
                    {"ngay_giao_hang": "2025-11-01", "ma_don_hang": "D1", "luot_giao": 1},
                    {"ngay_giao_hang": "2025-11-02", "ma_don_hang": "D1", "luot_giao": 2},
                    {"ngay_giao_hang": "2025-11-03", "ma_don_hang": "D2", "luot_giao": 1},
                    {"ngay_giao_hang": "2026-06-05", "ma_don_hang": "D3", "luot_giao": 1},
                    {"ngay_giao_hang": "2026-06-08", "ma_don_hang": "D4", "luot_giao": 1},
                ]
            )
        },
    )
    res = resolve_domain_extensions("Có mấy đợt giao hàng trong 11/2025?", ctx_priv, "2026-06-10")
    assert res == ("number", 3, None)

    res2 = resolve_domain_extensions(
        "Thang nay tiem giao hang tong cong may dot?", ctx_priv, "2026-06-10"
    )
    assert res2 == ("number", 2, None)


def test_unsold_items():
    cat = pd.DataFrame(
        [
            {"ten_hang": "Sản phẩm A"},
            {"ten_hang": "Sản phẩm B"},
            {"ten_hang": "Sản phẩm C"},
        ]
    )
    sbh = pd.DataFrame(
        [
            {"ngay": "2026-02-11", "ten_hang": "Sản phẩm A", "sl": 1},
            {"ngay": "2026-02-12", "ten_hang": "Sản phẩm B", "sl": 2},
            {"ngay": "2026-02-16", "ten_hang": "Sản phẩm C", "sl": 1},
        ]
    )
    ctx_syn = _make_synthetic_ctx("priv_unsold_01", {"danh_muc_hang": cat, "so_ban_hang": sbh})
    res = resolve_domain_extensions(
        "7 ngày gần nhất có mấy mặt hàng ế, không bán được cái nào?",
        ctx_syn,
        "2026-02-15",
    )
    assert res == ("number", 1, None)

    res2 = resolve_domain_extensions(
        "Trong 7 ngày gần nhất có bao nhiêu mặt hàng không bán được cái nào?",
        ctx_syn,
        "2026-02-17",
    )
    assert res2 == ("number", 0, None)


def test_phone_analytics_schema_without_dev_id():
    sbh = pd.DataFrame(
        [
            {
                "stt": 1,
                "ngay": "2025-06-05",
                "hang": "ip16pm",
                "sl": 2,
                "thanh_tien": 60000000.0,
                "khach_tra": "55.000.000đ",
                "thu_may_cu": 5000000.0,
            },
            {
                "stt": 1,
                "ngay": "2025-06-05",
                "hang": "cuong_luc",
                "sl": 2,
                "thanh_tien": 200000.0,
                "khach_tra": "55.000.000đ",
                "thu_may_cu": 0.0,
            },
            {
                "stt": 2,
                "ngay": "2025-06-10",
                "hang": "ip13_cu",
                "sl": 3,
                "thanh_tien": 30000000.0,
                "khach_tra": "27.000.000đ",
                "thu_may_cu": 3000000.0,
            },
            {
                "stt": 3,
                "ngay": "2025-07-08",
                "hang": "ip16pm",
                "sl": 4,
                "thanh_tien": 120000000.0,
                "khach_tra": "120.000.000đ",
                "thu_may_cu": 0.0,
            },
        ]
    )
    ghi_tat = pd.DataFrame(
        [
            {"ghi_tat": "ip16pm", "ten_hang": "iPhone 16 Pro Max", "loai": "máy mới"},
            {"ghi_tat": "ip13_cu", "ten_hang": "iPhone 13 Cũ", "loai": "máy cũ"},
            {"ghi_tat": "cuong_luc", "ten_hang": "Kính cường lực", "loai": "phụ kiện"},
        ]
    )
    ctx11 = _make_synthetic_ctx("priv_phone_11", {"so_ban_hang": sbh, "so_ban_hang_ghi_tat": ghi_tat})
    assert resolve_domain_extensions(
        "Tổng tiền thu mua máy cũ của khách 6-2025 là bao nhiêu?",
        ctx11,
        "2025-07-31",
    ) == ("number", 8000000, None)
    assert resolve_domain_extensions(
        "6-2025 tổng tiền khách phải trả là bnhiu nhỉ?", ctx11, "2025-07-31"
    ) == ("number", 82000000, None)
    assert resolve_domain_extensions(
        "Tiệm bán th7/2025 được tổng cộng bao nhiêu tiền nhỉ?",
        ctx11,
        "2025-07-31",
    ) == ("number", 120000000, None)
    assert resolve_domain_extensions(
        "Tháng 6/2025 báng đượt mấy máy, không kể phụ kiện nha?",
        ctx11,
        "2025-07-31",
    ) == ("number", 5, None)
    assert resolve_domain_extensions(
        "Điện thoại cũ tháng 6/25 bán ra mấy máy thế?", ctx11, "2025-07-31"
    ) == ("number", 3, None)
    assert resolve_domain_extensions(
        "Tháng này bán được bao nhiêu chiếc iPhone 16 Pro Max?",
        ctx11,
        "2025-07-31",
    ) == ("number", 4, None)


def test_phone_analytics_installment_schema():
    sbh = pd.DataFrame(
        [
            {"stt": 1, "ngay": "2025-08-05", "hang": "ip13_cu", "sl": 4},
            {"stt": 2, "ngay": "2026-04-02", "hang": "ip16pm", "sl": 1},
            {"stt": 3, "ngay": "2026-04-03", "hang": "ip16pm", "sl": 1},
            {"stt": 4, "ngay": "2026-04-05", "hang": "ip13_cu", "sl": 1},
            {"stt": 5, "ngay": "2026-04-06", "hang": "ip16pm", "sl": 1},
        ]
    )
    ghi_tat = pd.DataFrame(
        [
            {"ghi_tat": "ip16pm", "ten_hang": "iPhone 16 Pro Max", "loai": "máy mới"},
            {"ghi_tat": "ip13_cu", "ten_hang": "iPhone 13 Cũ", "loai": "máy cũ"},
        ]
    )
    thu_mc = pd.DataFrame([{"ngay": "2026-04-02", "tru_vao_stt_so_ban": 2, "gia_thu": 5000000}])
    bh = pd.DataFrame(
        [
            {"imei": "I1", "ngay_mua": "2025-08-05", "ngay_nhan": "2025-09-10"},
            {"imei": "I2", "ngay_mua": "2025-08-05", "ngay_nhan": "2025-09-20"},
            {"imei": "I3", "ngay_mua": "2025-08-05", "ngay_nhan": "2025-11-01"},  # after as_of
        ]
    )
    ctx14 = _make_synthetic_ctx(
        "priv_phone_14",
        {"so_ban_hang": sbh, "so_ban_hang_ghi_tat": ghi_tat, "thu_may_cu": thu_mc, "so_no_bao_hanh": bh},
    )
    assert resolve_domain_extensions(
        "Máy cũ 8/25 bán đc mấy chiếc vậy?", ctx14, "2026-09-30"
    ) == ("number", 4, None)
    assert resolve_domain_extensions(
        "Đơn có thu lại máy cũ 4-2026 chiếm mấy % tổng đơn?", ctx14, "2026-09-30"
    ) == ("percent", 25.0, None)
    assert resolve_domain_extensions(
        "Máy bán ra tháng 8/2025, tới hnay có bao nhiêu máy đã mang lại bảo hành rồi?",
        ctx14,
        "2025-10-01",
    ) == ("number", 2, None)


@pytest.mark.slow
def test_schema_driven_top_supplier(train_loader):
    ctx124 = train_loader.load_business("biz_124")
    res = resolve_domain_extensions(
        "Nhà cung cấp chính trong tháng 5/2025 là ai?",
        ctx124,
        "2026-06-01",
    )
    assert res == ("string", "Cty Ông Hải", None)


@pytest.mark.slow
def test_schema_driven_the_kho_stockout_dynamic_date(train_loader):
    # Real twin biz_123
    ctx123 = train_loader.load_business("biz_123")
    res = resolve_domain_extensions(
        "Đến hết ngày 15/06/2026 có bao nhiêu mặt hàng hết hàng trong kho?",
        ctx123,
        "2026-06-28",
    )
    assert res == ("number", 0, None)

    # Synthetic priv_* context proving date is dynamically parsed from question/as_of_date
    the_kho_df = pd.DataFrame(
        [
            {"ma_hang_hoa": "A", "ma_giao_dich_kho": 1, "ngay_gio_phat_sinh": "2025-05-01 09:00:00", "ton_cuoi": 10},
            {"ma_hang_hoa": "A", "ma_giao_dich_kho": 2, "ngay_gio_phat_sinh": "2025-06-01 15:00:00", "ton_cuoi": 0},
            {"ma_hang_hoa": "B", "ma_giao_dich_kho": 3, "ngay_gio_phat_sinh": "2025-05-20 10:00:00", "ton_cuoi": 0},
            {"ma_hang_hoa": "B", "ma_giao_dich_kho": 4, "ngay_gio_phat_sinh": "2025-07-01 10:00:00", "ton_cuoi": 5},
        ]
    )
    ctx_syn = _make_synthetic_ctx("priv_pharm_02", {"the_kho": the_kho_df})
    assert resolve_domain_extensions(
        "Ngày 01/06/2025 có bao nhiêu mặt hàng hết hàng?", ctx_syn, "2026-08-01"
    ) == ("number", 2, None)
    assert resolve_domain_extensions(
        "Hiện có bao nhiêu sản phẩm bị cháy hàng?", ctx_syn, "2026-08-01"
    ) == ("number", 1, None)


def test_schema_driven_orders_above_dynamic_threshold():
    ban_hang_df = pd.DataFrame(
        [
            {"hd": "O1", "ngay": "2025-05-01", "tt": 400000.0},
            {"hd": "O1", "ngay": "2025-05-01", "tt": 200000.0},  # total 600k
            {"hd": "O2", "ngay": "2025-05-02", "tt": 800000.0},  # total 800k
            {"hd": "O3", "ngay": "2025-05-03", "tt": 300000.0},  # total 300k
        ]
    )
    ctx_syn = _make_synthetic_ctx("priv_mb_99", {"ban_hang": ban_hang_df})
    assert resolve_domain_extensions(
        "Tháng 5/2025 có bao nhiêu đơn từ 500k trở lên?", ctx_syn, "2025-06-01"
    ) == ("number", 2, None)
    assert resolve_domain_extensions(
        "Tháng 5/2025 có mấy đơn giá trị từ 760.000 trở lên?", ctx_syn, "2025-06-01"
    ) == ("number", 1, None)


def test_rolling_window_typo():
    ctx_rw = _make_synthetic_ctx(
        "priv_rw_typo",
        {
            "ban_hang": pd.DataFrame(
                [
                    {"ngay": "2025-09-01", "ma_don": "D1", "thanh_tien": 999000, "trang_thai": "Hoàn thành"},  # outside 30d window ending 2025-10-08
                    {"ngay": "2025-09-20", "ma_don": "D2", "thanh_tien": 150000, "trang_thai": "Hoàn thành"},
                    {"ngay": "2025-10-01", "ma_don": "D3", "thanh_tien": 250000, "trang_thai": "Hoàn thành"},
                ]
            )
        },
    )
    res = resolve_rolling_window_query(
        "30 ngay gan nhat doanhthu duoc bao nhieu?", ctx_rw, "2025-10-08"
    )
    assert res == ("number", 400000, None)


def test_purchase_only_schema_and_order_threshold_status_guard():
    # Purchase ledger without stock/lot tables -> Gate 1 unanswerable for inventory questions
    ctx_purch = _make_synthetic_ctx(
        "priv_purch_only",
        {
            "ban_hang": pd.DataFrame(
                [
                    {"hd": "O1", "ngay": "2025-05-01", "tt": 600000.0, "trang_thai": "Hoàn thành"},
                    {"hd": "O2", "ngay": "2025-05-02", "tt": 800000.0, "trang_thai": "Đã hủy"},
                ]
            ),
            "nhap_hang": pd.DataFrame([{"ngay": "2025-05-01", "ten_hang": "Bánh mì", "sl": 10}]),
        },
    )
    res_inv = resolve_domain_extensions(
        "Tính tới ngày 10/05/2025 Bánh mì còn tồn kho bao nhiêu cái?",
        ctx_purch,
        "2025-05-15",
    )
    assert res_inv is not None and res_inv[0] == "unanswerable"
    assert resolve_domain_extensions(
        "Tháng 5/2025 có bao nhiêu đơn từ 500k trở lên?", ctx_purch, "2025-06-01"
    ) == ("number", 1, None)
