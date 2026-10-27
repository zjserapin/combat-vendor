"""Assemble the scorecard: load -> split -> vendor column -> score each slice.

    card = build_scorecard(db, config)          # latest agent decision per alert
    card = build_scorecard(db, config, run_id)  # only one run's decisions
"""

from __future__ import annotations

from dataclasses import dataclass, field

from config import Config
from src.backtest import metrics
from src.backtest.data import BacktestCase, load_cases, load_repeats
from src.backtest.metrics import AgentExtras, Disagreement, Rate, SliceMetrics
from src.backtest.split import DEV, HOLDOUT, assign_slices
from src.backtest.vendor import VendorColumn, attach_vendor
from src.db import Database

SLICES = (HOLDOUT, DEV)
ALL = "all"


@dataclass
class Scorecard:
    bank_name: str
    synthetic: bool  # local generated bank: numbers exercise the method, they prove nothing
    split_salt: str
    holdout_share: float
    cases: list[BacktestCase]
    vendor: VendorColumn
    input_usd_per_mtok: float = 0.0
    output_usd_per_mtok: float = 0.0
    run_filter: str | None = None
    agent: dict[str, SliceMetrics] = field(default_factory=dict)
    extras: dict[str, AgentExtras] = field(default_factory=dict)
    vendor_metrics: dict[str, SliceMetrics] = field(default_factory=dict)
    stability: dict[str, Rate] = field(default_factory=dict)
    disagreements: dict[str, list[Disagreement]] = field(default_factory=dict)

    @property
    def decided(self) -> list[BacktestCase]:
        return [c for c in self.cases if c.decided]

    @property
    def not_run(self) -> list[BacktestCase]:
        return [c for c in self.cases if not c.decided]

    @property
    def run_ids(self) -> list[str]:
        return sorted({c.run_id for c in self.decided})

    @property
    def prompt_versions(self) -> list[str]:
        return sorted({c.prompt_version for c in self.decided})

    @property
    def model_ids(self) -> list[str]:
        return sorted({c.model_id for c in self.decided})

    def in_slice(self, name: str) -> list[BacktestCase]:
        return [c for c in self.cases if c.slice == name]


def build_scorecard(db: Database, config: Config, run_id: str | None = None) -> Scorecard:
    """Everything the scorecard shows, computed from the labels and the decision log."""
    cases = load_cases(db, config, run_id)
    slices = assign_slices(((c.alert_id, f"{c.typology}|{c.human_decision}") for c in cases),
                           config.backtest_holdout_share, config.backtest_split_salt)
    for case in cases:
        case.slice = slices[case.alert_id]

    card = Scorecard(bank_name=config.bank_name, synthetic=config.db_backend == "duckdb",
                     split_salt=config.backtest_split_salt,
                     holdout_share=config.backtest_holdout_share, cases=cases,
                     vendor=attach_vendor(db, config, cases), run_filter=run_id,
                     input_usd_per_mtok=config.input_usd_per_mtok,
                     output_usd_per_mtok=config.output_usd_per_mtok)
    repeats = load_repeats(db, config)
    for name in SLICES:
        part = card.in_slice(name)
        card.agent[name] = metrics.score_agent(part)
        card.extras[name] = metrics.agent_extras(part)
        card.vendor_metrics[name] = metrics.score_vendor(part)
        card.stability[name] = metrics.stability(repeats, (c.alert_id for c in part))
        card.disagreements[name] = metrics.disagreements(part)
    card.extras[ALL] = metrics.agent_extras(cases)  # cost and speed don't depend on the split
    return card
