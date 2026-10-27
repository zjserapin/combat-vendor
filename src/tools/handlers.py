"""What each tool actually does. Handlers return text; user-fixable problems raise ToolError.

Handlers never write. Agent SQL runs through the guard, then inside a
transaction that is always rolled back, with a row cap.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from config import Config
from src import ledger
from src.db import Database, Row
from src.tools.sql_guard import check_sql

# Catalog queries differ only in where each warehouse keeps comments.
_CATALOG_SQL = {
    "duckdb": """
        SELECT t.table_name, t.table_type, t.TABLE_COMMENT AS table_comment,
               c.column_name, c.data_type, c.COLUMN_COMMENT AS column_comment
        FROM information_schema.tables t
        JOIN information_schema.columns c
          ON c.table_schema = t.table_schema AND c.table_name = t.table_name
        WHERE t.table_schema = ?
        ORDER BY t.table_name, c.ordinal_position""",
    "snowflake": """
        SELECT t.table_name, t.table_type, t.comment AS table_comment,
               c.column_name, c.data_type, c.comment AS column_comment
        FROM information_schema.tables t
        JOIN information_schema.columns c
          ON c.table_schema = t.table_schema AND c.table_name = t.table_name
        WHERE t.table_schema = UPPER(?)
        ORDER BY t.table_name, c.ordinal_position""",
}


class ToolError(Exception):
    """A problem the model can fix (bad input, rejected SQL). Returned as an error result."""


def run_sql(db: Database, config: Config, query: Any, max_rows: int, max_chars: int) -> str:
    if not isinstance(query, str) or not query.strip():
        raise ToolError("Provide the SQL as a non-empty string in 'query'.")
    verdict = check_sql(query, config.bank_schema, config.snowflake_database)
    if not verdict.ok:
        raise ToolError(f"Query rejected: {verdict.reason}")
    rows = db.query_then_rollback(query.strip().rstrip(";"), max_rows=max_rows + 1)
    truncated = len(rows) > max_rows
    rows = rows[:max_rows]
    header = f"{len(rows)} row(s)" + (f" (truncated to the first {max_rows})" if truncated else "")
    return _clip(f"{header}\n{_table(rows)}" if rows else "0 rows.", max_chars)


def describe_tables(db: Database, config: Config) -> str:
    rows = db.query(_CATALOG_SQL[config.db_backend], [config.bank_schema])
    lines: list[str] = []
    current = None
    for r in rows:
        if r["table_name"] != current:
            current = r["table_name"]
            kind = "view" if "VIEW" in str(r["table_type"]).upper() else "table"
            comment = f" — {r['table_comment']}" if r["table_comment"] else ""
            lines.append(f"\n{config.bank_schema}.{str(current).lower()} ({kind}){comment}")
        note = f" — {r['column_comment']}" if r["column_comment"] else ""
        lines.append(f"  - {str(r['column_name']).lower()} {r['data_type']}{note}")
    return "\n".join(lines).strip() or "No tables found."


def recall_case_memory(memory_db: Database, config: Config, customer_id: Any) -> str:
    if isinstance(customer_id, bool) or not isinstance(customer_id, int):
        raise ToolError("Provide 'customer_id' as an integer.")
    notes = ledger.recall_memory(memory_db, config, customer_id)
    if not notes:
        return f"No earlier decisions on customer {customer_id}."
    return "\n".join(f"- {n}" for n in notes)


def acknowledge_decision() -> str:
    return "Decision received. The investigation is complete."


def _table(rows: list[Row]) -> str:
    columns = list(rows[0])
    lines = ["| " + " | ".join(columns) + " |", "|" + "---|" * len(columns)]
    lines += ["| " + " | ".join(_cell(r[c]) for c in columns) + " |" for r in rows]
    return "\n".join(lines)


def _cell(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, datetime):
        return value.isoformat(sep=" ", timespec="minutes")
    if isinstance(value, date):
        return value.isoformat()
    return str(value).replace("|", "/").replace("\n", " ")


def _clip(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n... (output clipped; narrow the query or select fewer columns)"
