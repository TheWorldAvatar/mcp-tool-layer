"""
TTL publishing utilities for the pipeline.

Goal: provide a super reliable mechanism to "publish" the latest ontology TTLs
into a deterministic output folder with deterministic filenames.

Key requirements:
- Do NOT hardcode ontology names in execution scripts.
- Output locations/names are driven by config (meta_task_config) with safe defaults.
- Robust to different MCP server persistence conventions (memory/ + exports/ fallbacks).
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from typing import Iterable, Optional
from rdflib import Graph, URIRef
from rdflib.namespace import RDF, RDFS

from src.utils.global_logger import get_logger


logger = get_logger("pipeline", "ttl_publisher")


@dataclass(frozen=True)
class OutputNamingConfig:
    output_dir: str
    top_ttl_name: str
    entity_ttl_pattern: str


def load_meta_task_config(config_path: str = "configs/meta_task/meta_task_config.json") -> dict:
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def get_main_ontology_name(meta_cfg: dict, default: str = "ontosynthesis") -> str:
    try:
        return (meta_cfg.get("ontologies", {}).get("main", {}).get("name") or default).strip() or default
    except Exception:
        return default


def _render(template: str, **kwargs: str) -> str:
    s = template or ""
    try:
        return s.format(**kwargs)
    except Exception:
        # fall back to raw string if formatting fails
        return s


def get_output_naming_config(
    *,
    meta_cfg: dict,
    ontology_name: str,
    default_output_dir: Optional[str] = None,
) -> OutputNamingConfig:
    main_cfg = (meta_cfg or {}).get("ontologies", {}).get("main", {}) or {}

    # Support multiple possible schema keys (backward/forward compatible).
    output_dir_tpl = (
        main_cfg.get("output_dir")
        or (main_cfg.get("output", {}) or {}).get("dir")
        or default_output_dir
        or "{ontology_name}_output"
    )
    top_ttl_name = (
        main_cfg.get("top_ttl_name")
        or (main_cfg.get("output", {}) or {}).get("top_ttl_name")
        or "top.ttl"
    )
    entity_ttl_pattern = (
        main_cfg.get("entity_ttl_pattern")
        or (main_cfg.get("output", {}) or {}).get("entity_ttl_pattern")
        or "{entity_safe}.ttl"
    )

    output_dir = _render(str(output_dir_tpl), ontology_name=ontology_name).strip() or f"{ontology_name}_output"
    top_ttl_name = str(top_ttl_name).strip() or "top.ttl"
    entity_ttl_pattern = str(entity_ttl_pattern).strip() or "{entity_safe}.ttl"

    return OutputNamingConfig(output_dir=output_dir, top_ttl_name=top_ttl_name, entity_ttl_pattern=entity_ttl_pattern)


def _get_runtime_policies(meta_cfg: dict) -> dict:
    return ((meta_cfg or {}).get("ontologies", {}).get("main", {}) or {}).get("runtime_policies", {}) or {}


def _get_main_entity_kg_policy(meta_cfg: dict) -> dict:
    return _get_runtime_policies(meta_cfg).get("main_entity_kg", {}) or {}


def _latest_export(exports_dir: str, prefix: str) -> Optional[str]:
    try:
        if not os.path.isdir(exports_dir):
            return None
        cands = [
            os.path.join(exports_dir, f)
            for f in os.listdir(exports_dir)
            if f.lower().startswith(prefix.lower() + "_") and f.lower().endswith(".ttl")
        ]
        if not cands:
            return None
        cands.sort(key=lambda p: os.path.getmtime(p), reverse=True)
        return cands[0]
    except Exception:
        return None


def _choose_preferred_typed_target(g: Graph, typed_targets: list[URIRef]) -> Optional[URIRef]:
    """
    Pick one deterministic target when multiple typed nodes of the same class exist.

    Prefer the node with the richest local description (most incoming/outgoing triples),
    then fall back to lexical URI ordering for stability.
    """
    if not typed_targets:
        return None

    def _score(node: URIRef) -> tuple[int, str]:
        outgoing = sum(1 for _ in g.triples((node, None, None)))
        incoming = sum(1 for _ in g.triples((None, None, node)))
        return (outgoing + incoming, str(node))

    return sorted(typed_targets, key=_score, reverse=True)[0]


def _first_label(g: Graph, node: URIRef) -> str:
    for value in g.objects(node, RDFS.label):
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _normalize_entity_label_key(text: str) -> str:
    normalized = str(text or "").strip().lower()
    if normalized.endswith(" synthesis"):
        normalized = normalized[: -len(" synthesis")]
    normalized = normalized.replace("·", "").replace("•", "").replace(".", "")
    return "".join(ch for ch in normalized if ch.isalnum())


def _resolve_expected_top_entity(
    *,
    top_g: Graph,
    entity_g: Graph,
    top_class_iri: str,
    entity_uri: str = "",
    entity_label: str = "",
) -> URIRef | None:
    class_iri = str(top_class_iri or "").strip()
    if not class_iri:
        return URIRef(entity_uri) if entity_uri else None

    typed_top_entities = [
        s for s in top_g.subjects(RDF.type, URIRef(class_iri))
        if isinstance(s, URIRef)
    ]
    typed_entity_entities = [
        s for s in entity_g.subjects(RDF.type, URIRef(class_iri))
        if isinstance(s, URIRef)
    ]

    explicit_ref = URIRef(entity_uri) if entity_uri else None
    if explicit_ref in typed_top_entities or explicit_ref in typed_entity_entities:
        return explicit_ref

    label_keys: list[str] = []
    if entity_label:
        label_key = _normalize_entity_label_key(entity_label)
        if label_key:
            label_keys.append(label_key)
    if explicit_ref is not None:
        explicit_label = _first_label(top_g, explicit_ref) or _first_label(entity_g, explicit_ref)
        explicit_label_key = _normalize_entity_label_key(explicit_label)
        if explicit_label_key and explicit_label_key not in label_keys:
            label_keys.append(explicit_label_key)

    for label_key in label_keys:
        matches = [
            node for node in typed_top_entities
            if _normalize_entity_label_key(_first_label(top_g, node)) == label_key
        ]
        if matches:
            return _choose_preferred_typed_target(top_g, matches)
        matches = [
            node for node in typed_entity_entities
            if _normalize_entity_label_key(_first_label(entity_g, node)) == label_key
        ]
        if matches:
            return _choose_preferred_typed_target(entity_g, matches)

    if len(typed_top_entities) == 1:
        return typed_top_entities[0]
    if len(typed_entity_entities) == 1:
        return typed_entity_entities[0]
    return explicit_ref


def _remap_nodes(g: Graph, remap: dict[URIRef, URIRef]) -> Graph:
    if not remap:
        return g

    rewritten = Graph()
    for prefix, ns in g.namespaces():
        rewritten.bind(prefix, ns)

    for s, p, o in g:
        new_s = remap.get(s, s) if isinstance(s, URIRef) else s
        new_o = remap.get(o, o) if isinstance(o, URIRef) else o
        rewritten.add((new_s, p, new_o))

    return rewritten


def _dedupe_typed_nodes_by_label(
    g: Graph,
    *,
    class_iris: list[str],
    messages: list[str],
) -> Graph:
    remap_map: dict[URIRef, URIRef] = {}
    seen_classes: set[str] = set()

    for class_iri in class_iris:
        class_iri = str(class_iri or "").strip()
        if not class_iri or class_iri in seen_classes:
            continue
        seen_classes.add(class_iri)
        class_ref = URIRef(class_iri)
        nodes = [s for s in g.subjects(RDF.type, class_ref) if isinstance(s, URIRef)]
        if len(nodes) < 2:
            continue

        by_label: dict[str, list[URIRef]] = {}
        for node in nodes:
            label = _first_label(g, node)
            if label:
                by_label.setdefault(label, []).append(node)

        for label, labelled_nodes in by_label.items():
            if len(labelled_nodes) < 2:
                continue
            canonical = _choose_preferred_typed_target(g, labelled_nodes)
            if canonical is None:
                continue
            for node in labelled_nodes:
                if node != canonical:
                    remap_map[node] = canonical
            messages.append(
                f"Merged {len(labelled_nodes)} nodes of type {class_iri} with shared label '{label}' into {canonical}"
            )

    if remap_map:
        return _remap_nodes(g, remap_map)
    return g


def _drop_nodes_as_subjects(g: Graph, nodes: list[URIRef]) -> Graph:
    """
    Remove triples whose subject is one of the supplied nodes.

    This is used to keep exactly one top-level shell entity in a published per-entity TTL.
    The top shell may contain multiple synthesis shell nodes for the whole paper; publishing a
    single entity TTL should retain only the current entity's top-level subject.
    """
    if not nodes:
        return g

    rewritten = Graph()
    for prefix, ns in g.namespaces():
        rewritten.bind(prefix, ns)

    node_set = set(nodes)
    for s, p, o in g:
        if s in node_set:
            continue
        rewritten.add((s, p, o))
    return rewritten


def _resolve_top_source(*, doi_folder: str, naming: OutputNamingConfig) -> Optional[str]:
    mem_top = os.path.join(doi_folder, "memory", "top.ttl")
    if os.path.exists(mem_top):
        return mem_top
    exp_top = _latest_export(os.path.join(doi_folder, "exports"), prefix="top")
    if exp_top and os.path.exists(exp_top):
        return exp_top
    out_top = os.path.join(doi_folder, naming.output_dir, naming.top_ttl_name)
    if os.path.exists(out_top):
        return out_top
    return None


def _build_composite_entity_graph(
    *,
    entity_src_path: str | Iterable[str],
    top_src_path: Optional[str],
    shell_policy: dict,
    entity_uri: str = "",
    entity_label: str = "",
) -> tuple[Optional[Graph], list[str]]:
    """
    Build a composite graph from top shell + entity graph and apply config-driven
    structural repair/validation.
    """
    messages: list[str] = []
    top_g = Graph()
    entity_g = Graph()
    try:
        if top_src_path and os.path.exists(top_src_path):
            top_g.parse(top_src_path, format="turtle")
        if isinstance(entity_src_path, str):
            entity_paths = [entity_src_path]
        else:
            entity_paths = [p for p in entity_src_path if p]
        for p in entity_paths:
            if p and os.path.exists(p):
                entity_g.parse(p, format="turtle")
    except Exception as e:
        return None, [f"Failed to parse TTL for composition: {e}"]

    top_class_iri = str(shell_policy.get("top_entity_class_iri") or "").strip()
    require_entity_uri_subject = bool(shell_policy.get("require_entity_uri_subject"))
    required_links = shell_policy.get("required_links") or []
    expected_top_entity = _resolve_expected_top_entity(
        top_g=top_g,
        entity_g=entity_g,
        top_class_iri=top_class_iri,
        entity_uri=entity_uri,
        entity_label=entity_label,
    )

    def _typed_entities(src_g: Graph, class_iri: str) -> list[URIRef]:
        if not class_iri:
            return []
        return [s for s in src_g.subjects(RDF.type, URIRef(class_iri)) if isinstance(s, URIRef)]

    # Build the composite graph from top shell + entity fragments. If both sides already
    # contain a singleton top entity but with different IRIs, remap the entity-side node
    # onto the top-shell IRI so publish-time validation sees one MedicalCase, not two.
    top_only_entities = _typed_entities(top_g, top_class_iri)
    entity_only_entities = _typed_entities(entity_g, top_class_iri)
    g = Graph()
    for prefix, ns in top_g.namespaces():
        g.bind(prefix, ns)
    for prefix, ns in entity_g.namespaces():
        g.bind(prefix, ns)
    for triple in top_g:
        g.add(triple)

    remap_from: URIRef | None = None
    remap_to: URIRef | None = None
    if len(top_only_entities) == 1 and len(entity_only_entities) == 1 and top_only_entities[0] != entity_only_entities[0]:
        if expected_top_entity in {top_only_entities[0], entity_only_entities[0]}:
            remap_to = expected_top_entity
            remap_from = entity_only_entities[0] if remap_to == top_only_entities[0] else top_only_entities[0]
        else:
            remap_from = entity_only_entities[0]
            remap_to = top_only_entities[0]
        messages.append(f"Reused top-shell entity URI {remap_to} instead of duplicate entity URI {remap_from}")

    for s, p, o in entity_g:
        new_s = remap_to if (remap_from is not None and s == remap_from) else s
        new_o = remap_to if (remap_from is not None and o == remap_from) else o
        g.add((new_s, p, new_o))

    if top_class_iri:
        top_entities = _typed_entities(g, top_class_iri)
        if len(top_entities) > 1:
            remap_map: dict[URIRef, URIRef] = {}
            by_label: dict[str, list[URIRef]] = {}
            for node in top_entities:
                label = _first_label(g, node)
                if label:
                    by_label.setdefault(label, []).append(node)

            for label, nodes in by_label.items():
                if len(nodes) < 2:
                    continue
                canonical = expected_top_entity if expected_top_entity in nodes else _choose_preferred_typed_target(g, nodes)
                if canonical is None:
                    continue
                for node in nodes:
                    if node != canonical:
                        remap_map[node] = canonical
                messages.append(
                    f"Merged {len(nodes)} top-level entities with shared label '{label}' into {canonical}"
                )

            if remap_map:
                g = _remap_nodes(g, remap_map)

        # Publishing a single entity TTL should not keep unrelated top-level shell entities
        # from the paper-wide top.ttl. Retain only the expected entity URI when available.
        top_entities = _typed_entities(g, top_class_iri)
        if expected_top_entity and expected_top_entity in top_entities and len(top_entities) > 1:
            drop_nodes = [node for node in top_entities if node != expected_top_entity]
            g = _drop_nodes_as_subjects(g, drop_nodes)
            messages.append(
                f"Pruned {len(drop_nodes)} unrelated top-level shell entities; kept {expected_top_entity}"
            )

    singleton_target_classes = [
        str((spec or {}).get("target_class_iri") or "").strip()
        for spec in required_links
        if str((spec or {}).get("target_class_iri") or "").strip()
    ]
    if singleton_target_classes:
        g = _dedupe_typed_nodes_by_label(
            g,
            class_iris=singleton_target_classes,
            messages=messages,
        )

    top_entities: list[URIRef] = []
    if top_class_iri:
        top_entities = _typed_entities(g, top_class_iri)
        if not top_entities:
            messages.append(f"Missing top-level class instance: {top_class_iri}")

    top_entity = top_entities[0] if len(top_entities) == 1 else None
    if require_entity_uri_subject and len(top_entities) != 1:
        messages.append(f"Expected exactly one top-level entity of type {top_class_iri}, found {len(top_entities)}")

    for spec in required_links:
        pred_iri = str((spec or {}).get("predicate_iri") or "").strip()
        target_class_iri = str((spec or {}).get("target_class_iri") or "").strip()
        min_count = int((spec or {}).get("min_count") or 0)
        repair_mode = str((spec or {}).get("repair_mode") or "").strip()
        if not (top_entity and pred_iri and target_class_iri):
            continue

        pred = URIRef(pred_iri)
        target_cls = URIRef(target_class_iri)
        current_targets = [o for o in g.objects(top_entity, pred) if isinstance(o, URIRef)]
        if len(current_targets) >= min_count:
            continue

        if repair_mode == "attach_singleton_if_missing":
            typed_targets = [s for s in g.subjects(RDF.type, target_cls) if isinstance(s, URIRef)]
            typed_targets = sorted(set(typed_targets), key=str)
            chosen_target = _choose_preferred_typed_target(g, typed_targets)
            if chosen_target is not None:
                g.add((top_entity, pred, chosen_target))
                if len(typed_targets) == 1:
                    messages.append(f"Repaired missing link {pred_iri} by attaching singleton {chosen_target}")
                else:
                    messages.append(
                        f"Repaired missing link {pred_iri} by attaching preferred target {chosen_target} "
                        f"from {len(typed_targets)} candidates"
                    )
                current_targets = [chosen_target]

        if len(current_targets) < min_count:
            messages.append(
                f"Missing required link {pred_iri} from top entity; "
                f"required >= {min_count}, found {len(current_targets)}"
            )

    return g, messages


def _write_graph_to_temp(g: Graph, *, doi_folder: str, suffix: str) -> str:
    os.makedirs(doi_folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=doi_folder, suffix=suffix)
    os.close(fd)
    g.serialize(destination=tmp, format="turtle")
    return tmp


def publish_ttl(
    *,
    doi_hash: str,
    ontology_name: str,
    entity_safe: str,
    entity_uri: str = "",
    entity_label: str = "",
    data_dir: str = "data",
    meta_cfg: Optional[dict] = None,
    src_candidates: Optional[Iterable[str]] = None,
) -> Optional[str]:
    """
    Publish the best-available entity TTL to `data/<hash>/<output_dir>/<entity_filename>`.

    Returns the destination path on success, None on failure.
    """
    meta_cfg = meta_cfg or load_meta_task_config()
    naming = get_output_naming_config(meta_cfg=meta_cfg, ontology_name=ontology_name)
    main_entity_kg_policy = _get_main_entity_kg_policy(meta_cfg)
    publish_policy = main_entity_kg_policy.get("publish", {}) or {}
    shell_policy = main_entity_kg_policy.get("shell_validation", {}) or {}

    doi_folder = os.path.join(data_dir, doi_hash)
    out_dir = os.path.join(doi_folder, naming.output_dir)
    out_name = _render(naming.entity_ttl_pattern, entity_safe=entity_safe, ontology_name=ontology_name).strip()
    out_name = out_name or f"{entity_safe}.ttl"
    dest_path = os.path.join(out_dir, out_name)

    mem_path = os.path.join(doi_folder, "memory", f"{entity_safe}.ttl")
    exports_dir = os.path.join(doi_folder, "exports")
    exp_path = _latest_export(exports_dir=exports_dir, prefix=entity_safe)

    candidates: list[str] = []
    # Prefer canonical MCP persistence locations first (memory/ then latest exports/).
    # `src_candidates` is treated as a fallback, not an override, to avoid publishing
    # stale intermediate snapshots when memory is healthier/newer.
    candidates.extend([mem_path, exp_path or ""])
    if src_candidates:
        candidates.extend([c for c in src_candidates if c])

    src_path = next((p for p in candidates if p and os.path.exists(p)), None)
    if not src_path:
        logger.warning(
            "publish_ttl: no source TTL found for entity_safe=%s (candidates=%s)",
            entity_safe,
            [p for p in candidates if p],
        )
        return None

    try:
        effective_src_path = src_path
        temp_paths: list[str] = []

        if bool(publish_policy.get("merge_top_ttl_into_entity_ttl")):
            top_src_path = _resolve_top_source(doi_folder=doi_folder, naming=naming)
            composite_sources: list[str] | str = src_path
            if bool(publish_policy.get("prefer_composite_intermediate")) and src_candidates:
                existing_sources = [c for c in src_candidates if c and os.path.exists(c)]
                if existing_sources:
                    composite_sources = existing_sources
            composite_graph, messages = _build_composite_entity_graph(
                entity_src_path=composite_sources,
                top_src_path=top_src_path,
                shell_policy=shell_policy,
                entity_uri=entity_uri,
                entity_label=entity_label,
            )
            if composite_graph is None:
                logger.error(
                    "publish_ttl: failed to build composite graph for entity_safe=%s: %s",
                    entity_safe,
                    "; ".join(messages) if messages else "unknown error",
                )
                return None
            if messages:
                logger.warning(
                    "publish_ttl: composite graph has pre-repair issues for entity_safe=%s: %s",
                    entity_safe,
                    "; ".join(messages),
                )
            tmp = _write_graph_to_temp(composite_graph, doi_folder=doi_folder, suffix=".publish_entity.ttl")
            temp_paths.append(tmp)
            effective_src_path = tmp

        os.makedirs(out_dir, exist_ok=True)
        shutil.copy2(effective_src_path, dest_path)
        for tmp in temp_paths:
            try:
                os.remove(tmp)
            except Exception:
                pass
        return dest_path
    except Exception as e:
        logger.exception("publish_ttl: failed to publish entity_safe=%s from %s: %s", entity_safe, src_path, e)
        return None


def publish_top_ttl(
    *,
    doi_hash: str,
    ontology_name: str,
    data_dir: str = "data",
    meta_cfg: Optional[dict] = None,
    src_candidates: Optional[Iterable[str]] = None,
) -> Optional[str]:
    """
    Publish the best-available "top" TTL to `data/<hash>/<output_dir>/<top_ttl_name>`.
    """
    meta_cfg = meta_cfg or load_meta_task_config()
    naming = get_output_naming_config(meta_cfg=meta_cfg, ontology_name=ontology_name)

    doi_folder = os.path.join(data_dir, doi_hash)
    out_dir = os.path.join(doi_folder, naming.output_dir)
    dest_path = os.path.join(out_dir, naming.top_ttl_name)

    mem_path = os.path.join(doi_folder, "memory", "top.ttl")
    exports_dir = os.path.join(doi_folder, "exports")
    exp_path = _latest_export(exports_dir=exports_dir, prefix="top")

    candidates: list[str] = []
    candidates.extend([mem_path, exp_path or ""])
    if src_candidates:
        candidates.extend([c for c in src_candidates if c])

    src_path = next((p for p in candidates if p and os.path.exists(p)), None)
    if not src_path:
        return None

    try:
        os.makedirs(out_dir, exist_ok=True)
        shutil.copy2(src_path, dest_path)
        return dest_path
    except Exception:
        return None

