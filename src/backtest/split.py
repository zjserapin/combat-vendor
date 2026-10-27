"""Dev / holdout split: tune prompts on dev, report holdout.

Deterministic and order-free: an alert's slice depends only on its id, its
group, and the salt — not on row order, a random seed, or which backend ran
the query. Stratified by typology x human decision so each slice keeps the
same mix (a holdout with no escalations would make recall meaningless).
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from collections.abc import Iterable

DEV, HOLDOUT = "dev", "holdout"


def assign_slices(alerts: Iterable[tuple[str, str]], holdout_share: float,
                  salt: str) -> dict[str, str]:
    """(alert_id, stratum) pairs -> {alert_id: "dev" | "holdout"}."""
    if not 0 <= holdout_share <= 1:
        raise ValueError(f"holdout share must be between 0 and 1, got {holdout_share}")
    groups: dict[str, list[str]] = defaultdict(list)
    for alert_id, stratum in alerts:
        groups[stratum].append(alert_id)

    slices: dict[str, str] = {}
    for ids in groups.values():
        ranked = sorted(ids, key=lambda alert_id: _rank(alert_id, salt))
        n_holdout = int(len(ranked) * holdout_share + 0.5)  # round half up, not to even
        for i, alert_id in enumerate(ranked):
            slices[alert_id] = HOLDOUT if i < n_holdout else DEV
    return slices


def _rank(alert_id: str, salt: str) -> str:
    return hashlib.sha256(f"{salt}:{alert_id}".encode()).hexdigest()
