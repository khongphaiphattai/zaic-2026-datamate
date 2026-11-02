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
