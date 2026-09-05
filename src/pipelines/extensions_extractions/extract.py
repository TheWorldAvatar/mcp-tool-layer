"""
Extensions Extractions Module

Handles LLM-based content extraction for extension ontologies (OntoMOPs and OntoSpecies).
KG building is handled separately in extensions_kg_building module.
"""

import os
import json
import asyncio
import logging
from typing import Dict

from src.agents.mops.dynamic_mcp.modules.extraction import extract_content
from src.pipelines.main_ontology_extractions.extract import _kg_revision_relation_errors
from src.pipelines.structured_extraction import validate_hint_payload
from src.pipelines.utils.extension_revision import hint_revision_prompt_block
from src.pipelines.utils.runtime_paths import (
    first_existing_runtime_path,
    read_runtime_text,
    resolve_extension_artifact,
    write_runtime_text,
)
from src.pipelines.utils.published_synthesis_queue import load_extension_synthesis_queue
from src.pipelines.utils.top_entity_identity import entity_artifact_name
from src.pipelines.utils.ttl_publisher import get_main_ontology_name

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)


def _safe_name(label: str) -> str:
    """Stable, length-capped artifact stem shared with main extraction."""
    return entity_artifact_name(label)


def load_tbox(tbox_path: str) -> str:
    """Load T-Box content from file."""
    try:
        with open(tbox_path, 'r', encoding='utf-8') as f:
            content = f.read()
        logger.info(f"    📖 Loaded T-Box from {os.path.basename(tbox_path)}")
        return content
    except Exception as e:
        logger.warning(f"    ⚠️  Could not load T-Box from {tbox_path}: {e}")
        return ""


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
                with open(full_path, "r", encoding="utf-8") as f:
                    content = f.read()
                logger.info(
                    f"    📖 Loaded prompt: {os.path.basename(full_path)} (from {os.path.dirname(full_path)})"
                )
                return content
            except Exception as e:
                logger.error(f"    ❌ Failed to load prompt from {full_path}: {e}")
                continue

    logger.error(f"    ❌ Failed to load prompt. Tried:")
    for p in paths_to_try:
        logger.error(f"      - {p}")
    return ""


async def run_extraction(
    doi_hash: str,
    entity_label: str,
    entity_uri: str,
    paper_content: str,
    tbox_content: str,
    extraction_prompt_template: str,
    model_name: str,
    output_file: str,
    prompt_file: str,
    data_dir: str = "data",
    output_lookup: list[str] | None = None,
    revision_feedback: str = "",
    force: bool = False,
) -> str:
    """Run extraction for a single entity."""
    doi_folder = os.path.join(data_dir, doi_hash)
    
    # Check if extraction already exists (canonical or legacy long name)
    extraction_path = (
        output_file if os.path.isabs(output_file) else os.path.join(doi_folder, output_file)
    )
    lookup = list(output_lookup or [])
    if extraction_path not in lookup:
        lookup.insert(0, extraction_path)
    existing = first_existing_runtime_path(lookup)
    previous_extraction = read_runtime_text(existing) if existing else ""
    if existing and not force and not str(revision_feedback or "").strip():
        logger.info(f"    ⏭️  Extraction exists: {os.path.basename(existing)}")
        return previous_extraction
    
    # Format extraction prompt (goal) - replace placeholders if they exist
    # Some prompts (like ontospecies EXTRACTION.md) don't have placeholders
    goal = extraction_prompt_template
    try:
        goal = extraction_prompt_template.format(
            entity_label=entity_label,
            entity_uri=entity_uri,
            ontomops_t_box=tbox_content,  # Works for both ontomops and ontospecies
            ontospecies_t_box=tbox_content
        )
    except (KeyError, IndexError):
        # Template doesn't have placeholders or has different placeholders - use as-is
        pass
    correction_block = hint_revision_prompt_block(revision_feedback)
    if correction_block:
        goal = correction_block + goal
    
    # Run LLM extraction using extract_content
    logger.info(
        "    🔍 Extracting content%s...",
        " (hint revision)" if correction_block else "",
    )
    max_attempts = 3 if correction_block else 1
    response = ""
    validation_feedback = ""
    for attempt in range(max_attempts):
        effective_goal = goal
        if validation_feedback:
            effective_goal = (
                goal
                + "\n\nYour previous output was rejected by the structural "
                "materializability validator. Correct every issue below and "
                "return only the complete corrected JSON payload:\n"
                f"{validation_feedback}"
            )
        response = await extract_content(
            paper_content=paper_content,
            goal=effective_goal,
            t_box=tbox_content,
            entity_label=entity_label,
            entity_uri=entity_uri,
            previous_extraction=previous_extraction,
            model_name=model_name,
            save_prompt_path=(
                prompt_file if os.path.isabs(prompt_file) else os.path.join(doi_folder, prompt_file)
            )
        )
        if not correction_block:
            break
        try:
            valid_payload, payload_errors = validate_hint_payload(
                response,
                expected_schema="ref-entity-relations.v1",
                allowed_entity_iris={str(entity_uri or "")},
            )
        except ValueError as exc:
            valid_payload, payload_errors = False, [str(exc)]
        revision_errors = _kg_revision_relation_errors(response, revision_feedback)
        if valid_payload and not revision_errors:
            break
        validation_feedback = "\n".join(
            f"- {error}" for error in list(payload_errors) + revision_errors
        )
        if attempt == max_attempts - 1:
            logger.warning(
                "    ⚠️  Hint revision still has validation issues after %d attempt(s)",
                max_attempts,
            )
    
    write_runtime_text(extraction_path, response)
    
    logger.info(f"    ✅ Saved extraction: {os.path.basename(extraction_path)}")
    return response


async def process_extension(
    ontology_name: str,
    doi_hash: str,
    entity: Dict,
    paper_content: str,
    config: Dict,
    data_dir: str = "data",
    project_root: str = ".",
    revision_feedback: str = "",
    force: bool = False,
):
    """Process extraction for a single extension entity (extraction only, no KG building)."""
    entity_label = entity.get("label", "")
    entity_uri = entity.get("uri", "")
    
    logger.info(f"  🔄 {ontology_name.upper()}: {entity_label}")
    
    # Load iteration config
    iteration = config["iterations"][0]  # Extensions only have one iteration
    
    # Load T-Box - load directly from ontologies directory
    tbox_content = ""
    if ontology_name == "ontomops":
        tbox_path = os.path.join(project_root, "data", "ontologies", "ontomops-subgraph.ttl")
        tbox_content = load_tbox(tbox_path)
    elif ontology_name == "ontospecies":
        tbox_path = os.path.join(project_root, "data", "ontologies", "ontospecies-subgraph.ttl")
        tbox_content = load_tbox(tbox_path)
    else:
        # Fallback: try to load from inputs if specified
        if "inputs" in iteration and "tbox" in iteration["inputs"]:
            tbox_path = iteration["inputs"]["tbox"]
            tbox_full_path = os.path.join(project_root, tbox_path)
            tbox_content = load_tbox(tbox_full_path)
        else:
            logger.warning(f"  ⚠️  No T-Box specified for {ontology_name}, using empty T-Box")
    
    # Get model name
    from src.utils.extraction_models import get_extraction_model
    model_name = get_extraction_model(iteration["model_config_key"])
    
    # Extraction step — resolve a Windows-safe write path, but still see
    # any already-written legacy long filename from earlier runtimes.
    doi_folder = os.path.join(data_dir, doi_hash)
    extraction_template = iteration["outputs"].get(
        "extraction_file", f"mcp_run_{ontology_name}/extraction_{{entity_safe}}.txt"
    )
    prompt_template = iteration["outputs"].get(
        "extraction_prompt_file", "prompts/extraction_{entity_safe}.md"
    )
    extraction_file, extraction_lookup = resolve_extension_artifact(
        doi_folder, extraction_template, entity_label, entity_uri=entity_uri
    )
    extraction_prompt_file, _prompt_lookup = resolve_extension_artifact(
        doi_folder, prompt_template, entity_label, entity_uri=entity_uri
    )
    
    # Load extraction prompt from markdown file
    extraction_prompt_path = iteration["extraction_prompt"]
    extraction_prompt_template = load_prompt(extraction_prompt_path, project_root)
    if not extraction_prompt_template:
        logger.error(f"  ❌ Failed to load extraction prompt for {ontology_name}")
        return
    
    extracted_content = await run_extraction(
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
        revision_feedback=revision_feedback,
        force=force or bool(str(revision_feedback or "").strip()),
    )
    
    logger.info(f"  ✅ Extraction completed for {entity_label}")


def run_step(doi_hash: str, config: dict) -> bool:
    """
    Main Extensions Extractions step: Process OntoMOPs and OntoSpecies extensions.
    
    Args:
        doi_hash: DOI hash for the paper
        config: Pipeline configuration dictionary
        
    Returns:
        True if extensions completed successfully
    """
    # Extract config parameters
    data_dir = config.get("data_dir", "data")
    project_root = config.get("project_root", ".")
    
    logger.info(f"🔌 Starting extensions extractions for DOI: {doi_hash}")
    
    doi_folder = os.path.join(data_dir, doi_hash)
    if not os.path.exists(doi_folder):
        logger.error(f"DOI folder not found: {doi_folder}")
        return False
    
    # Check if step is already completed
    marker_file = os.path.join(doi_folder, ".extensions_extractions_done")
    if os.path.exists(marker_file):
        logger.info(f"  ⏭️  Extensions extractions already completed (marker exists)")
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
    
    # Get extension ontologies
    extensions = meta_config.get("ontologies", {}).get("extensions", [])
    if not extensions:
        logger.warning("No extension ontologies configured")
        return True
    
    top_entities = load_extension_synthesis_queue(
        doi_folder,
        ontology_name=get_main_ontology_name(meta_config),
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
    
    # Load stitched paper content
    stitched_path = os.path.join(doi_folder, f"{doi_hash}_stitched.md")
    if not os.path.exists(stitched_path):
        logger.error(f"Stitched paper not found: {stitched_path}")
        return False
    
    try:
        with open(stitched_path, 'r', encoding='utf-8') as f:
            paper_content = f.read()
    except Exception as e:
        logger.error(f"Failed to load stitched paper: {e}")
        return False
    
    # Process each extension ontology
    had_failures = False
    for extension in extensions:
        ontology_name = extension.get("name")
        logger.info(f"\n  📚 Extension: {ontology_name}")
        
        # Load iteration config for this ontology
        artifact_roots = []
        override_root = os.environ.get("TWA_GENERATED_ARTIFACT_ROOT", "").strip()
        if override_root:
            artifact_roots.append(override_root)
        artifact_roots.extend(["ai_generated_contents_candidate", "ai_generated_contents"])
        iterations_config_path = ""
        for artifact_root in dict.fromkeys(artifact_roots):
            candidate = os.path.join(project_root, artifact_root, "iterations", ontology_name, "iterations.json")
            if os.path.exists(candidate):
                iterations_config_path = candidate
                break

        if not iterations_config_path:
            logger.error(f"  ❌ Iterations config not found for {ontology_name}. Tried roots: {artifact_roots}")
            had_failures = True
            continue
        
        try:
            with open(iterations_config_path, 'r', encoding='utf-8') as f:
                iterations_config = json.load(f)
        except Exception as e:
            logger.error(f"  ❌ Failed to load iterations config: {e}")
            had_failures = True
            continue
        
        # Process each entity
        for i, entity in enumerate(top_entities):
            entity_label = entity.get("label", "")
            logger.info(f"\n  Entity {i+1}/{len(top_entities)}: {entity_label}")
            
            try:
                asyncio.run(process_extension(
                    ontology_name=ontology_name,
                    doi_hash=doi_hash,
                    entity=entity,
                    paper_content=paper_content,
                    config=iterations_config,
                    data_dir=data_dir,
                    project_root=project_root
                ))
            except Exception as e:
                logger.error(f"  ❌ Extension failed for '{entity_label}': {e}")
                had_failures = True
                continue
    
    if had_failures:
        logger.error("❌ Extensions extractions finished with one or more failures")
        return False
    
    # Create completion marker
    try:
        with open(marker_file, 'w') as f:
            f.write("completed\n")
        logger.info(f"  📌 Created completion marker")
    except Exception as e:
        logger.warning(f"  ⚠️  Failed to create completion marker: {e}")
    
    logger.info(f"✅ Extensions extractions completed for DOI: {doi_hash}")
    return True


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python -m src.pipelines.extensions_extractions.extract <doi_hash>")
        sys.exit(1)
    
    # Create config dict for standalone usage
    config = {
        "data_dir": "data",
        "project_root": "."
    }
    
    success = run_step(sys.argv[1], config)
    sys.exit(0 if success else 1)

