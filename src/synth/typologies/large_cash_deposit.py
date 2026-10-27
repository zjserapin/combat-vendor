"""LARGE_CASH_DEPOSIT: a single cash deposit above $50,000."""

from datetime import timedelta

from src.synth import reference as ref
from src.synth.context import SeedContext
from src.synth.typologies.common import PlantedCase, Variant, new_case

TYPOLOGY = "LARGE_CASH_DEPOSIT"


def build(ctx: SeedContext, variant: Variant) -> PlantedCase:
    return {Variant.TRUE_POSITIVE: _placement, Variant.AMBIGUOUS: _unexplained_retiree,
            Variant.BENIGN: _documented_sale}[variant](ctx, variant)


def _placement(ctx: SeedContext, variant: Variant) -> PlantedCase:
    """New account, low-income profile, big cash deposit, most of it wired out days later."""
    opened = ctx.days_ago(ctx.rng.randint(30, 90))
    case = new_case(ctx, TYPOLOGY, variant, "RETAIL",
                    occupation=ctx.rng.choice(["Graduate student", "Retail associate"]),
                    risk_rating=ctx.rng.randint(45, 85), onboarded_date=opened,
                    account_opened=opened)
    day, _ = ctx.recent_window(1)
    deposit = ctx.dollars(55_000, 120_000)
    case.add(ctx, day, deposit, "CREDIT", "BRANCH_CASH", "Cash deposit", flag=TYPOLOGY)
    case.add(ctx, day + timedelta(days=ctx.rng.randint(1, 3)),
             int(deposit * ctx.rng.uniform(0.7, 0.9)), "DEBIT", "WIRE",
             ctx.rng.choice(ref.SHELL_COUNTERPARTIES))
    if ctx.rng.random() < 0.5:
        case.prior_sar(ctx, "Large cash deposit inconsistent with stated occupation, followed "
                            "by outbound wire to an unrelated company.")
    return case


def _unexplained_retiree(ctx: SeedContext, variant: Variant) -> PlantedCase:
    """Long-tenured retiree, one large cash deposit, nothing on file explaining it."""
    case = new_case(ctx, TYPOLOGY, variant, "RETAIL", occupation="Retired",
                    risk_rating=ctx.rng.randint(15, 35),
                    onboarded_date=ctx.days_ago(ctx.rng.randint(3_000, 6_000)))
    day, _ = ctx.recent_window(1)
    case.add(ctx, day, ctx.dollars(52_000, 75_000), "CREDIT", "BRANCH_CASH", "Cash deposit",
             flag=TYPOLOGY)
    return case


def _documented_sale(ctx: SeedContext, variant: Variant) -> PlantedCase:
    """Cash from a private vehicle sale, bill of sale scanned the same day."""
    case = new_case(ctx, TYPOLOGY, variant, "RETAIL", risk_rating=ctx.rng.randint(25, 60))
    day, _ = ctx.recent_window(1)
    amount = ctx.dollars(50_500, 65_000)
    case.add(ctx, day, amount, "CREDIT", "BRANCH_CASH", "Cash deposit", flag=TYPOLOGY)
    case.note(day, f"Customer provided bill of sale for private sale of a classic vehicle "
                   f"(${amount / 100:,.0f}); buyer paid cash. Title transfer and ID scanned "
                   "to file.", author="branch_banker")
    return case
