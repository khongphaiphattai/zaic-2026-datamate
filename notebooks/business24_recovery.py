"""Verified backup recovery and managed serving; no training dependencies."""
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time
import zipfile

BASE_MODEL = 'Qwen/Qwen2.5-Coder-7B-Instruct'
REVISION = 'c03e6d358207e414f1eca0bb1891e29f1db0e242'
DATA_SHA = '55c2918798e68452be79cf69bc9244311d900af60533554d2fa436bb414a908e'
SPLIT_SHA = 'ba0a70ea94ecf36c6d74b308ba7bcf29358f1ec693fc6cf73167c0e59b6c4ffa'
BACKUP_SHA = '65914f4369f8409854b75c220ef3999077d4f73b2761c4c48dc71dbde7604965'


def ensure_pip(python, bootstrap_python, run_logged):
    """A Python executable may exist in a venv whose pip seed failed."""
    command = [str(python), '-m', 'pip', '--version']
    if subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT).returncode == 0:
        return
    # pip >=22.3 can manage a target environment even when it has no pip.
    run_logged([str(bootstrap_python), '-m', 'pip', '--python', str(python), 'install', 'pip'], 'install.log')
    if subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT).returncode:
        raise RuntimeError('pip trong venv chưa hoạt động; dừng và gửi install.log, không chạy cell tiếp theo')


def check_manifest(directory, verify_data=True):
    directory = Path(directory)
    manifest = json.loads((directory / 'training_manifest.json').read_text())
    expected = {'training_complete': True, 'examples': 633, 'overlap_businesses': [],
                'base_model': BASE_MODEL, 'base_revision': REVISION,
                'data_sha256': DATA_SHA, 'split_sha256': SPLIT_SHA}
    for key, value in expected.items():
        if manifest.get(key) != value:
            raise ValueError(f'Manifest mismatch: {key}')
    if verify_data:
        for name, key in [('training_data.jsonl', 'data_sha256'), ('split.json', 'split_sha256')]:
            if hashlib.sha256((directory / name).read_bytes()).hexdigest() != manifest[key]:
                raise ValueError(f'Corrupted {name}')
        for name in ['adapter_model.safetensors', 'adapter_config.json', 'tokenizer.json', 'tokenizer_config.json']:
            if not (directory / name).is_file() or not (directory / name).stat().st_size:
                raise ValueError(f'Missing adapter file: {name}')
    return manifest


def restore_backup(archive, target, expected_sha=BACKUP_SHA):
    target = Path(target)
    if target.exists():
        return check_manifest(target)
    archive = Path(archive)
    with archive.open('rb') as stream:
        if hashlib.file_digest(stream, 'sha256').hexdigest() != expected_sha:
            raise ValueError('Backup SHA256 mismatch; upload đúng business24-adapter-backup.zip')
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as z:
        if z.testzip() is not None:
            raise ValueError('Backup ZIP CRC failed')
        if len(z.namelist()) != len(set(z.namelist())):
            raise ValueError('Duplicate ZIP entries')
        for name in z.namelist():
            if Path(name).is_absolute() or '..' in Path(name).parts:
                raise ValueError('Invalid ZIP path')
        with tempfile.TemporaryDirectory(prefix='business24-restore-', dir=target.parent) as temporary:
            staged = Path(temporary) / 'adapter'
            z.extractall(staged)
            manifest = check_manifest(staged)
            staged.rename(target)
    return manifest


def check_merged(directory):
    directory = Path(directory)
    check_manifest(directory, verify_data=False)
    merge = json.loads((directory / 'merge_manifest.json').read_text())
    for key, expected in {'merge_complete': True, 'base_model': BASE_MODEL, 'base_revision': REVISION,
                          'data_sha256': DATA_SHA, 'split_sha256': SPLIT_SHA}.items():
        if merge.get(key) != expected:
            raise ValueError(f'Merge manifest mismatch: {key}')
    weights = merge.get('weights_files', [])
    if not weights or any(Path(name).name != name for name in weights):
        raise ValueError('Invalid weights list')
    if any(not (directory / name).is_file() for name in weights):
        raise ValueError('Missing merged weights shard')
    if any((directory / name).stat().st_size == 0 for name in weights):
        raise ValueError('Empty merged weights shard')
    if sum((directory / name).stat().st_size for name in weights) != merge.get('weights_bytes'):
        raise ValueError('Incomplete merged weights size')
    if not (directory / 'config.json').is_file() or not (directory / 'tokenizer_config.json').is_file():
        raise ValueError('Missing merged config/tokenizer')
    index = directory / 'model.safetensors.index.json'
    if index.is_file() and set(json.loads(index.read_text())['weight_map'].values()) != set(weights):
        raise ValueError('Merged weights index mismatch')
    return merge


def process_identity(pid):
    """Linux PID start time distinguishes an owned process from a reused PID."""
    try:
        fields = Path(f'/proc/{pid}/stat').read_text().rsplit(') ', 1)[1].split()
        return None if fields[0] == 'Z' else fields[19]
    except (OSError, IndexError):
        return None


def managed_process(work):
    state = Path(work) / 'api-process.json'
    if state.is_file():
        info = json.loads(state.read_text())
        if process_identity(info['pid']) == info['start_time']:
            return info
    return None


def stop_api(work):
    info = managed_process(work)
    if info:
        # Only the session started here (start_new_session=True) may be stopped.
        if os.getpgid(info['pid']) != info['pid']:
            raise RuntimeError('Process group mismatch; không dừng process lạ')
        os.killpg(info['pid'], signal.SIGTERM)
        for _ in range(50):
            if process_identity(info['pid']) is None:
                break
            time.sleep(0.1)
        if process_identity(info['pid']) is not None:
            raise RuntimeError('API chưa dừng; kiểm tra trước khi mở lại')
    (Path(work) / 'api-process.json').unlink(missing_ok=True)


def start_api(python, api_script, work, restart=False):
    work = Path(work)
    if restart:
        stop_api(work)
    info = managed_process(work)
    if info:
        return info['pid']
    work.mkdir(parents=True, exist_ok=True)
    runner = work / 'serve_business24.py'
    runner.write_text(
        'import runpy, threading, signal\n'
        f'namespace = runpy.run_path({str(Path(api_script).resolve())!r})\n'
        'reopen = threading.Event()\n'
        'signal.signal(signal.SIGUSR1, lambda *_: reopen.set())\n'
        'namespace["start_business24_api"]()\n'
        'while True:\n'
        '    reopen.wait()\n'
        '    reopen.clear()\n'
        '    state = namespace["start_business24_api"].__globals__\n'
        '    tunnel = state.get("_business24_tunnel")\n'
        '    if tunnel is not None and tunnel.poll() is None:\n'
        '        tunnel.terminate()\n'
        '        tunnel.wait(timeout=10)\n'
        '    state["_business24_tunnel"] = None\n'
        '    namespace["start_business24_api"]()\n'
    )
    with (work / 'api.log').open('w') as log:
        proc = subprocess.Popen([str(python), '-u', str(runner)], stdout=log,
                                stderr=subprocess.STDOUT, start_new_session=True)
    identity = process_identity(proc.pid)
    if identity is None:
        raise RuntimeError('API đã thoát; xem api.log')
    state = {'pid': proc.pid, 'start_time': identity}
    (work / 'api-process.json').write_text(json.dumps(state))
    return proc.pid


def reopen_tunnel(work):
    info = managed_process(work)
    if info is None:
        raise RuntimeError('API chưa chạy; chạy cell mở API trước')
    os.kill(info['pid'], signal.SIGUSR1)
