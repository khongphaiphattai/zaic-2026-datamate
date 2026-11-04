"""Evaluate trusted MTD annotations and replay full-month calls, entirely offline.

Run from repo root: python -m scripts.evaluate_mtd_compiler --output-dir FRESH
"""
import argparse
from collections import Counter
import json
from pathlib import Path

from scripts.audit_growth_feasibility import sha, validate_cases, write_json
from zaic_2026_datamate.data_loader import DataLoader
from zaic_2026_datamate.evaluation import evaluate_match
from zaic_2026_datamate.intent_probe import parse_intent
from zaic_2026_datamate.monthly_growth import (
    GrowthIntent, monthly_growth_period, resolve_growth_intent, resolve_monthly_growth,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir',required=True)
    args = parser.parse_args()
    out = Path(args.output_dir)
    if out.exists():
        raise FileExistsError('Use a fresh output directory')
    root = Path('artifacts/growth-feasibility')
    manifest = json.loads((root/'manifest.json').read_text())
    if sha(root/'cases.json') != manifest['cases_sha256'] or sha(manifest['baseline_trace']) != manifest['baseline_trace_sha256']:
        raise ValueError('Frozen annotation/baseline mismatch')
    cases = json.loads((root/'cases.json').read_text())
    baseline_rows = [json.loads(line) for line in Path(manifest['baseline_trace']).open()]
    baseline = {r['id']:r for r in baseline_rows}
    if len(baseline) != len(baseline_rows):
        raise ValueError('Duplicate baseline IDs')
    split = json.loads(Path('artifacts/selective-evaluation/split.json').read_text())
    validate_cases(cases,baseline,set(split['train_businesses']))
    mtd = [c for c in cases if c['category']=='month_to_date']
    # Validate every intent before loading data or consulting numeric targets.
    for c in mtd:
        parse_intent(json.dumps(c['intent']),c['as_of_date'])
    prior = {r['id']:r for r in json.loads((root/'run-01/results.json').read_text())}
    if any(any(prior[c['id']][k]!=c[k] for k in ('question','business_id','as_of_date','intent')) for c in mtd):
        raise ValueError('Prior readiness evidence does not match annotations')
    full_path = Path('artifacts/mtd-compiler/full-month-baseline.json')
    full = json.loads(full_path.read_text())
    fields = ('id','business_id','question','as_of_date','type','answer','reason','route')
    expected = [{k:r.get(k) for k in fields} for r in baseline_rows
                if r['route'] in ('monthly_growth_path','abstain')
                and monthly_growth_period(r['question'],r['as_of_date']) is not None]
    if full != expected:
        raise ValueError('Frozen full-month call set differs from baseline')
    out.mkdir(parents=True)
    sources = [Path(__file__),root/'cases.json',root/'manifest.json',root/'run-01/results.json',full_path,
               Path(manifest['baseline_trace']),Path('artifacts/selective-evaluation/split.json'),
               Path('scripts/audit_growth_feasibility.py')]
    sources += [Path('src/zaic_2026_datamate')/name for name in
                ('monthly_growth.py','revenue_metric.py','intent_parser.py','intent_probe.py','data_loader.py','evaluation.py')]
    frozen = {str(p):sha(p) for p in sources}
    write_json(out/'config.json',{'sources':frozen,'network_calls':0,
                                'scope':'Trusted question annotations, not model/automatic inference. No Public evaluation.'})
    loader = DataLoader('data/train/train/data','data/train/train/calendar.csv')
    results, regression = [], []
    for bid in sorted({c['business_id'] for c in mtd+full}):
        context = loader.load_business(bid)
        try:
            for c in (c for c in mtd if c['business_id']==bid):
                request = c['intent']
                intent = GrowthIntent(request['period'],request['year'],request['month'])
                audit = {}
                kind,value,reason = resolve_growth_intent(intent,context,c['as_of_date'],audit)
                target = baseline[c['id']]['target']  # scoring only, after execution
                results.append({**c,'type':kind,'answer':value,'reason':reason,'audit':audit,
                                'correct':None if kind=='abstain' else evaluate_match(kind,value,target['type'],target['answer']),
                                'previously_ready':prior[c['id']]['stage']=='mtd_compiler_missing'})
            for c in (c for c in full if c['business_id']==bid):
                audit = {}
                kind,value,reason = resolve_monthly_growth(c['question'],context,c['as_of_date'],audit)
                regression.append({'id':c['id'],'business_id':bid,'type':kind,'answer':value,'reason':reason,'audit':audit,
                                   'matches_baseline':(kind,value,reason)==(c['type'],c['answer'],c['reason'])})
        finally:
            context.duckdb_conn.close()
        print(json.dumps({'business':bid,'mtd_done':len(results),'full_month_done':len(regression)}),flush=True)
    # A run cannot claim results under a source that changed while it executed.
    if any(sha(p)!=digest for p,digest in frozen.items()):
        raise ValueError('Source changed during evaluation; results not finalized')
    def summary(rows):
        return {'total':len(rows),'correct':sum(r['correct'] is True for r in rows),
                'wrong':sum(r['correct'] is False for r in rows),
                'abstain':sum(r['type']=='abstain' for r in rows)}
    report = {'mtd':summary(results),'previously_ready':summary([r for r in results if r['previously_ready']]),
              'previously_blocked':summary([r for r in results if not r['previously_ready']]),
              'businesses':{bid:summary([r for r in results if r['business_id']==bid]) for bid in sorted({r['business_id'] for r in results})},
              'rejections':dict(Counter(r['reason'] for r in results if r['type']=='abstain')),
              'full_month_regression':{'total':len(regression),'changed':sum(not r['matches_baseline'] for r in regression)},
              'network_calls':0,'production_promotion':False,
              'scope':'Selected Train-24 development MTD with question-labelled intents. Not automatic coverage, unseen validation or Public gain.'}
    write_json(out/'mtd-results.json',results)
    write_json(out/'full-month-results.json',regression)
    write_json(out/'report.json',report)
    print(json.dumps(report,ensure_ascii=False),flush=True)


if __name__=='__main__':
    main()
