"""Compute the impact board for one run — from the database alone.

Every number is one of three kinds, and the board says which:
  measured   — read from the decision log, run log, or recorded handle time
  policy     — a choice the bank makes (e.g. the dismissal re-review sample)
  ASSUMPTION — used only when a measurement doesn't exist, and labelled as such

The board deliberately contains no accuracy figures: agreement with human
decisions is the backtest's job (Block 8), not the impact board's.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from config import Config
from src.db import Database

DECISION_ORDER = ["ESCALATE", "KYC_REVIEW", "DISMISS", "REVIEW_REQUIRED"]


@dataclass
class ImpactBoard:
    run_id: str
    model_id: str
    prompt_version: str
    started_at: Any
    finished_at: Any
    alerts_attempted: int
    alerts_failed: int
    decisions: Counter = field(default_factory=Counter)
    exposure_cents: Counter = field(default_factory=Counter)
    avg_confidence: dict[str, float] = field(default_factory=dict)
    routed_by_threshold: int = 0
    review_reasons: Counter = field(default_factory=Counter)
    decisions_with_warnings: int = 0
    tool_calls: int = 0
    tool_errors: int = 0
    model_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    model_seconds: float = 0.0
    handle_minutes_all: float = 0.0
    handle_minutes_dismissed: float = 0.0
    handle_time_source: str = "measured"  # or "ASSUMPTION"
    review_sample_rate: float = 0.0
    manual_minutes_assumption: float = 0.0

    @property
    def decided(self) -> int:
        return sum(self.decisions.values())

    @property
    def hours_returned(self) -> float:
        """Analyst hours freed if agent dismissals are accepted after the sample re-review."""
        return self.handle_minutes_dismissed * (1 - self.review_sample_rate) / 60


def latest_run_id(db: Database, config: Config) -> str | None:
    rows = db.query(f"SELECT run_id FROM {config.agent_schema}.run_log "
                    f"WHERE finished_at IS NOT NULL ORDER BY started_at DESC LIMIT 1")
    return rows[0]["run_id"] if rows else None


def compute_board(db: Database, config: Config, run_id: str) -> ImpactBoard:
    run = db.query(f"SELECT run_id, model_id, prompt_version, started_at, finished_at, "
                   f"alerts_attempted, alerts_failed, model_calls, input_tokens, output_tokens, "
                   f"model_seconds FROM {config.agent_schema}.run_log WHERE run_id = ?",
                   [run_id])
    if not run:
        raise ValueError(f"No run {run_id!r} in the run log.")
    r = run[0]
    rows = db.query(f"SELECT alert_id, decision, proposed_decision, confidence, flagged_cents, "
                    f"review_reason, warnings, tool_trace FROM {config.agent_schema}.decision_log "
                    f"WHERE run_id = ?", [run_id])

    board = ImpactBoard(
        run_id=run_id, model_id=r["model_id"], prompt_version=r["prompt_version"],
        started_at=r["started_at"], finished_at=r["finished_at"],
        alerts_attempted=int(r["alerts_attempted"] or 0),
        alerts_failed=int(r["alerts_failed"] or 0),
        model_calls=int(r["model_calls"] or 0), input_tokens=int(r["input_tokens"] or 0),
        output_tokens=int(r["output_tokens"] or 0),
        model_seconds=float(r["model_seconds"] or 0.0),
        review_sample_rate=config.review_sample_rate,
        manual_minutes_assumption=config.manual_minutes_per_alert,
    )
    board.cost_usd = (board.input_tokens * config.input_usd_per_mtok
                      + board.output_tokens * config.output_usd_per_mtok) / 1_000_000
    _tally_decisions(board, rows)
    _handle_time(board, db, config, rows)
    return board


def _tally_decisions(board: ImpactBoard, rows: list[dict[str, Any]]) -> None:
    confidences: dict[str, list[float]] = {}
    for row in rows:
        decision = row["decision"]
        board.decisions[decision] += 1
        board.exposure_cents[decision] += int(row["flagged_cents"] or 0)
        if row["confidence"] is not None:
            confidences.setdefault(decision, []).append(float(row["confidence"]))
        if decision == "REVIEW_REQUIRED":
            board.review_reasons[_reason_category(row["review_reason"] or "")] += 1
            if row["proposed_decision"] == "DISMISS" and "threshold" in (row["review_reason"] or ""):
                board.routed_by_threshold += 1
        if json.loads(row["warnings"] or "[]"):
            board.decisions_with_warnings += 1
        trace = json.loads(row["tool_trace"] or "[]")
        board.tool_calls += len(trace)
        board.tool_errors += sum(1 for t in trace if t.get("error"))
    board.avg_confidence = {d: sum(v) / len(v) for d, v in confidences.items()}


def _handle_time(board: ImpactBoard, db: Database, config: Config,
                 rows: list[dict[str, Any]]) -> None:
    """Measured analyst handle time when recorded; otherwise the labelled assumption."""
    ids = [row["alert_id"] for row in rows]
    dismissed = {row["alert_id"] for row in rows if row["decision"] == "DISMISS"}
    minutes: dict[str, float] = {}
    if ids:
        try:
            placeholders = ", ".join("?" for _ in ids)
            found = db.query(f"SELECT alert_id, handle_minutes FROM "
                             f"{config.labels_schema}.alert_dispositions "
                             f"WHERE alert_id IN ({placeholders})", ids)
            minutes = {f["alert_id"]: float(f["handle_minutes"]) for f in found
                       if f["handle_minutes"] is not None}
        except Exception:  # noqa: BLE001 — no handle-time table: fall back to the assumption
            minutes = {}
    if ids and len(minutes) < len(ids):
        board.handle_time_source = "ASSUMPTION"
        minutes = {i: config.manual_minutes_per_alert for i in ids}
    board.handle_minutes_all = sum(minutes.values())
    board.handle_minutes_dismissed = sum(v for i, v in minutes.items() if i in dismissed)


def _reason_category(reason: str) -> str:
    text = reason.lower()
    for key, label in (("validator", "invalid decision"), ("threshold", "low-confidence dismissal"),
                       ("budget", "budget exhausted"), ("refusal", "model declined"),
                       ("cut off", "response cut off"), ("submit_decision", "no decision"),
                       ("more than one", "conflicting decisions")):
        if key in text:
            return label
    return "other"
