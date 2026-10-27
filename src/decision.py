"""The validator: the model owns the judgment, the harness owns the vocabulary.

    record = finalize(result, alert, run_id, config, pack_text)
    ledger.record_decision(db, config, record)

`finalize` always returns a DecisionRecord whose decision is one of ESCALATE,
KYC_REVIEW, DISMISS, or REVIEW_REQUIRED — never anything else, never a guess.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import Any

from config import Config
from src.agent import DECIDED, AgentResult
from src.alert_queue import Alert
from src.grounding import ungrounded_facts
from src.ledger import DECISIONS, REVIEW_REQUIRED, DecisionRecord

MAX_RATIONALE_CHARS = 5_000
MAX_KEY_FACTS = 10


@dataclass
class Validation:
    ok: bool
    problems: list[str] = field(default_factory=list)
    decision: str | None = None
    confidence: float | None = None
    rationale: str = ""
    key_facts: list[str] = field(default_factory=list)
    next_action: str = ""


def validate_decision(raw: dict[str, Any] | None) -> Validation:
    """Check submit_decision arguments. Collects every problem, not just the first."""
    if not isinstance(raw, dict):
        return Validation(ok=False, problems=["no decision was submitted"])
    problems: list[str] = []

    decision = raw.get("decision")
    if decision not in DECISIONS:
        problems.append(f"decision {decision!r} is not one of {', '.join(DECISIONS)}")

    confidence = raw.get("confidence")
    if isinstance(confidence, bool) or not isinstance(confidence, int | float) \
            or math.isnan(confidence) or not 0.0 <= confidence <= 1.0:
        problems.append(f"confidence {confidence!r} is not a number between 0 and 1")
        confidence = None

    rationale = raw.get("rationale")
    if not isinstance(rationale, str) or not rationale.strip():
        problems.append("rationale is missing")
        rationale = ""
    elif len(rationale) > MAX_RATIONALE_CHARS:
        problems.append(f"rationale is longer than {MAX_RATIONALE_CHARS} characters")

    key_facts = raw.get("key_facts")
    if not isinstance(key_facts, list) or not key_facts \
            or not all(isinstance(f, str) and f.strip() for f in key_facts):
        problems.append("key_facts must be a non-empty list of non-empty strings")
        key_facts = []
    elif len(key_facts) > MAX_KEY_FACTS:
        problems.append(f"more than {MAX_KEY_FACTS} key_facts")

    next_action = raw.get("next_action")
    if not isinstance(next_action, str) or not next_action.strip():
        problems.append("next_action is missing")
        next_action = ""

    return Validation(
        ok=not problems, problems=problems,
        decision=decision if isinstance(decision, str) else None,
        confidence=float(confidence) if confidence is not None else None,
        rationale=rationale.strip(), key_facts=[f.strip() for f in key_facts],
        next_action=next_action.strip(),
    )


def finalize(result: AgentResult, alert: Alert, run_id: str, config: Config,
             pack_text: str) -> DecisionRecord:
    """Turn an agent run into the record that goes in the decision log."""
    check = validate_decision(result.decision_input) if result.outcome == DECIDED else None
    decision, reason = _route(result, check, config)

    sources = [pack_text, *(c.output for c in result.tool_calls)]
    claims = [check.rationale, *check.key_facts] if check else []
    warnings = ungrounded_facts(claims, sources)

    return DecisionRecord.for_alert(
        alert, run_id,
        decision=decision,
        confidence=check.confidence if check else None,
        rationale=check.rationale if check else "",
        next_action=check.next_action if check else "",
        proposed_decision=check.decision if check else None,
        key_facts=json.dumps(check.key_facts if check else []),
        review_reason=reason,
        warnings=json.dumps(warnings),
        tool_trace=json.dumps(_trace(result)),
        model_id=config.model_id,
        prompt_version=result.prompt_version,
        steps=result.steps,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        seconds=result.seconds,
        evidence_hash=result.evidence_hash,
    )


def _route(result: AgentResult, check: Validation | None, config: Config) -> tuple[str, str | None]:
    """Final disposition and, if a human must look, why."""
    if check is None:
        return REVIEW_REQUIRED, result.review_reason or "agent did not finish"
    if not check.ok:
        return REVIEW_REQUIRED, "validator: " + "; ".join(check.problems)
    if check.decision == "DISMISS" and (check.confidence or 0.0) < config.dismiss_min_confidence:
        return REVIEW_REQUIRED, (f"DISMISS at confidence {check.confidence:.2f} is below the "
                                 f"{config.dismiss_min_confidence:.2f} threshold for dismissals")
    return str(check.decision), None


def _trace(result: AgentResult) -> list[dict[str, Any]]:
    """Compact, reviewer-friendly record of what the agent looked up."""
    return [{"tool": c.name, "purpose": c.input.get("purpose", ""),
             "query": c.input.get("query", ""), "error": c.is_error, "ms": c.millis}
            for c in result.tool_calls if c.name != "submit_decision"]
