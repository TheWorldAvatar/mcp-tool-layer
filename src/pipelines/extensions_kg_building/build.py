"""
Extensions KG Building Module

Handles agent-based A-Box building for extension ontologies (OntoMOPs and OntoSpecies).
"""

import os
import json
import re
import glob
import asyncio
import logging
import hashlib
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import quote
from filelock import FileLock
from rdflib import Graph, Namespace, URIRef, Literal
from rdflib.namespace import RDF, RDFS

from models.BaseAgent import BaseAgent
from models.ModelConfig import ModelConfig
from src.pipelines.utils.ttl_publisher import get_output_naming_config, load_meta_task_config
from src.pipelines.utils.ordered_member_integrity import (
    align_ordered_members_to_reference_content,
    enforce_ordered_member_integrity_file,
    load_all_runtime_ordered_member_profiles,
)

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)


def _safe_name(label: str) -> str:
    """Convert entity label to safe filename."""
    return (
        (label or "entity")
        .replace(" ", "_")
        .replace("/", "_")
        .replace("\\", "_")
        .replace(":", "_")
        .replace("?", "_")
        .replace("*", "_")
        .replace("<", "_")
        .replace(">", "_")
        .replace("|", "_")
        .replace('"', "_")
        .replace("'", "_")
    )


def _entity_name_variants(name: str) -> List[str]:
    """Build filename-safe variants for entity labels with punctuation differences."""
    text = str(name or "").strip()
    if not text:
        return []

    variants: List[str] = []
    seen: set[str] = set()

    def _add(value: str) -> None:
        value = str(value or "").strip()
        if value and value not in seen:
            seen.add(value)
            variants.append(value)

    punct_variants = [text, text.lower()]
    punct_tokens = ["·", "•", "∙", "⋅", "●", "–", "—", "−"]

    for value in list(punct_variants):
        _add(value)
        _add(value.replace("_", "-"))
        _add(value.replace("-", "_"))
        for token in punct_tokens:
            _add(value.replace(token, "_"))
            _add(value.replace(token, "-"))
            _add(value.replace(token, ""))

    normalized = unicodedata.normalize("NFKC", text)
    if normalized != text:
        _add(normalized)
        _add(normalized.lower())

    transliterated_chars: List[str] = []
    for char in normalized:
        if ord(char) < 128:
            transliterated_chars.append(char)
            continue
        try:
            char_name = unicodedata.name(char)
        except ValueError:
            transliterated_chars.append("_")
            continue
        if char_name.startswith("GREEK ") and " LETTER " in char_name:
            transliterated_chars.append(char_name.rsplit(" LETTER ", 1)[-1].lower())
        else:
            transliterated_chars.append("_")
    transliterated = "".join(transliterated_chars)
    if transliterated != normalized:
        _add(transliterated)
        _add(transliterated.lower())
        _add(transliterated.replace("_", "-"))
        _add(transliterated.replace("-", "_"))

    return variants


def _looks_like_valid_extension_ttl(content: str, ontology_name: Optional[str]) -> bool:
    """Reject placeholder/shared-memory TTLs that do not contain extension ontology facts."""
    text = str(content or "")
    if not text.strip():
        return False

    markers = {
        "ontomops": (
            "https://www.theworldavatar.com/kg/OntoMOPs/",
            "https://www.theworldavatar.com/kg/ontomops/",
            "MetalOrganicPolyhedron",
            "hasCCDCNumber",
            "isBuiltFrom",
            "hasMOPFormula",
        ),
        "ontospecies": (
            "https://www.theworldavatar.com/kg/OntoSpecies/",
            "http://www.theworldavatar.com/ontology/ontospecies/OntoSpecies.owl#",
            "Species",
            "hasMolecularFormula",
            "hasInChI",
            "Characterisation",
            "hasCCDCNumberValue",
        ),
    }
    required_markers = markers.get(str(ontology_name or "").strip().lower())
    if not required_markers:
        return True
    return any(marker in text for marker in required_markers)


def _read_valid_extension_ttl(path: str, ontology_name: Optional[str]) -> Optional[str]:
    """Read an extension TTL only when it contains ontology-specific content."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception:
        return None
    return content if _looks_like_valid_extension_ttl(content, ontology_name) else None


def _repair_ontospecies_scoped_anchor(
    content: str,
    *,
    entity_label: str,
    entity_uri: str,
    ontology_name: Optional[str],
) -> str:
    """Ensure OntoSpecies output keeps the scoped ChemicalSynthesis -> Species anchor."""
    if str(ontology_name or "").strip().lower() != "ontospecies":
        return content
    if not str(content or "").strip() or not str(entity_uri or "").strip():
        return content

    try:
        graph = Graph()
        graph.parse(data=content, format="turtle")
    except Exception:
        return content

    ontospecies = Namespace("http://www.theworldavatar.com/ontology/ontospecies/OntoSpecies.owl#")
    ontosyn = Namespace("https://www.theworldavatar.com/kg/OntoSyn/")
    top_entity = URIRef(str(entity_uri).strip())
    graph.bind("ontospecies", ontospecies)
    graph.bind("ontosyn", ontosyn)

    graph.add((top_entity, RDF.type, ontosyn.ChemicalSynthesis))
    scoped_label = str(entity_label or "").strip()
    if scoped_label:
        graph.remove((top_entity, RDFS.label, None))
        graph.add((top_entity, RDFS.label, Literal(scoped_label)))

    # Entity-scoped OntoSpecies TTLs must not keep foreign ChemicalSynthesis shells from
    # neighboring runs. Preserve any Species anchor facts, then prune the extra shells.
    foreign_synths = []
    for subject in set(graph.subjects()):
        if not isinstance(subject, URIRef) or subject == top_entity:
            continue
        if (subject, RDF.type, ontosyn.ChemicalSynthesis) in graph or "ChemicalSynthesis/" in str(subject):
            foreign_synths.append(subject)

    for foreign in foreign_synths:
        for obj in list(graph.objects(foreign, ontosyn.hasChemicalOutput)):
            if (obj, RDF.type, ontospecies.Species) in graph:
                graph.add((top_entity, ontosyn.hasChemicalOutput, obj))
        for triple in list(graph.triples((foreign, None, None))):
            graph.remove(triple)
        for triple in list(graph.triples((None, None, foreign))):
            graph.remove(triple)

    existing_outputs = [
        obj for obj in graph.objects(top_entity, ontosyn.hasChemicalOutput)
        if (obj, RDF.type, ontospecies.Species) in graph
    ]
    if existing_outputs:
        return graph.serialize(format="turtle")

    scoped_name = re.sub(r"\s+synthesis\s*$", "", str(entity_label or "").strip(), flags=re.IGNORECASE).strip()
    species_nodes = list(graph.subjects(RDF.type, ontospecies.Species))
    preferred = None
    for species in species_nodes:
        labels = {str(v).strip() for v in graph.objects(species, RDFS.label)}
        labels.update(str(v).strip() for v in graph.objects(species, ontospecies.hasProductName))
        if scoped_name and scoped_name in labels:
            preferred = species
            break

    if preferred is None and len(species_nodes) == 1:
        preferred = species_nodes[0]

    if preferred is not None:
        graph.add((top_entity, ontosyn.hasChemicalOutput, preferred))

    return graph.serialize(format="turtle")


def _repair_ontomops_missing_ccdc(
    content: str,
    *,
    entity_label: str,
    ontology_name: Optional[str],
) -> str:
    """Fill missing ontomops:hasCCDCNumber using the local CCDC lookup when possible."""
    if str(ontology_name or "").strip().lower() != "ontomops":
        return content
    if not str(content or "").strip():
        return content

    try:
        graph = Graph()
        graph.parse(data=content, format="turtle")
    except Exception:
        return content

    ontomops = Namespace("https://www.theworldavatar.com/kg/ontomops/")
    graph.bind("ontomops", ontomops)

    try:
        from src.mcp_servers.ccdc.operations.wsl_ccdc import search_ccdc_by_mop_name
    except Exception:
        return content

    fallback_label = re.sub(r"^\s*Synthesis\s+of\s+", "", str(entity_label or "").strip(), flags=re.IGNORECASE).strip()
    changed = False
    for mop in graph.subjects(RDF.type, ontomops.MetalOrganicPolyhedron):
        if any(str(v).strip() for v in graph.objects(mop, ontomops.hasCCDCNumber)):
            continue
        labels = [str(v).strip() for v in graph.objects(mop, RDFS.label) if str(v).strip()]
        search_terms = labels + ([fallback_label] if fallback_label else [])
        ccdc_number = ""
        for term in search_terms:
            try:
                results = search_ccdc_by_mop_name(term, exact=False) or []
            except Exception:
                results = []
            if results:
                _, ccdc_number = results[0]
                if ccdc_number:
                    break
        if ccdc_number:
            graph.add((mop, ontomops.hasCCDCNumber, Literal(str(ccdc_number).strip())))
            changed = True

    return graph.serialize(format="turtle") if changed else content


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
    data_dir: str = "data",
    test_mode: bool = False,
    ontology_name: str = "ontosynthesis",
    meta_cfg: Optional[dict] = None,
) -> str:
    """
    Load entity-specific OntoSynthesis TTL file.
    
    In normal mode:
        - Looks for: output_{entity_safe}.ttl in doi_hash root
    
    In test mode:
        - Looks for: {entity_safe}.ttl in the configured published output dir (defaults to `{ontology_name}_output/`)
    """
    doi_folder = os.path.join(data_dir, doi_hash)

    # Prefer the "published" deterministic output location first (config-driven).
    # This avoids reliance on MCP server internal persistence conventions.
    published_dir = os.path.join(doi_folder, f"{ontology_name}_output")
    try:
        meta_cfg = meta_cfg or load_meta_task_config()
        naming = get_output_naming_config(meta_cfg=meta_cfg, ontology_name=ontology_name)
        published_dir = os.path.join(doi_folder, naming.output_dir)
        try:
            primary_name = naming.entity_ttl_pattern.format(entity_safe=entity_safe, ontology_name=ontology_name)
        except Exception:
            primary_name = f"{entity_safe}.ttl"

        published_candidates: List[str] = []
        for base in [primary_name, *_entity_name_variants(entity_safe), f"{entity_safe}.ttl"]:
            root, ext = os.path.splitext(base)
            if ext:
                published_candidates.extend(_entity_name_variants(base))
            else:
                published_candidates.extend([f"{variant}.ttl" for variant in _entity_name_variants(base)])
        for candidate in published_candidates:
            ttl_path = os.path.join(published_dir, candidate)
            if os.path.exists(ttl_path):
                with open(ttl_path, "r", encoding="utf-8") as f:
                    content = f.read()
                if test_mode:
                    logger.info(f"    📄 [TEST MODE] Loaded entity TTL from published output: {candidate}")
                else:
                    logger.info(f"    📄 Loaded entity TTL from published output: {candidate}")
                return content
    except Exception as e:
        logger.debug(f"    Published TTL lookup failed: {e}")
    
    # Normal mode: prefer the persisted MCP memory graph, then fall back to older conventions.
    for candidate in _entity_name_variants(entity_safe):
        memory_ttl = os.path.join(doi_folder, "memory", f"{candidate}.ttl")
        if os.path.exists(memory_ttl):
            try:
                with open(memory_ttl, "r", encoding="utf-8") as f:
                    content = f.read()
                logger.info(f"    📄 Loaded entity TTL from memory: {os.path.basename(memory_ttl)}")
                return content
            except Exception as e:
                logger.error(f"    ❌ Failed to read {memory_ttl}: {e}")

    # Next: try latest exported snapshot (export_memory default location)
    exports_dir = os.path.join(doi_folder, "exports")
    try:
        if os.path.isdir(exports_dir):
            entity_prefixes = {variant.lower() for variant in _entity_name_variants(entity_safe)}
            export_candidates = [
                os.path.join(exports_dir, f)
                for f in os.listdir(exports_dir)
                if f.lower().endswith(".ttl")
                and any(f.lower().startswith(prefix + "_") for prefix in entity_prefixes)
            ]
            if export_candidates:
                export_candidates.sort(key=lambda p: os.path.getmtime(p), reverse=True)
                latest = export_candidates[0]
                with open(latest, "r", encoding="utf-8") as f:
                    content = f.read()
                logger.info(f"    📄 Loaded entity TTL from exports: {os.path.basename(latest)}")
                return content
    except Exception as e:
        logger.warning(f"    ⚠️  Error scanning exports for entity TTL: {e}")

    # Backward-compat: Try multiple naming conventions in root
    candidates = [f"output_{variant}.ttl" for variant in _entity_name_variants(entity_safe)]
    for candidate in candidates:
        ttl_path = os.path.join(doi_folder, candidate)
        if os.path.exists(ttl_path):
            try:
                with open(ttl_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                logger.info(f"    📄 Loaded entity TTL: {candidate}")
                return content
            except Exception as e:
                logger.error(f"    ❌ Failed to read {candidate}: {e}")
                continue
    
    # Fallback: scan directory for matching files
    try:
        for fname in os.listdir(doi_folder):
            if fname.startswith("output_") and fname.endswith(".ttl"):
                inner = fname[len("output_"):-len(".ttl")]
                if inner.lower().replace("-", "_") == entity_safe.lower().replace("-", "_"):
                    ttl_path = os.path.join(doi_folder, fname)
                    with open(ttl_path, 'r', encoding='utf-8') as f:
                        content = f.read()
                    logger.info(f"    📄 Loaded entity TTL (matched): {fname}")
                    return content
    except Exception as e:
        logger.warning(f"    ⚠️  Error scanning for TTL files: {e}")
    
    if test_mode:
        logger.error(
            f"[TEST MODE] Entity TTL not found for {entity_safe} in {published_dir} or in memory/exports fallbacks"
        )
        return ""

    raise FileNotFoundError(f"Could not find OntoSynthesis TTL for entity {entity_safe}")


def resolve_doi_from_hash(doi_hash: str, data_dir: str = "data") -> tuple:
    """Return (pipeline_doi_with_underscore, slash_doi) for a given hash."""
    try:
        mapping_path = os.path.join(data_dir, "doi_to_hash.json")
        if not os.path.exists(mapping_path):
            logger.warning(f"DOI mapping file not found: {mapping_path}")
            return (doi_hash, doi_hash)
        
        with open(mapping_path, 'r', encoding='utf-8') as f:
            doi_to_hash = json.load(f)
        
        # Invert mapping
        hash_to_doi = {h: d for d, h in doi_to_hash.items()}
        
        if doi_hash not in hash_to_doi:
            logger.warning(f"Hash {doi_hash} not found in mapping")
            return (doi_hash, doi_hash)
        
        slash_doi = hash_to_doi[doi_hash]
        underscore_doi = slash_doi.replace("/", "_")
        
        return (underscore_doi, slash_doi)
    except Exception as e:
        logger.error(f"Error resolving DOI from hash: {e}")
        return (doi_hash, doi_hash)


def write_global_state(ontology_name: str, hash_value: str, entity_label: str, entity_uri: str, data_dir: str = "data"):
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
        "top_level_entity_iri": entity_uri
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
) -> str:
    """Run extension agent for a single entity."""
    doi_folder = os.path.join(data_dir, doi_hash)
    output_ttl_path = os.path.join(doi_folder, output_ttl_name)

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
            os.makedirs(out_dir, exist_ok=True)
            mapping_file = os.path.join(out_dir, "ontomops_output_mapping.json")
            mapping = {}
            if os.path.exists(mapping_file):
                try:
                    with open(mapping_file, "r", encoding="utf-8") as f:
                        mapping = json.load(f) or {}
                except Exception:
                    mapping = {}
            fn = os.path.basename(final_path)
            if entity_label:
                mapping[entity_label] = fn
            if entity_uri:
                mapping[entity_uri] = fn
            with open(mapping_file, "w", encoding="utf-8") as f:
                json.dump(mapping, f, indent=2, ensure_ascii=False)
        except Exception:
            return

    meta_cfg = meta_cfg or load_meta_task_config()
    runtime_ordered_member_profile = load_all_runtime_ordered_member_profiles(
        meta_cfg=meta_cfg,
        project_root=project_root,
    )

    def _finalize_extension_output(content: str, final_path: str, completion_message: str) -> str:
        finalized = _repair_ontospecies_scoped_anchor(
            content,
            entity_label=entity_label,
            entity_uri=entity_uri,
            ontology_name=ontology_name,
        )
        finalized = _repair_ontomops_missing_ccdc(
            finalized,
            entity_label=entity_label,
            ontology_name=ontology_name,
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
        os.makedirs(os.path.dirname(final_path), exist_ok=True)
        with open(final_path, "w", encoding="utf-8") as f:
            f.write(finalized)

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

        _maybe_update_ontomops_mapping(final_path)
        with open(final_path, "r", encoding="utf-8") as f:
            final_text = f.read()
        logger.info(completion_message)
        return final_text
    
    # Check if extension already exists
    if os.path.exists(output_ttl_path):
        existing_content = _read_valid_extension_ttl(output_ttl_path, ontology_name)
        if existing_content is not None:
            return _finalize_extension_output(
                existing_content,
                output_ttl_path,
                f"    ⏭️  Extension exists: {os.path.basename(output_ttl_path)}",
            )
        logger.warning(
            f"    ⚠️  Existing extension TTL is not a valid {ontology_name} graph, regenerating: {os.path.basename(output_ttl_path)}"
        )

    # If the extension MCP server already persisted an entity TTL under memory_<ontology_name>,
    # use it directly to avoid unnecessary agent reruns (and LLM costs).
    if ontology_name:
        try:
            import shutil
            safe_local = _safe_name(entity_label)
            mem_name_variants = []
            for value in [entity_label, safe_local]:
                mem_name_variants.extend(_entity_name_variants(value))
            mem_dir = os.path.join(doi_folder, f"memory_{ontology_name}")
            mem_candidates = [
                os.path.join(mem_dir, f"{candidate}.ttl")
                for candidate in mem_name_variants
            ]
            for mem_path in mem_candidates:
                if os.path.exists(mem_path):
                    content = _read_valid_extension_ttl(mem_path, ontology_name)
                    if content is None:
                        logger.warning(
                            f"    ⚠️  Ignoring invalid {ontology_name} memory TTL: {os.path.basename(mem_path)}"
                        )
                        continue
                    return _finalize_extension_output(
                        content,
                        output_ttl_path,
                        f"    ✅ Extension completed: {os.path.basename(output_ttl_path)} (copied from {os.path.basename(mem_dir)})",
                    )

            # Do not satisfy extension outputs from the main ontology's shared
            # memory/exports locations: those graphs can contain valid-looking
            # extension markers from prior contaminated runs.
        except Exception as e:
            logger.debug(f"    Pre-run memory TTL shortcut failed: {e}")
    
    # Resolve DOI
    doi_us, doi_sl = resolve_doi_from_hash(doi_hash, data_dir)
    
    # Debug: Check what placeholders are in the template
    import re
    placeholders = set(re.findall(r'\{([^}]+)\}', extension_prompt_template))
    logger.info(f"    🔍 Found placeholders in template: {sorted(placeholders)}")
    
    # Format extension prompt - provide both old and new placeholder names for compatibility
    format_kwargs = {
        "doi": doi_sl,
        "hash": doi_hash,
        "doi_underscore": doi_us,
        "doi_slash": doi_sl,
        "entity_label": entity_label,
        "entity_uri": entity_uri,
        "ontosynthesis_a_box": ontosynthesis_ttl,  # Old key name
        "main_ontology_a_box": ontosynthesis_ttl,  # New key name (for updated templates)
        "paper_content": extracted_content
    }
    
    logger.info(f"    🔍 Formatting prompt with keys: {sorted(format_kwargs.keys())}")
    
    class _PreserveUnknownPlaceholders(dict):
        def __missing__(self, key: str) -> str:
            return "{" + str(key) + "}"

    try:
        prompt = extension_prompt_template.format_map(_PreserveUnknownPlaceholders(format_kwargs))
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
    
    # Save prompt
    prompt_path = os.path.join(doi_folder, prompt_file)
    os.makedirs(os.path.dirname(prompt_path), exist_ok=True)
    with open(prompt_path, 'w', encoding='utf-8') as f:
        f.write(prompt)
    
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
        mcp_set_name=mcp_set_name
    )
    
    # Retry mechanism for agent execution
    max_retries = 3
    retry_delays = [5, 10, 15]  # Progressive backoff in seconds
    
    for attempt in range(max_retries):
        try:
            if attempt > 0:
                logger.info(f"    🔄 Retry attempt {attempt + 1}/{max_retries}")
            
            response, metadata = await agent.run(prompt, recursion_limit=recursion_limit)
            tool_activity = (metadata or {}).get("tool_activity", {}) or {}
            logger.info(
                "    🔧 Agent tool activity: planned=%s, executed=%s, tools=%s",
                tool_activity.get("planned_tool_call_count", 0),
                tool_activity.get("tool_message_count", 0),
                tool_activity.get("executed_tool_name_set", []),
            )
            
            # The agent should have created the output file via MCP
            # Check if it exists (exact path first, then try pattern matching for ontomops with hash)
            if os.path.exists(output_ttl_path):
                direct_content = _read_valid_extension_ttl(output_ttl_path, ontology_name)
                if direct_content is not None:
                    return _finalize_extension_output(
                        direct_content,
                        output_ttl_path,
                        f"    ✅ Extension completed: {os.path.basename(output_ttl_path)}",
                    )
                logger.warning(
                    f"    ⚠️  Agent wrote an invalid {ontology_name} TTL, continuing fallback checks: {os.path.basename(output_ttl_path)}"
                )
            # Continue fallback discovery when the exact output is missing or invalid.
            # For ontomops, try to find the file using the mapping or pattern matching
            if ontology_name == "ontomops":
                output_dir = os.path.dirname(output_ttl_path)
                # Try reading from mapping file first
                mapping_file = os.path.join(output_dir, "ontomops_output_mapping.json")
                if os.path.exists(mapping_file):
                    try:
                        with open(mapping_file, 'r', encoding='utf-8') as f:
                            mapping = json.load(f)
                        # Look up by entity_label
                        if entity_label in mapping:
                            mapped_file = os.path.join(output_dir, mapping[entity_label])
                            if os.path.exists(mapped_file):
                                mapped_content = _read_valid_extension_ttl(mapped_file, ontology_name)
                                if mapped_content is not None:
                                    return _finalize_extension_output(
                                        mapped_content,
                                        mapped_file,
                                        f"    ✅ Extension completed: {os.path.basename(mapped_file)} (found via mapping)",
                                    )
                    except Exception as e:
                        logger.debug(f"    Could not read mapping file: {e}")
                
                # Fallback: pattern matching for files with hash
                if os.path.exists(output_dir):
                    import glob
                    pattern_base = os.path.basename(output_ttl_path).replace('.ttl', '')
                    # Try pattern with hash suffix
                    pattern = f"{pattern_base}_*.ttl"
                    matches = glob.glob(os.path.join(output_dir, pattern))
                    if matches:
                        for matched_file in matches:
                            matched_content = _read_valid_extension_ttl(matched_file, ontology_name)
                            if matched_content is not None:
                                return _finalize_extension_output(
                                    matched_content,
                                    matched_file,
                                    f"    ✅ Extension completed: {os.path.basename(matched_file)} (found via pattern matching)",
                                )

            # Final fallback (all extensions): use persisted entity-specific memory TTL even if export_memory
            # wasn't called or the tool exports to a non-standard location.
            #
            # The generated extension MCP servers persist memory under:
            #   data/<hash>/memory_<ontology_name>/<entity_label>.ttl
            # (observed: memory_ontomops, memory_ontospecies)
            try:
                import shutil
                safe_local = _safe_name(entity_label)
                mem_name_variants = []
                for value in [entity_label, safe_local]:
                    mem_name_variants.extend(_entity_name_variants(value))
                mem_dirs = [os.path.join(doi_folder, f"memory_{ontology_name}")]
                for mem_dir in mem_dirs:
                    mem_candidates = [
                        os.path.join(mem_dir, f"{candidate}.ttl")
                        for candidate in mem_name_variants
                    ]
                    for mem_path in mem_candidates:
                        if os.path.exists(mem_path):
                            content = _read_valid_extension_ttl(mem_path, ontology_name)
                            if content is None:
                                logger.warning(
                                    f"    ⚠️  Ignoring fallback TTL without {ontology_name} facts: {os.path.basename(mem_path)}"
                                )
                                continue
                            return _finalize_extension_output(
                                content,
                                output_ttl_path,
                                f"    ✅ Extension completed: {os.path.basename(output_ttl_path)} (copied from {os.path.basename(mem_dir)})",
                            )
            except Exception as e:
                logger.debug(f"    Memory TTL fallback failed: {e}")
            
            raise RuntimeError(
                f"Extension agent did not produce a valid {ontology_name} TTL for '{entity_label}'"
            )
        
        except Exception as e:
            error_msg = str(e)
            logger.error(f"    ❌ Agent execution failed (attempt {attempt + 1}/{max_retries}): {error_msg}")
            
            if attempt < max_retries - 1:
                delay = retry_delays[attempt]
                logger.info(f"    ⏳ Waiting {delay}s before retry...")
                await asyncio.sleep(delay)
            else:
                logger.error(f"    ❌ All {max_retries} attempts failed for extension agent")
                raise


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
):
    """Process KG building for a single extension entity."""
    entity_label = entity.get("label", "")
    entity_uri = entity.get("uri", "")
    safe = _safe_name(entity_label)
    
    logger.info(f"  🔄 {ontology_name.upper()}: {entity_label}")
    
    # Load iteration config
    logger.info(f"    🔍 Loading iteration config...")
    logger.info(f"    📋 Config keys: {list(config.keys())}")
    if "iterations" not in config:
        logger.error(f"  ❌ No 'iterations' key in config")
        logger.error(f"    📋 Available keys: {list(config.keys())}")
        return
    
    if not config["iterations"]:
        logger.error(f"  ❌ Iterations list is empty")
        return
    
    iteration = config["iterations"][0]  # Extensions only have one iteration
    logger.info(f"    ✅ Loaded iteration config")
    logger.info(f"    📋 Iteration keys: {list(iteration.keys())}")
    
    # Check for required keys
    if "outputs" not in iteration:
        logger.error(f"  ❌ No 'outputs' key in iteration config")
        logger.error(f"    📋 Available keys: {list(iteration.keys())}")
        return
    
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
        doi_hash,
        safe,
        data_dir,
        test_mode=test_mcp_config is not None,
        ontology_name=main_ontology_name,
        meta_cfg=meta_cfg,
    )
    if not entity_ttl:
        logger.error(f"  ❌ Failed to load main ontology TTL for {entity_label}")
        return
    logger.info(f"    ✅ Loaded main ontology TTL ({len(entity_ttl)} chars)")
    
    # Load extracted content
    # Extension extractions are stored in mcp_run_{ontology_name}/extraction_{entity_safe}.txt
    # Try both the configured path (if exists) and the standard extension path
    extraction_paths = []
    
    # Try configured path first (if exists in iterations.json)
    if "outputs" in iteration and "extraction_file" in iteration["outputs"]:
        extraction_file = iteration["outputs"]["extraction_file"].replace("{entity_safe}", safe)
        extraction_paths.append(os.path.join(data_dir, doi_hash, extraction_file))
    
    # Standard extension extraction path
    standard_extraction_file = f"mcp_run_{ontology_name}/extraction_{safe}.txt"
    extraction_paths.append(os.path.join(data_dir, doi_hash, standard_extraction_file))
    
    extraction_path = None
    for path in extraction_paths:
        if os.path.exists(path):
            extraction_path = path
            logger.info(f"    ✅ Found extraction file: {os.path.basename(path)}")
            break
    
    if not extraction_path:
        logger.error(f"  ❌ Extraction file not found. Tried:")
        for path in extraction_paths:
            logger.error(f"      - {path}")
            if os.path.exists(os.path.dirname(path)):
                logger.error(f"        📋 Files in directory: {os.listdir(os.path.dirname(path))}")
        return
    
    try:
        with open(extraction_path, 'r', encoding='utf-8') as f:
            extracted_content = f.read()
        logger.info(f"    ✅ Loaded extraction content ({len(extracted_content)} chars)")
    except Exception as e:
        logger.error(f"  ❌ Failed to load extraction: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return
    
    # Extension agent step
    # Construct output file paths - use config if available, otherwise use standard extension paths
    if "extension_prompt_file" in iteration.get("outputs", {}):
        extension_prompt_file = iteration["outputs"]["extension_prompt_file"].replace("{entity_safe}", safe)
    else:
        # Standard extension prompt file path
        extension_prompt_file = f"prompts/{ontology_name}_kg_building/{safe}.md"
        logger.info(f"    ℹ️  Using standard extension prompt file path: {extension_prompt_file}")
    
    if "output_ttl" in iteration.get("outputs", {}):
        output_ttl = iteration["outputs"]["output_ttl"]
        
        # Create slugified versions for both ontologies
        # For ontospecies: URL-encoded slugification (matches ontospecies _slugify)
        entity_slugified_ontospecies_raw = unicodedata.normalize("NFKC", entity_label).strip()
        entity_slugified_ontospecies_raw = re.sub(r"\s+", "-", entity_slugified_ontospecies_raw)
        entity_slugified_ontospecies_raw = re.sub(r"[^\w\-.~]", "-", entity_slugified_ontospecies_raw)
        entity_slugified_ontospecies_raw = re.sub(r"[-_]{2,}", "-", entity_slugified_ontospecies_raw).strip("-_.")
        entity_slugified_ontospecies = quote(entity_slugified_ontospecies_raw[:120].rstrip("-_.") or "entity", safe="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.~")
        
        # For ontomops: casefold slugification (matches ontomops _slugify)
        entity_slugified_ontomops = unicodedata.normalize("NFKC", entity_label).casefold()
        entity_slugified_ontomops = re.sub(r"\s+", "-", entity_slugified_ontomops)
        entity_slugified_ontomops = re.sub(r"[^a-z0-9\-_]+", "-", entity_slugified_ontomops)
        entity_slugified_ontomops = re.sub(r"-+", "-", entity_slugified_ontomops).strip("-") or "entity"
        
        # Replace all possible placeholders
        output_ttl = output_ttl.replace("{entity_safe}", safe)
        # For {entity_name}, use slugified version that matches what MCP server will generate
        if ontology_name == "ontomops":
            # ontomops MCP server uses slugified name + entity IRI hash in export_memory
            # to avoid collisions (e.g., "VMOP-α" and "VMOP-β" both slugify to "vmop-")
            entity_iri = entity.get("uri", "")
            if entity_iri:
                entity_hash = hashlib.sha256(entity_iri.encode()).hexdigest()[:8]
                # Replace {entity_name} with slugified_name_hash format
                output_ttl = output_ttl.replace("{entity_name}", f"{entity_slugified_ontomops}_{entity_hash}")
            else:
                # Fallback if no IRI
                output_ttl = output_ttl.replace("{entity_name}", entity_slugified_ontomops)
        else:
            # For other ontologies, use raw entity_label (though this shouldn't happen)
            output_ttl = output_ttl.replace("{entity_name}", entity_label)
        # For {entity_slugified}, use ontospecies-style slugification
        output_ttl = output_ttl.replace("{entity_slugified}", entity_slugified_ontospecies)
    else:
        # Standard extension output TTL path
        output_ttl = f"{ontology_name}_extension_{safe}.ttl"
        logger.info(f"    ℹ️  Using standard extension output TTL path: {output_ttl}")
    
    # Load extension prompt from markdown file
    # Try kg_building_prompt first (if exists), then extension_prompt
    extension_prompt_path = None
    if "kg_building_prompt" in iteration:
        extension_prompt_path = iteration["kg_building_prompt"]
        logger.info(f"    ℹ️  Using kg_building_prompt from config: {extension_prompt_path}")
    elif "extension_prompt" in iteration:
        extension_prompt_path = iteration["extension_prompt"]
        logger.info(f"    ℹ️  Using extension_prompt from config: {extension_prompt_path}")
    else:
        # Fallback to standard path
        extension_prompt_path = f"ai_generated_contents/prompts/{ontology_name}/EXTENSION.md"
        logger.info(f"    ℹ️  Using standard extension prompt path: {extension_prompt_path}")
    
    logger.info(f"    🔍 Loading extension prompt from: {extension_prompt_path}")
    extension_prompt_template = load_prompt(extension_prompt_path, project_root)
    if not extension_prompt_template:
        logger.error(f"  ❌ Failed to load extension prompt for {ontology_name}")
        logger.error(f"    📁 Expected path: {os.path.join(project_root, extension_prompt_path)}")
        return
    logger.info(f"    ✅ Loaded extension prompt template ({len(extension_prompt_template)} chars)")
    
    # Write global state for MCP server (using hash, not DOI)
    # Note: DOI is only resolved for prompt formatting, not for global state
    logger.info(f"    🔍 Resolving DOI from hash: {doi_hash}")
    doi_us, doi_sl = resolve_doi_from_hash(doi_hash, data_dir)
    logger.info(f"    ✅ Resolved DOI - underscore: {doi_us}, slash: {doi_sl}")
    write_global_state(ontology_name, doi_hash, entity_label, entity_uri, data_dir)
    
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
    
    await run_extension_agent(
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
    )
    
    logger.info(f"  ✅ KG building completed for {entity_label}")


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
    
    # Load top entities
    entities_path = os.path.join(doi_folder, "mcp_run", "iter1_top_entities.json")
    if not os.path.exists(entities_path):
        logger.error(f"Top entities file not found: {entities_path}")
        return False
    
    try:
        with open(entities_path, 'r', encoding='utf-8') as f:
            top_entities = json.load(f)
    except Exception as e:
        logger.error(f"Failed to load top entities: {e}")
        return False
    
    if not top_entities:
        logger.warning("No top entities found")
        return False
    
    logger.info(f"Found {len(top_entities)} top entities")
    
    # Process all extensions and entities sequentially using a single event loop
    async def process_all_extensions() -> bool:
        """Process all extensions and entities sequentially in a single event loop."""
        had_failures = False
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
                had_failures = True
                continue
            
            try:
                with open(iterations_config_path, 'r', encoding='utf-8') as f:
                    iterations_config = json.load(f)
            except Exception as e:
                logger.error(f"  ❌ Failed to load iterations config: {e}")
                had_failures = True
                continue
            
            # Process each entity STRICTLY SEQUENTIALLY
            # This ensures global state is set correctly for each entity
            for i, entity in enumerate(top_entities):
                entity_label = entity.get("label", "")
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
                    )
                    logger.info(f"  ✅ Completed entity {i+1}/{len(top_entities)}: {entity_label}")
                except Exception as e:
                    logger.error(f"  ❌ KG building failed for '{entity_label}': {e}")
                    import traceback
                    logger.error(f"  Traceback: {traceback.format_exc()}")
                    had_failures = True
                    continue
        return not had_failures
    
    # Run all processing in a single event loop
    try:
        success = asyncio.run(process_all_extensions())
    except Exception as e:
        logger.error(f"❌ Failed to process extensions: {e}")
        import traceback
        logger.error(f"Traceback: {traceback.format_exc()}")
        return False
    if not success:
        logger.error("❌ Extensions KG building finished with one or more failures")
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

