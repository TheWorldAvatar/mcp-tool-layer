"""Rewrite OX ownership so ObjectProperties are nested hops.

The locked occurrence JSON still lists Pipeline MCP argument names because
the server expands strings into nodes. OntoLogX has no expander. This rewrite
is the default OntoSynthesis occurrence surface for generic-strict,
generic-noprompt, and with-prompt. It copies the locked JSON; the file itself
stays frozen.
"""

from __future__ import annotations

from typing import Any

from quantity_ownership import apply_quantity_nested_ownership

NESTED_HOP_RANGES: dict[str, str] = {
    "hasAddedChemicalInput": "ontosyn:ChemicalInput",
    "hasWashingSolvent": "ontosyn:ChemicalInput",
    "hasSeparationSolvent": "ontosyn:ChemicalInput",
    "hasDryingAgent": "ontosyn:ChemicalInput",
    "removesSpecies": "ontosyn:ChemicalInput",
    "hasVessel": "ontosyn:Vessel",
    "hasVesselEnvironment": "ontosyn:VesselEnvironment",
    "hasVesselType": "ontosyn:VesselType",
    "usesEquipment": "ontosyn:Equipment",
    "hasHeatChillDevice": "ontosyn:HeatChillDevice",
    "isTransferedTo": "ontosyn:Vessel",
    "isSuppliedBy": "ontosyn:Supplier",
    "referencesMaterial": "ontosyn:Material",
    "isRepresentedBy": "ontomops:MetalOrganicPolyhedron",
    "isSeparationType": "ontosyn:SeparationType",
}

CHILD_DATATYPES = {
    "hasAlternativeNames",
    "hasAmount",
    "hasChemicalDescription",
    "hasChemicalFormula",
    "hasPurity",
    "hasCCDCNumber",
}

HEADING_KEYS_ON_CHILD: dict[str, dict[str, tuple[str, ...]]] = {
    "Add": {
        "hasAddedChemicalInput": (
            "hasAddedChemicalInput",
            "hasAlternativeNames",
            "hasAmount",
            "hasChemicalDescription",
            "hasChemicalFormula",
            "hasPurity",
            "isSuppliedBy",
            "referencesMaterial",
        )
    },
    "Filter": {
        "hasWashingSolvent": (
            "hasWashingSolvent",
            "hasAlternativeNames",
            "hasAmount",
            "hasChemicalDescription",
            "hasChemicalFormula",
            "hasPurity",
        )
    },
    "Separate": {
        "hasSeparationSolvent": (
            "hasSeparationSolvent",
            "hasAlternativeNames",
            "hasAmount",
            "hasChemicalDescription",
            "hasChemicalFormula",
            "hasPurity",
        )
    },
    "Dry": {
        "hasDryingAgent": (
            "hasDryingAgent",
            "hasAlternativeNames",
            "hasAmount",
            "hasChemicalDescription",
            "hasChemicalFormula",
            "hasPurity",
        )
    },
    "Evaporate": {
        "removesSpecies": (
            "removesSpecies",
            "hasAlternativeNames",
            "hasAmount",
            "hasChemicalDescription",
            "hasChemicalFormula",
            "hasPurity",
        )
    },
}


def apply_hops_ownership(surface: dict[str, Any]) -> dict[str, Any]:
    """Quantity hops plus range_class on nested object hops. Does not mutate input."""
    out = apply_quantity_nested_ownership(surface)
    note = str(out.get("note") or "").rstrip()
    extra = (
        "Ledger hop names are relationships to child nodes, not parent properties."
    )
    out["note"] = f"{note} {extra}".strip()
    for owner in out.get("owner_occurrences") or []:
        name = str(owner.get("owner_class") or "")
        heading = HEADING_KEYS_ON_CHILD.get(name) or {}
        for item in owner.get("nested_ownership") or []:
            prop = str(item.get("property") or "")
            if prop in NESTED_HOP_RANGES and not item.get("range_class"):
                item["range_class"] = NESTED_HOP_RANGES[prop]
            if prop in heading:
                item["heading_keys_on_child"] = list(heading[prop])
        if name == "ChemicalOutput":
            nested = list(owner.get("nested_ownership") or [])
            if not any(str(item.get("argument") or "") == "hasYield" for item in nested):
                nested.append(
                    {
                        "argument": "hasYield",
                        "owner_path": "self.hasYield",
                        "property": "hasYield",
                        "role": "nested_quantity",
                        "range_class": "om-2:AmountOfSubstanceFraction",
                        "subject": "bound_root",
                        "requires": [],
                    }
                )
            owner["nested_ownership"] = nested
    linkers = list(out.get("root_linkers") or [])
    if not any(str(item.get("predicate") or "") == "hasYield" for item in linkers):
        linkers.append(
            {
                "predicate": "hasYield",
                "subject_class": "ChemicalSynthesis",
                "object_class": "AmountOfSubstanceFraction",
            }
        )
    out["root_linkers"] = linkers
    return out
