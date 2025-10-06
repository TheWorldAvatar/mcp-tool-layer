"""
With the extracted A-Box of OntoSynthesis,
it creates the ontomops A-Box and connect the entities 
across the two A-Boxes.

It uses agent created mcp server (for now, semi-automatically created mcp server
named "mop_extension")
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

Here is the OntoSynthesis A-Box:

{ontosynthesis_a_box}

Here is the paper content:

{paper_content}

"""

TEST_ONTOSYNTHESIS_A_BOX = """

@prefix bibo: <http://purl.org/ontology/bibo/> .
@prefix dc: <http://purl.org/dc/elements/1.1/> .
@prefix om-2: <http://www.ontology-of-units-of-measure.org/resource/om-2/> .
@prefix ontomops: <https://www.theworldavatar.com/kg/ontomops/> .
@prefix ontosyn: <https://www.theworldavatar.com/kg/OntoSyn/> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .

<https://www.theworldavatar.com/kg/OntoSyn/instance/ChemicalSynthesis/be17d765> a ontosyn:ChemicalSynthesis ;
    rdfs:label "https://www.theworldavatar.com/kg/OntoSyn/instance/ChemicalSynthesis/be17d765" ;
    ontosyn:hasYield <https://www.theworldavatar.com/kg/OntoSyn/instance/AmountOfSubstanceFraction/yield-of-vmopa> .

<https://www.theworldavatar.com/kg/OntoSyn/instance/ChemicalSynthesis/structural-transformation-from-vmopa-to-vmopb> a ontosyn:ChemicalSynthesis ;
    rdfs:label "Structural transformation from VMOPa to VMOPb" ;
    ontosyn:hasChemicalInput <https://www.theworldavatar.com/kg/OntoSyn/instance/ChemicalInput/h2edb>,
        <https://www.theworldavatar.com/kg/OntoSyn/instance/ChemicalInput/v6o6-och3-9-so4-4-5> ;
    ontosyn:hasChemicalOutput <https://www.theworldavatar.com/kg/OntoSyn/instance/ChemicalOutput/vmopa> ;
    ontosyn:hasYield <https://www.theworldavatar.com/kg/OntoSyn/instance/AmountOfSubstanceFraction/yield-of-vmopb> .

<https://www.theworldavatar.com/kg/OntoSyn/instance/ChemicalSynthesis/structural-transformation-from-vmopb-to-vmopa> a ontosyn:ChemicalSynthesis ;
    rdfs:label "Structural transformation from VMOPb to VMOPa" ;
    ontosyn:hasChemicalInput <https://www.theworldavatar.com/kg/OntoSyn/instance/ChemicalInput/h2edb>,
        <https://www.theworldavatar.com/kg/OntoSyn/instance/ChemicalInput/v6o6-och3-9-so4-4-5> ;
    ontosyn:hasChemicalOutput <https://www.theworldavatar.com/kg/OntoSyn/instance/ChemicalOutput/vmopa> ;
    ontosyn:hasYield <https://www.theworldavatar.com/kg/OntoSyn/instance/AmountOfSubstanceFraction/yield-of-vmopa> .

<https://www.theworldavatar.com/kg/OntoSyn/instance/ChemicalSynthesis/synthesis-of-vmopa> a ontosyn:ChemicalSynthesis ;
    rdfs:label "Synthesis of VMOPa" .

<https://www.theworldavatar.com/kg/OntoSyn/instance/ChemicalSynthesis/synthesis-of-vmopb> a ontosyn:ChemicalSynthesis ;
    rdfs:label "Synthesis of VMOPb" ;
    ontosyn:hasChemicalInput <https://www.theworldavatar.com/kg/OntoSyn/instance/ChemicalInput/h2edb>,
        <https://www.theworldavatar.com/kg/OntoSyn/instance/ChemicalInput/me2nh2-5-v6o6-och3-9-so4-4> ;
    ontosyn:hasChemicalOutput <https://www.theworldavatar.com/kg/OntoSyn/instance/ChemicalOutput/vmopb> ;
    ontosyn:retrievedFrom <https://doi.org/10.1002/anie.201811027> .

<https://www.theworldavatar.com/kg/OntoSyn/instance/Session> a owl:NamedIndividual ;
    rdfs:label "Session be17d765" ;
    dc:identifier "be17d765" .

<https://www.theworldavatar.com/kg/OntoSyn/instance/Session/be17d765> a ontosyn:ChemicalSynthesis ;
    rdfs:label "https://www.theworldavatar.com/kg/OntoSyn/instance/Session/be17d765" ;
    ontosyn:hasChemicalInput <https://www.theworldavatar.com/kg/OntoSyn/instance/ChemicalInput/H2edb>,
        <https://www.theworldavatar.com/kg/OntoSyn/instance/ChemicalInput/[Me2NH2]5[V6O6(OCH3)9(SO4)4]> ;
    ontosyn:hasChemicalOutput <https://www.theworldavatar.com/kg/OntoSyn/instance/ChemicalOutput/vmopa> ;
    ontosyn:hasYield <https://www.theworldavatar.com/kg/OntoSyn/instance/AmountOfSubstanceFraction/yield-of-vmopa> .

<https://doi.org/10.1002/anie.201811027> a bibo:Document ;
    rdfs:label "https://doi.org/10.1002/anie.201811027" .

<https://www.theworldavatar.com/kg/OntoSyn/instance/ChemicalInput/H2edb> a ontosyn:ChemicalInput ;
    rdfs:label "https://www.theworldavatar.com/kg/OntoSyn/instance/ChemicalInput/H2edb" ;
    ontosyn:isSuppliedBy <https://www.theworldavatar.com/kg/OntoSyn/instance/Supplier/n-a> .

<https://www.theworldavatar.com/kg/OntoSyn/instance/ChemicalInput/[Me2NH2]5[V6O6(OCH3)9(SO4)4]> a ontosyn:ChemicalInput ;
    rdfs:label "https://www.theworldavatar.com/kg/OntoSyn/instance/ChemicalInput/[Me2NH2]5[V6O6(OCH3)9(SO4)4]" ;
    ontosyn:isSuppliedBy <https://www.theworldavatar.com/kg/OntoSyn/instance/Supplier/n-a> .

<https://www.theworldavatar.com/kg/OntoSyn/instance/ChemicalInput/me2nh2-5-v6o6-och3-9-so4-4> a ontosyn:ChemicalInput ;
    rdfs:label "[Me2NH2]5[V6O6(OCH3)9(SO4)4]" ;
    ontosyn:hasChemicalDescription "Hexanuclear polyoxovanadate cluster used in the synthesis of VMOPb" ;
    ontosyn:hasChemicalFormula "C12H18N5O16S4V6" ;
    ontosyn:hasPurity "N/A" ;
    ontosyn:isSuppliedBy <https://www.theworldavatar.com/kg/OntoSyn/instance/Supplier/n-a> .

<https://www.theworldavatar.com/kg/OntoSyn/instance/ChemicalOutput/VMOPa> a ontomops:MetalOrganicPolyhedron ;
    rdfs:label "https://www.theworldavatar.com/kg/OntoSyn/instance/ChemicalOutput/VMOPa" .

<https://www.theworldavatar.com/kg/OntoSyn/instance/ChemicalOutput/VMOPb> a ontomops:MetalOrganicPolyhedron ;
    rdfs:label "https://www.theworldavatar.com/kg/OntoSyn/instance/ChemicalOutput/VMOPb" .

<https://www.theworldavatar.com/kg/OntoSyn/instance/MetalOrganicPolyhedron/vmopb> a ontomops:MetalOrganicPolyhedron ;
    rdfs:label "https://www.theworldavatar.com/kg/OntoSyn/instance/MetalOrganicPolyhedron/vmopb" .

<https://www.theworldavatar.com/kg/OntoSyn/instance/Supplier/changchun-university-of-science-and-technology> a ontosyn:Supplier ;
    rdfs:label "Changchun University of Science and Technology" .

<https://www.theworldavatar.com/kg/OntoSyn/instance/Supplier/northeast-normal-university> a ontosyn:Supplier ;
    rdfs:label "Northeast Normal University" .

<https://www.theworldavatar.com/kg/OntoSyn/instance/AmountOfSubstanceFraction/yield-of-vmopb> a om-2:AmountOfSubstanceFraction ;
    rdfs:label "Yield of VMOPb" ;
    om-2:hasNumericalValue 5e+01 ;
    om-2:hasUnit om-2:percent .

<https://www.theworldavatar.com/kg/OntoSyn/instance/ChemicalInput/v6o6-och3-9-so4-4-5> a ontosyn:ChemicalInput ;
    rdfs:label "[V6O6(OCH3)9(SO4)4] 5-" ;
    ontosyn:hasAlternativeNames "Hexanuclear polyoxovanadate cluster",
        "Hexavanadate cluster" ;
    ontosyn:hasChemicalDescription "Hexanuclear polyoxovanadate cluster" ;
    ontosyn:hasChemicalFormula "C0H0O0S0V6",
        "C12H27O9S4V6",
        "C9H27O12S4V6" ;
    ontosyn:hasPurity "N/A" ;
    ontosyn:isSuppliedBy <https://www.theworldavatar.com/kg/OntoSyn/instance/Supplier/northeast-normal-university> .

<https://www.theworldavatar.com/kg/OntoSyn/instance/ChemicalOutput/vmopb> a ontosyn:ChemicalOutput,
        ontosyn:ChemicalSynthesis ;
    rdfs:label "VMOPb" ;
    ontosyn:hasChemicalOutput <https://www.theworldavatar.com/kg/OntoSyn/instance/ChemicalOutput/vmopb> ;
    ontosyn:hasYield <https://www.theworldavatar.com/kg/OntoSyn/instance/AmountOfSubstanceFraction/yield-of-vmopb> ;
    ontosyn:isRepresentedBy <https://www.theworldavatar.com/kg/OntoSyn/instance/ChemicalOutput/VMOPb>,
        <https://www.theworldavatar.com/kg/OntoSyn/instance/MetalOrganicPolyhedron/vmopb> .

<https://www.theworldavatar.com/kg/OntoSyn/instance/ChemicalInput/h2edb> a ontosyn:ChemicalInput ;
    rdfs:label "H2edb" ;
    ontosyn:hasAlternativeNames "2-diyl)dibenzoic acid",
        "4",
        "4'-(ethyne-1" ;
    ontosyn:hasChemicalDescription "4,4'-(ethyne-1,2-diyl)dibenzoic acid",
        "4,4'-(ethyne-1,2-diyl)dibenzoic acid ligand used in the synthesis of VMOPb" ;
    ontosyn:hasChemicalFormula "C16H12O4",
        "C18H14O4" ;
    ontosyn:hasPurity "N/A" ;
    ontosyn:isSuppliedBy <https://www.theworldavatar.com/kg/OntoSyn/instance/Supplier/changchun-university-of-science-and-technology> .

<https://www.theworldavatar.com/kg/OntoSyn/instance/Supplier/n-a> a ontosyn:Supplier ;
    rdfs:label "N/A" .

<https://www.theworldavatar.com/kg/OntoSyn/instance/AmountOfSubstanceFraction/yield-of-vmopa> a om-2:AmountOfSubstanceFraction ;
    rdfs:label "Yield of VMOPa" ;
    om-2:hasNumericalValue 5.6e+01,
        6e+01 ;
    om-2:hasUnit om-2:percent .

<https://www.theworldavatar.com/kg/OntoSyn/instance/ChemicalOutput/vmopa> a ontosyn:ChemicalOutput,
        ontosyn:ChemicalSynthesis ;
    rdfs:label "VMOPa" ;
    ontosyn:hasChemicalOutput <https://www.theworldavatar.com/kg/OntoSyn/instance/ChemicalOutput/vmopa> ;
    ontosyn:hasYield <https://www.theworldavatar.com/kg/OntoSyn/instance/AmountOfSubstanceFraction/yield-of-vmopa> ;
    ontosyn:isRepresentedBy <https://www.theworldavatar.com/kg/OntoSyn/instance/ChemicalOutput/VMOPa> .

"""
 

TEST_PROMPT = """

Your task is to extend the provided A-Box of OntoSynthesis with the ontomops A-Box, according to the paper content. 

This is the existing A-Box of OntoSynthesis:

{ontosynthesis_a_box}
 
Make up the ontomops A-Box content. This is only a trial run, so no paper content is provided.
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

async def mop_extension_agent():
    model_config = ModelConfig()
    mcp_tools = ["mops_extension"]
    agent = BaseAgent(model_name="gpt-4o", model_config=model_config, remote_model=True, mcp_tools=mcp_tools, mcp_set_name="extension.json")
    
    # Load actual output.ttl content
    ontosynthesis_a_box = load_output_ttl_content()
    response, metadata = await agent.run(PROMPT.format(ontosynthesis_a_box=ontosynthesis_a_box), recursion_limit=200)
    return response

async def mop_extension_agent_with_content(paper_content):
    model_config = ModelConfig()
    mcp_tools = ["mops_extension"]
    agent = BaseAgent(model_name="gpt-4o", model_config=model_config, remote_model=True, mcp_tools=mcp_tools, mcp_set_name="extension.json")
    
    # Load actual output.ttl content
    ontosynthesis_a_box = load_output_ttl_content()
    response, metadata = await agent.run(PROMPT.format(ontosynthesis_a_box=ontosynthesis_a_box, paper_content=paper_content), recursion_limit=200)
    return response

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='MOP Extension Agent')
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
                response = asyncio.run(mop_extension_agent_with_content(paper_content))
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
        response = asyncio.run(mop_extension_agent())
        print(response)