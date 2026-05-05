from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

from rdflib import Graph, Literal, URIRef
from rdflib.namespace import RDF, RDFS, XSD

from src.agents.scripts_and_prompts_generation.ttl_parser import (
    extract_ontology_integrity_profile,
    parse_ontology_ttl,
)


_PLACEHOLDER_MARKERS = (
    "placeholder",
    "not specified",
    "missing",
    "unknown",
    "unspecified",
    "dummy",
    "todo",
    "tbd",
    "n/a",
)

_GENERIC_LABEL_PATTERNS = (
    re.compile(r"^(step|member|item|node|entity|object|thing)[\s_-]*\d*$", re.IGNORECASE),
)


def _safe_text(value: Any) -> str:
    return str(value or "").strip()


def _looks_placeholder_label(text: str) -> bool:
    lowered = _safe_text(text).lower()
    if not lowered:
        return True
    if any(marker in lowered for marker in _PLACEHOLDER_MARKERS):
        return True
    return any(pattern.match(lowered) for pattern in _GENERIC_LABEL_PATTERNS)


def _first_label(g: Graph, node: URIRef) -> str:
    for value in g.objects(node, RDFS.label):
        text = _safe_text(value)
        if text:
            return text
    return ""


def _local_name(iri: Any) -> str:
    text = _safe_text(iri)
    if not text:
        return ""
    for sep in ("#", "/"):
        if sep in text:
            text = text.rsplit(sep, 1)[-1]
    return text


def _iter_extension_configs(meta_cfg: dict) -> list[dict]:
    return ((meta_cfg or {}).get("ontologies", {}).get("extensions") or []) if isinstance(meta_cfg, dict) else []


def _resolve_ontology_ttl_path(meta_cfg: dict, ontology_name: str, project_root: str = ".") -> str:
    ontology_name = _safe_text(ontology_name)
    main_cfg = ((meta_cfg or {}).get("ontologies", {}).get("main") or {}) if isinstance(meta_cfg, dict) else {}
    if _safe_text(main_cfg.get("name")) == ontology_name:
        raw_path = _safe_text(main_cfg.get("ttl_file"))
    else:
        raw_path = ""
        for extension in _iter_extension_configs(meta_cfg):
            if _safe_text(extension.get("name")) == ontology_name:
                raw_path = _safe_text(extension.get("ttl_file"))
                break

    if not raw_path:
        return ""

    path = Path(raw_path)
    if not path.is_absolute():
        path = Path(project_root) / path
    return str(path.resolve())


@lru_cache(maxsize=16)
def _load_runtime_ordered_member_profile_from_ttl(ttl_path: str) -> dict[str, Any]:
    parsed = parse_ontology_ttl(ttl_path)
    integrity = extract_ontology_integrity_profile(ttl_path)

    classes = parsed.get("classes", {}) or {}
    properties = parsed.get("properties", {}) or {}
    class_iris = {name: _safe_text(data.get("iri")) for name, data in classes.items()}
    property_iris = {name: _safe_text(data.get("iri")) for name, data in properties.items()}
    parent_map = {
        name: [parent for parent in (data.get("parent_classes") or []) if parent in classes]
        for name, data in classes.items()
    }

    def _descendants(class_name: str) -> list[str]:
        queue = [class_name]
        seen: list[str] = []
        while queue:
            current = queue.pop(0)
            if current in seen:
                continue
            seen.append(current)
            for candidate, parents in parent_map.items():
                if current in parents and candidate not in seen:
                    queue.append(candidate)
        return seen

    ordered_member_classes = integrity.get("ordered_member_classes", []) or []
    order_property_names = integrity.get("single_valued_ordering_properties", []) or []
    collection_property_names = integrity.get("individually_linked_object_properties", []) or []
    property_constraints = integrity.get("property_constraints", {}) or {}

    ordered_contracts: list[dict[str, Any]] = []
    for prop_name in collection_property_names:
        prop_entry = property_constraints.get(prop_name, {}) or {}
        range_name = _safe_text(prop_entry.get("range"))
        if not range_name:
            continue
        compatible_member_class = None
        compatible_descendants: list[str] = []
        for ordered_class in ordered_member_classes:
            descendants = _descendants(ordered_class)
            if range_name == ordered_class or range_name in descendants:
                compatible_member_class = ordered_class
                compatible_descendants = descendants
                break
        if compatible_member_class is None:
            continue
        order_prop_iris = [property_iris.get(name, "") for name in order_property_names if property_iris.get(name)]
        if not order_prop_iris:
            continue
        ordered_contracts.append(
            {
                "collection_property_name": prop_name,
                "collection_property_iri": property_iris.get(prop_name, ""),
                "member_class_name": compatible_member_class,
                "member_class_iri": class_iris.get(compatible_member_class, ""),
                "member_class_descendant_iris": [
                    class_iris.get(name, "")
                    for name in compatible_descendants
                    if class_iris.get(name)
                ],
                "ordering_property_names": [name for name in order_property_names if property_iris.get(name)],
                "ordering_property_iris": order_prop_iris,
            }
        )

    return {
        "source_ttl_path": ttl_path,
        "ordered_member_classes": ordered_member_classes,
        "single_valued_ordering_properties": order_property_names,
        "individually_linked_object_properties": collection_property_names,
        "ordered_collection_contracts": ordered_contracts,
    }


def load_runtime_ordered_member_profile(
    *,
    meta_cfg: dict,
    ontology_name: str,
    project_root: str = ".",
) -> dict[str, Any]:
    ttl_path = _resolve_ontology_ttl_path(meta_cfg, ontology_name, project_root=project_root)
    if not ttl_path or not os.path.exists(ttl_path):
        return {
            "source_ttl_path": ttl_path,
            "ordered_collection_contracts": [],
        }
    return _load_runtime_ordered_member_profile_from_ttl(ttl_path)


def load_all_runtime_ordered_member_profiles(
    *,
    meta_cfg: dict,
    project_root: str = ".",
) -> dict[str, Any]:
    ontology_names: list[str] = []
    main_cfg = ((meta_cfg or {}).get("ontologies", {}).get("main") or {}) if isinstance(meta_cfg, dict) else {}
    main_name = _safe_text(main_cfg.get("name"))
    if main_name:
        ontology_names.append(main_name)
    for extension in _iter_extension_configs(meta_cfg):
        extension_name = _safe_text(extension.get("name"))
        if extension_name and extension_name not in ontology_names:
            ontology_names.append(extension_name)

    merged: dict[str, Any] = {
        "source_ttl_path": [],
        "ordered_member_classes": [],
        "single_valued_ordering_properties": [],
        "individually_linked_object_properties": [],
        "ordered_collection_contracts": [],
    }
    seen_contract_keys: set[tuple[str, str, tuple[str, ...]]] = set()

    for ontology_name in ontology_names:
        profile = load_runtime_ordered_member_profile(
            meta_cfg=meta_cfg,
            ontology_name=ontology_name,
            project_root=project_root,
        )
        source_ttl = _safe_text(profile.get("source_ttl_path"))
        if source_ttl:
            merged["source_ttl_path"].append(source_ttl)
        for key in (
            "ordered_member_classes",
            "single_valued_ordering_properties",
            "individually_linked_object_properties",
        ):
            for value in profile.get(key, []) or []:
                if value not in merged[key]:
                    merged[key].append(value)
        for contract in profile.get("ordered_collection_contracts", []) or []:
            contract_key = (
                _safe_text(contract.get("collection_property_iri")),
                _safe_text(contract.get("member_class_iri")),
                tuple(sorted(_safe_text(iri) for iri in (contract.get("ordering_property_iris") or []) if _safe_text(iri))),
            )
            if contract_key in seen_contract_keys:
                continue
            seen_contract_keys.add(contract_key)
            merged["ordered_collection_contracts"].append(contract)

    return merged


def _parse_scalar_order(value: Any) -> Optional[int]:
    raw = _safe_text(value)
    if not raw:
        return None
    try:
        return int(raw)
    except Exception:
        pass
    match = re.match(r"^-?\d+", raw)
    if match:
        try:
            return int(match.group(0))
        except Exception:
            return None
    return None


def _node_information_score(g: Graph, node: URIRef, order_predicates: list[URIRef]) -> tuple[int, int, int, int, str]:
    label = _first_label(g, node) or _local_name(node)
    non_placeholder = 0 if _looks_placeholder_label(label) else 1
    generic_label = 0 if any(pattern.match(label) for pattern in _GENERIC_LABEL_PATTERNS) else 1
    non_trivial_outgoing = 0
    for _, predicate, obj in g.triples((node, None, None)):
        if predicate in (RDF.type, RDFS.label):
            continue
        if predicate in order_predicates:
            continue
        if isinstance(obj, Literal) and not _safe_text(obj):
            continue
        non_trivial_outgoing += 1
    incoming = sum(1 for _ in g.triples((None, None, node)))
    return (non_placeholder, generic_label, non_trivial_outgoing, incoming, str(node))


def _compatible_member(g: Graph, node: URIRef, contract: dict[str, Any]) -> bool:
    compatible_types = {
        URIRef(iri)
        for iri in (contract.get("member_class_descendant_iris") or [])
        if _safe_text(iri)
    }
    if not compatible_types:
        member_class_iri = _safe_text(contract.get("member_class_iri"))
        if not member_class_iri:
            return True
        compatible_types.add(URIRef(member_class_iri))
    return any((node, RDF.type, class_ref) in g for class_ref in compatible_types)


def _normalize_member_orders(
    g: Graph,
    *,
    parent: URIRef,
    collection_predicate: URIRef,
    order_predicates: list[URIRef],
    members: list[URIRef],
    messages: list[str],
) -> bool:
    changed = False
    member_infos: list[dict[str, Any]] = []
    primary_order_predicate = order_predicates[0]

    for member in members:
        order_literals: list[tuple[URIRef, Any, Optional[int]]] = []
        for predicate in order_predicates:
            for obj in g.objects(member, predicate):
                order_literals.append((predicate, obj, _parse_scalar_order(obj)))

        parsed_values = [value for _, _, value in order_literals if value is not None]
        unique_values = sorted(set(parsed_values))
        chosen_order = unique_values[0] if unique_values else None

        if len(unique_values) > 1:
            changed = True
            messages.append(
                f"Removed conflicting order values from {member}; kept scalar order {chosen_order}"
            )
        if order_literals:
            for predicate, obj, parsed in order_literals:
                if parsed != chosen_order:
                    g.remove((member, predicate, obj))
                    changed = True

        member_infos.append(
            {
                "node": member,
                "order": chosen_order,
                "score": _node_information_score(g, member, order_predicates),
            }
        )

    grouped: dict[Optional[int], list[dict[str, Any]]] = {}
    for info in member_infos:
        grouped.setdefault(info["order"], []).append(info)

    survivors: list[dict[str, Any]] = []
    for order_value, infos in grouped.items():
        if order_value is not None and len(infos) > 1:
            infos = sorted(infos, key=lambda item: item["score"], reverse=True)
            keep = infos[0]
            survivors.append(keep)
            for dropped in infos[1:]:
                g.remove((parent, collection_predicate, dropped["node"]))
                changed = True
                messages.append(
                    f"Dropped duplicate ordered member {dropped['node']} from {parent} at order {order_value}"
                )
        else:
            survivors.extend(infos)

    survivors = sorted(
        survivors,
        key=lambda item: (
            item["order"] is None,
            item["order"] if item["order"] is not None else 10**9,
            tuple(-part if isinstance(part, int) else part for part in item["score"][:-1]),
            item["score"][-1],
        ),
    )

    for new_order, info in enumerate(survivors, start=1):
        node = info["node"]
        current_values = {
            _parse_scalar_order(obj)
            for predicate in order_predicates
            for obj in g.objects(node, predicate)
        }
        if current_values != {new_order}:
            for predicate in order_predicates:
                for obj in list(g.objects(node, predicate)):
                    g.remove((node, predicate, obj))
            g.add((node, primary_order_predicate, Literal(new_order, datatype=XSD.integer)))
            changed = True
            messages.append(f"Normalized order for {node} under {parent} to {new_order}")

    return changed


def _validate_ordered_member_contracts(
    g: Graph,
    *,
    runtime_profile: dict[str, Any],
    top_entity: Optional[URIRef] = None,
) -> list[str]:
    failures: list[str] = []
    for contract in runtime_profile.get("ordered_collection_contracts", []) or []:
        collection_iri = _safe_text(contract.get("collection_property_iri"))
        if not collection_iri:
            continue
        collection_predicate = URIRef(collection_iri)
        order_predicates = [
            URIRef(iri) for iri in (contract.get("ordering_property_iris") or []) if _safe_text(iri)
        ]
        if not order_predicates:
            continue

        if top_entity is not None:
            parents = [top_entity]
        else:
            parents = sorted(
                {
                    subject
                    for subject, _, obj in g.triples((None, collection_predicate, None))
                    if isinstance(subject, URIRef) and isinstance(obj, URIRef)
                },
                key=str,
            )

        for parent in parents:
            members = [
                obj for obj in g.objects(parent, collection_predicate)
                if isinstance(obj, URIRef) and _compatible_member(g, obj, contract)
            ]
            if not members:
                continue
            order_values: list[int] = []
            for member in members:
                member_values = {
                    _parse_scalar_order(obj)
                    for predicate in order_predicates
                    for obj in g.objects(member, predicate)
                }
                member_values.discard(None)
                if len(member_values) != 1:
                    failures.append(
                        f"Ordered member {member} under {parent} must carry exactly one scalar order value"
                    )
                    continue
                order_values.extend(member_values)
            if not order_values:
                continue
            expected = list(range(1, len(order_values) + 1))
            if sorted(order_values) != expected:
                failures.append(
                    f"Ordered members under {parent} must be contiguous {expected}, found {sorted(order_values)}"
                )
    return failures


def _merge_node_into_target(g: Graph, source: URIRef, target: URIRef) -> bool:
    if source == target:
        return False
    changed = False
    outgoing = list(g.triples((source, None, None)))
    incoming = list(g.triples((None, None, source)))
    for _, predicate, obj in outgoing:
        g.add((target, predicate, obj))
        g.remove((source, predicate, obj))
        changed = True
    for subject, predicate, _ in incoming:
        g.add((target if subject == source else subject, predicate, target))
        g.remove((subject, predicate, source))
        changed = True
    return changed


def _single_member_order(g: Graph, member: URIRef, order_predicates: list[URIRef]) -> Optional[int]:
    values = {
        _parse_scalar_order(obj)
        for predicate in order_predicates
        for obj in g.objects(member, predicate)
    }
    values.discard(None)
    if len(values) != 1:
        return None
    return next(iter(values))


def _remove_unlinked_low_information_members(
    g: Graph,
    *,
    contract: dict[str, Any],
    collection_predicate: URIRef,
    order_predicates: list[URIRef],
    messages: list[str],
) -> bool:
    """Remove generated ordered-member placeholders that are not linked to a parent."""
    changed = False
    member_class = URIRef(_safe_text(contract.get("member_class_iri")))
    candidate_types = {
        URIRef(iri)
        for iri in (contract.get("member_class_descendant_iris") or [])
        if _safe_text(iri)
    }
    if _safe_text(member_class):
        candidate_types.add(member_class)
    if not candidate_types:
        return False

    linked_members = {
        obj
        for _, _, obj in g.triples((None, collection_predicate, None))
        if isinstance(obj, URIRef)
    }
    candidates = {
        node
        for type_iri in candidate_types
        for node in g.subjects(RDF.type, type_iri)
        if isinstance(node, URIRef)
    }
    for node in sorted(candidates - linked_members, key=str):
        score = _node_information_score(g, node, order_predicates)
        non_trivial_outgoing = int(score[2])
        non_collection_incoming = [
            triple for triple in g.triples((None, None, node)) if triple[1] != collection_predicate
        ]
        if non_trivial_outgoing or non_collection_incoming:
            continue
        for triple in list(g.triples((node, None, None))):
            g.remove(triple)
        messages.append(f"Removed unlinked low-information ordered member {node}")
        changed = True
    return changed


def _ordered_member_candidates(g: Graph, contract: dict[str, Any]) -> set[URIRef]:
    candidate_types = {
        URIRef(iri)
        for iri in (contract.get("member_class_descendant_iris") or [])
        if _safe_text(iri)
    }
    member_class_iri = _safe_text(contract.get("member_class_iri"))
    if member_class_iri:
        candidate_types.add(URIRef(member_class_iri))
    return {
        node
        for type_iri in candidate_types
        for node in g.subjects(RDF.type, type_iri)
        if isinstance(node, URIRef)
    }


def _adopt_richer_unlinked_members_by_order(
    g: Graph,
    *,
    parent: URIRef,
    contract: dict[str, Any],
    collection_predicate: URIRef,
    order_predicates: list[URIRef],
    members: list[URIRef],
    messages: list[str],
) -> tuple[list[URIRef], bool]:
    """Prefer richer same-order ordered members that were generated but not linked."""
    linked_members = {
        obj
        for _, _, obj in g.triples((None, collection_predicate, None))
        if isinstance(obj, URIRef)
    }
    unlinked_by_order: dict[int, list[URIRef]] = {}
    for candidate in _ordered_member_candidates(g, contract) - linked_members:
        order = _single_member_order(g, candidate, order_predicates)
        if order is not None:
            unlinked_by_order.setdefault(order, []).append(candidate)

    changed = False
    current_members = list(members)
    for idx, member in enumerate(list(current_members)):
        order = _single_member_order(g, member, order_predicates)
        if order is None or order not in unlinked_by_order:
            continue
        challenger = sorted(
            unlinked_by_order[order],
            key=lambda node: _node_information_score(g, node, order_predicates),
            reverse=True,
        )[0]
        if _node_information_score(g, challenger, order_predicates) <= _node_information_score(g, member, order_predicates):
            continue
        g.add((parent, collection_predicate, challenger))
        g.remove((parent, collection_predicate, member))
        current_members[idx] = challenger
        changed = True
        messages.append(
            f"Replaced linked low-information ordered member {member} with richer same-order member {challenger}"
        )
        if not list(g.triples((None, None, member))):
            continue
        if _node_information_score(g, member, order_predicates)[2] == 0:
            for triple in list(g.triples((member, None, None))):
                g.remove(triple)
    return current_members, changed


def enforce_ordered_member_integrity(
    g: Graph,
    runtime_profile: dict[str, Any],
    *,
    top_entity: Optional[URIRef] = None,
) -> tuple[Graph, dict[str, Any]]:
    report: dict[str, Any] = {
        "status": "skipped",
        "messages": [],
        "repairs_applied": 0,
        "parents_checked": 0,
    }
    contracts = runtime_profile.get("ordered_collection_contracts", []) or []
    if not contracts:
        return g, report

    report["status"] = "no_action"
    changed = False
    for contract in contracts:
        collection_iri = _safe_text(contract.get("collection_property_iri"))
        if not collection_iri:
            continue
        collection_predicate = URIRef(collection_iri)
        order_predicates = [
            URIRef(iri) for iri in (contract.get("ordering_property_iris") or []) if _safe_text(iri)
        ]
        if not order_predicates:
            continue

        if _remove_unlinked_low_information_members(
            g,
            contract=contract,
            collection_predicate=collection_predicate,
            order_predicates=order_predicates,
            messages=report["messages"],
        ):
            changed = True
            report["repairs_applied"] += 1

        if top_entity is not None:
            parents = [top_entity]
        else:
            parents = sorted(
                {
                    subject
                    for subject, _, obj in g.triples((None, collection_predicate, None))
                    if isinstance(subject, URIRef) and isinstance(obj, URIRef)
                },
                key=str,
            )

        for parent in parents:
            members = [
                obj for obj in g.objects(parent, collection_predicate)
                if isinstance(obj, URIRef) and _compatible_member(g, obj, contract)
            ]
            if not members:
                continue
            report["parents_checked"] += 1
            members, adopted = _adopt_richer_unlinked_members_by_order(
                g,
                parent=parent,
                contract=contract,
                collection_predicate=collection_predicate,
                order_predicates=order_predicates,
                members=members,
                messages=report["messages"],
            )
            if adopted:
                changed = True
                report["repairs_applied"] += 1
            if _normalize_member_orders(
                g,
                parent=parent,
                collection_predicate=collection_predicate,
                order_predicates=order_predicates,
                members=members,
                messages=report["messages"],
            ):
                changed = True
                report["repairs_applied"] += 1

    failures = _validate_ordered_member_contracts(g, runtime_profile=runtime_profile, top_entity=top_entity)
    if failures:
        report["status"] = "failed"
        report["messages"].extend(failures)
    elif changed:
        report["status"] = "repaired"

    return g, report


def align_ordered_members_to_reference_content(
    ttl_content: str,
    reference_ttl_content: str,
    runtime_profile: dict[str, Any],
    *,
    top_entity_uri: str = "",
) -> tuple[str, dict[str, Any]]:
    report: dict[str, Any] = {
        "status": "skipped",
        "messages": [],
        "remapped_members": 0,
    }
    if not _safe_text(ttl_content) or not _safe_text(reference_ttl_content):
        return ttl_content, report
    contracts = runtime_profile.get("ordered_collection_contracts", []) or []
    if not contracts:
        return ttl_content, report

    try:
        current_graph = Graph()
        current_graph.parse(data=ttl_content, format="turtle")
        reference_graph = Graph()
        reference_graph.parse(data=reference_ttl_content, format="turtle")
    except Exception as exc:
        return ttl_content, {
            "status": "failed",
            "messages": [f"Failed to parse TTL for ordered-member reference alignment: {exc}"],
            "remapped_members": 0,
        }

    top_entity = URIRef(top_entity_uri) if _safe_text(top_entity_uri) else None
    changed = False
    for contract in contracts:
        collection_iri = _safe_text(contract.get("collection_property_iri"))
        if not collection_iri:
            continue
        collection_predicate = URIRef(collection_iri)
        order_predicates = [
            URIRef(iri) for iri in (contract.get("ordering_property_iris") or []) if _safe_text(iri)
        ]
        if not order_predicates:
            continue
        parents = [top_entity] if top_entity is not None else sorted(
            {
                subject
                for subject, _, obj in current_graph.triples((None, collection_predicate, None))
                if isinstance(subject, URIRef) and isinstance(obj, URIRef)
            },
            key=str,
        )
        for parent in parents:
            if parent is None:
                continue
            reference_by_order: dict[int, URIRef] = {}
            for member in reference_graph.objects(parent, collection_predicate):
                if not isinstance(member, URIRef) or not _compatible_member(reference_graph, member, contract):
                    continue
                order_value = _single_member_order(reference_graph, member, order_predicates)
                if order_value is None or order_value in reference_by_order:
                    continue
                reference_by_order[order_value] = member
            if not reference_by_order:
                continue

            current_members = [
                member for member in current_graph.objects(parent, collection_predicate)
                if isinstance(member, URIRef) and _compatible_member(current_graph, member, contract)
            ]
            for member in current_members:
                order_value = _single_member_order(current_graph, member, order_predicates)
                canonical = reference_by_order.get(order_value) if order_value is not None else None
                if canonical is None or canonical == member:
                    continue
                if _merge_node_into_target(current_graph, member, canonical):
                    changed = True
                    report["remapped_members"] += 1
                    report["messages"].append(
                        f"Aligned ordered member {member} to canonical reference node {canonical} at order {order_value}"
                    )

    if not changed:
        report["status"] = "no_action"
        return ttl_content, report

    report["status"] = "repaired"
    return current_graph.serialize(format="turtle"), report


def enforce_ordered_member_integrity_file(
    *,
    ttl_path: str,
    runtime_profile: dict[str, Any],
    top_entity_uri: str = "",
) -> tuple[bool, dict[str, Any]]:
    if not ttl_path or not os.path.exists(ttl_path):
        return False, {"status": "failed", "messages": [f"TTL not found for ordered-member enforcement: {ttl_path}"]}

    try:
        g = Graph()
        g.parse(ttl_path, format="turtle")
    except Exception as exc:
        return False, {"status": "failed", "messages": [f"Failed to parse TTL for ordered-member enforcement: {exc}"]}

    top_entity = URIRef(top_entity_uri) if _safe_text(top_entity_uri) else None
    g, report = enforce_ordered_member_integrity(g, runtime_profile, top_entity=top_entity)

    try:
        g.serialize(destination=ttl_path, format="turtle")
    except Exception as exc:
        return False, {"status": "failed", "messages": [f"Failed to serialize enforced TTL: {exc}"]}

    return report.get("status") != "failed", report
