"""Train-24 syntax fallback for existing day/week/quarter/YTD order counts."""

import math
import re
import unicodedata
from datetime import date

from zaic_2026_datamate.cleaner import normalize_query_telex
from zaic_2026_datamate.hybrid_agent import HybridAgent
from zaic_2026_datamate.intent_parser import parse_time_expressions, resolve_query

_TODAY = r"(?:hom nay|hnay|bua nay)"
_RELATIVE = rf"(?:{_TODAY}|hom qua|hqua|bua qua)"
_PERIOD = (
    rf"(?P<period>(?:ngay\s+)?{_RELATIVE}|ngay \d{{1,2}}/\d{{1,2}}/\d{{4}}|"
    r"quy [1-4](?:/| nam )\d{4}|tuan (?:truoc|roi|nay)|"
    rf"(?:tinh )?tu (?:dau nam(?: nay)?|1/1 nam nay) (?:toi|den) het {_TODAY})"
)
_AMOUNT = r"(?:bao nhi(?:eu|u)|nhieu|bn|bnhiu)"
_UNIT = r"(?:don(?:\s+hang)?|bill)"
_PREFIX = r"(?:(?:e|cho minh biet|cho toi biet|cho minh hoi|cho hoi|check gium|coi gium|xem giup)\s+)?"
_SUFFIX = r"(?:\s+(?:a|nhi|ta|do|ha|vay|z))?"
_PATTERNS = tuple(
    re.compile(_PREFIX + body + _SUFFIX)
    for body in (
        rf"so {_UNIT}\s+{_PERIOD}(?:\s+(?:la\s+)?{_AMOUNT})?",
        rf"{_PERIOD}(?:\s*,\s*|\s+)(?:cua hang\s+)?(?:da\s+)?(?:co|ban duoc|ban dc)\s+(?:tong cong\s+)?{_AMOUNT}\s+{_UNIT}",
        rf"cua hang ban duoc\s+{_AMOUNT}\s+{_UNIT}\s+trong\s+{_PERIOD}",
    )
)
_TELEX = {"quys": "quy", "nawm": "nam", "hoom": "hom", "tooi": "toi", "bieets": "biet"}


def canonical_daily_order_count(question, as_of_date):
    # Historical function name retained for the daily wrapper's existing callers.
    try:
        anchor = date.fromisoformat(as_of_date)
    except TypeError, ValueError:
        return None
    normalized_q = normalize_query_telex(question)
    text = unicodedata.normalize("NFD", normalized_q.lower()).replace("đ", "d")
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    text = " ".join(text.strip(" ?!.").split())
    text = re.sub(r"\b\w+\b", lambda m: _TELEX.get(m.group(0), m.group(0)), text)
    match = next((m for pattern in _PATTERNS if (m := pattern.fullmatch(text))), None)
    if match is None:
        return None
    token = match.group("period")
    try:
        period = parse_time_expressions(token, as_of_date)
        if period.exact_date:
            day = date.fromisoformat(period.exact_date)
            return (
                f"Ngày {day:%d/%m/%Y} có bao nhiêu đơn hàng?" if day <= anchor else None
            )
        if (
            not period.start_date
            or not period.end_date
            or date.fromisoformat(period.end_date) > anchor
        ):
            return None
        if period.quarter_str:
            year, quarter = period.quarter_str.split("-Q")
            return f"Quý {quarter}/{year} có bao nhiêu đơn hàng?"
        if period.is_last_week:
            return "Tuần trước có bao nhiêu đơn hàng?"
        if period.is_this_week:
            return "Tuần này có bao nhiêu đơn hàng?"
        if token.startswith(("tu dau nam", "tinh tu dau nam", "tu 1/1")):
            return "Từ đầu năm đến hết hôm nay có bao nhiêu đơn hàng?"
    except ValueError, OverflowError:
        return None
    return None


class DailyOrderParaphraseAgent(HybridAgent):
    """Opt-in wrapper; never replaces an existing Fast or Slow answer."""

    def answer_query(self, question, context, as_of_date=None):
        self.last_canonical_question = None
        original = super().answer_query(question, context, as_of_date)
        if original[0] != "abstain":
            return original
        canonical = canonical_daily_order_count(question, as_of_date)
        if canonical is None:
            return original
        self.last_canonical_question = canonical
        kind, value, reason = resolve_query(
            question=canonical, context=context, as_of_date=as_of_date
        )
        if (
            kind == "number"
            and type(value) in (int, float)
            and math.isfinite(value)
            and value > 0
        ):
            return kind, value, reason, "paraphrase_fast_path"
        return original
