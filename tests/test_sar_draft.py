"""Block 11: fact sheet, drafter, and the draft -> QC -> revise pipeline. No network."""

import inspect
import json
import pkgutil
from dataclasses import replace
from importlib import import_module

import pytest

import src.sar
from src import ledger
from src.alert_queue import fetch_alerts
from src.db import Database
from src.ledger import DecisionRecord
from src.sar import NotEscalated, build_sar_facts, prepare_sar
from src.sar.drafter import draft_narrative, parse_draft
from src.sar.findings import error
from src.sar.records import NEEDS_REVISION, NO_DRAFT, READY_FOR_REVIEW
from src.sar.schemas import SECTIONS, qc_review_tool, sar_narrative_tool
from tests.fakes import ScriptedLLM, reply
from tests.sar_helpers import clean_sections, narrative_call, review_call


@pytest.fixture
def case(workspace):
    with Database(workspace) as db:
        ledger.ensure_agent_schema(db, workspace)
        alert = next(a for a in fetch_alerts(db, workspace) if a.blocked_txns)
        yield workspace, db, alert


def _escalate(db, config, alert, decision="ESCALATE"):
    ledger.record_decision(db, config, DecisionRecord.for_alert(
        alert, "run-test", decision=decision, confidence=0.9,
        rationale="Card used in two countries hours apart.", next_action="Open SAR review.",
        key_facts=json.dumps(["Purchases abroad the same afternoon."])))


# --- the fact sheet ------------------------------------------------------------------------

def test_fact_sheet_matches_the_alert_contract_for_every_alert(module_bank):
    with Database(module_bank) as db:
        ledger.ensure_agent_schema(db, module_bank)
        for alert in fetch_alerts(db, module_bank):
            facts = build_sar_facts(db, module_bank, alert)
            assert len(facts.transactions) == alert.flagged_txns
            assert facts.total_cents == alert.flagged_cents
            assert len(facts.attempted) == alert.blocked_txns
            assert {t["account_id"] for t in facts.transactions} == \
                {a["account_id"] for a in facts.accounts}


def test_fact_sheet_is_deterministic_and_label_free(case):
    config, db, alert = case
    a, b = build_sar_facts(db, config, alert), build_sar_facts(db, config, alert)
    assert a.text == b.text and a.fingerprint == b.fingerprint
    for word in ("human_decision", "analyst_id", "human_rationale", "DISMISS", "KYC_REVIEW"):
        assert word not in a.text


def test_triage_rationale_is_context_not_evidence(case):
    config, db, alert = case
    _escalate(db, config, alert)
    facts = build_sar_facts(db, config, alert)
    assert "Card used in two countries" in facts.triage_text
    assert "Card used in two countries" not in facts.evidence_text


def test_sar_code_never_reads_labels():
    for mod in pkgutil.iter_modules(src.sar.__path__):
        source = inspect.getsource(import_module(f"src.sar.{mod.name}"))
        assert "labels_schema" not in source and "alert_dispositions" not in source, mod.name


# --- tool schemas --------------------------------------------------------------------------

def _assert_strict(schema):
    if schema.get("type") == "object":
        assert schema["additionalProperties"] is False
        assert set(schema["required"]) == set(schema["properties"])
        for child in schema["properties"].values():
            _assert_strict(child)
    if schema.get("type") == "array":
        _assert_strict(schema["items"])


@pytest.mark.parametrize("make", [sar_narrative_tool, qc_review_tool])
def test_tools_are_strict_and_complete(module_bank, make):
    tool = make(module_bank)
    assert tool["strict"] is True
    _assert_strict(tool["input_schema"])
    assert "strict" not in make(replace(module_bank, tools_strict=False))


# --- the drafter ---------------------------------------------------------------------------

def test_parse_assembles_sections_in_order_with_fixed_closing(case):
    config, db, alert = case
    sections = clean_sections(build_sar_facts(db, config, alert), config)
    draft = parse_draft({**sections, "cited_txn_ids": [1], "drafting_notes": " none "}, "CLOSE.")
    assert draft.problems == [] and draft.usable
    assert draft.narrative.split("\n\n") == [*(sections[n] for n, _ in SECTIONS), "CLOSE."]
    assert draft.drafting_notes == "none"


def test_stub_sections_are_problems_not_silent():
    draft = parse_draft({"introduction": "x" * 60, "subject": "", "activity": "short",
                         "basis_for_suspicion": None, "investigation": "y" * 60,
                         "cited_txn_ids": ["a"], "drafting_notes": ""}, "CLOSE.")
    assert len(draft.problems) == 4 and draft.usable  # still QC-able, and revisable


def test_drafter_sends_fact_sheet_and_one_tool(case):
    config, db, alert = case
    facts = build_sar_facts(db, config, alert)
    llm = ScriptedLLM([reply(narrative_call(clean_sections(facts, config)))])
    draft = draft_narrative(facts, llm, config)
    request = llm.requests[0]
    assert draft.usable and not draft.problems
    assert request["tools"] == ["submit_sar_narrative"]
    assert facts.text in request["messages"][0]["content"]
    assert config.bank_name in request["system"]
    assert draft.input_tokens == 1_000


@pytest.mark.parametrize(("script", "problem"), [
    ([reply(), reply()], "without calling"),
    ([reply(stop="refusal")], "refusal"),
    ([reply(stop="max_tokens")], "cut off"),
])
def test_drafter_failures_give_no_draft(case, script, problem):
    config, db, alert = case
    draft = draft_narrative(build_sar_facts(db, config, alert), ScriptedLLM(script), config)
    assert not draft.usable and problem in draft.problems[0]


def test_revision_prompt_carries_findings_and_previous_draft(case):
    config, db, alert = case
    facts = build_sar_facts(db, config, alert)
    first = parse_draft({**clean_sections(facts, config), "cited_txn_ids": [],
                         "drafting_notes": ""}, "CLOSE.")
    llm = ScriptedLLM([reply(narrative_call(clean_sections(facts, config)))])
    draft_narrative(facts, llm, config, previous=first,
                    findings=[error("misspelling", "mechanics", "'recieved' should be "
                                                                "'received'", "recieved")])
    prompt = llm.requests[0]["messages"][0]["content"]
    assert "1. [ERROR] 'recieved' should be 'received'" in prompt
    assert first.narrative in prompt


# --- the pipeline --------------------------------------------------------------------------

def test_not_escalated_is_refused_unless_forced(case):
    config, db, alert = case
    with pytest.raises(NotEscalated):
        prepare_sar(db, config, alert, ScriptedLLM([]))
    _escalate(db, config, alert, decision="DISMISS")
    with pytest.raises(NotEscalated):
        prepare_sar(db, config, alert, ScriptedLLM([]))


def test_qc_error_triggers_one_revision_then_ready(case):
    config, db, alert = case
    _escalate(db, config, alert)
    facts = build_sar_facts(db, config, alert)
    good = clean_sections(facts, config)
    typo = {**good, "investigation": good["investigation"].replace("reviewed", "recieved")}
    llm = ScriptedLLM([reply(narrative_call(typo)), reply(review_call()),
                       reply(narrative_call(good)), reply(review_call())])

    outcome = prepare_sar(db, config, alert, llm)

    assert [a.record.status for a in outcome.attempts] == [NEEDS_REVISION, READY_FOR_REVIEW]
    assert "recieved" in llm.requests[2]["messages"][0]["content"]  # the finding went back
    rows = db.query(f"SELECT attempt, status, rule_errors, triage_run_id "
                    f"FROM {config.agent_schema}.sar_drafts ORDER BY attempt")
    assert [(r["attempt"], r["status"]) for r in rows] == [(1, NEEDS_REVISION),
                                                            (2, READY_FOR_REVIEW)]
    assert rows[0]["rule_errors"] >= 1 and rows[0]["triage_run_id"] == "run-test"
    checks = db.query(f"SELECT check_name FROM {config.agent_schema}.sar_qc_findings "
                      f"WHERE attempt = 1 AND severity = 'ERROR'")
    assert {"misspelling"} <= {r["check_name"] for r in checks}


def test_revision_budget_is_respected(case):
    config, db, alert = case
    _escalate(db, config, alert)
    config = replace(config, sar_max_revisions=0)
    good = clean_sections(build_sar_facts(db, config, alert), config)
    bad = {**good, "investigation": good["investigation"] + " [TBD]"}
    llm = ScriptedLLM([reply(narrative_call(bad)), reply(review_call())])
    outcome = prepare_sar(db, config, alert, llm)
    assert len(outcome.attempts) == 1 and outcome.final.record.status == NEEDS_REVISION


def test_no_draft_skips_qc_and_is_logged(case):
    config, db, alert = case
    _escalate(db, config, alert)
    llm = ScriptedLLM([reply(stop="refusal")])
    outcome = prepare_sar(db, config, alert, llm)
    assert outcome.final.record.status == NO_DRAFT and outcome.final.qc is None
    assert len(llm.requests) == 1
    rows = db.query(f"SELECT status, draft_problems FROM {config.agent_schema}.sar_drafts")
    assert rows[0]["status"] == NO_DRAFT and "refusal" in rows[0]["draft_problems"]


def test_pipeline_writes_only_agent_tables(case):
    config, db, alert = case
    _escalate(db, config, alert)
    before = db.query(f"SELECT COUNT(*) AS n FROM {config.bank_schema}.transactions")[0]["n"]
    good = clean_sections(build_sar_facts(db, config, alert), config)
    prepare_sar(db, config, alert, ScriptedLLM([reply(narrative_call(good)),
                                                reply(review_call())]))
    assert db.query(f"SELECT COUNT(*) AS n FROM {config.bank_schema}.transactions")[0]["n"] \
        == before
    tables = {r["table_name"] for r in db.query(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = ?",
        [config.agent_schema])}
    assert {"sar_drafts", "sar_qc_findings"} <= tables
