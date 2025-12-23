"""
Pipeline utilities for MOPs Extraction

This module contains the core pipeline functions for processing DOI files
through the complete MOPs extraction workflow.
"""

import os
import sys
import json
import shutil
import asyncio
import subprocess
import hashlib
import glob
import time
from scripts.pdf_to_markdown import convert_doi_pdfs, convert_all_dois
from src.utils.division_wrapper import run_division_and_classify, run_division_and_classify_all
from models.locations import RAW_DATA_DIR
from src.agents.mops.dynamic_mcp.mcp_run_agent_hint_only_dynamic import run_task, run_task_iter1_only, run_task_hints_only

# -------------------- Global content-based cache helpers --------------------
def _hash_of_files(paths: list[str]) -> str:
    h = hashlib.sha256()
    for p in sorted(set(paths)):
        if os.path.exists(p) and os.path.isfile(p):
            try:
                with open(p, 'rb') as f:
                    while True:
                        chunk = f.read(8192)
                        if not chunk:
                            break
                        h.update(chunk)
            except Exception:
                continue
    return h.hexdigest()

def _extraction_cache_paths(doi_hash: str, sig: str) -> tuple[str, list[tuple[str, str]]]:
    base = os.path.join('data', 'cache', 'extraction', sig)
    targets = [
        (os.path.join(base, 'output.ttl'), 'output.ttl'),
        (os.path.join(base, 'output_top.ttl'), 'output_top.ttl'),
        (os.path.join(base, 'iteration_1.ttl'), 'iteration_1.ttl'),
        (os.path.join(base, 'mcp_run', 'iter1_top_entities.json'), os.path.join('mcp_run', 'iter1_top_entities.json')),
    ]
    return base, targets

def _try_restore_extraction_cache(doi_hash: str, cache_enabled: bool) -> bool:
    if not cache_enabled:
        return False
    ddir = os.path.join('data', doi_hash)
    sig = _hash_of_files([
        os.path.join(ddir, f"{doi_hash}_stitched.md"),
        os.path.join(ddir, "ontosynthesis.ttl"),
    ])
    base, targets = _extraction_cache_paths(doi_hash, sig)
    if not os.path.isdir(base):
        return False
    restored_any = False
    for src_rel, dst_rel in targets:
        src = src_rel
        dst = os.path.join(ddir, dst_rel)
        if os.path.exists(src):
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            try:
                shutil.copy2(src, dst)
                restored_any = True
            except Exception:
                pass
    return restored_any and os.path.exists(os.path.join(ddir, 'output.ttl'))

def _save_extraction_cache(doi_hash: str, cache_enabled: bool) -> None:
    if not cache_enabled:
        return
    ddir = os.path.join('data', doi_hash)
    sig = _hash_of_files([
        os.path.join(ddir, f"{doi_hash}_stitched.md"),
        os.path.join(ddir, "ontosynthesis.ttl"),
    ])
    base, targets = _extraction_cache_paths(doi_hash, sig)
    try:
        for src_rel, dst_rel in targets:
            src = os.path.join(ddir, dst_rel)
            dst = src_rel
            if os.path.exists(src):
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy2(src, dst)
    except Exception:
        pass

def _organic_cache_key(doi_hash: str) -> str:
    ddir = os.path.join('data', doi_hash)
    ttl_files = sorted(glob.glob(os.path.join(ddir, 'ontomops_output', '*.ttl')))
    hint_files = sorted(glob.glob(os.path.join(ddir, 'mcp_run', 'iter2_hints_*.txt')))
    return _hash_of_files(ttl_files + hint_files)

def _try_restore_organic_cache(doi_hash: str, cache_enabled: bool) -> bool:
    if not cache_enabled:
        return False
    sig = _organic_cache_key(doi_hash)
    cache_dir = os.path.join('data', 'cache', 'organic', sig)
    if not os.path.isdir(cache_dir):
        return False
    target_dir = os.path.join('data', doi_hash, 'cbu_derivation', 'organic')
    try:
        os.makedirs(target_dir, exist_ok=True)
        for root, dirs, files in os.walk(cache_dir):
            rel = os.path.relpath(root, cache_dir)
            dst_root = os.path.join(target_dir, rel) if rel != '.' else target_dir
            os.makedirs(dst_root, exist_ok=True)
            for fn in files:
                shutil.copy2(os.path.join(root, fn), os.path.join(dst_root, fn))
        return True
    except Exception:
        return False

def _save_organic_cache(doi_hash: str, cache_enabled: bool) -> None:
    if not cache_enabled:
        return
    sig = _organic_cache_key(doi_hash)
    cache_dir = os.path.join('data', 'cache', 'organic', sig)
    src_dir = os.path.join('data', doi_hash, 'cbu_derivation', 'organic')
    if not os.path.isdir(src_dir):
        return
    try:
        for root, dirs, files in os.walk(src_dir):
            rel = os.path.relpath(root, src_dir)
            dst_root = os.path.join(cache_dir, rel) if rel != '.' else cache_dir
            os.makedirs(dst_root, exist_ok=True)
            for fn in files:
                shutil.copy2(os.path.join(root, fn), os.path.join(dst_root, fn))
    except Exception:
        pass

def run_pipeline_for_doi(doi: str):
    """Run the complete pipeline for a specific DOI."""
    print(f"Running pipeline for DOI: {doi}")
    print("=" * 60)
    
    # Step 1: Convert PDFs to markdown (skip if already exists)
    print("Step 1: Converting PDFs to markdown (skip if already exists)...")
    success = convert_doi_pdfs(doi)
    if not success:
        print(f"X Failed to convert PDFs for DOI: {doi}")
        return False
    print("OK PDF conversion completed successfully")
    
    # Step 2: Run division and classification
    print("Step 2: Running division and classification...")
    success = run_division_and_classify(doi)
    if not success:
        print(f"X Failed to run division and classification for DOI: {doi}")
        return False
    print("OK Division and classification completed successfully")
    
    # TODO: Add additional pipeline steps here
    # Step 3: [Next pipeline step]
    # Step 4: [Next pipeline step]
    # etc.
    
    print("=" * 60)
    print(f"✓ Pipeline completed successfully for DOI: {doi}")
    return True

def run_pipeline_for_all():
    """Run the complete pipeline for all DOIs in the data directory."""
    print("Running pipeline for all DOIs")
    print("=" * 60)
    
    # Step 1: Convert all PDFs to markdown (skip if already exists)
    print("Step 1: Converting all PDFs to markdown (skip if already exists)...")
    success = convert_all_dois()
    if not success:
        print("X Failed to convert PDFs for some DOIs")
        return False
    print("OK PDF conversion completed successfully")
    
    # Step 2: Run division and classification for all DOIs
    print("Step 2: Running division and classification for all DOIs...")
    success = run_division_and_classify_all()
    if not success:
        print("X Failed to run division and classification for some DOIs")
        return False
    print("OK Division and classification completed successfully for all DOIs")
    
    # TODO: Add additional pipeline steps here
    # Step 3: [Next pipeline step]
    # Step 4: [Next pipeline step]
    # etc.
    
    print("=" * 60)
    print("✓ Pipeline completed successfully for all DOIs")
    return True

def run_pipeline_for_hash(doi_hash, test_mode=False, input_dir: str | None = None, cache_enabled: bool = False):
    """Run the pipeline for a specific DOI hash."""
    print(f"Processing DOI hash: {doi_hash}")
    
    # Create the hash-based directory
    directory = os.path.join('data', doi_hash)
    os.makedirs(directory, exist_ok=True)
    print(f"Created/verified directory: {directory}")
    
    # Find the original DOI from the mapping
    doi_mapping_path = os.path.join('data', 'doi_to_hash.json')
    if not os.path.exists(doi_mapping_path):
        print(f"DOI mapping file not found: {doi_mapping_path}")
        return False
    
    with open(doi_mapping_path, 'r') as f:
        doi_mapping = json.load(f)
    
    # Find the DOI that corresponds to this hash
    original_doi = None
    for doi, hash_val in doi_mapping.items():
        if hash_val == doi_hash:
            original_doi = doi
            break
    
    if not original_doi:
        print(f"No DOI found for hash: {doi_hash}")
        return False
    
    print(f"Found original DOI: {original_doi}")
    
    # Check if PDFs exist in the specified input directory (fallback to RAW_DATA_DIR)
    base_dir = input_dir or RAW_DATA_DIR
    pdf_path = os.path.join(base_dir, f"{original_doi}.pdf")
    si_pdf_path = os.path.join(base_dir, f"{original_doi}_si.pdf")
    
    if not os.path.exists(pdf_path):
        print(f"PDF not found: {pdf_path}")
        return False
    
    # Copy PDFs to the hash directory
    hash_pdf_path = os.path.join(directory, f"{doi_hash}.pdf")
    hash_si_pdf_path = os.path.join(directory, f"{doi_hash}_si.pdf")
    
    if not os.path.exists(hash_pdf_path):
        shutil.copy2(pdf_path, hash_pdf_path)
        print(f"Copied PDF to: {hash_pdf_path}")
    
    if os.path.exists(si_pdf_path) and not os.path.exists(hash_si_pdf_path):
        shutil.copy2(si_pdf_path, hash_si_pdf_path)
        print(f"Copied SI PDF to: {hash_si_pdf_path}")
    
    # Step 1: PDF to Markdown conversion (skip if already exists)
    print("Step 1: Converting PDFs to markdown...")
    hash_md_path = os.path.join(directory, f"{doi_hash}.md")
    hash_si_md_path = os.path.join(directory, f"{doi_hash}_si.md")
    
    if os.path.exists(hash_md_path) and os.path.exists(hash_si_md_path):
        print("⏭️  Skipping PDF conversion: markdown files already exist")
    else:
        success = convert_doi_pdfs(doi_hash)  # Use hash as the identifier
        if not success:
            print(f"Failed to convert PDFs for hash: {doi_hash}")
            return False
        print("✅ PDF conversion completed successfully")
    
    # Step 2: Division and classification (skip if stitched file exists)
    print("Step 2: Running division and classification...")
    stitched_path = os.path.join(directory, f"{doi_hash}_stitched.md")
    
    if os.path.exists(stitched_path):
        print("⏭️  Skipping division and classification: stitched file already exists")
    else:
        success = run_division_and_classify(doi_hash)  # Use hash as the identifier
        if not success:
            print(f"Failed to run division and classification for hash: {doi_hash}")
            return False
        print("✅ Division and classification completed successfully")
    
    # Step 3: Iterative MCP hints and CCDC enrichment prior to full extraction
    print("Step 3: Preparing MCP hints (iter1, iter2) and CCDC (iter2_1)...")
    mcp_dir = os.path.join(directory, "mcp_run")
    entities_path = os.path.join(mcp_dir, "iter1_top_entities.json")

    # 3a) Ensure iteration 1 outputs exist
    iter1_required_files = [
        os.path.join(mcp_dir, "iter1_hints.txt"),
        os.path.join(directory, "output_top.ttl"),
        os.path.join(directory, "iteration_1.ttl"),
        entities_path,
    ]
    missing_iter1 = [f for f in iter1_required_files if not os.path.exists(f)]
    if missing_iter1:
        print(f"▶️  {doi_hash}: Running iteration 1 (missing: {[os.path.basename(f) for f in missing_iter1]})...")
        try:
            # Fallback to main dynamic agent; it performs iter1 and will skip if artifacts exist
            asyncio.run(run_task(doi_hash, test=test_mode))
            print(f"✅ {doi_hash}: Iteration 1 (via run_task) completed/skipped as needed")
        except Exception as e:
            print(f"❌ {doi_hash}: Iteration 1 failed: {e}")
            return False
    else:
        print(f"⏭️  {doi_hash}: Skip iteration 1 (already complete)")

    # 3b) Generate iter2 hints if any are missing
    need_iter2 = False
    try:
        if os.path.exists(entities_path):
            with open(entities_path, 'r', encoding='utf-8') as f:
                entities = json.load(f)
            for entity in entities:
                label = entity.get("label", "")
                safe_label = (label or "entity").replace(" ", "_").replace("/", "_").replace("\\", "_").replace(":", "_")
                iter2_hints = os.path.join(mcp_dir, f"iter2_hints_{safe_label}.txt")
                if not os.path.exists(iter2_hints):
                    need_iter2 = True
                    break
        if need_iter2:
            print(f"▶️  {doi_hash}: Running iteration 2 (MCP hints generation)...")
            try:
                cmd = [sys.executable, "-m", "src.agents.mops.misc.extraction_agent_mcp_test", "--file", doi_hash]
                subprocess.run(cmd, cwd=os.getcwd(), capture_output=False, check=True)
                print(f"✅ {doi_hash}: Iteration 2 hints completed")
            except subprocess.CalledProcessError as e:
                print(f"⚠️  {doi_hash}: Iteration 2 hints generation failed: {e}")
                return False
        else:
            print(f"⏭️  {doi_hash}: Skip iteration 2 hints (already complete)")
    except Exception as e:
        print(f"⚠️  {doi_hash}: Could not check/generate iter2 hints: {e}")

    # 3c) Run iter2_1 to enrich iter2 outputs with CCDC numbers
    # Skip iter2_1 when iter2 hints files are already present
    try:
        iter2_hints_complete = False
        if os.path.exists(entities_path):
            with open(entities_path, 'r', encoding='utf-8') as f:
                entities = json.load(f)
            if entities:
                all_present = True
                for entity in entities:
                    label = entity.get("label", "")
                    safe_label = (label or "entity").replace(" ", "_").replace("/", "_").replace("\\", "_").replace(":", "_")
                    iter2_hints = os.path.join(mcp_dir, f"iter2_hints_{safe_label}.txt")
                    if not os.path.exists(iter2_hints):
                        all_present = False
                        break
                iter2_hints_complete = all_present

        if iter2_hints_complete:
            print(f"⏭️  {doi_hash}: Skip iter2_1 (iter2 hints already present)")
        else:
            print(f"▶️  {doi_hash}: Running iter2_1 (CCDC enrichment)...")
            cmd = [sys.executable, "-m", "src.agents.mops.misc.extraction_agent_mcp_test", "--file", doi_hash, "--iter2_1"]
            subprocess.run(cmd, cwd=os.getcwd(), capture_output=False, check=True)
            print(f"✅ {doi_hash}: iter2_1 CCDC enrichment completed")
    except subprocess.CalledProcessError as e:
        print(f"⚠️  {doi_hash}: iter2_1 CCDC enrichment failed: {e}")
        return False

    # Step 4: Dynamic MCP-based MOPs extraction (skip if output.ttl already exists)
    print("Step 4: Running dynamic MCP-based MOPs extraction...")
    out_ttl = os.path.join(directory, "output.ttl")
    if os.path.exists(out_ttl):
        print("⏭️  Skipping dynamic MCP extraction: output.ttl already exists")
    else:
        # Try cache restore first
        if _try_restore_extraction_cache(doi_hash, cache_enabled):
            print("✅ Restored extraction outputs from cache")
        else:
            try:
                asyncio.run(run_task(doi_hash, test=test_mode))
                print("✅ Dynamic MCP-based MOPs extraction completed successfully")
                _save_extraction_cache(doi_hash, cache_enabled)
            except Exception as e:
                print(f"Failed to run dynamic MCP agent for hash: {doi_hash}")
                print(f"Error: {e}")
                return False

    # Step 5: OntoMOPs extension (skip if ontomops_output already populated)
    print("Step 5: Running OntoMOPs extension agent...")
    ontomops_out_dir = os.path.join(directory, "ontomops_output")
    ontomops_done = os.path.isdir(ontomops_out_dir) and any(
        name for name in os.listdir(ontomops_out_dir) if name.endswith(".ttl")
    )
    if ontomops_done:
        print("⏭️  Skipping OntoMOPs extension: ontomops_output already populated")
    else:
        try:
            cmd = [sys.executable, "-m", "src.agents.mops.extension.ontomop-extension-agent", "--file", doi_hash]
            subprocess.run(cmd, cwd=os.getcwd(), capture_output=False, check=True)
            print("✅ OntoMOPs extension completed")
        except subprocess.CalledProcessError as e:
            print(f"Failed to run OntoMOPs extension for hash: {doi_hash}")
            print(f"Error: {e}")
            return False

    # Step 6: OntoSpecies extension (skip if ontospecies_output already populated)
    print("Step 6: Running OntoSpecies extension agent...")
    ontospecies_out_dir = os.path.join(directory, "ontospecies_output")
    ontospecies_done = os.path.isdir(ontospecies_out_dir) and any(
        name for name in os.listdir(ontospecies_out_dir) if name.endswith(".ttl")
    )
    if ontospecies_done:
        print("⏭️  Skipping OntoSpecies extension: ontospecies_output already populated")
    else:
        # Retry logic for transient API failures (e.g., Cloudflare 500 errors)
        max_retries = 3
        retry_delay = 10  # seconds
        cmd = [sys.executable, "-m", "src.agents.mops.extension.ontospecies-extension-agent", "--file", doi_hash]
        
        for attempt in range(1, max_retries + 1):
            try:
                print(f"🔄 OntoSpecies extension attempt {attempt}/{max_retries}...")
                subprocess.run(cmd, cwd=os.getcwd(), capture_output=False, check=True)
                print("✅ OntoSpecies extension completed")
                break  # Success, exit retry loop
            except subprocess.CalledProcessError as e:
                if attempt < max_retries:
                    print(f"⚠️  Attempt {attempt} failed: {e}")
                    print(f"⏳ Retrying in {retry_delay} seconds...")
                    time.sleep(retry_delay)
                else:
                    print(f"❌ Failed to run OntoSpecies extension for hash: {doi_hash} after {max_retries} attempts")
                    print(f"Error: {e}")
                    return False

    # Step 7 & 8: Run Metal and Organic CBU derivations in parallel for this DOI
    print("Step 7 & 8: Running Metal + Organic CBU derivations in parallel...")
    async def _run_module(module: str, hv: str) -> bool:
        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable, "-m", module, "--file", hv,
                cwd=os.getcwd()
            )
            rc = await proc.wait()
            return rc == 0
        except Exception:
            return False

    async def _run_pair(hv: str) -> tuple[bool, bool]:
        metal = _run_module("src.agents.mops.cbu_derivation.metal_cbu_derivation_agent", hv)
        if _try_restore_organic_cache(hv, cache_enabled):
            async def _ok():
                return True
            organic = _ok()
        else:
            organic = _run_module("src.agents.mops.cbu_derivation.organic_cbu_derivation_agent", hv)
        m_ok, o_ok = await asyncio.gather(metal, organic)
        if o_ok:
            _save_organic_cache(hv, cache_enabled)
        return m_ok, o_ok

    m_ok, o_ok = asyncio.run(_run_pair(doi_hash))
    if m_ok:
        print("✅ Metal CBU derivation completed")
    else:
        print("❌ Metal CBU derivation failed")
    if o_ok:
        print("✅ Organic CBU derivation completed")
    else:
        print("❌ Organic CBU derivation failed")
    if not (m_ok and o_ok):
        return False

    # Step 9: Integration of results
    print("Step 9: Integrating metal and organic CBUs...")
    try:
        cmd = [sys.executable, "-m", "src.agents.mops.cbu_derivation.integration", "--file", doi_hash]
        subprocess.run(cmd, cwd=os.getcwd(), capture_output=False, check=True)
        print("✅ Integration completed")
    except subprocess.CalledProcessError as e:
        print(f"Failed to integrate results for hash: {doi_hash}")
        print(f"Error: {e}")
        return False

    # Step 10: MOP formula derivation (new agent)
    print("Step 10: Running MOP formula derivation agent...")
    try:
        cmd = [sys.executable, "-m", "src.agents.mops.ontomop_derivation.agent_mop_formula", "--file", doi_hash]
        subprocess.run(cmd, cwd=os.getcwd(), capture_output=False, check=True)
        print("✅ MOP formula derivation completed")
    except subprocess.CalledProcessError as e:
        print(f"Failed to run MOP formula derivation for hash: {doi_hash}")
        print(f"Error: {e}")
        return False

    # Step 11: Re-run integration to override hasMOPFormula with derived value
    print("Step 11: Updating integrated TTLs with derived MOP formula...")
    try:
        cmd = [sys.executable, "-m", "src.agents.mops.cbu_derivation.integration", "--file", doi_hash]
        subprocess.run(cmd, cwd=os.getcwd(), capture_output=False, check=True)
        print("✅ Integrated TTLs updated with derived MOP formula")
    except subprocess.CalledProcessError as e:
        print(f"Failed to update integrated TTLs for hash: {doi_hash}")
        print(f"Error: {e}")
        return False

    print(f"Pipeline completed for DOI hash: {doi_hash}")
    return True

def run_pipeline_for_all_hashes(test_mode=False, input_dir: str | None = None, cache_enabled: bool = False):
    """Run the pipeline for all DOI hashes in phased order with extraction moved up.

    Phases (per all DOIs):
      1) Prepare inputs: copy PDFs, convert to markdown, division/classification
      2) Extraction (dynamic MCP) — done for all DOIs before any other steps
      3) Post-processing: extensions, derivations, integration
    """
    doi_mapping_path = 'data/doi_to_hash.json'

    # Check if the mapping file exists
    if not os.path.exists(doi_mapping_path):
        print(f"DOI mapping file not found: {doi_mapping_path}")
        print("Please create the mapping file or use run_pipeline_for_doi() for individual DOIs.")
        return False

    with open(doi_mapping_path) as f:
        doi_mapping = json.load(f)

    if not doi_mapping:
        print("DOI mapping file is empty. No DOIs to process.")
        return True

    base_dir = input_dir or RAW_DATA_DIR
    doi_hashes = list(doi_mapping.values())

    overall_success = True

    # -------------------- Phase 1: Prepare inputs --------------------
    print("\n=== Phase 1/3: Preparing inputs (copy PDFs, convert, division) for all DOIs ===")
    for doi_hash in doi_hashes:
        try:
            directory = os.path.join('data', doi_hash)
            os.makedirs(directory, exist_ok=True)

            # Find original DOI for this hash
            original_doi = None
            for doi, hash_val in doi_mapping.items():
                if hash_val == doi_hash:
                    original_doi = doi
                    break
            if not original_doi:
                print(f"No DOI found for hash: {doi_hash}")
                overall_success = False
                continue

            pdf_path = os.path.join(base_dir, f"{original_doi}.pdf")
            si_pdf_path = os.path.join(base_dir, f"{original_doi}_si.pdf")
            if not os.path.exists(pdf_path):
                print(f"PDF not found for {doi_hash}: {pdf_path}")
                overall_success = False
                continue

            # Copy PDFs into hash dir if needed
            hash_pdf_path = os.path.join(directory, f"{doi_hash}.pdf")
            hash_si_pdf_path = os.path.join(directory, f"{doi_hash}_si.pdf")
            if not os.path.exists(hash_pdf_path):
                shutil.copy2(pdf_path, hash_pdf_path)
                print(f"Copied PDF to: {hash_pdf_path}")
            if os.path.exists(si_pdf_path) and not os.path.exists(hash_si_pdf_path):
                shutil.copy2(si_pdf_path, hash_si_pdf_path)
                print(f"Copied SI PDF to: {hash_si_pdf_path}")

            # Convert PDFs to markdown if needed
            hash_md_path = os.path.join(directory, f"{doi_hash}.md")
            hash_si_md_path = os.path.join(directory, f"{doi_hash}_si.md")
            if os.path.exists(hash_md_path) and os.path.exists(hash_si_md_path):
                print(f"⏭️  {doi_hash}: Skip PDF conversion (markdown exists)")
            else:
                ok = convert_doi_pdfs(doi_hash)
                if not ok:
                    print(f"Failed PDF conversion for {doi_hash}")
                    overall_success = False
                    continue
                print(f"✅ {doi_hash}: PDF conversion completed")

            # Division and classification if needed
            stitched_path = os.path.join(directory, f"{doi_hash}_stitched.md")
            if os.path.exists(stitched_path):
                print(f"⏭️  {doi_hash}: Skip division (stitched exists)")
            else:
                ok = run_division_and_classify(doi_hash)
                if not ok:
                    print(f"Failed division/classification for {doi_hash}")
                    overall_success = False
                    continue
                print(f"✅ {doi_hash}: Division and classification completed")
        except Exception as e:
            print(f"Phase 1 error for {doi_hash}: {e}")
            overall_success = False

    # -------------------- Phase 2: Extraction (moved up) --------------------
    print("\n=== Phase 2/3: Running extraction for all DOIs (moved up) ===")
    for doi_hash in doi_hashes:
        try:
            directory = os.path.join('data', doi_hash)
            stitched_path = os.path.join(directory, f"{doi_hash}_stitched.md")
            if not os.path.exists(stitched_path):
                print(f"⏭️  {doi_hash}: Skip extraction (stitched missing)")
                overall_success = False
                continue
            out_ttl = os.path.join(directory, "output.ttl")
            if os.path.exists(out_ttl):
                print(f"⏭️  {doi_hash}: Skip extraction (output.ttl exists)")
                continue
            
            # Step 2a: Check and run iter1 if needed
            iter1_required_files = [
                os.path.join(directory, "mcp_run", "iter1_hints.txt"),
                os.path.join(directory, "output_top.ttl"),
                os.path.join(directory, "iteration_1.ttl"),
                os.path.join(directory, "mcp_run", "iter1_top_entities.json")
            ]
            missing_iter1 = [f for f in iter1_required_files if not os.path.exists(f)]
            
            if missing_iter1:
                print(f"▶️  {doi_hash}: Running iteration 1 (missing: {[os.path.basename(f) for f in missing_iter1]})...")
                try:
                    asyncio.run(run_task(doi_hash, test=test_mode))
                    print(f"✅ {doi_hash}: Iteration 1 (via run_task) completed/skipped as needed")
                except Exception as e:
                    print(f"❌ {doi_hash}: Iteration 1 failed: {e}")
                    overall_success = False
                    continue
            else:
                print(f"⏭️  {doi_hash}: Skip iteration 1 (already complete)")
            
            # Step 2b: Check and run iter2 hints generation if needed
            mcp_dir = os.path.join(directory, "mcp_run")
            entities_path = os.path.join(mcp_dir, "iter1_top_entities.json")
            if not os.path.exists(entities_path):
                print(f"⏭️  {doi_hash}: Skip iteration 2 hints (no top entities)")
            else:
                # Check if iter2 hints exist for any entity
                try:
                    with open(entities_path, 'r', encoding='utf-8') as f:
                        entities = json.load(f)
                    need_iter2 = False
                    for entity in entities:
                        label = entity.get("label", "")
                        safe_label = (label or "entity").replace(" ", "_").replace("/", "_").replace("\\", "_").replace(":", "_")
                        iter2_hints = os.path.join(mcp_dir, f"iter2_hints_{safe_label}.txt")
                        if not os.path.exists(iter2_hints):
                            need_iter2 = True
                            break
                    
                    if need_iter2:
                        print(f"▶️  {doi_hash}: Running iteration 2 (MCP hints generation)...")
                        try:
                            cmd = [sys.executable, "-m", "src.agents.mops.misc.extraction_agent_mcp_test", "--file", doi_hash]
                            subprocess.run(cmd, cwd=os.getcwd(), capture_output=False, check=True)
                            print(f"✅ {doi_hash}: Iteration 2 hints completed")
                        except subprocess.CalledProcessError as e:
                            print(f"⚠️  {doi_hash}: Iteration 2 hints generation failed: {e}")
                            overall_success = False
                            continue
                    else:
                        print(f"⏭️  {doi_hash}: Skip iteration 2 hints (already complete)")
                except Exception as e:
                    print(f"⚠️  {doi_hash}: Could not check iter2 status: {e}")
            
            # Step 2c: Run full extraction (uses iter1 and iter2 hints to produce output.ttl)
            print(f"▶️  {doi_hash}: Running full extraction (iter1+iter2 → output.ttl)...")
            try:
                asyncio.run(run_task(doi_hash, test=test_mode))
                print(f"✅ {doi_hash}: Extraction completed")
            except Exception as e:
                print(f"❌ {doi_hash}: Extraction failed: {e}")
                overall_success = False
                
        except Exception as e:
            print(f"❌ Extraction failed for {doi_hash}: {e}")
            overall_success = False

    # -------------------- Phase 3: Post-processing steps --------------------
    print("\n=== Phase 3/3: Running post-processing (extensions, derivations, integration) ===")

    # 3A) Run extensions first (sequential per DOI)
    for doi_hash in doi_hashes:
        directory = os.path.join('data', doi_hash)

        # OntoMOPs extension
        print(f"{doi_hash}: Step 4 OntoMOPs extension...")
        ontomops_out_dir = os.path.join(directory, "ontomops_output")
        ontomops_done = os.path.isdir(ontomops_out_dir) and any(
            name for name in os.listdir(ontomops_out_dir) if name.endswith(".ttl")
        )
        if ontomops_done:
            print(f"⏭️  {doi_hash}: Skip OntoMOPs extension (already populated)")
        else:
            try:
                cmd = [sys.executable, "-m", "src.agents.mops.extension.ontomop-extension-agent", "--file", doi_hash]
                subprocess.run(cmd, cwd=os.getcwd(), capture_output=False, check=True)
                print(f"✅ {doi_hash}: OntoMOPs extension completed")
            except subprocess.CalledProcessError as e:
                print(f"Failed OntoMOPs extension for {doi_hash}: {e}")
                overall_success = False

        # OntoSpecies extension
        print(f"{doi_hash}: Step 5 OntoSpecies extension...")
        ontospecies_out_dir = os.path.join(directory, "ontospecies_output")
        ontospecies_done = os.path.isdir(ontospecies_out_dir) and any(
            name for name in os.listdir(ontospecies_out_dir) if name.endswith(".ttl")
        )
        if ontospecies_done:
            print(f"⏭️  {doi_hash}: Skip OntoSpecies extension (already populated)")
        else:
            # Retry logic for transient API failures (e.g., Cloudflare 500 errors)
            max_retries = 3
            retry_delay = 10  # seconds
            cmd = [sys.executable, "-m", "src.agents.mops.extension.ontospecies-extension-agent", "--file", doi_hash]
            
            success = False
            for attempt in range(1, max_retries + 1):
                try:
                    print(f"🔄 {doi_hash}: OntoSpecies extension attempt {attempt}/{max_retries}...")
                    subprocess.run(cmd, cwd=os.getcwd(), capture_output=False, check=True)
                    print(f"✅ {doi_hash}: OntoSpecies extension completed")
                    success = True
                    break  # Success, exit retry loop
                except subprocess.CalledProcessError as e:
                    if attempt < max_retries:
                        print(f"⚠️  {doi_hash}: Attempt {attempt} failed: {e}")
                        print(f"⏳ Retrying in {retry_delay} seconds...")
                        time.sleep(retry_delay)
                    else:
                        print(f"❌ Failed OntoSpecies extension for {doi_hash} after {max_retries} attempts: {e}")
                        overall_success = False

    # 3B) Derivations: run Metal and Organic in parallel per DOI, with flexible batch size across DOIs
    print("\n🚦 Running CBU derivations in parallel (metal + organic per DOI)...")

    # Global task counter for logging
    task_counter = 0

    async def _run_module(module: str, hv: str) -> bool:
        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable, "-m", module, "--file", hv,
                cwd=os.getcwd()
            )
            rc = await proc.wait()
            return rc == 0
        except Exception as _e:
            return False

    async def _run_cbu_pair(hv: str) -> bool:
        print(f"{hv}: ▶️  Metal + Organic CBU derivation starting...")
        nonlocal task_counter
        task_counter += 1
        tid_m = task_counter
        print(f"Task {tid_m} is started: metal [{hv}]")
        metal = _run_module("src.agents.mops.cbu_derivation.metal_cbu_derivation_agent", hv)
        task_counter += 1
        tid_o = task_counter
        print(f"Task {tid_o} is started: organic [{hv}]")
        organic = _run_module("src.agents.mops.cbu_derivation.organic_cbu_derivation_agent", hv)
        m_ok, o_ok = await asyncio.gather(metal, organic)
        if m_ok:
            print(f"{hv}: ✅ Metal CBU derivation completed")
        else:
            print(f"{hv}: ❌ Metal CBU derivation failed")
        if o_ok:
            print(f"{hv}: ✅ Organic CBU derivation completed")
        else:
            print(f"{hv}: ❌ Organic CBU derivation failed")
        return m_ok and o_ok

    async def _run_cbu_in_batches(hashes: list[str]) -> bool:
        # Determine concurrency
        try:
            max_conc = int(os.getenv("MOPS_CBU_MAX_CONCURRENCY", "0"))
        except Exception:
            max_conc = 0
        if max_conc <= 0:
            max_conc = min(4, len(hashes)) if hashes else 1
        print(f"Parallel CBU batch size (concurrency): {max_conc}")

        sem = asyncio.Semaphore(max_conc)
        results: list[bool] = []

        async def _guarded(hv: str):
            async with sem:
                ok = await _run_cbu_pair(hv)
                results.append(ok)

        tasks = [asyncio.create_task(_guarded(hv)) for hv in hashes]
        await asyncio.gather(*tasks)
        return all(results) if results else True

    ok_all = asyncio.run(_run_cbu_in_batches(doi_hashes))
    if not ok_all:
        overall_success = False

    # 3C) Integration after both derivations are done
    for doi_hash in doi_hashes:
        print(f"{doi_hash}: Step 8 Integration...")
        try:
            cmd = [sys.executable, "-m", "src.agents.mops.cbu_derivation.integration", "--file", doi_hash]
            subprocess.run(cmd, cwd=os.getcwd(), capture_output=False, check=True)
            print(f"✅ {doi_hash}: Integration completed")
        except subprocess.CalledProcessError as e:
            print(f"Failed integration for {doi_hash}: {e}")
            overall_success = False

    # 3D) MOP formula derivation (new agent)
    for doi_hash in doi_hashes:
        print(f"{doi_hash}: Step 9 MOP formula derivation...")
        try:
            cmd = [sys.executable, "-m", "src.agents.mops.ontomop_derivation.agent_mop_formula", "--file", doi_hash]
            subprocess.run(cmd, cwd=os.getcwd(), capture_output=False, check=True)
            print(f"✅ {doi_hash}: MOP formula derivation completed")
        except subprocess.CalledProcessError as e:
            print(f"Failed MOP formula derivation for {doi_hash}: {e}")
            overall_success = False

    return overall_success

def run_extraction_only_for_all_hashes(test_mode=False, input_dir: str | None = None, cache_enabled: bool = False):
    """Run extraction for all DOI hashes stopping after iter4 hints are created.
    
    This function runs the same extraction sequence as the regular pipeline but stops after 
    iter4 hints are created (does NOT create output.ttl).
    
    This includes:
    1. PDF conversion (if needed)
    2. Division and classification (if needed) 
    3. Dynamic MCP-based MOPs extraction through all iterations (iter1-4 hints + iter3_1 + iter3_2 enrichments)
    
    It stops before the final TTL generation step.
    """
    doi_mapping_path = 'data/doi_to_hash.json'

    # Check if the mapping file exists
    if not os.path.exists(doi_mapping_path):
        print(f"DOI mapping file not found: {doi_mapping_path}")
        print("Please create the mapping file or use run_pipeline_for_doi() for individual DOIs.")
        return False

    with open(doi_mapping_path) as f:
        doi_mapping = json.load(f)

    if not doi_mapping:
        print("DOI mapping file is empty. No DOIs to process.")
        return True

    base_dir = input_dir or RAW_DATA_DIR
    doi_hashes = list(doi_mapping.values())

    overall_success = True

    # -------------------- Phase 1: Prepare inputs --------------------
    print("\n=== Phase 1/2: Preparing inputs (copy PDFs, convert, division) for all DOIs ===")
    for doi_hash in doi_hashes:
        try:
            directory = os.path.join('data', doi_hash)
            os.makedirs(directory, exist_ok=True)

            # Find original DOI for this hash
            original_doi = None
            for doi, hash_val in doi_mapping.items():
                if hash_val == doi_hash:
                    original_doi = doi
                    break
            if not original_doi:
                print(f"No DOI found for hash: {doi_hash}")
                overall_success = False
                continue

            pdf_path = os.path.join(base_dir, f"{original_doi}.pdf")
            si_pdf_path = os.path.join(base_dir, f"{original_doi}_si.pdf")
            if not os.path.exists(pdf_path):
                print(f"PDF not found for {doi_hash}: {pdf_path}")
                overall_success = False
                continue

            # Copy PDFs into hash dir if needed
            hash_pdf_path = os.path.join(directory, f"{doi_hash}.pdf")
            hash_si_pdf_path = os.path.join(directory, f"{doi_hash}_si.pdf")
            if not os.path.exists(hash_pdf_path):
                shutil.copy2(pdf_path, hash_pdf_path)
                print(f"Copied PDF to: {hash_pdf_path}")
            if os.path.exists(si_pdf_path) and not os.path.exists(hash_si_pdf_path):
                shutil.copy2(si_pdf_path, hash_si_pdf_path)
                print(f"Copied SI PDF to: {hash_si_pdf_path}")

            # Convert PDFs to markdown if needed
            hash_md_path = os.path.join(directory, f"{doi_hash}.md")
            hash_si_md_path = os.path.join(directory, f"{doi_hash}_si.md")
            if os.path.exists(hash_md_path) and os.path.exists(hash_si_md_path):
                print(f"⏭️  {doi_hash}: Skip PDF conversion (markdown exists)")
            else:
                ok = convert_doi_pdfs(doi_hash)
                if not ok:
                    print(f"Failed PDF conversion for {doi_hash}")
                    overall_success = False
                    continue
                print(f"✅ {doi_hash}: PDF conversion completed")

            # Division and classification if needed
            stitched_path = os.path.join(directory, f"{doi_hash}_stitched.md")
            if os.path.exists(stitched_path):
                print(f"⏭️  {doi_hash}: Skip division (stitched exists)")
            else:
                ok = run_division_and_classify(doi_hash)
                if not ok:
                    print(f"Failed division/classification for {doi_hash}")
                    overall_success = False
                    continue
                print(f"✅ {doi_hash}: Division and classification completed")
        except Exception as e:
            print(f"Phase 1 error for {doi_hash}: {e}")
            overall_success = False

    # -------------------- Phase 2: Extraction (hints only, with test=True) --------------------
    print("\n=== Phase 2/2: Running extraction for all DOIs (stopping after iter4 hints) ===")
    for doi_hash in doi_hashes:
        try:
            directory = os.path.join('data', doi_hash)
            stitched_path = os.path.join(directory, f"{doi_hash}_stitched.md")
            if not os.path.exists(stitched_path):
                print(f"⏭️  {doi_hash}: Skip extraction (stitched missing)")
                overall_success = False
                continue
            
            # Step 2a: Ensure iteration 1 is completed (hints + top entity TTL)
            print(f"▶️  {doi_hash}: Running iteration 1 only (hints + top entity TTL)...")
            try:
                asyncio.run(run_task_iter1_only(doi_hash, test=test_mode))
                print(f"✅ {doi_hash}: Iteration 1 completed (hints + iteration_1.ttl)")
            except Exception as e:
                print(f"❌ {doi_hash}: Iteration 1 failed: {e}")
                overall_success = False
                continue

            # Step 2b: Generate ONLY hints for iterations 2–4 and STOP
            print(f"▶️  {doi_hash}: Generating hints for iterations 2–4 (no execution)...")
            try:
                asyncio.run(run_task_hints_only(doi_hash, start_iter=2, end_iter=4))
                print(f"✅ {doi_hash}: Hints generated for iterations 2–4")
            except Exception as e:
                print(f"❌ {doi_hash}: Hints-only generation failed: {e}")
                overall_success = False

        except Exception as e:
            print(f"❌ Extraction failed for {doi_hash}: {e}")
            overall_success = False

    if overall_success:
        print("\n🎉 Extraction-only completed successfully for all DOIs!")
    else:
        print("\n⚠️  Extraction-only completed with some failures. Check the logs above.")
    
    return overall_success

def run_iter1_for_all_hashes(test_mode=False, input_dir: str | None = None, cache_enabled: bool = False, iter1_test_num: int | None = None, only_hash: str | None = None, extraction_only: bool = False):
    """Run until iter1_hints.txt is created for each DOI hash, then proceed to next.
    
    This function runs:
    1. PDF conversion (if needed, skipped if extraction_only=True)
    2. Division and classification (if needed, skipped if extraction_only=True) 
    3. Dynamic MCP-based MOPs extraction until iter1_hints.txt is created
    
    It stops the dynamic MCP agent as soon as iter1_hints.txt is created for each hash.
    
    Args:
        extraction_only: If True, skip PDF conversion and division/classification steps
    """
    doi_mapping_path = 'data/doi_to_hash.json'

    # Check if the mapping file exists
    if not os.path.exists(doi_mapping_path):
        print(f"DOI mapping file not found: {doi_mapping_path}")
        print("Please create the mapping file or use run_pipeline_for_doi() for individual DOIs.")
        return False

    with open(doi_mapping_path) as f:
        doi_mapping = json.load(f)

    if not doi_mapping:
        print("DOI mapping file is empty. No DOIs to process.")
        return True

    base_dir = input_dir or RAW_DATA_DIR
    if only_hash:
        doi_hashes = [only_hash]
    else:
        doi_hashes = list(doi_mapping.values())
    overall_success = True

    print("\n=== Running iter1 extraction for all DOIs (stops when iter1_hints.txt is created) ===")
    for doi_hash in doi_hashes:
        try:
            directory = os.path.join('data', doi_hash)
            os.makedirs(directory, exist_ok=True)

            # Find original DOI for this hash
            original_doi = None
            for doi, hash_val in doi_mapping.items():
                if hash_val == doi_hash:
                    original_doi = doi
                    break
            if not original_doi:
                print(f"No DOI found for hash: {doi_hash}")
                overall_success = False
                continue

            # Check if iteration 1 is already complete
            iter1_required_files = [
                os.path.join(directory, "mcp_run", "iter1_hints.txt"),
                os.path.join(directory, "output_top.ttl"),
                os.path.join(directory, "iteration_1.ttl"),
                os.path.join(directory, "mcp_run", "iter1_top_entities.json")
            ]
            
            missing_files = [f for f in iter1_required_files if not os.path.exists(f)]
            if not missing_files:
                print(f"⏭️  {doi_hash}: Skip (iteration 1 already complete)")
                continue

            # Step 1: PDF conversion (skip if extraction_only)
            if not extraction_only:
                print(f"{doi_hash}: Step 1 PDF conversion...")
                pdf_path = os.path.join(base_dir, f"{original_doi}.pdf")
                si_pdf_path = os.path.join(base_dir, f"{original_doi}_si.pdf")
                hash_pdf_path = os.path.join(directory, f"{doi_hash}.pdf")
                hash_si_pdf_path = os.path.join(directory, f"{doi_hash}_si.pdf")

                # If RAW_DATA PDF missing, but hash PDF already exists, proceed using existing
                if not os.path.exists(pdf_path) and not os.path.exists(hash_pdf_path):
                    print(f"⏭️  {doi_hash}: Skip (PDF not found in RAW_DATA_DIR and hash dir missing: {pdf_path})")
                    overall_success = False
                    continue

                # Copy PDFs into hash dir if needed
                if os.path.exists(pdf_path) and not os.path.exists(hash_pdf_path):
                    shutil.copy2(pdf_path, hash_pdf_path)
                    print(f"Copied PDF to: {hash_pdf_path}")
                if os.path.exists(si_pdf_path) and not os.path.exists(hash_si_pdf_path):
                    shutil.copy2(si_pdf_path, hash_si_pdf_path)
                    print(f"Copied SI PDF to: {hash_si_pdf_path}")

                # Convert PDFs to markdown if needed
                hash_md_path = os.path.join(directory, f"{doi_hash}.md")
                hash_si_md_path = os.path.join(directory, f"{doi_hash}_si.md")
                if os.path.exists(hash_md_path) and os.path.exists(hash_si_md_path):
                    print(f"⏭️  {doi_hash}: Skip PDF conversion (markdown exists)")
                else:
                    ok = convert_doi_pdfs(doi_hash)
                    if not ok:
                        print(f"❌ {doi_hash}: Failed PDF conversion")
                        overall_success = False
                        continue
                    print(f"✅ {doi_hash}: PDF conversion completed")

            # Step 2: Division and classification (skip if extraction_only)
            if not extraction_only:
                print(f"{doi_hash}: Step 2 Division and classification...")
                stitched_path = os.path.join(directory, f"{doi_hash}_stitched.md")
                if os.path.exists(stitched_path):
                    print(f"⏭️  {doi_hash}: Skip division (stitched exists)")
                else:
                    ok = run_division_and_classify(doi_hash)
                    if not ok:
                        print(f"❌ {doi_hash}: Failed division/classification")
                        overall_success = False
                        continue
                    print(f"✅ {doi_hash}: Division and classification completed")

            # Step 3: Dynamic MCP-based MOPs extraction (complete iteration 1 process)
            print(f"{doi_hash}: Step 3 Dynamic MCP extraction (complete iteration 1)...")
            
            # Check again if iteration 1 is complete after preprocessing
            missing_files_after = [f for f in iter1_required_files if not os.path.exists(f)]
            if not missing_files_after:
                print(f"⏭️  {doi_hash}: Skip extraction (iteration 1 already complete)")
                continue
                
            print(f"▶️  {doi_hash}: Running complete iteration 1 process...")
            
            # Run the dynamic MCP agent with complete iteration 1 process
            try:
                asyncio.run(run_task_iter1_only(doi_hash, test=test_mode, test_iteration_num=iter1_test_num))
                
                # Check if iteration 1 is now complete
                final_missing_files = [f for f in iter1_required_files if not os.path.exists(f)]
                if not final_missing_files:
                    print(f"✅ {doi_hash}: Complete iteration 1 process finished successfully")
                else:
                    print(f"⚠️  {doi_hash}: Iteration 1 incomplete, missing: {[os.path.basename(f) for f in final_missing_files]}")
                    overall_success = False
                    
            except Exception as e:
                print(f"❌ {doi_hash}: Failed to run dynamic MCP extraction: {e}")
                overall_success = False
            
        except Exception as e:
            print(f"❌ Processing failed for {doi_hash}: {e}")
            overall_success = False

    if overall_success:
        print("\n🎉 iter1 extraction completed successfully for all DOIs!")
    else:
        print("\n⚠️  iter1 extraction completed with some failures. Check the logs above.")
    
    return overall_success

def run_iter2_for_all_hashes(test_mode=False, input_dir: str | None = None, cache_enabled: bool = False, iter2_test_num: int | None = None, only_hash: str | None = None, extraction_only: bool = False):
    """Run until iter2_hints.txt is created for each DOI hash, then proceed to next.
    If iter2_test_num is provided, perform N separate iter2 hint generations, copying
    results into data/<hash>/iter2_test_results/iter2_hints_<entity>_<i>.txt
    """
    return _run_iter_for_all_hashes(2, test_mode, input_dir, test_num=iter2_test_num, only_hash=only_hash, extraction_only=extraction_only)

def run_iter3_for_all_hashes(test_mode=False, input_dir: str | None = None, cache_enabled: bool = False, only_hash: str | None = None, extraction_only: bool = False):
    """Run until iter3_hints.txt is created for each DOI hash, then proceed to next.
    
    This function runs:
    1. PDF conversion (if needed, skipped if extraction_only=True)
    2. Division and classification (if needed, skipped if extraction_only=True)
    3. Dynamic MCP-based MOPs extraction until iter3_hints.txt is created (requires iter1 complete, skips iter2)
    
    Args:
        only_hash: If provided, only process this specific DOI hash
        extraction_only: If True, skip PDF conversion and division/classification steps
    """
    return _run_iter_for_all_hashes(3, test_mode, input_dir, test_num=None, only_hash=only_hash, extraction_only=extraction_only)

def run_iter4_for_all_hashes(test_mode=False, input_dir: str | None = None, cache_enabled: bool = False, only_hash: str | None = None, extraction_only: bool = False):
    """Run until iter4_hints.txt is created for each DOI hash, then proceed to next.
    
    This function runs:
    1. PDF conversion (if needed, skipped if extraction_only=True)
    2. Division and classification (if needed, skipped if extraction_only=True)
    3. Dynamic MCP-based MOPs extraction until iter4_hints.txt is created (requires iter1 complete, skips iter2)
    
    Args:
        only_hash: If provided, only process this specific DOI hash
        extraction_only: If True, skip PDF conversion and division/classification steps
    """
    return _run_iter_for_all_hashes(4, test_mode, input_dir, test_num=None, only_hash=only_hash, extraction_only=extraction_only)

def _run_iter_for_all_hashes(iter_num: int, test_mode=False, input_dir: str | None = None, test_num: int | None = None, only_hash: str | None = None, extraction_only: bool = False):
    """Generic function to run until iter{N}_hints.txt is created for each DOI hash."""
    doi_mapping_path = 'data/doi_to_hash.json'

    # Check if the mapping file exists
    if not os.path.exists(doi_mapping_path):
        print(f"DOI mapping file not found: {doi_mapping_path}")
        print("Please create the mapping file or use run_pipeline_for_doi() for individual DOIs.")
        return False

    with open(doi_mapping_path) as f:
        doi_mapping = json.load(f)

    if not doi_mapping:
        print("DOI mapping file is empty. No DOIs to process.")
        return True

    base_dir = input_dir or RAW_DATA_DIR
    if only_hash:
        doi_hashes = [only_hash]
    else:
        doi_hashes = list(doi_mapping.values())
    overall_success = True

    print(f"\n=== Running iter{iter_num} extraction for all DOIs (stops when iter{iter_num}_hints.txt is created) ===")
    for doi_hash in doi_hashes:
        try:
            directory = os.path.join('data', doi_hash)
            os.makedirs(directory, exist_ok=True)

            # Find original DOI for this hash
            original_doi = None
            for doi, hash_val in doi_mapping.items():
                if hash_val == doi_hash:
                    original_doi = doi
                    break
            if not original_doi:
                print(f"No DOI found for hash: {doi_hash}")
                overall_success = False
                continue

            # For iterations >= 2, check if iteration 1 is complete
            if iter_num >= 2:
                iter1_required_files = [
                    os.path.join(directory, "mcp_run", "iter1_hints.txt"),
                    os.path.join(directory, "output_top.ttl"),
                    os.path.join(directory, "iteration_1.ttl"),
                    os.path.join(directory, "mcp_run", "iter1_top_entities.json")
                ]
                
                missing_files = [f for f in iter1_required_files if not os.path.exists(f)]
                if missing_files:
                    print(f"⏭️  {doi_hash}: Skip (iteration 1 not complete, missing: {[os.path.basename(f) for f in missing_files]})")
                    overall_success = False
                    continue

            # Check if iteration N hints already exist
            if iter_num == 1:
                # For iter1, check for single hints file
                iter_hints_path = os.path.join(directory, "mcp_run", f"iter{iter_num}_hints.txt")
                if os.path.exists(iter_hints_path):
                    print(f"⏭️  {doi_hash}: Skip (iter{iter_num}_hints.txt already exists)")
                    continue
            else:
                # For iter2+, check if any entity-specific hints files exist
                iter1_entities_file = os.path.join(directory, "mcp_run", "iter1_top_entities.json")
                if not os.path.exists(iter1_entities_file):
                    print(f"⏭️  {doi_hash}: Skip (iter1_top_entities.json not found, run iter1 first)")
                    continue
                
                try:
                    with open(iter1_entities_file, 'r') as f:
                        top_entities = json.load(f)
                    
                    if not top_entities:
                        print(f"⏭️  {doi_hash}: Skip (no top entities found)")
                        continue
                    
                    # Check if any entity-specific hints files exist
                    hints_exist = False
                    for entity in top_entities:
                        label = entity.get("label", "")
                        safe_name = label.replace(" ", "_").replace("/", "_").replace("\\", "_").replace(":", "_").replace("?", "_").replace("*", "_").replace("<", "_").replace(">", "_").replace("|", "_").replace('"', "_").replace("'", "_")
                        hint_file = os.path.join(directory, "mcp_run", f"iter{iter_num}_hints_{safe_name}.txt")
                        if os.path.exists(hint_file):
                            hints_exist = True
                            break
                    
                    if hints_exist and not (iter_num == 2 and isinstance(test_num, int) and test_num > 0):
                        print(f"⏭️  {doi_hash}: Skip (iter{iter_num} hints already exist)")
                        continue
                        
                except Exception as e:
                    print(f"⏭️  {doi_hash}: Skip (error reading iter1_top_entities.json: {e})")
                    continue

            # Step 1: PDF conversion (skip if extraction_only)
            if not extraction_only:
                print(f"{doi_hash}: Step 1 PDF conversion...")
                pdf_path = os.path.join(base_dir, f"{original_doi}.pdf")
                si_pdf_path = os.path.join(base_dir, f"{original_doi}_si.pdf")
                hash_pdf_path = os.path.join(directory, f"{doi_hash}.pdf")
                hash_si_pdf_path = os.path.join(directory, f"{doi_hash}_si.pdf")

                # If RAW_DATA PDF missing, but hash PDF already exists, proceed using existing
                if not os.path.exists(pdf_path) and not os.path.exists(hash_pdf_path):
                    print(f"⏭️  {doi_hash}: Skip (PDF not found in RAW_DATA_DIR and hash dir missing: {pdf_path})")
                    overall_success = False
                    continue

                # Copy PDFs into hash dir if needed
                if os.path.exists(pdf_path) and not os.path.exists(hash_pdf_path):
                    shutil.copy2(pdf_path, hash_pdf_path)
                    print(f"Copied PDF to: {hash_pdf_path}")
                if os.path.exists(si_pdf_path) and not os.path.exists(hash_si_pdf_path):
                    shutil.copy2(si_pdf_path, hash_si_pdf_path)
                    print(f"Copied SI PDF to: {hash_si_pdf_path}")

                # Convert PDFs to markdown if needed
                hash_md_path = os.path.join(directory, f"{doi_hash}.md")
                hash_si_md_path = os.path.join(directory, f"{doi_hash}_si.md")
                if os.path.exists(hash_md_path) and os.path.exists(hash_si_md_path):
                    print(f"⏭️  {doi_hash}: Skip PDF conversion (markdown exists)")
                else:
                    ok = convert_doi_pdfs(doi_hash)
                    if not ok:
                        print(f"❌ {doi_hash}: Failed PDF conversion")
                        overall_success = False
                        continue
                    print(f"✅ {doi_hash}: PDF conversion completed")

            # Step 2: Division and classification (skip if extraction_only)
            if not extraction_only:
                print(f"{doi_hash}: Step 2 Division and classification...")
                stitched_path = os.path.join(directory, f"{doi_hash}_stitched.md")
                if os.path.exists(stitched_path):
                    print(f"⏭️  {doi_hash}: Skip division (stitched exists)")
                else:
                    ok = run_division_and_classify(doi_hash)
                    if not ok:
                        print(f"❌ {doi_hash}: Failed division/classification")
                        overall_success = False
                        continue
                    print(f"✅ {doi_hash}: Division and classification completed")

            # Step 3: Dynamic MCP-based MOPs extraction (until iter{N}_hints.txt is created)
            print(f"{doi_hash}: Step 3 Dynamic MCP extraction (until iter{iter_num}_hints.txt)...")
            
            # Check again if iteration N hints exist after preprocessing
            if iter_num == 1:
                # For iter1, check single hints file
                iter_hints_path = os.path.join(directory, "mcp_run", f"iter{iter_num}_hints.txt")
                if os.path.exists(iter_hints_path):
                    print(f"⏭️  {doi_hash}: Skip extraction (iter{iter_num}_hints.txt already exists)")
                    continue
            else:
                # For iter2+, check if any entity-specific hints files exist
                iter1_entities_file = os.path.join(directory, "mcp_run", "iter1_top_entities.json")
                if os.path.exists(iter1_entities_file):
                    try:
                        with open(iter1_entities_file, 'r') as f:
                            top_entities = json.load(f)
                        
                        hints_exist = False
                        for entity in top_entities:
                            label = entity.get("label", "")
                            safe_name = label.replace(" ", "_").replace("/", "_").replace("\\", "_").replace(":", "_").replace("?", "_").replace("*", "_").replace("<", "_").replace(">", "_").replace("|", "_").replace('"', "_").replace("'", "_")
                            hint_file = os.path.join(directory, "mcp_run", f"iter{iter_num}_hints_{safe_name}.txt")
                            if os.path.exists(hint_file):
                                hints_exist = True
                                break
                        
                        if hints_exist:
                            print(f"⏭️  {doi_hash}: Skip extraction (iter{iter_num} hints already exist)")
                            continue
                    except Exception as e:
                        print(f"⚠️  {doi_hash}: Error checking iter{iter_num} hints: {e}")
                        # Continue with extraction despite error
                
            print(f"▶️  {doi_hash}: Running dynamic MCP extraction until iter{iter_num}_hints.txt...")
            
            # Run the dynamic MCP agent with a custom stop condition
            try:
                def _cleanup_between_runs():
                    # Remove memory directories and TTL files under doi directory
                    try:
                        for entry in os.listdir(directory):
                            p = os.path.join(directory, entry)
                            if os.path.isdir(p) and entry.startswith("memory"):
                                shutil.rmtree(p, ignore_errors=True)
                            elif os.path.isfile(p) and p.lower().endswith(".ttl"):
                                try:
                                    os.remove(p)
                                except Exception:
                                    pass
                    except Exception:
                        pass

                def _delete_existing_iter2_hints():
                    try:
                        mcp_dir = os.path.join(directory, "mcp_run")
                        if os.path.isdir(mcp_dir):
                            for fn in os.listdir(mcp_dir):
                                if fn.startswith("iter2_hints_") and fn.endswith(".txt"):
                                    try:
                                        os.remove(os.path.join(mcp_dir, fn))
                                    except Exception:
                                        pass
                    except Exception:
                        pass

                if iter_num == 2:
                    if isinstance(test_num, int) and test_num > 0:
                        test_dir = os.path.join(directory, "iter2_test_results")
                        os.makedirs(test_dir, exist_ok=True)
                        for i in range(1, test_num + 1):
                            if i > 1:
                                print("🧹 Cleaning memory/TTL and previous iter2 hints before next test run...")
                                _cleanup_between_runs()
                                _delete_existing_iter2_hints()
                            cmd = [sys.executable, "-m", "src.agents.mops.misc.extraction_agent_mcp_test", "--file", doi_hash]
                            subprocess.run(cmd, cwd=os.getcwd(), capture_output=False, check=True)
                            # Run iter2_1 enrichment to add CCDC info
                            cmd_enrich = [sys.executable, "-m", "src.agents.mops.misc.extraction_agent_mcp_test", "--iter2_1", "--file", doi_hash]
                            subprocess.run(cmd_enrich, cwd=os.getcwd(), capture_output=False, check=True)
                            # Copy generated iter2 hints into test_dir with numbered suffix
                            mcp_dir = os.path.join(directory, "mcp_run")
                            if os.path.isdir(mcp_dir):
                                for fn in os.listdir(mcp_dir):
                                    if fn.startswith("iter2_hints_") and fn.endswith(".txt"):
                                        src = os.path.join(mcp_dir, fn)
                                        # fn pattern: iter2_hints_<safe_name>.txt
                                        name = fn.replace("iter2_hints_", "", 1)[:-4]
                                        dst = os.path.join(test_dir, f"iter2_hints_{name}_{i}.txt")
                                        try:
                                            shutil.copy2(src, dst)
                                            print(f"✅ Saved {dst}")
                                        except Exception as e:
                                            print(f"⚠️  Failed to save test iter2 hints: {e}")
                    else:
                        # Use the MCP test driver to run iter2 hint creation
                        cmd = [sys.executable, "-m", "src.agents.mops.misc.extraction_agent_mcp_test", "--file", doi_hash]
                        subprocess.run(cmd, cwd=os.getcwd(), capture_output=False, check=True)
                        # Then enrich with iter2_1 (CCDC)
                        cmd_enrich = [sys.executable, "-m", "src.agents.mops.misc.extraction_agent_mcp_test", "--iter2_1", "--file", doi_hash]
                        subprocess.run(cmd_enrich, cwd=os.getcwd(), capture_output=False, check=True)
                elif iter_num >= 3:
                    # For iter3+, use run_task_hints_only directly (doesn't require iter2 to be complete)
                    asyncio.run(run_task_hints_only(doi_hash, start_iter=iter_num, end_iter=iter_num))
                else:
                    # iter_num == 1
                    asyncio.run(run_task_iter1_only(doi_hash, test=test_mode))

                # Check if iteration N hints were created successfully
                if iter_num == 1:
                    # For iter1, check single hints file
                    iter_hints_path = os.path.join(directory, "mcp_run", f"iter{iter_num}_hints.txt")
                    if os.path.exists(iter_hints_path):
                        print(f"✅ {doi_hash}: iter{iter_num}_hints.txt created successfully")
                    else:
                        print(f"⚠️  {doi_hash}: iter{iter_num}_hints.txt was not created")
                        overall_success = False
                else:
                    # For iter2+, check entity-specific hints files
                    iter1_entities_file = os.path.join(directory, "mcp_run", "iter1_top_entities.json")
                    try:
                        with open(iter1_entities_file, 'r') as f:
                            top_entities = json.load(f)
                        
                        created_count = 0
                        for entity in top_entities:
                            label = entity.get("label", "")
                            safe_name = label.replace(" ", "_").replace("/", "_").replace("\\", "_").replace(":", "_").replace("?", "_").replace("*", "_").replace("<", "_").replace(">", "_").replace("|", "_").replace('"', "_").replace("'", "_")
                            hint_file = os.path.join(directory, "mcp_run", f"iter{iter_num}_hints_{safe_name}.txt")
                            if os.path.exists(hint_file):
                                created_count += 1
                        
                        if created_count == len(top_entities):
                            print(f"✅ {doi_hash}: iter{iter_num} hints created successfully for all {len(top_entities)} entities")
                        else:
                            print(f"⚠️  {doi_hash}: iter{iter_num} hints created for {created_count}/{len(top_entities)} entities")
                            overall_success = False
                    except Exception as e:
                        print(f"⚠️  {doi_hash}: Error checking iter{iter_num} hints: {e}")
                        overall_success = False
                    
            except Exception as e:
                print(f"❌ {doi_hash}: Failed to run dynamic MCP extraction: {e}")
                overall_success = False
            
        except Exception as e:
            print(f"❌ Processing failed for {doi_hash}: {e}")
            overall_success = False

    if overall_success:
        print(f"\n🎉 iter{iter_num} extraction completed successfully for all DOIs!")
    else:
        print(f"\n⚠️  iter{iter_num} extraction completed with some failures. Check the logs above.")
    
    return overall_success

# -------------------- Config-driven pipeline runner --------------------
def run_pipeline_from_config(config_path: str, test_mode: bool = False, input_dir: str | None = None, cache_enabled: bool = False, only_hash: str | None = None) -> bool:
    """Run the pipeline according to a JSON configuration file.

    Config format example (configs/pipeline.json):
    {
      "mode": "per_doi",
      "steps": [
        "pdf_conversion",
        "section_classification",
        "stitching",
        "ontosynthesis_pre_extractions",
        "extractions",
        "ontosynthesis_kg_building",
        "ontomops_extension",
        "ontospecies_extension",
        "cbu_derivation"
      ]
    }
    """
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            cfg = json.load(f)
    except Exception as e:
        print(f"Failed to read config: {config_path}: {e}")
        return False

    mode = str(cfg.get("mode", "per_doi") or "per_doi").lower()
    steps = cfg.get("steps") or []
    if not isinstance(steps, list) or not steps:
        print("Config has no steps to run")
        return False

    doi_mapping_path = 'data/doi_to_hash.json'
    if not os.path.exists(doi_mapping_path):
        print(f"DOI mapping file not found: {doi_mapping_path}")
        return False
    try:
        with open(doi_mapping_path, 'r', encoding='utf-8') as f:
            doi_mapping = json.load(f)
    except Exception as e:
        print(f"Failed to read DOI mapping: {e}")
        return False
    if not doi_mapping:
        print("DOI mapping file is empty. No DOIs to process.")
        return True

    # Helper to find original DOI string for a given hash
    def _original_doi_for_hash(hv: str) -> str | None:
        for doi, h in doi_mapping.items():
            if h == hv:
                return doi
        return None

    if only_hash:
        doi_hashes = [only_hash]
    else:
        doi_hashes = list(doi_mapping.values())

    base_dir = input_dir or RAW_DATA_DIR
    overall_success = True

    def _ensure_onto_tbox_present(doi_hash: str):
        # If a global ontosynthesis.ttl exists, mirror it to the DOI directory for local discovery
        doi_dir = os.path.join('data', doi_hash)
        src_tbox = os.path.join('data', 'ontologies', 'ontosynthesis.ttl')
        dst_tbox = os.path.join(doi_dir, 'ontosynthesis.ttl')
        try:
            if os.path.exists(src_tbox) and not os.path.exists(dst_tbox):
                os.makedirs(doi_dir, exist_ok=True)
                shutil.copy2(src_tbox, dst_tbox)
                print(f"{doi_hash}: Copied ontosynthesis.ttl into DOI directory")
        except Exception:
            pass

    def _step_pdf_conversion(doi_hash: str, original_doi: str) -> bool:
        directory = os.path.join('data', doi_hash)
        os.makedirs(directory, exist_ok=True)
        pdf_path = os.path.join(base_dir, f"{original_doi}.pdf")
        si_pdf_path = os.path.join(base_dir, f"{original_doi}_si.pdf")
        hash_pdf_path = os.path.join(directory, f"{doi_hash}.pdf")
        hash_si_pdf_path = os.path.join(directory, f"{doi_hash}_si.pdf")
        if not os.path.exists(pdf_path) and not os.path.exists(hash_pdf_path):
            print(f"PDF not found for {doi_hash}: {pdf_path}")
            return False
        try:
            if os.path.exists(pdf_path) and not os.path.exists(hash_pdf_path):
                shutil.copy2(pdf_path, hash_pdf_path)
                print(f"Copied PDF to: {hash_pdf_path}")
            if os.path.exists(si_pdf_path) and not os.path.exists(hash_si_pdf_path):
                shutil.copy2(si_pdf_path, hash_si_pdf_path)
                print(f"Copied SI PDF to: {hash_si_pdf_path}")
            hash_md_path = os.path.join(directory, f"{doi_hash}.md")
            hash_si_md_path = os.path.join(directory, f"{doi_hash}_si.md")
            if os.path.exists(hash_md_path) and os.path.exists(hash_si_md_path):
                print(f"⏭️  {doi_hash}: Skip PDF conversion (markdown exists)")
                return True
            ok = convert_doi_pdfs(doi_hash)
            if ok:
                print(f"✅ {doi_hash}: PDF conversion completed")
            return ok
        except Exception as e:
            print(f"{doi_hash}: PDF conversion error: {e}")
            return False

    def _step_section_classification(doi_hash: str) -> bool:
        directory = os.path.join('data', doi_hash)
        stitched_path = os.path.join(directory, f"{doi_hash}_stitched.md")
        if os.path.exists(stitched_path):
            print(f"⏭️  {doi_hash}: Skip division (stitched exists)")
            return True
        ok = run_division_and_classify(doi_hash)
        if ok:
            print(f"✅ {doi_hash}: Division and classification completed")
        return ok

    def _step_stitching(doi_hash: str) -> bool:
        # Validate stitched file exists; if not, try division
        directory = os.path.join('data', doi_hash)
        stitched_path = os.path.join(directory, f"{doi_hash}_stitched.md")
        if os.path.exists(stitched_path):
            print(f"⏭️  {doi_hash}: Skip stitching (exists)")
            return True
        return _step_section_classification(doi_hash)

    def _step_ontosynthesis_pre_extractions(doi_hash: str) -> bool:
        directory = os.path.join('data', doi_hash)
        mcp_dir = os.path.join(directory, "mcp_run")
        entities_path = os.path.join(mcp_dir, "iter1_top_entities.json")
        iter1_required_files = [
            os.path.join(mcp_dir, "iter1_hints.txt"),
            os.path.join(directory, "output_top.ttl"),
            os.path.join(directory, "iteration_1.ttl"),
            entities_path,
        ]
        missing_iter1 = [f for f in iter1_required_files if not os.path.exists(f)]
        if missing_iter1:
            print(f"▶️  {doi_hash}: Preparing iteration 1 (missing: {[os.path.basename(f) for f in missing_iter1]})...")
            try:
                asyncio.run(run_task(doi_hash, test=test_mode))
            except Exception as e:
                print(f"❌ {doi_hash}: Iteration 1 failed: {e}")
                return False
        else:
            print(f"⏭️  {doi_hash}: Skip iteration 1 (already complete)")

        # iter2 hints
        try:
            need_iter2 = False
            if os.path.exists(entities_path):
                with open(entities_path, 'r', encoding='utf-8') as f:
                    entities = json.load(f)
                for entity in entities:
                    label = entity.get("label", "")
                    safe_label = (label or "entity").replace(" ", "_").replace("/", "_").replace("\\", "_").replace(":", "_")
                    iter2_hints = os.path.join(mcp_dir, f"iter2_hints_{safe_label}.txt")
                    if not os.path.exists(iter2_hints):
                        need_iter2 = True
                        break
            if need_iter2:
                print(f"▶️  {doi_hash}: Generating iter2 hints...")
                cmd = [sys.executable, "-m", "src.agents.mops.misc.extraction_agent_mcp_test", "--file", doi_hash]
                subprocess.run(cmd, cwd=os.getcwd(), capture_output=False, check=True)
            else:
                print(f"⏭️  {doi_hash}: Skip iter2 hints (already complete)")
        except subprocess.CalledProcessError as e:
            print(f"⚠️  {doi_hash}: Iter2 hints generation failed: {e}")
            return False

        # iter2_1 enrichment (CCDC)
        try:
            # if any iter2 hints missing, iter2_1 will be run anyway after hints
            print(f"▶️  {doi_hash}: Running iter2_1 (CCDC enrichment)...")
            cmd = [sys.executable, "-m", "src.agents.mops.misc.extraction_agent_mcp_test", "--file", doi_hash, "--iter2_1"]
            subprocess.run(cmd, cwd=os.getcwd(), capture_output=False, check=True)
            return True
        except subprocess.CalledProcessError as e:
            print(f"⚠️  {doi_hash}: iter2_1 enrichment failed: {e}")
            return False

    def _step_extractions(doi_hash: str) -> bool:
        directory = os.path.join('data', doi_hash)
        out_ttl = os.path.join(directory, "output.ttl")
        if os.path.exists(out_ttl):
            print(f"⏭️  {doi_hash}: Skip extraction (output.ttl exists)")
            return True
        if _try_restore_extraction_cache(doi_hash, cache_enabled):
            print(f"✅ {doi_hash}: Restored extraction outputs from cache")
            return True
        try:
            asyncio.run(run_task(doi_hash, test=test_mode))
            print(f"✅ {doi_hash}: Extraction completed")
            _save_extraction_cache(doi_hash, cache_enabled)
            return True
        except Exception as e:
            print(f"❌ {doi_hash}: Extraction failed: {e}")
            return False

    def _step_ontosynthesis_kg_building(doi_hash: str) -> bool:
        # Ensure local ontosynthesis.tbox is present for downstream agents; treat KG building as satisfied by output.ttl
        _ensure_onto_tbox_present(doi_hash)
        directory = os.path.join('data', doi_hash)
        out_ttl = os.path.join(directory, "output.ttl")
        if os.path.exists(out_ttl):
            print(f"✅ {doi_hash}: Ontosynthesis KG available (output.ttl present)")
            return True
        print(f"⚠️  {doi_hash}: KG not built yet (output.ttl missing); run 'extractions' first")
        return False

    def _step_ontomops_extension(doi_hash: str) -> bool:
        directory = os.path.join('data', doi_hash)
        ontomops_out_dir = os.path.join(directory, "ontomops_output")
        ontomops_done = os.path.isdir(ontomops_out_dir) and any(name for name in os.listdir(ontomops_out_dir) if name.endswith(".ttl"))
        if ontomops_done:
            print(f"⏭️  {doi_hash}: Skip OntoMOPs extension (already populated)")
            return True
        try:
            cmd = [sys.executable, "-m", "src.agents.mops.extension.ontomop-extension-agent", "--file", doi_hash]
            subprocess.run(cmd, cwd=os.getcwd(), capture_output=False, check=True)
            print(f"✅ {doi_hash}: OntoMOPs extension completed")
            return True
        except subprocess.CalledProcessError as e:
            print(f"❌ {doi_hash}: OntoMOPs extension failed: {e}")
            return False

    def _step_ontospecies_extension(doi_hash: str) -> bool:
        directory = os.path.join('data', doi_hash)
        ontospecies_out_dir = os.path.join(directory, "ontospecies_output")
        ontospecies_done = os.path.isdir(ontospecies_out_dir) and any(name for name in os.listdir(ontospecies_out_dir) if name.endswith(".ttl"))
        if ontospecies_done:
            print(f"⏭️  {doi_hash}: Skip OntoSpecies extension (already populated)")
            return True
        max_retries = 3
        retry_delay = 10
        cmd = [sys.executable, "-m", "src.agents.mops.extension.ontospecies-extension-agent", "--file", doi_hash]
        for attempt in range(1, max_retries + 1):
            try:
                print(f"🔄 {doi_hash}: OntoSpecies extension attempt {attempt}/{max_retries}...")
                subprocess.run(cmd, cwd=os.getcwd(), capture_output=False, check=True)
                print(f"✅ {doi_hash}: OntoSpecies extension completed")
                return True
            except subprocess.CalledProcessError as e:
                if attempt < max_retries:
                    print(f"⚠️  {doi_hash}: Attempt {attempt} failed: {e}")
                    print(f"⏳ Retrying in {retry_delay} seconds...")
                    time.sleep(retry_delay)
                else:
                    print(f"❌ {doi_hash}: OntoSpecies extension failed after {max_retries} attempts: {e}")
                    return False

    async def _run_module(module: str, hv: str) -> bool:
        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable, "-m", module, "--file", hv,
                cwd=os.getcwd()
            )
            rc = await proc.wait()
            return rc == 0
        except Exception:
            return False

    def _step_cbu_derivation(doi_hash: str) -> bool:
        async def _pair(hv: str) -> tuple[bool, bool]:
            metal = _run_module("src.agents.mops.cbu_derivation.metal_cbu_derivation_agent", hv)
            organic = _run_module("src.agents.mops.cbu_derivation.organic_cbu_derivation_agent", hv)
            return await asyncio.gather(metal, organic)
        m_ok, o_ok = asyncio.run(_pair(doi_hash))
        if m_ok:
            print(f"{doi_hash}: ✅ Metal CBU derivation completed")
        else:
            print(f"{doi_hash}: ❌ Metal CBU derivation failed")
        if o_ok:
            print(f"{doi_hash}: ✅ Organic CBU derivation completed")
        else:
            print(f"{doi_hash}: ❌ Organic CBU derivation failed")
        return m_ok and o_ok

    step_handlers = {
        "pdf_conversion": _step_pdf_conversion,
        "section_classification": lambda hv, _doi=None: _step_section_classification(hv),
        "stitching": lambda hv, _doi=None: _step_stitching(hv),
        "ontosynthesis_pre_extractions": lambda hv, _doi=None: _step_ontosynthesis_pre_extractions(hv),
        "extractions": lambda hv, _doi=None: _step_extractions(hv),
        "ontosynthesis_kg_building": lambda hv, _doi=None: _step_ontosynthesis_kg_building(hv),
        "ontomops_extension": lambda hv, _doi=None: _step_ontomops_extension(hv),
        "ontospecies_extension": lambda hv, _doi=None: _step_ontospecies_extension(hv),
        "cbu_derivation": lambda hv, _doi=None: _step_cbu_derivation(hv),
    }

    print("\n=== Running pipeline from config ===")
    print(f"Mode: {mode}")
    print(f"Steps: {steps}")

    if mode == "per_doi":
        for doi_hash in doi_hashes:
            original_doi = _original_doi_for_hash(doi_hash)
            if not original_doi:
                print(f"No DOI found for hash: {doi_hash}")
                overall_success = False
                continue
            print(f"\n--- DOI {doi_hash} ---")
            # Ensure T-box presence ahead of runs if available
            _ensure_onto_tbox_present(doi_hash)
            for step in steps:
                handler = step_handlers.get(step)
                if not handler:
                    print(f"⚠️  {doi_hash}: Unknown step '{step}' in config — skipping")
                    continue
                print(f"{doi_hash}: ▶️  Step '{step}'")
                ok = False
                try:
                    if step == "pdf_conversion":
                        ok = handler(doi_hash, original_doi)
                    else:
                        ok = handler(doi_hash, original_doi)
                except Exception as e:
                    print(f"{doi_hash}: Step '{step}' failed with exception: {e}")
                    ok = False
                if not ok:
                    print(f"{doi_hash}: ❌ Step '{step}' failed")
                    overall_success = False
                    # Continue to next DOI rather than aborting entire run
                    break
        return overall_success

    # Fallback: treat any other mode as simple per_doi
    print("Unknown/unsupported mode; defaulting to per_doi execution")
    return run_pipeline_from_config(config_path, test_mode=test_mode, input_dir=input_dir, cache_enabled=cache_enabled, only_hash=only_hash)