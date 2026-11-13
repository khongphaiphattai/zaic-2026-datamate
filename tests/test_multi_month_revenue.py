from pathlib import Path
import duckdb
import pandas as pd
from zaic_2026_datamate.data_loader import BusinessContext
from zaic_2026_datamate.multi_month_revenue import (
    extract_consecutive_months,
    resolve_multi_month_query,
)


def _make_mm_ctx() -> BusinessContext:
    df = pd.DataFrame(
        [
            {"ngay": "2025-01-10", "thanh_tien": 1000000, "trang_thai": "Hoàn thành"},
            {"ngay": "2025-02-10", "thanh_tien": 1500000, "trang_thai": "Hoàn thành"},
            {"ngay": "2025-03-10", "thanh_tien": 2000000, "trang_thai": "Hoàn thành"},
            {"ngay": "2025-04-10", "thanh_tien": 1200000, "trang_thai": "Hoàn thành"},
            {"ngay": "2025-05-10", "thanh_tien": 1800000, "trang_thai": "Hoàn thành"},
            {"ngay": "2025-06-10", "thanh_tien": 2400000, "trang_thai": "Hoàn thành"},
        ]
    )
    conn = duckdb.connect()
    conn.register("ban_hang", df)
    return BusinessContext(
        business_id="priv_mm_01",
        data_dir=Path("."),
        tables={"ban_hang": df},
        duckdb_conn=conn,
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
    ctx = _make_mm_ctx()

    # List query
    q = "Kể giúp dthu 3 tháng liền từ T4/2025 tới T6/2025, theo thứ tự thời gian."
    ans_type, ans_val, _ = resolve_multi_month_query(q, ctx, "2026-08-08")
    assert ans_type == "list"
    assert ans_val == [1200000, 1800000, 2400000]

    # Boolean query
    q_bool = "Từ th 1/25 tới th 3/25, dthu tháng sau đều cao hơn tháng trước hả?"
    ans_type, ans_val, _ = resolve_multi_month_query(q_bool, ctx, "2026-05-10")
    assert (ans_type, ans_val) == ("boolean", True)
