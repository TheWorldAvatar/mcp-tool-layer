"""
Step agent for extracting synthesis step information.

Modes:
- --test: run a single file with a simple actual prompt
- --mock: generate a fictional step object via MCP tools
- default: iterate sandbox tasks and run with the actual prompt
"""

from models.BaseAgent import BaseAgent
from models.ModelConfig import ModelConfig
from models.locations import SANDBOX_TASK_DIR, PLAYGROUND_DATA_DIR
from src.utils.global_logger import get_logger
import os
import asyncio  
import argparse

SIMPLE_ACTUAL_PROMPT = """
Task: Build a structured Synthesis Step document for task '{task_name}' using the available MCP tools from the step server.

Instructions:
- Follow the step server's built-in instruction prompt for exact fields and step types.
- Use tools in this typical order: init_step_object, add_product_synthesis, add_step_* (as needed), get_step_summary, mops_step_output.

Paper content:
{paper_content}
"""

MOCK_PROMPT = """
Create a fictional Synthesis Step document for task '{task_name}':
- 2 products with made-up names and CCDC numbers
- 5-8 varied steps across different types (Add, HeatChill, Filter, Stir, etc.)
- Use the step MCP tools to construct and then output via mops_step_output(task_name)
"""

async def step_agent(task_meta_name: str, test_mode: bool = False, mock_mode: bool = False):
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

    if mock_mode:
        instruction = MOCK_PROMPT.format(task_name=task_meta_name)
    else:
        instruction = SIMPLE_ACTUAL_PROMPT.format(task_name=task_meta_name, paper_content=paper_content)

    response, metadata = await agent.run(instruction, recursion_limit=500)
    return response

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Step Agent for MOPs')
    parser.add_argument('--test', action='store_true', help='Run single file with actual prompt')
    parser.add_argument('--mock', action='store_true', help='Run mock prompt to generate random fictional data')
    args = parser.parse_args()
    
    with open('data/log/agent.log', 'w') as log_file:
        log_file.write('')

    if args.mock:
        print("Running in mock mode to generate random fictional synthesis data")
        asyncio.run(step_agent("mock_synthesis", test_mode=False, mock_mode=True))
    elif args.test:
        print("Running in test mode with actual prompt")
        asyncio.run(step_agent("10.1021_acs.inorgchem.4c02394", test_mode=True, mock_mode=False))
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
