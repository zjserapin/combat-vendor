"""Rule checks: does the narrative match the records? (data vs narrative)

Each check is `(narrative, facts) -> list[Finding]`. No model, no network: the
same narrative and records always give the same findings.
"""

from __future__ import annotations

import difflib
import re
from collections.abc import Callable
from datetime import date, datetime

from src.evidence.render import money
from src.grounding import ungrounded_facts
from src.sar.facts import SarFacts
from src.sar.findings import Finding, context, error, warning

_PROPER = re.compile(r"\b[A-Z][a-z]+(?:['-][A-Za-z]+)?\b")
_NUMBER_WORDS = {w: i for i, w in enumerate(
    ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen", "twenty"])}
_COUNT = re.compile(
    r"(?<![\d$.,])\b(\d+|" + "|".join(_NUMBER_WORDS) + r")\s+(?:[a-z-]+\s+){0,3}?"
    r"(?:transactions?|deposits?|withdrawals?|purchases?|transfers?|wires?|payments?|attempts?)\b",
    re.IGNORECASE)
# An identifier introduced as one: "account 501133", "customer ID 10675", "transaction #812".
_IDENTIFIER = re.compile(
    r"\b(?:account|acct\.?|customer|transaction|txn)\s*(?:number|no\.?|id|#)?\s*:?\s*#?(\d{4,})\b",
    re.IGNORECASE)
_ENDING_IN = re.compile(r"\bending in\s+(\d{3,4})\b", re.IGNORECASE)
_MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"]
_LONG_DATE = re.compile(r"\b(" + "|".join(m[:3] for m in _MONTHS) + r")[a-z]*\.?\s+(\d{1,2}),?\s+"
                        r"(20\d{2})\b", re.IGNORECASE)
_SLASH_DATE = re.compile(r"\b(\d{1,2})/(\d{1,2})/(20\d{2})\b")
_ISO_DATE = re.compile(r"\b(20\d{2})-(\d{2})-(\d{2})\b")


def subject_name(narrative: str, facts: SarFacts) -> list[Finding]:
    name = facts.subject["full_name"]
    if name in narrative:
        return []
    return [error("subject_name", "accuracy",
                  f"the subject's full name as on record ('{name}') does not appear")]


def name_spelling(narrative: str, facts: SarFacts) -> list[Finding]:
    """Near-misses of the subject's name (ERROR) or other proper nouns (WARNING)."""
    subject_parts = facts.subject["full_name"].split()
    names = sorted(_name_vocabulary(facts) - set(subject_parts))
    known = set(_PROPER.findall(facts.evidence_text)) | set(subject_parts)
    findings = []
    for word in sorted(set(_PROPER.findall(narrative)) - known):
        hit = difflib.get_close_matches(word, subject_parts, n=1, cutoff=0.75)
        if hit:
            findings.append(error("name_spelling", "accuracy",
                                  f"'{word}' looks like a misspelling of the subject's name "
                                  f"'{hit[0]}'", word))
            continue
        hit = difflib.get_close_matches(word, names, n=1, cutoff=0.85)
        if hit and word.rstrip("s") != hit[0].rstrip("s"):  # plurals aren't typos
            findings.append(warning("name_spelling", "accuracy",
                                    f"'{word}' looks like a misspelling of '{hit[0]}' in the "
                                    f"records", word))
    return findings


def unknown_identifiers(narrative: str, facts: SarFacts) -> list[Finding]:
    """Account, customer, or transaction numbers that aren't on this case."""
    known = facts.known_ids
    findings = [error("unknown_identifier", "accuracy",
                      f"identifier {m.group(1)} is not the subject's customer ID, one of their "
                      f"accounts, or a reported transaction", context(narrative, m))
                for m in _IDENTIFIER.finditer(narrative) if m.group(1) not in known]
    accounts = [str(a["account_id"]) for a in facts.pack.accounts]
    findings += [error("unknown_identifier", "accuracy",
                       f"no account of the subject ends in {m.group(1)}", context(narrative, m))
                 for m in _ENDING_IN.finditer(narrative)
                 if not any(a.endswith(m.group(1)) for a in accounts)]
    return findings


def grounded_amounts_and_dates(narrative: str, facts: SarFacts) -> list[Finding]:
    """Every amount and date stated must appear in the records (any common date format)."""
    corpus = facts.evidence_text
    findings = [error("ungrounded_fact", "accuracy",
                      f"{w.replace(' in evidence', ' in the records')}")
                for w in ungrounded_facts([narrative], [corpus])]
    for day, raw in _non_iso_dates(narrative):
        if day is None:
            findings.append(error("invalid_date", "accuracy", f"'{raw}' is not a valid date", raw))
        elif day.isoformat() not in corpus:
            findings.append(error("ungrounded_fact", "accuracy",
                                  f"date '{raw}' ({day}) not found in the records", raw))
    if _ISO_DATE.search(narrative) and _non_iso_dates(narrative):
        findings.append(warning("date_format", "mechanics",
                                "dates are written in more than one format; pick one"))
    return findings


def key_facts_stated(narrative: str, facts: SarFacts) -> list[Finding]:
    """The when and how-much a reader needs first: activity dates and the total."""
    findings = []
    amounts = {money(facts.total_cents), money(facts.completed_cents)}
    if not any(a in narrative or a.split(".")[0] in narrative for a in amounts):
        findings.append(error("total_stated", "completeness",
                              f"the total amount involved ({money(facts.total_cents)}"
                              f", or {money(facts.completed_cents)} completed) is not stated"))
    stated = all_dates(narrative)
    for label, day in (("first", facts.first_date), ("last", facts.last_date)):
        if day not in stated:
            findings.append(error("dates_stated", "completeness",
                                  f"the {label} activity date ({day}) is not stated"))
    return findings


def transaction_counts(narrative: str, facts: SarFacts) -> list[Finding]:
    """'four card purchases' must match a count somewhere in the records."""
    allowed = _record_counts(facts)
    findings = []
    for m in _COUNT.finditer(narrative):
        raw = m.group(1).lower()
        n = int(raw) if raw.isdigit() else _NUMBER_WORDS[raw]
        if n not in allowed:
            findings.append(error("transaction_count", "accuracy",
                                  f"stated count {n} matches no count in the records "
                                  f"(reported: {len(facts.completed)} completed, "
                                  f"{len(facts.attempted)} attempted)", context(narrative, m)))
    return findings


FACT_CHECKS: tuple[Callable[[str, SarFacts], list[Finding]], ...] = (
    subject_name, name_spelling, unknown_identifiers, grounded_amounts_and_dates,
    key_facts_stated, transaction_counts,
)


# --- helpers ---------------------------------------------------------------------------

def all_dates(text: str) -> set[date]:
    days = {_safe_date(int(y), int(m), int(d)) for y, m, d in _ISO_DATE.findall(text)}
    days |= {day for day, _ in _non_iso_dates(text)}
    return {d for d in days if d}


def _non_iso_dates(text: str) -> list[tuple[date | None, str]]:
    out = []
    for m in _LONG_DATE.finditer(text):
        month = [x[:3] for x in _MONTHS].index(m.group(1).lower()[:3]) + 1
        out.append((_safe_date(int(m.group(3)), month, int(m.group(2))), m.group(0)))
    for m in _SLASH_DATE.finditer(text):  # US order: MM/DD/YYYY
        out.append((_safe_date(int(m.group(3)), int(m.group(1)), int(m.group(2))), m.group(0)))
    return out


def _name_vocabulary(facts: SarFacts) -> set[str]:
    """Words from real names in the records: people, counterparties, places."""
    p = facts.pack
    phrases = [facts.subject["full_name"], facts.subject["home_city"],
               facts.subject["home_country"], *(n["author"] for n in p.kyc_notes)]
    for t in [*facts.transactions, *p.window_activity, *p.context_activity]:
        phrases += [t["counterparty"], t["txn_city"], t["txn_country"] or "",
                    t["counterparty_country"] or ""]
    words = {w for phrase in phrases for w in re.split(r"[\s,]+", phrase or "")}
    return {w for w in words if len(w) >= 4 and w[:1].isupper()}


def _safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return datetime(year, month, day).date()
    except ValueError:
        return None


def _record_counts(facts: SarFacts) -> set[int]:
    """Every count a narrative could legitimately cite."""
    counts = {1, len(facts.transactions), len(facts.completed), len(facts.attempted)}
    for s in facts.subtotals:
        counts |= {s.completed, s.attempted, s.total}
    p = facts.pack
    for rows in (p.monthly_profile, p.flows_30d, p.counterparty_history, p.country_history):
        counts |= {int(r["txn_count"]) for r in rows}
    return counts
