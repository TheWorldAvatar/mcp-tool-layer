"""Attribute official KG scorer mistakes to extraction vs graph building.

The KG agent may only copy the extraction ledger. For each GT/Pred mismatch,
an LLM (default ``openai/gpt-5.6-sol``) decides whether the ledger already
stated that fact. Ledger-supported false negatives are KG-building errors;
the rest are extraction errors. Counterfactual extraction F1 is the official
score after promoting those KG misses to TP, so it is >= the published F1.
"""

from src.kg_building.attribution.run import (
    ATTRIBUTION_MODEL,
    attribute_paper,
    attribute_run,
)

__all__ = [
    "ATTRIBUTION_MODEL",
    "attribute_paper",
    "attribute_run",
]
