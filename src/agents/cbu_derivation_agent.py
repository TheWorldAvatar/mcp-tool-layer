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
        self.model_config = ModelConfig(temperature=0.1, top_p=0.1)
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
                print(f"using concentrated paper content file")
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
        INSTRUCTION_CBU_DERIVATION_PROMPT = f"""
        Your task: derive the Chemical Building Units (CBUs) of the given MOP and output two bracketed formulas with strict, deterministic formatting.

        OUTPUT
        - Exactly two lines: line 1 = metal CBU, line 2 = organic CBU. Nothing else.
        - Each line is one bracketed formula. No prose.

        CANONICAL SYMBOLS AND NORMALIZATION
        - Suppress all “1” coefficients anywhere (O not O1; HO not H1O1; SO4 not S1O4).
        - Suppress all zero-count terms. If H would be 0 in a fragment, omit H entirely: (C2H0) → (C2).
        - Inside fragments: element order = C then H then other heteroatoms in alphabetical order.
        - Allowed inorganic anions: SO4, PO4, VO4, PhPO3 (use only if present; do not invent VO3/VO2).
        - Allowed terminal caps: OH, OCH3. Never atomize caps to C1H3O1 or H1O1.
        - H2O is allowed only if aqua is a bound ligand; otherwise omit solvents/counterions.

        GROUP ORDER (metal line only)
        1) Metals (summed by element), e.g., V6
        2) O-only terms → O then OH (with suppressed “1”)
        3) Inorganic anions → (SO4)n, (PO4)n, (PhPO3)n, (VO4)n
        4) Neutral caps → (OCH3)n, then optional neutral coligands if explicitly present

        METAL CBU RULES
        - Merge all metal fragments into one combined metal term.
        - Retain terminal oxo/hydroxo/methoxy explicitly: O, OH, OCH3.
        - Keep SO4/PO4/PhPO3/VO4 intact as parenthesized groups with multipliers (no “1”).
        - Exclude linker carboxylates from the metal line.
        - Do not atomize canonical caps/anions.

        AROMATIC AND LINKER RULES (organic line)
        - Represent the linker as concatenated fragments: (Ck[H][hetero]…); omit H if 0. Never print H0.
        - Use multipliers for identical fragments: (C6H4)2 instead of repetition.
        - Aryl H rule: a benzene ring with two substituents (e.g., ethynyl + carboxylate) must be (C6H4), not (C6H5). One substituent → (C6H5). Zero → (C6H6).
        - Ethyne encoding: if an sp–C≡C unit links two rings, you may write either (C6H4C)2 or (C6H4)2(C2). Prefer (C6H4C)2. If you use (C2), ensure ring H obey the aryl H rule.
        - No stray tiny carbon fragments: do not emit (C2) unless the paper/mol2 explicitly indicates an alkyne/ethyn* motif. If uncertain, merge C-only fragments into the nearest aryl token.
        - Append the exact number of coordinating carboxylates at the end as (CO2)x with x ≥ 1. Do not invent extra fragments to force x.

        STRICT SELF-CHECK (regenerate until true)
        - No digit “1” anywhere; no zero-count terms.
        - Line 1 starts with a metal and contains only {{O, OH, (SO4/PO4/PhPO3/VO4), (OCH3), optional neutral coligands}} in the specified order.
        - Line 2 contains one or more fragments (Ck[H][hetero]…) followed by a final (CO2)x; no metals on line 2.
        - If line 2 contains any metal symbol and line 1 contains none, swap and re-validate.
        - If any (C2) is present, the paper/mol2 must indicate an alkyne/ethyn* or C≡C; otherwise remove (C2) and merge into aryl fragments.
        - For each aryl ring involved in two attachments, verify H=4 on that ring. Do not output (C6H5) when two attachments exist.

        FORMAT REGEX HINTS (allow bare H, forbid “1” coefficients)
        - Fragment token:
        \\(C\\d+(?:H(?:[2-9]\\d*)?)?(?:[A-Z][a-z]?(?:[2-9]\\d*)?)*\\)
        - Line 1 (informal idea):
        ^\\[[A-Z][a-z]?\\d+(?:O(?:[2-9]\\d*)?)?(?:OH(?:[2-9]\\d*)?)?(?:\\((?:SO4|PO4|PhPO3|VO4)\\)(?:[2-9]\\d*)?)*(?:\\((?:OCH3|C\\d+(?:H(?:[2-9]\\d*)?)?(?:[A-Z][a-z]?(?:[2-9]\\d*)?)*)\\)(?:[2-9]\\d*)?)*\\]$
        - Line 2 (informal idea):
        ^\\[(?:\\(C\\d+(?:H(?:[2-9]\\d*)?)?(?:[A-Z][a-z]?(?:[2-9]\\d*)?)*\\)(?:[2-9]\\d*)?)+\\(CO2\\)(?:[2-9]\\d*|[2-9])\\]$

        INPUTS AND EVIDENCE PRIORITY
        - Prefer mol2 for O/OH/OCH3/SO4/PO4/PhPO3/VO4 counts.
        - Prefer paper for linker topology and the final (CO2)x. Do not use summary organic fragments.
        - If a minimal summary JSON is provided, use it only to sanity-check metal counts and anion/cap counts; ignore any organic fragments.

        OUTPUT
        - Line 1: metal CBU
        - Line 2: organic CBU

        CBU summary (reference only; ignore organic fragments):
        {cbu_summary}

        Mol2 file:
        {mol2_content}

        Summarized paper content:
        {paper_content}
        """

 
        llm_response = self.llm_gpt_5.invoke(INSTRUCTION_CBU_DERIVATION_PROMPT).content
        return llm_response

 

if __name__ == "__main__":
    # 10.1039_C6CC04583A, CCDC: 1479717
    # 
    doi_skip_list = ["10.1002_anie.201811027"]
    doi_include_list = ["1039_C6CC04583A"]
    ccdc_include_list = ["1479717"]
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
        # Create progress bar for DOIs
        doi_progress = tqdm(task_json.items(), desc="Processing DOIs", unit="doi", position=0)
        
        for doi, ccdc_number_list in doi_progress:
            if doi in doi_skip_list or doi not in doi_include_list or ccdc_number_list[0] not in ccdc_include_list:
                continue
            doi_progress.set_description(f"Processing DOI: {doi}")
            print(f"doi: {doi}")
            print(f"ccdc_number_list: {ccdc_number_list}")
            cbu_derivation_agent_concentrated = CBUDerivationAgent(doi, concentrate=True, cbu_model=cbu_model)
              
            mop_ccdc_number_list = [ccdc_number_list[0]]
            # Run 5 iterations with concentration
            print("="*100)
            print("RUNNING WITH CONCENTRATION")
            print("="*100)

            response_dict = {}
            print(f"Doi: {doi}")
            
            # Create progress bar for CCDC numbers within each DOI
            ccdc_progress = tqdm(mop_ccdc_number_list, desc=f"Processing CCDC numbers for {doi}", unit="ccdc", position=1, leave=False)
            
            for mop_ccdc_number in ccdc_progress:
                ccdc_progress.set_description(f"Processing CCDC: {mop_ccdc_number}")
                
                # Print file paths for this run
                concentrated_md_path = os.path.join(PLAYGROUND_DIR, "data", "triple_compare", "cbu", doi, f"{doi}_concentrated_paper_content.md")
                cbu_summary_path = os.path.join(DATA_CCDC_DIR, "cbu_summaries_new", f"{mop_ccdc_number}_cbu_summary.json")
                mol2_path = os.path.join(DATA_CCDC_DIR, f"{mop_ccdc_number}.mol2")
                
                print("-"*50)
                print(f"Concentrated MD file: {concentrated_md_path}")
                print(f"CBU summary file: {cbu_summary_path}")
                print(f"Mol2 file: {mol2_path}")
                print("-"*50)
                
                for i in range(1):
                    print("-"*50)
                    print(f"MOP CCDC Number: {mop_ccdc_number}")
                    print(f"Concentrated Iteration {i+1}")
                    START_TIME = time.time()
                    response = asyncio.run(cbu_derivation_agent_concentrated.run(mop_ccdc_number))
                    print(response)
                    END_TIME = time.time()
                    print(f"Time taken: {END_TIME - START_TIME} seconds")
                    print("-"*50)
   

                ground_truth_json_content = load_ground_truth_json(doi, mop_ccdc_number)
                print(f"Ground truth metal CBU: {ground_truth_json_content['cbuFormula1']}")
                print(f"Ground truth organic CBU: {ground_truth_json_content['cbuFormula2']}")
                print("="*100)
            
            # Close the CCDC progress bar for this DOI
            ccdc_progress.close()
    
    finally:
        # Restore original stdout and close file
        sys.stdout = original_stdout
        f.close()
        print(f"\nOutput saved to: {output_file}")
    
 
     