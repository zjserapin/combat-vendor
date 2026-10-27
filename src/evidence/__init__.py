"""The evidence pack: the case file the harness assembles for each alert.

    pack = build_evidence(db, config, alert)
    pack.text          # what the model reads
    pack.fingerprint   # stored with the decision
"""

from src.evidence.pack import EvidencePack, EvidencePeriod, build_evidence

__all__ = ["EvidencePack", "EvidencePeriod", "build_evidence"]
