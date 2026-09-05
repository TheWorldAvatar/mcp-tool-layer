"""Derive runtime semantic contracts from a primary T-Box.

These contracts used to be hand-copied into domain config. Domain config now
holds only human orchestration; this module compiles the T-Box equivalents.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from src.agents.scripts_and_prompts_generation.generation_contracts import (
    build_ontology_publish_contract_from_tbox,
)
from src.agents.scripts_and_prompts_generation.ttl_parser import (
    extract_ontology_integrity_profile,
    parse_ontology_ttl,
)

def _local_name(value: Any) -> str:
    text = str(value or "").rstrip("/")
    if not text:
        return ""
    if "#" in text:
        return text.rsplit("#", 1)[-1]
    return text.rsplit("/", 1)[-1]


def validate_required_link_bindings(bindings: list[Any]) -> None:
    """Reject malformed required-link bindings after T-Box derivation."""
    if not isinstance(bindings, list):
        raise ValueError("required_link_bindings must be an array")
    seen: set[str] = set()
    for index, item in enumerate(bindings):
        if not isinstance(item, dict):
            raise ValueError(f"required_link_bindings[{index}] must be an object")
        predicate_iri = str(item.get("predicate_iri") or "").strip()
        identity_slot = str(item.get("identity_slot") or "").strip()
        materialization_iteration = item.get("materialization_iteration")
        if not predicate_iri or not identity_slot:
            raise ValueError(
                f"required_link_bindings[{index}] requires predicate_iri and identity_slot"
            )
        if not (
            identity_slot.startswith("{")
            and identity_slot.endswith("}")
            and len(identity_slot) > 2
        ):
            raise ValueError(
                f"required_link_bindings[{index}].identity_slot "
                "must be a single-braced runtime slot"
            )
        if (
            isinstance(materialization_iteration, bool)
            or not isinstance(materialization_iteration, int)
            or materialization_iteration < 1
        ):
            raise ValueError(
                f"required_link_bindings[{index}].materialization_iteration "
                "must be a positive integer"
            )
        if predicate_iri in seen:
            raise ValueError(
                "required_link_bindings contains duplicate predicate_iri: "
                + predicate_iri
            )
        seen.add(predicate_iri)


def validate_external_identity_bindings(bindings: list[Any] | None) -> list[dict[str, Any]]:
    """Validate human bindings from an external identifier to A-Box classes."""
    if bindings is None:
        return []
    if not isinstance(bindings, list):
        raise ValueError("runtime.external_identity_bindings must be an array")
    normalized: list[dict[str, Any]] = []
    seen_locals: set[str] = set()
    for index, item in enumerate(bindings):
        if not isinstance(item, dict):
            raise ValueError(f"external_identity_bindings[{index}] must be an object")
        identity_slot = str(item.get("identity_slot") or "").strip()
        target_locals = [
            str(value).strip()
            for value in (item.get("target_class_locals") or [])
            if str(value).strip()
        ]
        materialization_iteration = item.get("materialization_iteration", 1)
        if not identity_slot:
            raise ValueError(
                f"external_identity_bindings[{index}] requires identity_slot"
            )
        if not (
            identity_slot.startswith("{")
            and identity_slot.endswith("}")
            and len(identity_slot) > 2
        ):
            raise ValueError(
                f"external_identity_bindings[{index}].identity_slot "
                "must be a single-braced runtime slot"
            )
        if not target_locals:
            raise ValueError(
                f"external_identity_bindings[{index}] requires target_class_locals"
            )
        if (
            isinstance(materialization_iteration, bool)
            or not isinstance(materialization_iteration, int)
            or materialization_iteration < 1
        ):
            raise ValueError(
                f"external_identity_bindings[{index}].materialization_iteration "
                "must be a positive integer"
            )
        for local in target_locals:
            if local in seen_locals:
                raise ValueError(
                    "external_identity_bindings contains duplicate "
                    f"target_class_local: {local}"
                )
            seen_locals.add(local)
        normalized.append(
            {
                "identity_slot": identity_slot,
                "target_class_locals": target_locals,
                "materialization_iteration": materialization_iteration,
            }
        )
    return normalized


def derive_ordered_member_contracts(tbox_path: str | Path) -> list[dict[str, str]]:
    """Compile ordered-member contracts from OWL comments and property shape."""
    path = Path(tbox_path)
    parsed = parse_ontology_ttl(str(path))
    profile = extract_ontology_integrity_profile(str(path))
    properties = parsed.get("properties") or {}
    classes = parsed.get("classes") or {}
    ordered_member_locals = {
        str(item).strip()
        for item in (profile.get("ordered_member_classes") or [])
        if str(item).strip()
    }
    contracts: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for collection_local in profile.get("individually_linked_object_properties") or []:
        collection = properties.get(str(collection_local).strip()) or {}
        collection_iri = str(collection.get("iri") or "").strip()
        member_local = str(collection.get("range") or "").strip()
        member_iri = str((classes.get(member_local) or {}).get("iri") or "").strip()
        if not collection_iri or not member_iri:
            continue
        order_iri = ""
        for order_local in profile.get("single_valued_ordering_properties") or []:
            order = properties.get(str(order_local).strip()) or {}
            domains = {
                str(domain).strip()
                for domain in (order.get("domains") or [])
                if str(domain).strip()
            }
            if member_local in domains or domains & ordered_member_locals:
                order_iri = str(order.get("iri") or "").strip()
                if order_iri:
                    break
        if not order_iri:
            continue
        key = (collection_iri, member_iri, order_iri)
        if key in seen:
            continue
        seen.add(key)
        contracts.append(
            {
                "collection_property_iri": collection_iri,
                "member_class_iri": member_iri,
                "order_property_iri": order_iri,
            }
        )
    return contracts


def derive_required_link_bindings(
    *,
    tbox_path: str | Path,
    top_entity: Mapping[str, Any] | None,
    external_identity_bindings: list[Any] | None = None,
) -> list[dict[str, Any]]:
    """Bind T-Box-required top-entity links to human-declared identity slots.

    Domain config names the external identifier and the A-Box classes that
    represent the source document. The T-Box still supplies the required
    predicate from the top entity to those classes.
    """
    declared = validate_external_identity_bindings(external_identity_bindings)
    if not declared or not isinstance(top_entity, Mapping):
        return []
    top_iri = str(top_entity.get("class_iri") or "").strip()
    if not top_iri:
        return []
    target_lookup = {
        local: (item["identity_slot"], item["materialization_iteration"])
        for item in declared
        for local in item["target_class_locals"]
    }
    publish = build_ontology_publish_contract_from_tbox(
        tbox_path,
        ontology_name=str(top_entity.get("class_local") or ""),
    )
    superclasses = {top_iri}
    for item in publish.get("subclass_closure") or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("class_iri") or "").strip() != top_iri:
            continue
        superclasses.update(
            str(value).strip()
            for value in (item.get("superclass_iris") or [])
            if str(value).strip()
        )
        break
    bindings: list[dict[str, Any]] = []
    seen: set[str] = set()
    for link in publish.get("required_links") or []:
        if not isinstance(link, dict):
            continue
        subject_iri = str(link.get("subject_class_iri") or "").strip()
        predicate_iri = str(link.get("predicate_iri") or "").strip()
        target_iri = str(link.get("target_class_iri") or "").strip()
        target_local = _local_name(target_iri)
        if (
            not predicate_iri
            or subject_iri not in superclasses
            or target_local not in target_lookup
        ):
            continue
        if predicate_iri in seen:
            continue
        seen.add(predicate_iri)
        identity_slot, materialization_iteration = target_lookup[target_local]
        bindings.append(
            {
                "predicate_iri": predicate_iri,
                "identity_slot": identity_slot,
                "materialization_iteration": materialization_iteration,
            }
        )
    validate_required_link_bindings(bindings)
    return bindings
