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
from scripts.pdf_to_markdown import convert_doi_pdfs, convert_all_dois
from src.utils.division_wrapper import run_division_and_classify, run_division_and_classify_all
from models.locations import RAW_DATA_DIR
from src.agents.mops.dynamic_mcp.mcp_run_agent_hint_only_dynamic import run_task

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

def run_pipeline_for_hash(doi_hash, test_mode=False):
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
    
    # Check if PDFs exist in raw_data
    pdf_path = os.path.join(RAW_DATA_DIR, f"{original_doi}.pdf")
    si_pdf_path = os.path.join(RAW_DATA_DIR, f"{original_doi}_si.pdf")
    
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
    
    # Step 3: Dynamic MCP-based MOPs extraction
    print("Step 3: Running dynamic MCP-based MOPs extraction...")
    try:
        # Run the dynamic MCP agent
        asyncio.run(run_task(doi_hash, test=test_mode))
        print("✅ Dynamic MCP-based MOPs extraction completed successfully")
    except Exception as e:
        print(f"Failed to run dynamic MCP agent for hash: {doi_hash}")
        print(f"Error: {e}")
        return False

    # Step 4: OntoMOPs extension (extract per-entity hints then extend A-Box)
    print("Step 4: Running OntoMOPs extension agent...")
    try:
        # Always run against the specific hash to keep consistency with current pipeline run
        cmd = [sys.executable, "-m", "src.agents.mops.extension.ontomop-extension-agent", "--file", doi_hash]
        completed = subprocess.run(cmd, cwd=os.getcwd(), capture_output=False, check=True)
        print("✅ OntoMOPs extension completed")
    except subprocess.CalledProcessError as e:
        print(f"Failed to run OntoMOPs extension for hash: {doi_hash}")
        print(f"Error: {e}")
        return False

    # Step 5: OntoSpecies extension (extract per-entity hints then extend A-Box)
    print("Step 5: Running OntoSpecies extension agent...")
    try:
        cmd = [sys.executable, "-m", "src.agents.mops.extension.ontospecies-extension-agent", "--file", doi_hash]
        completed = subprocess.run(cmd, cwd=os.getcwd(), capture_output=False, check=True)
        print("✅ OntoSpecies extension completed")
    except subprocess.CalledProcessError as e:
        print(f"Failed to run OntoSpecies extension for hash: {doi_hash}")
        print(f"Error: {e}")
        return False
    
    print(f"Pipeline completed for DOI hash: {doi_hash}")
    return True

def run_pipeline_for_all_hashes(test_mode=False):
    """Run the pipeline for all DOI hashes."""
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

    success = True
    for doi_hash in doi_mapping.values():
        if not run_pipeline_for_hash(doi_hash, test_mode=test_mode):
            success = False

    return success