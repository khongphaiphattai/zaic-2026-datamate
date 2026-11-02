from unittest.mock import patch

import pytest

from zaic_2026_datamate.paraphrase_fast import DailyOrderParaphraseAgent, canonical_daily_order_count


@pytest.mark.parametrize('question,expected', [
    ('Số đơn hnay là bao nhiêu?', 'Ngày 07/09/2026 có bao nhiêu đơn hàng?'),
    ('Cho mình biết số đơn hôm qua.', 'Ngày 06/09/2026 có bao nhiêu đơn hàng?'),
    ('Số đơn hàng hôm nay là bao nhiêu?', 'Ngày 07/09/2026 có bao nhiêu đơn hàng?'),
    ('cho tôi biết số đơn hqua', 'Ngày 06/09/2026 có bao nhiêu đơn hàng?'),
    ('Cho mình hỏi số đơn hàng hôm qua là bao nhiêu?', 'Ngày 06/09/2026 có bao nhiêu đơn hàng?'),
    ('Số đơn hàng hôm nay là bao nhiêu ạ?', 'Ngày 07/09/2026 có bao nhiêu đơn hàng?'),
    ('Số đơn ngày 01/09/2026 là bao nhiêu?', 'Ngày 01/09/2026 có bao nhiêu đơn hàng?'),
])
def test_train24_daily_order_phrases(question, expected):
    assert canonical_daily_order_count(question, '2026-09-07') == expected


@pytest.mark.parametrize('question', [
    'Số đơn hủy hôm nay là bao nhiêu?',
    'Số đơn thành công hôm nay là bao nhiêu?',
    'Số đơn hàng hôm nay của khách Lan là bao nhiêu?',
    'Số đơn hôm nay mua bia là bao nhiêu?',
    'Số đơn từ đầu năm đến hôm nay là bao nhiêu?',
    'Số đơn hôm nay so với hôm qua là bao nhiêu?',
    'Số đơn hôm nay trên 500k là bao nhiêu?',
    'Số đơn hôm nay trả tiền mặt là bao nhiêu?',
    'Số đơn hôm nay có phải 5 không?',
    'Số đơn hôm nay là bao nhiêu ngày?',
    'Số đơn ngày 31/02/2026 là bao nhiêu?',
    'Số đơn ngày 08/09/2026 là bao nhiêu?',
])
def test_conditions_and_invalid_dates_stay_blocked(question):
    assert canonical_daily_order_count(question, '2026-09-07') is None


def test_missing_anchor_stays_blocked():
    assert canonical_daily_order_count('Số đơn hôm nay là bao nhiêu?', None) is None


def test_wrapper_preserves_original_answer_without_fallback():
    agent = DailyOrderParaphraseAgent()
    original = ('number', 7, None, 'fast_path')
    with patch('zaic_2026_datamate.paraphrase_fast.HybridAgent.answer_query', return_value=original), patch('zaic_2026_datamate.paraphrase_fast.resolve_query') as resolver:
        assert agent.answer_query('Số đơn hôm nay là bao nhiêu?', None, '2026-09-07') == original
        resolver.assert_not_called()


def test_wrapper_uses_existing_count_calculation_and_no_model():
    class ForbiddenModel:
        def generate(self, *args, **kwargs):
            pytest.fail('Order paraphrases must not call model')
    agent = DailyOrderParaphraseAgent(enable_slow_path=True, llm_client=ForbiddenModel())
    with patch('zaic_2026_datamate.hybrid_agent.resolve_query', return_value=('abstain', None, 'no match')), patch('zaic_2026_datamate.paraphrase_fast.resolve_query', return_value=('number', 8, None)) as resolver:
        assert agent.answer_query('Số đơn hôm qua là bao nhiêu?', None, '2026-09-07') == ('number', 8, None, 'paraphrase_fast_path')
        assert resolver.call_args.kwargs['question'] == 'Ngày 06/09/2026 có bao nhiêu đơn hàng?'


def test_wrapper_does_not_promote_unanswerable_or_abstain():
    agent = DailyOrderParaphraseAgent()
    original = ('abstain', None, 'safe', 'abstain')
    for result in [('unanswerable', None, 'out of range'), ('abstain', None, 'no table')]:
        with patch('zaic_2026_datamate.paraphrase_fast.HybridAgent.answer_query', return_value=original), patch('zaic_2026_datamate.paraphrase_fast.resolve_query', return_value=result):
            assert agent.answer_query('Số đơn hôm nay là bao nhiêu?', None, '2026-09-07') == original
