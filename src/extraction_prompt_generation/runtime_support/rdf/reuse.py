"""Central/document reuse memory load, publish, and authorization.

Storage scope comes from the compiled reuse policy, not class names.
See rdf/README.md.
"""

from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4

from rdflib import BNode, Graph, URIRef
from rdflib.namespace import RDF, RDFS

from .constants import RelationshipContractError, _REGISTRY_KEY, relationship_contract_path
from .paths import (
    central_memory_paths,
    document_memory_paths,
)
from .registry import (
    _reuse_grant_registry,
    current_memory_scope,
    new_graph,
)


def register_central_reuse_authorization(
    *,
    candidate_iri: str,
    pair_id: str,
    judgement: dict[str, Any],
) -> str:
    """Register one LLM-approved, scope-bound central identity grant."""
    scope = current_memory_scope()
    if not scope.get("doi") or not scope.get("top_level_entity_name"):
        raise RelationshipContractError(
            "REUSE_SCOPE_NOT_INITIALIZED",
            {"candidate_iri": candidate_iri},
        )
    required_true = (
        "reuse_authorized",
        "same_real_world_entity",
        "context_independent_identity",
        "match_basis_satisfied",
    )
    confidence = judgement.get("confidence")
    threshold = float(os.environ.get("TWA_REUSE_JUDGE_CONFIDENCE") or "0.95")
    if (
        not all(judgement.get(key) is True for key in required_true)
        or isinstance(confidence, bool)
        or not isinstance(confidence, (int, float))
        or confidence < threshold
    ):
        raise RelationshipContractError(
            "REUSE_JUDGEMENT_DENIED",
            {"candidate_iri": candidate_iri, "pair_id": pair_id},
        )
    token = uuid4().hex
    package_grants = _reuse_grant_registry().setdefault(_REGISTRY_KEY, {})
    package_grants[token] = {
        "candidate_iri": str(candidate_iri),
        "pair_id": str(pair_id),
        "doi": scope["doi"],
        "top_level_entity_name": scope["top_level_entity_name"],
    }
    return token


def _validate_central_reuse_authorization(
    candidate_iri: str,
    token: str | None,
) -> dict[str, str]:
    scope = current_memory_scope()
    grant = (
        (_reuse_grant_registry().get(_REGISTRY_KEY) or {}).get(str(token or ""))
        if token
        else None
    )
    if (
        not isinstance(grant, dict)
        or grant.get("candidate_iri") != str(candidate_iri)
        or grant.get("doi") != scope.get("doi")
        or grant.get("top_level_entity_name") != scope.get("top_level_entity_name")
    ):
        raise RelationshipContractError(
            "CENTRAL_REUSE_NOT_AUTHORIZED",
            {
                "candidate_iri": str(candidate_iri),
                "active_scope": scope,
                "authorization_token_present": bool(token),
            },
        )
    return {str(key): str(value) for key, value in grant.items()}


@contextmanager
def _central_memory_lock(path: Path):
    lock_path = path.with_suffix(path.suffix + ".lock")
    deadline = time.monotonic() + 30.0
    descriptor: int | None = None
    while descriptor is None:
        try:
            descriptor = os.open(
                str(lock_path),
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            )
            os.write(descriptor, f"{os.getpid()}\n".encode("ascii"))
        except FileExistsError:
            try:
                if time.time() - lock_path.stat().st_mtime > 120:
                    lock_path.unlink(missing_ok=True)
                    continue
            except OSError:
                pass
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"timed out waiting for central memory lock: {lock_path}"
                )
            time.sleep(0.05)
    try:
        yield
    finally:
        if descriptor is not None:
            os.close(descriptor)
        lock_path.unlink(missing_ok=True)


def _atomic_write_text(path: Path, text: str) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid4().hex}.tmp")
    temporary.write_text(text, encoding="utf-8")
    try:
        for attempt in range(20):
            try:
                os.replace(temporary, path)
                return
            except PermissionError:
                if attempt == 19:
                    raise
                # Windows readers can briefly hold the destination while an
                # MCP process reloads central memory. Retain atomic replace and
                # wait for that transient handle instead of losing the update.
                time.sleep(0.1 * (attempt + 1))
    finally:
        temporary.unlink(missing_ok=True)


def load_central_reuse_memory(
    ontology_name: str,
) -> tuple[Graph, dict[str, list[dict[str, str]]]]:
    """Load the independent cross-scope memory used only by existing checks."""
    graph_path, provenance_path = central_memory_paths(ontology_name)
    graph = new_graph()
    if graph_path.is_file() and graph_path.stat().st_size:
        graph.parse(graph_path, format="turtle")
    provenance: dict[str, list[dict[str, str]]] = {}
    if provenance_path.is_file() and provenance_path.stat().st_size:
        payload = json.loads(provenance_path.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            provenance = {
                str(iri): [
                    {
                        str(key): str(value)
                        for key, value in item.items()
                        if str(key).strip()
                    }
                    for item in entries
                    if isinstance(item, dict)
                ]
                for iri, entries in payload.items()
                if isinstance(entries, list)
            }
    return graph, provenance


def load_document_reuse_memory(
    ontology_name: str,
    doi: str | None = None,
) -> tuple[Graph, dict[str, list[dict[str, str]]]]:
    """Load candidates whose reviewed visibility is restricted to one DOI."""
    scope = current_memory_scope()
    document_id = str(doi or scope.get("doi") or "").strip()
    graph = new_graph()
    if not document_id:
        return graph, {}
    graph_path, provenance_path = document_memory_paths(ontology_name, document_id)
    if graph_path.is_file() and graph_path.stat().st_size:
        graph.parse(graph_path, format="turtle")
    provenance: dict[str, list[dict[str, str]]] = {}
    if provenance_path.is_file() and provenance_path.stat().st_size:
        payload = json.loads(provenance_path.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            provenance = {
                str(iri): [
                    {
                        str(key): str(value)
                        for key, value in item.items()
                        if str(key).strip()
                    }
                    for item in entries
                    if isinstance(item, dict)
                ]
                for iri, entries in payload.items()
                if isinstance(entries, list)
            }
    return graph, provenance


def _publish_candidate_projection(
    *,
    graph_path: Path,
    provenance_path: Path,
    existing_graph: Graph,
    existing_provenance: dict[str, list[dict[str, str]]],
    source_graph: Graph,
    class_iris: list[str],
    excluded_class_iris: list[str] | None,
    doi: str,
    top_level_entity_name: str,
) -> int:
    excluded_types = {URIRef(value) for value in (excluded_class_iris or [])}
    candidate_nodes = {
        subject
        for class_iri in class_iris
        for subject in source_graph.subjects(RDF.type, URIRef(class_iri))
        if isinstance(subject, URIRef)
        and not any(
            (subject, RDF.type, excluded_type) in source_graph
            for excluded_type in excluded_types
        )
    }
    for candidate in candidate_nodes:
        for triple in source_graph.triples((candidate, None, None)):
            existing_graph.add(triple)
            obj = triple[2]
            if isinstance(obj, (URIRef, BNode)):
                for predicate in (RDF.type, RDFS.label):
                    for value in source_graph.objects(obj, predicate):
                        existing_graph.add((obj, predicate, value))
        for subject, predicate in source_graph.subject_predicates(candidate):
            existing_graph.add((subject, predicate, candidate))
            if isinstance(subject, (URIRef, BNode)):
                for descriptor in (RDF.type, RDFS.label):
                    for value in source_graph.objects(subject, descriptor):
                        existing_graph.add((subject, descriptor, value))
        entry = {
            "doi": str(doi or "").strip(),
            "top_level_entity_name": str(top_level_entity_name or "").strip(),
        }
        records = existing_provenance.setdefault(str(candidate), [])
        if entry not in records:
            records.append(entry)
    _atomic_write_text(graph_path, str(existing_graph.serialize(format="turtle")))
    _atomic_write_text(
        provenance_path,
        json.dumps(
            existing_provenance,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ),
    )
    return len(candidate_nodes)


def publish_reusable_entities_to_document_memory(
    *,
    ontology_name: str,
    source_graph: Graph,
    reusable_class_iris: list[str],
    excluded_class_iris: list[str] | None = None,
    doi: str,
    top_level_entity_name: str,
) -> dict[str, Any]:
    """Publish only reviewed document-scope candidates into one DOI store."""
    graph_path, provenance_path = document_memory_paths(ontology_name, doi)
    with _central_memory_lock(graph_path):
        document_graph, provenance = load_document_reuse_memory(
            ontology_name, doi
        )
        published = _publish_candidate_projection(
            graph_path=graph_path,
            provenance_path=provenance_path,
            existing_graph=document_graph,
            existing_provenance=provenance,
            source_graph=source_graph,
            class_iris=reusable_class_iris,
            excluded_class_iris=excluded_class_iris,
            doi=doi,
            top_level_entity_name=top_level_entity_name,
        )
    return {
        "status": "ok",
        "ontology_name": ontology_name,
        "published_candidates": published,
        "document_graph_path": str(graph_path),
        "document_provenance_path": str(provenance_path),
    }


def publish_reusable_entities_to_central_memory(
    *,
    ontology_name: str,
    source_graph: Graph,
    reusable_class_iris: list[str],
    excluded_class_iris: list[str] | None = None,
    doi: str,
    top_level_entity_name: str,
) -> dict[str, Any]:
    """Publish a candidate-centered projection after a successful scoped export."""
    graph_path, provenance_path = central_memory_paths(ontology_name)
    with _central_memory_lock(graph_path):
        central, provenance = load_central_reuse_memory(ontology_name)
        excluded_types = {
            URIRef(value) for value in (excluded_class_iris or [])
        }
        candidate_nodes = {
            subject
            for class_iri in reusable_class_iris
            for subject in source_graph.subjects(RDF.type, URIRef(class_iri))
            if isinstance(subject, URIRef)
            and not any(
                (subject, RDF.type, excluded_type) in source_graph
                for excluded_type in excluded_types
            )
        }
        for candidate in candidate_nodes:
            for triple in source_graph.triples((candidate, None, None)):
                central.add(triple)
                obj = triple[2]
                if isinstance(obj, (URIRef, BNode)):
                    for predicate in (RDF.type, RDFS.label):
                        for value in source_graph.objects(obj, predicate):
                            central.add((obj, predicate, value))
            for subject, predicate in source_graph.subject_predicates(candidate):
                central.add((subject, predicate, candidate))
                if isinstance(subject, (URIRef, BNode)):
                    for descriptor in (RDF.type, RDFS.label):
                        for value in source_graph.objects(subject, descriptor):
                            central.add((subject, descriptor, value))
            entry = {
                "doi": str(doi or "").strip(),
                "top_level_entity_name": str(top_level_entity_name or "").strip(),
            }
            records = provenance.setdefault(str(candidate), [])
            if entry not in records:
                records.append(entry)
        _atomic_write_text(
            graph_path,
            str(central.serialize(format="turtle")),
        )
        _atomic_write_text(
            provenance_path,
            json.dumps(provenance, ensure_ascii=False, indent=2, sort_keys=True),
        )
    return {
        "status": "ok",
        "ontology_name": ontology_name,
        "published_candidates": len(candidate_nodes),
        "central_graph_path": str(graph_path),
        "central_provenance_path": str(provenance_path),
    }


def _package_reuse_policy() -> tuple[str, list[str]]:
    contract_path = relationship_contract_path()
    if not contract_path.is_file():
        return "", []
    payload = json.loads(contract_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        return "", []
    policy = payload.get("reuse_policy") or {}
    class_iris = [
        str(item.get("class_iri") or "").strip()
        for item in policy.get("classes") or []
        if isinstance(item, dict)
        and item.get("reusable") is True
        and str(item.get("class_iri") or "").strip()
    ]
    return str(payload.get("ontology_name") or "").strip(), class_iris
