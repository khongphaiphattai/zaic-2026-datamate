import math
import pytest
from zaic_2026_datamate.evaluation import evaluate_match, valid_answer

@pytest.mark.parametrize('pt,pv,tt,tv', [
 ('string','42','number',42), ('number','42','number',42),
 ('number',True,'number',1), ('percent',0.124,'percent',12.4),
 ('percent',12.41,'percent',12.4), ('number',1000001,'number',1000000),
 ('number',18.61,'number',18.6), ('number',float('inf'),'number',1),
 ('boolean',1,'boolean',True), ('date','2026-02-30','date','2026-02-28'),
 ('list',[1,2],'list',[2,1]), ('list',['1'],'list',[1]),
 ('table',{'columns':['x'],'rows':[['42']]},'table',{'columns':['x'],'rows':[[42]]}),
])
def test_reject_wrong_contract(pt,pv,tt,tv):
 assert not evaluate_match(pt,pv,tt,tv)

def test_valid_matching():
 assert evaluate_match('number',0.1+0.2,'number',0.3)
 assert evaluate_match('percent',12.4,'percent',12.40)
 assert evaluate_match('string','  CÀ PHÊ! ','string','cà phê')
 assert not evaluate_match('string','ca phe','string','cà phê')
 assert evaluate_match('table',{'columns':['Tên','Tiền'],'rows':[['B',2],['A',1]]},'table',{'columns':['Tên','Tiền'],'rows':[['A',1],['B',2]]})
 assert not valid_answer('table',{'columns':['a'],'rows':[[1,2]]})
 assert not evaluate_match('abstain',None,'unanswerable',None)
