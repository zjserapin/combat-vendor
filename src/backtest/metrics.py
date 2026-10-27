"""Scorecard metrics — pure functions over backtest cases, no database.

Definitions follow docs/05-evaluation-plan.md. Every rate keeps its numerator
and denominator, plus a 95% interval: on a few dozen alerts, "100%" can mean
"somewhere between 70% and 100%", and a validator will ask.
"""

from __future__ import annotations

import math
import statistics
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from src.backtest.data import BacktestCase, Repeat

ESCALATE, KYC_REVIEW, DISMISS, REVIEW_REQUIRED = (
    "ESCALATE", "KYC_REVIEW", "DISMISS", "REVIEW_REQUIRED")
HUMAN_DECISIONS = (ESCALATE, KYC_REVIEW, DISMISS)
PREDICTED_DECISIONS = (*HUMAN_DECISIONS, REVIEW_REQUIRED)
SEVERITY = {ESCALATE: 2, KYC_REVIEW: 1, DISMISS: 0}

# Starting targets from the evaluation plan — to be agreed with AML and MRM.
TARGETS: dict[str, tuple[str, float]] = {
    "escalation_recall": (">=", 0.95),
    "dismissal_precision": (">=", 0.90),
    "review_required": ("<=", 0.20),
    "stability": (">=", 0.90),
}


@dataclass(frozen=True)
class Rate:
    """hits / total, with a Wilson 95% interval. total == 0 means "not measurable"."""

    hits: int
    total: int

    @property
    def value(self) -> float | None:
        return self.hits / self.total if self.total else None

    @property
    def interval(self) -> tuple[float, float] | None:
        return wilson_interval(self.hits, self.total) if self.total else None


@dataclass
class SliceMetrics:
    """Accuracy of one decision-maker (agent or vendor) on one slice of alerts."""

    n: int
    confusion: Counter = field(default_factory=Counter)  # (human, predicted) -> count
    agreement: Rate = Rate(0, 0)
    escalation_recall: Rate = Rate(0, 0)
    dismissal_precision: Rate = Rate(0, 0)
    review_required: Rate = Rate(0, 0)
    by_typology: dict[str, Rate] = field(default_factory=dict)


@dataclass
class AgentExtras:
    """Agent-only measures: what the harness policy did, grounding, cost, speed."""

    proposed_agreement: Rate  # agreement if the model's own call stood (before policy)
    threshold_holds: int  # dismissals routed to a human by the confidence threshold
    threshold_holds_human_dismiss: int  # ...where the analyst also dismissed (cost of the policy)
    warning_rate: Rate
    warnings_when_agreed: Rate
    warnings_when_disagreed: Rate
    avg_input_tokens: float
    avg_output_tokens: float
    avg_seconds: float
    median_seconds: float
    max_seconds: float


@dataclass(frozen=True)
class Disagreement:
    case: BacktestCase
    kind: str  # "MISSED ESCALATION" | "under-called" | "over-called"


# --- core ----------------------------------------------------------------------------------------

def score_slice(rows: Iterable[tuple[str, str, str]]) -> SliceMetrics:
    """(typology, human_decision, predicted_decision) rows -> accuracy metrics.

    Escalation recall counts REVIEW_REQUIRED as caught: routing to a human is safe.
    Its cost shows up in the REVIEW_REQUIRED rate instead.
    """
    rows = list(rows)
    confusion = Counter((human, predicted) for _, human, predicted in rows)
    by_type: dict[str, list[bool]] = {}
    for typology, human, predicted in rows:
        by_type.setdefault(typology, []).append(human == predicted)

    human_esc = [p for _, h, p in rows if h == ESCALATE]
    agent_dismiss = [h for _, h, p in rows if p == DISMISS]
    return SliceMetrics(
        n=len(rows),
        confusion=confusion,
        agreement=Rate(sum(h == p for _, h, p in rows), len(rows)),
        escalation_recall=Rate(sum(p in (ESCALATE, REVIEW_REQUIRED) for p in human_esc),
                               len(human_esc)),
        dismissal_precision=Rate(sum(h == DISMISS for h in agent_dismiss), len(agent_dismiss)),
        review_required=Rate(sum(p == REVIEW_REQUIRED for _, _, p in rows), len(rows)),
        by_typology={t: Rate(sum(v), len(v)) for t, v in sorted(by_type.items())},
    )


def score_agent(cases: Sequence[BacktestCase]) -> SliceMetrics:
    """Agent accuracy over the cases it has decided."""
    return score_slice((c.typology, c.human_decision, c.agent_decision)
                       for c in cases if c.decided)


def score_vendor(cases: Sequence[BacktestCase]) -> SliceMetrics:
    return score_slice((c.typology, c.human_decision, c.vendor_decision)
                       for c in cases if c.vendor_decision is not None)


def agent_extras(cases: Sequence[BacktestCase]) -> AgentExtras:
    """Policy effect, grounding, cost, and latency over the cases the agent decided."""
    decided = [c for c in cases if c.decided]
    held = [c for c in decided if _held_by_threshold(c)]
    agreed = [c for c in decided if c.agent_decision == c.human_decision]
    disagreed = [c for c in decided if c.agent_decision != c.human_decision]
    seconds = [c.seconds for c in decided] or [0.0]
    return AgentExtras(
        proposed_agreement=Rate(sum(_model_call(c) == c.human_decision for c in decided),
                                len(decided)),
        threshold_holds=len(held),
        threshold_holds_human_dismiss=sum(c.human_decision == DISMISS for c in held),
        warning_rate=_warned(decided),
        warnings_when_agreed=_warned(agreed),
        warnings_when_disagreed=_warned(disagreed),
        avg_input_tokens=_mean(c.input_tokens for c in decided),
        avg_output_tokens=_mean(c.output_tokens for c in decided),
        avg_seconds=_mean(seconds),
        median_seconds=statistics.median(seconds),
        max_seconds=max(seconds),
    )


def stability(repeats: Sequence[Repeat], alert_ids: Iterable[str]) -> Rate:
    """Share of re-run alerts (identical prompt + evidence) that got the same decision."""
    wanted = set(alert_ids)
    scoped = [r for r in repeats if r.alert_id in wanted]
    return Rate(sum(r.stable for r in scoped), len(scoped))


def disagreements(cases: Sequence[BacktestCase]) -> list[Disagreement]:
    """Alerts where the agent made a call that differs from the analyst's, worst first.

    REVIEW_REQUIRED is not a disagreement — the agent handed the case to a human.
    """
    found = [Disagreement(c, _kind(c.human_decision, c.agent_decision)) for c in cases
             if c.decided and c.agent_decision not in (c.human_decision, REVIEW_REQUIRED)]
    order = {"MISSED ESCALATION": 0, "under-called": 1, "over-called": 2}
    return sorted(found, key=lambda d: (order[d.kind], d.case.typology, d.case.alert_id))


def meets_target(metric: str, rate: Rate) -> bool | None:
    """True/False against the starting target; None if there's nothing to measure."""
    if rate.value is None:
        return None
    op, threshold = TARGETS[metric]
    return rate.value >= threshold if op == ">=" else rate.value <= threshold


def wilson_interval(hits: int, total: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson score interval — sensible at small n and at 0% / 100%."""
    p = hits / total
    denom = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denom
    half = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


# --- helpers -------------------------------------------------------------------------------------

def _model_call(case: BacktestCase) -> str | None:
    """The model's own decision; falls back to the final one if it never proposed any."""
    return case.proposed_decision or case.agent_decision


def _held_by_threshold(case: BacktestCase) -> bool:
    return (case.agent_decision == REVIEW_REQUIRED and case.proposed_decision == DISMISS
            and "threshold" in case.review_reason.lower())


def _warned(cases: Sequence[BacktestCase]) -> Rate:
    return Rate(sum(bool(c.warnings) for c in cases), len(cases))


def _kind(human: str, agent: str | None) -> str:
    if human == ESCALATE and agent == DISMISS:
        return "MISSED ESCALATION"
    return "under-called" if SEVERITY[agent] < SEVERITY[human] else "over-called"


def _mean(values: Iterable[float]) -> float:
    values = list(values)
    return sum(values) / len(values) if values else 0.0
