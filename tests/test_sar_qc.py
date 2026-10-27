"""Block 12: SAR QC — no false alarms on clean narratives, and every planted kickback caught."""

from dataclasses import replace
from datetime import timedelta

import pytest

from src import ledger
from src.alert_queue import fetch_alerts
from src.db import Database
from src.evidence.render import money
from src.sar import build_sar_facts, run_qc
from src.sar.findings import ERROR, WARNING
from src.sar.qc_review import parse_review
from src.sar.records import NEEDS_REVISION, QC_INCOMPLETE, READY_FOR_REVIEW
from tests.fakes import ScriptedLLM, reply
from tests.sar_helpers import clean_narrative, review_call


@pytest.fixture(scope="module")
def all_facts(module_bank):
    with Database(module_bank) as db:
        ledger.ensure_agent_schema(db, module_bank)
        return [build_sar_facts(db, module_bank, a) for a in fetch_alerts(db, module_bank, limit=None)]


@pytest.fixture(scope="module")
def case(all_facts, module_bank):
    """An alert with completed and blocked activity abroad — the richest case to mutate."""
    facts = next(f for f in all_facts if f.attempted and len(f.completed) >= 2)
    return facts, clean_narrative(facts, module_bank)


def _checks(qc, severity=None):
    return {f.check for f in qc.findings if severity is None or f.severity == severity}


# --- no false alarms ---------------------------------------------------------------------

def test_clean_narratives_pass_for_every_alert(all_facts, module_bank):
    assert len(all_facts) == 60
    for facts in all_facts:
        qc = run_qc(clean_narrative(facts, module_bank), facts, module_bank)
        assert qc.errors == [], (facts.alert.alert_id, qc.errors)
        assert qc.status == READY_FOR_REVIEW


# --- every planted kickback is caught ----------------------------------------------------

def _rename(f, n):
    last = f.subject["full_name"].split()[-1]
    swapped = last[:-2] + last[-1] + last[-2]  # transpose the last two letters
    return n.replace(last, swapped)


def _wrong_amount(f, n):
    amount = money(f.transactions[0]["amount_cents"])
    return n.replace(amount, money(int(f.transactions[0]["amount_cents"]) + 100), 1)


def _wrong_date(f, n):
    return n.replace(str(f.first_date), str(f.first_date - timedelta(days=400)))


def _transposed_account(f, n):
    acct = str(f.accounts[0]["account_id"])
    return n.replace(f"account {acct}", f"account {acct[::-1]}")


def _no_total(f, n):
    for cents in (f.total_cents, f.completed_cents):
        n = n.replace(f"totalling {money(cents)}", "totalling an amount")
    return n


PLANTED = [
    ("misspelled subject name", _rename, "name_spelling"),
    ("misspelled subject name", _rename, "subject_name"),
    ("amount off by a dollar", _wrong_amount, "ungrounded_fact"),
    ("activity date wrong everywhere", _wrong_date, "dates_stated"),
    ("transposed account number", _transposed_account, "unknown_identifier"),
    ("total amount missing", _no_total, "total_stated"),
    ("wrong transaction count", lambda f, n: n + " The customer made 97 cash deposits.",
     "transaction_count"),
    ("date written in US format, wrong", lambda f, n: n + " Activity resumed on 13/45/2026.",
     "invalid_date"),
    ("placeholder left in", lambda f, n: n.replace("The Bank reviewed", "[TBD] The Bank reviewed"),
     "placeholder"),
    ("legal conclusion", lambda f, n: n + " The customer laundered the proceeds.",
     "legal_conclusion"),
    ("doubled word", lambda f, n: n.replace("The Bank reviewed", "The Bank reviewed the the"),
     "doubled_word"),
    ("common misspelling", lambda f, n: n.replace("reviewed", "recieved and reviewed"),
     "misspelling"),
    ("internal reference", lambda f, n: n + " See the case file for details.",
     "internal_reference"),
    ("AI reference", lambda f, n: n + " The model reviewed this account.", "internal_reference"),
]


@pytest.mark.parametrize(("label", "plant", "check"), PLANTED, ids=[p[0] for p in PLANTED])
def test_planted_kickback_is_an_error(case, module_bank, label, plant, check):
    facts, clean = case
    qc = run_qc(plant(facts, clean), facts, module_bank)
    assert check in _checks(qc, ERROR), (label, qc.findings)
    assert qc.status == NEEDS_REVISION


@pytest.mark.parametrize(("addition", "check"), [
    (" The customer probably knew.", "speculation"),
    (" I reviewed the account.", "first_person"),
    (" Several large amounts moved.", "vague_quantity"),
    (" The alert 10001-GEO_VELOCITY-2026-09-11 fired.", "internal_reference"),
    (" See attached spreadsheet.", "attachment_reference"),
    (" the customer did not respond.", "capitalization"),
    (" A fee of $1000 applied.", "amount_format"),
])
def test_style_problems_are_warnings(case, module_bank, addition, check):
    facts, clean = case
    qc = run_qc(clean + addition, facts, module_bank)
    assert check in _checks(qc, WARNING), qc.findings


def test_length_limits(case, module_bank):
    facts, clean = case
    assert "length" in _checks(run_qc(clean, facts, replace(module_bank, sar_narrative_max_chars=100)),
                               ERROR)
    assert "length" in _checks(run_qc(clean, facts, replace(module_bank, sar_narrative_min_chars=10**5)),
                               WARNING)


def test_cited_transactions_must_be_reported(case, module_bank):
    facts, clean = case
    good = [int(t["txn_id"]) for t in facts.transactions]
    assert "cited_txn" not in _checks(run_qc(clean, facts, module_bank, cited_txn_ids=good))
    assert "cited_txn" in _checks(run_qc(clean, facts, module_bank, cited_txn_ids=[999_999_999]), ERROR)


def test_findings_are_deterministic(case, module_bank):
    facts, clean = case
    dirty = _rename(facts, clean) + " The customer laundered the proceeds."
    assert run_qc(dirty, facts, module_bank).findings == run_qc(dirty, facts, module_bank).findings


# --- the model reviewer ------------------------------------------------------------------

def test_model_review_adds_findings(case, module_bank):
    facts, clean = case
    excerpt = clean.split(".")[0]
    llm = ScriptedLLM([reply(review_call([{
        "severity": "ERROR", "category": "clarity", "excerpt": excerpt,
        "problem": "Opening does not say how the activity was detected.", "fix": "Add it."}],
        verdict="REVISE"))])
    qc = run_qc(clean, facts, module_bank, llm=llm)
    assert qc.status == NEEDS_REVISION
    assert [f.source for f in qc.errors] == ["model"]
    assert llm.requests[0]["tools"] == ["submit_qc_review"]
    assert "NARRATIVE UNDER REVIEW" in llm.requests[0]["messages"][0]["content"]


def test_model_pass_with_clean_rules_is_ready(case, module_bank):
    facts, clean = case
    qc = run_qc(clean, facts, module_bank, llm=ScriptedLLM([reply(review_call())]))
    assert qc.status == READY_FOR_REVIEW and qc.review.scores["accuracy"] == 4


def test_misquoting_reviewer_is_downgraded():
    review = parse_review({"findings": [{"severity": "ERROR", "category": "accuracy",
                                         "excerpt": "words that are not there",
                                         "problem": "Wrong.", "fix": ""}],
                           "scores": {"accuracy": 3, "completeness": 3, "clarity": 3,
                                      "objectivity": 3},
                           "verdict": "REVISE", "summary": ""}, "The real narrative.")
    assert review.findings[0].severity == WARNING
    assert "not found in the narrative" in review.findings[0].message


@pytest.mark.parametrize("raw", [
    {"findings": [], "scores": {"accuracy": 9}, "verdict": "PASS", "summary": ""},
    {"findings": [], "scores": {}, "verdict": "MAYBE", "summary": ""},
    {"findings": [{"severity": "FATAL"}], "scores": {}, "verdict": "PASS", "summary": ""},
])
def test_malformed_review_is_incomplete_not_a_pass(raw):
    assert not parse_review(raw, "narrative").completed


def test_reviewer_that_never_answers_means_qc_incomplete(case, module_bank):
    facts, clean = case
    llm = ScriptedLLM([reply(), reply()])  # two plain-text turns, no tool call
    qc = run_qc(clean, facts, module_bank, llm=llm)
    assert qc.status == QC_INCOMPLETE and len(llm.requests) == 2
    assert "Call it now" in llm.requests[1]["messages"][-1]["content"]


def test_rule_errors_outrank_an_incomplete_review(case, module_bank):
    facts, clean = case
    qc = run_qc(clean + " [TBD]", facts, module_bank, llm=ScriptedLLM([reply(stop="refusal")]))
    assert qc.status == NEEDS_REVISION
