import hashlib
import importlib.util
import json
from pathlib import Path
import zipfile

import pytest


def load_recovery():
    spec = importlib.util.spec_from_file_location('recovery', 'notebooks/business24_recovery.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def backup_fixture(tmp_path, recovery):
    files = {'training_data.jsonl': b'train', 'split.json': b'split',
             'adapter_model.safetensors': b'weights', 'adapter_config.json': b'{}',
             'tokenizer_config.json': b'{}', 'tokenizer.json': b'{}'}
    manifest = {'training_complete': True, 'examples': 633, 'overlap_businesses': [],
                'base_model': recovery.BASE_MODEL, 'base_revision': recovery.REVISION,
                'data_sha256': hashlib.sha256(files['training_data.jsonl']).hexdigest(),
                'split_sha256': hashlib.sha256(files['split.json']).hexdigest()}
    files['training_manifest.json'] = json.dumps(manifest).encode()
    archive = tmp_path / 'backup.zip'
    with zipfile.ZipFile(archive, 'w') as z:
        for name, data in files.items():
            z.writestr(name, data)
    recovery.DATA_SHA = manifest['data_sha256']
    recovery.SPLIT_SHA = manifest['split_sha256']
    return archive, hashlib.sha256(archive.read_bytes()).hexdigest()


def test_restore_verified_backup_and_reuse_without_zip(tmp_path):
    recovery = load_recovery()
    archive, digest = backup_fixture(tmp_path, recovery)
    target = tmp_path / 'adapter'
    assert recovery.restore_backup(archive, target, digest)['examples'] == 633
    archive.unlink()
    assert recovery.restore_backup(archive, target, digest)['training_complete']
    (target / 'training_data.jsonl').write_text('corrupted')
    with pytest.raises(ValueError, match='training_data'):
        recovery.restore_backup(archive, target, digest)


def test_wrong_archive_leaves_destination_absent(tmp_path):
    recovery = load_recovery()
    archive, _ = backup_fixture(tmp_path, recovery)
    target = tmp_path / 'adapter'
    with pytest.raises(ValueError, match='SHA256'):
        recovery.restore_backup(archive, target, 'wrong')
    assert not target.exists()


def test_merged_check_rejects_missing_shard_and_mismatched_identity(tmp_path):
    recovery = load_recovery()
    archive, digest = backup_fixture(tmp_path, recovery)
    target = tmp_path / 'merged'
    recovery.restore_backup(archive, target, digest)
    report = {'merge_complete': True, 'base_model': recovery.BASE_MODEL,
              'base_revision': recovery.REVISION, 'data_sha256': recovery.DATA_SHA,
              'split_sha256': recovery.SPLIT_SHA, 'weights_files': ['model.safetensors'],
              'weights_bytes': 7}
    (target / 'merge_manifest.json').write_text(json.dumps(report))
    (target / 'config.json').write_text('{}')
    with pytest.raises(ValueError, match='weights'):
        recovery.check_merged(target)
    (target / 'model.safetensors').write_bytes(b'1234567')
    assert recovery.check_merged(target)['merge_complete']
    report['base_revision'] = 'wrong'
    (target / 'merge_manifest.json').write_text(json.dumps(report))
    with pytest.raises(ValueError, match='base_revision'):
        recovery.check_merged(target)


def test_notebook_is_standalone_compiles_and_has_no_training():
    import ast
    nb = json.loads(Path('notebooks/04_business24_restore_and_serve.ipynb').read_text())
    sources = [''.join(c['source']) for c in nb['cells'] if c['cell_type'] == 'code']
    for source in sources:
        compile(source, '<cell>', 'exec')
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'smoke' for t in node.targets):
                compile(ast.literal_eval(node.value.func.value), '<CUDA smoke script>', 'exec')
    embedded = next(n.value for n in ast.parse(sources[0]).body
                    if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'EMBEDDED_SOURCES' for t in n.targets))
    for name, content in ast.literal_eval(embedded).items():
        assert content == Path('notebooks', name).read_text()
    assert 'subprocess.run(TRAIN' not in ''.join(sources)
    assert 'SFTTrainer(' not in ''.join(sources)
    assert 'DO_STOP = False' in ''.join(sources)


def test_managed_api_reuses_process_and_stops_only_its_group(tmp_path):
    import sys
    import time
    recovery = load_recovery()
    api = tmp_path / 'fake_api.py'
    api.write_text('def start_business24_api():\n    print("ready", flush=True)\n')
    work = tmp_path / 'work'
    pid = recovery.start_api(sys.executable, api, work)
    try:
        for _ in range(50):
            if 'ready' in (work / 'api.log').read_text():
                break
            time.sleep(0.02)
        assert recovery.start_api(sys.executable, api, work) == pid
        recovery.reopen_tunnel(work)
        for _ in range(50):
            if (work / 'api.log').read_text().count('ready') == 2:
                break
            time.sleep(0.02)
        assert (work / 'api.log').read_text().count('ready') == 2
    finally:
        recovery.stop_api(work)
    assert recovery.managed_process(work) is None
    assert recovery.process_identity(pid) is None
