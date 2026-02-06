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
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import quote
from filelock import FileLock

from models.BaseAgent import BaseAgent
from models.ModelConfig import ModelConfig
from src.pipelines.utils.ttl_publisher import get_output_naming_config, load_meta_task_config

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


def load_prompt(prompt_path: str, project_root: str = ".") -> str:
    """Load prompt template from markdown file.
    
    Tries candidate directory first, then production directory.
    """
    # Try candidate first (where generation scripts write), then fallback to production
    candidate_path = prompt_path.replace("ai_generated_contents/", "ai_generated_contents_candidate/", 1)
    production_path = prompt_path
    
    paths_to_try = [
        os.path.join(project_root, candidate_path),
        os.path.join(project_root, production_path)
    ]
    
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

        published_candidates = [
            primary_name,
            f"{entity_safe}.ttl",
            f"{entity_safe.lower()}.ttl",
            f"{entity_safe.replace('_', '-')}.ttl",
        ]
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
    memory_ttl = os.path.join(doi_folder, "memory", f"{entity_safe}.ttl")
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
            export_candidates = [
                os.path.join(exports_dir, f)
                for f in os.listdir(exports_dir)
                if f.lower().startswith(entity_safe.lower() + "_") and f.lower().endswith(".ttl")
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
    candidates = [
        f"output_{entity_safe}.ttl",
        f"output_{entity_safe.lower()}.ttl",
        f"output_{entity_safe.replace('_', '-')}.ttl",
    ]
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
    ontology_name: str = None
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
    
    # Check if extension already exists
    if os.path.exists(output_ttl_path):
        logger.info(f"    ⏭️  Extension exists: {os.path.basename(output_ttl_path)}")
        _maybe_update_ontomops_mapping(output_ttl_path)
        with open(output_ttl_path, 'r', encoding='utf-8') as f:
            return f.read()

    # If the extension MCP server already persisted an entity TTL under memory_<ontology_name>,
    # use it directly to avoid unnecessary agent reruns (and LLM costs).
    if ontology_name:
        try:
            import shutil
            safe_local = _safe_name(entity_label)
            mem_dir = os.path.join(doi_folder, f"memory_{ontology_name}")
            mem_candidates = [
                os.path.join(mem_dir, f"{entity_label}.ttl"),
                os.path.join(mem_dir, f"{safe_local}.ttl"),
                os.path.join(mem_dir, f"{safe_local.lower()}.ttl"),
            ]
            for mem_path in mem_candidates:
                if os.path.exists(mem_path):
                    os.makedirs(os.path.dirname(output_ttl_path), exist_ok=True)
                    shutil.copy2(mem_path, output_ttl_path)
                    logger.info(
                        f"    ✅ Extension completed: {os.path.basename(output_ttl_path)} (copied from {os.path.basename(mem_dir)})"
                    )
                    _maybe_update_ontomops_mapping(output_ttl_path)
                    with open(output_ttl_path, "r", encoding="utf-8") as f:
                        return f.read()

            # Some MCP servers persist under the shared memory/ + exports/ conventions.
            shared_mem_dir = os.path.join(doi_folder, "memory")
            shared_mem_candidates = [
                os.path.join(shared_mem_dir, f"{entity_label}.ttl"),
                os.path.join(shared_mem_dir, f"{safe_local}.ttl"),
                os.path.join(shared_mem_dir, f"{safe_local.lower()}.ttl"),
            ]
            for mem_path in shared_mem_candidates:
                if os.path.exists(mem_path):
                    os.makedirs(os.path.dirname(output_ttl_path), exist_ok=True)
                    shutil.copy2(mem_path, output_ttl_path)
                    logger.info(
                        f"    ✅ Extension completed: {os.path.basename(output_ttl_path)} (copied from {os.path.basename(shared_mem_dir)})"
                    )
                    _maybe_update_ontomops_mapping(output_ttl_path)
                    with open(output_ttl_path, "r", encoding="utf-8") as f:
                        return f.read()

            exports_dir = os.path.join(doi_folder, "exports")
            if os.path.isdir(exports_dir):
                import glob
                # Prefer exact safe_local prefix, then raw label prefix
                patterns = [
                    os.path.join(exports_dir, f"{safe_local}_*.ttl"),
                    os.path.join(exports_dir, f"{entity_label}_*.ttl"),
                ]
                export_matches = []
                for pat in patterns:
                    export_matches.extend(glob.glob(pat))
                if export_matches:
                    export_matches.sort(key=lambda p: os.path.getmtime(p), reverse=True)
                    latest = export_matches[0]
                    os.makedirs(os.path.dirname(output_ttl_path), exist_ok=True)
                    shutil.copy2(latest, output_ttl_path)
                    logger.info(
                        f"    ✅ Extension completed: {os.path.basename(output_ttl_path)} (copied from exports/{os.path.basename(latest)})"
                    )
                    _maybe_update_ontomops_mapping(output_ttl_path)
                    with open(output_ttl_path, "r", encoding="utf-8") as f:
                        return f.read()
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
        "ontosynthesis_a_box": ontosynthesis_ttl,  # Old key name
        "main_ontology_a_box": ontosynthesis_ttl,  # New key name (for updated templates)
        "paper_content": extracted_content
    }
    
    logger.info(f"    🔍 Formatting prompt with keys: {sorted(format_kwargs.keys())}")
    
    try:
        prompt = extension_prompt_template.format(**format_kwargs)
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
            
            # The agent should have created the output file via MCP
            # Check if it exists (exact path first, then try pattern matching for ontomops with hash)
            if os.path.exists(output_ttl_path):
                logger.info(f"    ✅ Extension completed: {os.path.basename(output_ttl_path)}")
                _maybe_update_ontomops_mapping(output_ttl_path)
                with open(output_ttl_path, 'r', encoding='utf-8') as f:
                    return f.read()
            else:
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
                                    logger.info(f"    ✅ Extension completed: {os.path.basename(mapped_file)} (found via mapping)")
                                    with open(mapped_file, 'r', encoding='utf-8') as f:
                                        return f.read()
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
                            matched_file = matches[0]
                            logger.info(f"    ✅ Extension completed: {os.path.basename(matched_file)} (found via pattern matching)")
                            with open(matched_file, 'r', encoding='utf-8') as f:
                                return f.read()

                # Final fallback (all extensions): use persisted entity-specific memory TTL even if export_memory
                # wasn't called or the tool exports to a non-standard location.
                #
                # The generated extension MCP servers persist memory under:
                #   data/<hash>/memory_<ontology_name>/<entity_label>.ttl
                # (observed: memory_ontomops, memory_ontospecies)
                try:
                    import shutil
                    safe_local = _safe_name(entity_label)
                    mem_dirs = [
                        os.path.join(doi_folder, f"memory_{ontology_name}"),
                        os.path.join(doi_folder, "memory"),
                    ]
                    for mem_dir in mem_dirs:
                        mem_candidates = [
                            os.path.join(mem_dir, f"{entity_label}.ttl"),
                            os.path.join(mem_dir, f"{safe_local}.ttl"),
                            os.path.join(mem_dir, f"{safe_local.lower()}.ttl"),
                        ]
                        for mem_path in mem_candidates:
                            if os.path.exists(mem_path):
                                os.makedirs(os.path.dirname(output_ttl_path), exist_ok=True)
                                shutil.copy2(mem_path, output_ttl_path)
                                logger.info(
                                    f"    ✅ Extension completed: {os.path.basename(output_ttl_path)} (copied from {os.path.basename(mem_dir)})"
                                )
                                with open(output_ttl_path, "r", encoding="utf-8") as f:
                                    return f.read()

                    # Last resort: use latest export snapshot
                    exports_dir = os.path.join(doi_folder, "exports")
                    if os.path.isdir(exports_dir):
                        import glob
                        patterns = [
                            os.path.join(exports_dir, f"{safe_local}_*.ttl"),
                            os.path.join(exports_dir, f"{entity_label}_*.ttl"),
                        ]
                        export_matches = []
                        for pat in patterns:
                            export_matches.extend(glob.glob(pat))
                        if export_matches:
                            export_matches.sort(key=lambda p: os.path.getmtime(p), reverse=True)
                            latest = export_matches[0]
                            os.makedirs(os.path.dirname(output_ttl_path), exist_ok=True)
                            shutil.copy2(latest, output_ttl_path)
                            logger.info(
                                f"    ✅ Extension completed: {os.path.basename(output_ttl_path)} (copied from exports/{os.path.basename(latest)})"
                            )
                            with open(output_ttl_path, "r", encoding="utf-8") as f:
                                return f.read()
                except Exception as e:
                    logger.debug(f"    Memory TTL fallback failed: {e}")
                
                logger.warning(f"    ⚠️  Extension TTL not found: {os.path.basename(output_ttl_path)}")
                return str(response)
        
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
    agent_model = iteration.get("agent_model", "gpt-4o")

    # Portability: drop Docker/binary-dependent tools when present in configs.
    # (CCDC tool commonly fails on Windows without external deps.)
    if mcp_tools and "ccdc" in set(mcp_tools):
        logger.warning(f"    ⚠️  Dropping 'ccdc' from extension MCP tools for portability: {mcp_tools}")
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
        ontology_name=ontology_name
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
    meta_config_path = os.path.join(project_root, "configs/meta_task/meta_task_config.json")
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
    async def process_all_extensions():
        """Process all extensions and entities sequentially in a single event loop."""
        # Process each extension ontology
        for extension in extensions:
            ontology_name = extension.get("name")
            logger.info(f"\n  📚 Extension: {ontology_name}")
            
            # Load iteration config for this ontology
            # Try candidate first (where generation scripts write), then fallback to production
            iterations_config_paths = [
                os.path.join(project_root, "ai_generated_contents_candidate/iterations", ontology_name, "iterations.json"),
                os.path.join(project_root, "ai_generated_contents/iterations", ontology_name, "iterations.json")
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
                continue
            
            try:
                with open(iterations_config_path, 'r', encoding='utf-8') as f:
                    iterations_config = json.load(f)
            except Exception as e:
                logger.error(f"  ❌ Failed to load iterations config: {e}")
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
                    continue
    
    # Run all processing in a single event loop
    try:
        asyncio.run(process_all_extensions())
    except Exception as e:
        logger.error(f"❌ Failed to process extensions: {e}")
        import traceback
        logger.error(f"Traceback: {traceback.format_exc()}")
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

