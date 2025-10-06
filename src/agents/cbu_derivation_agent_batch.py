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

def load_cbu_summaries(ccdc_number: str):
    """
    Load the cbu summaries from the ccdc directory.
    """
    cbu_summary_file_path = os.path.join(DATA_CCDC_DIR, "cbu_summaries_new", f"{ccdc_number}_cbu_summary.json")
    with open(cbu_summary_file_path, "r") as file:
        cbu_summary_content = json.load(file)
    return cbu_summary_content


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



def load_mol2_file(ccdc_number: str):
    """
    Load the mol2 file from the ccdc directory.
    """
    mol2_file_path = os.path.join(DATA_CCDC_DIR, f"{ccdc_number}.mol2")
    mol2_content = open(mol2_file_path, "r").read()
    return mol2_content

def load_paper_content(doi: str):
    """
    Load the paper content from the playground directory.
    """
    paper_content_file_path = os.path.join(PLAYGROUND_DIR, "data", "triple_compare", "cbu", doi, f"{doi}_complete.md")
    paper_content = open(paper_content_file_path, "r").read()
    return paper_content

class CBUDerivationAgent:
    def __init__(self, doi: str, concentrate: bool = False, cbu_model: str = "gpt-5-mini"):
        self.model_config = ModelConfig(temperature=0.0, top_p=0.02)
        self.logger = get_logger("agent", "CBUDerivationAgent")
        self.llm_creator_gpt_5 = LLMCreator(model=cbu_model, remote_model=True, model_config=self.model_config, structured_output=False, structured_output_schema=None)
        self.llm_creator_gpt_4_1 = LLMCreator(model="gpt-4.1", remote_model=True, model_config=self.model_config, structured_output=False, structured_output_schema=None)
        self.llm_gpt_5 = self.llm_creator_gpt_5.setup_llm() 
        self.llm_gpt_4_1 = self.llm_creator_gpt_4_1.setup_llm()

        self.raw_paper_content = load_paper_content(doi)
        self.concentrate = concentrate
        
        if self.concentrate:
            # check if the concentrated paper content file exists
            if os.path.exists(os.path.join(PLAYGROUND_DIR, "data", "triple_compare", "cbu", f"{doi}_concentrated_paper_content.md")):
                with open(os.path.join(PLAYGROUND_DIR, "data", "triple_compare", "cbu", f"{doi}_concentrated_paper_content.md"), "r") as f:
                    self.concentrated_paper_content = f.read()
            else:   
                print(f"concentrating paper content")
                TIME_START = time.time()
                self.concentrated_paper_content = self.llm_gpt_4_1.invoke(f"""
                Concentrate the paper content to the relevent information for deriving the CBUs. Don't come up with any information that is not in the paper content, you only do concentration. Inclusion prioritizes shorter content. 

                The paper content is:
                {self.raw_paper_content}
                """).content    
                TIME_END = time.time()
                print(f"Time taken to concentrate paper content: {TIME_END - TIME_START} seconds")
                with open(os.path.join(PLAYGROUND_DIR, "data", "triple_compare", "cbu", f"{doi}_concentrated_paper_content.md"), "w") as f:
                    f.write(self.concentrated_paper_content)
        else:
            print(f"using raw paper content")
            self.concentrated_paper_content = self.raw_paper_content


    async def run(self, ccdc_number: str):
        """
        Given the paper content, ccdc_number, and chemical_input, derive the CBUs, organic and inorganic, from the paper content and the CBU database.

        ccdc_number links to the mol2 file in the ccdc directory.
        """
        mol2_content = load_mol2_file(ccdc_number)
        paper_content = self.concentrated_paper_content.strip()
        cbu_summary = load_cbu_summaries(ccdc_number)
        INSTRUCTION_METAL_CBU_PROMPT = f"""
        Your task: derive the **metal CBU** (inorganic cluster unit) of the given MOP from the mol2 file and paper content. 
        You must output only one valid formula.

        OUTPUT FORMAT
        - Output exactly one line enclosed in square brackets, e.g. [V6O6(OCH3)9(SO4)].
        - Inside the brackets: list metals, oxo groups, hydroxo/aqua groups, and allowed ligand groups only.
        - No prose, no explanation, no spaces between tokens.

        ALLOWED TOKENS
        - Metals: any element symbol (V, Zr, Fe, …) with an integer count (omit if 1).
        - O, OH, (H2O) with integer counts (omit if 1).
        - Group tokens: (SO4), (PO4), (VO4), (PhPO3), (OCH3).
        - Nothing else. Do NOT invent shorthand like “py”, “pyridine”, “Ph”.

        RULES
        1. Metal count MUST match the majority metal(s) explicitly present in the mol2 file. Never guess from paper if mol2 disagrees.
        2. Merge metals of the same element into one combined count (e.g. V7 not V6+V1).
        3. Suppress coefficient “1”; always use integer multipliers >1.
        4. Wrap all polyatomic groups in parentheses with multiplier outside (e.g. (SO4)3 not SO43).
        5. Aqua ligands are written as (H2O)n ONLY if both mol2 and paper content confirm their presence.
        6. Do not include linker fragments (organic CBUs) or neutral coligands not in the allowed set.
        7. No spaces inside the brackets; tokens must be concatenated directly.

        STRICT SELF-CHECK BEFORE OUTPUT
        - Ensure output starts with "[" and ends with "]".
        - Ensure only allowed tokens appear.
        - Ensure counts are positive integers.
        - Ensure no forbidden terms like "py", "pyridine", "Ph" appear.
        - Ensure metals and their counts match the mol2 metals.

        INPUTS
        Mol2 summary: it gives you some overall ideas about the mol2 file, with some important restrictions. 
        {cbu_summary}


        Mol2 file:
        {mol2_content}

        Paper content:
        {paper_content}


        """




 
        llm_response = self.llm_gpt_5.invoke(INSTRUCTION_METAL_CBU_PROMPT).content
        llm_response = f"Agent derived metal CBU: {llm_response.strip().replace(' ', '')}"
        return llm_response

def process_single_ccdc(doi: str, mop_ccdc_number: str, cbu_model: str, output_lock: threading.Lock):
    """
    Process a single CCDC number for a given DOI.
    This function is designed to be run in parallel.
    """
    try:
        # Create agent for this specific DOI
        cbu_derivation_agent_concentrated = CBUDerivationAgent(doi, concentrate=True, cbu_model=cbu_model)
        
        # Print file paths for this run
        concentrated_md_path = os.path.join(PLAYGROUND_DIR, "data", "triple_compare", "cbu", doi, f"{doi}_concentrated_paper_content.md")
        cbu_summary_path = os.path.join(DATA_CCDC_DIR, "cbu_summaries_new", f"{mop_ccdc_number}_cbu_summary.json")
        mol2_path = os.path.join(DATA_CCDC_DIR, f"{mop_ccdc_number}.mol2")
  
        # Run the CBU derivation
        for i in range(1):
            with output_lock:
                print("-"*50)
                print(f"DOI: {doi}, MOP CCDC Number: {mop_ccdc_number}")
                print(f"Concentrated Iteration {i+1}")
            
            START_TIME = time.time()
            response = asyncio.run(cbu_derivation_agent_concentrated.run(mop_ccdc_number))
            END_TIME = time.time()
            
            with output_lock:
                print(response)
                print(f"Time taken: {END_TIME - START_TIME} seconds")
                print("-"*50)
        
        # Load and print ground truth
        ground_truth_json_content = load_ground_truth_json(doi, mop_ccdc_number)
        with output_lock:
            print(f"DOI: {doi}, CCDC: {mop_ccdc_number}")
            print(f"Ground truth metal CBU: {ground_truth_json_content['cbuFormula1']}")
            print(f"Ground truth organic CBU: {ground_truth_json_content['cbuFormula2']}")
            print("="*100)
        
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
    doi_skip_list = [""]
    do_list = ["10.1039_C6CC04583A", "10.1021_ic402428m", "10.1021_ja042802q"]
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    cbu_model = "gpt-5"
    output_file = f"cbu_derivation_output_{timestamp}_{cbu_model}.txt"
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
            if doi in doi_skip_list:
                continue
            # Process ALL CCDC numbers for each DOI
            for mop_ccdc_number in ccdc_number_list:
                all_tasks.append((doi, mop_ccdc_number))
        
        print("="*100)
        print("RUNNING WITH CONCENTRATION - PARALLEL PROCESSING")
        print(f"Total tasks: {len(all_tasks)}")
        print("="*100)
        
        # Create a lock for thread-safe output
        output_lock = threading.Lock()
        
        # Configure parallel processing
        max_workers = 8  # Adjust this based on your system and API rate limits
        print(f"Using {max_workers} parallel workers")
        
        # Process all tasks in parallel
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Submit all tasks
            future_to_task = {
                executor.submit(process_single_ccdc, doi, ccdc_number, cbu_model, output_lock): (doi, ccdc_number)
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
                    progress_bar.set_description(f"Completed: {doi} - CCDC {ccdc_number}")
                    progress_bar.update(1)
                except Exception as e:
                    print(f"Task failed for {doi} - CCDC {ccdc_number}: {str(e)}")
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
    
 
     