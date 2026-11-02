from pathlib import Path
import json
import duckdb
import pandas as pd
import pytest
from zaic_2026_datamate.data_loader import BusinessContext
from zaic_2026_datamate.revenue_metric import verify_revenue_metric


def context(readme):
 df=pd.DataFrame({'tien_hang':[100,200],'tong_tien':[110,200],'status':['Hoàn thành']*2,'ngay':['2026-01-01']*2})
 con=duckdb.connect();con.register('sales',df)
 return BusinessContext(business_id='unseen_mock',data_dir=Path('.'),tables={'sales':df},duckdb_conn=con,readme=readme)


README='''Mỗi dòng một đơn hàng.
## Cột trong `sales.csv`
- **Tien hang**: tiền hàng của đơn: cộng thành tiền các mặt hàng, trước giảm giá của đơn, chưa gồm phí giao
- **TongTien**: số khách phải trả cho cả đơn (đã tính giảm giá của đơn và phí giao)
'''


def test_reject_payable_even_alias_comment_and_equal_values():
 ctx=context(README)
 for sql in ['SELECT SUM(tong_tien) FROM sales','SELECT SUM(tong_tien) AS tien_hang FROM sales',
             'SELECT SUM(tong_tien) /* SUM(tien_hang) */ FROM sales',
             "SELECT SUM(tong_tien) FROM sales WHERE tien_hang=tong_tien"]:
  decision=verify_revenue_metric(ctx,sql)
  assert not decision.allowed and decision.reason
 assert verify_revenue_metric(ctx,'SELECT ROUND(SUM(s.tien_hang)) AS answer FROM sales s').allowed


@pytest.mark.parametrize('sql',[
 'WITH x AS (SELECT tong_tien AS tien_hang FROM sales) SELECT SUM(tien_hang) FROM x',
 'SELECT SUM(tien_hang)+1 FROM sales', 'SELECT SUM(DISTINCT tien_hang) FROM sales',
 'SELECT SUM(tien_hang) FILTER(WHERE tong_tien>100) FROM sales',
 'SELECT AVG(tien_hang) FROM sales', 'SELECT SUM(tien_hang) FROM (SELECT * FROM sales) x',
 'SELECT SUM(tien_hang),SUM(tong_tien) FROM sales', 'SELECT SUM(tien_hang) FROM sales;SELECT 1',
 'SELECT ROUND(SUM(tien_hang),-3) FROM sales',
])
def test_unverified_lineage_fails_closed(sql):
 assert not verify_revenue_metric(context(README),sql).allowed


def test_missing_conflicting_and_other_file_definitions():
 for text in ['',README.replace('sales.csv','purchases.csv'),README+'- **Tien hang**: tổng khách phải trả\n']:
  assert not verify_revenue_metric(context(text),'SELECT SUM(tien_hang) FROM sales').allowed


def test_line_amount_needs_line_grain():
 text='''Mỗi dòng một sản phẩm.
## Cột trong `sales.csv`
- **TongTien**: thành tiền sau giảm
'''
 assert verify_revenue_metric(context(text),'SELECT SUM(tong_tien) FROM sales').allowed
 assert not verify_revenue_metric(context(text.replace('Mỗi dòng một sản phẩm','Mỗi dòng một đơn')),'SELECT SUM(tong_tien) FROM sales').allowed


def test_guard_runs_before_execution_and_does_not_repair():
 from zaic_2026_datamate.llm_client import MockLLMClient
 from zaic_2026_datamate.slow_path import SlowPathEngine
 ctx=context(README); calls=[]
 def generate(_):
  calls.append(True)
  return json.dumps({'type':'number','sql':'SELECT SUM(tong_tien) AS tien_hang FROM sales','confidence':0.99})
 engine=SlowPathEngine(MockLLMClient(generate),verify_daily_revenue=True)
 assert engine.resolve_slow_path('Doanh thu hôm nay bao nhiêu?',ctx,'2026-01-01')[0]=='abstain'
 assert len(calls)==1 and ctx.last_sql is None
 assert not engine.last_metric_check.allowed and not engine.last_execution_succeeded


def test_corrected_sql_is_checked_again():
 from zaic_2026_datamate.llm_client import MockLLMClient
 from zaic_2026_datamate.slow_path import SlowPathEngine
 ctx=context(README); replies=iter(['SELEC broken','SELECT SUM(tong_tien) FROM sales'])
 engine=SlowPathEngine(MockLLMClient(lambda _:json.dumps({'type':'number','sql':next(replies),'confidence':0.99})),verify_daily_revenue=True)
 assert engine.resolve_slow_path('Doanh thu hôm nay bao nhiêu?',ctx,'2026-01-01')[0]=='abstain'
 assert not engine.last_metric_check.allowed


def test_verified_metric_can_execute():
 from zaic_2026_datamate.llm_client import MockLLMClient
 from zaic_2026_datamate.slow_path import SlowPathEngine
 ctx=context(README)
 engine=SlowPathEngine(MockLLMClient(lambda _:json.dumps({'type':'number','sql':'SELECT SUM(tien_hang) FROM sales','confidence':0.99})),verify_daily_revenue=True)
 assert engine.resolve_slow_path('Doanh thu hôm nay bao nhiêu?',ctx,'2026-01-01')[:2]==('number',300)
 assert engine.last_metric_check.allowed and engine.last_execution_succeeded


def test_contradictory_definition_and_undocumented_modifiers():
 contradictory=README.replace('chưa gồm phí giao','chưa gồm phí giao nhưng đã cộng VAT')
 assert not verify_revenue_metric(context(contradictory),'SELECT SUM(tien_hang) FROM sales').allowed
 for sql in ['SELECT SUM(tien_hang) FROM sales GROUP BY status','SELECT SUM(tien_hang) FROM sales LIMIT 1',
             'SELECT SUM(x.tien_hang) FROM sales s','SELECT SUM(tien_hang) FROM sales a(tien_hang,tong_tien,status,ngay)']:
  assert not verify_revenue_metric(context(README),sql).allowed


def test_file_definition_does_not_cover_prefixed_auxiliary_table():
 ctx=context(README)
 ctx.tables['sales_returns']=ctx.tables['sales']
 ctx.duckdb_conn.register('sales_returns',ctx.tables['sales'])
 assert not verify_revenue_metric(ctx,'SELECT SUM(tien_hang) FROM sales_returns').allowed


def test_other_section_cannot_supply_line_grain():
 text='''## Cột trong `sales.csv`
- **TongTien**: thành tiền sau giảm
## Cột trong `stock.csv`
Mỗi dòng một sản phẩm.
'''
 assert not verify_revenue_metric(context(text),'SELECT SUM(tong_tien) FROM sales').allowed
