"""Rewrite OX ownership so quantity ObjectProperties are nested measure hops.

The locked occurrence JSON still lists those names as self-facets because the
Pipeline MCP takes them as strings and attaches the measure node internally.
OntoLogX has no such expander: a self-facet is copied onto node.properties,
and Pydantic rejects ObjectProperty / measure-class names there.

with-prompt-qty applies this rewrite to a copy of the locked surface. The
locked JSON and with-prompt prompt stay unchanged.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

QUANTITY_FACETS: dict[str, str] = {
    "hasTargetTemperature": "om-2:Temperature",
    "hasStepDuration": "om-2:Duration",
    "hasTemperatureRate": "om-2:TemperatureRate",
    "hasStirringTemperature": "om-2:Temperature",
    "hasDryingTemperature": "om-2:Temperature",
    "hasDryingPressure": "om-2:Pressure",
    "hasEvaporationTemperature": "om-2:Temperature",
    "hasEvaporationPressure": "om-2:Pressure",
    "isEvaporatedToVolume": "om-2:Volume",
    "hasTransferedAmount": "om-2:Volume",
}


def apply_quantity_nested_ownership(surface: dict[str, Any]) -> dict[str, Any]:
    """Move quantity self-facets onto nested_quantity hops. Does not mutate input."""
    out = deepcopy(surface)
    note = str(out.get("note") or "").rstrip()
    extra = (
        "Quantity ObjectProperties are nested measure hops for OntoLogX, "
        "not datatype self-facets."
    )
    out["note"] = f"{note} {extra}".strip() if note else extra
    for owner in out.get("owner_occurrences") or []:
        facets = [str(item) for item in owner.get("self_facets") or [] if item]
        nested = list(owner.get("nested_ownership") or [])
        existing = {str(item.get("argument") or "") for item in nested}
        keep: list[str] = []
        added: list[dict[str, Any]] = []
        for facet in facets:
            if facet not in QUANTITY_FACETS:
                keep.append(facet)
                continue
            if facet in existing:
                continue
            added.append(
                {
                    "argument": facet,
                    "owner_path": f"self.{facet}",
                    "property": facet,
                    "role": "nested_quantity",
                    "range_class": QUANTITY_FACETS[facet],
                    "requires": [],
                }
            )
            existing.add(facet)
        owner["self_facets"] = keep
        owner["nested_ownership"] = added + nested
    return out
