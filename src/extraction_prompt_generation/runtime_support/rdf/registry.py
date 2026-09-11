"""Process-wide graph, scope, reuse-grant, and rejection registries.

The retained graph is keyed by package path so concurrent generated
packages do not share state. See rdf/README.md.
"""

from __future__ import annotations

import builtins
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from rdflib import BNode, Graph, URIRef
from rdflib.namespace import OWL, RDF, RDFS

from .constants import (
    _GRAPH_TRANSACTION_LOCK,
    _REGISTRY_KEY,
    _REGISTRY_NAME,
    _REJECTION_REGISTRY_NAME,
    _REUSE_GRANT_REGISTRY_NAME,
    _SCOPE_REGISTRY_NAME,
)
from .envelopes import error_json, result_json
from .paths import scoped_memory_paths


def _graph_registry() -> dict[str, Graph]:
    registry = getattr(builtins, _REGISTRY_NAME, None)
    if not isinstance(registry, dict):
        registry = {}
        setattr(builtins, _REGISTRY_NAME, registry)
    return registry


def new_graph(*, namespace_bindings: dict[str, str] | None = None) -> Graph:
    """Create a graph with optional stable namespace bindings."""
    graph = Graph()
    for prefix, namespace in sorted((namespace_bindings or {}).items()):
        graph.bind(str(prefix), URIRef(str(namespace)))
    return graph


def retained_graph() -> Graph:
    """Return the process-wide graph retained for this generated package."""
    registry = _graph_registry()
    graph = registry.get(_REGISTRY_KEY)
    if not isinstance(graph, Graph):
        graph = new_graph()
        registry[_REGISTRY_KEY] = graph
    return graph


def _scope_registry() -> dict[str, dict[str, str]]:
    registry = getattr(builtins, _SCOPE_REGISTRY_NAME, None)
    if not isinstance(registry, dict):
        registry = {}
        setattr(builtins, _SCOPE_REGISTRY_NAME, registry)
    return registry


def _reuse_grant_registry() -> dict[str, dict[str, dict[str, Any]]]:
    registry = getattr(builtins, _REUSE_GRANT_REGISTRY_NAME, None)
    if not isinstance(registry, dict):
        registry = {}
        setattr(builtins, _REUSE_GRANT_REGISTRY_NAME, registry)
    return registry


def _rejection_registry() -> dict[str, dict[str, dict[str, Any]]]:
    registry = getattr(builtins, _REJECTION_REGISTRY_NAME, None)
    if not isinstance(registry, dict):
        registry = {}
        setattr(builtins, _REJECTION_REGISTRY_NAME, registry)
    return registry


def current_memory_scope() -> dict[str, str]:
    """Return the active DOI/top-entity scope for this generated package."""
    return dict(_scope_registry().get(_REGISTRY_KEY) or {})


def bound_root_iri() -> str:
    """Return the pipeline-bound root for the active package session."""
    return str(current_memory_scope().get("bound_root_iri") or "").strip()


def bind_root_argument(requested_iri: str) -> dict[str, Any]:
    """Canonicalize an agent-supplied root handle to the session-bound root."""
    requested = str(requested_iri or "").strip()
    effective = bound_root_iri()
    if not effective:
        # Compatibility for direct unit/harness calls that predate root binding.
        effective = requested
    return {
        "requested_root_iri": requested,
        "effective_root_iri": effective,
        "root_argument_canonicalized": bool(effective and requested != effective),
        "binding_source": "session" if bound_root_iri() else "legacy_argument",
    }


def bind_parent_occurrence_argument(requested_iri: str) -> dict[str, Any]:
    """Resolve a nested parent_iri without substituting the session root.

    Extension packages bind an upstream parent root for init/export, then
    seed exactly-one enrichment identities into the retained graph. Agents
    often pass that session root into a child creator. When the pipeline has
    seeded one enrichment target, rewrite to that parent occurrence.
    """
    requested = str(requested_iri or "").strip()
    bound = bound_root_iri()
    from .lifecycle import _enrichment_targets_from_global_state

    targets = [
        str(item.get("target_iri") or "").strip()
        for item in _enrichment_targets_from_global_state()
        if str(item.get("target_iri") or "").strip()
    ]
    if requested and requested in targets:
        return {
            "requested_root_iri": requested,
            "effective_root_iri": requested,
            "root_argument_canonicalized": False,
            "binding_source": "enrichment_target",
            "enrichment_targets": targets,
        }
    if requested and bound and requested != bound:
        return {
            "requested_root_iri": requested,
            "effective_root_iri": requested,
            "root_argument_canonicalized": False,
            "binding_source": "parent_occurrence",
            "enrichment_targets": targets,
        }
    if len(targets) == 1:
        return {
            "requested_root_iri": requested,
            "effective_root_iri": targets[0],
            "root_argument_canonicalized": requested != targets[0],
            "binding_source": "enrichment_target",
            "enrichment_targets": targets,
        }
    return {
        "requested_root_iri": requested,
        "effective_root_iri": "",
        "root_argument_canonicalized": False,
        "binding_source": "unbound",
        "bound_root_iri": bound,
        "enrichment_targets": targets,
        "message": (
            "parent_iri must be the parent occurrence IRI, not the session "
            "bound root. Pass a created or pipeline-seeded parent IRI."
        ),
    }


def bound_enrichment_target_iri(class_iri: str = "") -> str:
    """Return the unique pipeline-seeded enrichment IRI for one class, if any."""
    from .lifecycle import _enrichment_targets_from_global_state

    wanted = str(class_iri or "").strip()
    matches: list[str] = []
    seen: set[str] = set()
    for item in _enrichment_targets_from_global_state():
        target_iri = str(item.get("target_iri") or "").strip()
        item_class = str(item.get("class_iri") or "").strip()
        if not target_iri or target_iri in seen:
            continue
        if wanted and item_class != wanted:
            continue
        seen.add(target_iri)
        matches.append(target_iri)
    if len(matches) == 1:
        return matches[0]
    return ""


def register_semantic_rejection(
    fingerprint: str,
    payload: dict[str, Any],
    *,
    skippable: bool,
) -> None:
    """Register one scope-local rejection before a later skip can resolve it."""
    token = str(fingerprint or "").strip().lower()
    if len(token) != 64 or any(char not in "0123456789abcdef" for char in token):
        return
    scope = current_memory_scope()
    _rejection_registry().setdefault(_REGISTRY_KEY, {})[token] = {
        "doi": str(scope.get("doi") or ""),
        "top_level_entity_name": str(scope.get("top_level_entity_name") or ""),
        "bound_root_iri": str(scope.get("bound_root_iri") or ""),
        "code": str(payload.get("code") or ""),
        "tool_name": str(payload.get("tool_name") or ""),
        "skippable": bool(skippable),
        "resolved": False,
        "evidence": {
            key: payload.get(key)
            for key in ("code", "message", "facet", "source_value")
            if payload.get(key) is not None
        },
    }


def resolve_semantic_skip(obligation_id: str, reason: str) -> str:
    """Authorize a skip only for a registered, explicitly skippable rejection."""
    token = str(obligation_id or "").strip().lower()
    explanation = str(reason or "").strip()
    if len(token) != 64 or any(char not in "0123456789abcdef" for char in token):
        return error_json(
            code="INVALID_OBLIGATION_ID",
            message=(
                "Use the exact 64-character obligation_id for a facet warning, "
                "or semantic_fingerprint for a rejected operation."
            ),
            graph_changed=False,
            retryable=True,
        )
    if not explanation:
        return error_json(
            code="MISSING_SKIP_REASON",
            message="A concise policy-grounded skip reason is required.",
            obligation_id=token,
            graph_changed=False,
            retryable=True,
        )
    rejection = (_rejection_registry().get(_REGISTRY_KEY) or {}).get(token)
    if not isinstance(rejection, dict) or rejection.get("resolved") is True:
        return error_json(
            code="UNKNOWN_SEMANTIC_OBLIGATION",
            message="No unresolved rejection with this fingerprint exists in the current session.",
            obligation_id=token,
            graph_changed=False,
            retryable=True,
            skippable=False,
            recovery={"action": "retry_original_operation"},
        )
    if rejection.get("skippable") is not True:
        return error_json(
            code="SKIP_NOT_AUTHORIZED",
            message="This rejection is required and must be repaired, not skipped.",
            obligation_id=token,
            semantic_fingerprint=token,
            graph_changed=False,
            retryable=True,
            skippable=False,
            original_rejection={
                key: rejection.get(key)
                for key in ("code", "tool_name", "bound_root_iri")
                if rejection.get(key)
            },
            recovery={
                "action": "retry_original_operation",
                "bound_root_iri": str(rejection.get("bound_root_iri") or ""),
            },
        )
    rejection["resolved"] = True
    return result_json(
        {
            "status": "skipped",
            "policy_valid": True,
            "obligation_id": token,
            "graph_changed": False,
            "reason": explanation,
            "skip_receipt": {
                "policy": "parser_verified_unrepresentable_facet",
                "controlled": True,
                "evidence": dict(rejection.get("evidence") or {}),
            },
        }
    )


def reset_retained_graph() -> Graph:
    """Reset and return this generated package's retained graph."""
    _scope_registry().pop(_REGISTRY_KEY, None)
    _reuse_grant_registry().pop(_REGISTRY_KEY, None)
    return reset_graph(retained_graph())


def reset_graph(graph: Graph) -> Graph:
    """Remove all triples while preserving the graph object used by callers."""
    graph.remove((None, None, None))
    return graph


@contextmanager
def atomic_graph_transaction():
    """Rollback every retained-graph mutation if a composite operation fails."""
    with _GRAPH_TRANSACTION_LOCK:
        graph = retained_graph()
        snapshot = set(graph)
        try:
            yield graph
        except BaseException:
            graph.remove((None, None, None))
            for triple in snapshot:
                graph.add(triple)
            raise


def serialize_turtle(graph: Graph) -> str:
    """Serialize a graph to normalized UTF-8 Turtle text."""
    serialized = graph.serialize(format="turtle")
    if isinstance(serialized, bytes):
        return serialized.decode("utf-8")
    return str(serialized)


def abox_graph(graph: Graph) -> Graph:
    """Return asserted instance facts without schema or runtime bookkeeping."""
    internal_runtime_prefixes = ("urn:twa:semantic-mutation:",)
    schema_types = {
        OWL.Class,
        RDFS.Class,
        OWL.ObjectProperty,
        OWL.DatatypeProperty,
        OWL.AnnotationProperty,
        OWL.Ontology,
        RDF.Property,
    }
    schema_subjects = {
        subject
        for subject, _, obj in graph.triples((None, RDF.type, None))
        if obj in schema_types
    }
    schema_predicates = {
        RDF.first,
        RDF.rest,
        RDFS.domain,
        RDFS.range,
        RDFS.subClassOf,
        RDFS.subPropertyOf,
        OWL.intersectionOf,
        OWL.equivalentClass,
        OWL.equivalentProperty,
        OWL.inverseOf,
    }
    result = new_graph(
        namespace_bindings={
            prefix: str(namespace) for prefix, namespace in graph.namespaces()
        }
    )
    for subject, predicate, obj in graph:
        if (
            isinstance(subject, BNode)
            or subject in schema_subjects
            or predicate in schema_predicates
            or any(
                str(subject).startswith(prefix) or str(predicate).startswith(prefix)
                for prefix in internal_runtime_prefixes
            )
        ):
            continue
        result.add((subject, predicate, obj))
    return result


def export_graph_result(
    graph: Graph,
    *,
    top_iri: str | URIRef | None = None,
    status: str = "ok",
    include_schema: bool = False,
    **metadata: Any,
) -> dict[str, Any]:
    """Export A-Box data and persist it when runtime scope metadata is supplied."""
    exported_graph = graph if include_schema else abox_graph(graph)
    ttl = serialize_turtle(exported_graph)
    safe_metadata = {
        key: value
        for key, value in metadata.items()
        if key not in {"status", "top_iri", "ttl", "triple_count", "includes_schema"}
    }
    doi = str(metadata.get("doi") or "").strip()
    scope = str(
        metadata.get("scope") or metadata.get("top_level_entity_name") or ""
    ).strip()
    if doi and scope:
        memory_path, export_path = scoped_memory_paths(doi, scope)
        _write_text_long_path_safe(memory_path, ttl)
        _write_text_long_path_safe(export_path, ttl)
        safe_metadata["memory_path"] = str(memory_path)
        safe_metadata["export_path"] = str(export_path)
    return {
        "status": str(status),
        "top_iri": str(top_iri or ""),
        "ttl": ttl,
        "triple_count": len(exported_graph),
        "includes_schema": bool(include_schema),
        **safe_metadata,
    }


def _write_text_long_path_safe(path: Path, content: str) -> None:
    """Write UTF-8 text using extended Windows paths when necessary."""
    resolved = str(path.resolve())
    if os.name == "nt" and not resolved.startswith("\\\\?\\"):
        resolved = f"\\\\?\\{resolved}"
    with open(resolved, "w", encoding="utf-8") as handle:
        handle.write(content)
