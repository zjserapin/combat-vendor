"""A QC finding: one problem, where it is, and how bad. Shared by rules and the model."""

from __future__ import annotations

import re
from dataclasses import dataclass

ERROR = "ERROR"  # would get the narrative sent back; triggers a revision
WARNING = "WARNING"  # worth fixing; never blocks on its own


@dataclass(frozen=True)
class Finding:
    check: str  # e.g. "subject_name", "legal_conclusion", or "model" for the reviewer
    severity: str  # ERROR | WARNING
    category: str  # accuracy | completeness | consistency | clarity | objectivity | mechanics
    message: str
    excerpt: str = ""
    source: str = "rule"  # rule | model


def error(check: str, category: str, message: str, excerpt: str = "") -> Finding:
    return Finding(check, ERROR, category, message, excerpt)


def warning(check: str, category: str, message: str, excerpt: str = "") -> Finding:
    return Finding(check, WARNING, category, message, excerpt)


def context(text: str, match: re.Match[str], width: int = 30) -> str:
    """The match plus a little text either side, on one line — enough to find it."""
    start, end = max(0, match.start() - width), min(len(text), match.end() + width)
    snippet = " ".join(text[start:end].split())
    return ("..." if start else "") + snippet + ("..." if end < len(text) else "")
