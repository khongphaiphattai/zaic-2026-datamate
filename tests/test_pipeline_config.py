import json

import duckdb
import pandas as pd
import pytest

from zaic_2026_datamate.data_loader import BusinessContext
from zaic_2026_datamate.pipeline import PipelineConfig, build_agent
from scripts import diagnose_hybrid, generate_public_submission


def make_context():
    frame = pd.DataFrame({
        'ma_don_hang': ['a', 'a', 'b'],
        'ngay_dat_hang': ['2025-06-02'] * 3,
        'trang_thai': ['Hoàn thành'] * 3,
        'tong_tien': [100, 200, 300],
    })
    con = duckdb.connect(':memory:')
    con.register('don_hang', frame)
    return BusinessContext(business_id='biz_test', data_dir='.',
                           tables={'don_hang': frame}, duckdb_conn=con)


def test_enabled_slow_requires_client():
    with pytest.raises(ValueError, match='client'):
        build_agent(PipelineConfig(enable_slow_path=True))


@pytest.mark.parametrize('threshold', [-0.1, 1.1, float('nan')])
def test_invalid_confidence_rejected(threshold):
    with pytest.raises(ValueError):
        PipelineConfig(confidence_threshold=threshold)


def test_generator_matches_diagnostic_agent_and_counts_routes(tmp_path, monkeypatch):
    question = {'id': 'one', 'business_id': 'biz_test',
                'question': 'Cho tôi biết số đơn hôm qua.', 'as_of_date': '2025-06-03'}
    questions = tmp_path/'questions.jsonl'
    questions.write_text(json.dumps(question)+'\n')
    config = PipelineConfig(order_paraphrases=True)
    reference_context = make_context()
    try:
        expected = diagnose_hybrid.build_agent(config).answer_query(
            question['question'], reference_context, question['as_of_date'])
    finally:
        reference_context.duckdb_conn.close()
    assert expected == ('number', 2, None, 'paraphrase_fast_path')
    generated_context = make_context()
    class Loader:
        def __init__(self, *args): pass
        def load_business(self, bid): return generated_context
    monkeypatch.setattr(generate_public_submission, 'DataLoader', Loader)
    def forbidden(*args, **kwargs): pytest.fail('Fast-only must not access remote provenance')
    monkeypatch.setattr(generate_public_submission, 'fetch_model_provenance', forbidden)
    output = tmp_path/'submission.jsonl'
    stats = generate_public_submission.generate_submission(
        questions_path=questions, output_path=output, order_paraphrases=True)
    assert json.loads(output.read_text()) == {'id':'one', 'type':'number', 'answer':2}
    assert stats['paraphrase_fast_path']==1 and stats['slow_path']==0 and stats['fast_path']==0
    with pytest.raises(duckdb.ConnectionException):
        generated_context.duckdb_conn.execute('SELECT 1')


def test_generator_rejects_provenance_before_inference_or_output(tmp_path, monkeypatch):
    questions=tmp_path/'questions.jsonl'
    questions.write_text(json.dumps({'id':'one'})+'\n')
    output=tmp_path/'submission.jsonl'
    def reject(*args, **kwargs): raise ValueError('checkpoint mismatch')
    monkeypatch.setattr(generate_public_submission,'fetch_model_provenance',reject)
    monkeypatch.setattr(generate_public_submission,'OpenAICompatibleClient',
                        lambda **kwargs:pytest.fail('No client before provenance approval'))
    with pytest.raises(ValueError,match='checkpoint mismatch'):
        generate_public_submission.generate_submission(questions_path=questions,
            output_path=output,enable_slow_path=True)
    assert not output.exists()
