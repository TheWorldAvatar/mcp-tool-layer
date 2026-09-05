"""
Top Entity KG Building Pipeline Step

This step extracts top-level entities from the stitched markdown and builds
a knowledge graph using an LLM agent with MCP tools.
"""

import os
import sys
import json
import asyncio
import importlib.util
import tempfile
import re
import unicodedata
import hashlib
import base64
import types
from pathlib import Path
from typing import List, Dict
from urllib.parse import urlparse
from filelock import FileLock
from rdflib import Graph, URIRef
from rdflib.namespace import RDF, RDFS

# Add project root to path for imports
project_root = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..")
)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from models.BaseAgent import BaseAgent
from models.ModelConfig import ModelConfig
from src.utils.global_logger import get_logger
from src.pipelines.utils.atomic_replace import replace_with_retry
from src.pipelines.utils.llm_transport_retry import (
    is_llm_transport_error,
    retry_async_on_transport,
)
from src.pipelines.utils.ttl_publisher import publish_top_ttl
from src.pipelines.utils.top_entity_identity import (
    attach_entity_identity_dossiers,
    hydrate_and_validate_top_entity_types,
    load_selected_top_class,
    persist_entity_identity_sidecars,
)
from src.agents.scripts_and_prompts_generation.generation_contracts import (
    build_ontology_publish_contract_from_tbox,
)
from src.agents.scripts_and_prompts_generation.llm_global_context_resolver import (
    inject_global_context_brief,
    load_global_context_brief,
)

logger = get_logger("pipeline", "top_entity_kg_building")


def _first_label(g: Graph, node: URIRef) -> str:
    for value in g.objects(node, RDFS.label):
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _normalize_entity_label_key(text: str) -> str:
    raw = unicodedata.normalize("NFKC", str(text or "")).strip()
    bracket_match = re.match(r"^[A-Za-z][A-Za-z0-9_]*-\d+\s+\[(.+)\]\s*$", raw)
    if bracket_match:
        raw = bracket_match.group(1).strip()
    normalized = raw.lower()
    greek_names = {
        "α": "alpha",
        "β": "beta",
        "γ": "gamma",
        "δ": "delta",
    }
    for symbol, name in greek_names.items():
        normalized = normalized.replace(symbol, name)
    normalized = normalized.replace("·", "").replace("•", "").replace(".", "")
    normalized = re.sub(r"[^a-z0-9]+", "", normalized)
    return normalized


def _choose_preferred_typed_target(
    g: Graph, typed_targets: list[URIRef]
) -> URIRef | None:
    if not typed_targets:
        return None

    def _score(node: URIRef) -> tuple[int, str]:
        outgoing = sum(1 for _ in g.triples((node, None, None)))
        incoming = sum(1 for _ in g.triples((None, None, node)))
        return (outgoing + incoming, str(node))

    return sorted(typed_targets, key=_score, reverse=True)[0]


def _canonicalize_parsed_top_entities(
    *, g: Graph, entities: list[dict]
) -> list[dict]:
    if not isinstance(entities, list) or not entities:
        return entities

    by_label: dict[str, list[dict]] = {}
    for entity in entities:
        if not isinstance(entity, dict):
            continue
        label_key = _normalize_entity_label_key(str(entity.get("label") or ""))
        if label_key:
            by_label.setdefault(label_key, []).append(entity)

    canonical_uris: dict[str, str] = {}
    for label_key, group in by_label.items():
        uri_candidates = []
        for entity in group:
            uri = str(entity.get("uri") or "").strip()
            if uri:
                uri_ref = URIRef(uri)
                if any(g.triples((uri_ref, None, None))):
                    uri_candidates.append(uri_ref)
        chosen = _choose_preferred_typed_target(g, sorted(set(uri_candidates), key=str))
        if chosen is not None:
            canonical_uris[label_key] = str(chosen)

    normalized_entities: list[dict] = []
    seen_uris: set[str] = set()
    for entity in entities:
        if not isinstance(entity, dict):
            continue
        updated = dict(entity)
        label_key = _normalize_entity_label_key(str(entity.get("label") or ""))
        canonical_uri = canonical_uris.get(label_key)
        if canonical_uri:
            updated["uri"] = canonical_uri
        uri = str(updated.get("uri") or "").strip()
        if not uri or uri in seen_uris:
            continue
        seen_uris.add(uri)
        normalized_entities.append(updated)
    return normalized_entities


def _filter_runtime_context_top_entities(
    entities: list[dict], meta_config: dict
) -> list[dict]:
    """Drop shared runtime-context shell nodes when concrete top entities exist."""
    if not isinstance(entities, list) or len(entities) <= 1:
        return entities

    policies = _get_runtime_policies(meta_config)
    iter1 = policies.get("iter1_top_entity_kg", {}) or {}
    context_names = {
        _normalize_entity_label_key(value)
        for value in (
            iter1.get("global_state_entity_name"),
            ((iter1.get("prompt_rules") or {}).get("top_level_entity_name")),
            "top",
        )
        if str(value or "").strip()
    }
    context_names = {name for name in context_names if name}
    if not context_names:
        return entities

    def _is_context_entity(entity: dict) -> bool:
        label_key = _normalize_entity_label_key(str(entity.get("label") or ""))
        uri_key = _normalize_entity_label_key(_local_name(str(entity.get("uri") or "")))
        return label_key in context_names or uri_key in context_names

    concrete_entities = [
        entity
        for entity in entities
        if isinstance(entity, dict) and not _is_context_entity(entity)
    ]
    if not concrete_entities:
        return entities
    removed = len(entities) - len(concrete_entities)
    if removed:
        logger.warning(
            "⚠️  Ignored %s runtime-context shell top-entity node(s) from iter1 TTL",
            removed,
        )
    return concrete_entities


def _local_name(iri: str) -> str:
    text = str(iri or "").strip()
    if "#" in text:
        return text.rsplit("#", 1)[-1]
    return text.rstrip("/").rsplit("/", 1)[-1]


def _get_ontology_publish_contract(meta_config: dict) -> dict:
    """Build semantic pipeline input solely from the configured T-Box."""
    main = ((meta_config.get("ontologies") or {}).get("main") or {})
    ttl_file = str(main.get("ttl_file") or "").strip()
    if not ttl_file:
        return {}
    try:
        return build_ontology_publish_contract_from_tbox(
            ttl_file,
            ontology_name=str(main.get("name") or ""),
            configured_ttl_file=ttl_file,
        )
    except Exception as exc:
        logger.error("❌ Cannot build ontology publish contract: %s", exc)
        return {}


def _get_top_entity_class_iri(meta_config: dict, default: str = "") -> str:
    role = _get_ontology_publish_contract(meta_config).get("top_role") or {}
    if str(role.get("status") or "") != "known":
        return ""
    return str(role.get("class_iri") or default).strip()


def _get_pipeline_selected_top_class(
    doi_folder: str,
) -> tuple[str, str]:
    """Read the authoritative top-class selection persisted by extraction."""
    return load_selected_top_class(doi_folder)


def _requires_pipeline_doi_document_lock(meta_config: dict) -> bool:
    """Enable the DOI lock only when the active contract requires bibo:Document."""
    document_iri = "http://purl.org/ontology/bibo/Document"
    return any(
        str(item.get("target_class_iri") or "").strip() == document_iri
        for item in (
            _get_ontology_publish_contract(meta_config).get("required_links")
            or []
        )
        if isinstance(item, dict)
    )


def _seed_iter1_top_entity_lock(
    *,
    doi_hash: str,
    doi_folder: str,
    top_entities: list[dict],
    top_class_iri: str,
    entity_context_name: str,
    entity_context_aliases: list[str],
    seed_doi_document: bool = False,
) -> list[dict]:
    """Preseed one immutable top URI per extracted scope before agent mutation."""
    canonical: list[dict] = []
    seen_uris: set[str] = set()
    seen_labels: set[str] = set()
    for index, entity in enumerate(top_entities, start=1):
        label = str((entity or {}).get("label") or "").strip()
        uri = str((entity or {}).get("uri") or "").strip()
        label_key = _normalize_entity_label_key(label)
        if not label or not uri or not label_key:
            raise ValueError("Top-entity identity lock requires label and URI")
        if label_key in seen_labels or uri in seen_uris:
            raise ValueError(
                "Top-entity identity lock requires one URI per extracted scope"
            )
        seen_labels.add(label_key)
        seen_uris.add(uri)
        canonical.append(
            {
                "scope_index": index,
                "label": label,
                "uri": uri,
                "types": [top_class_iri],
            }
        )

    _reset_iter1_shared_persistence(
        doi_folder=doi_folder,
        entity_context_names=entity_context_aliases,
    )
    graph = Graph()
    top_class = URIRef(top_class_iri)
    from rdflib import Literal
    document_class = URIRef("http://purl.org/ontology/bibo/Document")
    document_digest = hashlib.sha1(
        str(doi_hash or "").strip().encode("utf-8")
    ).hexdigest()
    document_uri = (
        "https://www.theworldavatar.com/kg/instance/Document/"
        f"{document_digest}"
    )
    document_node = URIRef(document_uri)

    for entity in canonical:
        node = URIRef(entity["uri"])
        graph.add((node, RDF.type, top_class))
        graph.add((node, RDFS.label, Literal(entity["label"])))
    # The source document is pipeline-owned identity: one stable IRI per DOI.
    # Seed it into the shared Iteration-1 graph so create_Document(label=doi)
    # resolves the existing node instead of minting one per top entity.
    if seed_doi_document:
        graph.add((document_node, RDF.type, document_class))
        graph.add((document_node, RDFS.label, Literal(str(doi_hash).strip())))

    safe_context = (
        re.sub(
            r"[^A-Za-z0-9_.-]+", "_", str(entity_context_name or "top")
        ).strip("._")
        or "top"
    )
    memory_dir = Path(doi_folder) / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)
    memory_path = memory_dir / f"{safe_context}.ttl"
    graph.serialize(destination=memory_path, format="turtle")
    if seed_doi_document:
        document_graph = Graph()
        document_graph.add((document_node, RDF.type, document_class))
        document_graph.add(
            (document_node, RDFS.label, Literal(str(doi_hash).strip()))
        )
        document_graph.serialize(
            destination=memory_dir / "document.ttl",
            format="turtle",
        )

    lock_path = Path(doi_folder) / "mcp_run" / "top_entity_identity_lock.json"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": "top-entity-identity-lock.v1",
        "doi": doi_hash,
        "policy": "one_uri_per_extracted_scope",
        "top_class_iri": top_class_iri,
        "shared_memory_context": safe_context,
        "entities": canonical,
    }
    if seed_doi_document:
        payload["document"] = {
            "label": str(doi_hash).strip(),
            "uri": document_uri,
            "types": [str(document_class)],
            "identity_authority": "pipeline_doi_lock",
        }
    fd, temporary = tempfile.mkstemp(
        dir=str(lock_path.parent), prefix=f".{lock_path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(temporary, lock_path)
    finally:
        if os.path.exists(temporary):
            os.remove(temporary)
    logger.info(
        "🔒 Top-entity interceptor locked %d extracted scope(s) in %s",
        len(canonical),
        memory_path,
    )
    return canonical


def _mint_top_entity_iri(label: str, top_class_iri: str = "") -> str:
    identity = "\0".join(
        (
            str(top_class_iri or "").strip(),
            " ".join(str(label or "").casefold().split()),
        )
    )
    digest = hashlib.sha256(identity.encode("utf-8")).digest()[:12]
    token = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return f"https://www.theworldavatar.com/kg/instance/{_local_name(top_class_iri) or 'TopEntity'}/{token}"


def _top_entities_from_txt(doi_folder: str, top_class_iri: str = "") -> list[dict]:
    """Fallback structured top-entity JSON from the simple text extraction output."""
    txt_path = os.path.join(doi_folder, "top_entities.txt")
    if not os.path.exists(txt_path):
        return []
    try:
        with open(txt_path, "r", encoding="utf-8") as f:
            lines = [line.strip(" \t\r\n-•") for line in f if line.strip()]
    except Exception:
        return []

    entities: list[dict] = []
    seen: set[str] = set()
    for source_index, source_anchor in enumerate(lines, start=1):
        label = source_anchor
        bracket_match = re.match(
            r"^[A-Za-z][A-Za-z0-9_]*-(\d+)\s+\[(.+)\]\s*$",
            label,
        )
        if bracket_match:
            scope_index = int(bracket_match.group(1))
            label = bracket_match.group(2)
        else:
            scope_index = source_index
        label = re.sub(r"^\s*[A-Za-z][A-Za-z0-9_]*\s*[—:]\s*", "", label).strip()
        if not label:
            continue
        key = _normalize_entity_label_key(label)
        if not key or key in seen:
            continue
        seen.add(key)
        entities.append(
            {
                "uri": _mint_top_entity_iri(label, top_class_iri),
                "label": label,
                "types": [],
                "scope_index": scope_index,
                "source_anchor": source_anchor,
            }
        )
    return entities


def _merge_txt_top_entity_fallback(
    doi_folder: str,
    entities: list[dict],
    top_class_iri: str = "",
) -> list[dict]:
    """Return one canonical identity per extracted top-entity scope."""
    txt_entities = _top_entities_from_txt(doi_folder, top_class_iri)
    # Once extraction has declared top-entity scopes, parsed graph roots are
    # observations only. Merging arbitrary parsed roots here can turn one extracted
    # scope into both a generated URI and an agent-minted UUID.
    candidates = txt_entities if txt_entities else (entities or [])
    merged: list[dict] = []
    seen_labels: set[str] = set()
    seen_uris: set[str] = set()
    for entity in candidates:
        if not isinstance(entity, dict):
            continue
        label_key = _normalize_entity_label_key(str(entity.get("label") or ""))
        uri = str(entity.get("uri") or "").strip()
        parsed_uri = urlparse(uri)
        if (
            not label_key
            or parsed_uri.scheme not in {"http", "https", "urn"}
            or label_key in seen_labels
            or (uri and uri in seen_uris)
        ):
            continue
        seen_labels.add(label_key)
        if uri:
            seen_uris.add(uri)
        merged.append(entity)
    return merged


def _materialize_supplemented_top_entities(
    g: Graph,
    entities: list[dict],
    top_class_iri: str = "",
) -> bool:
    """Enforce exactly one canonical root for each extracted top-entity scope."""
    changed = False
    top_class = URIRef(top_class_iri) if top_class_iri else None
    expected_nodes = {
        URIRef(str(entity.get("uri") or "").strip())
        for entity in entities or []
        if isinstance(entity, dict) and str(entity.get("uri") or "").strip()
    }
    for entity in entities or []:
        if not isinstance(entity, dict):
            continue
        uri = str(entity.get("uri") or "").strip()
        label = str(entity.get("label") or "").strip()
        if not uri or not label:
            continue
        node = URIRef(uri)
        label_key = _normalize_entity_label_key(label)
        equivalent_nodes: set[URIRef] = set()
        if top_class is not None and label_key:
            for candidate in g.subjects(RDF.type, top_class):
                if candidate == node or not isinstance(candidate, URIRef):
                    continue
                if any(
                    _normalize_entity_label_key(str(existing_label)) == label_key
                    for existing_label in g.objects(candidate, RDFS.label)
                ):
                    equivalent_nodes.add(candidate)
        for equivalent in equivalent_nodes:
            for predicate, obj in list(g.predicate_objects(equivalent)):
                g.add((node, predicate, obj))
            for subject, predicate in list(g.subject_predicates(equivalent)):
                g.add((subject, predicate, node))
            g.remove((equivalent, None, None))
            g.remove((None, None, equivalent))
            changed = True
        if top_class is not None and (node, RDF.type, top_class) not in g:
            g.add((node, RDF.type, top_class))
            changed = True
        if (node, RDFS.label, None) not in g:
            from rdflib import Literal

            g.add((node, RDFS.label, Literal(label)))
            changed = True
    if top_class is not None and expected_nodes:
        for candidate in list(g.subjects(RDF.type, top_class)):
            if not isinstance(candidate, URIRef) or candidate in expected_nodes:
                continue
            g.remove((candidate, None, None))
            g.remove((None, None, candidate))
            changed = True
    return changed


def _load_generated_iter1_modules(
    *, ontology_name: str, project_root: str = "."
) -> dict[str, object]:
    """Load regenerated MCP modules from the active generated-artifact root."""
    roots: list[str] = []
    override = os.environ.get("TWA_GENERATED_ARTIFACT_ROOT", "").strip()
    if override:
        roots.append(override)
    roots.append("ai_generated_contents_candidate")
    roots.append("ai_generated_contents")
    scripts_dir: Path | None = None
    for root in roots:
        candidate = Path(project_root) / root / "scripts" / ontology_name
        if candidate.is_dir():
            scripts_dir = candidate
            break
    if scripts_dir is None:
        raise FileNotFoundError(
            f"Generated scripts directory not found for ontology {ontology_name!r} "
            f"(tried: {[Path(project_root) / r / 'scripts' / ontology_name for r in roots]})"
        )

    package_name = (
        f"_agentic_iter1_{ontology_name}_{abs(hash(str(scripts_dir.resolve())))}"
    )
    for name in list(sys.modules):
        if name == package_name or name.startswith(package_name + "."):
            del sys.modules[name]
    package = types.ModuleType(package_name)
    package.__path__ = [str(scripts_dir.resolve())]  # type: ignore[attr-defined]
    sys.modules[package_name] = package

    modules: dict[str, object] = {}
    for module_stem in (
        "main",
        f"{ontology_name}_creation_base",
        f"{ontology_name}_creation_entities",
        f"{ontology_name}_creation_relationships",
    ):
        module_path = scripts_dir / f"{module_stem}.py"
        spec = importlib.util.spec_from_file_location(
            f"{package_name}.{module_stem}", module_path
        )
        if spec is None or spec.loader is None:
            raise ImportError(f"Could not load generated module spec: {module_path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[f"{package_name}.{module_stem}"] = module
        spec.loader.exec_module(module)
        modules[module_stem.rsplit("_creation_", 1)[-1]] = module
    return modules


def _generated_public_callable(value):
    """Return a generated function or the function wrapped by FastMCP."""
    if callable(value):
        return value
    wrapped = getattr(value, "fn", None)
    return wrapped if callable(wrapped) else None


def _invoke_generated_export(
    export_memory,
    *,
    doi_hash: str,
    entity_context_name: str,
):
    """Invoke either a scoped public export or a legacy wrapper export."""
    import inspect

    parameters = inspect.signature(export_memory).parameters
    if not parameters:
        return export_memory()
    return export_memory(doi_hash, entity_context_name)


def _repair_iter1_ttl_with_generated_tools(
    *,
    doi_hash: str,
    data_dir: str,
    ontology_name: str,
    meta_config: dict,
    entity_context_name: str,
) -> bool:
    """
    Repair a failed ITER1 KG attempt by calling regenerated MCP implementation
    functions directly. The concrete classes/properties come from the ontology
    contract in the meta-task config, not from domain-specific code.
    """
    doi_folder = os.path.join(data_dir, doi_hash)
    top_class_iri, top_class_local = _get_pipeline_selected_top_class(doi_folder)
    if not top_class_iri or not top_class_local:
        logger.error(
            "❌ Cannot repair ITER1 TTL because pipeline top-class selection "
            "lineage is missing or incomplete"
        )
        return False

    top_entities = _top_entities_from_txt(doi_folder, top_class_iri)
    if not top_entities:
        logger.error(
            "❌ Cannot repair ITER1 TTL because top_entities.txt has no usable labels"
        )
        return False
    try:
        top_entities = _seed_iter1_top_entity_lock(
            doi_hash=doi_hash,
            doi_folder=doi_folder,
            top_entities=top_entities,
            top_class_iri=top_class_iri,
            entity_context_name=entity_context_name,
            entity_context_aliases=_get_iter1_entity_context_aliases(
                meta_config, default=entity_context_name
            ),
            seed_doi_document=_requires_pipeline_doi_document_lock(meta_config),
        )
        os.environ["TWA_AGENTIC_DATA_DIR"] = data_dir
        modules = _load_generated_iter1_modules(
            ontology_name=ontology_name, project_root=project_root
        )
        base = modules["base"]
        entities = modules["entities"]
        relationships = modules["relationships"]

        main = modules.get("main")
        init_memory = _generated_public_callable(
            getattr(base, "init_memory_wrapper", None)
        )
        export_memory = _generated_public_callable(
            getattr(base, "export_memory_wrapper", None)
        )
        if init_memory is None and main is not None:
            init_memory = _generated_public_callable(
                getattr(main, "init_memory", None)
            )
        if export_memory is None and main is not None:
            export_memory = _generated_public_callable(
                getattr(main, "export_memory", None)
            )
        if init_memory is None or export_memory is None:
            raise AttributeError(
                "Generated package exposes neither wrapper nor public memory tools"
            )
        try:
            init_memory(doi_hash, entity_context_name)
        except TypeError:
            init_memory()

        ontology_contract = _get_ontology_publish_contract(meta_config)

        for top_entity in top_entities:
            top_label = (
                str((top_entity or {}).get("label") or top_class_local).strip()
                or top_class_local
            )
            top_iri = str((top_entity or {}).get("uri") or "").strip()
            if not top_iri:
                logger.error(
                    "❌ Top-entity identity lock did not provide an IRI for %s",
                    top_label,
                )
                return False

            for spec in ontology_contract.get("required_links") or []:
                predicate_local = _local_name(
                    str((spec or {}).get("predicate_iri") or "")
                )
                target_local = _local_name(
                    str((spec or {}).get("target_class_iri") or "")
                )
                if not predicate_local or not target_local:
                    continue
                create_target = getattr(entities, f"create_{target_local}", None)
                add_link = getattr(relationships, f"add_{predicate_local}", None)
                if create_target is None or add_link is None:
                    logger.warning(
                        "⚠️  Generated repair tools missing for %s -> %s",
                        predicate_local,
                        target_local,
                    )
                    continue
                target_class_iri = str(
                    (spec or {}).get("target_class_iri") or ""
                ).strip()
                target_label = (
                    doi_hash
                    if target_class_iri
                    == "http://purl.org/ontology/bibo/Document"
                    else f"{top_label} {target_local}"
                )
                target_result = json.loads(create_target(target_label))
                target_iri = str(target_result.get("iri") or "").strip()
                if target_iri:
                    add_link(top_iri, target_iri)

        _invoke_generated_export(
            export_memory,
            doi_hash=doi_hash,
            entity_context_name=entity_context_name,
        )
        safe_context = (
            re.sub(r"[^A-Za-z0-9_.-]+", "_", str(entity_context_name or "top")).strip(
                "._"
            )
            or "top"
        )
        memory_ttl = os.path.join(doi_folder, "memory", f"{safe_context}.ttl")
        iteration_ttl = os.path.join(doi_folder, "iteration_1.ttl")
        if os.path.exists(memory_ttl):
            import shutil

            shutil.copy2(memory_ttl, iteration_ttl)
            _ = publish_top_ttl(
                doi_hash=doi_hash,
                ontology_name=ontology_name,
                data_dir=data_dir,
                meta_cfg=meta_config,
                src_candidates=[iteration_ttl, memory_ttl],
            )
        logger.warning(
            "⚠️  Repaired ITER1 TTL by directly invoking regenerated MCP implementation functions"
        )
        return True
    except Exception as exc:
        logger.warning(
            "⚠️  Generated-tool ITER1 repair failed (%s: %s); "
            "keeping the seeded top shell so extraction can proceed",
            type(exc).__name__,
            exc,
        )
        if _promote_existing_iter1_ttl(
            doi_hash=doi_hash,
            data_dir=data_dir,
            ontology_name=ontology_name,
            meta_config=meta_config,
            entity_context_name=entity_context_name,
        ):
            _write_iter1_fail_open_warning(
                os.path.join(data_dir, doi_hash),
                kind="generated_tool_repair_unavailable",
                message=f"{type(exc).__name__}: {exc}",
            )
            return True
        return False


def _write_iter1_fail_open_warning(
    doi_folder: str, *, kind: str, message: str
) -> None:
    path = Path(doi_folder) / "mcp_run" / "iter1_kg_fail_open.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": "iter1-kg-fail-open.v1",
                "kind": kind,
                "message": message,
                "policy": "keep_top_shell_and_continue_extraction",
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def _promote_existing_iter1_ttl(
    *,
    doi_hash: str,
    data_dir: str,
    ontology_name: str,
    meta_config: dict,
    entity_context_name: str,
) -> bool:
    """Copy a seeded memory graph to iteration_1.ttl when MCP export never ran."""
    doi_folder = os.path.join(data_dir, doi_hash)
    iteration_ttl = os.path.join(doi_folder, "iteration_1.ttl")
    if os.path.isfile(iteration_ttl) and os.path.getsize(iteration_ttl) > 0:
        return True
    aliases = _get_iter1_entity_context_aliases(
        meta_config, default=entity_context_name
    )
    import shutil

    for alias in aliases:
        safe = (
            re.sub(r"[^A-Za-z0-9_.-]+", "_", str(alias or "")).strip("._") or "top"
        )
        memory_ttl = os.path.join(doi_folder, "memory", f"{safe}.ttl")
        if not (os.path.isfile(memory_ttl) and os.path.getsize(memory_ttl) > 0):
            continue
        shutil.copy2(memory_ttl, iteration_ttl)
        try:
            publish_top_ttl(
                doi_hash=doi_hash,
                ontology_name=ontology_name,
                data_dir=data_dir,
                meta_cfg=meta_config,
                src_candidates=[iteration_ttl, memory_ttl],
            )
        except Exception:
            pass
        logger.warning(
            "⚠️  Promoted seeded memory/%s.ttl to iteration_1.ttl after ITER1 repair failure",
            safe,
        )
        return True
    return False


def _fail_open_iter1_for_extraction(
    *,
    doi_hash: str,
    data_dir: str,
    ontology_name: str,
    meta_config: dict,
    meta_task_config_path: str,
    entity_context_name: str,
    reason: str,
) -> bool:
    """Write iter1_top_entities.json from the seeded shell or top_entities.txt.

    Poor or empty graphs are acceptable. Aborting before main extraction is not.
    """
    doi_folder = os.path.join(data_dir, doi_hash)
    top_class_iri, _ = _get_pipeline_selected_top_class(doi_folder)
    if not top_class_iri:
        top_class_iri = _get_top_entity_class_iri(meta_config)
    txt_entities = _top_entities_from_txt(doi_folder, top_class_iri)
    if not _promote_existing_iter1_ttl(
        doi_hash=doi_hash,
        data_dir=data_dir,
        ontology_name=ontology_name,
        meta_config=meta_config,
        entity_context_name=entity_context_name,
    ) and txt_entities and top_class_iri:
        try:
            _seed_iter1_top_entity_lock(
                doi_hash=doi_hash,
                doi_folder=doi_folder,
                top_entities=txt_entities,
                top_class_iri=top_class_iri,
                entity_context_name=entity_context_name,
                entity_context_aliases=_get_iter1_entity_context_aliases(
                    meta_config, default=entity_context_name
                ),
                seed_doi_document=_requires_pipeline_doi_document_lock(meta_config),
            )
        except Exception as exc:
            logger.warning(
                "⚠️  ITER1 fail-open could not reseed the identity lock: %s", exc
            )
        _promote_existing_iter1_ttl(
            doi_hash=doi_hash,
            data_dir=data_dir,
            ontology_name=ontology_name,
            meta_config=meta_config,
            entity_context_name=entity_context_name,
        )

    json_path = os.path.join(doi_folder, "mcp_run", "iter1_top_entities.json")
    iteration_ttl = os.path.join(doi_folder, "iteration_1.ttl")
    if os.path.isfile(iteration_ttl):
        parse_ok = bool(
            parse_top_entities_from_ttl(
                doi_hash,
                ontology_name,
                data_dir,
                meta_task_config_path=meta_task_config_path,
            )
        )
        if parse_ok and os.path.isfile(json_path):
            try:
                parsed = json.loads(Path(json_path).read_text(encoding="utf-8"))
            except Exception:
                parsed = []
            if parsed:
                _write_iter1_fail_open_warning(
                    doi_folder,
                    kind="generated_tool_repair_unavailable",
                    message=reason,
                )
                logger.warning(
                    "⚠️  ITER1 MCP repair unavailable; continuing extraction "
                    "from the existing top shell (%s entit%s)",
                    len(parsed),
                    "y" if len(parsed) == 1 else "ies",
                )
                return True

    if not txt_entities or not top_class_iri:
        return False
    entities = []
    for index, raw in enumerate(txt_entities, start=1):
        entity = dict(raw)
        entity["types"] = [top_class_iri]
        entity["scope_index"] = entity.get("scope_index") or index
        entities.append(entity)
    if os.path.isfile(iteration_ttl):
        try:
            entities = hydrate_and_validate_top_entity_types(
                entities=entities,
                iteration_1_ttl=iteration_ttl,
                top_class_iri=top_class_iri,
            )
            entities = attach_entity_identity_dossiers(
                entities=entities,
                iteration_1_ttl=iteration_ttl,
            )
        except Exception as exc:
            logger.warning("⚠️  ITER1 fail-open dossier attach skipped: %s", exc)
    os.makedirs(os.path.dirname(json_path), exist_ok=True)
    with open(json_path, "w", encoding="utf-8") as handle:
        json.dump(entities, handle, indent=2)
    try:
        persist_entity_identity_sidecars(
            doi_hash=doi_hash,
            doi_folder=doi_folder,
            entities=entities,
            top_class_iri=top_class_iri,
        )
    except Exception as exc:
        logger.warning("⚠️  ITER1 fail-open sidecar persist skipped: %s", exc)
    _write_iter1_fail_open_warning(
        doi_folder,
        kind="generated_tool_repair_unavailable",
        message=reason,
    )
    logger.warning(
        "⚠️  ITER1 MCP repair unavailable; synthesized iter1_top_entities.json "
        "from top_entities.txt (%s entit%s)",
        len(entities),
        "y" if len(entities) == 1 else "ies",
    )
    return True


def _reset_iter1_shared_persistence(
    *,
    doi_folder: str,
    entity_context_names: list[str],
) -> list[str]:
    """Clear only the shared ITER1 graph before canonical one-per-scope replay."""
    aliases = {
        re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "")).strip("._")
        for value in entity_context_names
        if str(value or "").strip()
    }
    removed: list[str] = []
    memory_dir = os.path.join(doi_folder, "memory")
    exports_dir = os.path.join(doi_folder, "exports")
    for name in ("output.ttl", "output_top.ttl"):
        path = os.path.join(doi_folder, name)
        if os.path.isfile(path):
            os.remove(path)
            removed.append(path)
    for alias in aliases:
        memory_path = os.path.join(memory_dir, f"{alias}.ttl")
        if os.path.isfile(memory_path):
            os.remove(memory_path)
            removed.append(memory_path)
        if os.path.isdir(exports_dir):
            for name in os.listdir(exports_dir):
                if name.lower().startswith(f"{alias.lower()}_") and name.lower().endswith(
                    ".ttl"
                ):
                    path = os.path.join(exports_dir, name)
                    os.remove(path)
                    removed.append(path)
    if removed:
        logger.info(
            "🔒 Top-entity interceptor cleared %d shared ITER1 persistence "
            "artifact(s) before canonical replay",
            len(removed),
        )
    return removed


def _iter1_needs_generated_top_uri_repair(
    *,
    doi_hash: str,
    data_dir: str,
    ontology_name: str,
    meta_config: dict,
) -> bool:
    """Detect multi-top shells whose subjects do not match generated top-tool IRIs."""
    doi_folder = os.path.join(data_dir, doi_hash)
    selected_iri, _ = _get_pipeline_selected_top_class(doi_folder)
    top_class_iri = selected_iri or _get_top_entity_class_iri(meta_config)
    lock_entities: list[dict] = []
    lock_path = Path(doi_folder) / "mcp_run" / "top_entity_identity_lock.json"
    try:
        lock_payload = json.loads(lock_path.read_text(encoding="utf-8"))
        lock_entities = [
            entity
            for entity in (lock_payload.get("entities") or [])
            if isinstance(entity, dict) and str(entity.get("uri") or "").strip()
        ]
    except (OSError, json.JSONDecodeError, TypeError):
        lock_entities = []
    top_entities = lock_entities or _top_entities_from_txt(doi_folder, top_class_iri)
    if not top_entities:
        return False

    ttl_path = os.path.join(doi_folder, "iteration_1.ttl")
    if not os.path.exists(ttl_path):
        return False

    try:
        g = Graph()
        g.parse(ttl_path, format="turtle")
    except Exception as exc:
        logger.warning("⚠️  Could not validate generated top IRIs for ITER1: %s", exc)
        return False

    top_class = URIRef(top_class_iri) if top_class_iri else None
    expected_nodes: set[URIRef] = set()
    for entity in top_entities:
        label = str((entity or {}).get("label") or "").strip()
        if not label:
            continue
        locked_uri = str((entity or {}).get("uri") or "").strip()
        node = URIRef(locked_uri or _mint_top_entity_iri(label, top_class_iri))
        expected_nodes.add(node)
        has_type = top_class is None or (node, RDF.type, top_class) in g
        has_label = _normalize_entity_label_key(
            _first_label(g, node)
        ) == _normalize_entity_label_key(label)
        if not (has_type and has_label):
            return True
    actual_nodes = (
        {
            node
            for node in g.subjects(RDF.type, top_class)
            if isinstance(node, URIRef)
        }
        if top_class is not None
        else expected_nodes
    )
    return actual_nodes != expected_nodes


def resolve_generated_file(path: str) -> str:
    """
    Resolve a generated artifact path.

    Prefer `ai_generated_contents_candidate/` (where generation writes in this repo),
    then fall back to `ai_generated_contents/` if present.
    """
    path = (path or "").replace("\\", "/")
    candidates: list[str] = []
    override_root = (
        os.environ.get("TWA_GENERATED_ARTIFACT_ROOT", "")
        .strip()
        .replace("\\", "/")
        .rstrip("/")
    )
    strict_root = os.environ.get("TWA_REQUIRE_GENERATED_ARTIFACT_ROOT") == "1"
    if path.startswith("ai_generated_contents/"):
        if override_root:
            candidates.append(path.replace("ai_generated_contents", override_root, 1))
        if not strict_root:
            candidates.append(
                path.replace(
                    "ai_generated_contents/", "ai_generated_contents_candidate/", 1
                )
            )
            candidates.append(path)
    elif path.startswith("ai_generated_contents_candidate/"):
        if override_root:
            candidates.append(
                path.replace("ai_generated_contents_candidate", override_root, 1)
            )
        if not strict_root:
            candidates.append(path)
            candidates.append(
                path.replace(
                    "ai_generated_contents_candidate/", "ai_generated_contents/", 1
                )
            )
    else:
        candidates.append(path)

    for p in candidates:
        if p and os.path.exists(p):
            return p
    if strict_root:
        raise FileNotFoundError(f"Required generated artifact is missing: {candidates[0]}")
    return candidates[0]


# -------------------- Global state writer --------------------
GLOBAL_STATE_DIR = "data"
GLOBAL_STATE_JSON = os.path.join(GLOBAL_STATE_DIR, "global_state.json")
GLOBAL_STATE_LOCK = os.path.join(GLOBAL_STATE_DIR, "global_state.lock")


def _get_runtime_policies(meta_config: dict) -> dict:
    """Return runtime policy block from meta config."""
    return (meta_config or {}).get("ontologies", {}).get("main", {}).get(
        "runtime_policies", {}
    ) or {}


def _get_iter1_entity_context_name(meta_config: dict, default: str = "top") -> str:
    """Resolve the configured iter1 entity context name."""
    policies = _get_runtime_policies(meta_config)
    value = (policies.get("iter1_top_entity_kg", {}) or {}).get(
        "global_state_entity_name"
    ) or default
    return str(value).strip() or default


def _get_iter1_entity_context_aliases(
    meta_config: dict, default: str = "top"
) -> list[str]:
    """
    Return acceptable ITER1 persistence context names.

    Some older/generated ITER1 prompts initialized memory with the concrete top-level
    class name instead of the configured shared context name. During fallback recovery,
    accept both names so existing runs can still be recovered deterministically.
    """
    primary = _get_iter1_entity_context_name(meta_config, default=default)
    policies = _get_runtime_policies(meta_config)
    iter1_cfg = policies.get("iter1_top_entity_kg", {}) or {}
    prompt_rules = iter1_cfg.get("prompt_rules", {}) or {}

    top_level_entity_name = str(prompt_rules.get("top_level_entity_name") or "").strip()
    aliases: list[str] = []
    for name in (primary, top_level_entity_name, "top"):
        clean = str(name or "").strip()
        if clean and clean not in aliases:
            aliases.append(clean)
    return aliases


def _apply_identifier_runtime_env(meta_config: dict) -> None:
    """
    Export config-derived identifier handling rules to environment variables so
    generated MCP utility modules can normalize doi/hash arguments consistently.
    """
    policies = _get_runtime_policies(meta_config)
    identifier = policies.get("identifier_handling", {}) or {}

    def _set_or_unset(key: str, value: str | None) -> None:
        if value is None or str(value).strip() == "":
            os.environ.pop(key, None)
        else:
            os.environ[key] = str(value)

    prefer = identifier.get("prefer_global_state_for_nonhash")
    _set_or_unset(
        "TWA_MCP_IDENTIFIER_PREFER_GLOBAL_STATE_FOR_NONHASH",
        "1" if bool(prefer) else None,
    )
    _set_or_unset(
        "TWA_MCP_IDENTIFIER_PRESERVE_HASH_REGEX",
        identifier.get("preserve_hash_regex"),
    )
    patterns = identifier.get("fallback_to_global_state_patterns")
    _set_or_unset(
        "TWA_MCP_IDENTIFIER_FALLBACK_PATTERNS_JSON",
        json.dumps(patterns, ensure_ascii=False)
        if isinstance(patterns, list)
        else None,
    )


def _augment_iter1_prompt_with_runtime_rules(
    prompt_template: str, doi_hash: str, meta_config: dict
) -> str:
    """
    Append config-derived runtime rules to the ITER1 prompt.
    This keeps the policy outside code while still making the agent behavior explicit.
    """
    policies = _get_runtime_policies(meta_config)
    iter1 = policies.get("iter1_top_entity_kg", {}) or {}
    prompt_rules = iter1.get("prompt_rules", {}) or {}
    lines: list[str] = []

    doi_source = str(prompt_rules.get("doi_argument_source") or "").strip()
    top_name = str(prompt_rules.get("top_level_entity_name") or "").strip()
    memory_context_name = (
        str(iter1.get("global_state_entity_name") or "top").strip() or "top"
    )
    forbid_label_as_doi = bool(prompt_rules.get("forbid_human_readable_label_as_doi"))

    if doi_source or top_name or forbid_label_as_doi:
        lines.append("Config-derived runtime rules:")
        if doi_source:
            lines.append(
                f"- When calling `init_memory`, pass the current document identifier value `{doi_hash}` as the `doi` argument."
            )
        if memory_context_name:
            lines.append(
                f"- When calling `init_memory`, set `top_level_entity_name` to `{memory_context_name}`."
            )
        top_class_local = _local_name(_get_top_entity_class_iri(meta_config))
        if top_class_local:
            lines.append(
                f"- `{memory_context_name}` is only the shared memory/runtime context label; never pass it as the `{top_class_local}` entity label."
            )
        elif top_name:
            lines.append(
                f"- `{top_name}` is only a runtime context label unless it appears as source-supported entity text."
            )
        if forbid_label_as_doi:
            lines.append(
                "- Never pass a human-readable case label, title, or extracted description as the `doi` argument."
            )
            lines.append(
                "- Use human-readable text only for entity labels or descriptive fields, never for document identifiers."
            )

    if not lines:
        return prompt_template
    return prompt_template.rstrip() + "\n\n" + "\n".join(lines) + "\n"


def bind_iter1_runtime_context(
    prompt_template: str,
    *,
    doi_hash: str,
    paper_content: str,
    top_entities: str,
) -> str:
    """Bind Iter1 bootstrap inputs and enforce label-to-root identity boundaries."""
    declared_doi = "{doi}" in prompt_template or "{hash}" in prompt_template
    declared_paper = "{paper_content}" in prompt_template or "{context}" in prompt_template
    declared_entities = (
        "{top_entities}" in prompt_template or "{hints}" in prompt_template
    )
    prompt = prompt_template.replace("{doi}", doi_hash).replace("{hash}", doi_hash)
    prompt = prompt.replace("{paper_content}", paper_content)
    prompt = prompt.replace("{context}", paper_content)
    prompt = prompt.replace("{top_entities}", top_entities)
    prompt = prompt.replace("{hints}", top_entities)

    boundary = [
        "---- PIPELINE-INJECTED ITER1 BOOTSTRAP CONTEXT: BEGIN ----",
        "Bootstrap rules:",
        "- Treat each normalized, deduplicated upstream top-entity label as a distinct root request.",
        "- Create or reuse one root per label using the exact T-Box-derived creator.",
        "- Never invent one shared entity IRI for multiple labels; obtain identity from the creator/runtime.",
        "- Do not create downstream entities or relationships in Iteration 1.",
    ]
    if not declared_doi:
        boundary.append(f"Document DOI/hash: {doi_hash}")
    if not declared_entities:
        boundary.extend(["Upstream top-entity labels:", top_entities])
    if not declared_paper:
        boundary.extend(["Paper/source text:", paper_content])
    boundary.append("---- PIPELINE-INJECTED ITER1 BOOTSTRAP CONTEXT: END ----")
    return prompt.rstrip() + "\n\n" + "\n".join(boundary) + "\n"


def write_global_state(
    doi: str, top_level_entity_name: str, top_level_entity_iri: str | None = None
):
    """Write global state atomically with file lock for MCP server to read."""
    os.makedirs(GLOBAL_STATE_DIR, exist_ok=True)
    lock = FileLock(GLOBAL_STATE_LOCK)
    lock.acquire(timeout=30.0)
    try:
        state = {"doi": doi, "top_level_entity_name": top_level_entity_name}
        if top_level_entity_iri:
            state["top_level_entity_iri"] = top_level_entity_iri
        fd, tmp = tempfile.mkstemp(dir=GLOBAL_STATE_DIR, suffix=".json.tmp")
        os.close(fd)
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2)
            replace_with_retry(tmp, GLOBAL_STATE_JSON)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
        logger.info(f"Global state written: doi={doi}, entity={top_level_entity_name}")
    finally:
        lock.release()


def load_meta_config(
    config_path: str = "configs/meta_task/meta_task_config.json",
) -> dict:
    """Load the meta task configuration."""
    if not os.path.exists(config_path):
        logger.error(f"Meta config not found: {config_path}")
        return {}

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Failed to load meta config: {e}")
        return {}


def load_extraction_prompt(prompt_path: str) -> str:
    """Load the extraction prompt from a markdown file."""
    if not os.path.exists(prompt_path):
        logger.error(f"Prompt file not found: {prompt_path}")
        return ""

    try:
        with open(prompt_path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        logger.error(f"Failed to load prompt: {e}")
        return ""


def load_extraction_hints(doi_hash: str, data_dir: str = "data") -> str:
    """Load the extraction hints from the top_entity_extraction step."""
    hints_path = os.path.join(data_dir, doi_hash, "top_entities.txt")

    if not os.path.exists(hints_path):
        logger.error(f"Extraction hints not found: {hints_path}")
        return ""

    try:
        with open(hints_path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        logger.error(f"Failed to load extraction hints: {e}")
        return ""


def load_paper_content(doi_hash: str, data_dir: str = "data") -> str:
    """
    Load the best-available paper content for KG building.

    Prefer stitched markdown, then text-only markdown, then raw markdown. Append
    supporting-information markdown when present because top-level synthesis
    procedures may only be named in the SI experimental section.
    """
    doi_dir = os.path.join(data_dir, doi_hash)
    stitched = os.path.join(doi_dir, f"{doi_hash}_stitched.md")
    text_md = os.path.join(doi_dir, f"{doi_hash}_text.md")
    raw_md = os.path.join(doi_dir, f"{doi_hash}.md")

    main_text = ""
    for p in (stitched, text_md, raw_md):
        if not os.path.exists(p):
            continue
        try:
            txt = Path(p).read_text(encoding="utf-8")
        except Exception as e:
            logger.warning(f"Failed to read {p}: {e}")
            continue
        if txt and txt.strip():
            main_text = txt
            break
    if not main_text:
        return ""

    parts = [main_text]
    for si_name in (
        f"{doi_hash}_si_text.md",
        f"{doi_hash}_si_vision.md",
        f"{doi_hash}_si.md",
        f"{doi_hash}_si_tables.md",
    ):
        si_path = os.path.join(doi_dir, si_name)
        if not os.path.exists(si_path):
            continue
        try:
            si_txt = Path(si_path).read_text(encoding="utf-8")
        except Exception as e:
            logger.warning(f"Failed to read {si_path}: {e}")
            continue
        if si_txt and si_txt.strip():
            parts.append(f"\n\n# Supporting Information: {si_name}\n\n{si_txt}")
    return "".join(parts)


async def run_kg_building_agent(
    doi_hash: str,
    prompt_template: str,
    hints: str,
    paper_content: str,
    mcp_tools: List[str],
    mcp_set_name: str,
    model_name: str = "gpt-4o",
    temperature: float = 0.0,
    top_p: float = 0.01,
    entity_context_name: str = "top",
    excluded_tool_names: List[str] | None = None,
    data_dir: str = "data",
) -> tuple[str, dict]:
    """
    Run the KG building agent with the given configuration.

    Args:
        doi_hash: DOI hash identifier
        prompt_template: Prompt template for the agent
        hints: Extracted hints from previous step
        mcp_tools: List of MCP tool names to use
        mcp_set_name: Name of the MCP set configuration file
        model_name: LLM model name
        temperature: Model temperature
        top_p: Model top_p parameter

    Returns:
        Tuple of (response, metadata)
    """
    instruction = bind_iter1_runtime_context(
        prompt_template,
        doi_hash=doi_hash,
        paper_content=paper_content or "",
        top_entities=hints or "",
    )
    instruction = inject_global_context_brief(
        instruction,
        load_global_context_brief(
            Path(data_dir) / doi_hash / "global_procedure_context.json"
        ),
    )

    # Write global state for MCP server using the configured iter1 entity context name.
    logger.info(f"📝 Writing global state for MCP server")
    write_global_state(doi_hash, entity_context_name)

    # Create agent with MCP tools
    agent = BaseAgent(
        model_name=model_name,
        model_config=ModelConfig(temperature=temperature, top_p=top_p),
        remote_model=True,
        mcp_tools=mcp_tools,
        mcp_set_name=mcp_set_name,
        excluded_tool_names=excluded_tool_names or [],
    )

    logger.info(f"🚀 Running KG building agent for {doi_hash}")
    logger.info(f"   Model: {model_name}, MCP: {mcp_set_name}, Tools: {mcp_tools}")

    # Retry mechanism for agent execution
    max_retries = 3
    retry_delays = [5, 10, 15]  # Progressive backoff in seconds

    for attempt in range(max_retries):
        try:
            if attempt > 0:
                logger.info(f"🔄 Retry attempt {attempt + 1}/{max_retries}")

            response, metadata = await retry_async_on_transport(
                lambda: agent.run(
                    instruction,
                    recursion_limit=600,
                    required_final_tool="export_memory",
                    required_final_tool_args={
                        "doi": doi_hash,
                        "top_level_entity_name": entity_context_name,
                    },
                ),
                logger=logger,
                what=f"top-entity KG '{doi_hash}'",
            )
            logger.info(f"✅ Agent completed successfully on attempt {attempt + 1}")
            return response, metadata

        except Exception as e:
            if is_llm_transport_error(e):
                raise
            import traceback

            logger.error(
                f"❌ Agent execution failed on attempt {attempt + 1}/{max_retries}: {e}"
            )
            logger.error(traceback.format_exc())

            if attempt < max_retries - 1:
                delay = retry_delays[attempt]
                logger.info(f"⏳ Waiting {delay}s before retry...")
                await asyncio.sleep(delay)
            else:
                logger.error(
                    f"❌ All {max_retries} attempts failed for KG building agent"
                )
                raise


def save_agent_response(doi_hash: str, response: str, data_dir: str = "data") -> None:
    """Save the agent response to a file."""
    output_dir = os.path.join(data_dir, doi_hash, "kg_building")
    os.makedirs(output_dir, exist_ok=True)

    response_path = os.path.join(output_dir, "iter1_response.md")

    try:
        with open(response_path, "w", encoding="utf-8") as f:
            f.write(f"# Iteration 1 - Top Entity KG Building\n\n")
            f.write(f"## Response\n\n{response}")
        logger.info(f"✅ Saved agent response to {response_path}")
    except Exception as e:
        logger.error(f"Failed to save agent response: {e}")


def save_full_prompt(doi_hash: str, prompt: str, data_dir: str = "data") -> None:
    """Save the full prompt for reproducibility/debugging."""
    output_dir = os.path.join(data_dir, doi_hash, "kg_building")
    os.makedirs(output_dir, exist_ok=True)
    prompt_path = os.path.join(output_dir, "iter1_full_prompt.md")
    try:
        Path(prompt_path).write_text(prompt or "", encoding="utf-8")
    except Exception as e:
        logger.warning(f"Failed to save full prompt: {e}")


def copy_output_ttl(
    doi_hash: str,
    data_dir: str = "data",
    test_mode: bool = False,
    ontology_name: str = "ontosynthesis",
    meta_task_config_path: str = "configs/meta_task/meta_task_config.json",
) -> bool:
    """
    Copy the output.ttl to iteration_1.ttl.

    In normal mode:
        - Looks for: output.ttl or output_top.ttl in doi_hash root

    In test mode:
        - Looks for: top.ttl in `{ontology_name}_output/` directory
    """
    meta_cfg = load_meta_config(meta_task_config_path)
    iter1_entity_name = _get_iter1_entity_context_name(meta_cfg, default="top")
    iter1_entity_aliases = _get_iter1_entity_context_aliases(meta_cfg, default="top")
    doi_folder = os.path.join(data_dir, doi_hash)
    iteration_1_ttl = os.path.join(doi_folder, "iteration_1.ttl")

    if test_mode:
        # Test mode: Look for top.ttl in `{ontology_name}_output/`
        test_output_dir = os.path.join(doi_folder, f"{ontology_name}_output")
        test_candidates = [
            os.path.join(test_output_dir, "top.ttl"),
            os.path.join(test_output_dir, "Top.ttl"),
        ]

        for candidate in test_candidates:
            if os.path.exists(candidate):
                try:
                    import shutil

                    shutil.copy2(candidate, iteration_1_ttl)
                    logger.info(
                        f"✅ [TEST MODE] Saved iteration_1.ttl from {os.path.basename(candidate)}"
                    )
                    _ = publish_top_ttl(
                        doi_hash=doi_hash,
                        ontology_name=ontology_name,
                        data_dir=data_dir,
                        meta_cfg=meta_cfg,
                        src_candidates=[iteration_1_ttl, candidate],
                    )
                    return True
                except Exception as e:
                    logger.error(f"Failed to copy {candidate}: {e}")

        # Fallbacks: candidate-first MCP servers in this repo often persist the working graph under
        # data/<hash>/memory/<entity_context>.ttl and/or export snapshots under data/<hash>/exports/<entity_context>_*.ttl.
        exports_dir = os.path.join(doi_folder, "exports")
        for alias in iter1_entity_aliases:
            memory_top_ttl = os.path.join(doi_folder, "memory", f"{alias}.ttl")
            if not os.path.exists(memory_top_ttl):
                continue
            try:
                import shutil

                shutil.copy2(memory_top_ttl, iteration_1_ttl)
                logger.info(
                    f"✅ [TEST MODE] Saved iteration_1.ttl from memory/{alias}.ttl"
                )
                _ = publish_top_ttl(
                    doi_hash=doi_hash,
                    ontology_name=ontology_name,
                    data_dir=data_dir,
                    meta_cfg=meta_cfg,
                    src_candidates=[iteration_1_ttl, memory_top_ttl],
                )
                return True
            except Exception as e:
                logger.error(f"Failed to copy memory/{alias}.ttl: {e}")
                return False

        try:
            if os.path.isdir(exports_dir):
                export_candidates = []
                for alias in iter1_entity_aliases:
                    export_candidates.extend(
                        os.path.join(exports_dir, f)
                        for f in os.listdir(exports_dir)
                        if f.lower().startswith(f"{alias.lower()}_")
                        and f.lower().endswith(".ttl")
                    )
                if export_candidates:
                    export_candidates = sorted(set(export_candidates))
                    export_candidates.sort(
                        key=lambda p: os.path.getmtime(p), reverse=True
                    )
                    latest = export_candidates[0]
                    import shutil

                    shutil.copy2(latest, iteration_1_ttl)
                    logger.info(
                        f"✅ [TEST MODE] Saved iteration_1.ttl from latest export: {os.path.basename(latest)}"
                    )
                    _ = publish_top_ttl(
                        doi_hash=doi_hash,
                        ontology_name=ontology_name,
                        data_dir=data_dir,
                        meta_cfg=meta_cfg,
                        src_candidates=[iteration_1_ttl, latest],
                    )
                    return True
        except Exception as e:
            logger.warning(f"⚠️  [TEST MODE] Failed scanning exports fallback: {e}")

        logger.warning(
            f"⚠️  [TEST MODE] No top.ttl found in {test_output_dir} and no configured memory/export fallback found"
        )
        return False
    else:
        # Normal mode: Look for output.ttl or output_top.ttl
        output_ttl = os.path.join(doi_folder, "output.ttl")
        output_top_ttl = os.path.join(doi_folder, "output_top.ttl")
        # Candidate-first MCP servers in this repo persist the working graph under memory/
        # and (optionally) export snapshots under exports/. They DO NOT necessarily write
        # output.ttl/output_top.ttl into the DOI folder root.
        exports_dir = os.path.join(doi_folder, "exports")

        if os.path.exists(output_ttl):
            try:
                import shutil

                shutil.copy2(output_ttl, iteration_1_ttl)
                logger.info(f"✅ Saved iteration_1.ttl from output.ttl")
                _ = publish_top_ttl(
                    doi_hash=doi_hash,
                    ontology_name=ontology_name,
                    data_dir=data_dir,
                    meta_cfg=meta_cfg,
                    src_candidates=[iteration_1_ttl, output_ttl],
                )
                return True
            except Exception as e:
                logger.error(f"Failed to copy output.ttl: {e}")
                return False
        elif os.path.exists(output_top_ttl):
            try:
                import shutil

                shutil.copy2(output_top_ttl, iteration_1_ttl)
                logger.info(f"✅ Saved iteration_1.ttl from output_top.ttl")
                _ = publish_top_ttl(
                    doi_hash=doi_hash,
                    ontology_name=ontology_name,
                    data_dir=data_dir,
                    meta_cfg=meta_cfg,
                    src_candidates=[iteration_1_ttl, output_top_ttl],
                )
                return True
            except Exception as e:
                logger.error(f"Failed to copy output_top.ttl: {e}")
                return False
        else:
            for alias in iter1_entity_aliases:
                memory_top_ttl = os.path.join(doi_folder, "memory", f"{alias}.ttl")
                if not os.path.exists(memory_top_ttl):
                    continue
                # Fallback: use persisted memory graph with the configured iter1 entity
                # context name or a compatible legacy class-name alias.
                try:
                    import shutil

                    shutil.copy2(memory_top_ttl, iteration_1_ttl)
                    logger.info(f"✅ Saved iteration_1.ttl from memory/{alias}.ttl")
                    _ = publish_top_ttl(
                        doi_hash=doi_hash,
                        ontology_name=ontology_name,
                        data_dir=data_dir,
                        meta_cfg=meta_cfg,
                        src_candidates=[iteration_1_ttl, memory_top_ttl],
                    )
                    return True
                except Exception as e:
                    logger.error(f"Failed to copy memory/{alias}.ttl: {e}")
                    return False
            # Last-resort fallback: try the latest exported snapshot for the configured
            # iter1 entity context or a compatible legacy alias.
            try:
                if os.path.isdir(exports_dir):
                    export_candidates = []
                    for alias in iter1_entity_aliases:
                        export_candidates.extend(
                            os.path.join(exports_dir, f)
                            for f in os.listdir(exports_dir)
                            if f.lower().startswith(f"{alias.lower()}_")
                            and f.lower().endswith(".ttl")
                        )
                    if export_candidates:
                        export_candidates = sorted(set(export_candidates))
                        export_candidates.sort(
                            key=lambda p: os.path.getmtime(p), reverse=True
                        )
                        latest = export_candidates[0]
                        import shutil

                        shutil.copy2(latest, iteration_1_ttl)
                        logger.info(
                            f"✅ Saved iteration_1.ttl from latest export: {os.path.basename(latest)}"
                        )
                        _ = publish_top_ttl(
                            doi_hash=doi_hash,
                            ontology_name=ontology_name,
                            data_dir=data_dir,
                            meta_cfg=meta_cfg,
                            src_candidates=[iteration_1_ttl, latest],
                        )
                        return True
            except Exception as e:
                logger.warning(f"⚠️  Failed scanning exports fallback: {e}")

            logger.warning(
                "⚠️  No output.ttl/output_top.ttl and no memory/export fallback found"
            )
            return False


def parse_top_entities_from_ttl(
    doi_hash: str,
    ontology_name: str,
    data_dir: str = "data",
    meta_task_config_path: str = "configs/meta_task/meta_task_config.json",
) -> bool:
    """
    Parse the iteration_1.ttl using SPARQL to extract top entities and save as JSON.

    Args:
        doi_hash: DOI hash identifier
        ontology_name: Name of the ontology (e.g., "ontosynthesis")
        data_dir: Base data directory

    Returns:
        True if parsing succeeded
    """
    try:
        doi_folder = os.path.join(data_dir, doi_hash)
        meta_config = load_meta_config(meta_task_config_path)
        top_class_iri, _ = _get_pipeline_selected_top_class(doi_folder)
        if not top_class_iri:
            logger.error(
                "❌ Cannot parse top entities because pipeline top-class selection is missing"
            )
            return False
        ttl_path = os.path.join(doi_folder, "iteration_1.ttl")
        sparql_path = resolve_generated_file(
            f"ai_generated_contents/sparqls/{ontology_name}/top_entity_parsing.sparql"
        )
        output_json_path = os.path.join(
            doi_folder, "mcp_run", "iter1_top_entities.json"
        )

        # Check if TTL exists
        if not os.path.exists(ttl_path):
            logger.error(f"❌ TTL file not found: {ttl_path}")
            return False

        # Check if SPARQL query exists
        if not os.path.exists(sparql_path):
            logger.error(f"❌ SPARQL query not found: {sparql_path}")
            return False

        # Load SPARQL query
        with open(sparql_path, "r", encoding="utf-8") as f:
            sparql_query = f.read()

        # Parse TTL
        logger.info(f"📊 Parsing TTL with SPARQL query")
        g = Graph()
        g.parse(ttl_path, format="turtle")

        # Execute SPARQL query
        results = g.query(sparql_query)

        # Convert results to JSON format
        # NOTE: We do not assume any ontology-specific variable names here.
        # The SPARQL is expected to bind a top-entity variable (e.g. ?entity or ?synthesis)
        # and optionally ?label. We fall back to the first binding if needed.
        entities = []
        for row in results:
            # Prefer a generic ?entity variable if present, otherwise fall back to ?synthesis,
            # then finally to the first column of the row.
            if hasattr(row, "entity"):
                uri = str(row.entity)
            elif hasattr(row, "synthesis"):
                uri = str(row.synthesis)
            else:
                uri = str(row[0])

            label = (
                str(row.label)
                if hasattr(row, "label") and row.label
                else uri.split("/")[-1]
            )

            entities.append(
                {
                    "uri": uri,
                    "label": label,
                    "types": sorted(
                        {
                            str(type_iri)
                            for type_iri in g.objects(URIRef(uri), RDF.type)
                            if isinstance(type_iri, URIRef)
                        }
                    ),
                }
            )

        entities = _canonicalize_parsed_top_entities(g=g, entities=entities)
        entities = _filter_runtime_context_top_entities(entities, meta_config)
        supplemented_entities = _merge_txt_top_entity_fallback(
            doi_folder, entities, top_class_iri
        )
        supplemented_entities = _filter_runtime_context_top_entities(
            supplemented_entities, meta_config
        )
        if len(supplemented_entities) > len(entities):
            logger.warning(
                "⚠️  Supplemented top-entity JSON from top_entities.txt: %s -> %s",
                len(entities),
                len(supplemented_entities),
            )
        if _materialize_supplemented_top_entities(
            g, supplemented_entities, top_class_iri
        ):
            g.serialize(destination=ttl_path, format="turtle")
            logger.warning(
                "⚠️  Materialized supplemented top entities into iteration_1.ttl"
            )
        entities = supplemented_entities
        entities = hydrate_and_validate_top_entity_types(
            entities=entities,
            iteration_1_ttl=ttl_path,
            top_class_iri=top_class_iri,
        )
        entities = attach_entity_identity_dossiers(
            entities=entities,
            iteration_1_ttl=ttl_path,
        )

        # CRITICAL VALIDATION: Check if entities list is empty
        if not entities or len(entities) == 0:
            logger.error(
                f"❌ CRITICAL: Parsed 0 entities from TTL - KG building failed to create any entities!"
            )
            logger.error(
                f"   This usually means the agent didn't properly use the MCP tools"
            )
            # Save empty JSON anyway for debugging
            os.makedirs(os.path.dirname(output_json_path), exist_ok=True)
            with open(output_json_path, "w", encoding="utf-8") as f:
                json.dump(entities, f, indent=2)
            return False  # Signal failure so we can retry

        # Save to JSON
        os.makedirs(os.path.dirname(output_json_path), exist_ok=True)
        with open(output_json_path, "w", encoding="utf-8") as f:
            json.dump(entities, f, indent=2)
        persist_entity_identity_sidecars(
            doi_hash=doi_hash,
            doi_folder=doi_folder,
            entities=entities,
            top_class_iri=top_class_iri,
        )

        logger.info(f"✅ Parsed {len(entities)} canonical top entities from TTL")
        logger.info(f"   Saved to: {output_json_path}")

        # Log first few entities
        for entity in entities[:3]:
            logger.info(f"   - {entity['label']}")
        if len(entities) > 3:
            logger.info(f"   ... and {len(entities) - 3} more")

        return True

    except Exception as e:
        logger.error(f"❌ Failed to parse TTL: {e}")
        return False


def run_step(doi_hash: str, config: dict) -> bool:
    """
    Main entry point for the top entity KG building pipeline step.

    This step:
    1. Loads the meta task configuration to determine ontology and MCP settings
    2. Loads the extraction hints from the previous step
    3. Loads the KG building prompt
    4. Runs an LLM agent with MCP tools to build the knowledge graph
    5. Saves the output TTL as iteration_1.ttl

    Args:
        doi_hash: The DOI hash to process
        config: Pipeline configuration dictionary

    Returns:
        True if KG building succeeded
    """
    data_dir = config.get("data_dir", "data")
    doi_folder = os.path.join(data_dir, doi_hash)
    os.environ["TWA_AGENTIC_DATA_DIR"] = os.path.abspath(data_dir)

    logger.info(f"▶️  Top Entity KG Building for {doi_hash}")

    # Load meta task configuration
    meta_task_config_path = config.get(
        "meta_task_config", "configs/meta_task/meta_task_config.json"
    )
    meta_config = load_meta_config(meta_task_config_path)
    if not meta_config:
        logger.error("❌ Failed to load meta task configuration")
        return False

    # Get main ontology configuration
    main_ontology = meta_config.get("ontologies", {}).get("main", {})
    ontology_name = main_ontology.get("name", "ontosynthesis")
    mcp_set_name = main_ontology.get("mcp_set_name", "run_created_mcp.json")
    mcp_tools = main_ontology.get("mcp_list", ["llm_created_mcp"])
    agent_model = main_ontology.get("agent_model") or "gpt-4o"
    iter1_entity_context_name = _get_iter1_entity_context_name(
        meta_config, default="top"
    )
    # Main-KG processing pins this process-wide variable to its last entity
    # scope.  Iteration 1 owns a shared, document-level scope and must reset
    # the pin before either the MCP agent or the deterministic repair loads
    # memory; otherwise a later document opens the previous document's entity
    # memory instead of memory/top.ttl.
    os.environ["TWA_MCP_ENTITY_CONTEXT_EXPECTED_NAME"] = (
        iter1_entity_context_name
    )
    _apply_identifier_runtime_env(meta_config)

    # Check if iteration_1.ttl already exists
    iteration_1_ttl = os.path.join(doi_folder, "iteration_1.ttl")
    if os.path.exists(iteration_1_ttl):
        if _iter1_needs_generated_top_uri_repair(
            doi_hash=doi_hash,
            data_dir=data_dir,
            ontology_name=ontology_name,
            meta_config=meta_config,
        ):
            logger.warning(
                "⚠️  Existing ITER1 graph violates the one-top-entity-per-scope "
                "identity lock; rebuilding canonical shared state"
            )
            if not _repair_iter1_ttl_with_generated_tools(
                doi_hash=doi_hash,
                data_dir=data_dir,
                ontology_name=ontology_name,
                meta_config=meta_config,
                entity_context_name=iter1_entity_context_name,
            ):
                return _fail_open_iter1_for_extraction(
                    doi_hash=doi_hash,
                    data_dir=data_dir,
                    ontology_name=ontology_name,
                    meta_config=meta_config,
                    meta_task_config_path=meta_task_config_path,
                    entity_context_name=iter1_entity_context_name,
                    reason="existing ITER1 graph could not be rebuilt with generated tools",
                )
        logger.info(
            f"  ⏭️  iteration_1.ttl already exists; refreshing iter1_top_entities.json"
        )
        parse_ok = parse_top_entities_from_ttl(
            doi_hash,
            ontology_name,
            data_dir,
            meta_task_config_path=meta_task_config_path,
        )
        if parse_ok:
            return True
        return _fail_open_iter1_for_extraction(
            doi_hash=doi_hash,
            data_dir=data_dir,
            ontology_name=ontology_name,
            meta_config=meta_config,
            meta_task_config_path=meta_task_config_path,
            entity_context_name=iter1_entity_context_name,
            reason="existing ITER1 TTL could not be parsed into top entities",
        )

    # Override with test MCP config if provided
    if "test_mcp_config" in config:
        mcp_set_name = config["test_mcp_config"]
        logger.info(f"  🧪 Using test MCP config")

    logger.info(f"  📋 Ontology: {ontology_name}")
    logger.info(f"  🔧 MCP Set: {mcp_set_name}")
    logger.info(f"  🛠️  MCP Tools: {mcp_tools}")
    logger.info(f"  🤖 Agent model: {agent_model}")

    # Load extraction hints from previous step
    hints = load_extraction_hints(doi_hash, data_dir)
    if not hints:
        logger.error("❌ Failed to load extraction hints")
        return False

    logger.info(f"  ✓ Loaded extraction hints ({len(hints)} chars)")
    top_class_iri, top_class_local = _get_pipeline_selected_top_class(doi_folder)
    extracted_top_entities = _top_entities_from_txt(doi_folder, top_class_iri)
    if not top_class_iri or not top_class_local or not extracted_top_entities:
        logger.error("❌ Cannot establish the top-entity identity lock")
        return False
    locked_top_entities = _seed_iter1_top_entity_lock(
        doi_hash=doi_hash,
        doi_folder=doi_folder,
        top_entities=extracted_top_entities,
        top_class_iri=top_class_iri,
        entity_context_name=iter1_entity_context_name,
        entity_context_aliases=_get_iter1_entity_context_aliases(
            meta_config, default=iter1_entity_context_name
        ),
        seed_doi_document=_requires_pipeline_doi_document_lock(meta_config),
    )

    # Load KG building prompt
    prompt_path = resolve_generated_file(
        f"ai_generated_contents/prompts/{ontology_name}/KG_BUILDING_ITER_1.md"
    )
    prompt_template = load_extraction_prompt(prompt_path)
    if not prompt_template:
        logger.error(f"❌ Failed to load prompt from {prompt_path}")
        return False
    prompt_template = _augment_iter1_prompt_with_runtime_rules(
        prompt_template, doi_hash, meta_config
    )
    prompt_template += (
        "\n\nPipeline-owned top-entity identity lock (authoritative):\n"
        + json.dumps(locked_top_entities, ensure_ascii=False, indent=2)
        + "\n- These top entities already exist in retained memory under the exact "
        "listed URIs.\n"
        f"- The `create_{top_class_local}` tool is intentionally unavailable. "
        "Never create another top-class instance; only attach ontology-supported "
        "facts and required links to these locked subjects.\n"
    )

    logger.info(f"  ✓ Loaded KG building prompt")

    # Run the agent with retry logic for empty entity lists
    max_kg_retries = 3
    test_mode = "test_mcp_config" in config

    for kg_attempt in range(max_kg_retries):
        try:
            if kg_attempt > 0:
                logger.info(
                    f"  🔄 KG Building retry attempt {kg_attempt + 1}/{max_kg_retries}"
                )
                # Clean up previous failed attempt
                if os.path.exists(iteration_1_ttl):
                    os.remove(iteration_1_ttl)
                    logger.info(
                        f"  🗑️  Removed failed iteration_1.ttl from previous attempt"
                    )

            paper_content = load_paper_content(doi_hash, data_dir)
            if not paper_content:
                logger.error("❌ Failed to load paper content for KG building")
                return False

            prompt_for_attempt = prompt_template
            if kg_attempt > 0:
                prompt_for_attempt = (
                    prompt_template.rstrip()
                    + "\n\nVALIDATION FEEDBACK FROM PREVIOUS ATTEMPT:\n"
                    + "- The prior response did not produce a persisted Turtle file for the pipeline.\n"
                    + "- Do not answer with prose-only success claims.\n"
                    + "- Call `init_memory`, bind the exact pipeline-locked root URIs, attach only allowed facts when needed, and call `export_memory`.\n"
                    + f"- Never call `create_{top_class_local}` or mint a replacement root during retry.\n"
                    + "- The retry is successful only if a TTL file is written for the current document.\n"
                )

            # Save full prompt for reproducibility/debugging
            try:
                preview_prompt = bind_iter1_runtime_context(
                    prompt_for_attempt,
                    doi_hash=doi_hash,
                    paper_content=paper_content,
                    top_entities=hints,
                )
                preview_prompt = inject_global_context_brief(
                    preview_prompt,
                    load_global_context_brief(
                        Path(data_dir)
                        / doi_hash
                        / "global_procedure_context.json"
                    ),
                )
                save_full_prompt(doi_hash, preview_prompt, data_dir)
            except Exception:
                pass

            response, metadata = asyncio.run(
                run_kg_building_agent(
                    doi_hash=doi_hash,
                    prompt_template=prompt_for_attempt,
                    hints=hints,
                    paper_content=paper_content,
                    mcp_tools=mcp_tools,
                    mcp_set_name=mcp_set_name,
                    model_name=agent_model,
                    temperature=0.0,
                    top_p=0.01,
                    entity_context_name=iter1_entity_context_name,
                    excluded_tool_names=[f"create_{top_class_local}"],
                    data_dir=data_dir,
                )
            )

            # Save agent response
            save_agent_response(doi_hash, response, data_dir)

            # Copy output TTL to iteration_1.ttl
            if not copy_output_ttl(
                doi_hash,
                data_dir,
                test_mode=test_mode,
                ontology_name=ontology_name,
                meta_task_config_path=meta_task_config_path,
            ):
                logger.warning("⚠️  Failed to save iteration_1.ttl")
                repaired = _repair_iter1_ttl_with_generated_tools(
                    doi_hash=doi_hash,
                    data_dir=data_dir,
                    ontology_name=ontology_name,
                    meta_config=meta_config,
                    entity_context_name=iter1_entity_context_name,
                )
                if repaired and copy_output_ttl(
                    doi_hash,
                    data_dir,
                    test_mode=test_mode,
                    ontology_name=ontology_name,
                    meta_task_config_path=meta_task_config_path,
                ):
                    logger.warning(
                        "⚠️  Saved iteration_1.ttl after generated-tool repair"
                    )
                else:
                    repaired = False
                if repaired:
                    pass
                elif kg_attempt < max_kg_retries - 1:
                    logger.info(f"  ⏳ Waiting 5s before retry...")
                    import time

                    time.sleep(5)
                    continue
                else:
                    return _fail_open_iter1_for_extraction(
                        doi_hash=doi_hash,
                        data_dir=data_dir,
                        ontology_name=ontology_name,
                        meta_config=meta_config,
                        meta_task_config_path=meta_task_config_path,
                        entity_context_name=iter1_entity_context_name,
                        reason="agent did not persist iteration_1.ttl and generated-tool repair was unavailable",
                    )
            elif _iter1_needs_generated_top_uri_repair(
                doi_hash=doi_hash,
                data_dir=data_dir,
                ontology_name=ontology_name,
                meta_config=meta_config,
            ):
                logger.warning(
                    "⚠️  ITER1 top shell uses non-generated top IRIs; rebuilding with generated tools"
                )
                repaired = _repair_iter1_ttl_with_generated_tools(
                    doi_hash=doi_hash,
                    data_dir=data_dir,
                    ontology_name=ontology_name,
                    meta_config=meta_config,
                    entity_context_name=iter1_entity_context_name,
                )
                if not repaired:
                    return _fail_open_iter1_for_extraction(
                        doi_hash=doi_hash,
                        data_dir=data_dir,
                        ontology_name=ontology_name,
                        meta_config=meta_config,
                        meta_task_config_path=meta_task_config_path,
                        entity_context_name=iter1_entity_context_name,
                        reason="generated-tool IRI repair was unavailable",
                    )

            # Parse TTL to extract top entities as JSON
            logger.info(f"  📊 Parsing top entities from TTL")
            parse_success = parse_top_entities_from_ttl(
                doi_hash,
                ontology_name,
                data_dir,
                meta_task_config_path=meta_task_config_path,
            )

            if not parse_success:
                # Parsing failed or returned empty entities list
                logger.error(
                    f"  ❌ KG building attempt {kg_attempt + 1}/{max_kg_retries} produced no entities"
                )
                if kg_attempt < max_kg_retries - 1:
                    logger.info(f"  ⏳ Waiting 5s before retry...")
                    import time

                    time.sleep(5)
                    continue
                else:
                    logger.error(
                        f"  ❌ All {max_kg_retries} KG building attempts failed to produce entities"
                    )
                    return _fail_open_iter1_for_extraction(
                        doi_hash=doi_hash,
                        data_dir=data_dir,
                        ontology_name=ontology_name,
                        meta_config=meta_config,
                        meta_task_config_path=meta_task_config_path,
                        entity_context_name=iter1_entity_context_name,
                        reason="SPARQL parse of iteration_1.ttl produced no entities",
                    )

            # Success! Entities were created
            logger.info(f"✅ Top Entity KG Building completed for {doi_hash}")
            return True

        except Exception as e:
            logger.error(
                f"❌ KG building attempt {kg_attempt + 1}/{max_kg_retries} failed: {e}"
            )
            if kg_attempt < max_kg_retries - 1:
                logger.info(f"  ⏳ Waiting 5s before retry...")
                import time

                time.sleep(5)
            else:
                logger.error(f"❌ All {max_kg_retries} KG building attempts failed")
                return _fail_open_iter1_for_extraction(
                    doi_hash=doi_hash,
                    data_dir=data_dir,
                    ontology_name=ontology_name,
                    meta_config=meta_config,
                    meta_task_config_path=meta_task_config_path,
                    entity_context_name=iter1_entity_context_name,
                    reason=f"KG building attempts exhausted: {e}",
                )

    return _fail_open_iter1_for_extraction(
        doi_hash=doi_hash,
        data_dir=data_dir,
        ontology_name=ontology_name,
        meta_config=meta_config,
        meta_task_config_path=meta_task_config_path,
        entity_context_name=iter1_entity_context_name,
        reason="KG building loop exited without a parsed top-entity JSON",
    )


if __name__ == "__main__":
    # Example usage for standalone testing
    if len(sys.argv) > 1:
        test_doi_hash = sys.argv[1]
        test_config = {"data_dir": "data"}
        print(f"Running top entity KG building step for DOI hash: {test_doi_hash}")
        success = run_step(test_doi_hash, test_config)
        print(f"Top entity KG building step {'succeeded' if success else 'failed'}.")
    else:
        print("Usage: python -m src.pipelines.top_entity_kg_building.build <doi_hash>")
