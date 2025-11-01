#!/usr/bin/env python3
"""
Detailed analysis script to compare iter1_top_entities.json with ground truth steps files.
Shows actual entity names and procedure names for detailed comparison.
"""

import os
import json
from pathlib import Path


def load_doi_mapping():
    """Load the DOI to hash mapping."""
    mapping_path = 'data/doi_to_hash.json'
    if not os.path.exists(mapping_path):
        print(f"Error: DOI mapping file not found: {mapping_path}")
        return {}
    
    with open(mapping_path, 'r') as f:
        doi_mapping = json.load(f)
    
    # Create reverse mapping (hash -> doi)
    hash_to_doi = {v: k for k, v in doi_mapping.items()}
    return hash_to_doi


def find_steps_file(doi):
    """Find the corresponding steps JSON file for a given DOI."""
    filename = doi + ".json"
    
    # Look in earlier_ground_truth/steps/
    steps_dir = Path("earlier_ground_truth/steps")
    if steps_dir.exists():
        steps_file = steps_dir / filename
        if steps_file.exists():
            return steps_file
    
    return None


def get_iter1_entities(hash_dir):
    """Get the entities from iter1_top_entities.json."""
    iter1_entities_file = hash_dir / "mcp_run" / "iter1_top_entities.json"
    
    if not iter1_entities_file.exists():
        return []
    
    try:
        with open(iter1_entities_file, 'r', encoding='utf-8') as f:
            entities = json.load(f)
        
        if isinstance(entities, list):
            return entities
        else:
            return []
    except Exception as e:
        print(f"Error reading {iter1_entities_file}: {e}")
        return []


def get_gt_procedures(steps_file):
    """Get the synthesis procedures from a steps JSON file."""
    try:
        with open(steps_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        if "Synthesis" in data and isinstance(data["Synthesis"], list):
            return data["Synthesis"]
        else:
            return []
    except Exception as e:
        print(f"Error reading {steps_file}: {e}")
        return []


def detailed_analysis():
    """Perform detailed analysis of synthesis entities vs ground truth."""
    data_dir = Path("data")
    hash_to_doi = load_doi_mapping()
    
    if not hash_to_doi:
        print("No DOI mapping found. Exiting.")
        return
    
    print("Detailed Synthesis Analysis")
    print("=" * 100)
    
    # Get all hash folders
    hash_folders = [item for item in data_dir.iterdir() if item.is_dir() and not item.name.startswith('.')]
    hash_folders = sorted(hash_folders, key=lambda x: x.name)
    
    for hash_folder in hash_folders:
        hash_name = hash_folder.name
        
        # Skip if not a valid hash
        if hash_name not in hash_to_doi:
            continue
        
        doi = hash_to_doi[hash_name]
        
        # Get iter1 entities
        entities = get_iter1_entities(hash_folder)
        
        # Get ground truth procedures
        steps_file = find_steps_file(doi)
        procedures = []
        if steps_file:
            procedures = get_gt_procedures(steps_file)
        
        print(f"\n{hash_name} ({doi})")
        print("-" * 80)
        print(f"Iter1 Entities ({len(entities)}):")
        for i, entity in enumerate(entities, 1):
            label = entity.get('label', 'No label')
            # Handle Unicode characters safely
            try:
                print(f"  {i}. {label}")
            except UnicodeEncodeError:
                print(f"  {i}. {label.encode('ascii', 'replace').decode('ascii')}")
        
        print(f"\nGround Truth Procedures ({len(procedures)}):")
        for i, procedure in enumerate(procedures, 1):
            product_names = procedure.get('productNames', ['Unknown'])
            product_name = product_names[0] if product_names else 'Unknown'
            # Handle Unicode characters safely
            try:
                print(f"  {i}. {product_name}")
            except UnicodeEncodeError:
                print(f"  {i}. {product_name.encode('ascii', 'replace').decode('ascii')}")
        
        # Analysis
        if len(entities) == len(procedures):
            print(f"\nStatus: PERFECT MATCH ({len(entities)} entities = {len(procedures)} procedures)")
        elif len(entities) > len(procedures):
            print(f"\nStatus: OVER-EXTRACTED (+{len(entities) - len(procedures)} extra entities)")
        else:
            print(f"\nStatus: UNDER-EXTRACTED ({len(entities) - len(procedures)} missing entities)")
        
        print("=" * 100)


if __name__ == "__main__":
    detailed_analysis()
