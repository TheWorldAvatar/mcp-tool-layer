#!/usr/bin/env python3
"""
test_generation.py

Simple validation script to test the generation pipeline setup.
This does NOT run the full generation (which requires API calls and takes time),
but validates that all modules can be imported and basic configurations are correct.
"""

import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).resolve().parents[3]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))


def test_imports():
    """Test that all agent modules can be imported."""
    print("Testing imports...")
    
    modules = [
        "iteration_creation_agent",
        "mcp_underlying_script_creation_agent",
        "mcp_main_script_creation_agent",
        "task_division_agent",
        "task_extraction_prompt_creation_agent",
        "task_prompt_creation_agent",
        "generation_main"
    ]
    
    all_imported = True
    for module_name in modules:
        try:
            exec(f"from src.agents.scripts_and_prompts_generation import {module_name}")
            print(f"  [OK] {module_name}")
        except ImportError as e:
            print(f"  [FAIL] {module_name}: {e}")
            all_imported = False
        except Exception as e:
            print(f"  [ERROR] {module_name}: {e}")
            all_imported = False
    
    return all_imported


def test_config_files():
    """Test that required configuration files exist."""
    print("\nTesting configuration files...")
    
    required_files = [
        "configs/meta_task/meta_task_config.json",
        "data/ontologies/ontosynthesis.ttl",
        "data/ontologies/ontomops-subgraph.ttl",
        "data/ontologies/ontospecies-subgraph.ttl",
    ]
    
    all_exist = True
    for file_path in required_files:
        path = Path(file_path)
        if path.exists():
            print(f"  [OK] {file_path}")
        else:
            print(f"  [MISSING] {file_path}")
            all_exist = False
    
    return all_exist


def test_meta_config_structure():
    """Test that meta_task_config.json has the expected structure."""
    print("\nTesting meta_task_config.json structure...")
    
    try:
        import json
        config_path = Path("configs/meta_task/meta_task_config.json")
        
        if not config_path.exists():
            print("  [FAIL] Config file not found")
            return False
        
        with open(config_path, 'r') as f:
            config = json.load(f)
        
        # Check required keys
        if "ontologies" not in config:
            print("  [FAIL] Missing 'ontologies' key")
            return False
        
        ontologies = config["ontologies"]
        
        if "main" not in ontologies:
            print("  [FAIL] Missing 'main' ontology")
            return False
        
        if "extensions" not in ontologies:
            print("  [FAIL] Missing 'extensions' list")
            return False
        
        main = ontologies["main"]
        print(f"  [OK] Main ontology: {main.get('name', 'unknown')}")
        
        extensions = ontologies["extensions"]
        print(f"  [OK] Extension ontologies: {len(extensions)}")
        for ext in extensions:
            print(f"     - {ext.get('name', 'unknown')}")
        
        return True
        
    except Exception as e:
        print(f"  [ERROR] Error reading config: {e}")
        return False


def test_output_directories():
    """Test that output directories can be created."""
    print("\nTesting output directories...")
    
    output_dirs = [
        "ai_generated_contents_candidate/iterations",
        "ai_generated_contents_candidate/scripts",
        "ai_generated_contents_candidate/prompts",
    ]
    
    all_ok = True
    for dir_path in output_dirs:
        path = Path(dir_path)
        try:
            path.mkdir(parents=True, exist_ok=True)
            if path.exists() and path.is_dir():
                print(f"  [OK] {dir_path}")
            else:
                print(f"  [FAIL] {dir_path} (could not create)")
                all_ok = False
        except Exception as e:
            print(f"  [ERROR] {dir_path} ({e})")
            all_ok = False
    
    return all_ok


def main():
    """Run all tests."""
    print("="*60)
    print("GENERATION PIPELINE VALIDATION")
    print("="*60)
    
    all_passed = True
    
    # Run tests
    all_passed = test_imports() and all_passed
    all_passed = test_config_files() and all_passed
    all_passed = test_meta_config_structure() and all_passed
    all_passed = test_output_directories() and all_passed
    
    # Summary
    print("\n" + "="*60)
    if all_passed:
        print("[SUCCESS] ALL VALIDATION TESTS PASSED")
        print("\nYou can now run the generation pipeline:")
        print("  python -m src.agents.scripts_and_prompts_generation.generation_main --all")
    else:
        print("[FAILED] SOME VALIDATION TESTS FAILED")
        print("\nPlease fix the issues above before running the generation pipeline.")
    print("="*60)
    
    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())

