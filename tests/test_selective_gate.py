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
