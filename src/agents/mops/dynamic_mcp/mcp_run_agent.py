from models.BaseAgent import BaseAgent
from models.ModelConfig import ModelConfig
from models.locations import SANDBOX_TASK_DIR, PLAYGROUND_DATA_DIR
from src.utils.global_logger import get_logger
import os
import asyncio
import argparse
import shutil
import glob
import json
from datetime import datetime
import rdflib
from rdflib import Graph, URIRef, Literal, Namespace

iteration = 3

def setup_usage_tracking():
    """Setup file-based usage tracking for MCP run agent."""
    usage_log_dir = "data/usage_logs"
    os.makedirs(usage_log_dir, exist_ok=True)
    
    # Create session-specific log file
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    usage_log_file = os.path.join(
        usage_log_dir, 
        f"mcp_run_agent_usage_{timestamp}.jsonl"
    )
    
    # Create summary log file (appends to existing)
    usage_summary_file = os.path.join(
        usage_log_dir, 
        "mcp_run_usage_summary.jsonl"
    )
    
    return usage_log_file, usage_summary_file

def log_usage(usage_log_file: str, usage_summary_file: str, task_name: str, 
              iteration_num: int, entity_info: dict, metadata: dict, 
              task_instruction: str = ""):
    """Log usage data to file."""
    logger = get_logger("agent", "MCPRunAgent")
    
    try:
        # Create usage record
        usage_record = {
            "timestamp": datetime.now().isoformat(),
            "task_name": task_name,
            "iteration": iteration_num,
            "entity_info": entity_info,
            "model_name": metadata.get("model_name", ""),
            "task_instruction": task_instruction[:200] + "..." if len(task_instruction) > 200 else task_instruction,
            "aggregated_usage": metadata.get("aggregated_usage", {}),
            "per_call_usage": metadata.get("per_call_usage", []),
            "final_call_token_usage": metadata.get("final_call_token_usage", {}),
        }
        
        # Write to session log
        with open(usage_log_file, 'a', encoding='utf-8') as f:
            f.write(json.dumps(usage_record) + '\n')
        
        # Write summary to summary log
        summary_record = {
            "timestamp": usage_record["timestamp"],
            "task_name": task_name,
            "iteration": iteration_num,
            "entity_label": entity_info.get("label", "") if entity_info else "",
            "total_tokens": usage_record["aggregated_usage"].get("total_tokens", 0),
            "total_cost_usd": usage_record["aggregated_usage"].get("total_cost_usd", 0),
            "calls": usage_record["aggregated_usage"].get("calls", 0),
            "model_name": usage_record["model_name"]
        }
        
        with open(usage_summary_file, 'a', encoding='utf-8') as f:
            f.write(json.dumps(summary_record) + '\n')
            
        logger.info(f"Usage logged to {usage_log_file}")
        
    except Exception as e:
        logger.error(f"Failed to log usage: {e}")

def parse_top_level_entities(ttl_file_path="iteration_1.ttl"):
    """
    Parse the TTL file and extract top-level entities (synthesis processes/transformations).
    Returns a list of entities with their URIs and labels.
    """
    logger = get_logger("agent", "MCPRunAgent")
    
    if not os.path.exists(ttl_file_path):
        logger.warning(f"TTL file not found: {ttl_file_path}")
        return []
    
    try:
        g = Graph()
        g.parse(ttl_file_path, format="ttl")
        
        # Define the namespaces
        ONTOSYN = Namespace("https://www.theworldavatar.com/kg/OntoSyn/")
        RDFS = Namespace("http://www.w3.org/2000/01/rdf-schema#")
        
        # SPARQL query to retrieve distinct ontosyn:ChemicalSynthesis objects
        query = """
        SELECT DISTINCT ?synthesis ?label WHERE {
            ?synthesis a ontosyn:ChemicalSynthesis .
            OPTIONAL { ?synthesis rdfs:label ?label }
        }
        """
        
        entities = []
        
        # Execute the SPARQL query
        for row in g.query(query, initNs={"ontosyn": ONTOSYN, "rdfs": RDFS}):
            synthesis_uri = str(row.synthesis)
            label = str(row.label) if row.label else synthesis_uri.split('/')[-1]
            
            entity_info = {
                'uri': synthesis_uri,
                'label': label,
                'types': ['ontosyn:ChemicalSynthesis']
            }
            entities.append(entity_info)
        
        logger.info(f"Found {len(entities)} top-level entities in {ttl_file_path}")
        for entity in entities:
            logger.info(f"  - {entity['label']} ({entity['uri']})")
            
        return entities
        
    except Exception as e:
        logger.error(f"Error parsing TTL file {ttl_file_path}: {e}")
        return []



MCP_RUN_PROMPT = """
Create a complete and detailed knowledge graph using the MCP server given. Make sure you output the file. 

Here are some general guidelines for creating the knowledge graph:

- **Output file name**: output.ttl. Make sure you use the mcp tool to export the file with the provided name. 
- **Critical**: Be patient and careful, always include all the information mentioned in the paper that is relevant to the knowledge graph. 
I can confirm you that all functions in the provided MCP servers are necessary to be used. All objects you created should be connected to each other. 
- Make sure you put the correct and meaningful labels to the entities you created, preferrably original labels from the paper.
- **Critical**: Only use standard ASCII characters in all labels, URIs, and text. Avoid superscript characters (⁰, ¹, ², ³, etc.), special unicode symbols, or any non-ASCII characters. Use regular numbers and letters only (e.g., use "SD0" instead of "SD⁰", "BRB+" instead of "BRB⁺").

Please note that the MCP keeps a persistent memory of previous creation or updates.

You are currently at iteration {iteration} and iterations start from 1.

For each iteration, you focus on different aspects of the knowledge graph. 

In iteration 1, only focus on creating the top-level entities, you should do nothing else but make sure 
you include the top level entities. Be very inclusive in this iteration, prioritize inclusion over accuracy. 
In this particular case, we think all synthesis processes or transformation processes should be included as top level entities. (Don't include document objects.)

In iteration 2, you should focus on chemcial inputs and outputs. Make sure they are correctly connected to the top level entities.
Don't create any other entities in this iteration.
  

Here is the content of the paper: 

{paper_content}
"""

MCP_RUN_ENTITY_SPECIFIC_PROMPT = """
Focus specifically on the following top-level entity and create its chemical inputs and outputs using the MCP server given. Make sure you output the file.

**Target Entity**: {entity_label}
**Entity URI**: {entity_uri}

Here are the specific guidelines for this iteration:

- **Output file name**: output.ttl. Make sure you use the mcp tool to export the file with the provided name. 
- **Critical**: Focus ONLY on this specific entity: {entity_label}
- **Important**: Check the labels of existing chemical entities, they may have different IRIs, but referring to the same chemical species. 
In that case, don't create new entities, just connect the existing entities to the target entity. Avoid duplicats at all cost.
But keep in mind, there might be same species with different type (e.g., Input and Output, in that case, you should create two entities).
In almost all the cases, there should be at least two chemical inputs for a chemical synthesis.
- **Critical**: Create chemical inputs and outputs that are connected to this entity
- **Critical**: Use internal verification tools to check the health of the knowledge graph you created, eliminate any warnings or errors
- **Critical**: Be patient and careful, ensure all chemical inputs and outputs mentioned in the paper for this entity are included
- Make sure you put correct and meaningful labels to the chemical entities you create, preferably original labels from the paper
- **Critical**: Only use standard ASCII characters in all labels, URIs, and text. Avoid superscript characters (⁰, ¹, ², ³, etc.), special unicode symbols, or any non-ASCII characters. Use regular numbers and letters only (e.g., use "SD0" instead of "SD⁰", "BRB+" instead of "BRB⁺").
- **Important**: Only create chemical inputs and outputs in this iteration. Don't create any other types of entities
- **Critical**: Don't include solvents as chemical inputs. If you are not sure about one species to be a reactant or a solvent, you can use enhanced_websearch tool to search the web to find more information about the species. Usually, if it commonly used as a solvent, it is a solvent. 
- Suppliers should be created and connected to the chemical inputs in this iteration.
- Yield should be created and connected to the chemical outputs in this iteration.

Please note that the MCP keeps a persistent memory of previous creation or updates.

You are currently at iteration 2, focusing specifically on entity: {entity_label}

Here is the content of the paper: 

{paper_content}
"""

MCP_RUN_ENTITY_SYNTHESIS_PROMPT = """
Focus specifically on the following top-level entity and create its synthesis steps using the MCP server given. Make sure you output the file.

**Target Entity**: {entity_label}
**Entity URI**: {entity_uri}

Here are the specific guidelines for this iteration:

- **Output file name**: output.ttl. Make sure you use the mcp tool to export the file with the provided name. 
- **Critical**: Break down the synthesis steps into smaller steps whenever possible. 
- **Critical**: Focus ONLY on this specific entity: {entity_label}
- **Critical**: Don't forget to include solvents as either addedChemical, washingSolvent, solvent in the steps. (The solvents are not inputs of synthesis, but should be included in the steps as those mentioned.)
- **Important**: Check the labels of existing synthesis step entities, they may have different IRIs, but referring to the same synthesis steps. 
In that case, don't create new entities, just connect the existing entities to the target entity. Avoid duplicates at all cost.
- **Critical**: Create synthesis steps that are connected to this entity
- **Critical**: Use internal verification tools to check the health of the knowledge graph you created, eliminate any warnings or errors
- **Critical**: Be patient and careful, ensure all synthesis steps mentioned in the paper for this entity are included
- Make sure you put correct and meaningful labels to the synthesis step entities you create, preferably original labels from the paper
- **Critical**: Only use standard ASCII characters in all labels, URIs, and text. Avoid superscript characters (⁰, ¹, ², ³, etc.), special unicode symbols, or any non-ASCII characters. Use regular numbers and letters only (e.g., use "SD0" instead of "SD⁰", "BRB+" instead of "BRB⁺").
- **Important**: Only create synthesis steps in this iteration. Don't create any other types of entities
- Vessels, solvents, and devices should be created and connected to the synthesis steps in this iteration.

Please note that the MCP keeps a persistent memory of previous creation or updates.

You are currently at iteration 3, focusing specifically on entity: {entity_label}

Here is the content of the paper: 

{paper_content}
"""

MCP_RUN_CLEANUP_PROMPT = """
Focus on cleaning up the knowledge graph by removing orphan and dangling entities using the MCP server given. Make sure you output the file.

Here are the specific guidelines for this iteration:

- **Output file name**: output.ttl. Make sure you use the mcp tool to export the file with the provided name.   
- **Critical**: This is a CLEANUP iteration - DO NOT add any new entities
- **Critical**: Focus ONLY on removing orphan and dangling entities that are not properly connected
- **Critical**: Remove entities that have no meaningful connections to other entities in the knowledge graph
- **Critical**: Remove duplicate entities that represent the same concept but have different URIs
- **Critical**: Use internal verification tools to check the health of the knowledge graph you created, eliminate any warnings or errors
- **Critical**: Only use standard ASCII characters in all labels, URIs, and text. Avoid superscript characters (⁰, ¹, ², ³, etc.), special unicode symbols, or any non-ASCII characters. Use regular numbers and letters only (e.g., use "SD0" instead of "SD⁰", "BRB+" instead of "BRB⁺").
- **Important**: Preserve all properly connected and meaningful entities
- **Important**: Ensure all remaining entities have proper connections and serve a purpose in the knowledge graph
- **Important**: DO NOT create any new entities in this iteration - only remove problematic ones

Please note that the MCP keeps a persistent memory of previous creation or updates.

You are currently at iteration 4, focusing on cleanup and removing orphan/dangling entities.

Here is the content of the paper: 

{paper_content}
"""
 

async def mcp_run_agent_for_entity(task_meta_name: str, entity_info: dict, paper_content: str):
    """
    Run the MCP agent for a specific entity in iteration 2.
    """
    logger = get_logger("agent", "MCPRunAgent")
    logger.info(f"Starting MCP run agent for entity: {entity_info['label']} in task: {task_meta_name}")
    
    # Setup usage tracking
    usage_log_file, usage_summary_file = setup_usage_tracking()
    
    model_config = ModelConfig(temperature=0.2, top_p=0.2)
    mcp_tools = ["llm_created_mcp", "enhanced_websearch"]
    agent = BaseAgent(model_name="gpt-4.1-mini", model_config=model_config, remote_model=True, mcp_tools=mcp_tools, mcp_set_name="run_created_mcp.json")
    
    instruction = MCP_RUN_ENTITY_SPECIFIC_PROMPT.format(
        entity_label=entity_info['label'],
        entity_uri=entity_info['uri'],
        paper_content=paper_content
    )
    
    response, metadata = await agent.run(instruction, recursion_limit=500)
    aggregated_usage = metadata["aggregated_usage"]
    
    # Log usage
    log_usage(usage_log_file, usage_summary_file, task_meta_name, 2, 
              entity_info=entity_info, metadata=metadata, task_instruction=instruction)
    
    # Save response to markdown file
    output_dir = os.path.join("data", "mcp_run_results")
    os.makedirs(output_dir, exist_ok=True)
    
    entity_safe_name = entity_info['label'].replace(' ', '_').replace('/', '_')
    output_file = os.path.join(output_dir, f"{task_meta_name}_entity_{entity_safe_name}_iter2_response.md")
    
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(f"# MCP Run Agent Response for Entity: {entity_info['label']}\n\n")
        f.write(f"**Task:** {task_meta_name}\n")
        f.write(f"**Entity URI:** {entity_info['uri']}\n")
        f.write(f"**Iteration:** 2\n")
        f.write(f"**Timestamp:** {asyncio.get_event_loop().time()}\n\n")
        f.write("## Response\n\n")
        f.write(str(response))
        f.write("\n\n## Metadata\n\n")
        f.write(f"```json\n{metadata}\n```")
    
    logger.info(f"Entity response saved to: {output_file}")
    return response, aggregated_usage


async def mcp_run_agent_for_entity_synthesis(task_meta_name: str, entity_info: dict, paper_content: str):
    """
    Run the MCP agent for a specific entity in iteration 3 (synthesis steps).
    """
    logger = get_logger("agent", "MCPRunAgent")
    logger.info(f"Starting MCP run agent for entity synthesis steps: {entity_info['label']} in task: {task_meta_name}")
    
    # Setup usage tracking
    usage_log_file, usage_summary_file = setup_usage_tracking()
    
    model_config = ModelConfig(temperature=0.2, top_p=0.2)
    mcp_tools = ["llm_created_mcp", "enhanced_websearch"]
    agent = BaseAgent(model_name="gpt-4.1-mini", model_config=model_config, remote_model=True, mcp_tools=mcp_tools, mcp_set_name="run_created_mcp.json")
    
    instruction = MCP_RUN_ENTITY_SYNTHESIS_PROMPT.format(
        entity_label=entity_info['label'],
        entity_uri=entity_info['uri'],
        paper_content=paper_content
    )
    
    response, metadata = await agent.run(instruction, recursion_limit=500)
    
    # Log usage
    log_usage(usage_log_file, usage_summary_file, task_meta_name, 3, 
              entity_info=entity_info, metadata=metadata, task_instruction=instruction)
    
    # Save response to markdown file
    output_dir = os.path.join("data", "mcp_run_results")
    os.makedirs(output_dir, exist_ok=True)
    
    entity_safe_name = entity_info['label'].replace(' ', '_').replace('/', '_')
    output_file = os.path.join(output_dir, f"{task_meta_name}_entity_{entity_safe_name}_iter3_synthesis_response.md")
    
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(f"# MCP Run Agent Response for Entity Synthesis Steps: {entity_info['label']}\n\n")
        f.write(f"**Task:** {task_meta_name}\n")
        f.write(f"**Entity URI:** {entity_info['uri']}\n")
        f.write(f"**Iteration:** 3 (Synthesis Steps)\n")
        f.write(f"**Timestamp:** {asyncio.get_event_loop().time()}\n\n")
        f.write("## Response\n\n")
        f.write(str(response))
        f.write("\n\n## Metadata\n\n")
        f.write(f"```json\n{metadata}\n```")
    
    logger.info(f"Entity synthesis response saved to: {output_file}")
    return response


async def mcp_run_agent_cleanup(task_meta_name: str, paper_content: str):
    """
    Run the MCP agent for cleanup in iteration 4 (remove orphan/dangling entities).
    """
    logger = get_logger("agent", "MCPRunAgent")
    logger.info(f"Starting MCP run agent for cleanup in task: {task_meta_name}")
    
    # Setup usage tracking
    usage_log_file, usage_summary_file = setup_usage_tracking()
    
    model_config = ModelConfig(temperature=0.2, top_p=0.2)
    mcp_tools = ["llm_created_mcp", "enhanced_websearch"]
    agent = BaseAgent(model_name="gpt-4.1-mini", model_config=model_config, remote_model=True, mcp_tools=mcp_tools, mcp_set_name="run_created_mcp.json")
    
    instruction = MCP_RUN_CLEANUP_PROMPT.format(
        paper_content=paper_content
    )
    
    response, metadata = await agent.run(instruction, recursion_limit=500)
    
    # Log usage
    log_usage(usage_log_file, usage_summary_file, task_meta_name, 4, 
              entity_info={}, metadata=metadata, task_instruction=instruction)
    
    # Save response to markdown file
    output_dir = os.path.join("data", "mcp_run_results")
    os.makedirs(output_dir, exist_ok=True)
    
    output_file = os.path.join(output_dir, f"{task_meta_name}_iter4_cleanup_response.md")
    
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(f"# MCP Run Agent Response for Cleanup (Iteration 4): {task_meta_name}\n\n")
        f.write(f"**Task:** {task_meta_name}\n")
        f.write(f"**Iteration:** 4 (Cleanup - Remove Orphans/Dangling Entities)\n")
        f.write(f"**Timestamp:** {asyncio.get_event_loop().time()}\n\n")
        f.write("## Response\n\n")
        f.write(str(response))
        f.write("\n\n## Metadata\n\n")
        f.write(f"```json\n{metadata}\n```")
    
    logger.info(f"Cleanup response saved to: {output_file}")
    return response


async def mcp_run_agent(task_meta_name: str, test_mode: bool = False, iteration: int = 1):
    """
    This agent runs the MCP server to create knowledge graphs from markdown files.
    """
    logger = get_logger("agent", "MCPRunAgent")
    logger.info(f"Starting MCP run agent for task: {task_meta_name}")
    
    # Setup usage tracking
    usage_log_file, usage_summary_file = setup_usage_tracking()
    
    # Read the complete markdown file from sandbox tasks
    md_file_path = os.path.join(SANDBOX_TASK_DIR, task_meta_name, f"{task_meta_name}_complete.md")

    try:
        with open(md_file_path, 'r', encoding='utf-8') as f:
            paper_content = f.read()
        logger.info(f"Successfully read complete markdown file: {md_file_path}")
    except FileNotFoundError:
        logger.error(f"Complete markdown file not found: {md_file_path}")
        paper_content = "Error: Complete markdown file not found"
    except Exception as e:
        logger.error(f"Error reading complete markdown file: {e}")
        paper_content = f"Error reading file: {str(e)}"
    
    model_config = ModelConfig(temperature=0.2, top_p=0.2)
    mcp_tools = ["llm_created_mcp"]
    agent = BaseAgent(model_name="gpt-4.1-mini", model_config=model_config, remote_model=True, mcp_tools=mcp_tools, mcp_set_name="run_created_mcp.json")
    
    if test_mode:
        instruction = "Create a simple test knowledge graph using the MCP server given."
    else:
        instruction = MCP_RUN_PROMPT.format(
            paper_content=paper_content, 
            iteration=iteration
        )
    
    response, metadata = await agent.run(instruction, recursion_limit=800)
    aggregated_usage = metadata["aggregated_usage"]
    
    # Log usage
    log_usage(usage_log_file, usage_summary_file, task_meta_name, iteration, 
              entity_info={}, metadata=metadata, task_instruction=instruction)
    
    # Save response to markdown file
    output_dir = os.path.join("data", "mcp_run_results")
    os.makedirs(output_dir, exist_ok=True)
    
    output_file = os.path.join(output_dir, f"{task_meta_name}_mcp_run_response.md")
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(f"# MCP Run Agent Response for {task_meta_name}\n\n")
        f.write(f"**Task:** {task_meta_name}\n")
        f.write(f"**Timestamp:** {asyncio.get_event_loop().time()}\n\n")
        f.write("## Response\n\n")
        f.write(str(response))
        f.write("\n\n## Metadata\n\n")
        f.write(f"```json\n{metadata}\n```")
    
    logger.info(f"Response saved to: {output_file}")
    return response, aggregated_usage   


def clear_previous_data():
    """
    Clear previous markdown files, .kg_memory, and .kg_state folders before running.
    """
    logger = get_logger("agent", "MCPRunAgent")
    
    # Clear data/mcp_run_results directory
    results_dir = "data/mcp_run_results"
    if os.path.exists(results_dir):
        shutil.rmtree(results_dir)
        logger.info(f"Cleared {results_dir}")

    # Clear memory directory
    memory_dir = "memory"
    if os.path.exists(memory_dir):
        shutil.rmtree(memory_dir)
        logger.info(f"Cleared {memory_dir}")
    
    # Clear .kg_memory directory
    kg_memory_dir = ".kg_memory"
    if os.path.exists(kg_memory_dir):
        shutil.rmtree(kg_memory_dir)
        logger.info(f"Cleared {kg_memory_dir}")
    
    # Clear .kg_state directory
    kg_state_dir = ".kg_state"
    if os.path.exists(kg_state_dir):
        shutil.rmtree(kg_state_dir)
        logger.info(f"Cleared {kg_state_dir}")
    
    # Clear iteration-specific TTL files
    iteration_files = ["iteration_1.ttl", "output.ttl"]
    for file_path in iteration_files:
        if os.path.exists(file_path):
            os.remove(file_path)
            logger.info(f"Removed {file_path}")
    
    # Recreate directories
    os.makedirs(results_dir, exist_ok=True)
    os.makedirs(kg_memory_dir, exist_ok=True)
    os.makedirs(kg_state_dir, exist_ok=True)
    
    logger.info("Previous data cleared and directories recreated")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='MCP Run Agent for Knowledge Graph Creation')
    parser.add_argument('--test', action='store_true', help='Run test mode with specific file (10.1002_anie.201811027)')
    parser.add_argument('--single', action='store_true', help='Run single file with actual prompt')
    parser.add_argument('--clear', action='store_true', help='Clear previous data')
    args = parser.parse_args()
    if args.clear:
        print("Clearing previous data...")
        clear_previous_data()
 
    
    with open('data/log/agent.log', 'w') as log_file:
        log_file.write('')

    if args.test:
        # Test mode: run specific file 10.1002_anie.201811027
        print("Running in test mode with specific file: 10.1002_anie.201811027")
        
        # Look for the file in sandbox/tasks with _complete suffix
        data_folder = "sandbox/tasks"
        if os.path.exists(data_folder):
            # Find DOI-specific subfolder
            test_file = None
            task_name = None
            for item in os.listdir(data_folder):
                if os.path.isdir(os.path.join(data_folder, item)) and "10.1002_anie.201811027" in item:
                    complete_file = os.path.join(data_folder, item, f"{item}_complete.md")
                    if os.path.exists(complete_file):
                        test_file = complete_file
                        task_name = item
                        break
            
            if test_file and task_name:
                print(f"Found test file: {test_file}")
                
                # Run iteration 1
                print("Running iteration 1: Creating top-level entities")
                response, aggregated_usage = asyncio.run(mcp_run_agent(task_name, test_mode=False, iteration=1))
                
                # total token usage. 
                print(f"Total token usage and cost for iteration 1: {aggregated_usage}")


                # Save output.ttl as iteration_1.ttl for later reference
                print("Saving iteration 1 results...")
                if os.path.exists("output.ttl"):
                    shutil.copy2("output.ttl", "iteration_1.ttl")
                    print("Saved output.ttl as iteration_1.ttl")
                else:
                    print("Warning: output.ttl not found after iteration 1")
                
                # Parse the iteration_1.ttl to get top-level entities
                print("Parsing iteration_1.ttl to extract top-level entities")
                entities = parse_top_level_entities("iteration_1.ttl")
                
                if entities:
                    print(f"Found {len(entities)} top-level entities:")
                    for entity in entities:
                        print(f"  - {entity['label']}")
                    
                    # Read paper content for entity-specific iterations
                    try:
                        with open(test_file, 'r', encoding='utf-8') as f:
                            paper_content = f.read()
                    except FileNotFoundError:
                        print(f"Warning: Complete markdown file not found at {test_file}")
                        paper_content = "Error: Complete markdown file not found"
                    
                    # Run iteration 2 for each entity
                    print(f"Running iteration 2 for {len(entities)} entities individually (chemical inputs/outputs)")
                    for i, entity in enumerate(entities, 1):
                        print(f"Processing entity {i}/{len(entities)} for chemicals: {entity['label']}")
                        response, aggregated_usage = asyncio.run(mcp_run_agent_for_entity(task_name, entity, paper_content))
                        print(f"Total token usage and cost for iteration 2: {aggregated_usage}")
                    
                    # Run iteration 3 for each entity
                    print(f"Running iteration 3 for {len(entities)} entities individually (synthesis steps)")
                    for i, entity in enumerate(entities, 1):
                        print(f"Processing entity {i}/{len(entities)} for synthesis steps: {entity['label']}")
                        asyncio.run(mcp_run_agent_for_entity_synthesis(task_name, entity, paper_content))
                    
                    # Run iteration 4 for cleanup
                    print("Running iteration 4: Cleanup - removing orphan and dangling entities")
                    asyncio.run(mcp_run_agent_cleanup(task_name, paper_content))
                else:
                    print("No top-level entities found in iteration_1.ttl")
            else:
                print("Test file not found. Available DOI folders:")
                for item in os.listdir(data_folder):
                    if os.path.isdir(os.path.join(data_folder, item)):
                        print(f"  - {item}")
        else:
            print("Data folder not found: sandbox/tasks")
 
    else:
        # Normal mode: iterate all DOI folders in sandbox/tasks with _complete.md files
        data_folder = "sandbox/tasks"
        if os.path.exists(data_folder):
            doi_folders = [item for item in os.listdir(data_folder) 
                          if os.path.isdir(os.path.join(data_folder, item)) 
                          and not item.startswith('.') 
                          and not item in ['archive', 'gaussian']]
            print(f"Running in normal mode with {len(doi_folders)} DOI folders")
            for doi_folder in doi_folders:
                complete_file = os.path.join(data_folder, doi_folder, f"{doi_folder}_complete.md")
                if os.path.exists(complete_file):
                    print(f"Processing: {doi_folder}")
                    asyncio.run(mcp_run_agent(doi_folder, test_mode=False))
                else:
                    print(f"Skipping {doi_folder}: _complete.md file not found")
        else:
            print("Data folder not found: sandbox/tasks")

