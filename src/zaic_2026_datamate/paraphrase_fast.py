"""Train-24-derived syntax fallback for the existing daily order calculation."""
from datetime import date
import math
import re
import unicodedata

from zaic_2026_datamate.hybrid_agent import HybridAgent
from zaic_2026_datamate.intent_parser import resolve_query
from zaic_2026_datamate.selective_gate import daily_revenue_date


_ORDER_QUESTION = re.compile(
    r'(?:(?:cho minh biet|cho toi biet|cho minh hoi)\s+)?'
    r'so don(?: hang)?\s+'
    r'(?P<day>(?:ngay\s+)?(?:hom nay|hnay|hom qua|hqua)|ngay \d{1,2}/\d{1,2}/\d{4})'
    r'(?:\s+(?:la\s+)?bao nhi(?:eu|u))?'
    r'(?:\s+(?:a|nhi))?'
)


def canonical_daily_order_count(question, as_of_date):
    text = unicodedata.normalize('NFD', question.lower()).replace('đ', 'd')
    text = ''.join(c for c in text if unicodedata.category(c) != 'Mn')
    text = ' '.join(text.strip(' ?!.').split())
    match = _ORDER_QUESTION.fullmatch(text)
    if match is None:
        return None
    # Reuse the frozen date-only validation, not its revenue metric semantics.
    day = daily_revenue_date('doanh thu ' + match.group('day'), as_of_date)
    if day is None:
        return None
    return f'Ngày {date.fromisoformat(day):%d/%m/%Y} có bao nhiêu đơn hàng?'


class DailyOrderParaphraseAgent(HybridAgent):
    """Opt-in wrapper; never replaces an existing Fast or Slow answer."""

    def answer_query(self, question, context, as_of_date=None):
        self.last_canonical_question = None
        original = super().answer_query(question, context, as_of_date)
        if original[0] != 'abstain':
            return original
        canonical = canonical_daily_order_count(question, as_of_date)
        if canonical is None:
            return original
        self.last_canonical_question = canonical
        kind, value, reason = resolve_query(question=canonical, context=context, as_of_date=as_of_date)
        if kind == 'number' and type(value) in (int, float) and math.isfinite(value) and value > 0:
            return kind, value, reason, 'paraphrase_fast_path'
        return original
