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


def test_dev_006_extensions(public_loader):
    ctx6 = public_loader.load_business("dev_006")
    # Debt customer count
    res = resolve_domain_extensions("Den het ngay 13/12/2025 tiem con bao nhieu khach no chua tra xong?", ctx6, "2025-12-13")
    assert res == ("number", 20, None)

    # Debt aging buckets
    res2 = resolve_domain_extensions("Đến hết 17/12/2025 chia tiền nợ còn lại theo tuổi nợ 0–7 ngày, 8–15 ngày và trên 15 ngày, từng nhóm có bao nhiêu tiền?", ctx6, "2025-12-17")
    assert res2 == ("list", [2506050, 250200, 1954218], None)

    # Expiring lots list
    res3 = resolve_domain_extensions("Kể giúp các sp còn lô tồn hết hạn trong 90 ngày tới.", ctx6, "2026-08-08")
    assert res3 is not None and res3[0] == "list" and len(res3[1]) == 10

    # Top gross profit product
    res4 = resolve_domain_extensions("2/2026 hàng mô lãi gộp nhiều nhất rứa?", ctx6, "2026-08-08")
    assert res4 == ("string", "Costar Omega 3 1000mg lọ 100 viên", None)


def test_dev_003_extensions(public_loader):
    ctx3 = public_loader.load_business("dev_003")
    # Debt customer count
    res = resolve_domain_extensions("Đến hết ngày 15/05/2026, cửa hàng còn bao nhiêu khách hàng chưa trả hết nợ?", ctx3, "2026-05-15")
    assert res == ("number", 38, None)

    # Debt aging buckets
    res2 = resolve_domain_extensions("Đến ngày 06/07/2025 tiền khách còn nợ chia theo tuổi nợ 0–7, 8–15 và trên 15 ngày thì mỗi nhóm nhiêu z?", ctx3, "2025-07-06")
    assert res2 == ("list", [382834, 387167, 357338], None)

    # Customer debt additions and repayments
    res3 = resolve_domain_extensions("Khách Lưu Sơn Hằng tháng 10 năm 2025 nợ thêm bnhiu, trả đc bnhiu? Cho số nợ thêm trước rồi số trả được.", ctx3, "2026-08-21")
    assert res3 == ("list", [14000, 31000], None)

    # Store net profit
    res4 = resolve_domain_extensions("Tháng 7/26 trừ hết chi phí quán còn lãi ròng bnhiu?", ctx3, "2026-08-21")
    assert res4 == ("number", 39578870, None)

    # Gross profit with typo laix gop
    res5 = resolve_domain_extensions("Laix gộp 5/2025 bnhiu?", ctx3, "2026-08-21")
    assert res5 == ("number", 164672604, None)


def test_dev_008_extensions(public_loader):
    ctx8 = public_loader.load_business("dev_008")
    # YTD order count
    res1 = resolve_domain_extensions("Dau nam den hnay tong so dh bnhiu?", ctx8, "2026-05-16")
    assert res1 == ("number", 9848, None)

    # Total debt on 2026-07-22
    res2 = resolve_domain_extensions("Tính đến hết ngày 22/07/2026, khách hàng còn nợ cửa hàng tổng cộng bao nhiêu tiền?", ctx8, "2026-07-22")
    assert res2 == ("number", 4549169, None)

    # Debt customer count
    res3 = resolve_domain_extensions("Đến hết ngày 22/07/2026, số khách còn công nợ với cửa hàng là bao nhiêu?", ctx8, "2026-07-22")
    assert res3 == ("number", 15, None)

    # Aging buckets
    res4 = resolve_domain_extensions("Đến hết ngày 22/07/2026, nợ còn lại chia theo tuổi nợ: nhóm 0–7 ngày, 8–15 ngày, trên 15 ngày mỗi nhóm bao nhiêu?", ctx8, "2026-07-22")
    assert res4 == ("list", [1429334, 1961667, 1158168], None)

    # Inventory at cost
    res5 = resolve_domain_extensions("Hết ngày 22/07/2026, hàng còn dư trong kho của quán tính theo giá vốn là bao nhiêu tiền?", ctx8, "2026-07-22")
    assert res5 == ("number", 172773009, None)


def test_dev_014_debt_extensions(public_loader):
    ctx14 = public_loader.load_business("dev_014")
    # Total debt on 2026-09-27
    res1 = resolve_domain_extensions("chị ơi, tih toi het ngay 27/9/2026 tong tien khach kòn nợ shop mik bnhiu đó ha chị", ctx14, "2026-09-27")
    assert res1 == ("number", 21218668, None)

    # Aging buckets on 2026-09-23
    res2 = resolve_domain_extensions("Đến ngày 23/09/2026 tiền khách còn nợ chia theo tuổi nợ 0–7, 8–15 và trên 15 ngày, mỗi nhóm bnhiu?", ctx14, "2026-09-23")
    assert res2 == ("list", [12652000, 8511500, 159168], None)

    # Debt customer count on 2026-06-02
    res3 = resolve_domain_extensions("Chéc giùm tại ngày 02/06/2026 tiệm còn mấy khách có công nợ?", ctx14, "2026-06-02")
    assert res3 == ("number", 5, None)


def test_dev_007_extensions(public_loader):
    ctx7 = public_loader.load_business("dev_007")
    # Inventory at cost
    res1 = resolve_domain_extensions("Cuối ngày 19/09/2026, hàng còn dư trong kho tính theo giá vốn đáng bao nhiêu tiền?", ctx7, "2026-09-19")
    assert res1 == ("number", 146562947, None)

    # Purchase cost August 2026
    res2 = resolve_domain_extensions("Tháng 8/2026 quán cất hàng hết bao nhiêu tiền?", ctx7, "2026-09-19")
    assert res2 == ("number", 133121600, None)

    # Best employee July 2026
    res3 = resolve_domain_extensions("Th7/2026 nhân diên nào bán giỏi nhất?", ctx7, "2026-09-19")
    assert res3 == ("string", "NV005", None)

    # Top 5 customers March 2025
    res4 = resolve_domain_extensions("5 khách mua nhiều tiền nhất th3/2025 là ai z?", ctx7, "2026-09-19")
    assert res4 == ("list", ["Triệu Dương Đức", "Ngô Hồng Duy", "Phạm Thảo Loan", "Diệp Thanh Quang", "Phùng Bảo Khoa"], None)


def test_dev_002_and_dev_011_extensions(public_loader):
    ctx2 = public_loader.load_business("dev_002")
    res1 = resolve_domain_extensions("Liệt kê 3 mặt hàng bán chạy nhất tháng 6/2025.", ctx2, "2026-05-24")
    assert res1 == ("list", ["Cà phê đen (M)", "Trà đào cam sả (M)", "Cà phê sữa (M)"], None)

    res2 = resolve_domain_extensions("Th9/2025 3 khach nao chi nhieu tien nhat?", ctx2, "2026-05-24")
    assert res2 == ("list", ["Châu Quang Khánh", "Đặng Trung Huy", "Vũ Bích Phong"], None)

    ctx11 = public_loader.load_business("dev_011")
    res3 = resolve_domain_extensions("Tháng 1/26 sp nào bán chạy nhất?", ctx11, "2026-07-31")
    assert res3 == ("string", "Ốp lưng silicon iPhone", None)

