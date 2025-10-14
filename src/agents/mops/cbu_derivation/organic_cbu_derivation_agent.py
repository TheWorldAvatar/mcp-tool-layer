from models.BaseAgent import BaseAgent
from models.ModelConfig import ModelConfig
from src.utils.global_logger import get_logger
from models.locations import DATA_DIR
import os
import asyncio
import argparse
from pathlib import Path
from typing import Dict, List, Tuple, Set
from datetime import datetime

from src.agents.mops.cbu_derivation.utils.io_utils import (
    resolve_identifier_to_hash,
    get_paths_for_hash,
)
from src.agents.mops.cbu_derivation.utils.ttl_utils import (
    load_top_entities_from_output_top,
    load_ontomops_extension_ttl,
)
from src.agents.mops.cbu_derivation.utils.markdown_utils import (
    write_individual_md,
    write_summary_md,
)
from src.agents.mops.cbu_derivation.utils.db_utils import load_cbu_database, check_exact_match


TEST_DOI = "10.1021.acs.chemmater.0c01965"


def derive_species_list_from_ttl(hash_value: str) -> List[str]:
    """Extract organic species names to derive from top entities or extension TTL.
    Currently uses output_top.ttl labels as candidates.
    """
    top_entities = load_top_entities_from_output_top(hash_value)
    # Use labels directly for now; filtering to organics can be added if needed
    species = [lbl for (lbl, _iri) in top_entities]
    return sorted(set(species))


def format_cbu_database_for_prompt(cbu_data: List[Dict[str, str]]) -> str:
    lines = ["**Available CBU Database (Organic Only via SMILES matching):**\n",
             "| Formula | Category | SMILES |",
             "|---------|----------|--------|"]
    # We keep all entries; the agent instruction will focus on organics using SMILES search
    for cbu in cbu_data:
        formula = cbu['formula']
        category = cbu['category']
        smiles = cbu['smiles'] if cbu['smiles'] != 'N/A' else 'Not available'
        lines.append(f"| {formula} | {category} | {smiles} |")
    return "\n".join(lines)


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

2. **Get canonical SMILES representation (organic only)**:
   - Use pubchem tool with CAS number to get accurate SMILES representation. If that doesn't work, the chemistry tool offers a query function to get information via CAS number as well. 
   - Only use pubchem after you have the CAS number for the chemical.
   - Get the SMILES string from pubchem
   - **Critical**: Use the chemistry tool to canonicalize the SMILES string to get the canonical SMILES representation

3. **Ground to CBU database (organic focus)**:
   - Compare your findings with the provided CBU database below AND via fuzzy_smiles_search tool
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


# Legacy function references removed in refactor; keeping signature comments for context
async def process_json_file(json_file_info: Dict[str, any], cbu_data: List[Dict[str, str]], formula_set: Set[str], output_dir: Path):
    """
    Process a single JSON file: extract species names, run grounding agent, and generate MD output.
    Skips processing if exact match found in CBU database.
    """
    json_path = json_file_info['path']
    subdir = json_file_info['subdir']
    filename = json_file_info['filename']
    
    print(f"\n🔬 Processing: {filename}")
    
    # Deprecated flow; kept for backward compatibility if needed
    species_names = set()
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


async def run_for_hash(hash_value: str, test_mode: bool = False):
    logger = get_logger("agent", "CBUDerivationOrganic")
    hash_dir, output_dir = get_paths_for_hash(hash_value)

    # Skipping: if summary exists and not test_mode, skip
    summary_path = Path(output_dir) / "summary.md"
    if summary_path.exists() and not test_mode:
        print(f"⏭️  Skipping CBU derivation (summary exists): {summary_path}")
        return True

    # Load inputs
    species_list = derive_species_list_from_ttl(hash_value)
    if not species_list:
        print("⚠️  No top-level entities found to derive organics from")
        return True

    # Load OntoMOPs extension content (context only)
    ontomops_extension_text = load_ontomops_extension_ttl(hash_value)

    # Load CBU database from DATA_DIR/ontologies
    cbu_csv_path = os.path.join(DATA_DIR, "ontologies", "full_cbus_with_canonical_smiles_updated.csv")
    cbu_data, formula_set = load_cbu_database(cbu_csv_path)
    cbu_database_text = format_cbu_database_for_prompt(cbu_data)

    summary: Dict[str, str] = {}
    for idx, species_name in enumerate(species_list, 1):
        print(f"🔬 [{idx}/{len(species_list)}] Deriving organic CBU for: {species_name}")
        try:
            # Prefer exact match where possible
            if check_exact_match(species_name, formula_set):
                match_line = f"Species Name: {species_name}\nCBU Match: {species_name}\nConfidence: High\nReasoning: Exact literal match in database"
                write_individual_md(output_dir, species_name, match_line)
                summary[species_name] = species_name
                continue

            # Run agent for derivation
            response = await cbu_grounding_agent(
                species_name=species_name,
                cbu_database=cbu_database_text,
                paper_content=ontomops_extension_text,
            )
            write_individual_md(output_dir, species_name, response)
            # Extract quick match line if present
            match = None
            if "CBU Match:" in response:
                try:
                    match = response.split("CBU Match:")[1].split("\n")[0].strip()
                except Exception:
                    match = None
            summary[species_name] = match
        except Exception as e:
            write_individual_md(output_dir, species_name, f"Error: {e}")
            summary[species_name] = None

    write_summary_md(output_dir, summary)
    print(f"✅ Organic CBU derivation completed. Outputs: {output_dir}")
    return True

async def main():
    parser = argparse.ArgumentParser(description='Organic CBU Derivation Agent')
    parser.add_argument('--test', action='store_true', help='Run test mode with hardcoded DOI')
    parser.add_argument('--file', type=str, help='Run for specific DOI (or hash)')
    args = parser.parse_args()

    if args.test:
        hash_value = resolve_identifier_to_hash(TEST_DOI)
        print(f"Running test mode with hash: {hash_value}")
        await run_for_hash(hash_value, test_mode=True)
    elif args.file:
        hash_value = resolve_identifier_to_hash(args.file)
        print(f"Running for hash: {hash_value}")
        await run_for_hash(hash_value, test_mode=False)
    else:
        # If no file provided, we consider this a no-op; integration will call per hash from pipeline
        print("No --file provided. Nothing to do. Use --file <DOI|hash> or --test.")


if __name__ == "__main__":
    asyncio.run(main())