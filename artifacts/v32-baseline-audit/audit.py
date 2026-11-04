"""Read-only frozen answer audit; run from repo root with .venv/bin/python."""
import collections
import hashlib
import json
import re
from pathlib import Path
from zaic_2026_datamate.data_loader import DataLoader

OUT = Path(__file__).parent
TRACE = Path('artifacts/v31-status-public/trace.jsonl')
rows = [json.loads(s) for s in TRACE.read_text().splitlines()]
answers = [r for r in rows if r['type'] != 'abstain']
assert len(rows) == 1000 and len(answers) == 205
loader = DataLoader('data/public_test/public_test/data')
records = []
profiles = {}
for bid in sorted({r['business_id'] for r in answers}):
    ctx = loader.load_business(bid)
    selected = [r for r in answers if r['business_id'] == bid]
    profiles[bid] = {'readme': ctx.readme, 'tables': {t: {'rows': len(df), 'columns': list(df.columns)} for t, df in ctx.tables.items() if t != 'calendar'}}
    for r in selected:
        sql = r.get('sql')
        rec = {k: r.get(k) for k in ('id', 'business_id', 'question', 'as_of_date', 'type', 'answer', 'route', 'sql')}
        rec['replay_status'] = 'not_available'
        if sql:
            try:
                result = ctx.query(sql)
                value = result.iloc[0, 0] if result.shape == (1, 1) else None
                rec['replay_value'] = value.item() if hasattr(value, 'item') else value
                if r['route'] == 'mtd_growth_path':
                    audit = r['mtd_growth_audit']
                    previous_sql = sql.replace(audit['current_start'], audit['previous_start']).replace(audit['current_end'], audit['previous_end'])
                    previous = ctx.scalar(previous_sql)
                    rec['current_revenue'] = rec['replay_value']
                    rec['previous_revenue'] = previous
                    rec['replay_value'] = round((rec['current_revenue'] / previous - 1) * 100, 2)
                rec['replay_status'] = 'matches' if rec['replay_value'] == r.get('answer') else 'differs'
            except Exception as exc:
                rec['replay_status'] = 'error'
                rec['replay_error'] = str(exc)
        rec['risk_signals'] = []
        if sql and 'ILIKE' in sql:
            rec['risk_signals'].append('substring_status_policy_requires_review')
        if sql and re.search(r'TRY_CAST\((?:gio|thoi_gian_tao) AS DATE\)', sql):
            rec['risk_signals'].append('time_role_requires_readme_review')
        if sql and 'SUM(' in sql.upper():
            rec['risk_signals'].append('revenue_grain_discount_requires_review')
        if r['type'] == 'unanswerable':
            rec['risk_signals'].append('missing_data_claim_requires_review')
        records.append(rec)
    ctx.duckdb_conn.close()
assert len(records) == 205 and len({r['id'] for r in records}) == 205
summary = {'scope': 'Reproducibility and risk inventory only; Public has no labels. Risk signals are not confirmed wrong answers.', 'trace_sha256': hashlib.sha256(TRACE.read_bytes()).hexdigest(), 'submission_sha256': hashlib.sha256(Path('submission.jsonl').read_bytes()).hexdigest(), 'answers': len(records), 'by_business': dict(collections.Counter(r['business_id'] for r in records)), 'replay': dict(collections.Counter(r['replay_status'] for r in records)), 'risk_signals': dict(collections.Counter(s for r in records for s in r['risk_signals']))}
for name, data in [('answers.json', records), ('profiles.json', profiles), ('summary.json', summary)]:
    (OUT / name).write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str) + '\n')
print(json.dumps(summary, ensure_ascii=False, indent=2))
