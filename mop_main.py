"""
MOPs Extraction Pipeline

Default run will process all DOI folders in the data directory.
--test mode will only run the test DOI and complete the full pipeline.
--file <doi> mode will only run the specified DOI and complete the full pipeline.
--extraction-only mode will run the same extraction sequence as the regular pipeline for all hashes, stopping after iter4 hints and iter3_1 + iter3_2 enrichments are created (includes PDF conversion and division, stops before final TTL generation).
--iter1 mode will run the complete iteration 1 process (hints, TTL, JSON) for each hash, then proceed to next.
--iter2 mode will run until iter2_hints_<entity>.txt files are created for each hash, then proceed to next.
--iter3 mode will run until iter3_hints_<entity>.txt files are created (includes iter3_1 and iter3_2 enrichments) for each hash, then proceed to next.
--iter4 mode will run until iter4_hints_<entity>.txt files are created for each hash, then proceed to next.

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
from datetime import datetime
from src.utils.pipeline import run_pipeline_for_hash, run_pipeline_for_all_hashes, run_extraction_only_for_all_hashes, run_iter1_for_all_hashes, run_iter2_for_all_hashes, run_iter3_for_all_hashes, run_iter4_for_all_hashes, run_pipeline_from_config
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

def discover_and_create_mappings(input_dir: str | None = None):
    """Discover all DOIs from an input directory and create mappings.
    If input_dir is None, defaults to RAW_DATA_DIR.
    """
    base_dir = input_dir or RAW_DATA_DIR
    if not os.path.exists(base_dir):
        print(f"Raw data directory not found: {base_dir}")
        return False
    
    # Get all PDF files in raw_data directory (deterministic order)
    try:
        entries = os.listdir(base_dir)
    except Exception as e:
        print(f"Failed to list directory {base_dir}: {e}")
        return False
    pdf_files = sorted(
        [f for f in entries if f.endswith('.pdf') and not f.endswith('_si.pdf')],
        key=lambda s: s.lower()
    )
    
    if not pdf_files:
        print(f"No PDF files found in {base_dir}")
        return False
    
    print(f"Found {len(pdf_files)} DOI PDF files in {base_dir}")
    print("Creating DOI to hash mappings...")
    
    # Extract DOIs from PDF filenames and create mappings
    for pdf_file in pdf_files:
        # Remove .pdf extension to get DOI
        doi = pdf_file[:-4]  # Remove '.pdf'
        doi_hash = generate_hash(doi)
        create_doi_mapping(doi, doi_hash)
    
    print(f"Successfully created mappings for {len(pdf_files)} DOIs")
    return True

def create_pipeline_timing_file(run_mode: str, args_dict: dict) -> tuple[dict, str]:
    """Create initial pipeline timing JSON file at start of execution.
    Returns (timing_data, filepath) tuple."""
    # Create data directory if it doesn't exist
    os.makedirs('data', exist_ok=True)
    
    # Capture start time
    start_dt = datetime.now()
    start_time_str = start_dt.strftime('%Y-%m-%d %H:%M:%S')
    
    # Generate filename with timestamp
    timestamp = start_time_str.replace(':', '-').replace(' ', '_')
    filename = f"pipeline_run_{timestamp}.json"
    filepath = os.path.join('data', filename)
    
    # Create initial timing data
    timing_data = {
        "run_mode": run_mode,
        "start_time": start_time_str,
        "start_timestamp": start_dt.timestamp(),
        "end_time": None,
        "end_timestamp": None,
        "total_duration_seconds": None,
        "total_duration_formatted": None,
        "status": "running",
        "arguments": args_dict,
        "success": None
    }
    
    # Save initial timing data
    with open(filepath, 'w') as f:
        json.dump(timing_data, f, indent=2)
    
    print(f"\n📊 Pipeline timing file created: {filepath}")
    print(f"⏱️  Start time: {start_time_str}\n")
    
    return timing_data, filepath

def update_pipeline_timing_file(filepath: str, timing_data: dict, success: bool):
    """Update pipeline timing JSON file at end of execution."""
    # Capture end time
    end_dt = datetime.now()
    end_time_str = end_dt.strftime('%Y-%m-%d %H:%M:%S')
    
    # Calculate duration
    start_dt = datetime.fromtimestamp(timing_data['start_timestamp'])
    duration = end_dt - start_dt
    duration_seconds = duration.total_seconds()
    
    # Format duration as human-readable string
    hours, remainder = divmod(int(duration_seconds), 3600)
    minutes, seconds = divmod(remainder, 60)
    duration_formatted = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    
    # Update timing data
    timing_data['end_time'] = end_time_str
    timing_data['end_timestamp'] = end_dt.timestamp()
    timing_data['total_duration_seconds'] = duration_seconds
    timing_data['total_duration_formatted'] = duration_formatted
    timing_data['status'] = 'completed' if success else 'failed'
    timing_data['success'] = success
    
    # Save updated timing data
    with open(filepath, 'w') as f:
        json.dump(timing_data, f, indent=2)
    
    print(f"\n📊 Pipeline timing file updated: {filepath}")
    print(f"⏱️  End time: {end_time_str}")
    print(f"⏱️  Total duration: {duration_formatted} ({duration_seconds:.2f} seconds)")
    
    return timing_data

def main():
    """Main function with command line argument handling."""
    parser = argparse.ArgumentParser(description='MOPs Extraction Pipeline')
    parser.add_argument('--test', action='store_true', 
                       help='Run pipeline for test DOI only')
    parser.add_argument('--file', type=str, 
                       help='Run pipeline for specific DOI')
    parser.add_argument('--extraction-only', action='store_true',
                       help='Run the same extraction sequence as the regular pipeline for all hashes, stopping after iter4 hints and iter3_1 + iter3_2 enrichments are created (includes PDF conversion and division, stops before final TTL generation)')
    parser.add_argument('--iter1', action='store_true',
                       help='Run the complete iteration 1 process (hints, TTL, JSON) for each hash, then proceed to next')
    parser.add_argument('--iter2', action='store_true',
                       help='Run until iter2_hints_<entity>.txt files are created for each hash, then proceed to next')
    parser.add_argument('--iter3', action='store_true',
                       help='Run until iter3_hints_<entity>.txt files are created (includes iter3_1 and iter3_2 enrichments) for each hash, then proceed to next')
    parser.add_argument('--iter4', action='store_true',
                       help='Run until iter4_hints_<entity>.txt files are created (yield extraction only) for each hash, then proceed to next')
    parser.add_argument('--input-dir', type=str,
                       help='Directory containing input DOI PDFs (defaults to RAW_DATA_DIR)')
    parser.add_argument('--cache', action='store_true',
                       help='Enable content-based caching for extraction and organic derivation')
    parser.add_argument('--num', dest='num', type=int,
                       help='For --iter1/--iter2 test runs: if N, perform N separate runs and write iter1_test_results/*_1..N or iter2_test_results/*_1..N files')
    parser.add_argument('--config', type=str,
                       help='Path to JSON config defining pipeline steps (overrides default flow)')
    
    args = parser.parse_args()
    
    # Determine run mode and prepare arguments dict
    run_mode = "full_pipeline"
    if args.test:
        run_mode = "test"
    elif args.iter1:
        run_mode = "iter1"
    elif args.iter2:
        run_mode = "iter2"
    elif args.iter3:
        run_mode = "iter3"
    elif args.iter4:
        run_mode = "iter4"
    elif args.file:
        run_mode = "single_file"
    elif args.extraction_only:
        run_mode = "extraction_only"
    elif args.config:
        run_mode = "config"
    
    args_dict = {
        "test": args.test,
        "file": args.file,
        "extraction_only": args.extraction_only,
        "iter1": args.iter1,
        "iter2": args.iter2,
        "iter3": args.iter3,
        "iter4": args.iter4,
        "input_dir": args.input_dir,
        "cache": args.cache,
        "num": args.num,
        "config": args.config
    }
    
    # Create timing file at start
    timing_data, timing_filepath = create_pipeline_timing_file(run_mode, args_dict)
    
    # Wrap execution in try-finally to ensure timing file is always updated
    success = False
    try:
        if args.config:
            print("Running pipeline from JSON config")
            # First, discover DOIs and create mappings
            if not discover_and_create_mappings(args.input_dir):
                print("\n❌ Failed to discover DOIs and create mappings!")
                sys.exit(1)
            success = run_pipeline_from_config(args.config, test_mode=args.test, input_dir=args.input_dir, cache_enabled=args.cache, only_hash=None)
        elif args.test:
            doi_hash = generate_hash(TEST_FILE_DOI)
            create_doi_mapping(TEST_FILE_DOI, doi_hash)
            print(f"Running in test mode with DOI hash: {doi_hash}")
            success = run_pipeline_for_hash(doi_hash, test_mode=True, cache_enabled=args.cache)
        elif args.iter1:
            print("Running until iter1_hints.txt is created for each hash")
            # First, discover DOIs and create mappings
            if not discover_and_create_mappings(args.input_dir):
                print("\n❌ Failed to discover DOIs and create mappings!")
                sys.exit(1)
            # Optional: restrict to a single hash via --file
            only_hash: str | None = None
            if args.file:
                file_arg = (args.file or "").strip()
                def _is_hash(s: str) -> bool:
                    if len(s) != 8:
                        return False
                    hexdigits = set("0123456789abcdefABCDEF")
                    return all(ch in hexdigits for ch in s)
                if _is_hash(file_arg):
                    only_hash = file_arg
                else:
                    only_hash = generate_hash(file_arg)
                    create_doi_mapping(file_arg, only_hash)
            # Force extraction model to gpt-4.1 for iter1
            os.environ["MOPS_EXTRACTION_MODEL"] = "gpt-4.1"
            success = run_iter1_for_all_hashes(test_mode=False, input_dir=args.input_dir, cache_enabled=args.cache, iter1_test_num=args.num, only_hash=only_hash, extraction_only=args.extraction_only)
        elif args.iter2:
            print("Running until iter2_hints.txt is created for each hash")
            # First, discover DOIs and create mappings
            if not discover_and_create_mappings(args.input_dir):
                print("\n❌ Failed to discover DOIs and create mappings!")
                sys.exit(1)
            # Optional: restrict to a single hash via --file
            only_hash: str | None = None
            if args.file:
                file_arg = (args.file or "").strip()
                def _is_hash(s: str) -> bool:
                    if len(s) != 8:
                        return False
                    hexdigits = set("0123456789abcdefABCDEF")
                    return all(ch in hexdigits for ch in s)
                if _is_hash(file_arg):
                    only_hash = file_arg
                else:
                    only_hash = generate_hash(file_arg)
                    create_doi_mapping(file_arg, only_hash)
            # Then run iter2 for all hashes
            # Force extraction model to gpt-4.1 for iter2
            os.environ["MOPS_EXTRACTION_MODEL"] = "gpt-4.1"
            success = run_iter2_for_all_hashes(test_mode=False, input_dir=args.input_dir, cache_enabled=args.cache, iter2_test_num=args.num, only_hash=only_hash, extraction_only=args.extraction_only)
        elif args.iter3:
            print("Running until iter3_hints.txt is created for each hash")
            # First, discover DOIs and create mappings
            if not discover_and_create_mappings(args.input_dir):
                print("\n❌ Failed to discover DOIs and create mappings!")
                sys.exit(1)
            # Optional: restrict to a single hash via --file
            only_hash: str | None = None
            if args.file:
                file_arg = (args.file or "").strip()
                def _is_hash(s: str) -> bool:
                    if len(s) != 8:
                        return False
                    hexdigits = set("0123456789abcdefABCDEF")
                    return all(ch in hexdigits for ch in s)
                if _is_hash(file_arg):
                    only_hash = file_arg
                else:
                    only_hash = generate_hash(file_arg)
                    create_doi_mapping(file_arg, only_hash)
            # Then run iter3 for all hashes
            success = run_iter3_for_all_hashes(test_mode=False, input_dir=args.input_dir, cache_enabled=args.cache, only_hash=only_hash, extraction_only=args.extraction_only)
        elif args.iter4:
            print("Running until iter4_hints.txt is created for each hash")
            # First, discover DOIs and create mappings
            if not discover_and_create_mappings(args.input_dir):
                print("\n❌ Failed to discover DOIs and create mappings!")
                sys.exit(1)
            # Optional: restrict to a single hash via --file
            only_hash: str | None = None
            if args.file:
                file_arg = (args.file or "").strip()
                def _is_hash(s: str) -> bool:
                    if len(s) != 8:
                        return False
                    hexdigits = set("0123456789abcdefABCDEF")
                    return all(ch in hexdigits for ch in s)
                if _is_hash(file_arg):
                    only_hash = file_arg
                else:
                    only_hash = generate_hash(file_arg)
                    create_doi_mapping(file_arg, only_hash)
            # Then run iter4 for all hashes
            success = run_iter4_for_all_hashes(test_mode=False, input_dir=args.input_dir, cache_enabled=args.cache, only_hash=only_hash, extraction_only=args.extraction_only)
        elif args.file:
            # Allow --file to accept either a DOI or an 8-char hash
            file_arg = (args.file or "").strip()
            def _is_hash(s: str) -> bool:
                if len(s) != 8:
                    return False
                hexdigits = set("0123456789abcdefABCDEF")
                return all(ch in hexdigits for ch in s)

            if _is_hash(file_arg):
                doi_hash = file_arg
                print(f"Running pipeline for specified DOI hash: {doi_hash}")
            else:
                doi_hash = generate_hash(file_arg)
                create_doi_mapping(file_arg, doi_hash)
                print(f"Running pipeline for specified DOI (mapped hash): {doi_hash}")
            success = run_pipeline_for_hash(doi_hash, test_mode=False, cache_enabled=args.cache)
        elif args.extraction_only:
            print("Running extraction for all hashes with minimal preprocessing")
            # First, discover DOIs and create mappings
            if not discover_and_create_mappings(args.input_dir):
                print("\n❌ Failed to discover DOIs and create mappings!")
                sys.exit(1)
            # Then run extraction with minimal preprocessing for all hashes
            success = run_extraction_only_for_all_hashes(test_mode=False, input_dir=args.input_dir, cache_enabled=args.cache)
        else:
            print("Running pipeline for all DOIs")
            # First, discover DOIs and create mappings
            if not discover_and_create_mappings(args.input_dir):
                print("\n❌ Failed to discover DOIs and create mappings!")
                sys.exit(1)
            # Then run the pipeline for all hashes
            success = run_pipeline_for_all_hashes(test_mode=False, input_dir=args.input_dir, cache_enabled=args.cache)
    finally:
        # Update timing file with end time and duration
        update_pipeline_timing_file(timing_filepath, timing_data, success)
    
    if success:
        print("\n🎉 Pipeline execution completed successfully!")
        sys.exit(0)
    else:
        print("\n❌ Pipeline execution failed!")
        sys.exit(1)

if __name__ == "__main__":
    main()



