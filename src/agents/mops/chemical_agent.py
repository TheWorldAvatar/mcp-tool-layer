"""
The chemical agent solely reads the md file produced and outputs the chemicals involved
"""

from models.BaseAgent import BaseAgent
from models.ModelConfig import ModelConfig
from models.locations import SANDBOX_TASK_DIR
from src.utils.global_logger import get_logger
import os
import asyncio  
import argparse

INSTRUCTION_CHEMICAL_PROMPT = """
Task Specification: Summarize the synthesis procedure into a JSON file. Extract the relevant data from the synthesis text and structure it into a JSON file adhering to the specified schema.

Task name is always {task_name}

List the chemicals for each synthesis procedure. Make sure to only list one synthesis product per synthesis procedure. 
The supplier name and purity are usually given in the general procedure of the paper while additional names and the chemical formula usually is listed in the exact procedure. 
If any information is missing or uncertain, fill the cell with N/A for strings or 0 for numeric types.

Category Specifications:
"synthesisProcedure": Some of the provided text describe procedures for multiple products make sure to list each of them as a separate synthesisProcedure. 
    "procedureName": name the procedure after the product or copy an existing title.
        "inputChemicals": all chemicals listed that are not the end product of the synthesis and used in the specific procedure.
        If multiple chemical species are added or used as washing solvent etc. in one step make a new entry for each chemical. For all ChemicalAmount entries if the chemical is a mixture make sure to enter the amount for all components either by specifying the absolute amount as the names
        (Example: for 10 mL ethanol and 20 mL water write: "addedChemical":[{{"addedChemicalName":["ethanol"],"addedChemicalAmount":"10 mL"}}, {{"addedChemicalName":["water"],"addedChemicalAmount":"20 mL"}}] 
            "chemicalFormula": a set of chemical symbols showing the elements present in a compound and their relative proportions.
            "names": name of the inputChemicals, make sure to list all names that are used in the text. Separate the names by comma and extract them as individual strings. 
            "chemicalAmount": amount of the chemical in mole, kg or both. Example: 1.45 g, 4.41 mmol 
            "supplierName": Company name that produces the chemical which is usually given in the general procedure.  Example: Iron-
            (III) sulfate hydrate, 1,4-benzenedicarboxylic acid (H2BDC) was
            purchased from Aldrich Chemical Co. => "supplierName" = Aldrich Chemical Co.
            "purity": Usually an additional percentage value indicating the purity of the chemical. Example: N,N-Dimethylformamide (DMF) (99.9%) => "purity" = 99.9%
        "outputChemical": "Product or target of the synthesis.
        "chemicalFormula": a set of chemical symbols showing the elements present in a compound and their relative proportions.
        "names": name of the outputChemicals, make sure to list all names that are used in the text. 
        "yield": Ratio expressing the efficiency of a mass conversion process. Usually reported as follows (28% yield based on H2BPDC) or (65% yield). Please extract the percentage: E.g.: yield = "65%"
        "CCDCNumber": Number of the Cambridge Crystallographic Database. Specific to metallic organic polyhedra chemical. 

Data Entry Guidelines:
- Make sure to extract the input and output chemicals for all MOPs in the text.
- For chemical names make sure to write each name as separate string. Wrong: ["C4H9NO, DMA, N,N'-dimethylacetamide"], Correct: ["C4H9NO", "DMA", "N,N'-dimethylacetamide"]
- If multiple chemical species are added or used as washing solvent etc. in one step make a new entry for each chemical.
- For all ChemicalAmount entries if the chemical is a mixture make sure to enter the amount for all components either by specifying the absolute amount or give the ratio of the two chemicals (Example: chemicalAmount: 30 mL (1:2))

Output the information in a structured format that can be used to populate the Chemical class structure, use the mops_chemical_output tool for output.

If you need to search for information in the paper, use the mops_misc tool. I recommend you to use the in_context_search tool
to find more concentrated information about chemical names and formulas.

Paper Content:

{paper_content}

"""

INSTRUCTION_TEST_PROMPT = """
Simply make up a Chemical object and output it.
"""

async def chemical_agent(task_meta_name: str, paper_content: str, test_mode: bool = False):
    """
    This agent reads the md file produced and outputs the chemicals involved
    """
    logger = get_logger("agent", "ChemicalAgent")
    logger.info(f"Starting chemical agent for task: {task_meta_name}")
    
    model_config = ModelConfig(temperature=0.2, top_p=0.02)
    mcp_tools = ["mops_chemical_output", "mops_misc"]
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
    args = parser.parse_args()
    
    with open('data/log/agent.log', 'w') as log_file:
        log_file.write('')

    if args.test:
        # Test mode: run mock prompt without any file
        print("Running in test mode with mock prompt (no file)")
        asyncio.run(chemical_agent("", "", test_mode=True))
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
                    asyncio.run(chemical_agent(folder, paper_content, test_mode=False))
                except FileNotFoundError:
                    print(f"Markdown file not found: {md_file_path}")
                except Exception as e:
                    print(f"Error reading markdown file: {e}")
