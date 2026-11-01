import pytest
from zaic_2026_datamate.submission import write_submission

def test_preserves_existing_and_checks_completeness(tmp_path):
 out=tmp_path/'submission.jsonl'; out.write_text('original')
 with pytest.raises(FileExistsError):
  write_submission(out,[{'id':'a','type':'abstain'}],['a'])
 assert out.read_text()=='original'
 with pytest.raises(ValueError):
  write_submission(out,[{'id':'a','type':'abstain'}],['a','b'],overwrite=True)
 assert out.read_text()=='original'

def test_duplicate_and_format_errors(tmp_path):
 out=tmp_path/'submission.jsonl'
 for rows in [[{'id':'a','type':'number','answer':'1'}],[{'id':'a','type':'abstain'}]*2]:
  with pytest.raises(ValueError): write_submission(out,rows,['a'])
  assert not out.exists()
 write_submission(out,[{'id':'a','type':'number','answer':1}],['a'])
 assert '"answer": 1' in out.read_text()

def test_preview_cannot_overwrite_submission(tmp_path):
 from scripts.generate_public_submission import generate_submission
 out=tmp_path/'submission.jsonl'; out.write_text('keep')
 with pytest.raises(ValueError): generate_submission(output_path=out,limit=5,overwrite=True)
 assert out.read_text()=='keep'
