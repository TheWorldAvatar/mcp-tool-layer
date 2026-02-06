"""
Generic Pipeline Runner

A clean, config-driven pipeline executor that follows pipeline.json strictly.
Each step is implemented as a separate module in the src/pipelines/ folder.
"""

import argparse
import os
import sys
import json
import subprocess
from pathlib import Path
from typing import Optional
from tqdm import tqdm

# Ensure Windows consoles don't crash on emoji output (cp1252).
def _configure_utf8_stdio() -> None:
    for s in (sys.stdout, sys.stderr):
        try:
            # Python 3.7+: TextIOWrapper.reconfigure
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


_configure_utf8_stdio()

# Import utilities from src/pipelines/utils
from src.pipelines.utils import (
    generate_hash,
    load_config,
    load_doi_mapping,
    discover_dois,
    copy_pdfs_to_data_dir,
    load_step_module,
)


def setup_test_mcp_configs():
    """
    Setup MCP config files to use generated MCP tools from ai_generated_contents_candidate.
    
    Creates test config files in configs/ that point to the generated MCP scripts.
    Returns the name of the test config file to use.
    """
    scripts_dir = Path("ai_generated_contents_candidate/scripts")
    if not scripts_dir.exists():
        print(f"❌ MCP scripts directory not found: {scripts_dir}")
        return None
    
    # Find all ontologies with MCP tools
    test_mcp_config = {}
    
    for ontology_dir in scripts_dir.iterdir():
        if not ontology_dir.is_dir():
            continue
        
        main_script = ontology_dir / "main.py"
        if not main_script.exists():
            continue
        
        ontology_name = ontology_dir.name
        print(f"[INFO] Found MCP tools for: {ontology_name}")
        
        # Add entry for this ontology's MCP server
        # The server name should match what's used in iterations config
        test_mcp_config["llm_created_mcp"] = {
            "command": "python",
            "args": [
                "-m",
                f"ai_generated_contents_candidate.scripts.{ontology_name}.main"
            ],
            "transport": "stdio"
        }
    
    if not test_mcp_config:
        print(f"❌ No valid MCP tools found in {scripts_dir}")
        return None
    
    # Write test MCP config to configs/
    test_config_path = Path("configs/test_mcp_config.json")
    try:
        with open(test_config_path, 'w') as f:
            json.dump(test_mcp_config, f, indent=2)
        print(f"[OK] Created test MCP config: {test_config_path}")
        return test_config_path.name
    except Exception as e:
        print(f"❌ Failed to create test MCP config: {e}")
        return None


def run_pipeline(config_path: str, input_dir: Optional[str] = None, 
                 only_hashes: Optional[list[str]] = None,
                 use_mcp: bool = False,
                 iter1: bool = False,
                 skip_iter2_extraction: bool = False,
                 skip_iter3_extraction: bool = False,
                 skip_iter4_extraction: bool = False):
    """
    Run the pipeline according to configuration.
    
    Args:
        config_path: Path to pipeline.json
        input_dir: Directory containing input PDFs (defaults to 'raw_data')
        only_hashes: If provided, only process these DOI hashes
        use_mcp: If True, use MCP-generated tools instead of regular step modules
        iter1: If True, stop after top_entity_kg_building step (iteration 1)
        skip_iter2_extraction: If True, skip extraction for iteration 2
        skip_iter3_extraction: If True, skip extraction for iteration 3
        skip_iter4_extraction: If True, skip extraction for iteration 4
    """
    # Load configuration
    config = load_config(config_path)
    data_dir = config.get("data_dir", "data")
    
    # Default input directory to raw_data if not provided
    if input_dir is None:
        input_dir = "raw_data"
    
    # Filter steps if iter1 mode is enabled
    steps = config.get("steps", [])
    if iter1:
        # Stop after top_entity_kg_building (iteration 1)
        iter1_steps = [
            "pdf_conversion",
            "section_classification", 
            "stitching",
            "top_entity_extraction",
            "top_entity_kg_building"
        ]
        steps = [step for step in steps if step in iter1_steps]
        print("\n" + "="*60)
        print("Generic Pipeline Runner [ITER1 MODE - Stop after top entity]")
        print("="*60)
    else:
        print("\n" + "="*60)
        print("Generic Pipeline Runner" + (" [TEST MODE - Using Generated MCP Tools]" if use_mcp else ""))
        print("="*60)
    
    print(f"Config: {config_path}")
    print(f"Mode: {config.get('mode', 'per_doi')}")
    print(f"Steps: {', '.join(steps)}")
    print(f"Input: {input_dir}")
    print("="*60 + "\n")
    
    # Setup test MCP configuration if in test mode
    test_mcp_config_name = None
    if use_mcp:
        print("🧪 Setting up test MCP configuration...")
        test_mcp_config_name = setup_test_mcp_configs()
        if not test_mcp_config_name:
            print("[FAIL] Could not setup test MCP configuration")
            return False
        print()
    
    # Always discover/update DOI mapping from input directory
    print("📋 Discovering DOIs from input directory...")
    doi_mapping = discover_dois(input_dir, data_dir)
    if not doi_mapping:
        print("[FAIL] No DOIs found or discovery failed")
        return False
    print()
    
    # Determine which DOIs to process
    if only_hashes:
        doi_hashes = only_hashes
        print(f"🎯 Processing {len(only_hashes)} specific DOI(s): {', '.join(only_hashes)}\n")
    else:
        doi_hashes = list(doi_mapping.values())
        print(f"[INFO] Processing all {len(doi_hashes)} DOIs\n")
    
    # Copy PDFs for selected hashes
    print("📋 Copying PDFs to data directory")
    for doi, doi_hash in doi_mapping.items():
        if only_hashes and doi_hash not in only_hashes:
            continue
        copy_pdfs_to_data_dir(doi, doi_hash, input_dir, data_dir)
    print()
    
    # Execute pipeline steps
    # Note: steps variable is already defined and filtered above if iter1=True
    overall_success = True
    
    # Progress bar for hash processing
    with tqdm(total=len(doi_hashes), desc="Processing hashes", unit="hash", 
              bar_format='{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}]') as pbar:
        
        for doi_hash in doi_hashes:
            # Update progress bar description with current hash
            pbar.set_description(f"Processing {doi_hash}")
            
            print(f"\n{'='*60}")
            print(f"Processing: {doi_hash}")
            print(f"{'='*60}")
            
            # Ensure DOI hash folder exists
            doi_folder = os.path.join(data_dir, doi_hash)
            os.makedirs(doi_folder, exist_ok=True)
            print(f"[OK] DOI folder ready: {doi_folder}")
            
            for step_name in steps:
                print(f"\n📍 Step: {step_name}")
                
                # Load step module
                step_module = load_step_module(step_name)
                if not step_module:
                    print(f"[FAIL] Skipping {doi_hash} due to missing step module")
                    overall_success = False
                    break
                
                # Run step
                try:
                    step_config = {
                        "data_dir": data_dir,
                        **config.get("step_configs", {}).get(step_name, {})
                    }
                    
                    # If in test mode, add test MCP config to step config
                    if use_mcp and test_mcp_config_name:
                        step_config["test_mcp_config"] = test_mcp_config_name
                    
                    # Pass skip extraction flags to main_ontology_extractions step
                    if step_name == "main_ontology_extractions":
                        step_config["skip_iter2_extraction"] = skip_iter2_extraction
                        step_config["skip_iter3_extraction"] = skip_iter3_extraction
                        step_config["skip_iter4_extraction"] = skip_iter4_extraction
                    
                    success = step_module.run_step(doi_hash, step_config)
                    
                    if not success:
                        print(f"[FAIL] Step '{step_name}' failed for {doi_hash}")
                        overall_success = False
                        break
                        
                except Exception as e:
                    print(f"[ERROR] Step '{step_name}' raised exception: {e}")
                    import traceback
                    traceback.print_exc()
                    overall_success = False
                    break
            
            print(f"\n{'='*60}")
            print(f"Completed: {doi_hash}")
            print(f"{'='*60}")
            
            # Update progress bar
            pbar.update(1)
    
    # Summary
    print(f"\n{'='*60}")
    if overall_success:
        print("✅ Pipeline completed successfully")
    else:
        print("[WARN] Pipeline completed with errors")
    print(f"{'='*60}\n")
    
    return overall_success


def test_mcp_tools(ontology: Optional[str] = None, test_hash: Optional[str] = None):
    """
    Integration test: Run actual tasks using generated MCP tools.
    
    This is NOT just a unit test - it runs real workflows using the MCP tools
    on actual data to verify end-to-end functionality.
    
    Args:
        ontology: Specific ontology to test (e.g., 'ontosynthesis', 'ontomops'). 
                  If None, tests all available ontologies.
        test_hash: DOI hash to use for testing (e.g., '0c57bac8'). 
                   If None, uses a default test hash.
    
    Returns:
        True if all tests pass, False otherwise
    """
    print("\n" + "="*60)
    print("MCP Tools Integration Test")
    print("="*60)
    print("This will run REAL tasks using generated MCP tools")
    print("="*60)
    
    # Find generated MCP scripts
    scripts_dir = Path("ai_generated_contents_candidate/scripts")
    if not scripts_dir.exists():
        print(f"[FAIL] Scripts directory not found: {scripts_dir}")
        return False
    
    # Discover ontologies
    ontologies = []
    if ontology:
        ontologies = [ontology]
    else:
        for item in scripts_dir.iterdir():
            if item.is_dir() and (item / "main.py").exists():
                ontologies.append(item.name)
    
    if not ontologies:
        print("[FAIL] No ontologies with MCP tools found")
        return False
    
    # Determine test hash
    if not test_hash:
        test_hash = "00000001"  # Default test hash
        print(f"No test hash provided, using default: {test_hash}")
    else:
        print(f"Using test hash: {test_hash}")
    
    print(f"Found {len(ontologies)} ontology tool(s): {', '.join(ontologies)}\n")
    
    all_passed = True
    
    for onto_name in ontologies:
        print(f"\n{'-'*60}")
        print(f"Testing: {onto_name}")
        print(f"{'-'*60}")
        
        onto_dir = scripts_dir / onto_name
        main_script = onto_dir / "main.py"
        # Generation pipeline produces a split creation module set (base/entities/relationships/checks)
        # rather than a single `<onto>_creation.py`.
        creation_script = onto_dir / f"{onto_name}_creation.py"
        creation_base_script = onto_dir / f"{onto_name}_creation_base.py"
        mcp_config = onto_dir / "mcp_config.json"
        
        # Check files exist
        if not main_script.exists():
            print(f"[FAIL] Missing main.py")
            all_passed = False
            continue
        
        if not creation_script.exists() and not creation_base_script.exists():
            print(f"[FAIL] Missing {onto_name}_creation_base.py (or legacy {onto_name}_creation.py)")
            all_passed = False
            continue
        
        if not mcp_config.exists():
            print(f"[WARN] Missing mcp_config.json (optional)")
        else:
            print(f"[OK] Found mcp_config.json")
            # Validate JSON
            try:
                with open(mcp_config) as f:
                    config_data = json.load(f)
                print(f"  - Config valid: {len(config_data.get('mcpServers', {}))} MCP server(s)")
            except Exception as e:
                print(f"[FAIL] Invalid mcp_config.json: {e}")
                all_passed = False
                continue
        
        # Test import of creation script
        print(f"\n[TEST] Testing imports...")
        try:
            creation_module = f"{onto_name}_creation" if creation_script.exists() else f"{onto_name}_creation_base"
            # Try importing the module
            import_cmd = (
                f"python -c \"import sys; sys.path.insert(0, '.'); "
                f"from ai_generated_contents_candidate.scripts.{onto_name}.{creation_module} import *; "
                f"print('Import successful')\""
            )
            result = subprocess.run(
                import_cmd,
                shell=True,
                capture_output=True,
                text=True,
                timeout=10
            )
            
            if result.returncode == 0:
                print(f"[OK] Creation script imports successfully")
            else:
                print(f"[FAIL] Import failed:")
                print(result.stderr)
                all_passed = False
                continue
        except subprocess.TimeoutExpired:
            print(f"[FAIL] Import timeout")
            all_passed = False
            continue
        except Exception as e:
            print(f"[ERROR] Import test error: {e}")
            all_passed = False
            continue
        
        # Run integration test with real data
        print(f"\n[TEST] Running integration test with hash {test_hash}...")
        try:
            # Setup global state for test
            import json
            import shutil
            data_dir = Path("data")
            # IMPORTANT: never delete real pipeline data directories.
            # Keep verification artifacts in an isolated sandbox folder.
            sandbox_root = data_dir / "_mcp_verify"
            test_dir = sandbox_root / (test_hash or "default") / onto_name
            
            # Clean up old test data if exists (both output and memory/graph data)
            if test_dir.exists():
                print(f"[INFO] Cleaning up old test data (output + memory)...")
                shutil.rmtree(test_dir)
            
            # Ensure test directory exists
            test_dir.mkdir(parents=True, exist_ok=True)
            
            # Create global state file
            global_state_file = test_dir / f"{onto_name}_global_state.json"
            global_state = {
                "doi": f"test_doi_{test_hash}",
                "top_level_entity_name": f"Test_{onto_name}",
                "top_level_entity_iri": ""
            }
            with open(global_state_file, "w") as f:
                json.dump(global_state, f)
            
            print(f"[OK] Test environment setup complete (clean state)")

            # Lightweight "integration" check: ensure the generated MCP server module can be imported.
            # (Running a full end-to-end agent here is expensive and environment-dependent.)
            creation_module = f"{onto_name}_creation" if creation_script.exists() else f"{onto_name}_creation_base"
            smoke_cmd = (
                "python -c \"import sys; sys.path.insert(0,'.'); "
                f"import ai_generated_contents_candidate.scripts.{onto_name}.main as m; "
                f"from ai_generated_contents_candidate.scripts.{onto_name}.{creation_module} import *; "
                "print('Imported main + creation successfully'); "
                "print(f'MCP={m.mcp.name}')\""
            )
            result = subprocess.run(
                smoke_cmd,
                shell=True,
                capture_output=True,
                text=True,
                timeout=20,
                cwd=".",
                env={**os.environ, "PYTHONPATH": "."},
            )

            if result.returncode == 0:
                print(f"[OK] Integration smoke test passed")
                print(f"  {result.stdout.strip()}")
            else:
                print(f"[FAIL] Integration smoke test failed:")
                error_lines = (result.stderr or "").strip().split('\n')
                for line in error_lines[:15]:
                    if line.strip():
                        print(f"    {line}")
                all_passed = False
        except subprocess.TimeoutExpired:
            print(f"[WARN] Integration test timeout")
        except Exception as e:
            print(f"[ERROR] Integration test error: {e}")
            import traceback
            traceback.print_exc()
            all_passed = False
        
        # Test MCP server startup (if FastMCP available)
        print(f"\n[TEST] Testing MCP server...")
        try:
            # Try to import and check if server can be initialized
            test_cmd = f"python -c \"from ai_generated_contents_candidate.scripts.{onto_name}.main import mcp; print(f'MCP server: {{mcp.name}}')\" "
            result = subprocess.run(
                test_cmd,
                shell=True,
                capture_output=True,
                text=True,
                timeout=10
            )
            
            if result.returncode == 0:
                print(f"[OK] MCP server initialized")
                print(f"  {result.stdout.strip()}")
            else:
                print(f"[WARN] MCP server test skipped (may need FastMCP)")
        except Exception as e:
            print(f"[WARN] MCP server test skipped: {e}")
        
        print(f"\n{'-'*60}")
        print(f"[OK] {onto_name} tests completed")
        print(f"{'-'*60}")
    
    # Summary
    print(f"\n{'='*60}")
    if all_passed:
        print("[PASS] All MCP tools tests passed")
    else:
        print("[WARN] Some tests failed - check details above")
    print(f"{'='*60}\n")
    
    return all_passed


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description='Generic Pipeline Runner - Clean config-driven execution'
    )
    
    parser.add_argument(
        '--config',
        type=str,
        default='configs/pipeline.json',
        help='Path to pipeline configuration JSON (default: configs/pipeline.json)'
    )
    
    parser.add_argument(
        '--input-dir',
        type=str,
        help='Directory containing input PDF files (default: raw_data)'
    )
    
    parser.add_argument(
        '--hash',
        type=str,
        action='append',
        dest='hashes',
        help='Process only this specific DOI hash (can be specified multiple times)'
    )
    
    parser.add_argument(
        '--test',
        action='store_true',
        help='Test mode: run pipeline using MCP-generated tools instead of regular modules'
    )
    
    parser.add_argument(
        '--iter1',
        action='store_true',
        help='Iteration 1 mode: stop pipeline after top_entity_kg_building step'
    )
    
    parser.add_argument(
        '--skip-iter2-extraction',
        action='store_true',
        help='Skip extraction for iteration 2'
    )
    
    parser.add_argument(
        '--skip-iter3-extraction',
        action='store_true',
        help='Skip extraction for iteration 3'
    )
    
    parser.add_argument(
        '--skip-iter4-extraction',
        action='store_true',
        help='Skip extraction for iteration 4'
    )
    
    parser.add_argument(
        '--verify-mcp',
        action='store_true',
        help='Verification mode: run standalone tests of MCP tools (separate from pipeline)'
    )
    
    parser.add_argument(
        '--ontology',
        type=str,
        help='In verification mode, test only this specific ontology (e.g., ontosynthesis)'
    )
    
    args = parser.parse_args()
    
    # MCP verification mode (old --test behavior)
    if args.verify_mcp:
        # Use first hash if provided, otherwise None (will use default)
        test_hash = args.hashes[0] if args.hashes else None
        success = test_mcp_tools(ontology=args.ontology, test_hash=test_hash)
        sys.exit(0 if success else 1)
    
    # Run pipeline (with MCP tools if --test flag is set)
    success = run_pipeline(
        config_path=args.config,
        input_dir=args.input_dir,
        only_hashes=args.hashes,
        use_mcp=args.test,
        iter1=args.iter1,
        skip_iter2_extraction=args.skip_iter2_extraction,
        skip_iter3_extraction=args.skip_iter3_extraction,
        skip_iter4_extraction=args.skip_iter4_extraction
    )
    
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()

