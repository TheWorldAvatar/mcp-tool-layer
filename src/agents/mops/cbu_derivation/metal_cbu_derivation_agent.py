"""
This agent derives the CBUs, organic and inorganic, from the paper content and the CBU database. 
"""


from models.ModelConfig import ModelConfig
from src.utils.global_logger import get_logger
from models.locations import DATA_CCDC_DIR, DATA_DIR
import os
import asyncio
import json
from models.LLMCreator import LLMCreator
import sys
import hashlib
from datetime import datetime
import time
import shutil
from tqdm import tqdm
from typing import List, Dict, Set, Tuple       
from src.agents.mops.cbu_derivation.utils.io_utils import resolve_identifier_to_hash
from src.agents.mops.cbu_derivation.utils.metal_cbu import (
    load_top_level_entities as util_load_entities,
    load_entity_extraction_content as util_load_extraction,
    load_entity_ttl_content as util_load_ttl,
    ensure_ccdc_files as util_ensure_ccdc,
    extract_ccdc_from_entity_ttl as util_extract_ccdc,
)
from src.agents.mops.cbu_derivation.utils.cbu_general import (
    load_cbu_database as util_load_cbu_database,
    format_cbu_database_for_prompt as util_format_cbu_database_for_prompt,
    load_res_file as util_load_res_file,
    load_cif_file as util_load_cif_file,
)
from src.agents.mops.cbu_derivation.cbu_derivation_prompts import (
    INSTRUCTION_PROMPT_ENHANCED_3,
    INSTRUCTION_PROMPT_ENHANCED_3_WITH_CBU,
)


class CBUDerivationAgent:
    def __init__(self, doi: str, concentrate: bool = False, cbu_model: str = "gpt-5-mini", use_cbu_database: bool = True):
        self.model_config = ModelConfig(temperature=0.0, top_p=0.02)
        self.logger = get_logger("agent", "CBUDerivationAgent")
        self.llm_creator_gpt_5 = LLMCreator(model=cbu_model, remote_model=True, model_config=self.model_config, structured_output=False, structured_output_schema=None)
        self.llm_creator_gpt_4_1 = LLMCreator(model="gpt-4.1", remote_model=True, model_config=self.model_config, structured_output=False, structured_output_schema=None)
        self.llm_gpt_5 = self.llm_creator_gpt_5.setup_llm() 
        self.llm_gpt_4_1 = self.llm_creator_gpt_4_1.setup_llm()

        # Resolve hash for DOI or pre-hashed identifier
        self.hash_value = resolve_identifier_to_hash(doi)
        self.use_cbu_database = use_cbu_database
        
        # Load CBU database if requested
        if self.use_cbu_database:
            cbu_csv_path = os.path.join(DATA_DIR, "ontologies", "full_cbus_with_canonical_smiles_updated.csv")
            print(f"Loading CBU database from: {cbu_csv_path}")
            cbu_data, formula_set = util_load_cbu_database(cbu_csv_path)
            self.cbu_database_text = util_format_cbu_database_for_prompt(cbu_data)
            inorganic_count = len([c for c in cbu_data if c['category'] == 'Inorganic'])
            print(f"Loaded {len(cbu_data)} total CBU entries, providing {inorganic_count} METAL/INORGANIC CBUs only (organic excluded)")
        else:
            self.cbu_database_text = ""


    async def run(self, ccdc_number: str, paper_content: str, ttl_content: str, provide_cbu_db: bool = True):
        """
        Given the paper content, ccdc_number, derive the CBUs from the RES file, MOL2 file, and paper content.

        ccdc_number links to both RES and MOL2 files in the ccdc directories.
        """
        res_content = util_load_res_file(ccdc_number)
        cif_content = util_load_cif_file(ccdc_number)
        paper_content = (paper_content or "").strip()
        
        # Choose prompt based on whether CBU database is being used
        if self.use_cbu_database and provide_cbu_db:
            prompt = INSTRUCTION_PROMPT_ENHANCED_3_WITH_CBU.format(
                res_content=res_content, 
                cif_content=cif_content, 
                paper_content=paper_content,
                existing_cbu_database=self.cbu_database_text
            )
        else:
            prompt = INSTRUCTION_PROMPT_ENHANCED_3.format(
                res_content=res_content, 
                cif_content=cif_content, 
                paper_content=paper_content
            )

        # Emphasize fallback: if similarity to existing CBUs is low, output the explicit metal CBU directly
        prompt = (
            f"{prompt}\n\n"
            "Important: If the similarity between any existing CBU and the target is low, "
            "you must directly output the explicit metal CBU you derive."
        )

        # Append entity TTL content without changing the core prompt semantics
        if ttl_content:
            prompt = f"{prompt}\n\nAdditional OntoMOPs A-Box for this entity (TTL):\n```ttl\n{ttl_content}\n```"

        # Return both prompt and response for I/O writing by caller
        response = self.llm_gpt_5.invoke(prompt).content
        return prompt, response


def process_single_ccdc(doi: str, mop_ccdc_number: str, cbu_model: str, output_lock, use_cbu_database: bool = True):
    """
    Process a single CCDC number for a given DOI.
    This function is designed to be run in parallel.
    """
    try:
        # Create agent for this specific DOI
        cbu_derivation_agent_concentrated = CBUDerivationAgent(doi, concentrate=True, cbu_model=cbu_model, use_cbu_database=use_cbu_database)
        
        # Print file paths for this run
        # Per-DOI output directory under data/<hash>/metal_cbu_derivation/
        doi_hash = resolve_identifier_to_hash(doi)
        output_dir = os.path.join(DATA_DIR, doi_hash, "metal_cbu_derivation")
        os.makedirs(output_dir, exist_ok=True)
        concentrated_md_path = os.path.join(DATA_DIR, doi_hash, "metal_cbu_derivation", f"{doi_hash}_concentrated_paper_content.md")
        res_path = os.path.join(DATA_CCDC_DIR, "res", f"{mop_ccdc_number}.res")
        mol2_path = os.path.join(DATA_CCDC_DIR, "mol2", f"{mop_ccdc_number}.mol2")
  
        # Prepare output directories under data/<hash>/metal_cbu_derivation
        from src.agents.mops.cbu_derivation.utils.markdown_utils import (
            write_metal_instruction_md,
            write_metal_individual_md,
        )

        instructions_dir = os.path.join(output_dir, "instructions")

        # Run the CBU derivation
        for i in range(1):
            START_TIME = time.time()
            prompt_text, response = asyncio.run(cbu_derivation_agent_concentrated.run(mop_ccdc_number, ""))
            END_TIME = time.time()

            # Persist instruction and result files
            try:
                write_metal_instruction_md(instructions_dir, mop_ccdc_number, prompt_text)
                write_metal_individual_md(output_dir, mop_ccdc_number, response)
            except Exception as _ioe:
                # Non-fatal I/O error; continue printing to console
                pass
            
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
    import argparse

    parser = argparse.ArgumentParser(description="Metal CBU Derivation Agent (sequential)")
    parser.add_argument('--file', type=str, help='Run for specific DOI (10.1021_...) or hash (8-char)')
    parser.add_argument('--clear', action='store_true', help='Clear output folder(s) before running')
    parser.add_argument('--ablation', action='store_true', help='Do not provide CBU database in prompt and suffix outputs with _without_cbu')
    args = parser.parse_args()

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    cbu_model = "gpt-5"
    use_cbu_database = True
    prompt_suffix = "_with_cbu" if use_cbu_database else "_enhanced_3"

    # Build list of identifiers (DOIs or hashes)
    ids: List[str] = []
    if args.file:
        ids = [args.file]
    else:
        try:
            ids = [name for name in os.listdir(DATA_DIR) if os.path.isdir(os.path.join(DATA_DIR, name)) and len(name) == 8]
        except Exception:
            ids = []
    if not ids:
        print("Nothing to run. Provide --file or ensure data/<hash>/ exists.")
        sys.exit(0)

    try:
        for identifier in ids:
            hv = resolve_identifier_to_hash(identifier)
            if args.clear:
                try:
                    out_root = os.path.join(DATA_DIR, hv, "cbu_derivation", "metal")
                    if os.path.isdir(out_root):
                        shutil.rmtree(out_root, ignore_errors=True)
                        print(f"Cleared: {out_root}")
                except Exception as _e:
                    print(f"Clear failed for {hv}: {_e}")
            entities = util_load_entities(hv)
            if not entities:
                print(f"No top-level entities found for {identifier}; skipping.")
                continue
            out_dir = os.path.join(DATA_DIR, hv, "cbu_derivation", "metal")
            os.makedirs(out_dir, exist_ok=True)
            # Fast skip: if all per-entity structured JSONs already exist, skip this identifier
            try:
                from src.agents.mops.cbu_derivation.utils.metal_cbu import safe_name as _safe
                structured_dir_all = os.path.join(out_dir, "structured")
                if os.path.isdir(structured_dir_all):
                    expected_jsons = {f"{_safe(e.get('label', ''))}.json" for e in entities if e.get('label')}
                    present_jsons = {n for n in os.listdir(structured_dir_all) if n.endswith('.json')}
                    if expected_jsons and expected_jsons.issubset(present_jsons):
                        print(f"⏭️  Skipping CBU derivation for {identifier} — all structured JSONs already present")
                        continue
            except Exception:
                pass
            # Copy earlier_ground_truth CBU json for reference under data/<hash>/cbu_derivation/
            try:
                doi_basename = identifier.replace('/', '_') if ('.' in identifier or '/' in identifier) else None
                if not doi_basename:
                    # reverse lookup from data/doi_to_hash.json
                    mapping_path = os.path.join(DATA_DIR, 'doi_to_hash.json')
                    doi_basename = ''
                    try:
                        with open(mapping_path, 'r', encoding='utf-8') as mf:
                            _map = json.load(mf) or {}
                        for _doi_key, _hv in _map.items():
                            if _hv == hv:
                                doi_basename = _doi_key
                                break
                    except Exception:
                        doi_basename = ''
                if doi_basename:
                    src_json = os.path.join('earlier_ground_truth', 'cbu', f"{doi_basename}.json")
                    if os.path.exists(src_json):
                        dest_dir = os.path.join(DATA_DIR, hv, 'cbu_derivation')
                        os.makedirs(dest_dir, exist_ok=True)
                        shutil.copy2(src_json, os.path.join(dest_dir, f"{doi_basename}.json"))
            except Exception:
                pass
            print("="*100)
            print(f"Processing {identifier} ({hv}) — {len(entities)} entities")
            print("="*100)
            agent = CBUDerivationAgent(identifier, cbu_model=cbu_model, use_cbu_database=use_cbu_database)
            for i, e in enumerate(entities, 1):
                label = e.get("label", "")
                if not label:
                    continue
                try:
                    # Per-entity fast skip based on structured JSON presence
                    try:
                        from src.agents.mops.cbu_derivation.utils.metal_cbu import safe_name as _safe
                        structured_dir_entity = os.path.join(out_dir, "structured")
                        json_file = os.path.join(structured_dir_entity, f"{_safe(label)}.json")
                        if os.path.exists(json_file):
                            print(f"[{i}/{len(entities)}] {label}: Skip (structured JSON exists)")
                            continue
                    except Exception:
                        pass
                    ttl = util_load_ttl(hv, label)
                    ccdc = util_extract_ccdc(ttl)
                    if not ccdc or ccdc.strip().upper() == "N/A":
                        print(f"[{i}/{len(entities)}] {label}: Skip (no/invalid CCDC)")
                        continue
                    res_p = os.path.join(DATA_CCDC_DIR, "res", f"{ccdc}.res")
                    cif_p = os.path.join(DATA_CCDC_DIR, "cif", f"{ccdc}.cif")
                    if not (os.path.exists(res_p) and os.path.exists(cif_p)):
                        from src.mcp_servers.ccdc.operations.wsl_ccdc import get_res_cif_file_by_ccdc
                        try:
                            get_res_cif_file_by_ccdc(ccdc)
                        except Exception as _e:
                            print(f"[{label}] Failed to fetch RES/CIF for {ccdc}: {_e}")
                    paper = util_load_extraction(hv, label)
                    # up to 2 attempts if empty output
                    attempt = 0
                    prompt_text, resp = "", ""
                    while attempt < 2:
                        provide_db = (not args.ablation)
                        prompt_text, resp = asyncio.run(agent.run(ccdc, paper, ttl, provide_cbu_db=provide_db))
                        only_formula_tmp = (lambda s: __import__('re').search(r"Metal\s*CBU:\s*([^\n\r]+)", s or '', __import__('re').IGNORECASE) or __import__('re').search(r"(\[[^\n\r\]]+\])", s or '') )
                        has_content = bool((resp or '').strip())
                        if has_content:
                            break
                        attempt += 1
                    from src.agents.mops.cbu_derivation.utils.metal_cbu import safe_name as _safe
                    # write prompt record under prompts subfolder
                    prompts_dir = os.path.join(out_dir, "prompts")
                    os.makedirs(prompts_dir, exist_ok=True)
                    prompt_file = os.path.join(prompts_dir, f"{_safe(label)}_prompt{'_without_cbu' if args.ablation else ''}.md")
                    with open(prompt_file, 'w', encoding='utf-8') as pf:
                        pf.write(prompt_text)
                    # extract final-only formula and write structured/<entity>.txt
                    def _extract_cbu_formula(model_response: str) -> str:
                        import re as _re
                        txt = (model_response or "").strip()
                        m = _re.search(r"Metal\s*CBU:\s*([^\n\r]+)", txt, _re.IGNORECASE)
                        if m:
                            return m.group(1).strip()
                        m2 = _re.search(r"(\[[^\n\r\]]+\])", txt)
                        return m2.group(1).strip() if m2 else txt
                    only_formula = _extract_cbu_formula(resp)
                    structured_dir = os.path.join(out_dir, "structured")
                    os.makedirs(structured_dir, exist_ok=True)
                    final_txt = os.path.join(structured_dir, f"{_safe(label)}.txt")
                    with open(final_txt, 'w', encoding='utf-8') as ff:
                        ff.write(only_formula)

                    # Persist per-entity JSON for selector
                    try:
                        cbu_json_path = os.path.join(structured_dir, f"{_safe(label)}.json")
                        with open(cbu_json_path, 'w', encoding='utf-8') as jf:
                            json.dump({
                                "metal_cbu": only_formula,
                                "entity_label": label,
                                "ccdc": ccdc
                            }, jf, indent=2, ensure_ascii=False)
                    except Exception:
                        pass

                    # Run auxiliary GPT-5 to choose ontomops:ChemicalBuildingUnit IRI
                    try:
                        iri_prompt = (
                            "Select the best-matching ontomops:ChemicalBuildingUnit IRI.\n"
                            "Respond with ONLY the chosen IRI, nothing else.\n\n"
                            "OntoMOPs A-Box (TTL):\n" + (ttl or "") + "\n\n"
                            "Metal CBU JSON:\n" + json.dumps({
                                "metal_cbu": only_formula,
                                "entity_label": label,
                                "ccdc": ccdc
                            }, ensure_ascii=False, indent=2)
                        )
                        selector_llm = LLMCreator(model="gpt-5", remote_model=True, model_config=ModelConfig(temperature=0.0, top_p=0.02)).setup_llm()
                        iri_choice = (selector_llm.invoke(iri_prompt).content or "").strip()
                        iri_file = os.path.join(structured_dir, f"{_safe(label)}_iri.txt")
                        with open(iri_file, 'w', encoding='utf-8') as wf:
                            wf.write(iri_choice)
                    except Exception:
                        try:
                            iri_file = os.path.join(structured_dir, f"{_safe(label)}_iri.txt")
                            with open(iri_file, 'w', encoding='utf-8') as wf:
                                wf.write("")
                        except Exception:
                            pass

                    print(f"[{i}/{len(entities)}] {label}: OK → structured outputs (+ prompt)")
                except Exception as e:
                    print(f"[{i}/{len(entities)}] {label}: ERROR {e}")

    finally:
        pass
    
 
     