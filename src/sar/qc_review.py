"""The model reviewer: an independent second read of the narrative (maker / checker).

It sees the narrative, the fact sheet, and the case file — not the drafter's notes or
reasoning. Its output is checked like any model output: a finding that "quotes"
words not in the narrative is downgraded, and a malformed review means QC did not
finish (never a silent pass).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from config import Config
from src.agent import ChatModel
from src.sar import prompts
from src.sar.facts import SarFacts
from src.sar.findings import ERROR, WARNING, Finding
from src.sar.schemas import CATEGORIES, QC_SCORES, QC_VERDICTS, SEVERITIES, qc_review_tool
from src.sar.structured_call import ask_for_tool


@dataclass
class ModelReview:
    findings: list[Finding] = field(default_factory=list)
    scores: dict[str, int] = field(default_factory=dict)
    verdict: str | None = None  # PASS | REVISE; None = the review did not complete
    summary: str = ""
    problems: list[str] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def completed(self) -> bool:
        return self.verdict is not None and not self.problems


def model_review(narrative: str, facts: SarFacts, llm: ChatModel, config: Config) -> ModelReview:
    """One review call. Model-API errors propagate (a failure to retry, not a verdict)."""
    reply = ask_for_tool(llm, prompts.qc_system(config.bank_name),
                         prompts.qc_instruction(narrative, facts.text, facts.pack.text),
                         qc_review_tool(config), config.agent_max_tokens)
    review = parse_review(reply.input, narrative) if reply.input is not None else \
        ModelReview(problems=[reply.problem or "no review"])
    review.input_tokens, review.output_tokens = reply.input_tokens, reply.output_tokens
    return review


def parse_review(raw: dict[str, Any], narrative: str) -> ModelReview:
    """Validate the reviewer's tool arguments. Collects every problem."""
    review = ModelReview(summary=str(raw.get("summary") or "").strip())
    verdict = raw.get("verdict")
    if verdict in QC_VERDICTS:
        review.verdict = verdict
    else:
        review.problems.append(f"verdict {verdict!r} is not one of {', '.join(QC_VERDICTS)}")

    scores = raw.get("scores")
    for name in QC_SCORES:
        value = scores.get(name) if isinstance(scores, dict) else None
        if isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 5:
            review.scores[name] = value
        else:
            review.problems.append(f"score {name}={value!r} is not an integer from 1 to 5")

    findings = raw.get("findings")
    if not isinstance(findings, list):
        review.problems.append("findings must be a list")
        findings = []
    for item in findings:
        finding = _finding(item, narrative)
        if finding is None:
            review.problems.append(f"malformed finding: {item!r}"[:200])
        else:
            review.findings.append(finding)
    return review


def _finding(item: Any, narrative: str) -> Finding | None:
    if not isinstance(item, dict) or item.get("severity") not in SEVERITIES \
            or item.get("category") not in CATEGORIES or not str(item.get("problem", "")).strip():
        return None
    excerpt = str(item.get("excerpt") or "").strip()
    message = str(item["problem"]).strip()
    if item.get("fix"):
        message += f" Fix: {str(item['fix']).strip()}"
    severity = item["severity"]
    if excerpt and " ".join(excerpt.split()) not in " ".join(narrative.split()):
        # The reviewer quoted words that aren't there: keep it visible, don't let it block.
        severity = WARNING if severity == ERROR else severity
        message = "(quoted text not found in the narrative) " + message
    return Finding("model", severity, item["category"], message, excerpt, source="model")
