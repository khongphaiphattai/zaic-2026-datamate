"""Frozen business split, SFT overlap audit and Fast/Selective comparison.

Existing SFT/checkpoints have seen all businesses; a new split cannot erase that.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import urllib.request

from zaic_2026_datamate.data_loader import DataLoader
from zaic_2026_datamate.evaluation import evaluate_match
from zaic_2026_datamate.hybrid_agent import HybridAgent
from zaic_2026_datamate.llm_client import OpenAICompatibleClient
from zaic_2026_datamate.selective_gate import daily_revenue_date


def read_rows(path):
    return [json.loads(l) for l in Path(path).read_text().splitlines() if l.strip()]


def verify_model_provenance(metadata, data_sha256, split_sha256, businesses, model):
    expected = {'model':model, 'training_complete':True, 'merge_complete':True,
                'data_sha256':data_sha256, 'split_sha256':split_sha256,
                'base_model':'Qwen/Qwen2.5-Coder-7B-Instruct',
                'base_revision':'c03e6d358207e414f1eca0bb1891e29f1db0e242'}
    if not isinstance(metadata,dict) or any(metadata.get(k)!=v for k,v in expected.items()):
        raise ValueError('Served model provenance does not match the requested trained checkpoint')
    if sorted(metadata.get('businesses',[])) != sorted(businesses):
        raise ValueError('Served model training businesses differ from the training partition')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output-dir', default='artifacts/selective-evaluation')
    p.add_argument('--training-data', default='train_sft.jsonl', help='Actual complete SFT provenance for checkpoint')
    p.add_argument('--base-url', help='Supplying an endpoint enables model evaluation')
    p.add_argument('--model', default='datamate')
    p.add_argument('--exploratory', action='store_true', help='Permit overlap only for explicitly labeled development exploration')
    p.add_argument('--audit-only', action='store_true')
    args = p.parse_args()
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    qs = read_rows('data/train/train/questions.jsonl')
    bids = sorted({q['business_id'] for q in qs}, key=lambda b: hashlib.sha256(('selective-v1:'+b).encode()).hexdigest())
    split = {'version':1, 'train_businesses':sorted(bids[6:]), 'validation_businesses':sorted(bids[:6]),
             'limitation':'Prospective split only. Existing rule tuning used Train; existing SFT saw all businesses.'}
    manifest = out/'split.json'
    if manifest.exists() and json.loads(manifest.read_text()) != split:
        raise ValueError('Existing split differs; refusing to change validation membership')
    manifest.write_text(json.dumps(split, ensure_ascii=False, indent=2))
    training = read_rows(args.training_data)
    overlap = sorted({r['business_id'] for r in training} & set(split['validation_businesses']))
    provenance = {'training_sha256':hashlib.sha256(Path(args.training_data).read_bytes()).hexdigest(),
                  'training_examples':len(training), 'overlap_businesses':overlap,
                  'checkpoint_training_provenance':'Caller must attest this is the complete training data; base pretraining unknown',
                  'model':args.model, 'eligible_for_model_holdout_evaluation':not overlap}
    (out/'provenance.json').write_text(json.dumps(provenance, indent=2))
    print(json.dumps(provenance, indent=2), flush=True)
    if args.base_url and overlap and not args.exploratory:
        raise ValueError('Checkpoint SFT overlaps held-out businesses; retrain on split train partition first')
    if args.audit_only:
        return
    if args.base_url and not args.exploratory:
        with urllib.request.urlopen(args.base_url.rstrip('/')+'/model-provenance',timeout=20) as response:
            served = json.load(response)
        verify_model_provenance(served,provenance['training_sha256'],
                                hashlib.sha256(manifest.read_bytes()).hexdigest(),
                                sorted({r['business_id'] for r in training}),args.model)
        (out/'served-model-provenance.json').write_text(json.dumps(served,indent=2))

    answers = {r['id']:r for r in read_rows('data/train/train/answers.jsonl')}
    loader = DataLoader('data/train/train/data','data/train/train/calendar.csv')
    agent = HybridAgent(enable_slow_path=bool(args.base_url), llm_client=OpenAICompatibleClient(base_url=args.base_url,model=args.model) if args.base_url else None)
    stats = {'fast':Counter(), 'slow':Counter(), 'abstain':0, 'gate_eligible':0, 'total':0, 'inference_errors':0}
    with (out/'trace.jsonl').open('w') as trace:
        for bid in split['validation_businesses']:
            ctx = loader.load_business(bid)
            for q in (q for q in qs if q['business_id']==bid):
                ctx.last_sql = None
                kind,value,reason,route = agent.answer_query(q['question'],ctx,q['as_of_date'])
                stats['total']+=1
                if route!='fast_path' and daily_revenue_date(q['question'],q['as_of_date']):
                    stats['gate_eligible']+=1
                if reason and 'LLM generation failed' in reason:
                    stats['inference_errors'] += 1
                target = answers[q['id']]
                correct = evaluate_match(kind,value,target['type'],target.get('answer'))
                if kind=='abstain': stats['abstain']+=1
                else: stats['fast' if route=='fast_path' else 'slow']['correct' if correct else 'wrong']+=1
                trace.write(json.dumps({'id':q['id'],'business_id':bid,'type':kind,'answer':value,'route':route,'reason':reason,'sql':ctx.last_sql,'correct':correct},ensure_ascii=False,default=str)+'\n')
            ctx.duckdb_conn.close()
    net = lambda k: stats[k]['correct']-stats[k]['wrong']
    report = {**stats,'fast_pa':net('fast')/stats['total'], 'hybrid_pa':(net('fast')+net('slow'))/stats['total'],
              'slow_delta_pa':net('slow')/stats['total'], 'model_attempted':bool(args.base_url), 'model_evaluated':bool(args.base_url) and stats['gate_eligible'] > stats['inference_errors'], 'exploratory':args.exploratory, 'training_overlap':overlap,
              'recommend_enable':False, 'limitation':'SFT-business isolation only; historical rule/prompt/gate tuning used Train and base pretraining is unknown.'}
    (out/'report.json').write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__=='__main__':
    main()
