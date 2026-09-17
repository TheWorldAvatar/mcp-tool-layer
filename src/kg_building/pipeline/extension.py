"""Build extension KG from hints. SPARQL comes from generated/sparqls/<ext>/."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path


from rdflib import Graph, URIRef

from src.extraction_runtime.artifact_root import (
    load_generation_contract,
    resolve_generated_file,
    sparql_dir,
)
from src.extraction_runtime.domain_binding import (
    resolve_enrichment_target,
    runtime_domain_from_config,
)
from src.extraction_runtime.discovery import read_paper_doi
from src.extraction_runtime.names import entity_artifact_name, entity_scope_name
from src.extraction_runtime.publish.publish import publish_ttl
from src.extraction_runtime.steps.extensions.queue import (
    configured_extensions,
    load_extension_queue,
)
from src.extraction_prompt_generation.runtime_support.rdf.paths import (
    safe_filename_component,
)
from src.kg_building.pipeline.main_kg.agent import (
    build_kg_prompt,
    ontology_from_config,
    run_kg_agent,
)
from src.kg_building.pipeline.main_kg.repair import graph_is_parseable
from src.kg_building.revision_lock import assert_kg_revision_locked_off


def _extension_output_dir(doi_folder: Path, extension: dict) -> Path:
    name = str(extension.get("name") or "extension")
    pattern = ((extension.get("output") or {}).get("dir") or f"{name}_output")
    return doi_folder / pattern.replace("{ontology_name}", name)


def _extension_ttl_path(doi_folder: Path, extension: dict, entity_label: str) -> Path:
    name = str(extension.get("name") or "extension")
    pattern = (extension.get("output") or {}).get("entity_ttl_pattern") or (
        f"{name}_extension_{{entity_name}}.ttl"
    )
    safe = entity_artifact_name(entity_label)
    filename = (
        str(pattern)
        .replace("{ontology_name}", name)
        .replace("{entity_name}", safe)
        .replace("{entity_safe}", safe)
        .replace("{entity_slugified}", safe)
    )
    return _extension_output_dir(doi_folder, extension) / filename


def extension_memory_dir(doi_folder: Path, ontology_name: str) -> Path:
    """Return the MCP-scoped memory directory for one extension package."""
    return doi_folder / f"memory_{safe_filename_component(ontology_name)}"


def extension_memory_candidate(
    doi_folder: Path,
    ontology_name: str,
    entity_label: str,
    entity_uri: str = "",
) -> Path | None:
    """Locate the TTL written by this extension MCP for one entity.

    Extension packages persist under ``memory_<ontology>/``. Never fall back to
    the main-ontology ``memory/`` directory or to the latest mtime in a shared
    folder: that copies the wrong entity.
    """
    memory = extension_memory_dir(doi_folder, ontology_name)
    names = [
        f"{entity_artifact_name(entity_label)}.ttl",
        f"{entity_scope_name(entity_label, entity_uri)}.ttl",
    ]
    for name in names:
        path = memory / name
        if path.is_file() and path.stat().st_size > 0:
            return path
    return None


def extension_focus_class_iri(extension: dict) -> str:
    """Prefer the compiled extension focus, then the parent-declared bridge."""
    name = str(extension.get("name") or "").strip()
    if name:
        contract = load_generation_contract(name)
        focus = contract.get("extension_focus") or {}
        iri = str(focus.get("class_iri") or "").strip()
        if iri.startswith(("http://", "https://", "urn:")):
            return iri
    return str(extension.get("bridge_class_iri") or "").strip()


def write_extension_global_state(
    *,
    data_dir: str | Path,
    doi_folder: Path,
    ontology_name: str,
    target_iris: list[str],
    class_iri: str,
) -> None:
    """Write enrichment identities where the generated MCP runtime looks them up."""
    payload = {
        "enrichment_targets": [
            {"target_iri": iri, "class_iri": class_iri}
            for iri in target_iris
            if str(iri).strip() and str(class_iri).strip()
        ]
    }
    text = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    filename = f"{ontology_name}_global_state.json"
    for path in (doi_folder / filename, Path(data_dir) / filename):
        path.write_text(text, encoding="utf-8")


def resolve_enrichment_targets(
    *,
    ontology_name: str,
    entity_uri: str,
    main_ttl_paths: list[Path],
    target_variable: str = "target",
    root_variable: str = "root",
    cardinality: str = "exactly_one",
) -> list[str]:
    query_path = sparql_dir(ontology_name) / "enrichment_target.sparql"
    if not query_path.is_file():
        query_path = resolve_generated_file(
            f"sparqls/{ontology_name}/enrichment_target.sparql"
        )
    if not query_path.is_file():
        raise FileNotFoundError(f"enrichment SPARQL missing: {query_path}")
    query = query_path.read_text(encoding="utf-8")
    graph = Graph()
    for path in main_ttl_paths:
        graph.parse(str(path), format="turtle")
    rows = graph.query(
        query,
        initBindings={root_variable: URIRef(str(entity_uri).strip())},
    )
    target_iris = sorted(
        {
            str(value)
            for row in rows
            if isinstance((value := row.asdict().get(target_variable)), URIRef)
        }
    )
    if cardinality == "exactly_one" and len(target_iris) != 1:
        raise RuntimeError(
            f"{ontology_name} enrichment target must resolve exactly one URI "
            f"for {entity_uri}; resolved {len(target_iris)}"
        )
    if not target_iris:
        raise RuntimeError(
            f"{ontology_name} enrichment target resolved no URI for {entity_uri}"
        )
    return target_iris


async def _run_extension_agent(
    prompt: str,
    extension: dict,
    config: dict,
    *,
    doi_hash: str,
    entity_label: str,
    entity_uri: str,
) -> tuple[str, dict]:
    tools = [str(item) for item in (extension.get("mcp_list") or [])]
    mcp_set = str(extension.get("mcp_set_name") or "").strip()
    if config.get("test_mcp_config"):
        mcp_set = str(config["test_mcp_config"])
    if not mcp_set:
        raise ValueError(
            f"{extension.get('name') or 'extension'} mcp_set_name is missing from domain config"
        )
    data_dir = str(config.get("data_dir") or "")
    dump_dir = (
        Path(data_dir) / doi_hash / "prompts" / "kg_building"
        if data_dir
        else None
    )
    return await run_kg_agent(
        prompt,
        config,
        doi_hash=doi_hash,
        entity_label=entity_label,
        entity_uri=entity_uri,
        mcp_set=mcp_set,
        mcp_tools=tools,
        recursion_limit=100,
        dump_dir=dump_dir,
    )


def run_step(doi_hash: str, config: dict) -> bool:
    assert_kg_revision_locked_off(config)
    data_dir = config.get("data_dir", "data")
    doi_folder = Path(data_dir) / doi_hash
    print(f">> Extensions KG Building: {doi_hash}")
    doi_value = read_paper_doi(doi_folder) or doi_hash
    extensions = configured_extensions(config)
    if not extensions:
        print("[WARN] No extension ontologies configured")
        return True
    had_failures = False
    for extension in extensions:
        name = str(extension.get("name") or "").strip()
        upstream = str(extension.get("upstream_ontology") or "").strip()
        if not upstream:
            print(f"  [WARN] {name} has no upstream_ontology in domain config")
            had_failures = True
            continue
        try:
            queue = load_extension_queue(
                doi_folder=doi_folder,
                extension_name=name,
                main_ontology=upstream,
            )
        except Exception as exc:
            print(f"  [WARN] Queue failed for {name}: {exc}")
            had_failures = True
            continue
        main_ttl = sorted((doi_folder / f"{upstream}_output").glob("*.ttl"))
        enrichment = resolve_enrichment_target(
            extension=extension,
            domain=runtime_domain_from_config(config),
        )
        for entity in queue:
            label = str(entity.get("label") or "")
            uri = str(entity.get("uri") or "")
            dest = _extension_ttl_path(doi_folder, extension, label)
            if dest.is_file() and dest.stat().st_size > 0:
                continue
            try:
                targets = resolve_enrichment_targets(
                    ontology_name=name,
                    entity_uri=uri,
                    main_ttl_paths=main_ttl,
                    target_variable=str(enrichment.get("target_variable") or "target"),
                    root_variable=str(enrichment.get("root_variable") or "root"),
                    cardinality=str(enrichment.get("cardinality") or "exactly_one"),
                )
            except Exception as exc:
                print(f"    [WARN] Enrichment target failed for {label}: {exc}")
                had_failures = True
                continue
            safe = entity_artifact_name(label)
            hint_files = sorted(
                (doi_folder / "mcp_run").glob(f"{name}_iter*_hints_{safe}.txt")
            )
            hints = "\n\n".join(
                path.read_text(encoding="utf-8") for path in hint_files if path.stat().st_size > 0
            )
            if not hints.strip():
                print(f"    [WARN] No extension hints for {label}")
                had_failures = True
                continue
            class_iri = extension_focus_class_iri(extension)
            if not class_iri:
                print(
                    f"    [WARN] {name} has no compiled extension_focus or "
                    "parent-declared bridge_class_iri; skipping this entity"
                )
                had_failures = True
                continue
            write_extension_global_state(
                data_dir=data_dir,
                doi_folder=doi_folder,
                ontology_name=name,
                target_iris=targets,
                class_iri=class_iri,
            )
            prompt = build_kg_prompt(
                hints=hints,
                doi=doi_value,
                entity_label=label,
                entity_uri=uri,
                extra_bindings="Bridge targets: " + ", ".join(targets),
                protocol=str(config.get("experiment_protocol") or "").strip() or None,
                ontology=ontology_from_config(config),
                mcp_tools=[str(item) for item in (extension.get("mcp_list") or [])],
            )
            try:
                reply, metadata = asyncio.run(
                    _run_extension_agent(
                        prompt,
                        extension,
                        config,
                        doi_hash=doi_hash,
                        entity_label=label,
                        entity_uri=uri,
                    )
                )
            except Exception as exc:
                print(f"    [WARN] Extension KG agent failed for {label}: {exc}")
                had_failures = True
                continue
            produced = extension_memory_candidate(doi_folder, name, label, uri)
            if produced is None:
                print(f"    [WARN] No TTL written for {name}/{label}; skipping this entity")
                print(f"      tools: {metadata.get('tool_activity', {}).get('executed_tool_name_set')}")
                print(f"      reply preview: {(reply or '')[:160]}")
                had_failures = True
                continue
            publish_ttl(produced, dest)
            if not graph_is_parseable(dest):
                print(f"    [WARN] Extension TTL is not parseable: {dest}; skipping this entity")
                had_failures = True
                continue
            print(f"    [OK] Published {dest.name}")
    if had_failures:
        print(
            f"[WARN] Extensions KG Building partial for {doi_hash}; "
            "continuing without the done marker"
        )
        return True
    (doi_folder / ".extensions_kg_building_done").write_text("completed\n", encoding="utf-8")
    print(f"[OK] Extensions KG Building completed: {doi_hash}")
    return True
