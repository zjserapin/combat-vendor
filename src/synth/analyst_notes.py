"""The analyst's written reason for each planted scenario — ground truth, labels schema only.

At Ally the equivalent is the disposition narrative in case management. The
backtest shows it next to the agent's rationale for every disagreement, so an
SME can judge who was right. Text only: picking a note draws no random numbers,
so the rest of the synthetic bank is unchanged by it.
"""

from src.synth.typologies.common import Variant

TP, AMB, BEN = Variant.TRUE_POSITIVE, Variant.AMBIGUOUS, Variant.BENIGN

ANALYST_RATIONALE: dict[tuple[str, Variant], str] = {
    ("STRUCTURING", TP): "Repeated cash deposits just under $10K across branches, no cash "
                         "business to explain them. Classic structuring; escalated for SAR.",
    ("STRUCTURING", AMB): "Legitimate business, but cash is far above the declared profile and "
                          "KYC is years stale. Needs a refresh before a call can be made.",
    ("STRUCTURING", BEN): "Owner-operated restaurant with a steady twice-weekly cash pattern, "
                          "confirmed by a site visit on file. Consistent with the business.",
    ("GEO_VELOCITY", TP): "Foreign card spend hours after a home purchase — physically "
                          "impossible travel, and the bank already blocked an attempt.",
    ("GEO_VELOCITY", AMB): "Foreign spend with no travel notice, though the customer has been "
                           "to that city before. Contact the customer to confirm.",
    ("GEO_VELOCITY", BEN): "Travel notice on file; the 'home' charge is a recurring "
                           "subscription that posts automatically. No fraud.",
    ("RAPID_CASH_OUT", TP): "New account receives a wire from an unrelated company and almost "
                            "all of it leaves in cash within two days. Mule pattern.",
    ("RAPID_CASH_OUT", AMB): "Wire from an individual abroad, half withdrawn in cash, no "
                             "explanation on file. Ask the customer about source and purpose.",
    ("RAPID_CASH_OUT", BEN): "Escrow proceeds then one cash withdrawal the customer announced "
                             "in advance with documentation. Explained.",
    ("HIGH_RISK_COUNTRY", TP): "Unrelated inbound credits funnelled out to a high-risk "
                               "jurisdiction within days. Layering; escalated.",
    ("HIGH_RISK_COUNTRY", AMB): "First-ever wire to a new high-risk supplier; invoice requested "
                                "but not received. Enhanced due diligence needed.",
    ("HIGH_RISK_COUNTRY", BEN): "Same long-standing supplier every month, contract and EDD on "
                                "file. Expected activity for this importer.",
    ("LARGE_CASH_DEPOSIT", TP): "New low-income account, large cash deposit, most of it wired "
                                "out days later. Placement; escalated.",
    ("LARGE_CASH_DEPOSIT", AMB): "Long-tenured retiree, one large cash deposit, nothing on file "
                                 "explaining the source. Ask before deciding.",
    ("LARGE_CASH_DEPOSIT", BEN): "Cash from a private vehicle sale; bill of sale scanned the "
                                 "same day. Documented and one-off.",
}
