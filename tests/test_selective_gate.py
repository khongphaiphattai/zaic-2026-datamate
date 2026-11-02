from unittest.mock import patch
import pytest
from zaic_2026_datamate.selective_gate import daily_revenue_date
from zaic_2026_datamate.hybrid_agent import HybridAgent
from zaic_2026_datamate.llm_client import MockLLMClient

@pytest.mark.parametrize('q,day',[
 ('Tổng doanh thu trong ngày hôm nay là bao nhiêu?', '2026-09-07'),
 ('Doanh thu hôm nay bao nhiêu?', '2026-09-07'),
 ('Hôm qua tổng doanh thu của shop là bao nhiêu tiền?', '2026-09-06'),
 ('Ngày 01/09/2026 doanh thu bao nhiêu?', '2026-09-01'),
 ('doanh thu ngày 31/02/2026 bao nhiêu?',None),
 ('doanh thu bia hôm nay bao nhiêu?',None),
 ('doanh thu hôm nay so với hôm qua?',None),
 ('doanh thu ngày 08/09/2026 bao nhiêu?',None),
 ('hôm nay doanh thu tiền mặt bao nhiêu?',None),
 ('hôm nay doanh thu tháng này bao nhiêu?',None),
])
def test_gate(q,day): assert daily_revenue_date(q,'2026-09-07')==day

def test_no_anchor(): assert daily_revenue_date('doanh thu hôm nay bao nhiêu?',None) is None

def test_complex_never_calls_model():
 client=MockLLMClient(handler=lambda _: pytest.fail('Model must not be called'))
 agent=HybridAgent(enable_slow_path=True,llm_client=client)
 with patch('zaic_2026_datamate.hybrid_agent.resolve_query',return_value=('abstain',None,None)):
  assert agent.answer_query('khách nào mua nhiều nhất?',None,'2026-09-07')[0]=='abstain'

@pytest.mark.parametrize('q,day',[
 ('Tổng doanh thu trong ngày hôm nay là bao nhiêu nhỉ?', '2026-09-07'),
 ('hôm nay cuwar hangf bán đươc bao nhiu tiền z', '2026-09-07'),
 ('Giúp mình tính riêng ngày hôm nay, doanh thu của cửa hàng được bao nhiêu tiền?', '2026-09-07'),
 ('check giùm tổng dthu hqua là nhiêu', '2026-09-06'),
 ('Cho tôi biết doanh thu hôm qua.', '2026-09-06'),
 ('tổng doanh thu trong ngày hôm nay là nhiêu', '2026-09-07'),
 ('hnay cua hang ban dc bao nhiu tien vay', '2026-09-07'),
 ('hôm qua cửa hàng bán dc bn tiền', '2026-09-06'),
 ('coi giumf toongr dthu trong ngày hôm nay là bnhiu', '2026-09-07'),
 ('e tinh rieng ngay hom nay, doanh thu cua cua hang dc bao nhieu tien', '2026-09-07'),
 ('dthu hqua cua cua hang la bnhiu', '2026-09-06'),
])
def test_train24_paraphrases(q,day):
 assert daily_revenue_date(q,'2026-09-07')==day


@pytest.mark.parametrize('q',[
 'Cho tôi biết doanh thu hôm qua của bia.',
 'Giúp mình tính riêng ngày hôm nay, doanh thu của khách Lan được bao nhiêu tiền?',
 'hôm nay cửa hàng bán được bao nhiêu đơn?',
 'hôm qua tổng cộng bán được bao nhiêu cái',
 'Tổng doanh thu từ đầu năm đến hôm nay là bao nhiêu nhỉ?',
 'Cho tôi biết doanh thu hôm qua so với hôm nay.',
 'Hôm nay doanh thu từ 500k trở lên là bao nhiêu?',
 'Hôm nay cửa hàng bán được bao nhiêu tiền mặt?',
 'Hôm nay cửa hàng bán được bao nhiêu tiền phải không?',
 'Cho tôi biết doanh thu hôm qua và ngày 01/09/2026.',
 'Tính riêng ngày hôm nay, doanh thu của shop trên Shopee là bao nhiêu tiền?',
 'Hôm nay doanh thu là 500k đúng không?',
 'Hôm nay cửa hàng bán thuốc được bao nhiêu tiền?',
])
def test_minimal_pairs_keep_conditions_blocked(q):
 assert daily_revenue_date(q,'2026-09-07') is None
