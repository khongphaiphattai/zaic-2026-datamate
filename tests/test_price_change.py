"""Unit test for price_change.py deterministic engine."""

from pathlib import Path
import pytest
from zaic_2026_datamate.data_loader import DataLoader
from zaic_2026_datamate.price_change import resolve_price_change_query


def test_price_change_train_and_public_cases():
    test_loader = DataLoader(data_dir=Path("data/public_test/public_test/data"))

    # Test dev_008: Má heo / má lợn
    ctx8 = test_loader.load_business("dev_008")
    q8 = "Má lợn nướng nước mắm đổi giá từ 99.000đ lên 104.000đ vào ngày 15/04/2026. 30 ngày kể từ ngày đó bán được nhiều hay ít hơn 30 ngày liền trước bao nhiêu %, tính theo đĩa?"
    res8 = resolve_price_change_query(q8, ctx8, "2026-07-22")
    assert res8 is not None
    assert res8[0] == "percent"
    assert res8[1] == 40.78

    # Test dev_001: Lạc rang muối / đậu phộng
    ctx1 = test_loader.load_business("dev_001")
    q1 = "Lạc rang muối 200g lên giá 21.000đ từ ngày 01/09/2025; so 30 ngày kể từ ngày tăng giá với 30 ngày ngay trước, số gói bán ra tăng hay giảm bao nhiêu phần trăm?"
    res1 = resolve_price_change_query(q1, ctx1, "2026-04-30")
    assert res1 is not None
    assert res1[0] == "percent"
    assert res1[1] == 27.27

    # Test dev_004: Gas Petrolimex
    ctx4 = test_loader.load_business("dev_004")
    q4 = "Gas Petrolimex 12kg đổi giá từ 520.000đ lên 546.000đ vào ngày 31/10/2025. 30 ngày kể từ ngày đó bán được nhiều hay ít hơn 30 ngày liền trước bao nhiêu %, tính theo bình?"
    res4 = resolve_price_change_query(q4, ctx4, "2026-06-30")
    assert res4 is not None
    assert res4[0] == "percent"
    assert res4[1] == -9.2

    # Test dev_014: Cáp C-L
    ctx14 = test_loader.load_business("dev_014")
    q14 = "Ngày 01/12/2025 Cáp Type-C sang Lightning 1m tăng giá từ 150.000đ lên 157.500đ. Số cái bán được trong 14 ngày kể từ ngày đó so với 14 ngày trước đó thay đổi bao nhiêu %?"
    res14 = resolve_price_change_query(q14, ctx14, "2026-03-31")
    assert res14 is not None
    assert res14[0] == "percent"
    assert res14[1] == -23.81
