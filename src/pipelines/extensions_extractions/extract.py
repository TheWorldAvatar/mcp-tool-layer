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
    """Load prompt template from markdown file."""
    full_path = os.path.join(project_root, prompt_path)
    try:
        with open(full_path, 'r', encoding='utf-8') as f:
            content = f.read()
        logger.info(f"    📖 Loaded prompt: {os.path.basename(prompt_path)}")
        return content
    except Exception as e:
        logger.error(f"    ❌ Failed to load prompt from {full_path}: {e}")
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
    data_dir: str = "data"
) -> str:
    """Run extraction for a single entity."""
    doi_folder = os.path.join(data_dir, doi_hash)
    
    # Check if extraction already exists
    extraction_path = os.path.join(doi_folder, output_file)
    if os.path.exists(extraction_path):
        logger.info(f"    ⏭️  Extraction exists: {os.path.basename(extraction_path)}")
        with open(extraction_path, 'r', encoding='utf-8') as f:
            return f.read()
    
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
    
    # Run LLM extraction using extract_content
    logger.info(f"    🔍 Extracting content...")
    response = await extract_content(
        paper_content=paper_content,
        goal=goal,
        t_box=tbox_content,
        entity_label=entity_label,
        entity_uri=entity_uri,
        model_name=model_name,
        save_prompt_path=os.path.join(doi_folder, prompt_file)
    )
    
    # Save extraction
    os.makedirs(os.path.dirname(extraction_path), exist_ok=True)
    with open(extraction_path, 'w', encoding='utf-8') as f:
        f.write(response)
    
    logger.info(f"    ✅ Saved extraction: {os.path.basename(extraction_path)}")
    return response


async def process_extension(
    ontology_name: str,
    doi_hash: str,
    entity: Dict,
    paper_content: str,
    config: Dict,
    data_dir: str = "data",
    project_root: str = "."
):
    """Process extraction for a single extension entity (extraction only, no KG building)."""
    entity_label = entity.get("label", "")
    entity_uri = entity.get("uri", "")
    safe = _safe_name(entity_label)
    
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
    
    # Extraction step
    extraction_file = iteration["outputs"]["extraction_file"].replace("{entity_safe}", safe)
    # Note: extraction_prompt_file may not exist in outputs - use a default if missing
    extraction_prompt_file = iteration["outputs"].get("extraction_prompt_file", f"prompts/extraction_{safe}.md")
    extraction_prompt_file = extraction_prompt_file.replace("{entity_safe}", safe)
    
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
        data_dir=data_dir
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
    for extension in extensions:
        ontology_name = extension.get("name")
        logger.info(f"\n  📚 Extension: {ontology_name}")
        
        # Load iteration config for this ontology
        iterations_config_path = os.path.join(
            project_root,
            "ai_generated_contents_candidate/iterations",
            ontology_name,
            "iterations.json"
        )
        
        if not os.path.exists(iterations_config_path):
            logger.error(f"  ❌ Iterations config not found: {iterations_config_path}")
            continue
        
        try:
            with open(iterations_config_path, 'r', encoding='utf-8') as f:
                iterations_config = json.load(f)
        except Exception as e:
            logger.error(f"  ❌ Failed to load iterations config: {e}")
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
                continue
    
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

