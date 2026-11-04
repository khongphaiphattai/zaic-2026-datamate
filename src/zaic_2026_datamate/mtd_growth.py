"""Opt-in certified MTD fallback; preserve every preexisting answer."""
from zaic_2026_datamate.growth_semantics import certify_growth_question
from zaic_2026_datamate.monthly_growth import resolve_growth_intent


class MTDGrowthAgent:
    def __init__(self, base):
        self.base = base
        self.last_mtd_growth = {}

    @property
    def slow_engine(self):
        return self.base.slow_engine

    @property
    def last_canonical_question(self):
        return getattr(self.base, 'last_canonical_question', None)

    @property
    def last_monthly_growth(self):
        return getattr(self.base, 'last_monthly_growth', {})

    def answer_query(self, question, context, as_of_date=None):
        self.last_mtd_growth = {}
        original = self.base.answer_query(question, context, as_of_date)
        if original[0] != 'abstain':
            return original
        plan = certify_growth_question(question, as_of_date)
        if plan is None or plan.intent.period != 'month_to_date':
            return original
        kind, value, reason = resolve_growth_intent(plan.intent, context, as_of_date, self.last_mtd_growth)
        return (kind, value, reason, 'mtd_growth_path') if kind == 'percent' else original
