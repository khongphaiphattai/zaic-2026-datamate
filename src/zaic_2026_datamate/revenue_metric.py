"""Conservative revenue lineage verification, scoped to README column definitions.

DuckDB SQL JSON AST: https://duckdb.org/docs/current/data/json/sql_to_and_from_json
This checks the selected metric only, not WHERE/status/date correctness.
"""

import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from zaic_2026_datamate.data_loader import (
    BusinessContext,
    get_base_table_stem,
    normalize_identifier,
)


@dataclass(frozen=True)
class MetricCheck:
    allowed: bool
    reason: str
    table: str | None = None
    column: str | None = None
    evidence: tuple[str, ...] = ()
    coverage: str = "partial"
    residual_unknowns: tuple[str, ...] = (
        "row filters, date anchor, status semantics and table selection are not verified",
    )


def _text(value: str) -> str:
    value = unicodedata.normalize("NFD", value.lower()).replace("đ", "d")
    return " ".join(
        "".join(c for c in value if unicodedata.category(c) != "Mn").split()
    )


def _documented_revenue(context: BusinessContext, table: str) -> dict[str, str]:
    """No fallback to column names: require evidence for this source file/table."""
    readme = context.readme or ""
    # Only use the introductory sales-layout description for grain evidence.
    # A later stock/purchase section must not certify the sales table's grain.
    introduction = re.split(r"^#{2,6}\s", readme, maxsplit=1, flags=re.MULTILINE)[0]
    text = _text(introduction)
    line_grain = bool(
        re.search(
            r"moi dong (?:la )?mot (?:san pham|dong hang|mat hang)|mot dong hang trong don",
            text,
        )
    )
    order_grain = bool(
        re.search(r"moi dong (?:la )?mot (?:don hang|hoa don|don|phieu)\b", text)
    )
    matching_section = False
    definitions: dict[str, set[str]] = {}
    for line in readme.splitlines():
        if re.match(r"^#{1,6}\s", line):
            heading = re.match(r"^#{1,6}\s+Cột trong\s+`([^`]+)`", line, re.IGNORECASE)
            source = (
                normalize_identifier(get_base_table_stem(Path(heading.group(1)).stem))
                if heading
                else None
            )
            matching_section = bool(source and table == source)
            continue
        entry = re.match(r"^\s*-\s+\*\*([^*]+)\*\*\s*:\s*(.+)$", line)
        if matching_section and entry:
            column = normalize_identifier(entry.group(1))
            if column in context.tables[table].columns:
                definitions.setdefault(column, set()).add(
                    _text(entry.group(2)).rstrip(" .")
                )
    approved = {}
    for column, meanings in definitions.items():
        if len(meanings) != 1:
            continue
        meaning = next(iter(meanings))
        merchandise = meaning in {
            "tien hang cua don: cong thanh tien cac mat hang, truoc giam gia cua don, chua gom phi giao",
            "tien hang cua don: tong thanh tien cac dong hang, truoc giam gia cua don, chua gom phi giao",
        }
        line_amount = (
            line_grain
            and not order_grain
            and meaning
            in {
                "thanh tien sau giam",
                "thanh tien sau giam gia",
                "thanh tien dong hang sau giam",
                "thanh tien sau giam gia dong",
            }
        )
        if merchandise or line_amount:
            approved[column] = meaning
    return approved


def _arguments(expression: dict) -> list[dict]:
    if "arguments" in expression:
        args = expression["arguments"]
        if not isinstance(args, list) or any(a.get("name") for a in args):
            raise ValueError("Named or unknown function arguments are unsupported")
        return [a["expression"] for a in args]
    # DuckDB versions with the documented children representation.
    return expression["children"]


def _function(expression: dict, name: str) -> list[dict]:
    if (
        expression.get("class") != "FUNCTION"
        or expression.get("function_name") != name
        or expression.get("schema")
        or expression.get("catalog")
        or expression.get("distinct")
        or expression.get("filter")
        or expression.get("is_operator")
        or expression.get("export_state")
        or expression.get("order_bys", {}).get("orders")
    ):
        raise ValueError(f"Expected plain {name.upper()} expression")
    return _arguments(expression)


def verify_revenue_metric(context: BusinessContext, sql: str) -> MetricCheck:
    """Inspect parsed output lineage before executing model-generated SQL."""
    table = column = None
    try:
        # Consume the AST result completely, including when later guards refuse.
        parsed = json.loads(
            context.duckdb_conn.execute(
                "SELECT json_serialize_sql(?)", [sql]
            ).fetchall()[0][0]
        )
        if parsed.get("error") or len(parsed.get("statements", [])) != 1:
            raise ValueError("Expected one parseable SELECT")
        node = parsed["statements"][0]["node"]
        if (
            node.get("type") != "SELECT_NODE"
            or node.get("cte_map", {}).get("map")
            or node.get("modifiers")
            or node.get("group_expressions")
            or node.get("group_sets")
            or node.get("having")
            or node.get("qualify")
            or node.get("sample")
        ):
            raise ValueError(
                "CTE, grouped, sampled or modified revenue SELECT is unverified"
            )
        source = node["from_table"]
        if (
            source.get("type") != "BASE_TABLE"
            or source.get("schema_name")
            or source.get("catalog_name")
            or source.get("column_name_alias")
            or source.get("sample")
            or source.get("at_clause")
        ):
            raise ValueError("Expected a direct registered base table")
        table = source["table_name"]
        if table not in context.tables:
            raise ValueError("Source table is not registered in business context")
        expressions = node["select_list"]
        if len(expressions) != 1:
            raise ValueError("Revenue output must be one aggregate")
        expression = expressions[0]
        if expression.get("function_name") == "round":
            args = _function(expression, "round")
            if len(args) != 1:
                raise ValueError("Only ROUND(SUM(column)) is verified")
            expression = args[0]
        args = _function(expression, "sum")
        if len(args) != 1 or args[0].get("class") != "COLUMN_REF":
            raise ValueError("Revenue must sum one directly documented column")
        names = args[0]["column_names"]
        if len(names) not in (1, 2) or (
            len(names) == 2 and names[0] != (source.get("alias") or table)
        ):
            raise ValueError("Unverified column qualifier")
        column = names[-1]
        approved = _documented_revenue(context, table)
        if column not in approved:
            raise ValueError(
                f"{table}.{column} lacks unambiguous README evidence for merchandise revenue"
            )
        return MetricCheck(
            True,
            "Revenue column matches scoped README evidence",
            table,
            column,
            (approved[column],),
        )
    except (ValueError, KeyError, TypeError, IndexError, AttributeError) as exc:
        return MetricCheck(False, str(exc), table, column)
    except Exception as exc:
        return MetricCheck(
            False,
            f"Revenue verification unavailable: {exc}",
            table,
            column,
            coverage="unknown",
        )
