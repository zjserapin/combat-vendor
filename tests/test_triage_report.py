"""Block 7: the triage run (failure isolation, early stop) and the impact board."""

import inspect
from dataclasses import replace

import pytest

from src import ledger
from src.alert_queue import fetch_alerts
from src.db import Database
from src.report import compute_board, latest_run_id, render_board
from src.report import metrics as metrics_module
from src.triage import estimate_cost, run_triage
from tests.fakes import RuleLLM, simple_rule


@pytest.fixture
def db(workspace):
    with Database(workspace) as conn:
        ledger.ensure_agent_schema(conn, workspace)
        yield conn


def _count(db, config, table, where="1=1", params=()):
    return db.query(f"SELECT COUNT(*) AS n FROM {config.agent_schema}.{table} WHERE {where}",
                    list(params))[0]["n"]


# --- the run ------------------------------------------------------------------------------------

def test_full_run_works_the_whole_queue(db, workspace):
    seen = []
    summary = run_triage(db, workspace, RuleLLM(simple_rule),
                         on_alert=lambda i, n, outcome: seen.append(i))
    s = summary.stats
    assert s.alerts_attempted == 60 and s.alerts_failed == 0 and not summary.stopped_early
    assert s.alerts_decided + s.alerts_review_required == 60
    assert s.model_calls == 60 and seen == list(range(1, 61))
    assert _count(db, workspace, "decision_log", "run_id = ?", [summary.run_id]) == 60
    run = db.query(f"SELECT * FROM {workspace.agent_schema}.run_log WHERE run_id = ?",
                   [summary.run_id])[0]
    assert run["finished_at"] is not None and run["alerts_attempted"] == 60


def test_second_run_finds_nothing_new(db, workspace):
    run_triage(db, workspace, RuleLLM(simple_rule))
    assert run_triage(db, workspace, RuleLLM(simple_rule)).stats.alerts_attempted == 0


def test_limit_caps_the_run(db, workspace):
    assert run_triage(db, workspace, RuleLLM(simple_rule), limit=5).stats.alerts_attempted == 5


def test_one_failure_does_not_stop_the_run(db, workspace):
    alerts = fetch_alerts(db, workspace)
    bad = (alerts[1].alert_id, alerts[4].alert_id)  # not consecutive
    summary = run_triage(db, workspace, RuleLLM(simple_rule, fail_on=bad))
    assert summary.stats.alerts_failed == 2 and summary.stats.alerts_attempted == 60
    failed = [o for o in summary.outcomes if o.error]
    assert {o.alert.alert_id for o in failed} == set(bad)
    assert all("unreachable" in o.error for o in failed)


def test_failed_alerts_stay_in_the_queue_and_out_of_the_log(db, workspace):
    first = fetch_alerts(db, workspace)[0]
    run_triage(db, workspace, RuleLLM(simple_rule, fail_on=(first.alert_id,)))
    assert _count(db, workspace, "decision_log", "alert_id = ?", [first.alert_id]) == 0
    assert [a.alert_id for a in fetch_alerts(db, workspace)] == [first.alert_id]


def test_repeated_failures_stop_the_run_early(db, workspace):
    summary = run_triage(db, workspace, RuleLLM(simple_rule, fail_on=("CASE FILE",)))
    assert summary.stopped_early
    assert summary.stats.alerts_attempted == workspace.triage_max_consecutive_failures
    assert _count(db, workspace, "decision_log") == 0


# --- the impact board -----------------------------------------------------------------------------

def test_board_matches_the_logs(db, workspace):
    summary = run_triage(db, workspace, RuleLLM(simple_rule))
    board = compute_board(db, workspace, summary.run_id)
    logged = {r["decision"]: r["n"] for r in db.query(
        f"SELECT decision, COUNT(*) AS n FROM {workspace.agent_schema}.decision_log "
        f"GROUP BY decision")}
    assert dict(board.decisions) == logged and board.decided == 60
    expected_cost = (board.input_tokens * workspace.input_usd_per_mtok
                     + board.output_tokens * workspace.output_usd_per_mtok) / 1_000_000
    assert board.cost_usd == pytest.approx(expected_cost) and board.cost_usd > 0


def test_analyst_time_is_measured_from_recorded_handle_time(db, workspace):
    summary = run_triage(db, workspace, RuleLLM(simple_rule))
    board = compute_board(db, workspace, summary.run_id)
    dismissed_minutes = db.query(
        f"SELECT SUM(l.handle_minutes) AS m FROM {workspace.labels_schema}.alert_dispositions l "
        f"JOIN {workspace.agent_schema}.decision_log d ON d.alert_id = l.alert_id "
        f"WHERE d.decision = 'DISMISS'")[0]["m"]
    assert board.handle_time_source == "measured"
    assert board.handle_minutes_dismissed == pytest.approx(dismissed_minutes)
    assert board.hours_returned == pytest.approx(dismissed_minutes * 0.9 / 60)


def test_missing_handle_time_falls_back_to_the_labelled_assumption(db, workspace):
    summary = run_triage(db, workspace, RuleLLM(simple_rule), limit=4)
    no_labels = replace(workspace, labels_schema="no_such_schema")
    board = compute_board(db, no_labels, summary.run_id)
    assert board.handle_time_source == "ASSUMPTION"
    assert board.handle_minutes_all == pytest.approx(4 * workspace.manual_minutes_per_alert)
    assert "[ASSUMPTION]" in render_board(board, workspace.bank_name)


def test_board_tags_every_kind_of_number_and_claims_no_accuracy(db, workspace):
    summary = run_triage(db, workspace, RuleLLM(simple_rule))
    text = render_board(compute_board(db, workspace, summary.run_id), workspace.bank_name)
    assert "[measured]" in text and "[policy]" in text
    assert "Accuracy against human decisions is measured separately" in text
    assert "count only if the backtest shows" in text
    assert "What the agent did not do" in text


def test_board_never_reads_the_human_decision():
    assert "human_decision" not in inspect.getsource(metrics_module)


def test_latest_run_is_the_default(db, workspace):
    run_triage(db, workspace, RuleLLM(simple_rule), limit=2)
    second = run_triage(db, workspace, RuleLLM(simple_rule), limit=2)
    assert latest_run_id(db, workspace) == second.run_id


def test_cost_estimate_scales_and_brackets(workspace):
    low, high = estimate_cost(workspace, 60)
    assert 0 < low < high
    assert estimate_cost(workspace, 0) == (0.0, 0.0)
    assert estimate_cost(workspace, 120)[0] == pytest.approx(2 * low, rel=0.01)
