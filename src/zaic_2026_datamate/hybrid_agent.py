"""
hybrid_agent.py: Hybrid Fast-Slow Architecture for ZAIC 2026 DataMate.

Orchestrates:
1. Fast Path (Deterministic Rule-based + DuckDB In-Memory):
   - Handles supported revenue, order count, cancellations, AOV and OOD rules.
   - Makes no model calls; measured accuracy and latency belong in run reports.
2. Slow Path (SLM Qwen2.5-Coder-7B/14B + DuckDB Execution Sandbox):
   - Opt-in after Fast abstains AND the single-day store-revenue grammar matches.
   - Executes a single registered-table SELECT with a README-backed metric check.
   - Abstains on policy/execution failures, null results or low declared confidence.
   - Metric verification is partial: date/status/table selection remain unverified.
"""

from __future__ import annotations

import logging
import math
import re
import unicodedata
from datetime import datetime
from typing import Any

from zaic_2026_datamate.data_loader import BusinessContext
from zaic_2026_datamate.intent_parser import resolve_query
from zaic_2026_datamate.llm_client import LLMClient
from zaic_2026_datamate.selective_gate import daily_revenue_date
from zaic_2026_datamate.slow_path import SlowPathEngine

logger = logging.getLogger(__name__)


def technical_abstain_gate(
    question: str,
    ans_type: str,
    ans_val: Any,
    reason: str | None,
    route_source: str,
) -> tuple[str, Any, str | None, str]:
    """Enforce deterministic technical invariants on outgoing predictions.

    Under Penalized Accuracy (+1 / 0 / -1), leaking NaN, Inf, invalid dates,
    empty scalars, or syntactic type mismatches causes guaranteed -1 penalties.
    """
    if ans_type in ("abstain", "unanswerable"):
        return ans_type, None if ans_type == "abstain" else ans_val, reason, route_source

    if ans_type in ("number", "percent"):
        if isinstance(ans_val, bool) or ans_val is None:
            return "abstain", None, f"Technical gate: invalid scalar {ans_val!r} for {ans_type}", "abstain"
        try:
            num = float(ans_val)
        except (TypeError, ValueError):
            return "abstain", None, f"Technical gate: non-numeric {ans_val!r} for {ans_type}", "abstain"
        if not math.isfinite(num):
            return "abstain", None, f"Technical gate: non-finite {ans_val!r} for {ans_type}", "abstain"
    elif ans_type == "boolean":
        if not isinstance(ans_val, bool):
            return "abstain", None, f"Technical gate: non-bool {ans_val!r} for boolean", "abstain"
    elif ans_type == "string":
        if not isinstance(ans_val, str) or not ans_val.strip() or ans_val.strip().lower() in ("nan", "none", "null", "nat"):
            return "abstain", None, f"Technical gate: invalid string {ans_val!r}", "abstain"
    elif ans_type == "date":
        if not isinstance(ans_val, str):
            return "abstain", None, f"Technical gate: non-str date {ans_val!r}", "abstain"
        s = ans_val.strip()
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
            try:
                datetime.strptime(s, "%Y-%m-%d")
            except ValueError:
                return "abstain", None, f"Technical gate: invalid calendar date {s!r}", "abstain"
        elif re.fullmatch(r"\d{4}-\d{2}", s):
            yr, mo = int(s[:4]), int(s[5:7])
            if not (2020 <= yr <= 2030 and 1 <= mo <= 12):
                return "abstain", None, f"Technical gate: out-of-range month {s!r}", "abstain"
        else:
            return "abstain", None, f"Technical gate: invalid date format {s!r}", "abstain"
    elif ans_type == "list":
        if not isinstance(ans_val, list) or len(ans_val) == 0:
            return "abstain", None, f"Technical gate: empty/invalid list {ans_val!r}", "abstain"
        for elem in ans_val:
            if elem is None or (isinstance(elem, float) and not math.isfinite(elem)):
                return "abstain", None, f"Technical gate: invalid list element {elem!r}", "abstain"
    elif ans_type == "table":
        if (
            not isinstance(ans_val, dict)
            or not isinstance(ans_val.get("columns"), list)
            or not ans_val.get("columns")
            or not isinstance(ans_val.get("rows"), list)
            or not ans_val.get("rows")
        ):
            return "abstain", None, "Technical gate: invalid or empty table", "abstain"
        for row in ans_val["rows"]:
            if not isinstance(row, (list, tuple)) or len(row) != len(ans_val["columns"]):
                return "abstain", None, "Technical gate: malformed table row", "abstain"
            for cell in row:
                if isinstance(cell, float) and not math.isfinite(cell):
                    return "abstain", None, f"Technical gate: non-finite table cell {cell!r}", "abstain"

    if ans_type in ("list", "number"):
        # Type-vs-question mismatch: prevent returning a numeric list/scalar for boolean or peak-date questions
        q_norm = re.sub(
            r"\s+",
            " ",
            "".join(
                ch
                for ch in unicodedata.normalize("NFD", question.lower().replace("đ", "d"))
                if unicodedata.category(ch) != "Mn"
            ),
        ).strip()
        if not any(k in q_norm for k in ("liet ke", "ke ten", "danh sach")):
            asks_bool = bool(
                re.search(
                    r"\b(?:co|cos|co\s+phai|phai)\b.*\b(?:tang|giam|lien\s+tuc|cao\s+hon|nhieu\s+hon)\b.*\b(?:khong|khoong|ko|hong)\b"
                    r"|\b(?:tang|giam)\s+lien\s+tuc\b",
                    q_norm,
                )
            ) and not re.search(r"\bbao\s+nhieu\b", q_norm)
            asks_peak_period = bool(
                re.search(
                    r"\b(?:bua|hom|ngay|thang)\s+naof?\b.*?\b(?:cao|lon|nhieu|it|thap)\b.*?\bnh(?:at|aast|ut|atf)f?\b",
                    q_norm,
                )
            ) and not re.search(
                r"\b(?:bao\s+nhieu\s+(?:tien|don|ngay|thang)|gio\s+naof?)\b",
                q_norm,
            )
            if asks_bool or asks_peak_period:
                return (
                    "abstain",
                    None,
                    f"Technical gate: type-vs-question mismatch ({ans_type} returned for boolean/date question)",
                    "abstain",
                )

    return ans_type, ans_val, reason, route_source


class HybridAgent:
    """The central Hybrid Data Agent combining Deterministic Fast Path

    with SLM Slow Path.
    """

    def __init__(
        self,
        enable_slow_path: bool = False,
        llm_client: LLMClient | None = None,
        confidence_threshold: float = 0.95,
    ):
        self.enable_slow_path = enable_slow_path
        self.slow_engine: SlowPathEngine | None = None
        if enable_slow_path and llm_client is not None:
            self.slow_engine = SlowPathEngine(
                llm_client=llm_client,
                confidence_threshold=confidence_threshold,
                verify_daily_revenue=True,
            )

    def answer_query(
        self,
        question: str,
        context: BusinessContext,
        as_of_date: str | None = None,
    ) -> tuple[str, Any, str | None, str]:
        """Resolve a query using the Hybrid Fast-Slow cascade.

        Returns:
            Tuple: (ans_type, ans_value, reason, route_source)
            route_source: 'fast_path' | 'slow_path' | 'abstain'
        """
        if self.slow_engine is not None:
            self.slow_engine.reset_audit()
        # =====================================================================
        # ROUTE 1: FAST PATH (Deterministic Router & DuckDB Engine)
        # =====================================================================
        fast_type, fast_val, fast_reason = resolve_query(
            question=question,
            context=context,
            as_of_date=as_of_date,
        )

        if fast_type != "abstain":
            # Preserve Fast Path decisions after technical invariant validation.
            return technical_abstain_gate(
                question, fast_type, fast_val, fast_reason, "fast_path"
            )

        # =====================================================================
        # ROUTE 2: SLOW PATH (SLM Text-to-SQL + DuckDB Execution Sandbox)
        # =====================================================================
        if self.enable_slow_path and self.slow_engine is not None:
            if daily_revenue_date(question, as_of_date) is None:
                return (
                    "abstain",
                    None,
                    "Selective gate: only single-day store revenue is eligible",
                    "abstain",
                )
            logger.info(
                "Fast Path abstained. Invoking Slow Path for: '%s'", question[:60]
            )
            slow_type, slow_val, slow_reason = self.slow_engine.resolve_slow_path(
                question=question,
                context=context,
                as_of_date=as_of_date,
            )

            if slow_type == "number":
                return technical_abstain_gate(
                    question, slow_type, slow_val, slow_reason, "slow_path"
                )
            return (
                "abstain",
                None,
                slow_reason or "Selective gate rejected output type",
                "abstain",
            )

        # =====================================================================
        # ROUTE 3: SAFE ABSTENTION (Silence is Free Axiom - 0 Points Protected)
        # =====================================================================
        return "abstain", None, fast_reason or "Safe fallback abstention", "abstain"
