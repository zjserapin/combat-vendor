"""SAR QC: rules first (free, deterministic), then optionally the model reviewer.

    qc = run_qc(narrative, facts, config)                 # rules only — no model call
    qc = run_qc(narrative, facts, config, llm=llm)        # rules + model reviewer

Works on any narrative — an agent draft or one an analyst wrote. The status never
means "file it": READY_FOR_REVIEW is the best outcome, and an analyst still decides.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from config import Config
from src.agent import ChatModel
from src.sar.facts import SarFacts
from src.sar.findings import ERROR, Finding, error
from src.sar.qc_facts import FACT_CHECKS
from src.sar.qc_language import language_checks
from src.sar.qc_review import ModelReview, model_review
from src.sar.records import NEEDS_REVISION, QC_INCOMPLETE, READY_FOR_REVIEW


@dataclass
class QcResult:
    findings: list[Finding]
    status: str
    review: ModelReview | None = None  # None = rules-only QC
    notes: list[str] = field(default_factory=list)

    def count(self, source: str, severity: str) -> int:
        return sum(1 for f in self.findings if f.source == source and f.severity == severity)

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == ERROR]


def run_rules(narrative: str, facts: SarFacts, config: Config,
              draft_problems: list[str] | None = None,
              cited_txn_ids: list[int] | None = None) -> list[Finding]:
    """Every deterministic check, in a fixed order."""
    findings = [error("draft_structure", "completeness", p) for p in draft_problems or []]
    if cited_txn_ids is not None:
        reported = {int(t["txn_id"]) for t in facts.transactions}
        findings += [error("cited_txn", "accuracy",
                           f"cited transaction {i} is not one of the reported transactions")
                     for i in cited_txn_ids if i not in reported]
    for check in FACT_CHECKS:
        findings += check(narrative, facts)
    findings += language_checks(narrative, config)
    return findings


def run_qc(narrative: str, facts: SarFacts, config: Config, llm: ChatModel | None = None,
           draft_problems: list[str] | None = None,
           cited_txn_ids: list[int] | None = None) -> QcResult:
    """Rules, then (if `llm` is given) the model reviewer, then one status."""
    findings = run_rules(narrative, facts, config, draft_problems, cited_txn_ids)
    review = model_review(narrative, facts, llm, config) if llm is not None else None
    if review is not None:
        findings += review.findings
    return QcResult(findings, _status(findings, review), review,
                    notes=review.problems if review else [])


def _status(findings: list[Finding], review: ModelReview | None) -> str:
    if any(f.severity == ERROR for f in findings):  # known problems win over an unknown
        return NEEDS_REVISION
    if review is not None and not review.completed:
        return QC_INCOMPLETE
    if review is not None and review.verdict == "REVISE":  # conservative: trust a REVISE
        return NEEDS_REVISION
    return READY_FOR_REVIEW
