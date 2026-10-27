"""SAR commands: draft a narrative for an escalated alert, and QC any narrative."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from config import Config

if TYPE_CHECKING:
    from src.sar.qc import QcResult


def sar(config: Config, alert_id: str | None, live: bool, force: bool) -> int:
    """Without --live: show the fact sheet and the plan. With --live: draft -> QC -> revise."""
    from src.alert_queue import get_alert
    from src.db import Database
    from src.ledger import ensure_agent_schema
    from src.sar import NotEscalated, build_sar_facts, prepare_sar
    from src.sar.drafter import closing

    with Database(config) as db:
        ensure_agent_schema(db, config)
        alert_id = alert_id or _latest_escalation(db, config)
        alert = get_alert(db, config, alert_id) if alert_id else None
        if alert is None:
            print(f"No alert {alert_id!r}." if alert_id else
                  "No escalated alerts yet. Name one: python main.py sar ALERT_ID")
            return 1
        if not live:
            facts = build_sar_facts(db, config, alert)
            calls = 2 * (config.sar_max_revisions + 1)
            print(facts.text + "\n" + facts.triage_text + "\n")
            print(f"Fixed closing paragraph: {closing(config)}\n")
            print(f"PLAN — no model calls. Draft + QC review per attempt, up to "
                  f"{config.sar_max_revisions + 1} attempt(s) = at most {calls} calls "
                  f"(plus one nudge each if needed) with {config.model_id}. Fact sheet "
                  f"~{len(facts.text) // 4:,} tokens, case file ~{facts.pack.approx_tokens:,}.")
            print("Re-run with --live to draft.")
            return 0

        from src.llm_client import LLMClient

        config.validate()
        try:
            outcome = prepare_sar(db, config, alert, LLMClient(config),
                                  require_escalation=not force)
        except NotEscalated as exc:
            print(f"{exc}. Use --force to draft anyway.")
            return 1

    for attempt in outcome.attempts:
        print(f"--- attempt {attempt.record.attempt}: {attempt.record.status}")
        if attempt.qc:
            print(format_qc(attempt.qc))
    final = outcome.final
    print(f"\n=== DRAFT ({final.record.status}) — an analyst reviews and decides ===\n")
    print(final.draft.narrative or "(no narrative)")
    if final.draft.drafting_notes:
        print(f"\nNotes for the analyst: {final.draft.drafting_notes}")
    return 0


def sar_qc(config: Config, alert_id: str, path: str, live: bool) -> int:
    """QC a narrative file against an alert's records. Rules only unless --live."""
    from src.alert_queue import get_alert
    from src.db import Database
    from src.ledger import ensure_agent_schema
    from src.sar import build_sar_facts, run_qc

    narrative = Path(path).read_text()
    with Database(config) as db:
        ensure_agent_schema(db, config)
        alert = get_alert(db, config, alert_id)
        if alert is None:
            print(f"No alert {alert_id!r}.")
            return 1
        facts = build_sar_facts(db, config, alert)

    llm = None
    if live:
        from src.llm_client import LLMClient

        config.validate()
        llm = LLMClient(config)
    qc = run_qc(narrative, facts, config, llm=llm)
    mode = "rules + model reviewer" if live else "rules only (add --live for the model reviewer)"
    print(f"QC of {path} against alert {alert_id} — {mode}\n")
    print(format_qc(qc))
    return 0 if not qc.errors else 2


def format_qc(qc: QcResult) -> str:
    """Findings grouped errors-first, one line each."""
    lines = [(f"Status: {qc.status} — {len(qc.errors)} error(s), "
             f"{len(qc.findings) - len(qc.errors)} warning(s)")]
    for f in sorted(qc.findings, key=lambda f: (f.severity != "ERROR", f.category, f.check)):
        quote = f'  «{f.excerpt}»' if f.excerpt else ""
        lines.append(f"  {f.severity:<7} {f.category:<12} [{f.source}:{f.check}] {f.message}{quote}")
    if qc.review is not None:
        lines.append(f"  Reviewer scores: {qc.review.scores or '-'}; {qc.review.summary}")
    lines += [f"  QC note: {n}" for n in qc.notes]
    return "\n".join(lines)


def _latest_escalation(db, config: Config) -> str | None:
    from src.ledger import latest_decisions

    escalated = [d for d in latest_decisions(db, config) if d["decision"] == "ESCALATE"]
    return escalated[0]["alert_id"] if escalated else None
