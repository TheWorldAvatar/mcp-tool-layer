from __future__ import annotations

from copy import deepcopy
from typing import Any


def _local_name(value: Any) -> str:
    text = str(value or "").strip()
    if "#" in text:
        return text.rsplit("#", 1)[-1]
    return text.rstrip("/").rsplit("/", 1)[-1]


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

    property_iris = {
        str(local): str((spec or {}).get("iri") or "")
        for local, spec in parsed_properties.items()
    }
    assigned_properties: set[str] = set()
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
        unknown_classes = sorted(set(classes) - set(class_iris))
        unknown_properties = sorted(set(object_properties) - set(property_iris))
        if unknown_classes or unknown_properties:
            raise ValueError(
                f"Iteration {iteration.get('iteration_number')} has symbols absent from "
                f"the active T-Box: classes={unknown_classes}, properties={unknown_properties}"
            )
        assigned_properties.update(object_properties)
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

    required_links = list(contract.get("required_links") or [])
    top_entity = contract.get("top_entity") or {}
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
