"""Validate complete outputs before atomically publishing them."""
import json
import os
from pathlib import Path
import tempfile
from zaic_2026_datamate.evaluation import valid_answer


def write_submission(path, rows, expected_ids, *, overwrite=False):
    path = Path(path)
    if path.exists() and not overwrite:
        raise FileExistsError(f'Output exists: {path}; use --overwrite explicitly')
    ids = [r.get('id') for r in rows]
    if not ids or len(ids) != len(set(ids)) or set(ids) != set(expected_ids) or len(ids) != len(expected_ids):
        raise ValueError('Output IDs must match all input questions exactly once')
    for r in rows:
        if not valid_answer(r.get('type'), r.get('answer')):
            raise ValueError(f'Invalid answer format for {r.get("id")}')
        if 'reason' in r and not isinstance(r['reason'], str):
            raise ValueError('reason must be a string')
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False) as f:
            tmp = Path(f.name)
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False, allow_nan=False) + '\n')
            f.flush()
            os.fsync(f.fileno())
        if overwrite:
            os.replace(tmp, path)
        else:
            os.link(tmp, path)  # Atomic exclusive publication, even if another writer raced us.
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)
