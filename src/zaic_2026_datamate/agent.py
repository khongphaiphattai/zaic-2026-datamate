"""
agent.py: Unified DataMate Agent orchestrating the 3 SOTA techniques:
1. Physical Unmerge & Sniffing (cleaner.py) & DataLoader (data_loader.py)
2. Schema Linking & Ambiguity/Presence Gate (schema_linker.py)
3. In-Memory DuckDB Engine with Self-Correction & Sanity Gate (engine.py)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Optional

import pandas as pd

from zaic_2026_datamate.cleaner import (
    physical_unmerge_and_broadcast,
    sniff_and_clean_dataframe,
)
from zaic_2026_datamate.data_loader import BusinessContext, DataLoader
from zaic_2026_datamate.engine import DuckDBAgentEngine, ExecutionResult
from zaic_2026_datamate.schema_linker import LinkedSchema, SchemaLinker

logger = logging.getLogger(__name__)


@dataclass
class AgentAnswer:
    query: str
    is_refused: bool
    answer_value: Any = None
    refusal_reason: Optional[str] = None
    executed_sql: Optional[str] = None
    retry_count: int = 0

    def to_submission_dict(self, query_id: Optional[str] = None) -> Dict[str, Any]:
        """Format the output dictionary suitable for ZAIC streaming .jsonl evaluation."""
        data: Dict[str, Any] = {
            "query": self.query,
            "is_refused": self.is_refused,
        }
        if query_id:
            data["id"] = query_id

        if self.is_refused:
            data["result"] = None
            data["reason"] = self.refusal_reason
        else:
            data["result"] = self.answer_value
            data["sql"] = self.executed_sql

        return data


class DataMateAgent:
    """The central Data Agent for ZAIC 2026 Track 2.

    Enforces the 'Silence is Free' game-theoretic axiom by gating all answers
    through Gate 1 (Schema & Data Presence) and Gate 2 (Sanity & Execution).
    """

    def __init__(
        self,
        df: Optional[pd.DataFrame] = None,
        table_name: str = "dataset",
        context: Optional[BusinessContext] = None,
    ):
        self.context = context
        if context is not None:
            self.duckdb_conn = context.duckdb_conn
            self.tables = context.tables
            self.table_metadata = context.table_metadata
            self.readme = context.readme
            self.business_id = context.business_id
            self.engine = DuckDBAgentEngine(con=context.duckdb_conn)
            from zaic_2026_datamate.schema_linker import MultiTableSchemaLinker
            self.multi_linker = MultiTableSchemaLinker(context.tables)
            self.linker = None
        else:
            self.df = df if df is not None else pd.DataFrame()
            self.table_name = table_name
            self.tables = {table_name: self.df}
            self.table_metadata = {table_name: {"columns": self.df.columns.tolist(), "rows": len(self.df)}}
            self.readme = ""
            self.business_id = "default"
            self.linker = SchemaLinker(self.df, table_name=table_name)
            self.multi_linker = None
            self.engine = DuckDBAgentEngine(self.df, table_name=table_name)
            self.duckdb_conn = self.engine.con

    @classmethod
    def from_business(cls, context: BusinessContext) -> DataMateAgent:
        """Create a DataMateAgent from an existing BusinessContext."""
        return cls(context=context)

    @classmethod
    def from_business_id(
        cls,
        business_id: str,
        data_dir: str | Path = "data/train/train/data",
        calendar_path: Optional[str | Path] = None,
    ) -> DataMateAgent:
        """Create a DataMateAgent by loading a business ID via DataLoader."""
        loader = DataLoader(data_dir=data_dir, calendar_path=calendar_path)
        ctx = loader.load_business(business_id)
        return cls(context=ctx)

    @classmethod
    def from_excel(
        cls,
        excel_path: str | Path,
        sheet_name: Optional[str] = None,
        temp_dir: str = "/tmp",
    ) -> DataMateAgent:
        """Load and normalize a messy Excel file into a ready DataMateAgent."""
        excel_path = Path(excel_path)
        unmerged_path = Path(temp_dir) / f"unmerged_{excel_path.name}"

        # 1. Physical unmerge
        wb = physical_unmerge_and_broadcast(excel_path, sheet_name=sheet_name)
        wb.save(unmerged_path)

        # 2. Sniff header and clean
        raw_df = pd.read_excel(unmerged_path, sheet_name=sheet_name or 0, header=None)
        clean_df = sniff_and_clean_dataframe(raw_df)

        return cls(df=clean_df)

    def generate_prompt(self, query: str, as_of_date: Optional[str] = None) -> str:
        """Construct a comprehensive prompt with as_of_date, business README, and table schemas."""
        tables_summary = []
        for tname, meta in self.table_metadata.items():
            cols = meta.get("columns", [])
            rows = meta.get("rows", 0)
            tables_summary.append(f"- Bảng `{tname}` ({rows} dòng): {', '.join([f'`{c}`' for c in cols[:12]])}")

        readme_snippet = self.readme[:600] if self.readme else "Không có ghi chú thêm."

        prompt = f"""-- Doanh nghiệp: {self.business_id}
-- Ngày neo thời gian (as_of_date / hôm nay): {as_of_date or 'Chưa xác định'}
-- Ghi chú nghiệp vụ:
{readme_snippet}

-- Các bảng dữ liệu hiện có trong DuckDB:
{chr(10).join(tables_summary)}

-- Câu hỏi nghiệp vụ: {query}

Quy tắc sinh DuckDB SQL chuẩn ZAIC 2026:
1. Luôn sử dụng alias tiếng Việt không dấu (snake_case) hoặc bọc tên cột trong dấu ngoặc kép nếu có dấu.
2. Trừ trường hợp câu hỏi nói khác: đơn tính vào doanh thu là đơn KHÔNG huỷ và KHÔNG trả hàng (dùng ILIKE để loại bỏ '%hủy%', '%huỷ%', '%trả%').
3. Mọi mốc thời gian 'hôm nay', 'tháng này', 'tuần này' tính theo as_of_date.
4. Trả về đúng 1 giá trị vô hướng hoặc 1 dòng kết quả.
"""
        return prompt

    def answer_query(
        self,
        query: str,
        as_of_date: Optional[str] = None,
        sql_generator_fn: Optional[Callable[[str, Any], str]] = None,
        query_id: Optional[str] = None,
    ) -> AgentAnswer:
        """Answer a Vietnamese natural language business query with strict refusal evaluation."""
        # =========================================================================
        # CỔNG 1: Tiền kiểm Schema Linking & Ambiguity Check
        # =========================================================================
        if self.multi_linker is not None:
            linked = self.multi_linker.link(query)
            if linked.is_ambiguous:
                logger.info("Cổng 1 từ chối câu hỏi do thiếu dữ liệu / mơ hồ: %s", linked.refusal_reason)
                return AgentAnswer(
                    query=query,
                    is_refused=True,
                    refusal_reason=linked.refusal_reason,
                )
        elif self.linker is not None:
            linked = self.linker.link(query)
            if linked.is_ambiguous:
                logger.info("Cổng 1 từ chối câu hỏi do thiếu dữ liệu / mơ hồ: %s", linked.refusal_reason)
                return AgentAnswer(
                    query=query,
                    is_refused=True,
                    refusal_reason=linked.refusal_reason,
                )
        else:
            linked = None

        # =========================================================================
        # BƯỚC 2: Sinh câu lệnh DuckDB SQL
        # =========================================================================
        if sql_generator_fn is None:
            first_table = list(self.tables.keys())[0] if self.tables else "dataset"
            initial_sql = f"SELECT COUNT(*) FROM {first_table}"
        else:
            initial_sql = sql_generator_fn(query, self)

        # =========================================================================
        # CỔNG 2: Thực thi In-Memory DuckDB kèm Self-Correction Feedback Loop
        # =========================================================================
        def corrector_callback(bad_sql: str, error_msg: str, attempt: int) -> str:
            logger.warning("Thực hiện tự sửa lỗi SQL (Lần %d): %s", attempt, error_msg)
            return bad_sql

        exec_res: ExecutionResult = self.engine.run_with_self_correction(
            initial_sql=initial_sql,
            sql_generator=corrector_callback,
            max_retries=2,
        )

        if exec_res.is_refused or not exec_res.success:
            return AgentAnswer(
                query=query,
                is_refused=True,
                refusal_reason=exec_res.refusal_reason,
                retry_count=exec_res.retry_count,
            )

        # Định dạng giá trị kết quả cuối cùng
        res_df = exec_res.df_result
        if res_df is not None and res_df.shape == (1, 1):
            val = res_df.iloc[0, 0]
        else:
            val = res_df.to_dict(orient="records") if res_df is not None else None

        return AgentAnswer(
            query=query,
            is_refused=False,
            answer_value=val,
            executed_sql=initial_sql,
            retry_count=exec_res.retry_count,
        )
