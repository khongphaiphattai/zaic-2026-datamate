"""Build the standalone recovery notebook from tested local helper sources."""
import json
from pathlib import Path
import textwrap

ROOT = Path(__file__).resolve().parents[1]
cells = []


def markdown(text):
    cells.append({'cell_type': 'markdown', 'metadata': {}, 'source': textwrap.dedent(text).strip().splitlines(True)})


def code(text):
    cells.append({'cell_type': 'code', 'execution_count': None, 'outputs': [],
                  'metadata': {'cellView': 'form'}, 'source': textwrap.dedent(text).strip().splitlines(True)})


markdown('''
    # Business24 — Khôi phục backup và mở API
    **Không training.** Chạy lần lượt cell 1 → 7 trong runtime A100 40 GB / High RAM.
    Khi có Base URL, gửi agent để đo Train/Public; giữ runtime hoạt động trong lượt đo.
    120 units là ngân sách người dùng báo; notebook không ước lượng số giờ hoặc tự train.

    **Chỉ cần 1 file upload:** `business24-adapter-backup.zip` (~154 MB).
    File trên máy: `/home/pt/AI Challenge/business24-adapter-backup.zip`.
    Windows/WSL có thể mở `\\\\wsl.localhost\\Ubuntu\\home\\pt\\AI Challenge` nếu distro tên Ubuntu;
    tên distro khác thì dùng đường dẫn tương ứng, hoặc tải ZIP về Downloads để chọn dễ hơn.
    Không cần upload training bundle, `training_manifest.json` riêng hay file `.py`.

    Nếu mất runtime: mở lại notebook này, chạy 1 → 7, upload lại ZIP; model gốc sẽ tải và merge lại.
    Adapter đã train xong nên **không train lại**. Nếu runtime còn model hoàn chỉnh, cell 5 sẽ dùng lại.
    Notebook không tạo/nộp submission và không thao tác portal.
''')

embedded = {name: (ROOT / 'notebooks' / name).read_text() for name in [
    'business24_recovery.py', 'business24_merge_cell.py', 'business24_api_cell.py']}
code('#@title 1. Kiểm tra GPU/RAM và chuẩn bị công cụ (mở code nếu cần xem chi tiết)\n'
     + 'EMBEDDED_SOURCES = ' + repr(embedded) + '\n' + textwrap.dedent('''
    from pathlib import Path
    import importlib.util, json, os, shutil, subprocess, sys, time

    WORK = Path('/content/business24-recovery')
    ADAPTER = Path('/content/qwen2.5-coder-7b-lora-business24')
    MERGED = Path('/content/qwen2.5-coder-7b-datamate-business24')
    VENV = Path('/content/business24-venv')
    PYTHON = VENV / 'bin/python'
    if sys.version_info < (3, 11) or sys.version_info >= (3, 15):
        raise RuntimeError('Notebook cần Python 3.11–3.14. Gửi phiên bản runtime cho agent.')
    if not shutil.which('nvidia-smi'):
        raise RuntimeError('Chưa có GPU. Chọn Runtime → Change runtime type → A100 GPU / High RAM.')
    gpu = subprocess.check_output(['nvidia-smi', '--query-gpu=name,memory.total', '--format=csv,noheader,nounits'], text=True)
    print('GPU:', gpu.strip())
    if max(float(line.rsplit(',', 1)[1]) for line in gpu.strip().splitlines()) < 35000:
        raise RuntimeError('Chọn A100 40 GB như phiên trước; dừng trước khi tải model.')
    ram_kib = {line.split(':')[0]: int(line.split()[1]) for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith(('MemTotal:', 'MemAvailable:'))}
    print('RAM tổng / khả dụng (GiB):', round(ram_kib['MemTotal']/1024**2, 1), '/', round(ram_kib['MemAvailable']/1024**2, 1))
    if not MERGED.exists() and ram_kib['MemAvailable'] < 32 * 1024**2:
        raise RuntimeError('RAM khả dụng dưới 32 GiB. Chọn High RAM; cấu hình đã chạy thành công trước đó khoảng 80 GB.')
    free_gib = shutil.disk_usage('/content').free / 1024**3
    print('Ổ đĩa trống (GiB):', round(free_gib, 1))
    if not MERGED.exists() and free_gib < 60:
        raise RuntimeError('Cần ít nhất 60 GiB trống cho thư viện, base cache và merged model.')
    WORK.mkdir(parents=True, exist_ok=True)
    for name, source in EMBEDDED_SOURCES.items():
        (WORK / name).write_text(source)
    spec = importlib.util.spec_from_file_location('business24_recovery', WORK / 'business24_recovery.py')
    recovery = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(recovery)
    print('Sẵn sàng. Adapter còn:', ADAPTER.exists(), '| Merged còn:', MERGED.exists())
'''))

markdown('''
    ## Cell 2 — Môi trường riêng
    Cài đúng Torch CUDA build trong training manifest và bộ merge đã dùng trước đó.
    `venv` tách khỏi Gradio/Diffusers của Colab; **không pip install vào kernel hệ thống**.
    Lần đầu tải thư viện có thể mất vài phút; chạy lại cell sẽ dùng môi trường đã kiểm.
    Không cần Restart session sau bước này. Log lưu tại `/content/business24-recovery/install.log`.
''')
code('''
    #@title 2. Cài thư viện riêng và kiểm CUDA/bfloat16
    def run_logged(command, filename):
        log_path = WORK / filename
        with log_path.open('a') as log:
            proc = subprocess.Popen([str(x) for x in command], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            for line in proc.stdout:
                print(line, end='')
                log.write(line)
                log.flush()
            status = proc.wait()
        if status:
            raise RuntimeError(f'Lệnh thất bại (exit {status}); gửi output phía trên. Log: {log_path}')

    stamp = VENV / 'business24-environment.json'
    expected_environment = {'torch': '2.11.0+cu130', 'transformers': '4.57.1',
                            'peft': '0.17.1', 'accelerate': '1.11.0', 'huggingface-hub': '0.36.2'}
    if not stamp.is_file() or json.loads(stamp.read_text()) != expected_environment:
        if recovery.managed_process(WORK):
            raise RuntimeError('API đang chạy; không cài lại thư viện giữa lượt đo. Dừng bằng cell 8 trước.')
        if not PYTHON.exists():
            run_logged([sys.executable, '-m', 'venv', str(VENV)], 'install.log')
        run_logged([PYTHON, '-m', 'pip', 'install', 'torch==2.11.0+cu130', '--index-url', 'https://download.pytorch.org/whl/cu130'], 'install.log')
        run_logged([PYTHON, '-m', 'pip', 'install', 'transformers==4.57.1', 'peft==0.17.1', 'accelerate==1.11.0', 'huggingface-hub==0.36.2'], 'install.log')
        run_logged([PYTHON, '-m', 'pip', 'check'], 'install.log')
        stamp.write_text(json.dumps(expected_environment))
    smoke = """import importlib.metadata as m, json, torch
    from transformers import AutoModelForCausalLM, AutoTokenizer
    from peft import PeftModel
    expected = EXPECTED_ENVIRONMENT
    assert all(m.version(k) == v for k, v in expected.items()), 'Installed version mismatch'
    assert torch.cuda.is_available(), 'CUDA không hoạt động; gửi output nvidia-smi cho agent'
    assert torch.cuda.is_bf16_supported(), 'GPU không hỗ trợ bfloat16'
    print('IMPORT/CUDA OK:', torch.__version__, torch.cuda.get_device_name(0))
    """.replace('EXPECTED_ENVIRONMENT', repr(expected_environment))
    run_logged([PYTHON, '-u', '-c', smoke], 'install.log')
''')

markdown('''
    ## Cell 3 — Upload ZIP backup
    Chọn **`business24-adapter-backup.zip`** tại `/home/pt/AI Challenge/business24-adapter-backup.zip` trên máy.
    Trong Colab file được đặt ở `/content/business24-adapter-backup.zip`.
    Nếu ZIP đã nằm trên Drive, có thể điền đường dẫn ở `BACKUP_ON_DRIVE`; để trống thì chọn upload từ máy.
    Adapter còn và hợp lệ sẽ được dùng lại, không hỏi upload. ZIP kiểm SHA256 + CRC trước giải nén.
''')
code('''
    #@title 3. Khôi phục adapter từ backup đã train xong
    BACKUP_ON_DRIVE = '' #@param {type:"string"}
    archive = Path('/content/business24-adapter-backup.zip')
    if MERGED.exists():
        recovery.check_merged(MERGED)
        manifest = recovery.check_manifest(MERGED, verify_data=False)
        print('Merged model đã hợp lệ; không cần upload hoặc khôi phục adapter.')
    elif ADAPTER.exists():
        manifest = recovery.check_manifest(ADAPTER)
        print('Adapter đã có và hợp lệ; bỏ qua upload.')
    else:
        if BACKUP_ON_DRIVE:
            from google.colab import drive
            drive.mount('/content/drive')
            archive = Path(BACKUP_ON_DRIVE)
        elif not archive.is_file():
            from google.colab import files
            os.chdir('/content')
            uploaded = files.upload()  # CHỈ chọn business24-adapter-backup.zip
            del uploaded  # Giải phóng bản upload trong RAM.
        if not archive.is_file():
            raise FileNotFoundError('Chưa thấy business24-adapter-backup.zip đúng tên/đường dẫn; chọn lại file.')
        manifest = recovery.restore_backup(archive, ADAPTER)
    print('BACKUP OK:', manifest['examples'], 'mẫu; train complete:', manifest['training_complete'])
    print('Base revision:', manifest['base_revision'])
''')

markdown('''
    ## Cell 4 — Merge một lần
    Nạp base Qwen đúng revision trên RAM hệ thống rồi gộp adapter; **không training**.
    Lần đầu cần tải khoảng 15 GB base weights. Log: `/content/business24-recovery/merge.log`.
    Chạy lại sẽ kiểm và dùng merged model hoàn chỉnh. Thư mục dở dang sẽ bị từ chối, không tự xóa.
''')
code('''
    #@title 4. Merge hoặc dùng lại model đã hoàn tất
    if MERGED.exists():
        merge = recovery.check_merged(MERGED)
        print('Dùng lại merged model đã kiểm:', MERGED)
    else:
        if recovery.managed_process(WORK):
            raise RuntimeError('API đang chạy; không merge trong cùng lượt đo.')
        run_logged([PYTHON, '-u', WORK / 'business24_merge_cell.py'], 'merge.log')
        merge = recovery.check_merged(MERGED)
    print('MERGED OK:', round(merge['weights_bytes'] / 1024**3, 2), 'GiB weights')
''')

markdown('''
    ## Cell 5 — Mở API và lấy URL
    Mặc định dùng lại process API đang chạy, không nạp thêm model. Đợi tới khi in **Base URL** rồi gửi agent.
    Nếu URL cũ mất nhưng runtime còn hoạt động: bật `REOPEN_TUNNEL`, chạy lại cell này; chỉ mở lại tunnel, giữ model trong GPU.
    Nếu model/API lỗi, xem log; không lặp Run all khi chưa đọc lỗi. Không chạy notebook 02/training đồng thời trên runtime này.
''')
code('''
    #@title 5. Mở API (hoặc mở lại riêng tunnel)
    import re, socket, urllib.request
    REOPEN_TUNNEL = False #@param {type:"boolean"}
    recovery.check_merged(MERGED)
    already_running = recovery.managed_process(WORK) is not None
    if not already_running:
        with socket.socket() as probe:
            if probe.connect_ex(('127.0.0.1', 8000)) == 0:
                raise RuntimeError('Cổng 8000 đã có API khác. Dừng đúng API cũ trước; chưa nạp thêm model.')
    log_path = WORK / 'api.log'
    offset = log_path.stat().st_size if already_running and REOPEN_TUNNEL else 0
    pid = recovery.start_api(PYTHON, WORK / 'business24_api_cell.py', WORK)
    if already_running and REOPEN_TUNNEL:
        recovery.reopen_tunnel(WORK)
    deadline = time.monotonic() + 300
    printed = offset
    BASE_URL = None
    while time.monotonic() < deadline:
        content = log_path.read_text(errors='replace')
        if len(content) > printed:
            print(content[printed:], end='')
            printed = len(content)
        urls = re.findall(r'Base URL:\\s*(https://[a-zA-Z0-9-]+\\.trycloudflare\\.com/v1)', content[offset:])
        if urls:
            BASE_URL = urls[-1]
            break
        if recovery.managed_process(WORK) is None:
            raise RuntimeError(f'API đã thoát. Gửi log phía trên; file {log_path}')
        time.sleep(2)
    if not BASE_URL:
        raise TimeoutError(f'Chưa sẵn sàng sau 300 giây; xem {log_path}, đừng nạp thêm model.')
    print('\\nGỬI AGENT:', BASE_URL, '| Model: datamate-business24')
    REOPEN_TUNNEL = False
''')

markdown('''
    ## Cell 6 — Kiểm API/provenance (không sinh token)
    Kiểm localhost trước, rồi đường dẫn công khai. Tunnel mới có thể cần ít giây để sẵn sàng.
    Lỗi public sau khi local OK có thể do tunnel/network; chưa có nghĩa weights hỏng.
''')
code('''
    #@title 6. Kiểm tra API và manifest
    def read_provenance(base_url):
        with urllib.request.urlopen(base_url + '/model-provenance', timeout=20) as response:
            metadata = json.load(response)
        expected = {'model': 'datamate-business24', 'training_complete': True, 'merge_complete': True,
                    'data_sha256': recovery.DATA_SHA, 'split_sha256': recovery.SPLIT_SHA,
                    'base_model': recovery.BASE_MODEL, 'base_revision': recovery.REVISION, 'examples': 633}
        for key, value in expected.items():
            if metadata.get(key) != value:
                raise ValueError(f'API provenance mismatch: {key}')
        return metadata
    read_provenance('http://127.0.0.1:8000/v1')
    for attempt in range(6):
        try:
            metadata = read_provenance(BASE_URL)
            break
        except ValueError:
            raise
        except Exception:
            if attempt == 5:
                raise
            time.sleep(5)
    (WORK / 'served-model-provenance.json').write_text(json.dumps(metadata, indent=2))
    print('LOCAL + PUBLIC API OK. Gửi agent:', BASE_URL)
    print('Giữ runtime hoạt động trong lượt đánh giá. Không cần gọi thử model nhiều lần.')
''')

markdown('''
    ## Cell 7 — Log/bàn giao
    Khi agent xác nhận đo xong mới dừng phiên. Mất runtime không mất adapter đã backup local;
    merged model và thư viện trong `/content` cần dựng lại ở runtime mới.
    Chưa có bằng chứng cần training thêm. Nếu cần nâng cấp, agent sẽ báo lý do và kế hoạch riêng.
''')
code('''
    #@title 7. Xem trạng thái và phần cuối log
    print('Process:', recovery.managed_process(WORK))
    print('Model:', MERGED)
    print('Base URL:', globals().get('BASE_URL', 'Chưa chạy cell 5'))
    print('\\n'.join((WORK / 'api.log').read_text(errors='replace').splitlines()[-20:]) if (WORK / 'api.log').exists() else 'Chưa có api.log')
''')

markdown('''
    ## Cell 8 — Chỉ dừng sau khi đã đo xong
    **Mặc định tắt**, nên Run all không tự dừng API vừa mở. Khi agent xác nhận xong, bật `DO_STOP` và chạy cell.
    Cell dừng process model/tunnel của notebook này, giữ các file. Muốn ngừng tính units, sau đó dùng menu Colab
    **Runtime → Disconnect and delete runtime** khi đã chắc backup ZIP nằm trên máy/Drive; thao tác đó xóa dữ liệu runtime.
''')
code('''
    #@title 8. Dừng API (mặc định bỏ qua)
    DO_STOP = False #@param {type:"boolean"}
    if DO_STOP:
        recovery.stop_api(WORK)
        print('Đã dừng process API/tunnel của notebook. File model vẫn còn cho tới khi runtime bị xóa.')
    else:
        print('Bỏ qua; API tiếp tục phục vụ agent.')
''')

nb = {'cells': cells, 'metadata': {'colab': {'name': '04_business24_restore_and_serve.ipynb'},
      'kernelspec': {'display_name': 'Python 3', 'language': 'python', 'name': 'python3'},
      'language_info': {'name': 'python'}, 'accelerator': 'GPU'}, 'nbformat': 4, 'nbformat_minor': 5}
for index, cell in enumerate(cells):
    cell['id'] = f'business24-{index:02}'
(ROOT / 'notebooks/04_business24_restore_and_serve.ipynb').write_text(json.dumps(nb, ensure_ascii=False, indent=2) + '\n')
