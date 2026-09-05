"""Domain-independent RDF graph state and Turtle serialization."""

from __future__ import annotations

import builtins
import base64
import functools
import importlib
import inspect
import json
import os
import re
import threading
import time
import unicodedata
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from rdflib import BNode, Graph, Literal, URIRef
from rdflib.namespace import OWL, RDF, RDFS, XSD


_REGISTRY_NAME = "_twa_generated_rdf_graph_registry"
_SCOPE_REGISTRY_NAME = "_twa_generated_rdf_scope_registry"
_REUSE_GRANT_REGISTRY_NAME = "_twa_generated_reuse_grant_registry"
_REJECTION_REGISTRY_NAME = "_twa_generated_semantic_rejection_registry"
_PACKAGE_NAMESPACE = __name__.rsplit(".", 1)[0]
_REGISTRY_KEY = f"{Path(__file__).resolve()}::{_PACKAGE_NAMESPACE}"
_INSTANCE_BASE_IRI = "https://www.theworldavatar.com/kg/instance/generated/"
_GRAPH_TRANSACTION_LOCK = threading.RLock()
# Official indep10 0827 Mo24: 46009-char argument, ChemicalOutput+export still landed.
# Larger recorded args (56062 / 66235) starved the rest of the session.
_TOOL_TEXT_MAX_CHARS = 46009
_SKIP_SANITIZE_PARAMS = frozenset(
    {
        "parent_iri",
        "subject_iri",
        "doi",
        "root_iri",
        "top_level_entity_name",
        "obligation_id",
        "semantic_fingerprint",
        "requested_root_iri",
    }
)


def _looks_like_iri(value: str) -> bool:
    return value.startswith(("http://", "https://", "urn:"))


def sanitize_tool_text(
    value: str,
    *,
    max_chars: int = _TOOL_TEXT_MAX_CHARS,
) -> str:
    """Cap oversized string arguments. Ordinary values are unchanged."""
    text = str(value)
    if len(text) <= max_chars or _looks_like_iri(text):
        return text
    return text[:max_chars]


def wrap_public_tool(function: Callable[..., Any]) -> Callable[..., Any]:
    """Cap long string kwargs at the public MCP tool boundary."""
    signature = inspect.signature(function)

    @functools.wraps(function)
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        bound = signature.bind_partial(*args, **kwargs)
        bound.apply_defaults()
        for name, item in list(bound.arguments.items()):
            if name in _SKIP_SANITIZE_PARAMS or not isinstance(item, str):
                continue
            bound.arguments[name] = sanitize_tool_text(item)
        return function(*bound.args, **bound.kwargs)

    wrapped.__signature__ = signature
    return wrapped


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

    Extension packages bind an upstream synthesis root for init/export, then
    seed exactly-one enrichment identities into the retained graph. Agents
    often pass that session root into a child creator. When the pipeline has
    seeded one enrichment target, rewrite to that parent occurrence.
    """
    requested = str(requested_iri or "").strip()
    bound = bound_root_iri()
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


def _short_random_iri() -> str:
    """Mint a compact 96-bit URL-safe identifier for non-deterministic instances."""
    token = base64.urlsafe_b64encode(uuid4().bytes[:12]).decode("ascii").rstrip("=")
    return f"{_INSTANCE_BASE_IRI}{token}"


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


def reset_retained_graph() -> Graph:
    """Reset and return this generated package's retained graph."""
    _scope_registry().pop(_REGISTRY_KEY, None)
    _reuse_grant_registry().pop(_REGISTRY_KEY, None)
    return reset_graph(retained_graph())


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


def safe_filename_component(value: str) -> str:
    """Normalize a runtime scope using the pipeline's canonical filename policy."""
    normalized = unicodedata.normalize("NFKC", str(value or "").strip())
    chars: list[str] = []
    for char in normalized:
        if ord(char) < 128:
            chars.append(char)
            continue
        try:
            char_name = unicodedata.name(char)
        except ValueError:
            chars.append("_")
            continue
        if char_name.startswith("GREEK ") and " LETTER " in char_name:
            chars.append(char_name.rsplit(" LETTER ", 1)[-1].lower())
        else:
            chars.append("_")
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", "".join(chars)).strip("._")
    return text or "entity"


def resolve_case_dirname(doi_value: str) -> str:
    """Resolve a DOI/document identifier to the pipeline's canonical case folder."""
    raw = str(doi_value or "").strip() or "unknown"
    safe = safe_filename_component(raw)
    root = Path(os.environ.get("TWA_AGENTIC_DATA_DIR") or "data")
    mapping_path = root / "doi_to_hash.json"
    if not mapping_path.exists():
        return safe
    try:
        mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
    except Exception:
        return safe
    hashes = {str(value).strip() for value in mapping.values() if str(value).strip()}
    if safe in hashes:
        return safe
    candidates = {
        raw,
        safe,
        raw.replace("_", "/"),
        raw.replace("/", "_"),
        safe.replace("_", "/"),
    }
    for doi_key, hash_value in mapping.items():
        key = str(doi_key or "").strip()
        hashed = str(hash_value or "").strip()
        if not key or not hashed:
            continue
        key_us = key.replace("/", "_")
        if (
            key in candidates
            or key_us in candidates
            or safe_filename_component(key_us) == safe
        ):
            return hashed
    return safe


def _package_ontology_name() -> str:
    """Return the ontology name declared by this package's relationship contract."""
    contract_path = Path(__file__).with_name("_relationship_contract.json")
    if not contract_path.is_file():
        return ""
    try:
        payload = json.loads(contract_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    if not isinstance(payload, dict):
        return ""
    return str(payload.get("ontology_name") or "").strip()


def scoped_memory_paths(
    doi: str,
    top_level_entity_name: str,
) -> tuple[Path, Path]:
    """Return canonical memory and timestamped export paths for one scope.

    The pipeline-owned main ontology keeps the historical ``memory/`` directory.
    Extension packages persist under ``memory_<ontology>/`` so the pipeline can
    validate and promote them without reading the shared main A-Box memory.
    """
    root = Path(os.environ.get("TWA_AGENTIC_DATA_DIR") or "data")
    case_dir = root / resolve_case_dirname(doi)
    ontology_name = _package_ontology_name()
    main_ontology = str(
        os.environ.get("TWA_MAIN_ONTOLOGY_NAME") or "ontosynthesis"
    ).strip()
    if ontology_name and ontology_name != main_ontology:
        memory_dir = case_dir / f"memory_{safe_filename_component(ontology_name)}"
        exports_dir = case_dir / f"exports_{safe_filename_component(ontology_name)}"
    else:
        memory_dir = case_dir / "memory"
        exports_dir = case_dir / "exports"
    memory_dir.mkdir(parents=True, exist_ok=True)
    exports_dir.mkdir(parents=True, exist_ok=True)
    safe_entity = safe_filename_component(top_level_entity_name)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return (
        memory_dir / f"{safe_entity}.ttl",
        exports_dir / f"{safe_entity}_{timestamp}.ttl",
    )


def central_memory_paths(ontology_name: str) -> tuple[Path, Path]:
    """Return ontology-wide reusable-entity graph and provenance paths."""
    configured = str(os.environ.get("TWA_CENTRAL_MEMORY_DIR") or "").strip()
    root = (
        Path(configured)
        if configured
        else Path(os.environ.get("TWA_AGENTIC_DATA_DIR") or "data") / "central_memory"
    )
    root.mkdir(parents=True, exist_ok=True)
    safe_ontology = safe_filename_component(ontology_name)
    return (
        root / f"{safe_ontology}_reusable_entities.ttl",
        root / f"{safe_ontology}_reusable_entities.provenance.json",
    )


def document_memory_paths(
    ontology_name: str,
    doi: str,
) -> tuple[Path, Path]:
    """Return one DOI-owned reusable-entity graph and provenance path."""
    safe_ontology = safe_filename_component(ontology_name)
    root = Path(os.environ.get("TWA_AGENTIC_DATA_DIR") or "data")
    case_dir = root / resolve_case_dirname(doi)
    memory_dir = (
        case_dir / f"memory_{safe_ontology}"
        if safe_ontology and safe_ontology != "ontosynthesis"
        else case_dir / "memory"
    )
    memory_dir.mkdir(parents=True, exist_ok=True)
    graph_path = memory_dir / "document.ttl"
    return graph_path, graph_path.with_suffix(".provenance.json")


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
    contract_path = Path(__file__).with_name("_relationship_contract.json")
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


def _package_relationship_contract() -> dict[str, Any]:
    contract_path = Path(__file__).with_name("_relationship_contract.json")
    if not contract_path.is_file():
        return {}
    try:
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return contract if isinstance(contract, dict) else {}


def _package_ontology_name() -> str:
    return str(_package_relationship_contract().get("ontology_name") or "").strip()


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


def _enrichment_targets_from_global_state() -> list[dict[str, str]]:
    """Read pipeline-bound extension identities from the package global state."""
    ontology_name = _package_ontology_name()
    if not ontology_name:
        return []
    data_dir = (
        os.environ.get("TWA_AGENTIC_DATA_DIR")
        or os.environ.get("TWA_EXTENSION_DATA_DIR")
        or "data"
    )
    state_path = Path(data_dir) / f"{ontology_name}_global_state.json"
    if not state_path.is_file():
        return []
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:
        return []
    targets: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
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


class RelationshipContractError(ValueError):
    """Structured rejection raised before an invalid relationship can mutate RDF."""

    def __init__(self, code: str, details: dict[str, Any]) -> None:
        self.code = str(code)
        self.details = dict(details)
        super().__init__(
            json.dumps(
                {"status": "rejected", "code": self.code, **self.details},
                sort_keys=True,
            )
        )


def _compatible_type(
    actual_types: set[str],
    expected_types: set[str],
    subclass_closure: dict[str, set[str]],
) -> bool:
    return any(
        actual == expected or expected in subclass_closure.get(actual, {actual})
        for actual in actual_types
        for expected in expected_types
    )


def _typed_subject_candidates(
    graph: Graph,
    expected_types: set[str],
    subclass_closure: dict[str, set[str]],
    *,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """List scope-local subjects compatible with an expected domain."""
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    bound = bound_root_iri()
    for subject in graph.subjects(RDF.type, None):
        if not isinstance(subject, URIRef) or str(subject) in seen:
            continue
        actual_types = {
            str(value)
            for value in graph.objects(subject, RDF.type)
            if isinstance(value, URIRef)
        }
        if expected_types and not _compatible_type(
            actual_types,
            expected_types,
            subclass_closure,
        ):
            continue
        iri = str(subject)
        seen.add(iri)
        candidates.append(
            {
                "iri": iri,
                "type_iris": sorted(actual_types),
                "labels": sorted(
                    {
                        str(value)
                        for value in graph.objects(subject, RDFS.label)
                        if str(value).strip()
                    }
                )[:3],
                "is_bound_root": bool(bound and iri == bound),
            }
        )
    candidates.sort(
        key=lambda item: (
            not bool(item.get("is_bound_root")),
            str(item.get("iri") or ""),
        )
    )
    return candidates[: max(1, int(limit))]


def _hydrate_reusable_object_from_central_memory(
    *,
    graph: Graph,
    obj: URIRef,
    ontology_name: str,
    reusable_class_iris: set[str],
    document_reusable_class_iris: set[str],
    expected_range_iris: set[str],
    subclass_closure: dict[str, set[str]],
    reuse_authorization_token: str | None,
) -> dict[str, Any] | None:
    """Project trusted descriptors from the policy-routed reuse store."""
    if (
        not ontology_name
        or not (reusable_class_iris or document_reusable_class_iris)
        or not expected_range_iris
    ):
        return None
    central, _ = load_central_reuse_memory(ontology_name)
    candidate_graph = central
    allowed_class_iris = reusable_class_iris
    candidate_types = {
        str(value)
        for value in candidate_graph.objects(obj, RDF.type)
        if isinstance(value, URIRef)
    }
    source = "central_memory"
    if not candidate_types.intersection(allowed_class_iris):
        document, _ = load_document_reuse_memory(ontology_name)
        candidate_graph = document
        allowed_class_iris = document_reusable_class_iris
        candidate_types = {
            str(value)
            for value in candidate_graph.objects(obj, RDF.type)
            if isinstance(value, URIRef)
        }
        source = "document_memory"
    if not candidate_types.intersection(allowed_class_iris):
        return None
    if not _compatible_type(
        candidate_types,
        expected_range_iris,
        subclass_closure,
    ):
        return None
    grant = _validate_central_reuse_authorization(
        str(obj),
        reuse_authorization_token,
    )

    hydrated_types = sorted(candidate_types)
    hydrated_labels = sorted(
        {
            str(value)
            for value in candidate_graph.objects(obj, RDFS.label)
            if isinstance(value, Literal) and str(value).strip()
        }
    )
    for type_iri in hydrated_types:
        graph.add((obj, RDF.type, URIRef(type_iri)))
    for label in hydrated_labels:
        graph.add((obj, RDFS.label, Literal(label)))
    return {
        "object_type_source": source,
        "hydrated_type_iris": hydrated_types,
        "hydrated_labels": hydrated_labels,
        "reuse_authorization_pair_id": grant["pair_id"],
    }


def _clone_occurrence_local_object(graph: Graph, source: URIRef) -> URIRef:
    """Mint a shallow clone of an occurrence-local node for a new owner slot.

    Incoming owner links stay on ``source``. Outgoing descriptors (types, labels,
    values, units, and other attributes) are copied onto a fresh IRI so equal
    values can satisfy a second ordered member without sharing identity.
    """
    clone = URIRef(_short_random_iri())
    for predicate, value in graph.predicate_objects(source):
        graph.add((clone, predicate, value))
    return clone


def _bound_relationship_writer(
    *,
    predicate_iri: str,
    domain_iris: set[str],
    range_iris: set[str],
    subclass_closure: dict[str, set[str]],
    relationship_specs: list[dict[str, Any]] | None = None,
    creator_owned_relationships: dict[str, list[dict[str, str]]] | None = None,
    ontology_name: str = "",
    reusable_class_iris: set[str] | None = None,
    document_reusable_class_iris: set[str] | None = None,
    non_reusable_class_iris: set[str] | None = None,
    ordered_member_class_iris: set[str] | None = None,
) -> Callable[[str, str, str | None], dict[str, Any]]:
    """Create one private mutation capability with an immutable T-Box contract."""
    predicate = URIRef(predicate_iri)
    reusable_classes = set(reusable_class_iris or set())
    document_reusable_classes = set(document_reusable_class_iris or set())
    non_reusable_classes = set(non_reusable_class_iris or set())
    ordered_member_classes = set(ordered_member_class_iris or set())
    available_relationship_specs = list(relationship_specs or [])
    atomic_owners = dict(creator_owned_relationships or {})

    def write(
        subject_iri: str,
        object_iri: str,
        reuse_authorization_token: str | None = None,
    ) -> dict[str, Any]:
        graph = retained_graph()
        subject = URIRef(str(subject_iri))
        obj = URIRef(str(object_iri))
        subject_types = {str(value) for value in graph.objects(subject, RDF.type)}
        object_types = {str(value) for value in graph.objects(obj, RDF.type)}

        def compatible_bindings() -> list[dict[str, Any]]:
            bindings: list[dict[str, Any]] = []
            if not subject_types or not object_types:
                return bindings
            for spec in available_relationship_specs:
                candidate_iri = str(spec.get("property_iri") or "").strip()
                candidate_domains = {
                    str(value) for value in spec.get("domain_iris") or []
                }
                candidate_ranges = {
                    str(value) for value in spec.get("range_iris") or []
                }
                if (
                    not candidate_iri
                    or not candidate_domains
                    or not candidate_ranges
                    or not _compatible_type(
                        subject_types, candidate_domains, subclass_closure
                    )
                    or not _compatible_type(
                        object_types, candidate_ranges, subclass_closure
                    )
                ):
                    continue
                owners = atomic_owners.get(candidate_iri) or []
                local = candidate_iri.rsplit("#", 1)[-1].rsplit("/", 1)[-1]
                bindings.append(
                    {
                        "predicate_iri": candidate_iri,
                        "operation": (
                            {
                                "mode": "atomic_creator",
                                "public_tools": sorted(
                                    {
                                        str(owner.get("public_tool") or "")
                                        for owner in owners
                                        if str(owner.get("public_tool") or "")
                                    }
                                ),
                                "instruction": (
                                    "This edge is creator-owned. If its owner was already created "
                                    "successfully, the binding is already complete; do not call or "
                                    "invent a standalone relationship writer."
                                ),
                            }
                            if owners
                            else {
                                "mode": "standalone_relationship",
                                "public_tool": f"add_{local}",
                            }
                        ),
                    }
                )
            return sorted(bindings, key=lambda item: item["predicate_iri"])

        if not subject_types:
            raise RelationshipContractError(
                "SUBJECT_TYPE_MISSING",
                {
                    "predicate_iri": predicate_iri,
                    "domain_iris": sorted(domain_iris),
                    "domain_node": str(subject),
                    "skippable": False,
                    "retryable": True,
                    "recovery": {
                        "action": "use_bound_root_or_candidate_subject",
                        "bound_root_iri": bound_root_iri(),
                        "candidate_subjects": _typed_subject_candidates(
                            graph,
                            domain_iris,
                            subclass_closure,
                        ),
                        "instruction": (
                            "Retry with the bound root when this is a root-owned "
                            "operation; otherwise choose only a source-grounded "
                            "candidate from the current session."
                        ),
                    },
                },
            )
        if domain_iris and not _compatible_type(
            subject_types,
            domain_iris,
            subclass_closure,
        ):
            raise RelationshipContractError(
                "DOMAIN_TYPE_MISMATCH",
                {
                    "predicate_iri": predicate_iri,
                    "skippable": True,
                    "recovery": {
                        "action": "use_compatible_binding_or_skip_relationship",
                        "do_not_retry_subject_iri": str(subject),
                        "compatible_bindings": compatible_bindings(),
                        "instruction": (
                            "Do not retry the rejected predicate with this subject or another "
                            "subject of the same type. Follow compatible_bindings when non-empty. "
                            "For an atomic_creator binding, a successful owner creator already "
                            "completed the edge. Otherwise use the listed standalone tool. If no "
                            "source-grounded compatible binding applies, skip this relationship "
                            "and continue remaining obligations without repeating prior successes."
                        ),
                    },
                    "actual_type_iris": sorted(subject_types),
                    "expected_domain_iris": sorted(domain_iris),
                },
            )

        central_projection = None
        if not object_types:
            central_projection = _hydrate_reusable_object_from_central_memory(
                graph=graph,
                obj=obj,
                ontology_name=ontology_name,
                reusable_class_iris=reusable_classes,
                document_reusable_class_iris=document_reusable_classes,
                expected_range_iris=range_iris,
                subclass_closure=subclass_closure,
                reuse_authorization_token=reuse_authorization_token,
            )
            object_types = {
                str(value) for value in graph.objects(obj, RDF.type)
            }
        if not object_types:
            raise RelationshipContractError(
                "OBJECT_TYPE_MISSING",
                {
                    "predicate_iri": predicate_iri,
                    "range_iris": sorted(range_iris),
                    "range_node": str(obj),
                    "skippable": True,
                    "retryable": True,
                    "recovery": {
                        "action": "use_candidate_object_or_skip_relationship",
                        "candidate_objects": _typed_subject_candidates(
                            graph,
                            range_iris,
                            subclass_closure,
                        ),
                    },
                },
            )
        if range_iris and not _compatible_type(
            object_types,
            range_iris,
            subclass_closure,
        ):
            raise RelationshipContractError(
                "RANGE_TYPE_MISMATCH",
                {
                    "predicate_iri": predicate_iri,
                    "skippable": True,
                    "recovery": {
                        "action": "use_compatible_binding_or_skip_relationship",
                        "do_not_retry_object_iri": str(obj),
                        "compatible_bindings": compatible_bindings(),
                        "instruction": (
                            "Do not retry the rejected predicate with this object or another "
                            "object of the same type. Follow compatible_bindings when non-empty. "
                            "For an atomic_creator binding, a successful owner creator already "
                            "completed the edge. Otherwise use the listed standalone tool. If no "
                            "source-grounded compatible binding applies, skip this relationship "
                            "and continue remaining obligations without repeating prior successes."
                        ),
                    },
                    "actual_type_iris": sorted(object_types),
                    "expected_range_iris": sorted(range_iris),
                },
            )
        object_is_non_reusable = bool(
            non_reusable_classes
            and _compatible_type(
                object_types,
                non_reusable_classes,
                subclass_closure,
            )
        )
        subject_is_ordered_member = bool(
            ordered_member_classes
            and _compatible_type(
                subject_types,
                ordered_member_classes,
                subclass_closure,
            )
        )
        clone_meta: dict[str, Any] | None = None
        if object_is_non_reusable and subject_is_ordered_member:
            conflicting_uses = sorted(
                {
                    (str(existing_subject), str(existing_predicate))
                    for existing_subject, existing_predicate in graph.subject_predicates(
                        obj
                    )
                    if _compatible_type(
                        {
                            str(value)
                            for value in graph.objects(existing_subject, RDF.type)
                        },
                        ordered_member_classes,
                        subclass_closure,
                    )
                    # Non-reusability prevents one occurrence from filling the
                    # same semantic role for multiple ordered members. It does
                    # not prohibit an exact occurrence from participating in a
                    # different T-Box relationship role.
                    and existing_predicate == predicate
                    and existing_subject != subject
                }
            )
            if conflicting_uses:
                requested_object_iri = str(obj)
                obj = _clone_occurrence_local_object(graph, obj)
                clone_meta = {
                    "auto_cloned_occurrence": True,
                    "requested_object_iri": requested_object_iri,
                    "cloned_object_iri": str(obj),
                    "reason": "OBJECT_OCCURRENCE_REUSE_FORBIDDEN",
                    "conflicting_uses": [
                        {
                            "subject_iri": existing_subject,
                            "predicate_iri": existing_predicate,
                        }
                        for existing_subject, existing_predicate in conflicting_uses
                    ],
                    "message": (
                        "Requested object was already bound to another ordered "
                        "member for this predicate. Minted a fresh occurrence-local "
                        "clone for this owner so the relationship could proceed."
                    ),
                }
        graph.add((subject, predicate, obj))
        result = {
            "status": "ok",
            "action": "add_relationship",
            "triple": [str(subject), predicate_iri, str(obj)],
        }
        if clone_meta:
            result.update(clone_meta)
        if central_projection:
            result.update(central_projection)
        return result

    return write


def _compile_relationship_capabilities(
    ontology_contract: dict[str, Any],
) -> dict[str, Callable[[str, str], dict[str, Any]]]:
    """Compile property-specific, fail-closed writers from a T-Box contract."""
    closure = {
        str(item.get("class_iri") or ""): {
            str(value) for value in item.get("superclass_iris") or []
        }
        for item in ontology_contract.get("subclass_closure") or []
        if str(item.get("class_iri") or "")
    }
    reuse_policy = ontology_contract.get("reuse_policy") or {}
    reusable_class_iris = {
        str(item.get("class_iri") or "").strip()
        for item in reuse_policy.get("classes") or []
        if isinstance(item, dict)
        and item.get("reusable") is True
        and str(item.get("class_iri") or "").strip()
        and str(item.get("reuse_scope") or "legacy_unspecified").strip()
        in {"global", "global_value", "global_reference", "legacy_unspecified"}
    }
    document_reusable_class_iris = {
        str(item.get("class_iri") or "").strip()
        for item in reuse_policy.get("classes") or []
        if isinstance(item, dict)
        and item.get("reusable") is True
        and str(item.get("class_iri") or "").strip()
        and str(item.get("reuse_scope") or "").strip() == "document"
    }
    non_reusable_class_iris = {
        str(item.get("class_iri") or "").strip()
        for item in reuse_policy.get("classes") or []
        if isinstance(item, dict)
        and item.get("reusable") is False
        and str(item.get("class_iri") or "").strip()
    }
    ordered_member_class_iris = {
        str(item.get("class_iri") or "").strip()
        for item in ontology_contract.get("ordered_entity_creators") or []
        if isinstance(item, dict)
        and str(item.get("class_iri") or "").strip()
    }
    ontology_name = str(ontology_contract.get("ontology_name") or "").strip()
    relationship_specs = [
        dict(item)
        for item in ontology_contract.get("object_properties") or []
        if isinstance(item, dict)
    ]
    creator_owned_relationships = {
        str(predicate_iri): [
            dict(owner)
            for owner in owners or []
            if isinstance(owner, dict)
        ]
        for predicate_iri, owners in (
            ontology_contract.get("creator_owned_relationships") or {}
        ).items()
    }
    capabilities: dict[str, Callable[[str, str], dict[str, Any]]] = {}
    for item in relationship_specs:
        predicate_iri = str(item.get("property_iri") or "").strip()
        domain_iris = {str(value) for value in item.get("domain_iris") or []}
        range_iris = {str(value) for value in item.get("range_iris") or []}
        if not predicate_iri or not domain_iris or not range_iris:
            continue
        capabilities[predicate_iri] = _bound_relationship_writer(
            predicate_iri=predicate_iri,
            domain_iris=domain_iris,
            range_iris=range_iris,
            subclass_closure=closure,
            relationship_specs=relationship_specs,
            creator_owned_relationships=creator_owned_relationships,
            ontology_name=ontology_name,
            reusable_class_iris=reusable_class_iris,
            document_reusable_class_iris=document_reusable_class_iris,
            non_reusable_class_iris=non_reusable_class_iris,
            ordered_member_class_iris=ordered_member_class_iris,
        )
    return capabilities


def package_relationship_capabilities() -> dict[
    str, Callable[[str, str], dict[str, Any]]
]:
    """Load immutable relationship capabilities shipped with this package."""
    contract_path = Path(__file__).with_name("_relationship_contract.json")
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    if not isinstance(contract, dict):
        raise ValueError("Package relationship contract must be a JSON object")
    return _compile_relationship_capabilities(contract)


def _bound_entity_creator(
    class_iri: str,
    *,
    explicit_type_iris: set[str] | None = None,
    strict_subclass_iris: set[str] | None = None,
    reuse_by_label: bool = False,
) -> Callable[[str], str]:
    """Create one class-bound capability with policy-controlled identity reuse."""
    class_ref = URIRef(class_iri)
    type_refs = {
        URIRef(type_iri)
        for type_iri in (explicit_type_iris or {class_iri})
        if str(type_iri).strip()
    }
    type_refs.add(class_ref)
    strict_subclass_refs = {
        URIRef(value)
        for value in (strict_subclass_iris or set())
        if str(value).strip() and str(value) != class_iri
    }

    def configured_target() -> URIRef | None:
        """Return an exact config/SPARQL-bound extension target for this class."""
        matches = {
            item["target_iri"]
            for item in _enrichment_targets_from_global_state()
            if item["class_iri"] == class_iri
        }
        if len(matches) != 1:
            return None
        return URIRef(next(iter(matches)))

    def create(label: str) -> str:
        if not isinstance(label, str):
            raise RelationshipContractError(
                "INVALID_ENTITY_LABEL_TYPE",
                {
                    "class_iri": class_iri,
                    "actual_python_type": type(label).__name__,
                },
            )
        normalized_label = label.strip()
        if not normalized_label:
            raise RelationshipContractError(
                "EMPTY_ENTITY_LABEL",
                {"class_iri": class_iri},
            )
        graph = retained_graph()
        bound_target = configured_target()
        if bound_target is not None:
            for type_ref in sorted(type_refs, key=str):
                graph.add((bound_target, RDF.type, type_ref))
            graph.set((bound_target, RDFS.label, Literal(normalized_label)))
            return str(bound_target)
        if reuse_by_label:
            for subject in graph.subjects(RDF.type, class_ref):
                if any(
                    (subject, RDF.type, subclass_ref) in graph
                    for subclass_ref in strict_subclass_refs
                ):
                    continue
                if any(
                    str(value).strip() == normalized_label
                    for value in graph.objects(subject, RDFS.label)
                ):
                    return str(subject)
        iri = _short_random_iri()
        subject = URIRef(iri)
        for type_ref in sorted(type_refs, key=str):
            graph.add((subject, RDF.type, type_ref))
        graph.add((subject, RDFS.label, Literal(normalized_label)))
        return iri

    return create


def _compile_entity_capabilities(
    ontology_contract: dict[str, Any],
) -> dict[str, Callable[[str], str]]:
    """Compile class creators using the contract's explicit reuse policy."""
    closure = {
        str(item.get("class_iri") or ""): {
            str(value)
            for value in item.get("superclass_iris") or []
            if str(value).strip()
        }
        for item in ontology_contract.get("subclass_closure") or []
        if str(item.get("class_iri") or "").strip()
    }
    strict_subclasses = {
        class_iri: {
            candidate_iri
            for candidate_iri, superclass_iris in closure.items()
            if candidate_iri != class_iri and class_iri in superclass_iris
        }
        for class_iri in closure
    }
    reuse_policy = ontology_contract.get("reuse_policy") or {}
    reusable_class_iris = {
        str(item.get("class_iri") or "").strip()
        for item in reuse_policy.get("classes") or []
        if isinstance(item, dict)
        and item.get("reusable") is True
        and str(item.get("class_iri") or "").strip()
    }
    return {
        class_iri: _bound_entity_creator(
            class_iri,
            explicit_type_iris=closure.get(class_iri, {class_iri}),
            strict_subclass_iris=strict_subclasses.get(class_iri, set()),
            reuse_by_label=class_iri in reusable_class_iris,
        )
        for item in ontology_contract.get("classes") or []
        if (class_iri := str(item.get("class_iri") or "").strip())
    }


def package_entity_capabilities() -> dict[str, Callable[[str], str]]:
    """Load immutable class-creation capabilities shipped with this package."""
    contract_path = Path(__file__).with_name("_relationship_contract.json")
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    if not isinstance(contract, dict):
        raise ValueError("Package ontology contract must be a JSON object")
    return _compile_entity_capabilities(contract)


def _compile_ordered_entity_capabilities(
    ontology_contract: dict[str, Any],
) -> dict[str, Callable[[str, int], str]]:
    """Compile atomic label-plus-order creators from a T-Box-derived contract."""
    entity_capabilities = _compile_entity_capabilities(ontology_contract)
    datatype_capabilities = _compile_datatype_capabilities(ontology_contract)
    capabilities: dict[str, Callable[[str, int], str]] = {}
    for item in ontology_contract.get("ordered_entity_creators") or []:
        class_iri = str(item.get("class_iri") or "").strip()
        ordering_property_iri = str(item.get("ordering_property_iri") or "").strip()
        entity_creator = entity_capabilities.get(class_iri)
        order_writer = datatype_capabilities.get(ordering_property_iri)
        if not class_iri or entity_creator is None or order_writer is None:
            continue

        def create(
            label: str,
            order: int,
            *,
            _entity_creator: Callable[[str], str] = entity_creator,
            _order_writer: Callable[[str, Any], dict[str, Any]] = order_writer,
            _class_iri: str = class_iri,
        ) -> str:
            if isinstance(order, bool) or not isinstance(order, int) or order < 1:
                raise RelationshipContractError(
                    "INVALID_ORDER",
                    {
                        "class_iri": _class_iri,
                        "order": order,
                        "requirement": "positive integer",
                    },
                )
            graph = retained_graph()
            before = set(graph)
            try:
                iri = _entity_creator(label)
                _order_writer(iri, order)
                return iri
            except BaseException:
                graph.remove((None, None, None))
                for triple in before:
                    graph.add(triple)
                raise

        capabilities[class_iri] = create
    return capabilities


def package_ordered_entity_capabilities() -> dict[str, Callable[[str, int], str]]:
    """Load atomic ordered creators compiled from this package's T-Box contract."""
    contract_path = Path(__file__).with_name("_relationship_contract.json")
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    if not isinstance(contract, dict):
        raise ValueError("Package ontology contract must be a JSON object")
    return _compile_ordered_entity_capabilities(contract)


def _om2_quantity_range_iris(ontology_contract: dict[str, Any]) -> set[str]:
    """Return OM-2 classes authorized as object-property ranges."""
    marker = "ontology-of-units-of-measure.org/resource/om-2/"
    return {
        str(range_iri)
        for item in ontology_contract.get("object_properties") or []
        for range_iri in item.get("range_iris") or []
        if marker in str(range_iri)
    }


def package_om2_quantity_creator() -> Callable[[str, str], str]:
    """Create a T-Box-range-bounded OM-2 quantity creator for this package."""
    contract_path = Path(__file__).with_name("_relationship_contract.json")
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    if not isinstance(contract, dict):
        raise ValueError("Package ontology contract must be a JSON object")
    allowed_class_iris = _om2_quantity_range_iris(contract)
    try:
        om2_runtime = importlib.import_module(f"{__package__}._fixed_om2_runtime")
    except ModuleNotFoundError:
        om2_runtime = importlib.import_module(f"{__package__}.fixed_om2_runtime")
    quantity_from_label = om2_runtime.find_or_create_om2_quantity_from_label

    def create(quantity_class_iri: str, label: str) -> str:
        normalized_class_iri = str(quantity_class_iri or "").strip()
        if normalized_class_iri not in allowed_class_iris:
            raise RelationshipContractError(
                "OM2_QUANTITY_CLASS_NOT_ALLOWED",
                {
                    "quantity_class_iri": normalized_class_iri,
                    "allowed_class_iris": sorted(allowed_class_iris),
                },
            )
        if not isinstance(label, str) or not label.strip():
            raise RelationshipContractError(
                "INVALID_OM2_QUANTITY_LABEL",
                {"quantity_class_iri": normalized_class_iri},
            )

        def mint_iri(class_local: str, source_label: str) -> URIRef:
            return URIRef(_short_random_iri())

        return str(
            quantity_from_label(
                retained_graph(),
                quantity_class=URIRef(normalized_class_iri),
                label=label.strip(),
                mint_iri=mint_iri,
            )
        )

    return create


def create_om2_quantity(quantity_class_iri: str, label: str) -> str:
    """Create a bounded OM-2 quantity from a compact label.

    Numeric labels use ``<number> <unit>``. Temperature also accepts controlled
    qualitative labels such as ``room temperature`` without inventing a value.
    """
    try:
        iri = package_om2_quantity_creator()(quantity_class_iri, label)
    except RelationshipContractError as exc:
        return error_json(
            code=exc.code,
            message="OM-2 quantity creation rejected by the package contract.",
            **exc.details,
        )
    except ValueError as exc:
        return error_json(
            code="INVALID_OM2_QUANTITY",
            message=str(exc),
        )
    return success_json(
        iri=iri,
        occurrence_local=True,
        message=(
            "Created a fresh occurrence-local OM-2 quantity. Never reuse this IRI "
            "for another relationship owner, even when the value and unit match."
        ),
    )


_PYTHON_DATATYPES: dict[str, tuple[type, ...]] = {
    str(XSD.string): (str,),
    str(XSD.integer): (int,),
    str(XSD.int): (int,),
    str(XSD.decimal): (int, float),
    str(XSD.double): (int, float),
    str(XSD.float): (int, float),
    str(XSD.boolean): (bool,),
}


def _bound_datatype_writer(
    *,
    predicate_iri: str,
    domain_iris: set[str],
    range_iri: str,
    subclass_closure: dict[str, set[str]],
) -> Callable[[str, Any], dict[str, Any]]:
    """Create one private literal capability with immutable T-Box semantics."""
    predicate = URIRef(predicate_iri)
    expected_python = _PYTHON_DATATYPES.get(range_iri)

    def write(subject_iri: str, value: Any) -> dict[str, Any]:
        graph = retained_graph()
        subject = URIRef(str(subject_iri))
        subject_types = {str(item) for item in graph.objects(subject, RDF.type)}
        if not subject_types:
            raise RelationshipContractError(
                "SUBJECT_TYPE_MISSING",
                {
                    "predicate_iri": predicate_iri,
                    "subject_iri": str(subject),
                    "skippable": False,
                    "retryable": True,
                    "recovery": {
                        "action": "use_bound_root_or_candidate_subject",
                        "bound_root_iri": bound_root_iri(),
                        "candidate_subjects": _typed_subject_candidates(
                            graph,
                            domain_iris,
                            subclass_closure,
                        ),
                    },
                },
            )
        if not _compatible_type(subject_types, domain_iris, subclass_closure):
            raise RelationshipContractError(
                "DOMAIN_TYPE_MISMATCH",
                {
                    "predicate_iri": predicate_iri,
                    "actual_type_iris": sorted(subject_types),
                    "expected_domain_iris": sorted(domain_iris),
                },
            )
        invalid_bool_subclass = isinstance(value, bool) and bool not in expected_python
        if invalid_bool_subclass or not isinstance(value, expected_python):
            raise RelationshipContractError(
                "DATATYPE_MISMATCH",
                {
                    "predicate_iri": predicate_iri,
                    "expected_range_iri": range_iri,
                    "actual_python_type": type(value).__name__,
                },
            )
        if isinstance(value, str):
            value = sanitize_tool_text(value)
        # Datatype setters have replace/exactly-one semantics. This prevents
        # retries or corrected values from leaving conflicting literals behind.
        graph.remove((subject, predicate, None))
        graph.add((subject, predicate, Literal(value, datatype=URIRef(range_iri))))
        return {
            "status": "ok",
            "action": "set_datatype_property",
            "subject_iri": str(subject),
            "predicate_iri": predicate_iri,
        }

    return write


def _compile_datatype_capabilities(
    ontology_contract: dict[str, Any],
) -> dict[str, Callable[[str, Any], dict[str, Any]]]:
    """Compile property-specific literal writers from complete T-Box contracts."""
    closure = {
        str(item.get("class_iri") or ""): {
            str(value) for value in item.get("superclass_iris") or []
        }
        for item in ontology_contract.get("subclass_closure") or []
        if str(item.get("class_iri") or "")
    }
    capabilities: dict[str, Callable[[str, Any], dict[str, Any]]] = {}
    for item in ontology_contract.get("datatype_properties") or []:
        predicate_iri = str(item.get("property_iri") or "").strip()
        domain_iris = {str(value) for value in item.get("domain_iris") or []}
        range_iris = [str(value) for value in item.get("range_iris") or []]
        if (
            not predicate_iri
            or not domain_iris
            or len(range_iris) != 1
            or range_iris[0] not in _PYTHON_DATATYPES
        ):
            continue
        capabilities[predicate_iri] = _bound_datatype_writer(
            predicate_iri=predicate_iri,
            domain_iris=domain_iris,
            range_iri=range_iris[0],
            subclass_closure=closure,
        )
    return capabilities


def package_datatype_capabilities() -> dict[str, Callable[[str, Any], dict[str, Any]]]:
    """Load immutable datatype capabilities shipped with this package."""
    contract_path = Path(__file__).with_name("_relationship_contract.json")
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    if not isinstance(contract, dict):
        raise ValueError("Package ontology contract must be a JSON object")
    return _compile_datatype_capabilities(contract)


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


_SUCCESS_RESERVED_ENVELOPE_KEYS = frozenset({"status", "code", "message", "iri"})
_ERROR_RESERVED_ENVELOPE_KEYS = frozenset({"status", "code", "message"})


def _envelope_metadata(
    metadata: dict[str, Any],
    *,
    reserved: frozenset[str],
) -> dict[str, Any]:
    """Drop reserved envelope keys so callers cannot overwrite the contract."""
    return {
        key: value
        for key, value in metadata.items()
        if key not in reserved
    }


def success_result(
    *,
    iri: str = "",
    message: str = "",
    **metadata: Any,
) -> dict[str, Any]:
    """Return the shared JSON-safe success envelope for generated tools.

    ``status`` is always ``\"ok\"``. Callers must not pass ``status=``; it is ignored.
    """
    return {
        "status": "ok",
        "iri": str(iri),
        "message": str(message),
        **_envelope_metadata(metadata, reserved=_SUCCESS_RESERVED_ENVELOPE_KEYS),
    }


def error_result(
    *,
    code: str,
    message: str,
    **metadata: Any,
) -> dict[str, Any]:
    """Return the shared JSON-safe error envelope for generated tools.

    ``status`` is always ``\"rejected\"``. Put the machine-readable reason in
    ``code`` (for example ``PROPOSED_ENTITY_EVIDENCE_REQUIRED``). Callers must
    not pass ``status=`` / ``code=`` / ``message=`` via metadata; they are ignored.
    """
    return {
        "status": "rejected",
        "code": str(code),
        "message": str(message),
        **_envelope_metadata(metadata, reserved=_ERROR_RESERVED_ENVELOPE_KEYS),
    }


def result_json(result: dict[str, Any]) -> str:
    """Serialize one standard result envelope for public MCP tool transport."""
    return json.dumps(result, ensure_ascii=False, sort_keys=True)


def success_json(
    *,
    iri: str = "",
    message: str = "",
    **metadata: Any,
) -> str:
    """Return a serialized standard success envelope for public MCP tools.

    Envelope ``status`` is always ``\"ok\"``; a caller-supplied ``status=`` is ignored.
    """
    return result_json(success_result(iri=iri, message=message, **metadata))


def error_json(
    *,
    code: str,
    message: str,
    **metadata: Any,
) -> str:
    """Return a serialized standard rejection envelope for public MCP tools.

    Envelope ``status`` is always ``\"rejected\"``. Use ``code=`` for the rejection
    reason (for example ``PROPOSED_ENTITY_EVIDENCE_REQUIRED``). A caller-supplied
    ``status=`` that repeats the code string must not overwrite ``rejected``.
    """
    return result_json(error_result(code=code, message=message, **metadata))
