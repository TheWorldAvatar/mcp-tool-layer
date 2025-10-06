"""
Pipeline utilities for MOPs Extraction

This module contains the core pipeline functions for processing DOI files
through the complete MOPs extraction workflow.
"""

import os
import sys
from scripts.pdf_to_markdown import convert_doi_pdfs, convert_all_dois
from src.utils.division_wrapper import run_division_and_classify, run_division_and_classify_all

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
