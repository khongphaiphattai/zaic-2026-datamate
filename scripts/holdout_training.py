"""Train a fresh-base LoRA with business-isolated SFT provenance.

API reference: https://huggingface.co/docs/trl/v0.25.0/en/sft_trainer
"""
import argparse
from collections import Counter
import hashlib
import importlib.metadata
import json
from pathlib import Path
import subprocess

BASE_MODEL = 'Qwen/Qwen2.5-Coder-7B-Instruct'


def read_jsonl(path):
    return [json.loads(l) for l in Path(path).read_text().splitlines() if l.strip()]


def validate_training_data(rows, split):
    train, validation = set(split['train_businesses']), set(split['validation_businesses'])
    if not train or not validation or train & validation or not rows:
        raise ValueError('Nonempty disjoint partitions and training examples are required')
    ids = set()
    for row in rows:
        if row['id'] in ids or row['business_id'] not in train:
            raise ValueError('Duplicate question or business outside training partition')
        ids.add(row['id'])
        messages = row['messages']
        if [m['role'] for m in messages] != ['system','user','assistant']:
            raise ValueError('Expected system/user/assistant messages')
        if not all(isinstance(m['content'],str) and m['content'] for m in messages):
            raise ValueError('Empty or invalid message')
        answer = json.loads(messages[-1]['content'])
        if answer['type'] != row['type']:
            raise ValueError('Inconsistent target type')
        if answer['type'] != 'unanswerable' and (not isinstance(answer.get('sql'),str) or not answer['sql'].strip()):
            raise ValueError('Numeric training targets must contain verified SQL')
    return {'examples':len(rows),'businesses':sorted({r['business_id'] for r in rows}),
            'types':dict(Counter(r['type'] for r in rows)), 'overlap_businesses':[]}


def completion_records(rows):
    return [{'prompt':r['messages'][:-1],'completion':r['messages'][-1:]} for r in rows]


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--data', default='/content/zaic-holdout/train-only-sft.jsonl')
    p.add_argument('--split', default='/content/zaic-holdout/split.json')
    p.add_argument('--manifest', default='/content/zaic-holdout/train-only-sft.manifest.json')
    p.add_argument('--revision', help='Immutable base-model commit SHA; resolved and recorded if omitted')
    p.add_argument('--output', default='/content/qwen2.5-coder-7b-lora-business24')
    p.add_argument('--max-length',type=int,default=8192)
    p.add_argument('--preflight-only',action='store_true')
    args = p.parse_args()
    rows = read_jsonl(args.data)
    split = json.loads(Path(args.split).read_text())
    audit = validate_training_data(rows,split)
    expected = json.loads(Path(args.manifest).read_text())
    for key, filename in [('data_sha256', args.data), ('split_sha256', args.split)]:
        if hashlib.sha256(Path(filename).read_bytes()).hexdigest() != expected[key]:
            raise ValueError(f'Input SHA256 does not match manifest: {filename}')
    output = Path(args.output)
    if output.exists():
        raise FileExistsError(f'Refusing to overwrite checkpoint: {output}')
    # Heavy GPU dependencies are imported only on the training runtime.
    import torch
    from datasets import Dataset
    from transformers import AutoTokenizer, AutoModelForCausalLM, set_seed
    from peft import LoraConfig, TaskType
    from trl import SFTConfig, SFTTrainer
    set_seed(42)
    from huggingface_hub import model_info
    revision = model_info(BASE_MODEL, revision=args.revision).sha
    tokenizer = AutoTokenizer.from_pretrained(BASE_MODEL,revision=revision,trust_remote_code=False)
    if not revision:
        raise ValueError('Cannot resolve immutable base-model revision')
    lengths = [len(tokenizer.apply_chat_template(r['messages'],tokenize=True,add_generation_prompt=False)) for r in rows]
    audit.update(base_model=BASE_MODEL,base_revision=revision,seed=42,
                 data_sha256=hashlib.sha256(Path(args.data).read_bytes()).hexdigest(),
                 split_sha256=hashlib.sha256(Path(args.split).read_bytes()).hexdigest(),
                 max_tokens=max(lengths),overlength_examples=sum(n>args.max_length for n in lengths),
                 versions={k:importlib.metadata.version(k) for k in ('torch','transformers','trl','peft','datasets','accelerate')},
                 scope='SFT-business isolation only; historical rule/prompt tuning and base pretraining are not unseen')
    print(json.dumps(audit,indent=2),flush=True)
    Path(args.data).with_suffix('.token-audit.json').write_text(json.dumps(audit,indent=2))
    if audit['overlength_examples']:
        raise ValueError('Some samples exceed max length; refusing silent target truncation')
    if args.preflight_only:
        return
    if not torch.cuda.is_available() or not torch.cuda.is_bf16_supported():
        raise RuntimeError('Training requires a CUDA GPU with bfloat16 support')
    free,_ = torch.cuda.mem_get_info()
    if free < 28 * 1024**3:
        raise RuntimeError('Need at least 28 GiB free VRAM. Restart the old inference kernel before training.')
    model = AutoModelForCausalLM.from_pretrained(BASE_MODEL,revision=revision,torch_dtype=torch.bfloat16,
                                               device_map={'':0},trust_remote_code=False,attn_implementation='sdpa')
    model.config.use_cache = False
    config = SFTConfig(output_dir=str(output),num_train_epochs=3,per_device_train_batch_size=1,
                       gradient_accumulation_steps=16,learning_rate=2e-4,lr_scheduler_type='cosine',
                       warmup_ratio=0.05,logging_steps=5,save_strategy='epoch',save_total_limit=2,
                       bf16=True,max_length=args.max_length,packing=False,completion_only_loss=True,
                       gradient_checkpointing=True,gradient_checkpointing_kwargs={'use_reentrant':False},
                       seed=42,data_seed=42,report_to='none',optim='adamw_torch')
    lora = LoraConfig(task_type=TaskType.CAUSAL_LM,r=16,lora_alpha=32,lora_dropout=0.05,
                      target_modules=['q_proj','k_proj','v_proj','o_proj','gate_proj','up_proj','down_proj'],bias='none')
    trainer = SFTTrainer(model=model,args=config,train_dataset=Dataset.from_list(completion_records(rows)),
                         processing_class=tokenizer,peft_config=lora)
    # No validation labels or validation loss are used for checkpoint selection.
    trainer.train()
    trainer.save_model(str(output))
    tokenizer.save_pretrained(output)
    audit['training_complete'] = True
    audit['trainer_log_history'] = trainer.state.log_history
    (output/'training_manifest.json').write_text(json.dumps(audit,indent=2,default=str))
    (output/'training_data.jsonl').write_bytes(Path(args.data).read_bytes())
    (output/'split.json').write_bytes(Path(args.split).read_bytes())
    with (output/'environment.freeze.txt').open('w') as f:
        subprocess.run(['python','-m','pip','freeze'],stdout=f,check=True)
    print(f'Training complete: {output}',flush=True)


if __name__=='__main__':
    main()
