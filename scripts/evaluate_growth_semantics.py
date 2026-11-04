"""Offline raw-question shadow evaluation; no annotated intent used for execution."""
import argparse
from collections import Counter
import json
from pathlib import Path

from scripts.audit_growth_feasibility import sha, validate_cases, write_json
from zaic_2026_datamate.data_loader import DataLoader
from zaic_2026_datamate.evaluation import evaluate_match
from zaic_2026_datamate.growth_semantics import certify_growth_question, validate_growth_proposal
from zaic_2026_datamate.monthly_growth import resolve_growth_intent


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir',required=True)
    args=parser.parse_args();out=Path(args.output_dir)
    if out.exists():raise FileExistsError('Use a fresh output directory')
    root=Path('artifacts/growth-feasibility')
    manifest=json.loads((root/'manifest.json').read_text())
    cases=json.loads((root/'cases.json').read_text())
    if sha(root/'cases.json')!=manifest['cases_sha256'] or sha(manifest['baseline_trace'])!=manifest['baseline_trace_sha256']:
        raise ValueError('Frozen inputs changed')
    baseline_rows=[json.loads(line) for line in Path(manifest['baseline_trace']).open()]
    baseline={r['id']:r for r in baseline_rows}
    if len(baseline)!=len(baseline_rows):raise ValueError('Duplicate baseline IDs')
    split=json.loads(Path('artifacts/selective-evaluation/split.json').read_text())
    validate_cases(cases,baseline,set(split['train_businesses']))
    controls_path=Path('artifacts/growth-semantic-gate/controls.json')
    controls_manifest=json.loads(controls_path.with_name('controls-manifest.json').read_text())
    if sha(controls_path)!=controls_manifest['sha256']:raise ValueError('Frozen controls changed')
    out.mkdir(parents=True)
    sources=[Path(__file__),Path('scripts/audit_growth_feasibility.py'),root/'cases.json',root/'manifest.json',
             Path(manifest['baseline_trace']),controls_path,controls_path.with_name('controls-manifest.json'),
             Path('artifacts/structured-intent-probe/cases.json'),Path('artifacts/selective-evaluation/split.json')]
    sources += [Path('src/zaic_2026_datamate')/name for name in ('growth_semantics.py','monthly_growth.py',
                'intent_parser.py','revenue_metric.py','data_loader.py','evaluation.py')]
    frozen={str(p):sha(p) for p in sources}
    write_json(out/'config.json',{'sources':frozen,'scope':'Raw question deterministic shadow; no model, no production integration.'})
    controls=[]
    for c in json.loads(controls_path.read_text()):
        plan=certify_growth_question(c['question'],c['as_of_date'])
        proposal=plan.proposal() if plan else None
        forged_or_valid=c.get('forged_proposal',c['expected'])
        checked=validate_growth_proposal(json.dumps(forged_or_valid),c['question'],c['as_of_date'])
        controls.append({'id':c['id'],'actual':proposal,'expected':c['expected'],
                         'exact':proposal==c['expected'],'proposal_admitted':checked is not None})
    old_probe=[]
    for c in json.loads(Path('artifacts/structured-intent-probe/cases.json').read_text()):
        plan=certify_growth_question(c['question'],c['as_of_date'])
        projected={k:v for k,v in plan.proposal().items() if k not in ('comparison','filters')} if plan else {'intent':'abstain'}
        old_probe.append({'id':c['id'],'actual':projected,'expected':c['expected'],
                          'exact':projected==c['expected'],
                          'false_accept':c['expected']['intent']=='abstain' and plan is not None})
    loader=DataLoader('data/train/train/data','data/train/train/calendar.csv')
    results=[]
    for bid in sorted({c['business_id'] for c in cases}):
        # Only raw question/date enter certification; labels are used below for measurement.
        decisions=[(c,certify_growth_question(c['question'],c['as_of_date'])) for c in cases if c['business_id']==bid]
        context=None
        try:
            if any(plan is not None for _,plan in decisions):context=loader.load_business(bid)
            for c,plan in decisions:
                audit={}
                if plan is not None:
                    kind,value,reason=resolve_growth_intent(plan.intent,context,c['as_of_date'],audit)
                else:kind,value,reason='abstain',None,'Question semantics unverified'
                projected={k:v for k,v in plan.proposal().items() if k not in ('comparison','filters')} if plan else None
                target=baseline[c['id']]['target']  # only after execution
                results.append({'id':c['id'],'business_id':bid,'question':c['question'],'as_of_date':c['as_of_date'],
                                'category':c['category'],'plan':plan.proposal() if plan else None,
                                'intent_matches_annotation':projected==c['intent'] if plan else None,
                                'semantic_false_accept':plan is not None and projected!=c['intent'],
                                'type':kind,'answer':value,'reason':reason,'audit':audit,
                                'correct':None if kind=='abstain' else evaluate_match(kind,value,target['type'],target['answer'])})
        finally:
            if context is not None:context.duckdb_conn.close()
        print(json.dumps({'business':bid,'done':len(results)}),flush=True)
    if any(sha(p)!=digest for p,digest in frozen.items()):raise ValueError('Source changed during run')
    report={'total':len(results),'certified':sum(r['plan'] is not None for r in results),
            'semantic_false_accepts':sum(r['semantic_false_accept'] for r in results),
            'correct':sum(r['correct'] is True for r in results),'wrong':sum(r['correct'] is False for r in results),
            'abstain':sum(r['type']=='abstain' for r in results),
            'categories':{cat:{'total':len(rows),'certified':sum(r['plan'] is not None for r in rows),
                              'answered':sum(r['type']!='abstain' for r in rows)}
                          for cat in sorted({r['category'] for r in results})
                          if (rows:=[r for r in results if r['category']==cat])},
            'answered_businesses':dict(Counter(r['business_id'] for r in results if r['type']!='abstain')),
            'synthetic_controls':{'total':len(controls),'exact':sum(c['exact'] for c in controls),
                                  'negative_false_accepts':sum(c['expected'] is None and c['proposal_admitted'] for c in controls)},
            'old_probe':{'positive_exact':sum(r['exact'] for r in old_probe if r['expected']['intent']!='abstain'),
                         'positive_total':6,'negative_exact':sum(r['exact'] for r in old_probe if r['expected']['intent']=='abstain'),
                         'negative_total':8,'false_accepts':sum(r['false_accept'] for r in old_probe)},
            'model_calls':0,'production_promotion':False,
            'scope':'Selected/tuned Train-24 development shadow. Deterministic only; not model benefit or Public score.'}
    for name,value in [('results',results),('controls',controls),('old-probe',old_probe),('report',report)]:
        write_json(out/f'{name}.json',value)
    print(json.dumps(report,ensure_ascii=False),flush=True)


if __name__=='__main__':main()
