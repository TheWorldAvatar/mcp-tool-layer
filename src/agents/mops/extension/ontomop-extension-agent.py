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

Requirements:

- In the provided OntoSynthesis A-Box, instances have their IRIs already, you should reuse the exact same IRI for the ontomops A-Box instances.
- The final output file name should be "ontomops_extension.ttl" only. 
- Make sure when you create the instances, provide all inputs. (e.g., CCDC number, MOP formula, etc.)
- Extract information from the paper content provided below - do not make up any information.
- When calling init_memory, provide the hash value: {hash}
- When calling export_memory, provide the hash value: {hash}

Here is the OntoSynthesis A-Box:

{ontosynthesis_a_box}

Here is the paper content:

{paper_content}

""" 

import asyncio
import os
import shutil
import argparse
import sys
import json
import hashlib

from models.BaseAgent import BaseAgent
from models.ModelConfig import ModelConfig
from src.agents.mops.dynamic_mcp.modules.extraction import extract_content

def generate_hash(doi):
    """Generate an 8-digit hash from the DOI."""
    return hashlib.sha256(doi.encode()).hexdigest()[:8]

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
    """
    Clear previous memory and output files before running.
    """
    # Clear memory directory

    memory_file_list = ["memory_ontomops.ttl", "memory_ontomops.lock"]

    memory_dir = "memory"
    if os.path.exists(memory_dir):
        for file_path in memory_file_list:
            if os.path.exists(os.path.join(memory_dir, file_path)):
                os.remove(os.path.join(memory_dir, file_path))
                print(f"Removed {os.path.join(memory_dir, file_path)}")
    
    # Clear output TTL files
    output_files = ["ontomops_extension.ttl", "ontomops_snapshot.ttl"]
    for file_path in output_files:
        if os.path.exists(file_path):
            os.remove(file_path)
            print(f"Removed {file_path}")
    
    # Recreate memory directory
    os.makedirs(memory_dir, exist_ok=True)
    print("Previous data cleared and directories recreated")

def load_entity_ttl_files(hash_value):
    """Load all entity-specific output TTL files (excluding output_top.ttl) and concatenate them."""
    from models.locations import DATA_DIR
    import glob
    
    hash_dir = os.path.join(DATA_DIR, hash_value)
    # Find all output_*.ttl files except output_top.ttl
    ttl_pattern = os.path.join(hash_dir, "output_*.ttl")
    ttl_files = glob.glob(ttl_pattern)
    
    # Filter out output_top.ttl
    ttl_files = [f for f in ttl_files if not f.endswith("output_top.ttl")]
    
    if not ttl_files:
        raise RuntimeError(f"No entity-specific TTL files found in {hash_dir}")
    
    print(f"Found {len(ttl_files)} entity-specific TTL files")
    
    # Load and concatenate all TTL content
    combined_content = []
    for ttl_file in sorted(ttl_files):
        try:
            with open(ttl_file, 'r', encoding='utf-8') as f:
                content = f.read()
                combined_content.append(f"# Content from {os.path.basename(ttl_file)}\n{content}\n")
                print(f"  - Loaded {os.path.basename(ttl_file)}")
        except Exception as e:
            print(f"  ⚠️  Warning: Could not load {ttl_file}: {e}")
    
    if not combined_content:
        raise RuntimeError(f"Failed to load any TTL content from {hash_dir}")
    
    return "\n".join(combined_content)

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
            )
            
            # Save extracted content
            _write_text(extraction_file, extracted_content)
            print(f"✅ Saved extraction for '{label}' to {extraction_file}")
            
        except Exception as e:
            print(f"❌ Error extracting content for '{label}': {e}")
            continue
    
    print(f"✅ Completed OntoMOPs content extraction for {len(top_entities)} entities")

async def mop_extension_agent(hash_value):
    model_config = ModelConfig()
    mcp_tools = ["mops_extension"]
    agent = BaseAgent(model_name="gpt-4.1", model_config=model_config, remote_model=True, mcp_tools=mcp_tools, mcp_set_name="extension.json")
    
    # Load all entity-specific TTL files from hash directory
    ontosynthesis_a_box = load_entity_ttl_files(hash_value)
    response, metadata = await agent.run(PROMPT.format(ontosynthesis_a_box=ontosynthesis_a_box), recursion_limit=200)
    return response

async def mop_extension_agent_with_content(hash_value, paper_content):
    model_config = ModelConfig()
    mcp_tools = ["mops_extension"]
    agent = BaseAgent(model_name="gpt-4.1", model_config=model_config, remote_model=True, mcp_tools=mcp_tools, mcp_set_name="extension.json")
    
    # Load all entity-specific TTL files from hash directory
    ontosynthesis_a_box = load_entity_ttl_files(hash_value)
    
    # Format the prompt with the hash value
    formatted_prompt = PROMPT.format(
        hash=hash_value,
        ontosynthesis_a_box=ontosynthesis_a_box, 
        paper_content=paper_content
    )
    
    response, metadata = await agent.run(formatted_prompt, recursion_limit=200)
    return response

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='MOP Extension Agent')
    parser.add_argument('--test', action='store_true', help='Run test mode with specific hash (9f13ab77)')
    parser.add_argument('--file', type=str, help='Run for specific DOI (e.g., 10.1021_acs.cgd.6b00306) or hash (e.g., 9f13ab77)')
    args = parser.parse_args()
    
    # Clear previous data before every run
    print("Clearing previous data...")
    clear_previous_data()
    
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
            
            # Run both extraction and extension agent
            async def run_extraction_and_extension():
                # First, extract OntoMOPs content for each entity (test mode)
                print("Step 1: Extracting OntoMOPs content for each top-level entity...")
                await extract_ontomops_content(test_hash, paper_content, test_mode=True)
                
                # Then run the extension agent
                print("Step 2: Running OntoMOPs extension agent...")
                response = await mop_extension_agent_with_content(test_hash, paper_content)
                return response
            
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
            
            # Run both extraction and extension agent
            async def run_extraction_and_extension():
                # First, extract OntoMOPs content for each entity (normal mode - all entities)
                print("Step 1: Extracting OntoMOPs content for each top-level entity...")
                await extract_ontomops_content(hash_value, paper_content, test_mode=False)
                
                # Then run the extension agent
                print("Step 2: Running OntoMOPs extension agent...")
                response = await mop_extension_agent_with_content(hash_value, paper_content)
                return response
            
            response = asyncio.run(run_extraction_and_extension())
            print(response)
        except Exception as e:
            print(f"Error: {e}")
            sys.exit(1)
    else:
        # Normal mode - will load output.ttl dynamically from current directory
        response = asyncio.run(mop_extension_agent("."))
        print(response)