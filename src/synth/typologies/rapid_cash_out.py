"""RAPID_CASH_OUT: a large inbound credit drained in cash within ~48 hours."""

from datetime import timedelta

from src.synth import reference as ref
from src.synth.context import SeedContext
from src.synth.typologies.common import PlantedCase, Variant, new_case

TYPOLOGY = "RAPID_CASH_OUT"


def build(ctx: SeedContext, variant: Variant) -> PlantedCase:
    return {Variant.TRUE_POSITIVE: _mule_drain, Variant.AMBIGUOUS: _family_wire,
            Variant.BENIGN: _vehicle_purchase}[variant](ctx, variant)


def _mule_drain(ctx: SeedContext, variant: Variant) -> PlantedCase:
    """New account, wire from an unrelated company, 85-95% withdrawn in cash within 48h."""
    opened = ctx.days_ago(ctx.rng.randint(40, 110))
    case = new_case(ctx, TYPOLOGY, variant, "RETAIL",
                    occupation=ctx.rng.choice(["Graduate student", "Retail associate"]),
                    risk_rating=ctx.rng.randint(40, 80), onboarded_date=opened,
                    account_opened=opened)
    day, _ = ctx.recent_window(2)
    wire = ctx.dollars(30_000, 80_000)
    arrival = ctx.at(day, 9, 11)
    case.add(ctx, day, wire, "CREDIT", "WIRE", ctx.rng.choice(ref.SHELL_COUNTERPARTIES), ts=arrival)
    remaining, ts = int(wire * ctx.rng.uniform(0.85, 0.95)), arrival
    while remaining > 0:
        ts += timedelta(hours=ctx.rng.randint(2, 7))
        channel = ctx.rng.choice(["ATM", "BRANCH_CASH"])
        cap = ctx.dollars(500, 1_000) if channel == "ATM" else ctx.dollars(5_000, 9_500)
        amount = min(cap, remaining)
        case.add(ctx, ts.date(), amount, "DEBIT", channel, "Cash withdrawal", ts=ts, flag=TYPOLOGY)
        remaining -= amount
    if ctx.rng.random() < 0.5:
        case.prior_sar(ctx, "Account received third-party wire and was emptied through ATM "
                            "withdrawals at multiple locations within two days.")
    return case


def _family_wire(ctx: SeedContext, variant: Variant) -> PlantedCase:
    """Wire from an individual abroad, half withdrawn in cash over three days. No explanation."""
    case = new_case(ctx, TYPOLOGY, variant, "RETAIL", risk_rating=ctx.rng.randint(30, 55))
    _, country = ctx.rng.choice(ref.FOREIGN_CITIES)
    day, _ = ctx.recent_window(3)
    wire = ctx.dollars(20_000, 30_000)
    arrival = ctx.at(day, 9, 11)
    case.add(ctx, day, wire, "CREDIT", "WIRE", ctx.person_name(), cp_country=country, ts=arrival)
    ts = arrival
    for _ in range(ctx.rng.randint(3, 4)):
        ts += timedelta(hours=ctx.rng.randint(12, 20))
        case.add(ctx, ts.date(), int(wire * ctx.rng.uniform(0.13, 0.18)), "DEBIT", "BRANCH_CASH",
                 "Cash withdrawal", ts=ts, flag=TYPOLOGY)
    return case


def _vehicle_purchase(ctx: SeedContext, variant: Variant) -> PlantedCase:
    """Escrow proceeds, then one cash withdrawal the customer pre-announced with documentation."""
    case = new_case(ctx, TYPOLOGY, variant, "RETAIL", risk_rating=ctx.rng.randint(20, 55))
    day, _ = ctx.recent_window(1)
    proceeds = ctx.dollars(25_000, 45_000)
    arrival = ctx.at(day, 9, 11)
    case.add(ctx, day, proceeds, "CREDIT", "WIRE", "Carolina Title & Escrow", ts=arrival)
    withdrawal = int(proceeds * ctx.rng.uniform(0.5, 0.65))
    case.note(day, "Customer called ahead: withdrawing cash for a private-party vehicle "
                   f"purchase (~${withdrawal / 100:,.0f}). Bill of sale provided and scanned "
                   "to file. Funds are proceeds from home sale via Carolina Title & Escrow.")
    ts = arrival + timedelta(hours=ctx.rng.randint(20, 30))
    case.add(ctx, ts.date(), withdrawal, "DEBIT", "BRANCH_CASH", "Cash withdrawal", ts=ts,
             flag=TYPOLOGY)
    return case
