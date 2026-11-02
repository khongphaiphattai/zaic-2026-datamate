# Dán toàn bộ nội dung này vào cell MỚI cuối notebook 03, sau khi đã backup adapter.
# Chạy trong runtime 03 hiện tại; không restart hoặc chạy lại cell training.
import gc
import hashlib
import json
from pathlib import Path

ADAPTER = Path('/content/qwen2.5-coder-7b-lora-business24')
MERGED = Path('/content/qwen2.5-coder-7b-datamate-business24')
manifest = json.loads((ADAPTER / 'training_manifest.json').read_text())
assert manifest['training_complete'] is True
assert manifest['examples'] == 633 and not manifest['overlap_businesses']
assert manifest['base_model'] == 'Qwen/Qwen2.5-Coder-7B-Instruct'
assert manifest['base_revision'] == 'c03e6d358207e414f1eca0bb1891e29f1db0e242'
assert manifest['data_sha256'] == '55c2918798e68452be79cf69bc9244311d900af60533554d2fa436bb414a908e'
for filename, key in [('training_data.jsonl', 'data_sha256'), ('split.json', 'split_sha256')]:
    assert hashlib.sha256((ADAPTER / filename).read_bytes()).hexdigest() == manifest[key]
if MERGED.exists():
    raise FileExistsError(f'Không ghi đè thư mục đã có: {MERGED}')

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer
from peft import PeftModel

print('Đang nạp đúng Qwen revision trên RAM hệ thống...', flush=True)
base = AutoModelForCausalLM.from_pretrained(
    manifest['base_model'], revision=manifest['base_revision'],
    torch_dtype=torch.bfloat16, device_map='cpu', trust_remote_code=False,
)
model_with_adapter = PeftModel.from_pretrained(base, str(ADAPTER), is_trainable=False)
print('Đang gộp adapter...', flush=True)
merged_model = model_with_adapter.merge_and_unload(safe_merge=True)
merged_model.eval()
merged_model.config.use_cache = True
merged_model.save_pretrained(str(MERGED), safe_serialization=True, max_shard_size='5GB')
tokenizer = AutoTokenizer.from_pretrained(str(ADAPTER), trust_remote_code=False)
tokenizer.save_pretrained(str(MERGED))
(MERGED / 'training_manifest.json').write_bytes((ADAPTER / 'training_manifest.json').read_bytes())
weights = list(MERGED.glob('*.safetensors'))
assert weights and (MERGED / 'config.json').is_file()
merge_report = {
    'merge_complete': True,
    'base_model': manifest['base_model'], 'base_revision': manifest['base_revision'],
    'data_sha256': manifest['data_sha256'], 'split_sha256': manifest['split_sha256'],
    'weights_bytes': sum(p.stat().st_size for p in weights),
    'weights_files': sorted(p.name for p in weights),
    'validation_pa': None,
}
(MERGED / 'merge_manifest.json').write_text(json.dumps(merge_report, indent=2))
del merged_model, model_with_adapter, base
gc.collect()
if torch.cuda.is_available():
    torch.cuda.empty_cache()
print('MERGE HOÀN TẤT:', MERGED, flush=True)
print('Bước tiếp theo: phục vụ model từ đường dẫn mới này và đánh giá 6 business giữ riêng.')
