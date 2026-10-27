"""Block 3: the evidence pack is complete, point-in-time, leak-free, and sufficient."""

from datetime import datetime, time, timedelta

import pytest

from src.alert_queue import fetch_alerts, get_alert
from src.db import Database
from src.evidence import build_evidence, queries
from src.evidence.render import as_date, as_datetime, money

MAX_TOKENS = 4_000


@pytest.fixture(scope="module")
def packs(seeded):
    """Every alert's pack, keyed by alert_id, plus the hidden human decision for checks."""
    with Database(seeded, read_only=True) as db:
        alerts = fetch_alerts(db, seeded, include_decided=True)
        labels = {r["alert_id"]: r["human_decision"] for r in db.query(
            f"SELECT alert_id, human_decision FROM {seeded.labels_schema}.alert_dispositions")}
        built = {a.alert_id: build_evidence(db, seeded, a) for a in alerts}
    return built, labels


def _cases(packs, typology, decision):
    built, labels = packs
    found = [p for aid, p in built.items()
             if p.alert.typology == typology and labels[aid] == decision]
    assert found, f"no {typology}/{decision} cases"
    return found


def _end_of(day):
    return datetime.combine(day + timedelta(days=1), time.min)


# --- completeness and stability ----------------------------------------------------

def test_every_alert_gets_a_compact_pack(packs):
    built, _ = packs
    assert len(built) == 60
    assert max(p.approx_tokens for p in built.values()) < MAX_TOKENS
    assert all("## 11. Prior SARs" in p.text for p in built.values())


def test_same_alert_same_pack(seeded, packs):
    built, _ = packs
    alert_id, original = next(iter(built.items()))
    with Database(seeded, read_only=True) as db:
        again = build_evidence(db, seeded, get_alert(db, seeded, alert_id))
    assert again.text == original.text and again.fingerprint == original.fingerprint


def test_flagged_section_matches_alert_header(packs):
    for pack in packs[0].values():
        assert len(pack.flagged) == pack.alert.flagged_txns
        assert sum(r["amount_cents"] for r in pack.flagged) == pack.alert.flagged_cents


# --- point-in-time -------------------------------------------------------------------

def test_cutoff_is_window_end_plus_lookahead_capped_at_as_of(seeded, packs):
    for pack in packs[0].values():
        expected = min(seeded.as_of_date,
                       pack.alert.window_end + timedelta(days=seeded.evidence_after_days))
        assert pack.period.cutoff == expected


def test_no_evidence_dated_after_the_cutoff(packs):
    for pack in packs[0].values():
        limit = _end_of(pack.period.cutoff)
        for row in pack.flagged + pack.window_activity + pack.context_activity:
            assert as_datetime(row["txn_ts"]) < limit
        assert all(as_date(n["note_date"]) <= pack.period.cutoff for n in pack.kyc_notes)
        assert all(as_date(m["month"]) <= pack.period.cutoff for m in pack.monthly_profile)


def test_only_sars_filed_before_the_window(packs):
    for pack in packs[0].values():
        assert all(as_date(s["filed_date"]) < pack.alert.window_start for s in pack.prior_sars)


def test_later_sar_and_later_note_stay_out(workspace):
    """Plant a SAR and a note dated after the cutoff; neither may appear."""
    with Database(workspace) as db:
        alert = min(fetch_alerts(db, workspace, include_decided=True),
                    key=lambda a: a.window_end)  # earliest window leaves room after the cutoff
        late = workspace.as_of_date
        db.execute(f"INSERT INTO {workspace.bank_schema}.sar_reports VALUES (?, ?, 'FILED', ?, "
                   f"'FUTURE SAR', 999)", [alert.customer_id, alert.typology, late])
        db.execute(f"INSERT INTO {workspace.bank_schema}.kyc_notes VALUES (?, ?, 'x', "
                   f"'FUTURE NOTE', 999)", [alert.customer_id, late])
        pack = build_evidence(db, workspace, alert)
    assert pack.period.cutoff < late
    assert "FUTURE SAR" not in pack.text and "FUTURE NOTE" not in pack.text


# --- leakage -----------------------------------------------------------------------------

def test_evidence_sql_never_touches_labels(seeded):
    sql = [v for k, v in vars(queries).items() if k.isupper() and isinstance(v, str)]
    assert len(sql) >= 11
    for statement in sql:
        assert f"{seeded.labels_schema}." not in statement and "dispositions" not in statement


def test_case_file_never_mentions_a_decision(packs):
    for pack in packs[0].values():
        for leak in ("ESCALATE", "KYC_REVIEW", "DISMISS", "human_decision", "analyst_"):
            assert leak not in pack.text, f"{pack.alert.alert_id} leaks {leak!r}"


# --- sufficiency: can the evidence support the right answer? -------------------------------

def test_every_dismissal_has_its_explanation_in_the_pack(packs):
    built, labels = packs
    for alert_id, pack in built.items():
        if labels[alert_id] == "DISMISS":
            assert pack.kyc_notes, f"{alert_id}: explanation missing from case file"


def test_structuring_history_separates_restaurant_from_smurf(packs):
    for pack in _cases(packs, "STRUCTURING", "DISMISS"):
        assert pack.counterparty_history[0]["txn_count"] >= 8  # months of regular cash
    for pack in _cases(packs, "STRUCTURING", "ESCALATE"):
        assert pack.counterparty_history[0]["txn_count"] == 0  # no cash history at all


def test_high_risk_supplier_history_is_visible(packs):
    for pack in _cases(packs, "HIGH_RISK_COUNTRY", "DISMISS"):
        assert all(r["txn_count"] >= 1 for r in pack.counterparty_history)
    for decision in ("ESCALATE", "KYC_REVIEW"):
        for pack in _cases(packs, "HIGH_RISK_COUNTRY", decision):
            assert all(r["txn_count"] == 0 for r in pack.counterparty_history)
            assert {j["risk_tier"] for j in pack.jurisdictions} == {"HIGH"}


def test_cash_out_shows_the_inbound_wire(packs):
    for pack in _cases(packs, "RAPID_CASH_OUT", "ESCALATE"):
        rows = pack.window_activity + pack.context_activity
        assert any(r["channel"] == "WIRE" and r["direction"] == "CREDIT" for r in rows)


def test_large_deposit_shows_the_wire_out_afterwards(packs):
    for pack in _cases(packs, "LARGE_CASH_DEPOSIT", "ESCALATE"):
        rows = pack.window_activity + pack.context_activity
        assert any(r["channel"] == "WIRE" and r["direction"] == "DEBIT" for r in rows)


def test_geo_velocity_shows_home_purchase_and_travel_history(packs):
    for pack in _cases(packs, "GEO_VELOCITY", "ESCALATE"):
        home = pack.customer["home_country"]
        assert any(r["txn_country"] == home and r["channel"] == "CARD"
                   for r in pack.window_activity)
        assert all(r["txn_count"] == 0 for r in pack.country_history)  # never been there
    for pack in _cases(packs, "GEO_VELOCITY", "KYC_REVIEW"):
        assert any(r["txn_count"] > 0 for r in pack.country_history)  # visited before


# --- formatting ----------------------------------------------------------------------------

def test_money_formatting():
    assert money(123456) == "$1,234.56"
    assert money(None) == "$0.00"
