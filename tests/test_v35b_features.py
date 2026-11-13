"""
test_v35b_features.py: Regression and validation tests for v3.5b enhancements:
1. Reason guard precision (vỉ / ví điện tử not blocked)
2. MoM / MTD 'tăng giảm' unblocking
3. North-South regional synonyms
4. Price inquiry safety guard
5. Dịp Tết lunar calendar window (Rulebook §2)
"""

from __future__ import annotations

import pytest
from zaic_2026_datamate.data_loader import DataLoader
from zaic_2026_datamate.intent_parser import resolve_query
from zaic_2026_datamate.pipeline import PipelineConfig, build_agent
from zaic_2026_datamate.semantic_contract import BusinessSemanticContract, resolve_semantic_plan

pytestmark = pytest.mark.slow


@pytest.fixture(scope="module")
def train_loader():
    return DataLoader("data/train/train/data", "data/train/train/calendar.csv")


def test_blister_and_ewallet_not_blocked(train_loader):
    """Test that queries containing 'vỉ' or 'ví điện tử' are not blocked as reason queries."""
    ctx_120 = train_loader.load_business("biz_120")
    # A genuine item query with 'vỉ'
    q = "Tiền bán Savi Losartan 50mg hộp 3 vỉ x 10 viên tháng 2/2025 là bao nhiêu?"
    ans_type, ans_val, reason = resolve_query(q, ctx_120, "2026-06-12")
    assert reason != "Conditional / reason query abstained"


def test_growth_tang_giam_not_blocked(train_loader):
    """Test that MoM / MTD growth questions containing 'tăng giảm' are not blocked by 'tang gia'."""
    agent = build_agent(PipelineConfig(order_paraphrases=True, monthly_growth=True, mtd_growth=True))
    ctx_106 = train_loader.load_business("biz_106")
    q = "Doanh thu tháng 2/2026 so với tháng liền trước tăng giảm bao nhiêu %?"
    ans_type, ans_val, reason, route = agent.answer_query(q, ctx_106, "2026-09-14")
    assert ans_type == "percent"
    assert isinstance(ans_val, float)


def test_price_inquiry_does_not_return_revenue(train_loader):
    """Test that price inquiries (đơn giá) do not mistakenly return total revenue."""
    ctx_128 = train_loader.load_business("biz_128")
    q = "Hôm 26/12/2025, Tôm sốt Thái bán ra với đơn giá bao nhiêu tiền một đĩa?"
    ans_type, ans_val, reason = resolve_semantic_plan(q, ctx_128, "2026-09-07")
    # Must NOT return the total revenue of 1064000
    assert ans_val != 1064000
    assert ans_type == "abstain"


def test_tet_period_resolution(train_loader):
    """Test that Dịp Tết 2026 query correctly executes on calendar table."""
    ctx_106 = train_loader.load_business("biz_106")
    BusinessSemanticContract.from_context(ctx_106)
    q = "Dịp Tết âm lịch 2026 có doanh thu hơn hay kém 17 ngày trước Tết bao nhiêu %?"
    ans_type, ans_val, reason = resolve_semantic_plan(q, ctx_106, "2026-09-14")
    assert ans_type == "percent"
    assert ans_val == -41.86


def test_tet_best_seller(train_loader):
    """Test best seller during Tết 2026 on biz_123."""
    ctx_123 = train_loader.load_business("biz_123")
    BusinessSemanticContract.from_context(ctx_123)
    q = "Dịp Tết 2026, mặt hàng nào bán chạy nhất ạ?"
    ans_type, ans_val, reason = resolve_semantic_plan(q, ctx_123, "2026-06-28")
    assert ans_type == "string"
    assert ans_val == "Berberin 100mg Mekophar chai 100 viên"
