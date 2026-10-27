"""HIGH_RISK_COUNTRY: wires to a high-risk jurisdiction shortly after inbound credits."""

from datetime import timedelta

from src.synth import reference as ref
from src.synth.context import SeedContext
from src.synth.typologies.common import PlantedCase, Variant, new_case

TYPOLOGY = "HIGH_RISK_COUNTRY"


def build(ctx: SeedContext, variant: Variant) -> PlantedCase:
    return {Variant.TRUE_POSITIVE: _layering, Variant.AMBIGUOUS: _new_supplier,
            Variant.BENIGN: _documented_supplier}[variant](ctx, variant)


def _layering(ctx: SeedContext, variant: Variant) -> PlantedCase:
    """Several unrelated inbound credits, then 80-95% wired out to a high-risk jurisdiction."""
    case = new_case(ctx, TYPOLOGY, variant, "RETAIL", risk_rating=ctx.rng.randint(45, 80))
    start, end = ctx.recent_window(ctx.rng.randint(7, 12))
    inflow = 0
    for i in range(ctx.rng.randint(3, 5)):
        amount = ctx.dollars(8_000, 22_000)
        sender = ctx.rng.choice(ref.SHELL_COUNTERPARTIES + [ctx.person_name()])
        case.add(ctx, start + timedelta(days=i), amount, "CREDIT",
                 ctx.rng.choice(["WIRE", "ACH"]), sender)
        inflow += amount
    jurisdiction = ctx.rng.choice(ref.HIGH_RISK_JURISDICTIONS)
    beneficiary = f"{jurisdiction} Capital Exchange"
    out_total = int(inflow * ctx.rng.uniform(0.8, 0.95))
    wires = ctx.rng.randint(1, 2)
    for w in range(wires):
        day = min(end, start + timedelta(days=5 + w))
        case.add(ctx, day, out_total // wires, "DEBIT", "WIRE", beneficiary,
                 cp_country=jurisdiction, flag=TYPOLOGY)
    if ctx.rng.random() < 0.5:
        case.prior_sar(ctx, f"Funds from multiple unrelated senders forwarded to {jurisdiction} "
                            "within days; no apparent business purpose.")
    return case


def _new_supplier(ctx: SeedContext, variant: Variant) -> PlantedCase:
    """Importer's first-ever wire to a new high-risk counterparty; invoice requested, not received."""
    case = new_case(ctx, TYPOLOGY, variant, "SMALL_BUSINESS",
                    occupation="Import/export (home goods)",
                    full_name=f"{ctx.rng.choice(ref.LAST_NAMES)} Home Imports LLC",
                    risk_rating=ctx.rng.randint(35, 55))
    jurisdiction = ctx.rng.choice(ref.HIGH_RISK_JURISDICTIONS)
    day, _ = ctx.recent_window(1)
    case.add(ctx, day, ctx.dollars(18_000, 35_000), "DEBIT", "WIRE",
             f"{jurisdiction} Trading House", cp_country=jurisdiction, flag=TYPOLOGY)
    case.note(day + timedelta(days=1),
              f"Requested invoice and purchase order for wire to {jurisdiction} Trading House. "
              "Not yet received.")
    return case


def _documented_supplier(ctx: SeedContext, variant: Variant) -> PlantedCase:
    """Monthly wire to the same long-standing supplier, contract and EDD on file."""
    case = new_case(ctx, TYPOLOGY, variant, "SMALL_BUSINESS",
                    occupation="Import/export (furniture)",
                    full_name=f"{ctx.rng.choice(ref.LAST_NAMES)} Furniture Imports LLC",
                    risk_rating=ctx.rng.randint(55, 80))  # high-risk exposure raises the rating
    jurisdiction = ctx.rng.choice(ref.HIGH_RISK_JURISDICTIONS)
    supplier = f"{jurisdiction} Timber Cooperative"
    latest, _ = ctx.recent_window(1)
    for months_back in range(3, -1, -1):
        day = latest - timedelta(days=30 * months_back)
        if day >= ctx.history_start:
            case.add(ctx, day, ctx.dollars(12_000, 20_000), "DEBIT", "WIRE", supplier,
                     cp_country=jurisdiction, flag=TYPOLOGY if months_back == 0 else None)
    case.note(ctx.days_ago(ctx.rng.randint(150, 300)),
              f"Supplier contract with {supplier} on file (renewed this year). Enhanced due "
              "diligence completed; beneficial owners verified. Expect monthly wires of "
              "$10K-$25K.")
    return case
