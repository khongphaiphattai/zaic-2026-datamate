"""Unit tests for domain extensions: dev_011, dev_014, logistics, unsold, OOD."""

import pytest
from zaic_2026_datamate.data_loader import DataLoader
from zaic_2026_datamate.domain_ext import resolve_domain_extensions
from zaic_2026_datamate.rolling_window import resolve_rolling_window_query

@pytest.fixture(scope="module")
def public_loader():
    return DataLoader("data/public_test/public_test/data")


def test_ood_menu_items(public_loader):
    ctx8 = public_loader.load_business("dev_008")
    res = resolve_domain_extensions("Tiền bán Đậu phụ rán giòn tháng 10/2025 là bao nhiêu?", ctx8, "2026-07-28")
    assert res is not None
    assert res[0] == "unanswerable"

    res2 = resolve_domain_extensions("Tháng 9/2025 Cháo ngao bán được mấy nồi?", ctx8, "2026-07-28")
    assert res2 is not None
    assert res2[0] == "unanswerable"


def test_ood_clinical_advice(public_loader):
    ctx6 = public_loader.load_business("dev_006")
    res = resolve_domain_extensions("Bà bầu dùng Acemuc Kids 200mg hộp 30 gói được không?", ctx6, "2026-08-08")
    assert res is not None
    assert res[0] == "unanswerable"


def test_delivery_rounds(public_loader):
    ctx4 = public_loader.load_business("dev_004")
    res = resolve_domain_extensions("Có mấy đợt giao hàng trong 11/2025?", ctx4, "2026-06-10")
    assert res == ("number", 671, None)

    res2 = resolve_domain_extensions("Thang nay tiem giao hang tong cong may dot?", ctx4, "2026-06-10")
    assert res2 == ("number", 249, None)


def test_unsold_items(public_loader):
    ctx4 = public_loader.load_business("dev_004")
    res = resolve_domain_extensions("7 ngày gần nhất có mấy mặt hàng ế, không bán được cái nào?", ctx4, "2026-02-15")
    assert res == ("number", 1, None)

    res2 = resolve_domain_extensions("Trong 7 ngày gần nhất có bao nhiêu mặt hàng không bán được cái nào?", ctx4, "2026-02-17")
    assert res2 == ("number", 0, None)

    ctx13 = public_loader.load_business("dev_013")
    res3 = resolve_domain_extensions("7 ngày gần nhất shop mik kó bnhiu món hok bán ra đc cái nào zị, coi nha e", ctx13, "2025-09-27")
    assert res3 == ("number", 4, None)


def test_phone_analytics_dev_011(public_loader):
    ctx11 = public_loader.load_business("dev_011")
    # Trade-in phone value
    res = resolve_domain_extensions("Tổng tiền thu mua máy cũ của khách 6-2025 là bao nhiêu?", ctx11, "2026-07-31")
    assert res == ("number", 31100000, None)

    # Customer net payment
    res = resolve_domain_extensions("6-2025 tổng tiền khách phải trả là bnhiu nhỉ?", ctx11, "2026-07-31")
    assert res == ("number", 871448000, None)

    # All phones sold (excluding accessories)
    res = resolve_domain_extensions("Tháng 5/2026 báng đượt mấy máy, không kể phụ kiện nha?", ctx11, "2026-07-31")
    assert res == ("number", 85, None)

    # Used phones sold
    res = resolve_domain_extensions("Điện thoại cũ tháng 6/26 bán ra mấy máy thế?", ctx11, "2026-07-31")
    assert res == ("number", 29, None)

    # Specific model
    res = resolve_domain_extensions("Tháng này bán được bao nhiêu chiếc iPhone 16 Pro Max?", ctx11, "2026-07-31")
    assert res == ("number", 6, None)


def test_phone_analytics_dev_014(public_loader):
    ctx14 = public_loader.load_business("dev_014")
    # Used phones count
    res = resolve_domain_extensions("Máy cũ 8/25 bán đc mấy chiếc vậy?", ctx14, "2026-09-30")
    assert res == ("number", 44, None)

    # Trade-in order percent
    res = resolve_domain_extensions("Đơn có thu lại máy cũ 4-2026 chiếm mấy % tổng đơn?", ctx14, "2026-09-30")
    assert res == ("percent", 4.58, None)

    # Warranty machine count
    res = resolve_domain_extensions("Máy bán ra tháng 8/2025, tới hnay có bao nhiêu máy đã mang lại bảo hành rồi?", ctx14, "2025-10-01")
    assert res == ("number", 5, None)


def test_rolling_window_typo(public_loader):
    ctx13 = public_loader.load_business("dev_013")
    res = resolve_rolling_window_query("30 ngay gan nhat doanhthu duoc bao nhieu?", ctx13, "2025-10-08")
    assert res is not None
    assert res[0] == "number"
    assert res[1] == 400282000
