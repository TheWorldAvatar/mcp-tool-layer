"""
Main Ontology Extractions Pipeline Step

This module handles iterations 2+ for ontology-driven extractions.
It ONLY performs extraction (hints generation), NOT KG building.

It processes each top-level entity through multiple iterations:
- Iteration 2: Chemical inputs/outputs (uses agent with MCP tools)
- Iteration 3: Synthesis steps (with pre-extraction, uses simple LLM)
- Iteration 3.1: Step enrichment
- Iteration 3.2: Vessel enrichment  
- Iteration 4: Yield extraction
"""
import os
import sys
import json
import asyncio
from typing import List, Dict

# Add project root to path for imports
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from models.BaseAgent import BaseAgent
from models.ModelConfig import ModelConfig
from models.LLMCreator import LLMCreator
from src.utils.global_logger import get_logger
from src.utils.extraction_models import get_extraction_model

logger = get_logger("pipeline", "main_ontology_extractions")


def _safe_name(label: str) -> str:
    """Convert entity label to safe filename."""
    return (label or "entity").replace(" ", "_").replace("/", "_")


def resolve_file_path(path_template: str, doi_hash: str, entity_safe: str, data_dir: str = "data") -> str:
    """
    Resolve a file path template with placeholders.
    
    Args:
        path_template: Template with {entity_safe} placeholder
        doi_hash: DOI hash
        entity_safe: Safe entity name
        data_dir: Data directory root
        
    Returns:
        Resolved absolute file path
    """
    # Replace placeholder
    resolved = path_template.replace("{entity_safe}", entity_safe)
    # Build full path
    return os.path.join(data_dir, doi_hash, resolved)


def load_iterations_config(ontology_name: str) -> dict:
    """Load the iterations configuration for the ontology."""
    config_path = f"ai_generated_contents/iterations/{ontology_name}/iterations.json"
    
    if not os.path.exists(config_path):
        logger.error(f"Iterations config not found: {config_path}")
        return {}
    
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Failed to load iterations config: {e}")
        return {}


def load_top_entities(doi_hash: str, data_dir: str = "data") -> List[Dict]:
    """Load the top entities JSON from iteration 1."""
    json_path = os.path.join(data_dir, doi_hash, "mcp_run", "iter1_top_entities.json")
    
    if not os.path.exists(json_path):
        logger.error(f"Top entities JSON not found: {json_path}")
        return []
    
    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Failed to load top entities: {e}")
        return []


def load_prompt(prompt_path: str) -> str:
    """Load a prompt from a markdown file."""
    if not os.path.exists(prompt_path):
        logger.error(f"Prompt file not found: {prompt_path}")
        return ""
    
    try:
        with open(prompt_path, 'r', encoding='utf-8') as f:
            return f.read()
    except Exception as e:
        logger.error(f"Failed to load prompt: {e}")
        return ""


def load_paper_content(doi_hash: str, data_dir: str = "data") -> str:
    """Load the stitched markdown paper content."""
    md_path = os.path.join(data_dir, doi_hash, f"{doi_hash}_stitched.md")
    
    if not os.path.exists(md_path):
        logger.error(f"Stitched markdown not found: {md_path}")
        return ""
    
    try:
        with open(md_path, 'r', encoding='utf-8') as f:
            return f.read()
    except Exception as e:
        logger.error(f"Failed to load paper content: {e}")
        return ""


async def run_pre_extraction(
    doi_hash: str,
    entity_label: str,
    entity_uri: str,
    paper_content: str,
    prompt_template: str,
    model_key: str,
    iter_num: int,
    data_dir: str = "data"
) -> str:
    """
    Run pre-extraction for an entity (e.g., iteration 3 pre-extraction).
    
    Returns:
        Extracted text content
    """
    safe = _safe_name(entity_label)
    output_dir = os.path.join(data_dir, doi_hash, "pre_extraction")
    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, f"entity_text_{safe}.txt")
    
    # Check if already exists
    if os.path.exists(output_path):
        logger.info(f"    ⏭️  Pre-extraction already exists for '{entity_label}'")
        with open(output_path, 'r', encoding='utf-8') as f:
            return f.read()
    
    logger.info(f"    🔍 Running pre-extraction for '{entity_label}'...")
    
    # Format prompt - CRITICAL: Replace all placeholders
    prompt = prompt_template.replace("{entity_label}", entity_label)
    prompt = prompt.replace("{entity_uri}", entity_uri)
    prompt = prompt.replace("{paper_content}", paper_content)
    prompt = prompt.replace("{context}", paper_content)
    
    # Save full prompt for debugging in organized subfolder
    prompts_dir = os.path.join(data_dir, doi_hash, "prompts", f"iter{iter_num}_pre_extraction")
    os.makedirs(prompts_dir, exist_ok=True)
    prompt_file = os.path.join(prompts_dir, f"{safe}.md")
    try:
        with open(prompt_file, 'w', encoding='utf-8') as f:
            f.write(f"# Iteration {iter_num} Pre-Extraction Prompt\n\n")
            f.write(f"**Entity**: {entity_label}\n\n")
            f.write(f"**Entity URI**: {entity_uri}\n\n")
            f.write(f"**Model**: {get_extraction_model(model_key)}\n\n")
            f.write("---\n\n")
            f.write(prompt)
        logger.info(f"    💾 Saved pre-extraction prompt to: {prompt_file}")
    except Exception as e:
        logger.warning(f"    ⚠️  Failed to save pre-extraction prompt: {e}")
    
    # Get model
    model_name = get_extraction_model(model_key)
    llm = LLMCreator(
        model=model_name,
        model_config=ModelConfig(temperature=0, top_p=1.0),
        remote_model=True,
    ).setup_llm()
    
    # Extract with retries (increased to 5 attempts with validation)
    max_retries = 5
    for attempt in range(max_retries):
        try:
            logger.info(f"    🔍 Running pre-extraction (attempt {attempt + 1}/{max_retries})")
            result = await llm.ainvoke(prompt)
            content = result.content if hasattr(result, 'content') else str(result)
            
            # CRITICAL VALIDATION: Check if content is meaningful
            if not content or not content.strip():
                raise ValueError(f"LLM returned empty content for pre-extraction of '{entity_label}'")
            
            if len(content.strip()) < 50:  # Minimum reasonable content length
                raise ValueError(f"LLM returned suspiciously short content ({len(content)} chars) for pre-extraction of '{entity_label}'")
            
            # Save result to pre_extraction folder
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(content)
            
            # CRITICAL: Verify file was actually written
            if not os.path.exists(output_path):
                raise IOError(f"Failed to write pre-extraction file: {output_path}")
            
            # Verify file has content
            with open(output_path, 'r', encoding='utf-8') as f:
                written_content = f.read()
            if not written_content or not written_content.strip():
                raise IOError(f"Pre-extraction file was created but is empty: {output_path}")
            
            # Also save response in responses folder for tracking
            responses_dir = os.path.join(data_dir, doi_hash, "responses", f"iter{iter_num}_pre_extraction")
            os.makedirs(responses_dir, exist_ok=True)
            response_file = os.path.join(responses_dir, f"{safe}.md")
            with open(response_file, 'w', encoding='utf-8') as f:
                f.write(f"# Iteration {iter_num} Pre-Extraction Response\n\n")
                f.write(f"**Entity**: {entity_label}\n\n")
                f.write(f"**Model**: {model_name}\n\n")
                f.write("---\n\n")
                f.write(content)
            
            logger.info(f"    ✅ Pre-extraction completed ({len(content)} chars) - file verified")
            return content
            
        except Exception as e:
            if attempt < max_retries - 1:
                wait_time = 5 * (attempt + 1)  # Exponential backoff: 5s, 10s, 15s, 20s
                logger.warning(f"    ⚠️  Pre-extraction attempt {attempt + 1}/{max_retries} failed: {e}")
                logger.info(f"    ⏳ Waiting {wait_time}s before retry...")
                await asyncio.sleep(wait_time)
            else:
                logger.error(f"    ❌ Pre-extraction failed after {max_retries} attempts: {e}")
                raise RuntimeError(f"Failed to pre-extract for entity '{entity_label}' after {max_retries} attempts. Last error: {e}")
    
    # Should never reach here due to raise above, but just in case
    raise RuntimeError(f"Failed to pre-extract for entity '{entity_label}' after {max_retries} attempts")


async def run_extraction(
    doi_hash: str,
    entity_label: str,
    entity_uri: str,
    source_text: str,
    prompt_template: str,
    model_key: str,
    hints_file: str,
    iter_num: int,
    use_agent: bool = False,
    mcp_tools: list = None,
    mcp_set_name: str = None
) -> str:
    """
    Run extraction (hints generation) for an entity.
    Can use either a simple LLM or an agent with MCP tools.
    
    Returns:
        Extracted hints content
    """
    # Check if already exists
    if os.path.exists(hints_file):
        logger.info(f"    ⏭️  Extraction already exists for '{entity_label}'")
        with open(hints_file, 'r', encoding='utf-8') as f:
            return f.read()
    
    logger.info(f"    🔍 Running extraction for '{entity_label}'...")
    
    # Format prompt
    prompt = prompt_template.replace("{entity_label}", entity_label)
    prompt = prompt.replace("{entity_uri}", entity_uri)
    prompt = prompt.replace("{paper_content}", source_text)
    prompt = prompt.replace("{context}", source_text)
    
    # Save full prompt for debugging in organized subfolder
    safe = _safe_name(entity_label)
    # Determine the prompt directory based on iteration type
    prompts_dir = os.path.join(os.path.dirname(os.path.dirname(hints_file)), "prompts", f"iter{iter_num}_extraction")
    os.makedirs(prompts_dir, exist_ok=True)
    prompt_file = os.path.join(prompts_dir, f"{safe}.md")
    try:
        with open(prompt_file, 'w', encoding='utf-8') as f:
            f.write(f"# Iteration {iter_num} Extraction Prompt\n\n")
            f.write(f"**Entity**: {entity_label}\n\n")
            f.write(f"**Entity URI**: {entity_uri}\n\n")
            f.write(f"**Model**: {get_extraction_model(model_key)}\n\n")
            if use_agent:
                f.write(f"**Mode**: Agent with MCP tools\n\n")
                f.write(f"**MCP Tools**: {mcp_tools}\n\n")
                f.write(f"**MCP Set**: {mcp_set_name}\n\n")
            else:
                f.write(f"**Mode**: Simple LLM\n\n")
            f.write("---\n\n")
            f.write(prompt)
        logger.info(f"    💾 Saved prompt to: {prompt_file}")
    except Exception as e:
        logger.warning(f"    ⚠️  Failed to save prompt: {e}")
    
    # Get model
    model_name = get_extraction_model(model_key)
    
    # Extract with retries (increased to 5 attempts)
    max_retries = 5
    agent = None  # Initialize agent once outside retry loop
    
    for attempt in range(max_retries):
        try:
            if use_agent and mcp_tools and mcp_set_name:
                # Use agent with MCP tools (e.g., for iter2)
                # Create agent only once on first attempt, reuse for retries
                if agent is None:
                    logger.info(f"    🤖 Initializing agent with MCP tools: {mcp_tools}")
                    agent = BaseAgent(
                        model_name=model_name,
                        model_config=ModelConfig(temperature=0, top_p=1.0),
                        remote_model=True,
                        mcp_tools=mcp_tools,
                        mcp_set_name=mcp_set_name
                    )
                
                logger.info(f"    🔍 Running agent extraction (attempt {attempt + 1}/{max_retries})")
                result, _meta = await agent.run(prompt, recursion_limit=600)
                content = str(result or "")
            else:
                # Use simple LLM (e.g., for iter3, iter4)
                logger.info(f"    🔍 Running simple LLM extraction (attempt {attempt + 1}/{max_retries})")
                llm = LLMCreator(
                    model=model_name,
                    model_config=ModelConfig(temperature=0, top_p=1.0),
                    remote_model=True,
                ).setup_llm()
                result = await llm.ainvoke(prompt)
                content = result.content if hasattr(result, 'content') else str(result)
            
            # CRITICAL VALIDATION: Check if content is meaningful
            if not content or not content.strip():
                raise ValueError(f"LLM returned empty content for entity '{entity_label}'")
            
            if len(content.strip()) < 50:  # Minimum reasonable content length
                raise ValueError(f"LLM returned suspiciously short content ({len(content)} chars) for entity '{entity_label}'")
            
            # Save result to hints file
            os.makedirs(os.path.dirname(hints_file), exist_ok=True)
            with open(hints_file, 'w', encoding='utf-8') as f:
                f.write(content)
            
            # CRITICAL: Verify file was actually written
            if not os.path.exists(hints_file):
                raise IOError(f"Failed to write hints file: {hints_file}")
            
            # Verify file has content
            with open(hints_file, 'r', encoding='utf-8') as f:
                written_content = f.read()
            if not written_content or not written_content.strip():
                raise IOError(f"Hints file was created but is empty: {hints_file}")
            
            # Also save response in responses folder for tracking
            responses_dir = os.path.join(os.path.dirname(os.path.dirname(hints_file)), "responses", f"iter{iter_num}_extraction")
            os.makedirs(responses_dir, exist_ok=True)
            response_file = os.path.join(responses_dir, f"{safe}.md")
            with open(response_file, 'w', encoding='utf-8') as f:
                f.write(f"# Iteration {iter_num} Extraction Response\n\n")
                f.write(f"**Entity**: {entity_label}\n\n")
                f.write(f"**Model**: {model_name}\n\n")
                if use_agent:
                    f.write(f"**Mode**: Agent with MCP tools\n\n")
                    f.write(f"**MCP Tools**: {mcp_tools}\n\n")
                else:
                    f.write(f"**Mode**: Simple LLM\n\n")
                f.write("---\n\n")
                f.write(content)
            
            logger.info(f"    ✅ Extraction completed ({len(content)} chars) - hints file verified")
            return content
            
        except Exception as e:
            if attempt < max_retries - 1:
                wait_time = 5 * (attempt + 1)  # Exponential backoff: 5s, 10s, 15s, 20s
                logger.warning(f"    ⚠️  Extraction attempt {attempt + 1}/{max_retries} failed: {e}")
                logger.info(f"    ⏳ Waiting {wait_time}s before retry...")
                await asyncio.sleep(wait_time)
            else:
                logger.error(f"    ❌ Extraction failed after {max_retries} attempts: {e}")
                raise RuntimeError(f"Failed to extract hints for entity '{entity_label}' after {max_retries} attempts. Last error: {e}")
    
    # Should never reach here due to raise above, but just in case
    raise RuntimeError(f"Failed to extract hints for entity '{entity_label}' after {max_retries} attempts")


# KG building has been moved to a separate pipeline step
# This module ONLY handles extraction (hints generation)


def run_step(doi_hash: str, config: dict) -> bool:
    """
    Main entry point for the main ontology extractions pipeline step.
    
    This step processes iterations 2+ for all top-level entities.
    
    Args:
        doi_hash: The DOI hash to process
        config: Pipeline configuration dictionary
        
    Returns:
        True if all extractions succeeded
    """
    data_dir = config.get("data_dir", "data")
    doi_folder = os.path.join(data_dir, doi_hash)
    
    logger.info(f"▶️  Main Ontology Extractions for {doi_hash}")
    
    # Check if step is already completed
    marker_file = os.path.join(doi_folder, ".main_ontology_extractions_done")
    if os.path.exists(marker_file):
        logger.info(f"  ⏭️  Main ontology extractions already completed (marker exists)")
        return True
    
    # Load meta config to get ontology info
    try:
        from src.pipelines.top_entity_kg_building.build import load_meta_config
        meta_config = load_meta_config()
        main_ontology = meta_config.get("ontologies", {}).get("main", {})
        ontology_name = main_ontology.get("name", "ontosynthesis")
        mcp_set_name = main_ontology.get("mcp_set_name", "run_created_mcp.json")
        mcp_tools = main_ontology.get("mcp_list", ["llm_created_mcp"])
    except Exception as e:
        logger.error(f"❌ Failed to load meta config: {e}")
        return False
    
    # Override with test MCP config if provided
    if "test_mcp_config" in config:
        test_mcp_config = config["test_mcp_config"]
        logger.info(f"  🧪 Using test MCP config: {test_mcp_config}")
        mcp_set_name = test_mcp_config
    
    logger.info(f"  📋 Ontology: {ontology_name}")
    logger.info(f"  🔧 MCP Config: {mcp_set_name}")
    
    # Load iterations config
    iterations_config = load_iterations_config(ontology_name)
    if not iterations_config:
        logger.error("❌ Failed to load iterations configuration")
        return False
    
    iterations = iterations_config.get("iterations", [])
    logger.info(f"  📊 Found {len(iterations)} iterations to process")
    
    # Load top entities
    top_entities = load_top_entities(doi_hash, data_dir)
    if not top_entities:
        logger.error("❌ No top entities found")
        return False
    
    logger.info(f"  🎯 Processing {len(top_entities)} top-level entities")
    
    # Load paper content
    paper_content = load_paper_content(doi_hash, data_dir)
    if not paper_content:
        logger.error("❌ Failed to load paper content")
        return False
    
    # Get skip extraction flags from config
    skip_iter2 = config.get("skip_iter2_extraction", False)
    skip_iter3 = config.get("skip_iter3_extraction", False)
    skip_iter4 = config.get("skip_iter4_extraction", False)
    
    # Process each iteration
    for iteration in iterations:
        iter_num = iteration.get("iteration_number")
        iter_name = iteration.get("name", f"iteration_{iter_num}")
        per_entity = iteration.get("per_entity", False)
        use_agent = iteration.get("use_agent", False)
        has_pre_extraction = iteration.get("has_pre_extraction", False)
        
        logger.info(f"\n  🔄 Iteration {iter_num}: {iter_name}")
        
        # Check if this iteration should be skipped
        if iter_num == 2 and skip_iter2:
            logger.info(f"    ⏭️  Skipping iteration 2 extraction (--skip-iter2-extraction)")
            continue
        if iter_num == 3 and skip_iter3:
            logger.info(f"    ⏭️  Skipping iteration 3 extraction (--skip-iter3-extraction)")
            continue
        if iter_num == 4 and skip_iter4:
            logger.info(f"    ⏭️  Skipping iteration 4 extraction (--skip-iter4-extraction)")
            continue
        
        if not per_entity:
            logger.warning(f"    ⚠️  Iteration {iter_num} is not per-entity, skipping")
            continue
        
        # Get paths and config
        pre_extraction_prompt_path = iteration.get("pre_extraction_prompt")
        extraction_prompt_path = iteration.get("extraction_prompt")
        model_key = iteration.get("model_config_key", f"iter{iter_num}_hints")
        pre_extraction_model_key = iteration.get("pre_extraction_model_key", "iter3_pre_extraction")
        
        # Process each entity
        for entity in top_entities:
            entity_label = entity.get("label", "")
            entity_uri = entity.get("uri", "")
            safe = _safe_name(entity_label)
            
            logger.info(f"  📌 Entity: {entity_label}")
            
            # Get output paths from config (with fallback to defaults)
            outputs = iteration.get("outputs", {})
            hint_file_template = outputs.get("hints_file", f"mcp_run/iter{iter_num}_hints_{{entity_safe}}.txt")
            hint_file = resolve_file_path(hint_file_template, doi_hash, safe, data_dir)
            
            # Ensure directory exists
            os.makedirs(os.path.dirname(hint_file), exist_ok=True)
            
            # Step 1: Pre-extraction (if needed)
            source_text = paper_content
            if has_pre_extraction and pre_extraction_prompt_path:
                logger.info(f"    🔍 Pre-extraction for iteration {iter_num}")
                pre_extraction_prompt = load_prompt(pre_extraction_prompt_path)
                if pre_extraction_prompt:
                    try:
                        pre_extracted_text = asyncio.run(run_pre_extraction(
                            doi_hash, entity_label, entity_uri, paper_content,
                            pre_extraction_prompt, pre_extraction_model_key, iter_num, data_dir
                        ))
                        if pre_extracted_text:
                            source_text = pre_extracted_text
                    except Exception as e:
                        logger.error(f"    ❌ Pre-extraction failed: {e}")
            
            # Step 2: Extraction (hints generation)
            # For iter2: extraction uses agent with MCP tools
            # For iter3/4: extraction uses simple LLM
            if extraction_prompt_path:
                logger.info(f"    📝 Extraction for iteration {iter_num}")
                extraction_prompt = load_prompt(extraction_prompt_path)
                if extraction_prompt:
                    # Determine if this iteration uses agent for extraction
                    # (iter2 uses agent, iter3/4 use simple LLM)
                    extraction_uses_agent = use_agent and (
                        iteration.get("extraction_mcp_tools") is not None or 
                        iteration.get("mcp_tools") is not None
                    )
                    
                    # Get MCP configuration for extraction (if using agent)
                    # Use extraction-specific config if available, otherwise fall back to general config
                    extraction_mcp_set = iteration.get("extraction_mcp_set_name") or iteration.get("mcp_set_name") if extraction_uses_agent else None
                    extraction_mcp_tools = iteration.get("extraction_mcp_tools") or iteration.get("mcp_tools") if extraction_uses_agent else None
                    
                    # If test MCP config is provided, override the set name for generated tools
                    # (but keep extraction-specific tools like pubchem, websearch unchanged)
                    if "test_mcp_config" in config and extraction_mcp_tools and "llm_created_mcp" in extraction_mcp_tools:
                        extraction_mcp_set = config["test_mcp_config"]
                    
                    try:
                        hints = asyncio.run(run_extraction(
                            doi_hash, entity_label, entity_uri, source_text,
                            extraction_prompt, model_key, hint_file, iter_num,
                            use_agent=extraction_uses_agent,
                            mcp_tools=extraction_mcp_tools,
                            mcp_set_name=extraction_mcp_set
                        ))
                    except Exception as e:
                        logger.error(f"    ❌ Extraction failed: {e}")
                        continue
        
        # Handle sub-iterations (enrichment steps like 3.1, 3.2)
        sub_iterations = iteration.get("sub_iterations", [])
        for sub_iter in sub_iterations:
            sub_iter_num = sub_iter.get("iteration_number")
            sub_iter_name = sub_iter.get("name", f"iteration_{sub_iter_num}")
            enriches = sub_iter.get("enriches")
            
            logger.info(f"\n  🔄 Sub-iteration {sub_iter_num}: {sub_iter_name} (enriches iter {enriches})")
            
            sub_extraction_prompt_path = sub_iter.get("extraction_prompt")
            sub_model_key = sub_iter.get("model_config_key", f"iter{sub_iter_num}_enrichment")
            
            if not sub_extraction_prompt_path:
                logger.warning(f"    ⚠️  No extraction prompt for sub-iteration {sub_iter_num}")
                continue
            
            sub_extraction_prompt = load_prompt(sub_extraction_prompt_path)
            if not sub_extraction_prompt:
                continue
            
            # Process each entity for enrichment
            for entity in top_entities:
                entity_label = entity.get("label", "")
                safe = _safe_name(entity_label)
                
                logger.info(f"  📌 Entity: {entity_label}")
                
                # Get input/output paths from config
                sub_inputs = sub_iter.get("inputs", {})
                sub_outputs = sub_iter.get("outputs", {})
                
                # Resolve done marker path
                done_marker_template = sub_outputs.get("done_marker", f"mcp_run/iter{enriches}_{sub_iter_num}_done_{{entity_safe}}.marker")
                done_marker = resolve_file_path(done_marker_template, doi_hash, safe, data_dir)
                
                if os.path.exists(done_marker):
                    logger.info(f"    ⏭️  Sub-iteration {sub_iter_num} already completed")
                    continue
                
                # Resolve base hints file path
                base_hints_template = sub_inputs.get("base_hints", f"mcp_run/iter{enriches}_hints_{{entity_safe}}.txt")
                base_hint_file = resolve_file_path(base_hints_template, doi_hash, safe, data_dir)
                
                if not os.path.exists(base_hint_file):
                    logger.warning(f"    ⚠️  Base hints file not found: {base_hint_file}")
                    continue
                
                # Read base hints
                with open(base_hint_file, 'r', encoding='utf-8') as f:
                    base_hints = f.read()
                
                # Resolve pre-extracted text path
                pre_extracted_template = sub_inputs.get("pre_extracted_text", f"llm_based_results/entity_text_{{entity_safe}}.txt")
                entity_text_path = resolve_file_path(pre_extracted_template, doi_hash, safe, data_dir)
                
                if os.path.exists(entity_text_path):
                    with open(entity_text_path, 'r', encoding='utf-8') as f:
                        source_text = f.read()
                else:
                    source_text = paper_content
                
                # Format enrichment prompt
                enrichment_prompt = sub_extraction_prompt
                enrichment_prompt += f"\n\nEntity: {entity_label}\n\n"
                enrichment_prompt += f"Iter{enriches} Results (for guidance):\n{base_hints}\n\n"
                enrichment_prompt += f"Text:\n{source_text}"
                
                # Save enrichment prompt in organized subfolder
                prompts_dir = os.path.join(data_dir, doi_hash, "prompts", f"iter{sub_iter_num}_enrichment")
                os.makedirs(prompts_dir, exist_ok=True)
                prompt_file = os.path.join(prompts_dir, f"{safe}.md")
                try:
                    with open(prompt_file, 'w', encoding='utf-8') as f:
                        f.write(f"# Sub-iteration {sub_iter_num} Enrichment Prompt\n\n")
                        f.write(f"**Entity**: {entity_label}\n\n")
                        f.write(f"**Enriches**: Iteration {enriches}\n\n")
                        f.write(f"**Model**: {get_extraction_model(sub_model_key)}\n\n")
                        f.write("---\n\n")
                        f.write(enrichment_prompt)
                    logger.info(f"    💾 Saved enrichment prompt to: {prompt_file}")
                except Exception as e:
                    logger.warning(f"    ⚠️  Failed to save enrichment prompt: {e}")
                
                logger.info(f"    🔍 Running enrichment for sub-iteration {sub_iter_num}")
                
                # Run enrichment extraction with retry logic
                max_retries = 3
                enriched_content = None
                for attempt in range(max_retries):
                    try:
                        async def _run_enrichment():
                            model_name = get_extraction_model(sub_model_key)
                            llm = LLMCreator(
                                model=model_name,
                                model_config=ModelConfig(temperature=0, top_p=1.0),
                                remote_model=True,
                            ).setup_llm()
                            
                            result = await llm.ainvoke(enrichment_prompt)
                            enriched_content = result.content if hasattr(result, 'content') else str(result)
                            return enriched_content
                        
                        logger.info(f"    Enrichment attempt {attempt + 1}/{max_retries}")
                        enriched_content = asyncio.run(_run_enrichment())
                        
                        if enriched_content and enriched_content.strip():
                            logger.info(f"    ✅ Enrichment succeeded on attempt {attempt + 1}")
                            break
                        else:
                            logger.warning(f"    ⚠️  Empty enrichment result on attempt {attempt + 1}")
                            if attempt < max_retries - 1:
                                wait_time = 5 * (attempt + 1)
                                logger.info(f"    Waiting {wait_time}s before retry...")
                                import time
                                time.sleep(wait_time)
                    except Exception as e:
                        logger.error(f"    ❌ Enrichment attempt {attempt + 1}/{max_retries} failed: {e}")
                        if attempt < max_retries - 1:
                            wait_time = 5 * (attempt + 1)
                            logger.info(f"    Waiting {wait_time}s before retry...")
                            import time
                            time.sleep(wait_time)
                        else:
                            raise RuntimeError(f"Enrichment failed after {max_retries} attempts. Last error: {e}")
                
                if not enriched_content or not enriched_content.strip():
                    logger.error(f"    ❌ Enrichment returned empty content after {max_retries} attempts")
                    continue
                
                # Write enriched hints
                # Get output hints file path (usually same as input to overwrite)
                output_hints_template = sub_outputs.get("hints_file", base_hints_template)
                output_hints_file = resolve_file_path(output_hints_template, doi_hash, safe, data_dir)
                
                # Write enriched hints
                os.makedirs(os.path.dirname(output_hints_file), exist_ok=True)
                with open(output_hints_file, 'w', encoding='utf-8') as f:
                    f.write(enriched_content)
                
                # Save response in responses folder for tracking
                responses_dir = os.path.join(data_dir, doi_hash, "responses", f"iter{sub_iter_num}_enrichment")
                os.makedirs(responses_dir, exist_ok=True)
                response_file = os.path.join(responses_dir, f"{safe}.md")
                with open(response_file, 'w', encoding='utf-8') as f:
                    f.write(f"# Sub-iteration {sub_iter_num} Enrichment Response\n\n")
                    f.write(f"**Entity**: {entity_label}\n\n")
                    f.write(f"**Enriches**: Iteration {enriches}\n\n")
                    f.write(f"**Model**: {get_extraction_model(sub_model_key)}\n\n")
                    f.write("---\n\n")
                    f.write(enriched_content)
                
                # Create done marker
                os.makedirs(os.path.dirname(done_marker), exist_ok=True)
                with open(done_marker, 'w', encoding='utf-8') as f:
                    f.write("done")
                
                logger.info(f"    ✅ Enrichment completed for sub-iteration {sub_iter_num}")
    
    # Create completion marker
    try:
        with open(marker_file, 'w') as f:
            f.write("completed\n")
        logger.info(f"  📌 Created completion marker")
    except Exception as e:
        logger.warning(f"  ⚠️  Failed to create completion marker: {e}")
    
    logger.info(f"✅ Main Ontology Extractions completed for {doi_hash}")
    return True


if __name__ == "__main__":
    # Example usage for standalone testing
    if len(sys.argv) > 1:
        test_doi_hash = sys.argv[1]
        test_config = {
            "data_dir": "data"
        }
        print(f"Running main ontology extractions step for DOI hash: {test_doi_hash}")
        success = run_step(test_doi_hash, test_config)
        print(f"Main ontology extractions step {'succeeded' if success else 'failed'}.")
    else:
        print("Usage: python -m src.pipelines.main_ontology_extractions.extract <doi_hash>")

