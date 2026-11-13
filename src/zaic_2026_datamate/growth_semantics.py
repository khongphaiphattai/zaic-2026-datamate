"""Bounded whole-question certification; unknown semantics always refuse.

This deterministic comparator does not demonstrate extra coverage from a model.
Neither this module nor its model proposal contract is wired into the pipeline.
"""

import json
import re
from dataclasses import dataclass
from datetime import date
from typing import Literal

from zaic_2026_datamate.monthly_growth import (
    GrowthIntent,
    GrowthWindows,
    norm_text,
    compile_growth_windows,
    monthly_growth_period,
)


@dataclass(frozen=True)
class CertifiedGrowthPlan:
    intent: GrowthIntent
    comparison: Literal["previous_full_month", "previous_month_same_days"]
    windows: GrowthWindows
    filters: tuple[str, ...] = ()

    def proposal(self):
        return {
            "intent": "revenue_growth",
            "period": self.intent.period,
            "year": self.intent.year,
            "month": self.intent.month,
            "comparison": self.comparison,
            "filters": list(self.filters),
        }


# Finite grammar; every word must participate. No arbitrary suffix stripping,
# keyword-only admission, fuzzy rewriting or condition denylist.
_PREFIX = r"(?:(?:cho minh hoi|cho hoi|xem giup|coi gium|check gium|e)\s+)?"
_SUFFIX = r"(?:\s+(?:a|nhi|ta|vay|ha))?"
_METRIC = r"(?:doanh thu|dthu)"
_PERIOD = r"tu dau thang nay den het (?:hom nay|hnay|bua nay)"
_PERIOD_METRIC = rf"(?:{_PERIOD},? {_METRIC}|{_METRIC} {_PERIOD})"
_AMOUNT = r"(?:bao nhieu|bao nhiu|nhiu|nhieu|bnhiu|bn|may) (?:%|phan tram)"
_CHANGE = rf"(?:tang hay giam|thay doi) {_AMOUNT}"
_COMPARISON = r"cung ky (?:thang|th) (?:truoc|trc|roi)(?: do)?"
_SO = r"so (?:voi|vs)"
_MTD = tuple(
    re.compile(_PREFIX + body + _SUFFIX)
    for body in (
        rf"{_PERIOD_METRIC} {_CHANGE} {_SO} {_COMPARISON}",
        rf"{_SO} {_COMPARISON},? {_METRIC} {_PERIOD} {_CHANGE}",
        rf"{_PERIOD_METRIC} hon hay kem {_COMPARISON} {_AMOUNT}",
    )
)


def certify_growth_question(
    question: str, as_of_date: str
) -> CertifiedGrowthPlan | None:
    if not isinstance(question, str):
        return None
    try:
        anchor = date.fromisoformat(as_of_date)
        full_month = monthly_growth_period(question, as_of_date)
        if full_month is not None:
            intent = GrowthIntent("full_month", *full_month)
            comparison = "previous_full_month"
        elif any(pattern.fullmatch(norm_text(question)) for pattern in _MTD):
            intent = GrowthIntent("month_to_date", anchor.year, anchor.month)
            comparison = "previous_month_same_days"
        else:
            return None
        return CertifiedGrowthPlan(
            intent, comparison, compile_growth_windows(intent, as_of_date)
        )
    except TypeError, ValueError:
        return None


def _unique_keys(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("Duplicate proposal key")
        value[key] = item
    return value


def validate_growth_proposal(
    raw_json: str, question: str, as_of_date: str
) -> CertifiedGrowthPlan | None:
    """Admit only exact agreement with a separately certified whole question.

    A refusal here is not evidence that the question is unanswerable. An agreed
    plan still needs the compiler's data/metric guards before any answer exists.
    """
    try:
        value = json.loads(raw_json, object_pairs_hook=_unique_keys)
        keys = {"intent", "period", "year", "month", "comparison", "filters"}
        if not isinstance(value, dict) or set(value) != keys:
            return None
        if type(value["year"]) is not int or type(value["month"]) is not int:
            return None
        if not isinstance(value["filters"], list) or value["filters"]:
            return None
        plan = certify_growth_question(question, as_of_date)
        return plan if plan is not None and value == plan.proposal() else None
    except TypeError, ValueError:
        return None
