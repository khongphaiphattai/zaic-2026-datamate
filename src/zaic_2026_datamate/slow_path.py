"""
slow_path.py: Slow Path Engine for ZAIC 2026 DataMate.

Translates complex, multi-condition long-tail queries into DuckDB ANSI SQL
using Small Language Models (Qwen2.5-Coder-7B/14B <= 15B) with strict Gate 2 execution guards.
Enforces 'Silence is Free' by refusing to guess when SQL fails, returns NULL, or has low confidence.
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from typing import Any, Dict, List, Optional, Tuple

import duckdb
import pandas as pd

from zaic_2026_datamate.data_loader import BusinessContext
from zaic_2026_datamate.engine import DuckDBAgentEngine
from zaic_2026_datamate.llm_client import LLMClient
from zaic_2026_datamate.prompt_generator import SYSTEM_PROMPT, generate_text_to_sql_prompt

logger = logging.getLogger(__name__)


class SlowPathEngine:
    """Executes long-tail natural language questions via SLM Text-to-SQL

    sandboxed in DuckDB with self-correction and double refusal guards.
    """

    def __init__(
        self,
        llm_client: LLMClient,
        confidence_threshold: float = 0.88,
        enable_self_correction: bool = True,
    ):
        self.client = llm_client
        self.confidence_threshold = confidence_threshold
        self.enable_self_correction = enable_self_correction

    def resolve_slow_path(
        self,
        question: str,
        context: BusinessContext,
        as_of_date: Optional[str] = None,
    ) -> Tuple[str, Any, Optional[str]]:
        """Attempt to resolve a query via Slow Path Text-to-SQL.

        Returns:
            Tuple of (answer_type, answer_value, reason).
            If execution fails or is uncertain, returns ('abstain', None, reason).
        """
        # 1. Build prompt
        prompt = generate_text_to_sql_prompt(
            question=question,
            context=context,
            as_of_date=as_of_date,
            business_id=context.business_id,
        )

        # 2. Call LLM
        try:
            raw_response = self.client.generate(prompt=prompt, system_prompt=SYSTEM_PROMPT)
        except Exception as e:
            logger.warning("LLM client generation failed: %s", e)
            return "abstain", None, f"LLM generation failed: {e}"

        # 3. Parse JSON response
        parsed = self._extract_json(raw_response)
        if not parsed:
            logger.warning("LLM response did not contain valid JSON: %s", raw_response[:100])
            return "abstain", None, "LLM returned invalid JSON"

        ans_type = parsed.get("type", "abstain")
        sql = parsed.get("sql")
        confidence = float(parsed.get("confidence", 0.0))
        reason = parsed.get("reason") or parsed.get("thought")

        # 4. Silence is Free: Gate 1 - Confidence and Refusal Check
        if confidence < self.confidence_threshold:
            return "abstain", None, f"Confidence {confidence:.2f} < threshold {self.confidence_threshold}"

        if ans_type == "abstain":
            return "abstain", None, reason or "Model opted to abstain"

        if ans_type == "unanswerable":
            return "unanswerable", None, reason or "Model determined query is unanswerable from data"

        if not sql or not isinstance(sql, str) or not sql.strip():
            return "abstain", None, "No valid SQL query provided"

        # 5. Gate 2: In-Memory DuckDB Execution Sandbox
        engine = DuckDBAgentEngine(con=context.duckdb_conn)
        cleaned_sql = self._clean_sql(sql)

        # Execute query
        success, res_df, err = engine.execute_query(cleaned_sql)

        # Optional self-correction loop (1 retry)
        if not success and self.enable_self_correction and err:
            logger.info("Retrying SQL with self-correction feedback: %s", err)
            correction_prompt = f"{prompt}\n\nLệnh SQL sau bị lỗi: `{cleaned_sql}`\nThông báo lỗi DuckDB: `{err}`\nHãy sửa lại câu lệnh SQL để khắc phục lỗi trên."
            try:
                corr_resp = self.client.generate(prompt=correction_prompt, system_prompt=SYSTEM_PROMPT)
                corr_parsed = self._extract_json(corr_resp)
                if corr_parsed and corr_parsed.get("sql"):
                    cleaned_sql = self._clean_sql(corr_parsed["sql"])
                    success, res_df, err = engine.execute_query(cleaned_sql)
            except Exception as e:
                logger.warning("Self-correction attempt failed: %s", e)

        if not success or res_df is None:
            return "abstain", None, f"SQL execution failed: {err}"

        # Gate 2 Sanity Checks: Empty / Null / Plausibility
        if res_df.empty:
            return "abstain", None, "SQL executed successfully but returned 0 rows"

        # Format output to ZAIC Rulebook Section 9 specifications
        return self._format_result(res_df, ans_type)

    def _extract_json(self, text: str) -> Optional[Dict[str, Any]]:
        """Safely extract JSON object from LLM response text."""
        text = text.strip()
        # Direct parse
        try:
            return json.loads(text)
        except Exception:
            pass

        # Markdown block ```json ... ```
        m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(1))
            except Exception:
                pass

        # First outer curly braces {...}
        m2 = re.search(r"(\{.*\})", text, re.DOTALL)
        if m2:
            try:
                return json.loads(m2.group(1))
            except Exception:
                pass

        return None

    def _clean_sql(self, sql: str) -> str:
        """Strip markdown fences and whitespace from SQL."""
        s = sql.strip()
        s = re.sub(r"^```(?:sql)?\s*", "", s, flags=re.IGNORECASE)
        s = re.sub(r"\s*```$", "", s)
        return s.strip()

    def _format_result(self, df: pd.DataFrame, target_type: str) -> Tuple[str, Any, Optional[str]]:
        """Format DuckDB DataFrame result into exact ZAIC Rulebook output types."""
        # Check all-null
        if df.isna().all().all():
            return "abstain", None, "Result values are all NULL"

        target_type = target_type.lower().strip()

        # 1. NUMBER
        if target_type == "number":
            val = df.iloc[0, 0]
            if pd.isna(val) or val is None:
                return "abstain", None, "Scalar number result is NULL"
            try:
                num = float(val)
                # Revenue / count sanity: negative revenue or negative order count -> refuse
                if num < 0:
                    return "abstain", None, f"Negative numeric result {num} is invalid for positive business metric"
                if num.is_integer() or abs(num - round(num)) < 1e-4:
                    return "number", int(round(num)), None
                return "number", round(num, 2), None
            except Exception as e:
                return "abstain", None, f"Cannot format number: {e}"

        # 2. PERCENT
        elif target_type == "percent":
            val = df.iloc[0, 0]
            if pd.isna(val) or val is None:
                return "abstain", None, "Percent result is NULL"
            try:
                num = float(val)
                # If number is ratio (e.g. 0.1245 -> 12.45)
                # Usually SQL might return 12.45 or 0.1245.
                # If strictly between 0 and 1, check if model intended percentage
                # But in ZAIC answers, e.g. 2.08, -12.45.
                return "percent", round(num, 2), None
            except Exception as e:
                return "abstain", None, f"Cannot format percent: {e}"

        # 3. DATE
        elif target_type == "date":
            val = df.iloc[0, 0]
            if pd.isna(val) or val is None:
                return "abstain", None, "Date result is NULL"
            dt_str = str(val).strip()
            # YYYY-MM-DD or YYYY-MM format
            m = re.search(r"(\d{4}-\d{2}(?:-\d{2})?)", dt_str)
            if m:
                return "date", m.group(1), None
            return "abstain", None, f"Result {dt_str} does not match ISO date pattern"

        # 4. LIST
        elif target_type == "list":
            # Extract first column as list of items
            col = df.columns[0]
            items = []
            for item in df[col].dropna():
                s = str(item).strip()
                if s:
                    items.append(s)
            if not items:
                return "abstain", None, "List result is empty"
            return "list", items, None

        # 5. STRING
        elif target_type == "string":
            val = df.iloc[0, 0]
            if pd.isna(val) or val is None:
                return "abstain", None, "String result is NULL"
            s = unicodedata.normalize("NFC", str(val).strip().lower())
            s = re.sub(r"\s+", " ", s)
            return "string", s, None

        # 6. TABLE
        elif target_type == "table":
            columns = [str(c) for c in df.columns]
            rows = []
            for _, r in df.iterrows():
                rows.append([None if pd.isna(x) else x for x in r])
            if not rows:
                return "abstain", None, "Table rows are empty"
            return "table", {"columns": columns, "rows": rows}, None

        # Fallback to scalar extraction
        if df.shape == (1, 1):
            val = df.iloc[0, 0]
            if pd.isna(val) or val is None:
                return "abstain", None, "Result is NULL"
            try:
                num = float(val)
                if num.is_integer():
                    return "number", int(num), None
                return "number", round(num, 2), None
            except Exception:
                return "string", str(val).strip(), None

        return "abstain", None, f"Unsupported target type {target_type}"
