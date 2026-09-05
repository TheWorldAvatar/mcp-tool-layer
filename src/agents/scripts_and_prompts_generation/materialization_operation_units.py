"""Deterministically compile ontology-neutral atomic materialization operations."""

from __future__ import annotations

import hashlib
import re
from typing import Any, Mapping

from src.agents.scripts_and_prompts_generation.reuse_policy import (
    prohibited_class_locals,
)


_PYTHON_TYPE_BY_RANGE = {
    "http://www.w3.org/2001/XMLSchema#string": "str",
    "http://www.w3.org/2001/XMLSchema#boolean": "bool",
    "http://www.w3.org/2001/XMLSchema#integer": "int",
    "http://www.w3.org/2001/XMLSchema#int": "int",
    "http://www.w3.org/2001/XMLSchema#double": "float",
    "http://www.w3.org/2001/XMLSchema#float": "float",
    "http://www.w3.org/2001/XMLSchema#decimal": "float",
}


def _local_name(iri: str) -> str:
    return str(iri or "").rsplit("#", 1)[-1].rsplit("/", 1)[-1].rsplit(":", 1)[-1]


def _python_name(value: str) -> str:
    name = re.sub(r"[^A-Za-z0-9_]", "_", str(value or "").strip())
    if name and name[0].isdigit():
        name = f"_{name}"
    return name


def _base_creator_contracts(
    *, parsed: Mapping[str, Any], contract: Mapping[str, Any]
) -> list[dict[str, Any]]:
    """Compile the legacy per-class creator surface as operation-unit seeds."""
    profile = contract.get("ordered_member_profile") or {}
    abstract_ordered_parents = {
        str(value).strip()
        for value in (profile.get("most_specific_subclass_targets") or {})
        if str(value).strip()
    }
    ordered_classes = {
        str(value).strip()
        for value in profile.get("ordered_member_classes") or []
        if str(value).strip()
    }
    ordering_properties = [
        str(value).strip()
        for value in profile.get("single_valued_ordering_properties") or []
        if str(value).strip()
    ]
    publish = contract.get("ontology_publish_contract") or {}
    datatype_properties = publish.get("datatype_properties") or []
    subclass_closure = {
        str(item.get("class_iri") or ""): {
            str(value) for value in item.get("superclass_iris") or []
        }
        for item in publish.get("subclass_closure") or []
        if str(item.get("class_iri") or "")
    }
    prohibited = prohibited_class_locals(contract.get("reuse_policy"))
    include_prohibited_ordered = bool(
        contract.get("occurrence_surface_include_prohibited_ordered")
    )
    most_specific_targets = (
        (contract.get("ordered_member_profile") or {}).get(
            "most_specific_subclass_targets"
        )
        or {}
    )
    creators: list[dict[str, Any]] = []
    for class_local, raw_spec in sorted((parsed.get("classes") or {}).items()):
        spec = raw_spec or {}
        class_local = str(class_local).strip()
        class_iri = str(spec.get("iri") or "").strip()
        class_comment = str(spec.get("comment") or "").casefold()
        explicitly_non_creatable = (
            "must never be instantiated" in class_comment
            or "must not be instantiated" in class_comment
        )
        skip_prohibited = class_local in prohibited and not (
            include_prohibited_ordered and class_local in ordered_classes
        )
        if (
            not class_local
            or not class_iri
            or spec.get("creatable") is False
            or explicitly_non_creatable
            or skip_prohibited
            or (
                class_local in most_specific_targets
                and not (spec.get("datatype_properties") or {})
                and not (spec.get("object_properties") or {})
            )
            or (
                class_local in abstract_ordered_parents
                and class_local in ordered_classes
            )
        ):
            continue
        datatype_inputs = []
        for item in datatype_properties:
            domains = {str(value) for value in item.get("domain_iris") or []}
            if class_iri not in domains and not (
                subclass_closure.get(class_iri, set()) & domains
            ):
                continue
            property_iri = str(item.get("property_iri") or "")
            property_local = _local_name(property_iri)
            ranges = item.get("range_iris") or [""]
            datatype_inputs.append(
                {
                    "property_local": property_local,
                    "property_iri": property_iri,
                    "range_iri": str(ranges[0]),
                    "python_type": _PYTHON_TYPE_BY_RANGE.get(str(ranges[0]), "str"),
                    "tbox_comment": str(
                        (
                            (parsed.get("properties") or {}).get(property_local)
                            or {}
                        ).get("comment")
                        or ""
                    ).strip(),
                    "required": (
                        class_local in ordered_classes
                        and property_local in ordering_properties
                    ),
                }
            )
        creators.append(
            {
                "class_local": class_local,
                "class_iri": class_iri,
                "public_tool": f"create_{class_local}",
                "fixed_capability_key": class_iri,
                "parent_classes": list(spec.get("parent_classes") or []),
                "ordered_member": class_local in ordered_classes,
                "ordering_property_local": (
                    ordering_properties[0]
                    if class_local in ordered_classes
                    and len(ordering_properties) == 1
                    else ""
                ),
                "datatype_inputs": datatype_inputs,
                "required_edges": [],
                "dependent_entities": [],
            }
        )
    creators.extend(
        {
            "class_local": str((spec or {}).get("class_local") or ""),
            "class_iri": str((spec or {}).get("class_iri") or ""),
            "public_tool": str((spec or {}).get("tool_name") or ""),
            "fixed_capability_key": str((spec or {}).get("class_iri") or ""),
            "parent_classes": [],
            "ordered_member": False,
            "ordering_property_local": "",
            "datatype_inputs": [],
            "required_edges": [],
            "dependent_entities": [],
            "external_range_class": True,
        }
        for spec in contract.get("external_class_creators") or []
        if str((spec or {}).get("class_iri") or "").strip()
        and str((spec or {}).get("tool_name") or "").strip()
    )
    return creators


def _candidate_id(kind: str, owner_iri: str, predicate_iri: str, target_iri: str) -> str:
    digest = hashlib.sha256(
        "\n".join((kind, owner_iri, predicate_iri, target_iri)).encode("utf-8")
    ).hexdigest()[:16]
    return f"operation-candidate:{digest}"


def discover_materialization_operation_candidates(
    *,
    parsed: Mapping[str, Any],
    contract: Mapping[str, Any],
    iteration_plan: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Select structurally plausible atomic-operation candidates without deciding them."""
    creators = _base_creator_contracts(parsed=parsed, contract=contract)
    by_iri = {
        str(item.get("class_iri") or ""): item
        for item in creators
        if str(item.get("class_iri") or "")
    }
    publish = contract.get("ontology_publish_contract") or {}
    closure = {
        str(item.get("class_iri") or ""): {
            str(value) for value in item.get("superclass_iris") or []
        }
        for item in publish.get("subclass_closure") or []
    }
    relationships = contract.get("relationship_tool_contracts") or {}
    class_iterations: dict[str, set[str]] = {}
    property_iterations: dict[str, set[str]] = {}
    linked_iterations: dict[str, set[str]] = {}
    for raw_iteration in (iteration_plan or {}).get("iterations") or []:
        iteration = raw_iteration if isinstance(raw_iteration, Mapping) else {}
        number = str(iteration.get("iteration_number") or "")
        responsibilities = iteration.get("responsibilities") or {}
        for local in responsibilities.get("classes") or []:
            class_iterations.setdefault(str(local), set()).add(number)
        for local in responsibilities.get("object_properties") or []:
            property_iterations.setdefault(str(local), set()).add(number)
        for local in iteration.get("linked_materialization_classes") or []:
            linked_iterations.setdefault(str(local), set()).add(number)
    reusable_by_iri = {
        str(item.get("class_iri") or ""): item.get("reusable")
        for item in (contract.get("reuse_policy") or {}).get("classes") or []
        if isinstance(item, Mapping) and str(item.get("class_iri") or "")
    }
    property_comments = parsed.get("properties") or {}
    class_comments = parsed.get("classes") or {}
    candidates: list[dict[str, Any]] = []
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

    for creator in creators:
        if not creator.get("ordered_member"):
            continue
        owner_iri = str(creator.get("class_iri") or "")
        compatible = [
            relationships[local]
            for local in sorted(member_predicates)
            if local in relationships
            and (
                set(
                    str(value)
                    for value in relationships[local].get("range_iris") or []
                )
                & ({owner_iri} | closure.get(owner_iri, set()))
            )
        ]
        if len(compatible) != 1:
            continue
        spec = compatible[0]
        predicate_iri = str(spec.get("predicate_iri") or "")
        predicate_local = str(spec.get("predicate_local") or "")
        owner_local = str(creator.get("class_local") or "")
        common = class_iterations.get(owner_local, set()) & property_iterations.get(
            predicate_local, set()
        )
        if class_iterations.get(owner_local) and property_iterations.get(
            predicate_local
        ) and not common:
            continue
        candidates.append(
            {
                "candidate_id": _candidate_id(
                    "container_membership", owner_iri, predicate_iri, owner_iri
                ),
                "kind": "container_membership",
                "owner_class_local": owner_local,
                "owner_class_iri": owner_iri,
                "predicate_local": predicate_local,
                "predicate_iri": predicate_iri,
                "target_class_local": owner_local,
                "target_class_iri": owner_iri,
                "container_class_iris": list(spec.get("domain_iris") or []),
                "iteration_ownership": sorted(
                    common
                    or class_iterations.get(owner_local, set())
                    or property_iterations.get(predicate_local, set())
                ),
                "structural_evidence": {
                    "ordered_member": True,
                    "unique_compatible_membership_predicate": True,
                    "single_valued_ordering_property": bool(
                        creator.get("ordering_property_local")
                    ),
                },
                "tbox_evidence": {
                    "owner_comment": str(
                        (class_comments.get(owner_local) or {}).get("comment") or ""
                    ),
                    "predicate_comment": str(
                        (property_comments.get(predicate_local) or {}).get("comment")
                        or ""
                    ),
                },
            }
        )

    required_links = {
        (
            str(item.get("subject_class_iri") or ""),
            str(item.get("predicate_iri") or ""),
            str(item.get("target_class_iri") or ""),
        ): int(item.get("min_count") or 0)
        for item in contract.get("required_links") or []
        if isinstance(item, Mapping)
    }
    cue_pattern = re.compile(
        r"\b(exactly\s+one|fresh|step[- ]local|occurrence|owns?|ownership|must\s+link)\b",
        flags=re.IGNORECASE,
    )
    for predicate_local, raw_spec in sorted(relationships.items()):
        if predicate_local in member_predicates:
            continue
        spec = raw_spec if isinstance(raw_spec, Mapping) else {}
        if spec.get("fixed_runtime_range_iris") or spec.get("external_range_iris"):
            continue
        predicate_iri = str(spec.get("predicate_iri") or "")
        ranges = {
            str(value) for value in spec.get("range_iris") or [] if str(value)
        }
        target_creators = [
            creator
            for iri, creator in by_iri.items()
            if iri in ranges or bool(closure.get(iri, set()) & ranges)
        ]
        if len(target_creators) != 1:
            continue
        target = target_creators[0]
        target_iri = str(target.get("class_iri") or "")
        if reusable_by_iri.get(target_iri) is True:
            continue
        domains = {
            str(value) for value in spec.get("domain_iris") or [] if str(value)
        }
        owners = [
            creator
            for iri, creator in by_iri.items()
            if iri in domains or bool(closure.get(iri, set()) & domains)
        ]
        predicate_comment = str(
            (property_comments.get(predicate_local) or {}).get("comment") or ""
        )
        for owner in owners:
            owner_iri = str(owner.get("class_iri") or "")
            owner_local = str(owner.get("class_local") or "")
            target_local = str(target.get("class_local") or "")
            formal_min = max(
                (
                    count
                    for (subject, predicate, required_target), count in required_links.items()
                    if predicate == predicate_iri
                    and (not subject or subject == owner_iri)
                    and (not required_target or required_target == target_iri)
                ),
                default=0,
            )
            if formal_min < 1 and cue_pattern.search(predicate_comment) is None:
                continue
            common = class_iterations.get(owner_local, set()) & property_iterations.get(
                predicate_local, set()
            )
            target_available = (
                class_iterations.get(target_local, set())
                | linked_iterations.get(target_local, set())
            )
            if iteration_plan and (
                not class_iterations.get(owner_local)
                or not property_iterations.get(predicate_local)
            ):
                continue
            if class_iterations.get(owner_local) and property_iterations.get(
                predicate_local
            ) and not common:
                continue
            if common and target_available and not (common & target_available):
                continue
            candidates.append(
                {
                    "candidate_id": _candidate_id(
                        "owned_dependent", owner_iri, predicate_iri, target_iri
                    ),
                    "kind": "owned_dependent",
                    "owner_class_local": owner_local,
                    "owner_class_iri": owner_iri,
                    "predicate_local": predicate_local,
                    "predicate_iri": predicate_iri,
                    "target_class_local": target_local,
                    "target_class_iri": target_iri,
                    "iteration_ownership": sorted(
                        common
                        or class_iterations.get(owner_local, set())
                        or property_iterations.get(predicate_local, set())
                    ),
                    "structural_evidence": {
                        "formal_min_count": formal_min,
                        "target_reusable": reusable_by_iri.get(target_iri),
                        "same_iteration_path": True,
                        "single_creatable_target": True,
                    },
                    "tbox_evidence": {
                        "owner_comment": str(
                            (class_comments.get(owner_local) or {}).get("comment")
                            or ""
                        ),
                        "predicate_comment": predicate_comment,
                        "target_comment": str(
                            (class_comments.get(target_local) or {}).get("comment")
                            or ""
                        ),
                    },
                }
            )
    return {
        "schema_version": "materialization-operation-candidates.v1",
        "candidates": candidates,
        "selection_policy": (
            "Code selects only structurally legal candidates; no candidate is merged "
            "without a separately validated semantic judgement."
        ),
    }


def compile_materialization_operation_units(
    *,
    parsed: Mapping[str, Any],
    contract: Mapping[str, Any],
    iteration_plan: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Compile atomic units from role-based policy and T-Box-derived contracts."""
    creators = _base_creator_contracts(parsed=parsed, contract=contract)
    by_iri = {
        str(item.get("class_iri") or ""): item
        for item in creators
        if str(item.get("class_iri") or "")
    }
    publish = contract.get("ontology_publish_contract") or {}
    closure = {
        str(item.get("class_iri") or ""): {
            str(value) for value in item.get("superclass_iris") or []
        }
        for item in publish.get("subclass_closure") or []
    }
    relationships = contract.get("relationship_tool_contracts") or {}
    candidate_bundle = contract.get("materialization_operation_candidates") or {}
    candidates_by_id = {
        str(item.get("candidate_id") or ""): item
        for item in candidate_bundle.get("candidates") or []
        if isinstance(item, Mapping) and str(item.get("candidate_id") or "")
    }
    decision_bundle = contract.get("materialization_operation_decisions") or {}
    accepted_candidates = [
        candidates_by_id[str(item.get("candidate_id") or "")]
        for item in decision_bundle.get("decisions") or []
        if isinstance(item, Mapping)
        and str(item.get("decision") or "") == "merge"
        and str(item.get("candidate_id") or "") in candidates_by_id
    ]
    accepted_membership_owner_iris = {
        str(item.get("owner_class_iri") or "")
        for item in accepted_candidates
        if item.get("kind") == "container_membership"
    }
    policy = {
        "merge_ordered_membership": bool(accepted_membership_owner_iris),
        "owned_dependents": [
            {
                "owner_class_iri": item.get("owner_class_iri"),
                "predicate_iri": item.get("predicate_iri"),
                "dependent_class_iri": item.get("target_class_iri"),
                "cardinality": "exactly_one",
                "lifecycle": "fresh_per_owner",
                "exclusive_target": bool(
                    item.get("exclusive_target", True)
                ),
            }
            for item in accepted_candidates
            if item.get("kind") == "owned_dependent"
        ],
    }
    merged_predicates: set[str] = set()
    errors: list[str] = []
    class_iterations: dict[str, set[str]] = {}
    property_iterations: dict[str, set[str]] = {}
    linked_iterations: dict[str, set[str]] = {}
    for raw_iteration in (iteration_plan or {}).get("iterations") or []:
        iteration = raw_iteration if isinstance(raw_iteration, Mapping) else {}
        number = str(iteration.get("iteration_number") or "")
        responsibilities = iteration.get("responsibilities") or {}
        for local in responsibilities.get("classes") or []:
            class_iterations.setdefault(str(local), set()).add(number)
        for local in responsibilities.get("object_properties") or []:
            property_iterations.setdefault(str(local), set()).add(number)
        for local in iteration.get("linked_materialization_classes") or []:
            linked_iterations.setdefault(str(local), set()).add(number)

    if policy.get("merge_ordered_membership") is True:
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
        candidates = [
            relationships[local]
            for local in sorted(member_predicates)
            if local in relationships
        ]
        for creator in creators:
            if not creator.get("ordered_member"):
                continue
            owner_iri = str(creator.get("class_iri") or "")
            if owner_iri not in accepted_membership_owner_iris:
                continue
            compatible = [
                spec
                for spec in candidates
                if set(str(value) for value in spec.get("range_iris") or [])
                & ({owner_iri} | closure.get(owner_iri, set()))
            ]
            if len(compatible) != 1:
                errors.append(
                    f"ordered owner {owner_iri} requires exactly one compatible membership predicate"
                )
                continue
            spec = compatible[0]
            predicate_local = str(spec.get("predicate_local") or "")
            owner_iterations = class_iterations.get(
                str(creator.get("class_local") or ""), set()
            )
            predicate_iterations = property_iterations.get(predicate_local, set())
            if (
                owner_iterations
                and predicate_iterations
                and owner_iterations.isdisjoint(predicate_iterations)
            ):
                errors.append(
                    f"ordered owner and membership predicate cross iteration boundaries: "
                    f"{owner_iri} / {predicate_local}"
                )
                continue
            creator["required_edges"].append(
                {
                    "role": "container_membership",
                    "predicate_local": predicate_local,
                    "predicate_iri": str(spec.get("predicate_iri") or ""),
                    "direction": "container_as_subject_owner_as_object",
                    "target_resolution": "existing_iri_parameter",
                    "parameter_name": "parent_iri",
                    "parameter_type": "str",
                    "container_class_iris": list(spec.get("domain_iris") or []),
                    "required": True,
                    "iteration_ownership": sorted(
                        owner_iterations & predicate_iterations
                        or owner_iterations
                        or predicate_iterations
                    ),
                }
            )
            merged_predicates.add(predicate_local)

    for index, raw in enumerate(policy.get("owned_dependents") or []):
        item = raw if isinstance(raw, Mapping) else {}
        owner_iri = str(item.get("owner_class_iri") or "").strip()
        predicate_iri = str(item.get("predicate_iri") or "").strip()
        dependent_iri = str(item.get("dependent_class_iri") or "").strip()
        cardinality = str(item.get("cardinality") or "").strip()
        lifecycle = str(item.get("lifecycle") or "").strip()
        predicate_local = _local_name(predicate_iri)
        owner = by_iri.get(owner_iri)
        dependent = by_iri.get(dependent_iri)
        relationship = relationships.get(predicate_local) or {}
        if cardinality != "exactly_one" or lifecycle != "fresh_per_owner":
            errors.append(
                f"owned_dependents[{index}] must be exactly_one and fresh_per_owner"
            )
            continue
        if owner is None or dependent is None:
            errors.append(f"owned_dependents[{index}] references a non-creatable class")
            continue
        if str(relationship.get("predicate_iri") or "") != predicate_iri:
            errors.append(f"owned_dependents[{index}] predicate is not contract-bound")
            continue
        owner_iterations = class_iterations.get(
            str(owner.get("class_local") or ""), set()
        )
        predicate_iterations = property_iterations.get(predicate_local, set())
        dependent_iterations = (
            class_iterations.get(str(dependent.get("class_local") or ""), set())
            | linked_iterations.get(str(dependent.get("class_local") or ""), set())
        )
        common_iterations = owner_iterations & predicate_iterations
        if (
            owner_iterations
            and predicate_iterations
            and not common_iterations
        ):
            errors.append(
                f"owned_dependents[{index}] owner and predicate cross iteration boundaries"
            )
            continue
        if common_iterations and dependent_iterations and not (
            common_iterations & dependent_iterations
        ):
            errors.append(
                f"owned_dependents[{index}] dependent has no same-iteration materialization path"
            )
            continue
        domains = {str(value) for value in relationship.get("domain_iris") or []}
        ranges = {str(value) for value in relationship.get("range_iris") or []}
        if owner_iri not in domains and not (closure.get(owner_iri, set()) & domains):
            errors.append(f"owned_dependents[{index}] owner is outside predicate domain")
            continue
        if dependent_iri not in ranges and not (
            closure.get(dependent_iri, set()) & ranges
        ):
            errors.append(f"owned_dependents[{index}] dependent is outside predicate range")
            continue
        prefix = _python_name(f"owned_{predicate_local}")
        dependent_inputs = [
            {
                **dict(datatype),
                "parameter_name": _python_name(
                    f"{prefix}_{datatype.get('property_local')}"
                ),
            }
            for datatype in dependent.get("datatype_inputs") or []
            if str(datatype.get("property_local") or "")
            != str(dependent.get("ordering_property_local") or "")
        ]
        edge = {
            "role": "owned_dependent",
            "predicate_local": predicate_local,
            "predicate_iri": predicate_iri,
            "direction": "owner_as_subject_dependent_as_object",
            "target_resolution": "same_operation_create",
            "required": True,
            "cardinality": cardinality,
            "lifecycle": lifecycle,
            "exclusive_target": item.get("exclusive_target") is True,
            "exclusive_predicate_iris": sorted(
                {
                    str(candidate.get("predicate_iri") or "")
                    for candidate in relationships.values()
                    if (
                        dependent_iri
                        in {
                            str(value)
                            for value in candidate.get("range_iris") or []
                        }
                        or bool(
                            closure.get(dependent_iri, set())
                            & {
                                str(value)
                                for value in candidate.get("range_iris") or []
                            }
                        )
                    )
                    and str(candidate.get("predicate_iri") or "")
                }
            )
            if item.get("exclusive_target") is True
            else [],
            "dependent_class_local": str(dependent.get("class_local") or ""),
            "dependent_class_iri": dependent_iri,
            "dependent_fixed_capability_key": str(
                dependent.get("fixed_capability_key") or dependent_iri
            ),
            "iteration_ownership": sorted(
                common_iterations
                or owner_iterations
                or predicate_iterations
                or dependent_iterations
            ),
            "label_parameter": f"{prefix}_label",
            "datatype_inputs": dependent_inputs,
        }
        owner["required_edges"].append(edge)
        owner["dependent_entities"].append(
            {
                "role": "owned_dependent",
                "class_local": str(dependent.get("class_local") or ""),
                "class_iri": dependent_iri,
                "label_parameter": edge["label_parameter"],
                "datatype_inputs": dependent_inputs,
                "cardinality": cardinality,
                "lifecycle": lifecycle,
            }
        )
        merged_predicates.add(predicate_local)

    units = [
        {
            "operation_id": f"create:{item.get('class_iri')}",
            "public_tool": item.get("public_tool"),
            "owner_class_local": item.get("class_local"),
            "owner_class_iri": item.get("class_iri"),
            "creator_contract": item,
            "atomicity_policy": {
                "prevalidate_all_before_mutation": True,
                "rollback_on_failure": bool(item.get("required_edges")),
                "forbidden_partial_graph": True,
            },
        }
        for item in creators
    ]
    return {
        "schema_version": "materialization-operation-units.v1",
        "policy_source": "deterministic_tbox_candidates_plus_validated_llm_decisions",
        "units": units,
        "merged_predicate_locals": sorted(merged_predicates),
        "errors": errors,
        "iteration_plan_present": bool(iteration_plan),
        "inference_mode": (
            "accepted_atomic" if accepted_candidates else "legacy_split"
        ),
        "decision_schema_version": str(
            decision_bundle.get("schema_version") or ""
        ),
    }


def operation_creator_contracts(compiled: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        dict(unit.get("creator_contract") or {})
        for unit in compiled.get("units") or []
        if unit.get("creator_contract")
    ]


def standalone_relationship_tool_contracts(
    relationship_contracts: Mapping[str, Any],
    compiled: Mapping[str, Any],
) -> dict[str, Any]:
    merged = {
        str(value)
        for value in compiled.get("merged_predicate_locals") or []
        if str(value)
    }
    return {
        str(local): dict(spec or {})
        for local, spec in relationship_contracts.items()
        if str(local) not in merged
    }
