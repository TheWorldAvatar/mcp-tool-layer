"""Build Iteration-1 KG from top-entity hints using generated create_* helpers.

Occurrence-surface MCP servers do not expose create_<TopClass>. The runtime
materializes those individuals through the generated entity module, then exports.
"""

from __future__ import annotations

import importlib
import json
import os
import sys
from pathlib import Path

from rdflib import Graph

from src.extraction_runtime.artifact_root import resolve_generated_file, scripts_dir, sparql_dir
from src.extraction_runtime.kg_binding import pin_entity_context
from src.extraction_runtime.publish.publish import publish_ttl
from src.extraction_runtime.steps.top_entity.identity import load_selected_top_class
from src.extraction_runtime.steps.top_entity.membership import listing_labels


def _load_hints(doi_hash: str, data_dir: str) -> str:
    path = Path(data_dir) / doi_hash / "top_entities.txt"
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8")


def _load_generated_top_creator(ontology_name: str, class_local: str):
    scripts_root = scripts_dir(ontology_name).parent
    root_text = str(scripts_root)
    if root_text not in sys.path:
        sys.path.insert(0, root_text)
    entities = importlib.import_module(f"{ontology_name}.{ontology_name}_creation_entities")
    creator = getattr(entities, f"create_{class_local}", None)
    if creator is None:
        raise FileNotFoundError(
            f"generated {ontology_name}_creation_entities has no create_{class_local}"
        )
    rdf_runtime = importlib.import_module(f"{ontology_name}._fixed_rdf_runtime")
    return creator, rdf_runtime


def _creator_payload(raw: str) -> dict:
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"create_* did not return JSON: {raw!r}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"create_* JSON must be an object: {raw!r}")
    return payload


def parse_top_entities_from_ttl(doi_hash: str, ontology_name: str, data_dir: str) -> bool:
    doi_folder = Path(data_dir) / doi_hash
    class_iri, _ = load_selected_top_class(doi_folder)
    if not class_iri:
        print("[WARN] Cannot parse top entities without top-class selection; continuing")
        return True
    ttl_path = doi_folder / "iteration_1.ttl"
    sparql_path = sparql_dir(ontology_name) / "top_entity_parsing.sparql"
    if not sparql_path.is_file():
        sparql_path = resolve_generated_file(
            f"sparqls/{ontology_name}/top_entity_parsing.sparql"
        )
    if not ttl_path.is_file() or not sparql_path.is_file():
        print(f"[WARN] Missing TTL or SPARQL: {ttl_path} / {sparql_path}; continuing")
        return True
    query = sparql_path.read_text(encoding="utf-8")
    graph = Graph()
    graph.parse(str(ttl_path), format="turtle")
    entities: list[dict[str, str]] = []
    for row in graph.query(query):
        if hasattr(row, "entity"):
            uri = str(row.entity)
        else:
            uri = str(row[0])
        label = (
            str(row.label)
            if hasattr(row, "label") and row.label
            else uri.rsplit("/", 1)[-1]
        )
        entities.append({"uri": uri, "label": label, "types": [class_iri]})
    output = doi_folder / "mcp_run" / "iter1_top_entities.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(entities, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[OK] Wrote {len(entities)} top entities to {output}")
    return bool(entities)


def materialize_top_class_members(
    *,
    doi_hash: str,
    ontology_name: str,
    class_local: str,
    labels: list[str],
) -> Path:
    creator, rdf_runtime = _load_generated_top_creator(ontology_name, class_local)
    pin_entity_context(name="top")
    init_result = _creator_payload(rdf_runtime.init_memory(doi_hash, "top"))
    if str(init_result.get("status") or "").lower() not in {"ok", "success", ""}:
        if init_result.get("ok") is False:
            raise RuntimeError(f"init_memory rejected: {init_result}")
    created: list[str] = []
    for label in labels:
        payload = _creator_payload(creator(label))
        status = str(payload.get("status") or "").lower()
        iri = str(payload.get("iri") or "").strip()
        if status in {"rejected", "error", "failed"} or payload.get("ok") is False or not iri:
            print(f"  [WARN] create_{class_local}({label!r}) failed: {payload}; skipping")
            continue
        created.append(iri)
        print(f"  [OK] create_{class_local} {label} -> {iri}")
    if not created:
        raise RuntimeError(f"create_{class_local} created no members")
    export_result = _creator_payload(rdf_runtime.export_memory(doi_hash, "top"))
    memory_path = Path(str(export_result.get("memory_path") or "")).expanduser()
    if not memory_path.is_file():
        memory_path = (
            Path(os.environ.get("TWA_AGENTIC_DATA_DIR") or "data")
            / doi_hash
            / "memory"
            / "top.ttl"
        )
    if not memory_path.is_file() or memory_path.stat().st_size <= 0:
        raise RuntimeError(f"export_memory wrote no TTL: {export_result}")
    if len(created) != len(labels):
        print(
            f"  [WARN] Top-class materialization partial: "
            f"{len(created)}/{len(labels)} members; continuing"
        )
    return memory_path


def run_step(doi_hash: str, config: dict) -> bool:
    data_dir = config.get("data_dir", "data")
    ontology_name = str(config.get("ontology_name") or "").strip()
    doi_folder = Path(data_dir) / doi_hash
    print(f">> Top Entity KG Building: {doi_hash}")
    iteration_ttl = doi_folder / "iteration_1.ttl"
    if iteration_ttl.is_file() and iteration_ttl.stat().st_size > 0:
        print("[SKIP] iteration_1.ttl already exists; refreshing top-entity JSON")
        return parse_top_entities_from_ttl(doi_hash, ontology_name, data_dir)

    hints = _load_hints(doi_hash, data_dir)
    if not hints.strip():
        print("[WARN] top_entities.txt is missing; continuing")
        return True
    labels = listing_labels(hints)
    if not labels:
        print("[WARN] top_entities.txt has no labels; continuing")
        return True
    class_iri, class_local = load_selected_top_class(doi_folder)
    if not class_local:
        print("[WARN] top-class selection is missing; continuing")
        return True
    try:
        produced = materialize_top_class_members(
            doi_hash=doi_hash,
            ontology_name=ontology_name,
            class_local=class_local,
            labels=labels,
        )
    except Exception as exc:
        print(f"[WARN] Top-entity materialization failed: {exc}; continuing")
        return True
    publish_ttl(produced, iteration_ttl)
    parsed = parse_top_entities_from_ttl(doi_hash, ontology_name, data_dir)
    if not parsed:
        print("[WARN] Generated top-class TTL parsed to no members; continuing")
        return True
    return True
