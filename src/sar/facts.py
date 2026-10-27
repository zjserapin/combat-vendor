"""The SAR fact sheet: every fact a narrative may state, computed by SQL and arithmetic.

    facts = build_sar_facts(db, config, alert)

The harness does the counting and adding; the model only writes prose. That split
is what makes QC possible: every name, account, amount, count and date in a draft
can be checked against this sheet. Reads bank data and the agent's own decision
log — never labels.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from config import Config
from src import ledger
from src.alert_queue import Alert
from src.db import Database, Row
from src.evidence import EvidencePack, build_evidence
from src.evidence.render import as_datetime
from src.sar import queries as q

TXN_LIMIT = 500
BLOCKED = "BLOCKED"


@dataclass(frozen=True)
class Subtotal:
    """One group of the reported transactions (a counterparty, a day, an account...)."""

    dimension: str
    label: str
    completed: int
    completed_cents: int
    attempted: int
    attempted_cents: int

    @property
    def total(self) -> int:
        return self.completed + self.attempted

    @property
    def total_cents(self) -> int:
        return self.completed_cents + self.attempted_cents


@dataclass
class SarFacts:
    """Everything the drafter is allowed to state, plus the case file for context."""

    alert: Alert
    pack: EvidencePack
    subject: Row
    accounts: list[Row]  # accounts that carried the reported activity
    transactions: list[Row]  # flagged + blocked in the window, chronological
    triage: Row | None  # latest triage decision on this alert, if any
    truncated: bool = False
    text: str = field(default="", repr=False)  # sections A-F: checkable facts
    triage_text: str = field(default="", repr=False)  # section G: context, not evidence

    @property
    def completed(self) -> list[Row]:
        return [t for t in self.transactions if t["status"] != BLOCKED]

    @property
    def attempted(self) -> list[Row]:
        return [t for t in self.transactions if t["status"] == BLOCKED]

    @property
    def completed_cents(self) -> int:
        return sum(int(t["amount_cents"]) for t in self.completed)

    @property
    def attempted_cents(self) -> int:
        return sum(int(t["amount_cents"]) for t in self.attempted)

    @property
    def total_cents(self) -> int:
        return self.completed_cents + self.attempted_cents

    @property
    def first_date(self) -> date:
        return min((txn_day(t) for t in self.transactions), default=self.alert.window_start)

    @property
    def last_date(self) -> date:
        return max((txn_day(t) for t in self.transactions), default=self.alert.window_end)

    @property
    def known_ids(self) -> set[str]:
        """Identifiers a narrative may cite: the customer, their accounts, the transactions."""
        ids = {str(self.subject["customer_id"])}
        ids |= {str(a["account_id"]) for a in self.pack.accounts}
        ids |= {str(t["txn_id"]) for t in self.transactions}
        return ids

    @property
    def subtotals(self) -> list[Subtotal]:
        """Pre-computed groupings, so the model never has to add anything up."""
        return [*_group(self.transactions, "counterparty", lambda t: t["counterparty"]),
                *_group(self.transactions, "day", lambda t: txn_day(t).isoformat()),
                *_group(self.transactions, "account", lambda t: str(t["account_id"])),
                *_group(self.transactions, "channel",
                        lambda t: f"{t['channel']} {'IN' if t['direction'] == 'CREDIT' else 'OUT'}")]

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256((self.text + self.triage_text).encode()).hexdigest()[:16]

    @property
    def evidence_text(self) -> str:
        """What QC checks a narrative against: the fact sheet plus the triage case file."""
        return self.text + "\n" + self.pack.text


def build_sar_facts(db: Database, config: Config, alert: Alert) -> SarFacts:
    """Assemble the fact sheet for one alert (and its triage case file, for context).

    Call ledger.ensure_agent_schema first: the sheet reads the latest triage decision.
    """
    from src.sar.facts_render import render_facts, render_triage  # imports this module

    pack = build_evidence(db, config, alert)
    rows = db.query(
        q.SAR_TRANSACTIONS.replace("{bank}", config.bank_schema).replace("{limit}", str(TXN_LIMIT)),
        [alert.customer_id, alert.typology, _ts(alert.window_start), _ts(alert.window_end, 1)],
    )
    involved = {r["account_id"] for r in rows}
    facts = SarFacts(
        alert=alert, pack=pack, subject=pack.customer,
        accounts=[a for a in pack.accounts if a["account_id"] in involved],
        transactions=rows,
        triage=ledger.latest_decision(db, config, alert.alert_id),
        truncated=len(rows) >= TXN_LIMIT,
    )
    facts.text = render_facts(facts, config)
    facts.triage_text = render_triage(facts)
    return facts


def txn_day(row: Row) -> date:
    return as_datetime(row["txn_ts"]).date()


def _group(rows: list[Row], dimension: str, key: Callable[[Row], str]) -> list[Subtotal]:
    groups: dict[str, list[Row]] = defaultdict(list)
    for r in rows:
        groups[key(r)].append(r)
    out = []
    for label in sorted(groups):
        members = groups[label]
        done = [r for r in members if r["status"] != BLOCKED]
        tried = [r for r in members if r["status"] == BLOCKED]
        out.append(Subtotal(dimension, label, len(done), sum(int(r["amount_cents"]) for r in done),
                            len(tried), sum(int(r["amount_cents"]) for r in tried)))
    return out


def _ts(day: date, plus_days: int = 0) -> datetime:
    return datetime.combine(day + timedelta(days=plus_days), time.min)
