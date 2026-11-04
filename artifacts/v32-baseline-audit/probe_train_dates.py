"""Counterfactual date-only probe of five known Train errors; not a fix."""
import json
from pathlib import Path
from zaic_2026_datamate.data_loader import DataLoader
rows=[json.loads(s) for s in Path('artifacts/v31-status-train/trace.jsonl').read_text().splitlines()]
loader=DataLoader('data/train/train/data')
results=[]
for bid, date in [('biz_105','ngay_giao_dich'),('biz_117','ngay_lap')]:
 ctx=loader.load_business(bid)
 for r in rows:
  if r['business_id']!=bid or r.get('correct') is not False or r['type']=='abstain': continue
  original=r['sql']; revised=original.replace('TRY_CAST(gio AS DATE)',f'TRY_CAST({date} AS DATE)')
  before=ctx.scalar(original); after=ctx.scalar(revised)
  results.append({'id':r['id'],'business_id':bid,'question':r['question'],'original':before,'date_only_result':after,'target':r['target'],'changed':before!=after,'new_matches_target':after==r['target']['answer'],'original_sql':original,'probe_sql':revised,'scope':'Date-column-only counterfactual; not production correction or complete semantic validation.'})
 ctx.duckdb_conn.close()
Path('artifacts/v32-baseline-audit/train-date-probe.json').write_text(json.dumps(results,ensure_ascii=False,indent=2)+'\n')
for r in results: print(r['business_id'],r['original'],r['date_only_result'],r['target']['answer'],r['new_matches_target'])
