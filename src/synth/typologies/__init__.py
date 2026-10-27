"""Registry of planted AML typologies and how many of each variant to plant."""

from collections.abc import Callable

from src.synth.context import SeedContext
from src.synth.typologies import (
    geo_velocity,
    high_risk_country,
    large_cash_deposit,
    rapid_cash_out,
    structuring,
)
from src.synth.typologies.common import HUMAN_DECISION, PlantedCase, Variant

Builder = Callable[[SeedContext, Variant], PlantedCase]

TYPOLOGIES: dict[str, Builder] = {
    structuring.TYPOLOGY: structuring.build,
    geo_velocity.TYPOLOGY: geo_velocity.build,
    rapid_cash_out.TYPOLOGY: rapid_cash_out.build,
    high_risk_country.TYPOLOGY: high_risk_country.build,
    large_cash_deposit.TYPOLOGY: large_cash_deposit.build,
}

# Per typology. Half the queue is benign on purpose: real alert queues are mostly
# false positives, and a demo that only contains fraud flatters any model.
VARIANT_COUNTS: dict[Variant, int] = {
    Variant.TRUE_POSITIVE: 4,
    Variant.AMBIGUOUS: 2,
    Variant.BENIGN: 6,
}

__all__ = ["HUMAN_DECISION", "TYPOLOGIES", "VARIANT_COUNTS", "PlantedCase", "Variant"]
