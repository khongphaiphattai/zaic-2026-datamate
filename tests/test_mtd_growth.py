from datetime import date

import duckdb
import pandas as pd
import pytest

from zaic_2026_datamate.data_loader import BusinessContext
from zaic_2026_datamate import monthly_growth
from zaic_2026_datamate.monthly_growth import GrowthIntent, compile_growth_windows


def test_mtd_uses_same_elapsed_day_in_previous_month():
    windows = compile_growth_windows(GrowthIntent('month_to_date',2025,3),'2025-03-09')
    assert (windows.previous_start,windows.previous_end,windows.current_start,windows.current_end)==(
        date(2025,2,1),date(2025,2,9),date(2025,3,1),date(2025,3,9))


@pytest.fixture
def context():
    frame = pd.DataFrame({
        'ma_don_hang':['a','b','later1','later2','c','c','d','later3','later4','cancel','return'],
        'ngay':['2025-02-01','2025-02-09','2025-02-10','2025-02-28',
                '2025-03-01','2025-03-01','2025-03-09 23:59:59','2025-03-10','2025-03-31',
                '2025-03-05','2025-03-06'],
        'trang_thai':['Đã hoàn thành']*9+['Đã hủy','Đã trả hàng'],
        'thanh_tien':[100,100,9000,10000,40,60,200,90000,100000,999,888],
    })
    # Normalize just as DataLoader does; mixed raw date parsing is not this seam.
    frame['ngay']=pd.to_datetime(frame['ngay'],format='mixed')
    con=duckdb.connect(':memory:');con.register('ban_hang',frame)
    readme='''Mỗi dòng một sản phẩm.
## Cột trong `ban_hang.csv`
- **ma_don_hang**: mã đơn
- **ngay**: ngày bán
- **trang_thai**: trạng thái đơn
- **thanh_tien**: thành tiền sau giảm
'''
    try:
        yield BusinessContext(business_id='fixture',data_dir='.',tables={'ban_hang':frame},
                              readme=readme,duckdb_conn=con)
    finally:
        con.close()


def test_mtd_computes_two_partial_windows_excluding_cancel_return_and_later_sales(context):
    audit={}
    result=monthly_growth.resolve_growth_intent(GrowthIntent('month_to_date',2025,3),context,'2025-03-09',audit)
    assert result==('percent',50.0,None)
    assert audit['previous_revenue']==200 and audit['current_revenue']==300
    assert audit['previous_end']=='2025-02-09' and audit['current_end']=='2025-03-09'


@pytest.mark.parametrize('anchor,previous_start,previous_end',[
    ('2025-03-28','2025-02-01','2025-02-28'),
    ('2025-03-29','2025-02-01','2025-02-28'),
    ('2025-03-30','2025-02-01','2025-02-28'),
    ('2025-03-31','2025-02-01','2025-02-28'),
    ('2024-03-31','2024-02-01','2024-02-29'),
    ('2024-02-29','2024-01-01','2024-01-29'),
    ('2025-05-31','2025-04-01','2025-04-30'),
    ('2026-01-01','2025-12-01','2025-12-01'),
    ('2026-01-31','2025-12-01','2025-12-31'),
])
def test_calendar_edges(anchor,previous_start,previous_end):
    today=date.fromisoformat(anchor)
    windows=compile_growth_windows(GrowthIntent('month_to_date',today.year,today.month),anchor)
    assert windows.previous_start.isoformat()==previous_start
    assert windows.previous_end.isoformat()==previous_end
    assert windows.current_end==today and windows.current_start.day==1


@pytest.mark.parametrize('intent,anchor',[
    (GrowthIntent('month_to_date',2025,2),'2025-03-09'),
    (GrowthIntent('full_month',2025,3),'2025-03-30'),
    (GrowthIntent('year_over_year',2025,3),'2025-03-31'),
    (GrowthIntent('month_to_date',True,3),'2025-03-31'),
    (GrowthIntent('month_to_date',2025,True),'2025-03-31'),
    (GrowthIntent('month_to_date',2025,13),'2025-03-31'),
    (GrowthIntent('month_to_date',1900,3),'1900-03-31'),
    (GrowthIntent('month_to_date',2100,3),'2100-03-31'),
    (GrowthIntent('month_to_date',2025,3),'bad'),
    (GrowthIntent('month_to_date',2025,3),None),
    ({'period':'month_to_date','year':2025,'month':3},'2025-03-31'),
])
def test_invalid_execution_request_refuses_before_reading_context(intent,anchor):
    assert monthly_growth.resolve_growth_intent(intent,None,anchor)[0]=='abstain'


@pytest.mark.parametrize('column,index,value',[
    ('ma_don_hang',0,None),('ma_don_hang',0,''),
    ('ngay',4,None),('ngay',4,pd.Timestamp('2025-03-02')),
    ('trang_thai',0,'Unknown'),('trang_thai',4,'Đã trả hàng'),
    ('thanh_tien',0,None),('thanh_tien',0,float('inf')),('thanh_tien',0,-1),
])
def test_mtd_preserves_data_rejections(context,column,index,value):
    frame=context.tables['ban_hang'].copy().astype({column:object})
    frame.loc[index,column]=value
    context.tables['ban_hang']=frame;context.duckdb_conn.register('ban_hang',frame)
    assert monthly_growth.resolve_growth_intent(GrowthIntent('month_to_date',2025,3),context,'2025-03-09')[0]=='abstain'


@pytest.mark.parametrize('indices',[[0,1],[4,5,6]])
def test_zero_previous_or_current_revenue_still_refuses(context,indices):
    frame=context.tables['ban_hang'];frame.loc[indices,'thanh_tien']=0
    context.duckdb_conn.register('ban_hang',frame)
    result=monthly_growth.resolve_growth_intent(GrowthIntent('month_to_date',2025,3),context,'2025-03-09')
    assert result[0]=='abstain' and 'positive' in result[2]


def test_missing_boundary_and_metric_are_not_relaxed_for_mtd(context):
    intent=GrowthIntent('month_to_date',2025,3)
    original=context.tables['ban_hang']
    incomplete=original.iloc[1:].copy()
    context.tables['ban_hang']=incomplete;context.duckdb_conn.register('ban_hang',incomplete)
    assert 'boundaries' in monthly_growth.resolve_growth_intent(intent,context,'2025-03-09')[2]
    context.tables['ban_hang']=original;context.duckdb_conn.register('ban_hang',original)
    context.readme=context.readme.replace('thành tiền sau giảm','tiền khách đã thanh toán')
    assert monthly_growth.resolve_growth_intent(intent,context,'2025-03-09')[0]=='abstain'


def test_empty_revenue_window_and_empty_table_refuse(context):
    frame=context.tables['ban_hang']
    frame.loc[[0,1],'trang_thai']='Đã hủy'
    context.duckdb_conn.register('ban_hang',frame)
    intent=GrowthIntent('month_to_date',2025,3)
    assert monthly_growth.resolve_growth_intent(intent,context,'2025-03-09')[0]=='abstain'
    empty=frame.iloc[:0]
    context.tables['ban_hang']=empty;context.duckdb_conn.register('ban_hang',empty)
    assert monthly_growth.resolve_growth_intent(intent,context,'2025-03-09')[0]=='abstain'


def test_mtd_is_not_automatically_enabled_in_existing_wrapper(context):
    class Base:
        def answer_query(self,*args):return ('abstain',None,'base refusal','abstain')
    agent=monthly_growth.MonthlyGrowthAgent(Base())
    assert agent.answer_query('Từ đầu tháng này đến hôm nay doanh thu thay đổi bao nhiêu % so với cùng kỳ tháng trước?',
                              context,'2025-03-09')==('abstain',None,'base refusal','abstain')


MTD_QUESTION='Từ đầu tháng này đến hết hôm nay, doanh thu thay đổi bao nhiêu % so với cùng kỳ tháng trước?'


def test_shared_factory_mtd_opt_in(context):
    from zaic_2026_datamate.pipeline import PipelineConfig,build_agent
    assert build_agent(PipelineConfig()).answer_query(MTD_QUESTION,context,'2025-03-09')[0]=='abstain'
    agent=build_agent(PipelineConfig(monthly_growth=True,mtd_growth=True))
    assert agent.answer_query(MTD_QUESTION,context,'2025-03-09')==('percent',50.0,None,'mtd_growth_path')
    assert agent.last_mtd_growth['previous_revenue']==200
    assert agent.last_monthly_growth=={}


@pytest.mark.parametrize('original',[
    ('number',7,None,'fast_path'),('percent',9,None,'slow_path'),
    ('unanswerable',None,'missing','fast_path'),('abstain',None,'base','abstain')])
def test_mtd_wrapper_preserves_existing_answer_and_refusal(context,original):
    from zaic_2026_datamate.mtd_growth import MTDGrowthAgent
    class Base:
        def answer_query(self,*args):return original
    agent=MTDGrowthAgent(Base())
    if original[0]=='abstain':context.readme='No verified metric'
    assert agent.answer_query(MTD_QUESTION,context,'2025-03-09')==original


def test_generator_exports_same_mtd_factory_route(context,tmp_path,monkeypatch):
    import json
    from scripts import generate_public_submission as generator
    class Loader:
        def __init__(self,*args):pass
        def load_business(self,bid):return context
    monkeypatch.setattr(generator,'DataLoader',Loader)
    source=tmp_path/'questions.jsonl'
    source.write_text(json.dumps({'id':'mtd','business_id':'fixture','question':MTD_QUESTION,'as_of_date':'2025-03-09'})+'\n')
    output=tmp_path/'submission.jsonl'
    stats=generator.generate_submission(questions_path=source,output_path=output,monthly_growth=True,mtd_growth=True)
    assert json.loads(output.read_text())=={'id':'mtd','type':'percent','answer':50.0}
    assert stats['mtd_growth_path']==1 and stats['monthly_growth_path']==stats['slow_path']==0


@pytest.mark.parametrize('completed,shipping,cancelled,returned',[
    ('xong','đang vận chuyển','huy','tra hang'),
    ('Hoàn thành','đang giao','cancel','hoan tra'),
    ('Đã xong','đang gửi','đã hủy đơn','đổi trả'),
    ('thành công','đang giao hàng','khách hủy','hàng trả về'),
    ('completed','shipping','cancelled','returned'),
])
def test_exact_status_aliases_share_one_inclusion_policy(context,completed,shipping,cancelled,returned):
    frame=context.tables['ban_hang']
    frame.loc[:8,'trang_thai']=completed
    frame.loc[6,'trang_thai']=shipping
    frame.loc[9,'trang_thai']=cancelled
    frame.loc[10,'trang_thai']=returned
    context.duckdb_conn.register('ban_hang',frame)
    assert monthly_growth.resolve_growth_intent(GrowthIntent('month_to_date',2025,3),context,'2025-03-09')==('percent',50.0,None)


@pytest.mark.parametrize('label',['không hủy','chưa hoàn thành','không giao được','ok','cancel requested'])
def test_ambiguous_or_negated_status_is_not_guessed(context,label):
    frame=context.tables['ban_hang'];frame.loc[0,'trang_thai']=label
    context.duckdb_conn.register('ban_hang',frame)
    assert monthly_growth.resolve_growth_intent(GrowthIntent('month_to_date',2025,3),context,'2025-03-09')[0]=='abstain'


def test_returned_multi_line_order_is_entirely_excluded(context):
    frame=context.tables['ban_hang'];frame.loc[[4,5],'trang_thai']='tra hang'
    context.duckdb_conn.register('ban_hang',frame)
    assert monthly_growth.resolve_growth_intent(GrowthIntent('month_to_date',2025,3),context,'2025-03-09')==('percent',0.0,None)
