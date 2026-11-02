"""Fixed-policy full Train diagnosis or unlabelled Public shadow generation."""
import argparse
from collections import Counter, defaultdict
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import time
import urllib.request

from zaic_2026_datamate.data_loader import DataLoader
from zaic_2026_datamate.evaluation import evaluate_match
from zaic_2026_datamate.hybrid_agent import HybridAgent
from zaic_2026_datamate.llm_client import OpenAICompatibleClient
from zaic_2026_datamate.provenance import verify_model_provenance
from zaic_2026_datamate.selective_gate import daily_revenue_date
from zaic_2026_datamate.submission import write_submission


def read_rows(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


def summarize(cases, labelled):
    n = len(cases)
    def metrics(accepted, population):
        a = len(accepted)
        c = sum(r['correct'] is True for r in accepted) if labelled else None
        w = a-c if labelled else None
        return {'total':population,'answered':a,'abstain':population-a,'correct':c,'wrong':w,
                'precision':c/a if labelled and a else None,
                'pa':(c-w)/population if labelled and population else None}
    fast = metrics([r for r in cases if r['route']=='fast_path'],n)
    eligible = sum(r['eligible'] for r in cases)
    slow = metrics([r for r in cases if r['route']=='slow_path'],eligible)
    hybrid = metrics([r for r in cases if r['type']!='abstain'],n)
    reasons = Counter((r.get('reason') or 'unspecified').split(':')[0][:160] for r in cases if r['type']=='abstain')
    return {'questions':n,'fast':fast,'slow':slow,'hybrid':hybrid,
            'slow_delta_pa':(slow['correct']-slow['wrong'])/n if labelled and n else None,
            'metric_rejected':sum(bool(r.get('revenue_metric_check')) and not r['revenue_metric_check']['allowed'] for r in cases),
            'inference_errors':sum('LLM generation failed' in (r.get('reason') or '') for r in cases),
            'abstain_reasons':dict(reasons)}


def compare_baseline(baseline, actual):
    def index(rows):
        result = {r['id']:r for r in rows}
        if len(result)!=len(rows):
            raise ValueError('Duplicate IDs in baseline comparison')
        return result
    old,new = index(baseline),index(actual)
    return [{'id':qid,'baseline':old.get(qid),'actual_fast':new.get(qid)} for qid in sorted(set(old)|set(new))
            if (old.get(qid,{}).get('type'),old.get(qid,{}).get('answer')) !=
               (new.get(qid,{}).get('type'),new.get(qid,{}).get('answer'))]


def load_cached_responses(run_dir, served):
    run_dir = Path(run_dir)
    old = json.loads((run_dir/'served-model-provenance.json').read_text())
    identity = ['model','base_model','base_revision','data_sha256','split_sha256',
                'businesses','training_complete','merge_complete']
    if any(old.get(k)!=served.get(k) for k in identity):
        raise ValueError('Cache checkpoint provenance mismatch')
    config = json.loads((run_dir/'run-config.json').read_text())
    if any(config.get(k)!=v for k,v in {'model':served['model'],'temperature':0,'seed':42,'max_tokens':1024}.items()):
        raise ValueError('Cache generation settings mismatch')
    # Only selective_gate may change. Prompt and SQL execution semantics stay frozen.
    for name in ['hybrid_agent.py','slow_path.py','revenue_metric.py','prompt_generator.py','intent_parser.py']:
        actual = hashlib.sha256((Path('src/zaic_2026_datamate')/name).read_bytes()).hexdigest()
        if config.get('sources',{}).get(name)!=actual:
            raise ValueError(f'Cache frozen-source mismatch: {name}')
    cached = {}
    for row in read_rows(run_dir/'trace.jsonl'):
        for call in row.get('llm_calls',[]):
            if isinstance(call.get('response'),str):
                key,value = call['prompt_sha256'],call['response']
                if key in cached and cached[key]!=value:
                    raise ValueError('Different recorded responses for the same prompt')
                cached[key] = value
    return cached


class RecordedClient:
    def __init__(self, client, cached_responses=None):
        self.client = client
        self.cached_responses = cached_responses or {}
        self.calls = []

    def generate(self, prompt, system_prompt=None):
        record = {'prompt_sha256':hashlib.sha256(prompt.encode()).hexdigest()}
        self.calls.append(record)
        key = record['prompt_sha256']
        record['from_cache'] = key in self.cached_responses
        if record['from_cache']:
            record['response'] = self.cached_responses[key]
            return record['response']
        try:
            result = self.client.generate(prompt,system_prompt)
            record['response'] = result
            return result
        except Exception as exc:
            record['error'] = str(exc)
            raise


def output_row(qid, kind, value, reason):
    row = {'id':qid,'type':kind}
    if kind=='unanswerable':
        row['reason'] = reason or 'Không đủ dữ liệu'
    elif kind!='abstain':
        row['answer'] = value
    return row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset',choices=['train','public_test'],required=True)
    parser.add_argument('--base-url',required=True)
    parser.add_argument('--model',default='datamate-business24')
    parser.add_argument('--output-dir',required=True)
    parser.add_argument('--reuse-run',help='Reuse matching recorded model responses; SQL is checked and executed again')
    args = parser.parse_args()
    out = Path(args.output_dir)
    if out.exists():
        raise FileExistsError(f'Use a fresh output directory: {out}')
    split_path = Path('artifacts/selective-evaluation/split.json')
    split = json.loads(split_path.read_text())
    data_hash = hashlib.sha256(Path('artifacts/train-only-sft.jsonl').read_bytes()).hexdigest()
    with urllib.request.urlopen(args.base_url.rstrip('/')+'/model-provenance',timeout=20) as response:
        served = json.load(response)
    verify_model_provenance(served,data_hash,hashlib.sha256(split_path.read_bytes()).hexdigest(),split['train_businesses'],args.model)
    cached = load_cached_responses(args.reuse_run,served) if args.reuse_run else {}
    root = Path('data')/args.dataset/args.dataset
    questions = read_rows(root/'questions.jsonl')
    if len({q['id'] for q in questions})!=len(questions):
        raise ValueError('Duplicate question IDs')
    labelled = args.dataset=='train'
    answers = {r['id']:r for r in read_rows(root/'answers.jsonl')} if labelled else {}
    if labelled and set(answers)!={q['id'] for q in questions}:
        raise ValueError('Train answers do not cover all question IDs')
    groups = defaultdict(list)
    for q in questions:groups[q['business_id']].append(q)
    out.mkdir(parents=True)
    (out/'served-model-provenance.json').write_text(json.dumps(served,indent=2))
    sources = ['hybrid_agent.py','slow_path.py','revenue_metric.py','selective_gate.py','prompt_generator.py','intent_parser.py']
    run_config = {'dataset':args.dataset,'model':args.model,'endpoint':args.base_url,'reuse_run':args.reuse_run,'temperature':0,'seed':42,'max_tokens':1024,
                  'training_data_sha256':data_hash,'split_sha256':hashlib.sha256(split_path.read_bytes()).hexdigest(),
                  'sources':{name:hashlib.sha256((Path('src/zaic_2026_datamate')/name).read_bytes()).hexdigest() for name in sources},
                  'scope':'Descriptive development diagnostics only; SFT-seen vs SFT-excluded, not untouched validation. Public correctness unknown.'}
    (out/'run-config.json').write_text(json.dumps(run_config,indent=2))
    client = RecordedClient(OpenAICompatibleClient(base_url=args.base_url,model=args.model),cached)
    agent = HybridAgent(enable_slow_path=True,llm_client=client)
    loader = DataLoader(root/'data',root/'calendar.csv')
    cases=[]; predictions={};fast_rows={}; started=time.monotonic()
    with (out/'trace.jsonl').open('w') as trace:
        for bid,qs in sorted(groups.items()):
            context = loader.load_business(bid)
            try:
                for q in qs:
                    client.calls=[];context.last_sql=None;t0=time.monotonic()
                    kind,value,reason,route = agent.answer_query(q['question'],context,q.get('as_of_date'))
                    prediction = output_row(q['id'],kind,value,reason)
                    predictions[q['id']] = prediction
                    fast_rows[q['id']] = prediction if route=='fast_path' else {'id':q['id'],'type':'abstain'}
                    target = answers.get(q['id'])
                    correct = evaluate_match(kind,value,target['type'],target.get('answer')) if target else None
                    slow = agent.slow_engine
                    partition = ('sft_seen_24' if bid in split['train_businesses'] else 'sft_excluded_6') if labelled else 'public_unlabelled'
                    record = {'id':q['id'],'business_id':bid,'partition':partition,'question':q['question'],'as_of_date':q.get('as_of_date'),
                              **prediction,'route':route,'reason':reason,'correct':correct,'target':target,
                              'eligible':route!='fast_path' and daily_revenue_date(q['question'],q.get('as_of_date')) is not None,
                              'sql':context.last_sql,'slow_sql_attempt':slow.last_sql_attempt,'slow_execution_succeeded':slow.last_execution_succeeded,
                              'revenue_metric_check':asdict(slow.last_metric_check) if slow.last_metric_check else None,
                              'llm_calls':list(client.calls),'elapsed_seconds':time.monotonic()-t0}
                    cases.append(record)
                    trace.write(json.dumps(record,ensure_ascii=False,allow_nan=False,default=str)+'\n');trace.flush()
            finally:
                context.duckdb_conn.close()
            s=summarize([r for r in cases if r['business_id']==bid],labelled)
            print(json.dumps({'business_id':bid,'completed':len(cases),'questions':len(questions),'fast':s['fast']['answered'],
                              'slow':s['slow']['answered'],'eligible':s['slow']['total'],'inference_errors':s['inference_errors']},ensure_ascii=False),flush=True)
    report = {'overall':summarize(cases,labelled),
              'partitions':{p:summarize([r for r in cases if r['partition']==p],labelled) for p in sorted({r['partition'] for r in cases})},
              'businesses':{bid:summarize([r for r in cases if r['business_id']==bid],labelled) for bid in sorted(groups)},
              'model_calls':sum(len(r['llm_calls']) for r in cases),
              'network_model_calls':sum(not c['from_cache'] for r in cases for c in r['llm_calls']),
              'reused_model_calls':sum(c['from_cache'] for r in cases for c in r['llm_calls']),
              'elapsed_seconds':time.monotonic()-started,
              'scope':run_config['scope'],'recommend_submit':False}
    expected = [q['id'] for q in questions]
    if labelled:
        write_submission(out/'predictions.jsonl',[predictions[qid] for qid in expected],expected)
    else:
        baseline = read_rows('artifacts/baseline-v2.8/submission.jsonl')
        differences = compare_baseline(baseline,list(fast_rows.values()))
        (out/'fast-baseline-differences.json').write_text(json.dumps(differences,ensure_ascii=False,indent=2))
        report['fast_matches_v2_8'] = not differences
        report['new_answer_ids'] = [r['id'] for r in cases if r['route']=='slow_path']
        report['candidate_created'] = False
        if not differences:
            write_submission(out/'submission.jsonl',[predictions[qid] for qid in expected],expected)
            report['candidate_created'] = True
            report['candidate_sha256'] = hashlib.sha256((out/'submission.jsonl').read_bytes()).hexdigest()
    (out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps(report['overall'],ensure_ascii=False,indent=2),flush=True)


if __name__=='__main__':
    main()
