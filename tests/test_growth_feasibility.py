"""Contracts for the offline diagnostic; it must not masquerade as inference."""
import copy

import pandas as pd
import pytest

from scripts.audit_growth_feasibility import check_data, diagnose_case, validate_cases


def case(period='month_to_date'):
    return {'id':'one','business_id':'biz_a','question':'development annotation',
            'as_of_date':'2025-03-31','category':period,
            'intent':{'intent':'revenue_growth','period':period,'year':2025,'month':3},
            'annotation_scope':'question-only'}


def test_annotations_must_match_abstaining_train_partition():
    c=case()
    baseline={'one':{**c,'partition':'sft_seen_24','type':'abstain'}}
    validate_cases([c],baseline,{'biz_a'})
    for field,value in [('question','changed'),('partition','sft_excluded_6'),('type','percent')]:
        bad=copy.deepcopy(baseline);bad['one'][field]=value
        with pytest.raises(ValueError):validate_cases([c],bad,{'biz_a'})
    with pytest.raises(ValueError):validate_cases([c,c],baseline,{'biz_a'})
    with pytest.raises(ValueError):validate_cases([c],baseline,{'biz_other'})
    with pytest.raises(ValueError):validate_cases([{**c,'target':42}],baseline,{'biz_a'})


def frame():
    return pd.DataFrame({'order':['a','b','c'], 'day':['2025-02-01','2025-03-01','2025-03-31'],
                         'status':['Đã hoàn thành']*3,'money':[100,50,60]})


@pytest.mark.parametrize('column,value',[('money',-1),('money',float('inf')),('day','bad'),('order',''),('status','Unknown')])
def test_bad_data_never_becomes_ready(column,value):
    data=frame();data[column]=data[column].astype(object);data.loc[0,column]=value
    assert check_data(data,'order','day','status','money')['valid'] is False


def test_conflicting_orders_empty_and_missing_data_fail():
    data=frame();data.loc[1,'order']='a'
    assert check_data(data,'order','day','status','money')['valid'] is False
    assert check_data(data.iloc[:0],'order','day','status','money')['valid'] is False
    data=frame();data.loc[0,'money']=None
    assert check_data(data,'order','day','status','money')['valid'] is False


def test_mtd_readiness_is_not_computed_answer(monkeypatch):
    from scripts import audit_growth_feasibility as audit
    def forbidden(*args):raise AssertionError('MTD must not invoke full-month resolver')
    monkeypatch.setattr(audit,'resolve_monthly_growth',forbidden)
    profile={'roles_verified':True,'metric':{'allowed':True},
             'data':check_data(frame(),'order','day','status','money')}
    r=diagnose_case(case(),None,profile)
    assert r['stage']=='mtd_compiler_missing' and r['answer'] is None
    bad=copy.deepcopy(profile);bad['data']['max_date']='2025-03-30'
    assert diagnose_case(case(),None,bad)['stage']=='boundary_rejected'
    bad=copy.deepcopy(profile);bad['roles_verified']=False
    assert diagnose_case(case(),None,bad)['stage']=='role_unverified'
    assert diagnose_case(case('full_month'),None,profile | {'roles_verified':False})['stage']=='role_unverified'
    c=case('full_month');c['as_of_date']='2025-03-30'
    assert diagnose_case(c,None,profile)['stage']=='date_rejected'
