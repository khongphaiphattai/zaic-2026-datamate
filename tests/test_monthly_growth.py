import duckdb
import pandas as pd
import pytest

from zaic_2026_datamate.data_loader import BusinessContext
from zaic_2026_datamate.monthly_growth import monthly_growth_period, resolve_monthly_growth


@pytest.fixture
def context():
    frame = pd.DataFrame({
        'ma_don_hang':['a','b','c','c','d','e'],
        'ngay':['2025-02-01','2025-02-28','2025-03-01','2025-03-01','2025-03-31','2025-03-15'],
        'trang_thai':['Đã hoàn thành']*5+['Đã hủy'],
        'thanh_tien':[100,100,40,60,0,9999],
    })
    con=duckdb.connect(':memory:');con.register('ban_hang',frame)
    readme='''Mỗi dòng một sản phẩm.
## Cột trong `ban_hang.csv`
- **ma_don_hang**: mã đơn
- **ngay**: ngày bán
- **trang_thai**: trạng thái đơn
- **thanh_tien**: thành tiền sau giảm
'''
    ctx=BusinessContext(business_id='test',data_dir='.',tables={'ban_hang':frame},
                        readme=readme,duckdb_conn=con)
    try:yield ctx
    finally:con.close()


QUESTION='Doanh thu tháng 3/2025 tăng hay giảm bao nhiêu phần trăm so với tháng trước?'


def test_signed_growth_counts_lines_and_excludes_cancelled(context):
    result=resolve_monthly_growth(QUESTION,context,'2025-03-31')
    assert result[:2]==('percent',-50.0)


@pytest.mark.parametrize('question',[
    'So với tháng liền trước, doanh thu tháng 3 năm 2025 thay đổi bao nhiêu %?',
    'Tháng 3/2025 dthu thay đổi bao nhiêu % so vs tháng liền trước',
    'Doanh thu tháng 3/2025 hơn hay kém tháng trc bao nhiu %',
])
def test_train24_phrases(question):
    assert monthly_growth_period(question,'2025-03-31')==(2025,3)


@pytest.mark.parametrize('question',[
    'Doanh thu tháng 3/2025 của khách Lan tăng hay giảm bao nhiêu phần trăm so với tháng trước?',
    'Doanh thu tháng 3/2025 tăng hay giảm bao nhiêu phần trăm so với năm trước?',
    'Doanh thu tháng 3/2025 tăng hay giảm bao nhiêu phần trăm so với tháng trước ca sáng?',
    'Từ đầu tháng này đến hôm nay doanh thu thay đổi bao nhiêu % so với cùng kỳ tháng trước?',
    'Doanh thu tháng 3/2025 hơn tháng trước phải không?',
    'Doanh thu tháng 13/2025 tăng hay giảm bao nhiêu phần trăm so với tháng trước?',
])
def test_conditions_and_invalid_month_rejected(question):
    assert monthly_growth_period(question,'2025-03-31') is None


@pytest.mark.parametrize('anchor',[None,'bad','2025-03-30'])
def test_missing_invalid_or_partial_month_anchor(anchor):
    assert monthly_growth_period(QUESTION,anchor) is None


def test_missing_readme_semantics_rejected(context):
    context.readme=context.readme.replace('ngày bán','ngày nhập kho')
    assert resolve_monthly_growth(QUESTION,context,'2025-03-31')[0]=='abstain'


@pytest.mark.parametrize('column,index,value',[
    ('ma_don_hang',0,None),('ma_don_hang',0,''),
    ('ngay',2,'bad'),('ngay',2,'2025-03-02'),
    ('trang_thai',0,'Chờ đối soát'),('trang_thai',2,'Đã trả hàng'),
    ('thanh_tien',0,float('inf')),('thanh_tien',0,-1),
])
def test_invalid_or_ambiguous_data_rejected(context,column,index,value):
    frame=context.tables['ban_hang'].copy().astype({column:object})
    frame.loc[index,column]=value
    context.tables['ban_hang']=frame;context.duckdb_conn.register('ban_hang',frame)
    assert resolve_monthly_growth(QUESTION,context,'2025-03-31')[0]=='abstain'


def test_zero_denominator_and_incomplete_coverage_rejected(context):
    frame=context.tables['ban_hang']
    frame.loc[:1,'thanh_tien']=0
    context.duckdb_conn.register('ban_hang',frame)
    assert resolve_monthly_growth(QUESTION,context,'2025-03-31')[0]=='abstain'
    frame.loc[:1,'thanh_tien']=100
    frame.loc[0,'ngay']='2025-02-02'
    context.duckdb_conn.register('ban_hang',frame)
    assert resolve_monthly_growth(QUESTION,context,'2025-03-31')[0]=='abstain'


def test_year_boundary(context):
    frame=context.tables['ban_hang']
    frame['ngay']=['2024-12-01','2024-12-31','2025-01-01','2025-01-01','2025-01-31','2025-01-15']
    context.duckdb_conn.register('ban_hang',frame)
    question=QUESTION.replace('3/2025','1/2025')
    assert resolve_monthly_growth(question,context,'2025-01-31')[:2]==('percent',-50.0)


def test_wrapper_preserves_old_answer(context):
    from zaic_2026_datamate.monthly_growth import MonthlyGrowthAgent
    class Existing:
        def answer_query(self,*args): return ('percent',123,None,'fast_path')
    agent=MonthlyGrowthAgent(Existing())
    assert agent.answer_query(QUESTION,context,'2025-03-31')==('percent',123,None,'fast_path')
    assert agent.last_monthly_growth=={}


def test_shared_factory_exposes_monthly_route_only_when_enabled(context):
    from zaic_2026_datamate.pipeline import PipelineConfig, build_agent
    assert build_agent(PipelineConfig()).answer_query(QUESTION,context,'2025-03-31')[0]=='abstain'
    agent=build_agent(PipelineConfig(monthly_growth=True))
    assert agent.answer_query(QUESTION,context,'2025-03-31')==('percent',-50.0,None,'monthly_growth_path')
    assert agent.last_monthly_growth['previous_revenue']==200


def test_generator_exports_monthly_route_as_percent(context,tmp_path,monkeypatch):
    import json
    from scripts import generate_public_submission as generator
    class Loader:
        def __init__(self,*args): pass
        def load_business(self,bid): return context
    monkeypatch.setattr(generator,'DataLoader',Loader)
    source=tmp_path/'questions.jsonl'
    source.write_text(json.dumps({'id':'growth','business_id':'test','question':QUESTION,'as_of_date':'2025-03-31'})+'\n')
    output=tmp_path/'submission.jsonl'
    stats=generator.generate_submission(questions_path=source,output_path=output,monthly_growth=True)
    assert json.loads(output.read_text())=={'id':'growth','type':'percent','answer':-50.0}
    assert stats['monthly_growth_path']==1 and stats['slow_path']==0
