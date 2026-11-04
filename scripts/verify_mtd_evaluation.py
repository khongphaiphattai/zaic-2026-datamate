import json,hashlib,sys
from pathlib import Path
from collections import Counter
from zaic_2026_datamate.evaluation import evaluate_match,valid_answer

dataset=sys.argv[1]
run=Path('artifacts/v31-mtd-'+('train' if dataset=='train' else 'public'))
old=Path('artifacts/v31-growth-'+('train' if dataset=='train' else 'public'))
read=lambda p:[json.loads(l) for l in Path(p).read_text().splitlines() if l.strip()]
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
rows=read(run/'trace.jsonl');before=read(old/'trace.jsonl')
current={r['id']:r for r in rows};base={r['id']:r for r in before}
questions=read(Path('data')/dataset/dataset/'questions.jsonl')
assert len(rows)==len(current)==len(base)==len(questions)
assert set(current)==set(base)=={q['id'] for q in questions}
cfg=json.loads((run/'run-config.json').read_text());report=json.loads((run/'report.json').read_text())
assert cfg['offline_replay'] and cfg['allow_execution_changes'] and cfg['pipeline']['mtd_growth']
assert all(sha(Path('src/zaic_2026_datamate')/name)==value for name,value in cfg['sources'].items())
assert report['network_model_calls']==0
new=[]
for qid,r in current.items():
    b=base[qid]
    assert all(r[k]==b[k] for k in ('question','business_id','as_of_date'))
    assert [(c['prompt_sha256'],c['response']) for c in r['llm_calls']]==[(c['prompt_sha256'],c['response']) for c in b['llm_calls']]
    if b['type']!='abstain':
        assert all(r.get(k)==b.get(k) for k in ('type','answer','reason','route'))
    elif r['type']!='abstain':
        assert r['route']=='mtd_growth_path' and r['type']=='percent' and valid_answer(r['type'],r['answer'])
        new.append(r)
    else:assert r['route']==b['route'] and r['reason']==b['reason']
    if dataset=='train':
        target=r['target'];assert r['correct']==evaluate_match(r['type'],r.get('answer'),target['type'],target.get('answer'))
    else:assert r['correct'] is None and r['target'] is None
expected_ids=[q['id'] for q in questions]
candidate=run/('predictions.jsonl' if dataset=='train' else 'submission.jsonl')
predictions=read(candidate);assert [r['id'] for r in predictions]==expected_ids
for prediction in predictions:
    actual=current[prediction['id']]
    assert prediction['type']==actual['type']
    if prediction['type'] not in ('abstain','unanswerable'):assert prediction['answer']==actual['answer']
release='288ed868fde9175d36afd8995cdaf0e2c5f978a716be5c2a845f35c5e89afb9f'
assert sha('submission.jsonl')==sha('artifacts/releases/v3.0/submission.jsonl')==release
verification={'verified':True,'ids':len(rows),'all_old_non_abstain_predictions_routes_reasons_preserved':True,
              'remaining_abstain_routes_reasons_preserved':True,'raw_model_calls_prompts_responses_preserved':True,
              'new_answers':len(new),'new_correct':sum(r['correct'] is True for r in new) if dataset=='train' else None,
              'new_wrong':sum(r['correct'] is False for r in new) if dataset=='train' else None,
              'partitions':dict(Counter(r['partition'] for r in new)),'businesses':dict(Counter(r['business_id'] for r in new)),
              'source_hashes_match':True,'network_calls':0,'candidate_sha256':sha(candidate),'release_sha256':release,
              'candidate_matches_release':sha(candidate)==release if dataset!='train' else None,
              'scope':'Development evaluation, Public correctness unknown. New MTD answers are deterministic.'}
if dataset=='train':
    overall=report['overall']['hybrid'];correct=sum(r['correct'] is True for r in rows if r['type']!='abstain')
    wrong=sum(r['correct'] is False for r in rows if r['type']!='abstain')
    assert overall['correct']==correct and overall['wrong']==wrong
    assert overall['pa']==(correct-wrong)/len(rows)
else:
    train_cfg=json.loads(Path('artifacts/v31-mtd-train/run-config.json').read_text())
    assert cfg['sources']==train_cfg['sources'] and cfg['pipeline']==train_cfg['pipeline']
    verification['train_public_sources_config_equal']=True
(run/'verification.json').write_text(json.dumps(verification,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(verification,ensure_ascii=False,indent=2))
