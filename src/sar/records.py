"""Rows for agent.sar_drafts and agent.sar_qc_findings. Written only by src.ledger."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from src.ledger import utc_now

# Draft statuses. None of them means "file it": an analyst always decides.
READY_FOR_REVIEW = "READY_FOR_REVIEW"  # QC found no errors; an analyst reviews and decides
NEEDS_REVISION = "NEEDS_REVISION"  # QC found errors the revision budget didn't fix
QC_INCOMPLETE = "QC_INCOMPLETE"  # the model reviewer didn't finish; quality unknown
NO_DRAFT = "NO_DRAFT"  # the drafter produced nothing usable; an analyst writes it
STATUSES = (READY_FOR_REVIEW, NEEDS_REVISION, QC_INCOMPLETE, NO_DRAFT)


@dataclass
class SarDraftRecord:
    """One draft attempt for one alert, with its QC outcome."""

    draft_id: str
    sar_run_id: str
    alert_id: str
    customer_id: int
    attempt: int  # 1 = first draft, 2+ = revisions after QC
    status: str
    narrative: str
    sections: str  # JSON object, section name -> text
    cited_txn_ids: str  # JSON array
    drafting_notes: str  # gaps the drafter flagged for the analyst
    draft_problems: str  # JSON array — why a draft was unusable, if it was
    facts_hash: str  # fingerprint of the fact sheet the draft was written from
    evidence_hash: str
    triage_run_id: str | None
    model_id: str
    sar_prompt_version: str
    qc_prompt_version: str
    rule_errors: int = 0
    rule_warnings: int = 0
    model_errors: int = 0
    model_warnings: int = 0
    qc_scores: str = ""  # JSON object from the model reviewer
    qc_summary: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    seconds: float = 0.0
    created_at: datetime = field(default_factory=utc_now)


@dataclass
class SarFindingRecord:
    """One QC finding on one draft attempt."""

    draft_id: str
    alert_id: str
    attempt: int
    source: str  # rule | model
    check_name: str
    severity: str  # ERROR | WARNING
    category: str
    message: str
    excerpt: str
    created_at: datetime = field(default_factory=utc_now)
