"""Conservative syntax allowlist; eligibility does not imply answer correctness."""
from datetime import date, timedelta
import re
import unicodedata


def daily_revenue_date(question, as_of_date):
    try:
        anchor = date.fromisoformat(as_of_date)
    except (TypeError, ValueError):
        return None
    q = unicodedata.normalize('NFD', question.lower()).replace('đ','d')
    q = ''.join(c for c in q if unicodedata.category(c) != 'Mn')
    q = ' '.join(q.strip(' ?!.').split())
    period = r'(?:hom nay|hnay|bua nay|hom qua|hqua|ngay \d{1,2}/\d{1,2}/\d{4})'
    metric = r'(?:tong )?(?:doanh thu|dthu|tien ban)(?: (?:cua )?(?:shop|quan|tiem|cua hang))?'
    period = rf'(?:trong ngay )?{period}'
    suffix = r'(?: (?:la |duoc )?(?:bao nhi(?:eu|u)(?: tien)?|bn|bnhiu))?'
    m = re.fullmatch(rf'(?:({period}) {metric}|{metric} ({period})){suffix}(?: ha|a|nhi)?',q)
    if not m:
        return None
    token = (m.group(1) or m.group(2)).removeprefix("trong ngay ")
    if token in ('hom nay','hnay','bua nay'):
        day = anchor
    elif token in ('hom qua','hqua'):
        day = anchor-timedelta(days=1)
    else:
        try:
            d,mo,y = map(int,token.removeprefix('ngay ').split('/'))
            day = date(y,mo,d)
        except ValueError:
            return None
    return day.isoformat() if day <= anchor else None
