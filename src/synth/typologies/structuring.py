"""STRUCTURING: repeated cash deposits just under the $10,000 CTR threshold."""

from datetime import timedelta

from src.synth import reference as ref
from src.synth.context import SeedContext
from src.synth.typologies.common import PlantedCase, Variant, new_case

TYPOLOGY = "STRUCTURING"


def build(ctx: SeedContext, variant: Variant) -> PlantedCase:
    return {Variant.TRUE_POSITIVE: _smurfing, Variant.AMBIGUOUS: _outgrown_profile,
            Variant.BENIGN: _cash_restaurant}[variant](ctx, variant)


def _smurfing(ctx: SeedContext, variant: Variant) -> PlantedCase:
    """Retail customer, no cash business, 6-9 sub-$10K deposits across branches in ~2 weeks."""
    case = new_case(ctx, TYPOLOGY, variant, "RETAIL",
                    occupation=ctx.rng.choice(["Retail associate", "Graduate student"]),
                    risk_rating=ctx.rng.randint(45, 85),
                    expected_monthly_cash_cents=ctx.dollars(0, 500))
    start, end = ctx.recent_window(ctx.rng.randint(12, 18))
    span = (end - start).days
    days = sorted(ctx.rng.sample(range(span + 1), ctx.rng.randint(6, 9)))
    for offset in days:
        city = ctx.rng.choice(ref.HOME_CITIES[:4])
        channel = ctx.rng.choice(["BRANCH_CASH", "ATM"])
        case.add(ctx, start + timedelta(days=offset), ctx.dollars(8_000, 9_950), "CREDIT",
                 channel, "Cash deposit", city=city, flag=TYPOLOGY)
    if ctx.rng.random() < 0.5:
        case.prior_sar(ctx, "Multiple cash deposits between $8,000 and $9,900 at different "
                            "branches within ten days; customer declined to explain source.")
    return case


def _outgrown_profile(ctx: SeedContext, variant: Variant) -> PlantedCase:
    """Real business, but cash is running ~3x its declared profile and KYC is stale."""
    onboarded = ctx.days_ago(ctx.rng.randint(1_400, 2_500))
    case = new_case(ctx, TYPOLOGY, variant, "SMALL_BUSINESS", include_cash=False,
                    occupation="Landscaping services",
                    full_name=f"{ctx.rng.choice(ref.LAST_NAMES)} Landscaping LLC",
                    risk_rating=ctx.rng.randint(40, 60), onboarded_date=onboarded,
                    kyc_refreshed_date=onboarded + timedelta(days=30),
                    expected_monthly_cash_cents=ctx.dollars(14_000, 16_000))
    start, _end = ctx.recent_window(14)
    for offset in sorted(ctx.rng.sample(range(15), 5)):
        case.add(ctx, start + timedelta(days=offset), ctx.dollars(9_000, 9_900), "CREDIT",
                 "BRANCH_CASH", "Cash deposit", flag=TYPOLOGY)
    case.note(ctx.days_ago(ctx.rng.randint(120, 300)),
              "Periodic KYC refresh overdue. Last site visit more than three years ago.")
    return case


def _cash_restaurant(ctx: SeedContext, variant: Variant) -> PlantedCase:
    """Restaurant that deposits every Mon/Thu for months, documented by a site visit."""
    case = new_case(ctx, TYPOLOGY, variant, "SMALL_BUSINESS", include_cash=False,
                    occupation="Restaurant (owner-operated)",
                    full_name=f"{ctx.rng.choice(ref.LAST_NAMES)} Kitchen LLC",
                    risk_rating=ctx.rng.randint(50, 80),  # cash-intensive = high inherent risk
                    expected_monthly_cash_cents=ctx.dollars(60_000, 75_000))
    start, end = ctx.recent_window(14)
    day = ctx.history_start
    while day < ctx.as_of:
        if day.weekday() in (0, 3):
            in_window = start <= day <= end
            case.add(ctx, day, ctx.dollars(6_500, 9_800), "CREDIT", "BRANCH_CASH",
                     "Cash deposit", flag=TYPOLOGY if in_window else None)
        day += timedelta(days=1)
    visit = ctx.days_ago(ctx.rng.randint(150, 220))
    case.note(visit, "Cash-intensive restaurant. Deposits every Monday and Thursday after "
                     "weekend and midweek receipts. Site visit confirmed POS cash totals "
                     "reconcile to deposits.")
    return case
