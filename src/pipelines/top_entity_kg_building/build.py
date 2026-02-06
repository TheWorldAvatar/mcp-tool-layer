"""
Top Entity KG Building Pipeline Step

This step extracts top-level entities from the stitched markdown and builds
a knowledge graph using an LLM agent with MCP tools.
"""
import os
import sys
import json
import asyncio
import tempfile
from typing import List, Dict
from filelock import FileLock

# Add project root to path for imports
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from models.BaseAgent import BaseAgent
from models.ModelConfig import ModelConfig
from src.utils.global_logger import get_logger
from src.pipelines.utils.ttl_publisher import publish_top_ttl

logger = get_logger("pipeline", "top_entity_kg_building")

def resolve_generated_file(path: str) -> str:
    """
    Resolve a generated artifact path.

    Prefer `ai_generated_contents_candidate/` (where generation writes in this repo),
    then fall back to `ai_generated_contents/` if present.
    """
    path = (path or "").replace("\\", "/")
    candidates: list[str] = []
    if path.startswith("ai_generated_contents/"):
        candidates.append(path.replace("ai_generated_contents/", "ai_generated_contents_candidate/", 1))
        candidates.append(path)
    elif path.startswith("ai_generated_contents_candidate/"):
        candidates.append(path)
        candidates.append(path.replace("ai_generated_contents_candidate/", "ai_generated_contents/", 1))
    else:
        candidates.append(path)

    for p in candidates:
        if p and os.path.exists(p):
            return p
    return candidates[0]

# -------------------- Global state writer --------------------
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


def load_meta_config(config_path: str = "configs/meta_task/meta_task_config.json") -> dict:
    """Load the meta task configuration."""
    if not os.path.exists(config_path):
        logger.error(f"Meta config not found: {config_path}")
        return {}
    
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
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
        with open(prompt_path, 'r', encoding='utf-8') as f:
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
        with open(hints_path, 'r', encoding='utf-8') as f:
            return f.read()
    except Exception as e:
        logger.error(f"Failed to load extraction hints: {e}")
        return ""


async def run_kg_building_agent(
    doi_hash: str,
    prompt_template: str,
    hints: str,
    mcp_tools: List[str],
    mcp_set_name: str,
    model_name: str = "gpt-4o",
    temperature: float = 0.1,
    top_p: float = 0.1
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
    # Format the prompt with the hints
    instruction = prompt_template.replace("{paper_content}", hints)
    instruction = instruction.replace("{doi}", doi_hash)
    
    # Write global state for MCP server (iteration 1 uses "top" as entity name)
    logger.info(f"📝 Writing global state for MCP server")
    write_global_state(doi_hash, "top")
    
    # Create agent with MCP tools
    agent = BaseAgent(
        model_name=model_name,
        model_config=ModelConfig(temperature=temperature, top_p=top_p),
        remote_model=True,
        mcp_tools=mcp_tools,
        mcp_set_name=mcp_set_name
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
            
            response, metadata = await agent.run(instruction, recursion_limit=600)
            logger.info(f"✅ Agent completed successfully on attempt {attempt + 1}")
            return response, metadata
            
        except Exception as e:
            logger.error(f"❌ Agent execution failed on attempt {attempt + 1}/{max_retries}: {e}")
            
            if attempt < max_retries - 1:
                delay = retry_delays[attempt]
                logger.info(f"⏳ Waiting {delay}s before retry...")
                await asyncio.sleep(delay)
            else:
                logger.error(f"❌ All {max_retries} attempts failed for KG building agent")
                raise


def save_agent_response(doi_hash: str, response: str, data_dir: str = "data") -> None:
    """Save the agent response to a file."""
    output_dir = os.path.join(data_dir, doi_hash, "kg_building")
    os.makedirs(output_dir, exist_ok=True)
    
    response_path = os.path.join(output_dir, "iter1_response.md")
    
    try:
        with open(response_path, 'w', encoding='utf-8') as f:
            f.write(f"# Iteration 1 - Top Entity KG Building\n\n")
            f.write(f"## Response\n\n{response}")
        logger.info(f"✅ Saved agent response to {response_path}")
    except Exception as e:
        logger.error(f"Failed to save agent response: {e}")


def copy_output_ttl(doi_hash: str, data_dir: str = "data", test_mode: bool = False, ontology_name: str = "ontosynthesis") -> bool:
    """
    Copy the output.ttl to iteration_1.ttl.
    
    In normal mode:
        - Looks for: output.ttl or output_top.ttl in doi_hash root
    
    In test mode:
        - Looks for: top.ttl in {ontology_name}_output/ directory
    """
    doi_folder = os.path.join(data_dir, doi_hash)
    iteration_1_ttl = os.path.join(doi_folder, "iteration_1.ttl")
    
    if test_mode:
        # Test mode: Look for top.ttl in ontosynthesis_output/
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
                    logger.info(f"✅ [TEST MODE] Saved iteration_1.ttl from {os.path.basename(candidate)}")
                    _ = publish_top_ttl(
                        doi_hash=doi_hash,
                        ontology_name=ontology_name,
                        data_dir=data_dir,
                        meta_cfg=load_meta_config(),
                        src_candidates=[iteration_1_ttl, candidate],
                    )
                    return True
                except Exception as e:
                    logger.error(f"Failed to copy {candidate}: {e}")

        # Fallbacks: candidate-first MCP servers in this repo often persist the working graph under
        # data/<hash>/memory/top.ttl and/or export snapshots under data/<hash>/exports/top_*.ttl.
        memory_top_ttl = os.path.join(doi_folder, "memory", "top.ttl")
        exports_dir = os.path.join(doi_folder, "exports")
        if os.path.exists(memory_top_ttl):
            try:
                import shutil
                shutil.copy2(memory_top_ttl, iteration_1_ttl)
                logger.info("✅ [TEST MODE] Saved iteration_1.ttl from memory/top.ttl")
                _ = publish_top_ttl(
                    doi_hash=doi_hash,
                    ontology_name=ontology_name,
                    data_dir=data_dir,
                    meta_cfg=load_meta_config(),
                    src_candidates=[iteration_1_ttl, memory_top_ttl],
                )
                return True
            except Exception as e:
                logger.error(f"Failed to copy memory/top.ttl: {e}")
                return False

        try:
            if os.path.isdir(exports_dir):
                export_candidates = [
                    os.path.join(exports_dir, f)
                    for f in os.listdir(exports_dir)
                    if f.lower().startswith("top_") and f.lower().endswith(".ttl")
                ]
                if export_candidates:
                    export_candidates.sort(key=lambda p: os.path.getmtime(p), reverse=True)
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
                        meta_cfg=load_meta_config(),
                        src_candidates=[iteration_1_ttl, latest],
                    )
                    return True
        except Exception as e:
            logger.warning(f"⚠️  [TEST MODE] Failed scanning exports fallback: {e}")

        logger.warning(
            f"⚠️  [TEST MODE] No top.ttl found in {test_output_dir} and no memory/export fallback found"
        )
        return False
    else:
        # Normal mode: Look for output.ttl or output_top.ttl
        output_ttl = os.path.join(doi_folder, "output.ttl")
        output_top_ttl = os.path.join(doi_folder, "output_top.ttl")
        # Candidate-first MCP servers in this repo persist the working graph under memory/
        # and (optionally) export snapshots under exports/. They DO NOT necessarily write
        # output.ttl/output_top.ttl into the DOI folder root.
        memory_top_ttl = os.path.join(doi_folder, "memory", "top.ttl")
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
                    meta_cfg=load_meta_config(),
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
                    meta_cfg=load_meta_config(),
                    src_candidates=[iteration_1_ttl, output_top_ttl],
                )
                return True
            except Exception as e:
                logger.error(f"Failed to copy output_top.ttl: {e}")
                return False
        elif os.path.exists(memory_top_ttl):
            # Fallback: use persisted memory graph (top-level iteration uses entity name "top")
            try:
                import shutil
                shutil.copy2(memory_top_ttl, iteration_1_ttl)
                logger.info("✅ Saved iteration_1.ttl from memory/top.ttl")
                _ = publish_top_ttl(
                    doi_hash=doi_hash,
                    ontology_name=ontology_name,
                    data_dir=data_dir,
                    meta_cfg=load_meta_config(),
                    src_candidates=[iteration_1_ttl, memory_top_ttl],
                )
                return True
            except Exception as e:
                logger.error(f"Failed to copy memory/top.ttl: {e}")
                return False
        else:
            # Last-resort fallback: try the latest exported snapshot for "top"
            try:
                if os.path.isdir(exports_dir):
                    export_candidates = [
                        os.path.join(exports_dir, f)
                        for f in os.listdir(exports_dir)
                        if f.lower().startswith("top_") and f.lower().endswith(".ttl")
                    ]
                    if export_candidates:
                        export_candidates.sort(key=lambda p: os.path.getmtime(p), reverse=True)
                        latest = export_candidates[0]
                        import shutil
                        shutil.copy2(latest, iteration_1_ttl)
                        logger.info(f"✅ Saved iteration_1.ttl from latest export: {os.path.basename(latest)}")
                        _ = publish_top_ttl(
                            doi_hash=doi_hash,
                            ontology_name=ontology_name,
                            data_dir=data_dir,
                            meta_cfg=load_meta_config(),
                            src_candidates=[iteration_1_ttl, latest],
                        )
                        return True
            except Exception as e:
                logger.warning(f"⚠️  Failed scanning exports fallback: {e}")

            logger.warning("⚠️  No output.ttl/output_top.ttl and no memory/export fallback found")
            return False


def parse_top_entities_from_ttl(doi_hash: str, ontology_name: str, data_dir: str = "data") -> bool:
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
        from rdflib import Graph
        
        doi_folder = os.path.join(data_dir, doi_hash)
        ttl_path = os.path.join(doi_folder, "iteration_1.ttl")
        sparql_path = resolve_generated_file(
            f"ai_generated_contents/sparqls/{ontology_name}/top_entity_parsing.sparql"
        )
        output_json_path = os.path.join(doi_folder, "mcp_run", "iter1_top_entities.json")
        
        # Check if TTL exists
        if not os.path.exists(ttl_path):
            logger.error(f"❌ TTL file not found: {ttl_path}")
            return False
        
        # Check if SPARQL query exists
        if not os.path.exists(sparql_path):
            logger.error(f"❌ SPARQL query not found: {sparql_path}")
            return False
        
        # Load SPARQL query
        with open(sparql_path, 'r', encoding='utf-8') as f:
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
            
            entities.append({
                "uri": uri,
                "label": label,
                # Type information can be inferred downstream; we keep this generic here.
                "types": []
            })
        
        # CRITICAL VALIDATION: Check if entities list is empty
        if not entities or len(entities) == 0:
            logger.error(f"❌ CRITICAL: Parsed 0 entities from TTL - KG building failed to create any entities!")
            logger.error(f"   This usually means the agent didn't properly use the MCP tools")
            # Save empty JSON anyway for debugging
            os.makedirs(os.path.dirname(output_json_path), exist_ok=True)
            with open(output_json_path, 'w', encoding='utf-8') as f:
                json.dump(entities, f, indent=2)
            return False  # Signal failure so we can retry
        
        # Save to JSON
        os.makedirs(os.path.dirname(output_json_path), exist_ok=True)
        with open(output_json_path, 'w', encoding='utf-8') as f:
            json.dump(entities, f, indent=2)
        
        logger.info(f"✅ Parsed {len(entities)} top entities from TTL")
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
    
    logger.info(f"▶️  Top Entity KG Building for {doi_hash}")
    
    # Check if iteration_1.ttl already exists
    iteration_1_ttl = os.path.join(doi_folder, "iteration_1.ttl")
    if os.path.exists(iteration_1_ttl):
        logger.info(f"  ⏭️  iteration_1.ttl already exists")
        return True
    
    # Load meta task configuration
    meta_config = load_meta_config()
    if not meta_config:
        logger.error("❌ Failed to load meta task configuration")
        return False
    
    # Get main ontology configuration
    main_ontology = meta_config.get("ontologies", {}).get("main", {})
    ontology_name = main_ontology.get("name", "ontosynthesis")
    mcp_set_name = main_ontology.get("mcp_set_name", "run_created_mcp.json")
    mcp_tools = main_ontology.get("mcp_list", ["llm_created_mcp"])
    
    # Override with test MCP config if provided
    if "test_mcp_config" in config:
        mcp_set_name = config["test_mcp_config"]
        logger.info(f"  🧪 Using test MCP config")
    
    logger.info(f"  📋 Ontology: {ontology_name}")
    logger.info(f"  🔧 MCP Set: {mcp_set_name}")
    logger.info(f"  🛠️  MCP Tools: {mcp_tools}")
    
    # Load extraction hints from previous step
    hints = load_extraction_hints(doi_hash, data_dir)
    if not hints:
        logger.error("❌ Failed to load extraction hints")
        return False
    
    logger.info(f"  ✓ Loaded extraction hints ({len(hints)} chars)")
    
    # Load KG building prompt
    prompt_path = resolve_generated_file(
        f"ai_generated_contents/prompts/{ontology_name}/KG_BUILDING_ITER_1.md"
    )
    prompt_template = load_extraction_prompt(prompt_path)
    if not prompt_template:
        logger.error(f"❌ Failed to load prompt from {prompt_path}")
        return False
    
    logger.info(f"  ✓ Loaded KG building prompt")
    
    # Run the agent with retry logic for empty entity lists
    max_kg_retries = 3
    test_mode = "test_mcp_config" in config
    
    for kg_attempt in range(max_kg_retries):
        try:
            if kg_attempt > 0:
                logger.info(f"  🔄 KG Building retry attempt {kg_attempt + 1}/{max_kg_retries}")
                # Clean up previous failed attempt
                if os.path.exists(iteration_1_ttl):
                    os.remove(iteration_1_ttl)
                    logger.info(f"  🗑️  Removed failed iteration_1.ttl from previous attempt")
            
            response, metadata = asyncio.run(
                run_kg_building_agent(
                    doi_hash=doi_hash,
                    prompt_template=prompt_template,
                    hints=hints,
                    mcp_tools=mcp_tools,
                    mcp_set_name=mcp_set_name,
                    model_name="gpt-4o",
                    temperature=0.1,
                    top_p=0.1
                )
            )
            
            # Save agent response
            save_agent_response(doi_hash, response, data_dir)
            
            # Copy output TTL to iteration_1.ttl
            if not copy_output_ttl(doi_hash, data_dir, test_mode=test_mode, ontology_name=ontology_name):
                logger.warning("⚠️  Failed to save iteration_1.ttl")
                if kg_attempt < max_kg_retries - 1:
                    logger.info(f"  ⏳ Waiting 5s before retry...")
                    import time
                    time.sleep(5)
                    continue
                else:
                    return False
            
            # Parse TTL to extract top entities as JSON
            logger.info(f"  📊 Parsing top entities from TTL")
            parse_success = parse_top_entities_from_ttl(doi_hash, ontology_name, data_dir)
            
            if not parse_success:
                # Parsing failed or returned empty entities list
                logger.error(f"  ❌ KG building attempt {kg_attempt + 1}/{max_kg_retries} produced no entities")
                if kg_attempt < max_kg_retries - 1:
                    logger.info(f"  ⏳ Waiting 5s before retry...")
                    import time
                    time.sleep(5)
                    continue
                else:
                    logger.error(f"  ❌ All {max_kg_retries} KG building attempts failed to produce entities")
                    return False
            
            # Success! Entities were created
            logger.info(f"✅ Top Entity KG Building completed for {doi_hash}")
            return True
            
        except Exception as e:
            logger.error(f"❌ KG building attempt {kg_attempt + 1}/{max_kg_retries} failed: {e}")
            if kg_attempt < max_kg_retries - 1:
                logger.info(f"  ⏳ Waiting 5s before retry...")
                import time
                time.sleep(5)
            else:
                logger.error(f"❌ All {max_kg_retries} KG building attempts failed")
                return False
    
    return False


if __name__ == "__main__":
    # Example usage for standalone testing
    if len(sys.argv) > 1:
        test_doi_hash = sys.argv[1]
        test_config = {
            "data_dir": "data"
        }
        print(f"Running top entity KG building step for DOI hash: {test_doi_hash}")
        success = run_step(test_doi_hash, test_config)
        print(f"Top entity KG building step {'succeeded' if success else 'failed'}.")
    else:
        print("Usage: python -m src.pipelines.top_entity_kg_building.build <doi_hash>")

