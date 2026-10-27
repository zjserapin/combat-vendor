"""Inspection commands: look at what the agent sees and does, without recording anything."""

from config import Config


def queue(config: Config, limit: int | None, include_decided: bool) -> int:
    """Print the alert queue. Creates the agent schema if it doesn't exist yet."""
    from src.alert_queue import fetch_alerts
    from src.db import Database
    from src.ledger import ensure_agent_schema

    with Database(config) as db:
        ensure_agent_schema(db, config)
        alerts = fetch_alerts(db, config, include_decided=include_decided, limit=limit)

    window = f"{config.alert_lookback_days}d lookback to {config.as_of_date}"
    print(f"{len(alerts)} alert(s) awaiting decision ({window})\n")
    print(f"{'#':>3}  {'alert_id':<36} {'segment':<15} {'risk':>4} {'txns':>4} "
          f"{'amount':>13} {'blk':>3}  window")
    for i, a in enumerate(alerts, start=1):
        print(f"{i:>3}  {a.alert_id:<36} {a.segment:<15} {a.risk_rating:>4} "
              f"{a.flagged_txns:>4} {a.flagged_cents / 100:>13,.2f} {a.blocked_txns:>3}  "
              f"{a.window_start} -> {a.window_end}")
    return 0


def evidence(config: Config, alert_id: str | None) -> int:
    """Print the evidence pack for one alert (default: top of the queue). No model call."""
    from src.alert_queue import fetch_alerts, get_alert
    from src.db import Database
    from src.evidence import build_evidence

    with Database(config, read_only=True) as db:
        if alert_id:
            alert = get_alert(db, config, alert_id)
        else:
            top = fetch_alerts(db, config, include_decided=True, limit=1)
            alert = top[0] if top else None
        if alert is None:
            print(f"No alert found{f' with id {alert_id}' if alert_id else ''}.")
            return 1
        pack = build_evidence(db, config, alert)

    print(pack.text)
    print(f"-- fingerprint {pack.fingerprint} · ~{pack.approx_tokens:,} tokens")
    return 0


def agent_sql(config: Config, query: str) -> int:
    """Run one query through the agent's run_sql tool: guard, rollback, row cap."""
    from src.db import Database
    from src.tools import Toolbox, check_sql

    verdict = check_sql(query, config.bank_schema, config.snowflake_database)
    print(f"guard: {'ALLOWED ' + str(list(verdict.tables)) if verdict.ok else 'REJECTED'}")
    with Database(config) as db:
        result = Toolbox(db, config).run(
            {"id": "cli", "name": "run_sql", "input": {"query": query, "purpose": "cli"}})
    print(result["content"])
    return 1 if result.get("is_error") else 0


def investigate(config: Config, alert_id: str | None, live: bool) -> int:
    """Dry run: show exactly what the model would receive. --live: run the loop (model calls)."""
    import json

    from src.alert_queue import fetch_alerts, get_alert
    from src.db import Database, agent_database
    from src.evidence import build_evidence
    from src.ledger import ensure_agent_schema
    from src.prompts import PROMPT_VERSION, system_prompt, triage_instruction
    from src.tools import Toolbox

    with Database(config) as db:
        ensure_agent_schema(db, config)
        alert = get_alert(db, config, alert_id) if alert_id else \
            next(iter(fetch_alerts(db, config, include_decided=True, limit=1)), None)
        if alert is None:
            print("No alert found.")
            return 1
        pack = build_evidence(db, config, alert)
        toolbox = Toolbox(agent_database(config, db), config, memory_db=db)

        if not live:
            system = system_prompt(config.bank_name)
            first = triage_instruction(pack.text, config.agent_max_steps)
            tools = json.dumps(toolbox.schemas)
            print(f"DRY RUN — no model call. Alert {alert.alert_id}")
            print(f"model={config.model_id} effort={config.llm_effort} prompt={PROMPT_VERSION} "
                  f"budget={config.agent_max_steps} steps / {config.agent_max_seconds:.0f}s")
            print(f"tools: {', '.join(t['name'] for t in toolbox.schemas)}")
            print(f"~tokens per first request: system {len(system) // 4:,} + tools "
                  f"{len(tools) // 4:,} + case file {len(first) // 4:,}\n")
            print("===== SYSTEM =====\n" + system)
            print("===== FIRST USER MESSAGE =====\n" + first)
            return 0

        from src.agent import investigate
        from src.llm_client import LLMClient

        config.validate()
        result = investigate(alert, pack, LLMClient(config), toolbox, config)

    print(f"{result.alert_id}: {result.outcome} after {result.steps} step(s), "
          f"{result.seconds:.1f}s, {result.input_tokens:,} in / {result.output_tokens:,} out")
    for call in result.tool_calls:
        print(f"  tool {call.name} {'ERROR' if call.is_error else 'ok'} "
              f"{call.input.get('purpose', '')}")
    print(json.dumps(result.decision_input, indent=2) if result.decision_input
          else f"  review reason: {result.review_reason}")
    return 0
