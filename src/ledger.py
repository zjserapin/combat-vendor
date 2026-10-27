"""The agent's own state: decision log, case memory, run log.

Append-only by design: a re-decided alert gets a new row, never an overwrite,
so an examiner can see every decision ever made. "Current" means latest.
This is the only module that writes to the database, and it writes only to the
agent schema.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any

from config import Config
from src.db import Database

if TYPE_CHECKING:
    from src.alert_queue import Alert

DECISIONS = ("ESCALATE", "KYC_REVIEW", "DISMISS")
REVIEW_REQUIRED = "REVIEW_REQUIRED"  # harness fallback: a human works it

AGENT_TABLES: dict[str, str] = {
    "decision_log": """
        alert_id VARCHAR, run_id VARCHAR, customer_id INTEGER, typology VARCHAR,
        window_start DATE, window_end DATE, flagged_txns INTEGER, flagged_cents BIGINT,
        decision VARCHAR, proposed_decision VARCHAR, confidence DOUBLE, rationale VARCHAR,
        next_action VARCHAR, key_facts VARCHAR, review_reason VARCHAR, warnings VARCHAR,
        tool_trace VARCHAR, model_id VARCHAR, prompt_version VARCHAR, steps INTEGER,
        input_tokens INTEGER, output_tokens INTEGER, seconds DOUBLE, evidence_hash VARCHAR,
        decided_at TIMESTAMP""",
    "case_memory": """
        memory_id VARCHAR, customer_id INTEGER, typology VARCHAR, alert_id VARCHAR,
        kind VARCHAR, note_text VARCHAR, created_at TIMESTAMP""",
    "run_log": """
        run_id VARCHAR, started_at TIMESTAMP, finished_at TIMESTAMP, model_id VARCHAR,
        prompt_version VARCHAR, alerts_attempted INTEGER, alerts_decided INTEGER,
        alerts_review_required INTEGER, alerts_failed INTEGER, model_calls INTEGER,
        input_tokens BIGINT, output_tokens BIGINT, model_seconds DOUBLE""",
}


def utc_now() -> datetime:
    """Naive UTC timestamp — stored in TIMESTAMP (NTZ) columns on both backends."""
    return datetime.now(UTC).replace(tzinfo=None)


@dataclass
class DecisionRecord:
    """One row of agent.decision_log. Build with for_alert()."""

    alert_id: str
    run_id: str
    customer_id: int
    typology: str
    window_start: date
    window_end: date
    flagged_txns: int
    flagged_cents: int
    decision: str
    confidence: float | None
    rationale: str
    next_action: str
    proposed_decision: str | None = None  # what the model chose, even if routed to a human
    key_facts: str = ""  # JSON array of the specific facts the decision relied on
    review_reason: str | None = None
    warnings: str = ""  # JSON array — e.g. cited amounts not found in the evidence
    tool_trace: str = ""  # JSON array — compact log of tool calls
    model_id: str = ""
    prompt_version: str = ""
    steps: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    seconds: float = 0.0
    evidence_hash: str = ""  # fingerprint of the evidence pack the decision was made on
    decided_at: datetime = field(default_factory=utc_now)

    @classmethod
    def for_alert(cls, alert: Alert, run_id: str, decision: str, confidence: float | None,
                  rationale: str, next_action: str, **extra: Any) -> DecisionRecord:
        """Copy the alert's identity and window onto a decision."""
        return cls(alert_id=alert.alert_id, run_id=run_id, customer_id=alert.customer_id,
                   typology=alert.typology, window_start=alert.window_start,
                   window_end=alert.window_end, flagged_txns=alert.flagged_txns,
                   flagged_cents=alert.flagged_cents, decision=decision,
                   confidence=confidence, rationale=rationale, next_action=next_action,
                   **extra)


@dataclass
class RunStats:
    """Totals for one triage run. Filled in by the run loop (Block 7)."""

    alerts_attempted: int = 0
    alerts_decided: int = 0
    alerts_review_required: int = 0
    alerts_failed: int = 0
    model_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    model_seconds: float = 0.0


# --- setup -------------------------------------------------------------------

def ensure_agent_schema(db: Database, config: Config) -> None:
    """Create the agent schema and tables if missing, and add any new columns.

    Additive only — never drops or alters an existing column — so a table created
    by an older version of this code keeps its rows and gains the new fields.
    """
    db.execute(f"CREATE SCHEMA IF NOT EXISTS {config.agent_schema}")
    for name, columns in AGENT_TABLES.items():
        db.execute(f"CREATE TABLE IF NOT EXISTS {config.agent_schema}.{name} ({columns})")
        existing = {r["column_name"].lower() for r in db.query(
            "SELECT column_name FROM information_schema.columns "
            "WHERE UPPER(table_schema) = UPPER(?) AND UPPER(table_name) = UPPER(?)",
            [config.agent_schema, name])}
        for column, sql_type in _declared_columns(columns):
            if column not in existing:
                db.execute(f"ALTER TABLE {config.agent_schema}.{name} "
                           f"ADD COLUMN {column} {sql_type}")


# --- writes ------------------------------------------------------------------

def record_decision(db: Database, config: Config, record: DecisionRecord) -> None:
    """Write the decision twice — structured row + memory line — in one transaction."""
    memory = {
        "memory_id": uuid.uuid4().hex,
        "customer_id": record.customer_id,
        "typology": record.typology,
        "alert_id": record.alert_id,
        "kind": "case_decision",
        "note_text": memory_line(record),
        "created_at": record.decided_at,
    }
    with db.transaction():
        _insert(db, f"{config.agent_schema}.decision_log", asdict(record))
        _insert(db, f"{config.agent_schema}.case_memory", memory)


def start_run(db: Database, config: Config, model_id: str, prompt_version: str,
              run_id: str | None = None) -> str:
    """Open a run_log row and return its id."""
    run_id = run_id or f"run-{utc_now():%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:6]}"
    _insert(db, f"{config.agent_schema}.run_log", {
        "run_id": run_id, "started_at": utc_now(), "model_id": model_id,
        "prompt_version": prompt_version,
    })
    return run_id


def finish_run(db: Database, config: Config, run_id: str, stats: RunStats) -> None:
    """Close a run_log row with its totals."""
    values = asdict(stats)
    assignments = ", ".join(f"{col} = ?" for col in values)
    db.execute(
        f"UPDATE {config.agent_schema}.run_log SET finished_at = ?, {assignments} "
        f"WHERE run_id = ?",
        [utc_now(), *values.values(), run_id],
    )


# --- reads -------------------------------------------------------------------

def decided_windows(db: Database, config: Config) -> dict[str, date]:
    """alert_id -> the latest window_end the agent has already decided on."""
    rows = db.query(
        f"SELECT alert_id, MAX(window_end) AS window_end "
        f"FROM {config.agent_schema}.decision_log GROUP BY alert_id"
    )
    return {r["alert_id"]: _as_date(r["window_end"]) for r in rows}


def latest_decisions(db: Database, config: Config) -> list[dict[str, Any]]:
    """The current (most recent) decision per alert."""
    return db.query(
        f"SELECT alert_id, run_id, customer_id, typology, window_start, window_end, "
        f"flagged_cents, decision, confidence, rationale, next_action, review_reason, "
        f"decided_at "
        f"FROM {config.agent_schema}.decision_log "
        f"QUALIFY ROW_NUMBER() OVER (PARTITION BY alert_id ORDER BY decided_at DESC) = 1 "
        f"ORDER BY decided_at DESC"
    )


def recall_memory(db: Database, config: Config, customer_id: int, limit: int = 5) -> list[str]:
    """Most recent memory lines for a customer, newest first."""
    rows = db.query(
        f"SELECT note_text FROM {config.agent_schema}.case_memory "
        f"WHERE customer_id = ? ORDER BY created_at DESC LIMIT {int(limit)}",
        [customer_id],
    )
    return [r["note_text"] for r in rows]


# --- helpers -----------------------------------------------------------------

def memory_line(record: DecisionRecord) -> str:
    """One sentence the agent can recall later about this decision."""
    confidence = f" ({record.confidence:.2f})" if record.confidence is not None else ""
    if record.decision == REVIEW_REQUIRED:
        reason = record.review_reason or ""
    else:
        reason = record.rationale or ""
    reason = reason.strip().replace("\n", " ")
    if len(reason) > 220:
        reason = reason[:217] + "..."
    return (f"{record.decided_at:%Y-%m-%d} {record.typology} alert {record.alert_id} "
            f"(window {record.window_start} to {record.window_end}): "
            f"{record.decision}{confidence}. {reason}").strip()


def _declared_columns(columns: str) -> list[tuple[str, str]]:
    """'a VARCHAR, b INTEGER' -> [('a', 'VARCHAR'), ('b', 'INTEGER')]."""
    pairs = [c.split(None, 1) for c in columns.replace("\n", " ").split(",") if c.strip()]
    return [(name.lower(), sql_type.strip()) for name, sql_type in pairs]


def _insert(db: Database, table: str, values: dict[str, Any]) -> None:
    columns = ", ".join(values)
    placeholders = ", ".join("?" for _ in values)
    db.execute(f"INSERT INTO {table} ({columns}) VALUES ({placeholders})", list(values.values()))


def _as_date(value: Any) -> date:
    return value.date() if isinstance(value, datetime) else value
