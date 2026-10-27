"""The backtest: agent decisions vs. human labels, scored like a model validation."""

from src.backtest.render import render_scorecard
from src.backtest.scorecard import Scorecard, build_scorecard

__all__ = ["Scorecard", "build_scorecard", "render_scorecard"]
