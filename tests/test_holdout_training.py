import json
import pytest
from scripts.holdout_training import validate_training_data, completion_records


def sample(bid='train',sql='SELECT SUM(tien) FROM sales'):
 return {'id':'q1','business_id':bid,'type':'number','messages':[{'role':'system','content':'rules'},{'role':'user','content':'question'},{'role':'assistant','content':json.dumps({'type':'number','sql':sql,'confidence':0.98})}]}

def test_partition_and_contract():
 split={'train_businesses':['train'],'validation_businesses':['held']}
 assert validate_training_data([sample()],split)['examples']==1
 for rows in [[sample('held')],[sample(),sample()],[sample(sql=None)],[]]:
  with pytest.raises(ValueError): validate_training_data(rows,split)
 with pytest.raises(ValueError): validate_training_data([sample()],{'train_businesses':['train'],'validation_businesses':['train']})

def test_completion_target_not_lost():
 r=completion_records([sample()])[0]
 assert [m['role'] for m in r['prompt']]==['system','user']
 assert r['completion'][0]['role']=='assistant'
 assert 'business_id' not in r
