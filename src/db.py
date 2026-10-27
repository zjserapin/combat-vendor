"""Thin database wrapper: DuckDB locally, Snowflake at Ally.

Both backends speak `?` (qmark) parameters and return rows as dicts, so query
code is written once. SQL itself sticks to the overlap of the two dialects —
see docs/04-snowflake-port-guide.md for the handful of differences.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import Any, Self

from config import Config

Row = dict[str, Any]


class Database:
    """Wraps a DB-API connection with dict-returning queries and a transaction helper.

    One cursor is held for the object's lifetime: in DuckDB every new cursor is a
    separate connection, so BEGIN/COMMIT only work if all statements share one.
    """

    def __init__(self, config: Config, read_only: bool = False, role: str | None = None) -> None:
        self.backend = config.db_backend
        self._conn = _connect(config, read_only, role)
        self._cur = self._conn.cursor()

    def query(self, sql: str, params: Sequence[Any] = (), max_rows: int | None = None) -> list[Row]:
        """Run a SELECT and return rows as lowercase-keyed dicts (at most max_rows)."""
        self._run(sql, params)
        columns = [d[0].lower() for d in self._cur.description or []]
        if max_rows is None:
            rows = self._cur.fetchall()
        else:
            rows = self._cur.fetchmany(max_rows)
            abort = getattr(self._cur, "abort", None)  # DuckDB: release the unread remainder
            if abort is not None:
                abort()
        return [dict(zip(columns, row, strict=True)) for row in rows]

    def query_then_rollback(
        self, sql: str, params: Sequence[Any] = (), max_rows: int | None = None
    ) -> list[Row]:
        """Run a query inside a transaction that is always rolled back.

        Used for agent-written SQL: even if a write slipped past the SQL guard,
        it could not persist.
        """
        self._run("BEGIN")
        try:
            return self.query(sql, params, max_rows)
        finally:
            self._run("ROLLBACK")

    def execute(self, sql: str, params: Sequence[Any] = ()) -> None:
        """Run a statement that returns nothing worth reading (DDL, INSERT, UPDATE)."""
        self._run(sql, params)

    @contextmanager
    def transaction(self) -> Iterator[Self]:
        """All-or-nothing block. BEGIN/COMMIT/ROLLBACK work on DuckDB and Snowflake."""
        self._run("BEGIN")
        try:
            yield self
        except BaseException:
            self._run("ROLLBACK")
            raise
        self._run("COMMIT")

    def close(self) -> None:
        self._cur.close()
        self._conn.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _run(self, sql: str, params: Sequence[Any] = ()) -> None:
        if params:
            self._cur.execute(sql, list(params))
        else:
            self._cur.execute(sql)


def agent_database(config: Config, harness_db: Database) -> Database:
    """The connection the agent's tools query through.

    DuckDB can't hold read-only and read-write handles to one file in a single
    process, so locally the agent shares the harness connection (protected by the
    SQL guard + always-rolled-back queries). On Snowflake the agent gets its own
    connection under a SELECT-only role (SNOWFLAKE_AGENT_ROLE) — a database-level wall.
    """
    if config.db_backend == "snowflake" and config.snowflake_agent_role:
        return Database(config, role=config.snowflake_agent_role)
    return harness_db


def _connect(config: Config, read_only: bool, role: str | None) -> Any:
    if config.db_backend == "snowflake":
        return _connect_snowflake(config, role)
    import duckdb

    if read_only and not config.duckdb_path.exists():
        raise FileNotFoundError(f"{config.duckdb_path} not found. Run `python main.py seed`.")
    config.duckdb_path.parent.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(str(config.duckdb_path), read_only=read_only)


def _connect_snowflake(config: Config, role: str | None) -> Any:
    # Imported lazily so local runs never need the Snowflake package installed.
    import snowflake.connector

    snowflake.connector.paramstyle = "qmark"
    # Read-only is enforced by the Snowflake ROLE (grant SELECT only), not by the client.
    return snowflake.connector.connect(
        account=config.snowflake_account,
        user=config.snowflake_user,
        role=role or config.snowflake_role or None,
        warehouse=config.snowflake_warehouse or None,
        database=config.snowflake_database or None,
        authenticator=config.snowflake_authenticator,
    )
