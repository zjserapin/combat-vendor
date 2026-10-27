"""Ordinary customers doing ordinary things: payroll, groceries, bills.

Every customer (planted or not) gets this background activity, so planted cases
sit inside realistic noise instead of standing out by having no other history.
"""

from __future__ import annotations

from datetime import date, timedelta

from src.synth import reference as ref
from src.synth.context import SeedContext
from src.synth.models import Account, Customer, Txn

SEGMENT_WEIGHTS = {"RETAIL": 0.80, "SMALL_BUSINESS": 0.15, "PRIVATE": 0.05}


def make_customer(ctx: SeedContext, segment: str | None = None, **overrides) -> Customer:
    """A plausible customer. Planted cases pass overrides for the fields that matter."""
    segment = segment or ctx.rng.choices(list(SEGMENT_WEIGHTS), list(SEGMENT_WEIGHTS.values()))[0]
    name = ctx.person_name()
    if segment == "SMALL_BUSINESS":
        biz = ctx.rng.choice(ref.SMALL_BUSINESS_TYPES)
        name, occupation = f"{name.split()[1]} {biz} LLC", biz
        expected_cash = ctx.dollars(2_000, 15_000)
    else:
        occupation = ctx.rng.choice(ref.RETAIL_OCCUPATIONS)
        expected_cash = ctx.dollars(0, 800)

    onboarded = ctx.onboarded()
    fields = {
        "customer_id": ctx.next_customer_id(),
        "full_name": name,
        "segment": segment,
        "occupation": occupation,
        "home_city": ctx.home_city(),
        "home_country": "United States",
        "onboarded_date": onboarded,
        "risk_rating": ctx.rng.randint(5, 45),
        "expected_monthly_cash_cents": expected_cash,
        "kyc_refreshed_date": max(onboarded, ctx.days_ago(ctx.rng.randint(30, 700))),
    }
    fields.update(overrides)
    return Customer(**fields)


def make_account(ctx: SeedContext, customer: Customer, opened: date | None = None) -> Account:
    """Primary account for a customer. Balance is filled in after all activity exists."""
    kind = "BUSINESS_CHECKING" if customer.segment == "SMALL_BUSINESS" else "CHECKING"
    opened = opened or customer.onboarded_date
    return Account(ctx.next_account_id(), customer.customer_id, kind, opened, 0, "OPEN")


def normal_activity(
    ctx: SeedContext, customer: Customer, account: Account, include_cash: bool = True
) -> list[Txn]:
    """90 days of unremarkable activity. Planted cases may supply their own cash pattern."""
    start = max(ctx.history_start, account.opened_date)
    days = [start + timedelta(days=i) for i in range((ctx.as_of - start).days)]
    if customer.segment == "SMALL_BUSINESS":
        return _business_activity(ctx, customer, account, days, include_cash)
    return _personal_activity(ctx, customer, account, days)


def _personal_activity(ctx: SeedContext, c: Customer, a: Account, days: list[date]) -> list[Txn]:
    txns: list[Txn] = []
    city = c.home_city
    employer = ctx.rng.choice(ref.EMPLOYERS)
    pay = ctx.dollars(1_200, 4_500) if c.segment == "RETAIL" else ctx.dollars(8_000, 20_000)
    for i, day in enumerate(days):
        if i % 14 == 3 and c.occupation != "Retired":
            txns.append(make_txn(ctx, a, day, pay, "CREDIT", "ACH", employer, city))
        if day.day == 1:
            for biller in ctx.rng.sample(ref.BILLERS, 2):
                txns.append(make_txn(ctx, a, day, ctx.dollars(60, 1_800), "DEBIT", "ACH", biller, city))
        if ctx.rng.random() < 0.45:
            merchant = ctx.rng.choice(ref.CARD_MERCHANTS)
            txns.append(make_txn(ctx, a, day, ctx.dollars(5, 150), "DEBIT", "CARD", merchant, city))
        if ctx.rng.random() < 0.04:
            txns.append(make_txn(ctx, a, day, ctx.dollars(40, 300), "DEBIT", "ATM", "Cairn Bank ATM", city))
    return txns


def _business_activity(
    ctx: SeedContext, c: Customer, a: Account, days: list[date], include_cash: bool
) -> list[Txn]:
    txns: list[Txn] = []
    city = c.home_city
    weekly_cash = c.expected_monthly_cash_cents / 4.3
    for day in days:
        if ctx.rng.random() < 0.35:
            payer = f"{ctx.rng.choice(ref.LAST_NAMES)} Household"
            txns.append(make_txn(ctx, a, day, ctx.dollars(200, 4_000), "CREDIT", "ACH", payer, city))
        if ctx.rng.random() < 0.25:
            merchant = ctx.rng.choice(ref.CARD_MERCHANTS)
            txns.append(make_txn(ctx, a, day, ctx.dollars(20, 900), "DEBIT", "CARD", merchant, city))
        if include_cash and day.weekday() == 0 and weekly_cash > 0:
            amount = int(weekly_cash * ctx.rng.uniform(0.7, 1.3))
            txns.append(make_txn(ctx, a, day, amount, "CREDIT", "BRANCH_CASH", "Cash deposit", city))
        if day.day in (14, 28):
            txns.append(make_txn(ctx, a, day, ctx.dollars(2_000, 9_000), "DEBIT", "ACH", "Gusto Payroll", city))
    return txns


def make_txn(ctx, account, day, amount, direction, channel, counterparty, city,
             country="United States", cp_country="United States") -> Txn:
    """One completed transaction at a random time on `day`."""
    return Txn(account.account_id, ctx.at(day), amount, direction, channel,
               counterparty, cp_country, city, country)
