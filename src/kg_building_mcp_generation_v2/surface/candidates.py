"""Discover structurally legal occurrence-surface candidates."""

from __future__ import annotations

from typing import Any, Mapping

from src.extraction_prompt_generation.compile.operation_units import (
    _base_creator_contracts,
    compile_materialization_operation_units,
    discover_materialization_operation_candidates,
)
from src.extraction_prompt_generation.compile.reuse_policy import (
    prohibited_class_locals,
)
from src.kg_building_mcp_generation_v2.surface.helpers import (
    CANDIDATE_SCHEMA,
    LINKER_KIND,
    MEMBERSHIP_KIND,
    ROOT_QUANTITY_KIND,
    _candidate_id,
    _closure_map,
    _is_quantity,
    _matches_class,
    _member_predicates,
    _quantity_range_iri,
    _reusable_by_iri,
    _tbox_evidence,
    _top_entity,
    _unique_creatable,
    domain_includes_focus,
    extension_focus_iri,
    extension_public_reusable_owner_iris,
    incoming_parent_index,
    select_unique_incoming_parent,
)

def install_membership_only_operation_units(
    *,
    parsed: Mapping[str, Any],
    contract: dict[str, Any],
    iteration_plan: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Deterministically merge unique ordered-member container membership only."""
    contract["occurrence_surface_include_prohibited_ordered"] = True
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
    focus_iri = extension_focus_iri(contract)
    kept_reusable_owners = extension_public_reusable_owner_iris(
        parsed=parsed, contract=contract
    )
    member_predicates = _member_predicates(contract)
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

    incoming = incoming_parent_index(
        relationships=relationships,
        by_iri=by_iri,
        closure=closure,
        member_predicates=member_predicates,
    )

    for creator in creators:
        owner_iri = str(creator.get("class_iri") or "")
        owner_local = str(creator.get("class_local") or "")
        if (
            not owner_iri
            or (owner_local in prohibited and not creator.get("ordered_member"))
            or (top_iri and owner_iri == top_iri)
            or (focus_iri and owner_iri == focus_iri)
        ):
            continue
        if (
            reusable.get(owner_iri) is True
            and owner_iri not in kept_reusable_owners
        ):
            continue
        if creator.get("ordered_member"):
            continue
        options = incoming.get(owner_iri) or []
        selected = select_unique_incoming_parent(
            options, top_iri=top_iri, closure=closure
        )
        if selected is None:
            continue
        predicate_local, spec, unique_from_top = selected
        unique_from_focus = domain_includes_focus(
            spec, focus_iri=focus_iri, closure=closure
        )
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
                    "unique_incoming_from_extension_focus": unique_from_focus,
                    "owner_reusable": reusable.get(owner_iri) is True,
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
                if (
                    reusable.get(owner_iri) is True
                    and owner_iri not in kept_reusable_owners
                ):
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
            if target_iri in kept_reusable_owners and target_iri != owner_iri:
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
            if (
                reusable.get(owner_iri) is True
                and owner_iri not in kept_reusable_owners
            ):
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

