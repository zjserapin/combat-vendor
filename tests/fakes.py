"""Scripted stand-ins for the model. Real SDK types, zero network.

    llm = ScriptedLLM([reply(tool_use("run_sql", query=..., purpose=...)),
                       reply(decision("DISMISS"))])
"""

from __future__ import annotations

import itertools
from collections.abc import Callable
from typing import Any

from anthropic.types import Message, TextBlock, ToolUseBlock, Usage

from src.llm_client import Meter

_ids = itertools.count(1)
Step = Message | Callable[[list[dict[str, Any]]], Message] | Exception


def tool_use(name: str, **args: Any) -> ToolUseBlock:
    return ToolUseBlock(type="tool_use", id=f"toolu_{next(_ids):03d}", name=name, input=args)


def text(body: str) -> TextBlock:
    return TextBlock(type="text", text=body)


def decision(choice: str = "DISMISS", confidence: float = 0.85, **overrides: Any) -> ToolUseBlock:
    args = {"decision": choice, "confidence": confidence,
            "rationale": "Documented explanation on file matches the activity.",
            "key_facts": ["Note on file dated before the window."],
            "next_action": "Close the alert."}
    return tool_use("submit_decision", **{**args, **overrides})


def reply(*blocks: Any, stop: str | None = None, tokens: tuple[int, int] = (1_000, 200)) -> Message:
    """An assistant message. stop defaults to tool_use if any block is a tool call."""
    has_tool = any(getattr(b, "type", "") == "tool_use" for b in blocks)
    return Message(id=f"msg_{next(_ids):03d}", type="message", role="assistant",
                   model="claude-scripted", content=list(blocks),
                   stop_reason=stop or ("tool_use" if has_tool else "end_turn"),
                   stop_sequence=None, usage=Usage(input_tokens=tokens[0], output_tokens=tokens[1]))


class ScriptedLLM:
    """Replays a fixed script, one step per call. Records what each request contained.

    A step is a Message, a function of the messages so far, or an Exception to raise.
    """

    def __init__(self, script: list[Step]) -> None:
        self.script = list(script)
        self.meter = Meter()
        self.requests: list[dict[str, Any]] = []

    def create(self, messages: list[dict[str, Any]], system: str = "",
               tools: list[dict[str, Any]] | None = None, max_tokens: int = 16000) -> Message:
        self.requests.append({"messages": list(messages), "system": system,
                              "tools": [t["name"] for t in tools or []],
                              "max_tokens": max_tokens})
        if not self.script:
            raise AssertionError("ScriptedLLM ran out of script")
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        message = step(messages) if callable(step) else step
        self.meter.record(message, 0.01)
        return message


class RuleLLM:
    """Decides every alert in one turn by applying `rule` to the case file text.

    Lets a whole run go through the real pipeline with no network. Optionally fails
    (raises) for alerts whose case file contains any of `fail_on`.
    """

    def __init__(self, rule: Callable[[str], ToolUseBlock], fail_on: tuple[str, ...] = ()) -> None:
        self.rule, self.fail_on = rule, fail_on
        self.meter = Meter()

    def create(self, messages: list[dict[str, Any]], system: str = "",
               tools: list[dict[str, Any]] | None = None, max_tokens: int = 16000) -> Message:
        case_file = messages[0]["content"]
        if any(marker in case_file for marker in self.fail_on):
            raise ConnectionError("model endpoint unreachable")
        message = reply(self.rule(case_file))
        self.meter.record(message, 2.0)
        return message


def simple_rule(case_file: str) -> ToolUseBlock:
    """A crude stand-in policy for pipeline tests — not a model of good judgment."""
    if "Blocked attempts: 1" in case_file:
        return decision("ESCALATE", 0.9)
    if "## 10. KYC and operations notes" in case_file and "None on file." in case_file:
        return decision("KYC_REVIEW", 0.7)
    return decision("DISMISS", 0.8)
