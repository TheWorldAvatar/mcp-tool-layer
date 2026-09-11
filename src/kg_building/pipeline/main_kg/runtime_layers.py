"""Official no-contract runtime layers still injected after the thin envelope.

Official `mcp_runtime_only` keeps identity manifest, persisted A-Box inventory,
graph lifecycle, and an optional global-procedure brief. It drops thick
construction rules and orphan-check instructions.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from rdflib import Graph, Literal, URIRef
from rdflib.namespace import RDF, RDFS


BRIEF_BEGIN = "---- GLOBAL_PROCEDURE_CONTEXT: BEGIN ----"
BRIEF_END = "---- GLOBAL_PROCEDURE_CONTEXT: END ----"


def extracted_hints_block(hints: str) -> str:
    """Official ExtractedHints wrapper used by no-contract KG binding."""
    return (
        "These are extracted hints for this iteration. Treat them as the primary source for KG building.\n"
        "Do not downgrade an explicit canonical field in these hints into a weaker fallback field.\n\n"
        "ExtractedHints:\n<<<\n"
        f"{str(hints or '').rstrip()}\n"
        ">>>\n"
    )


def _entity_persistence_artifacts(
    *,
    doi_folder: Path,
    entity_safe: str,
    entity_label: str,
) -> list[Path]:
    memory = doi_folder / "memory"
    candidates = [
        memory / f"{entity_safe}.ttl",
        memory / f"{entity_safe.lower()}.ttl",
        memory / f"{entity_label}.ttl",
    ]
    found = [path for path in candidates if path.is_file()]
    exports = doi_folder / "exports"
    if exports.is_dir():
        found.extend(
            path
            for path in sorted(exports.glob(f"{entity_safe}*.ttl"))
            if path.is_file()
        )
    return list(dict.fromkeys(found))


def _load_entity_ref_registry(doi_folder: Path, entity_scope: str) -> dict[str, Any]:
    path = doi_folder / "memory" / f"{entity_scope}.refs.json"
    empty = {
        "schema_version": "pipeline-ref-registry.v1",
        "entity_scope": entity_scope,
        "refs": {},
    }
    if not path.is_file():
        return empty
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return empty
    if not isinstance(payload, dict):
        return empty
    refs = payload.get("refs")
    if not isinstance(refs, dict):
        payload["refs"] = {}
    return payload


def _persisted_abox_entity_inventory(
    paths: list[Path],
    *,
    ref_registry: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    graph = Graph()
    for path in paths:
        try:
            graph.parse(path.as_posix(), format="turtle")
        except Exception:
            continue
    refs_by_iri: dict[str, list[str]] = {}
    for ref, entry in (ref_registry or {}).get("refs", {}).items():
        if not isinstance(entry, dict):
            continue
        iri = str(entry.get("iri") or "").strip()
        if iri:
            refs_by_iri.setdefault(iri, []).append(str(ref))
    inventory: list[dict[str, Any]] = []
    for subject in sorted(
        {node for node in graph.subjects(RDF.type, None) if isinstance(node, URIRef)},
        key=str,
    ):
        type_iris = sorted(
            {
                str(type_iri)
                for type_iri in graph.objects(subject, RDF.type)
                if isinstance(type_iri, URIRef)
            }
        )
        labels = sorted({str(label) for label in graph.objects(subject, RDFS.label)})
        if not type_iris:
            continue
        datatype_values: dict[str, list[str]] = {}
        outgoing_relations: list[dict[str, str]] = []
        incoming_relations: list[dict[str, str]] = []
        for predicate, obj in graph.predicate_objects(subject):
            if predicate in {RDF.type, RDFS.label}:
                continue
            if isinstance(obj, Literal):
                datatype_values.setdefault(str(predicate), []).append(str(obj))
            elif isinstance(obj, URIRef):
                outgoing_relations.append(
                    {"property_iri": str(predicate), "object_iri": str(obj)}
                )
        for source, predicate in graph.subject_predicates(subject):
            if isinstance(source, URIRef):
                incoming_relations.append(
                    {"subject_iri": str(source), "property_iri": str(predicate)}
                )
        entry: dict[str, Any] = {
            "iri": str(subject),
            "types": type_iris,
            "labels": labels,
        }
        refs = sorted(set(refs_by_iri.get(str(subject), [])))
        if refs:
            entry["refs"] = refs
        if datatype_values:
            entry["datatype_values"] = {
                predicate: sorted(set(values))
                for predicate, values in sorted(datatype_values.items())
            }
        if outgoing_relations:
            entry["outgoing_relations"] = sorted(
                outgoing_relations,
                key=lambda item: (item["property_iri"], item["object_iri"]),
            )
        if incoming_relations:
            entry["incoming_relations"] = sorted(
                incoming_relations,
                key=lambda item: (item["property_iri"], item["subject_iri"]),
            )
        inventory.append(entry)
    return inventory


def _load_global_context_brief(cache_path: Path) -> str:
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
        resolution = payload["resolution"]
    except (FileNotFoundError, OSError, json.JSONDecodeError, KeyError, TypeError):
        return ""
    if not isinstance(resolution, dict):
        return ""
    return (
        f"{BRIEF_BEGIN}\n"
        "This complete-source context ledger is authoritative runtime evidence. Before "
        "per-entity work, resolve which entries cover the exact target. In PRE extraction, "
        "copy every applicable source statement into scope_resolution.source_dependencies. "
        "In every extraction iteration, attach applicable inherited context to every "
        "compatible owned occurrence unless narrower explicit evidence overrides it. In "
        "every KG iteration, preserve the ledger and materialize only iteration-owned "
        "facts; inherited properties remain operation-local and must not create standalone "
        "context operations. Never apply an entry outside declared_scope.\n"
        + json.dumps(resolution, ensure_ascii=False, indent=2, sort_keys=True)
        + f"\n{BRIEF_END}"
    )


def _inject_global_context_brief(prompt: str, brief: str) -> str:
    if not brief.strip():
        return prompt
    start = prompt.find(BRIEF_BEGIN)
    end = prompt.find(BRIEF_END)
    if start >= 0 and end >= start:
        prompt = prompt[:start].rstrip() + prompt[end + len(BRIEF_END) :]
    return prompt.rstrip() + "\n\n" + brief.strip() + "\n"


def attach_no_contract_runtime_layers(
    base: str,
    *,
    doi_folder: str | Path | None = None,
    known_top_entities: list[dict[str, Any]] | None = None,
    entity_label: str = "",
    entity_safe: str = "",
) -> str:
    """Append official no-contract layers. Do not add thick construction rules."""
    layered = str(base or "")
    folder = Path(doi_folder) if doi_folder else None
    if folder is not None:
        layered = _inject_global_context_brief(
            layered,
            _load_global_context_brief(folder / "global_procedure_context.json"),
        )
    canonical_top_entities = [
        {
            "label": str(item.get("label") or ""),
            "uri": str(item.get("uri") or ""),
            "types": list(item.get("types") or []),
        }
        for item in (known_top_entities or [])
        if str(item.get("uri") or "").strip()
    ]
    if canonical_top_entities:
        layered += (
            "\n\nGlobal top-entity identity manifest (authoritative across entity fragments):\n"
            + json.dumps(canonical_top_entities, ensure_ascii=False, indent=2)
            + "\n- Reuse these exact URIs for every relationship to another top entity, "
            "including procedure inheritance.\n"
            "- Never create a top-entity instance during main KG iterations. If a referenced "
            "top label is absent from this manifest, report an upstream identity blocker.\n"
        )
    if folder is not None and entity_safe:
        artifacts = _entity_persistence_artifacts(
            doi_folder=folder,
            entity_safe=entity_safe,
            entity_label=entity_label,
        )
        if artifacts:
            inventory = _persisted_abox_entity_inventory(
                artifacts,
                ref_registry=_load_entity_ref_registry(folder, entity_safe),
            )
            if inventory:
                layered += (
                    "\n\nPersisted A-Box entity inventory (authoritative existing identities):\n"
                    + json.dumps(inventory, ensure_ascii=False, indent=2)
                    + "\n"
                    "- Resolve any prior hint ref from its exact `refs` entry before considering "
                    "type/label matching. Reuse these IRIs whenever the source hint refers to that "
                    "exact ref. Do not create a generic, placeholder, or renamed duplicate merely "
                    "because the current iteration omits the prior entity record.\n"
                )
    layered += (
        "\n\nGraph lifecycle instructions:\n"
        "- Lifecycle: open_or_resume. Call `init_memory` for this DOI/entity scope before "
        "mutation. It is idempotent and internally resumes canonical persisted state.\n"
        "- No reset/replace/clear or arbitrary-path loader is part of the public lifecycle.\n"
    )
    return layered
