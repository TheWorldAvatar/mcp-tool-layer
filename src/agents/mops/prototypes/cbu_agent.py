"""
The CBU agent reads the md file produced and outputs the CBU (Chemical Building Unit) information involved
"""

from models.BaseAgent import BaseAgent
from models.ModelConfig import ModelConfig
from models.locations import SANDBOX_TASK_DIR, PLAYGROUND_DATA_DIR
from src.utils.global_logger import get_logger
import os
import asyncio  
import argparse

INSTRUCTION_CBU_PROMPT = """
Objective: Extract and match Chemical Building Units (CBUs) with corresponding lab species based on synthesis data.

Task name is always {task_name}

Each MOP (Metal-Organic Polyhedra) has exactly two CBUs. You need to identify:
1. All MOPs mentioned in the paper
2. The two chemical building units that make up each MOP
3. CCDC numbers if available
4. Chemical formulas and names for each CBU

The CBU formulas are just abstractions of the CBU and your task is to find the respecting equivalent(s) from the lab species list. Write the result to a JSON file adhering to the specified schema.

Category Specifications:
- "mopCCDCNumber": The CCDC number identifier for the MOP
- "cbu1" and "cbu2": The two chemical building units that make up the MOP
- "labSpecies": The actual chemical species from the lab that correspond to each CBU
- "chemicalFormula": Chemical formula for each CBU
- "names": Names of the chemical species that represent each CBU

Data Entry Guidelines:
- For chemical names make sure to write each name as separate string. Wrong: ["C4H9NO, DMA, N,N'-dimethylacetamide"], Correct: ["C4H9NO", "DMA", "N,N'-dimethylacetamide"]
- Make sure you include all the alias and other names of the chemical specie in the names field.
- If any information is missing or uncertain, fill the cell with N/A for strings or 0 for numeric types
- Make sure to extract the CBU information for all MOPs mentioned in the text
- Each MOP must have exactly two CBUs
- Map each CBU to the corresponding lab species that are actually used in the synthesis

Mapping Instructions:
- Look for the synthesis procedures in the text
- Identify the input chemicals and reagents used
- Match these to the abstract CBU formulas
- Consider the chemical context and synthesis pathway
- Ensure the lab species actually participate in the MOP formation

Output the information in a structured format that can be used to populate the CBU class structure, use the mops_cbu_output tool for output.

If you need to search for information in the paper, use the mops_misc tool. I recommend you to use the in_context_search tool
to find more concentrated information about chemical names and formulas.


Paper Content:
{paper_content}

"""

INSTRUCTION_TEST_PROMPT = """
Simply make up a CBU object and output it.
"""

async def cbu_agent(task_meta_name: str, test_mode: bool = False):
    """
    This agent reads the md file produced and outputs the CBU information involved
    """
    logger = get_logger("agent", "CBUAgent")
    logger.info(f"Starting CBU agent for task: {task_meta_name}")
    
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
    mcp_tools = ["mops_cbu_output", "mops_misc"]
    agent = BaseAgent(model_name="gpt-4o", model_config=model_config, remote_model=True, mcp_tools=mcp_tools, mcp_set_name="mops_mcp.json")   
    
    if test_mode:
        instruction = INSTRUCTION_TEST_PROMPT
    else:
        instruction = INSTRUCTION_CBU_PROMPT.format(
            task_name=task_meta_name, 
            paper_content=paper_content
        )
    
    response, metadata = await agent.run(instruction, recursion_limit=200)
    return response

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='CBU Agent for MOPs')
    parser.add_argument('--test', action='store_true', help='Run mock prompt without any file')
    parser.add_argument('--single', action='store_true', help='Run single file with actual prompt')
    args = parser.parse_args()
    
    with open('data/log/agent.log', 'w') as log_file:
        log_file.write('')

    if args.test:
        # Test mode: run mock prompt without any file
        print("Running in test mode with mock prompt (no file)")
        asyncio.run(cbu_agent("", test_mode=True))
    elif args.single:
        # Single file mode: run one specific file with actual prompt
        single_file = "10.1021_acs.inorgchem.4c02394"
        print(f"Running single file mode with: {single_file}")
        asyncio.run(cbu_agent(single_file, test_mode=False))
    else:
        # Normal mode: iterate all .md files in the playground/data folder, which has no _si.md suffix
        data_folder = "playground/data"
        md_files = [f for f in os.listdir(data_folder) if f.endswith('.md') and not f.endswith('_si.md')]
        print(f"Running in normal mode with {len(md_files)} files")
        for md_file in md_files:
            print(f"Processing: {md_file}")
            asyncio.run(cbu_agent(md_file.replace('.md', ''), test_mode=False))
