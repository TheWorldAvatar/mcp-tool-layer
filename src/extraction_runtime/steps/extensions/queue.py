"""Queue inherited root individuals from published main-ontology TTL."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from rdflib import Graph

from src.extraction_runtime.artifact_root import resolve_generated_file, sparql_dir
from src.extraction_runtime.domain_binding import (
    RuntimeDomain,
    load_runtime_domain,
    resolve_upstream_ontology,
    runtime_domain_from_config,
    selected_top_class,
)
from src.extraction_runtime.steps.top_entity.identity import load_top_entities_json


def _main_ttl_files(doi_folder: Path, main_ontology: str) -> list[Path]:
    output = doi_folder / f"{main_ontology}_output"
    published = []
    if output.is_dir():
        published = sorted(
            path for path in output.glob("*.ttl") if path.stat().st_size > 0
        )
    if published:
        return published
    iteration_1 = doi_folder / "iteration_1.ttl"
    if iteration_1.is_file() and iteration_1.stat().st_size > 0:
        return [iteration_1]
    return []


def load_extension_queue(
    *,
    doi_folder: Path,
    extension_name: str,
    main_ontology: str,
) -> list[dict[str, Any]]:
    sparql_path = sparql_dir(extension_name) / "top_entity_parsing.sparql"
    if not sparql_path.is_file():
        sparql_path = resolve_generated_file(
            f"sparqls/{extension_name}/top_entity_parsing.sparql"
        )
    if not sparql_path.is_file():
        raise FileNotFoundError(f"extension queue SPARQL missing: {sparql_path}")
    query = sparql_path.read_text(encoding="utf-8")
    top = selected_top_class(main_ontology)
    entities: list[dict[str, Any]] = []
    seen: set[str] = set()
    for ttl_path in _main_ttl_files(doi_folder, main_ontology):
        graph = Graph()
        graph.parse(str(ttl_path), format="turtle")
        for row in graph.query(query):
            if hasattr(row, "entity"):
                uri = str(row.entity)
            else:
                uri = str(row[0])
            if uri in seen:
                continue
            seen.add(uri)
            label = (
                str(row.label)
                if hasattr(row, "label") and row.label
                else uri.rsplit("/", 1)[-1]
            )
            entities.append(
                {
                    "uri": uri,
                    "label": label,
                    "types": [top.get("class_iri") or ""],
                }
            )
    if entities:
        return entities
    fallback = load_top_entities_json(doi_folder.name, str(doi_folder.parent))
    return [
        {
            "uri": str(item.get("uri") or ""),
            "label": str(item.get("label") or ""),
            "types": list(item.get("types") or [top.get("class_iri") or ""]),
        }
        for item in fallback
        if str(item.get("uri") or "").strip()
    ]


def _extension_domain(name: str, current: RuntimeDomain | None) -> RuntimeDomain | None:
    if current is not None and current.ontology_name == name:
        return current
    if not name:
        return None
    try:
        return load_runtime_domain(name)
    except (FileNotFoundError, OSError, ValueError):
        return None


def _merge_extension_record(
    raw: dict[str, Any],
    *,
    child: RuntimeDomain | None,
    parent: RuntimeDomain | None,
) -> dict[str, Any]:
    record = dict(raw)
    name = str(record.get("name") or getattr(child, "ontology_name", "") or "").strip()
    record["name"] = name
    if child is not None:
        kg_set, kg_tools = child.mcp_capability("kg_building")
        if not record.get("mcp_list"):
            record["mcp_list"] = kg_tools
        if not record.get("mcp_set_name"):
            record["mcp_set_name"] = kg_set
        if not record.get("enrichment_target"):
            record["enrichment_target"] = dict(child.runtime.get("enrichment_target") or {})
        if not record.get("output"):
            record["output"] = dict(child.runtime.get("output") or {})
        if not record.get("agent_model"):
            record["agent_model"] = str(child.config.models.get("runtime_kg_building") or "")
    upstream = resolve_upstream_ontology(extension=record, domain=child or parent)
    if not upstream and parent is not None and not parent.is_extension:
        upstream = parent.ontology_name
    record["upstream_ontology"] = upstream
    if not record.get("bridge_class_iri"):
        parent_domain = parent
        if (
            parent_domain is None
            or parent_domain.ontology_name != upstream
            or parent_domain.is_extension
        ) and upstream:
            try:
                parent_domain = load_runtime_domain(upstream)
            except (FileNotFoundError, OSError, ValueError):
                parent_domain = None
        if parent_domain is not None:
            for item in parent_domain.extensions:
                if str(item.get("name") or "").strip() != name:
                    continue
                iri = str(item.get("bridge_class_iri") or "").strip()
                if iri:
                    record["bridge_class_iri"] = iri
                    break
    return record


def configured_extensions(config: dict) -> list[dict[str, Any]]:
    domain = runtime_domain_from_config(config)
    if domain is None:
        return []
    if domain.is_extension:
        kg_set, kg_tools = domain.mcp_capability("kg_building")
        return [
            _merge_extension_record(
                {
                    "name": domain.ontology_name,
                    "mcp_list": kg_tools,
                    "mcp_set_name": kg_set,
                    "upstream_ontology": resolve_upstream_ontology(domain=domain),
                    "enrichment_target": dict(domain.runtime.get("enrichment_target") or {}),
                    "output": dict(domain.runtime.get("output") or {}),
                    "agent_model": str(domain.config.models.get("runtime_kg_building") or ""),
                },
                child=domain,
                parent=domain,
            )
        ]
    return [
        _merge_extension_record(
            dict(item),
            child=_extension_domain(str(item.get("name") or "").strip(), domain),
            parent=domain,
        )
        for item in domain.extensions
    ]
