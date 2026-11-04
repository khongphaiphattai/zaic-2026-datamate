"""Opt-in, compiled month-over-month revenue for documented single-table sales."""
import calendar
from dataclasses import dataclass
from datetime import date
import math
from pathlib import Path
import re
import unicodedata
from typing import Literal

import pandas as pd

from zaic_2026_datamate.data_loader import get_base_table_stem, normalize_identifier
from zaic_2026_datamate.intent_parser import find_primary_sales_table, find_revenue_column
from zaic_2026_datamate.revenue_metric import verify_revenue_metric
from zaic_2026_datamate.sales_status import STATUS_ALIASES, classify_sales_status, canonical_status_clause


def _text(value):
    text = unicodedata.normalize('NFD', value.lower().replace('đ', 'd'))
    return ' '.join(''.join(c for c in text if not unicodedata.combining(c)).strip(' ?!.').split())


_PREFIX = r'(?:(?:e|cho minh hoi|cho hoi|xem giup|check gium|coi gium)\s+)?'
_SUFFIX = r'(?:\s+(?:a|nhi|ta|z|vay|ha))?'
_MONTH = r'thang (?P<m>\d{1,2})(?:/| nam )(?P<y>\d{4})'
_METRIC = r'(?:doanh thu|dthu)'
_AMOUNT = r'(?:bao nhieu|bao nhiu|bnhiu|bn|may)'
_CHANGE = rf'(?:tang hay giam|thay doi) {_AMOUNT} (?:%|phan tram)'
_COMPARE = r'so (?:voi|vs) thang (?:lien )?(?:truoc|trc)(?: do)?'
_PATTERNS = tuple(re.compile(_PREFIX+p+_SUFFIX) for p in [
    rf'{_METRIC} {_MONTH} {_CHANGE} {_COMPARE}',
    rf'{_COMPARE},? {_METRIC} {_MONTH} {_CHANGE}',
    rf'{_MONTH} {_METRIC} {_CHANGE} {_COMPARE}',
    rf'{_METRIC} {_MONTH} hon hay kem thang (?:truoc|trc) {_AMOUNT} (?:%|phan tram)',
])
_STATUSES = set(STATUS_ALIASES)


@dataclass(frozen=True)
class GrowthIntent:
    """Trusted execution request; does not validate semantics of a raw question."""
    period: Literal['full_month', 'month_to_date']
    year: int
    month: int


@dataclass(frozen=True)
class GrowthWindows:
    previous_start: date
    previous_end: date
    current_start: date
    current_end: date


def compile_growth_windows(intent: GrowthIntent, as_of_date: str) -> GrowthWindows:
    anchor = date.fromisoformat(as_of_date)
    if not isinstance(intent, GrowthIntent) or intent.period not in ('full_month', 'month_to_date'):
        raise ValueError('Unsupported growth intent')
    y, m = intent.year, intent.month
    if type(y) is not int or type(m) is not int or not 1901 <= y <= 2099 or not 1 <= m <= 12:
        raise ValueError('Invalid year/month')
    py, pm = (y, m-1) if m > 1 else (y-1, 12)
    previous_last = calendar.monthrange(py, pm)[1]
    if intent.period == 'month_to_date':
        if (y, m) != (anchor.year, anchor.month):
            raise ValueError('MTD must use anchor month')
        end = anchor
        previous_end = date(py, pm, min(anchor.day, previous_last))
    else:
        end = date(y, m, calendar.monthrange(y, m)[1])
        if end > anchor:
            raise ValueError('Month is not complete')
        previous_end = date(py, pm, previous_last)
    return GrowthWindows(date(py, pm, 1), previous_end, date(y, m, 1), end)


def monthly_growth_period(question, as_of_date):
    try:
        anchor = date.fromisoformat(as_of_date)
        match = next((m for p in _PATTERNS if (m := p.fullmatch(_text(question)))), None)
        if match is None:
            return None
        y, m = int(match['y']), int(match['m'])
        if not 1901 <= y <= 2099 or not 1 <= m <= 12:
            return None
        if date(y, m, calendar.monthrange(y, m)[1]) > anchor:
            return None
        return y, m
    except (TypeError, ValueError):
        return None


def _documented_columns(context, table):
    selected = False
    definitions = {}
    for line in context.readme.splitlines():
        if re.match(r'^#{1,6}\s', line):
            heading = re.match(r'^#{1,6}\s+Cột trong\s+`([^`]+)`', line, re.I)
            selected = bool(heading and normalize_identifier(get_base_table_stem(Path(heading[1]).stem)) == table)
        entry = re.match(r'^\s*-\s+\*\*([^*]+)\*\*\s*:\s*(.+)$', line)
        if selected and entry:
            definitions.setdefault(normalize_identifier(entry[1]), set()).add(_text(entry[2]))
    return definitions


def resolve_monthly_growth(question, context, as_of_date, audit=None):
    period = monthly_growth_period(question, as_of_date)
    if period is None:
        return 'abstain', None, 'Monthly growth grammar/date not supported'
    y, m = period
    return resolve_growth_intent(GrowthIntent('full_month', y, m), context, as_of_date, audit)


def resolve_growth_intent(intent: GrowthIntent, context, as_of_date: str, audit=None):
    """Execute a trusted intent; raw/model questions need a separate semantic gate."""
    audit = audit if audit is not None else {}
    try:
        windows = compile_growth_windows(intent, as_of_date)
    except (TypeError, ValueError) as exc:
        audit['rejection'] = str(exc)
        return 'abstain', None, f'Growth intent rejected: {exc}'
    start, boundary, end = windows.previous_start, windows.current_start, windows.current_end
    audit.update(previous_start=start.isoformat(), previous_end=windows.previous_end.isoformat(),
                 current_start=boundary.isoformat(), current_end=end.isoformat(), period=intent.period)
    try:
        table, order, day, status = find_primary_sales_table(context)
        revenue = find_revenue_column(context, table) if table else None
        if not all([table, order, day, status, revenue]):
            raise ValueError('Missing sales columns')
        definitions = _documented_columns(context, table)
        for column, meaning in [(order, 'ma don'), (day, 'ngay ban'), (status, 'trang thai don')]:
            if definitions.get(column) != {meaning}:
                raise ValueError(f'Unverified README role: {column}')
        quote = lambda identifier: '"'+identifier.replace('"', '""')+'"'
        qt, qo, qd, qs, qr = map(quote, [table, order, day, status, revenue])
        metric = verify_revenue_metric(context, f'SELECT SUM({qr}) FROM {qt}')
        if not metric.allowed:
            raise ValueError(metric.reason)
        frame = context.tables[table]
        if frame[[order, day, status, revenue]].isna().any().any() or frame[order].astype(str).str.strip().eq('').any():
            raise ValueError('Missing order/date/status/revenue values')
        dates = pd.to_datetime(frame[day], errors='coerce').dt.normalize()
        money = pd.to_numeric(frame[revenue], errors='coerce')
        statuses = frame[status].astype(str).map(_text)
        if dates.isna().any() or not money.map(lambda v: math.isfinite(v) and v >= 0).all():
            raise ValueError('Invalid date or revenue values')
        if frame[status].map(classify_sales_status).isna().any():
            raise ValueError('Unsupported sales status')
        contracts = pd.DataFrame({'order':frame[order], 'day':dates, 'status':statuses})
        if (contracts.groupby('order')[['day', 'status']].nunique() > 1).any().any():
            raise ValueError('Conflicting dates/statuses within one order')
        if dates.min().date() > start or dates.max().date() < end:
            reason = ('Data does not cover both complete month boundaries' if intent.period == 'full_month'
                      else 'Data does not cover both MTD window boundaries')
            raise ValueError(reason)
        status_clause = canonical_status_clause(qs, qo, qt, frame[status].unique())
        totals = []
        for first, last in [(start, windows.previous_end), (boundary, end)]:
            sql = f"SELECT SUM({qr}) FROM {qt} WHERE TRY_CAST({qd} AS DATE) BETWEEN '{first}' AND '{last}' {status_clause}"
            value = context.query(sql).iloc[0, 0]
            if value is None or not math.isfinite(float(value)) or value <= 0:
                raise ValueError('Both monthly revenues must be positive and finite')
            totals.append(float(value))
        value = round((totals[1]-totals[0])*100/totals[0], 2)
        if not math.isfinite(value):
            raise ValueError('Non-finite growth')
        audit.update(table=table, revenue_column=revenue, previous_revenue=totals[0], current_revenue=totals[1],
                     coverage='boundary/schema checks; internal export completeness is not proven')
        return 'percent', value, None
    except Exception as exc:
        audit['rejection'] = str(exc)
        return 'abstain', None, f'Monthly growth rejected: {exc}'


class MonthlyGrowthAgent:
    """Preserve every existing answer, then try the compiled monthly capability."""
    def __init__(self, base):
        self.base = base
        self.last_monthly_growth = {}

    @property
    def slow_engine(self):
        return self.base.slow_engine

    @property
    def last_canonical_question(self):
        return getattr(self.base, 'last_canonical_question', None)

    def answer_query(self, question, context, as_of_date=None):
        self.last_monthly_growth = {}
        original = self.base.answer_query(question, context, as_of_date)
        if original[0] != 'abstain' or monthly_growth_period(question, as_of_date) is None:
            return original
        kind, value, reason = resolve_monthly_growth(question, context, as_of_date, self.last_monthly_growth)
        return (kind, value, reason, 'monthly_growth_path' if kind == 'percent' else 'abstain')
