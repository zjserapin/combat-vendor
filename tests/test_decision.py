"""Block 6: the validator, routing policy, grounding warnings, and the final record."""

import json
from dataclasses import replace

import pytest

from src import ledger
from src.agent import investigate
from src.alert_queue import fetch_alerts
from src.db import Database
from src.decision import finalize, validate_decision
from src.evidence import build_evidence
from src.grounding import ungrounded_facts
from src.ledger import DECISIONS, REVIEW_REQUIRED
from src.tools import Toolbox
from tests.fakes import ScriptedLLM, decision, reply, text, tool_use

GOOD = {"decision": "ESCALATE", "confidence": 0.82, "rationale": "Pattern of sub-threshold cash.",
        "key_facts": ["Six deposits under $10,000"], "next_action": "Open SAR review."}


@pytest.fixture
def setup(workspace):
    with Database(workspace) as db:
        ledger.ensure_agent_schema(db, workspace)
        alert = fetch_alerts(db, workspace)[0]
        pack = build_evidence(db, workspace, alert)
        yield workspace, db, alert, pack


def _agent(setup, script, **overrides):
    config, db, alert, pack = setup
    config = replace(config, **overrides)
    result = investigate(alert, pack, ScriptedLLM(script), Toolbox(db, config), config)
    return finalize(result, alert, "run-test", config, pack.text), config


# --- the validator -------------------------------------------------------------------------

def test_valid_input_passes():
    check = validate_decision(GOOD)
    assert check.ok and check.decision == "ESCALATE" and check.confidence == 0.82


@pytest.mark.parametrize(("field", "value", "problem"), [
    ("decision", "SAR", "not one of"),
    ("decision", "escalate", "not one of"),
    ("decision", None, "not one of"),
    ("confidence", 1.7, "between 0 and 1"),
    ("confidence", -0.1, "between 0 and 1"),
    ("confidence", "0.8", "between 0 and 1"),
    ("confidence", True, "between 0 and 1"),
    ("confidence", float("nan"), "between 0 and 1"),
    ("rationale", "   ", "rationale is missing"),
    ("rationale", "x" * 6_000, "longer than"),
    ("key_facts", [], "key_facts"),
    ("key_facts", "one fact", "key_facts"),
    ("key_facts", ["ok", ""], "key_facts"),
    ("key_facts", ["f"] * 11, "more than"),
    ("next_action", "", "next_action is missing"),
])
def test_each_bad_field_is_named(field, value, problem):
    check = validate_decision({**GOOD, field: value})
    assert not check.ok and any(problem in p for p in check.problems)


def test_collects_every_problem_at_once():
    check = validate_decision({"decision": "MAYBE", "confidence": 9})
    assert len(check.problems) == 5


@pytest.mark.parametrize("junk", [None, [], "ESCALATE", 42, {}, {"decision": "DISMISS"}])
def test_junk_never_becomes_a_decision(junk):
    assert not validate_decision(junk).ok


# --- routing to a final disposition ---------------------------------------------------------

def test_clean_decision_is_recorded_as_is(setup):
    record, config = _agent(setup, [reply(decision("ESCALATE", 0.9))])
    _, _, alert, pack = setup
    assert record.decision == record.proposed_decision == "ESCALATE"
    assert record.review_reason is None and record.confidence == 0.9
    assert record.evidence_hash == pack.fingerprint and record.model_id == config.model_id
    assert json.loads(record.key_facts) == ["Note on file dated before the window."]
    assert record.alert_id == alert.alert_id


def test_invalid_decision_goes_to_a_human_but_keeps_the_reasoning(setup):
    record, _ = _agent(setup, [reply(decision("ESCALATE", 1.5))])
    assert record.decision == REVIEW_REQUIRED and record.proposed_decision == "ESCALATE"
    assert "validator: confidence 1.5" in record.review_reason
    assert record.rationale  # the human reviewer still sees what the model argued


def test_low_confidence_dismissal_goes_to_a_human(setup):
    record, _ = _agent(setup, [reply(decision("DISMISS", 0.45))])
    assert record.decision == REVIEW_REQUIRED and record.proposed_decision == "DISMISS"
    assert "below the 0.60 threshold" in record.review_reason


def test_threshold_is_configurable(setup):
    record, _ = _agent(setup, [reply(decision("DISMISS", 0.45))], dismiss_min_confidence=0.4)
    assert record.decision == "DISMISS"


def test_low_confidence_escalation_stands(setup):
    record, _ = _agent(setup, [reply(decision("ESCALATE", 0.3))])
    assert record.decision == "ESCALATE"


def test_agent_hand_off_carries_its_reason(setup):
    record, _ = _agent(setup, [reply(text("..."), stop="refusal")])
    assert record.decision == REVIEW_REQUIRED and "refusal" in record.review_reason
    assert record.proposed_decision is None and record.confidence is None


@pytest.mark.parametrize("bad", [
    decision("SAR"), decision(confidence="high"), decision(key_facts=[]), decision(rationale=""),
])
def test_final_disposition_is_always_in_the_vocabulary(setup, bad):
    record, _ = _agent(setup, [reply(bad)])
    assert record.decision in {*DECISIONS, REVIEW_REQUIRED}
    assert record.decision == REVIEW_REQUIRED


# --- grounding -------------------------------------------------------------------------------

def test_cited_facts_from_the_case_file_are_grounded(setup):
    _, _, alert, _pack = setup
    amount = f"${alert.flagged_cents / 100:,.2f}"
    record, _ = _agent(setup, [reply(decision(
        "ESCALATE", key_facts=[f"{amount} flagged starting {alert.window_start}"]))])
    assert json.loads(record.warnings) == []


def test_invented_facts_are_flagged_not_rejected(setup):
    record, _ = _agent(setup, [reply(decision(
        "ESCALATE", key_facts=["A $123,456.78 wire on 2026-01-02"]))])
    assert record.decision == "ESCALATE"
    assert json.loads(record.warnings) == ["amount $123,456.78 not found in evidence",
                                           "date 2026-01-02 not found in evidence"]


def test_facts_found_by_the_agents_own_lookups_count(setup):
    sql = {"query": "SELECT SUM(amount_cents) AS cents FROM bank.transactions "
                    "WHERE txn_id = 1", "purpose": "check one txn"}
    _config, db, *_ = setup
    cents = db.query("SELECT amount_cents FROM bank.transactions WHERE txn_id = 1")[0]["amount_cents"]
    cited = f"${cents / 100:,.2f}"
    record, _ = _agent(setup, [reply(tool_use("run_sql", **sql)),
                               reply(decision("ESCALATE", key_facts=[f"Txn 1 was {cited}"]))])
    assert json.loads(record.warnings) == []
    assert json.loads(record.tool_trace)[0]["purpose"] == "check one txn"


def test_grounding_rules():
    sources = ["| $2,150.58 | 2026-09-11 |", "| cents |\n| 4745064 |"]
    assert ungrounded_facts(["$2,150.58 on 2026-09-11", "$47,450.64", "about $2,150",
                             "roughly $54K"], sources) == []
    assert ungrounded_facts(["$45 fee"], ["| id |\n| 4512 |"]) == ["amount $45 not found in evidence"]


# --- end to end into the ledger -----------------------------------------------------------------

def test_record_lands_in_the_log_and_memory(setup):
    config, db, alert, pack = setup
    record, _ = _agent(setup, [reply(tool_use("describe_tables")),
                               reply(decision("KYC_REVIEW", 0.7))])
    ledger.record_decision(db, config, record)
    row = db.query(f"SELECT * FROM {config.agent_schema}.decision_log")[0]
    assert (row["decision"], row["proposed_decision"], row["steps"]) == ("KYC_REVIEW",
                                                                         "KYC_REVIEW", 2)
    assert row["evidence_hash"] == pack.fingerprint and row["prompt_version"] == "prompt_v1"
    assert json.loads(row["tool_trace"])[0]["tool"] == "describe_tables"
    memory = ledger.recall_memory(db, config, alert.customer_id)[0]
    assert "KYC_REVIEW (0.70)" in memory


def test_review_memory_line_explains_why(setup):
    config, db, alert, _ = setup
    record, _ = _agent(setup, [reply(decision("DISMISS", 0.3))])
    ledger.record_decision(db, config, record)
    memory = ledger.recall_memory(db, config, alert.customer_id)[0]
    assert "REVIEW_REQUIRED" in memory and "threshold" in memory
