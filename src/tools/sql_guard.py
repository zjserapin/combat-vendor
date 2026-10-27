"""Parser-based guard for SQL the agent writes.

Keyword blocklists are easy to slip past. Instead, DuckDB's parser turns the
query into a syntax tree (without running it) and we check the tree:

  1. exactly one statement, and it is a SELECT (WITH ... SELECT counts)
  2. every table lives in the allowed schema (CTE names are fine)
  3. no table functions — in DuckDB `SELECT * FROM read_csv('/any/file')` is a
     SELECT that reads the filesystem
  4. no risky scalar functions (environment, settings, system calls)

The parser runs locally even when the data is in Snowflake; Snowflake-only
syntax that DuckDB can't parse is rejected (fail closed) with a readable reason.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import duckdb

MAX_SQL_CHARS = 4_000
BLOCKED_FUNCTION_PREFIXES = (
    "read_", "glob", "getenv", "current_setting", "system$", "pg_", "duckdb_", "sniff_",
    "parquet_", "query", "load", "install", "export", "import", "copy", "write_",
)

_parser: duckdb.DuckDBPyConnection | None = None


@dataclass(frozen=True)
class GuardResult:
    ok: bool
    reason: str = ""
    tables: tuple[str, ...] = ()


def check_sql(sql: str, allowed_schema: str, allowed_catalog: str = "") -> GuardResult:
    """Accept or reject one agent-written query. Never executes anything."""
    text = sql.strip().rstrip(";").strip()
    if not text:
        return _no("The query is empty.")
    if len(text) > MAX_SQL_CHARS:
        return _no(f"The query is longer than {MAX_SQL_CHARS} characters; simplify it.")

    tree = _parse(text)
    if tree.get("error"):
        message = str(tree.get("error_message", ""))
        if "Only SELECT" in message:
            return _no("Only a single read-only SELECT (or WITH ... SELECT) is allowed.")
        return _no(f"Could not parse the SQL ({message}). Use plain SELECT/WITH syntax.")
    if len(tree["statements"]) != 1:
        return _no("Send exactly one statement per call.")

    nodes = list(_walk(tree["statements"][0]))
    cte_names = {entry["key"].lower() for node in nodes if "cte_map" in node
                 for entry in node["cte_map"].get("map", [])}
    schema, catalog = allowed_schema.lower(), allowed_catalog.lower()
    tables: set[str] = set()

    for node in nodes:
        kind = node.get("type")
        if kind == "TABLE_FUNCTION":
            return _no(f"Table functions are not allowed. Query {allowed_schema}.<table> only.")
        if kind == "FUNCTION" and node.get("class") == "FUNCTION":
            name = str(node.get("function_name", "")).lower()
            if name.startswith(BLOCKED_FUNCTION_PREFIXES):
                return _no(f"The function '{name}' is not allowed.")
        if kind == "BASE_TABLE":
            problem = _table_problem(node, schema, catalog, cte_names, allowed_schema)
            if problem:
                return _no(problem)
            if node.get("schema_name"):
                tables.add(f"{node['schema_name'].lower()}.{node['table_name'].lower()}")

    return GuardResult(ok=True, tables=tuple(sorted(tables)))


def _table_problem(node: dict[str, Any], schema: str, catalog: str, cte_names: set[str],
                   allowed_schema: str) -> str:
    table = str(node.get("table_name", "")).lower()
    node_schema = str(node.get("schema_name", "")).lower()
    node_catalog = str(node.get("catalog_name", "")).lower()
    if not node_schema:
        if table in cte_names:
            return ""
        return f"Qualify the table '{table}' with its schema, e.g. {allowed_schema}.{table}."
    if node_schema != schema:
        return (f"The schema '{node_schema}' is not available. "
                f"Only {allowed_schema}.* tables can be queried.")
    if node_catalog and node_catalog != catalog:
        return f"The database '{node_catalog}' is not available."
    return ""


def _parse(text: str) -> dict[str, Any]:
    global _parser
    if _parser is None:
        _parser = duckdb.connect(":memory:")
    try:
        row = _parser.execute("SELECT json_serialize_sql(?)", [text]).fetchone()
    except duckdb.Error as exc:
        return {"error": True, "error_message": str(exc)}
    return json.loads(row[0]) if row else {"error": True, "error_message": "no output"}


def _walk(node: Any) -> Iterator[dict[str, Any]]:
    """Every dict in the syntax tree, depth first."""
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk(item)


def _no(reason: str) -> GuardResult:
    return GuardResult(ok=False, reason=reason)
