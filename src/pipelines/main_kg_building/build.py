"""
Main KG Building Module

Handles knowledge graph building for iterations 2, 3, and 4.
Uses BaseAgent with MCP tools to build TTL files from extraction hints.
"""

import os
import json
import asyncio
import shutil
import tempfile
from pathlib import Path
from filelock import FileLock
from typing import Dict, List

from models.BaseAgent import BaseAgent
from models.ModelConfig import ModelConfig
from src.utils.global_logger import get_logger
from src.utils.extraction_models import get_extraction_model
from src.pipelines.utils.ttl_publisher import load_meta_task_config, publish_ttl

logger = get_logger("pipeline", "MainKGBuilding")

# Generated artifacts resolver (candidate-first)
def resolve_generated_file(path: str, project_root: str = ".") -> str:
    """
    Resolve a generated artifact path.

    Prefer `ai_generated_contents_candidate/` (where generation writes in this repo),
    then fall back to `ai_generated_contents/` if present.

    Returns an absolute-ish path joined with project_root, suitable for open().
    """
    rel = (path or "").replace("\\", "/")
    candidates: list[str] = []
    if rel.startswith("ai_generated_contents/"):
        candidates.append(rel.replace("ai_generated_contents/", "ai_generated_contents_candidate/", 1))
        candidates.append(rel)
    elif rel.startswith("ai_generated_contents_candidate/"):
        candidates.append(rel)
        candidates.append(rel.replace("ai_generated_contents_candidate/", "ai_generated_contents/", 1))
    else:
        candidates.append(rel)

    for c in candidates:
        full = os.path.join(project_root, c)
        if c and os.path.exists(full):
            return full
    # Default to the first candidate even if it doesn't exist (caller may log)
    return os.path.join(project_root, candidates[0])

# Global state management for MCP server
GLOBAL_STATE_DIR = "data"
GLOBAL_STATE_JSON = os.path.join(GLOBAL_STATE_DIR, "global_state.json")
GLOBAL_STATE_LOCK = os.path.join(GLOBAL_STATE_DIR, "global_state.lock")


def write_global_state(doi: str, top_level_entity_name: str, top_level_entity_iri: str | None = None):
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
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
        os.replace(tmp, GLOBAL_STATE_JSON)
        logger.info(f"Global state written: doi={doi}, entity={top_level_entity_name}")
    finally:
        lock.release()


def _safe_name(label: str) -> str:
    """Convert entity label to safe filename."""
    return (label or "entity").replace(" ", "_").replace("/", "_").replace("\\", "_").replace(":", "_")

def _try_copy_entity_ttl_to_intermediate(
    *,
    doi_folder: str,
    entity_label: str,
    entity_safe: str,
    intermediate_ttl: str,
) -> bool:
    """
    Copy the best-available per-entity OntoSynthesis TTL into `intermediate_ttl`.

    The created ontosynthesis MCP server persists graphs under:
      - data/<hash>/memory/<entity_safe>.ttl
    and exports snapshots under:
      - data/<hash>/exports/<entity_safe>_<timestamp>.ttl

    Older pipelines used:
      - data/<hash>/output.ttl
    """
    os.makedirs(os.path.dirname(intermediate_ttl), exist_ok=True)

    memory_ttl = os.path.join(doi_folder, "memory", f"{entity_safe}.ttl")
    if os.path.exists(memory_ttl):
        shutil.copy2(memory_ttl, intermediate_ttl)
        logger.info(f"    ✅ Saved intermediate TTL from memory: {os.path.basename(memory_ttl)}")
        return True

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
                shutil.copy2(latest, intermediate_ttl)
                logger.info(f"    ✅ Saved intermediate TTL from exports: {os.path.basename(latest)}")
                return True
    except Exception as e:
        logger.warning(f"    ⚠️  Error scanning exports TTLs for {entity_label}: {e}")

    # Backward-compat fallback (rarely produced by the created MCP server)
    output_ttl = os.path.join(doi_folder, "output.ttl")
    if os.path.exists(output_ttl):
        shutil.copy2(output_ttl, intermediate_ttl)
        logger.info(f"    ✅ Saved intermediate TTL from output.ttl")
        return True

    logger.warning(f"    ⚠️  No entity TTL found for {entity_label} (safe={entity_safe})")
    return False


def load_prompt(prompt_path: str, project_root: str = ".") -> str:
    """Load prompt from file."""
    full_path = resolve_generated_file(prompt_path, project_root=project_root)
    if not os.path.exists(full_path):
        logger.error(f"Prompt file not found: {full_path}")
        return ""
    
    try:
        with open(full_path, 'r', encoding='utf-8') as f:
            return f.read()
    except Exception as e:
        logger.error(f"Failed to load prompt from {full_path}: {e}")
        return ""


async def run_kg_building_agent(
    doi_hash: str,
    entity_label: str,
    entity_uri: str,
    hints_content: str,
    kg_prompt: str,
    iter_num: int,
    mcp_tools: List[str],
    mcp_set_name: str,
    data_dir: str = "data"
) -> str:
    """
    Run KG building agent for a single entity.
    
    Args:
        doi_hash: DOI hash for the paper
        entity_label: Entity label
        entity_uri: Entity URI
        hints_content: Extraction hints content
        kg_prompt: KG building prompt template
        iter_num: Iteration number
        mcp_tools: List of MCP tools to use
        mcp_set_name: MCP set name
        data_dir: Data directory
        
    Returns:
        Agent response content
    """
    safe = _safe_name(entity_label)
    doi_folder = os.path.join(data_dir, doi_hash)
    
    # Replace placeholders in prompt
    prompt = kg_prompt.replace("{doi}", doi_hash)
    prompt = prompt.replace("{entity_label}", entity_label)
    prompt = prompt.replace("{entity_uri}", entity_uri)
    prompt = prompt.replace("{paper_content}", hints_content)
    
    # Add orphan entity check instruction
    prompt += ("\n\n"
              "Before exporting the final TTL/memory, call the tool `check_orphan_entities` to detect any orphan entities. "
              "If any are found, attempt to connect them appropriately (e.g., attach to synthesis, steps, IO, or parameters). "
              "If you cannot connect some, list their details in your response and proceed with export.")
    
    # Save full prompt
    kg_prompts_dir = os.path.join(doi_folder, "prompts", f"iter{iter_num}_kg_building")
    os.makedirs(kg_prompts_dir, exist_ok=True)
    prompt_file = os.path.join(kg_prompts_dir, f"{safe}.md")
    try:
        with open(prompt_file, 'w', encoding='utf-8') as f:
            f.write(f"# Iteration {iter_num} KG Building Prompt\n\n")
            f.write(f"**Entity**: {entity_label}\n\n")
            f.write(f"**Entity URI**: {entity_uri}\n\n")
            f.write(f"**MCP Tools**: {mcp_tools}\n\n")
            f.write(f"**MCP Set**: {mcp_set_name}\n\n")
            f.write("---\n\n")
            f.write(prompt)
    except Exception as e:
        logger.warning(f"Failed to save prompt to {prompt_file}: {e}")
    
    # Write global state for MCP server
    write_global_state(doi_hash, safe, entity_uri)
    
    # Create agent
    logger.info(f"    🚀 Running KG building agent for '{entity_label}' (iter {iter_num})")
    agent = BaseAgent(
        model_name="gpt-4o",  # Default model for KG building
        model_config=ModelConfig(temperature=0.1, top_p=0.1),
        remote_model=True,
        mcp_tools=mcp_tools,
        mcp_set_name=mcp_set_name,
    )
    
    # Run agent with retry
    max_retries = 3
    for attempt in range(max_retries):
        try:
            logger.info(f"    Agent execution attempt {attempt + 1}/{max_retries}")
            response, metadata = await agent.run(prompt, recursion_limit=80)
            logger.info(f"    ✅ Agent execution succeeded on attempt {attempt + 1}")
            
            # CRITICAL: Wait for MCP server operations to complete before proceeding
            # The MCP server is a separate process that may have delayed I/O operations
            logger.info(f"    ⏳ Waiting for MCP server operations to complete...")
            await asyncio.sleep(3)
            
            # Note: Direct export removed to avoid race condition with global state
            # The agent should call export_memory through MCP tools with explicit parameters
            logger.info(f"    ✅ MCP server operations completed")
            
            # Save response
            kg_responses_dir = os.path.join(doi_folder, "responses", f"iter{iter_num}_kg_building")
            os.makedirs(kg_responses_dir, exist_ok=True)
            response_file = os.path.join(kg_responses_dir, f"{safe}.md")
            try:
                with open(response_file, 'w', encoding='utf-8') as f:
                    f.write(f"# Iteration {iter_num} KG Building Response\n\n")
                    f.write(f"**Entity**: {entity_label}\n\n")
                    f.write("---\n\n")
                    f.write(str(response))
            except Exception as e:
                logger.warning(f"Failed to save response to {response_file}: {e}")
            
            return str(response)
            
        except Exception as e:
            logger.error(f"    Agent execution failed on attempt {attempt + 1}/{max_retries}: {e}")
            if attempt < max_retries - 1:
                wait_time = 5 * (attempt + 1)
                logger.info(f"    Waiting {wait_time}s before retry...")
                await asyncio.sleep(wait_time)
            else:
                logger.error(f"    ❌ Agent execution failed after {max_retries} attempts")
                raise RuntimeError(f"Agent execution failed after {max_retries} attempts. Last error: {e}")


async def _process_iterations(doi_hash: str, config: dict, doi_folder: str, 
                              top_entities: list, iterations: list, 
                              mcp_run_dir: str, data_dir: str, project_root: str,
                              ontology_name: str = "ontosynthesis") -> bool:
    """Async helper to process all iterations and entities."""
    meta_cfg = load_meta_task_config()
    intermediate_ttl_dir = os.path.join(doi_folder, "intermediate_ttl_files")
    os.makedirs(intermediate_ttl_dir, exist_ok=True)
    
    # Process iterations 2, 3, 4 (skip iteration 1 - handled by top_entity_kg_building)
    for iteration in iterations:
        iter_num = iteration.get("iteration_number")
        if iter_num == 1:
            continue  # Skip iteration 1
        
        iter_name = iteration.get("name", f"iteration_{iter_num}")
        kg_building_prompt_path = iteration.get("kg_building_prompt")
        
        if not kg_building_prompt_path:
            logger.info(f"  ⏭️  No KG building for iteration {iter_num}")
            continue
        
        # Get MCP configuration
        mcp_set_name = iteration.get("mcp_set_name", "run_created_mcp.json")
        mcp_tools = iteration.get("mcp_tools", ["llm_created_mcp"])
        
        # Override with test MCP config if provided
        if "test_mcp_config" in config:
            mcp_set_name = config["test_mcp_config"]
        
        logger.info(f"\n  🔄 Iteration {iter_num}: {iter_name} - KG Building")
        logger.info(f"    MCP Set: {mcp_set_name}, Tools: {mcp_tools}")
        
        # Load KG building prompt
        kg_prompt = load_prompt(kg_building_prompt_path, project_root)
        if not kg_prompt:
            logger.error(f"  ❌ Failed to load KG building prompt for iteration {iter_num}")
            continue
        
        # Process each entity sequentially with strict isolation
        for idx, entity in enumerate(top_entities):
            entity_label = entity.get("label", "")
            entity_uri = entity.get("uri", "")
            safe = _safe_name(entity_label)
            
            logger.info(f"  📌 Entity {idx+1}/{len(top_entities)}: {entity_label}")
            
            # Check if KG building already done
            response_file = os.path.join(doi_folder, "responses", f"iter{iter_num}_kg_building", f"{safe}.md")
            intermediate_ttl = os.path.join(intermediate_ttl_dir, f"iteration_{iter_num}_{safe}.ttl")
            
            if os.path.exists(response_file) and os.path.exists(intermediate_ttl):
                logger.info(f"    ⏭️  KG building already completed")
                continue
            
            # Load hints
            hints_file = os.path.join(mcp_run_dir, f"iter{iter_num}_hints_{safe}.txt")
            if not os.path.exists(hints_file):
                logger.warning(f"    ⚠️  Hints file not found: {hints_file}")
                continue
            
            try:
                with open(hints_file, 'r', encoding='utf-8') as f:
                    hints_content = f.read()
            except Exception as e:
                logger.error(f"    ❌ Failed to read hints file: {e}")
                continue
            
            # Run KG building agent
            try:
                response = await run_kg_building_agent(
                    doi_hash=doi_hash,
                    entity_label=entity_label,
                    entity_uri=entity_uri,
                    hints_content=hints_content,
                    kg_prompt=kg_prompt,
                    iter_num=iter_num,
                    mcp_tools=mcp_tools,
                    mcp_set_name=mcp_set_name,
                    data_dir=data_dir
                )
                
                # Copy output.ttl to intermediate TTL file
                # In test mode, look for entity-specific TTL in ontosynthesis_output/
                test_mode = "test_mcp_config" in config
                
                if test_mode:
                    # Test mode MUST still prefer the canonical MCP persistence locations (memory/ + exports/)
                    # over any previously-published {ontology}_output snapshots, otherwise we can "lock in"
                    # an early (iter2) TTL that lacks later additions like steps.
                    found = False

                    # 1) Prefer memory/ (canonical; should contain the latest merged graph)
                    mem_dir = os.path.join(doi_folder, "memory")
                    mem_candidates = [
                        os.path.join(mem_dir, f"{safe}.ttl"),
                        os.path.join(mem_dir, f"{safe.lower()}.ttl"),
                        os.path.join(mem_dir, f"{entity_label}.ttl"),
                    ]
                    for mem_path in mem_candidates:
                        if os.path.exists(mem_path):
                            shutil.copy2(mem_path, intermediate_ttl)
                            logger.info(f"    ✅ [TEST MODE] Saved from memory/{os.path.basename(mem_path)}")
                            found = True
                            break

                    # 2) Fallback to latest exports snapshot (if any)
                    if not found:
                        if _try_copy_entity_ttl_to_intermediate(
                            doi_folder=doi_folder,
                            entity_label=entity_label,
                            entity_safe=safe,
                            intermediate_ttl=intermediate_ttl,
                        ):
                            found = True

                    # 3) Last resort: previously published output dir (may be stale)
                    if not found:
                        test_output_dir = os.path.join(doi_folder, f"{ontology_name}_output")
                        entity_slug = entity_label.lower().replace(" ", "-").replace("_", "-")
                        test_candidates = [
                            os.path.join(test_output_dir, f"{safe}.ttl"),
                            os.path.join(test_output_dir, f"{entity_slug}.ttl"),
                            os.path.join(test_output_dir, f"{safe.lower()}.ttl"),
                            os.path.join(test_output_dir, f"{entity_label}.ttl"),
                        ]
                        for candidate in test_candidates:
                            if os.path.exists(candidate):
                                shutil.copy2(candidate, intermediate_ttl)
                                logger.info(f"    ✅ [TEST MODE] Saved from {os.path.basename(candidate)} (stale fallback)")
                                found = True
                                break

                    if not found:
                        logger.warning(f"    ⚠️  [TEST MODE] No TTL found (memory/exports/output) for {entity_label}")
                else:
                    _try_copy_entity_ttl_to_intermediate(
                        doi_folder=doi_folder,
                        entity_label=entity_label,
                        entity_safe=safe,
                        intermediate_ttl=intermediate_ttl,
                    )

                # Publish a deterministic per-entity TTL for downstream steps:
                # data/<hash>/<output_dir_from_config>/{entity_safe}.ttl
                published = publish_ttl(
                    doi_hash=doi_hash,
                    ontology_name=ontology_name,
                    entity_safe=safe,
                    data_dir=data_dir,
                    meta_cfg=meta_cfg,
                    src_candidates=[intermediate_ttl],
                )
                if published:
                    logger.info(f"    ✅ Published entity TTL: {os.path.relpath(published, doi_folder)}")
                else:
                    logger.warning(f"    ⚠️  Failed to publish entity TTL for {entity_label}")
                
            except Exception as e:
                logger.error(f"    ❌ KG building failed for '{entity_label}': {e}")
                continue
            
            # CRITICAL: Synchronization point between entities
            # Wait to ensure all MCP server file operations are flushed to disk
            # before moving to next entity (which will overwrite global state)
            if idx < len(top_entities) - 1:  # Not the last entity
                logger.info(f"    🔒 Entity synchronization point (preparing for next entity)...")
                await asyncio.sleep(2)
                logger.info(f"    ✅ Ready for next entity")
    
    return True


def run_step(doi_hash: str, config: dict) -> bool:
    """
    Main KG Building step: Build knowledge graphs for iterations 2, 3, and 4.
    
    Args:
        doi_hash: DOI hash for the paper
        config: Pipeline configuration dictionary
        
    Returns:
        True if KG building succeeded
    """
    # Extract config parameters
    data_dir = config.get("data_dir", "data")
    project_root = config.get("project_root", ".")
    
    logger.info(f"🏗️  Starting main KG building for DOI: {doi_hash}")
    
    doi_folder = os.path.join(data_dir, doi_hash)
    if not os.path.exists(doi_folder):
        logger.error(f"DOI folder not found: {doi_folder}")
        return False
    
    # Check if step is already completed
    marker_file = os.path.join(doi_folder, ".main_kg_building_done")
    if os.path.exists(marker_file):
        logger.info(f"  ⏭️  Main KG building already completed (marker exists)")
        return True
    
    # Load top entities
    mcp_run_dir = os.path.join(doi_folder, "mcp_run")
    entities_path = os.path.join(mcp_run_dir, "iter1_top_entities.json")
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
    
    # Load iterations config
    # Determine ontology name (currently only main ontology supported here)
    ontology_name = config.get("ontology_name", "ontosynthesis")

    # Candidate-first resolution (supports repos without ai_generated_contents/)
    iterations_config_path = resolve_generated_file(
        f"ai_generated_contents/iterations/{ontology_name}/iterations.json",
        project_root=project_root,
    )
    if not os.path.exists(iterations_config_path):
        logger.error(f"Iterations config not found: {iterations_config_path}")
        return False
    
    try:
        with open(iterations_config_path, 'r', encoding='utf-8') as f:
            iterations_config = json.load(f)
        iterations = iterations_config.get("iterations", [])
    except Exception as e:
        logger.error(f"Failed to load iterations config: {e}")
        return False
    
    # Process all iterations and entities with proper async handling
    try:
        success = asyncio.run(_process_iterations(
            doi_hash=doi_hash,
            config=config,
            doi_folder=doi_folder,
            top_entities=top_entities,
            iterations=iterations,
            mcp_run_dir=mcp_run_dir,
            data_dir=data_dir,
            project_root=project_root,
            ontology_name=ontology_name,
        ))
        
        if not success:
            logger.error(f"  ❌ Iteration processing failed")
            return False
    except Exception as e:
        logger.error(f"  ❌ Iteration processing raised exception: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    # Create completion marker
    try:
        with open(marker_file, 'w') as f:
            f.write("completed\n")
        logger.info(f"  📌 Created completion marker")
    except Exception as e:
        logger.warning(f"  ⚠️  Failed to create completion marker: {e}")
    
    logger.info(f"✅ Main KG building completed for DOI: {doi_hash}")
    return True


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python -m src.pipelines.main_kg_building.build <doi_hash>")
        sys.exit(1)
    
    # Create config dict for standalone usage
    config = {
        "data_dir": "data",
        "project_root": "."
    }
    
    success = run_step(sys.argv[1], config)
    sys.exit(0 if success else 1)

