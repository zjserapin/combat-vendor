"""Ask the model for one structured answer through one tool. No tool loop.

    reply = ask_for_tool(llm, system, prompt, tool_schema, max_tokens)
    if reply.input is None: ...  # reply.problem says why

The same exits as the investigator loop (D9, D26): refusal / cut-off -> no answer;
no tool call -> one nudge, then no answer. Model-API errors propagate (D28).
Reusable for any single-shot "fill in this form" call — a `core/` candidate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.agent import HAND_OFF_STOPS, ChatModel
from src.sar.prompts import nudge


@dataclass
class ToolReply:
    input: dict[str, Any] | None  # the tool arguments, unvalidated; None = no answer
    problem: str | None
    calls: int
    input_tokens: int
    output_tokens: int
    stop_reasons: list[str] = field(default_factory=list)


def ask_for_tool(llm: ChatModel, system: str, prompt: str, tool: dict[str, Any],
                 max_tokens: int) -> ToolReply:
    """One request (plus at most one nudge) that should end in exactly one `tool` call."""
    name = tool["name"]
    tokens_in, tokens_out = llm.meter.input_tokens, llm.meter.output_tokens
    messages: list[dict[str, Any]] = [{"role": "user", "content": prompt}]
    stops: list[str] = []

    def done(args: dict[str, Any] | None, problem: str | None) -> ToolReply:
        return ToolReply(args, problem, len(stops), llm.meter.input_tokens - tokens_in,
                         llm.meter.output_tokens - tokens_out, stops)

    for attempt in range(2):
        response = llm.create(messages, system=system, tools=[tool], max_tokens=max_tokens)
        stop = response.stop_reason or "unknown"
        stops.append(stop)
        if stop in HAND_OFF_STOPS:
            return done(None, HAND_OFF_STOPS[stop])
        calls = [b for b in response.content if b.type == "tool_use" and b.name == name]
        if len(calls) > 1:
            return done(None, f"model called {name} more than once")
        if calls:
            return done(dict(calls[0].input), None)
        if attempt == 0:
            # Append verbatim (thinking blocks included), then one plain-text reminder.
            messages.append({"role": "assistant", "content": response.content})
            messages.append({"role": "user", "content": nudge(name)})
    return done(None, f"model ended without calling {name}")
