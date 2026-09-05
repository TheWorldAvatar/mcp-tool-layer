"""
MOP Derivation Pipeline Step

Derives Chemical Building Units (CBUs) from CCDC files and paper content:
1. Metal CBU derivation
2. Organic CBU derivation  
3. Integration and MOP formula derivation
"""

import os
import sys
import json
import asyncio
import subprocess
import logging
import shutil
from pathlib import Path
from typing import List, Dict, Optional, Any

# Add project root to path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

# Import existing utilities and agents
from src.agents.mops.cbu_derivation.organic_cbu_derivation_agent import run_for_hash as run_organic_derivation
from src.agents.mops.cbu_derivation.metal_cbu_derivation_agent import CBUDerivationAgent
from src.agents.mops.cbu_derivation.utils.metal_cbu import (
    load_top_level_entities as util_load_entities,
    load_entity_extraction_content as util_load_extraction,
    load_entity_ttl_content as util_load_ttl,
    extract_ccdc_from_entity_ttl as util_extract_ccdc,
    safe_name as metal_safe_name,
)
from src.agents.mops.cbu_derivation.integration import integrate_hash
from src.pipelines.utils.process_watchdog import STALL_EXIT_CODE, run_subprocess_timeout
from src.agents.mops.ontomop_derivation.agent_mop_formula import run_for_hash as run_mop_formula
from models.locations import DATA_DIR, DATA_CCDC_DIR

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(message)s')


def entity_integration_usable(data: dict) -> bool:
    """True when at least one CBU side has an IRI. One empty side is not fatal."""
    metal_iri = str(((data or {}).get("metal_cbu") or {}).get("iri") or "").strip()
    organic_iri = str(((data or {}).get("organic_cbu") or {}).get("iri") or "").strip()
    return bool(metal_iri or organic_iri)
logger = logging.getLogger(__name__)


def _latest_mtime_in_dir(path: str, *, suffixes: tuple[str, ...] = ()) -> float:
    if not os.path.isdir(path):
        return 0.0
    latest = 0.0
    for name in os.listdir(path):
        full = os.path.join(path, name)
        if not os.path.isfile(full):
            continue
        if suffixes and not name.endswith(suffixes):
            continue
        try:
            latest = max(latest, os.path.getmtime(full))
        except Exception:
            continue
    return latest


def _clear_dir_if_exists(path: str) -> None:
    if not os.path.exists(path):
        return
    shutil.rmtree(path, ignore_errors=True)


def _derivation_subprocess_env(
    *,
    data_dir: str,
    project_root_path: str,
) -> Dict[str, str]:
    """Build the explicit run-data and shared-resource contract for legacy workers."""
    runtime_root = os.path.abspath(data_dir)
    repository_root = os.path.abspath(project_root_path)
    cbu_database_path = os.path.join(
        repository_root,
        "data",
        "ontologies",
        "full_cbus_with_canonical_smiles_updated.csv",
    )
    ccdc_data_dir = os.path.join(repository_root, "data", "ontologies", "ccdc")
    log_dir = os.path.join(runtime_root, "log")

    if not os.path.isdir(runtime_root):
        raise FileNotFoundError(f"Configured derivation data_dir does not exist: {runtime_root}")
    if not os.path.isfile(cbu_database_path):
        raise FileNotFoundError(f"Configured CBU database does not exist: {cbu_database_path}")
    if not os.path.isdir(ccdc_data_dir):
        raise FileNotFoundError(f"Configured CCDC data directory does not exist: {ccdc_data_dir}")
    os.makedirs(log_dir, exist_ok=True)

    child_env = os.environ.copy()
    child_env.update(
        {
            "DATA_DIR": runtime_root,
            "DATA_LOG_DIR": log_dir,
            "DATA_CCDC_DIR": ccdc_data_dir,
            "CBU_DATABASE_PATH": cbu_database_path,
            "TWA_AGENTIC_DATA_DIR": runtime_root,
        }
    )
    return child_env


def _env_timeout(name: str, default: float) -> float:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value


def _run_derivation_command(
    cmd: List[str],
    *,
    cwd: str,
    env: Dict[str, str],
    timeout_seconds: float,
    logger: logging.Logger,
    label: str,
) -> None:
    logger.info("  ⏱️  %s timeout=%.0fs", label, timeout_seconds)
    code = run_subprocess_timeout(
        cmd,
        cwd=cwd,
        env=env,
        timeout_seconds=timeout_seconds,
    )
    if code == STALL_EXIT_CODE:
        raise subprocess.CalledProcessError(
            code,
            cmd,
            f"{label} timed out after {timeout_seconds:.0f}s",
        )
    if code != 0:
        raise subprocess.CalledProcessError(code, cmd)


async def run_metal_derivation(doi_hash: str) -> bool:
    """
    Run metal CBU derivation for a given hash.
    
    Args:
        doi_hash: DOI hash for the paper
        
    Returns:
        True if derivation completed successfully
    """
    cbu_model = "gpt-5"
    use_cbu_database = True
    
    # Load top-level entities
    entities = util_load_entities(doi_hash)
    if not entities:
        logger.info(f"    ⚠️  No top-level entities found for metal CBU derivation")
        return True
    
    out_dir = os.path.join(DATA_DIR, doi_hash, "cbu_derivation", "metal")
    os.makedirs(out_dir, exist_ok=True)
    
    # Fast skip: if all per-entity structured JSONs already exist, skip
    try:
        structured_dir_all = os.path.join(out_dir, "structured")
        if os.path.isdir(structured_dir_all):
            expected_jsons = {f"{metal_safe_name(e.get('label', ''))}.json" for e in entities if e.get('label')}
            present_jsons = {n for n in os.listdir(structured_dir_all) if n.endswith('.json')}
            if expected_jsons and expected_jsons.issubset(present_jsons):
                logger.info(f"    ⏭️  All metal CBU structured JSONs already present, skipping")
                return True
    except Exception:
        pass
    
    logger.info(f"    Processing {len(entities)} entities for metal CBU derivation")
    
    # Create agent
    agent = CBUDerivationAgent(doi_hash, cbu_model=cbu_model, use_cbu_database=use_cbu_database)
    
    # Process each entity
    for i, e in enumerate(entities, 1):
        label = e.get("label", "")
        if not label:
            continue
        
        try:
            # Per-entity fast skip based on structured JSON presence
            structured_dir_entity = os.path.join(out_dir, "structured")
            json_file = os.path.join(structured_dir_entity, f"{metal_safe_name(label)}.json")
            if os.path.exists(json_file):
                logger.info(f"    [{i}/{len(entities)}] {label}: Skip (structured JSON exists)")
                continue
            
            # Load TTL and extract CCDC
            ttl = util_load_ttl(doi_hash, label)
            ccdc = util_extract_ccdc(ttl)
            if not ccdc or ccdc.strip().upper() == "N/A":
                logger.info(f"    [{i}/{len(entities)}] {label}: Skip (no/invalid CCDC)")
                continue
            
            # Ensure CCDC files exist
            res_p = os.path.join(DATA_CCDC_DIR, "res", f"{ccdc}.res")
            cif_p = os.path.join(DATA_CCDC_DIR, "cif", f"{ccdc}.cif")
            if not (os.path.exists(res_p) and os.path.exists(cif_p)):
                from src.mcp_servers.ccdc.operations.wsl_ccdc import get_res_cif_file_by_ccdc
                try:
                    get_res_cif_file_by_ccdc(ccdc)
                except Exception as _e:
                    logger.warning(f"    [{label}] Failed to fetch RES/CIF for {ccdc}: {_e}")
            
            # Load paper content
            paper = util_load_extraction(doi_hash, label)
            
            # Retry mechanism: up to 3 attempts for valid metal CBU formula
            max_retries = 3
            resp = ""
            prompt_text = ""
            only_formula = ""
            
            for attempt in range(1, max_retries + 1):
                prompt_text, resp = await agent.run(ccdc, paper, ttl, provide_cbu_db=True)
                has_content = bool((resp or '').strip())
                
                if has_content:
                    # Extract formula to validate it
                    import re as _re
                    txt = (resp or "").strip()
                    # Prefer bracketed formula
                    m2 = _re.search(r"(\[[^\n\r\]]+\])", txt)
                    if m2:
                        only_formula = m2.group(1).strip()
                    else:
                        m = _re.search(r"Metal\s*CBU:\s*([^\n\r]+)", txt, _re.IGNORECASE)
                        if m:
                            only_formula = m.group(1).strip()
                        else:
                            only_formula = txt
                    
                    # Check if we got a valid bracketed formula
                    if only_formula and '[' in only_formula and ']' in only_formula:
                        logger.info(f"    ✓ Successfully derived metal CBU for {label} (attempt {attempt})")
                        break
                    elif attempt < max_retries:
                        logger.warning(f"    ⚠️  Attempt {attempt}/{max_retries} returned invalid formula for {label}, retrying...")
                    else:
                        logger.warning(f"    ⚠️  Failed to derive valid metal CBU after {max_retries} attempts for {label}")
                elif attempt < max_retries:
                    logger.warning(f"    ⚠️  Attempt {attempt}/{max_retries} returned empty response for {label}, retrying...")
                else:
                    logger.warning(f"    ⚠️  All {max_retries} attempts returned empty for {label}")

            # Write prompt record under prompts subfolder
            from src.pipelines.utils.runtime_paths import write_runtime_text
            prompts_dir = os.path.join(out_dir, "prompts")
            os.makedirs(prompts_dir, exist_ok=True)
            prompt_file = os.path.join(prompts_dir, f"{metal_safe_name(label)}_prompt.md")
            write_runtime_text(prompt_file, prompt_text)

            # Enforce bracketed-only formula (strip any commentary after the bracket)
            try:
                import re as _re
                mm = _re.search(r"(\[[^\]]+\])", only_formula)
                if mm:
                    only_formula = mm.group(1).strip()
                else:
                    only_formula = only_formula.strip()
            except Exception:
                only_formula = (only_formula or "").strip()

            # Validate final formula
            def _is_valid_formula_block(s: str) -> bool:
                if not s or not isinstance(s, str):
                    return False
                t = s.strip()
                return t.startswith("[") and ("]" in t)

            structured_dir = os.path.join(out_dir, "structured")
            os.makedirs(structured_dir, exist_ok=True)
            final_txt = os.path.join(structured_dir, f"{metal_safe_name(label)}.txt")

            if _is_valid_formula_block(only_formula):
                write_runtime_text(final_txt, only_formula)
                logger.info(f"    [{i}/{len(entities)}] {label}: OK → structured outputs (+ prompt)")
            else:
                error_msg = f"❌ CRITICAL: Failed to derive valid metal CBU formula for {label} after {max_retries} attempts. " \
                           f"Final result: '{only_formula}'. This indicates LLM failure in CBU derivation."
                logger.error(error_msg)
                raise RuntimeError(error_msg)

            # Persist per-entity JSON for selector
            try:
                cbu_json_path = os.path.join(structured_dir, f"{metal_safe_name(label)}.json")
                write_runtime_text(
                    cbu_json_path,
                    json.dumps({
                        "metal_cbu": only_formula,
                        "entity_label": label,
                        "ccdc": ccdc
                    }, indent=2, ensure_ascii=False),
                )
            except Exception as e:
                logger.warning(f"    Failed to write JSON for {label}: {e}")
        except Exception as e:
            logger.error(f"    [{i}/{len(entities)}] {label}: ERROR {e}")
    
    return True


def perform_final_integration(
    hash_value: str,
    *,
    data_dir: str,
) -> Optional[List[Dict[str, Any]]]:
    """Perform final integration by applying derived MOP formulas to create final TTL files."""
    from src.agents.mops.cbu_derivation.integration import _write_integrated_ttl

    from src.pipelines.utils.top_entity_identity import entity_artifact_name

    def _safe_name(name: str) -> str:
        return entity_artifact_name(name)

    logger.info(f"[FINAL-INTEGRATION] Starting final integration for {hash_value}")

    case_dir = os.path.join(data_dir, hash_value)
    full_dir = os.path.join(case_dir, "cbu_derivation", "full")
    integrated_dir = os.path.join(case_dir, "cbu_derivation", "integrated")

    logger.info(f"[FINAL-INTEGRATION] Checking directories: full={os.path.exists(full_dir)}, integrated={os.path.exists(integrated_dir)}")

    if not os.path.exists(full_dir) or not os.path.exists(integrated_dir):
        logger.error(f"Required directories missing: full={os.path.exists(full_dir)}, integrated={os.path.exists(integrated_dir)}")
        return None

    results = []
    logger.info(f"[FINAL-INTEGRATION] Processing files in {full_dir}")
    for full_file in os.listdir(full_dir):
        if not full_file.endswith('.json'):
            continue

        entity_name = full_file[:-5]  # Remove .json
        logger.info(f"[FINAL-INTEGRATION] Processing entity: {entity_name}")
        full_path = os.path.join(full_dir, full_file)

        # The integration stage and this stage share one canonical filename
        # transform; do not discover files by partial-name matching.
        safe_entity_name = _safe_name(entity_name)
        integrated_path = os.path.join(integrated_dir, f"{safe_entity_name}.json")
        if not os.path.isfile(integrated_path):
            logger.warning(f"Integrated file missing for {entity_name}, skipping")
            continue

        try:
            # Read full JSON (with derived MOP formula)
            with open(full_path, 'r', encoding='utf-8') as f:
                full_data = json.load(f)

            # Read integrated JSON (with IRI mappings)
            with open(integrated_path, 'r', encoding='utf-8') as f:
                integrated_data = json.load(f)

            entity_label = full_data.get('entity', entity_name)
            derived_mop_formula = full_data.get('mop_formula', '')

            if not derived_mop_formula:
                logger.warning(f"No derived MOP formula for {entity_name}, skipping TTL generation")
                continue

            # Prepare data for TTL generation
            metal_cbu = integrated_data.get('metal_cbu', {})
            organic_cbu = integrated_data.get('organic_cbu', {})

            # Generate final TTL with derived formula
            # We need to get the TTL filename from the mapping
            ttl_dir = os.path.join(case_dir, "ontomops_output")
            mapping_file = os.path.join(ttl_dir, "ontomops_output_mapping.json")

            if not os.path.isfile(mapping_file):
                logger.warning(f"OntoMOPs output mapping missing for {entity_label}, skipping TTL generation")
                continue
            with open(mapping_file, 'r', encoding='utf-8') as mf:
                mapping = json.load(mf)
            ttl_filename = mapping.get(entity_label)
            if not ttl_filename:
                logger.warning(f"No TTL filename mapping found for {entity_label}, skipping TTL generation")
                continue

            try:
                _write_integrated_ttl(
                    hash_value=hash_value,
                    entity_label=entity_label,
                    ttl_filename=ttl_filename,
                    metal_cbu=metal_cbu,
                    organic_cbu=organic_cbu,
                    out_dir=integrated_dir,
                    data_dir=data_dir,
                    mop_formula_override=derived_mop_formula
                )
                final_ttl_path = os.path.join(integrated_dir, f"{_safe_name(entity_label)}.ttl")
                logger.info(f"Successfully generated final TTL for {entity_name} at {final_ttl_path}")
            except Exception as e:
                logger.error(f"Failed to generate TTL for {entity_name}: {e}")
                continue

            results.append({
                'entity': entity_label,
                'mop_formula': derived_mop_formula,
                'ttl_path': final_ttl_path
            })

            logger.info(f"Generated final TTL for {entity_name} with formula: {derived_mop_formula}")

        except Exception as e:
            logger.error(f"Failed to process final integration for {entity_name}: {e}")
            continue

    return results if results else None


def run_step(doi_hash: str, config: dict) -> bool:
    """
    Main MOP Derivation step: Derive metal and organic CBUs, then integrate.
    
    Args:
        doi_hash: DOI hash for the paper
        config: Pipeline configuration dictionary
        
    Returns:
        True if derivation completed successfully
    """
    # Extract config parameters
    data_dir = config.get("data_dir", "data")
    project_root_path = config.get("project_root") or project_root
    
    logger.info(f"🧪 Starting MOP derivation for DOI: {doi_hash}")
    
    doi_folder = os.path.join(data_dir, doi_hash)
    if not os.path.exists(doi_folder):
        logger.error(f"DOI folder not found: {doi_folder}")
        return False
    
    # Check if ontomops_output exists (prerequisite)
    ontomops_dir = os.path.join(doi_folder, "ontomops_output")
    if not os.path.isdir(ontomops_dir):
        logger.warning(f"  ⚠️  ontomops_output directory not found, skipping MOP derivation")
        return True
    
    # Check if there are any ontomops extension files
    ttl_files = [f for f in os.listdir(ontomops_dir) if f.startswith("ontomops_extension_") and f.endswith(".ttl")]
    if not ttl_files:
        logger.warning(f"  ⚠️  No ontomops extension files found, skipping MOP derivation")
        return True

    source_mtime = _latest_mtime_in_dir(ontomops_dir, suffixes=(".ttl", ".json"))

    # Check if step is already completed
    marker_file = os.path.join(doi_folder, ".mop_derivation_done")
    if os.path.exists(marker_file):
        try:
            marker_mtime = os.path.getmtime(marker_file)
        except Exception:
            marker_mtime = 0.0
        if marker_mtime >= source_mtime:
            logger.info(f"  ⏭️  MOP derivation already completed (marker exists)")
            return True
        logger.warning("  🔁 MOP derivation marker is older than ontomops outputs; re-running downstream derivation")

    # Check if CBU derivation results already exist (optional optimization)
    cbu_derivation_dir = os.path.join(doi_folder, "cbu_derivation")
    metal_structured_dir = os.path.join(cbu_derivation_dir, "metal", "structured")
    organic_structured_dir = os.path.join(cbu_derivation_dir, "organic", "structured")

    def _structured_stems(path: str) -> set[str]:
        if not os.path.isdir(path):
            return set()
        return {name[:-5] for name in os.listdir(path) if name.endswith(".json")}

    metal_stems = _structured_stems(metal_structured_dir)
    organic_stems = _structured_stems(organic_structured_dir)
    metal_results_exist = bool(metal_stems)
    organic_results_exist = bool(organic_stems)
    metal_results_current = _latest_mtime_in_dir(metal_structured_dir, suffixes=(".json", ".txt", ".md")) >= source_mtime
    organic_results_current = _latest_mtime_in_dir(organic_structured_dir, suffixes=(".json", ".txt", ".md")) >= source_mtime
    if metal_results_exist and not metal_results_current:
        logger.warning("  🔁 Metal CBU results are stale; clearing structured outputs before re-derivation")
        _clear_dir_if_exists(metal_structured_dir)
        metal_stems = set()
        metal_results_exist = False
        metal_results_current = False
    if organic_results_exist and not organic_results_current:
        logger.warning("  🔁 Organic CBU results are stale; clearing structured outputs before re-derivation")
        _clear_dir_if_exists(organic_structured_dir)
        organic_stems = set()
        organic_results_exist = False
        organic_results_current = False

    skip_metal = metal_results_exist and metal_results_current
    # Do not require metal stems first. An empty metal run used to force a
    # full organic rerun even when OntoMOPs-aligned organic JSONs already exist.
    skip_organic = organic_results_exist and organic_results_current
    if metal_stems and not metal_stems.issubset(organic_stems):
        skip_organic = False
    if len(organic_stems) < len(ttl_files):
        skip_organic = False
    skip_derivation = skip_metal and skip_organic
    if skip_derivation:
        logger.info(f"  ⏭️  CBU derivation results already exist, skipping derivation steps")
    elif skip_metal and not skip_organic:
        logger.info(
            f"  📝 Metal CBU already complete ({len(metal_stems)} entities); "
            f"organic missing {sorted(metal_stems - organic_stems)}"
        )
    elif skip_organic and not skip_metal:
        logger.info(
            f"  📝 Organic CBU already complete ({len(organic_stems)} entities); "
            "will derive metal only"
        )
    else:
        logger.info(f"  📝 CBU derivation results not found, will run derivation")
    
    logger.info(f"  Found {len(ttl_files)} ontomops extension files")

    try:
        subprocess_env = _derivation_subprocess_env(
            data_dir=data_dir,
            project_root_path=project_root_path,
        )
        if skip_metal:
            logger.info(f"  ⏭️  Skipping metal CBU derivation (structured outputs already exist)")
        else:
            logger.info(f"\n  📍 Step 1: Metal CBU Derivation (legacy orchestrator)")
            try:
                cmd = [sys.executable, "-m", "src.agents.mops.cbu_derivation.metal_cbu_derivation_agent", "--file", doi_hash]
                _run_derivation_command(
                    cmd,
                    cwd=project_root_path,
                    env=subprocess_env,
                    timeout_seconds=_env_timeout("CBU_METAL_TIMEOUT_SECONDS", 1500),
                    logger=logger,
                    label="metal CBU derivation",
                )
                logger.info(f"  ✅ Metal CBU derivation completed")
            except subprocess.CalledProcessError as e:
                logger.error(f"  ❌ Metal CBU derivation failed: {e}")
                return False

        if skip_organic:
            logger.info(f"  ⏭️  Skipping organic CBU derivation (structured outputs already exist)")
        else:
            logger.info(f"\n  📍 Step 2: Organic CBU Derivation (legacy orchestrator)")
            try:
                cmd = [sys.executable, "-m", "src.agents.mops.cbu_derivation.organic_cbu_derivation_agent", "--file", doi_hash]
                _run_derivation_command(
                    cmd,
                    cwd=project_root_path,
                    env=subprocess_env,
                    timeout_seconds=_env_timeout("CBU_ORGANIC_TIMEOUT_SECONDS", 1500),
                    logger=logger,
                    label="organic CBU derivation",
                )
                logger.info(f"  ✅ Organic CBU derivation completed")
            except subprocess.CalledProcessError as e:
                logger.error(f"  ❌ Organic CBU derivation failed: {e}")
                return False

        # Step 3: Initial Integration (use previous integration module)
        integrated_dir = os.path.join(data_dir, doi_hash, "cbu_derivation", "integrated")
        integration_results_exist = os.path.exists(integrated_dir) and any(f.endswith('.json') for f in os.listdir(integrated_dir))
        integration_baseline = max(
            source_mtime,
            _latest_mtime_in_dir(metal_structured_dir, suffixes=(".json", ".txt", ".md")),
            _latest_mtime_in_dir(organic_structured_dir, suffixes=(".json", ".txt", ".md")),
        )
        integration_results_current = _latest_mtime_in_dir(integrated_dir, suffixes=(".json", ".ttl")) >= integration_baseline

        if integration_results_exist and integration_results_current:
            logger.info(f"  ⏭️  Integration results already exist, skipping integration step")
        else:
            if integration_results_exist and not integration_results_current:
                logger.warning("  🔁 Integration results are stale; clearing outputs before re-integration")
                _clear_dir_if_exists(integrated_dir)
            logger.info(f"\n  📍 Step 3: Integration (legacy orchestrator)")
            try:
                cmd = [sys.executable, "-m", "src.agents.mops.cbu_derivation.integration", "--file", doi_hash]
                _run_derivation_command(
                    cmd,
                    cwd=project_root_path,
                    env=subprocess_env,
                    timeout_seconds=_env_timeout("CBU_INTEGRATION_TIMEOUT_SECONDS", 900),
                    logger=logger,
                    label="CBU integration",
                )

                # Validate that at least one entity produced a usable IRI.
                # One empty metal/organic IRI must not fail the whole paper.
                integrated_dir = os.path.join(data_dir, doi_hash, "cbu_derivation", "integrated")
                usable = 0
                if os.path.exists(integrated_dir):
                    for json_file in os.listdir(integrated_dir):
                        if not json_file.endswith('.json'):
                            continue
                        json_path = os.path.join(integrated_dir, json_file)
                        with open(json_path, 'r', encoding='utf-8') as f:
                            data = json.load(f)
                        if entity_integration_usable(data):
                            usable += 1
                            continue
                        metal_iri = (data.get('metal_cbu') or {}).get('iri', '')
                        organic_iri = (data.get('organic_cbu') or {}).get('iri', '')
                        logger.warning(
                            "  ⚠️  Integration left empty IRIs in %s "
                            "(metal='%s', organic='%s'); keeping other entities",
                            json_file,
                            metal_iri,
                            organic_iri,
                        )
                if usable == 0:
                    raise RuntimeError(
                        "❌ Integration validation failed: no entity produced a usable metal or organic IRI."
                    )

                logger.info(f"  ✅ Integration completed and validated ({usable} usable entit{'y' if usable == 1 else 'ies'})")
            except subprocess.CalledProcessError as e:
                logger.error(f"  ❌ Integration failed: {e}")
                return False
            except RuntimeError as e:
                logger.error(f"  ❌ Integration validation failed: {e}")
                return False

        # Step 4: MOP Formula Derivation (use previous standalone module)
        mop_formula_dir = os.path.join(data_dir, doi_hash, "cbu_derivation", "full")
        mop_formula_results_exist = os.path.exists(mop_formula_dir) and any(f.endswith('.md') for f in os.listdir(mop_formula_dir))
        mop_formula_baseline = max(
            integration_baseline,
            _latest_mtime_in_dir(integrated_dir, suffixes=(".json", ".ttl")),
        )
        mop_formula_results_current = _latest_mtime_in_dir(mop_formula_dir, suffixes=(".json", ".md", ".ttl")) >= mop_formula_baseline

        if mop_formula_results_exist and mop_formula_results_current:
            logger.info(f"  ⏭️  MOP formula results already exist, skipping MOP formula derivation")
        else:
            if mop_formula_results_exist and not mop_formula_results_current:
                logger.warning("  🔁 MOP formula results are stale; clearing outputs before re-derivation")
                _clear_dir_if_exists(mop_formula_dir)
            logger.info(f"\n  📍 Step 4: MOP Formula Derivation (legacy orchestrator)")
            try:
                cmd = [sys.executable, "-m", "src.agents.mops.ontomop_derivation.agent_mop_formula", "--file", doi_hash]
                _run_derivation_command(
                    cmd,
                    cwd=project_root_path,
                    env=subprocess_env,
                    timeout_seconds=_env_timeout("CBU_FORMULA_TIMEOUT_SECONDS", 900),
                    logger=logger,
                    label="MOP formula derivation",
                )
                logger.info(f"  ✅ MOP formula derivation completed")
            except subprocess.CalledProcessError as e:
                logger.error(f"  ❌ MOP formula derivation failed: {e}")
                return False

        # Step 5: Final Integration (apply derived MOP formula) using in-process helper for speed
        logger.info(f"\n  📍 Step 5: Final Integration (apply derived MOP formula)")
        final_integration_result = perform_final_integration(
            doi_hash,
            data_dir=data_dir,
        )
        if final_integration_result is None:
            logger.error(f"  ❌ Final integration failed")
            return False

        logger.info(f"  ✅ Final integration completed and validated ({len(final_integration_result)} entities integrated)")
        
    except Exception as e:
        logger.error(f"  ❌ MOP derivation failed: {e}")
        return False
    
    # Create completion marker
    try:
        with open(marker_file, 'w') as f:
            f.write("completed\n")
        logger.info(f"  📌 Created completion marker")
    except Exception as e:
        logger.warning(f"  ⚠️  Failed to create completion marker: {e}")
    
    logger.info(f"✅ MOP derivation completed for DOI: {doi_hash}")
    return True


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python -m src.pipelines.mop_derivation.derive <doi_hash>")
        sys.exit(1)
    
    # Create config dict for standalone usage
    config = {
        "data_dir": "data",
        "project_root": "."
    }
    
    success = run_step(sys.argv[1], config)
    sys.exit(0 if success else 1)

