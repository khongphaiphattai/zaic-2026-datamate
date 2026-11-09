import pytest
from zaic_2026_datamate.data_loader import DataLoader
from zaic_2026_datamate.rolling_window import resolve_rolling_window_query


def test_rolling_window_train_cases():
    loader = DataLoader(data_dir="data/train/train/data")

    # 1. biz_118 7 days revenue
    ctx1 = loader.load_business("biz_118")
    res1 = resolve_rolling_window_query("Cho tôi biết doanh thu 7 ngày gần nhất.", ctx1, "2025-06-21")
    assert res1 is not None
    assert res1[0] == "number"
    assert res1[1] == 367415400

    # 2. biz_103 7 days revenue
    ctx2 = loader.load_business("biz_103")
    res2 = resolve_rolling_window_query("7 ngày gần nhất doanh thu đc bao nhiêu?", ctx2, "2025-09-29")
    assert res2 is not None
    assert res2[0] == "number"
    assert res2[1] == 37814600

    # 3. biz_117 30 days revenue
    ctx3 = loader.load_business("biz_117")
    res3 = resolve_rolling_window_query("Cho tôi biết dthu 30 ngày gần nhất", ctx3, "2025-02-19")
    assert res3 is not None
    assert res3[0] == "number"
    assert res3[1] == 730984600
