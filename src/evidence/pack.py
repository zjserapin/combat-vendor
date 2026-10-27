"""Assemble the evidence pack for one alert.

The harness — not the model — decides what evidence the model sees. Same alert
and same data in, byte-identical pack out; the fingerprint proves it.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from config import Config
from src.alert_queue import Alert
from src.db import Database, Row
from src.evidence import queries as q
from src.evidence.render import render

# Row caps per section keep the pack small and predictable.
FLAGGED_LIMIT = 25
WINDOW_LIMIT = 20
CONTEXT_LIMIT = 10
NOTES_LIMIT = 10
SARS_LIMIT = 5
FLOWS_DAYS = 30


@dataclass(frozen=True)
class EvidencePeriod:
    """All the date bounds for one pack. Dates are inclusive; see the *_ts helpers."""

    history_start: date  # profile + counterparty history start
    context_start: date  # largest-other-transactions start
    band_start: date  # window minus one day
    band_end: date  # window plus one day
    cutoff: date  # point-in-time limit: nothing after this day
    flows_start: date

    @classmethod
    def for_alert(cls, alert: Alert, config: Config) -> EvidencePeriod:
        cutoff = min(config.as_of_date, alert.window_end + timedelta(days=config.evidence_after_days))
        return cls(
            history_start=alert.window_start - timedelta(days=config.evidence_history_days),
            context_start=alert.window_start - timedelta(days=config.evidence_context_days),
            band_start=alert.window_start - timedelta(days=1),
            band_end=alert.window_end + timedelta(days=1),
            cutoff=cutoff,
            flows_start=cutoff - timedelta(days=FLOWS_DAYS - 1),
        )


@dataclass
class EvidencePack:
    """Everything the model is shown up front for one alert."""

    alert: Alert
    period: EvidencePeriod
    as_of: date
    customer: Row
    flagged: list[Row]
    window_activity: list[Row]
    context_activity: list[Row]
    monthly_profile: list[Row]
    counterparty_history: list[Row]
    country_history: list[Row]
    jurisdictions: list[Row]
    accounts: list[Row]
    flows_30d: list[Row]
    kyc_notes: list[Row]
    prior_sars: list[Row]
    text: str = field(default="", repr=False)

    @property
    def fingerprint(self) -> str:
        """Short hash of the rendered pack — stored with the decision as proof of input."""
        return hashlib.sha256(self.text.encode()).hexdigest()[:16]

    @property
    def approx_tokens(self) -> int:
        """Rough size (~4 characters per token). Good enough for budgeting."""
        return len(self.text) // 4


def build_evidence(db: Database, config: Config, alert: Alert) -> EvidencePack:
    """Run every evidence query for this alert and render the case file."""
    period = EvidencePeriod.for_alert(alert, config)
    sql = _Sql(config.bank_schema)
    cid = alert.customer_id
    window_start, window_end_excl = _ts(alert.window_start), _ts(alert.window_end, plus_days=1)
    band = (_ts(period.band_start), _ts(period.band_end, plus_days=1))
    cutoff_excl = _ts(period.cutoff, plus_days=1)

    customer = db.query(sql(q.CUSTOMER), [cid])[0]
    flagged = db.query(sql(q.FLAGGED, limit=FLAGGED_LIMIT),
                       [cid, alert.typology, window_start, window_end_excl])
    window_activity = db.query(sql(q.WINDOW_ACTIVITY, limit=WINDOW_LIMIT),
                               [cid, *band, alert.typology])
    context = db.query(sql(q.CONTEXT_ACTIVITY, limit=CONTEXT_LIMIT),
                       [cid, _ts(period.context_start), cutoff_excl, *band])
    profile = db.query(sql(q.MONTHLY_PROFILE), [cid, _ts(period.history_start), cutoff_excl])

    counterparties = sorted({r["counterparty"] for r in flagged})
    countries = _foreign_countries(flagged, customer["home_country"])
    history_bounds = [_ts(period.history_start), window_start]

    pack = EvidencePack(
        alert=alert,
        period=period,
        as_of=config.as_of_date,
        customer=customer,
        flagged=flagged,
        window_activity=sorted(window_activity, key=lambda r: (r["txn_ts"], r["txn_id"])),
        context_activity=sorted(context, key=lambda r: (r["txn_ts"], r["txn_id"])),
        monthly_profile=profile,
        counterparty_history=_with_zero_rows(
            _in_query(db, sql(q.COUNTERPARTY_HISTORY), [cid, *history_bounds], counterparties),
            "counterparty", counterparties),
        country_history=_with_zero_rows(
            _in_query(db, sql(q.COUNTRY_HISTORY),
                      [cid, customer["home_country"], *history_bounds], countries),
            "country", countries),
        jurisdictions=_in_query(db, sql(q.JURISDICTIONS), [], countries),
        accounts=db.query(sql(q.ACCOUNTS), [cid]),
        flows_30d=db.query(sql(q.FLOWS), [cid, _ts(period.flows_start), cutoff_excl]),
        kyc_notes=db.query(sql(q.KYC_NOTES, limit=NOTES_LIMIT), [cid, period.cutoff]),
        prior_sars=db.query(sql(q.PRIOR_SARS, limit=SARS_LIMIT), [cid, alert.window_start]),
    )
    pack.text = render(pack)
    return pack


class _Sql:
    """Fills {bank} and {limit} into a query template."""

    def __init__(self, bank: str) -> None:
        self.bank = bank

    def __call__(self, template: str, **extra: object) -> str:
        return template.replace("{bank}", self.bank).replace(
            "{limit}", str(extra.get("limit", ""))
        )


def _in_query(db: Database, template: str, params: list, values: list[str]) -> list[Row]:
    """Run a query with an `IN (...)` list; an empty list means no rows, not an error."""
    if not values:
        return []
    placeholders = ", ".join("?" for _ in values)
    return db.query(template.replace("{placeholders}", placeholders), [*params, *values])


def _with_zero_rows(rows: list[Row], key: str, wanted: list[str]) -> list[Row]:
    """Make "never seen before" explicit: a missing counterparty is itself evidence."""
    found = {r[key] for r in rows}
    zeros = [{key: v, "txn_count": 0, "total_cents": 0, "first_seen": None, "last_seen": None}
             for v in wanted if v not in found]
    return sorted([*rows, *zeros], key=lambda r: r[key])


def _foreign_countries(rows: list[Row], home: str) -> list[str]:
    countries = set()
    for r in rows:
        for country in (r["txn_country"], r["counterparty_country"]):
            if country and country != home:
                countries.add(country)
    return sorted(countries)


def _ts(day: date, plus_days: int = 0) -> datetime:
    return datetime.combine(day + timedelta(days=plus_days), time.min)
