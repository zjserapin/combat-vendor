"""Block 8, end to end: scripted triage runs -> scorecard, checked against the labels."""

import re
from collections import Counter

import pytest

from src import ledger
from src.backtest import build_scorecard, render_scorecard
from src.backtest.data import has_decisions, load_cases
from src.backtest.split import DEV, HOLDOUT
from src.backtest.vendor import STANDIN, VENDOR_TABLE, attach_vendor
from src.db import Database
from src.tools import check_sql
from src.triage import run_triage
from tests.fakes import RuleLLM, decision, simple_rule


@pytest.fixture
def db(workspace):
    with Database(workspace) as conn:
        ledger.ensure_agent_schema(conn, workspace)
        yield conn


@pytest.fixture
def labels(db, workspace):
    rows = db.query(f"SELECT alert_id, human_decision FROM "
                    f"{workspace.labels_schema}.alert_dispositions")
    return {r["alert_id"]: r["human_decision"] for r in rows}


def _oracle(labels):
    """A 'perfect agent' for tests only: reads the alert id off the case file, answers the label."""
    def rule(case_file):
        alert_id = re.search(r"# CASE FILE: alert (\S+)", case_file).group(1)
        return decision(labels[alert_id], 0.9)
    return rule


# --- the labels ------------------------------------------------------------------------------------

def test_every_label_carries_an_analyst_rationale(db, workspace):
    missing = db.query(f"SELECT COUNT(*) AS n FROM {workspace.labels_schema}.alert_dispositions "
                       f"WHERE human_rationale IS NULL OR LENGTH(TRIM(human_rationale)) = 0")
    assert missing[0]["n"] == 0


# --- scores ----------------------------------------------------------------------------------------

def test_a_perfect_agent_scores_perfectly(db, workspace, labels):
    run_triage(db, workspace, RuleLLM(_oracle(labels)))
    card = build_scorecard(db, workspace)
    for name in (HOLDOUT, DEV):
        m = card.agent[name]
        assert m.agreement.value == 1.0 and m.escalation_recall.value == 1.0
        assert m.dismissal_precision.value == 1.0 and m.review_required.value == 0.0
        assert card.disagreements[name] == []


def test_escalate_everything_scores_one_third(db, workspace):
    """The evaluation plan's claim: half the queue is benign, so this strategy scores 33%."""
    run_triage(db, workspace, RuleLLM(lambda _: decision("ESCALATE", 0.9)))
    card = build_scorecard(db, workspace)
    total = card.agent[HOLDOUT].agreement.hits + card.agent[DEV].agreement.hits
    assert total / 60 == pytest.approx(1 / 3)
    assert card.agent[HOLDOUT].escalation_recall.value == 1.0  # recall alone is not enough
    assert card.agent[HOLDOUT].dismissal_precision.value is None


def test_scorecard_matches_a_direct_join(db, workspace):
    run_triage(db, workspace, RuleLLM(simple_rule))
    card = build_scorecard(db, workspace)
    joined = db.query(
        f"SELECT l.human_decision AS h, d.decision AS a "
        f"FROM {workspace.labels_schema}.alert_dispositions l "
        f"JOIN {workspace.agent_schema}.decision_log d ON d.alert_id = l.alert_id")
    agree = sum(r["h"] == r["a"] for r in joined)
    assert card.agent[HOLDOUT].agreement.hits + card.agent[DEV].agreement.hits == agree
    confusion = card.agent[HOLDOUT].confusion + card.agent[DEV].confusion
    assert confusion == Counter((r["h"], r["a"]) for r in joined)


def test_split_is_half_and_half_with_the_same_mix(db, workspace):
    cases = build_scorecard(db, workspace).cases
    for name in (HOLDOUT, DEV):
        part = [c for c in cases if c.slice == name]
        assert len(part) == 30
        assert Counter(c.human_decision for c in part) == {"ESCALATE": 10, "KYC_REVIEW": 5,
                                                           "DISMISS": 15}


# --- coverage, run filter, stability ---------------------------------------------------------------

def test_undecided_alerts_are_reported_not_scored(db, workspace):
    run_triage(db, workspace, RuleLLM(simple_rule), limit=5)
    card = build_scorecard(db, workspace)
    assert len(card.decided) == 5 and len(card.not_run) == 55
    assert card.agent[HOLDOUT].n + card.agent[DEV].n == 5
    assert "55 labelled alert(s) have no agent decision" in render_scorecard(card)


def test_run_filter_scores_one_run_only(db, workspace):
    first = run_triage(db, workspace, RuleLLM(simple_rule), limit=3)
    run_triage(db, workspace, RuleLLM(simple_rule), limit=4)
    card = build_scorecard(db, workspace, first.run_id)
    assert card.run_ids == [first.run_id] and len(card.decided) == 3


def test_latest_decision_wins_and_stability_is_measured(db, workspace, labels):
    run_triage(db, workspace, RuleLLM(simple_rule))
    run_triage(db, workspace, RuleLLM(simple_rule), include_decided=True)
    card = build_scorecard(db, workspace)
    assert card.stability[HOLDOUT].value == 1.0 and card.stability[HOLDOUT].total == 30

    run_triage(db, workspace, RuleLLM(_oracle(labels)), include_decided=True)
    card = build_scorecard(db, workspace)
    assert card.agent[HOLDOUT].agreement.value == 1.0  # latest run is the one scored
    assert card.stability[HOLDOUT].value < 1.0  # the oracle disagreed with earlier runs


def test_no_decisions_is_detected(db, workspace):
    with Database(workspace) as fresh:
        assert not has_decisions(fresh, workspace)  # no agent schema at all
    assert not has_decisions(db, workspace)  # schema exists, log empty
    run_triage(db, workspace, RuleLLM(simple_rule), limit=1)
    assert has_decisions(db, workspace)


# --- vendor column -----------------------------------------------------------------------------------

def test_standin_vendor_is_labelled_everywhere(db, workspace):
    run_triage(db, workspace, RuleLLM(simple_rule))
    card = build_scorecard(db, workspace)
    text = render_scorecard(card)
    assert card.vendor.source == STANDIN and card.vendor.scored == 60
    assert "Vendor (STAND-IN)" in text and "STAND-IN, not a vendor" in text


def test_vendor_thresholds_never_see_holdout_labels(db, workspace):
    cases = build_scorecard(db, workspace).cases  # slices assigned
    before = attach_vendor(db, workspace, cases)
    for c in cases:
        if c.slice == HOLDOUT:
            c.human_decision = "ESCALATE" if c.human_decision == "DISMISS" else "DISMISS"
    after = attach_vendor(db, workspace, cases)
    assert (after.escalate_at, after.review_at) == (before.escalate_at, before.review_at)


def test_real_vendor_scores_replace_the_standin(db, workspace):
    labelled = load_cases(db, workspace)
    db.execute(f"CREATE TABLE {workspace.labels_schema}.vendor_scores "
               f"(alert_id VARCHAR, vendor_score DOUBLE)")
    for c in labelled:
        score = {"ESCALATE": 0.9, "KYC_REVIEW": 0.5, "DISMISS": 0.1}[c.human_decision]
        db.execute(f"INSERT INTO {workspace.labels_schema}.vendor_scores VALUES (?, ?)",
                   [c.alert_id, score])
    card = build_scorecard(db, workspace)
    assert card.vendor.source == VENDOR_TABLE and not card.vendor.is_standin
    assert card.vendor_metrics[HOLDOUT].agreement.value == 1.0
    assert "STAND-IN" not in render_scorecard(card)


def test_agent_cannot_query_the_vendor_scores():
    assert not check_sql("SELECT * FROM labels.vendor_scores", "bank").ok


# --- the rendered scorecard ---------------------------------------------------------------------------

def test_scorecard_is_reproducible_and_complete(db, workspace):
    run_triage(db, workspace, RuleLLM(simple_rule))
    first = render_scorecard(build_scorecard(db, workspace))
    assert first == render_scorecard(build_scorecard(db, workspace))
    assert "SYNTHETIC DATA" in first
    for heading in ("## Headline — holdout", "## Confusion matrices", "## Agreement by typology",
                    "## Cost and speed", "## Disagreements for SME review", "## Limits"):
        assert heading in first


def test_disagreements_show_both_rationales(db, workspace):
    run_triage(db, workspace, RuleLLM(simple_rule))
    card = build_scorecard(db, workspace)
    text = render_scorecard(card)
    found = card.disagreements[HOLDOUT]
    assert found, "simple_rule should disagree with analysts somewhere"
    case = found[0].case
    assert case.human_rationale in text and f"`{case.alert_id}`" in text
    assert "do NOT use these to tune prompts" in text
