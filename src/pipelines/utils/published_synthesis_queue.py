"""Load ChemicalSynthesis identities from the published main TTL via LLM SPARQL."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from rdflib import Graph, URIRef
from rdflib.namespace import RDF

from src.pipelines.utils.ttl_publisher import (
    get_main_ontology_name,
    get_output_naming_config,
    load_meta_task_config,
)


def resolve_llm_top_entity_sparql(
    ontology_name: str,
    project_root: str = ".",
) -> Path:
    """Resolve the LLM-generated top-entity listing SPARQL."""
    relative = f"sparqls/{ontology_name}/top_entity_parsing.sparql"
    roots: list[str] = []
    override = (
        os.environ.get("TWA_GENERATED_ARTIFACT_ROOT", "")
        .strip()
        .replace("\\", "/")
        .rstrip("/")
    )
    if override:
        roots.append(override)
    roots.extend(
        [
            "ai_generated_contents_candidate",
            "ai_generated_contents",
            "ai_generated_contents_ontosyn_regen_v4",
            "ai_generated_contents_ontosyn_extensions_regen_v6",
        ]
    )
    root = Path(project_root)
    for candidate_root in roots:
        path = root / candidate_root / relative
        if path.is_file():
            return path
    raise FileNotFoundError(
        f"LLM top-entity SPARQL not found for {ontology_name}: {relative}"
    )


def published_top_ttl_path(
    doi_folder: str,
    *,
    ontology_name: str = "",
    meta_cfg: dict[str, Any] | None = None,
) -> Path | None:
    cfg = meta_cfg or load_meta_task_config()
    name = ontology_name or get_main_ontology_name(cfg)
    naming = get_output_naming_config(meta_cfg=cfg, ontology_name=name)
    path = Path(doi_folder) / naming.output_dir / naming.top_ttl_name
    return path if path.is_file() else None


def parse_top_entities_from_ttl(
    ttl_text: str,
    sparql_text: str,
) -> list[dict[str, Any]]:
    graph = Graph()
    graph.parse(data=ttl_text, format="turtle")
    rows = graph.query(sparql_text)
    entities: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        if hasattr(row, "entity") and row.entity is not None:
            uri = str(row.entity)
        elif hasattr(row, "synthesis") and row.synthesis is not None:
            uri = str(row.synthesis)
        else:
            uri = str(row[0])
        uri = uri.strip()
        if not uri or uri in seen:
            continue
        seen.add(uri)
        label = (
            str(row.label).strip()
            if hasattr(row, "label") and row.label
            else uri.rsplit("/", 1)[-1]
        )
        types = sorted(
            {
                str(type_iri)
                for type_iri in graph.objects(URIRef(uri), RDF.type)
                if isinstance(type_iri, URIRef)
            }
        )
        entities.append({"uri": uri, "label": label, "types": types})
    return entities


def _load_json_list(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        entities = payload.get("entities")
        if isinstance(entities, list):
            return [item for item in entities if isinstance(item, dict)]
    return []


def _index_entities(entities: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for entity in entities:
        uri = str(entity.get("uri") or "").strip()
        if uri:
            indexed[uri] = entity
    return indexed


def hydrate_published_entities(
    published: list[dict[str, Any]],
    *fallbacks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    extras = _index_entities(
        [entity for group in fallbacks for entity in group]
    )
    hydrated: list[dict[str, Any]] = []
    for entity in published:
        extra = extras.get(str(entity.get("uri") or "").strip()) or {}
        merged = dict(extra)
        merged.update({key: value for key, value in entity.items() if value})
        if extra.get("identity_dossier") and "identity_dossier" not in merged:
            merged["identity_dossier"] = extra["identity_dossier"]
        hydrated.append(merged)
    return hydrated


def load_extension_synthesis_queue(
    doi_folder: str,
    *,
    ontology_name: str = "",
    project_root: str = ".",
    meta_cfg: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Prefer published TTL + LLM SPARQL over a possibly thinned iter1 JSON."""
    cfg = meta_cfg or load_meta_task_config()
    name = ontology_name or get_main_ontology_name(cfg)
    doi_path = Path(doi_folder)
    lock_entities = _load_json_list(doi_path / "mcp_run" / "top_entity_identity_lock.json")
    json_entities = _load_json_list(doi_path / "mcp_run" / "iter1_top_entities.json")
    top_ttl = published_top_ttl_path(
        doi_folder,
        ontology_name=name,
        meta_cfg=cfg,
    )
    published: list[dict[str, Any]] = []
    if top_ttl is not None:
        sparql_path = resolve_llm_top_entity_sparql(name, project_root=project_root)
        published = parse_top_entities_from_ttl(
            top_ttl.read_text(encoding="utf-8"),
            sparql_path.read_text(encoding="utf-8"),
        )
    if published:
        return hydrate_published_entities(published, lock_entities, json_entities)
    if lock_entities:
        return lock_entities
    return json_entities
