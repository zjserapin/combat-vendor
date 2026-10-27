"""The Toolbox: tool schemas for the model, dispatch for the harness, and a call log.

    toolbox = Toolbox(agent_db, config, memory_db=harness_db)
    llm.create(messages, tools=toolbox.schemas)
    results = [toolbox.run(block) for block in tool_use_blocks]   # tool_result blocks

`run` never raises: every failure becomes a tool_result with is_error=True and
a message the model can act on.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

from config import Config
from src.db import Database
from src.tools import handlers
from src.tools.handlers import ToolError
from src.tools.schemas import SUBMIT_DECISION, tool_schemas

MAX_ROWS = 50
MAX_CHARS = 6_000


@dataclass
class ToolCall:
    """One tool invocation, kept for the per-alert trace."""

    tool_use_id: str
    name: str
    input: dict[str, Any]
    is_error: bool
    output: str
    millis: float


@dataclass
class Toolbox:
    db: Database  # agent-scoped reads (SELECT-only role at Ally)
    config: Config
    memory_db: Database | None = None  # agent schema reads; defaults to db
    max_rows: int = MAX_ROWS
    max_chars: int = MAX_CHARS
    calls: list[ToolCall] = field(default_factory=list)
    _catalog: str | None = field(default=None, repr=False)

    @property
    def schemas(self) -> list[dict[str, Any]]:
        return tool_schemas(self.config)

    @staticmethod
    def is_terminal(name: str) -> bool:
        """Calling this tool ends the investigation."""
        return name == SUBMIT_DECISION

    def run(self, block: Any) -> dict[str, Any]:
        """Execute one tool_use block (SDK object or dict) and return its tool_result block."""
        tool_use_id, name, args = _unpack(block)
        started = time.monotonic()
        try:
            output, is_error = self._dispatch(name, args), False
        except ToolError as exc:
            output, is_error = str(exc), True
        except Exception as exc:  # noqa: BLE001 — a tool failure must never crash the loop
            output, is_error = f"Tool failed: {type(exc).__name__}: {exc}", True
        self.calls.append(ToolCall(tool_use_id, name, args, is_error, output,
                                   round((time.monotonic() - started) * 1000, 1)))
        result: dict[str, Any] = {"type": "tool_result", "tool_use_id": tool_use_id,
                                  "content": output}
        if is_error:
            result["is_error"] = True
        return result

    def _dispatch(self, name: str, args: dict[str, Any]) -> str:
        if name == "run_sql":
            return handlers.run_sql(self.db, self.config, args.get("query"),
                                    self.max_rows, self.max_chars)
        if name == "describe_tables":
            if self._catalog is None:
                self._catalog = handlers.describe_tables(self.db, self.config)
            return self._catalog
        if name == "recall_case_memory":
            return handlers.recall_case_memory(self.memory_db or self.db, self.config,
                                               args.get("customer_id"))
        if name == SUBMIT_DECISION:
            return handlers.acknowledge_decision()
        raise ToolError(f"Unknown tool '{name}'. Available: "
                        f"{', '.join(s['name'] for s in self.schemas)}.")


def _unpack(block: Any) -> tuple[str, str, dict[str, Any]]:
    """Accept SDK ToolUseBlock objects or plain dicts (tests, recorded transcripts)."""
    if isinstance(block, dict):
        tool_use_id, name, args = block.get("id", ""), block.get("name", ""), block.get("input")
    else:
        tool_use_id, name, args = block.id, block.name, block.input
    if isinstance(args, str):  # defensive: some transports deliver JSON text
        try:
            args = json.loads(args)
        except json.JSONDecodeError:
            args = {}
    return str(tool_use_id), str(name), args if isinstance(args, dict) else {}
