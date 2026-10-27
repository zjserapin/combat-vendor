"""Row shapes for the synthetic Cairn Bank. One dataclass per table.

Field order matches the column order in schema.py, because rows are written
positionally to CSV and bulk-loaded.
"""

from dataclasses import dataclass
from datetime import date, datetime


@dataclass
class Customer:
    customer_id: int
    full_name: str
    segment: str  # RETAIL | SMALL_BUSINESS | PRIVATE
    occupation: str
    home_city: str
    home_country: str
    onboarded_date: date
    risk_rating: int  # 1-100, higher is riskier
    expected_monthly_cash_cents: int  # declared at onboarding (KYC "expected activity")
    kyc_refreshed_date: date


@dataclass
class Account:
    account_id: int
    customer_id: int
    account_type: str  # CHECKING | SAVINGS | BUSINESS_CHECKING
    opened_date: date
    balance_cents: int
    status: str


@dataclass
class Txn:
    account_id: int
    txn_ts: datetime
    amount_cents: int  # always positive; direction says which way it moved
    direction: str  # CREDIT | DEBIT
    channel: str  # BRANCH_CASH | ATM | WIRE | ACH | CARD
    counterparty: str
    counterparty_country: str
    txn_city: str
    txn_country: str
    status: str = "COMPLETED"  # COMPLETED | FLAGGED | BLOCKED
    flag_reason: str | None = None
    txn_id: int = 0  # assigned after all rows are sorted by time


@dataclass
class KycNote:
    customer_id: int
    note_date: date
    author: str
    note_text: str
    note_id: int = 0


@dataclass
class SarReport:
    customer_id: int
    typology: str
    status: str  # FILED
    filed_date: date
    narrative: str
    sar_id: int = 0


@dataclass
class JurisdictionRisk:
    country: str
    risk_tier: str  # LOW | MEDIUM | HIGH
    note: str


@dataclass
class Disposition:
    """Ground truth: what a human analyst decided. Lives in the labels schema."""

    alert_id: str  # same id the bank.alerts view produces — the backtest join key
    customer_id: int
    typology: str
    window_start: date
    window_end: date
    human_decision: str  # ESCALATE | KYC_REVIEW | DISMISS
    analyst_id: str
    decided_at: datetime
    handle_minutes: int
    human_rationale: str  # the analyst's written reason — shown next to the agent's
