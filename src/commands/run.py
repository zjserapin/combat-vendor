"""Run commands: work the queue, report what a run was worth, and backtest it."""

from pathlib import Path

from config import PROJECT_ROOT, Config


def triage(config: Config, limit: int | None, include_decided: bool, live: bool) -> int:
    """Without --live: show the plan and a cost estimate. With --live: run it (model calls)."""
    from src.alert_queue import fetch_alerts
    from src.db import Database
    from src.ledger import ensure_agent_schema
    from src.triage import estimate_cost, run_triage

    with Database(config) as db:
        ensure_agent_schema(db, config)
        alerts = fetch_alerts(db, config, include_decided=include_decided, limit=limit)
        low, high = estimate_cost(config, len(alerts))
        if not live:
            print(f"PLAN — no model calls. {len(alerts)} alert(s) to triage with "
                  f"{config.model_id} (effort {config.llm_effort}).")
            print(f"Estimated model cost: ${low:,.2f} typical, up to ${high:,.2f} if every alert "
                  f"uses all {config.agent_max_steps} steps "
                  f"(at ${config.input_usd_per_mtok}/${config.output_usd_per_mtok} per M tokens).")
            print("Re-run with --live to start.")
            return 0

        from src.llm_client import LLMClient

        config.validate()
        print(f"Triage {len(alerts)} alert(s) — estimated ${low:,.2f}–${high:,.2f}")
        summary = run_triage(db, config, LLMClient(config), limit=limit,
                             include_decided=include_decided, on_alert=_progress)

    s = summary.stats
    print(f"\nRun {summary.run_id}: {s.alerts_decided} decided, {s.alerts_review_required} to a "
          f"human, {s.alerts_failed} failed{' — STOPPED EARLY' if summary.stopped_early else ''}.")
    print("Next: python main.py report")
    return 1 if summary.stopped_early else 0


def report(config: Config, run_id: str | None, save: bool) -> int:
    """Print the impact board for a run (default: the latest finished run)."""
    from src.db import Database
    from src.report import compute_board, latest_run_id, render_board

    with Database(config, read_only=config.db_backend == "duckdb") as db:
        run_id = run_id or latest_run_id(db, config)
        if not run_id:
            print("No finished runs yet. Run `python main.py triage --live` first.")
            return 1
        text = render_board(compute_board(db, config, run_id), config.bank_name)

    print(text)
    if save:
        path = Path(PROJECT_ROOT) / "reports" / f"impact_board_{run_id}.md"
        path.parent.mkdir(exist_ok=True)
        path.write_text(text)
        print(f"Saved {path}")
    return 0


def backtest(config: Config, run_id: str | None) -> int:
    """Score agent decisions against analyst labels; write reports/scorecard.md."""
    from src.backtest import build_scorecard, render_scorecard
    from src.backtest.data import has_decisions
    from src.db import Database

    with Database(config, read_only=config.db_backend == "duckdb") as db:
        if not has_decisions(db, config):
            print("No agent decisions yet. Run `python main.py triage --all --live` first.")
            return 1
        card = build_scorecard(db, config, run_id)
    if not card.decided:
        print("No agent decisions on labelled alerts"
              f"{f' in run {run_id}' if run_id else ''}. Nothing to score.")
        return 1

    text = render_scorecard(card)
    print(text)
    path = Path(PROJECT_ROOT) / "reports" / "scorecard.md"
    path.parent.mkdir(exist_ok=True)
    path.write_text(text)
    print(f"Saved {path}")
    return 0


def _progress(i: int, total: int, outcome) -> None:
    a = outcome.alert
    if outcome.record is None:
        print(f"[{i}/{total}] {a.alert_id:<38} FAILED  {outcome.error}")
        return
    r = outcome.record
    conf = f"({r.confidence:.2f})" if r.confidence is not None else ""
    print(f"[{i}/{total}] {a.alert_id:<38} {r.decision:<16}{conf:<7} {r.steps} step(s) "
          f"{r.seconds:5.1f}s")
