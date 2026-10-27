"""Render a SarFacts as the fact sheet text. Deterministic, like the case file."""

from __future__ import annotations

import json

from config import Config
from src.evidence.render import Column, as_date, as_datetime, money, table
from src.sar.facts import BLOCKED, SarFacts, Subtotal

# Plain-English behaviour, no rule codes: a narrative describes activity, not alert logic.
ACTIVITY_DESCRIPTIONS = {
    "STRUCTURING": "cash deposits kept below the $10,000 currency transaction reporting "
                   "threshold, repeated over a short period",
    "GEO_VELOCITY": "card transactions in places too far apart, too close together in time, "
                    "for the cardholder to have travelled between them",
    "RAPID_CASH_OUT": "a large incoming credit withdrawn in cash within about 48 hours",
    "HIGH_RISK_COUNTRY": "funds sent to a higher-risk jurisdiction, shortly after incoming "
                         "credits",
    "LARGE_CASH_DEPOSIT": "a single cash deposit above $50,000",
}


def render_facts(facts: SarFacts, config: Config) -> str:
    """Sections A-F: the checkable facts. Section G (triage) is rendered separately."""
    sections = [
        f"# SAR FACT SHEET: alert {facts.alert.alert_id}",
        (f"Facts a narrative may state, computed from {config.bank_name} records as of "
         f"{facts.pack.as_of}. Amounts in USD; dates YYYY-MM-DD."),
        _subject(facts),
        f"## B. Accounts involved\n{table(facts.accounts, _ACCOUNT_COLUMNS)}",
        _summary(facts),
        f"## D. Transactions (chronological)\n{table(facts.transactions, _txn_columns(facts))}",
        _subtotals(facts.subtotals),
        _prior_sars(facts),
    ]
    return "\n\n".join(sections) + "\n"


def location(city: str, country: str | None, home: str) -> str:
    return city if not country or country == home else f"{city} ({country})"


def outcome(row: dict) -> str:
    return "attempted, blocked" if row["status"] == BLOCKED else "completed"


# --- sections ------------------------------------------------------------------------

def _subject(facts: SarFacts) -> str:
    s = facts.subject
    return "\n".join([
        "## A. Subject",
        f"- Full name as on record: {s['full_name']}",
        f"- Customer ID: {s['customer_id']}",
        f"- Occupation: {s['occupation']} ({s['segment']} customer)",
        f"- Location on record: {s['home_city']}, {s['home_country']}",
        f"- Customer since: {as_date(s['onboarded_date'])}",
        f"- KYC last refreshed: {as_date(s['kyc_refreshed_date'])}",
        f"- Declared expected monthly cash activity: {money(s['expected_monthly_cash_cents'])}",
    ])


def _summary(facts: SarFacts) -> str:
    home = facts.subject["home_country"]
    parties = sorted({location(t["counterparty"], t["counterparty_country"], home)
                      for t in facts.transactions})
    places = sorted({location(t["txn_city"], t["txn_country"], home) for t in facts.transactions})
    lines = [
        "## C. Activity summary",
        f"- Activity: {ACTIVITY_DESCRIPTIONS.get(facts.alert.typology, facts.alert.typology)}",
        f"- Activity dates: {facts.first_date} to {facts.last_date}",
        (f"- Completed transactions: {len(facts.completed)}, totalling "
         f"{money(facts.completed_cents)}"),
        (f"- Attempted transactions blocked by the bank: {len(facts.attempted)}, totalling "
         f"{money(facts.attempted_cents)}"),
        (f"- Total amount involved (completed + attempted): {len(facts.transactions)} "
         f"transactions, {money(facts.total_cents)}"),
        f"- Counterparties: {', '.join(parties) or 'none'}",
        f"- Transaction locations: {', '.join(places) or 'none'}",
    ]
    if facts.truncated:
        lines.append(f"- NOTE: more than {len(facts.transactions)} transactions; list truncated. "
                     f"An analyst must confirm the totals.")
    return "\n".join(lines)


_ACCOUNT_COLUMNS: list[Column] = [
    ("account", lambda r: str(r["account_id"])),
    ("type", lambda r: r["account_type"]),
    ("opened", lambda r: str(as_date(r["opened_date"]))),
    ("status", lambda r: r["status"]),
]


def _txn_columns(facts: SarFacts) -> list[Column]:
    home = facts.subject["home_country"]
    return [
        ("txn id", lambda r: str(r["txn_id"])),
        ("when", lambda r: f"{as_datetime(r['txn_ts']):%Y-%m-%d %H:%M}"),
        ("account", lambda r: str(r["account_id"])),
        ("dir", lambda r: "IN" if r["direction"] == "CREDIT" else "OUT"),
        ("channel", lambda r: r["channel"]),
        ("amount", lambda r: money(r["amount_cents"])),
        ("counterparty", lambda r: location(r["counterparty"], r["counterparty_country"], home)),
        ("location", lambda r: location(r["txn_city"], r["txn_country"], home)),
        ("outcome", outcome),
    ]


def _subtotals(subtotals: list[Subtotal]) -> str:
    def part(count: int, cents: int) -> str:
        return f"{count} / {money(cents)}" if count else "-"

    columns: list[Column] = [
        ("grouping", lambda s: s.dimension),
        ("group", lambda s: s.label),
        ("completed", lambda s: part(s.completed, s.completed_cents)),
        ("attempted", lambda s: part(s.attempted, s.attempted_cents)),
        ("total", lambda s: part(s.total, s.total_cents)),
    ]
    return ("## E. Subtotals (count / amount). Quote these; do not add amounts up yourself.\n"
            + table(subtotals, columns))


def _prior_sars(facts: SarFacts) -> str:
    title = f"## F. Prior SARs on this subject (filed before {facts.alert.window_start})"
    if not facts.pack.prior_sars:
        return f"{title}\nNone."
    return "\n".join([title, *(f"- {as_date(s['filed_date'])} ({s['status']}): {s['narrative']}"
                               for s in facts.pack.prior_sars)])


def render_triage(facts: SarFacts) -> str:
    """Kept apart from the sheet: context for the drafter, never evidence for QC."""
    title = "## G. Triage escalation (internal context: do not quote or cite it)"
    t = facts.triage
    if t is None:
        return f"{title}\nNo triage decision on file."
    confidence = f" at confidence {t['confidence']:.2f}" if t["confidence"] is not None else ""
    key_facts = json.loads(t["key_facts"] or "[]")
    return "\n".join([
        title,
        f"- Decision: {t['decision']}{confidence}, {as_date(t['decided_at'])}",
        f"- Rationale: {t['rationale'] or '(none)'}",
        *(f"- Key fact: {k}" for k in key_facts),
    ])
