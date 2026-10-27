"""Tool definitions the model sees. Descriptions are prompt text — versioned like prompts.

Every tool is `strict: true`, so the model's arguments always match the schema
(additionalProperties false, every property required). Policy the schema can't
express (confidence range, non-empty rationale) is enforced by the validator.
"""

from typing import Any

from config import Config
from src.ledger import DECISIONS as _DECISIONS

TOOLS_VERSION = "tools_v1"
DECISIONS = list(_DECISIONS)  # single source of truth lives in the ledger
SUBMIT_DECISION = "submit_decision"


def tool_schemas(config: Config) -> list[dict[str, Any]]:
    """All tool definitions, in a fixed order (stable order keeps prompt caching effective)."""
    tools = [_run_sql(config.bank_schema), _describe_tables(config.bank_schema),
             _recall_case_memory(), _submit_decision()]
    if not config.tools_strict:  # escape hatch for an endpoint that rejects `strict`
        for tool in tools:
            tool.pop("strict")
    return tools


def _object(properties: dict[str, Any]) -> dict[str, Any]:
    return {"type": "object", "additionalProperties": False,
            "required": list(properties), "properties": properties}


def _run_sql(bank: str) -> dict[str, Any]:
    return {
        "name": "run_sql",
        "description": (
            f"Run one read-only SQL query against the bank's data and get up to 50 rows back. "
            f"Use it to check something the case file doesn't show — older activity, a "
            f"counterparty's full history, other accounts. Rules: a single SELECT (or WITH ... "
            f"SELECT); every table must be schema-qualified as {bank}.<table>; money columns "
            f"ending in _cents are integer USD cents (divide by 100). Call describe_tables if you "
            f"need table or column names. Errors come back as text — fix the query and retry."
        ),
        "strict": True,
        "input_schema": _object({
            "query": {"type": "string", "description": "One SELECT statement."},
            "purpose": {"type": "string",
                        "description": "One sentence: what you are checking and why. "
                                       "Logged for reviewers."},
        }),
    }


def _describe_tables(bank: str) -> dict[str, Any]:
    return {
        "name": "describe_tables",
        "description": (f"List the tables in the {bank} schema with their columns, types, and "
                        f"descriptions. Use before writing SQL if unsure of names."),
        "strict": True,
        "input_schema": _object({}),
    }


def _recall_case_memory() -> dict[str, Any]:
    return {
        "name": "recall_case_memory",
        "description": ("Recall this desk's own earlier triage decisions about a customer "
                        "(most recent first). Check it before deciding: a prior decision and "
                        "its reasoning is relevant context."),
        "strict": True,
        "input_schema": _object({
            "customer_id": {"type": "integer", "description": "The customer id."},
        }),
    }


def _submit_decision() -> dict[str, Any]:
    return {
        "name": SUBMIT_DECISION,
        "description": (
            "Record your triage decision for this alert. Call exactly once, when you have "
            "finished investigating; it ends the investigation. ESCALATE = recommend a SAR "
            "review; KYC_REVIEW = real signal but key information is missing, request it "
            "before deciding; DISMISS = the activity is explained by documented, legitimate "
            "behaviour. A human reviews every decision."
        ),
        "strict": True,
        "input_schema": _object({
            "decision": {"type": "string", "enum": DECISIONS},
            "confidence": {"type": "number",
                           "description": "0.0 to 1.0 — how sure you are of this decision."},
            "rationale": {"type": "string",
                          "description": "2-5 sentences an examiner could follow, citing the "
                                         "specific evidence (dates, amounts, notes)."},
            "key_facts": {"type": "array", "items": {"type": "string"},
                          "description": "2-6 short facts from the evidence the decision rests "
                                         "on, each with its date or amount."},
            "next_action": {"type": "string",
                            "description": "The concrete next step for a human."},
        }),
    }
