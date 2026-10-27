"""Load the backtest's raw material: human labels joined to the agent's decisions.

This is evaluation code — it runs on the harness connection and may read the
labels schema. The agent's tools never import it and can't reach labels.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from config import Config
from src.db import Database

# Latest agent decision per labelled alert. LEFT JOIN: a labelled alert the agent
# hasn't decided yet still shows up, so coverage gaps are visible, not hidden.
CASES_SQL = """
WITH latest AS (
    SELECT alert_id, run_id, decision, proposed_decision, confidence, rationale, key_facts,
           review_reason, warnings, model_id, prompt_version, input_tokens, output_tokens,
           seconds
    FROM {agent}.decision_log
    {run_filter}
    QUALIFY ROW_NUMBER() OVER (PARTITION BY alert_id ORDER BY decided_at DESC) = 1
)
SELECT l.alert_id, l.typology, l.human_decision, l.human_rationale,
       d.run_id, d.decision, d.proposed_decision, d.confidence, d.rationale, d.key_facts,
       d.review_reason, d.warnings, d.model_id, d.prompt_version, d.input_tokens,
       d.output_tokens, d.seconds
FROM {labels}.alert_dispositions l
LEFT JOIN latest d ON d.alert_id = l.alert_id
ORDER BY l.alert_id
"""

# Repeat decisions on identical inputs (same alert, prompt, evidence) — the stability check.
REPEATS_SQL = """
SELECT alert_id, prompt_version, evidence_hash,
       COUNT(*)                 AS runs,
       COUNT(DISTINCT decision) AS distinct_decisions
FROM {agent}.decision_log
GROUP BY alert_id, prompt_version, evidence_hash
HAVING COUNT(*) >= 2
"""


@dataclass
class BacktestCase:
    """One labelled alert, the human's call, and the agent's (if it has made one)."""

    alert_id: str
    typology: str
    human_decision: str
    human_rationale: str
    agent_decision: str | None = None  # None: the agent hasn't decided this alert yet
    proposed_decision: str | None = None  # the model's own call, before harness policy
    confidence: float | None = None
    rationale: str = ""
    key_facts: list[str] = field(default_factory=list)
    review_reason: str = ""
    warnings: list[str] = field(default_factory=list)
    run_id: str = ""
    model_id: str = ""
    prompt_version: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    seconds: float = 0.0
    slice: str = ""  # "dev" | "holdout" — set by the split
    vendor_decision: str | None = None  # set by the vendor column

    @property
    def decided(self) -> bool:
        return self.agent_decision is not None


@dataclass(frozen=True)
class Repeat:
    """An alert decided more than once on identical inputs."""

    alert_id: str
    runs: int
    stable: bool  # every run reached the same decision


def load_cases(db: Database, config: Config, run_id: str | None = None) -> list[BacktestCase]:
    """Every labelled alert with the agent's latest decision (optionally from one run)."""
    run_filter, params = ("WHERE run_id = ?", [run_id]) if run_id else ("", [])
    sql = CASES_SQL.format(agent=config.agent_schema, labels=config.labels_schema,
                           run_filter=run_filter)
    return [_to_case(row) for row in db.query(sql, params)]


def has_decisions(db: Database, config: Config) -> bool:
    """False until a triage run has logged at least one decision (checked before scoring,
    so an empty or not-yet-created agent schema gets a clear message, not a SQL error)."""
    rows = db.query("SELECT COUNT(*) AS n FROM information_schema.tables "
                    "WHERE UPPER(table_schema) = UPPER(?) AND UPPER(table_name) = 'DECISION_LOG'",
                    [config.agent_schema])
    if not int(rows[0]["n"]):
        return False
    count = db.query(f"SELECT COUNT(*) AS n FROM {config.agent_schema}.decision_log")
    return int(count[0]["n"]) > 0


def load_repeats(db: Database, config: Config) -> list[Repeat]:
    rows = db.query(REPEATS_SQL.format(agent=config.agent_schema))
    return [Repeat(r["alert_id"], int(r["runs"]), int(r["distinct_decisions"]) == 1)
            for r in rows]


def _to_case(row: dict[str, Any]) -> BacktestCase:
    return BacktestCase(
        alert_id=str(row["alert_id"]),
        typology=str(row["typology"]),
        human_decision=str(row["human_decision"]),
        human_rationale=row["human_rationale"] or "",
        agent_decision=row["decision"],
        proposed_decision=row["proposed_decision"],
        confidence=None if row["confidence"] is None else float(row["confidence"]),
        rationale=row["rationale"] or "",
        key_facts=_json_list(row["key_facts"]),
        review_reason=row["review_reason"] or "",
        warnings=_json_list(row["warnings"]),
        run_id=row["run_id"] or "",
        model_id=row["model_id"] or "",
        prompt_version=row["prompt_version"] or "",
        input_tokens=int(row["input_tokens"] or 0),
        output_tokens=int(row["output_tokens"] or 0),
        seconds=float(row["seconds"] or 0.0),
    )


def _json_list(value: str | None) -> list[str]:
    """Stored JSON array -> list of strings. Tolerates empty or non-JSON text."""
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return [value]
    return [str(item) for item in parsed] if isinstance(parsed, list) else [str(parsed)]
