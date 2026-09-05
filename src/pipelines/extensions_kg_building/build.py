"""
Extensions KG Building Module

Handles agent-based A-Box building for extension ontologies (OntoMOPs and OntoSpecies).
"""

import os
import json
import re
import asyncio
import logging
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional
from filelock import FileLock
from rdflib import Graph, URIRef

from models.BaseAgent import BaseAgent
from models.ModelConfig import ModelConfig
from src.pipelines.utils.runtime_paths import (
    bounded_runtime_file,
    extension_filename_stems,
    find_existing_extension_artifact,
    first_existing_runtime_path,
    list_runtime_files,
    read_runtime_text,
    resolve_extension_artifact,
    runtime_path_exists,
    windows_fs_path,
    write_runtime_text,
)
from src.agents.scripts_and_prompts_generation.generation_contracts import (
    build_ontology_publish_contract_from_tbox,
)
from src.pipelines.utils.extension_bridge import apply_extension_bridge
from src.pipelines.utils.extension_revision import (
    collect_extension_structural_messages,
    collect_hint_violations,
    revision_attempt_limits,
)
from src.pipelines.utils.kg_full_hints_onepass import (
    build_mcp_semantic_surface_task_prompt,
)
from src.pipelines.utils.kg_revision_limits import (
    ensure_kg_norev,
    kg_agent_attempt_limit,
)
from src.pipelines.utils.llm_transport_retry import (
    is_llm_transport_error,
    retry_async_on_transport,
)
from src.pipelines.utils.top_entity_identity import entity_artifact_name
from src.pipelines.utils.ttl_publisher import (
    get_main_ontology_name,
    get_output_naming_config,
    load_meta_task_config,
)
from src.pipelines.utils.ordered_member_integrity import (
    align_ordered_members_to_reference_content,
    enforce_ordered_member_integrity_file,
    load_all_runtime_ordered_member_profiles,
)
from src.pipelines.utils.published_synthesis_queue import load_extension_synthesis_queue
from src.pipelines.utils.top_entity_identity import entity_scope_name
from src.pipelines.main_kg_building.build import (
    _next_post_publish_feedback_attempt,
    _post_publish_repair_prompt,
    _publish_central_memory_after_semantic_commit,
    _write_json_atomic,
    _write_post_publish_feedback,
)

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)


def _safe_name(label: str) -> str:
    """Stable, length-capped artifact stem shared with main extraction."""
    return entity_artifact_name(label)


def _fail_extension_kg(message: str) -> None:
    logger.error(message)
    raise RuntimeError(message)


def _fill_extension_prompt_template(template: str, values: dict) -> str:
    """Substitute known `{key}` tokens without interpreting chemistry braces.

    TBox examples such as `{[Cp3Zr3µ3-O(µ2-OH)3]4(BDC)6}` are positional to
    `str.format` (`{[` starts an empty field). Only replace explicit keys.
    """
    filled = str(template)
    for key, value in values.items():
        filled = filled.replace("{" + str(key) + "}", str(value))
    return filled


_EXPORT_TIMESTAMP_RE = re.compile(r"_\d{8}_\d{6}$")


def _read_nonempty_ttl(path: str) -> Optional[str]:
    """Read a TTL if the file has any content. Completeness is checked later."""
    try:
        content = read_runtime_text(path)
    except Exception:
        return None
    return content if str(content or "").strip() else None


def _export_identity_stem(path: str) -> str:
    """Strip the MCP ``_YYYYMMDD_HHMMSS`` suffix from an export filename."""
    return _EXPORT_TIMESTAMP_RE.sub("", os.path.splitext(os.path.basename(path))[0])


def _apply_extension_entity_context(entity_label: str, entity_uri: str) -> str:
    """Bind extension MCP to the same scope name main KG already uses."""
    uri = str(entity_uri or "").strip()
    scope = entity_scope_name(entity_label, uri) if uri else entity_artifact_name(entity_label)
    os.environ["TWA_MCP_ENTITY_CONTEXT_EXPECTED_NAME"] = scope
    os.environ["TWA_MCP_ENTITY_CONTEXT_EXPECTED_IRI"] = uri
    return scope


def _load_scoped_extension_ttl(
    doi_folder: str,
    ontology_name: Optional[str],
    entity_label: str,
    *,
    output_ttl_path: str = "",
    entity_uri: str = "",
) -> Optional[tuple[str, str]]:
    """Load this top entity's own nonempty extension TTL.

    Accept the exact published path, the MCP memory stem for this
    label/URI (main-KG scope, label artifact, or untruncated slug), or an
    export whose destemmed name equals that stem. Sibling files are never
    selected by token affinity or quality ranking. Missing required
    identities are rejected later by the publish-contract checks.
    """
    if not ontology_name or not str(entity_label or "").strip():
        return None

    stems = set(extension_filename_stems(entity_label, entity_uri=entity_uri))
    ordered: list[str] = []
    if output_ttl_path:
        ordered.append(output_ttl_path)

    memory_template = f"memory_{ontology_name}/{{entity_safe}}.ttl"
    _, memory_candidates = resolve_extension_artifact(
        doi_folder, memory_template, entity_label, entity_uri=entity_uri
    )
    ordered.extend(memory_candidates)

    for path in list_runtime_files(
        os.path.join(doi_folder, f"exports_{ontology_name}"), ".ttl"
    ):
        if _export_identity_stem(path) in stems:
            ordered.append(path)

    newest: Optional[tuple[float, str, str]] = None
    seen: set[str] = set()
    for path in ordered:
        key = os.path.normcase(os.path.abspath(path))
        if key in seen:
            continue
        seen.add(key)
        if not runtime_path_exists(path):
            continue
        content = _read_nonempty_ttl(path)
        if not content:
            continue
        try:
            mtime = os.path.getmtime(windows_fs_path(path))
        except OSError:
            mtime = 0.0
        if newest is None or mtime >= newest[0]:
            newest = (mtime, path, content)
    if newest is None:
        return None
    return newest[1], newest[2]


def load_prompt(prompt_path: str, project_root: str = ".") -> str:
    """Load prompt template from markdown file.
    
    Tries candidate directory first, then production directory.
    """
    prompt_path = (prompt_path or "").replace("\\", "/")
    override_root = os.environ.get("TWA_GENERATED_ARTIFACT_ROOT", "").strip().replace("\\", "/").rstrip("/")
    paths_to_try = []
    if override_root and prompt_path.startswith("ai_generated_contents/"):
        paths_to_try.append(os.path.join(project_root, prompt_path.replace("ai_generated_contents", override_root, 1)))
    paths_to_try.extend([
        os.path.join(project_root, prompt_path.replace("ai_generated_contents/", "ai_generated_contents_candidate/", 1)),
        os.path.join(project_root, prompt_path),
    ])
    
    for full_path in paths_to_try:
        if os.path.exists(full_path):
            try:
                with open(full_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                logger.info(f"    📄 Loaded prompt: {os.path.basename(full_path)} (from {os.path.dirname(full_path)})")
                return content
            except Exception as e:
                logger.error(f"    ❌ Failed to load prompt from {full_path}: {e}")
                continue
    
    # If we get here, neither path worked
    logger.error(f"    ❌ Prompt not found. Tried:")
    for path in paths_to_try:
        logger.error(f"      - {path}")
    return ""


@lru_cache(maxsize=1)
def _ccdc_tool_is_healthy() -> bool:
    """Return whether the local CCDC tool can answer a minimal lookup."""
    try:
        from src.mcp_servers.ccdc.operations.wsl_ccdc import search_ccdc_by_mop_name

        results = search_ccdc_by_mop_name("VMOP-a", exact=False)
        if results:
            logger.info("    ✅ CCDC tool health check passed")
            return True
        logger.warning("    ⚠️  CCDC tool health check returned no results")
    except Exception as exc:
        logger.warning(f"    ⚠️  CCDC tool health check failed: {exc}")
    return False


def load_entity_ttl(
    doi_hash: str,
    entity_safe: str,
    entity_uri: str = "",
    data_dir: str = "data",
    test_mode: bool = False,
    ontology_name: str = "ontosynthesis",
    meta_cfg: Optional[dict] = None,
) -> str:
    """Load this top entity's exact OntoSynthesis TTL. No sibling-name search."""
    doi_folder = os.path.join(data_dir, doi_hash)
    published_dir = os.path.join(doi_folder, f"{ontology_name}_output")
    exact_names: List[str] = []
    try:
        meta_cfg = meta_cfg or load_meta_task_config()
        naming = get_output_naming_config(meta_cfg=meta_cfg, ontology_name=ontology_name)
        published_dir = os.path.join(doi_folder, naming.output_dir)
        try:
            primary_name = naming.entity_ttl_pattern.format(
                entity_safe=entity_safe,
                ontology_name=ontology_name,
            )
        except Exception:
            primary_name = f"{entity_safe}.ttl"
        if not str(primary_name).endswith(".ttl"):
            primary_name = f"{primary_name}.ttl"
        exact_names.append(primary_name)
    except Exception as exc:
        logger.debug(f"    Published TTL naming lookup failed: {exc}")

    if str(entity_uri).strip():
        exact_names.insert(0, f"{entity_scope_name(entity_safe, entity_uri)}.ttl")
    exact_names.append(f"{entity_safe}.ttl")

    seen: set[str] = set()
    for candidate in exact_names:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        ttl_path = os.path.join(published_dir, candidate)
        if not runtime_path_exists(ttl_path):
            continue
        content = read_runtime_text(ttl_path)
        logger.info(
            "    📄 Loaded exact-identity entity TTL from published output: %s",
            candidate,
        )
        return content

    memory_ttl = os.path.join(doi_folder, "memory", f"{entity_safe}.ttl")
    if runtime_path_exists(memory_ttl):
        content = read_runtime_text(memory_ttl)
        logger.info(
            "    📄 Loaded exact-identity entity TTL from memory: %s",
            os.path.basename(memory_ttl),
        )
        return content

    if test_mode:
        logger.error(
            f"[TEST MODE] Entity TTL not found for {entity_safe} in {published_dir} "
            "or exact memory fallback"
        )
        return ""

    raise FileNotFoundError(f"Could not find OntoSynthesis TTL for entity {entity_safe}")


def resolve_doi_from_hash(doi_hash: str, data_dir: str = "data") -> tuple:
    """Return (pipeline_doi_with_underscore, slash_doi) for a given hash.

    Prefers ``<data_dir>/<hash>/paper_doi.txt`` when present, then
    ``doi_to_hash.json``. Always backfills paper_doi files when a mapping hit
    is found so CCDC prompts can inject a bibliographic DOI.
    """
    try:
        from src.pipelines.utils.file_ops import read_paper_doi, write_paper_doi_files

        doi_folder = os.path.join(data_dir, doi_hash)
        paper = read_paper_doi(doi_folder)
        if paper and paper.startswith("10.") and "/" in paper:
            write_paper_doi_files(doi_folder, paper)
            return (paper.replace("/", "_"), paper)

        mapping_path = os.path.join(data_dir, "doi_to_hash.json")
        if not os.path.exists(mapping_path):
            # Fall back to repo-root / env mapping commonly shared across runs
            for alt in (
                os.path.join(os.environ.get("TWA_AGENTIC_DATA_DIR") or "", "doi_to_hash.json"),
                os.path.join("data", "doi_to_hash.json"),
            ):
                if alt and os.path.exists(alt):
                    mapping_path = alt
                    break
        if not os.path.exists(mapping_path):
            logger.warning(f"DOI mapping file not found: {mapping_path}")
            return (doi_hash, doi_hash)

        with open(mapping_path, "r", encoding="utf-8") as f:
            doi_to_hash = json.load(f)

        hash_to_doi = {h: d for d, h in doi_to_hash.items()}

        if doi_hash not in hash_to_doi:
            logger.warning(f"Hash {doi_hash} not found in mapping")
            return (doi_hash, doi_hash)

        slash_doi = str(hash_to_doi[doi_hash]).replace("_", "/")
        underscore_doi = slash_doi.replace("/", "_")
        write_paper_doi_files(doi_folder, slash_doi)
        return (underscore_doi, slash_doi)
    except Exception as e:
        logger.error(f"Error resolving DOI from hash: {e}")
        return (doi_hash, doi_hash)


def _enrichment_query_source(
    query_path: Path, query_file: str, project_root: str
) -> str:
    declared = str(query_file or "").strip()
    if declared:
        candidate = Path(declared)
        if not candidate.is_absolute():
            candidate = Path(project_root) / candidate
        if candidate.resolve() == query_path.resolve():
            return declared
    return str(query_path)


def resolve_enrichment_targets(
    *,
    ontology_name: str,
    entity_uri: str,
    main_ontology_ttl: str,
    meta_cfg: dict,
    project_root: str = ".",
) -> List[dict]:
    """Resolve authoritative extension targets with a configured SPARQL query."""
    extension_cfg = next(
        (
            item
            for item in (meta_cfg.get("ontologies", {}).get("extensions", []) or [])
            if str(item.get("name") or "").strip() == str(ontology_name).strip()
        ),
        None,
    )
    policy = (
        (extension_cfg or {})
        .get("runtime_policies", {})
        .get("enrichment_target", {})
    )
    query_file = str(policy.get("query_file") or "").strip()
    target_variable = str(policy.get("target_variable") or "target").strip()
    target_class_iri = str(policy.get("target_class_iri") or "").strip()
    cardinality = str(policy.get("cardinality") or "exactly_one").strip()
    root_variable = str(policy.get("root_variable") or "synthesis").strip() or "synthesis"
    if not target_class_iri:
        raise RuntimeError(
            f"Missing deterministic enrichment-target policy for {ontology_name}"
        )
    if not str(entity_uri or "").startswith(("http://", "https://", "urn:")):
        raise RuntimeError(f"Invalid scoped entity URI for {ontology_name}: {entity_uri!r}")

    from src.agents.scripts_and_prompts_generation.enrichment_target_sparql import (
        resolve_enrichment_target_sparql_path,
    )

    query_path = resolve_enrichment_target_sparql_path(
        ontology_name,
        query_file=query_file,
        project_root=project_root,
    )
    source = _enrichment_query_source(query_path, query_file, project_root)
    query = query_path.read_text(encoding="utf-8")
    graph = Graph()
    graph.parse(data=main_ontology_ttl, format="turtle")
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
            f"{ontology_name} enrichment target query must resolve exactly one URI "
            f"for {entity_uri}; resolved {len(target_iris)}: {target_iris}"
        )
    if not target_iris:
        raise RuntimeError(
            f"{ontology_name} enrichment target query resolved no URI for {entity_uri}"
        )
    return [
        {
            "name": "primary",
            "target_iri": target_iri,
            "class_iri": target_class_iri,
            "source": source,
        }
        for target_iri in target_iris
    ]


def write_global_state(
    ontology_name: str,
    hash_value: str,
    entity_label: str,
    entity_uri: str,
    data_dir: str = "data",
    enrichment_targets: Optional[List[dict]] = None,
):
    """Write global state file for extension MCP server.
    
    Args:
        ontology_name: Name of the extension ontology (e.g., 'ontomops', 'ontospecies')
        hash_value: 8-character hash identifying the paper
        entity_label: Label of the top-level entity
        entity_uri: IRI of the top-level entity
        data_dir: Base data directory
    """
    # Use ontology-specific global state file
    global_state_path = os.path.join(data_dir, f"{ontology_name}_global_state.json")
    lock_path = f"{global_state_path}.lock"
    
    # Extension MCP scripts expect 'doi' key (though it's actually a hash)
    # Keep both 'doi' and 'hash' for compatibility
    state = {
        "doi": hash_value,  # Extension scripts use this key
        "hash": hash_value,  # For clarity
        "top_level_entity_name": entity_label,
        "top_level_entity_iri": entity_uri,
        "enrichment_targets": list(enrichment_targets or []),
    }
    
    try:
        with FileLock(lock_path, timeout=10):
            with open(global_state_path, 'w', encoding='utf-8') as f:
                json.dump(state, f, indent=2)
        logger.info(f"    📝 Updated global state: {os.path.basename(global_state_path)}")
    except Exception as e:
        logger.error(f"    ❌ Failed to write global state: {e}")
        raise


async def run_extension_agent(
    doi_hash: str,
    entity_label: str,
    entity_uri: str,
    ontosynthesis_ttl: str,
    extracted_content: str,
    extension_prompt_template: str,
    mcp_tools: List[str],
    mcp_set_name: str,
    agent_model: str,
    recursion_limit: int,
    prompt_file: str,
    output_ttl_name: str,
    data_dir: str = "data",
    ontology_name: str = None,
    meta_cfg: Optional[dict] = None,
    project_root: str = ".",
    enrichment_targets: Optional[List[dict]] = None,
    force_rerun: bool = False,
    extra_prompt: str = "",
    pipeline_config: Optional[dict] = None,
) -> str:
    """Run extension agent for a single entity."""
    doi_folder = os.path.join(data_dir, doi_hash)
    output_ttl_path = bounded_runtime_file(os.path.join(doi_folder, output_ttl_name))
    _apply_extension_entity_context(entity_label, entity_uri)

    def _maybe_update_ontomops_mapping(final_path: str) -> None:
        """
        Ensure OntoMOPs has a label/IRI → filename mapping even when we did not
        call the MCP server's `export_memory()` (e.g., when copying from memory/exports).
        """
        if ontology_name != "ontomops":
            return
        try:
            out_dir = os.path.dirname(final_path)
            if not out_dir:
                return
            os.makedirs(windows_fs_path(out_dir), exist_ok=True)
            mapping_file = os.path.join(out_dir, "ontomops_output_mapping.json")
            mapping = {}
            if runtime_path_exists(mapping_file):
                try:
                    mapping = json.loads(read_runtime_text(mapping_file)) or {}
                except Exception:
                    mapping = {}
            fn = os.path.basename(final_path)
            if entity_label:
                mapping[entity_label] = fn
            if entity_uri:
                mapping[entity_uri] = fn
            write_runtime_text(
                mapping_file, json.dumps(mapping, indent=2, ensure_ascii=False)
            )
        except Exception:
            return

    meta_cfg = meta_cfg or load_meta_task_config()
    runtime_ordered_member_profile = load_all_runtime_ordered_member_profiles(
        meta_cfg=meta_cfg,
        project_root=project_root,
    )

    def _finalize_extension_output(content: str, final_path: str, completion_message: str) -> str:
        tbox_path = None
        for ext_cfg in ((meta_cfg or {}).get("ontologies", {}).get("extensions", []) or []):
            if str(ext_cfg.get("name") or "").strip() == str(ontology_name or "").strip():
                raw_tbox = str(ext_cfg.get("ttl_file") or "").strip()
                if raw_tbox:
                    tbox_path = (
                        raw_tbox
                        if Path(raw_tbox).is_absolute()
                        else str(Path(project_root) / raw_tbox)
                    )
                break
        finalized, bridge_report = apply_extension_bridge(
            content,
            enrichment_targets,
            tbox_path=tbox_path,
        )
        bridge_status = str((bridge_report or {}).get("status") or "skipped")
        if bridge_status == "applied":
            seeded = (bridge_report.get("seed") or {}).get("seeded") or []
            attached = int((bridge_report.get("attach") or {}).get("attached") or 0)
            logger.info(
                "    ✅ Extension bridge seeded %d bound identit%s and attached %d unlinked range object(s)",
                len(seeded),
                "y" if len(seeded) == 1 else "ies",
                attached,
            )
        finalized, alignment_report = align_ordered_members_to_reference_content(
            finalized,
            ontosynthesis_ttl,
            runtime_ordered_member_profile,
            top_entity_uri=entity_uri,
        )
        alignment_status = str((alignment_report or {}).get("status") or "skipped")
        alignment_messages = (alignment_report or {}).get("messages") or []
        if alignment_status == "repaired":
            logger.info("    ✅ Ordered-member references aligned to canonical main TTL nodes")
        elif alignment_status == "no_action":
            logger.info("    ✅ Ordered-member references already aligned to canonical main TTL nodes")
        elif alignment_status == "skipped":
            logger.info("    ℹ️  Ordered-member reference alignment skipped")
        if alignment_messages:
            for message in alignment_messages:
                logger.info(f"    ↳ {message}")
        if alignment_status == "failed":
            raise RuntimeError(
                "Ordered-member reference alignment failed for extension output: "
                + "; ".join(str(msg) for msg in alignment_messages)
            )
        write_runtime_text(final_path, finalized)

        ordered_ok, ordered_report = enforce_ordered_member_integrity_file(
            ttl_path=final_path,
            runtime_profile=runtime_ordered_member_profile,
            top_entity_uri=entity_uri,
        )
        ordered_status = str((ordered_report or {}).get("status") or "skipped")
        ordered_messages = (ordered_report or {}).get("messages") or []
        if ordered_status == "repaired":
            logger.info("    ✅ Ordered-member integrity repaired for extension output")
        elif ordered_status == "no_action":
            logger.info("    ✅ Ordered-member integrity already satisfied for extension output")
        elif ordered_status == "skipped":
            logger.info("    ℹ️  Ordered-member integrity enforcement skipped for extension output")
        if ordered_messages:
            for message in ordered_messages:
                logger.info(f"    ↳ {message}")
        if not ordered_ok:
            raise RuntimeError(
                "Ordered-member integrity enforcement failed for extension output: "
                + "; ".join(str(msg) for msg in ordered_messages)
            )

        central_commit = _publish_central_memory_after_semantic_commit(
            ttl_path=final_path,
            ontology_name=ontology_name,
            doi_hash=doi_hash,
            entity_scope=_safe_name(entity_label),
        )
        logger.info(
            "    ✅ Pipeline-owned central-memory commit: %s",
            central_commit.get("status"),
        )
        _maybe_update_ontomops_mapping(final_path)
        logger.info(completion_message)
        return read_runtime_text(final_path)
    
    # Check if extension already exists
    if not force_rerun and runtime_path_exists(output_ttl_path):
        existing_content = _read_nonempty_ttl(output_ttl_path)
        if existing_content is not None:
            return _finalize_extension_output(
                existing_content,
                output_ttl_path,
                f"    ⏭️  Extension exists: {os.path.basename(output_ttl_path)}",
            )
        logger.warning(
            f"    ⚠️  Existing extension TTL is empty, regenerating: {os.path.basename(output_ttl_path)}"
        )

    persisted = _load_scoped_extension_ttl(
        doi_folder,
        ontology_name,
        entity_label,
        output_ttl_path=output_ttl_path,
        entity_uri=entity_uri,
    )
    if persisted is not None and not force_rerun:
        persisted_path, persisted_content = persisted
        return _finalize_extension_output(
            persisted_content,
            output_ttl_path,
            f"    ✅ Extension completed: {os.path.basename(output_ttl_path)} (this entity persist: {os.path.basename(persisted_path)})",
        )
    
    # Resolve DOI
    doi_us, doi_sl = resolve_doi_from_hash(doi_hash, data_dir)
    
    # Debug: Check what placeholders are in the template
    import re
    placeholders = set(re.findall(r'\{([^}]+)\}', extension_prompt_template))
    logger.info(f"    🔍 Found placeholders in template: {sorted(placeholders)}")
    
    # Format extension prompt - provide both old and new placeholder names for compatibility
    # Extension MCP servers persist under data/<case_id>/… and expect the
    # document identifier passed to init_memory to be the
    # pipeline hash (see write_global_state). Keep slash/underscore DOIs as
    # secondary placeholders for prompt context only.
    format_kwargs = {
        "doi": doi_hash,
        "hash": doi_hash,
        "doi_underscore": doi_us,
        "doi_slash": doi_sl,
        "entity_label": entity_label,
        "entity_uri": entity_uri,
        "ontosynthesis_a_box": ontosynthesis_ttl,  # Old key name
        "main_ontology_a_box": ontosynthesis_ttl,  # New key name (for updated templates)
        "paper_content": extracted_content,
        "iteration_hints": (
            "These are extracted hints for this iteration. Treat them as the "
            "primary source for KG building.\n"
            "Do not downgrade an explicit canonical field in these hints into a "
            "weaker fallback field.\n\n"
            "ExtractedHints:\n<<<\n"
            f"{extracted_content}\n"
            ">>>\n"
        ),
        "enrichment_targets": json.dumps(
            list(enrichment_targets or []),
            ensure_ascii=False,
            indent=2,
        ),
    }
    
    logger.info(f"    🔍 Formatting prompt with keys: {sorted(format_kwargs.keys())}")

    try:
        prompt = _fill_extension_prompt_template(extension_prompt_template, format_kwargs)
        if doi_sl and doi_sl != doi_hash and str(doi_sl).startswith("10."):
            prompt += (
                "\n\nDocument identity (CCDC):\n"
                f"- Document hash (use for init_memory / export_memory `doi` arg only): {doi_hash}\n"
                f"- Paper DOI for `search_ccdc_by_doi` only: {doi_sl}\n"
                f"- Pipeline underscore DOI (also accepted by CCDC): {doi_us}\n"
                "- Do NOT pass the document hash to `search_ccdc_by_doi`.\n"
            )
        if format_kwargs["enrichment_targets"] and format_kwargs["enrichment_targets"] != "[]":
            prompt += (
                "\n\n---- PIPELINE-INJECTED ENRICHMENT TARGETS: BEGIN ----\n"
                f"{format_kwargs['enrichment_targets']}\n"
                "---- PIPELINE-INJECTED ENRICHMENT TARGETS: END ----\n"
            )
        if extra_prompt:
            prompt += extra_prompt
        logger.info(f"    ✅ Prompt formatted successfully ({len(prompt)} chars)")
    except KeyError as e:
        missing_key = str(e).strip("'")
        logger.error(f"    ❌ Missing placeholder in template: {missing_key}")
        logger.error(f"    📋 Available placeholders in template: {sorted(placeholders)}")
        logger.error(f"    📋 Provided format keys: {sorted(format_kwargs.keys())}")
        raise
    except Exception as e:
        logger.error(f"    ❌ Failed to format prompt: {e}")
        logger.error(f"    📋 Template placeholders: {sorted(placeholders)}")
        raise
    
    # Save prompt (Windows long-path safe)
    prompt_path = prompt_file if os.path.isabs(prompt_file) else os.path.join(doi_folder, prompt_file)
    write_runtime_text(prompt_path, prompt)
    
    # Run agent with retry mechanism
    logger.info(f"    🤖 Running extension agent...")
    os.environ["TWA_EXTENSION_DATA_DIR"] = os.path.abspath(data_dir)
    os.environ["TWA_AGENTIC_DATA_DIR"] = os.path.abspath(data_dir)
    model_config = ModelConfig(temperature=0, top_p=1)
    agent = BaseAgent(
        model_name=agent_model,
        model_config=model_config,
        remote_model=True,
        mcp_tools=mcp_tools,
        mcp_set_name=mcp_set_name,
        excluded_tool_names=["materialize_hints"],
    )
    
    # Transport errors still retry inside retry_async_on_transport.
    # Agent/tool failures are not revised unless the caller opts out of norev.
    max_retries = kg_agent_attempt_limit(pipeline_config)
    retry_delays = [5, 10, 15]
    
    for attempt in range(max_retries):
        try:
            if attempt > 0:
                logger.info(f"    🔄 Retry attempt {attempt + 1}/{max_retries}")
            
            response, metadata = await retry_async_on_transport(
                lambda: agent.run(
                    prompt,
                    recursion_limit=recursion_limit,
                    required_initial_tool="init_memory",
                    required_initial_tool_args={
                        "doi": doi_hash,
                        "top_level_entity_name": entity_label,
                    },
                    required_final_tool="export_memory",
                    required_final_tool_args={
                        "doi": doi_hash,
                        "top_level_entity_name": entity_label,
                    },
                ),
                logger=logger,
                what=f"extension KG '{entity_label}'",
            )
            tool_activity = (metadata or {}).get("tool_activity", {}) or {}
            logger.info(
                "    🔧 Agent tool activity: planned=%s, executed=%s, tools=%s",
                tool_activity.get("planned_tool_call_count", 0),
                tool_activity.get("tool_message_count", 0),
                tool_activity.get("executed_tool_name_set", []),
            )
            
            if runtime_path_exists(output_ttl_path):
                direct_content = _read_nonempty_ttl(output_ttl_path)
                if direct_content is not None:
                    return _finalize_extension_output(
                        direct_content,
                        output_ttl_path,
                        f"    ✅ Extension completed: {os.path.basename(output_ttl_path)}",
                    )
                logger.warning(
                    f"    ⚠️  Agent wrote an empty TTL, continuing fallback checks: {os.path.basename(output_ttl_path)}"
                )
            persisted = _load_scoped_extension_ttl(
                doi_folder,
                ontology_name,
                entity_label,
                output_ttl_path=output_ttl_path,
                entity_uri=entity_uri,
            )
            if persisted is not None:
                persisted_path, persisted_content = persisted
                return _finalize_extension_output(
                    persisted_content,
                    output_ttl_path,
                    f"    ✅ Extension completed: {os.path.basename(output_ttl_path)} (this entity persist: {os.path.basename(persisted_path)})",
                )
            
            raise RuntimeError(
                f"Extension agent did not produce a TTL for '{entity_label}'"
            )
        
        except Exception as e:
            if is_llm_transport_error(e):
                raise
            error_msg = str(e)
            logger.error(f"    ❌ Agent execution failed (attempt {attempt + 1}/{max_retries}): {error_msg}")
            
            if attempt < max_retries - 1:
                delay = retry_delays[attempt]
                logger.info(f"    ⏳ Waiting {delay}s before retry...")
                await asyncio.sleep(delay)
            else:
                logger.error(f"    ❌ All {max_retries} attempts failed for extension agent")
                raise


def _extension_tbox_path(
    *,
    ontology_name: str,
    meta_cfg: Optional[dict],
    project_root: str,
    iteration: Optional[dict] = None,
) -> Optional[str]:
    raw_tbox = ""
    for ext_cfg in ((meta_cfg or {}).get("ontologies", {}).get("extensions", []) or []):
        if str(ext_cfg.get("name") or "").strip() == str(ontology_name or "").strip():
            raw_tbox = str(ext_cfg.get("ttl_file") or "").strip()
            break
    if not raw_tbox:
        raw_tbox = str(((iteration or {}).get("inputs") or {}).get("tbox_path") or "").strip()
    if not raw_tbox:
        return None
    path = Path(raw_tbox)
    if not path.is_absolute():
        path = Path(project_root) / raw_tbox
    return str(path) if path.is_file() else None


def _extension_publish_contract(
    *,
    ontology_name: str,
    tbox_path: Optional[str],
) -> dict:
    if not tbox_path:
        return {}
    try:
        return build_ontology_publish_contract_from_tbox(
            tbox_path,
            ontology_name=ontology_name,
            configured_ttl_file=tbox_path,
        )
    except Exception as exc:
        logger.warning(
            "    ⚠️  Failed to build extension publish contract for %s: %s",
            ontology_name,
            exc,
        )
        return {}


async def _revise_extension_extraction(
    *,
    doi_hash: str,
    entity_label: str,
    entity_uri: str,
    iteration: dict,
    extraction_file: str,
    extraction_lookup: list[str],
    extraction_prompt_file: str,
    tbox_path: Optional[str],
    project_root: str,
    data_dir: str,
    violations: list[dict],
) -> bool:
    from src.pipelines.extensions_extractions.extract import load_tbox, run_extraction
    from src.utils.extraction_models import get_extraction_model

    stitched_path = os.path.join(data_dir, doi_hash, f"{doi_hash}_stitched.md")
    if not os.path.exists(stitched_path):
        logger.error("    ❌ Cannot revise extension hints: stitched paper missing")
        return False
    extraction_prompt_path = str(iteration.get("extraction_prompt") or "").strip()
    extraction_prompt_template = load_prompt(extraction_prompt_path, project_root)
    if not extraction_prompt_template:
        logger.error("    ❌ Cannot revise extension hints: extraction prompt missing")
        return False
    try:
        paper_content = Path(stitched_path).read_text(encoding="utf-8")
    except Exception as exc:
        logger.error("    ❌ Cannot revise extension hints: failed to read paper: %s", exc)
        return False
    tbox_content = load_tbox(tbox_path) if tbox_path else ""
    model_name = get_extraction_model(iteration.get("model_config_key") or "")
    await run_extraction(
        doi_hash=doi_hash,
        entity_label=entity_label,
        entity_uri=entity_uri,
        paper_content=paper_content,
        tbox_content=tbox_content,
        extraction_prompt_template=extraction_prompt_template,
        model_name=model_name,
        output_file=extraction_file,
        prompt_file=extraction_prompt_file,
        data_dir=data_dir,
        output_lookup=extraction_lookup,
        revision_feedback=json.dumps(
            {
                "schema_version": "kg-hint-contract-revision.v1",
                "violations": violations,
            },
            indent=2,
            ensure_ascii=False,
        ),
        force=True,
    )
    return True


async def process_extension_kg(
    ontology_name: str,
    doi_hash: str,
    entity: Dict,
    config: Dict,
    data_dir: str = "data",
    project_root: str = ".",
    test_mcp_config: str = None,
    main_ontology_name: str = "ontosynthesis",
    meta_cfg: Optional[dict] = None,
    pipeline_config: Optional[dict] = None,
):
    """Process KG building for a single extension entity."""
    pipeline_config = ensure_kg_norev(pipeline_config, default=True)
    entity_label = entity.get("label", "")
    entity_uri = entity.get("uri", "")
    safe = _safe_name(entity_label)
    
    logger.info(f"  🔄 {ontology_name.upper()}: {entity_label}")
    
    # Load iteration config
    logger.info(f"    🔍 Loading iteration config...")
    logger.info(f"    📋 Config keys: {list(config.keys())}")
    if "iterations" not in config:
        logger.error(f"    📋 Available keys: {list(config.keys())}")
        _fail_extension_kg("No 'iterations' key in config")
    
    if not config["iterations"]:
        _fail_extension_kg("Iterations list is empty")
    
    iteration = config["iterations"][0]  # Extensions only have one iteration
    logger.info(f"    ✅ Loaded iteration config")
    logger.info(f"    📋 Iteration keys: {list(iteration.keys())}")
    
    # Check for required keys
    if "outputs" not in iteration:
        logger.error(f"    📋 Available keys: {list(iteration.keys())}")
        _fail_extension_kg("No 'outputs' key in iteration config")
    
    logger.info(f"    📋 Outputs keys: {list(iteration['outputs'].keys())}")
    
    # Note: extension iterations.json may not have 'extraction_file' - we'll use standard path
    if "extraction_file" in iteration["outputs"]:
        logger.info(f"    ✅ Found 'extraction_file' in config")
    else:
        logger.info(f"    ℹ️  No 'extraction_file' in config - will use standard extension path")
    
    # IMPORTANT:
    # Do NOT override the extension MCP config with the "test main-ontology MCP" config.
    # `test_mcp_config.json` is created to point `llm_created_mcp` at the generated main ontology server,
    # but extension agents require `mops_extension` / `ontospecies_extension` servers from `extension.json`.
    # Overriding here would remove those servers and cause:
    #   "Couldn't find a server with name 'mops_extension', expected one of '[]'"
    
    # Load entity-specific OntoSynthesis TTL
    logger.info(f"    🔍 Loading main ontology TTL for entity: {safe}")
    entity_ttl = load_entity_ttl(
        doi_hash=doi_hash,
        entity_safe=safe,
        entity_uri=entity_uri,
        data_dir=data_dir,
        test_mode=test_mcp_config is not None,
        ontology_name=main_ontology_name,
        meta_cfg=meta_cfg,
    )
    if not entity_ttl:
        _fail_extension_kg(f"Failed to load main ontology TTL for {entity_label}")
    logger.info(f"    ✅ Loaded main ontology TTL ({len(entity_ttl)} chars)")
    enrichment_targets = resolve_enrichment_targets(
        ontology_name=ontology_name,
        entity_uri=entity_uri,
        main_ontology_ttl=entity_ttl,
        meta_cfg=meta_cfg or load_meta_task_config(),
        project_root=project_root,
    )
    logger.info(
        "    ✅ Resolved deterministic enrichment target(s): %s",
        [item["target_iri"] for item in enrichment_targets],
    )
    
    # Load extracted content. Accept the capped canonical name and any
    # legacy long filename written before the Windows path bound existed.
    doi_folder = os.path.join(data_dir, doi_hash)
    extraction_templates = []
    if "outputs" in iteration and "extraction_file" in iteration["outputs"]:
        extraction_templates.append(iteration["outputs"]["extraction_file"])
    extraction_templates.append(
        f"mcp_run_{ontology_name}/extraction_{{entity_safe}}.txt"
    )
    extraction_paths: list[str] = []
    extraction_write_path = ""
    for template in extraction_templates:
        write_path, lookup = resolve_extension_artifact(
            doi_folder, template, entity_label, entity_uri=entity_uri
        )
        if not extraction_write_path:
            extraction_write_path = write_path
        extraction_paths.extend(lookup)
        recovered = find_existing_extension_artifact(
            doi_folder, template, entity_label, entity_uri=entity_uri
        )
        if recovered:
            extraction_paths.append(recovered)

    extraction_path = first_existing_runtime_path(extraction_paths)
    
    if not extraction_path:
        logger.error(f"  ❌ Extraction file not found. Tried:")
        for path in extraction_paths:
            logger.error(f"      - {path}")
            parent = os.path.dirname(path)
            if runtime_path_exists(parent):
                logger.error(
                    f"        📋 Files in directory: {os.listdir(windows_fs_path(parent))}"
                )
        _fail_extension_kg(f"Extraction file not found for {entity_label}")
    
    logger.info(f"    ✅ Found extraction file: {os.path.basename(extraction_path)}")
    try:
        extracted_content = read_runtime_text(extraction_path)
        logger.info(f"    ✅ Loaded extraction content ({len(extracted_content)} chars)")
    except Exception as e:
        logger.error(f"  ❌ Failed to load extraction: {e}")
        import traceback
        logger.error(traceback.format_exc())
        _fail_extension_kg(f"Failed to load extraction for {entity_label}: {e}")

    tbox_path = _extension_tbox_path(
        ontology_name=ontology_name,
        meta_cfg=meta_cfg,
        project_root=project_root,
        iteration=iteration,
    )
    ontology_contract = _extension_publish_contract(
        ontology_name=ontology_name,
        tbox_path=tbox_path,
    )
    hint_revision_max, structural_retry_limit = revision_attempt_limits(
        pipeline_config
    )
    hint_violations = collect_hint_violations(
        extracted_content,
        ontology_contract,
        iteration=int(iteration.get("iteration_number") or 0) or None,
    )
    revision_attempt = 0
    while hint_violations and revision_attempt < hint_revision_max:
        revision_attempt += 1
        feedback_path = os.path.join(
            doi_folder,
            "kg_hint_feedback",
            ontology_name,
            safe,
            f"iteration_{iteration.get('iteration_number') or 1}_attempt_{revision_attempt}.json",
        )
        _write_json_atomic(
            feedback_path,
            {
                "schema_version": "kg-hint-contract-feedback.v1",
                "entity_scope": safe,
                "entity_label": entity_label,
                "entity_uri": entity_uri,
                "ontology_name": ontology_name,
                "iteration": iteration.get("iteration_number"),
                "revision_attempt": revision_attempt,
                "violations": hint_violations,
            },
        )
        logger.warning(
            "    🔁 Revising %s extraction hints (attempt %d/%d); feedback=%s",
            ontology_name,
            revision_attempt,
            hint_revision_max,
            os.path.relpath(feedback_path, doi_folder),
        )
        extraction_prompt_template = iteration.get("outputs", {}).get(
            "extraction_prompt_file", "prompts/extraction_{entity_safe}.md"
        )
        extraction_prompt_file, _prompt_lookup = resolve_extension_artifact(
            doi_folder, extraction_prompt_template, entity_label, entity_uri=entity_uri
        )
        revised = await _revise_extension_extraction(
            doi_hash=doi_hash,
            entity_label=entity_label,
            entity_uri=entity_uri,
            iteration=iteration,
            extraction_file=extraction_write_path or extraction_path,
            extraction_lookup=extraction_paths,
            extraction_prompt_file=extraction_prompt_file,
            tbox_path=tbox_path,
            project_root=project_root,
            data_dir=data_dir,
            violations=hint_violations,
        )
        if not revised:
            break
        extracted_content = read_runtime_text(extraction_path)
        hint_violations = collect_hint_violations(
            extracted_content,
            ontology_contract,
            iteration=int(iteration.get("iteration_number") or 0) or None,
        )
    if hint_violations:
        _fail_extension_kg(
            "Extension extraction hints violate immutable KG relation "
            f"contracts after {revision_attempt} revision attempt(s): "
            f"{json.dumps(hint_violations, ensure_ascii=False)}"
        )
    
    # Extension agent step
    # Construct output file paths - use config if available, otherwise use standard extension paths
    prompt_template = iteration.get("outputs", {}).get(
        "extension_prompt_file",
        f"prompts/{ontology_name}_kg_building/{{entity_safe}}.md",
    )
    extension_prompt_file, _prompt_lookup = resolve_extension_artifact(
        doi_folder, prompt_template, entity_label, entity_uri=entity_uri
    )
    
    if "output_ttl" in iteration.get("outputs", {}):
        output_ttl = iteration["outputs"]["output_ttl"]
        for placeholder in ("{entity_safe}", "{entity_name}", "{entity_slugified}"):
            output_ttl = output_ttl.replace(placeholder, safe)
    else:
        # Standard extension output TTL path
        output_ttl = f"{ontology_name}_extension_{safe}.ttl"
        logger.info(f"    ℹ️  Using standard extension output TTL path: {output_ttl}")
    
    # Generated KG_BUILDING_*.md stays on disk for provenance. The agent task
    # is the same thin semantic-surface envelope used by main KG: bindings +
    # ledger only. MOP derivation is a separate pipeline and is unchanged.
    extension_prompt_path = iteration.get("kg_building_prompt") or iteration.get(
        "extension_prompt"
    )
    extension_prompt_template = build_mcp_semantic_surface_task_prompt()
    logger.info(
        "    ℹ️  Extension KG uses semantic-surface task prompt "
        "(generated kg_building_prompt is not injected)%s",
        f"; unused file={extension_prompt_path}" if extension_prompt_path else "",
    )
    
    # Write global state for MCP server (using hash, not DOI)
    # Note: DOI is only resolved for prompt formatting, not for global state
    logger.info(f"    🔍 Resolving DOI from hash: {doi_hash}")
    doi_us, doi_sl = resolve_doi_from_hash(doi_hash, data_dir)
    logger.info(f"    ✅ Resolved DOI - underscore: {doi_us}, slash: {doi_sl}")
    write_global_state(
        ontology_name,
        doi_hash,
        entity_label,
        entity_uri,
        data_dir,
        enrichment_targets=enrichment_targets,
    )
    
    # Get optional parameters with defaults
    recursion_limit = iteration.get("recursion_limit", 50)  # Default recursion limit
    mcp_tools = iteration.get("mcp_tools", [])
    mcp_set_name = iteration.get("mcp_set_name", f"{ontology_name}_mcp")
    extension_meta_cfg = {}
    for ext_cfg in ((meta_cfg or {}).get("ontologies", {}).get("extensions", []) or []):
        if str(ext_cfg.get("name") or "").strip() == ontology_name:
            extension_meta_cfg = ext_cfg or {}
            break
    agent_model = (
        extension_meta_cfg.get("agent_model")
        or iteration.get("agent_model")
        or "gpt-4o"
    )

    if mcp_tools and "ccdc" in set(mcp_tools):
        if _ccdc_tool_is_healthy():
            logger.info(f"    ✅ Keeping 'ccdc' enabled for extension MCP tools: {mcp_tools}")
        else:
            logger.warning(f"    ⚠️  Dropping unhealthy 'ccdc' from extension MCP tools: {mcp_tools}")
            mcp_tools = [t for t in mcp_tools if t != "ccdc"]
    
    logger.info(f"    📋 Agent config: model={agent_model}, recursion_limit={recursion_limit}, mcp_set={mcp_set_name}")
    
    agent_kwargs = dict(
        doi_hash=doi_hash,
        entity_label=entity_label,
        entity_uri=entity_uri,
        ontosynthesis_ttl=entity_ttl,
        extracted_content=extracted_content,
        extension_prompt_template=extension_prompt_template,
        mcp_tools=mcp_tools,
        mcp_set_name=mcp_set_name,
        agent_model=agent_model,
        recursion_limit=recursion_limit,
        prompt_file=extension_prompt_file,
        output_ttl_name=output_ttl,
        data_dir=data_dir,
        ontology_name=ontology_name,
        meta_cfg=meta_cfg,
        project_root=project_root,
        enrichment_targets=enrichment_targets,
        pipeline_config=pipeline_config,
    )
    await run_extension_agent(**agent_kwargs)

    output_ttl_path = bounded_runtime_file(os.path.join(doi_folder, output_ttl))
    if ontology_contract and structural_retry_limit and runtime_path_exists(output_ttl_path):
        struct_msgs = collect_extension_structural_messages(
            ttl_path=output_ttl_path,
            entity_uri=entity_uri,
            entity_label=entity_label,
            ontology_contract=ontology_contract,
            enrichment_targets=enrichment_targets,
            hints_content=extracted_content,
        )
        feedback_scope = f"{ontology_name}/{safe}"
        feedback_attempt_start = _next_post_publish_feedback_attempt(
            doi_folder=doi_folder,
            entity_scope=feedback_scope,
        )
        for repair_attempt in range(1, structural_retry_limit + 1):
            if not struct_msgs:
                break
            feedback_attempt = feedback_attempt_start + repair_attempt - 1
            failed_messages = list(struct_msgs)
            repair_context = {
                "iteration": iteration.get("iteration_number"),
                "prompt_path": extension_prompt_path,
                "owned_properties": list(
                    (iteration.get("responsibilities") or {}).get("object_properties")
                    or []
                ),
            }
            feedback_path = _write_post_publish_feedback(
                doi_folder=doi_folder,
                entity_scope=feedback_scope,
                entity_label=entity_label,
                entity_uri=entity_uri,
                attempt=feedback_attempt,
                messages=failed_messages,
                required_links=list(ontology_contract.get("required_links") or []),
                repair_context=repair_context,
                retry_status="pending",
            )
            logger.warning(
                "    ♻️  Routing %s post-publish structural failure to the "
                "retained-memory extension agent (attempt %d/%d); feedback=%s",
                ontology_name,
                repair_attempt,
                structural_retry_limit,
                os.path.relpath(feedback_path, doi_folder),
            )
            try:
                await run_extension_agent(
                    **agent_kwargs,
                    force_rerun=True,
                    extra_prompt=_post_publish_repair_prompt(
                        messages=failed_messages,
                        required_links=list(
                            ontology_contract.get("required_links") or []
                        ),
                    ),
                )
                struct_msgs = collect_extension_structural_messages(
                    ttl_path=output_ttl_path,
                    entity_uri=entity_uri,
                    entity_label=entity_label,
                    ontology_contract=ontology_contract,
                    enrichment_targets=enrichment_targets,
                    hints_content=extracted_content,
                )
                _write_post_publish_feedback(
                    doi_folder=doi_folder,
                    entity_scope=feedback_scope,
                    entity_label=entity_label,
                    entity_uri=entity_uri,
                    attempt=feedback_attempt,
                    messages=failed_messages,
                    required_links=list(ontology_contract.get("required_links") or []),
                    repair_context=repair_context,
                    retry_status="resolved" if not struct_msgs else "unresolved",
                    post_retry_messages=struct_msgs,
                )
                if not struct_msgs:
                    logger.info(
                        "    ✅ Extension agent resolved post-publish structural "
                        "validation on attempt %d",
                        repair_attempt,
                    )
                    break
                logger.error(
                    "    ❌ Extension post-publish structural retry %d remains invalid:",
                    repair_attempt,
                )
                for msg in struct_msgs:
                    logger.error(f"       - {msg}")
            except Exception as exc:
                _write_post_publish_feedback(
                    doi_folder=doi_folder,
                    entity_scope=feedback_scope,
                    entity_label=entity_label,
                    entity_uri=entity_uri,
                    attempt=feedback_attempt,
                    messages=failed_messages,
                    required_links=list(ontology_contract.get("required_links") or []),
                    repair_context=repair_context,
                    retry_status="agent_retry_failed",
                    post_retry_messages=[str(exc)],
                )
                logger.error(
                    "    ❌ Extension post-publish repair attempt %d failed: %s",
                    repair_attempt,
                    exc,
                )
                break
    
    logger.info(f"  ✅ KG building completed for {entity_label}")
    return True


def run_step(doi_hash: str, config: dict) -> bool:
    """
    Main Extensions KG Building step: Build KG for OntoMOPs and OntoSpecies extensions.
    
    Args:
        doi_hash: DOI hash for the paper
        config: Pipeline configuration dictionary
        
    Returns:
        True if KG building completed successfully
    """
    # Extract config parameters
    data_dir = config.get("data_dir", "data")
    project_root = config.get("project_root", ".")
    os.environ["TWA_AGENTIC_DATA_DIR"] = os.path.abspath(str(data_dir))
    os.environ["TWA_CENTRAL_MEMORY_DIR"] = os.path.join(
        os.path.abspath(str(data_dir)), "central_memory"
    )
    config = ensure_kg_norev(config, default=True)
    
    logger.info(f"🏗️  Starting extensions KG building for DOI: {doi_hash}")
    
    doi_folder = os.path.join(data_dir, doi_hash)
    if not os.path.exists(doi_folder):
        logger.error(f"DOI folder not found: {doi_folder}")
        return False
    
    # Check if step is already completed
    marker_file = os.path.join(doi_folder, ".extensions_kg_building_done")
    if os.path.exists(marker_file):
        logger.info(f"  ⏭️  Extensions KG building already completed (marker exists)")
        return True
    
    # Load meta task configuration
    meta_config_path = config.get("meta_task_config") or os.path.join(project_root, "configs/meta_task/meta_task_config.json")
    if not os.path.exists(meta_config_path):
        logger.error(f"Meta task config not found: {meta_config_path}")
        return False
    
    try:
        with open(meta_config_path, 'r', encoding='utf-8') as f:
            meta_config = json.load(f)
    except Exception as e:
        logger.error(f"Failed to load meta task config: {e}")
        return False

    # Determine main ontology name from meta config (do not hardcode).
    main_ontology_name = (meta_config.get("ontologies", {}).get("main", {}) or {}).get("name") or "ontosynthesis"
    try:
        main_ontology_name = str(main_ontology_name).strip() or "ontosynthesis"
    except Exception:
        main_ontology_name = "ontosynthesis"
    
    # Get extension ontologies
    extensions = meta_config.get("ontologies", {}).get("extensions", [])
    if not extensions:
        logger.warning("No extension ontologies configured")
        return True
    
    top_entities = load_extension_synthesis_queue(
        doi_folder,
        ontology_name=main_ontology_name or get_main_ontology_name(meta_config),
        project_root=project_root,
        meta_cfg=meta_config,
    )
    if not top_entities:
        logger.warning("No top entities found")
        return False

    logger.info(
        "Found %s top entities from published TTL / identity lock",
        len(top_entities),
    )
    
    # Process all extensions and entities sequentially using a single event loop
    async def process_all_extensions() -> bool:
        """Process all extensions and entities sequentially in a single event loop."""
        planned = 0
        completed = 0
        failed = 0
        # Process each extension ontology
        for extension in extensions:
            ontology_name = extension.get("name")
            logger.info(f"\n  📚 Extension: {ontology_name}")
            
            # Load iteration config for this ontology from the active generated-artifact root first.
            artifact_roots = []
            override_root = os.environ.get("TWA_GENERATED_ARTIFACT_ROOT", "").strip()
            if override_root:
                artifact_roots.append(override_root)
            artifact_roots.extend(["ai_generated_contents_candidate", "ai_generated_contents"])
            iterations_config_paths = [
                os.path.join(project_root, artifact_root, "iterations", ontology_name, "iterations.json")
                for artifact_root in dict.fromkeys(artifact_roots)
            ]
            
            iterations_config_path = None
            for path in iterations_config_paths:
                if os.path.exists(path):
                    iterations_config_path = path
                    logger.info(f"  ✅ Found iterations config: {path}")
                    break
            
            if not iterations_config_path:
                logger.error(f"  ❌ Iterations config not found. Tried:")
                for path in iterations_config_paths:
                    logger.error(f"      - {path}")
                planned += len(top_entities)
                failed += len(top_entities)
                continue
            
            try:
                with open(iterations_config_path, 'r', encoding='utf-8') as f:
                    iterations_config = json.load(f)
            except Exception as e:
                logger.error(f"  ❌ Failed to load iterations config: {e}")
                planned += len(top_entities)
                failed += len(top_entities)
                continue
            
            # Process each entity STRICTLY SEQUENTIALLY
            # This ensures global state is set correctly for each entity
            for i, entity in enumerate(top_entities):
                entity_label = entity.get("label", "")
                planned += 1
                logger.info(f"\n  Entity {i+1}/{len(top_entities)}: {entity_label}")
                
                try:
                    # Await each entity sequentially - no parallel processing
                    await process_extension_kg(
                        ontology_name=ontology_name,
                        doi_hash=doi_hash,
                        entity=entity,
                        config=iterations_config,
                        data_dir=data_dir,
                        project_root=project_root,
                        test_mcp_config=config.get("test_mcp_config"),
                        main_ontology_name=main_ontology_name,
                        meta_cfg=meta_config,
                        pipeline_config=config,
                    )
                    logger.info(f"  ✅ Completed entity {i+1}/{len(top_entities)}: {entity_label}")
                    completed += 1
                except Exception as e:
                    logger.error(f"  ❌ KG building failed for '{entity_label}': {e}")
                    import traceback
                    logger.error(f"  Traceback: {traceback.format_exc()}")
                    failed += 1
                    continue
        if failed or completed != planned or planned == 0:
            logger.warning(
                "  ⚠️  Extension KG incomplete: completed=%s failed=%s planned=%s",
                completed,
                failed,
                planned,
            )
            return False
        return True
    
    # Run all processing in a single event loop
    try:
        success = asyncio.run(process_all_extensions())
    except Exception as e:
        logger.error(f"❌ Failed to process extensions: {e}")
        import traceback
        logger.error(f"Traceback: {traceback.format_exc()}")
        return False
    if not success:
        logger.error(
            "❌ Extensions KG building incomplete; completion marker not written"
        )
        return False
    
    # Create completion marker
    try:
        with open(marker_file, 'w') as f:
            f.write("completed\n")
        logger.info(f"  📌 Created completion marker")
    except Exception as e:
        logger.warning(f"  ⚠️  Failed to create completion marker: {e}")
    
    logger.info(f"✅ Extensions KG building completed for DOI: {doi_hash}")
    return True


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python -m src.pipelines.extensions_kg_building.build <doi_hash>")
        sys.exit(1)
    
    # Create config dict for standalone usage
    config = {
        "data_dir": "data",
        "project_root": "."
    }
    
    success = run_step(sys.argv[1], config)
    sys.exit(0 if success else 1)

