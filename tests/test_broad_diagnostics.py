import pytest
from scripts.diagnose_hybrid import summarize, compare_baseline


def case(route, kind, correct=None, reason=None):
 return {'route':route,'type':kind,'correct':correct,'reason':reason,'eligible':route!='fast_path',
         'revenue_metric_check':{'allowed':False} if reason=='metric' else None}


def test_train_contribution_and_abstentions():
 rows=[case('fast_path','number',True),case('fast_path','number',False),
       case('slow_path','number',True),case('slow_path','number',False),case('abstain','abstain',False,'metric')]
 r=summarize(rows,True)
 assert r['fast']['correct']==1 and r['fast']['wrong']==1
 assert r['slow']['correct']==1 and r['slow']['wrong']==1
 assert r['hybrid']['abstain']==1 and r['hybrid']['pa']==0
 assert r['metric_rejected']==1


def test_public_has_no_quality_claims():
 r=summarize([case('slow_path','number')],False)
 assert r['hybrid']['correct'] is None and r['hybrid']['pa'] is None
 assert r['slow_delta_pa'] is None and r['hybrid']['answered']==1


def test_baseline_compares_scored_fields_and_all_ids():
 old=[{'id':'a','type':'number','answer':3},{'id':'b','type':'abstain'}]
 assert compare_baseline(old,old)==[]
 assert len(compare_baseline(old,[{'id':'a','type':'number','answer':4}]))==2
 with pytest.raises(ValueError): compare_baseline(old,old+[old[0]])


def test_cached_response_reexecutes_protocol_without_network():
 from scripts.diagnose_hybrid import RecordedClient
 import hashlib
 class Backend:
  def generate(self,prompt,system_prompt=None): return 'network:'+prompt
 key=hashlib.sha256(b'known').hexdigest()
 client=RecordedClient(Backend(),{key:'cached SQL'})
 assert client.generate('known')=='cached SQL'
 assert client.calls[-1]['from_cache'] is True
 assert client.generate('new')=='network:new'
 assert client.calls[-1]['from_cache'] is False


def test_cache_checks_identity_and_settings(tmp_path):
 import hashlib,json
 from pathlib import Path
 from scripts.diagnose_hybrid import load_cached_responses
 meta={'model':'m','base_model':'b','base_revision':'r','data_sha256':'data','split_sha256':'split',
       'businesses':['a'],'training_complete':True,'merge_complete':True}
 config={'model':'m','temperature':0,'seed':42,'max_tokens':1024,'sources':{
  n:hashlib.sha256((Path('src/zaic_2026_datamate')/n).read_bytes()).hexdigest()
  for n in ['hybrid_agent.py','slow_path.py','revenue_metric.py','prompt_generator.py','intent_parser.py']}}
 (tmp_path/'served-model-provenance.json').write_text(json.dumps(meta))
 (tmp_path/'run-config.json').write_text(json.dumps(config))
 (tmp_path/'trace.jsonl').write_text(json.dumps({'target':{'answer':'unused'},'llm_calls':[{'prompt_sha256':'p','response':'SQL'}]})+'\n')
 assert load_cached_responses(tmp_path,meta)=={'p':'SQL'}
 with pytest.raises(ValueError): load_cached_responses(tmp_path,{**meta,'base_revision':'wrong'})
 config['max_tokens']=99
 (tmp_path/'run-config.json').write_text(json.dumps(config))
 with pytest.raises(ValueError): load_cached_responses(tmp_path,meta)
 config['max_tokens']=1024
 original_hash=config['sources']['prompt_generator.py']
 config['sources']['prompt_generator.py']='changed prompt'
 (tmp_path/'run-config.json').write_text(json.dumps(config))
 with pytest.raises(ValueError,match='frozen-source mismatch'): load_cached_responses(tmp_path,meta)
 config['sources']['prompt_generator.py']=original_hash
 (tmp_path/'run-config.json').write_text(json.dumps(config))
 (tmp_path/'trace.jsonl').write_text(json.dumps({'llm_calls':[
  {'prompt_sha256':'p','response':'SQL'},
  {'prompt_sha256':'p','response':'different SQL'}]})+'\n')
 with pytest.raises(ValueError,match='Different recorded responses'): load_cached_responses(tmp_path,meta)
