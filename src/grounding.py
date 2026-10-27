"""Cheap check for made-up facts: every dollar amount and date the agent cites
should appear in what it was shown — the case file or its own tool results.

Findings are *warnings* for reviewers, not rejections: the model may legitimately
cite a total it computed. A pattern of warnings is a signal worth investigating.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

# "$1,234.56" or "$9,850"; abbreviations like "$54K" are skipped (too loose to check).
_AMOUNT = re.compile(r"\$\s?(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d{2}))?(?![\dKkMm])")
_DATE = re.compile(r"\b(20\d{2}-\d{2}-\d{2})\b")
# Raw integer cents in SQL results (e.g. "| 4745064 |") also count as evidence.
_CENTS_FIELD = re.compile(r"(?<![\d.,$])(\d{3,})(?![\d.,])")


def ungrounded_facts(claims: Iterable[str], sources: Iterable[str]) -> list[str]:
    """Amounts and dates in `claims` that appear nowhere in `sources`."""
    corpus = "\n".join(sources)
    known_amounts = {_normalize(m) for m in _AMOUNT.finditer(corpus)}
    known_cents = {_cents_from_raw(m.group(1)) for m in _CENTS_FIELD.finditer(corpus)}
    warnings: list[str] = []
    for claim in claims:
        for match in _AMOUNT.finditer(claim):
            if not _amount_known(match, known_amounts, known_cents):
                warnings.append(f"amount {match.group(0).strip()} not found in evidence")
        for match in _DATE.finditer(claim):
            if match.group(1) not in corpus:
                warnings.append(f"date {match.group(1)} not found in evidence")
    return sorted(set(warnings))


def _normalize(match: re.Match[str]) -> tuple[str, str | None]:
    return match.group(1).replace(",", ""), match.group(2)


def _cents_from_raw(digits: str) -> tuple[str, str]:
    return digits[:-2] or "0", digits[-2:]


def _amount_known(match: re.Match[str], known: set[tuple[str, str | None]],
                  cents: set[tuple[str, str]]) -> bool:
    dollars, decimals = _normalize(match)
    if decimals is not None:
        return (dollars, decimals) in known or (dollars, decimals) in cents
    # A whole-dollar citation like "$9,850" matches "$9,850.xx" in the evidence. Raw cents
    # only count for amounts of $100+, so short ids and years can't vouch for a claim.
    return any(d == dollars for d, _ in known) or \
        (len(dollars) >= 3 and any(d == dollars for d, _ in cents))
