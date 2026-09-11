"""Memory init/export, graph export repair, and identity seeding.

Public runtime tools: `init_memory` and `export_memory`. See rdf/README.md.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from rdflib import Literal, URIRef
from rdflib.namespace import RDF, RDFS, XSD

from .constants import _REGISTRY_KEY
from .envelopes import error_json, result_json, success_result
from .paths import (
    _package_ontology_name,
    _package_relationship_contract,
    resolve_case_dirname,
    scoped_memory_paths,
)
from .registry import (
    _rejection_registry,
    _reuse_grant_registry,
    _scope_registry,
    atomic_graph_transaction,
    bound_root_iri,
    current_memory_scope,
    export_graph_result,
    reset_graph,
    retained_graph,
)


def initialize_retained_graph(
    *,
    source_path: str | None = None,
) -> dict[str, Any]:
    """Open retained graph state and optionally merge a persisted A-Box."""
    graph = retained_graph()
    before = len(graph)
    loaded = 0
    if source_path:
        result = load_from_turtle_file(source_path, behavior="merge")
        loaded = int(result.get("loaded_triples") or 0)
    return {
        "status": "ok",
        "mode": "open_or_resume",
        "before_triples": before,
        "loaded_triples": loaded,
        "total_triples": len(graph),
    }


def load_from_turtle_file(path: str, behavior: str = "merge") -> dict[str, Any]:
    """Load a Turtle artifact into the retained graph for cross-process resume."""
    source = Path(path).expanduser().resolve(strict=True)
    if not source.is_file():
        raise ValueError(f"Turtle source is not a file: {source}")
    normalized_behavior = str(behavior).strip().casefold()
    if normalized_behavior not in {"merge", "replace"}:
        raise ValueError("behavior must be `merge` or `replace`")
    graph = retained_graph()
    if normalized_behavior == "replace":
        reset_graph(graph)
    before = len(graph)
    graph.parse(source, format="turtle")
    return {
        "status": "ok",
        "path": str(source),
        "behavior": normalized_behavior,
        "loaded_triples": len(graph) - before,
        "total_triples": len(graph),
    }


def _ensure_locked_identity_from_sidecar(memory_path: Path) -> dict[str, Any]:
    """Restore the locked identity and its explicit iteration-1 neighborhood.

    Pipeline seeds ``memory/{scope}.ttl`` and writes ``memory/{scope}.identity.json``.
    The dossier is a domain-neutral record of the top entity's explicit outgoing facts.
    Restoring those facts makes exact prior refs visible in scoped memory without
    requiring a generic central-memory lookup or hard-coding any neighbor class.
    """
    sidecar = memory_path.with_name(f"{memory_path.stem}.identity.json")
    if not sidecar.is_file():
        return {"applied": False, "reason": "sidecar_missing", "sidecar": str(sidecar)}
    try:
        payload = json.loads(sidecar.read_text(encoding="utf-8"))
    except Exception as exc:
        return {
            "applied": False,
            "reason": f"sidecar_unreadable:{exc}",
            "sidecar": str(sidecar),
        }
    identity = payload.get("identity") if isinstance(payload, dict) else None
    if not isinstance(identity, dict):
        return {"applied": False, "reason": "identity_missing", "sidecar": str(sidecar)}
    uri = str(identity.get("uri") or "").strip()
    label = str(identity.get("label") or "").strip()
    type_iris = [
        str(value).strip()
        for value in (identity.get("types") or [])
        if str(value).strip()
    ]
    top_class = str(identity.get("top_class_iri") or "").strip()
    if top_class and top_class not in type_iris:
        type_iris.append(top_class)
    if not uri or not type_iris:
        return {"applied": False, "reason": "uri_or_types_missing", "sidecar": str(sidecar)}

    graph = retained_graph()
    subject = URIRef(uri)
    before_types = {str(value) for value in graph.objects(subject, RDF.type)}
    added_types: list[str] = []
    for type_iri in type_iris:
        if type_iri not in before_types:
            graph.add((subject, RDF.type, URIRef(type_iri)))
            added_types.append(type_iri)
    added_label = False
    if label:
        existing_labels = {str(value) for value in graph.objects(subject, RDFS.label)}
        if label not in existing_labels:
            graph.add((subject, RDFS.label, Literal(label)))
            added_label = True
    dossier = identity.get("dossier") or identity.get("identity_dossier")
    if not isinstance(dossier, dict) and isinstance(payload, dict):
        dossier = payload.get("identity_dossier")
    restored_facts = 0
    restored_neighbor_types = 0
    restored_neighbor_labels = 0
    for fact in (
        dossier.get("explicit_iteration_1_facts") or []
        if isinstance(dossier, dict)
        else []
    ):
        if not isinstance(fact, dict):
            continue
        predicate_iri = str(fact.get("predicate_iri") or "").strip()
        if not predicate_iri:
            continue
        predicate = URIRef(predicate_iri)
        value_kind = str(fact.get("value_kind") or "").strip()
        if value_kind == "iri":
            object_iri = str(fact.get("object_iri") or "").strip()
            if not object_iri:
                continue
            object_node = URIRef(object_iri)
            triple = (subject, predicate, object_node)
            if triple not in graph:
                graph.add(triple)
                restored_facts += 1
            for object_type in fact.get("object_types") or []:
                type_iri = str(object_type or "").strip()
                if type_iri and (object_node, RDF.type, URIRef(type_iri)) not in graph:
                    graph.add((object_node, RDF.type, URIRef(type_iri)))
                    restored_neighbor_types += 1
            for object_label in fact.get("object_labels") or []:
                neighbor_label = str(object_label or "").strip()
                if (
                    neighbor_label
                    and (object_node, RDFS.label, Literal(neighbor_label)) not in graph
                ):
                    graph.add((object_node, RDFS.label, Literal(neighbor_label)))
                    restored_neighbor_labels += 1
        elif value_kind == "literal":
            value = str(fact.get("value") or "")
            datatype_iri = str(fact.get("datatype_iri") or "").strip()
            language = str(fact.get("language") or "").strip()
            literal = (
                Literal(value, lang=language)
                if language
                else Literal(value, datatype=URIRef(datatype_iri))
                if datatype_iri
                else Literal(value)
            )
            triple = (subject, predicate, literal)
            if triple not in graph:
                graph.add(triple)
                restored_facts += 1
    return {
        "applied": bool(
            added_types
            or added_label
            or before_types
            or restored_facts
            or restored_neighbor_types
            or restored_neighbor_labels
        ),
        "sidecar": str(sidecar),
        "uri": uri,
        "added_types": added_types,
        "added_label": added_label,
        "had_types": sorted(before_types),
        "restored_explicit_facts": restored_facts,
        "restored_neighbor_types": restored_neighbor_types,
        "restored_neighbor_labels": restored_neighbor_labels,
    }


def _package_top_entity_class_iri() -> str:
    """Return the package-declared bound-root class, if the TBox contract has one."""
    top_entity = _package_relationship_contract().get("top_entity")
    if not isinstance(top_entity, dict):
        return ""
    class_iri = str(top_entity.get("class_iri") or "").strip()
    if class_iri.startswith(("http://", "https://", "urn:")):
        return class_iri
    return ""


def _materialize_bound_root(label: str) -> dict[str, Any]:
    """Stamp the session root into the retained graph so export can keep it.

    Extension packages bind an upstream root that they do not create. Export
    previously rejected that empty root even when committed focus nodes existed.
    """
    root_text = bound_root_iri()
    if not root_text:
        return {"applied": False, "reason": "no_bound_root"}
    graph = retained_graph()
    root = URIRef(root_text)
    if any(graph.triples((root, None, None))):
        return {"applied": False, "reason": "already_present", "root_iri": root_text}
    added: list[str] = []
    class_iri = _package_top_entity_class_iri()
    if class_iri:
        graph.add((root, RDF.type, URIRef(class_iri)))
        added.append("type")
    stamp = str(label or "").strip() or root_text
    graph.add((root, RDFS.label, Literal(stamp)))
    added.append("label")
    return {
        "applied": True,
        "root_iri": root_text,
        "added": added,
        "class_iri": class_iri,
    }


def _enrichment_global_state_paths() -> list[Path]:
    ontology_name = _package_ontology_name()
    if not ontology_name:
        return []
    data_dir = Path(
        os.environ.get("TWA_AGENTIC_DATA_DIR")
        or os.environ.get("TWA_EXTENSION_DATA_DIR")
        or "data"
    )
    filename = f"{ontology_name}_global_state.json"
    paths: list[Path] = []
    doi = str(current_memory_scope().get("doi") or "").strip()
    if doi:
        paths.append(data_dir / resolve_case_dirname(doi) / filename)
    paths.append(data_dir / filename)
    return paths


def _enrichment_targets_from_global_state() -> list[dict[str, str]]:
    """Read pipeline-bound extension identities from the package global state."""
    targets: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for state_path in _enrichment_global_state_paths():
        if not state_path.is_file():
            continue
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(state, dict):
            continue
        for item in state.get("enrichment_targets") or []:
            if not isinstance(item, dict):
                continue
            target_iri = str(item.get("target_iri") or "").strip()
            class_iri = str(item.get("class_iri") or "").strip()
            if not target_iri.startswith(("http://", "https://", "urn:")):
                continue
            if not class_iri.startswith(("http://", "https://", "urn:")):
                continue
            key = (target_iri, class_iri)
            if key in seen:
                continue
            seen.add(key)
            targets.append({"target_iri": target_iri, "class_iri": class_iri})
        if targets:
            break
    return targets


def _seed_enrichment_targets_into_retained_graph() -> dict[str, Any]:
    """Type bound enrichment-target IRIs so agents need not call create_* to adopt them."""
    graph = retained_graph()
    seeded: list[str] = []
    already_present: list[str] = []
    for item in _enrichment_targets_from_global_state():
        subject = URIRef(item["target_iri"])
        class_ref = URIRef(item["class_iri"])
        triple = (subject, RDF.type, class_ref)
        if triple in graph:
            already_present.append(item["target_iri"])
            continue
        graph.add(triple)
        seeded.append(item["target_iri"])
    return {
        "applied": bool(seeded or already_present),
        "seeded": seeded,
        "already_present": already_present,
        "target_count": len(seeded) + len(already_present),
    }


def _canonical_runtime_scope(top_level_entity_name: str) -> tuple[str, str]:
    """Keep agent-supplied aliases inside the pipeline-owned entity scope."""
    requested = str(top_level_entity_name or "").strip()
    expected = str(
        os.environ.get("TWA_MCP_ENTITY_CONTEXT_EXPECTED_NAME") or ""
    ).strip()
    return (expected or requested, requested)


def init_memory(
    doi: str,
    top_level_entity_name: str,
    root_iri: str | None = None,
) -> str:
    """Open or resume one canonical retained-graph scope without clearing state."""
    canonical_scope, requested_scope = _canonical_runtime_scope(top_level_entity_name)
    normalized_doi = str(doi or "").strip()
    requested_root = str(root_iri or "").strip()
    expected_root = str(
        os.environ.get("TWA_MCP_ENTITY_CONTEXT_EXPECTED_IRI") or ""
    ).strip()
    if expected_root and requested_root and expected_root != requested_root:
        return error_json(
            code="ROOT_BINDING_MISMATCH",
            message="The host-supplied root does not match the pipeline entity context.",
            expected_root_iri=expected_root,
            requested_root_iri=requested_root,
            graph_changed=False,
        )
    canonical_root = expected_root or requested_root
    previous_scope = current_memory_scope()
    previous_doi = str(previous_scope.get("doi") or "").strip()
    cross_document_reset = bool(previous_doi and previous_doi != normalized_doi)
    if cross_document_reset:
        reset_graph(retained_graph())
    _scope_registry()[_REGISTRY_KEY] = {
        "doi": normalized_doi,
        "top_level_entity_name": canonical_scope,
        "bound_root_iri": canonical_root,
    }
    _reuse_grant_registry()[_REGISTRY_KEY] = {}
    _rejection_registry()[_REGISTRY_KEY] = {}
    memory_path, export_path = scoped_memory_paths(doi, canonical_scope)
    if memory_path.is_file():
        load_state = initialize_retained_graph(source_path=str(memory_path))
    else:
        load_state = initialize_retained_graph()
    identity_seed = _ensure_locked_identity_from_sidecar(memory_path)
    enrichment_seed = _seed_enrichment_targets_into_retained_graph()
    bound_root_seed = _materialize_bound_root(canonical_scope)
    return result_json(
        success_result(
            message="Initialized memory",
            doi=doi,
            top_level_entity_name=canonical_scope,
            requested_top_level_entity_name=requested_scope,
            bound_root_iri=canonical_root,
            scope_canonicalized=canonical_scope != requested_scope,
            cross_document_reset=cross_document_reset,
            previous_doi=previous_doi,
            memory_path=str(memory_path),
            export_path=str(export_path),
            load_state=load_state,
            identity_seed=identity_seed,
            enrichment_target_seed=enrichment_seed,
            bound_root_seed=bound_root_seed,
        )
    )


def prepare_graph_for_export(
    ordered_member_contracts: dict[str, dict[str, str]] | None = None,
    extra_keep_roots: list[str] | None = None,
) -> dict[str, Any]:
    """Apply graph-only export repairs without consulting pipeline hints."""
    graph = retained_graph()
    root_text = bound_root_iri()
    if not root_text:
        return {
            "status": "rejected",
            "ok": False,
            "code": "BOUND_ROOT_MISSING",
            "message": "Cannot prepare export without a session-bound root.",
            "graph_changed": False,
        }
    root = URIRef(root_text)
    if not any(graph.triples((root, None, None))):
        return {
            "status": "rejected",
            "ok": False,
            "code": "BOUND_ROOT_NOT_MATERIALIZED",
            "message": "The session-bound root is absent from the retained graph.",
            "graph_changed": False,
        }

    groups: dict[tuple[str, str], set[str]] = {}
    for contract in (ordered_member_contracts or {}).values():
        collection = str(contract.get("parent_predicate_iri") or "").strip()
        ordering = str(contract.get("ordering_property_iri") or "").strip()
        class_iri = str(contract.get("class_iri") or "").strip()
        if collection and ordering and class_iri:
            groups.setdefault((collection, ordering), set()).add(class_iri)

    def scalar_order(member: URIRef, ordering: URIRef) -> int | None:
        values: set[int] = set()
        for value in graph.objects(member, ordering):
            try:
                values.add(int(value.toPython()))
            except (TypeError, ValueError):
                return None
        return next(iter(values)) if len(values) == 1 else None

    missing_order: list[str] = []
    for (collection_text, ordering_text), class_iris in groups.items():
        collection = URIRef(collection_text)
        ordering = URIRef(ordering_text)
        accepted_types = {URIRef(value) for value in class_iris}
        for member in graph.objects(root, collection):
            if not isinstance(member, URIRef):
                continue
            if not any((member, RDF.type, class_iri) in graph for class_iri in accepted_types):
                continue
            if scalar_order(member, ordering) is None:
                missing_order.append(str(member))
    if missing_order:
        return {
            "status": "rejected",
            "ok": False,
            "code": "ORDERED_MEMBER_ORDER_INVALID",
            "message": "Ordered members must have exactly one integer order before export.",
            "members": sorted(missing_order),
            "retryable": True,
            "graph_changed": False,
        }

    before = set(graph)
    messages: list[str] = []
    with atomic_graph_transaction():
        for (collection_text, ordering_text), class_iris in groups.items():
            collection = URIRef(collection_text)
            ordering = URIRef(ordering_text)
            accepted_types = {URIRef(value) for value in class_iris}
            members = [
                member
                for member in graph.objects(root, collection)
                if isinstance(member, URIRef)
                and any(
                    (member, RDF.type, class_iri) in graph
                    for class_iri in accepted_types
                )
            ]
            by_order: dict[int, list[URIRef]] = {}
            for member in members:
                order = scalar_order(member, ordering)
                if order is not None:
                    by_order.setdefault(order, []).append(member)

            survivors: list[tuple[int, URIRef]] = []
            for order, candidates in by_order.items():
                ranked = sorted(
                    candidates,
                    key=lambda node: (
                        sum(
                            1
                            for _, predicate, _ in graph.triples((node, None, None))
                            if predicate not in {RDF.type, RDFS.label, ordering}
                        ),
                        sum(1 for _ in graph.triples((None, None, node))),
                        str(node),
                    ),
                    reverse=True,
                )
                keep = ranked[0]
                survivors.append((order, keep))
                for duplicate in ranked[1:]:
                    graph.remove((root, collection, duplicate))
                    messages.append(
                        f"Dropped duplicate ordered member {duplicate} at order {order}"
                    )

            for new_order, (_, member) in enumerate(
                sorted(survivors, key=lambda item: (item[0], str(item[1]))),
                start=1,
            ):
                current = list(graph.objects(member, ordering))
                if len(current) == 1 and scalar_order(member, ordering) == new_order:
                    continue
                for value in current:
                    graph.remove((member, ordering, value))
                graph.add(
                    (member, ordering, Literal(new_order, datatype=XSD.integer))
                )
                messages.append(f"Normalized order for {member} to {new_order}")

        reachable: set[URIRef] = {root}
        for extra in extra_keep_roots or []:
            extra_text = str(extra or "").strip()
            if extra_text:
                reachable.add(URIRef(extra_text))
        queue: list[URIRef] = list(reachable)
        while queue:
            subject = queue.pop()
            for obj in graph.objects(subject, None):
                if isinstance(obj, URIRef) and obj not in reachable:
                    reachable.add(obj)
                    queue.append(obj)
        unreachable = {
            node
            for node in graph.subjects(RDF.type, None)
            if isinstance(node, URIRef) and node not in reachable
        }
        for node in sorted(unreachable, key=str):
            for triple in list(graph.triples((node, None, None))):
                graph.remove(triple)
            for triple in list(graph.triples((None, None, node))):
                graph.remove(triple)
        if unreachable:
            messages.append(
                f"Pruned {len(unreachable)} unreachable typed node(s)"
            )

    return {
        "status": "ok",
        "ok": True,
        "graph_changed": set(graph) != before,
        "repairs_applied": len(messages),
        "messages": messages,
        "triple_count": len(graph),
    }


def export_memory(doi: str, top_level_entity_name: str) -> str:
    """Persist the scoped graph; central publication is pipeline-owned after audit."""
    canonical_scope, requested_scope = _canonical_runtime_scope(top_level_entity_name)
    graph = retained_graph()
    result = export_graph_result(
        graph,
        doi=doi,
        scope=canonical_scope,
    )
    result["requested_top_level_entity_name"] = requested_scope
    result["scope_canonicalized"] = canonical_scope != requested_scope
    result["central_memory"] = {
        "status": "deferred_to_pipeline",
        "reason": "central memory is published only after semantic commit",
    }
    return result_json(result)
