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


def _extract_top_level_select_clause(sql_low: str) -> str:
    """Return the outermost top-level SELECT ... FROM projection clause."""
    tokens = list(re.finditer(r"[()]|\bselect\b|\bfrom\b", sql_low))
    depth = 0
    select_start: int | None = None
    for tok in tokens:
        val = tok.group(0)
        if val == "(":
            depth += 1
        elif val == ")":
            depth = max(0, depth - 1)
        elif depth == 0:
            if val == "select":
                select_start = tok.end()
                select_end: int | None = None
            elif val == "from" and select_start is not None:
                select_end = tok.start()
                return sql_low[select_start:select_end]
    return ""


def _fold_vn_ascii(text: str) -> str:
    """Fold Vietnamese text into lowercase ASCII for accent-tolerant pattern matching."""
    folded = text.lower().replace("đ", "d")
    return "".join(
        ch
        for ch in unicodedata.normalize("NFKD", folded)
        if not unicodedata.combining(ch)
    )


_VN_WORD_NUM_G1 = r"(?:hai|ba|bon|nam|sau|bay|tam|chin|muoi|chuc)"
_BALANCED_PAREN_1 = r"[^()]*(?:\([^()]*\)[^()]*)*"
_G1_SINGLE_DAY_SQL_RE = re.compile(
    rf"(?:strftime\s*\({_BALANCED_PAREN_1},\s*'%y-%m-%d'\)\s*=\s*'\d{{4}}-\d{{2}}-\d{{2}}'"
    rf"|(?:try_cast|cast)\s*\({_BALANCED_PAREN_1}\bas\s+date\s*\)\s*=\s*(?:date\s*)?'\d{{4}}-\d{{2}}-\d{{2}}'"
    r"|\b[a-z_][a-z0-9_]*\s*=\s*date\s*'\d{4}-\d{2}-\d{2}')"
)
_G1_RANGE_SQL_RE = re.compile(
    rf"(?:>=|<=|\bbetween\b|\bdate_trunc\b|\bdate_diff\b|\bdatediff\b|\binterval\b"
    rf"|strftime\s*\({_BALANCED_PAREN_1},\s*'%y-%m'\)|\bextract\s*\(|\bmonth\s*\(|\bdate_add\b|\bdate_sub\b"
    r"|like\s*'(?:19|20)\d{2}-|\bunion\b|\bcase\s+when\b"
    r"|\bin\s*\([^)]*'\d{4}-\d{2}-\d{2}')"
)
_G3_UNIT_WORDS_ASCII = (
    r"(?:cai|chiec|coc|ly|tach|chai|lon|thung|hop|goi|bich|tui|cuon|cay|thanh|vien|"
    r"bo|doi|kg|ky|gam|lit|ml|met|qua|trai|bo|canh|suat|phan|dia|to|bat|o|mon|"
    r"quyen|to|san\s+pham|sp|mat\s+hang)"
)
_G3_UNIT_QTY_Q_RE = re.compile(
    rf"\b(?:bao\s+nhieu|bao\s+nheiu|bnhiu|bnhieu|bn|may)\s+{_G3_UNIT_WORDS_ASCII}\b"
)
_G3_MONEY_ASCII_RE = re.compile(
    r"\b(?:tien|doanh\s*thu|dthu|chi\s*phi|loi\s*nhuan|tri\s*gia|don\s*gia|"
    r"cong\s*no|gia\s+(?:ban|nhap|von|tri|bao\s+nhieu)|vnd|trieu|ngan|nghin)\b"
)
_G3_MONEY_ACCENT_RE = re.compile(
    r"(?:\b(?:giá|nợ|lãi|vốn|tỷ)\b|(?<=\d)\s*(?:đồng|đ)\b)"
)
_G3_MONEY_COL_SUM_RE = re.compile(
    rf"\bsum\s*\({_BALANCED_PAREN_1}\b(?:"
    r"thanh_tien\w*|tong_tien\w*|tien_hang\w*|doanh_thu\w*|revenue\w*|"
    r"amount\w*|gia_tri\w*|so_tien\w*|loi_nhuan\w*|tong_cong\w*|thuc_thu\w*|"
    r"paid_amount\w*|total_amount\w*|total_price\w*|net_amount\w*|gross_amount\w*"
    r")\b"
)
_G3_QTY_SELECT_RE = re.compile(
    r"\b(?:[a-z0-9_]*_)?(?:so_luong|qty|quantity|sl|units?)(?:_[a-z0-9_]+)?\b|\bcount\s*\("
)


def _sql_runtime_consistency_abstain_reason(
    question: str,
    ans_type: str,
    last_sql: str,
    as_of_date: str | None = None,
) -> str | None:
    """Detect zero-FP runtime SQL/question contradictions (G1 multi-day vs 1-day, G3 qty vs money)."""
    if ans_type != "number" or not last_sql or not last_sql.strip():
        return None
    from zaic_2026_datamate.cleaner import normalize_query_telex

    sql_low = re.sub(r"\s+", " ", last_sql).strip().lower()
    q_low = normalize_query_telex(question).lower()
    q_ascii = re.sub(r"\s+", " ", _fold_vn_ascii(q_low)).strip()

    # G1: SQL filters strictly 1 calendar day while question explicitly asks a multi-day period.
    if _G1_SINGLE_DAY_SQL_RE.search(sql_low) and not _G1_RANGE_SQL_RE.search(sql_low):
        distinct_dates = set(re.findall(r"'\d{4}-\d{2}-\d{2}'", sql_low))
        if len(distinct_dates) <= 1:
            q_strip = re.sub(
                r"(?:tinh\s+(?:ca|luon|toi|den)|ke\s+ca|toi|den)\s+(?:hom\s+nay|hnay)",
                " ",
                q_ascii,
            )
            has_standalone_day = bool(
                re.search(
                    r"\b(?:hom\s+nay|hnay|hom\s+qua|ngay\s+\d{1,2}|"
                    r"mung\s+\d{1,2}|mong\s+\d{1,2}|\d{1,2}[/-]\d{1,2}[/-]\d{2,4})\b",
                    q_strip,
                )
            )
            if not has_standalone_day:
                as_of_dt = None
                if as_of_date:
                    try:
                        as_of_dt = datetime.strptime(as_of_date[:10], "%Y-%m-%d").date()
                    except ValueError:
                        as_of_dt = None

                m_roll = re.search(
                    r"\b(\d+)\s*(?:ngay|hom)\s*(?:qua|gan\s+(?:day|nhat)|tro\s+lai\s+day|vua\s+qua)\b",
                    q_ascii,
                )
                is_multi_rolling = (
                    m_roll is not None and int(m_roll.group(1)) >= 2
                ) or bool(
                    re.search(
                        rf"\b{_VN_WORD_NUM_G1}\s+(?:ngay|hom)\s+"
                        r"(?:qua|gan\s+(?:day|nhat)|tro\s+lai\s+day|vua\s+qua)\b",
                        q_ascii,
                    )
                )
                is_multi_week = bool(
                    re.search(r"\btuan\s+(?:qua|truoc)\b", q_ascii)
                ) or (
                    bool(re.search(r"\btuan\s+nay\b", q_ascii))
                    and as_of_dt is not None
                    and as_of_dt.weekday() > 0
                )
                is_multi_mtd = (
                    bool(re.search(r"(?:\bthang\s+nay\b|\bdau\s+thang\b)", q_ascii))
                    and as_of_dt is not None
                    and as_of_dt.day > 1
                )
                is_prior_month_to_today = bool(
                    re.search(r"\bdat\s+(?:trong\s+)?thang\s+\d{1,2}\b", q_ascii)
                    and re.search(r"\b(?:toi|den)\s+(?:hom\s+nay|hnay|nay)\b", q_ascii)
                )
                if (
                    is_multi_rolling
                    or is_multi_week
                    or is_multi_mtd
                    or is_prior_month_to_today
                ):
                    return "Technical gate: G1 single-day SQL filter on multi-day period question"

    # G3: Pure unit-count question whose final SELECT sums only money columns.
    if (
        _G3_UNIT_QTY_Q_RE.search(q_ascii)
        and not _G3_MONEY_ASCII_RE.search(q_ascii)
        and not _G3_MONEY_ACCENT_RE.search(q_low)
    ):
        select_proj = _extract_top_level_select_clause(sql_low)
        if (
            select_proj
            and _G3_MONEY_COL_SUM_RE.search(select_proj)
            and not _G3_QTY_SELECT_RE.search(select_proj)
        ):
            return "Technical gate: G3 money aggregation returned for unit-quantity question"

    return None


def technical_abstain_gate(
    question: str,
    ans_type: str,
    ans_val: Any,
    reason: str | None,
    route_source: str,
    sql_history: list[str] | None = None,
    as_of_date: str | None = None,
) -> tuple[str, Any, str | None, str]:
    """Enforce deterministic technical invariants on outgoing predictions.

    Under Penalized Accuracy (+1 / 0 / -1), leaking NaN, Inf, invalid dates,
    empty scalars, or syntactic type mismatches causes guaranteed -1 penalties.
    """
    if ans_type in ("abstain", "unanswerable"):
        return (
            ans_type,
            None if ans_type == "abstain" else ans_val,
            reason,
            route_source,
        )

    if ans_type in ("number", "percent"):
        if isinstance(ans_val, bool) or ans_val is None:
            return (
                "abstain",
                None,
                f"Technical gate: invalid scalar {ans_val!r} for {ans_type}",
                "abstain",
            )
        try:
            num = float(ans_val)
        except TypeError, ValueError:
            return (
                "abstain",
                None,
                f"Technical gate: non-numeric {ans_val!r} for {ans_type}",
                "abstain",
            )
        if not math.isfinite(num):
            return (
                "abstain",
                None,
                f"Technical gate: non-finite {ans_val!r} for {ans_type}",
                "abstain",
            )
    elif ans_type == "boolean":
        if not isinstance(ans_val, bool):
            return (
                "abstain",
                None,
                f"Technical gate: non-bool {ans_val!r} for boolean",
                "abstain",
            )
    elif ans_type == "string":
        if (
            not isinstance(ans_val, str)
            or not ans_val.strip()
            or ans_val.strip().lower() in ("nan", "none", "null", "nat")
        ):
            return (
                "abstain",
                None,
                f"Technical gate: invalid string {ans_val!r}",
                "abstain",
            )
    elif ans_type == "date":
        if not isinstance(ans_val, str):
            return (
                "abstain",
                None,
                f"Technical gate: non-str date {ans_val!r}",
                "abstain",
            )
        s = ans_val.strip()
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
            try:
                datetime.strptime(s, "%Y-%m-%d")
            except ValueError:
                return (
                    "abstain",
                    None,
                    f"Technical gate: invalid calendar date {s!r}",
                    "abstain",
                )
        elif re.fullmatch(r"\d{4}-\d{2}", s):
            yr, mo = int(s[:4]), int(s[5:7])
            if not (2020 <= yr <= 2030 and 1 <= mo <= 12):
                return (
                    "abstain",
                    None,
                    f"Technical gate: out-of-range month {s!r}",
                    "abstain",
                )
        else:
            return (
                "abstain",
                None,
                f"Technical gate: invalid date format {s!r}",
                "abstain",
            )
    elif ans_type == "list":
        if not isinstance(ans_val, list) or len(ans_val) == 0:
            return (
                "abstain",
                None,
                f"Technical gate: empty/invalid list {ans_val!r}",
                "abstain",
            )
        for elem in ans_val:
            if elem is None or (isinstance(elem, float) and not math.isfinite(elem)):
                return (
                    "abstain",
                    None,
                    f"Technical gate: invalid list element {elem!r}",
                    "abstain",
                )
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
            if not isinstance(row, (list, tuple)) or len(row) != len(
                ans_val["columns"]
            ):
                return "abstain", None, "Technical gate: malformed table row", "abstain"
            for cell in row:
                if isinstance(cell, float) and not math.isfinite(cell):
                    return (
                        "abstain",
                        None,
                        f"Technical gate: non-finite table cell {cell!r}",
                        "abstain",
                    )

    if ans_type in ("list", "number"):
        # Type-vs-question mismatch: prevent returning a numeric list/scalar for boolean or peak-date questions
        q_norm = re.sub(
            r"\s+",
            " ",
            "".join(
                ch
                for ch in unicodedata.normalize(
                    "NFD", question.lower().replace("đ", "d")
                )
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

    if sql_history:
        consistency_reason = _sql_runtime_consistency_abstain_reason(
            question=question,
            ans_type=ans_type,
            last_sql=sql_history[-1],
            as_of_date=as_of_date,
        )
        if consistency_reason is not None:
            return "abstain", None, consistency_reason, "abstain"

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
        if context is not None and hasattr(context, "reset_trace"):
            context.reset_trace()
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
                question,
                fast_type,
                fast_val,
                fast_reason,
                "fast_path",
                sql_history=getattr(context, "sql_history", None),
                as_of_date=as_of_date,
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
