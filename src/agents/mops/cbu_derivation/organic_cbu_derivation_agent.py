from models.BaseAgent import BaseAgent
from models.ModelConfig import ModelConfig
from models.locations import SANDBOX_TASK_DIR, PLAYGROUND_DATA_DIR
from src.utils.global_logger import get_logger
import os
import asyncio  
import argparse
import json
import csv
from pathlib import Path
from typing import Dict, List, Set, Tuple
from datetime import datetime


def load_cbu_database(csv_path: str) -> Tuple[List[Dict[str, str]], Set[str]]:
    """
    Load CBU database from CSV file and return as list of dictionaries.
    Only includes formula, category, and smiles columns.
    Also returns a set of all formulas for quick exact matching.
    """
    cbu_data = []
    formula_set = set()
    try:
        with open(csv_path, 'r', encoding='utf-8') as file:
            reader = csv.DictReader(file)
            for row in reader:
                if row['formula']:  # Skip empty rows
                    cbu_data.append({
                        'formula': row['formula'],
                        'category': row['category'],
                        'smiles': row['canonical_smiles'] if row['canonical_smiles'] != 'N/A' else row['smiles']
                    })
                    # Add formula to set for quick lookups (case-insensitive)
                    formula_set.add(row['formula'].strip().lower())
    except Exception as e:
        print(f"Error loading CBU database: {e}")
    return cbu_data, formula_set


def scan_cbu_directories(base_path: str) -> List[Dict[str, any]]:
    """
    Scan all subdirectories under the CBU base path and identify JSON files
    without _ground_truth or _previous suffix.
    """
    base_dir = Path(base_path)
    json_files = []
    
    if not base_dir.exists():
        print(f"Base directory does not exist: {base_path}")
        return json_files
    
    for subdir in base_dir.iterdir():
        if subdir.is_dir():
            for json_file in subdir.glob("*.json"):
                # Skip files with _ground_truth or _previous suffix
                if json_file.stem.endswith('_ground_truth'):
                    json_files.append({
                        'path': json_file,
                        'subdir': subdir.name,
                        'filename': json_file.name
                    })
    
    return json_files


def extract_species_names(json_file_path: Path) -> Set[str]:
    """
    Extract and deduplicate all cbuSpeciesNames1 and cbuSpeciesNames2 from a JSON file.
    """
    species_names = set()
    
    try:
        with open(json_file_path, 'r', encoding='utf-8') as file:
            data = json.load(file)
            
        if 'synthesisProcedures' in data:
            for procedure in data['synthesisProcedures']:
                # Extract from cbuSpeciesNames1
                if 'cbuSpeciesNames1' in procedure and isinstance(procedure['cbuSpeciesNames1'], list):
                    species_names.update(procedure['cbuSpeciesNames1'])
                
                # Extract from cbuSpeciesNames2
                if 'cbuSpeciesNames2' in procedure and isinstance(procedure['cbuSpeciesNames2'], list):
                    species_names.update(procedure['cbuSpeciesNames2'])
                    
    except Exception as e:
        print(f"Error reading JSON file {json_file_path}: {e}")
    
    return species_names


def format_cbu_database_for_prompt(cbu_data: List[Dict[str, str]]) -> str:
    """
    Format CBU database for inclusion in the prompt.
    Only includes inorganic CBUs.
    """
    prompt_lines = ["**Available CBU Database (Inorganic Only):**\n"]
    prompt_lines.append("| Formula | Category | SMILES |")
    prompt_lines.append("|---------|----------|--------|")
    
    # Filter to only inorganic CBUs
    inorganic_cbus = [cbu for cbu in cbu_data if cbu['category'] == 'Inorganic']
    
    for cbu in inorganic_cbus:
        formula = cbu['formula']
        category = cbu['category']
        smiles = cbu['smiles'] if cbu['smiles'] != 'N/A' else 'Not available'
        prompt_lines.append(f"| {formula} | {category} | {smiles} |")
    
    return "\n".join(prompt_lines)


async def cbu_grounding_agent(species_name: str, cbu_database: str, paper_content: str = ""):
    """
    This agent reads species name and grounds it to the CBU database.
    """
    logger = get_logger("agent", "CBUGroundingAgent")
    logger.info(f"Starting CBU grounding agent for species: {species_name}")
    
    model_config = ModelConfig(temperature=0.2, top_p=0.02)
    mcp_tools = ["pubchem", "enhanced_websearch", "chemistry"]
    agent = BaseAgent(model_name="gpt-4.1-mini", model_config=model_config, remote_model=True, mcp_tools=mcp_tools, mcp_set_name="chemistry.json")   

    INSTRUCTION_CBU_PROMPT = """
You are provided a chemical product name. Your task is to use the MCP tools to ground this chemical to our CBU (Chemical Building Unit) database and obtain its ontology information.
Your workflow should be:

1. **Search for chemical information**:
   - Use enhanced websearch to find information about the chemical product name
   - Look for CAS number, which is usually the most reliable identifier and commonly available. If you can not find the CAS numberm, you can use other reliable unique identifiers for the task. 
   - Wrap the chemical product name in quotes for exact match searches

2. **Get canonical SMILES representation**:
   - **Critical**: This only applies to organic compounds. For inorganic compounds, you can skip this step and do Step 3 directly.
   - Use pubchem tool with CAS number to get accurate SMILES representation. If that doesn't work, the chemistry tool offers a query function to get information via CAS number as well. 
   - Only use pubchem after you have the CAS number for the chemical.
   - Get the SMILES string from pubchem
   - **Critical**: Use the chemistry tool to canonicalize the SMILES string to get the canonical SMILES representation

3. **Ground to CBU database**:
   - Compare your findings with the provided CBU database below **AND** via fuzzy_smiles_search tool
   - You need to choose the **ONE** best matching CBU for the given species name

   - **Critical**: One very important trick to find the suitable inorganic CBU is to consider the context of the paper, 
   especailly the full MOPs formula, it is likely that the full MOPs formula gives hints about what the inorganic CBU look like 
   as the inorganic CBU is usually a part of the MOPs formula.

   - **Critical**: For inorganic compounds, you will need to judge which inorganic CBU is the best matching for the given species name, the 
   inorganic CBUs are all in the CBU database below. In most of the cases, the below CBU database will not have a direct match.
   It is up to you to select with chemistry knowledge which inorganic CBU is the best matching for the given species name. In many case,
   the input species is an ingredient for the inorganic CBU after a reaction. You must choose **ONE** and **ONLY ONE** inorganic CBU for the given species name.

   - **Critical**: For organic compounds, CBUs are not directly provided in the CBU database below, so you need to match based on canonical SMILES representation using the fuzzy_smiles_search tool to find the best match. Be aware 
   that you should only input canonical SMILES converted as input. 

Here are some extra guidance for matching inorganic CBUs:

    - Elemental consistency: The metals present in the precursor must be contained in the CBU.
    - Anion retention: Counter-anions from the precursor (e.g. SO₄²⁻, NO₃⁻, Cl⁻) must also appear in the CBU.
    - Ligand context: Neutral ligands (water, alcohol, amines) can transform into hydroxo/oxo/alkoxo species in the CBU; treat them flexibly.
    - Hydration: Waters of crystallization are ignored, not required in the CBU.
    - Nuclearity preference: Choose CBUs with common nuclearities for that metal system, unless other rules exclude them.
    - Minimal extras: Prefer CBUs without additional metals or anions not indicated by the precursor.

{cbu_database}

**Important guidelines**:
- Enhanced websearch: Search first, then use docling tool only once or twice for the most relevant links (it's slow)
- Don't use docling until you're sure that's the link you want
- Don't use pubchem until you have the CAS number
- You **MUST** try both fuzzy_smiles_search tool and the CBU database to find the best matching CBU, but is it recommanded 
to use fuzzy_smiles_search tool for organic compounds and the CBU database for inorganic compounds.
 

**Output format**:
- Species Name: {species_name}
- CBU Match: [Formula from database or via fuzzy_smiles_search tool] 
- Confidence: [High/Medium/Low]
- Reasoning: [Brief explanation of the match or why not found]
- Chemical Information: [CAS, SMILES, etc. found during search]

The chemical product name is: "{species_name}"


The following is the paper content, providing all the context:

{paper_content}
"""
    
    instruction = INSTRUCTION_CBU_PROMPT.format(
        species_name=species_name,
        cbu_database=cbu_database,
        paper_content=f"**Paper Content for Context:**\n{paper_content}\n\n" if paper_content else ""
    )

    response, metadata = await agent.run(instruction)
    return response


def check_exact_match(species_name: str, formula_set: Set[str]) -> bool:
    """
    Check if the species name exactly matches any formula in the CBU database.
    Performs case-insensitive comparison.
    """
    return species_name.strip().lower() in formula_set


def create_exact_match_result(species_name: str, cbu_data: List[Dict[str, str]]) -> Dict[str, any]:
    """
    Create a result object for species that have exact matches in CBU database.
    """
    # Find the matching CBU entry
    matching_cbu = None
    for cbu in cbu_data:
        if cbu['formula'].strip().lower() == species_name.strip().lower():
            matching_cbu = cbu
            break
    
    if matching_cbu:
        response = f"""Species Name: {species_name}
CBU Match: {matching_cbu['formula']}
Confidence: High
Reasoning: Exact match found in CBU database - no grounding needed
Chemical Information: Formula: {matching_cbu['formula']}, Category: {matching_cbu['category']}, SMILES: {matching_cbu['smiles']}"""
    else:
        response = f"""Species Name: {species_name}
CBU Match: Direct match found but details unavailable
Confidence: High
Reasoning: Exact match found in CBU database - no grounding needed"""
    
    return {
        'species_name': species_name,
        'response': response,
        'success': True,
        'exact_match': True
    }


async def process_json_file(json_file_info: Dict[str, any], cbu_data: List[Dict[str, str]], formula_set: Set[str], output_dir: Path):
    """
    Process a single JSON file: extract species names, run grounding agent, and generate MD output.
    Skips processing if exact match found in CBU database.
    """
    json_path = json_file_info['path']
    subdir = json_file_info['subdir']
    filename = json_file_info['filename']
    
    print(f"\n🔬 Processing: {filename}")
    
    # Extract species names
    species_names = extract_species_names(json_path)
    if not species_names:
        print(f"  ⚠️  No species names found in {filename}")
        return
    
    print(f"  📋 Found {len(species_names)} unique species names")
    
    # Create subdirectory for this JSON file
    json_output_dir = output_dir / subdir
    json_output_dir.mkdir(parents=True, exist_ok=True)
    
    # Format CBU database for prompt
    cbu_database_text = format_cbu_database_for_prompt(cbu_data)
    
    # Process each species name
    results = []
    exact_matches = 0
    processed_via_agent = 0
    
    for i, species_name in enumerate(sorted(species_names), 1):
        print(f"  🧪 Processing species {i}/{len(species_names)}: {species_name}")
        
        # Check for exact match first
        if check_exact_match(species_name, formula_set):
            print(f"    🎯 Exact match found in CBU database - skipping agent processing")
            result = create_exact_match_result(species_name, cbu_data)
            results.append(result)
            exact_matches += 1
            
            # Generate individual species markdown file
            generate_individual_species_markdown(json_file_info, result, json_output_dir)
            print(f"    ✅ Completed (exact match): {species_name}")
            continue
        
        # Process via agent if no exact match
        try:
            response = await cbu_grounding_agent(species_name, cbu_database_text)
            result = {
                'species_name': species_name,
                'response': response,
                'success': True,
                'exact_match': False
            }
            results.append(result)
            processed_via_agent += 1
            
            # Generate individual species markdown file
            generate_individual_species_markdown(json_file_info, result, json_output_dir)
            
            print(f"    ✅ Completed (agent): {species_name}")
        except Exception as e:
            print(f"    ❌ Error processing {species_name}: {e}")
            result = {
                'species_name': species_name,
                'response': f"Error: {str(e)}",
                'success': False,
                'exact_match': False
            }
            results.append(result)
            
            # Generate individual species markdown file even for errors
            generate_individual_species_markdown(json_file_info, result, json_output_dir)
    
    # Generate cumulative markdown output
    cumulative_output_file = json_output_dir / f"{subdir}_cumulative_results.md"
    generate_cumulative_markdown_output(json_file_info, results, cumulative_output_file)
    print(f"  📝 Cumulative results saved to: {cumulative_output_file}")
    print(f"  📁 Individual species files saved in: {json_output_dir}")
    print(f"  📊 Processing summary: {exact_matches} exact matches, {processed_via_agent} processed via agent")


def generate_individual_species_markdown(json_file_info: Dict[str, any], result: Dict[str, any], output_dir: Path):
    """
    Generate markdown file for a single species grounding result.
    """
    subdir = json_file_info['subdir']
    filename = json_file_info['filename']
    species_name = result['species_name']
    
    # Create safe filename from species name
    safe_species_name = "".join(c for c in species_name if c.isalnum() or c in (' ', '-', '_')).rstrip()
    safe_species_name = safe_species_name.replace(' ', '_')
    
    content = [
        f"# CBU Grounding Result: {species_name}",
        f"",
        f"**Source File:** `{filename}`",
        f"**Directory:** `{subdir}`",
        f"**Processing Date:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"**Status:** {'✅ Success' if result['success'] else '❌ Error'}",
        f"",
        f"## Agent Response",
        f"",
        f"```",
        result['response'],
        f"```",
        f""
    ]
    
    # Create output file
    output_file = output_dir / f"{safe_species_name}.md"
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write('\n'.join(content))


def generate_cumulative_markdown_output(json_file_info: Dict[str, any], results: List[Dict[str, any]], output_path: Path):
    """
    Generate cumulative markdown output file with all grounding results.
    """
    subdir = json_file_info['subdir']
    filename = json_file_info['filename']
    
    content = [
        f"# CBU Grounding Results for {filename}",
        f"",
        f"**Source File:** `{filename}`",
        f"**Directory:** `{subdir}`",
        f"**Processing Date:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"**Total Species Processed:** {len(results)}",
        f"",
        f"## Results Summary",
        f""
    ]
    
    # Add summary table
    content.extend([
        "| Species Name | CBU Match | Status | Individual File |",
        "|--------------|-----------|--------|-----------------|"
    ])
    
    for result in results:
        species = result['species_name']
        status = "✅ Success" if result['success'] else "❌ Error"
        
        # Create safe filename for link
        safe_species_name = "".join(c for c in species if c.isalnum() or c in (' ', '-', '_')).rstrip()
        safe_species_name = safe_species_name.replace(' ', '_')
        
        # Try to extract CBU match from response
        response = result['response']
        cbu_match = "Processing..."
        if "CBU Match:" in response:
            try:
                cbu_match = response.split("CBU Match:")[1].split("\n")[0].strip()
            except:
                cbu_match = "See details"
        
        content.append(f"| {species} | {cbu_match} | {status} | [{safe_species_name}.md](./{safe_species_name}.md) |")
    
    content.extend([
        "",
        "## Detailed Results",
        ""
    ])
    
    # Add detailed results for each species
    for i, result in enumerate(results, 1):
        content.extend([
            f"### {i}. {result['species_name']}",
            "",
            "**Agent Response:**",
            "```",
            result['response'],
            "```",
            ""
        ])
    
    # Write to file
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(content))


async def test_cbu_grounding():
    """
    Test function to process converted_cbu.json with specific paper content.
    """
    print("🚀 Starting CBU Grounding Agent Test Mode")
    
    # Load paper content
    paper_file_path = os.path.join(SANDBOX_TASK_DIR, "10.1002_anie.201811027", "10.1002_anie.201811027_complete.md")
    try:
        with open(paper_file_path, 'r', encoding='utf-8') as f:
            paper_content = f.read()
        print(f"✅ Loaded paper content from: {paper_file_path}")
    except FileNotFoundError:
        print(f"❌ Paper file not found: {paper_file_path}")
        return
    except Exception as e:
        print(f"❌ Error reading paper file: {e}")
        return
    
    # Load CBU database
    cbu_csv_path = "scripts/cbu_alignment/data/full_cbus_with_canonical_smiles_updated.csv"
    print("📊 Loading CBU database...")
    cbu_data, formula_set = load_cbu_database(cbu_csv_path)
    if not cbu_data:
        print("❌ Failed to load CBU database. Exiting.")
        return
    print(f"  ✅ Loaded {len(cbu_data)} CBU entries")
    
    # Load converted_cbu.json
    converted_cbu_path = "converted_cbu.json"
    try:
        with open(converted_cbu_path, 'r', encoding='utf-8') as f:
            converted_data = json.load(f)
        print(f"✅ Loaded converted CBU data from: {converted_cbu_path}")
    except FileNotFoundError:
        print(f"❌ Converted CBU file not found: {converted_cbu_path}")
        return
    except Exception as e:
        print(f"❌ Error reading converted CBU file: {e}")
        return
    
    # Format CBU database for prompt
    cbu_database_text = format_cbu_database_for_prompt(cbu_data)
    
    # Process each synthesis procedure
    print("\n🔬 Processing synthesis procedures...")
    for i, procedure in enumerate(converted_data['synthesisProcedures'], 1):
        print(f"\n📁 Processing procedure {i}/{len(converted_data['synthesisProcedures'])}")
        print(f"  MOP CCDC Number: {procedure['mopCCDCNumber']}")
        
        # Combine cbuSpeciesNames1 and cbuSpeciesNames2
        all_species = procedure['cbuSpeciesNames1'] + procedure['cbuSpeciesNames2']
        species_string = ", ".join(all_species)
        print(f"  Species: {species_string}")
        
        # Process each species
        for j, species_name in enumerate(all_species, 1):
            print(f"\n  🧪 Processing species {j}/{len(all_species)}: {species_name}")
            
            # Check for exact match first
            if check_exact_match(species_name, formula_set):
                print(f"    🎯 Exact match found in CBU database")
                result = create_exact_match_result(species_name, cbu_data)
                print(f"    ✅ Result: {result['response']}")
                continue
            
            # Process via agent
            try:
                response = await cbu_grounding_agent(species_name, cbu_database_text, paper_content)
                print(f"    ✅ Agent Response:")
                print(f"    {response}")
                x = input("Press Enter to continue...")
            except Exception as e:
                print(f"    ❌ Error processing {species_name}: {e}")
    
    print(f"\n🎉 Test processing completed!")

async def main():
    """
    Main function to process all CBU JSON files and generate grounding results.
    """
    print("🚀 Starting CBU Grounding Agent Batch Processing")
    
    # Paths
    cbu_base_path = "playground/data/triple_compare/cbu"
    cbu_csv_path = "scripts/cbu_alignment/data/full_cbus_with_canonical_smiles_updated.csv"
    output_dir = Path("cbu_grounding")
    
    # Load CBU database
    print("📊 Loading CBU database...")
    cbu_data, formula_set = load_cbu_database(cbu_csv_path)
    if not cbu_data:
        print("❌ Failed to load CBU database. Exiting.")
        return
    print(f"  ✅ Loaded {len(cbu_data)} CBU entries")
    print(f"  📋 Created formula lookup set with {len(formula_set)} unique formulas")
    
    # Scan directories for JSON files
    print("🔍 Scanning for JSON files...")
    json_files = scan_cbu_directories(cbu_base_path)
    if not json_files:
        print("❌ No JSON files found. Exiting.")
        return
    print(f"  ✅ Found {len(json_files)} JSON files to process")
    
    # Process each JSON file
    print("\n🔬 Starting processing...")
    for i, json_file_info in enumerate(json_files, 1):
        print(f"\n📁 Processing file {i}/{len(json_files)}: {json_file_info['filename']}")
        try:
            await process_json_file(json_file_info, cbu_data, formula_set, output_dir)
        except Exception as e:
            print(f"❌ Error processing {json_file_info['filename']}: {e}")
    
    print(f"\n🎉 Batch processing completed!")
    print(f"📝 Results saved to: {output_dir}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='CBU Grounding Agent')
    parser.add_argument('--test', action='store_true', help='Run test mode with converted_cbu.json and paper content')
    args = parser.parse_args()
    
    if args.test:
        asyncio.run(test_cbu_grounding())
    else:
        asyncio.run(main())