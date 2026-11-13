"""Offline, question-labelled diagnosis; not an inference or submission entrypoint."""
import argparse
from collections import Counter
from datetime import date
import hashlib
import json
import math
from pathlib import Path

import pandas as pd

from zaic_2026_datamate.data_loader import DataLoader
from zaic_2026_datamate.evaluation import evaluate_match
from zaic_2026_datamate.intent_parser import find_primary_sales_table, find_revenue_column
from zaic_2026_datamate.intent_probe import parse_intent
from zaic_2026_datamate.monthly_growth import (
    _documented_columns, _STATUSES, norm_text, resolve_monthly_growth,
)
from zaic_2026_datamate.revenue_metric import verify_revenue_metric


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n')


def validate_cases(cases, baseline, train_businesses):
    allowed = {'id','business_id','question','as_of_date','category','intent','annotation_scope'}
    categories = {'full_month','month_to_date','share','filtered','filtered_amount',
                  'year_over_year','boolean_multimonth','seasonal','unclear'}
    if not cases or len({c['id'] for c in cases}) != len(cases):
        raise ValueError('Empty or duplicate annotation IDs')
    for c in cases:
        row = baseline.get(c['id'], {})
        if set(c) != allowed or c['category'] not in categories:
            raise ValueError('Unexpected annotation fields/category; numeric targets are forbidden')
        if (c['business_id'] not in train_businesses or row.get('partition') != 'sft_seen_24'
                or row.get('type') != 'abstain'
                or any(c[k] != row.get(k) for k in ('id','business_id','question','as_of_date'))):
            raise ValueError('Annotation does not match an abstaining Train-24 question')
        if c['category'] in ('full_month','month_to_date'):
            if not isinstance(c['intent'],dict) or c['intent'].get('period') != c['category']:
                raise ValueError('Annotated period/category mismatch')
        elif c['intent'] is not None:
            raise ValueError('Unsupported category must not carry an executable intent')


def check_data(frame, order, day, status, revenue):
    """Mirror current guard requirements, without silently accepting unknown data."""
    columns = [order,day,status,revenue]
    if frame.empty or any(c not in frame.columns for c in columns):
        return {'valid':False,'reason':'Empty data or missing columns','min_date':None,'max_date':None}
    dates = pd.to_datetime(frame[day],errors='coerce').dt.normalize()
    money = pd.to_numeric(frame[revenue],errors='coerce')
    statuses = frame[status].astype(str).map(norm_text)
    contracts = pd.DataFrame({'order':frame[order],'day':dates,'status':statuses})
    issues = {
        'null_values':int(frame[columns].isna().sum().sum()),
        'blank_order_ids':int(frame[order].astype(str).str.strip().eq('').sum()),
        'invalid_dates':int(dates.isna().sum()),
        'negative_or_nonfinite_money':int((~money.map(lambda v: math.isfinite(v) and v >= 0)).sum()),
        'unsupported_statuses':sorted(set(statuses)-_STATUSES),
        'conflicting_orders':int((contracts.groupby('order')[['day','status']].nunique()>1).any(axis=1).sum()),
    }
    return {'valid':not any(issues.values()),'issues':issues,
            'min_date':None if dates.isna().all() else dates.min().date().isoformat(),
            'max_date':None if dates.isna().all() else dates.max().date().isoformat()}


def inspect_context(context):
    table,order,day,status = find_primary_sales_table(context)
    revenue = find_revenue_column(context,table) if table else None
    definitions = _documented_columns(context,table) if table else {}
    roles = {role:{'selected':col,'meanings':sorted(definitions.get(col,set())),
                   'verified':definitions.get(col)=={meaning},
                   'readme_candidates':sorted(k for k,v in definitions.items()
                                              if v=={meaning} and k in context.tables[table].columns)}
             for role,col,meaning in [('order',order,'ma don'),('date',day,'ngay ban'),('status',status,'trang thai don')]}
    metric = {'allowed':False,'reason':'Missing table/revenue column'}
    if table and revenue:
        quote = lambda s:'"'+s.replace('"','""')+'"'
        checked = verify_revenue_metric(context,f'SELECT SUM({quote(revenue)}) FROM {quote(table)}')
        metric = {'allowed':checked.allowed,'reason':checked.reason}
    data = check_data(context.tables[table],order,day,status,revenue) if table else {
        'valid':False,'reason':'No selected table','min_date':None,'max_date':None}
    return {'table':table,'revenue_column':revenue,'roles':roles,
            'roles_verified':all(r['verified'] for r in roles.values()),'metric':metric,'data':data,
            'readme_overview':'\n'.join(context.readme.splitlines()[:4]),
            'readme_sha256':hashlib.sha256(context.readme.encode()).hexdigest(),
            'scope':'Selected existing resolver only. Candidates are evidence, not automatic replacements. Data observations on an unverified role are not a semantic readiness claim.'}


def diagnose_case(case, context, profile):
    row = {**case,'stage':'outside_capability','answer':None,'answer_type':None,'correct':None}
    if case['intent'] is None:
        return row
    try:
        intent = parse_intent(json.dumps(case['intent']),case['as_of_date'])
    except ValueError as exc:
        return row | {'stage':'date_rejected','reason':str(exc)}
    if not profile['roles_verified']:
        return row | {'stage':'role_unverified'}
    if not profile['metric']['allowed']:
        return row | {'stage':'metric_unverified'}
    if not profile['data']['valid']:
        return row | {'stage':'data_rejected'}
    if intent['period']=='month_to_date':
        anchor = date.fromisoformat(case['as_of_date'])
        start = date(anchor.year if anchor.month>1 else anchor.year-1,
                     anchor.month-1 if anchor.month>1 else 12,1)
        data = profile['data']
        if data['min_date']>start.isoformat() or data['max_date']<anchor.isoformat():
            return row | {'stage':'boundary_rejected'}
        # No full-month proxy SQL, no invented MTD result or correctness label.
        return row | {'stage':'mtd_compiler_missing',
                      'reason':'Roles/metric/common data/boundaries passed; MTD revenue and formula not executed.'}
    intent_question = (f"Doanh thu tháng {intent['month']}/{intent['year']} tăng hay giảm "
                       'bao nhiêu phần trăm so với tháng trước?')
    audit = {}
    kind,value,reason = resolve_monthly_growth(intent_question,context,case['as_of_date'],audit)
    return row | {'stage':'full_month_computed' if kind=='percent' else 'full_month_execution_rejected',
                  'answer_type':kind,'answer':value,'reason':reason,'execution_audit':audit,
                  'normalized_question':intent_question}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--annotations',default='artifacts/growth-feasibility/cases.json')
    parser.add_argument('--manifest',default='artifacts/growth-feasibility/manifest.json')
    parser.add_argument('--output-dir',required=True)
    args = parser.parse_args()
    out = Path(args.output_dir)
    if out.exists():
        raise FileExistsError('Use a fresh output directory')
    manifest = json.loads(Path(args.manifest).read_text())
    if sha(args.annotations)!=manifest['cases_sha256'] or sha(manifest['baseline_trace'])!=manifest['baseline_trace_sha256']:
        raise ValueError('Frozen annotations/baseline hash mismatch')
    cases = json.loads(Path(args.annotations).read_text())
    baseline_rows = [json.loads(line) for line in Path(manifest['baseline_trace']).open()]
    baseline = {r['id']:r for r in baseline_rows}
    if len(baseline)!=len(baseline_rows):
        raise ValueError('Duplicate baseline IDs')
    split_path = Path('artifacts/selective-evaluation/split.json')
    split = json.loads(split_path.read_text())
    validate_cases(cases,baseline,set(split['train_businesses']))
    out.mkdir(parents=True)
    loader = DataLoader('data/train/train/data','data/train/train/calendar.csv')
    profiles, results = {}, []
    for bid in sorted({c['business_id'] for c in cases}):
        subset = [c for c in cases if c['business_id']==bid]
        context = None
        try:
            if any(c['intent'] is not None for c in subset):
                context = loader.load_business(bid)
                profiles[bid] = inspect_context(context)
            for c in subset:
                row = diagnose_case(c,context,profiles.get(bid))
                # Targets used exclusively to score a completed diagnostic computation.
                if row['stage']=='full_month_computed':
                    target = baseline[c['id']]['target']
                    row['correct'] = evaluate_match(row['answer_type'],row['answer'],target['type'],target['answer'])
                results.append(row)
            print(json.dumps({'business':bid,'stages':dict(Counter(r['stage'] for r in results if r['business_id']==bid))}),flush=True)
        finally:
            if context is not None:
                context.duckdb_conn.close()
    results_by_id = {r['id']:r for r in results}
    results = [results_by_id[c['id']] for c in cases]
    counts = lambda rows:dict(Counter(r['stage'] for r in rows))
    stage_businesses = {stage:sorted({r['business_id'] for r in results if r['stage']==stage})
                        for stage in counts(results)}
    report = {'total':len(results),'stages':counts(results),'stage_businesses':stage_businesses,
              'categories':{cat:{'total':len(rows),'businesses':len({r['business_id'] for r in rows}),'stages':counts(rows)}
                            for cat in sorted({r['category'] for r in results})
                            if (rows:=[r for r in results if r['category']==cat])},
              'computed_correct':sum(r['correct'] is True for r in results),
              'computed_wrong':sum(r['correct'] is False for r in results),
              'network_calls':0,'production_promotion':False,
              'scope':'Question-labelled Train-24 development diagnosis, not automatic inference/new submission. MTD is not executed. First blocker counts do not exhaust overlapping blockers.',
              'coverage':manifest['coverage']}
    sources = [Path(__file__),split_path,Path(args.annotations),Path(args.manifest),Path(manifest['baseline_trace'])]
    sources += [Path('src/zaic_2026_datamate')/name for name in
                ('monthly_growth.py','intent_parser.py','intent_probe.py','revenue_metric.py','data_loader.py','evaluation.py')]
    write_json(out/'config.json',{'sources':{str(p):sha(p) for p in sources},'manifest':manifest})
    write_json(out/'profiles.json',profiles)
    write_json(out/'results.json',results)
    write_json(out/'report.json',report)
    print(json.dumps(report,ensure_ascii=False),flush=True)


if __name__=='__main__':
    main()
