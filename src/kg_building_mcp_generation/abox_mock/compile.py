"""Compile an occurrence surface from parsed T-Box inputs without an LLM."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Mapping

from src.kg_building_mcp_generation.overlay.quantity_surface import (
    compile_quantity_surface,
)
from src.kg_building_mcp_generation.surface.candidates import (
    discover_occurrence_surface_candidates,
    install_membership_only_operation_units,
)
from src.kg_building_mcp_generation.surface.compile import (
    compile_occurrence_surface,
    is_deterministic_candidate,
)
from src.kg_building_mcp_generation.surface.helpers import LINKER_KIND


def enrich_publish_contract(
    parsed: Mapping[str, Any],
    contract: dict[str, Any],
    *,
    ontology_name: str,
) -> dict[str, Any]:
    """Fill runtime-publish fields the MCP writers need, from parsed T-Box maps."""
    publish = dict(contract.get("ontology_publish_contract") or {})
    if not publish.get("classes"):
        publish["classes"] = [
            {
                "class_local": str(name),
                "class_iri": str((spec or {}).get("iri") or ""),
            }
            for name, spec in (parsed.get("classes") or {}).items()
            if str((spec or {}).get("iri") or "").strip()
        ]
    if not publish.get("object_properties"):
        publish["object_properties"] = [
            {
                "property_local": str(spec.get("predicate_local") or local),
                "property_iri": str(spec.get("predicate_iri") or ""),
                "domain_iris": list(spec.get("domain_iris") or []),
                "range_iris": list(
                    spec.get("range_iris") or spec.get("fixed_runtime_range_iris") or []
                ),
            }
            for local, spec in (contract.get("relationship_tool_contracts") or {}).items()
            if isinstance(spec, Mapping) and str(spec.get("predicate_iri") or "")
        ]
    if contract.get("reuse_policy") and not publish.get("reuse_policy"):
        publish["reuse_policy"] = dict(contract["reuse_policy"])
    top = contract.get("top_entity") or publish.get("top_entity")
    if top:
        publish["top_entity"] = dict(top)
    publish["ontology_name"] = str(ontology_name)
    if not contract.get("namespace_uri"):
        for spec in (parsed.get("classes") or {}).values():
            iri = str((spec or {}).get("iri") or "")
            if iri:
                contract["namespace_uri"] = iri.rstrip("/#").rsplit("/", 1)[0] + "/"
                break
    contract["ontology_publish_contract"] = publish
    return contract


def _decisions_without_llm(candidates: Mapping[str, Any]) -> dict[str, Any]:
    """Bundle owner facets and expose leftover public linkers. No judge call."""
    decisions: list[dict[str, Any]] = []
    for item in candidates.get("candidates") or []:
        if not isinstance(item, Mapping):
            continue
        kind = str(item.get("kind") or "")
        if kind == LINKER_KIND:
            decision = "expose"
        elif is_deterministic_candidate(item) or str(
            item.get("decision_space") or ""
        ).startswith("deterministic"):
            decision = "bundle"
        else:
            decision = "expose" if kind == LINKER_KIND else "bundle"
        decisions.append(
            {
                "candidate_id": str(item.get("candidate_id") or ""),
                "decision": decision,
                "evidence_quotes": [],
                "rationale": "abox_mock_no_llm",
            }
        )
    return {
        "schema_version": "occurrence-surface-decisions.v1",
        "decisions": decisions,
        "llm_judged_count": 0,
    }


def compile_surface_without_llm(
    parsed: Mapping[str, Any],
    contract: dict[str, Any],
    *,
    ontology_name: str,
) -> dict[str, Any]:
    """Return compiled occurrence units plus the OM-2 overlay surface."""
    enrich_publish_contract(parsed, contract, ontology_name=ontology_name)
    install_membership_only_operation_units(parsed=parsed, contract=contract)
    candidates = discover_occurrence_surface_candidates(
        parsed=parsed, contract=contract
    )
    contract["occurrence_surface_candidates"] = candidates
    contract["occurrence_surface_decisions"] = _decisions_without_llm(candidates)
    compiled = compile_occurrence_surface(parsed=parsed, contract=contract)
    contract["occurrence_surface_units"] = compiled
    surface = compile_quantity_surface(compiled, context=None)
    return {"compiled": compiled, "quantity_surface": surface, "contract": contract}


def compile_pack_surface(pack_root: Path, ontology_name: str) -> dict[str, Any]:
    """Load a frozen generation pack and return its compiled occurrence surface.

    Uses ``occurrence_surface_units`` from the pack contract when present.
    Otherwise rediscovers candidates and reapplies the pack's leftover
    decisions. Does not call a judge LLM.
    """
    root = Path(pack_root)
    structure = root / "ontology_structures" / ontology_name
    parsed_path = structure / "parsed.json"
    contract_path = structure / "generation_contract.json"
    if not parsed_path.is_file() or not contract_path.is_file():
        raise FileNotFoundError(
            f"pack {root} is missing ontology_structures/{ontology_name}"
        )
    parsed = json.loads(parsed_path.read_text(encoding="utf-8"))
    contract = copy.deepcopy(json.loads(contract_path.read_text(encoding="utf-8")))
    if not isinstance(parsed, dict) or not isinstance(contract, dict):
        raise ValueError(f"invalid parsed or contract JSON for {ontology_name}")
    units = contract.get("occurrence_surface_units") or {}
    if isinstance(units, dict) and units.get("public_tools"):
        compiled = units
        enrich_publish_contract(parsed, contract, ontology_name=ontology_name)
    else:
        decisions_path = (
            root / "semantic_planning" / ontology_name / "occurrence_surface_decisions.json"
        )
        frozen_decisions = None
        if decisions_path.is_file():
            frozen_decisions = json.loads(decisions_path.read_text(encoding="utf-8"))
        enrich_publish_contract(parsed, contract, ontology_name=ontology_name)
        install_membership_only_operation_units(parsed=parsed, contract=contract)
        candidates = discover_occurrence_surface_candidates(
            parsed=parsed, contract=contract
        )
        contract["occurrence_surface_candidates"] = candidates
        if isinstance(frozen_decisions, dict) and frozen_decisions.get("decisions"):
            contract["occurrence_surface_decisions"] = frozen_decisions
        else:
            contract["occurrence_surface_decisions"] = _decisions_without_llm(candidates)
        compiled = compile_occurrence_surface(parsed=parsed, contract=contract)
        contract["occurrence_surface_units"] = compiled
    sidecar = root / "scripts" / ontology_name / "_om2_quantity_surface.json"
    if sidecar.is_file():
        surface = json.loads(sidecar.read_text(encoding="utf-8"))
        if not isinstance(surface, dict):
            surface = compile_quantity_surface(compiled, context=None)
    else:
        surface = compile_quantity_surface(compiled, context=None)
    return {
        "compiled": compiled,
        "quantity_surface": surface,
        "parsed": parsed,
        "contract": contract,
    }
