"""Render the scorecard as Markdown. Deterministic: same inputs -> byte-identical file.

No wall-clock timestamps on purpose, so "reproducible from one command" is testable.
"""

from __future__ import annotations

from src.backtest.metrics import (
    HUMAN_DECISIONS,
    PREDICTED_DECISIONS,
    TARGETS,
    Rate,
    SliceMetrics,
    meets_target,
)
from src.backtest.render_review import render_disagreements
from src.backtest.scorecard import ALL, Scorecard
from src.backtest.split import DEV, HOLDOUT


def render_scorecard(card: Scorecard) -> str:
    sections = [_header(card), _headline(card), _dev_table(card), _confusion(card),
                _typology(card), _policy_and_grounding(card), _cost(card), _vendor_note(card),
                render_disagreements(card), _limits(card)]
    return "\n\n".join(s for s in sections if s).rstrip() + "\n"


# --- sections ------------------------------------------------------------------------------------

def _header(card: Scorecard) -> str:
    n_hold, n_dev = len(card.in_slice(HOLDOUT)), len(card.in_slice(DEV))
    lines = [f"# Backtest scorecard — {card.bank_name}"]
    if card.synthetic:
        lines += ["", ("> **SYNTHETIC DATA.** These numbers come from a generated bank. They show "
                      "the method works end to end; they are not evidence about real alerts and "
                      "must not appear in leadership material as results.")]
    lines += [
        "",
        (f"- Labelled alerts: {len(card.cases)} · decided by the agent: {len(card.decided)} · "
        f"not yet run: {len(card.not_run)}"),
        (f"- Split: **holdout {n_hold}** / dev {n_dev} — stratified by typology × analyst "
        f"decision, salt `{card.split_salt}`. Tune prompts on dev only; holdout is the "
        f"reported number."),
        (f"- Agent decisions from: {_names(card.run_ids)} · prompt {_names(card.prompt_versions)}"
        f" · model {_names(card.model_ids)}"),
    ]
    if card.run_filter:
        lines.append(f"- Restricted to run `{card.run_filter}`.")
    if len(card.prompt_versions) > 1:
        lines.append("- **WARNING: mixed prompt versions.** Re-run every alert on one version "
                     "before quoting these numbers.")
    if card.not_run:
        lines.append(f"- {len(card.not_run)} labelled alert(s) have no agent decision and are "
                     f"left out of every rate below.")
    return "\n".join(lines)


def _headline(card: Scorecard) -> str:
    agent, vendor = card.agent[HOLDOUT], card.vendor_metrics[HOLDOUT]
    rows = [
        ("Escalation recall", "escalation_recall", agent.escalation_recall,
         _rate(vendor.escalation_recall)),
        ("Dismissal precision", "dismissal_precision", agent.dismissal_precision,
         _rate(vendor.dismissal_precision)),
        ("Overall agreement", None, agent.agreement, _rate(vendor.agreement)),
        ("REVIEW_REQUIRED rate", "review_required", agent.review_required, "n/a (never defers)"),
        ("Stability (re-runs agree)", "stability", card.stability[HOLDOUT], "n/a"),
    ]
    lines = ["## Headline — holdout",
             "",
             f"| Metric | Target | Agent | {_vendor_label(card)} | Agent meets target? |",
             "|---|---|---|---|---|"]
    for label, key, rate, vendor_cell in rows:
        lines.append(f"| {label} | {_target(key)} | {_rate(rate)} | {vendor_cell} | "
                     f"{_status(key, rate)} |")
    lines += [
        "| Rationale quality (SME, 1–5) | mean ≥ 4 | pending SME scoring | n/a | — |",
        "| Agent right after SME adjudication | report | pending SME review | — | — |",
        "",
        ("Rates show hits/total and a 95% interval. Escalation recall counts REVIEW_REQUIRED as "
        "caught — handing a case to a human is safe; its cost shows in the REVIEW_REQUIRED rate."),
    ]
    return "\n".join(lines)


def _dev_table(card: Scorecard) -> str:
    agent, vendor = card.agent[DEV], card.vendor_metrics[DEV]
    return "\n".join([
        "## Dev slice (for tuning — not the reported number)",
        "",
        f"| Metric | Agent | {_vendor_label(card)} |",
        "|---|---|---|",
        (f"| Escalation recall | {_rate(agent.escalation_recall)} | "
        f"{_rate(vendor.escalation_recall)} |"),
        (f"| Dismissal precision | {_rate(agent.dismissal_precision)} | "
        f"{_rate(vendor.dismissal_precision)} |"),
        f"| Overall agreement | {_rate(agent.agreement)} | {_rate(vendor.agreement)} |",
        f"| REVIEW_REQUIRED rate | {_rate(agent.review_required)} | n/a |",
    ])


def _confusion(card: Scorecard) -> str:
    lines = ["## Confusion matrices — holdout", "",
             "Rows: the analyst's decision. Columns: the recommendation.", ""]
    lines += _matrix("Agent", card.agent[HOLDOUT], PREDICTED_DECISIONS)
    if card.vendor_metrics[HOLDOUT].n:
        lines += [""] + _matrix(_vendor_label(card), card.vendor_metrics[HOLDOUT],
                                HUMAN_DECISIONS)
    return "\n".join(lines)


def _typology(card: Scorecard) -> str:
    agent, vendor = card.agent[HOLDOUT].by_typology, card.vendor_metrics[HOLDOUT].by_typology
    lines = ["## Agreement by typology — holdout", "",
             f"| Typology | Agent | {_vendor_label(card)} |", "|---|---|---|"]
    for typology in sorted(set(agent) | set(vendor)):
        lines.append(f"| {typology} | {_rate(agent.get(typology, Rate(0, 0)))} | "
                     f"{_rate(vendor.get(typology, Rate(0, 0)))} |")
    return "\n".join(lines)


def _policy_and_grounding(card: Scorecard) -> str:
    x, agent = card.extras[HOLDOUT], card.agent[HOLDOUT]
    saved = x.threshold_holds - x.threshold_holds_human_dismiss
    return "\n".join([
        "## What the harness added — holdout",
        "",
        (f"- Agreement if the model's own call stood: {_rate(x.proposed_agreement)} · after "
        f"harness policy: {_rate(agent.agreement)}"),
        (f"- Dismissals held back by the confidence threshold: {x.threshold_holds} — the analyst "
        f"also dismissed {x.threshold_holds_human_dismiss} (extra review, no harm), "
        f"{saved} were not dismissals (risk avoided)"),
        (f"- Decisions with a grounding warning: {_rate(x.warning_rate)} — on agreements "
        f"{_rate(x.warnings_when_agreed)}, on disagreements {_rate(x.warnings_when_disagreed)}. "
        f"If warnings cluster on disagreements, promote them to routing (decision D33)."),
    ])


def _cost(card: Scorecard) -> str:
    x = card.extras[ALL]
    if not card.decided:
        return ""
    cost = (x.avg_input_tokens * card.input_usd_per_mtok
            + x.avg_output_tokens * card.output_usd_per_mtok) / 1_000_000
    return "\n".join([
        f"## Cost and speed — all {len(card.decided)} decided alerts",
        "",
        (f"- Model cost per alert: ${cost:,.3f} (avg {x.avg_input_tokens:,.0f} input / "
        f"{x.avg_output_tokens:,.0f} output tokens × configured price)"),
        (f"- Time per alert: mean {x.avg_seconds:,.1f}s · median {x.median_seconds:,.1f}s · "
        f"max {x.max_seconds:,.1f}s"),
    ])


def _vendor_note(card: Scorecard) -> str:
    v = card.vendor
    fit = (f"Thresholds fit on dev only, then applied unchanged: score ≥ {_cut(v.escalate_at)} "
           f"→ ESCALATE, ≥ {_cut(v.review_at)} → KYC_REVIEW, else DISMISS. "
           f"{v.scored} of {len(card.cases)} labelled alerts scored.")
    if v.is_standin:
        body = ("**STAND-IN, not a vendor.** A simple rules-engine score (customer risk rating, "
                "blocked attempts, amount) that exercises this column's code path. It is not "
                "evidence about any vendor product. At Ally, load the vendor's real scores into "
                "`labels.vendor_scores(alert_id, vendor_score)` and this column switches over.")
    else:
        body = "Source: vendor scores loaded into `labels.vendor_scores`."
    still_needed = ("Not shown here and still needed: onboarding time, licence + implementation "
                    "cost, data residency, explainability, validation burden.")
    lines = ["## Vendor column", "", body, "", fit, "", still_needed]
    return "\n".join(lines)


def _limits(card: Scorecard) -> str:
    return "\n".join([
        "## Limits",
        "",
        ("- Agreement with past analyst decisions, not ground-truth guilt — analysts can be "
        "wrong too; SME adjudication of the disagreements above is part of the result."),
        (f"- Small samples: {len(card.in_slice(HOLDOUT))} holdout alerts. Read the intervals, "
        f"not just the point estimates."),
        "- Performance can drift as typologies, rules, and customers change — re-run monthly.",
        "- The agent is a model under SR 11-7 and is validated like one.",
    ])


# --- formatting ----------------------------------------------------------------------------------

def _matrix(title: str, m: SliceMetrics, columns: tuple[str, ...]) -> list[str]:
    lines = [f"| {title} (n={m.n}) | " + " | ".join(columns) + " |",
             "|---|" + "---|" * len(columns)]
    for human in HUMAN_DECISIONS:
        cells = " | ".join(str(m.confusion.get((human, col), 0)) for col in columns)
        lines.append(f"| {human} | {cells} |")
    return lines


def _rate(rate: Rate) -> str:
    if rate.value is None:
        return "n/a (0 cases)"
    lo, hi = rate.interval
    return f"{rate.value:.0%} ({rate.hits}/{rate.total}; {lo:.0%}–{hi:.0%})"


def _target(key: str | None) -> str:
    if key is None:
        return "report"
    op, threshold = TARGETS[key]
    return f"{'≥' if op == '>=' else '≤'} {threshold:.0%}"


def _status(key: str | None, rate: Rate) -> str:
    if key is None:
        return "—"
    verdict = meets_target(key, rate)
    return "—" if verdict is None else ("yes" if verdict else "**NO**")


def _vendor_label(card: Scorecard) -> str:
    return "Vendor (STAND-IN)" if card.vendor.is_standin else "Vendor"


def _cut(value: float) -> str:
    return "never" if value == float("inf") else f"{value:.3f}"


def _names(values: list[str]) -> str:
    return ", ".join(f"`{v}`" for v in values) if values else "—"
