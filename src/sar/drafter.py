"""The drafter: fact sheet in, a sectioned narrative draft out.

    draft = draft_narrative(facts, llm, config)                        # first draft
    draft = draft_narrative(facts, llm, config, previous=draft, findings=qc.findings)

The model owns the prose; the harness owns the structure — it validates every
section, assembles them in a fixed order, and appends the fixed closing paragraph.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from config import Config
from src.agent import ChatModel
from src.sar import prompts
from src.sar.schemas import SECTIONS, sar_narrative_tool
from src.sar.structured_call import ask_for_tool

if TYPE_CHECKING:
    from src.sar.facts import SarFacts
    from src.sar.findings import Finding

MIN_SECTION_CHARS = 40  # anything shorter is a stub, not a section


@dataclass
class SarDraft:
    """One draft attempt. `problems` (e.g. a stub section) become QC errors to revise."""

    sections: dict[str, str]
    cited_txn_ids: list[int]
    drafting_notes: str
    narrative: str
    problems: list[str] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    seconds: float = 0.0

    @property
    def usable(self) -> bool:
        """False when there is no narrative at all: nothing to QC, an analyst writes it."""
        return bool(self.narrative)


def draft_narrative(facts: SarFacts, llm: ChatModel, config: Config,
                    previous: SarDraft | None = None,
                    findings: list[Finding] | None = None) -> SarDraft:
    """Ask the model for a draft (or a revision of `previous` that fixes `findings`)."""
    started = time.monotonic()
    prompt = prompts.draft_instruction(
        facts.text, facts.triage_text, facts.pack.text,
        findings=format_findings(findings) if findings else None,
        previous=previous.narrative if previous else None,
    )
    reply = ask_for_tool(llm, prompts.drafter_system(config.bank_name), prompt,
                         sar_narrative_tool(config), config.agent_max_tokens)
    draft = parse_draft(reply.input, closing(config)) if reply.input is not None else \
        SarDraft({}, [], "", "", problems=[reply.problem or "no draft"])
    draft.input_tokens, draft.output_tokens = reply.input_tokens, reply.output_tokens
    draft.seconds = round(time.monotonic() - started, 3)
    return draft


def parse_draft(raw: dict[str, Any], closing_text: str) -> SarDraft:
    """Validate the tool arguments and assemble the narrative. Collects every problem."""
    problems: list[str] = []
    sections: dict[str, str] = {}
    for name, covers in SECTIONS:
        value = raw.get(name)
        text = value.strip() if isinstance(value, str) else ""
        if len(text) < MIN_SECTION_CHARS:
            problems.append(f"section '{name}' is missing or too short ({covers})")
        sections[name] = text

    cited = raw.get("cited_txn_ids")
    if not isinstance(cited, list) or not all(isinstance(i, int) and not isinstance(i, bool)
                                              for i in cited):
        problems.append("cited_txn_ids must be a list of integers")
        cited = []
    notes = raw.get("drafting_notes")
    notes = notes.strip() if isinstance(notes, str) else ""

    body = [sections[name] for name, _ in SECTIONS if sections[name]]
    narrative = "\n\n".join([*body, closing_text]) if body else ""
    return SarDraft(sections, list(cited), notes, narrative, problems)


def closing(config: Config) -> str:
    """Fixed boilerplate the model never writes, so it can never get it wrong."""
    return (f"Supporting documentation for this report is retained by {config.bank_name} "
            f"{config.sar_records_office} and is available to law enforcement and regulators "
            f"upon request.")


def format_findings(findings: list[Finding]) -> str:
    """QC findings as a numbered list the drafter can work through."""
    lines = []
    for i, f in enumerate(findings, 1):
        quote = f' — "{f.excerpt}"' if f.excerpt else ""
        lines.append(f"{i}. [{f.severity}] {f.message}{quote}")
    return "\n".join(lines)
