import json
from pathlib import Path
import pytest

from zaic_2026_datamate import growth_semantics
from zaic_2026_datamate.growth_semantics import certify_growth_question


def test_mtd_question_certifies_both_periods():
    plan=certify_growth_question(
        'Từ đầu tháng này đến hết hôm nay, doanh thu thay đổi bao nhiêu % so với cùng kỳ tháng trước?',
        '2025-03-09')
    assert plan is not None
    assert plan.comparison=='previous_month_same_days'
    assert plan.windows.previous_end.isoformat()=='2025-02-09'
    assert plan.windows.current_end.isoformat()=='2025-03-09'
    assert plan.filters==()


CONTROLS=json.loads(Path('artifacts/growth-semantic-gate/controls.json').read_text())


@pytest.mark.parametrize('case',CONTROLS,ids=lambda c:c['id'])
def test_frozen_minimal_pairs_and_forged_empty_filters(case):
    plan=certify_growth_question(case['question'],case['as_of_date'])
    assert (plan.proposal() if plan else None)==case['expected']
    proposal=case.get('forged_proposal',case['expected'])
    checked=growth_semantics.validate_growth_proposal(json.dumps(proposal),case['question'],case['as_of_date'])
    assert checked==plan


@pytest.mark.parametrize('changes',[
    {'comparison':'previous_year'}, {'period':'month_to_date'},
    {'year':2024}, {'month':2}, {'year':2025.0}, {'month':True},
    {'filters':['ca sáng']}, {'filters':None}, {'sql':'SELECT 1'},
])
def test_proposal_must_match_independent_question_plan(changes):
    case=CONTROLS[0]
    proposal={**case['expected'],**changes}
    assert growth_semantics.validate_growth_proposal(json.dumps(proposal),case['question'],case['as_of_date']) is None


def test_duplicate_keys_legacy_shape_and_abstain():
    case=CONTROLS[0];raw=json.dumps(case['expected'])
    duplicated=raw.replace('"period":','"period":"full_month","period":',1)
    legacy={k:v for k,v in case['expected'].items() if k not in ('comparison','filters')}
    for value in (duplicated,json.dumps(legacy),'broken','[]','{"intent":"abstain"}'):
        assert growth_semantics.validate_growth_proposal(value,case['question'],case['as_of_date']) is None


def test_unknown_wording_and_invalid_anchor_refuse_without_proposal_rescue():
    case=CONTROLS[0]
    for question,anchor in [(None,case['as_of_date']),(case['question'],'bad'),
                             (case['question']+' Chỉ tính đơn đã thanh toán.',case['as_of_date'])]:
        assert certify_growth_question(question,anchor) is None
        assert growth_semantics.validate_growth_proposal(json.dumps(case['expected']),question,anchor) is None
