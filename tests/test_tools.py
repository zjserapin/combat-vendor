"""Block 4: the SQL guard, the rollback layer, and the toolbox."""

import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from src import ledger
from src.alert_queue import fetch_alerts
from src.db import Database, agent_database
from src.ledger import DecisionRecord
from src.tools import DECISIONS, Toolbox, check_sql, tool_schemas

ALLOWED = [
    "SELECT txn_id FROM bank.transactions LIMIT 5",
    "select * from BANK.CUSTOMERS where risk_rating > 50",
    ("WITH a AS (SELECT account_id FROM bank.accounts) SELECT * FROM a JOIN bank.transactions t "
     "ON t.account_id = a.account_id"),
    ("SELECT DATEADD(day, -30, txn_ts) FROM bank.transactions "
     "QUALIFY ROW_NUMBER() OVER (PARTITION BY account_id ORDER BY txn_ts) = 1"),
    "SELECT * FROM bank.alerts;",
]

ATTACKS = [
    "SELECT * FROM labels.alert_dispositions",
    'SELECT * FROM "labels"."alert_dispositions"',
    "SELECT * FROM LABELS.ALERT_DISPOSITIONS",
    "SELECT * FROM bank.alerts a, labels.alert_dispositions d",
    "SELECT alert_id FROM bank.alerts UNION SELECT alert_id FROM labels.alert_dispositions",
    "WITH x AS (SELECT * FROM labels.alert_dispositions) SELECT * FROM x",
    "SELECT * FROM bank.alerts WHERE alert_id IN (SELECT alert_id FROM agent.decision_log)",
    "SELECT * FROM information_schema.tables",
    "SELECT * FROM transactions",
    "DELETE FROM bank.customers",
    "UPDATE bank.customers SET risk_rating = 1",
    "INSERT INTO bank.kyc_notes SELECT * FROM bank.kyc_notes",
    "CREATE TABLE bank.x AS SELECT 1",
    "DROP TABLE bank.customers",
    "SELECT 1; DROP TABLE bank.customers",
    "COPY bank.customers TO '/tmp/out.csv'",
    "ATTACH 'other.db' AS other",
    "PRAGMA database_list",
    "SET threads = 1",
    "SELECT * FROM read_csv('/etc/passwd')",
    "SELECT * FROM '/etc/passwd'",
    "SELECT * FROM glob('*')",
    "SELECT * FROM duckdb_tables()",
    "SELECT getenv('HOME')",
    "SELECT current_setting('threads')",
    "",
    "SELECT " + "1, " * 2_000 + "1",
]


@pytest.fixture
def db(workspace):
    with Database(workspace) as conn:
        ledger.ensure_agent_schema(conn, workspace)
        yield conn


@pytest.fixture
def toolbox(db, workspace):
    return Toolbox(db, workspace)


def _call(toolbox, name, **args):
    return toolbox.run({"id": f"t-{name}", "name": name, "input": args})


# --- layer 1: the SQL guard -----------------------------------------------------------

@pytest.mark.parametrize("sql", ALLOWED)
def test_guard_allows_ordinary_reads(sql):
    assert check_sql(sql, "bank").ok


@pytest.mark.parametrize("sql", ATTACKS)
def test_guard_rejects_everything_else(sql):
    verdict = check_sql(sql, "bank")
    assert not verdict.ok and verdict.reason


def test_guard_reports_tables_touched():
    assert check_sql("SELECT 1 FROM bank.accounts JOIN bank.customers USING (customer_id)",
                     "bank").tables == ("bank.accounts", "bank.customers")


# --- layer 2: always-rolled-back execution ---------------------------------------------------

def test_rollback_layer_undoes_a_write_even_without_the_guard(db, workspace):
    """If a write ever slipped past the guard, it still could not persist."""
    table = f"{workspace.bank_schema}.kyc_notes"
    before = db.query(f"SELECT COUNT(*) AS n FROM {table}")[0]["n"]
    returned = db.query_then_rollback(
        f"INSERT INTO {table} VALUES (1, DATE '2026-01-01', 'x', 'planted', 9999) "
        f"RETURNING note_id")
    assert returned == [{"note_id": 9999}]
    assert db.query(f"SELECT COUNT(*) AS n FROM {table}")[0]["n"] == before


def test_row_cap_leaves_the_connection_usable(db, workspace):
    rows = db.query(f"SELECT txn_id FROM {workspace.bank_schema}.transactions", max_rows=3)
    assert len(rows) == 3
    assert db.query("SELECT 1 AS one") == [{"one": 1}]  # no half-read result blocking us


def test_local_agent_shares_the_guarded_harness_connection(db, workspace):
    assert agent_database(workspace, db) is db


# --- the toolbox -------------------------------------------------------------------------------

def test_schemas_are_strict_and_complete(workspace):
    schemas = tool_schemas(workspace)
    assert [s["name"] for s in schemas] == ["run_sql", "describe_tables", "recall_case_memory",
                                            "submit_decision"]
    for s in schemas:
        props = s["input_schema"]["properties"]
        assert s["strict"] is True
        assert s["input_schema"]["additionalProperties"] is False
        assert set(s["input_schema"]["required"]) == set(props)
    decision = schemas[-1]["input_schema"]["properties"]["decision"]
    assert decision["enum"] == DECISIONS


def test_run_sql_returns_a_table(toolbox):
    result = _call(toolbox, "run_sql", purpose="count",
                   query="SELECT COUNT(*) AS n FROM bank.customers")
    assert "is_error" not in result and "| n |" in result["content"] and "860" in result["content"]


def test_rejected_sql_is_an_error_the_model_can_read(toolbox):
    result = _call(toolbox, "run_sql", purpose="peek", query="SELECT * FROM labels.alert_dispositions")
    assert result["is_error"] and "not available" in result["content"]


def test_database_errors_come_back_as_text(toolbox):
    result = _call(toolbox, "run_sql", purpose="typo", query="SELECT nope FROM bank.customers")
    assert result["is_error"] and "nope" in result["content"]


def test_large_results_are_capped_and_clipped(db, workspace):
    small = Toolbox(db, workspace, max_rows=5, max_chars=120)
    content = _call(small, "run_sql", purpose="all", query="SELECT * FROM bank.transactions")["content"]
    assert "truncated to the first 5" in content and "output clipped" in content


def test_describe_tables_covers_bank_only_and_is_cached(toolbox):
    first = _call(toolbox, "describe_tables")["content"]
    assert "bank.transactions (table)" in first and "bank.alerts (view)" in first
    assert "USD cents" in first
    assert "dispositions" not in first and "decision_log" not in first
    assert _call(toolbox, "describe_tables")["content"] is first


def test_recall_case_memory_reads_prior_decisions(db, workspace, toolbox):
    alert = fetch_alerts(db, workspace)[0]
    ledger.record_decision(db, workspace, DecisionRecord.for_alert(
        alert, "run-1", "DISMISS", 0.9, "Travel notice on file.", "Close."))
    content = _call(toolbox, "recall_case_memory", customer_id=alert.customer_id)["content"]
    assert "DISMISS (0.90)" in content and "Travel notice" in content


@pytest.mark.parametrize("bad", ["10037", True, None])
def test_recall_rejects_non_integer_ids(toolbox, bad):
    assert _call(toolbox, "recall_case_memory", customer_id=bad)["is_error"]


def test_unknown_tool_lists_the_real_ones(toolbox):
    result = _call(toolbox, "teleport")
    assert result["is_error"] and "run_sql" in result["content"]


def test_submit_decision_is_terminal_and_acknowledged(toolbox):
    assert Toolbox.is_terminal("submit_decision") and not Toolbox.is_terminal("run_sql")
    result = _call(toolbox, "submit_decision", decision="DISMISS", confidence=0.9,
                   rationale="r", key_facts=["f"], next_action="n")
    assert "is_error" not in result and "complete" in result["content"]


def test_accepts_sdk_blocks_and_json_text(toolbox):
    sdk_block = SimpleNamespace(id="toolu_1", name="recall_case_memory", input={"customer_id": 1})
    assert toolbox.run(sdk_block)["tool_use_id"] == "toolu_1"
    as_text = {"id": "t", "name": "recall_case_memory", "input": json.dumps({"customer_id": 1})}
    assert "is_error" not in toolbox.run(as_text)


def test_every_call_is_logged(toolbox):
    _call(toolbox, "run_sql", purpose="p", query="SELECT 1 AS x FROM bank.customers LIMIT 1")
    _call(toolbox, "teleport")
    assert [(c.name, c.is_error) for c in toolbox.calls] == [("run_sql", False), ("teleport", True)]
    assert toolbox.calls[0].input["purpose"] == "p"


def test_snowflake_agent_gets_its_own_role(monkeypatch, workspace, db):
    opened = {}

    class FakeDatabase:
        def __init__(self, config, read_only=False, role=None):
            opened["role"] = role

    monkeypatch.setattr("src.db.Database", FakeDatabase)
    cfg = replace(workspace, db_backend="snowflake", snowflake_agent_role="AML_POC_READER")
    agent_database(cfg, db)
    assert opened["role"] == "AML_POC_READER"


def test_strict_can_be_switched_off_for_an_endpoint_that_rejects_it(workspace):
    assert all("strict" not in t for t in tool_schemas(replace(workspace, tools_strict=False)))
