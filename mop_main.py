"""
MOPs Extraction Pipeline

Default run will process all DOI folders in the data directory.
--test mode will only run the test DOI and complete the full pipeline.
--file <doi> mode will only run the specified DOI and complete the full pipeline.

# Full Pipeline

1. Conversion from PDF formats to markdown formats
   - input: data/<hash>/<hash>.pdf + data/<hash>/<hash>_si.pdf  
   - output: data/<hash>/<hash>.md + data/<hash>/<hash>_si.md
   - Script used: scripts/pdf_to_markdown.py

2. Division and classification of markdown sections
   - input: data/<hash>/<hash>.md + data/<hash>/<hash>_si.md
   - output: data/<hash>/sections.json + data/<hash>/<hash>_stitched.md
   - Agent: src/agents/division_and_classify_agent.py + MCP server: src/mcp_servers/document/main.py

3. Dynamic MCP-based MOPs extraction
   - input: data/<hash>/<hash>_stitched.md
   - output: data/<hash>/mcp_run/ (hints and instructions) + data/<hash>/output.ttl
   - Agent: src/agents/mops/dynamic_mcp/mcp_run_agent_hint_only_dynamic.py

# Skip Mechanism
- Step 1 is skipped if <hash>.md and <hash>_si.md already exist
- Step 2 is skipped if <hash>_stitched.md already exists
- Step 3 runs the dynamic MCP agent (no skip mechanism built-in)
"""

import argparse
import sys
import os
import json
import hashlib
from src.utils.pipeline import run_pipeline_for_hash, run_pipeline_for_all_hashes
from models.locations import RAW_DATA_DIR

TEST_FILE_DOI = "10.1021.acs.chemmater.0c01965"

def generate_hash(doi):
    """Generate an 8-digit hash from the DOI."""
    return hashlib.sha256(doi.encode()).hexdigest()[:8]

def create_doi_mapping(doi, doi_hash):
    """Create or update the DOI to hash mapping file."""
    mapping_path = os.path.join('data', 'doi_to_hash.json')
    
    # Load existing mapping or create new one
    if os.path.exists(mapping_path):
        with open(mapping_path, 'r') as f:
            mapping = json.load(f)
    else:
        mapping = {}
    
    # Add the new mapping
    mapping[doi] = doi_hash
    
    # Save the updated mapping
    os.makedirs('data', exist_ok=True)
    with open(mapping_path, 'w') as f:
        json.dump(mapping, f, indent=2)
    
    print(f"Updated DOI mapping: {doi} -> {doi_hash}")

def discover_and_create_mappings():
    """Discover all DOIs from raw_data directory and create mappings."""
    if not os.path.exists(RAW_DATA_DIR):
        print(f"Raw data directory not found: {RAW_DATA_DIR}")
        return False
    
    # Get all PDF files in raw_data directory (deterministic order)
    pdf_files = sorted(
        [f for f in os.listdir(RAW_DATA_DIR)
         if f.endswith('.pdf') and not f.endswith('_si.pdf')],
        key=lambda s: s.lower()
    )
    
    if not pdf_files:
        print(f"No PDF files found in {RAW_DATA_DIR}")
        return False
    
    print(f"Found {len(pdf_files)} DOI PDF files in raw_data directory")
    print("Creating DOI to hash mappings...")
    
    # Extract DOIs from PDF filenames and create mappings
    for pdf_file in pdf_files:
        # Remove .pdf extension to get DOI
        doi = pdf_file[:-4]  # Remove '.pdf'
        doi_hash = generate_hash(doi)
        create_doi_mapping(doi, doi_hash)
    
    print(f"Successfully created mappings for {len(pdf_files)} DOIs")
    return True

def main():
    """Main function with command line argument handling."""
    parser = argparse.ArgumentParser(description='MOPs Extraction Pipeline')
    parser.add_argument('--test', action='store_true', 
                       help='Run pipeline for test DOI only')
    parser.add_argument('--file', type=str, 
                       help='Run pipeline for specific DOI')
    
    args = parser.parse_args()
    
    if args.test:
        doi_hash = generate_hash(TEST_FILE_DOI)
        create_doi_mapping(TEST_FILE_DOI, doi_hash)
        print(f"Running in test mode with DOI hash: {doi_hash}")
        success = run_pipeline_for_hash(doi_hash, test_mode=True)
    elif args.file:
        doi_hash = generate_hash(args.file)
        create_doi_mapping(args.file, doi_hash)
        print(f"Running pipeline for specified DOI hash: {doi_hash}")
        success = run_pipeline_for_hash(doi_hash, test_mode=False)
    else:
        print("Running pipeline for all DOIs")
        # First, discover DOIs and create mappings
        if not discover_and_create_mappings():
            print("\n❌ Failed to discover DOIs and create mappings!")
            sys.exit(1)
        # Then run the pipeline for all hashes
        success = run_pipeline_for_all_hashes(test_mode=False)
    
    if success:
        print("\n🎉 Pipeline execution completed successfully!")
        sys.exit(0)
    else:
        print("\n❌ Pipeline execution failed!")
        sys.exit(1)

if __name__ == "__main__":
    main()



