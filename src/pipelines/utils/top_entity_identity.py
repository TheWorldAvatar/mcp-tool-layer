"""Pipeline-owned top-entity identity and checkpoint persistence."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import unicodedata
from pathlib import Path
from typing import Any

from rdflib import BNode, Graph, Literal, URIRef
from rdflib.namespace import RDF, RDFS


def load_selected_top_class(doi_folder: str) -> tuple[str, str]:
    """Load the extraction-selected top class and its local name."""
    path = Path(doi_folder) / "top_entity_selection.json"
    try:
        selection = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "", ""
    return (
        str(selection.get("class_iri") or "").strip(),
        str(selection.get("class_local") or "").strip(),
    )


def entity_scope_name(label: str, uri: str) -> str:
    """Return a filesystem-safe, collision-resistant entity scope."""
    normalized = unicodedata.normalize("NFKC", str(label or "entity"))
    safe_label = re.sub(r"[^A-Za-z0-9._-]+", "_", normalized)
    safe_label = re.sub(r"_+", "_", safe_label).strip("._") or "entity"
    # Keep sidecars safely below Windows MAX_PATH even inside deep scenario
    # runtimes. URI hash retention preserves collision resistance.
    safe_label = safe_label[:32].rstrip("._") or "entity"
    uri_hash = hashlib.sha256(str(uri or "").encode("utf-8")).hexdigest()[:12]
    return f"{safe_label}--{uri_hash}"


def entity_artifact_name(label: str) -> str:
    """Return the stable label-derived name used by extraction artifacts."""
    normalized = unicodedata.normalize("NFKC", str(label or "entity"))
    for character in [":", "：", "﹕", "∶", "꞉", "︰", "\uf03a"]:
        normalized = normalized.replace(character, ":")
    normalized = (
        normalized.replace("Ä", "Ae")
        .replace("Ö", "Oe")
        .replace("Ü", "Ue")
        .replace("ä", "ae")
        .replace("ö", "oe")
        .replace("ü", "ue")
        .replace("ß", "ss")
        .replace("α", "alpha")
        .replace("β", "beta")
        .replace("γ", "gamma")
        .replace("δ", "delta")
        .replace("Α", "Alpha")
        .replace("Β", "Beta")
        .replace("Γ", "Gamma")
        .replace("Δ", "Delta")
    )
    safe_label = re.sub(r"[^A-Za-z0-9._-]+", "_", normalized)
    safe_label = re.sub(r"_+", "_", safe_label).strip("_") or "entity"
    if len(safe_label) > 64:
        digest = hashlib.sha256(str(label or "").encode("utf-8")).hexdigest()[:12]
        safe_label = f"{safe_label[:48].rstrip('._-')}--{digest}"
    return safe_label


def attach_entity_identity_dossiers(
    *,
    entities: list[dict[str, Any]],
    iteration_1_ttl: str,
) -> list[dict[str, Any]]:
    """Attach an explicit, domain-independent identity dossier to each entity.

    The dossier contains only pipeline-owned identity fields and the entity's
    one-hop A-Box facts from Iteration 1. It does not infer or filter facts by
    ontology-specific predicate names.
    """
    graph = Graph()
    graph.parse(iteration_1_ttl, format="turtle")
    enriched: list[dict[str, Any]] = []
    for raw_entity in entities:
        entity = dict(raw_entity)
        uri = str(entity.get("uri") or "").strip()
        label = str(entity.get("label") or "").strip()
        if not uri or not label:
            raise ValueError("Top-entity identity dossier requires URI and label")
        node = URIRef(uri)
        outgoing_facts: list[dict[str, Any]] = []
        for predicate, value in sorted(
            graph.predicate_objects(node),
            key=lambda pair: (str(pair[0]), str(pair[1])),
        ):
            if predicate in {RDF.type, RDFS.label}:
                continue
            fact: dict[str, Any] = {"predicate_iri": str(predicate)}
            if isinstance(value, URIRef):
                fact.update(
                    {
                        "value_kind": "iri",
                        "object_iri": str(value),
                        "object_labels": sorted(
                            {
                                str(item).strip()
                                for item in graph.objects(value, RDFS.label)
                                if str(item).strip()
                            }
                        ),
                        "object_types": sorted(
                            {
                                str(item)
                                for item in graph.objects(value, RDF.type)
                                if isinstance(item, URIRef)
                            }
                        ),
                    }
                )
            elif isinstance(value, Literal):
                fact.update(
                    {
                        "value_kind": "literal",
                        "value": str(value),
                        "datatype_iri": str(value.datatype or ""),
                        "language": str(value.language or ""),
                    }
                )
            elif isinstance(value, BNode):
                fact.update({"value_kind": "blank_node", "value": str(value)})
            else:
                fact.update({"value_kind": "value", "value": str(value)})
            outgoing_facts.append(fact)
        entity["identity_dossier"] = {
            "schema_version": 1,
            "uri": uri,
            "label": label,
            "types": list(entity.get("types") or []),
            "scope_index": entity.get("scope_index"),
            "source_anchor": str(entity.get("source_anchor") or ""),
            "explicit_iteration_1_facts": outgoing_facts,
        }
        enriched.append(entity)
    return enriched


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # MCP runtimes monitor the memory directory for graph persistence. Keep
    # non-TTL atomic-write staging files outside that watched directory so a
    # concurrent scanner/cleanup cannot consume or remove them before replace.
    temporary_parent = (
        path.parent.parent
        if path.parent.name.casefold() == "memory"
        else path.parent
    )
    fd, temporary = tempfile.mkstemp(
        dir=str(temporary_parent),
        # Do not repeat the potentially long entity scope in the temporary
        # filename. On Windows that pushed otherwise valid identity paths past
        # MAX_PATH before the atomic replace.
        prefix=".identity.",
        suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def persist_entity_identity_sidecars(
    *,
    doi_hash: str,
    doi_folder: str,
    entities: list[dict[str, Any]],
    top_class_iri: str,
) -> list[str]:
    """Atomically persist one identity/checkpoint sidecar per validated entity."""
    memory_dir = Path(doi_folder) / "memory"
    paths: list[str] = []
    for entity in entities:
        uri = str(entity.get("uri") or "").strip()
        label = str(entity.get("label") or "").strip()
        types = sorted(
            {
                str(value).strip()
                for value in entity.get("types", [])
                if str(value).strip()
            }
        )
        if not uri or not label or top_class_iri not in types:
            raise ValueError(
                "Top-entity identity is incomplete or lacks the selected top class"
            )
        scope = entity_scope_name(label, uri)
        path = memory_dir / f"{scope}.identity.json"
        _write_json_atomic(
            path,
            {
                "schema_version": 1,
                "doi": doi_hash,
                "scope": scope,
                "identity": {
                    "uri": uri,
                    "label": label,
                    "types": types,
                    "top_class_iri": top_class_iri,
                    "dossier": dict(entity.get("identity_dossier") or {}),
                },
                "checkpoint": {
                    "last_completed_iteration": 1,
                    "status": "identity_persisted",
                },
            },
        )
        paths.append(str(path))
    return paths


def hydrate_and_validate_top_entity_types(
    *,
    entities: list[Any],
    iteration_1_ttl: str,
    top_class_iri: str,
) -> list[dict[str, Any]]:
    """Validate identities and backfill missing legacy ``types`` from Iteration 1."""
    if not top_class_iri:
        raise ValueError("Selected top class is required")
    graph = Graph()
    graph.parse(iteration_1_ttl, format="turtle")
    expected_type = URIRef(top_class_iri)
    hydrated: list[dict[str, Any]] = []
    for raw_entity in entities:
        if not isinstance(raw_entity, dict):
            raise ValueError("Top-entity manifest entries must be objects")
        entity = dict(raw_entity)
        uri = str(entity.get("uri") or "").strip()
        label = str(entity.get("label") or "").strip()
        if not uri or not label:
            raise ValueError("Top-entity manifest entry lacks URI or label")
        node = URIRef(uri)
        if (node, RDF.type, expected_type) not in graph:
            raise ValueError(
                f"Top entity {uri!r} is not typed as selected class {top_class_iri!r}"
            )
        graph_types = sorted(
            {
                str(type_iri)
                for type_iri in graph.objects(node, RDF.type)
                if isinstance(type_iri, URIRef)
            }
        )
        existing_types = entity.get("types")
        if not isinstance(existing_types, list) or not existing_types:
            entity["types"] = graph_types
        else:
            normalized_types = sorted(
                {str(value).strip() for value in existing_types if str(value).strip()}
            )
            if top_class_iri not in normalized_types:
                raise ValueError(
                    f"Top entity {uri!r} types do not include selected class"
                )
            entity["types"] = normalized_types
        hydrated.append(entity)
    return hydrated
