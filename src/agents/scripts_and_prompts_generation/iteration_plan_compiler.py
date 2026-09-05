from __future__ import annotations

from copy import deepcopy
from typing import Any


def _local_name(value: Any) -> str:
    text = str(value or "").strip()
    if "#" in text:
        return text.rsplit("#", 1)[-1]
    return text.rstrip("/").rsplit("/", 1)[-1]


def _namespace(value: Any) -> str:
    text = str(value or "").strip()
    if "#" in text:
        return text.rsplit("#", 1)[0] + "#"
    return text.rsplit("/", 1)[0] + "/" if "/" in text else ""


def _declared_materialization_classes(iteration: dict[str, Any]) -> list[str]:
    return [
        str(value).strip()
        for value in iteration.get("linked_materialization_classes") or []
        if str(value).strip()
    ]


def _ancestor_locals(class_local: str, parsed_classes: dict[str, Any]) -> set[str]:
    seen: set[str] = set()
    stack = [str(class_local or "").strip()]
    while stack:
        current = stack.pop()
        if not current or current in seen:
            continue
        seen.add(current)
        for parent in (parsed_classes.get(current) or {}).get("parent_classes") or []:
            local = str(parent).strip()
            if local:
                stack.append(local)
    seen.discard(str(class_local or "").strip())
    return seen


def _domains_owned_by_iteration(
    domains: set[str],
    owned_classes: set[str],
    parsed_classes: dict[str, Any],
) -> bool:
    if not domains:
        return False
    for domain in domains:
        if domain in owned_classes:
            continue
        if any(
            domain in _ancestor_locals(owned, parsed_classes)
            for owned in owned_classes
        ):
            continue
        return False
    return True


def _non_reusable_class_locals(contract: dict[str, Any]) -> set[str]:
    return {
        str(item.get("class_local") or "").strip()
        for item in (contract.get("reuse_policy") or {}).get("classes") or []
        if isinstance(item, dict)
        and item.get("reusable") is False
        and str(item.get("class_local") or "").strip()
    }


def _infer_linked_materialization_classes(
    *,
    object_properties: list[str],
    owned_classes: set[str],
    earlier_classes: set[str],
    parsed_classes: dict[str, Any],
    parsed_properties: dict[str, Any],
    contract: dict[str, Any],
) -> list[str]:
    """Infer step-local materialization classes from earlier non-reusable ranges."""
    non_reusable = _non_reusable_class_locals(contract)
    inferred: set[str] = set()
    for property_local in object_properties:
        spec = parsed_properties.get(property_local) or {}
        if str(spec.get("kind") or "").strip() != "object":
            continue
        range_local = _local_name(spec.get("range"))
        if range_local not in earlier_classes or range_local not in non_reusable:
            continue
        domains = {
            _local_name(value)
            for value in (spec.get("domains") or [spec.get("domain")])
            if _local_name(value)
        }
        if not _domains_owned_by_iteration(domains, owned_classes, parsed_classes):
            continue
        inferred.add(range_local)
    return sorted(inferred)


def _classes_with_creator_surface(
    classes: list[str],
    *,
    parsed: dict[str, Any],
    contract: dict[str, Any],
) -> list[str]:
    from types import SimpleNamespace

    from src.agents.scripts_and_prompts_generation.materialization_closure import (
        creator_surface_class_locals,
        restrict_classes_to_creator_surface,
    )

    return restrict_classes_to_creator_surface(
        classes,
        creator_surface_class_locals(
            SimpleNamespace(parsed=parsed, contract=contract)
        ),
    )


def compile_iteration_plan(
    *,
    blueprint: dict[str, Any],
    parsed: dict[str, Any],
    contract: dict[str, Any],
    ontology_name: str,
    blueprint_provenance: dict[str, Any],
) -> dict[str, Any]:
    """Compile scheduling intent against the active T-Box semantic surface."""
    iterations = deepcopy(blueprint.get("iterations") or [])
    if not isinstance(iterations, list):
        raise ValueError("Iteration blueprint must contain an iterations array")

    parsed_classes = parsed.get("classes") or {}
    parsed_properties = parsed.get("properties") or {}
    class_iris = {
        str(local): str((spec or {}).get("iri") or "")
        for local, spec in parsed_classes.items()
    }
    for spec in contract.get("external_class_creators") or []:
        local = str((spec or {}).get("class_local") or "").strip()
        iri = str((spec or {}).get("class_iri") or "").strip()
        if local and iri:
            class_iris.setdefault(local, iri)
    relationship_contracts = contract.get("relationship_tool_contracts") or {}
    relationship_specs = (
        relationship_contracts.values()
        if isinstance(relationship_contracts, dict)
        else relationship_contracts
    )
    for spec in relationship_specs:
        if not isinstance(spec, dict):
            continue
        for iri in spec.get("fixed_runtime_range_iris") or []:
            local = _local_name(iri)
            if local:
                class_iris.setdefault(local, str(iri))

    property_iris = {
        str(local): str((spec or {}).get("iri") or "")
        for local, spec in parsed_properties.items()
    }
    assigned_classes: set[str] = set()
    assigned_properties: set[str] = set()
    class_slot_kind: dict[str, str] = {}
    property_slot_kind: dict[str, str] = {}
    compiled: list[dict[str, Any]] = []
    for raw in iterations:
        if not isinstance(raw, dict):
            raise ValueError("Every iteration blueprint entry must be an object")
        iteration = deepcopy(raw)
        responsibilities = iteration.get("responsibilities") or {}
        classes = [
            str(value).strip()
            for value in responsibilities.get("classes") or []
            if str(value).strip()
        ]
        object_properties = [
            str(value).strip()
            for value in responsibilities.get("object_properties") or []
            if str(value).strip()
        ]
        repeated_classes = sorted(
            {local for local in classes if classes.count(local) > 1}
        )
        repeated_properties = sorted(
            {
                local
                for local in object_properties
                if object_properties.count(local) > 1
            }
        )
        if repeated_classes or repeated_properties:
            raise ValueError(
                "Iteration ownership lists must not contain duplicates: "
                f"classes={repeated_classes}, properties={repeated_properties}"
            )
        classes = sorted(classes)
        object_properties = sorted(object_properties)
        materialization_classes = _declared_materialization_classes(iteration)
        if not materialization_classes:
            materialization_classes = _infer_linked_materialization_classes(
                object_properties=object_properties,
                owned_classes=set(classes),
                earlier_classes=set(assigned_classes),
                parsed_classes=parsed_classes,
                parsed_properties=parsed_properties,
                contract=contract,
            )
        unknown_classes = sorted(set(classes) - set(class_iris))
        unknown_materialization = sorted(set(materialization_classes) - set(class_iris))
        unknown_properties = sorted(set(object_properties) - set(property_iris))
        if unknown_classes or unknown_materialization or unknown_properties:
            raise ValueError(
                f"Iteration {iteration.get('iteration_number')} has symbols absent from "
                f"the active T-Box: classes={unknown_classes}, "
                f"linked_materialization_classes={unknown_materialization}, "
                f"properties={unknown_properties}"
            )
        non_object_properties = sorted(
            {
                local
                for local in object_properties
                if str((parsed_properties.get(local) or {}).get("kind") or "")
                and str((parsed_properties.get(local) or {}).get("kind") or "")
                != "object"
            }
        )
        if non_object_properties:
            raise ValueError(
                f"Iteration {iteration.get('iteration_number')} assigns non-object "
                "properties as object_properties: "
                + ", ".join(non_object_properties)
            )
        duplicate_classes = sorted(set(classes) & assigned_classes)
        duplicate_properties = sorted(set(object_properties) & assigned_properties)
        if duplicate_classes or duplicate_properties:
            raise ValueError(
                "Iteration ownership must be unique across slots: "
                f"classes={duplicate_classes}, properties={duplicate_properties}"
            )
        classes = _classes_with_creator_surface(
            classes,
            parsed=parsed,
            contract=contract,
        )
        materialization_classes = _classes_with_creator_surface(
            materialization_classes,
            parsed=parsed,
            contract=contract,
        )
        iteration["linked_materialization_classes"] = materialization_classes
        if (
            not classes
            and not object_properties
            and str(iteration.get("slot_kind") or "").strip()
        ):
            raise ValueError(
                f"Iteration {iteration.get('iteration_number')} has an empty semantic "
                "scope: at least one class or object property is required before prompt "
                "or script generation"
            )
        assigned_classes.update(classes)
        assigned_properties.update(object_properties)
        slot_kind = str(iteration.get("slot_kind") or "").strip()
        class_slot_kind.update({local: slot_kind for local in classes})
        property_slot_kind.update(
            {local: slot_kind for local in object_properties}
        )
        iteration["responsibilities"] = {
            **responsibilities,
            "classes": classes,
            "object_properties": object_properties,
        }
        iteration["semantic_scope"] = {
            "source": "active_tbox",
            "classes": [
                {"local": local, "iri": class_iris[local]} for local in classes
            ],
            "object_properties": [
                {"local": local, "iri": property_iris[local]}
                for local in object_properties
            ],
        }
        compiled.append(iteration)

    top_entity = contract.get("top_entity") or {}
    top_local = _local_name(top_entity.get("class_iri")) or str(
        top_entity.get("class_local") or ""
    ).strip()
    if top_local and top_local in assigned_classes:
        raise ValueError(
            "Iteration 1 exclusively owns the top entity class; downstream plan "
            f"reassigned {top_local}"
        )
    enforce_deterministic_slots = any(class_slot_kind.values()) or any(
        property_slot_kind.values()
    )
    external_classes = {
        str((item or {}).get("class_local") or "").strip()
        for item in contract.get("external_class_creators") or []
        if str((item or {}).get("class_local") or "").strip()
    }
    assigned_external = (
        sorted(assigned_classes & external_classes)
        if enforce_deterministic_slots
        else []
    )
    if assigned_external:
        raise ValueError(
            "Supporting/external creator classes cannot enter primary iteration "
            "ownership: " + ", ".join(assigned_external)
        )
    scope_root = contract.get("extension_focus") or top_entity
    primary_namespace = _namespace(scope_root.get("class_iri"))
    foreign_classes = (
        sorted(
            local
            for local in assigned_classes
            if primary_namespace
            and _namespace(class_iris.get(local)) != primary_namespace
        )
        if enforce_deterministic_slots
        else []
    )
    if foreign_classes:
        raise ValueError(
            "Iteration ownership must remain in the active primary T-Box namespace: "
            + ", ".join(foreign_classes)
        )

    ordered_classes = {
        str(value).strip()
        for value in (
            (contract.get("ordered_member_profile") or {}).get(
                "ordered_member_classes"
            )
            or []
        )
        if str(value).strip()
    }
    misplaced_ordered_classes = (
        sorted(
            local
            for local in assigned_classes & ordered_classes
            if class_slot_kind.get(local) != "ordered"
        )
        if enforce_deterministic_slots
        else []
    )
    misplaced_ordered_properties = (
        sorted(
            local
            for local in assigned_properties
            if (
                {
                    _local_name(value)
                    for value in (
                        (parsed_properties.get(local) or {}).get("domains")
                        or [(parsed_properties.get(local) or {}).get("domain")]
                    )
                    if _local_name(value)
                }
                & ordered_classes
                or _local_name(
                    (parsed_properties.get(local) or {}).get("range")
                )
                in ordered_classes
            )
            and property_slot_kind.get(local) != "ordered"
        )
        if enforce_deterministic_slots
        else []
    )
    if misplaced_ordered_classes or misplaced_ordered_properties:
        raise ValueError(
            "Ordered-member closure must be owned by the ordered slot: "
            f"classes={misplaced_ordered_classes}, "
            f"properties={misplaced_ordered_properties}"
        )
    non_reusable = {
        str(item.get("class_local") or "").strip()
        for item in (contract.get("reuse_policy") or {}).get("classes") or []
        if isinstance(item, dict)
        and item.get("reusable") is False
        and str(item.get("class_local") or "").strip()
    }
    top_ranges = {
        _local_name((spec or {}).get("range"))
        for spec in parsed_properties.values()
        if top_local
        in {
            _local_name(value)
            for value in (
                (spec or {}).get("domains") or [(spec or {}).get("domain")]
            )
            if _local_name(value)
        }
    }
    ordered_ranges = {
        _local_name((spec or {}).get("range"))
        for spec in parsed_properties.values()
        if {
            _local_name(value)
            for value in (
                (spec or {}).get("domains") or [(spec or {}).get("domain")]
            )
            if _local_name(value)
        }
        & ordered_classes
    }
    misplaced_bridges = (
        sorted(
            local
            for local in (
                top_ranges & ordered_ranges & non_reusable & assigned_classes
            )
            if class_slot_kind.get(local) != "foundation"
        )
        if enforce_deterministic_slots
        else []
    )
    if misplaced_bridges:
        raise ValueError(
            "Non-reusable ordered-reference bridges must be materialized in the "
            "foundation slot: " + ", ".join(misplaced_bridges)
        )

    required_links = list(contract.get("required_links") or [])
    top_class_iri = str(top_entity.get("class_iri") or "").strip()
    required_property_locals = {
        _local_name((spec or {}).get("predicate_iri"))
        for spec in required_links
        if _local_name((spec or {}).get("predicate_iri"))
        and (
            not top_class_iri
            or not (spec or {}).get("subject_class_iri")
            or str((spec or {}).get("subject_class_iri")) == top_class_iri
        )
    }
    unassigned_required = sorted(required_property_locals - assigned_properties)
    if unassigned_required:
        raise ValueError(
            "Iteration scheduling intent omits T-Box-required object properties: "
            + ", ".join(unassigned_required)
        )

    explicit_property_owner = {
        local: str(iteration.get("iteration_number") or "")
        for iteration in compiled
        for local in (
            (iteration.get("responsibilities") or {}).get(
                "object_properties"
            )
            or []
        )
    }
    for iteration in compiled:
        iteration_number = str(iteration.get("iteration_number") or "")
        owned = set(
            (iteration.get("responsibilities") or {}).get("classes") or []
        )
        closure_classes = owned | set(_declared_materialization_classes(iteration))
        attached: dict[str, set[str]] = {}
        for class_local in sorted(closure_classes):
            class_spec = parsed_classes.get(class_local) or {}
            for key in ("datatype_properties", "object_properties"):
                for local in (class_spec.get(key) or {}):
                    property_local = str(local).strip()
                    if property_local:
                        attached.setdefault(property_local, set()).add(
                            class_local
                        )
        property_closure = []
        for local, source_classes in sorted(attached.items()):
            foreign_owner = explicit_property_owner.get(local)
            if foreign_owner and foreign_owner != iteration_number:
                continue
            spec = parsed_properties.get(local) or {}
            property_closure.append(
                {
                    "local": local,
                    "iri": str(spec.get("iri") or ""),
                    "kind": str(spec.get("kind") or "unknown"),
                    "source_classes": sorted(source_classes),
                    "explicit_owner": foreign_owner == iteration_number,
                }
            )
        iteration["semantic_scope"]["property_closure"] = property_closure

    return {
        "schema_version": "compiled-iteration-plan.v1",
        "ontology": ontology_name,
        "description": str(blueprint.get("description") or ""),
        "iterations": compiled,
        "provenance": {
            "semantic_scope": "active_tbox",
            "scheduling_intent": blueprint_provenance,
            "required_link_coverage": sorted(required_property_locals),
        },
    }
