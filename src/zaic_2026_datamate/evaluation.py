"""Local rulebook §9 evaluator; not a replacement for the organizer's scorer.

Shop-specific accepted aliases require upstream canonicalization from shop data.
"""

import math
import numbers
import re
import unicodedata
from datetime import date


def _text(value):
    value = unicodedata.normalize("NFC", value).casefold()
    return " ".join(
        "".join(
            " " if unicodedata.category(c).startswith("P") else c for c in value
        ).split()
    )


def _numeric(value):
    return (
        isinstance(value, (int, float, numbers.Number))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _cell(value):
    return value is None or type(value) in (str, bool) or _numeric(value)


def valid_answer(kind, value):
    if kind in ("abstain", "unanswerable"):
        return value is None
    if kind in ("number", "percent"):
        return _numeric(value)
    if kind == "boolean":
        return type(value) is bool
    if kind == "string":
        return isinstance(value, str) and bool(value.strip())
    if kind == "date":
        if not isinstance(value, str) or not re.fullmatch(
            r"\d{4}-\d{2}(?:-\d{2})?", value
        ):
            return False
        try:
            date.fromisoformat(value + "-01" if len(value) == 7 else value)
            return True
        except ValueError:
            return False
    if kind == "list":
        return isinstance(value, list) and all(
            _cell(x) and x is not None for x in value
        )
    if kind == "table":
        if not isinstance(value, dict) or set(value) != {"columns", "rows"}:
            return False
        cols, rows = value["columns"], value["rows"]
        return (
            isinstance(cols, list)
            and bool(cols)
            and all(isinstance(c, str) and c.strip() for c in cols)
            and len(set(map(_text, cols))) == len(cols)
            and isinstance(rows, list)
            and all(
                isinstance(r, list) and len(r) == len(cols) and all(_cell(x) for x in r)
                for r in rows
            )
        )
    return False


def _equal(a, b):
    if _numeric(a) and _numeric(b):
        if type(a) is int and type(b) is int:
            return a == b
        # Absolute floating representation error only; no relative business tolerance.
        return abs(a - b) <= max(1e-12, math.ulp(float(a)), math.ulp(float(b)))
    if type(a) is not type(b):
        return False
    return _text(a) == _text(b) if isinstance(a, str) else a == b


def evaluate_match(pred_type, pred_val, target_type, target_val):
    if pred_type != target_type or pred_type == "abstain":
        return False
    if not valid_answer(pred_type, pred_val) or not valid_answer(
        target_type, target_val
    ):
        return False
    if pred_type == "unanswerable":
        return True
    if pred_type == "list":
        return len(pred_val) == len(target_val) and all(
            _equal(a, b) for a, b in zip(pred_val, target_val)
        )
    if pred_type == "table":
        pc, tc = (
            list(map(_text, pred_val["columns"])),
            list(map(_text, target_val["columns"])),
        )
        if len(pc) != len(tc):
            return False
        order = (
            [pc.index(c) for c in tc] if set(pc) == set(tc) else list(range(len(pc)))
        )
        remaining = list(target_val["rows"])
        for row in pred_val["rows"]:
            row = [row[i] for i in order]
            match = next(
                (
                    i
                    for i, r in enumerate(remaining)
                    if all(_equal(a, b) for a, b in zip(row, r))
                ),
                None,
            )
            if match is None:
                return False
            remaining.pop(match)
        return not remaining
    return _equal(pred_val, target_val)
