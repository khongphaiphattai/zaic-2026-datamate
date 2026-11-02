# Copy toàn bộ file vào cell MỚI trong notebook 03, sau cell merge.
# Không restart runtime và không chạy lại training.
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import shutil
import subprocess
import threading
import urllib.request

MODEL_ID = 'datamate-business24'
MODEL_DIR = Path('/content/qwen2.5-coder-7b-datamate-business24')
EXPECTED_DATA = '55c2918798e68452be79cf69bc9244311d900af60533554d2fa436bb414a908e'
EXPECTED_REVISION = 'c03e6d358207e414f1eca0bb1891e29f1db0e242'


def make_server(generate, provenance, port=8000):
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass  # Do not print prompts or generated content into HTTP logs.

        def reply(self, status, payload):
            body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == '/v1/models':
                self.reply(200, {'data':[{'id':MODEL_ID}]})
            elif self.path == '/v1/model-provenance':
                self.reply(200, provenance)
            else:
                self.reply(404, {'error':'Unknown path'})

        def do_POST(self):
            if self.path != '/v1/chat/completions':
                self.reply(404, {'error':'Unknown path'})
                return
            try:
                self.connection.settimeout(30)
                size = int(self.headers.get('Content-Length','0'))
                if not 0 < size <= 131072:
                    raise ValueError('Invalid request size')
                payload = json.loads(self.rfile.read(size))
                if not isinstance(payload,dict) or payload.get('model') != MODEL_ID:
                    raise ValueError('Use model datamate-business24')
                if payload.get('temperature',0) != 0 or payload.get('seed',42) != 42 or payload.get('stream',False):
                    raise ValueError('Only temperature=0, seed=42, non-streaming requests are supported')
                messages = payload.get('messages')
                limit = payload.get('max_tokens',1024)
                if type(limit) is not int or not 1 <= limit <= 2048:
                    raise ValueError('max_tokens must be 1..2048')
                if not isinstance(messages,list) or not 1 <= len(messages) <= 16:
                    raise ValueError('Expected 1..16 messages')
                for m in messages:
                    if (not isinstance(m,dict) or m.get('role') not in ('system','user','assistant')
                            or not isinstance(m.get('content'),str) or len(m['content']) > 48000):
                        raise ValueError('Invalid message')
            except (ValueError, TypeError, OSError) as exc:
                self.reply(400, {'error':str(exc)})
                return
            if not lock.acquire(blocking=False):
                self.reply(429, {'error':'Model busy; retry after current request'})
                return
            try:
                result = generate(messages,limit)
                self.reply(200, {'model':MODEL_ID,'choices':[{'index':0,'message':{'role':'assistant','content':result['text']},
                                'finish_reason':result['finish_reason']}],
                                'usage':{'prompt_tokens':result['prompt_tokens'],'completion_tokens':result['completion_tokens']}})
            except ValueError as exc:
                self.reply(400, {'error':str(exc)})
            except Exception as exc:
                print('Inference error:',type(exc).__name__,str(exc),flush=True)
                self.reply(500, {'error':'Model generation failed; inspect Colab output'})
            finally:
                lock.release()

    server = ThreadingHTTPServer(('127.0.0.1',port),Handler)
    server.daemon_threads = True
    return server


def tunnel_binary():
    binary = shutil.which('cloudflared')
    if binary:
        return binary
    # Pinned official release + published SHA256; no dependency on GitHub API rate limits.
    # https://github.com/cloudflare/cloudflared/releases/tag/2026.10.7
    version = '2026.10.7'
    expected_sha256 = '1f99fb518b83dec2ba8d870a7cc991d82a77e6196295a3d2754ba17ab9f91872'
    url = f'https://github.com/cloudflare/cloudflared/releases/download/{version}/cloudflared-linux-amd64'
    with urllib.request.urlopen(url,timeout=60) as response:
        data = response.read(100_000_001)
    if len(data)>100_000_000 or hashlib.sha256(data).hexdigest()!=expected_sha256:
        raise RuntimeError('cloudflared SHA256 mismatch')
    target = Path('/tmp/cloudflared-business24')
    target.write_bytes(data)
    target.chmod(0o700)
    print('Verified cloudflared release:',version,flush=True)
    return str(target)


def start_business24_api():
    global _business24_server, _business24_tunnel
    if globals().get('_business24_server') is None:
        manifest = json.loads((MODEL_DIR/'training_manifest.json').read_text())
        merge = json.loads((MODEL_DIR/'merge_manifest.json').read_text())
        assert manifest['training_complete'] is True and merge['merge_complete'] is True
        assert manifest['examples']==633 and not manifest['overlap_businesses']
        assert manifest['data_sha256']==merge['data_sha256']==EXPECTED_DATA
        assert manifest['base_revision']==merge['base_revision']==EXPECTED_REVISION
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer
        if not torch.cuda.is_available():
            raise RuntimeError('Chọn runtime A100 GPU trước khi chạy cell này')
        print('Đang nạp MODEL BUSINESS24 MỚI vào GPU...',flush=True)
        tokenizer = AutoTokenizer.from_pretrained(str(MODEL_DIR),trust_remote_code=False)
        model = AutoModelForCausalLM.from_pretrained(str(MODEL_DIR),torch_dtype=torch.bfloat16,
                                                    device_map={'':0},trust_remote_code=False)
        model.eval()
        model.config.use_cache = True
        torch.manual_seed(42)

        def generate(messages,max_tokens):
            text = tokenizer.apply_chat_template(messages,tokenize=False,add_generation_prompt=True)
            inputs = tokenizer(text,return_tensors='pt').to('cuda:0')
            prompt_tokens = inputs.input_ids.shape[1]
            if prompt_tokens+max_tokens>8192:
                raise ValueError('Prompt + requested completion exceeds 8192 tokens')
            with torch.inference_mode():
                output = model.generate(**inputs,max_new_tokens=max_tokens,do_sample=False,
                                        pad_token_id=tokenizer.eos_token_id)
            new_tokens = output[0,prompt_tokens:]
            return {'text':tokenizer.decode(new_tokens,skip_special_tokens=True),
                    'prompt_tokens':prompt_tokens,'completion_tokens':len(new_tokens),
                    'finish_reason':'length' if len(new_tokens)>=max_tokens else 'stop'}

        _business24_server = make_server(generate,{'model':MODEL_ID,'model_dir':str(MODEL_DIR),
                                                 **manifest,'merge_complete':True})
        threading.Thread(target=_business24_server.serve_forever,daemon=True).start()
    with urllib.request.urlopen('http://127.0.0.1:8000/v1/models',timeout=5) as response:
        assert json.load(response)['data'][0]['id']==MODEL_ID
    if globals().get('_business24_tunnel') is not None and _business24_tunnel.poll() is None:
        print('Tunnel đã chạy. Dùng URL được in ở lần chạy trước; không restart cell.',flush=True)
        return
    proc = subprocess.Popen([tunnel_binary(),'--no-autoupdate','tunnel','--url','http://127.0.0.1:8000'],
                            stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,text=True)
    _business24_tunnel = proc
    ready = threading.Event()
    found = []

    def watch_tunnel():
        for line in proc.stderr:
            m = re.search(r'https://[a-zA-Z0-9-]+\.trycloudflare\.com',line)
            if m and not found:
                found.append(m.group(0))
                ready.set()
            if 'ERR' in line:
                print(line.strip(),flush=True)
        ready.set()

    threading.Thread(target=watch_tunnel,daemon=True).start()
    if not ready.wait(60) or not found:
        raise RuntimeError('Chưa nhận được URL tunnel; giữ runtime và gửi output này cho agent')
    print('\nAPI BUSINESS24 SẴN SÀNG',flush=True)
    print('Base URL:',found[0]+'/v1',flush=True)
    print('Model:',MODEL_ID,flush=True)
    print('Gửi Base URL này cho agent. Giữ runtime hoạt động; không cần chạy lại training.',flush=True)


if __name__=='__main__':
    start_business24_api()
