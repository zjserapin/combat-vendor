"""The SME review list: every disagreement, both rationales side by side.

Holdout and dev are listed apart on purpose — dev disagreements feed prompt
changes; holdout disagreements go to an SME and must never feed the prompt.
"""

from __future__ import annotations

from src.backtest.scorecard import Scorecard
from src.backtest.split import DEV, HOLDOUT


def render_disagreements(card: Scorecard) -> str:
    """Each disagreement with the analyst's and the agent's reasons side by side."""
    lines = ["## Disagreements for SME review",
             "",
             ("Alerts where the agent made a different call from the analyst, worst first. "
             "REVIEW_REQUIRED alerts are not listed — those already go to a human.")]
    for name, heading in ((HOLDOUT, "Holdout — adjudicate; do NOT use these to tune prompts"),
                          (DEV, "Dev — use these to improve the prompt")):
        found = card.disagreements[name]
        lines += ["", f"### {heading} ({len(found)})"]
        if not found:
            lines.append("\nNone.")
        for i, d in enumerate(found, start=1):
            c = d.case
            conf = f" ({c.confidence:.2f})" if c.confidence is not None else ""
            lines += ["", f"**{i}. `{c.alert_id}` — {c.typology} — {d.kind}**",
                      f"- Analyst: **{c.human_decision}** — {c.human_rationale or '(no rationale)'}",
                      f"- Agent: **{c.agent_decision}**{conf} — {_oneline(c.rationale)}"]
            if c.key_facts:
                lines.append(f"- Agent's key facts: {'; '.join(c.key_facts)}")
            if c.warnings:
                lines.append(f"- Grounding warnings: {'; '.join(c.warnings)}")
    return "\n".join(lines)


def _oneline(text: str) -> str:
    return " ".join(text.split()) or "(no rationale)"
