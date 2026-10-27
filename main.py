"""combat-vendor CLI. Parses arguments and dispatches — logic lives in src/.

    python main.py doctor [--llm]                   check config, database, (optionally) the model
    python main.py seed                             build the synthetic Cairn Bank in DuckDB
    python main.py queue [--limit N] [--all]        show the alert queue, most urgent first
    python main.py evidence [ALERT_ID]              print the case file the model would see
    python main.py agent-sql "SELECT ..."           run SQL exactly as the agent would (guarded)
    python main.py investigate [ALERT_ID] [--live]  dry run by default; --live calls the model
    python main.py triage [--limit N] [--all] [--live]   plan + cost estimate; --live runs it
    python main.py report [--run RUN_ID] [--save]   impact board for a run (default: latest)
    python main.py backtest [--run RUN_ID]          scorecard vs. analyst labels -> reports/scorecard.md
"""

import argparse
import sys

from config import load_config
from src.commands import inspection, run, setup


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Cairn Bank AML investigator POC")
    sub = parser.add_subparsers(dest="command", required=True)

    doctor = sub.add_parser("doctor", help="check config, database, and model access")
    doctor.add_argument("--llm", action="store_true", help="also make one tiny model call")
    sub.add_parser("seed", help="build the synthetic Cairn Bank (DuckDB only)")

    queue = sub.add_parser("queue", help="show alerts awaiting a decision")
    queue.add_argument("--limit", type=int, default=None, help="show at most N alerts")
    queue.add_argument("--all", action="store_true", help="include alerts already decided")

    evidence = sub.add_parser("evidence", help="print the evidence pack for an alert")
    evidence.add_argument("alert_id", nargs="?", help="alert id (default: top of the queue)")

    agent_sql = sub.add_parser("agent-sql", help="run SQL through the agent's guarded tool")
    agent_sql.add_argument("query", help="one SELECT statement")

    inv = sub.add_parser("investigate", help="dry-run (default) or run the agent on one alert")
    inv.add_argument("alert_id", nargs="?", help="alert id (default: top of the queue)")
    inv.add_argument("--live", action="store_true", help="make real model calls")

    tri = sub.add_parser("triage", help="work the alert queue (plan by default)")
    tri.add_argument("--limit", type=int, default=None, help="triage at most N alerts")
    tri.add_argument("--all", action="store_true", help="include alerts already decided")
    tri.add_argument("--live", action="store_true", help="make real model calls")

    rep = sub.add_parser("report", help="impact board for a triage run")
    rep.add_argument("--run", dest="run_id", default=None, help="run id (default: latest)")
    rep.add_argument("--save", action="store_true", help="also write reports/<run>.md")

    bt = sub.add_parser("backtest", help="score agent decisions against analyst labels")
    bt.add_argument("--run", dest="run_id", default=None,
                    help="score only this run (default: latest decision per alert)")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    config = load_config()
    commands = {
        "doctor": lambda: setup.doctor(config, args.llm),
        "seed": lambda: setup.seed(config),
        "queue": lambda: inspection.queue(config, args.limit, args.all),
        "evidence": lambda: inspection.evidence(config, args.alert_id),
        "agent-sql": lambda: inspection.agent_sql(config, args.query),
        "investigate": lambda: inspection.investigate(config, args.alert_id, args.live),
        "triage": lambda: run.triage(config, args.limit, args.all, args.live),
        "report": lambda: run.report(config, args.run_id, args.save),
        "backtest": lambda: run.backtest(config, args.run_id),
    }
    return commands[args.command]()


if __name__ == "__main__":
    sys.exit(main())
