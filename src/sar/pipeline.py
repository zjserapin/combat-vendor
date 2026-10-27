"""Draft -> QC -> revise (bounded) -> log. One escalated alert in, a reviewed draft out.

    outcome = prepare_sar(db, config, alert, llm)

The kickback loop, automated: QC errors go back to the drafter as a numbered list,
at most SAR_MAX_REVISIONS times. Every attempt is logged (append-only), so the
analyst — and an examiner — can see each version and why it was sent back.
Nothing is filed: the best status is READY_FOR_REVIEW.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field

from config import Config
from src import ledger
from src.agent import ChatModel
from src.alert_queue import Alert
from src.db import Database
from src.ledger import utc_now
from src.sar.drafter import SarDraft, draft_narrative
from src.sar.facts import SarFacts, build_sar_facts
from src.sar.prompts import QC_PROMPT_VERSION, SAR_PROMPT_VERSION
from src.sar.qc import QcResult, run_qc
from src.sar.records import NEEDS_REVISION, NO_DRAFT, SarDraftRecord, SarFindingRecord


class NotEscalated(ValueError):
    """The alert's latest triage decision isn't ESCALATE, so no SAR is drafted."""


@dataclass
class SarAttempt:
    draft: SarDraft
    qc: QcResult | None  # None when there was no narrative to check
    record: SarDraftRecord


@dataclass
class SarOutcome:
    facts: SarFacts
    sar_run_id: str
    attempts: list[SarAttempt] = field(default_factory=list)

    @property
    def final(self) -> SarAttempt:
        return self.attempts[-1]


def prepare_sar(db: Database, config: Config, alert: Alert, llm: ChatModel,
                require_escalation: bool = True) -> SarOutcome:
    """Draft and QC a SAR narrative for one alert; log every attempt."""
    ledger.ensure_agent_schema(db, config)
    facts = build_sar_facts(db, config, alert)
    if require_escalation and (facts.triage is None or facts.triage["decision"] != "ESCALATE"):
        found = facts.triage["decision"] if facts.triage else "no triage decision"
        raise NotEscalated(f"alert {alert.alert_id}: latest decision is {found}, not ESCALATE")

    outcome = SarOutcome(facts, sar_run_id=f"sar-{utc_now():%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:6]}")
    previous: SarDraft | None = None
    qc: QcResult | None = None
    for attempt in range(1, config.sar_max_revisions + 2):
        draft = draft_narrative(facts, llm, config, previous=previous,
                                findings=qc.findings if qc else None)
        qc = run_qc(draft.narrative, facts, config, llm=llm, draft_problems=draft.problems,
                    cited_txn_ids=draft.cited_txn_ids) if draft.usable else None
        record = _record(outcome, attempt, draft, qc, config)
        ledger.record_sar_draft(db, config, record, _finding_records(record, qc))
        outcome.attempts.append(SarAttempt(draft, qc, record))
        if qc is None or qc.status != NEEDS_REVISION:
            break
        previous = draft
    return outcome


def _record(outcome: SarOutcome, attempt: int, draft: SarDraft, qc: QcResult | None,
            config: Config) -> SarDraftRecord:
    facts, review = outcome.facts, qc.review if qc else None
    return SarDraftRecord(
        draft_id=uuid.uuid4().hex,
        sar_run_id=outcome.sar_run_id,
        alert_id=facts.alert.alert_id,
        customer_id=facts.alert.customer_id,
        attempt=attempt,
        status=qc.status if qc else NO_DRAFT,
        narrative=draft.narrative,
        sections=json.dumps(draft.sections),
        cited_txn_ids=json.dumps(draft.cited_txn_ids),
        drafting_notes=draft.drafting_notes,
        draft_problems=json.dumps(draft.problems),
        facts_hash=facts.fingerprint,
        evidence_hash=facts.pack.fingerprint,
        triage_run_id=facts.triage["run_id"] if facts.triage else None,
        model_id=config.model_id,
        sar_prompt_version=SAR_PROMPT_VERSION,
        qc_prompt_version=QC_PROMPT_VERSION,
        rule_errors=qc.count("rule", "ERROR") if qc else 0,
        rule_warnings=qc.count("rule", "WARNING") if qc else 0,
        model_errors=qc.count("model", "ERROR") if qc else 0,
        model_warnings=qc.count("model", "WARNING") if qc else 0,
        qc_scores=json.dumps(review.scores if review else {}),
        qc_summary=review.summary if review else "",
        input_tokens=draft.input_tokens + (review.input_tokens if review else 0),
        output_tokens=draft.output_tokens + (review.output_tokens if review else 0),
        seconds=draft.seconds,
    )


def _finding_records(record: SarDraftRecord, qc: QcResult | None) -> list[SarFindingRecord]:
    if qc is None:
        return []
    return [SarFindingRecord(draft_id=record.draft_id, alert_id=record.alert_id,
                             attempt=record.attempt, source=f.source, check_name=f.check,
                             severity=f.severity, category=f.category, message=f.message,
                             excerpt=f.excerpt)
            for f in qc.findings]
