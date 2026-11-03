import json

import duckdb
import pandas as pd
import pytest

from zaic_2026_datamate.data_loader import BusinessContext
from zaic_2026_datamate.llm_client import MockLLMClient
from zaic_2026_datamate.slow_path import SlowPathEngine


@pytest.fixture
def context():
    frame = pd.DataFrame({'tien': [100, 200]})
    con = duckdb.connect(':memory:')
    con.register('sales', frame)
    ctx = BusinessContext(business_id='test', data_dir='.', duckdb_conn=con,
                          tables={'sales': frame})
    try:
        yield ctx
    finally:
        con.close()


def responder(first_sql):
    calls = []
    def generate(prompt):
        calls.append(prompt)
        sql = first_sql if len(calls) == 1 else 'SELECT SUM(tien) FROM sales'
        return json.dumps({'type': 'number', 'sql': sql, 'confidence': 0.99})
    return calls, MockLLMClient(generate)


@pytest.mark.parametrize('sql', [
    'SELECT SUM(tien) FROM sales JOIN other ON true',
    'SELECT 1; SELECT 2',
    'DELETE FROM sales',
    "SELECT * FROM read_csv('/no-such-file.csv')",
    'SELECT 123',
])
def test_policy_rejection_is_final_even_if_model_could_correct(context, sql):
    calls, client = responder(sql)
    engine = SlowPathEngine(client)
    kind, value, reason = engine.resolve_slow_path('Doanh thu hôm nay?', context, '2025-01-01')
    assert kind == 'abstain' and value is None
    assert len(calls) == 1
    assert not engine.last_execution_succeeded


@pytest.mark.parametrize('sql', ['SELECT SUM(missing) FROM sales', 'SELECT SUM( FROM sales'])
def test_sql_errors_can_still_be_corrected(context, sql):
    calls, client = responder(sql)
    engine = SlowPathEngine(client)
    assert engine.resolve_slow_path('Doanh thu hôm nay?', context, '2025-01-01')[:2] == ('number', 300)
    assert len(calls) == 2
    assert engine.last_execution_succeeded


def test_policy_flag_does_not_leak_to_next_question(context):
    calls, client = responder('SELECT 123')
    engine = SlowPathEngine(client)
    assert engine.resolve_slow_path('first', context)[0] == 'abstain'
    calls2, client2 = responder('SELECT SUM(missing) FROM sales')
    engine.client = client2
    assert engine.resolve_slow_path('second', context)[:2] == ('number', 300)
    assert len(calls2) == 2
