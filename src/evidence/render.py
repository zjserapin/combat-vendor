"""Render an EvidencePack as a compact, labelled case file for the model.

The only place cents become dollars. Output is deterministic: no timestamps of
"now", no unordered sets — so the fingerprint is stable run to run.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import date, datetime
from typing import TYPE_CHECKING, Any

from src.db import Row

if TYPE_CHECKING:
    from src.evidence.pack import EvidencePack

Column = tuple[str, Callable[[Row], str]]


def render(pack: EvidencePack) -> str:
    """The full case file, sections in a fixed order."""
    a, p, c = pack.alert, pack.period, pack.customer
    home = c["home_country"]
    sections = [
        f"# CASE FILE: alert {a.alert_id}",
        (f"Data as of {pack.as_of}. Point-in-time evidence: nothing dated after {p.cutoff}. "
         f"All amounts in USD."),
        _alert_section(pack),
        _customer_section(pack),
        _txn_section(f"## 3. Flagged transactions in this alert ({len(pack.flagged)} of "
                     f"{a.flagged_txns} shown)", pack.flagged, home),
        _txn_section(f"## 4. Other activity during the window +/- 1 day "
                     f"({p.band_start} to {p.band_end}; up to 20 largest, chronological)",
                     pack.window_activity, home),
        _txn_section(f"## 5. Largest other transactions {p.context_start} to {p.cutoff}, "
                     f"outside the window (up to 10, chronological)", pack.context_activity, home),
        _profile_section(pack),
        _history_section(pack),
        _accounts_section(pack),
        _flows_section(pack),
        _notes_section(pack),
        _sars_section(pack),
    ]
    return "\n\n".join(sections) + "\n"


# --- sections ------------------------------------------------------------------

def _alert_section(pack: EvidencePack) -> str:
    a = pack.alert
    days = (a.window_end - a.window_start).days + 1
    return "\n".join([
        "## 1. Alert",
        f"- Typology: {a.typology}",
        f"- Window: {a.window_start} to {a.window_end} ({days} day{'s' if days > 1 else ''})",
        f"- Flagged: {a.flagged_txns} transaction(s) totalling {money(a.flagged_cents)}",
        f"- Blocked attempts: {a.blocked_txns}",
    ])


def _customer_section(pack: EvidencePack) -> str:
    c, a = pack.customer, pack.alert
    onboarded, refreshed = as_date(c["onboarded_date"]), as_date(c["kyc_refreshed_date"])
    return "\n".join([
        "## 2. Customer",
        (f"- {c['full_name']} (customer {c['customer_id']}): {c['segment']}, "
         f"{c['occupation']}, {c['home_city']}, {c['home_country']}"),
        f"- Customer since {onboarded} ({_age(onboarded, a.window_start)} before the window)",
        f"- Risk rating: {c['risk_rating']}/100",
        f"- Declared expected monthly cash activity: {money(c['expected_monthly_cash_cents'])}",
        f"- KYC last refreshed: {refreshed} ({_age(refreshed, a.window_start)} before the window)",
    ])


def _txn_section(title: str, rows: list[Row], home: str) -> str:
    columns: list[Column] = [
        ("when", lambda r: f"{as_datetime(r['txn_ts']):%Y-%m-%d %H:%M}"),
        ("dir", lambda r: "IN" if r["direction"] == "CREDIT" else "OUT"),
        ("channel", lambda r: r["channel"]),
        ("amount", lambda r: money(r["amount_cents"])),
        ("counterparty", lambda r: r["counterparty"] + _abroad(r["counterparty_country"], home)),
        ("where", lambda r: r["txn_city"] + _abroad(r["txn_country"], home)),
        ("status", lambda r: r["status"] + (f" ({r['flag_reason']})" if r["flag_reason"] else "")),
    ]
    return f"{title}\n{table(rows, columns)}"


def _profile_section(pack: EvidencePack) -> str:
    """Pivot: one line per channel/direction, one column per month."""
    p = pack.period
    title = (f"## 6. Monthly activity profile, {p.history_start} to {p.cutoff} "
             f"(count / total; first and last months are partial; blocked excluded)")
    months = sorted({as_date(r["month"]) for r in pack.monthly_profile})
    if not months:
        return f"{title}\nNone."
    cells: dict[tuple[str, str], dict[date, str]] = {}
    for r in pack.monthly_profile:
        key = (r["channel"], "IN" if r["direction"] == "CREDIT" else "OUT")
        cells.setdefault(key, {})[as_date(r["month"])] = \
            f"{r['txn_count']} / {money(r['total_cents'])}"
    header = "| activity | " + " | ".join(f"{m:%Y-%m}" for m in months) + " |"
    divider = "|" + "---|" * (len(months) + 1)
    lines = [f"| {ch} {d} | " + " | ".join(cells[(ch, d)].get(m, "-") for m in months) + " |"
             for ch, d in sorted(cells)]
    return "\n".join([title, header, divider, *lines])


def _history_section(pack: EvidencePack) -> str:
    p, a = pack.period, pack.alert
    title = (f"## 7. Prior history with this alert's counterparties and countries "
             f"({p.history_start} to {a.window_start}, before the window)")
    seen: list[Column] = [
        ("prior txns", lambda r: str(r["txn_count"])),
        ("prior total", lambda r: money(r["total_cents"])),
        ("first seen", lambda r: _day(r["first_seen"]) if r["first_seen"] else "never"),
    ]
    parts = [title, "Counterparties:",
             table(pack.counterparty_history, [("counterparty", lambda r: r["counterparty"]),
                                               *seen])]
    if pack.country_history:
        tiers = {j["country"]: j["risk_tier"] for j in pack.jurisdictions}
        parts += ["Foreign countries involved:",
                  table(pack.country_history,
                        [("country", lambda r: r["country"]),
                         ("bank risk tier", lambda r: tiers.get(r["country"], "UNRATED")), *seen])]
    else:
        parts.append("Foreign countries involved: none.")
    return "\n".join(parts)


def _accounts_section(pack: EvidencePack) -> str:
    columns: list[Column] = [
        ("account", lambda r: str(r["account_id"])),
        ("type", lambda r: r["account_type"]),
        ("opened", lambda r: _day(r["opened_date"])),
        (f"balance as of {pack.as_of}", lambda r: money(r["balance_cents"])),
        ("status", lambda r: r["status"]),
    ]
    return f"## 8. Accounts\n{table(pack.accounts, columns)}"


def _flows_section(pack: EvidencePack) -> str:
    totals = {r["direction"]: r for r in pack.flows_30d}
    money_in = totals.get("CREDIT", {"txn_count": 0, "total_cents": 0})
    money_out = totals.get("DEBIT", {"txn_count": 0, "total_cents": 0})
    return "\n".join([
        f"## 9. Money in / out, {pack.period.flows_start} to {pack.period.cutoff} (blocked excluded)",
        f"- In: {money_in['txn_count']} txns, {money(money_in['total_cents'])}",
        f"- Out: {money_out['txn_count']} txns, {money(money_out['total_cents'])}",
    ])


def _notes_section(pack: EvidencePack) -> str:
    title = f"## 10. KYC and operations notes (to {pack.period.cutoff}, newest first)"
    if not pack.kyc_notes:
        return f"{title}\nNone on file."
    lines = [f"- {_day(n['note_date'])} [{n['author']}]: {n['note_text']}" for n in pack.kyc_notes]
    return "\n".join([title, *lines])


def _sars_section(pack: EvidencePack) -> str:
    title = f"## 11. Prior SARs (filed before {pack.alert.window_start})"
    if not pack.prior_sars:
        return f"{title}\nNone."
    lines = [f"- {_day(s['filed_date'])} {s['typology']} ({s['status']}): {s['narrative']}"
             for s in pack.prior_sars]
    return "\n".join([title, *lines])


# --- formatting helpers ----------------------------------------------------------

def table(rows: Sequence[Row], columns: Sequence[Column]) -> str:
    """Pipe table, or 'None.' when empty."""
    if not rows:
        return "None."
    header = "| " + " | ".join(name for name, _ in columns) + " |"
    divider = "|" + "---|" * len(columns)
    body = ["| " + " | ".join(fn(r) for _, fn in columns) + " |" for r in rows]
    return "\n".join([header, divider, *body])


def money(cents: Any) -> str:
    """Integer cents -> '$1,234.56'."""
    return f"${int(cents or 0) / 100:,.2f}"


def as_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def as_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    return datetime.fromisoformat(str(value))


def _day(value: Any) -> str:
    return as_date(value).isoformat()


def _abroad(country: str | None, home: str) -> str:
    return f" ({country})" if country and country != home else ""


def _age(start: date, end: date) -> str:
    days = (end - start).days
    if days < 60:
        return f"{days} days"
    years, months = divmod(round(days / 30.44), 12)
    return f"{years} yr {months} mo" if years else f"{months} mo"
