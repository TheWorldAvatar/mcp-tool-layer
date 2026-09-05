from __future__ import annotations

import hashlib
import json
from typing import Any

from src.agents.scripts_and_prompts_generation.reuse_policy import (
    prohibited_class_locals,
)


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


def _domains(spec: dict[str, Any]) -> set[str]:
    values = spec.get("domains") or [spec.get("domain")]
    return {_local_name(value) for value in values if _local_name(value)}


def _is_object_property(spec: dict[str, Any]) -> bool:
    return str(spec.get("kind") or "").strip().lower() == "object"


def _prohibits_materialization(spec: dict[str, Any]) -> bool:
    return spec.get("creatable") is False


def _creator_surface_class_locals(
    *,
    classes: dict[str, Any],
    contract: dict[str, Any],
) -> set[str]:
    from types import SimpleNamespace

    from src.agents.scripts_and_prompts_generation.materialization_closure import (
        creator_surface_class_locals,
    )

    return creator_surface_class_locals(
        SimpleNamespace(
            parsed={"classes": classes, "properties": {}},
            contract=contract,
        )
    )


def _profile_slots(profile: dict[str, Any]) -> dict[str, str]:
    slots: dict[str, str] = {}
    for slot in profile.get("slots") or []:
        slot_id = str(slot.get("id") or "").strip()
        slot_kind = str(slot.get("slot_kind") or "").strip()
        if slot_id and slot_kind:
            slots[slot_kind] = slot_id
    return slots


def _canonical_digest(value: dict[str, Any]) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _primary_classes(
    *,
    classes: dict[str, Any],
    top_local: str,
    contract: dict[str, Any],
) -> set[str]:
    top_iri = str((classes.get(top_local) or {}).get("iri") or "")
    primary_namespace = _namespace(top_iri)
    external = {
        str((item or {}).get("class_local") or "").strip()
        for item in contract.get("external_class_creators") or []
        if str((item or {}).get("class_local") or "").strip()
    }
    policy_prohibited = prohibited_class_locals(contract.get("reuse_policy"))
    selected = {
        str(local)
        for local, spec in classes.items()
        if str(local) not in external
        and str(local) not in policy_prohibited
        and not _prohibits_materialization(spec or {})
        and (
            not primary_namespace
            or _namespace((spec or {}).get("iri")) == primary_namespace
        )
    }
    return selected & _creator_surface_class_locals(
        classes=classes,
        contract=contract,
    )


def _abstract_ordered_parents(
    *,
    classes: dict[str, Any],
    ordered_classes: set[str],
) -> set[str]:
    children: dict[str, set[str]] = {}
    for child, spec in classes.items():
        for parent in (spec or {}).get("parent_classes") or []:
            local = _local_name(parent)
            if local:
                children.setdefault(local, set()).add(str(child))
    return {
        parent
        for parent, child_set in children.items()
        if parent in ordered_classes
        and child_set
        and child_set <= ordered_classes
    }


def _simple_assignment(
    *,
    profile: dict[str, Any],
    classes: dict[str, Any],
    properties: dict[str, Any],
    contract: dict[str, Any],
    top_local: str,
    allow_top_assignment: bool,
) -> dict[str, Any]:
    slot = str((profile.get("slots") or [{}])[0].get("id") or "iter2")
    primary = _primary_classes(
        classes=classes,
        top_local=top_local,
        contract=contract,
    )
    owned = set(primary)
    if not allow_top_assignment:
        owned.discard(top_local)
    owned_properties = {
        str(local)
        for local, spec in properties.items()
        if _is_object_property(spec or {})
        and (
            (_domains(spec or {}) & (owned | {top_local}))
            or _local_name((spec or {}).get("range")) in owned
        )
    }
    if not owned and top_local in primary:
        owned.add(top_local)
    result = {
        "assignments": [
            {
                "slot": slot,
                "classes": sorted(owned),
                "object_properties": sorted(owned_properties),
                "rationale": "Deterministic complete scope for the single downstream slot.",
            }
        ],
        "ownership_provenance": {
            "classes": {local: "simple_top_reachability" for local in sorted(owned)},
            "object_properties": {
                local: "simple_domain_reachability"
                for local in sorted(owned_properties)
            },
        },
    }
    result["ownership_sha256"] = _canonical_digest(
        {
            "classes": result["ownership_provenance"]["classes"],
            "object_properties": result["ownership_provenance"][
                "object_properties"
            ],
        }
    )
    return result


def assign_iteration_ownership(
    *,
    profile: dict[str, Any],
    parsed: dict[str, Any],
    contract: dict[str, Any],
    top_local: str,
    allow_top_assignment: bool = False,
) -> dict[str, Any]:
    """Assign primary-T-Box semantics to fixed slots without LLM discretion."""
    classes = parsed.get("classes") or {}
    properties = parsed.get("properties") or {}
    slot_by_kind = _profile_slots(profile)
    if "simple_all" in slot_by_kind or len(profile.get("slots") or []) == 1:
        return _simple_assignment(
            profile=profile,
            classes=classes,
            properties=properties,
            contract=contract,
            top_local=top_local,
            allow_top_assignment=allow_top_assignment,
        )
    required_kinds = {"foundation", "ordered", "remainder"}
    if not required_kinds <= set(slot_by_kind):
        raise ValueError(
            "complex workflow profile requires foundation, ordered, and remainder "
            "slot_kind values"
        )

    primary = _primary_classes(
        classes=classes,
        top_local=top_local,
        contract=contract,
    )
    primary.discard(top_local)
    ordered_marked = {
        str(value).strip()
        for value in (
            (contract.get("ordered_member_profile") or {}).get(
                "ordered_member_classes"
            )
            or []
        )
        if str(value).strip()
    }
    abstract_ordered = _abstract_ordered_parents(
        classes=classes,
        ordered_classes=ordered_marked,
    )
    primary -= abstract_ordered
    ordered = (ordered_marked & primary) - abstract_ordered

    non_reusable = {
        str(item.get("class_local") or "").strip()
        for item in (contract.get("reuse_policy") or {}).get("classes") or []
        if isinstance(item, dict)
        and item.get("reusable") is False
        and str(item.get("class_local") or "").strip()
    }
    top_ranges = {
        _local_name((spec or {}).get("range"))
        for spec in properties.values()
        if top_local in _domains(spec or {})
        and _local_name((spec or {}).get("range"))
    }
    ordered_ranges = {
        _local_name((spec or {}).get("range"))
        for spec in properties.values()
        if _domains(spec or {}) & ordered_marked
        and _local_name((spec or {}).get("range"))
    }
    bridge = top_ranges & ordered_ranges & non_reusable & primary
    foundation = ((top_ranges & primary) | bridge) - ordered
    operation_assets = (
        (ordered_ranges & primary) - top_ranges - ordered - foundation
    )

    owner: dict[str, str] = {
        **{local: "ordered" for local in sorted(ordered)},
        **{local: "ordered" for local in sorted(operation_assets)},
        **{local: "foundation" for local in sorted(foundation)},
    }
    class_rule: dict[str, str] = {
        **{local: "ordered_member" for local in sorted(ordered)},
        **{
            local: "ordered_operation_local_range"
            for local in sorted(operation_assets)
        },
        **{
            local: (
                "non_reusable_ordered_bridge"
                if local in bridge
                else "top_direct_range"
            )
            for local in sorted(foundation)
        },
    }

    changed = True
    while changed:
        changed = False
        for spec in properties.values():
            if not _is_object_property(spec or {}):
                continue
            target = _local_name((spec or {}).get("range"))
            if target not in primary or target in owner:
                continue
            domain_owners = {
                owner[domain]
                for domain in _domains(spec or {})
                if domain in owner
            }
            if not domain_owners:
                continue
            selected = (
                "ordered"
                if "ordered" in domain_owners
                else "foundation"
                if "foundation" in domain_owners
                else "remainder"
            )
            owner[target] = selected
            class_rule[target] = f"{selected}_domain_range_closure"
            changed = True

    required_properties = {
        _local_name((item or {}).get("predicate_iri"))
        for item in contract.get("required_links") or []
        if _local_name((item or {}).get("predicate_iri"))
    }
    property_owner: dict[str, str] = {}
    property_rule: dict[str, str] = {}
    priority = {"ordered": 3, "foundation": 2, "remainder": 1}
    for local, spec in sorted(properties.items()):
        if not _is_object_property(spec or {}):
            continue
        domains = _domains(spec or {})
        target = _local_name((spec or {}).get("range"))
        candidates = {owner[domain] for domain in domains if domain in owner}
        if domains & ordered_marked:
            selected = "ordered"
            rule = "ordered_domain"
        elif target in ordered_marked:
            selected = "ordered"
            rule = "ordered_range"
        elif candidates:
            selected = max(candidates, key=priority.__getitem__)
            rule = f"{selected}_owned_domain"
        elif top_local in domains:
            if target in owner:
                selected = owner[target]
                rule = "top_bridge_range_owner"
            elif local in required_properties:
                selected = "foundation"
                rule = "required_external_top_link"
            else:
                selected = "remainder"
                rule = "top_summary_or_external_range"
        elif target in owner:
            selected = owner[target]
            rule = "owned_range"
        else:
            continue
        property_owner[str(local)] = selected
        property_rule[str(local)] = rule

    missing_required = sorted(required_properties - set(property_owner))
    if missing_required:
        raise ValueError(
            "deterministic ownership cannot place required properties: "
            + ", ".join(missing_required)
        )

    classes_by_slot = {
        slot_by_kind[kind]: sorted(
            local for local, assigned in owner.items() if assigned == kind
        )
        for kind in required_kinds
    }
    properties_by_slot = {
        slot_by_kind[kind]: sorted(
            local
            for local, assigned in property_owner.items()
            if assigned == kind
        )
        for kind in required_kinds
    }
    kind_by_slot = {slot: kind for kind, slot in slot_by_kind.items()}
    assignments = []
    for slot_spec in profile.get("slots") or []:
        slot = str(slot_spec.get("id") or "")
        kind = kind_by_slot[slot]
        assignments.append(
            {
                "slot": slot,
                "classes": classes_by_slot[slot],
                "object_properties": properties_by_slot[slot],
                "rationale": (
                    f"Deterministic {kind} ownership derived from the active "
                    "primary T-Box and compiled workflow contracts."
                ),
            }
        )
    ownership = {
        "schema_version": "iteration-ownership.v1",
        "top_entity": top_local,
        "classes": {
            local: slot_by_kind[kind] for local, kind in sorted(owner.items())
        },
        "object_properties": {
            local: slot_by_kind[kind]
            for local, kind in sorted(property_owner.items())
        },
        "rules": {
            "classes": dict(sorted(class_rule.items())),
            "object_properties": dict(sorted(property_rule.items())),
        },
        "excluded": {
            "abstract_ordered_parents": sorted(abstract_ordered),
            "prohibited_or_external_or_unreachable": sorted(
                set(classes) - set(owner) - {top_local}
            ),
        },
    }
    return {
        "assignments": assignments,
        "ownership_provenance": ownership,
        "ownership_sha256": _canonical_digest(ownership),
    }
