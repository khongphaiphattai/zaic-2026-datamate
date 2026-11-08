"""Whole-question daily store-revenue grammar; eligibility is not correctness.

Paraphrases and explicit Telex normalizations were derived from Train-24 only.
Unknown words and business conditions are never removed by normalization.
"""

import re
import unicodedata
from datetime import date, timedelta

_TELEX = {
    "cuwar": "cua",
    "hangf": "hang",
    "toongr": "tong",
    "giumf": "gium",
    "vaayj": "vay",
}
_RELATIVE = r"(?:hom nay|hnay|bua nay|hom qua|hqua)"
_DAY = rf"(?P<day>(?:(?:(?:tinh rieng|trong)\s+)?(?:ngay\s+)?{_RELATIVE}|ngay \d{{1,2}}/\d{{1,2}}/\d{{4}}))"
_METRIC = r"(?:tong\s+)?(?:doanh thu|dthu|tien ban)"
_STORE = r"(?:cua\s+)?(?:cua hang|shop|quan|tiem)"
_QUESTION = r"(?:(?:la|duoc|dc)\s+)?(?:bao nhi(?:eu|u)|bnhiu|bn|nhieu)(?:\s+tien)?"
_MONEY = r"(?:bao nhi(?:eu|u)|bnhiu|bn|nhieu)\s+tien"
_PREFIX = r"(?:e\s+)?(?:(?:cho minh hoi|cho hoi|cho toi biet|xem giup|check gium|coi gium|giup minh)\s+)?"
_SUFFIX = r"(?:\s+(?:ha|a|nhi|vay|z|ta|do))?"
_SEPARATOR = r"(?:\s*,\s*|\s+)"
_PATTERNS = tuple(
    re.compile(_PREFIX + body + _SUFFIX)
    for body in (
        rf"{_DAY}{_SEPARATOR}{_METRIC}(?:\s+{_STORE})?(?:\s+{_QUESTION})?",
        rf"{_METRIC}(?:\s+{_STORE})?\s+{_DAY}(?:\s+{_QUESTION})?",
        rf"{_METRIC}\s+{_DAY}\s+{_STORE}(?:\s+{_QUESTION})?",
        rf"{_DAY}{_SEPARATOR}(?:{_STORE}\s+)?(?:tong cong\s+)?ban (?:duoc|dc)\s+{_MONEY}",
        rf"{_STORE}\s+{_DAY}\s+ban (?:duoc|dc)\s+{_MONEY}",
    )
)


def daily_revenue_date(question, as_of_date):
    try:
        anchor = date.fromisoformat(as_of_date)
    except TypeError, ValueError:
        return None
    q = unicodedata.normalize("NFD", question.lower()).replace("đ", "d")
    q = "".join(c for c in q if unicodedata.category(c) != "Mn")
    q = " ".join(q.strip(" ?!.").split())
    q = re.sub(r"\b\w+\b", lambda m: _TELEX.get(m.group(0), m.group(0)), q)
    match = next((m for pattern in _PATTERNS if (m := pattern.fullmatch(q))), None)
    if match is None:
        return None
    token = match.group("day")
    relative = re.search(rf"\b({_RELATIVE})$", token)
    if relative:
        day = anchor - timedelta(days=relative.group(1) in ("hom qua", "hqua"))
    else:
        try:
            d, mo, y = map(int, token.removeprefix("ngay ").split("/"))
            day = date(y, mo, d)
        except ValueError:
            return None
    return day.isoformat() if day <= anchor else None
