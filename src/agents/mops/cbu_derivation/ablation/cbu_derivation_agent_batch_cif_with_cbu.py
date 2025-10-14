"""
This agent derives the CBUs, organic and inorganic, from the paper content and the CBU database. 
"""


from models.ModelConfig import ModelConfig
from src.utils.global_logger import get_logger
from models.locations import DATA_CCDC_DIR, PLAYGROUND_DIR
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
from src.agents.cbu_derivation_prompts import INSTRUCTION_PROMPT_ENHANCED_3, INSTRUCTION_PROMPT_ENHANCED_3_WITH_CBU, CONCENTRATION_PROMPT_2
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


def iterate_over_cbu_ground_truth_json():
    """
    This function iterates over the ground truth json file and get all the doi of the json files, and retrieve all 
    the ccdc number of the json files, yielding a json file with the doi and the ccdc numbers list per json file. 
    """ 
    # get all json files with _ground_truth
    cbu_ground_truth_json_files = glob.glob(os.path.join(PLAYGROUND_DIR, "data", "triple_compare", "cbu", "*", "*ground_truth.json"))
    result_dict = {}
    for file in cbu_ground_truth_json_files:
        with open(file, "r") as f:
            ground_truth_json_content = json.load(f)
            doi = os.path.basename(file).replace("_cbu_ground_truth.json", "")
            ccdc_numbers = [entry.get("mopCCDCNumber") for entry in ground_truth_json_content.get("synthesisProcedures", [])]

            result_dict[doi] = ccdc_numbers
    return result_dict


def load_ground_truth_json(doi: str, ccdc_number: str):
    """
    Load the ground truth json file from the playground directory and extract the entry with the specified ccdc_number.
    """
    ground_truth_json_file_path = os.path.join(PLAYGROUND_DIR, "data", "triple_compare", "cbu", doi, f"{doi}_cbu_ground_truth.json")
    with open(ground_truth_json_file_path, "r") as file:
        ground_truth_json_content = json.load(file)
    
    # Extract the entry with the specified ccdc_number
    for entry in ground_truth_json_content.get("synthesisProcedures", []):
        if entry.get("mopCCDCNumber") == ccdc_number:
            return entry
    
    return None  # Return None if no matching entry is found



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
        raise FileNotFoundError(f"MOL2 file not found at {mol2_file_path}")
    with open(mol2_file_path, "r", encoding="utf-8") as f:
        return f.read()



def load_paper_content(doi: str):
    """
    Load the paper content from the playground directory.
    """
    paper_content_file_path = os.path.join(PLAYGROUND_DIR, "data", "triple_compare", "cbu", doi, f"{doi}_complete.md")
    paper_content = open(paper_content_file_path, "r").read()
    return paper_content

class CBUDerivationAgent:
    def __init__(self, doi: str, concentrate: bool = False, cbu_model: str = "gpt-5", use_cbu_database: bool = True):
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
        
        PAPER_CONCENTRATION_PROMPT = CONCENTRATION_PROMPT_2.format(paper_content=self.raw_paper_content)
        # CONCENTRATION_PROMPT = f"""
        # Goal: Condense the PAPER TEXT to ONLY what is needed to derive metal CBUs. Proritize inclusion than briefness.

        # PAPER TEXT:
        # {self.raw_paper_content}
        # """
        if self.concentrate:
            # check if the concentrated paper content file exists
            concentrated_md_path = os.path.join(PLAYGROUND_DIR, "data", "triple_compare", "cbu", f"{doi}_concentrated_paper_content.md")
            if os.path.exists(concentrated_md_path):
                with open(concentrated_md_path, "r") as f:
                    self.concentrated_paper_content = f.read()
            else:   
                print(f"concentrating paper content")
                TIME_START = time.time()
                self.concentrated_paper_content = self.llm_gpt_4_1.invoke(PAPER_CONCENTRATION_PROMPT).content    
                TIME_END = time.time()
                print(f"Time taken to concentrate paper content: {TIME_END - TIME_START} seconds")
                with open(concentrated_md_path, "w") as f:
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
        concentrated_md_path = os.path.join(PLAYGROUND_DIR, "data", "triple_compare", "cbu", f"{doi}_concentrated_paper_content.md")
        res_path = os.path.join(DATA_CCDC_DIR, "res", f"{mop_ccdc_number}.res")
        mol2_path = os.path.join(DATA_CCDC_DIR, "mol2", f"{mop_ccdc_number}.mol2")
  
        # Run the CBU derivation
        for i in range(1):
            START_TIME = time.time()
            response = asyncio.run(cbu_derivation_agent_concentrated.run(mop_ccdc_number))
            # remove all parts after "## Explanation" if it exists
            if "## Explanation" in response:
                response = response.split("## Explanation")[0].strip()
            END_TIME = time.time()
            
            # Load ground truth
            ground_truth_json_content = load_ground_truth_json(doi, mop_ccdc_number)
            
            # Print everything together in one organized block
            with output_lock:
                print("="*100)
                print(f"DOI: {doi} | CCDC: {mop_ccdc_number}")
                print("-"*100)
                print(f"RES file: {res_path}")
                print(f"MOL2 file: {mol2_path}")
                print(f"Concentrated paper: {concentrated_md_path}")
                print("-"*50)
                print("AGENT DERIVED CBUs:")
                print(response)
                print("-"*50)
                print("GROUND TRUTH CBUs:")
                if ground_truth_json_content:
                    print(f"Metal CBU:   {ground_truth_json_content['cbuFormula1']}")
                    print(f"Organic CBU: {ground_truth_json_content['cbuFormula2']}")
                else:
                    print("Ground truth not found")
                print(f"Processing time: {END_TIME - START_TIME:.2f} seconds")
                print("="*100)
                print()  # Add blank line for readability
        time.sleep(10)
        return {
            'doi': doi,
            'ccdc_number': mop_ccdc_number,
            'response': response,
            'ground_truth': ground_truth_json_content,
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
    skip_list = []  # Add DOIs to skip here
    # include_list = ["10.1002_anie.201811027"]
    # include_list = ["10.1039_C6DT02764D"]
    include_list = []
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    cbu_model = "gpt-5"
    use_cbu_database = True  # Set to False to use original prompt without CBU database
    prompt_suffix = "_with_cbu" if use_cbu_database else "_enhanced_3"
    output_file = f"cbu_derivation_output_cif_{timestamp}_{cbu_model.replace('/', '_')}{prompt_suffix}.txt"
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
        task_json = iterate_over_cbu_ground_truth_json()
        
        # Prepare all tasks for parallel processing
        all_tasks = []
        for doi, ccdc_number_list in task_json.items():
            if include_list:
                if doi not in include_list:
                    continue
            # Process ALL CCDC numbers for each DOI
            # ccdc_number_list = random.sample(ccdc_number_list, 1)
            for mop_ccdc_number in ccdc_number_list:
                all_tasks.append((doi, mop_ccdc_number))
        
        print("="*100)
        print("CBU DERIVATION AGENT - CIF/RES VERSION")
        print("RUNNING WITH CONCENTRATION - PARALLEL PROCESSING")
        print(f"CBU Database: {'ENABLED' if use_cbu_database else 'DISABLED'}")
        print(f"Total DOI-CCDC pairs: {len(all_tasks)}")
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
    
 
     