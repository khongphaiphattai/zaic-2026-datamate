import importlib.util
import json
from pathlib import Path
import threading
import urllib.request
import urllib.error
import pytest

p=Path('notebooks/business24_api_cell.py')
spec=importlib.util.spec_from_file_location('business24_api',p)
api=importlib.util.module_from_spec(spec)
spec.loader.exec_module(api)


def test_api_contract_and_invalid_requests():
 calls=[]
 def generate(messages,max_tokens):
  calls.append((messages,max_tokens))
  return {'text':'{"type":"number","sql":"SELECT 1"}', 'prompt_tokens':10,'completion_tokens':5,'finish_reason':'stop'}
 server=api.make_server(generate,{'training_complete':True,'data_sha256':'test'},port=0)
 thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
 url=f'http://127.0.0.1:{server.server_port}'
 def post(data):
  request=urllib.request.Request(url+'/v1/chat/completions',json.dumps(data).encode(),{'Content-Type':'application/json'})
  with urllib.request.urlopen(request,timeout=5) as r:return json.load(r)
 try:
  with urllib.request.urlopen(url+'/v1/models') as r: assert json.load(r)['data'][0]['id']=='datamate-business24'
  with urllib.request.urlopen(url+'/v1/model-provenance') as r: assert json.load(r)['data_sha256']=='test'
  payload={'model':'datamate-business24','messages':[{'role':'user','content':'hello'}],'temperature':0,'seed':42,'max_tokens':1024}
  assert post(payload)['choices'][0]['message']['content'].startswith('{')
  for change in [{'model':'datamate'},{'temperature':0.5},{'max_tokens':100000},{'seed':0},{'messages':[]},{'messages':[{'role':'user','content':'x'*100000}]}]:
   with pytest.raises(urllib.error.HTTPError):post({**payload,**change})
  assert len(calls)==1
 finally:
  server.shutdown();server.server_close();thread.join()


def test_tunnel_rejects_wrong_binary(monkeypatch):
 import io
 monkeypatch.setattr(api.shutil,'which',lambda _:None)
 monkeypatch.setattr(api.urllib.request,'urlopen',lambda *a,**k:io.BytesIO(b'invalid binary'))
 with pytest.raises(RuntimeError,match='SHA256 mismatch'):api.tunnel_binary()
