"""SAR narrative drafting (Block 11) and SAR QC (Block 12).

    facts = build_sar_facts(db, config, alert)      # the fact sheet: SQL + arithmetic
    qc = run_qc(narrative, facts, config)           # rules-only QC of any narrative
    outcome = prepare_sar(db, config, alert, llm)   # draft -> QC -> revise -> log
"""

from src.sar.facts import SarFacts, build_sar_facts
from src.sar.pipeline import NotEscalated, SarOutcome, prepare_sar
from src.sar.qc import QcResult, run_qc

__all__ = ["NotEscalated", "QcResult", "SarFacts", "SarOutcome", "build_sar_facts",
           "prepare_sar", "run_qc"]
