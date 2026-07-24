"""
Full-tier warm specs for chemistry competency atomic tools.

Each entry is {tool, args} with namespace + filters. Never warm unbounded list-all
queries (empty label_fragment on large namespaces).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from mini_marie.marie.chemistry.chemistry_cache import TOOL_REGISTRY
from mini_marie.marie.chemistry.limits import WARM_NAMESPACES
from mini_marie.warm_manifest import merge_warm_specs

# Marie MQ / probe seeds — specific filters only
ONTO_SPECIES_SPECS: List[Dict[str, Any]] = [
    {
        "tool": "filter_by_literal",
        "args": {
            "namespace": "ontospecies",
            "class_local": "Species",
            "property_local": "hasMolecularFormula",
            "value_fragment": "C6H8O6",
            "match": "equals",
        },
    },
    {
        "tool": "get_linked_values",
        "args": {
            "namespace": "ontospecies",
            "class_local": "Species",
            "subject_label": "C=CC(=O)O",
            "link_property": "hasDissociationConstants",
            "identifier_type": "smiles",
        },
    },
    {
        "tool": "get_linked_values",
        "args": {
            "namespace": "ontospecies",
            "class_local": "Species",
            "subject_label": "C(CO)O",
            "link_property": "hasHydrogenBondDonorCount",
            "value_properties": ["hasHydrogenBondAcceptorCount"],
            "identifier_type": "smiles",
        },
    },
    {
        "tool": "get_linked_values",
        "args": {
            "namespace": "ontospecies",
            "class_local": "Species",
            "subject_label": "C=CC(=O)O",
            "link_property": "hasDissociationConstants",
            "identifier_type": "smiles",
            "include_metadata": True,
        },
    },
    {
        "tool": "count_instances",
        "args": {
            "namespace": "ontospecies",
            "class_local": "Species",
            "required_property": "hasDissociationConstants",
        },
    },
]

ONTOKIN_SPECS: List[Dict[str, Any]] = [
    {
        "tool": "traverse_mechanism_reactions",
        "args": {"namespace": "ontokin", "reaction_fragment": "H2O2"},
    },
    {
        "tool": "traverse_mechanism_reactions",
        "args": {
            "namespace": "ontokin",
            "reaction_fragment": "H2 + OH",
        },
    },
    {
        "tool": "count_instances",
        "args": {
            "namespace": "ontokin",
            "class_local": "GasPhaseReaction",
        },
    },
]

ONTOCOMPCHEM_SPECS: List[Dict[str, Any]] = [
    {
        "tool": "query_calculation_results",
        "args": {
            "namespace": "ontocompchem",
            "species_label": "798ae810",
            "result_kinds": ["rotational"],
        },
    },
    {
        "tool": "query_calculation_results",
        "args": {
            "namespace": "ontocompchem",
            "species_label": "663e4fd4",
            "result_kinds": ["homo", "lumo"],
        },
    },
    {
        "tool": "query_calculation_results",
        "args": {
            "namespace": "ontocompchem",
            "species_label": "663e4fd4",
            "result_kinds": ["zpe"],
        },
    },
]

ONTOZEOLITE_SPECS: List[Dict[str, Any]] = [
    {
        "tool": "filter_by_literal",
        "args": {
            "namespace": "ontozeolite",
            "class_local": "ZeoliticMaterial",
            "property_local": "hasFrameworkCode",
            "value_fragment": "AEN",
        },
    },
    {
        "tool": "query_zeolite_property",
        "args": {
            "namespace": "ontozeolite",
            "framework_code": "SFN",
            "property_local": "isReferenceZeolite",
        },
    },
    {
        "tool": "filter_by_literal",
        "args": {
            "namespace": "ontozeolite",
            "class_local": "ZeoliticMaterial",
            "property_local": "hasFrameworkCode",
            "value_fragment": "FAU",
        },
    },
    {
        "tool": "count_instances",
        "args": {
            "namespace": "ontozeolite",
            "class_local": "ZeoliteFramework",
        },
    },
]

ONTOPROVENANCE_SPECS: List[Dict[str, Any]] = [
    {
        "tool": "lookup_individuals",
        "args": {
            "namespace": "ontoprovenance",
            "class_local": "Person",
            "label_fragment": "Marinov",
        },
    },
]

FRAMEWORK_CODES = [
    "AEN", "SFN", "FAU", "MFI", "LTA", "BEA", "CHA", "MOR", "FER",
]


def comprehensive_warm_specs() -> List[Dict[str, Any]]:
    """MQ/probe-driven warm specs with specific filters (safe for fragile endpoints)."""
    specs: List[Dict[str, Any]] = []
    specs.extend(ONTO_SPECIES_SPECS)
    specs.extend(ONTOKIN_SPECS)
    specs.extend(ONTOCOMPCHEM_SPECS)
    specs.extend(ONTOZEOLITE_SPECS)
    specs.extend(ONTOPROVENANCE_SPECS)

    for code in FRAMEWORK_CODES:
        specs.append(
            {
                "tool": "filter_by_literal",
                "args": {
                    "namespace": "ontozeolite",
                    "class_local": "ZeoliticMaterial",
                    "property_local": "hasFrameworkCode",
                    "value_fragment": code,
                },
            }
        )

    seen: set[str] = set()
    out: List[Dict[str, Any]] = []
    for spec in specs:
        ns = spec.get("args", {}).get("namespace", "")
        if ns not in WARM_NAMESPACES:
            continue
        if spec["tool"] not in TOOL_REGISTRY:
            continue
        key = f"{spec['tool']}:{spec['args']}"
        if key in seen:
            continue
        seen.add(key)
        out.append(spec)
    return out


def workflow_driven_warm_specs() -> List[Dict[str, Any]]:
    """Comprehensive specs plus workflow JSON variants when present."""
    base = comprehensive_warm_specs()
    suite = Path(__file__).resolve().parent / "workflows" / "competency_suite.json"
    if not suite.exists():
        return base
    from mini_marie.warm_manifest import collect_atomic_specs_from_suite_manifest

    manifest = json.loads(suite.read_text(encoding="utf-8"))
    wf_specs = collect_atomic_specs_from_suite_manifest(manifest, resolve=lambda v, _: v)
    return merge_warm_specs(base, wf_specs)


def full_space_warm_specs(
    *,
    namespace: str | None = None,
    tool: str | None = None,
    dimension_id: str | None = None,
) -> List[Dict[str, Any]]:
    """Every catalog option variant per tool (see warm_option_catalog.json)."""
    from mini_marie.marie.chemistry.warm_option_catalog import full_space_warm_specs as _fs

    raw = _fs(namespace=namespace, tool=tool, dimension_id=dimension_id)
    return [{"tool": s["tool"], "args": s["args"]} for s in raw]
