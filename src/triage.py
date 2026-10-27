"""Work the queue: one alert at a time, end to end, without letting one failure stop the run.

    summary = run_triage(db, config, llm, limit=10)

Per alert: evidence pack -> investigator loop -> validator -> ledger.
A failure (model unreachable, bad data) is counted and the run moves on; repeated
failures in a row stop the run early so a dead endpoint doesn't burn the queue.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from config import Config
from src import ledger
from src.agent import ChatModel, investigate
from src.alert_queue import Alert, fetch_alerts
from src.db import Database, agent_database
from src.decision import finalize
from src.evidence import build_evidence
from src.ledger import REVIEW_REQUIRED, DecisionRecord, RunStats
from src.prompts import PROMPT_VERSION
from src.tools import Toolbox


@dataclass
class AlertOutcome:
    alert: Alert
    record: DecisionRecord | None = None  # None when the alert failed
    error: str | None = None


@dataclass
class RunSummary:
    run_id: str
    stats: RunStats
    outcomes: list[AlertOutcome] = field(default_factory=list)
    stopped_early: bool = False


def triage_alert(db: Database, config: Config, alert: Alert, llm: ChatModel,
                 toolbox: Toolbox, run_id: str) -> DecisionRecord:
    """The whole chain for one alert. Raises on failure; the caller decides what to do."""
    pack = build_evidence(db, config, alert)
    result = investigate(alert, pack, llm, toolbox, config)
    record = finalize(result, alert, run_id, config, pack.text)
    ledger.record_decision(db, config, record)
    return record


def run_triage(db: Database, config: Config, llm: ChatModel, *, limit: int | None = None,
               include_decided: bool = False,
               on_alert: Callable[[int, int, AlertOutcome], None] | None = None) -> RunSummary:
    """Triage the queue and log the run. `on_alert(i, total, outcome)` reports progress."""
    ledger.ensure_agent_schema(db, config)
    alerts = fetch_alerts(db, config, include_decided=include_decided, limit=limit)
    run_id = ledger.start_run(db, config, config.model_id, PROMPT_VERSION)
    toolbox = Toolbox(agent_database(config, db), config, memory_db=db)
    summary = RunSummary(run_id=run_id, stats=RunStats())
    meter_start = (llm.meter.calls, llm.meter.input_tokens, llm.meter.output_tokens,
                   llm.meter.seconds)
    consecutive_failures = 0

    for i, alert in enumerate(alerts, start=1):
        summary.stats.alerts_attempted += 1
        try:
            outcome = AlertOutcome(alert, record=triage_alert(db, config, alert, llm, toolbox,
                                                              run_id))
            consecutive_failures = 0
            if outcome.record.decision == REVIEW_REQUIRED:
                summary.stats.alerts_review_required += 1
            else:
                summary.stats.alerts_decided += 1
        except Exception as exc:  # noqa: BLE001 — one bad alert must not stop the run
            outcome = AlertOutcome(alert, error=f"{type(exc).__name__}: {exc}")
            summary.stats.alerts_failed += 1
            consecutive_failures += 1
        summary.outcomes.append(outcome)
        if on_alert:
            on_alert(i, len(alerts), outcome)
        if consecutive_failures >= config.triage_max_consecutive_failures:
            summary.stopped_early = True
            break

    m = llm.meter
    summary.stats.model_calls = m.calls - meter_start[0]
    summary.stats.input_tokens = m.input_tokens - meter_start[1]
    summary.stats.output_tokens = m.output_tokens - meter_start[2]
    summary.stats.model_seconds = round(m.seconds - meter_start[3], 2)
    ledger.finish_run(db, config, run_id, summary.stats)
    return summary


def estimate_cost(config: Config, alerts: int, avg_steps: float = 2.5,
                  first_request_tokens: int = 2_400,
                  output_tokens_per_step: int = 1_500) -> tuple[float, float]:
    """Rough (low, high) USD for a run, before spending anything.

    Each step re-sends the conversation, so input grows per step; output includes
    thinking. The high end assumes every alert uses the full step budget.
    """
    def cost(steps: float) -> float:
        input_tokens = sum(first_request_tokens + 800 * k for k in range(round(steps)))
        output_tokens = output_tokens_per_step * steps
        return (input_tokens * config.input_usd_per_mtok
                + output_tokens * config.output_usd_per_mtok) / 1_000_000

    return round(alerts * cost(avg_steps), 2), round(alerts * cost(config.agent_max_steps), 2)
