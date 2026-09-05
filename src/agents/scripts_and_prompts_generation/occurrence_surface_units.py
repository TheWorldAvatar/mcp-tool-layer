"""Compile a public occurrence MCP surface from T-Box structure plus judged facets.

Generator code is ontology-neutral. Class and predicate names enter only as
runtime values from the parsed T-Box, reuse policy, and iteration plan.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any, Mapping

from src.agents.scripts_and_prompts_generation.materialization_operation_units import (
    _base_creator_contracts,
    _local_name,
    _python_name,
    compile_materialization_operation_units,
)
from src.agents.scripts_and_prompts_generation.reuse_policy import (
    prohibited_class_locals,
)


CANDIDATE_SCHEMA = "occurrence-surface-candidates.v1"
DECISION_SCHEMA = "occurrence-surface-decisions.v1"
UNIT_SCHEMA = "occurrence-surface-units.v1"
INSTRUCTION_SCHEMA = "occurrence-surface-instruction.v1"
LOOP_GUARD_SCHEMA = "occurrence-loop-guard.v1"
LOOP_GUARD_FILENAME = "_occurrence_loop_guard.json"
ARGUMENT_OWNERSHIP_SCHEMA = "occurrence-argument-ownership.v1"
ARGUMENT_OWNERSHIP_FILENAME = "_occurrence_argument_ownership.json"

FACET_KINDS = frozenset(
    {
        "owner_quantity",
        "reusable_link",
        "fresh_dependent",
        "nested_reusable_link",
        "parent_link",
        "leftover_root_quantity",
    }
)
LINKER_KIND = "leftover_public_linker"
MEMBERSHIP_KIND = "container_membership"
ROOT_QUANTITY_KIND = "leftover_root_quantity"


def _candidate_id(*parts: str) -> str:
    digest = hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()[:16]
    return f"occurrence-candidate:{digest}"


def collect_extension_bridge_class_iris(
    *,
    contract: Mapping[str, Any] | None = None,
    runtime: Mapping[str, Any] | None = None,
) -> list[str]:
    """Collect human-declared extension bridge class IRIs.

    The only allowed non-T-Box domain fact is the extension's bridge class.
    Incoming object properties are derived later from T-Box domain/range.
    """
    found: list[str] = []
    seen: set[str] = set()

    def add(value: Any) -> None:
        iri = str(value or "").strip()
        if iri.startswith(("http://", "https://")) and iri not in seen:
            seen.add(iri)
            found.append(iri)

    payload = contract or {}
    for value in payload.get("extension_bridge_class_iris") or []:
        add(value)
    for source in (runtime, payload.get("runtime")):
        if not isinstance(source, Mapping):
            continue
        for item in source.get("extensions") or []:
            if not isinstance(item, Mapping):
                continue
            add(item.get("bridge_class_iri") or item.get("target_class_iri"))
            policies = item.get("runtime_policies") or {}
            if isinstance(policies, Mapping):
                target = policies.get("enrichment_target") or {}
                if isinstance(target, Mapping):
                    add(target.get("target_class_iri"))
    return found


def _closure_map(contract: Mapping[str, Any]) -> dict[str, set[str]]:
    publish = contract.get("ontology_publish_contract") or {}
    return {
        str(item.get("class_iri") or ""): {
            str(value) for value in item.get("superclass_iris") or []
        }
        for item in publish.get("subclass_closure") or []
        if str(item.get("class_iri") or "")
    }


def _matches_class(
    class_iri: str,
    target_iris: set[str],
    closure: Mapping[str, set[str]],
) -> bool:
    return class_iri in target_iris or bool(closure.get(class_iri, set()) & target_iris)


def _unique_creatable(
    ranges: set[str],
    by_iri: Mapping[str, Mapping[str, Any]],
    closure: Mapping[str, set[str]],
) -> Mapping[str, Any] | None:
    exact = [creator for iri, creator in by_iri.items() if iri in ranges]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        return None
    matches = [
        creator
        for iri, creator in by_iri.items()
        if bool(closure.get(iri, set()) & ranges)
    ]
    if len(matches) != 1:
        return None
    return matches[0]


def _iteration_maps(
    iteration_plan: Mapping[str, Any] | None,
) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    class_iterations: dict[str, set[str]] = {}
    property_iterations: dict[str, set[str]] = {}
    for raw in (iteration_plan or {}).get("iterations") or []:
        iteration = raw if isinstance(raw, Mapping) else {}
        number = str(iteration.get("iteration_number") or "")
        responsibilities = iteration.get("responsibilities") or {}
        for local in responsibilities.get("classes") or []:
            class_iterations.setdefault(str(local), set()).add(number)
        for local in responsibilities.get("object_properties") or []:
            property_iterations.setdefault(str(local), set()).add(number)
    return class_iterations, property_iterations


def _reusable_by_iri(contract: Mapping[str, Any]) -> dict[str, bool]:
    return {
        str(item.get("class_iri") or ""): bool(item.get("reusable"))
        for item in (contract.get("reuse_policy") or {}).get("classes") or []
        if isinstance(item, Mapping) and str(item.get("class_iri") or "")
    }


def _top_entity(contract: Mapping[str, Any]) -> tuple[str, str]:
    top = contract.get("top_entity") or {}
    return str(top.get("class_local") or ""), str(top.get("class_iri") or "")


def _quantity_range_iri(spec: Mapping[str, Any]) -> str:
    fixed = [str(value) for value in spec.get("fixed_runtime_range_iris") or [] if str(value)]
    return fixed[0] if fixed else ""


def _is_quantity(spec: Mapping[str, Any]) -> bool:
    return bool(_quantity_range_iri(spec))


def _tbox_evidence(
    parsed: Mapping[str, Any],
    *locals_: str,
) -> dict[str, str]:
    class_comments = parsed.get("classes") or {}
    property_comments = parsed.get("properties") or {}
    evidence: dict[str, str] = {}
    for local in locals_:
        if not local:
            continue
        comment = str(
            (class_comments.get(local) or property_comments.get(local) or {}).get(
                "comment"
            )
            or ""
        )
        if comment:
            evidence[local] = comment
    return evidence


def _creators_by_iri(
    parsed: Mapping[str, Any],
    contract: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    return {
        str(item.get("class_iri") or ""): item
        for item in _base_creator_contracts(parsed=parsed, contract=contract)
        if str(item.get("class_iri") or "")
    }


def install_membership_only_operation_units(
    *,
    parsed: Mapping[str, Any],
    contract: dict[str, Any],
    iteration_plan: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Deterministically merge unique ordered-member container membership only."""
    contract["occurrence_surface_include_prohibited_ordered"] = True
    from src.agents.scripts_and_prompts_generation.materialization_operation_units import (
        discover_materialization_operation_candidates,
    )

    candidates = discover_materialization_operation_candidates(
        parsed=parsed,
        contract=contract,
        iteration_plan=iteration_plan,
    )
    membership = [
        item
        for item in candidates.get("candidates") or []
        if item.get("kind") == "container_membership"
    ]
    contract["materialization_operation_candidates"] = {
        "schema_version": candidates.get("schema_version"),
        "candidates": membership,
        "selection_policy": (
            "Occurrence-surface primitives merge only unique ordered-member "
            "container membership. Optional facets are judged separately."
        ),
    }
    contract["materialization_operation_decisions"] = {
        "schema_version": "materialization-operation-decisions.v1",
        "decisions": [
            {
                "candidate_id": str(item.get("candidate_id") or ""),
                "decision": "merge",
                "cardinality": "exactly_one",
                "lifecycle": "existing_reference",
                "evidence_quotes": [],
                "rationale": "Deterministic unique ordered-member membership.",
            }
            for item in membership
        ],
    }
    compiled = compile_materialization_operation_units(
        parsed=parsed,
        contract=contract,
        iteration_plan=iteration_plan,
    )
    contract["materialization_operation_units"] = compiled
    return compiled


def discover_occurrence_surface_candidates(
    *,
    parsed: Mapping[str, Any],
    contract: Mapping[str, Any],
    iteration_plan: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Select structurally legal occurrence facets without deciding them."""
    creators = _base_creator_contracts(parsed=parsed, contract=contract)
    by_iri = {
        str(item.get("class_iri") or ""): item
        for item in creators
        if str(item.get("class_iri") or "")
    }
    closure = _closure_map(contract)
    relationships = contract.get("relationship_tool_contracts") or {}
    reusable = _reusable_by_iri(contract)
    prohibited = prohibited_class_locals(contract.get("reuse_policy"))
    top_local, top_iri = _top_entity(contract)
    member_predicates = {
        str(value).strip()
        for value in (
            (contract.get("ordered_member_profile") or {}).get(
                "individually_linked_object_properties"
            )
            or []
        )
        if str(value).strip()
    }
    candidates: list[dict[str, Any]] = []

    def _append(item: dict[str, Any]) -> None:
        candidates.append(item)

    for creator in creators:
        owner_iri = str(creator.get("class_iri") or "")
        owner_local = str(creator.get("class_local") or "")
        if not owner_iri or (
            owner_local in prohibited and not creator.get("ordered_member")
        ):
            continue
        if creator.get("ordered_member"):
            compatible = [
                relationships[local]
                for local in sorted(member_predicates)
                if local in relationships
                and _matches_class(
                    owner_iri,
                    {
                        str(value)
                        for value in (relationships[local].get("range_iris") or [])
                    },
                    closure,
                )
            ]
            if len(compatible) == 1:
                spec = compatible[0]
                predicate_iri = str(spec.get("predicate_iri") or "")
                predicate_local = str(spec.get("predicate_local") or "")
                _append(
                    {
                        "candidate_id": _candidate_id(
                            MEMBERSHIP_KIND, owner_iri, predicate_iri
                        ),
                        "kind": MEMBERSHIP_KIND,
                        "decision_space": "deterministic_bundle",
                        "owner_class_local": owner_local,
                        "owner_class_iri": owner_iri,
                        "predicate_local": predicate_local,
                        "predicate_iri": predicate_iri,
                        "target_class_local": owner_local,
                        "target_class_iri": owner_iri,
                        "structural_evidence": {
                            "ordered_member": True,
                            "unique_compatible_membership_predicate": True,
                            "single_valued_ordering_property": bool(
                                creator.get("ordering_property_local")
                            ),
                        },
                        "tbox_evidence": _tbox_evidence(
                            parsed, owner_local, predicate_local
                        ),
                    }
                )

    incoming: dict[str, list[tuple[str, Mapping[str, Any]]]] = {}
    for predicate_local, raw_spec in sorted(relationships.items()):
        spec = raw_spec if isinstance(raw_spec, Mapping) else {}
        if predicate_local in member_predicates or _is_quantity(spec):
            continue
        ranges = {str(value) for value in spec.get("range_iris") or [] if str(value)}
        target = _unique_creatable(ranges, by_iri, closure)
        if target is None:
            continue
        target_iri = str(target.get("class_iri") or "")
        incoming.setdefault(target_iri, []).append((str(predicate_local), spec))

    for creator in creators:
        owner_iri = str(creator.get("class_iri") or "")
        owner_local = str(creator.get("class_local") or "")
        if (
            not owner_iri
            or (owner_local in prohibited and not creator.get("ordered_member"))
            or reusable.get(owner_iri) is True
            or (top_iri and owner_iri == top_iri)
        ):
            continue
        if creator.get("ordered_member"):
            continue
        options = incoming.get(owner_iri) or []
        top_options = [
            (predicate_local, spec)
            for predicate_local, spec in options
            if top_iri
            and _matches_class(
                top_iri,
                {str(value) for value in spec.get("domain_iris") or []},
                closure,
            )
        ]
        if len(top_options) == 1:
            predicate_local, spec = top_options[0]
            unique_from_top = True
        elif len(options) == 1:
            predicate_local, spec = options[0]
            unique_from_top = False
        else:
            continue
        _append(
            {
                "candidate_id": _candidate_id(
                    "parent_link",
                    owner_iri,
                    str(spec.get("predicate_iri") or ""),
                ),
                "kind": "parent_link",
                "decision_space": "deterministic_bundle",
                "owner_class_local": owner_local,
                "owner_class_iri": owner_iri,
                "predicate_local": predicate_local,
                "predicate_iri": str(spec.get("predicate_iri") or ""),
                "target_class_local": owner_local,
                "target_class_iri": owner_iri,
                "container_class_iris": list(spec.get("domain_iris") or []),
                "structural_evidence": {
                    "unique_incoming_parent_predicate": len(options) == 1,
                    "unique_incoming_from_top_entity": unique_from_top,
                    "owner_reusable": False,
                    "ordered_member": False,
                },
                "tbox_evidence": _tbox_evidence(parsed, owner_local, predicate_local),
            }
        )

    for predicate_local, raw_spec in sorted(relationships.items()):
        spec = raw_spec if isinstance(raw_spec, Mapping) else {}
        predicate_iri = str(spec.get("predicate_iri") or "")
        domains = {str(value) for value in spec.get("domain_iris") or [] if str(value)}
        ranges = {str(value) for value in spec.get("range_iris") or [] if str(value)}
        owners = [
            creator
            for iri, creator in by_iri.items()
            if _matches_class(iri, domains, closure)
        ]
        if _is_quantity(spec):
            for owner in owners:
                owner_iri = str(owner.get("class_iri") or "")
                owner_local = str(owner.get("class_local") or "")
                if owner_local in prohibited and not owner.get("ordered_member"):
                    continue
                if reusable.get(owner_iri) is True:
                    continue
                if top_iri and owner_iri == top_iri:
                    _append(
                        {
                            "candidate_id": _candidate_id(
                                ROOT_QUANTITY_KIND, owner_iri, predicate_iri
                            ),
                            "kind": ROOT_QUANTITY_KIND,
                            "decision_space": "deterministic_bundle",
                            "owner_class_local": owner_local,
                            "owner_class_iri": owner_iri,
                            "predicate_local": predicate_local,
                            "predicate_iri": predicate_iri,
                            "target_class_iri": _quantity_range_iri(spec),
                            "structural_evidence": {
                                "quantity_range": True,
                                "root_quantity": True,
                                "optional_creator_argument": True,
                            },
                            "tbox_evidence": _tbox_evidence(
                                parsed, owner_local, predicate_local
                            ),
                        }
                    )
                    continue
                _append(
                    {
                        "candidate_id": _candidate_id(
                            "owner_quantity", owner_iri, predicate_iri
                        ),
                        "kind": "owner_quantity",
                        "decision_space": "deterministic_bundle",
                        "owner_class_local": owner_local,
                        "owner_class_iri": owner_iri,
                        "predicate_local": predicate_local,
                        "predicate_iri": predicate_iri,
                        "target_class_iri": _quantity_range_iri(spec),
                        "structural_evidence": {
                            "quantity_range": True,
                            "optional_creator_argument": True,
                        },
                        "tbox_evidence": _tbox_evidence(
                            parsed, owner_local, predicate_local
                        ),
                    }
                )
            continue
        if predicate_local in member_predicates:
            continue
        target = _unique_creatable(ranges, by_iri, closure)
        if target is None:
            continue
        target_iri = str(target.get("class_iri") or "")
        target_local = str(target.get("class_local") or "")
        target_reusable = reusable.get(target_iri) is True
        for owner in owners:
            owner_iri = str(owner.get("class_iri") or "")
            owner_local = str(owner.get("class_local") or "")
            if owner_local in prohibited and not owner.get("ordered_member"):
                continue
            if top_iri and owner_iri == top_iri:
                if target_reusable:
                    _append(
                        {
                            "candidate_id": _candidate_id(
                                LINKER_KIND, owner_iri, predicate_iri, target_iri
                            ),
                            "kind": LINKER_KIND,
                            "decision_space": "deterministic_expose",
                            "owner_class_local": owner_local,
                            "owner_class_iri": owner_iri,
                            "predicate_local": predicate_local,
                            "predicate_iri": predicate_iri,
                            "target_class_local": target_local,
                            "target_class_iri": target_iri,
                            "structural_evidence": {
                                "root_or_existing_subject": True,
                                "target_reusable": True,
                                "label_resolved_object": True,
                            },
                            "tbox_evidence": _tbox_evidence(
                                parsed, owner_local, predicate_local, target_local
                            ),
                        }
                    )
                continue
            if reusable.get(owner_iri) is True:
                continue
            if target_reusable:
                kind = "reusable_link"
            else:
                kind = "fresh_dependent"
            _append(
                {
                    "candidate_id": _candidate_id(
                        kind, owner_iri, predicate_iri, target_iri
                    ),
                    "kind": kind,
                    "decision_space": "deterministic_bundle",
                    "owner_class_local": owner_local,
                    "owner_class_iri": owner_iri,
                    "predicate_local": predicate_local,
                    "predicate_iri": predicate_iri,
                    "target_class_local": target_local,
                    "target_class_iri": target_iri,
                    "structural_evidence": {
                        "target_reusable": target_reusable,
                        "single_creatable_target": True,
                        "optional_creator_argument": True,
                    },
                    "tbox_evidence": _tbox_evidence(
                        parsed, owner_local, predicate_local, target_local
                    ),
                }
            )
            if not target_reusable and target.get("datatype_inputs"):
                candidates[-1]["dependent_datatype_inputs"] = [
                    dict(item) for item in target.get("datatype_inputs") or []
                ]
            hop_owner = target
            hop_iri = target_iri
            for child_local, child_raw in sorted(relationships.items()):
                child = child_raw if isinstance(child_raw, Mapping) else {}
                if _is_quantity(child) or child_local in member_predicates:
                    continue
                child_domains = {
                    str(value) for value in child.get("domain_iris") or [] if str(value)
                }
                if not _matches_class(hop_iri, child_domains, closure):
                    continue
                child_ranges = {
                    str(value) for value in child.get("range_iris") or [] if str(value)
                }
                nested = _unique_creatable(child_ranges, by_iri, closure)
                if nested is None or reusable.get(str(nested.get("class_iri") or "")) is not True:
                    continue
                nested_iri = str(nested.get("class_iri") or "")
                _append(
                    {
                        "candidate_id": _candidate_id(
                            "nested_reusable_link",
                            owner_iri,
                            predicate_iri,
                            str(child.get("predicate_iri") or ""),
                            nested_iri,
                        ),
                        "kind": "nested_reusable_link",
                        "decision_space": "deterministic_bundle",
                        "owner_class_local": owner_local,
                        "owner_class_iri": owner_iri,
                        "predicate_local": predicate_local,
                        "predicate_iri": predicate_iri,
                        "intermediate_class_local": str(
                            hop_owner.get("class_local") or ""
                        ),
                        "intermediate_class_iri": hop_iri,
                        "child_predicate_local": child_local,
                        "child_predicate_iri": str(child.get("predicate_iri") or ""),
                        "target_class_local": str(nested.get("class_local") or ""),
                        "target_class_iri": nested_iri,
                        "structural_evidence": {
                            "one_hop_reusable": True,
                            "intermediate_reusable": target_reusable,
                            "optional_creator_argument": True,
                        },
                        "tbox_evidence": _tbox_evidence(
                            parsed,
                            owner_local,
                            predicate_local,
                            child_local,
                            str(nested.get("class_local") or ""),
                        ),
                    }
                )

    return {
        "schema_version": CANDIDATE_SCHEMA,
        "candidates": candidates,
        "selection_policy": (
            "Code lists every structurally legal occurrence facet and leftover "
            "root linker. Unique ordered-member membership, a unique incoming "
            "parent, owner-local optional quantities, fresh dependents, reusable "
            "descriptors, and one-hop reusable descriptors are deterministically "
            "bundled from T-Box structure. Leftover root reusable links are "
            "deterministically exposed. No candidate is reserved for live "
            "semantic judgement unless a later structural rule marks it llm."
        ),
    }


def _decision_map(decisions: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(item.get("candidate_id") or ""): dict(item)
        for item in decisions.get("decisions") or []
        if isinstance(item, Mapping) and str(item.get("candidate_id") or "")
    }


def _semantic_optional_argument_names(tool: Mapping[str, Any]) -> list[str]:
    """Return stable public arguments that contribute to occurrence identity."""
    names: list[str] = []
    ordering = _python_name(str(tool.get("ordering_property_local") or ""))
    for item in tool.get("datatype_inputs") or []:
        name = _python_name(str(item.get("property_local") or ""))
        if name and name != ordering:
            names.append(name)
    for item in list(tool.get("quantities") or []) + list(
        tool.get("parent_quantities") or []
    ):
        names.append(str(item.get("parameter") or ""))
    for group in ("fresh_dependents", "reusable_links"):
        for item in tool.get(group) or []:
            names.append(str(item.get("label_parameter") or ""))
            names.extend(
                str(value.get("parameter_name") or "")
                for value in item.get("datatype_inputs") or []
            )
    for item in tool.get("nested_reusable_links") or []:
        names.append(str(item.get("label_parameter") or ""))
    return list(dict.fromkeys(name for name in names if name))


def compile_occurrence_surface(
    *,
    parsed: Mapping[str, Any],
    contract: Mapping[str, Any],
    iteration_plan: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Compile the public occurrence surface from candidates plus decisions."""
    creators = _base_creator_contracts(parsed=parsed, contract=contract)
    by_iri = {
        str(item.get("class_iri") or ""): item
        for item in creators
        if str(item.get("class_iri") or "")
    }
    reusable = _reusable_by_iri(contract)
    prohibited = prohibited_class_locals(contract.get("reuse_policy"))
    top_local, top_iri = _top_entity(contract)
    candidate_bundle = contract.get("occurrence_surface_candidates") or {}
    decisions = _decision_map(contract.get("occurrence_surface_decisions") or {})
    candidates = [
        item
        for item in candidate_bundle.get("candidates") or []
        if isinstance(item, Mapping)
    ]
    bundled_predicates: set[str] = set()
    errors: list[str] = []
    datatype_iri_by_local = {
        _local_name(str(item.get("property_iri") or "")): str(
            item.get("property_iri") or ""
        )
        for item in (
            (contract.get("ontology_publish_contract") or {}).get(
                "datatype_properties"
            )
            or []
        )
        if str(item.get("property_iri") or "")
    }

    def _accepted(candidate: Mapping[str, Any]) -> bool:
        if candidate.get("kind") == MEMBERSHIP_KIND:
            return True
        if candidate.get("decision_space") == "deterministic_bundle":
            return True
        if candidate.get("decision_space") == "deterministic_expose":
            return True
        item = decisions.get(str(candidate.get("candidate_id") or ""))
        if item is None:
            return False
        if candidate.get("kind") == LINKER_KIND:
            return str(item.get("decision") or "") == "expose"
        return str(item.get("decision") or "") == "bundle"

    tools: dict[str, dict[str, Any]] = {}
    for creator in creators:
        owner_iri = str(creator.get("class_iri") or "")
        owner_local = str(creator.get("class_local") or "")
        if (
            not owner_iri
            or (owner_local in prohibited and not creator.get("ordered_member"))
            or reusable.get(owner_iri) is True
            or (top_iri and owner_iri == top_iri)
            or creator.get("external_range_class")
        ):
            continue
        tool = {
            "name": f"create_{owner_local}",
            "owner_class_local": owner_local,
            "owner_class_iri": owner_iri,
            "primitive_tool": str(creator.get("public_tool") or f"create_{owner_local}"),
            "ordered_member": bool(creator.get("ordered_member")),
            "ordering_property_local": str(creator.get("ordering_property_local") or ""),
            "ordering_property_iri": str(
                creator.get("ordering_property_iri")
                or datatype_iri_by_local.get(
                    str(creator.get("ordering_property_local") or ""),
                    "",
                )
            ),
            "parent_parameter": "",
            "parent_predicate_local": "",
            "parent_predicate_iri": "",
            "parent_unique_incoming": False,
            "parent_binds_to_session_root": bool(creator.get("ordered_member")),
            "parent_via_primitive": bool(creator.get("ordered_member")),
            "idempotent": bool(creator.get("ordered_member")),
            "parent_quantities": [],
            "datatype_inputs": [
                dict(item) for item in creator.get("datatype_inputs") or []
            ],
            "quantities": [],
            "fresh_dependents": [],
            "reusable_links": [],
            "nested_reusable_links": [],
        }
        if creator.get("ordered_member"):
            tool["parent_parameter"] = "parent_iri"
        tools[owner_iri] = tool

    for candidate in candidates:
        if not _accepted(candidate):
            continue
        kind = str(candidate.get("kind") or "")
        owner_iri = str(candidate.get("owner_class_iri") or "")
        predicate_local = str(candidate.get("predicate_local") or "")
        if kind in {LINKER_KIND, ROOT_QUANTITY_KIND}:
            continue
        tool = tools.get(owner_iri)
        if tool is None:
            if kind == "parent_link":
                continue
            errors.append(
                f"{candidate.get('candidate_id')}: bundled facet has no public owner"
            )
            continue
        if kind == MEMBERSHIP_KIND:
            tool["parent_parameter"] = "parent_iri"
            tool["parent_predicate_local"] = predicate_local
            tool["parent_predicate_iri"] = str(candidate.get("predicate_iri") or "")
            tool["parent_via_primitive"] = True
            tool["parent_binds_to_session_root"] = True
            bundled_predicates.add(predicate_local)
            continue
        if kind == "parent_link":
            evidence = candidate.get("structural_evidence") or {}
            unique_incoming = bool(evidence.get("unique_incoming_parent_predicate"))
            unique_from_top = bool(evidence.get("unique_incoming_from_top_entity"))
            containers = {
                str(value) for value in candidate.get("container_class_iris") or [] if str(value)
            }
            tool["parent_parameter"] = "parent_iri"
            tool["parent_predicate_local"] = predicate_local
            tool["parent_predicate_iri"] = str(candidate.get("predicate_iri") or "")
            tool["parent_unique_incoming"] = bool(unique_incoming and unique_from_top)
            tool["parent_binds_to_session_root"] = bool(
                unique_from_top or (top_iri and top_iri in containers)
            )
            tool["parent_via_primitive"] = False
            if tool["parent_unique_incoming"]:
                tool["idempotent"] = True
            bundled_predicates.add(predicate_local)
            continue
        if kind == "owner_quantity":
            tool["quantities"].append(
                {
                    "parameter": _python_name(predicate_local),
                    "predicate_local": predicate_local,
                    "predicate_iri": str(candidate.get("predicate_iri") or ""),
                    "range_iri": str(candidate.get("target_class_iri") or ""),
                }
            )
            bundled_predicates.add(predicate_local)
            continue
        if kind == "fresh_dependent":
            target_iri = str(candidate.get("target_class_iri") or "")
            target = by_iri.get(target_iri) or {}
            prefix = _python_name(predicate_local)
            tool["fresh_dependents"].append(
                {
                    "label_parameter": f"{prefix}_label",
                    "predicate_local": predicate_local,
                    "predicate_iri": str(candidate.get("predicate_iri") or ""),
                    "target_class_local": str(candidate.get("target_class_local") or ""),
                    "target_class_iri": target_iri,
                    "create_tool": str(target.get("public_tool") or ""),
                    "datatype_inputs": [
                        {
                            **dict(item),
                            "parameter_name": _python_name(
                                f"{prefix}_{item.get('property_local')}"
                            ),
                        }
                        for item in (
                            candidate.get("dependent_datatype_inputs")
                            or target.get("datatype_inputs")
                            or []
                        )
                        if str(item.get("property_local") or "")
                        != str(target.get("ordering_property_local") or "")
                    ],
                }
            )
            bundled_predicates.add(predicate_local)
            continue
        if kind == "reusable_link":
            prefix = _python_name(predicate_local)
            target_iri = str(candidate.get("target_class_iri") or "")
            target = by_iri.get(target_iri) or {}
            datatype_inputs = [
                {
                    **dict(item),
                    "parameter_name": _python_name(
                        f"{prefix}_{item.get('property_local')}"
                    ),
                }
                for item in target.get("datatype_inputs") or []
                if str(item.get("property_local") or "")
            ]
            tool["reusable_links"].append(
                {
                    "label_parameter": f"{prefix}_label",
                    "predicate_local": predicate_local,
                    "predicate_iri": str(candidate.get("predicate_iri") or ""),
                    "target_class_local": str(candidate.get("target_class_local") or ""),
                    "target_class_iri": target_iri,
                    "create_tool": str(target.get("public_tool") or ""),
                    "create_fresh_with_datatypes": bool(datatype_inputs),
                    "datatype_inputs": datatype_inputs,
                }
            )
            bundled_predicates.add(predicate_local)
            continue
        if kind == "nested_reusable_link":
            child_local = str(candidate.get("child_predicate_local") or "")
            tool["nested_reusable_links"].append(
                {
                    "label_parameter": f"{_python_name(child_local)}_label",
                    "parent_predicate_local": predicate_local,
                    "parent_predicate_iri": str(candidate.get("predicate_iri") or ""),
                    "child_predicate_local": child_local,
                    "child_predicate_iri": str(candidate.get("child_predicate_iri") or ""),
                    "intermediate_class_iri": str(
                        candidate.get("intermediate_class_iri") or ""
                    ),
                    "target_class_local": str(candidate.get("target_class_local") or ""),
                    "target_class_iri": str(candidate.get("target_class_iri") or ""),
                }
            )
            bundled_predicates.add(child_local)
            continue
        errors.append(f"{candidate.get('candidate_id')}: unsupported bundled kind {kind}")

    root_quantities = [
        item
        for item in candidates
        if item.get("kind") == ROOT_QUANTITY_KIND and _accepted(item)
    ]
    unique_parent_tools = [
        tool
        for tool in tools.values()
        if tool.get("parent_unique_incoming") and not tool.get("ordered_member")
    ]
    public_linkers: list[dict[str, Any]] = []
    if len(unique_parent_tools) == 1:
        host = unique_parent_tools[0]
        for item in root_quantities:
            predicate_local = str(item.get("predicate_local") or "")
            host.setdefault("parent_quantities", []).append(
                {
                    "parameter": _python_name(predicate_local),
                    "predicate_local": predicate_local,
                    "predicate_iri": str(item.get("predicate_iri") or ""),
                    "range_iri": str(item.get("target_class_iri") or ""),
                }
            )
            bundled_predicates.add(predicate_local)
    else:
        for item in root_quantities:
            predicate_local = str(item.get("predicate_local") or "")
            public_linkers.append(
                {
                    "name": f"link_{_python_name(predicate_local)}",
                    "predicate_local": predicate_local,
                    "predicate_iri": str(item.get("predicate_iri") or ""),
                    "subject_class_local": str(item.get("owner_class_local") or ""),
                    "subject_class_iri": str(item.get("owner_class_iri") or ""),
                    "object_class_local": "",
                    "object_class_iri": str(item.get("target_class_iri") or ""),
                    "quantity_range_iri": str(item.get("target_class_iri") or ""),
                }
            )
            bundled_predicates.add(predicate_local)

    for candidate in candidates:
        if candidate.get("kind") != LINKER_KIND or not _accepted(candidate):
            continue
        predicate_local = str(candidate.get("predicate_local") or "")
        if predicate_local in bundled_predicates:
            continue
        public_linkers.append(
            {
                "name": f"link_{_python_name(predicate_local)}",
                "predicate_local": predicate_local,
                "predicate_iri": str(candidate.get("predicate_iri") or ""),
                "subject_class_local": str(candidate.get("owner_class_local") or ""),
                "subject_class_iri": str(candidate.get("owner_class_iri") or ""),
                "object_class_local": str(candidate.get("target_class_local") or ""),
                "object_class_iri": str(candidate.get("target_class_iri") or ""),
            }
        )
        bundled_predicates.add(predicate_local)

    reusable_classes = [
        {
            "class_local": str(item.get("class_local") or ""),
            "class_iri": str(item.get("class_iri") or ""),
            "create_tool": str(item.get("public_tool") or ""),
        }
        for item in creators
        if reusable.get(str(item.get("class_iri") or "")) is True
        and str(item.get("public_tool") or "")
    ]
    public_tools = sorted(tools.values(), key=lambda item: str(item.get("name") or ""))
    for tool in public_tools:
        parent = str(tool.get("parent_parameter") or "")
        ordering = _python_name(str(tool.get("ordering_property_local") or ""))
        if tool.get("ordered_member") and parent and ordering:
            identity_kind = "ordered"
            identity_args = [parent, ordering]
        elif tool.get("parent_unique_incoming") and parent:
            identity_kind = "unique_parent"
            identity_args = [parent]
        else:
            identity_kind = "semantic_occurrence"
            identity_args = [parent] if parent else []
            identity_args.append("label")
        tool["idempotent"] = True
        tool["identity_contract"] = {
            "kind": identity_kind,
            "identity_args": list(dict.fromkeys(arg for arg in identity_args if arg)),
        }
        if identity_kind == "unique_parent":
            for item in tool.get("fresh_dependents") or []:
                if str(item.get("label_parameter") or "") and str(
                    item.get("create_tool") or ""
                ):
                    item["default_label_from_owner"] = True
            for item in tool.get("reusable_links") or []:
                if item.get("create_fresh_with_datatypes") and str(
                    item.get("label_parameter") or ""
                ):
                    item["default_label_from_owner"] = True
    bridges = set(collect_extension_bridge_class_iris(contract=contract))
    closure = _closure_map(contract)
    if bridges:
        for tool in public_tools:
            for group in ("fresh_dependents", "reusable_links"):
                for item in tool.get(group) or []:
                    target_iri = str(item.get("target_class_iri") or "")
                    if target_iri and _matches_class(target_iri, bridges, closure):
                        item["required_bridge_link"] = True
                        item["default_label_from_owner"] = True
    for linker in public_linkers:
        linker["identity_contract"] = {
            "kind": "semantic_link",
            "identity_args": ["subject_iri", "object_label"],
        }
    compiled = {
        "schema_version": UNIT_SCHEMA,
        "policy_source": "deterministic_tbox_occurrence_surface",
        "public_tools": public_tools,
        "public_linkers": sorted(public_linkers, key=lambda item: str(item.get("name") or "")),
        "reusable_classes": sorted(
            reusable_classes, key=lambda item: str(item.get("class_local") or "")
        ),
        "bundled_predicate_locals": sorted(bundled_predicates),
        "lifecycle_tools": [
            "init_memory",
            "export_memory",
            "inspect_ordered_members",
            "skip_semantic_obligation",
        ],
        "instruction": "",
        "errors": errors,
        "top_entity_class_local": top_local,
        "top_entity_class_iri": top_iri,
    }
    compiled["instruction"] = compile_fallback_instruction(compiled)
    compiled["loop_guard"] = compile_loop_guard_contract(compiled)
    return compiled


def compile_loop_guard_contract(compiled: Mapping[str, Any]) -> dict[str, Any]:
    """Project host-side idempotent identities from the compiled public surface."""
    unique_parent: list[dict[str, Any]] = []
    ordered_members: list[dict[str, Any]] = []
    mutations: list[dict[str, Any]] = []
    for tool in compiled.get("public_tools") or []:
        name = str(tool.get("name") or "")
        identity = tool.get("identity_contract") or {}
        identity_args = [
            str(value) for value in identity.get("identity_args") or [] if str(value)
        ]
        identity_kind = str(identity.get("kind") or "semantic_occurrence")
        if not name:
            continue
        item = {
            "name": name,
            "identity_kind": identity_kind,
            "identity_args": identity_args,
        }
        mutations.append(item)
        if identity_kind == "ordered":
            ordered_members.append({"name": name, "identity_args": identity_args})
        elif identity_kind == "unique_parent":
            unique_parent.append({"name": name, "identity_args": identity_args})
    for linker in compiled.get("public_linkers") or []:
        name = str(linker.get("name") or "")
        identity = linker.get("identity_contract") or {}
        if name:
            mutations.append(
                {
                    "name": name,
                    "identity_kind": str(identity.get("kind") or "semantic_link"),
                    "identity_args": [
                        str(value)
                        for value in identity.get("identity_args") or []
                        if str(value)
                    ],
                }
            )
    unique_parent.sort(key=lambda item: str(item.get("name") or ""))
    ordered_members.sort(key=lambda item: str(item.get("name") or ""))
    mutations.sort(key=lambda item: str(item.get("name") or ""))
    return {
        "schema_version": LOOP_GUARD_SCHEMA,
        "unique_parent_tools": unique_parent,
        "ordered_member_tools": ordered_members,
        "mutation_tools": mutations,
    }


def is_deterministic_candidate(candidate: Mapping[str, Any]) -> bool:
    return (
        str(candidate.get("kind") or "") == MEMBERSHIP_KIND
        or str(candidate.get("decision_space") or "")
        in {"deterministic_bundle", "deterministic_expose"}
    )


def public_tool_names(compiled: Mapping[str, Any]) -> set[str]:
    names = {str(item) for item in compiled.get("lifecycle_tools") or []}
    names.update(str(item.get("name") or "") for item in compiled.get("public_tools") or [])
    names.update(str(item.get("name") or "") for item in compiled.get("public_linkers") or [])
    names.discard("")
    return names


_TOOL_MENTION = re.compile(
    r"\b(create_[A-Za-z0-9_]+|link_[A-Za-z0-9_]+|init_memory|export_memory|"
    r"inspect_ordered_members|skip_semantic_obligation|add_[A-Za-z0-9_]+|"
    r"check_existing_[A-Za-z0-9_]+|"
    r"update_[A-Za-z0-9_]+)\b"
)


def mentioned_tool_names(instruction: str) -> set[str]:
    return set(_TOOL_MENTION.findall(instruction or ""))


def compile_fallback_instruction(compiled: Mapping[str, Any]) -> str:
    """Operational instruction compiled only from the public surface contract."""
    creates = [str(item.get("name") or "") for item in compiled.get("public_tools") or []]
    linkers = [str(item.get("name") or "") for item in compiled.get("public_linkers") or []]
    reusable = [
        str(item.get("class_local") or "")
        for item in compiled.get("reusable_classes") or []
        if str(item.get("class_local") or "")
    ]
    create_list = ", ".join(f"`{name}`" for name in creates) or "the compiled create tools"
    linker_list = (
        ", ".join(f"`{name}`" for name in linkers)
        if linkers
        else "no additional public linkers"
    )
    reusable_list = ", ".join(reusable) if reusable else "the compiled reusable classes"
    required_bridge_labels = [
        str(item.get("label_parameter") or "")
        for tool in compiled.get("public_tools") or []
        for group in ("fresh_dependents", "reusable_links")
        for item in tool.get(group) or []
        if item.get("required_bridge_link") and str(item.get("label_parameter") or "")
    ]
    required_bridge_labels = list(dict.fromkeys(required_bridge_labels))
    required_bridge_text = (
        " Required representation labels compiled from declared extension bridge "
        "classes ("
        + ", ".join(f"`{name}`" for name in required_bridge_labels)
        + ") must be supplied on the owning create_* call; if the heading omits a "
        "distinct name, use the occurrence label. Do not omit that argument to skip "
        "the link. "
        if required_bridge_labels
        else ""
    )
    has_non_root_parent = any(
        item.get("parent_parameter")
        and not item.get("parent_binds_to_session_root", True)
        for item in compiled.get("public_tools") or []
    )
    parent_binding = (
        "When a create_* tool description says parent_iri is the session bound root, "
        "and for every public linker subject_iri, use the exact bound root IRI supplied "
        "by the pipeline. When a create_* tool description says parent_iri is another "
        "created occurrence, pass that occurrence's returned IRI; do not substitute the "
        "bound root. Use returned child handles only as that parent_iri or where an "
        "explicit public argument requests one. "
        if has_non_root_parent
        else (
            "For every public create_* parent_iri and every public linker subject_iri, use the "
            "exact bound root IRI supplied by the pipeline; never pass a created child handle "
            "as that argument. Use returned child handles "
            "only where an explicit public argument requests one. "
        )
    )
    return (
        "The pipeline has already called init_memory for the bound root; do not call "
        "it again. Read each occurrence heading in "
        f"the supplied ledger exactly once and issue the matching create_* call ({create_list}). "
        "Each public create_* binds to exactly one owner class. Headings of different owner "
        "classes remain distinct occurrences even when their labels match, so neither "
        "call satisfies the other. Put every supported detail from that heading into the same "
        "creator call through its compiled optional arguments. Do not split those details into "
        "later calls. Sentinel or empty optional labels mean that facet is absent. "
        f"{required_bridge_text}{parent_binding}Never pass an argument absent "
        "from the selected tool signature. Treat each tool description's allowed-arguments "
        "list and nested ownership paths as authoritative: a nested property's ontology name "
        "is not a keyword unless that exact keyword is listed; use the compiled flat/prefixed "
        "argument on its owning occurrence call. "
        "Take each order value from the heading and do not "
        "invent order positions. A unique parent-owned occurrence is created once; "
        "later calls for the same parent return the committed IRI. Identity fields of a "
        "compiled representation link belong on that owner call. References stay on the owner "
        "that compiled them. Occurrence owners and non-reusable "
        "dependents are always fresh. "
        f"Reusable classes ({reusable_list}) are resolved or created inside the semantic "
        "transaction according to the compiled reuse policy; never create or choose their "
        "IRIs yourself. "
        f"The only public label-resolved linkers are: {linker_list}. "
        "Calls for independent headings may be emitted together in one assistant turn. "
        "Reuse only returned IRIs. A failed optional quantity facet is omitted with a structured "
        "warning while its valid owner remains committed, but the warning remains a repairable "
        "obligation until that same facet succeeds. If the quantity parser explicitly marks "
        "the source value skippable after a parser-verified representation failure, call "
        "skip_semantic_obligation "
        "once with the warning's exact obligation_id and explain why it is unrepresentable. "
        "Other rejected transactions mutate "
        "nothing; correct that occurrence once from the structured error and continue. When "
        "a structured relationship rejection explicitly instructs you to skip the invalid "
        "relationship and no compatible source-grounded repair applies, call "
        "skip_semantic_obligation once with its exact semantic_fingerprint and a concise "
        "reason. Never skip a rejected owner creation. "
        "An already_committed receipt with graph_changed false is not progress. After every "
        "heading has exactly one successful semantic operation, call export_memory. Export "
        "performs graph-only orphan pruning, ordered-member integrity repair, and graph "
        "hygiene without consulting the source ledger or pipeline hints; if it rejects, "
        "correct the reported graph defect and retry export once."
    )
