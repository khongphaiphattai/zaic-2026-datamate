"""
tests/test_hybrid.py: Comprehensive test suite for Hybrid Fast-Slow Architecture
(Prompt Generator, LLM Client, Slow Path Engine, and Hybrid Router).
"""

import json
import duckdb
import pandas as pd
import pytest

from zaic_2026_datamate.data_loader import BusinessContext
from zaic_2026_datamate.hybrid_agent import HybridAgent
from zaic_2026_datamate.llm_client import MockLLMClient
from zaic_2026_datamate.prompt_generator import generate_text_to_sql_prompt, build_schema_markdown
from zaic_2026_datamate.slow_path import SlowPathEngine


@pytest.fixture
def sample_context():
    con = duckdb.connect(database=":memory:")
    orders_df = pd.DataFrame({
        "ma_don": ["DH01", "DH02", "DH03", "DH04"],
        "ngay": ["2025-06-01", "2025-06-02", "2025-06-03", "2025-06-04"],
        "khach": ["Nguyễn Văn A", "Trần Thị B", "Nguyễn Văn A", "Lê Văn C"],
        "tien": [100000.0, 250000.0, 150000.0, 50000.0],
        "trang_thai": ["Hoàn thành", "Hoàn thành", "Đã hủy", "Hoàn thành"],
        "kenh": ["Mang về", "Tại chỗ", "Mang về", "Tại chỗ"],
    })
    con.register("don_hang", orders_df)

    ctx = BusinessContext(
        business_id="biz_mock",
        data_dir="mock_dir",
        duckdb_conn=con,
        tables={"don_hang": orders_df},
        table_metadata={"don_hang": {"columns": list(orders_df.columns), "rows": len(orders_df)}},
        readme="Cửa hàng tạp hóa ABC. Chiết khấu đơn hàng 5%.",
    )
    return ctx


def test_prompt_generator(sample_context):
    prompt = generate_text_to_sql_prompt(
        question="Khách hàng nào mua nhiều tiền nhất?",
        context=sample_context,
        as_of_date="2025-06-10",
        business_id="biz_mock",
    )
    assert "biz_mock" in prompt
    assert "2025-06-10" in prompt
    assert "don_hang" in prompt
    assert "Khách hàng nào mua nhiều tiền nhất?" in prompt
    assert "Cửa hàng tạp hóa ABC" in prompt


def test_slow_path_number_query(sample_context):
    def mock_generator(prompt: str) -> str:
        return json.dumps({
            "thought": "Tính tổng tiền các đơn hoàn thành của khách Nguyễn Văn A",
            "type": "number",
            "sql": "SELECT SUM(tien) FROM don_hang WHERE khach = 'Nguyễn Văn A' AND trang_thai = 'Hoàn thành'",
            "confidence": 0.95,
        })

    client = MockLLMClient(handler=mock_generator)
    engine = SlowPathEngine(llm_client=client, confidence_threshold=0.85)

    ans_type, ans_val, reason = engine.resolve_slow_path(
        question="Khách Nguyễn Văn A đã mua bao nhiêu tiền?",
        context=sample_context,
        as_of_date="2025-06-10",
    )

    assert ans_type == "number"
    assert ans_val == 100000


def test_slow_path_date_query(sample_context):
    def mock_generator(prompt: str) -> str:
        return json.dumps({
            "thought": "Tìm ngày có doanh thu cao nhất",
            "type": "date",
            "sql": "SELECT ngay FROM don_hang WHERE trang_thai = 'Hoàn thành' ORDER BY tien DESC LIMIT 1",
            "confidence": 0.92,
        })

    client = MockLLMClient(handler=mock_generator)
    engine = SlowPathEngine(llm_client=client, confidence_threshold=0.85)

    ans_type, ans_val, reason = engine.resolve_slow_path(
        question="Ngày nào bán được nhiều tiền nhất?",
        context=sample_context,
        as_of_date="2025-06-10",
    )

    assert ans_type == "date"
    assert ans_val == "2025-06-02"


def test_slow_path_list_query(sample_context):
    def mock_generator(prompt: str) -> str:
        return json.dumps({
            "thought": "Lấy danh sách các khách hàng đã mua đơn thành công",
            "type": "list",
            "sql": "SELECT DISTINCT khach FROM don_hang WHERE trang_thai = 'Hoàn thành' ORDER BY khach",
            "confidence": 0.98,
        })

    client = MockLLMClient(handler=mock_generator)
    engine = SlowPathEngine(llm_client=client)

    ans_type, ans_val, reason = engine.resolve_slow_path(
        question="Kể tên các khách hàng đã mua?",
        context=sample_context,
    )

    assert ans_type == "list"
    assert "Lê Văn C" in ans_val
    assert "Nguyễn Văn A" in ans_val
    assert "Trần Thị B" in ans_val


def test_slow_path_refusal_on_low_confidence(sample_context):
    def mock_generator(prompt: str) -> str:
        return json.dumps({
            "thought": "Không chắc chắn câu hỏi",
            "type": "number",
            "sql": "SELECT 1000",
            "confidence": 0.60,
        })

    client = MockLLMClient(handler=mock_generator)
    engine = SlowPathEngine(llm_client=client, confidence_threshold=0.85)

    ans_type, ans_val, reason = engine.resolve_slow_path(
        question="Câu hỏi mơ hồ?",
        context=sample_context,
    )

    assert ans_type == "abstain"
    assert "threshold" in reason


def test_slow_path_refusal_on_sql_error(sample_context):
    def mock_generator(prompt: str) -> str:
        return json.dumps({
            "thought": "Cố tình viết sai tên cột",
            "type": "number",
            "sql": "SELECT SUM(non_existent_column) FROM don_hang",
            "confidence": 0.95,
        })

    client = MockLLMClient(handler=mock_generator)
    engine = SlowPathEngine(llm_client=client, enable_self_correction=False)

    ans_type, ans_val, reason = engine.resolve_slow_path(
        question="Tính tổng cột không tồn tại",
        context=sample_context,
    )

    assert ans_type == "abstain"
    assert "failed" in reason.lower() or "error" in reason.lower()


def test_slow_path_unanswerable(sample_context):
    def mock_generator(prompt: str) -> str:
        return json.dumps({
            "thought": "Dữ liệu không có thông tin thời tiết",
            "type": "unanswerable",
            "sql": None,
            "confidence": 0.99,
            "reason": "Dữ liệu không có nhiệt độ/thời tiết",
        })

    client = MockLLMClient(handler=mock_generator)
    engine = SlowPathEngine(llm_client=client)

    ans_type, ans_val, reason = engine.resolve_slow_path(
        question="Nhiệt độ ngày 01/06/2025 là bao nhiêu độ?",
        context=sample_context,
    )

    assert ans_type == "unanswerable"


def test_hybrid_agent_cascade(sample_context):
    # Case 1: Fast Path answers immediately (e.g. pure cancellation rate or revenue)
    hybrid = HybridAgent(enable_slow_path=False)
    # Question that fast path refuses
    ans_type, ans_val, reason, route = hybrid.answer_query(
        question="Khách hàng nào mua nhiều nhất?",
        context=sample_context,
    )
    assert ans_type == "abstain"
    assert route == "abstain"

    # Case 2: Enable Slow Path with Mock LLM
    def mock_generator(prompt: str) -> str:
        return json.dumps({
            "thought": "Trả về khách hàng nhiều tiền nhất",
            "type": "string",
            "sql": "SELECT khach FROM don_hang WHERE trang_thai = 'Hoàn thành' ORDER BY tien DESC LIMIT 1",
            "confidence": 0.95,
        })

    client = MockLLMClient(handler=mock_generator)
    hybrid_with_slow = HybridAgent(enable_slow_path=True, llm_client=client)

    ans_type, ans_val, reason, route = hybrid_with_slow.answer_query(
        question="Khách hàng nào mua nhiều nhất?",
        context=sample_context,
    )
    assert ans_type == "abstain"
    assert route == "abstain"

@pytest.mark.parametrize('payload',[
 '[]', '{"type":"number","confidence":"bad","sql":"SELECT 1"}',
 '{"type":"number","confidence":0.99,"sql":"SELECT 1 UNION ALL SELECT 2"}',
 '{"type":"number","confidence":0.99,"sql":"SELECT 1; SELECT 2"}',
 '{"type":"number","confidence":0.99,"sql":"CREATE TABLE unwanted AS SELECT 1"}',
])
def test_slow_path_rejects_invalid_or_unsafe_response(sample_context,payload):
 engine=SlowPathEngine(MockLLMClient(lambda _:payload),enable_self_correction=False)
 assert engine.resolve_slow_path('doanh thu hôm nay?',sample_context,'2025-06-10')[0]=='abstain'


def test_selective_daily_calls_model_after_fast_abstains(sample_context):
 from unittest.mock import patch
 calls=[]
 def handler(prompt):
  calls.append(prompt)
  return json.dumps({'type':'number','sql':"SELECT SUM(tien) FROM don_hang WHERE ngay='2025-06-02'",'confidence':0.99})
 agent=HybridAgent(enable_slow_path=True,llm_client=MockLLMClient(handler))
 with patch('zaic_2026_datamate.hybrid_agent.resolve_query',return_value=('abstain',None,None)):
  assert agent.answer_query('Doanh thu hôm nay bao nhiêu?',sample_context,'2025-06-02')[:2]==('number',250000)
 assert len(calls)==1


def test_fast_path_precedes_gate(sample_context):
 from unittest.mock import patch
 agent=HybridAgent(enable_slow_path=True,llm_client=MockLLMClient(lambda _:pytest.fail('No LLM for Fast Path')))
 with patch('zaic_2026_datamate.hybrid_agent.resolve_query',return_value=('number',42,None)):
  assert agent.answer_query('anything',sample_context,'2025-06-02')==('number',42,None,'fast_path')


def test_slow_path_blocks_multitable_and_external_reads(sample_context):
 for sql in ["SELECT COUNT(*) FROM don_hang JOIN other ON true", "SELECT * FROM read_csv('/etc/passwd')", "SELECT 123"]:
  engine=SlowPathEngine(MockLLMClient(lambda _, sql=sql:json.dumps({'type':'number','sql':sql,'confidence':0.99})),enable_self_correction=False)
  assert engine.resolve_slow_path('doanh thu hôm nay?',sample_context,'2025-06-02')[0]=='abstain'
