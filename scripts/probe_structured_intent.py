"""Bounded, sequential live extraction probe; never executes SQL or writes a submission."""
import argparse
import hashlib
import json
from pathlib import Path
import time

from zaic_2026_datamate.intent_probe import SYSTEM_PROMPT,build_prompt,parse_intent
from zaic_2026_datamate.llm_client import OpenAICompatibleClient
from zaic_2026_datamate.provenance import fetch_model_provenance


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url',required=True)
    parser.add_argument('--cases',default='artifacts/structured-intent-probe/cases.json')
    parser.add_argument('--output-dir',required=True)
    args=parser.parse_args()
    out=Path(args.output_dir)
    if out.exists():raise FileExistsError('Use a fresh output directory')
    source=Path(args.cases).read_bytes()
    cases=json.loads(source)
    if not 1<=len(cases)<=24 or len({c['id'] for c in cases})!=len(cases):
        raise ValueError('Probe must contain 1..24 unique cases')
    for case in cases:
        parse_intent(json.dumps(case['expected']),case['as_of_date'])
        build_prompt(case['question'],case['as_of_date'])
    metadata=fetch_model_provenance(args.base_url,'datamate-business24')
    out.mkdir(parents=True)
    (out/'provenance.json').write_text(json.dumps(metadata,indent=2)+'\n')
    config={'model':'datamate-business24','temperature':0,'seed':42,'max_tokens':256,
            'cases_sha256':hashlib.sha256(source).hexdigest(),
            'system_prompt_sha256':hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest(),
            'extractor_sha256':hashlib.sha256(Path('src/zaic_2026_datamate/intent_probe.py').read_bytes()).hexdigest(),
            'scope':'Selected development probe. Valid JSON is not proof of semantics; no execution/promotion.'}
    (out/'config.json').write_text(json.dumps(config,indent=2)+'\n')
    client=OpenAICompatibleClient(base_url=args.base_url,model='datamate-business24',max_tokens=256)
    results=[]
    with (out/'trace.jsonl').open('w') as trace:
        for case in cases:
            started=time.monotonic()
            row={'id':case['id'],'expected':case['expected'],'parsed':None,'error':None}
            try:
                raw=client.generate(build_prompt(case['question'],case['as_of_date']),SYSTEM_PROMPT)
                row['raw']=raw
                row['parsed']=parse_intent(raw,case['as_of_date'])
            except Exception as exc:row['error']=str(exc)
            row['exact_match']=row['parsed']==row['expected']
            row['false_accept']=case['expected']['intent']=='abstain' and bool(row['parsed'] and row['parsed']['intent']!='abstain')
            row['elapsed_seconds']=round(time.monotonic()-started,3)
            results.append(row)
            trace.write(json.dumps(row,ensure_ascii=False)+'\n');trace.flush()
            print(json.dumps({k:row[k] for k in ('id','exact_match','false_accept','error')}),flush=True)
    positives=[r for r in results if r['expected']['intent']!='abstain']
    negatives=[r for r in results if r['expected']['intent']=='abstain']
    report={'total':len(results),'positive_total':len(positives),
            'positive_exact':sum(r['exact_match'] for r in positives),
            'negative_total':len(negatives),'negative_exact':sum(r['exact_match'] for r in negatives),
            'false_accepts':sum(r['false_accept'] for r in results),
            'errors':sum(r['error'] is not None for r in results),'model_calls':len(results),
            'production_promotion':False,'scope':config['scope']}
    (out/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report),flush=True)


if __name__=='__main__':main()
