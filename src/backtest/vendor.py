"""The vendor column: same alerts, same metrics, thresholds fit fairly.

Source, in order:
  1. `labels.vendor_scores(alert_id, vendor_score)` if it exists — the vendor's real
     output, loaded at Ally. Lives beside the labels so the agent can't see it either.
  2. Otherwise a STAND-IN: a simple rules-engine score from bank data. It only
     exercises this code path. It is not a vendor and not evidence.

A score becomes a decision through two thresholds chosen on the dev slice
only (best agreement for the vendor), then applied unchanged to holdout.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from config import Config
from src.backtest.data import BacktestCase
from src.backtest.metrics import DISMISS, ESCALATE, KYC_REVIEW
from src.backtest.split import DEV
from src.db import Database

STANDIN, VENDOR_TABLE = "stand-in", "vendor_scores table"

VENDOR_SQL = "SELECT alert_id, vendor_score FROM {labels}.vendor_scores"

STANDIN_INPUTS_SQL = """
SELECT a.alert_id, a.blocked_txns, a.flagged_cents, c.risk_rating
FROM {bank}.alerts a
JOIN {bank}.customers c ON c.customer_id = a.customer_id
"""


@dataclass(frozen=True)
class VendorColumn:
    source: str  # STANDIN | VENDOR_TABLE
    escalate_at: float  # score >= this -> ESCALATE
    review_at: float  # score >= this (and below escalate_at) -> KYC_REVIEW; else DISMISS
    scored: int  # labelled alerts that had a vendor score

    @property
    def is_standin(self) -> bool:
        return self.source == STANDIN


def attach_vendor(db: Database, config: Config, cases: Sequence[BacktestCase]) -> VendorColumn:
    """Score every case, fit thresholds on dev, and set each case's vendor_decision."""
    scores, source = load_scores(db, config)
    dev = [(scores[c.alert_id], c.human_decision) for c in cases
           if c.slice == DEV and c.alert_id in scores]
    escalate_at, review_at = fit_thresholds(dev)
    for case in cases:
        if case.alert_id in scores:
            case.vendor_decision = to_decision(scores[case.alert_id], escalate_at, review_at)
    return VendorColumn(source, escalate_at, review_at,
                        sum(c.alert_id in scores for c in cases))


def load_scores(db: Database, config: Config) -> tuple[dict[str, float], str]:
    """Real vendor scores if the table exists; otherwise the stand-in.

    Checks existence explicitly rather than catching errors: a broken vendor table
    at Ally must fail loudly, not quietly turn into the stand-in.
    """
    if _vendor_table_exists(db, config):
        rows = db.query(VENDOR_SQL.format(labels=config.labels_schema))
        return {str(r["alert_id"]): float(r["vendor_score"]) for r in rows}, VENDOR_TABLE
    rows = db.query(STANDIN_INPUTS_SQL.format(bank=config.bank_schema))
    return {str(r["alert_id"]): standin_score(int(r["risk_rating"]), int(r["blocked_txns"]),
                                              int(r["flagged_cents"])) for r in rows}, STANDIN


def standin_score(risk_rating: int, blocked_txns: int, flagged_cents: int) -> float:
    """0-1 "rules engine" score: mostly customer risk, plus blocks and size. Not a model."""
    size = min(flagged_cents / 5_000_000, 1.0)  # saturates at $50,000
    return round(0.6 * risk_rating / 100 + 0.3 * (blocked_txns > 0) + 0.1 * size, 4)


def fit_thresholds(dev: Sequence[tuple[float, str]]) -> tuple[float, float]:
    """(escalate_at, review_at) with the best dev agreement; ties go to the lower pair.

    Tries every observed score (and "never") as each cut-off — exhaustive, and
    cheap at backtest sizes (n² pairs over a few hundred alerts).
    """
    cuts = sorted({score for score, _ in dev}) + [math.inf]
    best = (-1, math.inf, math.inf)
    for review_at in cuts:
        for escalate_at in (c for c in cuts if c >= review_at):
            hits = sum(to_decision(s, escalate_at, review_at) == h for s, h in dev)
            if hits > best[0]:
                best = (hits, escalate_at, review_at)
    return best[1], best[2]


def _vendor_table_exists(db: Database, config: Config) -> bool:
    rows = db.query("SELECT COUNT(*) AS n FROM information_schema.tables "
                    "WHERE UPPER(table_schema) = UPPER(?) AND UPPER(table_name) = 'VENDOR_SCORES'",
                    [config.labels_schema])
    return int(rows[0]["n"]) > 0


def to_decision(score: float, escalate_at: float, review_at: float) -> str:
    if score >= escalate_at:
        return ESCALATE
    return KYC_REVIEW if score >= review_at else DISMISS
