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
    extract_ccdc_from_entity_ttl,
    safe_name as metal_safe_name,
)
from src.agents.mops.cbu_derivation.utils.cbu_general import load_res_file as util_load_res_file
from src.agents.mops.cbu_derivation.utils.markdown_utils import write_individual_md, write_summary_md, write_instruction_md
from src.agents.mops.cbu_derivation.utils.organic_utils import (
    PROMPT as ORGANIC_PROMPT,
    organic_cbu_grounding_agent as run_cbu_agent,
    extract_formula_and_classify,
)
from models.locations import DATA_DIR


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

    # Skipping: if summary exists and not test_mode, skip
    summary_path = Path(output_dir) / "summary.md"
    if summary_path.exists() and not test_mode:
        print(f"⏭️  Skipping CBU derivation (summary exists): {summary_path}")
        return True

    # Load inputs: iterate top-level entities directly from ontomops_output TTL filenames
    ttl_dir = os.path.join(DATA_DIR, hash_value, "ontomops_output")
    if not os.path.isdir(ttl_dir):
        print("⚠️  ontomops_output directory missing; nothing to process")
        return True
    entity_names: List[str] = []
    for name in sorted(os.listdir(ttl_dir)):
        if not name.startswith("ontomops_extension_") or not name.endswith(".ttl"):
            continue
        entity_names.append(name[len("ontomops_extension_"):-len(".ttl")])
    if not entity_names:
        print("⚠️  No ontomops_extension_*.ttl files found; nothing to process")
        return True

    summary: Dict[str, str] = {}
    for idx, entity_label in enumerate(entity_names, 1):
        print(f"🔬 [{idx}/{len(entity_names)}] Deriving organic CBU for entity: {entity_label}")
        try:
            # Entity-specific context
            ttl_text = ""
            extraction_text = ""
            res_text = ""
            # Load the exact ontomops_output/ontomops_extension_<entity_name>.ttl; no fallback allowed
            ttl_dir = os.path.join(DATA_DIR, hash_value, "ontomops_output")
            ttl_file = os.path.join(ttl_dir, f"ontomops_extension_{metal_safe_name(entity_label)}.ttl")
            if not os.path.exists(ttl_file):
                print(f"⚠️  Skipping entity (TTL not found): {ttl_file}")
                summary[entity_label] = None
                continue
            with open(ttl_file, "r", encoding="utf-8") as tf:
                ttl_text = tf.read()
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
            # Extract CCDC strictly from the entity TTL and load RES; no fallback allowed
            ccdc = extract_ccdc_from_entity_ttl(ttl_text) if ttl_text else ""
            if not ccdc or ccdc.strip().upper() == "N/A":
                print(f"⚠️  Skipping entity (no valid CCDC in TTL): {ttl_file}")
                summary[entity_label] = None
                continue
            res_text = util_load_res_file(ccdc)
            if not res_text or res_text.strip().startswith("RES file is not provided"):
                raise FileNotFoundError(f"RES file not found for CCDC {ccdc}")

            # Strict validation: require non-empty entity-specific paper extraction
            missing_bits = []
            if not extraction_text or not extraction_text.strip():
                missing_bits.append("paper extraction")
            if missing_bits:
                print(f"⚠️  Skipping '{entity_label}' due to missing: {', '.join(missing_bits)}")
                summary[entity_label] = None
                continue

            # Build and persist instruction
            instruction_text = ORGANIC_PROMPT_STR.format(
                paper_content=combined_paper_content,
                res_content=res_text or "",
            )
            write_instruction_md(os.path.join(output_dir, "instructions"), entity_label, instruction_text)

            # Invoke agent with full context
            response = await run_cbu_agent(
                res_content=res_text or "",
                paper_content=combined_paper_content,
                ttl_content=ttl_text or "",
            )
            write_individual_md(output_dir, entity_label, response)
            # Post-process for controllable structured output
            structured = extract_formula_and_classify(response)
            structured_dir = os.path.join(output_dir, "structured")
            os.makedirs(structured_dir, exist_ok=True)
            if structured and structured != "Ignore":
                # Write formula-only output
                safe_name = entity_label.replace(' ', '_')
                txt_path = os.path.join(structured_dir, f"{safe_name}.txt")
                with open(txt_path, "w", encoding="utf-8") as f:
                    f.write(structured)
                summary[entity_label] = structured

                # Also write per-entity JSON to support IRI selection
                json_path = os.path.join(structured_dir, f"{safe_name}.json")
                try:
                    with open(json_path, "w", encoding="utf-8") as jf:
                        json.dump({"organic_cbu": structured, "entity_label": entity_label}, jf, indent=2, ensure_ascii=False)
                except Exception:
                    pass

                # Auxiliary GPT-5 to choose ontomops:ChemicalBuildingUnit IRI
                try:
                    from models.LLMCreator import LLMCreator
                    from models.ModelConfig import ModelConfig
                    iri_prompt = (
                        "Select the best-matching ontomops:ChemicalBuildingUnit IRI.\n"
                        "Respond with ONLY the chosen IRI, nothing else.\n\n"
                        "OntoMOPs A-Box (TTL):\n" + (ttl_text or "") + "\n\n"
                        "Organic CBU JSON:\n" + json.dumps({"organic_cbu": structured, "entity_label": entity_label}, ensure_ascii=False, indent=2)
                    )
                    selector_llm = LLMCreator(model="gpt-5", remote_model=True, model_config=ModelConfig(temperature=0.0, top_p=0.02)).setup_llm()
                    iri_choice = (selector_llm.invoke(iri_prompt).content or "").strip()
                    iri_file = os.path.join(structured_dir, f"{safe_name}_iri.txt")
                    with open(iri_file, 'w', encoding='utf-8') as wf:
                        wf.write(iri_choice)
                except Exception:
                    try:
                        iri_file = os.path.join(structured_dir, f"{safe_name}_iri.txt")
                        with open(iri_file, 'w', encoding='utf-8') as wf:
                            wf.write("")
                    except Exception:
                        pass
            else:
                # Ignore metal results
                summary[entity_label] = "Ignore"
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