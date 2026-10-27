"""Block 1: the synthetic bank is deterministic, realistic, and its labels line up."""

from collections import Counter

from src.db import Database
from src.synth.seed import build_bank
from src.synth.typologies import TYPOLOGIES, VARIANT_COUNTS


def test_same_seed_same_bank(config, bank):
    again = build_bank(config.seed, config.as_of_date)
    assert [t.amount_cents for t in again.transactions[:500]] == \
           [t.amount_cents for t in bank.transactions[:500]]
    assert again.alert_dispositions == bank.alert_dispositions


def test_one_label_per_planted_case(bank):
    per_typology = sum(VARIANT_COUNTS.values())
    assert len(bank.alert_dispositions) == len(TYPOLOGIES) * per_typology
    keys = {(d.customer_id, d.typology) for d in bank.alert_dispositions}
    assert len(keys) == len(bank.alert_dispositions), "each (customer, typology) is one alert"


def test_decision_mix_is_mostly_not_escalate(bank):
    mix = Counter(d.human_decision for d in bank.alert_dispositions)
    assert mix["DISMISS"] > mix["ESCALATE"] > mix["KYC_REVIEW"] > 0


def test_flags_only_exist_on_labelled_alerts(bank):
    owner = {a.account_id: a.customer_id for a in bank.accounts}
    flagged = {(owner[t.account_id], t.flag_reason) for t in bank.transactions if t.flag_reason}
    labelled = {(d.customer_id, d.typology) for d in bank.alert_dispositions}
    assert flagged == labelled


def test_label_windows_match_flagged_activity(bank):
    owner = {a.account_id: a.customer_id for a in bank.accounts}
    for d in bank.alert_dispositions:
        days = [t.txn_ts.date() for t in bank.transactions
                if t.flag_reason == d.typology and owner[t.account_id] == d.customer_id]
        assert (min(days), max(days)) == (d.window_start, d.window_end)


def test_nothing_happens_after_as_of(config, bank):
    assert max(t.txn_ts for t in bank.transactions).date() < config.as_of_date
    assert all(n.note_date <= config.as_of_date for n in bank.kyc_notes)
    assert all(d.decided_at.date() <= config.as_of_date for d in bank.alert_dispositions)


def test_structuring_deposits_sit_just_under_ctr_threshold(bank):
    amounts = [t.amount_cents for t in bank.transactions if t.flag_reason == "STRUCTURING"]
    assert amounts and all(650_000 <= a < 1_000_000 for a in amounts)


def test_every_benign_case_has_a_documented_explanation(bank):
    """A dismissal is only defensible if the reason is on file for the investigator to find."""
    noted = {n.customer_id for n in bank.kyc_notes}
    benign = [d for d in bank.alert_dispositions if d.human_decision == "DISMISS"]
    missing = [(d.customer_id, d.typology) for d in benign if d.customer_id not in noted]
    assert not missing


def test_geo_velocity_true_positives_end_in_a_block(bank):
    owner = {a.account_id: a.customer_id for a in bank.accounts}
    escalated = {d.customer_id for d in bank.alert_dispositions
                 if d.typology == "GEO_VELOCITY" and d.human_decision == "ESCALATE"}
    blocked = {owner[t.account_id] for t in bank.transactions if t.status == "BLOCKED"}
    assert escalated <= blocked


def test_decisions_come_after_the_alert_window(bank):
    assert all(d.decided_at.date() > d.window_end for d in bank.alert_dispositions)


def test_labels_live_outside_the_bank_schema(seeded):
    with Database(seeded, read_only=True) as db:
        tables = db.query("SELECT table_schema, table_name FROM information_schema.tables")
    by_schema = {(r["table_schema"], r["table_name"]) for r in tables}
    assert (seeded.labels_schema, "alert_dispositions") in by_schema
    assert (seeded.bank_schema, "alert_dispositions") not in by_schema


def test_money_column_is_documented_as_cents(seeded):
    with Database(seeded, read_only=True) as db:
        rows = db.query("SELECT comment FROM duckdb_columns() WHERE schema_name = ? AND "
                        "table_name = 'transactions' AND column_name = 'amount_cents'",
                        [seeded.bank_schema])
    assert "cents" in rows[0]["comment"].lower()


def test_risk_rating_alone_does_not_predict_the_decision(bank):
    """Guard against a proxy leak: one column shouldn't do the investigator's job."""
    risk = {c.customer_id: c.risk_rating for c in bank.customers}
    cases = [(risk[d.customer_id], d.human_decision == "ESCALATE")
             for d in bank.alert_dispositions if d.human_decision != "KYC_REVIEW"]
    best = max(sum((r >= t) == esc for r, esc in cases) / len(cases) for t in range(1, 101))
    assert best < 0.80, f"risk_rating threshold alone scores {best:.0%}"
