"""
The chemical agent solely read the md file produced and output the chemicals involved
"""

from models.BaseAgent import BaseAgent
from models.ModelConfig import ModelConfig
from models.locations import SANDBOX_TASK_DIR, PLAYGROUND_DATA_DIR
from src.utils.global_logger import get_logger
import os
import asyncio  
import argparse

INSTRUCTION_CHEMICAL_PROMPT = """
Your task is to extract and output structured output of the chemicals involved in the given paper content.

Task name is always {task_name}

Output the information in a structured format that can be used to populate the Chemical class structure, use the mops_chemical_output tool for output. 


Paper Content:
{paper_content}

"""

INSTRUCTION_TEST_PROMPT = """

Simply make up a Chemical object and output it. 

"""

async def chemical_agent(task_meta_name: str, test_mode: bool = False):
    """
    This agent reads the md file produced and output the chemicals involved
    """
    logger = get_logger("agent", "ChemicalAgent")
    logger.info(f"Starting chemical agent for task: {task_meta_name}")
    
    # Read the markdown file
    md_file_path = os.path.join(PLAYGROUND_DATA_DIR, f"{task_meta_name}.md")
    
    try:
        with open(md_file_path, 'r', encoding='utf-8') as f:
            paper_content = f.read()
        logger.info(f"Successfully read markdown file: {md_file_path}")
    except FileNotFoundError:
        logger.error(f"Markdown file not found: {md_file_path}")
        paper_content = "Error: Markdown file not found"
    except Exception as e:
        logger.error(f"Error reading markdown file: {e}")
        paper_content = f"Error reading file: {str(e)}"
    
    model_config = ModelConfig(temperature=0.2, top_p=0.02)
    mcp_tools = ["mops_chemical_output"]
    agent = BaseAgent(model_name="gpt-4o", model_config=model_config, remote_model=True, mcp_tools=mcp_tools, mcp_set_name="mops_mcp.json")   
    
    if test_mode:
        instruction = INSTRUCTION_TEST_PROMPT
    else:
        instruction = INSTRUCTION_CHEMICAL_PROMPT.format(
            task_name=task_meta_name, 
            paper_content=paper_content
        )
    
    response, metadata = await agent.run(instruction, recursion_limit=200)
    return response

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Chemical Agent for MOPs')
    parser.add_argument('--test', action='store_true', help='Run mock prompt without any file')
    parser.add_argument('--single', action='store_true', help='Run single file with actual prompt')
    args = parser.parse_args()
    
    with open('data/log/agent.log', 'w') as log_file:
        log_file.write('')

    if args.test:
        # Test mode: run mock prompt without any file
        print("Running in test mode with mock prompt (no file)")
        asyncio.run(chemical_agent("", test_mode=True))
    elif args.single:
        # Single file mode: run one specific file with actual prompt
        single_file = "10.1021_acs.inorgchem.4c02394"
        print(f"Running single file mode with: {single_file}")
        asyncio.run(chemical_agent(single_file, test_mode=False))
    else:
        # Normal mode: iterate all .md files in the playground/data folder, which has no _si.md suffix
        data_folder = "playground/data"
        md_files = [f for f in os.listdir(data_folder) if f.endswith('.md') and not f.endswith('_si.md')]
        print(f"Running in normal mode with {len(md_files)} files")
        for md_file in md_files:
            print(f"Processing: {md_file}")
            asyncio.run(chemical_agent(md_file.replace('.md', ''), test_mode=False))


