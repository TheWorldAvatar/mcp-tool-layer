"""
The step agent reads the md file produced and outputs the synthesis step information involved.
It uses the prompts from llm_prompts.py to guide the extraction process.
"""

from models.BaseAgent import BaseAgent
from models.ModelConfig import ModelConfig
from models.locations import SANDBOX_TASK_DIR, PLAYGROUND_DATA_DIR
from src.utils.global_logger import get_logger
import os
import asyncio  
import argparse

# Import the prompts from llm_prompts.py
import sys
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, project_root)

try:
    from playground.llm_prompts import step_types_prompt, step_prompt
except ImportError:
    # Fallback prompts if import fails
    step_types_prompt = lambda: "Extract synthesis step types from the paper content."
    step_prompt = lambda doi, dynamic_prompt: f"Extract synthesis steps for {doi} with dynamic prompt: {dynamic_prompt}"

INSTRUCTION_STEP_PROMPT = """
Objective:
Extract synthesis step information from scientific papers using a two-phase approach:

Phase 1: Identify which synthesis step types are present in the paper
Phase 2: Extract detailed information for each identified step type

This agent works with the synthesis step server to create structured, step-by-step synthesis procedures.

Task name is always {task_name}

EXTRACTION APPROACH:

1. First, identify all synthesis step types present in the paper:
   - Add, HeatChill, Filter, Stir, Sonicate, Crystallization, Dry, Evaporate, Dissolve, Separate, Transfer
   - Use the adaptive schema to determine which steps are present

2. For each identified step type, extract detailed information:
   - Vessel information (name and type from the 7 standard vessel types)
   - Chemical names and amounts (as arrays of strings)
   - Step numbers (sequential and unique)
   - Atmospheric conditions (N2, Ar, Air, N/A)
   - Duration, temperature, pressure as applicable
   - Equipment and conditions specific to each step type

STEP TYPE SPECIFICATIONS:

Add Steps:
- Identify all chemical additions
- Note vessel information and stirring during addition
- Record any pH changes, layering, or special conditions
- Include duration and atmospheric conditions

HeatChill Steps:
- Extract heating/cooling devices and target temperatures
- Note heating rates, vacuum conditions, vessel sealing
- Record stirring during heating and atmospheric conditions

Filter Steps:
- Identify washing solvents and amounts
- Note vacuum filtration and number of filtrations
- Record vessel information and atmospheric conditions

Stir Steps:
- Extract stirring duration and conditions
- Note temperature and whether it's a waiting period
- Record vessel information and atmospheric conditions

Crystallization Steps:
- Extract target temperatures and duration
- Note vessel information and atmospheric conditions
- Include any special crystallization conditions

Dry Steps:
- Extract drying conditions (pressure, temperature, duration)
- Note drying agents used
- Record vessel information and atmospheric conditions

Evaporate Steps:
- Extract evaporation conditions (pressure, temperature, duration)
- Note rotary evaporator usage
- Record removed species and target volumes

Dissolve Steps:
- Extract solvent information and amounts
- Note duration and atmospheric conditions
- Record vessel information

Separate Steps:
- Extract separation type (extraction, washing, column, centrifuge)
- Note solvent information and amounts
- Record duration and atmospheric conditions

Transfer Steps:
- Extract source and target vessel information
- Note layering and transferred amounts
- Record duration and atmospheric conditions

Sonicate Steps:
- Extract sonication duration
- Note vessel information and atmospheric conditions

VESSEL TYPES (use exactly as listed):
- Teflon-lined stainless-steel vessel
- glass vial
- quartz tube
- round bottom flask
- glass scintillation vial
- pyrex tube
- schlenk flask

ATMOSPHERE OPTIONS (use exactly as listed):
- N2
- Ar
- Air
- N/A

SEPARATION TYPES (use exactly as listed):
- extraction
- washing
- column
- centrifuge

DATA ENTRY GUIDELINES:
- Chemical names should be arrays of strings, not comma-separated strings
- Use "N/A" for missing string data, 0 for numeric data
- Ensure step numbers are sequential and unique within each product
- Each synthesis product should have its own set of steps
- Include CCDC numbers when available for product identification

WORKFLOW:
1) init_synthesis_object(task_name)
2) For each synthesis product mentioned in the paper:
   - add_synthesis_product(task_name, product_names, product_ccdc_number)
   - For each synthesis step:
     - Use the appropriate add_*_step tool based on step type
     - Include all required fields for each step type
     - Ensure proper step numbering and vessel tracking
3) get_synthesis_summary(task_name) to inspect
4) mops_step_output(task_name) to write final JSON

Paper Content:
{paper_content}

"""

INSTRUCTION_TEST_PROMPT = """
Simply make up a synthesis step object with one product and a few steps, then output it.
"""

INSTRUCTION_MOCK_PROMPT = """
Generate a completely random, fictional synthesis step object with:
- 2-3 synthesis products with made-up names and CCDC numbers
- 5-8 random synthesis steps of various types (Add, HeatChill, Filter, Stir, etc.)
- Use random vessel types, chemicals, and conditions
- Make it look realistic but completely fictional
- Output it using the mops_step_output tool
"""

async def step_agent(task_meta_name: str, test_mode: bool = False):
    """
    This agent reads the md file produced and outputs the synthesis step information involved.
    It uses a two-phase approach: first identifying step types, then extracting detailed information.
    """
    logger = get_logger("agent", "StepAgent")
    logger.info(f"Starting step agent for task: {task_meta_name}")
    
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
    
    model_config = ModelConfig(temperature=0.2, top_p=0.02)
    mcp_tools = ["mops_step_output"]
    agent = BaseAgent(model_name="gpt-4o", model_config=model_config, remote_model=True, mcp_tools=mcp_tools, mcp_set_name="mops_mcp.json")   
    
    if test_mode and task_meta_name == "mock_synthesis":
        # Mock mode: use mock prompt to generate random fictional data
        instruction = INSTRUCTION_MOCK_PROMPT
        response, metadata = await agent.run(instruction, recursion_limit=100)
        return response
    elif test_mode:
        # Test mode: run a single file with the actual prompt
        instruction = INSTRUCTION_STEP_PROMPT.format(
            task_name=task_meta_name, 
            paper_content=paper_content
        )
        response, metadata = await agent.run(instruction, recursion_limit=100)
        return response
    else:
        # Normal mode: use actual prompt with paper content
        instruction = INSTRUCTION_STEP_PROMPT.format(
            task_name=task_meta_name, 
            paper_content=paper_content
        )
        response, metadata = await agent.run(instruction, recursion_limit=100)
        return response

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Step Agent for MOPs')
    parser.add_argument('--test', action='store_true', help='Run single file with actual prompt')
    parser.add_argument('--mock', action='store_true', help='Run mock prompt to generate random fictional data')
    args = parser.parse_args()
    
    with open('data/log/agent.log', 'w') as log_file:
        log_file.write('')

    if args.mock:
        # Mock mode: run with mock prompt to generate random fictional data
        print("Running in mock mode to generate random fictional synthesis data")
        asyncio.run(step_agent("mock_synthesis", test_mode=True))
    elif args.test:
        # Test mode: run a single file with the actual prompt
        print("Running in test mode with actual prompt")
        asyncio.run(step_agent("10.1021_acs.inorgchem.4c02394", test_mode=False))
    else:
        # Iterate through SANDBOX_TASK_DIR, process folders with sections.json
        for folder in os.listdir(SANDBOX_TASK_DIR):
            folder_path = os.path.join(SANDBOX_TASK_DIR, folder)
            if os.path.isdir(folder_path) and os.path.exists(os.path.join(folder_path, "sections.json")):
                md_file_path = os.path.join(folder_path, f"{folder}_complete.md")
                try:
                    with open(md_file_path, 'r', encoding='utf-8') as f:
                        paper_content = f.read()
                    print(f"Processing: {md_file_path}")
                    asyncio.run(step_agent(folder, test_mode=False))
                except FileNotFoundError:
                    print(f"Markdown file not found: {md_file_path}")
                except Exception as e:
                    print(f"Error reading markdown file: {e}")
