"""Shared occurrence-surface constants and T-Box helpers."""

from __future__ import annotations

import hashlib
from typing import Any, Mapping

from src.extraction_prompt_generation.compile.operation_units import (
    _base_creator_contracts,
    _local_name,
    _python_name,
    compile_materialization_operation_units,
    discover_materialization_operation_candidates,
)
from src.extraction_prompt_generation.compile.reuse_policy import (
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


def extension_focus_iri(contract: Mapping[str, Any]) -> str:
    """Return the planned extension-focus class IRI, if this contract has one."""
    for payload in (
        contract.get("extension_focus"),
        (contract.get("ontology_publish_contract") or {}).get("extension_focus"),
    ):
        if isinstance(payload, Mapping):
            iri = str(payload.get("class_iri") or "").strip()
            if iri.startswith(("http://", "https://", "urn:")):
                return iri
    return ""


def _member_predicates(contract: Mapping[str, Any]) -> set[str]:
    return {
        str(value).strip()
        for value in (
            (contract.get("ordered_member_profile") or {}).get(
                "individually_linked_object_properties"
            )
            or []
        )
        if str(value).strip()
    }


def incoming_parent_index(
    *,
    relationships: Mapping[str, Any],
    by_iri: Mapping[str, Mapping[str, Any]],
    closure: Mapping[str, set[str]],
    member_predicates: set[str],
) -> dict[str, list[tuple[str, Mapping[str, Any]]]]:
    """Index unique creatable ranges by the class that is the relationship target."""
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
        if not target_iri:
            continue
        incoming.setdefault(target_iri, []).append((str(predicate_local), spec))
    return incoming


def select_unique_incoming_parent(
    options: list[tuple[str, Mapping[str, Any]]],
    *,
    top_iri: str,
    closure: Mapping[str, set[str]],
) -> tuple[str, Mapping[str, Any], bool] | None:
    """Return the unique incoming parent predicate, preferring a top-entity domain."""
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
        return predicate_local, spec, True
    if len(options) == 1:
        predicate_local, spec = options[0]
        return predicate_local, spec, False
    return None


def domain_includes_focus(
    spec: Mapping[str, Any],
    *,
    focus_iri: str,
    closure: Mapping[str, set[str]],
) -> bool:
    if not focus_iri:
        return False
    domains = {str(value) for value in spec.get("domain_iris") or [] if str(value)}
    return focus_iri in domains or _matches_class(focus_iri, domains, closure)


def extension_public_reusable_owner_iris(
    *,
    parsed: Mapping[str, Any],
    contract: Mapping[str, Any],
) -> set[str]:
    """Reusable classes that must stay public on an extension surface.

    Main ontologies skip reusable owners because another public heading hosts
    them as nested links. An extension surface adopts the planned focus class,
    so that class has no outer public owner. Classes whose unique incoming
    parent is that focus therefore need their own ``create_*`` even when
    reusable: reuse is identity, not 'no public heading'. Nested reusable
    descriptors of a non-reusable focus stay nested.
    """
    focus_iri = extension_focus_iri(contract)
    if not focus_iri:
        return set()
    kept = {focus_iri}
    reusable = _reusable_by_iri(contract)
    if reusable.get(focus_iri) is not True:
        return kept
    creators = _base_creator_contracts(parsed=parsed, contract=contract)
    by_iri = {
        str(item.get("class_iri") or ""): item
        for item in creators
        if str(item.get("class_iri") or "")
    }
    closure = _closure_map(contract)
    _top_local, top_iri = _top_entity(contract)
    incoming = incoming_parent_index(
        relationships=contract.get("relationship_tool_contracts") or {},
        by_iri=by_iri,
        closure=closure,
        member_predicates=_member_predicates(contract),
    )
    for creator in creators:
        owner_iri = str(creator.get("class_iri") or "")
        if (
            not owner_iri
            or owner_iri == focus_iri
            or (top_iri and owner_iri == top_iri)
            or reusable.get(owner_iri) is not True
        ):
            continue
        selected = select_unique_incoming_parent(
            incoming.get(owner_iri) or [],
            top_iri=top_iri,
            closure=closure,
        )
        if selected is None:
            continue
        _predicate, spec, _from_top = selected
        if domain_includes_focus(spec, focus_iri=focus_iri, closure=closure):
            kept.add(owner_iri)
    return kept

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

