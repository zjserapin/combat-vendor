"""Render the impact board as Markdown — readable in a terminal, pasteable into a deck."""

from __future__ import annotations

from src.report.metrics import DECISION_ORDER, ImpactBoard

MEASURED, POLICY, ASSUMPTION = "[measured]", "[policy]", "[ASSUMPTION]"


def render_board(board: ImpactBoard, bank_name: str) -> str:
    b = board
    lines = [
        f"# Impact board — {bank_name} AML triage run",
        (f"Run `{b.run_id}` · model `{b.model_id}` · prompt `{b.prompt_version}` · "
        f"{b.started_at:%Y-%m-%d %H:%M} to {b.finished_at:%H:%M} UTC"),
        "",
        ("Every figure is tagged: **[measured]** from the logs, **[policy]** a choice the bank "
        "makes, **[ASSUMPTION]** an estimate used only where no measurement exists."),
        "",
        "## Work done",
        (f"- Alerts attempted: {b.alerts_attempted} · completed: {b.decided} · failed (to retry): "
        f"{b.alerts_failed} {MEASURED}"),
        "",
        "| Disposition | Alerts | Share | Flagged exposure | Avg confidence |",
        "|---|---|---|---|---|",
    ]
    for decision in DECISION_ORDER:
        n = b.decisions.get(decision, 0)
        share = f"{n / b.decided:.0%}" if b.decided else "-"
        conf = f"{b.avg_confidence[decision]:.2f}" if decision in b.avg_confidence else "-"
        lines.append(f"| {decision} | {n} | {share} | ${b.exposure_cents.get(decision, 0) / 100:,.0f}"
                     f" | {conf} |")
    lines.append(f"\n{MEASURED} from the decision log.")

    lines += ["", "## Handed to a human (REVIEW_REQUIRED)"]
    if b.review_reasons:
        lines += [f"- {reason}: {n}" for reason, n in b.review_reasons.most_common()]
        lines.append(f"- of which proposed DISMISS held back by the confidence threshold: "
                     f"{b.routed_by_threshold} {POLICY}")
    else:
        lines.append("- none")

    lines += [
        "", "## Cost and speed",
        (f"- Model calls: {b.model_calls:,} · tokens in/out: {b.input_tokens:,} / "
        f"{b.output_tokens:,} {MEASURED}"),
        (f"- Model cost: ${b.cost_usd:,.2f} total, ${_per(b.cost_usd, b.decided):,.3f} per alert "
        f"{MEASURED} (tokens × configured price)"),
        (f"- Model time: {b.model_seconds / 60:,.1f} min total, {_per(b.model_seconds, b.decided):,.0f}"
        f" s per alert {MEASURED}"),
        (f"- Tool lookups: {b.tool_calls} ({b.tool_errors} returned errors the agent handled) "
        f"{MEASURED}"),
        "",
        "## Analyst time",
    ]
    tag = MEASURED if b.handle_time_source == "measured" else ASSUMPTION
    if b.handle_time_source != "measured":
        lines.append(f"- Handle time not recorded for every alert: using "
                     f"{b.manual_minutes_assumption:.0f} min per alert {ASSUMPTION}")
    lines += [
        f"- Analyst handle time for these alerts: {b.handle_minutes_all / 60:,.1f} h {tag}",
        f"- Of which on alerts the agent dismissed: {b.handle_minutes_dismissed / 60:,.1f} h {tag}",
        f"- Dismissals still re-reviewed by a human: {b.review_sample_rate:.0%} sample {POLICY}",
        (f"- **Analyst hours returned if dismissals are accepted: {b.hours_returned:,.1f} h** "
        f"{tag}+{POLICY}"),
        ("- These hours count only if the backtest shows the agent's dismissals agree with "
         "analysts' — a wrong dismissal is risk, not savings."),
        "",
        "## Quality signals (not accuracy)",
        (f"- Decisions citing an amount or date not found in the evidence: "
        f"{b.decisions_with_warnings} of {b.decided} {MEASURED} — each is shown to the reviewer"),
        "- Accuracy against human decisions is measured separately in the backtest.",
        "",
        "## What the agent did not do",
        "- File, close, or change anything: every disposition is a recommendation a human signs.",
        "- Write to bank data: its queries run read-only and are rolled back.",
        "- See analyst decisions: the labels schema is outside its reach.",
    ]
    return "\n".join(lines) + "\n"


def _per(total: float, count: int) -> float:
    return total / count if count else 0.0
