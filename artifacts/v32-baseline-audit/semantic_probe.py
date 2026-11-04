"""Read-only semantic evidence scan, not a classifier of Public correctness."""
import json,re,hashlib,unicodedata
from pathlib import Path
from zaic_2026_datamate.data_loader import DataLoader
from zaic_2026_datamate.sales_status import classify_sales_status
OUT=Path(__file__).parent
rows=json.loads((OUT/'answers.json').read_text())
loader=DataLoader('data/public_test/public_test/data')
findings=[]
for bid in sorted({r['business_id'] for r in rows}):
 ctx=loader.load_business(bid)
 item={'business':bid,'statuses':[],'address_evidence':None}
 subset=[r for r in rows if r['business_id']==bid]
 for table in sorted({t for r in subset for t in re.findall(r'FROM\s+"?([a-z_0-9]+)',r.get('sql') or '',re.I)}):
  df=ctx.tables[table]
  for col in df.columns:
   if not any(re.search(r'CAST\('+re.escape(col)+r' AS VARCHAR\)',r.get('sql') or '',re.I) for r in subset):continue
   for raw,n in df[col].fillna('<NULL>').value_counts().items():
    norm=unicodedata.normalize('NFD',str(raw).lower().replace('đ','d')); norm=''.join(c for c in norm if not unicodedata.combining(c))
    legacy=any(t in str(raw).lower() for t in ['hủy','huỷ','cancel','trả','return','hoàn hàng','hoan hang','hoàn trả','hoan tra','hàng trả','hang tra','đổi trả','doi tra','không giao','khong giao','thất bại','that bai'])
    canonical=classify_sales_status(str(raw))
    item['statuses'].append({'table':table,'column':col,'raw':str(raw),'rows':int(n),'canonical':canonical,'legacy_excluded':legacy,'known_disagreement':canonical is not None and legacy!=(canonical in ('cancelled','returned'))})
 if any(r['type']=='unanswerable' for r in subset):
  columns=[]; matches=[]
  for t,df in ctx.tables.items():
   if t=='calendar':continue
   columns.extend({'table':t,'column':c} for c in df.columns if any(k in c for k in ['dia_chi','duong','address','street']))
   for c in df.select_dtypes(include=['object','string']).columns:
    values=df[c].dropna().astype(str)
    normalized=values.map(lambda s: ''.join(ch for ch in unicodedata.normalize('NFD',s.lower().replace('đ','d')) if not unicodedata.combining(ch)))
    hit=normalized.str.contains(r'ly thuong kiet|nguyen van nghi|mai chi tho|nguyen van linh',regex=True)
    if hit.any():matches.append({'table':t,'column':c,'matching_rows':int(hit.sum())})
  files=[p for p in ctx.data_dir.rglob('*') if p.is_file()]
  item['address_evidence']={'address_named_columns':columns,'street_name_hits_any_text':matches,'files':[{'name':str(p.relative_to(ctx.data_dir)),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in files],'interpretation':'No address column and no queried street mentions supports missing-data refusal, but schema/content scan is not universal semantic proof.'}
 findings.append(item)
 ctx.duckdb_conn.close()
 print(bid,'disagreements',sum(s['known_disagreement'] for s in item['statuses']),flush=True)
(OUT/'semantic-evidence.json').write_text(json.dumps(findings,ensure_ascii=False,indent=2)+'\n')
