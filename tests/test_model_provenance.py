import pytest
from scripts.evaluate_selective import verify_model_provenance


def test_checkpoint_identity():
 metadata={'model':'datamate-business24','training_complete':True,'merge_complete':True,
           'data_sha256':'dataset','split_sha256':'split','base_revision':'c03e6d358207e414f1eca0bb1891e29f1db0e242',
           'base_model':'Qwen/Qwen2.5-Coder-7B-Instruct','businesses':['a']}
 verify_model_provenance(metadata,'dataset','split',['a'],'datamate-business24')
 for change in [{'model':'old'},{'data_sha256':'old'},{'training_complete':False},{'merge_complete':False},
                {'base_revision':'wrong'},{'businesses':['a','validation']}]:
  with pytest.raises(ValueError):
   verify_model_provenance({**metadata,**change},'dataset','split',['a'],'datamate-business24')
