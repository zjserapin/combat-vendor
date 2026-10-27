"""The alert queue: which cases need a decision, most urgent first.

Reads only the `alerts` view (the alert contract) and `customers`. It never
groups raw transactions itself — that's the view's job — so pointing the view
at a different alert source changes nothing here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from config import Config
from src import ledger
from src.db import Database, Row

# Priority: blocked attempts first (the bank's own controls already fired),
# then customer risk, then money at stake. alert_id last for a stable order.
QUEUE_SQL = """
WITH recent AS (
    SELECT alert_id, customer_id, typology, window_start, window_end,
           flagged_txns, flagged_cents, blocked_txns
    FROM {bank}.alerts
    WHERE window_end >= ?
      AND window_end <= ?
)
SELECT r.alert_id, r.customer_id, r.typology, r.window_start, r.window_end,
       r.flagged_txns, r.flagged_cents, r.blocked_txns,
       c.full_name AS customer_name, c.segment, c.risk_rating
FROM recent r
JOIN {bank}.customers c ON c.customer_id = r.customer_id
ORDER BY r.blocked_txns DESC, c.risk_rating DESC, r.flagged_cents DESC, r.alert_id
"""


ALERT_BY_ID_SQL = """
SELECT a.alert_id, a.customer_id, a.typology, a.window_start, a.window_end,
       a.flagged_txns, a.flagged_cents, a.blocked_txns,
       c.full_name AS customer_name, c.segment, c.risk_rating
FROM {bank}.alerts a
JOIN {bank}.customers c ON c.customer_id = a.customer_id
WHERE a.alert_id = ?
"""


@dataclass(frozen=True)
class Alert:
    """One case to triage: a customer, a typology, and the window of flagged activity."""

    alert_id: str
    customer_id: int
    typology: str
    window_start: date
    window_end: date
    flagged_txns: int
    flagged_cents: int
    blocked_txns: int
    customer_name: str
    segment: str
    risk_rating: int


def fetch_alerts(
    db: Database, config: Config, *, include_decided: bool = False, limit: int | None = None
) -> list[Alert]:
    """Alerts whose latest flagged activity is within the lookback window, in priority order.

    By default skips alerts the agent already decided — unless new flagged activity
    has arrived since (the alert's window_end moved past the decided window_end).
    Requires ledger.ensure_agent_schema() to have run when include_decided is False.
    """
    start = config.as_of_date - timedelta(days=config.alert_lookback_days)
    sql = QUEUE_SQL.format(bank=config.bank_schema)
    alerts = [_to_alert(row) for row in db.query(sql, [start, config.as_of_date])]
    if not include_decided:
        decided = ledger.decided_windows(db, config)
        alerts = [a for a in alerts if not _already_decided(a, decided)]
    return alerts[:limit] if limit else alerts


def get_alert(db: Database, config: Config, alert_id: str) -> Alert | None:
    """One alert by id, regardless of lookback or decision status."""
    rows = db.query(ALERT_BY_ID_SQL.format(bank=config.bank_schema), [alert_id])
    return _to_alert(rows[0]) if rows else None


def _already_decided(alert: Alert, decided: dict[str, date]) -> bool:
    last_window_end = decided.get(alert.alert_id)
    return last_window_end is not None and alert.window_end <= last_window_end


def _to_alert(row: Row) -> Alert:
    return Alert(
        alert_id=str(row["alert_id"]),
        customer_id=int(row["customer_id"]),
        typology=str(row["typology"]),
        window_start=_as_date(row["window_start"]),
        window_end=_as_date(row["window_end"]),
        flagged_txns=int(row["flagged_txns"]),
        flagged_cents=int(row["flagged_cents"]),
        blocked_txns=int(row["blocked_txns"]),
        customer_name=str(row["customer_name"]),
        segment=str(row["segment"]),
        risk_rating=int(row["risk_rating"]),
    )


def _as_date(value: Any) -> date:
    """Drivers differ: DuckDB returns date, some sources return datetime or ISO strings."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])
