"""Block 2: the alert contract, the queue, and the append-only ledger."""

from dataclasses import replace
from datetime import timedelta

import pytest

from src import alert_queue, ledger
from src.alert_queue import QUEUE_SQL, fetch_alerts
from src.db import Database
from src.ledger import DecisionRecord, RunStats


@pytest.fixture
def db(workspace):
    with Database(workspace) as conn:
        ledger.ensure_agent_schema(conn, workspace)
        yield conn


def _decide(db, config, alert, decision="DISMISS", **extra):
    record = DecisionRecord.for_alert(alert, "run-test", decision, 0.8,
                                      "Documented explanation on file.", "Close alert.", **extra)
    ledger.record_decision(db, config, record)
    return record


# --- the alert contract -------------------------------------------------------

def test_every_label_maps_to_exactly_one_alert(seeded):
    with Database(seeded, read_only=True) as conn:
        alerts = conn.query(f"SELECT alert_id, window_start, window_end "
                            f"FROM {seeded.bank_schema}.alerts")
        labels = conn.query(f"SELECT alert_id, window_start, window_end "
                            f"FROM {seeded.labels_schema}.alert_dispositions")
    by_id = {a["alert_id"]: a for a in alerts}
    assert len(by_id) == len(alerts), "alert_id must be unique"
    for label in labels:
        alert = by_id[label["alert_id"]]
        assert (alert["window_start"], alert["window_end"]) == \
               (label["window_start"], label["window_end"])


# --- the queue ------------------------------------------------------------------

def test_queue_holds_every_alert_in_the_lookback(db, workspace):
    alerts = fetch_alerts(db, workspace)
    assert len(alerts) == 60
    earliest = workspace.as_of_date - timedelta(days=workspace.alert_lookback_days)
    assert all(earliest <= a.window_end <= workspace.as_of_date for a in alerts)


def test_shorter_lookback_returns_a_subset(db, workspace):
    narrow = replace(workspace, alert_lookback_days=7)
    some = fetch_alerts(db, narrow)
    assert 0 < len(some) < 60
    assert all(a.window_end >= narrow.as_of_date - timedelta(days=7) for a in some)


def test_queue_is_in_priority_order(db, workspace):
    keys = [(-a.blocked_txns, -a.risk_rating, -a.flagged_cents) for a in fetch_alerts(db, workspace)]
    assert keys == sorted(keys)


def test_limit_caps_the_queue(db, workspace):
    assert len(fetch_alerts(db, workspace, limit=5)) == 5


def test_queue_never_touches_labels(workspace):
    assert workspace.labels_schema not in QUEUE_SQL
    assert "dispositions" not in QUEUE_SQL


def test_dates_normalize_from_any_driver_shape():
    from datetime import date, datetime
    assert alert_queue._as_date(datetime(2026, 9, 1, 13, 5)) == date(2026, 9, 1)
    assert alert_queue._as_date("2026-09-01") == date(2026, 9, 1)


# --- the ledger ------------------------------------------------------------------

def test_agent_schema_setup_is_idempotent(db, workspace):
    ledger.ensure_agent_schema(db, workspace)  # second call must not fail or drop data
    tables = db.query("SELECT table_name FROM information_schema.tables WHERE table_schema = ?",
                      [workspace.agent_schema])
    assert {t["table_name"] for t in tables} == set(ledger.AGENT_TABLES)


def test_decision_is_written_twice(db, workspace):
    alert = fetch_alerts(db, workspace)[0]
    _decide(db, workspace, alert)
    logged = db.query(f"SELECT decision FROM {workspace.agent_schema}.decision_log")
    memory = ledger.recall_memory(db, workspace, alert.customer_id)
    assert [r["decision"] for r in logged] == ["DISMISS"]
    assert len(memory) == 1 and alert.alert_id in memory[0] and "DISMISS (0.80)" in memory[0]


def test_decided_alerts_leave_the_queue(db, workspace):
    first = fetch_alerts(db, workspace)[0]
    _decide(db, workspace, first)
    remaining = fetch_alerts(db, workspace)
    assert first.alert_id not in {a.alert_id for a in remaining}
    assert len(remaining) == 59
    assert len(fetch_alerts(db, workspace, include_decided=True)) == 60


def test_new_activity_reopens_a_decided_alert(db, workspace):
    alert = fetch_alerts(db, workspace)[0]
    stale = replace(alert, window_end=alert.window_end - timedelta(days=1))
    _decide(db, workspace, stale)  # decided before the latest flagged activity arrived
    assert alert.alert_id in {a.alert_id for a in fetch_alerts(db, workspace)}


def test_ledger_is_append_only_and_latest_wins(db, workspace):
    alert = fetch_alerts(db, workspace)[0]
    _decide(db, workspace, alert, decision="KYC_REVIEW")
    _decide(db, workspace, alert, decision="ESCALATE")
    all_rows = db.query(f"SELECT decision FROM {workspace.agent_schema}.decision_log")
    latest = ledger.latest_decisions(db, workspace)
    assert len(all_rows) == 2
    assert [(r["alert_id"], r["decision"]) for r in latest] == [(alert.alert_id, "ESCALATE")]


def test_failed_memory_write_rolls_back_the_decision(db, workspace, monkeypatch):
    alert = fetch_alerts(db, workspace)[0]
    real_insert = ledger._insert

    def insert_then_fail(conn, table, values):
        if table.endswith("case_memory"):
            raise RuntimeError("simulated failure")
        real_insert(conn, table, values)

    monkeypatch.setattr(ledger, "_insert", insert_then_fail)
    with pytest.raises(RuntimeError):
        _decide(db, workspace, alert)
    assert db.query(f"SELECT COUNT(*) AS n FROM {workspace.agent_schema}.decision_log")[0]["n"] == 0


def test_review_required_records_its_reason(db, workspace):
    alert = fetch_alerts(db, workspace)[0]
    record = DecisionRecord.for_alert(alert, "run-test", ledger.REVIEW_REQUIRED, None, "", "",
                                      review_reason="budget exhausted")
    ledger.record_decision(db, workspace, record)
    memory = ledger.recall_memory(db, workspace, alert.customer_id)[0]
    assert "REVIEW_REQUIRED. budget exhausted" in memory


def test_run_log_round_trip(db, workspace):
    run_id = ledger.start_run(db, workspace, "claude-test", "v1", run_id="run-abc")
    ledger.finish_run(db, workspace, run_id, RunStats(alerts_attempted=3, alerts_decided=2,
                                                      alerts_failed=1, model_calls=7))
    row = db.query(f"SELECT * FROM {workspace.agent_schema}.run_log WHERE run_id = ?",
                   [run_id])[0]
    assert (row["alerts_attempted"], row["alerts_decided"], row["model_calls"]) == (3, 2, 7)
    assert row["finished_at"] >= row["started_at"]


def test_old_agent_tables_gain_new_columns_and_keep_their_rows(workspace):
    """A decision_log created by an earlier version must upgrade in place, not break inserts."""
    with Database(workspace) as conn:
        conn.execute(f"CREATE SCHEMA {workspace.agent_schema}")
        conn.execute(f"CREATE TABLE {workspace.agent_schema}.decision_log "
                     f"(alert_id VARCHAR, decision VARCHAR)")
        conn.execute(f"INSERT INTO {workspace.agent_schema}.decision_log VALUES ('old', 'DISMISS')")
        ledger.ensure_agent_schema(conn, workspace)
        alert = fetch_alerts(conn, workspace)[0]
        _decide(conn, workspace, alert)  # would fail on the old table without the upgrade
        rows = conn.query(f"SELECT alert_id, decision, evidence_hash "
                          f"FROM {workspace.agent_schema}.decision_log ORDER BY alert_id")
    assert [r["alert_id"] for r in rows] == sorted(["old", alert.alert_id])
    assert rows[[r["alert_id"] for r in rows].index("old")]["decision"] == "DISMISS"
