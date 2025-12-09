"""DOI discovery and mapping utilities"""

import os
import json
import sys
from .hash import generate_hash


def load_doi_mapping(data_dir: str = "data") -> dict:
    """
    Load DOI to hash mapping from JSON file.
    
    Args:
        data_dir: Base data directory containing the mapping file
        
    Returns:
        Dictionary mapping DOI -> hash
        
    Raises:
        SystemExit: If mapping file not found or cannot be loaded
    """
    mapping_path = os.path.join(data_dir, 'doi_to_hash.json')
    
    if not os.path.exists(mapping_path):
        print(f"❌ DOI mapping not found: {mapping_path}")
        print("Please run discovery first or use mop_main.py to create mappings")
        sys.exit(1)
    
    try:
        with open(mapping_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"❌ Failed to load DOI mapping: {e}")
        sys.exit(1)


def discover_dois(input_dir: str, data_dir: str = "data") -> dict:
    """
    Discover DOIs from PDF files and create hash mapping.
    
    Args:
        input_dir: Directory containing PDF files
        data_dir: Data directory for storing mapping
        
    Returns:
        Dictionary mapping DOI -> hash
    """
    if not os.path.exists(input_dir):
        print(f"❌ Input directory not found: {input_dir}")
        return {}
    
    # Find all PDF files (excluding _si.pdf)
    pdf_files = sorted(
        [f for f in os.listdir(input_dir) 
         if f.endswith('.pdf') and not f.endswith('_si.pdf')],
        key=lambda s: s.lower()
    )
    
    if not pdf_files:
        print(f"❌ No PDF files found in {input_dir}")
        return {}
    
    print(f"📄 Found {len(pdf_files)} PDF files")
    
    # Create mapping
    mapping = {}
    for pdf_file in pdf_files:
        doi = pdf_file[:-4]  # Remove .pdf extension
        doi_hash = generate_hash(doi)
        mapping[doi] = doi_hash
        print(f"  {doi} -> {doi_hash}")
    
    # Save mapping
    os.makedirs(data_dir, exist_ok=True)
    mapping_path = os.path.join(data_dir, 'doi_to_hash.json')
    with open(mapping_path, 'w', encoding='utf-8') as f:
        json.dump(mapping, f, indent=2)
    
    print(f"✅ Saved mapping to {mapping_path}")
    return mapping

