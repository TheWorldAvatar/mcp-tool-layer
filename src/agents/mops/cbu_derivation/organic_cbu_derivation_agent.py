from src.utils.global_logger import get_logger
import os
import shutil
import asyncio  
import argparse
from pathlib import Path
from typing import Dict, List
from datetime import datetime
import json

from src.agents.mops.cbu_derivation.utils.io_utils import resolve_identifier_to_hash, get_paths_for_hash
from src.agents.mops.cbu_derivation.utils.metal_cbu import (
    load_entity_extraction_content,
    resolve_ccdc_for_derivation,
    safe_name as metal_safe_name,
)
from src.pipelines.utils.runtime_paths import (
    list_runtime_files,
    read_runtime_text,
    runtime_path_exists,
)
from src.agents.mops.cbu_derivation.utils.cbu_general import load_res_file as util_load_res_file
from src.agents.mops.cbu_derivation.utils.markdown_utils import write_individual_md, write_summary_md, write_instruction_md
from src.agents.mops.cbu_derivation.utils.organic_utils import (
    PROMPT as ORGANIC_PROMPT,
    organic_cbu_grounding_agent as run_cbu_agent,
    extract_formula_and_classify,
)
from src.agents.mops.cbu_derivation.prompts.organic_prompts import (
    organic_prompt_doi_found,
    organic_prompt_doi_not_found,
)
from models.locations import CBU_DATABASE_PATH, DATA_DIR
import csv


TEST_DOI = "10.1021.acs.chemmater.0c01965"


def derive_species_list_from_ttl(hash_value: str) -> List[str]:
    """Deprecated: no longer used (CBU iteration removed)."""
    return []

ORGANIC_PROMPT_STR = ORGANIC_PROMPT


def get_prompt() -> str:
    # Emphasize fallback when similarity to existing CBUs is low
    return ORGANIC_PROMPT_STR + "\n\nImportant: If similarity to any existing CBU is low, directly output the explicit organic CBU you derive."

async def run_for_hash(hash_value: str, test_mode: bool = False):
    logger = get_logger("agent", "CBUDerivationOrganic")
    hash_dir, cbu_root_dir = get_paths_for_hash(hash_value)
    # Align with metal agent: data/<hash>/cbu_derivation/organic
    output_dir = os.path.join(cbu_root_dir, "organic")
    os.makedirs(output_dir, exist_ok=True)

    # A previous partial run may have written summary.md after only some
    # entities succeeded. Never skip the whole paper here; per-entity
    # structured JSON is the resume signal below.

    # Load inputs: iterate top-level entities directly from ontomops_output TTL filenames
    ttl_dir = os.path.join(DATA_DIR, hash_value, "ontomops_output")
    if not os.path.isdir(ttl_dir):
        print("⚠️  ontomops_output directory missing; nothing to process")
        return True
    
    # Load mapping file to convert filenames to actual entity labels
    mapping_file = os.path.join(ttl_dir, "ontomops_output_mapping.json")
    filename_to_label = {}  # Maps filename -> actual entity label
    if runtime_path_exists(mapping_file):
        try:
            mapping = json.loads(read_runtime_text(mapping_file))
            # Reverse mapping: filename -> entity_label
            for entity_label, filename in mapping.items():
                if not entity_label.startswith("https://"):  # Skip IRI entries, keep only label entries
                    filename_to_label[filename] = entity_label
        except Exception as e:
            print(f"⚠️  Could not load mapping file: {e}")
    
    # Collect pairs of (actual_entity_label, exact_filename) to avoid naming mismatches
    entities: List[tuple[str, str]] = []
    for path in sorted(list_runtime_files(ttl_dir, suffix=".ttl")):
        name = os.path.basename(path)
        if not name.startswith("ontomops_extension_"):
            continue
        # Try to get actual entity label from mapping, fallback to filename-based label
        actual_entity_label = filename_to_label.get(name, name[len("ontomops_extension_"):-len(".ttl")])
        entities.append((actual_entity_label, name))
    if not entities:
        print("⚠️  No ontomops_extension_*.ttl files found; nothing to process")
        return True

    summary: Dict[str, str] = {}
    # Resolve canonical DOI for this hash (from data/doi_to_hash.json)
    def _canonical_doi_for_hash(hv: str) -> str:
        try:
            mapping_path = os.path.join(DATA_DIR, 'doi_to_hash.json')
            with open(mapping_path, 'r', encoding='utf-8') as mf:
                mp = json.load(mf) or {}
            # mp keys are DOI with underscores → convert to slash form for CSV matching
            for doi_us, hvv in mp.items():
                if hvv == hv:
                    return doi_us.replace('_', '/')
        except Exception:
            return ''
        return ''

    def _organic_csv_rows_for_doi(canonical_doi: str) -> str:
        if not canonical_doi:
            return "(none)"
        csv_path = CBU_DATABASE_PATH
        if not os.path.exists(csv_path):
            return "(none)"
        doi_lc = canonical_doi.strip().lower()
        lines = ["| Formula | SMILES |", "|---|---|"]
        found = 0
        try:
            with open(csv_path, "r", encoding="utf-8") as cf:
                for row in csv.DictReader(cf):
                    if (row.get("category") or "").strip() != "Organic":
                        continue
                    field = (row.get("kg_dois") or "").lower()
                    if not any(doi_lc == item.strip() for item in field.split(";")):
                        continue
                    formula = (row.get("formula") or "").strip()
                    smiles = (
                        (row.get("canonical_smiles") or "").strip()
                        or (row.get("smiles") or "").strip()
                    )
                    if not formula:
                        continue
                    lines.append(f"| {formula} | {smiles} |")
                    found += 1
        except Exception:
            return "(none)"
        return "\n".join(lines) if found else "(none for this DOI)"

    def _doi_in_cbu_csv(canonical_doi: str) -> bool:
        if not canonical_doi:
            return False
        csv_path = CBU_DATABASE_PATH
        if not os.path.exists(csv_path):
            return False
        try:
            with open(csv_path, 'r', encoding='utf-8') as cf:
                reader = csv.DictReader(cf)
                for row in reader:
                    field = (row.get('kg_dois') or '')
                    if not field:
                        continue
                    for item in field.split(';'):
                        if canonical_doi.strip().lower() == item.strip().lower():
                            return True
        except Exception:
            return False
        return False

    canonical_doi = _canonical_doi_for_hash(hash_value)
    doi_in_db = _doi_in_cbu_csv(canonical_doi)
    doi_organic_cbu_rows = _organic_csv_rows_for_doi(canonical_doi) if doi_in_db else "(none)"
    print(f"🔎 DOI lookup: canonical='{canonical_doi}' in_cbu_db={doi_in_db}")

    for idx, (entity_label, exact_ttl_name) in enumerate(entities, 1):
        print(
            f"🔬 [{idx}/{len(entities)}] Deriving organic CBU for entity: {entity_label}",
            flush=True,
        )
        try:
            structured_dir_existing = os.path.join(output_dir, "structured")
            existing_json = os.path.join(structured_dir_existing, f"{metal_safe_name(entity_label)}.json")
            if runtime_path_exists(existing_json):
                print(f"    ⏭️  Skip (structured JSON exists)")
                try:
                    summary[entity_label] = json.loads(read_runtime_text(existing_json)).get("organic_cbu")
                except Exception:
                    summary[entity_label] = "existing"
                continue
            # Entity-specific context
            ttl_text = ""
            extraction_text = ""
            res_text = ""
            # Load the exact filename discovered in the directory to avoid safe-name mismatches
            ttl_dir = os.path.join(DATA_DIR, hash_value, "ontomops_output")
            ttl_file = os.path.join(ttl_dir, exact_ttl_name)
            if not runtime_path_exists(ttl_file):
                print(f"⚠️  Skipping entity (TTL not found): {ttl_file}")
                summary[entity_label] = None
                continue
            ttl_text = read_runtime_text(ttl_file)
            try:
                extraction_text = load_entity_extraction_content(hash_value, entity_label)
            except Exception as e:
                extraction_text = ""
            # Load iter2 hints if present and append to paper content
            hints_dir = os.path.join(DATA_DIR, hash_value, "mcp_run")
            safe = metal_safe_name(entity_label)
            iter2_hint_file = os.path.join(hints_dir, f"iter2_hints_{safe}.txt")
            hints_text = ""
            if os.path.exists(iter2_hint_file):
                try:
                    with open(iter2_hint_file, "r", encoding="utf-8") as hf:
                        hints_text = hf.read()
                except Exception:
                    hints_text = ""
            combined_paper_content = extraction_text
            if hints_text:
                combined_paper_content = f"[iter2_hints_file]: {iter2_hint_file}\n\n{extraction_text}\n\n[iter2_hints]\n{hints_text}"
            ccdc = resolve_ccdc_for_derivation(entity_label, ttl_text)
            if ccdc:
                res_text = util_load_res_file(ccdc)
                if not res_text or res_text.strip().startswith("RES file is not provided"):
                    print(f"⚠️  RES missing for CCDC {ccdc}; deriving organic from paper/TTL only")
                    res_text = ""
            else:
                print(f"⚠️  No CCDC for '{entity_label}'; deriving organic from paper/TTL only")
                res_text = ""

            # Strict validation: require non-empty entity-specific paper extraction
            missing_bits = []
            if not extraction_text or not extraction_text.strip():
                missing_bits.append("paper extraction")
            if missing_bits:
                print(f"⚠️  Skipping '{entity_label}' due to missing: {', '.join(missing_bits)}")
                summary[entity_label] = None
                continue

            # Build and persist instruction
            # Build two full prompts based on DOI presence
            tmpl = organic_prompt_doi_found if doi_in_db else organic_prompt_doi_not_found
            print(
                f"🧩 Prompt selection: {'doi_found' if doi_in_db else 'doi_not_found'} for '{entity_label}'",
                flush=True,
            )
            instruction_text = tmpl.format(
                paper_content=combined_paper_content,
                res_content=res_text or "",
                canonical_doi=canonical_doi,
                doi_organic_cbu_rows=doi_organic_cbu_rows,
            )
            write_instruction_md(os.path.join(output_dir, "instructions"), entity_label, instruction_text)

            max_retries = 3
            structured = None
            response = ""
            for attempt in range(1, max_retries + 1):
                try:
                    response = await run_cbu_agent(
                        res_content=res_text or "",
                        paper_content=combined_paper_content,
                        ttl_content=ttl_text or "",
                        doi_organic_cbu_rows=doi_organic_cbu_rows,
                    )
                    write_individual_md(output_dir, entity_label, response)
                    structured = extract_formula_and_classify(response)
                    if structured and structured != "Ignore" and structured.strip():
                        print(f"✓ Successfully derived organic CBU for {entity_label} (attempt {attempt})")
                        break
                    if attempt < max_retries:
                        print(f"⚠️  Attempt {attempt}/{max_retries} returned empty/invalid result for {entity_label}, retrying...")
                    else:
                        print(f"⚠️  Failed to derive valid organic CBU after {max_retries} attempts for {entity_label}")
                except Exception as e:
                    if isinstance(e, TimeoutError):
                        print(
                            f"⚠️  Organic agent timed out for {entity_label}; "
                            "not retrying a hung run",
                            flush=True,
                        )
                        raise
                    if attempt < max_retries:
                        print(f"⚠️  Attempt {attempt}/{max_retries} failed with error: {e}, retrying...")
                    else:
                        print(f"⚠️  All {max_retries} attempts failed for {entity_label}")
                        raise
            
            structured_dir = os.path.join(output_dir, "structured")
            os.makedirs(structured_dir, exist_ok=True)
            # Final validation: ensure structured string looks like a formula block [ ... ]
            def _is_valid_formula_block(s: str) -> bool:
                if not s or not isinstance(s, str):
                    return False
                t = s.strip()
                return t.startswith("[") and ("]" in t)

            if structured and structured != "Ignore" and structured.strip() and _is_valid_formula_block(structured):
                # Write formula-only output
                from src.pipelines.utils.runtime_paths import write_runtime_text
                safe_name = metal_safe_name(entity_label)
                txt_path = os.path.join(structured_dir, f"{safe_name}.txt")
                write_runtime_text(txt_path, structured)
                summary[entity_label] = structured

                # Also write per-entity JSON; IRI selection is deferred to integration
                json_path = os.path.join(structured_dir, f"{safe_name}.json")
                try:
                    write_runtime_text(
                        json_path,
                        json.dumps({"organic_cbu": structured, "entity_label": entity_label}, indent=2, ensure_ascii=False),
                    )
                except Exception:
                    pass

            else:
                # Ignore metal results or empty responses
                summary[entity_label] = "Ignore"
                print(f"⚠️  No valid organic CBU derived for {entity_label}, marked as 'Ignore'")
        except Exception as e:
            write_individual_md(output_dir, entity_label, f"Error: {e}")
            summary[entity_label] = None
            # Keep processing other entities; only CCDC=N/A is skipped above
            continue

    write_summary_md(output_dir, summary)
    print(f"✅ Organic CBU derivation completed. Outputs: {output_dir}")
    return True


async def main():
    parser = argparse.ArgumentParser(description='Organic CBU Derivation Agent')
    parser.add_argument('--file', type=str, required=True, help='Run for specific DOI (or hash)')
    args = parser.parse_args()

    hash_value = resolve_identifier_to_hash(args.file)
    print(f"Running for hash: {hash_value}")
    await run_for_hash(hash_value, test_mode=False)


if __name__ == "__main__":
    asyncio.run(main())