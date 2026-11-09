import pytest
from zaic_2026_datamate.data_loader import DataLoader
from zaic_2026_datamate.multi_month_revenue import (
    extract_consecutive_months,
    resolve_multi_month_query,
)


def test_extract_consecutive_months():
    # 1. Range
    res = extract_consecutive_months("dthu từ T4/2025 tới T6/2025")
    assert res == ["2025-04", "2025-05", "2025-06"]

    # 2. Ending
    res = extract_consecutive_months("dthu 3 thang lien nhau tinh toi 3-2026")
    assert res == ["2026-01", "2026-02", "2026-03"]

    # 3. Explicit list
    res = extract_consecutive_months("Dthu th 12/25, th 1/26, th 2/26 mỗi tháng bao nhiêu")
    assert res == ["2025-12", "2026-01", "2026-02"]

    # 4. Same year list
    res = extract_consecutive_months("Dthu tháng 3, tháng 4, tháng 5 năm 2025 lần lượt bnhiu")
    assert res == ["2025-03", "2025-04", "2025-05"]

    # 5. With as_of_date
    res = extract_consecutive_months("Dthu th4, th5, th6 mỗi tháng được nhiêu z?", "2026-08-21")
    assert res == ["2026-04", "2026-05", "2026-06"]


def test_resolve_multi_month_query():
    loader = DataLoader("data/public_test/public_test/data", "data/public_test/public_test/calendar.csv")
    ctx = loader.load_business("dev_006")

    # List query
    q = "Kể giúp dthu 3 tháng liền từ T4/2025 tới T6/2025, theo thứ tự thời gian."
    ans_type, ans_val, _ = resolve_multi_month_query(q, ctx, "2026-08-08")
    assert ans_type == "list"
    assert isinstance(ans_val, list)
    assert len(ans_val) == 3
    assert all(isinstance(x, int) and x > 0 for x in ans_val)

    # Boolean query
    q_bool = "Từ th 1/25 tới th 3/25, dthu tháng sau đều cao hơn tháng trước hả?"
    ctx_005 = loader.load_business("dev_005")
    ans_type, ans_val, _ = resolve_multi_month_query(q_bool, ctx_005, "2026-05-10")
    assert ans_type == "boolean"
    assert isinstance(ans_val, bool)
