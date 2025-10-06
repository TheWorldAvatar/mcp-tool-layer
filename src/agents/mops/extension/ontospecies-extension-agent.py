"""
With the extracted A-Box of OntoSynthesis,
it creates the ontospecies A-Box and connect the entities 
across the two A-Boxes.

It uses agent created mcp server (for now, semi-automatically created mcp server
named "species_extension")
"""

PROMPT = """

Your task is to extend the provided A-Box of OntoSynthesis with the ontospecies A-Box, according to the paper content. 

You should use the provided MCP server to populate the ontospecies A-Box. 

Here is the recommended route of task:

- In the provided OntoSynthesis A-Box, ChemicalOutput instances always have characterisations, this is where they should be 
connected to the ontospecies A-Box. (So for you, you should create the corresponding A-Box information about the characterisations of the 
products in the ontosynthesis A-Box, use the existing instances in the ontospecies A-Box as a reference.)
- Find the according information in the paper that you need to populate the ontospecies A-Box.
- Populate the ontospecies A-Box with the information you found with the MCP server.

Requirements:

- It is compulsory to call **every** mcp server function while populating the ontospecies A-Box. If the information is 
indeed missing in the paper, use 'N/A' as the value.
- In the provided OntoSynthesis A-Box, instances have their IRIs already, you should reuse the exact same IRI for the ontospecies A-Box instances.
- The final output file name should be "ontospecies_extension.ttl" only. 
- Make sure when you create the instances, provide all inputs.  
- Cover as many information as possible, try to call every mcp server function while populating the ontospecies A-Box. Inclusion priority over accuracy.
- Extract information from the paper content provided below - do not make up any information.
- Always include material used in the device for IR data. (e.g., KBr, KBr pellet, etc.)

Here is the OntoSynthesis A-Box:

{ontosynthesis_a_box}

Here is the paper content:

{paper_content}

"""

TEST_ONTOSYNTHESIS_A_BOX = """

@prefix ontospecies: <https://www.theworldavatar.com/kg/ontospecies/> .
@prefix ontosyn: <https://www.theworldavatar.com/kg/OntoSyn/> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .

<https://www.theworldavatar.com/kg/OntoSyn/instance/ChemicalOutput/VMOPa> a ontospecies:Species ;
    rdfs:label "https://www.theworldavatar.com/kg/OntoSyn/instance/ChemicalOutput/VMOPa" .

<https://www.theworldavatar.com/kg/OntoSyn/instance/ChemicalOutput/VMOPb> a ontospecies:Species ;
    rdfs:label "https://www.theworldavatar.com/kg/OntoSyn/instance/ChemicalOutput/VMOPb" .

<https://www.theworldavatar.com/kg/OntoSyn/instance/ChemicalOutput/vmopb> a ontosyn:ChemicalOutput,
        ontosyn:ChemicalSynthesis ;
    rdfs:label "VMOPb" ;
    ontosyn:hasChemicalOutput <https://www.theworldavatar.com/kg/OntoSyn/instance/ChemicalOutput/vmopb> ;
    ontosyn:hasYield <https://www.theworldavatar.com/kg/OntoSyn/instance/AmountOfSubstanceFraction/yield-of-vmopb> ;
    ontosyn:isRepresentedBy <https://www.theworldavatar.com/kg/OntoSyn/instance/ChemicalOutput/VMOPb>,
        <https://www.theworldavatar.com/kg/OntoSyn/instance/Species/vmopb> .

<https://www.theworldavatar.com/kg/OntoSyn/instance/ChemicalOutput/vmopa> a ontosyn:ChemicalOutput,
        ontosyn:ChemicalSynthesis ;
    rdfs:label "VMOPa" ;
    ontosyn:hasChemicalOutput <https://www.theworldavatar.com/kg/OntoSyn/instance/ChemicalOutput/vmopa> ;
    ontosyn:hasYield <https://www.theworldavatar.com/kg/OntoSyn/instance/AmountOfSubstanceFraction/yield-of-vmopa> ;
    ontosyn:isRepresentedBy <https://www.theworldavatar.com/kg/OntoSyn/instance/ChemicalOutput/VMOPa> .

"""

TEST_PROMPT = """

Your task is to extend the provided A-Box of OntoSynthesis with the ontospecies A-Box, according to the paper content. 

This is the existing A-Box of OntoSynthesis:

{ontosynthesis_a_box}
 
Make up the ontospecies A-Box content. This is only a trial run, so no paper content is provided.
"""


import asyncio
import os
import shutil
import argparse

from models.BaseAgent import BaseAgent
from models.ModelConfig import ModelConfig

def clear_previous_data():
    """
    Clear previous memory and output files before running.
    """
    # Clear memory directory

    memory_file_list = ["memory_ontospecies.ttl", "memory_ontospecies.lock"]

    memory_dir = "memory"
    if os.path.exists(memory_dir):
        for file_path in memory_file_list:
            if os.path.exists(os.path.join(memory_dir, file_path)):
                os.remove(os.path.join(memory_dir, file_path))
                print(f"Removed {os.path.join(memory_dir, file_path)}")
    
    # Clear output TTL files
    output_files = ["ontospecies_extension.ttl", "ontospecies_snapshot.ttl"]
    for file_path in output_files:
        if os.path.exists(file_path):
            os.remove(file_path)
            print(f"Removed {file_path}")
    
    # Recreate memory directory
    os.makedirs(memory_dir, exist_ok=True)
    print("Previous data cleared and directories recreated")

def load_output_ttl_content(output_file="output.ttl"):
    """Load the content from output.ttl file."""
    if not os.path.exists(output_file):
        print(f"Warning: {output_file} not found, using test data")
        return TEST_ONTOSYNTHESIS_A_BOX
    
    try:
        with open(output_file, 'r', encoding='utf-8') as f:
            content = f.read()
        print(f"Successfully loaded {output_file} content")
        return content
    except Exception as e:
        print(f"Error loading {output_file}: {e}, using test data")
        return TEST_ONTOSYNTHESIS_A_BOX

async def species_extension_agent():
    model_config = ModelConfig()
    mcp_tools = ["ontospecies_extension"]
    agent = BaseAgent(model_name="gpt-4o-mini", model_config=model_config, remote_model=True, mcp_tools=mcp_tools, mcp_set_name="extension.json")
    
    # Load actual output.ttl content
    ontosynthesis_a_box = load_output_ttl_content()
    response, metadata = await agent.run(PROMPT.format(ontosynthesis_a_box=ontosynthesis_a_box), recursion_limit=500)
    return response

async def species_extension_agent_with_content(paper_content):
    model_config = ModelConfig()
    mcp_tools = ["ontospecies_extension"]
    agent = BaseAgent(model_name="gpt-4o-mini", model_config=model_config, remote_model=True, mcp_tools=mcp_tools, mcp_set_name="extension.json")
    
    # Load actual output.ttl content
    ontosynthesis_a_box = load_output_ttl_content()
    response, metadata = await agent.run(PROMPT.format(ontosynthesis_a_box=ontosynthesis_a_box, paper_content=paper_content), recursion_limit=500)
    return response

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Species Extension Agent')
    parser.add_argument('--test', action='store_true', help='Run test mode with specific file (10.1002_anie.201811027)')
    args = parser.parse_args()
    
    # Clear previous data before every run
    print("Clearing previous data...")
    clear_previous_data()
    
    if args.test:
        # Test mode: run specific file 10.1002_anie.201811027
        print("Running in test mode with specific file: 10.1002_anie.201811027")
        
        # Look for the file in sandbox/tasks with _complete suffix
        data_folder = "sandbox/tasks"
        if os.path.exists(data_folder):
            # Find DOI-specific subfolder
            test_file = None
            for item in os.listdir(data_folder):
                if os.path.isdir(os.path.join(data_folder, item)) and "10.1002_anie.201811027" in item:
                    complete_file = os.path.join(data_folder, item, f"{item}_complete.md")
                    if os.path.exists(complete_file):
                        test_file = complete_file
                        break
            
            if test_file:
                print(f"Found test file: {test_file}")
                # Load the actual paper content from the file
                with open(test_file, 'r', encoding='utf-8') as f:
                    paper_content = f.read()
                
                # Use the PROMPT with the loaded paper content and output.ttl
                response = asyncio.run(species_extension_agent_with_content(paper_content))
                print(response)
            else:
                print("Test file not found. Available DOI folders:")
                for item in os.listdir(data_folder):
                    if os.path.isdir(os.path.join(data_folder, item)):
                        print(f"  - {item}")
        else:
            print("Data folder not found: sandbox/tasks")
    else:
        # Normal mode - will load output.ttl dynamically
        response = asyncio.run(species_extension_agent())
        print(response)
