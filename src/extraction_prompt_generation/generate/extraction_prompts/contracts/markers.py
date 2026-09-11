"""T-Box splice start/end markers for deterministic prompt components.

Used by `materializable.py` so GPT-5 does not edit compiled T-Box text.
See contracts/README.md.
"""

from __future__ import annotations

_DETERMINISTIC_TBOX_BEGIN = (
    "----- DETERMINISTIC T-BOX CONTRACT (mechanically spliced; do not edit) -----"
)


_DETERMINISTIC_TBOX_END = "----- END DETERMINISTIC T-BOX CONTRACT -----"


_NESTED_OWNED_SCALAR_BEGIN = (
    "----- NESTED OWNED-DEPENDENT SCALARS (mechanically spliced; do not edit) -----"
)


_NESTED_OWNED_SCALAR_END = "----- END NESTED OWNED-DEPENDENT SCALARS -----"
