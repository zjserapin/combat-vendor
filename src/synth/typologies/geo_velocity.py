"""GEO_VELOCITY: card used at home, then abroad a few hours later (impossible travel)."""

from datetime import timedelta

from src.synth import reference as ref
from src.synth.context import SeedContext
from src.synth.typologies.common import PlantedCase, Variant, new_case

TYPOLOGY = "GEO_VELOCITY"


def build(ctx: SeedContext, variant: Variant) -> PlantedCase:
    return {Variant.TRUE_POSITIVE: _cloned_card, Variant.AMBIGUOUS: _unannounced_trip,
            Variant.BENIGN: _noticed_trip}[variant](ctx, variant)


def _cloned_card(ctx: SeedContext, variant: Variant) -> PlantedCase:
    """Home purchase, then high-value foreign spend 2-5h later; the last attempt is blocked."""
    case = new_case(ctx, TYPOLOGY, variant, "RETAIL", risk_rating=ctx.rng.randint(25, 50))
    day, _ = ctx.recent_window(1)
    home = ctx.at(day, 9, 11)
    case.add(ctx, day, ctx.dollars(15, 90), "DEBIT", "CARD", ctx.rng.choice(ref.CARD_MERCHANTS),
             ts=home)
    city, country = ctx.rng.choice(ref.FOREIGN_CITIES)
    ts = home + timedelta(hours=ctx.rng.randint(2, 5))
    attempts = ctx.rng.randint(3, 4)
    for i in range(attempts):
        merchant = ctx.rng.choice(["Electronics Hub", "Luxe Jewelers", "Gift Card Kiosk"])
        case.add(ctx, ts.date(), ctx.dollars(400, 2_500), "DEBIT", "CARD", merchant, city=city,
                 country=country, cp_country=country, ts=ts, flag=TYPOLOGY,
                 blocked=(i == attempts - 1))
        ts += timedelta(minutes=ctx.rng.randint(10, 40))
    return case


def _unannounced_trip(ctx: SeedContext, variant: Variant) -> PlantedCase:
    """Ordinary foreign spend, no travel notice — but the customer visited the same city before."""
    case = new_case(ctx, TYPOLOGY, variant, "RETAIL", risk_rating=ctx.rng.randint(10, 35))
    city, country = ctx.rng.choice(ref.FOREIGN_CITIES)
    earlier = ctx.days_ago(ctx.rng.randint(55, 80))
    for d in range(3):
        case.add(ctx, earlier + timedelta(days=d), ctx.dollars(20, 180), "DEBIT", "CARD",
                 "Local merchant", city=city, country=country, cp_country=country)
    start, _ = ctx.recent_window(3)
    home = ctx.at(start, 6, 8)
    case.add(ctx, start, ctx.dollars(10, 60), "DEBIT", "CARD", ctx.rng.choice(ref.CARD_MERCHANTS),
             ts=home)
    ts = home + timedelta(hours=4)
    for d in range(3):
        case.add(ctx, ts.date(), ctx.dollars(20, 200), "DEBIT", "CARD", "Local merchant",
                 city=city, country=country, cp_country=country, ts=ts, flag=TYPOLOGY)
        ts += timedelta(days=1, hours=ctx.rng.randint(-2, 2))
    return case


def _noticed_trip(ctx: SeedContext, variant: Variant) -> PlantedCase:
    """Travel notice on file; the 'home' charge is a recurring subscription that posts monthly."""
    case = new_case(ctx, TYPOLOGY, variant, "RETAIL", risk_rating=ctx.rng.randint(5, 30))
    city, country = ctx.rng.choice(ref.FOREIGN_CITIES)
    start, end = ctx.recent_window(6)
    case.note(start - timedelta(days=ctx.rng.randint(3, 10)),
              f"Customer filed travel notice: {city}, {country} from {start} to {end}.")
    sub_amount = ctx.dollars(12, 25)
    for months_back in range(3, -1, -1):
        posting = start - timedelta(days=30 * months_back)
        if posting >= ctx.history_start:
            case.add(ctx, posting, sub_amount, "DEBIT", "CARD", "StreamBox Subscription",
                     ts=ctx.at(posting, 2, 4))
    first = ctx.at(start, 9, 12)
    for d in range((end - start).days):
        ts = first + timedelta(days=d)
        case.add(ctx, ts.date(), ctx.dollars(15, 250), "DEBIT", "CARD", "Local merchant",
                 city=city, country=country, cp_country=country, ts=ts, flag=TYPOLOGY)
    return case
