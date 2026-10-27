"""Rule checks: is the narrative written the way a SAR must be? (language and mechanics)

Pattern lists, not a spellchecker: they catch the common kickback reasons with
near-zero false alarms. General spelling and grammar are the model reviewer's job.
Each list is plain data — extend it from real kickback history.
"""

from __future__ import annotations

import re
from collections.abc import Callable

from config import Config
from src.sar.findings import Finding, context, error, warning

# (pattern, check, severity-maker, category, message)
Rule = tuple[str, str, Callable[..., Finding], str, str]

PATTERN_RULES: tuple[Rule, ...] = (
    ((r"\[(?:TBD|TODO|INSERT|NAME|DATE|AMOUNT)[^\]]*\]|\bTBD\b|\bTODO\b|\bXX+\b|\blorem ipsum\b"
     r"|<[a-z _]+>|\{[a-z_]+\}"), "placeholder", error, "completeness",
     "placeholder text left in the narrative"),
    ((r"\b(?:laundered|laundering the|committed (?:fraud|a crime|money laundering)|is guilty"
     r"|was guilty|guilty of|engaged in money laundering|criminal proceeds|drug money"
     r"|fraudster)\b"), "legal_conclusion", error, "objectivity",
     "legal conclusion: describe the activity and why it is suspicious, not the crime"),
    (r"\b(?:illegal(?:ly)?|criminal activity|terrorist)\b", "legal_conclusion", warning,
     "objectivity", "loaded term: make sure the records support it, or describe the activity"),
    ((r"\b(?:probably|presumably|possibly|apparently|it seems|seems to|appears to have|may have"
     r"|might have|could have been|we believe|we think|in our opinion|obviously|clearly)\b"),
     "speculation", warning, "objectivity", "speculation or opinion: state what the records show"),
    (r"(?<![A-Za-z.])(?:I|I'm|I've|my|me)(?![A-Za-z])", "first_person", warning, "objectivity",
     "first person: write in the Bank's voice"),
    ((r"(?i:\b(?:as an AI|language model|the model|LLM|AI system|fact sheet|case file"
     r"|evidence pack|triage|key_facts)\b)"), "internal_reference", error, "clarity",
     "internal or system reference: a law-enforcement reader can't see it"),
    ((r"\b\d{3,}-[A-Z_]{4,}-20\d{2}-\d{2}-\d{2}\b|\b[A-Z]{3,}_[A-Z_]{3,}\b|\brisk (?:rating|score)"
     r" of \d+"), "internal_reference", warning, "clarity",
     "alert ID, rule code, or internal score: describe the behaviour instead"),
    ((r"\b(?:see attached|see attachment|attached (?:spreadsheet|file|document)"
     r"|refer to the attachment)\b"), "attachment_reference", warning, "completeness",
     "the narrative should stand on its own; describe what the attachment shows"),
    ((r"\b(?:several|numerous|various|a number of|large amounts?|significant amounts?"
     r"|substantial sums?)\b"), "vague_quantity", warning, "clarity",
     "vague quantity: the records give exact counts and amounts"),
    (r"\$\d{4,}(?!\d)|\$\s+\d|\$[\d,]+\.\d(?!\d)|\b\d[\d,]*\s+dollars\b", "amount_format",
     warning, "mechanics", "amount format: write amounts as $1,234.56"),
    (r"[ \t]+[,.;:)](?!\d)", "punctuation", warning, "mechanics", "space before punctuation"),
    (r"[a-z]{2}[.;,][A-Z][a-z]", "punctuation", warning, "mechanics",
     "missing space after punctuation"),
)

# Common misspellings in investigations writing. Deliberately short and certain.
MISSPELLINGS = {
    "recieved": "received", "recieve": "receive", "transfered": "transferred",
    "transfering": "transferring", "withdrawl": "withdrawal", "withdrawls": "withdrawals",
    "seperate": "separate", "seperately": "separately", "occured": "occurred",
    "occuring": "occurring", "begining": "beginning", "acount": "account",
    "accout": "account", "transacton": "transaction", "trasaction": "transaction",
    "suspicous": "suspicious", "suspcious": "suspicious", "benificiary": "beneficiary",
    "beneficary": "beneficiary", "untill": "until", "wich": "which", "teh": "the",
    "adn": "and", "recieving": "receiving", "deposite": "deposit", "depostis": "deposits",
    "accomodate": "accommodate", "comission": "commission", "committment": "commitment",
    "goverment": "government", "independant": "independent", "occurence": "occurrence",
    "payed": "paid", "reciept": "receipt", "threshhold": "threshold", "thier": "their",
}
_ALLOWED_DOUBLES = {"had", "that", "is"}  # "had had", "that that" can be correct
_WORD = re.compile(r"[A-Za-z']+")


def language_checks(narrative: str, config: Config) -> list[Finding]:
    """Run every language and mechanics check."""
    findings: list[Finding] = []
    for pattern, check, make, category, message in PATTERN_RULES:
        flags = 0 if check in {"first_person", "internal_reference", "punctuation"} \
            else re.IGNORECASE
        for m in re.finditer(pattern, narrative, flags):
            findings.append(make(check, category, message, context(narrative, m)))
    findings += _misspellings(narrative)
    findings += _doubled_words(narrative)
    findings += _sentences(narrative)
    findings += _length(narrative, config)
    return findings


def _misspellings(text: str) -> list[Finding]:
    out = []
    for m in _WORD.finditer(text):
        fix = MISSPELLINGS.get(m.group(0).lower())
        if fix:
            out.append(error("misspelling", "mechanics",
                             f"'{m.group(0)}' should be '{fix}'", context(text, m)))
    return out


def _doubled_words(text: str) -> list[Finding]:
    return [error("doubled_word", "mechanics", f"repeated word '{m.group(1)}'", context(text, m))
            for m in re.finditer(r"\b([A-Za-z]+)\s+\1\b", text, re.IGNORECASE)
            if m.group(1).lower() not in _ALLOWED_DOUBLES]


def _sentences(text: str) -> list[Finding]:
    """Sentence-level mechanics: capital start, end punctuation, length, brackets."""
    out = []
    for paragraph in (p.strip() for p in text.split("\n\n") if p.strip()):
        if paragraph[-1] not in ".!?\")":
            out.append(warning("punctuation", "mechanics", "paragraph does not end with a "
                               "full stop", paragraph[-40:]))
        for sentence in re.split(r"(?<=[.!?])\s+(?=[A-Za-z])", paragraph):
            if sentence[:1].islower():
                out.append(warning("capitalization", "mechanics",
                                   "sentence starts with a lowercase letter", sentence[:40]))
            if len(sentence.split()) > 60:
                out.append(warning("long_sentence", "clarity",
                                   f"sentence of {len(sentence.split())} words: split it",
                                   sentence[:60] + "..."))
        if paragraph.count("(") != paragraph.count(")") or paragraph.count('"') % 2:
            out.append(warning("punctuation", "mechanics", "unbalanced brackets or quotes",
                               paragraph[:60] + "..."))
    return out


def _length(text: str, config: Config) -> list[Finding]:
    if len(text) > config.sar_narrative_max_chars:
        return [error("length", "completeness",
                      f"{len(text):,} characters exceeds the {config.sar_narrative_max_chars:,} "
                      f"limit")]
    if len(text) < config.sar_narrative_min_chars:
        return [warning("length", "completeness",
                        f"only {len(text):,} characters: thin narratives are a common kickback")]
    return []
