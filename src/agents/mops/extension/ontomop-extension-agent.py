"""
With the extracted A-Box of OntoSynthesis,
it creates the ontomops A-Box and connect the entities 
across the two A-Boxes.

It uses agent created mcp server (for now, semi-automatically created mcp server
named "mop_extension")
"""

EXTRACTION_PROMPT = """

Given the top-level entity and the T-Box, extract all the information you need from the paper 
to populate the ontomops A-Box according to the T-Box. Consider carefully about the comments. 

Only extract information that is directly related to the top-level entity

Here is the top-level entity:

{entity_label}, {entity_uri}

Here is the T-Box of OntoMOPs:

{ontomops_t_box}

"""

PROMPT = """

Your task is to extend the provided A-Box of OntoSynthesis with the ontomops A-Box, according to the paper content. 

You should use the provided MCP server to populate the ontomops A-Box. 

Here is the recommended route of task:

- In the provided OntoSynthesis A-Box, there are some instances that are related to ontomops, you can tell by their types and labels. 
- Find the according information in the paper that you need to populate the ontomops A-Box.
- Populate the ontomops A-Box with the information you found with the MCP server.
- For all chemicals/species, (e.g., https://www.theworldavatar.com/kg/OntoSyn/instance/ChemicalInput/xxx), you must reuse them 
for creating the ChemicalBuildingUnit instances. This is compulsory.

Requirements:

- In the provided OntoSynthesis A-Box, instances have their IRIs already, you should reuse the exact same IRI for the ontomops A-Box instances.
- The final output file name should be "ontomops_extension.ttl" only. 
- Make sure when you add the instances, provide all inputs. (e.g., CCDC number, MOP formula, etc.)
- Make sure at least two ChemicalBuildingUnit instances are created.
- Extract information from the paper content provided below - do not make up any information.
- When calling init_memory, provide the hash value: {hash}
- When calling export_memory, do not provide any parameters - it reads from global state automatically

Special note: 
- For the case where the entity is "transformation from A to B", you should only create the MOP for B. Never A. 

Also, use the ccdc mcp server to download the .res/.cif files from the CCDC, if ccdc number is not provided, use the search_ccdc_by_mop_name tool to search the CCDC by compound name.

Here is the DOI for this run (normalized and pipeline forms):

- DOI: {doi_slash}
- Pipeline DOI: {doi_underscore}

Here is the OntoSynthesis A-Box:

{ontosynthesis_a_box}

Here is the paper content:

{paper_content}

""" 

import asyncio
import re
import os
import shutil
import argparse
import sys
import json
import hashlib
import tempfile
from filelock import FileLock

from models.BaseAgent import BaseAgent
from models.ModelConfig import ModelConfig
from src.agents.mops.dynamic_mcp.modules.extraction import extract_content
from src.utils.extraction_models import get_extraction_model

def generate_hash(doi):
    """Generate an 8-digit hash from the DOI."""
    return hashlib.sha256(doi.encode()).hexdigest()[:8]

# -------------------- Global state writer for OntoMOPs --------------------
GLOBAL_STATE_DIR = "data"
GLOBAL_STATE_JSON = os.path.join(GLOBAL_STATE_DIR, "ontomops_global_state.json")
GLOBAL_STATE_LOCK = os.path.join(GLOBAL_STATE_DIR, "ontomops_global_state.lock")

def write_ontomops_global_state(hash_value: str, top_level_entity_name: str, top_level_entity_iri: str = ""):
    """Write global state atomically with file lock for OntoMOPs MCP server to read.
    Includes top_level_entity_iri for linking ontosyn:hasChemicalOutput.
    
    Args:
        hash_value: The 8-character hash identifying the paper (NOT the DOI)
        top_level_entity_name: Name of the top-level entity
        top_level_entity_iri: IRI of the top-level entity
    """
    os.makedirs(GLOBAL_STATE_DIR, exist_ok=True)
    lock = FileLock(GLOBAL_STATE_LOCK)
    lock.acquire(timeout=30.0)
    try:
        state = {"hash": hash_value, "top_level_entity_name": top_level_entity_name, "top_level_entity_iri": top_level_entity_iri}
        fd, tmp = tempfile.mkstemp(dir=GLOBAL_STATE_DIR, suffix=".json.tmp")
        os.close(fd)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
        os.replace(tmp, GLOBAL_STATE_JSON)
        print(f"OntoMOPs global state written: hash={hash_value}, entity={top_level_entity_name}")
    finally:
        lock.release()

def resolve_identifier(identifier: str) -> str:
    """
    Resolve identifier to hash value.
    If identifier looks like a DOI (contains dots and slashes), convert it to hash.
    Otherwise, assume it's already a hash.
    """
    # Simple heuristic: DOIs contain dots, hashes are 8-char hex strings
    if '.' in identifier or '/' in identifier:
        # It's a DOI, convert to hash
        hash_value = generate_hash(identifier)
        print(f"Converted DOI '{identifier}' to hash '{hash_value}'")
        return hash_value
    else:
        # Assume it's already a hash
        return identifier

def _safe_name(label: str) -> str:
    """Convert entity label to safe filename."""
    return (label or "entity").replace(" ", "_").replace("/", "_")

def _write_text(path: str, content: str):
    """Write content to file, creating directories as needed."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)

def _resolve_doi_from_hash(hash_value: str):
    """Return (pipeline_doi_with_underscore, slash_doi) for a given hash, or ("", "") if unknown."""
    try:
        from models.locations import DATA_DIR
        mapping_path = os.path.join(DATA_DIR, "doi_to_hash.json")
        if not os.path.exists(mapping_path):
            print(f"DOI mapping file not found: {mapping_path}")
            return "", ""
        with open(mapping_path, 'r', encoding='utf-8') as f:
            mapping = json.load(f) or {}
        for doi_us, hv in mapping.items():
            if hv == hash_value:
                doi_sl = doi_us.replace('_', '/')
                return doi_us, doi_sl
        print(f"No DOI found for hash: {hash_value}")
        return "", ""
    except Exception as e:
        print(f"Error resolving DOI for hash {hash_value}: {e}")
        return "", ""

def record_prompt(hash_value, entity_name, prompt_type, prompt_content):
    """Record prompt for debugging purposes."""
    from models.locations import DATA_DIR
    
    mcp_run_dir = os.path.join(DATA_DIR, hash_value, "mcp_run_ontomops")
    safe_entity_name = _safe_name(entity_name)
    prompt_file = os.path.join(mcp_run_dir, f"{prompt_type}_{safe_entity_name}.md")
    
    _write_text(prompt_file, prompt_content)
    print(f"📝 Recorded {prompt_type} prompt for {entity_name}: {os.path.basename(prompt_file)}")

def load_ontomops_tbox():
    """Load the OntoMOPs T-Box from ontologies directory."""
    from models.locations import DATA_DIR
    tbox_path = os.path.join(DATA_DIR, "ontologies", "ontomops-subgraph.ttl")
    try:
        with open(tbox_path, 'r', encoding='utf-8') as f:
            content = f.read()
        print(f"Successfully loaded OntoMOPs T-Box from {tbox_path}")
        return content
    except Exception as e:
        print(f"Warning: Could not load OntoMOPs T-Box from {tbox_path}: {e}")
        return ""

def clear_previous_data():
    pass

def load_entity_ttl_file(hash_value, entity_name):
    """Load the specific entity TTL file."""
    from models.locations import DATA_DIR
    
    hash_dir = os.path.join(DATA_DIR, hash_value)
    safe_entity_name = _safe_name(entity_name)
    ttl_file = os.path.join(hash_dir, f"output_{safe_entity_name}.ttl")
    
    if not os.path.exists(ttl_file):
        raise RuntimeError(f"Entity-specific TTL file not found: {ttl_file}")
    
    print(f"Loading entity-specific TTL file: {os.path.basename(ttl_file)}")
    
    try:
        with open(ttl_file, 'r', encoding='utf-8') as f:
            content = f.read()
        print(f"  - Loaded {os.path.basename(ttl_file)}")
        return content
    except Exception as e:
        raise RuntimeError(f"Failed to load TTL content from {ttl_file}: {e}")

def load_entity_extraction_content(hash_value, entity_name):
    """Load the extraction content for a specific entity."""
    from models.locations import DATA_DIR
    
    mcp_run_dir = os.path.join(DATA_DIR, hash_value, "mcp_run_ontomops")
    safe_entity_name = _safe_name(entity_name)
    extraction_file = os.path.join(mcp_run_dir, f"extraction_{safe_entity_name}.txt")
    
    if not os.path.exists(extraction_file):
        raise RuntimeError(f"Extraction file not found for entity {entity_name}: {extraction_file}")
    
    print(f"Loading extraction content for entity: {entity_name}")
    
    try:
        with open(extraction_file, 'r', encoding='utf-8') as f:
            content = f.read()
        print(f"  - Loaded extraction content from {os.path.basename(extraction_file)}")
        return content
    except Exception as e:
        raise RuntimeError(f"Failed to load extraction content from {extraction_file}: {e}")

def load_stitched_md_content(hash_value, stitched_file):
    """Load the content from _stitched.md file in the hash directory."""
    from models.locations import DATA_DIR
    stitched_path = os.path.join(DATA_DIR, hash_value, stitched_file)
    try:
        with open(stitched_path, 'r', encoding='utf-8') as f:
            content = f.read()
        print(f"Successfully loaded {stitched_path} content")
        return content
    except Exception as e:
        raise RuntimeError(f"Error loading {stitched_path}: {e}")

def load_top_level_entities(hash_value):
    """Load top-level entities from the JSON file produced by the previous agent."""
    from models.locations import DATA_DIR
    entities_path = os.path.join(DATA_DIR, hash_value, "mcp_run", "iter1_top_entities.json")
    try:
        with open(entities_path, 'r', encoding='utf-8') as f:
            entities = json.load(f)
        print(f"Successfully loaded {len(entities)} top-level entities from {entities_path}")
        return entities
    except Exception as e:
        print(f"Error loading top-level entities from {entities_path}: {e}")
        return []

async def extract_ontomops_content(hash_value, paper_content, test_mode=False):
    """Extract OntoMOPs content for each top-level entity."""
    from models.locations import DATA_DIR
    
    # Create mcp_run_ontomops directory
    mcp_run_dir = os.path.join(DATA_DIR, hash_value, "mcp_run_ontomops")
    os.makedirs(mcp_run_dir, exist_ok=True)
    
    # Load OntoMOPs T-Box
    ontomops_tbox = load_ontomops_tbox()
    
    # Load top-level entities from JSON file
    top_entities = load_top_level_entities(hash_value)
    
    if not top_entities:
        print("⚠️  No top-level entities found for OntoMOPs extraction")
        return
    
    # In test mode, only process the first entity
    if test_mode:
        top_entities = top_entities[3:4]
        print(f"🔍 Test mode: Processing only the first top-level entity for OntoMOPs extraction")
    else:
        print(f"🔍 Found {len(top_entities)} top-level entities for OntoMOPs extraction")
    
    # Extract content for each entity
    for i, entity in enumerate(top_entities):
        label = entity.get("label", "")
        uri = entity.get("uri", "")
        safe = _safe_name(label)
        
        print(f"🔄 Processing entity {i+1}/{len(top_entities)}: '{label}'")
        
        # Check if extraction already exists
        extraction_file = os.path.join(mcp_run_dir, f"extraction_{safe}.txt")
        if os.path.exists(extraction_file):
            print(f"⏭️  Skip extraction for '{label}': {extraction_file} exists")
            continue
        
        try:
            # Extract content using EXTRACTION_PROMPT
            extracted_content = await extract_content(
                paper_content=paper_content,
                goal=EXTRACTION_PROMPT.format(
                    entity_label=label,
                    entity_uri=uri,
                    ontomops_t_box=ontomops_tbox
                ),
                t_box=ontomops_tbox,
                entity_label=label,
                entity_uri=uri,
                model_name=get_extraction_model("extension_ontomops"),
            )
            
            # Save extracted content
            _write_text(extraction_file, extracted_content)
            print(f"✅ Saved extraction for '{label}' to {extraction_file}")
            
        except Exception as e:
            print(f"❌ Error extracting content for '{label}': {e}")
            continue
    
    print(f"✅ Completed OntoMOPs content extraction for {len(top_entities)} entities")

async def extract_ontomops_content_for_entity(hash_value, paper_content, entity_name, test_mode=False):
    """Extract OntoMOPs content for a specific entity."""
    from models.locations import DATA_DIR
    
    # Create mcp_run_ontomops directory
    mcp_run_dir = os.path.join(DATA_DIR, hash_value, "mcp_run_ontomops")
    os.makedirs(mcp_run_dir, exist_ok=True)
    
    # Load OntoMOPs T-Box
    ontomops_tbox = load_ontomops_tbox()
    
    safe_entity_name = _safe_name(entity_name)
    extraction_file = os.path.join(mcp_run_dir, f"extraction_{safe_entity_name}.txt")
    
    # Check if extraction already exists
    if os.path.exists(extraction_file):
        print(f"⏭️  Skip extraction for '{entity_name}': {os.path.basename(extraction_file)} exists")
        return
    
    try:
        # Extract content using EXTRACTION_PROMPT
        extracted_content = await extract_content(
            paper_content=paper_content,
            goal=EXTRACTION_PROMPT.format(
                entity_label=entity_name,
                entity_uri="",  # Will be filled by the extraction function
                ontomops_t_box=ontomops_tbox
            ),
            t_box=ontomops_tbox,
            entity_label=entity_name,
            entity_uri="",
            model_name=get_extraction_model("extension_ontomops"),
        )
        
        # Save extracted content
        _write_text(extraction_file, extracted_content)
        print(f"✅ Saved extraction for '{entity_name}' to {os.path.basename(extraction_file)}")
        
    except Exception as e:
        print(f"❌ Error extracting content for '{entity_name}': {e}")
        raise

async def mop_extension_agent(hash_value, entity_name):
    model_config = ModelConfig()
    mcp_tools = ["mops_extension", "ccdc"]
    agent = BaseAgent(model_name="gpt-4.1", model_config=model_config, remote_model=True, mcp_tools=mcp_tools, mcp_set_name="extension.json")
    
    # Resolve DOI from hash (only for prompt formatting, not for global state)
    doi_us, doi_sl = _resolve_doi_from_hash(hash_value)
    # Load entity-specific TTL file and resolve top-level entity IRI from top entities list
    ontosynthesis_a_box = load_entity_ttl_file(hash_value, entity_name)
    entity_iri = ""
    try:
        top_entities = load_top_level_entities(hash_value)
        for ent in top_entities:
            lbl = ent.get("label", "")
            uri = ent.get("uri", "")
            if lbl == entity_name or _safe_name(lbl) == entity_name:
                entity_iri = uri
                break
    except Exception:
        entity_iri = ""
    # Write hash to global state (NOT DOI) - MCP server will use hash directly
    write_ontomops_global_state(hash_value, entity_name, entity_iri)
    
    # Load entity-specific extraction content
    paper_content = load_entity_extraction_content(hash_value, entity_name)
    
    # Format the prompt
    formatted_prompt = PROMPT.format(
        hash=hash_value,
        doi_underscore=doi_us,
        doi_slash=doi_sl,
        ontosynthesis_a_box=ontosynthesis_a_box, 
        paper_content=paper_content
    )
    
    # Record prompt for debugging
    record_prompt(hash_value, entity_name, "extension_prompt", formatted_prompt)
    
    response, metadata = await agent.run(formatted_prompt, recursion_limit=200)
    return response

async def mop_extension_agent_with_content(hash_value, entity_name):
    model_config = ModelConfig()
    mcp_tools = ["mops_extension", "ccdc"]
    agent = BaseAgent(model_name="gpt-4.1", model_config=model_config, remote_model=True, mcp_tools=mcp_tools, mcp_set_name="extension.json")
    
    # Resolve DOI from hash (only for prompt formatting, not for global state)
    doi_us, doi_sl = _resolve_doi_from_hash(hash_value)
    # Load entity-specific TTL file and resolve top-level entity IRI from top entities list
    ontosynthesis_a_box = load_entity_ttl_file(hash_value, entity_name)
    entity_iri = ""
    try:
        top_entities = load_top_level_entities(hash_value)
        for ent in top_entities:
            lbl = ent.get("label", "")
            uri = ent.get("uri", "")
            if lbl == entity_name or _safe_name(lbl) == entity_name:
                entity_iri = uri
                break
    except Exception:
        entity_iri = ""
    # Write hash to global state (NOT DOI) - MCP server will use hash directly
    write_ontomops_global_state(hash_value, entity_name, entity_iri)
    
    # Load entity-specific extraction content
    paper_content = load_entity_extraction_content(hash_value, entity_name)
    
    # Format the prompt
    formatted_prompt = PROMPT.format(
        hash=hash_value,
        doi_underscore=doi_us,
        doi_slash=doi_sl,
        ontosynthesis_a_box=ontosynthesis_a_box, 
        paper_content=paper_content
    )
    
    # Record prompt for debugging
    record_prompt(hash_value, entity_name, "extension_prompt", formatted_prompt)
    
    response, metadata = await agent.run(formatted_prompt, recursion_limit=200)
    return response

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='MOP Extension Agent')
    parser.add_argument('--test', action='store_true', help='Run test mode with specific hash (9f13ab77)')
    parser.add_argument('--file', type=str, help='Run for specific DOI (e.g., 10.1021_acs.cgd.6b00306) or hash (e.g., 9f13ab77)')
    args = parser.parse_args()
    
    # No clearing of previous results
    
    if args.test:
        # Test mode: run specific hash 9f13ab77
        test_hash = "9f13ab77"
        print(f"Running in test mode with hash: {test_hash}")
        
        from models.locations import DATA_DIR
        hash_dir = os.path.join(DATA_DIR, test_hash)
        if not os.path.exists(hash_dir):
            print(f"Hash directory not found: {hash_dir}")
            sys.exit(1)
        
        # Load stitched markdown content
        stitched_file = f"{test_hash}_stitched.md"
        try:
            paper_content = load_stitched_md_content(test_hash, stitched_file)
            
            # Run extraction for all entities first (in parallel), then extension
            async def run_extraction_and_extension():
                # Load top-level entities to get entity names
                top_entities = load_top_level_entities(test_hash)
                if not top_entities:
                    print("⚠️  No top-level entities found for OntoMOPs extension")
                    return "No entities to process"
                
                # In test mode, only process the first entity
                if args.test:
                    top_entities = top_entities[:1]
                    print(f"🔍 Test mode: Processing only the first entity for OntoMOPs extension")
                
                # 1) Batch extract in parallel
                names = [e.get("label", "") for e in top_entities if e.get("label")]
                try:
                    max_conc = int(os.getenv("MOPS_ONTOMOPS_EXTRACT_MAX_CONCURRENCY", "0"))
                except Exception:
                    max_conc = 0
                if max_conc <= 0:
                    max_conc = min(8, len(names)) if names else 1
                print(f"🚦 OntoMOPs extraction concurrency: {max_conc}")

                sem = asyncio.Semaphore(max_conc)
                async def _extract_one(nm: str):
                    async with sem:
                        print(f"Step 1: Extracting OntoMOPs content for entity '{nm}'...")
                        await extract_ontomops_content_for_entity(test_hash, paper_content, nm, args.test)

                await asyncio.gather(*[asyncio.create_task(_extract_one(nm)) for nm in names])

                # 2) Then run extension per entity (sequential to control resource use)
                for i, entity in enumerate(top_entities):
                    entity_name = entity.get("label", "")
                    safe_entity_name = _safe_name(entity_name)
                    print(f"Step 2: Running OntoMOPs extension agent for entity: {entity_name} ({i+1}/{len(top_entities)})")
                    _ = await mop_extension_agent_with_content(test_hash, safe_entity_name)
                    print(f"✅ Completed extension for entity: {entity_name}")
                
                return "All entities processed"
            
            response = asyncio.run(run_extraction_and_extension())
            print(response)
        except Exception as e:
            print(f"Error: {e}")
            sys.exit(1)
            
    elif args.file:
        # File mode: run specific hash or DOI (will be converted to hash)
        hash_value = resolve_identifier(args.file)
        
        from models.locations import DATA_DIR
        hash_dir = os.path.join(DATA_DIR, hash_value)
        if not os.path.exists(hash_dir):
            print(f"Hash directory not found: {hash_dir}")
            print(f"Please ensure the pipeline has been run for this identifier first.")
            sys.exit(1)
        
        # Load stitched markdown content
        stitched_file = f"{hash_value}_stitched.md"
        try:
            paper_content = load_stitched_md_content(hash_value, stitched_file)
            
            # Run extraction for all entities first (in parallel), then extension
            async def run_extraction_and_extension():
                # Load top-level entities to get entity names
                top_entities = load_top_level_entities(hash_value)
                if not top_entities:
                    print("⚠️  No top-level entities found for OntoMOPs extension")
                    return "No entities to process"

                # 1) Batch extract in parallel
                names = [e.get("label", "") for e in top_entities if e.get("label")]
                try:
                    max_conc = int(os.getenv("MOPS_ONTOMOPS_EXTRACT_MAX_CONCURRENCY", "0"))
                except Exception:
                    max_conc = 0
                if max_conc <= 0:
                    max_conc = min(8, len(names)) if names else 1
                print(f"🚦 OntoMOPs extraction concurrency: {max_conc}")

                sem = asyncio.Semaphore(max_conc)
                async def _extract_one(nm: str):
                    async with sem:
                        print(f"Step 1: Extracting OntoMOPs content for entity '{nm}'...")
                        await extract_ontomops_content_for_entity(hash_value, paper_content, nm, False)

                await asyncio.gather(*[asyncio.create_task(_extract_one(nm)) for nm in names])

                # 2) Then run extension per entity (sequential)
                for i, entity in enumerate(top_entities):
                    entity_name = entity.get("label", "")
                    safe_entity_name = _safe_name(entity_name)
                    print(f"Step 2: Running OntoMOPs extension agent for entity: {entity_name} ({i+1}/{len(top_entities)})")
                    _ = await mop_extension_agent_with_content(hash_value, safe_entity_name)
                    print(f"✅ Completed extension for entity: {entity_name}")
                
                return "All entities processed"
            
            response = asyncio.run(run_extraction_and_extension())
            print(response)
        except Exception as e:
            print(f"Error: {e}")
            sys.exit(1)
    else:
        # Normal mode - will load output.ttl dynamically from current directory
        # This mode is not recommended for the new entity-wise memory system
        print("⚠️  Normal mode not supported with entity-wise memory system. Use --test or --file instead.")
        print("Use --test for testing with a specific hash, or --file for processing a specific DOI/hash.")