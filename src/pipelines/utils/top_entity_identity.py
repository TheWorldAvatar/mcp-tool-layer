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

from rdflib import Graph, URIRef
from rdflib.namespace import RDF


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
    uri_hash = hashlib.sha256(str(uri or "").encode("utf-8")).hexdigest()[:12]
    return f"{safe_label}--{uri_hash}"


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp"
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
