"""The investigator loop: one alert in, one decision (or a hand-off to a human) out.

    result = investigate(alert, pack, llm, toolbox, config)

Each pass is one model call. The loop stops when the model calls submit_decision,
or when a budget or failure mode ends it — and every exit that is not a clean
decision becomes REVIEW_REQUIRED, never a guess. Model-API errors are *not*
caught here: an unreachable model is a run failure (retry later), not a decision.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from anthropic.types import Message

from config import Config
from src.alert_queue import Alert
from src.evidence import EvidencePack
from src.llm_client import Meter
from src.prompts import (
    FINAL_STEP_NOTICE_V1,
    NUDGE_V1,
    PROMPT_VERSION,
    system_prompt,
    triage_instruction,
)
from src.tools import SUBMIT_DECISION, Toolbox, ToolCall

DECIDED = "DECIDED"
REVIEW_REQUIRED = "REVIEW_REQUIRED"
_HAND_OFF_STOPS = {
    "refusal": "model declined to answer (refusal)",
    "max_tokens": "model response was cut off (max_tokens)",
    "model_context_window_exceeded": "conversation exceeded the model's context window",
}


class ChatModel(Protocol):
    """What the loop needs from a model client. LLMClient satisfies it; so do test fakes."""

    meter: Meter

    def create(self, messages: list[dict[str, Any]], system: str = "",
               tools: list[dict[str, Any]] | None = None, max_tokens: int = 16000) -> Message: ...


@dataclass
class AgentResult:
    """What happened on one alert. Block 6 turns this into a decision record."""

    alert_id: str
    outcome: str  # DECIDED | REVIEW_REQUIRED
    decision_input: dict[str, Any] | None  # raw submit_decision arguments, unvalidated
    review_reason: str | None
    steps: int
    seconds: float
    input_tokens: int
    output_tokens: int
    prompt_version: str
    evidence_hash: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    stop_reasons: list[str] = field(default_factory=list)
    messages: list[dict[str, Any]] = field(default_factory=list, repr=False)


def investigate(alert: Alert, pack: EvidencePack, llm: ChatModel, toolbox: Toolbox,
                config: Config, clock: Callable[[], float] = time.monotonic) -> AgentResult:
    """Run the bounded tool-use loop for one alert."""
    run = _Run(alert, pack, llm, toolbox, config.agent_max_steps, clock)
    messages: list[dict[str, Any]] = [
        {"role": "user", "content": triage_instruction(pack.text, config.agent_max_steps)}
    ]
    system = system_prompt(config.bank_name)
    nudged = False

    for step in range(1, config.agent_max_steps + 1):
        if clock() - run.started > config.agent_max_seconds:
            return run.hand_off(messages, "time budget exhausted")
        response = llm.create(messages, system=system, tools=toolbox.schemas,
                              max_tokens=config.agent_max_tokens)
        run.steps, stop = step, response.stop_reason or "unknown"
        run.stop_reasons.append(stop)
        if stop in _HAND_OFF_STOPS:
            return run.hand_off(messages, _HAND_OFF_STOPS[stop])

        # Append verbatim: content carries thinking blocks the next turn needs. Append-only.
        messages.append({"role": "assistant", "content": response.content})
        calls = [b for b in response.content if b.type == "tool_use"]
        decisions = [c for c in calls if c.name == SUBMIT_DECISION]

        if len(decisions) > 1:
            return run.hand_off(messages, "model submitted more than one decision")
        if decisions:
            return run.decided(messages, dict(decisions[0].input))
        if not calls:
            if nudged:
                return run.hand_off(messages, "model ended without calling submit_decision")
            nudged = True
            messages.append({"role": "user", "content": _with_notice([], NUDGE_V1, step, run)})
            continue

        # All results for this turn go back in ONE user message, in call order.
        results = [toolbox.run(c) for c in calls]
        messages.append({"role": "user", "content": _with_notice(results, None, step, run)})

    return run.hand_off(messages, "step budget exhausted")


def _with_notice(blocks: list[dict[str, Any]], text: str | None, step: int,
                 run: _Run) -> list[dict[str, Any]]:
    """Tool results first (API rule), then any guidance text — incl. the last-step warning."""
    notes = [text] if text else []
    if step == run.max_steps - 1:
        notes.append(FINAL_STEP_NOTICE_V1)
    return [*blocks, *({"type": "text", "text": n} for n in notes)]


class _Run:
    """Per-alert bookkeeping: budgets, meter deltas, and the two ways to finish."""

    def __init__(self, alert: Alert, pack: EvidencePack, llm: ChatModel, toolbox: Toolbox,
                 max_steps: int, clock: Callable[[], float]) -> None:
        self.alert, self.pack, self.llm, self.toolbox = alert, pack, llm, toolbox
        self.max_steps, self.clock = max_steps, clock
        self.started = clock()
        self.steps = 0
        self.stop_reasons: list[str] = []
        self._tokens_in, self._tokens_out = llm.meter.input_tokens, llm.meter.output_tokens
        self._first_call = len(toolbox.calls)

    def decided(self, messages: list[dict[str, Any]], decision: dict[str, Any]) -> AgentResult:
        return self._result(messages, DECIDED, decision, None)

    def hand_off(self, messages: list[dict[str, Any]], reason: str) -> AgentResult:
        return self._result(messages, REVIEW_REQUIRED, None, reason)

    def _result(self, messages: list[dict[str, Any]], outcome: str,
                decision: dict[str, Any] | None, reason: str | None) -> AgentResult:
        return AgentResult(
            alert_id=self.alert.alert_id,
            outcome=outcome,
            decision_input=decision,
            review_reason=reason,
            steps=self.steps,
            seconds=round(self.clock() - self.started, 3),
            input_tokens=self.llm.meter.input_tokens - self._tokens_in,
            output_tokens=self.llm.meter.output_tokens - self._tokens_out,
            prompt_version=PROMPT_VERSION,
            evidence_hash=self.pack.fingerprint,
            tool_calls=self.toolbox.calls[self._first_call:],
            stop_reasons=self.stop_reasons,
            messages=messages,
        )
