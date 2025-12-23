#!/usr/bin/env python3
"""
generation_main.py

Orchestration script that runs all script and prompt generation agents.
This is the main entry point for generating ontology-specific scripts and prompts.

The pipeline automatically:
    0. Parses all ontology TTL files to generate/update *_parsed.md and *_parsed.json
    1. Generates iterations.json for each ontology
    2. Generates MCP underlying scripts (*_creation.py)
    3. Generates MCP main scripts (main.py)
    4. Generates extraction prompts
    5. Generates KG building prompts
    6. Generates MCP config JSON

Usage:
    # Generate for all ontologies
    python -m src.agents.scripts_and_prompts_generation.generation_main --all

    # Generate for specific ontologies
    python -m src.agents.scripts_and_prompts_generation.generation_main --ontosynthesis --ontomops

    # Skip specific steps
    python -m src.agents.scripts_and_prompts_generation.generation_main --all --skip-iterations --skip-mcp-configs
"""

import os
import sys
import json
import argparse
import asyncio
from pathlib import Path
from typing import List, Dict, Any
from tqdm import tqdm

# Add project root to path
project_root = Path(__file__).resolve().parents[3]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

# Import all the generation agents
from src.agents.scripts_and_prompts_generation.iteration_creation_agent import create_iterations_json, _load_meta_task_config
from src.agents.scripts_and_prompts_generation.mcp_underlying_script_creation_agent import create_underlying_script_with_retry
from src.agents.scripts_and_prompts_generation.mcp_main_script_creation_agent import create_main_script_with_retry
from src.agents.scripts_and_prompts_generation.task_extraction_prompt_creation_agent import generate_prompts_from_iterations
from src.agents.scripts_and_prompts_generation.task_prompt_creation_agent import generate_kg_prompts_from_iterations
from src.agents.scripts_and_prompts_generation.ttl_parser import parse_ontology_ttl, format_class_properties_markdown
# Import direct generation functions (no agents/MCP)
from src.agents.scripts_and_prompts_generation.direct_script_generation import (
    generate_underlying_script_direct,
    generate_main_script_direct,
    generate_base_script_direct,
    generate_checks_script_direct,
    generate_relationships_script_direct,
    generate_entities_script_direct
)


def load_meta_task_config() -> Dict[str, Any]:
    """Load meta task configuration."""
    config_path = Path("configs/meta_task/meta_task_config.json")
    try:
        if config_path.exists():
            return json.loads(config_path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"⚠️  Warning: Could not load meta_task_config.json: {e}")
    return {}


def get_ontologies_from_config(config: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Extract ontology information from meta task config."""
    ontologies = []
    
    # Add main ontology
    main = config.get("ontologies", {}).get("main", {})
    if main:
        ontologies.append({
            "name": main.get("name", "ontosynthesis"),
            "ttl_file": main.get("ttl_file", "data/ontologies/ontosynthesis.ttl"),
            "model": "gpt-4o",  # Default model for main
            "role": "main"
        })
    
    # Add extension ontologies
    for ext in config.get("ontologies", {}).get("extensions", []) or []:
        ontologies.append({
            "name": ext.get("name"),
            "ttl_file": ext.get("ttl_file"),
            "model": ext.get("agent_model", "gpt-4o"),
            "role": "extension"
        })
    
    return ontologies


def filter_ontologies(ontologies: List[Dict[str, Any]], args) -> List[Dict[str, Any]]:
    """Filter ontologies based on command-line arguments."""
    if args.all:
        return ontologies
    
    selected = []
    if args.ontosynthesis:
        selected.extend([o for o in ontologies if o["name"] == "ontosynthesis"])
    if args.ontomops:
        selected.extend([o for o in ontologies if o["name"] == "ontomops"])
    if args.ontospecies:
        selected.extend([o for o in ontologies if o["name"] == "ontospecies"])
    
    return selected if selected else ontologies


def ensure_package_structure():
    """
    Create __init__.py files to make directories proper Python packages.
    This enables -m module syntax for MCP servers.
    """
    print("\n" + "="*60)
    print("ENSURING PYTHON PACKAGE STRUCTURE")
    print("="*60)
    
    # Define all __init__.py files needed
    init_files = [
        Path("ai_generated_contents_candidate/__init__.py"),
        Path("ai_generated_contents_candidate/scripts/__init__.py"),
        Path("ai_generated_contents_candidate/iterations/__init__.py"),
        Path("ai_generated_contents_candidate/prompts/__init__.py"),
    ]
    
    created_count = 0
    for init_file in init_files:
        if not init_file.exists():
            init_file.parent.mkdir(parents=True, exist_ok=True)
            init_file.write_text("# Auto-generated by generation_main.py\n", encoding="utf-8")
            print(f"[CREATED] {init_file}")
            created_count += 1
        else:
            print(f"[EXISTS] {init_file}")
    
    if created_count > 0:
        print(f"\n[OK] Created {created_count} __init__.py files")
    else:
        print(f"\n[OK] All __init__.py files already exist")
    print()


def ensure_ontology_package(ontology_name: str):
    """
    Create __init__.py file for a specific ontology package.
    
    Args:
        ontology_name: Name of the ontology (e.g., 'ontosynthesis', 'ontomops')
    """
    init_file = Path(f"ai_generated_contents_candidate/scripts/{ontology_name}/__init__.py")
    if not init_file.exists():
        init_file.parent.mkdir(parents=True, exist_ok=True)
        init_file.write_text("# Auto-generated by generation_main.py\n", encoding="utf-8")
        print(f"   [CREATED] Package: {init_file}")


def ensure_universal_utils():
    """
    Copy universal_utils.py to ai_generated_contents_candidate/scripts/ if needed.
    This provides domain-agnostic utility functions for all generated scripts.
    """
    import shutil
    
    source = Path("sandbox/code/universal_utils.py")
    target = Path("ai_generated_contents_candidate/scripts/universal_utils.py")
    
    if not source.exists():
        print(f"   ⚠️  WARNING: Source universal_utils.py not found at {source}")
        return
    
    # Always copy to ensure it's up-to-date
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    print(f"   [COPIED] universal_utils.py → {target}")


def parse_all_ontology_ttls(ontologies: List[Dict[str, Any]]) -> bool:
    """
    Parse all ontology TTL files and generate parsed markdown/JSON files.
    This ensures the parsed files are always up-to-date with the ontology definitions.
    
    Args:
        ontologies: List of ontology dictionaries with 'name' and 'ttl_file' keys
    
    Returns:
        True if all parsing succeeded, False otherwise
    """
    print("\n" + "="*60)
    print("STEP 0: PARSING ONTOLOGY TTL FILES")
    print("="*60)
    print("Ensuring parsed markdown files are up-to-date...\n")
    
    all_success = True
    
    for ont in ontologies:
        ont_name = ont["name"]
        ttl_path = ont["ttl_file"]
        
        print(f"📄 Parsing {ont_name}...")
        print(f"   Source: {ttl_path}")
        
        # Check if TTL file exists
        if not Path(ttl_path).exists():
            print(f"   ⚠️  TTL file not found: {ttl_path}")
            all_success = False
            continue
        
        try:
            # Parse the ontology
            parsed = parse_ontology_ttl(ttl_path)
            
            # Generate output paths
            base_path = str(Path(ttl_path).with_suffix(""))
            json_path = f"{base_path}_parsed.json"
            md_path = f"{base_path}_parsed.md"
            
            # Save JSON
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(parsed, f, indent=2)
            print(f"   ✅ JSON: {json_path}")
            
            # Save Markdown
            markdown = format_class_properties_markdown(parsed)
            with open(md_path, "w", encoding="utf-8") as f:
                f.write(markdown)
            print(f"   ✅ Markdown: {md_path}")
            
            # Summary
            total_classes = parsed.get('metadata', {}).get('total_classes', len(parsed.get('classes', {})))
            print(f"   📊 Parsed {total_classes} classes\n")
            
        except Exception as e:
            print(f"   ❌ Parsing failed: {e}")
            import traceback
            traceback.print_exc()
            all_success = False
    
    if all_success:
        print("✅ All ontology TTL files parsed successfully\n")
    else:
        print("⚠️  Some ontology TTL files failed to parse\n")
    
    return all_success


def generate_mcp_config_json(ontologies: List[Dict[str, Any]], output_path: Path):
    """
    Generate MCP configuration JSON file for all ontology scripts.
    
    Args:
        ontologies: List of ontology configurations
        output_path: Path to output the config file
    """
    print("\n" + "="*60)
    print("GENERATING MCP CONFIG JSON")
    print("="*60)
    
    config = {}
    
    for ont in ontologies:
        name = ont["name"]
        # Use -m module syntax as requested
        config[f"{name}_mcp"] = {
            "command": "python",
            "args": [
                "-m",
                f"ai_generated_contents_candidate.scripts.{name}.main"
            ],
            "transport": "stdio"
        }
    
    # Write the config file
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=4, ensure_ascii=False)
    
    print(f"✅ Generated MCP config: {output_path}")
    print(f"   Included {len(config)} ontology MCP servers")
    for key in config.keys():
        print(f"   - {key}")
    print()


async def generate_iterations(ontologies: List[Dict[str, Any]], meta_cfg: Dict[str, Any]):
    """Generate iterations.json for all ontologies."""
    print("\n" + "="*60)
    print("STEP 1: GENERATING ITERATIONS.JSON FILES")
    print("="*60)
    
    output_dir = Path("ai_generated_contents_candidate/iterations")
    success = True
    
    with tqdm(total=len(ontologies), desc="Generating iterations", unit="ontology") as pbar:
        for ont in ontologies:
            name = ont["name"]
            ttl_file = ont["ttl_file"]
            
            pbar.set_description(f"Generating iterations for {name}")
            
            print(f"\n📋 Generating iterations for: {name}")
            print(f"   T-Box: {ttl_file}")
            
            try:
                ttl_path = Path(ttl_file)
                if not ttl_path.exists():
                    print(f"❌ T-Box not found: {ttl_file}")
                    success = False
                    pbar.update(1)
                    continue
                
                result_path = create_iterations_json([ttl_path], output_dir, meta_cfg=meta_cfg)
                print(f"✅ Generated: {result_path}")
            except Exception as e:
                print(f"❌ Failed to generate iterations for {name}: {e}")
                success = False
            
            pbar.update(1)
    
    return success


async def generate_underlying_scripts(ontologies: List[Dict[str, Any]], model_override: str = None, use_direct: bool = False):
    """Generate MCP underlying scripts for all ontologies."""
    print("\n" + "="*60)
    if use_direct:
        print("STEP 2: GENERATING MCP UNDERLYING SCRIPTS (DIRECT MODE)")
    else:
        print("STEP 2: GENERATING MCP UNDERLYING SCRIPTS (AGENT MODE)")
    print("="*60)
    
    success = True
    
    with tqdm(total=len(ontologies), desc="Generating underlying scripts", unit="ontology") as pbar:
        for ont in ontologies:
            name = ont["name"]
            ttl_file = ont["ttl_file"]
            model = model_override if model_override else ont["model"]
            script_name = f"{name}_creation.py"
            
            pbar.set_description(f"Generating underlying script for {name}")
            
            print(f"\n🔧 Generating underlying script for: {name}")
            print(f"   Model: {model}")
            print(f"   Script: {script_name}")
            print(f"   Mode: {'Direct LLM' if use_direct else 'Agent-based'}")
            
            # Ensure package structure for this ontology
            ensure_ontology_package(name)
            
            # Ensure universal_utils.py is available (domain-agnostic utilities)
            ensure_universal_utils()
            
            try:
                if use_direct:
                    # Direct LLM generation (no agents, no MCP tools)
                    # Generate base + MULTIPLE entity group scripts (split for manageability)
                    output_dir = f"ai_generated_contents_candidate/scripts/{name}"
                    
                    # Generate concise ontology structure first
                    from src.agents.scripts_and_prompts_generation.direct_script_generation import save_concise_structure
                    output_base_dir = Path("ai_generated_contents_candidate")
                    concise_md_path = save_concise_structure(ttl_file, name, output_base_dir)
                    print(f"   📄 Saved concise ontology structure: {concise_md_path.name}")
                    
                    # Step 1: Generate foundational scripts (TEMPLATE-BASED - NO LLM!)
                    from src.agents.scripts_and_prompts_generation.template_based_generation import (
                        generate_checks_script_from_template,
                        generate_relationships_script_from_template
                    )
                    from src.agents.scripts_and_prompts_generation.direct_script_generation import (
                        extract_concise_ontology_structure
                    )
                    
                    # Extract structure once for all template-based generation
                    concise_structure = extract_concise_ontology_structure(ttl_file)
                    
                    print(f"\n   [1/4] Generating check_existing functions (template-based)...")
                    generate_checks_script_from_template(
                        concise_structure=concise_structure,
                        ontology_name=name,
                        output_path=Path(output_dir) / f"{name}_creation_checks.py"
                    )
                    
                    print(f"\n   [2/4] Generating relationship functions (template-based)...")
                    generate_relationships_script_from_template(
                        concise_structure=concise_structure,
                        ontology_name=name,
                        output_path=Path(output_dir) / f"{name}_creation_relationships.py"
                    )
                    
                    print(f"\n   [3/4] Generating base utilities...")
                    await generate_base_script_direct(
                        ontology_path=ttl_file,
                        ontology_name=name,
                        output_dir=output_dir,
                        model_name=model,
                        max_retries=3
                    )
                    
                    # Step 2: Generate entity creation scripts (TEMPLATE-BASED - NO LLM!)
                    print(f"\n   [4/4] Generating entity creation scripts (template-based)...")
                    
                    # Use template-based generation instead of LLM
                    from src.agents.scripts_and_prompts_generation.template_based_generation import (
                        generate_entity_script_from_template, parse_concise_signatures
                    )
                    
                    # Get concise path
                    concise_md_path = Path("ai_generated_contents_candidate/ontology_structures") / f"{name}_concise.md"
                    
                    # Parse signatures to split into 2 parts
                    all_signatures = parse_concise_signatures(concise_md_path)
                    mid_point = len(all_signatures) // 2
                    
                    part1_classes = [sig['class_name'] for sig in all_signatures[:mid_point]]
                    part2_classes = [sig['class_name'] for sig in all_signatures[mid_point:]]
                    
                    print(f"   Part 1: {len(part1_classes)} classes")
                    print(f"   Part 2: {len(part2_classes)} classes")
                    
                    # Generate part 1
                    entity_script_1 = generate_entity_script_from_template(
                        concise_md_path=concise_md_path,
                        ontology_name=name,
                        output_path=Path(output_dir) / f"{name}_creation_entities_1.py",
                        class_subset=part1_classes
                    )
                    
                    # Generate part 2
                    entity_script_2 = generate_entity_script_from_template(
                        concise_md_path=concise_md_path,
                        ontology_name=name,
                        output_path=Path(output_dir) / f"{name}_creation_entities_2.py",
                        class_subset=part2_classes
                    )
                    
                    entity_scripts = [str(entity_script_1), str(entity_script_2)]
                    print(f"   ✅ Generated {len(entity_scripts)} entity creation scripts (template-based)")
                else:
                    # Agent-based generation (with MCP tools)
                    await create_underlying_script_with_retry(
                        ontology_path=ttl_file,
                        script_name=script_name,
                        version=1,
                        iteration=1,
                        model_name=model,
                        split_by_steps=False,
                        ontology_short=name,
                        max_retries=3,
                        retry_delay=5
                    )
                print(f"✅ Generated underlying script for {name}")
            except Exception as e:
                print(f"❌ Failed to generate underlying script for {name}: {e}")
                success = False
            
            pbar.update(1)
    
    return success


async def generate_main_scripts(ontologies: List[Dict[str, Any]], model_override: str = None, use_direct: bool = False):
    """Generate MCP main interface scripts for all ontologies."""
    print("\n" + "="*60)
    if use_direct:
        print("STEP 3: GENERATING MCP MAIN INTERFACE SCRIPTS (DIRECT MODE)")
    else:
        print("STEP 3: GENERATING MCP MAIN INTERFACE SCRIPTS (AGENT MODE)")
    print("="*60)
    
    success = True
    
    with tqdm(total=len(ontologies), desc="Generating main scripts", unit="ontology") as pbar:
        for ont in ontologies:
            name = ont["name"]
            ttl_file = ont["ttl_file"]
            model = model_override if model_override else ont["model"]
            
            # Auto-detect underlying script(s)
            if use_direct:
                # Multi-script architecture: foundational scripts + entity creation scripts
                base_path = f"ai_generated_contents_candidate/scripts/{name}/{name}_creation_base.py"
                checks_path = f"ai_generated_contents_candidate/scripts/{name}/{name}_creation_checks.py"
                relationships_path = f"ai_generated_contents_candidate/scripts/{name}/{name}_creation_relationships.py"
                
                # Find all entity creation scripts (e.g., *_entities_1.py, *_entities_2.py)
                scripts_dir = Path(f"ai_generated_contents_candidate/scripts/{name}")
                entity_scripts = sorted([
                    str(p) for p in scripts_dir.glob(f"{name}_creation_entities_*.py")
                ])
                
                candidate_path = base_path  # For compatibility
                
                pbar.set_description(f"Generating main script for {name}")
                
                print(f"\n🔧 Generating main script for: {name}")
                print(f"   Model: {model}")
                print(f"   Foundational scripts:")
                print(f"      - {Path(checks_path).name} (check_existing functions)")
                print(f"      - {Path(relationships_path).name} (add_xxx_to_yyy functions)")
                print(f"      - {Path(base_path).name} (utilities)")
                print(f"   Entity creation scripts: {len(entity_scripts)} files")
                for idx, script in enumerate(entity_scripts, 1):
                    print(f"      {idx}. {Path(script).name}")
                print(f"   Mode: Direct LLM")
                
                # Validate foundational scripts exist
                missing_scripts = []
                if not Path(checks_path).exists():
                    missing_scripts.append("checks")
                if not Path(relationships_path).exists():
                    missing_scripts.append("relationships")
                if not Path(base_path).exists():
                    missing_scripts.append("base")
                
                if missing_scripts:
                    print(f"❌ Missing foundational scripts: {', '.join(missing_scripts)}")
                    print(f"   Skipping main script generation for {name}")
                    success = False
                    pbar.update(1)
                    continue
                
                if not entity_scripts:
                    print(f"❌ No entity creation scripts found!")
                    print(f"   Skipping main script generation for {name}")
                    success = False
                    pbar.update(1)
                    continue
            else:
                # Agent mode: single file
                candidate_path = f"ai_generated_contents_candidate/scripts/{name}/{name}_creation.py"
                
                pbar.set_description(f"Generating main script for {name}")
                
                print(f"\n🔧 Generating main script for: {name}")
                print(f"   Model: {model}")
                print(f"   Underlying script: {candidate_path}")
                print(f"   Mode: Agent-based")
                
                if not Path(candidate_path).exists():
                    print(f"❌ Underlying script not found: {candidate_path}")
                    print(f"   Skipping main script generation for {name}")
                    success = False
                    pbar.update(1)
                    continue
            
            try:
                if use_direct:
                    # Direct LLM generation (no agents, no MCP tools)
                    # For multi-script architecture, pass foundational scripts and entity creation scripts
                    output_dir = f"ai_generated_contents_candidate/scripts/{name}"
                    await generate_main_script_direct(
                        ontology_path=ttl_file,
                        ontology_name=name,
                        checks_script_path=checks_path,
                        relationships_script_path=relationships_path,
                        base_script_path=base_path,
                        entity_script_paths=entity_scripts,  # List of entity creation scripts
                        output_dir=output_dir,
                        model_name=model,
                        max_retries=3
                    )
                else:
                    # Agent-based generation (with MCP tools)
                    await create_main_script_with_retry(
                        ontology_path=ttl_file,
                        underlying_script_path=candidate_path,
                        model_name=model,
                        ontology_short=name,
                        max_retries=3,
                        retry_delay=5
                    )
                print(f"✅ Generated main script for {name}")
            except Exception as e:
                print(f"❌ Failed to generate main script for {name}: {e}")
                success = False
            
            pbar.update(1)
    
    return success


async def generate_extraction_prompts(ontologies: List[Dict[str, Any]], iteration_filter: List[str] = None, pre_extraction_only: bool = False):
    """Generate extraction prompts for all ontologies.
    
    Args:
        ontologies: List of ontology configurations
        iteration_filter: Optional list of iteration numbers to generate (e.g., ["1", "2", "3"])
        pre_extraction_only: If True, only generate pre-extraction prompts
    """
    print("\n" + "="*60)
    if pre_extraction_only:
        print("STEP 4: GENERATING PRE-EXTRACTION PROMPTS ONLY")
    else:
        print("STEP 4: GENERATING EXTRACTION PROMPTS")
    print("="*60)
    
    ontology_names = [ont["name"] for ont in ontologies]
    
    if pre_extraction_only:
        print(f"\n📝 Generating PRE-EXTRACTION prompts only for: {', '.join(ontology_names)}")
    elif iteration_filter:
        print(f"\n📝 Generating extraction prompts for: {', '.join(ontology_names)}")
        print(f"   Iterations: {', '.join(iteration_filter)}")
    else:
        print(f"\n📝 Generating extraction prompts for: {', '.join(ontology_names)}")
        print(f"   Iterations: ALL")
    
    try:
        success = generate_prompts_from_iterations(ontology_names, iteration_filter=iteration_filter, pre_extraction_only=pre_extraction_only)
        if success:
            if pre_extraction_only:
                print(f"✅ Generated pre-extraction prompts")
            else:
                print(f"✅ Generated extraction prompts")
        else:
            print(f"⚠️  Some prompts failed to generate")
        return success
    except Exception as e:
        print(f"❌ Failed to generate prompts: {e}")
        return False


async def generate_kg_prompts(ontologies: List[Dict[str, Any]]):
    """Generate KG building prompts for all ontologies."""
    print("\n" + "="*60)
    print("STEP 5: GENERATING KG BUILDING PROMPTS")
    print("="*60)
    
    success = True
    
    with tqdm(total=len(ontologies), desc="Generating KG prompts", unit="ontology") as pbar:
        for ont in ontologies:
            name = ont["name"]
            
            pbar.set_description(f"Generating KG prompts for {name}")
            
            print(f"\n📝 Generating KG prompts for: {name}")
            
            try:
                result = generate_kg_prompts_from_iterations(name)
                if result:
                    print(f"✅ Generated KG prompts for {name}")
                else:
                    print(f"⚠️  Some KG prompts failed for {name}")
                    success = False
            except Exception as e:
                print(f"❌ Failed to generate KG prompts for {name}: {e}")
                success = False
            
            pbar.update(1)
    
    return success


async def main_async(args):
    """Main async orchestration function."""
    print("\n" + "="*80)
    print("ONTOLOGY SCRIPTS AND PROMPTS GENERATION PIPELINE")
    print("="*80)
    
    # Load configuration
    meta_cfg = load_meta_task_config()
    all_ontologies = get_ontologies_from_config(meta_cfg)
    selected_ontologies = filter_ontologies(all_ontologies, args)
    
    if not selected_ontologies:
        print("❌ No ontologies selected. Use --all or specify ontologies.")
        return False
    
    print(f"\n📋 Selected ontologies:")
    for ont in selected_ontologies:
        default_model = ont['model']
        actual_model = args.model if args.model else default_model
        print(f"   - {ont['name']} (model: {actual_model}{' [overridden]' if args.model else ''})")
    print()
    
    # Ensure Python package structure (create __init__.py files)
    ensure_package_structure()
    
    # Track overall success
    all_success = True
    
    # Step 0: Parse all ontology TTL files to generate/update parsed markdown
    ttl_parse_success = parse_all_ontology_ttls(selected_ontologies)
    all_success = all_success and ttl_parse_success
    if not ttl_parse_success:
        print("⚠️  Warning: TTL parsing had errors, but continuing with generation...\n")
    
    # Step 1: Generate iterations.json
    if not args.skip_iterations:
        success = await generate_iterations(selected_ontologies, meta_cfg)
        all_success = all_success and success
    else:
        print("\n⏭️  Skipping iterations generation (--skip-iterations)")
    
    # Step 2: Generate MCP underlying scripts
    if not args.skip_underlying:
        success = await generate_underlying_scripts(selected_ontologies, model_override=args.model, use_direct=args.direct)
        all_success = all_success and success
    else:
        print("\n⏭️  Skipping underlying script generation (--skip-underlying)")
    
    # Step 3: Generate MCP main scripts
    if not args.skip_main:
        success = await generate_main_scripts(selected_ontologies, model_override=args.model, use_direct=args.direct)
        all_success = all_success and success
    else:
        print("\n⏭️  Skipping main script generation (--skip-main)")
    
    # Step 4: Generate extraction prompts
    if not args.skip_extraction_prompts:
        # Build iteration filter from args
        iteration_filter = []
        if args.iter1_only:
            iteration_filter.append("1")
        if args.iter2_only:
            iteration_filter.append("2")
        if args.iter3_only:
            iteration_filter.append("3")
        if args.iter4_only:
            iteration_filter.append("4")
        
        # If no specific iterations requested, generate all
        if not iteration_filter:
            iteration_filter = None
        
        success = await generate_extraction_prompts(selected_ontologies, iteration_filter=iteration_filter, pre_extraction_only=args.pre_extraction_only)
        all_success = all_success and success
    else:
        print("\n⏭️  Skipping extraction prompts generation (--skip-extraction-prompts)")
    
    # Step 5: Generate KG building prompts
    if not args.skip_kg_prompts:
        success = await generate_kg_prompts(selected_ontologies)
        all_success = all_success and success
    else:
        print("\n⏭️  Skipping KG prompts generation (--skip-kg-prompts)")
    
    # Step 6: Generate MCP config JSON
    if not args.skip_mcp_config:
        output_path = Path("configs/generated_ontology_mcps.json")
        generate_mcp_config_json(selected_ontologies, output_path)
    else:
        print("\n⏭️  Skipping MCP config generation (--skip-mcp-config)")
    
    # Final summary
    print("\n" + "="*80)
    if all_success:
        print("✅ GENERATION PIPELINE COMPLETE - ALL STEPS SUCCEEDED")
    else:
        print("⚠️  GENERATION PIPELINE COMPLETE - SOME STEPS HAD ERRORS")
    print("="*80)
    
    print("\n📁 Generated files:")
    print("   - Parsed Ontologies: data/ontologies/*_parsed.{md,json}")
    print("   - Iterations: ai_generated_contents_candidate/iterations/")
    print("   - Scripts: ai_generated_contents_candidate/scripts/")
    print("   - Prompts: ai_generated_contents_candidate/prompts/")
    if not args.skip_mcp_config:
        print("   - MCP Config: configs/generated_ontology_mcps.json")
    print()
    
    return all_success


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Orchestrated generation of ontology scripts and prompts",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Generate everything for all ontologies
  python -m src.agents.scripts_and_prompts_generation.generation_main --all

  # Generate for specific ontologies
  python -m src.agents.scripts_and_prompts_generation.generation_main --ontosynthesis --ontomops

  # Use a specific model for all ontologies
  python -m src.agents.scripts_and_prompts_generation.generation_main --all --model gpt-4o

  # Skip certain steps
  python -m src.agents.scripts_and_prompts_generation.generation_main --all --skip-iterations --skip-mcp-config

  # Only generate scripts (skip prompts)
  python -m src.agents.scripts_and_prompts_generation.generation_main --all --skip-extraction-prompts --skip-kg-prompts

  # Generate only ITER3 extraction prompt
  python -m src.agents.scripts_and_prompts_generation.generation_main --ontosynthesis --skip-iterations --skip-underlying --skip-main --skip-kg-prompts --skip-mcp-config --iter3-only

  # Generate ITER2 and ITER3 extraction prompts only
  python -m src.agents.scripts_and_prompts_generation.generation_main --ontosynthesis --skip-iterations --skip-underlying --skip-main --skip-kg-prompts --skip-mcp-config --iter2-only --iter3-only

  # Generate only pre-extraction prompts (automatically skips all other steps)
  python -m src.agents.scripts_and_prompts_generation.generation_main --ontosynthesis --pre-extraction-only
        """
    )
    
    # Ontology selection
    parser.add_argument("--all", action="store_true", help="Generate for all ontologies in meta_task_config.json")
    parser.add_argument("--ontosynthesis", action="store_true", help="Generate for OntoSynthesis")
    parser.add_argument("--ontomops", action="store_true", help="Generate for OntoMOPs")
    parser.add_argument("--ontospecies", action="store_true", help="Generate for OntoSpecies")
    
    # Model selection
    parser.add_argument("--model", type=str, default=None, help="Override LLM model for all ontologies (e.g., gpt-4o, gpt-5)")
    
    # Generation mode
    parser.add_argument("--direct", action="store_true", help="Use direct LLM generation (faster, no MCP agent) - LLM outputs code directly to files")
    
    # Step control
    parser.add_argument("--skip-iterations", action="store_true", help="Skip iterations.json generation")
    parser.add_argument("--skip-underlying", action="store_true", help="Skip MCP underlying script generation")
    parser.add_argument("--skip-main", action="store_true", help="Skip MCP main script generation")
    parser.add_argument("--skip-extraction-prompts", action="store_true", help="Skip extraction prompts generation")
    parser.add_argument("--skip-kg-prompts", action="store_true", help="Skip KG building prompts generation")
    parser.add_argument("--skip-mcp-config", action="store_true", help="Skip MCP config JSON generation")
    
    # Iteration-specific control for extraction prompts
    parser.add_argument("--iter1-only", action="store_true", help="Generate extraction prompt for iteration 1 only")
    parser.add_argument("--iter2-only", action="store_true", help="Generate extraction prompt for iteration 2 only")
    parser.add_argument("--iter3-only", action="store_true", help="Generate extraction prompt for iteration 3 only")
    parser.add_argument("--iter4-only", action="store_true", help="Generate extraction prompt for iteration 4 only")
    parser.add_argument("--pre-extraction-only", action="store_true", help="Generate pre-extraction prompts only (skips all other steps)")
    
    args = parser.parse_args()
    
    # If pre-extraction-only mode, automatically set skip flags for all other steps
    if args.pre_extraction_only:
        args.skip_iterations = True
        args.skip_underlying = True
        args.skip_main = True
        args.skip_kg_prompts = True
        args.skip_mcp_config = True
        # Don't skip extraction prompts (we need them for pre-extraction)
        print("\n🎯 PRE-EXTRACTION ONLY MODE: Generating only pre-extraction prompts")
    
    # Validate arguments
    if not (args.all or args.ontosynthesis or args.ontomops or args.ontospecies):
        parser.print_help()
        print("\n❌ Error: Please specify --all or at least one ontology (--ontosynthesis, --ontomops, --ontospecies)")
        sys.exit(1)
    
    try:
        success = asyncio.run(main_async(args))
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        print("\n\n❌ Interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n\n❌ Fatal error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()

