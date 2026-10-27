"""The impact board: what a triage run did, cost, and was worth — every number tagged."""

from src.report.metrics import ImpactBoard, compute_board, latest_run_id
from src.report.render import render_board

__all__ = ["ImpactBoard", "compute_board", "latest_run_id", "render_board"]
