"""Independent Pandas detail aggregation vs legacy order-header revenue."""
import json
from pathlib import Path
from zaic_2026_datamate.data_loader import DataLoader
ctx=DataLoader('data/public_test/public_test/data').load_business('dev_010')
h=ctx.tables['don_hang']; d=ctx.tables['chi_tiet_don']
s=d.groupby('ma_bill',dropna=False)['cong'].sum()
p=h.copy();p['detail_revenue']=p['so_ct'].map(s);p['legacy_revenue']=p['thanh_tien']+p['tien_giam'].fillna(0)-p['phi_van_chuyen'].fillna(0)
matched=p['detail_revenue'].notna();diff=p.loc[matched & ((p['detail_revenue']-p['legacy_revenue']).abs()>0.5)]
result={'business':'dev_010','scope':'Independent Pandas aggregation with shared loader. Header/detail README semantics, no Public targets. No production join.', 'header_rows':len(h),'header_unique_orders':int(h['so_ct'].nunique()),'detail_orders':int(d['ma_bill'].nunique()),'matched_header_rows':int(matched.sum()),'mismatching_header_rows':len(diff),'header_total_matched':float(p.loc[matched,'legacy_revenue'].sum()),'detail_total_matched':float(p.loc[matched,'detail_revenue'].sum()),'mismatch_examples':diff[['so_ct','detail_revenue','legacy_revenue']].head(5).to_dict('records')}
Path('artifacts/v32-baseline-audit/header-detail-probe.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,default=str)+'\n');print(json.dumps(result,ensure_ascii=False));ctx.duckdb_conn.close()
