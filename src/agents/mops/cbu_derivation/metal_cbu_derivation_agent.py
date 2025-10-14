"""
This agent derives the CBUs, organic and inorganic, from the paper content and the CBU database. 
"""


from models.ModelConfig import ModelConfig
from src.utils.global_logger import get_logger
from models.locations import DATA_CCDC_DIR, SANDBOX_TASK_DIR
import os
import asyncio
import json
from models.LLMCreator import LLMCreator
import glob
import sys
from datetime import datetime
import time
from tqdm import tqdm
import random
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
from src.agents.cbu_derivation_prompts import  INSTRUCTION_PROMPT_ENHANCED_3, INSTRUCTION_PROMPT_ENHANCED_3_WITH_CBU, CONCENTRATION_PROMPT_2
import csv
from typing import List, Dict, Set, Tuple       


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


def format_cbu_database_for_prompt(cbu_data: List[Dict[str, str]]) -> str:
    """
    Format CBU database for inclusion in the prompt.
    **Only includes INORGANIC/METAL CBUs** - organic CBUs are explicitly excluded.
    """
    prompt_lines = ["**Available Existing Metal/Inorganic CBU Database (Organic CBUs excluded):**\n"]
    prompt_lines.append("| Formula | Category | SMILES |")
    prompt_lines.append("|---------|----------|--------|")
    
    # Filter to ONLY inorganic/metal CBUs - explicitly exclude organic
    inorganic_cbus = [cbu for cbu in cbu_data if cbu['category'] == 'Inorganic']
    
    for cbu in inorganic_cbus:
        formula = cbu['formula']
        category = cbu['category']
        smiles = cbu['smiles'] if cbu['smiles'] != 'N/A' else 'Not available'
        prompt_lines.append(f"| {formula} | {category} | {smiles} |")
    
    return "\n".join(prompt_lines)


def get_hardcoded_doi_ccdc_mapping():
    """
    Hardcoded mapping of DOI to CCDC numbers for the specific paper.
    """
    # TODO: Replace with actual CCDC numbers when provided
    return {
        "10.1021.acs.chemmater.0c01965": [
            "1955203", "1955204", "1955205", "1955206", "1955207", 
            "1955208", "1955210", "1955211", "1955214", "1955216", 
            "1955217", "1955218", "1955219", "1955220", "1955417"
        ]
    }


# Ground truth functions removed - no ground truth for this paper



def _strip_cif_semicolon_delimiters(text: str) -> str:
    """
    Strip CIF semicolon delimiters if present, otherwise return the text as-is.
    This handles both extracted RES files and copied CIF files.
    """
    lines = text.splitlines()
    if lines and lines[0].strip() == ';':
        lines = lines[1:]
    if lines and lines[-1].strip() == ';':
        lines = lines[:-1]
    return "\n".join(line.rstrip() for line in lines).strip() + "\n"

def load_res_file(ccdc_number: str):
    """
    Load the RES file from the ccdc/res directory.
    """
    res_file_path = os.path.join(DATA_CCDC_DIR, "res", f"{ccdc_number}.res")
    if not os.path.exists(res_file_path):
        raise FileNotFoundError(f"RES file not found at {res_file_path}")
    with open(res_file_path, "r", encoding="utf-8") as f:
        txt = f.read()
    # return _strip_cif_semicolon_delimiters(txt)
    return txt

def load_mol2_file(ccdc_number: str):
    """
    Load the MOL2 file from the ccdc/mol2 directory.
    """
    mol2_file_path = os.path.join(DATA_CCDC_DIR, "mol2", f"{ccdc_number}.mol2")
    if not os.path.exists(mol2_file_path):
        # raise FileNotFoundError(f"MOL2 file not found at {mol2_file_path}")
        return "MOL2 file is not provided. "
    with open(mol2_file_path, "r", encoding="utf-8") as f:
        return f.read()



def load_paper_content(doi: str):
    """
    Load the paper content from the sandbox directory for the new paper.
    """
    paper_content_file_path = os.path.join(SANDBOX_TASK_DIR, doi, f"{doi}_complete.md")
    paper_content = open(paper_content_file_path, "r").read()
    return paper_content

class CBUDerivationAgent:
    def __init__(self, doi: str, concentrate: bool = False, cbu_model: str = "gpt-5-mini", use_cbu_database: bool = True):
        self.model_config = ModelConfig(temperature=0.0, top_p=0.02)
        self.logger = get_logger("agent", "CBUDerivationAgent")
        self.llm_creator_gpt_5 = LLMCreator(model=cbu_model, remote_model=True, model_config=self.model_config, structured_output=False, structured_output_schema=None)
        self.llm_creator_gpt_4_1 = LLMCreator(model="gpt-4.1", remote_model=True, model_config=self.model_config, structured_output=False, structured_output_schema=None)
        self.llm_gpt_5 = self.llm_creator_gpt_5.setup_llm() 
        self.llm_gpt_4_1 = self.llm_creator_gpt_4_1.setup_llm()

        self.raw_paper_content = load_paper_content(doi)
        self.concentrate = concentrate
        self.use_cbu_database = use_cbu_database
        
        # Load CBU database if requested
        if self.use_cbu_database:
            cbu_csv_path = "scripts/cbu_alignment/data/full_cbus_with_canonical_smiles_updated.csv"
            print(f"Loading CBU database from: {cbu_csv_path}")
            cbu_data, formula_set = load_cbu_database(cbu_csv_path)
            self.cbu_database_text = format_cbu_database_for_prompt(cbu_data)
            inorganic_count = len([c for c in cbu_data if c['category'] == 'Inorganic'])
            print(f"Loaded {len(cbu_data)} total CBU entries, providing {inorganic_count} METAL/INORGANIC CBUs only (organic excluded)")
        else:
            self.cbu_database_text = ""
        
        if self.concentrate:
            # check if the concentrated paper content file exists
            concentrated_file_path = os.path.join(SANDBOX_TASK_DIR, doi, f"{doi}_concentrated_paper_content.md")
            if os.path.exists(concentrated_file_path):
                with open(concentrated_file_path, "r") as f:
                    self.concentrated_paper_content = f.read()
            else:   
                print(f"concentrating paper content")
                TIME_START = time.time()


                CONCENTRATION_PROMPT = f"""
                Goal: Condense the PAPER TEXT to ONLY what is needed to derive metal CBUs per our definition (ASU/primitive half-cluster). Do NOT invent or re-interpret chemistry. Extract exact phrases or formulas from the paper only.

                Output: STRICT JSON (no prose, no comments). Keys and constraints:

                {{
                "metal_node_description": "≤200 chars. Paper’s wording describing the inorganic node and caps (verbatim or near-verbatim).",
                "mop_formulas_original": [
                    // Each item is an ORIGINAL MOP formula as printed in the paper, e.g. "[Zr6(...)(L)3]2+" or "[Zr12(...)(L)6]4+". Keep charge and ligand abbreviations as-is. No rewriting.
                ],
                "ligand_aliases": [
                    // Map ligand abbreviations used in formulas to their names, from the paper only.
                    {{"abbr": "bdc", "name": "terephthalate"}}
                ],
                "phase_mapping": [
                    // If the paper links ligands to cage types, capture minimal mapping exactly as printed.
                    {{"ligand": "bdc", "phase": "T/C"}},
                    {{"ligand": "tpdc", "phase": "C"}}
                ],
                "node_nuclearity_rules": [
                    // Short bullets (≤100 chars each) stating any explicit statements about nuclearity/ratios/isomerization that affect node counting.
                ],
                "naming_rules_neutral_coligands": [
                    // Only if the paper specifies naming conventions (e.g., py vs C5H5N). Else empty.
                ],
                "structure_index": [
                    // If the paper enumerates structures (labels like 1C, 3T, etc.), list minimal crossrefs.
                    {{"label": "2C", "formula": "[Zr6(...)(bdc)3]2+"}}
                ],
                "exclusions_explicit": [
                    // Verbatim exclusions that help ignore solvents/guests/disorder from the paper.
                ],
                "verbatim_citations": [
                    // Page/figure/table identifiers or figure captions that justify the above (short).
                ]
                }}

                Rules:
                - Use only PAPER TEXT below. No external knowledge. No CIF/RES parsing here.
                - Keep formulas EXACT as printed (charges, abbreviations, capitalization).
                - No stoichiometric edits. No symmetry reasoning. No normalization.
                - Prefer lists/tables over sentences. Omit empty keys rather than guessing.
                - Total output ≤ 1200 words.

                PAPER TEXT:
                {self.raw_paper_content}
                """
                self.concentrated_paper_content = self.llm_gpt_4_1.invoke(CONCENTRATION_PROMPT).content    
                TIME_END = time.time()
                print(f"Time taken to concentrate paper content: {TIME_END - TIME_START} seconds")
                with open(concentrated_file_path, "w") as f:
                    f.write(self.concentrated_paper_content)
        else:
            print(f"using raw paper content")
            self.concentrated_paper_content = self.raw_paper_content


    async def run(self, ccdc_number: str):
        """
        Given the paper content, ccdc_number, derive the CBUs from the RES file, MOL2 file, and paper content.

        ccdc_number links to both RES and MOL2 files in the ccdc directories.
        """
        res_content = load_res_file(ccdc_number)
        mol2_content = load_mol2_file(ccdc_number)
        paper_content = self.concentrated_paper_content.strip()
        
        # Choose prompt based on whether CBU database is being used
        if self.use_cbu_database:
            prompt = INSTRUCTION_PROMPT_ENHANCED_3_WITH_CBU.format(
                res_content=res_content, 
                mol2_content=mol2_content, 
                paper_content=paper_content,
                existing_cbu_database=self.cbu_database_text
            )
        else:
            prompt = INSTRUCTION_PROMPT_ENHANCED_3.format(
                res_content=res_content, 
                mol2_content=mol2_content, 
                paper_content=paper_content
            )

        return self.llm_gpt_5.invoke(prompt).content


def process_single_ccdc(doi: str, mop_ccdc_number: str, cbu_model: str, output_lock: threading.Lock, use_cbu_database: bool = True):
    """
    Process a single CCDC number for a given DOI.
    This function is designed to be run in parallel.
    """
    try:
        # Create agent for this specific DOI
        cbu_derivation_agent_concentrated = CBUDerivationAgent(doi, concentrate=True, cbu_model=cbu_model, use_cbu_database=use_cbu_database)
        
        # Print file paths for this run
        concentrated_md_path = os.path.join(SANDBOX_TASK_DIR, doi, f"{doi}_concentrated_paper_content.md")
        res_path = os.path.join(DATA_CCDC_DIR, "res", f"{mop_ccdc_number}.res")
        mol2_path = os.path.join(DATA_CCDC_DIR, "mol2", f"{mop_ccdc_number}.mol2")
  
        # Run the CBU derivation
        for i in range(1):
            START_TIME = time.time()
            response = asyncio.run(cbu_derivation_agent_concentrated.run(mop_ccdc_number))
            END_TIME = time.time()
            
            # Print everything together in one organized block (no ground truth)
            with output_lock:
                print("="*100)
                print(f"DOI: {doi} | CCDC: {mop_ccdc_number}")
                print("-"*100)
                print(f"RES file: {res_path}")
                print(f"MOL2 file: {mol2_path}")
                print(f"Concentrated paper: {concentrated_md_path}")
                print("-"*50)
                print("AGENT DERIVED METAL CBU:")
                print(response)
                print(f"Processing time: {END_TIME - START_TIME:.2f} seconds")
                print("="*100)
                print()  # Add blank line for readability
        
        return {
            'doi': doi,
            'ccdc_number': mop_ccdc_number,
            'response': response,
            'success': True
        }
        
    except Exception as e:
        with output_lock:
            print(f"Error processing DOI: {doi}, CCDC: {mop_ccdc_number}")
            print(f"Error: {str(e)}")
        return {
            'doi': doi,
            'ccdc_number': mop_ccdc_number,
            'error': str(e),
            'success': False
        }

if __name__ == "__main__":
    # Create output directory
    output_dir = "cbu_derivation_outputs"
    os.makedirs(output_dir, exist_ok=True)
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    cbu_model = "gpt-5"
    use_cbu_database = True  # Set to False to use original prompt without CBU database
    prompt_suffix = "_with_cbu" if use_cbu_database else "_enhanced_3"
    output_file = os.path.join(output_dir, f"cbu_derivation_output_cif_new_paper_{timestamp}_{cbu_model}{prompt_suffix}.txt")
    # Redirect stdout to both console and file
    class Tee:
        def __init__(self, *files):
            self.files = files
        def write(self, obj):
            for f in self.files:
                f.write(obj)
                f.flush()
        def flush(self):
            for f in self.files:
                f.flush()
    
    # Open file for writing (append mode)
    f = open(output_file, 'a')
    original_stdout = sys.stdout
    sys.stdout = Tee(sys.stdout, f)
    
    try:
        # Use hardcoded DOI-CCDC mapping
        task_json = get_hardcoded_doi_ccdc_mapping()
        
        # Prepare all tasks for parallel processing
        all_tasks = []
        for doi, ccdc_number_list in task_json.items():
            ccdc_number_list = ccdc_number_list 
            # Process ALL CCDC numbers for each DOI
            for mop_ccdc_number in ccdc_number_list:
                all_tasks.append((doi, mop_ccdc_number))
        
        print("="*100)
        print("CBU DERIVATION AGENT - CIF/RES VERSION (NEW PAPER)")
        print("RUNNING WITH CONCENTRATION - PARALLEL PROCESSING")
        print(f"CBU Database: {'ENABLED' if use_cbu_database else 'DISABLED'}")
        print(f"DOI: 10.1021.acs.chemmater.0c01965")
        print(f"Total CCDC structures: {len(all_tasks)}")
        print("="*100)
        print()
        
        # Create a lock for thread-safe output
        output_lock = threading.Lock()
        
        # Configure parallel processing
        max_workers = 8  # Adjust this based on your system and API rate limits
        print(f"Using {max_workers} parallel workers")
        
        # Process all tasks in parallel
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Submit all tasks
            future_to_task = {
                executor.submit(process_single_ccdc, doi, ccdc_number, cbu_model, output_lock, use_cbu_database): (doi, ccdc_number)
                for doi, ccdc_number in all_tasks
            }
            
            # Create progress bar
            progress_bar = tqdm(total=len(all_tasks), desc="Processing DOI-CCDC pairs", unit="pair")
            
            # Process completed tasks
            results = []
            for future in as_completed(future_to_task):
                doi, ccdc_number = future_to_task[future]
                try:
                    result = future.result()
                    results.append(result)
                    progress_bar.set_description(f"Completed: {doi[:20]}... - {ccdc_number}")
                    progress_bar.update(1)
                except Exception as e:
                    with output_lock:
                        print(f"[ERROR] Task failed for {doi} - CCDC {ccdc_number}: {str(e)}")
                    progress_bar.update(1)
            
            progress_bar.close()
        
        # Print summary
        successful_tasks = [r for r in results if r['success']]
        failed_tasks = [r for r in results if not r['success']]
        
        # Count unique DOIs processed
        unique_dois = set(r['doi'] for r in results)
        successful_dois = set(r['doi'] for r in successful_tasks)
        
        print("\n" + "="*100)
        print("PROCESSING SUMMARY")
        print("="*100)
        print(f"Total DOI-CCDC pairs: {len(results)}")
        print(f"Unique DOIs processed: {len(unique_dois)}")
        print(f"Successful pairs: {len(successful_tasks)}")
        print(f"Failed pairs: {len(failed_tasks)}")
        print(f"Success rate: {len(successful_tasks)/len(results)*100:.1f}%")
        
        if failed_tasks:
            print("\nFailed DOI-CCDC pairs:")
            for task in failed_tasks:
                print(f"  - {task['doi']} - CCDC {task['ccdc_number']}: {task['error']}")
        
        print(f"\nSuccessfully processed DOIs: {len(successful_dois)}/{len(unique_dois)}")
        print("="*100)
    
    finally:
        # Restore original stdout and close file
        sys.stdout = original_stdout
        f.close()
        print(f"\nOutput saved to: {output_file}")
    
 
     