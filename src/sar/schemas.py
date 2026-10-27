"""Tool definitions for the SAR drafter and the QC reviewer. Descriptions are prompt text.

Both are strict, so arguments always match the shape; ranges and non-empty checks
the schema can't express are enforced in Python (drafter.py, qc_review.py).
"""

from typing import Any

from config import Config

SUBMIT_SAR_NARRATIVE = "submit_sar_narrative"
SUBMIT_QC_REVIEW = "submit_qc_review"

# (field, what it must cover) — the order the harness assembles the narrative in.
SECTIONS: tuple[tuple[str, str], ...] = (
    ("introduction", "why the report is filed, subject, activity, dates, total amount"),
    ("subject", "who: name, customer ID, occupation, location, tenure, accounts"),
    ("activity", "what / when / where / how, chronological, completed vs attempted"),
    ("basis_for_suspicion", "why: departure from profile and history, nothing explains it"),
    ("investigation", "what the Bank reviewed and actions already taken"),
)
SEVERITIES = ("ERROR", "WARNING")
CATEGORIES = ("accuracy", "completeness", "consistency", "clarity", "objectivity", "mechanics")
QC_SCORES = ("accuracy", "completeness", "clarity", "objectivity")
QC_VERDICTS = ("PASS", "REVISE")


def _object(properties: dict[str, Any]) -> dict[str, Any]:
    return {"type": "object", "additionalProperties": False,
            "required": list(properties), "properties": properties}


def _maybe_strict(tool: dict[str, Any], config: Config) -> dict[str, Any]:
    if config.tools_strict:
        tool["strict"] = True
    return tool


def sar_narrative_tool(config: Config) -> dict[str, Any]:
    sections = {name: {"type": "string", "description": covers} for name, covers in SECTIONS}
    return _maybe_strict({
        "name": SUBMIT_SAR_NARRATIVE,
        "description": ("Submit the SAR narrative draft, one field per section. Call exactly "
                        "once. An analyst reviews it; nothing is filed automatically."),
        "input_schema": _object({
            **sections,
            "cited_txn_ids": {"type": "array", "items": {"type": "integer"},
                              "description": "Transaction IDs from the fact sheet the "
                                             "narrative relies on."},
            "drafting_notes": {"type": "string",
                               "description": "For the analyst, not the narrative: missing "
                                              "information, judgement calls. Empty if none."},
        }),
    }, config)


def qc_review_tool(config: Config) -> dict[str, Any]:
    finding = _object({
        "severity": {"type": "string", "enum": list(SEVERITIES)},
        "category": {"type": "string", "enum": list(CATEGORIES)},
        "excerpt": {"type": "string",
                    "description": "Exact words from the narrative; empty if something is "
                                   "missing."},
        "problem": {"type": "string", "description": "What is wrong."},
        "fix": {"type": "string", "description": "How to fix it."},
    })
    scores = _object({name: {"type": "integer", "description": "1 (poor) to 5 (examiner-ready)"}
                      for name in QC_SCORES})
    return _maybe_strict({
        "name": SUBMIT_QC_REVIEW,
        "description": "Submit the QC review of the SAR narrative. Call exactly once.",
        "input_schema": _object({
            "findings": {"type": "array", "items": finding},
            "scores": scores,
            "verdict": {"type": "string", "enum": list(QC_VERDICTS)},
            "summary": {"type": "string", "description": "One or two sentences overall."},
        }),
    }, config)
