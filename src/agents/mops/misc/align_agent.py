"""
The SPARQL agent provides access to knowledge graph queries for Metal-Organic Polyhedra,
chemical synthesis data, and chemical species information through SPARQL endpoints.
Updated to support dynamic A-Box content from CBU ground truth data.
"""

from models.BaseAgent import BaseAgent
from models.ModelConfig import ModelConfig
from models.locations import SANDBOX_TASK_DIR, PLAYGROUND_DATA_DIR
from src.utils.global_logger import get_logger
import asyncio
import os
import glob
import json
from typing import Optional, Dict, Any
from datetime import datetime
from pathlib import Path  

def load_sampling_report():
    """Load the latest ontology sampling report from playground directory."""
    sampling_files = glob.glob("playground/ontomops_sampling_ontomops_ogm_2hop_10classes_20250919_165443.md")
    if not sampling_files:
        return "No sampling report found."
    
    # Get the most recent file
    latest_file = max(sampling_files, key=os.path.getmtime)
    
    try:
        with open(latest_file, 'r', encoding='utf-8') as f:
            return f.read()
    except Exception as e:
        return f"Error reading sampling report: {e}"

def load_cbu_files():
    """Load all CBU JSON files from the ground truth directory."""
    cbu_dir = "playground/data/ground_truth/cbu"
    cbu_files = glob.glob(os.path.join(cbu_dir, "*.json"))
    
    print(f"Found {len(cbu_files)} CBU JSON files")
    
    data = []
    for file_path in cbu_files:
        try:
            print(f"Loading: {file_path}")
            with open(file_path, 'r', encoding='utf-8') as f:
                file_data = json.load(f)
                # Extract DOI from filename (remove .json extension)
                doi = os.path.basename(file_path).replace('.json', '')
                file_data['doi'] = doi
                
                # Validate basic structure
                if 'synthesisProcedures' not in file_data:
                    print(f"  Warning: No 'synthesisProcedures' in {doi}")
                else:
                    print(f"  Found {len(file_data['synthesisProcedures'])} procedures in {doi}")
                
                data.append(file_data)
        except Exception as e:
            print(f"Error loading {file_path}: {e}")
    
    print(f"Successfully loaded {len(data)} CBU files")
    return data

def get_shorter_name(species_names):
    """Get the shorter name from a list of species names."""
    if not species_names:
        return ""
    if len(species_names) == 1:
        return species_names[0]
    
    # Return the shorter name
    return min(species_names, key=len)

def generate_a_box_content(mop_data: Dict[str, Any], doi: str) -> str:
    """Generate A-Box content for a specific MOP synthesis procedure."""
    mop_ccdc = mop_data['mopCCDCNumber']
    
    # Get shorter species names
    species_name1 = get_shorter_name(mop_data['cbuSpeciesNames1'])
    species_name2 = get_shorter_name(mop_data['cbuSpeciesNames2'])
    
    # Generate unique instance IDs
    instance_id1 = f"ChemicalInput_{mop_ccdc}_1"
    instance_id2 = f"ChemicalInput_{mop_ccdc}_2"
    doc_instance_id = f"Document_{mop_ccdc}"
    
    # Add deprotonation hint only for species that contain H2/H3/H4 in their names
    # This gives the agent a hint about deprotonation without revealing the formula
    deproton_comment = ""
    if any(h in species_name2.upper() for h in ['H2', 'H3', 'H4']):
        h_count = 'H2'
        if 'H3' in species_name2.upper():
            h_count = 'H3'
        elif 'H4' in species_name2.upper():
            h_count = 'H4'
        deproton_comment = f"\n    rdfs:comment \"In other knowledge graph, we use deprotonated form of the species. As a result, you should get the formula for the provided name first, and remove {h_count} from the formula at your discretion.\""
    
    a_box_content = f"""@prefix bibo: <http://purl.org/ontology/bibo/> .
                        @prefix dc: <http://purl.org/dc/elements/1.1/> .
                        @prefix inst: <https://www.theworldavatar.com/kg/ontosyn/instance/> .
                        @prefix kg: <https://www.theworldavatar.com/kg/> .
                        @prefix ontosyn: <https://www.theworldavatar.com/kg/OntoSyn/> .
                        @prefix owl: <http://www.w3.org/2002/07/owl#> .
                        @prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .

inst:{instance_id1} a ontosyn:ChemicalInput ;
    rdfs:label "{species_name1}" .
    
inst:{instance_id2} a ontosyn:ChemicalInput ;
    rdfs:label "{species_name2}" ;{deproton_comment}

inst:{doc_instance_id} a bibo:Document ;
    rdfs:label "Metal-Organic Polyhedra Synthesis" ;
    dc:title "Metal-Organic Polyhedra Synthesis" ;
    bibo:doi "{doi}" ."""
    
    return a_box_content

def get_all_mops_data():
    """Get all MOP data from CBU files with their A-Box content."""
    cbu_data = load_cbu_files()
    all_mops = []
    
    for file_data in cbu_data:
        doi = file_data['doi']
        print(f"Processing file: {doi}")
        
        if 'synthesisProcedures' not in file_data:
            print(f"Warning: No 'synthesisProcedures' found in {doi}")
            continue
            
        for idx, procedure in enumerate(file_data['synthesisProcedures']):
            try:
                # Debug: print the keys available in this procedure
                print(f"  Procedure {idx} keys: {list(procedure.keys())}")
                
                # Check for required fields
                required_fields = ['mopCCDCNumber', 'cbuFormula1', 'cbuSpeciesNames1', 'cbuFormula2', 'cbuSpeciesNames2']
                missing_fields = [field for field in required_fields if field not in procedure]
                
                if missing_fields:
                    print(f"  Warning: Missing fields in {doi}, procedure {idx}: {missing_fields}")
                    continue
                
                mop_info = {
                    'doi': doi,
                    'mop_ccdc': procedure['mopCCDCNumber'],
                    'procedure_index': idx,
                    'ground_truth': {
                        'cbuFormula1': procedure['cbuFormula1'],
                        'cbuSpeciesNames1': procedure['cbuSpeciesNames1'],
                        'cbuFormula2': procedure['cbuFormula2'],
                        'cbuSpeciesNames2': procedure['cbuSpeciesNames2'],
                        'selected_name1': get_shorter_name(procedure['cbuSpeciesNames1']),
                        'selected_name2': get_shorter_name(procedure['cbuSpeciesNames2'])
                    },
                    'a_box_content': generate_a_box_content(procedure, doi)
                }
                all_mops.append(mop_info)
                print(f"  ✓ Successfully processed MOP {procedure['mopCCDCNumber']}")
                
            except Exception as e:
                print(f"  ✗ Error processing procedure {idx} in {doi}: {e}")
                print(f"    Available keys: {list(procedure.keys()) if isinstance(procedure, dict) else 'Not a dict'}")
                continue
    
    print(f"Total MOPs loaded: {len(all_mops)}")
    return all_mops

def save_individual_result(mop_info: Dict[str, Any], response: str, error: Optional[str] = None):
    """Save individual alignment result to a markdown file."""
    
    # Create results directory structure
    results_dir = Path("playground/cbu_alignment_results")
    results_dir.mkdir(exist_ok=True)
    
    # Create timestamp for this run
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]  # Include milliseconds
    
    # Create filename with MOP info and timestamp
    mop_ccdc = mop_info['mop_ccdc']
    doi_clean = mop_info['doi'].replace('.', '_').replace('/', '_')
    filename = f"mop_{mop_ccdc}_{doi_clean}_{timestamp}.md"
    
    filepath = results_dir / filename
    
    # Generate markdown content
    status = "✅ SUCCESS" if error is None else "❌ ERROR"
    
    content = f"""# MOP Alignment Result: {mop_ccdc}

**Status**: {status}  
**Generated**: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}  
**DOI**: {mop_info['doi']}  
**MOP CCDC**: {mop_ccdc}  

## Ground Truth Data

### CBU Formula 1
```
{mop_info['ground_truth']['cbuFormula1']}
```

### CBU Species Names 1
{', '.join(f'`{name}`' for name in mop_info['ground_truth']['cbuSpeciesNames1'])}

**Selected Name 1**: `{mop_info['ground_truth']['selected_name1']}`

### CBU Formula 2  
```
{mop_info['ground_truth']['cbuFormula2']}
```

### CBU Species Names 2
{', '.join(f'`{name}`' for name in mop_info['ground_truth']['cbuSpeciesNames2'])}

**Selected Name 2**: `{mop_info['ground_truth']['selected_name2']}`

## A-Box Input (Provided to Agent)

```turtle
{mop_info['a_box_content']}
```

## Agent Response

"""
    
    if error:
        content += f"**Error occurred during processing:**\n\n```\n{error}\n```\n"
    else:
        content += f"```\n{response}\n```\n"
    
    content += f"""
---

*This file was automatically generated by the CBU alignment system.*  
*File: {filename}*
"""
    
    # Write the file
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(content)
    
    print(f"💾 Saved result to: {filepath}")
    return str(filepath)

A_BOX_CONTENT = """

@prefix bibo: <http://purl.org/ontology/bibo/> .
@prefix dc: <http://purl.org/dc/elements/1.1/> .
@prefix inst: <https://www.theworldavatar.com/kg/ontosyn/instance/> .
@prefix kg: <https://www.theworldavatar.com/kg/> .
@prefix ontosyn: <https://www.theworldavatar.com/kg/OntoSyn/> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .

inst:ChemicalInput_1757445403608918112_7f5b64c1c4fd7f51 a ontosyn:ChemicalInput ;
    rdfs:label "[V6O6(CH3O)9(SO4)4]" .
 
    
inst:ChemicalInput_1757445406523432393_31c9e0ce9152d106 a ontosyn:ChemicalInput ;
    rdfs:label "H2edb" .
    rdfs:comment "In other knowledge graph, we use deprotonated form of the species. As a result, you should get the formula for the provided name first, and remove H2 from the formula at your discretion."


inst:Document_1757445389002941096_99996f6b2078963f a bibo:Document ;
    rdfs:label "Bottom-Up Construction and Reversible Structural Transformation of Supramolecular Isomers based on Large Truncated Tetrahedra" ;
    dc:title "Bottom-Up Construction and Reversible Structural Transformation of Supramolecular Isomers based on Large Truncated Tetrahedra" ;
    bibo:doi "10.1002/anie.201811027" .

"""



GENERAL_QUERY_PROMPT = """
# A-Box Extension and Alignment Task

## Objective
Your task is to directly find instances in other knowledge graphs that can be used to **extend and enhance** the connections in the provided OntoSynthesis A-Box. 
You are looking for **potential connection points** that would enrich the existing knowledge graph, not existing direct connections.

For example, assume the A-Box is about specific buildings in a city, and the building instance is connected to a city label "London", 
you should find city instances in other knowledge graphs with label "London", and connect the building instance to the city instances.

When performing the task, ensure you provide context from the given A-Box. For instance, "London" is the label of a city, which is connected to a building. 
Don't just say "Find instance related to London". 

## **Critical**: Search and reorganize strategy: 

However, in many cases, the labels of the instances in the other knowledge graphs may not be exactly the same as the labels in the A-Box. 
As a result, you may need to reference the sampling report to understand the naming conventions of the instances in the other knowledge graphs.

Also, you can use the websearch tool to search the web for information. (e.g., search for chemical species information and its different representations)
The sampling report may give you better idea about how does the existing KG represent the entities (e.g., Whether the species are named as a formula or a product name, etc.)
You can use that information and the websearch tool to get more representations of the species. Then, based on those different representations,
you look at the labels in the other knowledge graphs and study the convention, and come up with reorganized chemical formulas by yourself.

## **Critical**: It is critical to come up with reorganized chemical formulas by yourself, and try fuzzy search with them. 
## **Critical**: If multiple results are found, you should reason with the following aspects:

1. The type of the entity in the other knowledge graphs, whether they match with the context, where the A-Box instances are chemcial inputs, which makes up 
larger products. 

2. Whether the representations found in the other knowledge graphs have **best** reprensentation of the chemical species in the A-Box. 

Although you should provide all vaild (according to cross comparison) results, you should provide the best one according to the context at the end. 

Use websearch tool to get more representations of the species. According to the sampling report, understand how different entity label or representations are used in the 
other knowledge graphs. Your best chance to get a match is to come up with a representation that aligns with the representation in the other knowledge graphs and do 
fuzzy search with them.

Never rely on fuzzy search of single representation, try **ALL** different representations and find the matches.
Cross compare the two different fuzzy searches. Keep in mind chemical fuzzy search can be done in strict mode.

Considering the context of the A-Box, rerank the matches based on the context, especially according to the entity type in the A-Box and in the other knowledge graphs.
Focus on finding instances that can be connected from **only ontomops** knowledge graph.

Make your best judgement according to all the context you have. Including and especially the A-Box rdf:comments. 
 
## Critical Notes

    Also, if the linker has name like H<Number of Hydrogen atoms>XXXX, you should get the formula from the given name first and remove <Number of Hydrogen atoms> x H from the formula.
    Any species with name following this pattern must only be searched with the formula after removing the <Number of Hydrogen atoms> x H. 
    And use strict mode. 

For example, if the species name is H8NCAA, and its formula is C8H16N8, you remove 8*H from the formula, and do search with C8H8N8.


- The ontosynthesis endpoint is down, so you cannot query it directly
- Focus on finding instances that **could be connected** to extend the A-Box
- Show labels and identifiers of found instances when possible
- Look for instances, not classes or properties
- The goal is to identify **extension opportunities**, not existing connections
- Use the sampling report above to understand URI patterns and naming conventions in ontomops

## Critical Requirements
- **Spare no effort** to try different SPARQL queries until you get comprehensive results
- **Perform the queries yourself** - break down the task and query ontologies systematically
- **Query one ontology at a time** for better focus and results
- **Reference the sampling report** to understand ontomops instance patterns and naming conventions

## Provided A-Box Content

{a_box_content}

Use the sampling report patterns to guide your search strategy and understand how similar entities are represented in the ontomops knowledge graph.
"""
 

async def align_agent(custom_a_box_content: Optional[str] = None):
    """
    This agent finds instances in other knowledge graphs that can be used to extend
    and enhance the connections in the provided OntoSynthesis A-Box.
    
    Args:
        custom_a_box_content: Optional custom A-Box content. If None, uses default A_BOX_CONTENT.
    """
    logger = get_logger("agent", "AlignAgent")
    logger.info(f"Starting A-Box alignment agent")
    
    # Load the sampling report for reference
    sampling_report = load_sampling_report()
    
    # Configure the model with appropriate settings for knowledge graph alignment
    model_config = ModelConfig(temperature=0.1, top_p=0.02)
    # MCP tools available for SPARQL operations
    mcp_tools = ["query_sparql", "websearch"]
    
    # Initialize the agent with alignment MCP configuration
    agent = BaseAgent(
        model_name="gpt-5", 
        model_config=model_config, 
        remote_model=True, 
        mcp_tools=mcp_tools, 
        mcp_set_name="sparql.json"
    )   
    
    # Use custom A-Box content if provided, otherwise use default
    a_box_content = custom_a_box_content if custom_a_box_content is not None else A_BOX_CONTENT
    
    # Format the prompt with both sampling report and A-Box content
    instruction = GENERAL_QUERY_PROMPT.format(
        sampling_report=sampling_report,
        a_box_content=a_box_content
    )
    
    # Run the alignment task
    response, metadata = await agent.run(instruction, recursion_limit=600)
    logger.info("A-Box alignment completed")
    return response

async def align_agent_for_mop(mop_ccdc: str, doi: Optional[str] = None):
    """
    Run alignment agent for a specific MOP CCDC number from CBU data.
    
    Args:
        mop_ccdc: The MOP CCDC number to process
        doi: Optional DOI to filter by. If None, uses first match found.
    """
    logger = get_logger("agent", "AlignAgent")
    
    # Get all MOP data
    all_mops = get_all_mops_data()
    
    # Find the specific MOP
    target_mop = None
    for mop in all_mops:
        if mop['mop_ccdc'] == mop_ccdc:
            if doi is None or mop['doi'] == doi:
                target_mop = mop
                break
    
    if target_mop is None:
        logger.error(f"MOP CCDC {mop_ccdc} not found in CBU data")
        return None
    
    logger.info(f"Processing MOP CCDC: {mop_ccdc} from DOI: {target_mop['doi']}")
    logger.info(f"Species 1: {target_mop['ground_truth']['selected_name1']}")
    logger.info(f"Species 2: {target_mop['ground_truth']['selected_name2']}")
    
    # Run alignment with the specific MOP's A-Box content
    try:
        response = await align_agent(target_mop['a_box_content'])
        
        # Save individual result
        result_file = save_individual_result(target_mop, response)
        logger.info(f"Result saved to: {result_file}")
        
        return {
            'mop_info': target_mop,
            'response': response,
            'result_file': result_file,
            'success': True,
            'error': None
        }
    except Exception as e:
        error_msg = str(e)
        logger.error(f"Error processing MOP {mop_ccdc}: {error_msg}")
        
        # Save error result
        result_file = save_individual_result(target_mop, "", error_msg)
        logger.info(f"Error result saved to: {result_file}")
        
        return {
            'mop_info': target_mop,
            'response': None,
            'result_file': result_file,
            'success': False,
            'error': error_msg
        }

async def process_all_cbu_mops():
    """
    Process all MOPs from CBU data and generate alignment results.
    """
    logger = get_logger("agent", "CBUProcessor")
    logger.info("Starting processing of all CBU MOPs")
    
    all_mops = get_all_mops_data()
    results = []
    
    for i, mop in enumerate(all_mops, 1):
        logger.info(f"Processing MOP {i}/{len(all_mops)}: {mop['mop_ccdc']} ({mop['doi']})")
        
        try:
            response = await align_agent(mop['a_box_content'])
            
            # Save individual result
            result_file = save_individual_result(mop, response)
            logger.info(f"Result saved to: {result_file}")
            
            result = {
                'mop_info': mop,
                'response': response,
                'result_file': result_file,
                'success': True,
                'error': None
            }
        except Exception as e:
            error_msg = str(e)
            logger.error(f"Error processing MOP {mop['mop_ccdc']}: {error_msg}")
            
            # Save error result
            result_file = save_individual_result(mop, "", error_msg)
            logger.info(f"Error result saved to: {result_file}")
            
            result = {
                'mop_info': mop,
                'response': None,
                'result_file': result_file,
                'success': False,
                'error': error_msg
            }
        
        results.append(result)
    
    logger.info(f"Completed processing {len(results)} MOPs")
    return results

if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1:
        mode = sys.argv[1]
        
        if mode == "list":
            # List all available MOPs
            all_mops = get_all_mops_data()
            print(f"Available MOPs ({len(all_mops)} total):")
            for mop in all_mops:
                print(f"  CCDC: {mop['mop_ccdc']}, DOI: {mop['doi']}")
                print(f"    Species 1: {mop['ground_truth']['selected_name1']}")
                print(f"    Species 2: {mop['ground_truth']['selected_name2']}")
                print()
        
        elif mode == "mop" and len(sys.argv) > 2:
            # Process specific MOP
            mop_ccdc = sys.argv[2]
            doi = sys.argv[3] if len(sys.argv) > 3 else None
            result = asyncio.run(align_agent_for_mop(mop_ccdc, doi))
            if result:
                if result['success']:
                    print(f"=== MOP {mop_ccdc} Results ===")
                    print(f"DOI: {result['mop_info']['doi']}")
                    print(f"Ground Truth:")
                    print(f"  Species 1: {result['mop_info']['ground_truth']['selected_name1']}")
                    print(f"  Species 2: {result['mop_info']['ground_truth']['selected_name2']}")
                    print(f"\nAgent Response:")
                    print(result['response'])
                    print(f"\n📁 Full result saved to: {result['result_file']}")
                else:
                    print(f"❌ Error processing MOP {mop_ccdc}: {result['error']}")
                    print(f"📁 Error details saved to: {result['result_file']}")
            else:
                print(f"MOP {mop_ccdc} not found")
        
        elif mode == "all":
            # Process all MOPs
            results = asyncio.run(process_all_cbu_mops())
            
            # Save results to markdown
            from datetime import datetime
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            report_file = f"playground/cbu_alignment_results_{timestamp}.md"
            
            with open(report_file, 'w', encoding='utf-8') as f:
                f.write(f"# CBU Alignment Results\n\n")
                f.write(f"Generated on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write(f"Total MOPs processed: {len(results)}\n\n")
                
                successful = sum(1 for r in results if r['success'])
                failed = len(results) - successful
                f.write(f"Success: {successful}, Failed: {failed}\n\n")
                
                for i, result in enumerate(results, 1):
                    mop = result['mop_info']
                    status = "✅ SUCCESS" if result['success'] else "❌ ERROR"
                    
                    f.write(f"## {i}. MOP CCDC: {mop['mop_ccdc']} ({mop['doi']}) {status}\n\n")
                    f.write(f"### Ground Truth Data\n")
                    f.write(f"- **CBU Formula 1**: `{mop['ground_truth']['cbuFormula1']}`\n")
                    f.write(f"- **CBU Species Names 1**: {mop['ground_truth']['cbuSpeciesNames1']}\n")
                    f.write(f"- **Selected Name 1**: `{mop['ground_truth']['selected_name1']}`\n\n")
                    f.write(f"- **CBU Formula 2**: `{mop['ground_truth']['cbuFormula2']}`\n")
                    f.write(f"- **CBU Species Names 2**: {mop['ground_truth']['cbuSpeciesNames2']}\n")
                    f.write(f"- **Selected Name 2**: `{mop['ground_truth']['selected_name2']}`\n\n")
                    
                    f.write(f"### A-Box Input\n```turtle\n{mop['a_box_content']}\n```\n\n")
                    
                    if result['success']:
                        f.write(f"### Agent Response\n```\n{result['response']}\n```\n\n")
                    else:
                        f.write(f"### Error\n{result['error']}\n\n")
                    
                    f.write("---\n\n")
            
            print(f"Results saved to: {report_file}")
            print(f"Processed {len(results)} MOPs: {successful} successful, {failed} failed")
            print(f"\n📁 Individual results saved to playground/cbu_alignment_results/")
            print(f"   Each MOP has its own markdown file with detailed results.")
        
        else:
            print("Usage:")
            print("  python align_agent.py list                    # List all available MOPs")
            print("  python align_agent.py mop <CCDC> [DOI]        # Process specific MOP")
            print("  python align_agent.py all                     # Process all MOPs")
            print("  python align_agent.py                         # Use default A-Box")
    else:
        # Default behavior - use the original static A-Box content
        result = asyncio.run(align_agent())
        print(result)