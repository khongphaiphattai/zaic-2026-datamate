import pandas as pd
import pytest

from zaic_2026_datamate.cleaner import clean_vietnamese_currency, sniff_and_clean_dataframe
from zaic_2026_datamate.engine import DuckDBAgentEngine
from zaic_2026_datamate.schema_linker import SchemaLinker


def test_vietnamese_currency_parser():
    assert clean_vietnamese_currency("100.000 đ") == 100000.0
    assert clean_vietnamese_currency("1.5tr") == 1500000.0
    assert clean_vietnamese_currency("120k") == 120000.0
    assert clean_vietnamese_currency("(50.000)") == -50000.0
    assert clean_vietnamese_currency(None) is None


def test_schema_linker_and_ambiguity_gate():
    df = pd.DataFrame({
        "ten_san_pham": ["Mì Hảo Hảo tôm chua cay", "Nước mắm Nam Ngư", "Dầu ăn Simply"],
        "so_luong": [10, 5, 2],
        "don_gia": [4500.0, 32000.0, 65000.0],
    })
    linker = SchemaLinker(df)

    # Test exact / fuzzy column match
    linked = linker.link("Có bao nhiêu gói mì tôm chua cay?")
    assert not linked.is_ambiguous
    assert "ten_san_pham" in linked.matched_columns
    assert len(linked.matched_values.get("ten_san_pham", [])) > 0

    # Test out-of-domain / unanswerable question
    ood_linked = linker.link("Nhiệt độ ngoài trời hôm nay là bao nhiêu?")
    assert ood_linked.is_ambiguous
    assert ood_linked.refusal_reason is not None


def test_duckdb_engine_and_self_correction():
    df = pd.DataFrame({
        "mat_hang": ["Bánh gạo", "Kẹo dẻo"],
        "doanh_thu": [200000.0, 150000.0],
    })
    engine = DuckDBAgentEngine(df)

    # Test correct SQL
    res = engine.execute_query("SELECT SUM(doanh_thu) as total FROM dataset")
    assert res[0] is True
    assert res[1].iloc[0, 0] == 350000.0

    # Test self-correction callback
    def mock_corrector(bad_sql, err_msg, attempt):
        # Fix intentional typo: 'danh_thu' -> 'doanh_thu'
        return "SELECT SUM(doanh_thu) as total FROM dataset"

    corrected_res = engine.run_with_self_correction(
        initial_sql="SELECT SUM(danh_thu) as total FROM dataset",
        sql_generator=mock_corrector,
        max_retries=1,
    )
    assert corrected_res.success is True
    assert corrected_res.retry_count == 1
    assert corrected_res.df_result.iloc[0, 0] == 350000.0


def test_datamate_agent_end_to_end():
    from zaic_2026_datamate.agent import DataMateAgent

    df = pd.DataFrame({
        "san_pham": ["Mì Hảo Hảo", "Muối tiêu Chinsu"],
        "so_luong": [100, 20],
        "gia_ban": [4500, 7000],
    })
    agent = DataMateAgent(df)

    # 1. Out-of-domain query -> Gate 1 Refusal
    ans_ood = agent.answer_query("Thời tiết ngày mai tại Hà Nội thế nào?")
    assert ans_ood.is_refused is True
    assert ans_ood.refusal_reason is not None

    # 2. In-domain query with SQL generator -> Success
    def dummy_sql_gen(q, linked):
        return "SELECT SUM(so_luong * gia_ban) AS total FROM dataset"

    ans_valid = agent.answer_query("Tính tổng doanh thu bán hàng?", sql_generator_fn=dummy_sql_gen)
    assert ans_valid.is_refused is False
    assert ans_valid.answer_value == 590000.0


def test_datamate_agent_from_business_context():
    from zaic_2026_datamate.agent import DataMateAgent
    from zaic_2026_datamate.data_loader import DataLoader
    from pathlib import Path

    train_dir = Path("data/train/train/data")
    if not train_dir.exists():
        pytest.skip("Train data directory not available")

    loader = DataLoader("data/train/train/data", "data/train/train/calendar.csv")
    ctx_128 = loader.load_business("biz_128")
    agent = DataMateAgent.from_business(ctx_128)

    assert agent.business_id == "biz_128"
    assert "danh_sach_nhap_hang" in agent.tables
    assert "calendar" in agent.tables

    # Test prompt generation
    prompt = agent.generate_prompt("tháng này tổng giá trị các phiếu nhập hàng là bao nhiu", as_of_date="2026-09-07")
    assert "2026-09-07" in prompt
    assert "danh_sach_nhap_hang" in prompt

    # Test answering query via agent
    def sql_gen(q, ag):
        return """
            SELECT SUM(can_tra_ncc)
            FROM danh_sach_nhap_hang
            WHERE strftime(thoi_gian, '%Y-%m') = '2026-09'
              AND trang_thai = 'Đã nhập hàng'
        """

    ans = agent.answer_query("tháng này tổng giá trị các phiếu nhập hàng là bao nhiu", sql_generator_fn=sql_gen)
    assert ans.is_refused is False
    assert ans.answer_value == 89526400.0
