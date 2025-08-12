"""
The characterisation agent reads the md file produced and outputs the characterisation information involved
"""

from models.BaseAgent import BaseAgent
from models.ModelConfig import ModelConfig
from models.locations import SANDBOX_TASK_DIR, PLAYGROUND_DATA_DIR
from src.utils.global_logger import get_logger
import os
import asyncio  
import argparse

INSTRUCTION_CHARACTERISATION_PROMPT = """
Objective:
Extract characterization data from the synthesis section of scientific papers in two steps:

Step 1: Extract the names of the characterization devices from the general synthesis section.
Step 2: Extract detailed characterization data for each synthesis procedure. Make sure to include the characterisation data for each and every product separately.

Write the extracted data into a JSON file following the specified schema. Ensure all required fields are filled. If any field is missing or unknown, use "N/A" for strings and 0 for numeric types.

Additionally, extract chemical product characterization data from the synthesis text and organize it according to the JSON schema. Create a new entry for each synthesized product, including the product name and CCDC number.

Instructions: Focus on extracting mentions of measurement devices and characterization data specifically from the synthesis section of the paper, particularly in the general synthesis paragraph.

Category Specifications:

HNMR (Proton Nuclear Magnetic Resonance):
- Copy the HNMR device name as stated in the paper.
- Include the frequency (with units) and list the solvent(s) used in the measurement.
- Extract all chemical shifts, solvent information, and temperature (if listed).
- Example: 10.58 (s, 12H, O-H), 7.94 (s, 24H, Ph-H), 6.64 (s, 60H, Cp-H)

Elemental Analysis:
- Copy the Elemental Analysis device name as stated in the paper.
- Extract the weight percentage for each element, and if provided, the chemical formula.
- Capture both measured (usually prefixed by "found:") and calculated data (indicated by "Anal. Calcd. for ...:").
- Include the chemical formula used for the calculation and the device used for measuring the weight percentage.
- Example: Found: C, 49.78%; H, 4.65%; N, 3.45%. Calculated for [(C2H5)3NH]2[Co4(TC4A)(Cl)]2[Co4(TC4A)(SO4)]4(C12H6O4N2)8·(CH3OH)10(DMF)4: C, 50.98%; H, 4.52%.

Infrared Spectroscopy (IR):
- Copy the IR device name as stated in the paper.
- Include the solvent(s) used in the process, if available.
- Extract all relevant bands and the material or technique used, such as "KBr pellet."
- Example: ν 1650 cm⁻¹ (C=O), 1600 cm⁻¹ (C=C), 1250 cm⁻¹ (C-N)

Data Entry Guidelines:
- Make sure to extract the peaks and bands for all MOPs mentioned in the text.
- For each synthesis procedure, fill in the relevant details under the specified categories.
- If any information is missing or unclear, use "N/A" as a placeholder.
- Each characterisation device can have multiple characterisation items.
- CCDC numbers are used as unique identifiers for products.
- Focus on the synthesis section of the paper.

Device Information Extraction:
- Look for equipment mentions in the general synthesis section
- Extract device names, models, and specifications
- Note any solvent or material requirements for each technique
- Pay attention to experimental conditions and parameters

Characterisation Data Extraction:
- Extract data for each product/MOP separately
- Include all available characterization results
- Note any specific experimental conditions
- Capture both qualitative and quantitative data

WORKFLOW:
1) init_characterisation_object(task_name)
2) For each set of characterisation data mentioned in the paper:
   - add_characterisation_device(task_name, hnmr_device_name, hnmr_frequency, hnmr_solvents, 
     ea_device_name, ir_device_name, ir_solvents)
   - add_characterisation_item(task_name, product_names, ccdc_number, hnmr_shifts, 
     hnmr_solvent, hnmr_temperature, ea_calculated, ea_experimental, ea_formula, 
     ea_device, ir_material, ir_bands)
3) get_characterisation_summary(task_name) to inspect
4) mops_characterisation_output(task_name) to write final JSON

Task name is always {task_name}

Paper Content:
{paper_content}

"""

INSTRUCTION_TEST_PROMPT = """
Simply make up a Characterisation object and output it.
"""

async def characterisation_agent(task_meta_name: str, test_mode: bool = False):
    """
    This agent reads the md file produced and outputs the characterisation information involved
    """
    logger = get_logger("agent", "CharacterisationAgent")
    logger.info(f"Starting characterisation agent for task: {task_meta_name}")
    
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
    mcp_tools = ["mops_characterisation_output"]
    agent = BaseAgent(model_name="gpt-4o", model_config=model_config, remote_model=True, mcp_tools=mcp_tools, mcp_set_name="mops_mcp.json")   
    
    if test_mode:
        instruction = INSTRUCTION_TEST_PROMPT
    else:
        instruction = INSTRUCTION_CHARACTERISATION_PROMPT.format(
            task_name=task_meta_name, 
            paper_content=paper_content
        )
    
    response, metadata = await agent.run(instruction, recursion_limit=200)
    return response

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Characterisation Agent for MOPs')
    parser.add_argument('--test', action='store_true', help='Run mock prompt without any file')
    args = parser.parse_args()
    
    with open('data/log/agent.log', 'w') as log_file:
        log_file.write('')

    if args.test:
        # Test mode: run mock prompt without any file
        print("Running in test mode with mock prompt (no file)")
        asyncio.run(characterisation_agent("", test_mode=True))
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
                    asyncio.run(characterisation_agent(folder, test_mode=False))
                except FileNotFoundError:
                    print(f"Markdown file not found: {md_file_path}")
                except Exception as e:
                    print(f"Error reading markdown file: {e}")
