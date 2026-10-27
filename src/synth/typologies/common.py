"""Building blocks shared by every planted typology."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum

from src.synth.background import make_account, make_customer, make_txn, normal_activity
from src.synth.context import SeedContext
from src.synth.models import Account, Customer, KycNote, SarReport, Txn


class Variant(StrEnum):
    """How a planted case *should* be decided. This is the ground truth design."""

    TRUE_POSITIVE = "TRUE_POSITIVE"  # real suspicious activity
    AMBIGUOUS = "AMBIGUOUS"  # real signal, missing information
    BENIGN = "BENIGN"  # rule fired, but a documented explanation exists


HUMAN_DECISION = {
    Variant.TRUE_POSITIVE: "ESCALATE",
    Variant.AMBIGUOUS: "KYC_REVIEW",
    Variant.BENIGN: "DISMISS",
}


@dataclass
class PlantedCase:
    customer: Customer
    account: Account
    typology: str
    variant: Variant
    txns: list[Txn] = field(default_factory=list)
    notes: list[KycNote] = field(default_factory=list)
    sars: list[SarReport] = field(default_factory=list)

    # --- helpers the typology modules call ---
    def add(self, ctx: SeedContext, day: date, amount: int, direction: str, channel: str,
            counterparty: str, city: str | None = None, country: str = "United States",
            cp_country: str = "United States", ts: datetime | None = None,
            flag: str | None = None, blocked: bool = False) -> Txn:
        """Append one transaction; `flag` marks it as the bank's rule engine would."""
        txn = make_txn(ctx, self.account, day, amount, direction, channel, counterparty,
                       city or self.customer.home_city, country, cp_country)
        if ts is not None:
            txn.txn_ts = ts
        if flag:
            txn.status = "BLOCKED" if blocked else "FLAGGED"
            txn.flag_reason = flag
        self.txns.append(txn)
        return txn

    def note(self, day: date, text: str, author: str = "kyc_ops") -> None:
        self.notes.append(KycNote(self.customer.customer_id, day, author, text))

    def prior_sar(self, ctx: SeedContext, narrative: str) -> None:
        """A SAR filed 50-85 days ago — history the investigator should weigh."""
        filed = ctx.days_ago(ctx.rng.randint(50, 85))
        self.sars.append(SarReport(self.customer.customer_id, self.typology, "FILED",
                                   filed, narrative))


def new_case(ctx: SeedContext, typology: str, variant: Variant, segment: str,
             include_cash: bool = True, account_opened: date | None = None,
             **customer_fields) -> PlantedCase:
    """Customer + account + 90 days of background activity, ready for a pattern on top."""
    customer = make_customer(ctx, segment, **customer_fields)
    account = make_account(ctx, customer, opened=account_opened)
    case = PlantedCase(customer, account, typology, variant)
    case.txns.extend(normal_activity(ctx, customer, account, include_cash=include_cash))
    return case
