import json, re
from collections import defaultdict
from zaic_2026_datamate.data_loader import DataLoader
from zaic_2026_datamate.intent_parser import find_primary_sales_table

qm = {}
for l in open('data/public_test/public_test/questions.jsonl'):
    d = json.loads(l); qm[d['id']] = d
sub = [json.loads(l) for l in open('submission.jsonl')]
ld = DataLoader('data/public_test/public_test/data', 'data/public_test/public_test/calendar.csv')
info = {}
byb = defaultdict(list)
for s in sub:
    if s['type'] == 'abstain': continue
    q = qm[s['id']]['question'].lower()
    if re.search(r'(đơn|don|đh|dh|bill)\b', q) and re.search(r'bao nhi|bnhiu|mấy|may|huỷ|hủy|huy|huỹ', q):
        byb[qm[s['id']]['business_id']].append((s['id'], q, s['type'], s.get('answer')))
for b, items in sorted(byb.items()):
    ctx = ld.load_business(b)
    t, o, d, st = find_primary_sales_table(ctx)
    n = ctx.tables[t].shape[0] if t else None
    nu = ctx.tables[t][o].nunique() if t and o else None
    print(f'== {b} table={t} order_col={o} date={d} status={st} rows={n} uniq_orders={nu} answered={len(items)}')
    for i in items: print('   ', i[0], i[2], i[3], '|', i[1][:90])
