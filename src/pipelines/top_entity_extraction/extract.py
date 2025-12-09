"""
Top entity extraction pipeline step.

This module extracts top-level entities (e.g., ChemicalSynthesis) from papers
using prompts defined in the main ontology configuration.
"""

import os
import sys
import json

# Add project root to path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from src.utils.global_logger import get_logger
from src.utils.extraction_models import get_extraction_model
from models.LLMCreator import LLMCreator
from models.ModelConfig import ModelConfig
import asyncio

logger = get_logger("pipeline", "top_entity_extraction")


def load_meta_config(config_path: str = "configs/meta_task/meta_task_config.json") -> dict:
    """Load the meta task configuration."""
    with open(config_path, 'r', encoding='utf-8') as f:
        return json.load(f)


def load_extraction_prompt(ontology_name: str, iteration: int = 1) -> str:
    """
    Load extraction prompt from markdown file.
    
    Args:
        ontology_name: Name of the ontology (e.g., 'ontosynthesis')
        iteration: Iteration number (default: 1)
        
    Returns:
        The prompt text
    """
    prompt_path = f"ai_generated_contents/prompts/{ontology_name}/EXTRACTION_ITER_{iteration}.md"
    
    if not os.path.exists(prompt_path):
        raise FileNotFoundError(f"Extraction prompt not found: {prompt_path}")
    
    with open(prompt_path, 'r', encoding='utf-8') as f:
        return f.read()


async def extract_top_entities(doi_hash: str, data_dir: str, ontology_name: str) -> bool:
    """
    Extract top-level entities from the stitched markdown.
    
    Args:
        doi_hash: DOI hash identifier
        data_dir: Base data directory
        ontology_name: Name of the ontology to use
        
    Returns:
        True if extraction succeeded
    """
    doi_dir = os.path.join(data_dir, doi_hash)
    stitched_md = os.path.join(doi_dir, f"{doi_hash}_stitched.md")
    output_file = os.path.join(doi_dir, "top_entities.txt")
    
    # Check if already exists
    if os.path.exists(output_file):
        logger.info(f"⏭️  Top entities already extracted: {output_file}")
        return True
    
    # Check if stitched markdown exists
    if not os.path.exists(stitched_md):
        logger.error(f"❌ Stitched markdown not found: {stitched_md}")
        return False
    
    # Read paper content
    logger.info(f"📄 Reading stitched markdown: {stitched_md}")
    with open(stitched_md, 'r', encoding='utf-8') as f:
        paper_content = f.read()
    
    # Load extraction prompt
    logger.info(f"📋 Loading extraction prompt for {ontology_name} iteration 1")
    try:
        extraction_prompt = load_extraction_prompt(ontology_name, iteration=1)
    except FileNotFoundError as e:
        logger.error(f"❌ {e}")
        return False
    
    # Build full prompt
    full_prompt = f"{extraction_prompt}\n\n{paper_content}"
    
    # Save full prompt for reproducibility
    prompt_save_path = os.path.join(doi_dir, "iter1_full_prompt.md")
    os.makedirs(doi_dir, exist_ok=True)
    with open(prompt_save_path, 'w', encoding='utf-8') as f:
        f.write(full_prompt)
    logger.info(f"💾 Full prompt saved to: {prompt_save_path}")
    
    # Get model from config
    model_key = "iter1_hints"
    model_name = get_extraction_model(model_key)
    logger.info(f"🤖 Using model: {model_name} (from {model_key})")
    
    # Create LLM
    llm = LLMCreator(
        model=model_name,
        model_config=ModelConfig(temperature=0, top_p=1.0),
        remote_model=True,
    ).setup_llm()
    
    # Extract with retries
    max_retries = 3
    for attempt in range(max_retries):
        try:
            logger.info(f"🔍 Extracting top entities (attempt {attempt + 1}/{max_retries})...")
            result = await llm.ainvoke(full_prompt)
            
            # Extract content
            content = result.content if hasattr(result, 'content') else str(result)
            
            # Save result
            os.makedirs(doi_dir, exist_ok=True)
            with open(output_file, 'w', encoding='utf-8') as f:
                f.write(content)
            
            logger.info(f"✅ Top entities saved to: {output_file}")
            
            # Log extracted entities
            lines = [line.strip() for line in content.split('\n') if line.strip() and line.strip().startswith('ChemicalSynthesis')]
            logger.info(f"   Found {len(lines)} top-level entities")
            for line in lines[:5]:  # Show first 5
                logger.info(f"   - {line[:80]}...")
            if len(lines) > 5:
                logger.info(f"   ... and {len(lines) - 5} more")
            
            return True
            
        except Exception as e:
            if attempt < max_retries - 1:
                logger.warning(f"⚠️  Attempt {attempt + 1} failed: {e}, retrying...")
                await asyncio.sleep(5 * (attempt + 1))
            else:
                logger.error(f"❌ Extraction failed after {max_retries} attempts: {e}")
                return False
    
    return False


def run_step(doi_hash: str, config: dict) -> bool:
    """
    Main entry point for the top entity extraction pipeline step.
    
    Args:
        doi_hash: The DOI hash to process
        config: Pipeline configuration dictionary
        
    Returns:
        True if extraction succeeded
    """
    data_dir = config.get("data_dir", "data")
    
    logger.info(f"▶️  Top Entity Extraction: {doi_hash}")
    
    # Load meta config to get main ontology
    try:
        meta_config = load_meta_config()
        main_ontology = meta_config.get("ontologies", {}).get("main", {})
        ontology_name = main_ontology.get("name", "ontosynthesis")
        logger.info(f"   Using ontology: {ontology_name}")
    except Exception as e:
        logger.error(f"❌ Failed to load meta config: {e}")
        return False
    
    # Run extraction
    try:
        success = asyncio.run(extract_top_entities(doi_hash, data_dir, ontology_name))
        
        if success:
            logger.info(f"✅ Top Entity Extraction completed: {doi_hash}")
        else:
            logger.error(f"❌ Top Entity Extraction failed: {doi_hash}")
        
        return success
        
    except Exception as e:
        logger.error(f"❌ Top Entity Extraction failed with exception: {e}")
        return False


if __name__ == "__main__":
    # Test mode
    if len(sys.argv) > 1:
        test_hash = sys.argv[1]
        test_config = {"data_dir": "data"}
        print(f"Running top entity extraction for DOI hash: {test_hash}")
        success = run_step(test_hash, test_config)
        print(f"Top entity extraction {'succeeded' if success else 'failed'}.")
    else:
        print("Usage: python -m src.pipelines.top_entity_extraction.extract <doi_hash>")

