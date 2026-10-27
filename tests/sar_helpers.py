"""Test helpers for Blocks 11-12: a clean narrative built straight from a fact sheet,
and scripted drafter / reviewer replies."""

from __future__ import annotations

from typing import Any

from anthropic.types import ToolUseBlock

from config import Config
from src.evidence.render import as_date, as_datetime, money
from src.sar.drafter import closing
from src.sar.facts import SarFacts, txn_day
from src.sar.facts_render import location, outcome
from src.sar.schemas import QC_SCORES, SECTIONS
from tests.fakes import tool_use


def clean_sections(facts: SarFacts, config: Config) -> dict[str, str]:
    """A plain but correct narrative: every fact copied from the sheet."""
    s, home = facts.subject, facts.subject["home_country"]
    accounts = ", ".join(f"{a['account_type'].lower()} account {a['account_id']}"
                         for a in facts.accounts)
    txns = []
    for t in facts.transactions:
        kind = "An attempted" if t["status"] == "BLOCKED" else "A"
        txns.append(f"On {txn_day(t)} at {as_datetime(t['txn_ts']):%H:%M}, {kind.lower()} "
                    f"{t['channel'].lower()} transaction of {money(t['amount_cents'])} took "
                    f"place with {location(t['counterparty'], t['counterparty_country'], home)} "
                    f"in {location(t['txn_city'], t['txn_country'], home)} ({outcome(t)}).")
    txns = [t[0].upper() + t[1:] for t in txns]
    return {
        "introduction": (f"{config.bank_name} is filing this report on suspicious activity by "
                         f"its customer {s['full_name']}. Between {facts.first_date} and "
                         f"{facts.last_date}, activity totalling {money(facts.total_cents)} took "
                         f"place on the customer's accounts."),
        "subject": (f"{s['full_name']} (customer ID {s['customer_id']}) lives in "
                    f"{s['home_city']}, {s['home_country']}, and has been a customer since "
                    f"{as_date(s['onboarded_date'])}. The activity took place on {accounts}."),
        "activity": " ".join(txns),
        "basis_for_suspicion": ("The activity departs from the customer's profile and history "
                                "with the Bank, and no documentation on file explains it."),
        "investigation": ("The Bank reviewed the customer's account history, profile, and "
                          "KYC records before preparing this report."),
    }


def clean_narrative(facts: SarFacts, config: Config) -> str:
    sections = clean_sections(facts, config)
    return "\n\n".join([*(sections[name] for name, _ in SECTIONS), closing(config)])


def narrative_call(sections: dict[str, str], cited: list[int] | None = None,
                   notes: str = "") -> ToolUseBlock:
    return tool_use("submit_sar_narrative", **sections, cited_txn_ids=cited or [],
                    drafting_notes=notes)


def review_call(findings: list[dict[str, Any]] | None = None, verdict: str = "PASS",
                score: int = 4) -> ToolUseBlock:
    return tool_use("submit_qc_review", findings=findings or [],
                    scores={name: score for name in QC_SCORES}, verdict=verdict,
                    summary="Reviewed.")
