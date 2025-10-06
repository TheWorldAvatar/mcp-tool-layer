"""
Wrapper for the division and classify agent to make it compatible with the pipeline.

This module provides synchronous wrapper functions for the async division and classify agent.
"""

import asyncio
import os
from src.agents.division_and_classify_agent import classify_sections_agent
from models.locations import DATA_DIR


def run_division_and_classify(doi: str):
    """
    Run the division and classify agent for a specific DOI.
    
    Args:
        doi (str): The DOI identifier
        
    Returns:
        bool: True if successful, False otherwise
    """
    try:
        print(f"Running division and classification for DOI: {doi}")
        
        # Check if markdown file exists in data directory
        md_file_path = os.path.join(DATA_DIR, doi, f"{doi}.md")
        if not os.path.exists(md_file_path):
            print(f"Markdown file not found in data directory: {md_file_path}")
            return False
        
        # Run the async agent
        result = asyncio.run(classify_sections_agent(doi))
        
        if result is not None:
            if result == "Section JSON file already exists, skipping division and classification":
                print(f"Section JSON file already exists, skipping division and classification for DOI: {doi}")
                return True
            else:
                print(f"Division and classification completed successfully for DOI: {doi}")
                return True
        else:
            print(f"Division and classification failed for DOI: {doi}")
            return False
            
    except Exception as e:
        print(f"Error running division and classification for DOI {doi}: {e}")
        return False


def run_division_and_classify_all():
    """
    Run the division and classify agent for all DOIs in the playground directory.
    
    Returns:
        bool: True if successful, False otherwise
    """
    try:
        print("Running division and classification for all DOIs")
        
        # Get all DOI folders in data directory
        if not os.path.exists(DATA_DIR):
            print(f"Data directory not found: {DATA_DIR}")
            return False
        
        # Skip log directory and other non-DOI directories
        excluded_dirs = {'log', '__pycache__', '.git', '.vscode', 'node_modules'}
        
        doi_folders = [d for d in os.listdir(DATA_DIR) 
                      if os.path.isdir(os.path.join(DATA_DIR, d)) 
                      and not d.startswith('.')
                      and d not in excluded_dirs]
        
        if not doi_folders:
            print("No DOI folders found in data directory")
            return False
        
        print(f"Found {len(doi_folders)} DOI folders to process")
        
        success_count = 0
        for doi in doi_folders:
            if run_division_and_classify(doi):
                success_count += 1
        
        print(f"Successfully processed {success_count}/{len(doi_folders)} DOIs")
        return success_count > 0
        
    except Exception as e:
        print(f"Error running division and classification for all DOIs: {e}")
        return False
