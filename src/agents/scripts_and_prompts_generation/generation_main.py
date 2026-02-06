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
import subprocess
import time
from pathlib import Path
from typing import List, Dict, Any
from tqdm import tqdm
import glob

# Add project root to path
project_root = Path(__file__).resolve().parents[3]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

# Import generation modules.
# IMPORTANT: Some agent-based modules depend on optional packages (e.g., langchain_openai).
# We keep this entrypoint usable for direct generation and skip-flows by lazily/optionally importing them.
_agent_import_error: dict[str, str] = {}

try:
    from src.agents.scripts_and_prompts_generation.iteration_creation_agent import create_iterations_json, _load_meta_task_config
except Exception as e:  # pragma: no cover
    create_iterations_json = None  # type: ignore[assignment]
    _load_meta_task_config = None  # type: ignore[assignment]
    _agent_import_error["iteration_creation_agent"] = str(e)

try:
    from src.agents.scripts_and_prompts_generation.mcp_underlying_script_creation_agent import create_underlying_script_with_retry
except Exception as e:  # pragma: no cover
    create_underlying_script_with_retry = None  # type: ignore[assignment]
    _agent_import_error["mcp_underlying_script_creation_agent"] = str(e)

try:
    from src.agents.scripts_and_prompts_generation.mcp_main_script_creation_agent import create_main_script_with_retry
except Exception as e:  # pragma: no cover
    create_main_script_with_retry = None  # type: ignore[assignment]
    _agent_import_error["mcp_main_script_creation_agent"] = str(e)

try:
    from src.agents.scripts_and_prompts_generation.task_extraction_prompt_creation_agent import generate_prompts_from_iterations
except Exception as e:  # pragma: no cover
    generate_prompts_from_iterations = None  # type: ignore[assignment]
    _agent_import_error["task_extraction_prompt_creation_agent"] = str(e)

try:
    from src.agents.scripts_and_prompts_generation.task_prompt_creation_agent import generate_kg_prompts_from_iterations
except Exception as e:  # pragma: no cover
    generate_kg_prompts_from_iterations = None  # type: ignore[assignment]
    _agent_import_error["task_prompt_creation_agent"] = str(e)

from src.agents.scripts_and_prompts_generation.ttl_parser import parse_ontology_ttl, format_class_properties_markdown

# Top-entity SPARQL generation (LLM) (optional)
try:
    from src.agents.scripts_and_prompts_generation.top_entity_sparql_generation_agent import (
        generate_top_entity_sparql_for_ontology,
    )
except Exception as e:  # pragma: no cover
    generate_top_entity_sparql_for_ontology = None  # type: ignore[assignment]
    _agent_import_error["top_entity_sparql_generation_agent"] = str(e)
import shutil
# Import direct generation functions (no agents/MCP)
from src.agents.scripts_and_prompts_generation.direct_script_generation import (
    generate_underlying_script_direct,
    generate_main_script_direct,
    generate_split_main_scripts_direct,
    generate_base_script_direct,
    generate_checks_script_direct,
    generate_relationships_script_direct,
    generate_entities_script_direct
)


def _require_optional(name: str, obj) -> None:
    """Raise a clear error when an optional generation module is unavailable."""
    if obj is not None:
        return
    err = _agent_import_error.get(name) or "Unknown import error"
    raise ModuleNotFoundError(
        f"Optional module '{name}' is unavailable: {err}\n"
        "Either install the missing dependency (see error above) or run with flags that skip this step / use direct generation."
    )

def _safe_unlink(p: Path) -> bool:
    """Delete a file if it exists. Return True if deleted."""
    try:
        if p.is_file() or p.is_symlink():
            p.unlink()
            return True
    except FileNotFoundError:
        return False
    except Exception:
        return False
    return False


def _delete_glob(parent: Path, pattern: str, *, keep_names: set[str] | None = None) -> int:
    """
    Delete files matching pattern under parent directory.
    Only deletes files (not directories) to keep cleanup safe.
    """
    keep_names = keep_names or set()
    deleted = 0
    if not parent.exists():
        return 0
    for p in parent.glob(pattern):
        if p.name in keep_names:
            continue
        if _safe_unlink(p):
            deleted += 1
    return deleted


def _clean_candidate_outputs_for_ontology(ontology_name: str, args) -> int:
    """
    Delete previously-generated candidate artifacts for steps that will run.
    Respects skip flags so we only delete things that are going to be regenerated.
    """
    deleted = 0

    # Step 0.5 outputs: top-entity parsing SPARQL (always runs; no skip flag today)
    sparql_path = Path("ai_generated_contents_candidate") / "sparqls" / ontology_name / "top_entity_parsing.sparql"
    if _safe_unlink(sparql_path):
        deleted += 1

    # Step 1 outputs: iterations.json
    if not args.skip_iterations:
        iter_path = Path("ai_generated_contents_candidate") / "iterations" / ontology_name / "iterations.json"
        if _safe_unlink(iter_path):
            deleted += 1

    # Step 2+3 outputs: scripts
    scripts_dir = Path("ai_generated_contents_candidate") / "scripts" / ontology_name
    if scripts_dir.exists():
        keep = {"__init__.py"}

        # Underlying scripts (direct: *_creation_{base,checks,relationships,entities_*.py}; agent: *_creation.py)
        if not args.skip_underlying:
            deleted += _delete_glob(scripts_dir, f"{ontology_name}_creation_*.py", keep_names=keep)
            deleted += _delete_glob(scripts_dir, f"{ontology_name}_creation.py", keep_names=keep)

            # Concise ontology structure cached for direct mode
            concise_md = Path("ai_generated_contents_candidate") / "ontology_structures" / f"{ontology_name}_concise.md"
            if _safe_unlink(concise_md):
                deleted += 1

        # Main scripts + stitching artifacts
        if not args.skip_main:
            # main.py and any split/stitch intermediates
            deleted += _delete_glob(scripts_dir, "main.py", keep_names=keep)
            deleted += _delete_glob(scripts_dir, "main_part_*.py", keep_names=keep)
            deleted += _delete_glob(scripts_dir, "main_attempt_*.py", keep_names=keep)
            deleted += _delete_glob(scripts_dir, "main_prompt_*.md", keep_names=keep)
            deleted += _delete_glob(scripts_dir, "main_stitch_prompt_attempt_*.md", keep_names=keep)
            deleted += _delete_glob(scripts_dir, "tbox_comment_summary.json", keep_names=keep)

    # Step 4+5 outputs: prompts (shared folder; delete only what will be regenerated)
    prompts_dir = Path("ai_generated_contents_candidate") / "prompts" / ontology_name
    if prompts_dir.exists():
        if not args.skip_extraction_prompts:
            if getattr(args, "pre_extraction_only", False):
                deleted += _delete_glob(prompts_dir, "PRE_EXTRACTION_*.md")
            else:
                deleted += _delete_glob(prompts_dir, "EXTRACTION_*.md")
                deleted += _delete_glob(prompts_dir, "PRE_EXTRACTION_*.md")
        if not args.skip_kg_prompts:
            deleted += _delete_glob(prompts_dir, "KG_BUILDING_*.md")

    return deleted


def clean_candidate_outputs(selected_ontologies: List[Dict[str, Any]], args) -> None:
    """Clean candidate output artifacts for selected ontologies, respecting skip flags."""
    print("\n" + "="*60)
    print("CLEANING PREVIOUS CANDIDATE OUTPUTS (SAFE CLEAN)")
    print("="*60)
    total_deleted = 0
    for ont in selected_ontologies:
        name = ont["name"]
        d = _clean_candidate_outputs_for_ontology(name, args)
        total_deleted += d
        print(f"   - {name}: deleted {d} file(s)")

    # Step 6 output: MCP config JSON (global file)
    if not args.skip_mcp_config:
        cfg_path = Path("configs") / "generated_ontology_mcps.json"
        if _safe_unlink(cfg_path):
            total_deleted += 1
            print(f"   - configs/generated_ontology_mcps.json: deleted")

    print(f"\n✅ Cleanup done. Deleted {total_deleted} file(s).\n")


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


def _docker_is_available() -> bool:
    """
    Return True if docker is installed and the daemon is reachable.
    This is used to decide whether agent-based script generation (which uses MCP tools) can run.
    """
    try:
        r = subprocess.run(
            ["docker", "info"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=3,
            check=False,
        )
        return r.returncode == 0
    except Exception:
        return False


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
                
                _require_optional("iteration_creation_agent", create_iterations_json)
                result_path = create_iterations_json([ttl_path], output_dir, meta_cfg=meta_cfg)
                print(f"✅ Generated: {result_path}")
            except Exception as e:
                print(f"❌ Failed to generate iterations for {name}: {e}")
                success = False
            
            pbar.update(1)
    
    return success


async def generate_underlying_scripts(
    ontologies: List[Dict[str, Any]],
    model_override: str = None,
    use_direct: bool = False,
    *,
    max_retries: int = 3,
):
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
                    
                    # Step 1: Generate foundational scripts (DIRECT LLM - no templates)
                    print(f"\n   [1/4] Generating check functions (direct LLM)...")
                    await generate_checks_script_direct(
                        ontology_path=ttl_file,
                        ontology_name=name,
                        output_dir=output_dir,
                        model_name=model,
                        max_retries=max_retries,
                    )

                    print(f"\n   [2/4] Generating relationship functions (direct LLM)...")
                    await generate_relationships_script_direct(
                        ontology_path=ttl_file,
                        ontology_name=name,
                        output_dir=output_dir,
                        model_name=model,
                        max_retries=max_retries,
                    )

                    print(f"\n   [3/4] Generating base utilities (direct LLM)...")
                    await generate_base_script_direct(
                        ontology_path=ttl_file,
                        ontology_name=name,
                        output_dir=output_dir,
                        model_name=model,
                        max_retries=max_retries
                    )
                    
                    # Step 2: Generate entity creation scripts (DIRECT LLM)
                    # Rationale: entity scripts must consider related external concepts mentioned in the T-Box
                    # (e.g., OM-2 quantities such as Temperature) and derive unit mappings from the OM-2 mock T-Box.
                    print(f"\n   [4/4] Generating entity creation scripts (direct LLM)...")

                    base_script_path = str(Path(output_dir) / f"{name}_creation_base.py")
                    checks_script_path = str(Path(output_dir) / f"{name}_creation_checks.py")
                    relationships_script_path = str(Path(output_dir) / f"{name}_creation_relationships.py")

                    entity_scripts = await generate_entities_script_direct(
                        ontology_path=ttl_file,
                        ontology_name=name,
                        output_dir=output_dir,
                        base_script_path=base_script_path,
                        checks_script_path=checks_script_path,
                        relationships_script_path=relationships_script_path,
                        model_name=model,
                        max_retries=max_retries,
                    )
                    print(f"   ✅ Generated {len(entity_scripts)} entity creation scripts (direct LLM)")
                else:
                    # Agent-based generation (with MCP tools)
                    _require_optional("mcp_underlying_script_creation_agent", create_underlying_script_with_retry)
                    await create_underlying_script_with_retry(
                        ontology_path=ttl_file,
                        script_name=script_name,
                        version=1,
                        iteration=1,
                        model_name=model,
                        split_by_steps=False,
                        ontology_short=name,
                        max_retries=max_retries,
                        retry_delay=5
                    )
                print(f"✅ Generated underlying script for {name}")
            except Exception as e:
                print(f"❌ Failed to generate underlying script for {name}: {e}")
                success = False
            
            pbar.update(1)
    
    return success


async def generate_main_scripts(
    ontologies: List[Dict[str, Any]],
    model_override: str = None,
    use_direct: bool = False,
    *,
    max_retries: int = 3,
):
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
                    # Two-step main generation: LLM generates fragments (core + relationships), then LLM stitches a single main.py.
                    await generate_split_main_scripts_direct(
                        ontology_path=ttl_file,
                        ontology_name=name,
                        checks_script_path=checks_path,
                        relationships_script_path=relationships_path,
                        base_script_path=base_path,
                        entity_script_paths=entity_scripts,  # List of entity creation scripts
                        output_dir=output_dir,
                        model_name=model,
                        max_retries=max_retries,
                    )
                else:
                    # Agent-based generation (with MCP tools)
                    _require_optional("mcp_main_script_creation_agent", create_main_script_with_retry)
                    await create_main_script_with_retry(
                        ontology_path=ttl_file,
                        underlying_script_path=candidate_path,
                        model_name=model,
                        ontology_short=name,
                        max_retries=max_retries,
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
        _require_optional("task_extraction_prompt_creation_agent", generate_prompts_from_iterations)
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
                _require_optional("task_prompt_creation_agent", generate_kg_prompts_from_iterations)
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
    start_ts = time.perf_counter()
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

    # Ensure shared universal utilities are present and up-to-date for any mode
    # (including --main-only). This also injects ontology-derived OM-2 unit constraints.
    ensure_universal_utils()

    # Clean previously generated artifacts for steps that will run (safe clean).
    # This makes repeated runs deterministic and avoids stale outputs.
    if not getattr(args, "no_clean", False):
        clean_candidate_outputs(selected_ontologies, args)
    else:
        print("\n⏭️  Skipping cleanup (--no-clean)")
    
    # Track overall success
    all_success = True
    
    # Step 0: Parse all ontology TTL files to generate/update parsed markdown
    ttl_parse_success = parse_all_ontology_ttls(selected_ontologies)
    all_success = all_success and ttl_parse_success
    if not ttl_parse_success:
        print("⚠️  Warning: TTL parsing had errors, but continuing with generation...\n")

    # Step 0.5: Generate top-entity parsing SPARQL (used by downstream pipeline steps).
    # Writes to ai_generated_contents_candidate/sparqls/<ontology>/top_entity_parsing.sparql
    # and mirrors to ai_generated_contents/sparqls/<ontology>/top_entity_parsing.sparql.
    print("\n" + "="*60)
    print("STEP 0.5: GENERATING TOP-ENTITY PARSING SPARQL")
    print("="*60)
    sparql_success = True
    for ont in selected_ontologies:
        ont_name = ont["name"]
        model = args.model if args.model else ont.get("model", "gpt-4o")
        try:
            _require_optional("top_entity_sparql_generation_agent", generate_top_entity_sparql_for_ontology)
            print(f"🧾 Generating top-entity SPARQL for {ont_name} (model: {model})")
            generate_top_entity_sparql_for_ontology(ont_name, model=model)
        except Exception as e:
            print(f"⚠️  Failed to generate top-entity SPARQL for {ont_name}: {e}")
            # Fallback: if a production SPARQL already exists, copy it into candidate tree
            try:
                src = Path("ai_generated_contents") / "sparqls" / ont_name / "top_entity_parsing.sparql"
                dst_dir = Path("ai_generated_contents_candidate") / "sparqls" / ont_name
                dst_dir.mkdir(parents=True, exist_ok=True)
                dst = dst_dir / "top_entity_parsing.sparql"
                if src.exists():
                    shutil.copy2(src, dst)
                    print(f"   ✅ Fallback copied existing SPARQL → {dst}")
                else:
                    print(f"   ❌ No existing SPARQL to copy from {src}")
                    sparql_success = False
            except Exception as e2:
                print(f"   ❌ Fallback copy failed: {e2}")
                sparql_success = False
    all_success = all_success and sparql_success
    
    # Step 1: Generate iterations.json
    if not args.skip_iterations:
        success = await generate_iterations(selected_ontologies, meta_cfg)
        all_success = all_success and success
    else:
        print("\n⏭️  Skipping iterations generation (--skip-iterations)")
    
    # Step 2: Generate MCP underlying scripts
    if not args.skip_underlying:
        success = await generate_underlying_scripts(
            selected_ontologies,
            model_override=args.model,
            use_direct=args.direct,
            max_retries=max(1, int(args.max_retries)),
        )
        all_success = all_success and success
    else:
        print("\n⏭️  Skipping underlying script generation (--skip-underlying)")
    
    # Step 3: Generate MCP main scripts
    if not args.skip_main:
        success = await generate_main_scripts(
            selected_ontologies,
            model_override=args.model,
            use_direct=args.direct,
            max_retries=max(1, int(args.max_retries)),
        )
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
    
    # Final summary + total runtime
    elapsed_s = time.perf_counter() - start_ts
    print("\n" + "="*80)
    if all_success:
        print("✅ GENERATION PIPELINE COMPLETE - ALL STEPS SUCCEEDED")
    else:
        print("⚠️  GENERATION PIPELINE COMPLETE - SOME STEPS HAD ERRORS")
    print(f"⏱️  Total runtime: {elapsed_s:.1f}s")
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

  # Regenerate ONLY main.py from existing candidate scripts (skips all previous steps)
  python -m src.agents.scripts_and_prompts_generation.generation_main --ontosynthesis --main-only --model gpt-5.2
        """
    )
    
    # Ontology selection
    parser.add_argument("--all", action="store_true", help="Generate for all ontologies in meta_task_config.json")
    parser.add_argument("--ontosynthesis", action="store_true", help="Generate for OntoSynthesis")
    parser.add_argument("--ontomops", action="store_true", help="Generate for OntoMOPs")
    parser.add_argument("--ontospecies", action="store_true", help="Generate for OntoSpecies")
    
    # Model selection
    parser.add_argument("--model", type=str, default=None, help="Override LLM model for all ontologies (e.g., gpt-4o, gpt-5)")

    # Retry control
    parser.add_argument(
        "--max-retries",
        type=int,
        default=3,
        help="Max retries for LLM-based generation substeps (direct/agent). Increase if validation failures persist.",
    )
    
    # Generation mode
    parser.add_argument(
        "--direct",
        action="store_true",
        help="Use direct LLM generation (no MCP agent) - LLM outputs code directly to files. "
             "NOTE: for script generation this is now the default; use --agent-scripts to force agent/MCP mode."
    )
    parser.add_argument(
        "--agent-scripts",
        action="store_true",
        help="Force MCP/agent-based generation for underlying/main scripts (requires Docker). "
             "By default, script generation uses direct LLM output."
    )
    
    # Step control
    parser.add_argument("--skip-iterations", action="store_true", help="Skip iterations.json generation")
    parser.add_argument("--skip-underlying", action="store_true", help="Skip MCP underlying script generation")
    parser.add_argument("--skip-main", action="store_true", help="Skip MCP main script generation")
    parser.add_argument("--skip-extraction-prompts", action="store_true", help="Skip extraction prompts generation")
    parser.add_argument("--skip-kg-prompts", action="store_true", help="Skip KG building prompts generation")
    parser.add_argument("--skip-mcp-config", action="store_true", help="Skip MCP config JSON generation")
    parser.add_argument("--no-clean", action="store_true", help="Do not delete previous candidate outputs before generating")

    # Convenience modes
    parser.add_argument(
        "--main-only",
        action="store_true",
        help="Regenerate ONLY MCP main.py using existing candidate scripts under ai_generated_contents_candidate/scripts/<ontology>/ "
             "(automatically skips iterations/underlying/prompts/mcp-config)."
    )
    
    # Iteration-specific control for extraction prompts
    parser.add_argument("--iter1-only", action="store_true", help="Generate extraction prompt for iteration 1 only")
    parser.add_argument("--iter2-only", action="store_true", help="Generate extraction prompt for iteration 2 only")
    parser.add_argument("--iter3-only", action="store_true", help="Generate extraction prompt for iteration 3 only")
    parser.add_argument("--iter4-only", action="store_true", help="Generate extraction prompt for iteration 4 only")
    parser.add_argument("--pre-extraction-only", action="store_true", help="Generate pre-extraction prompts only (skips all other steps)")
    
    args = parser.parse_args()

    # If main-only mode, automatically set skip flags for all other steps.
    # This will reuse existing candidate scripts (checks/base/relationships/entities_*.py) and only rebuild main.py.
    if args.main_only:
        args.skip_iterations = True
        args.skip_underlying = True
        args.skip_extraction_prompts = True
        args.skip_kg_prompts = True
        args.skip_mcp_config = True
        # Don't skip main (we want to regenerate it)
        args.skip_main = False
        print("\n🎯 MAIN-ONLY MODE: Regenerating only MCP main.py using existing candidate scripts")
    
    # If pre-extraction-only mode, automatically set skip flags for all other steps
    if args.pre_extraction_only:
        args.skip_iterations = True
        args.skip_underlying = True
        args.skip_main = True
        args.skip_kg_prompts = True
        args.skip_mcp_config = True
        # Don't skip extraction prompts (we need them for pre-extraction)
        print("\n🎯 PRE-EXTRACTION ONLY MODE: Generating only pre-extraction prompts")

    # Default behavior: for scripts (steps 2+3), use direct generation unless the user forces agent mode.
    wants_scripts = (not args.skip_underlying) or (not args.skip_main)
    if wants_scripts and not args.agent_scripts:
        # We treat scripts as direct-by-default to avoid MCP/Docker coupling during code output.
        args.direct = True

    # If the user forced agent mode, fail fast (or warn) when Docker isn't available.
    if wants_scripts and args.agent_scripts and not _docker_is_available():
        print("\n❌ Docker is not available (or daemon not running).")
        print("   You requested --agent-scripts, but agent-based MCP script generation requires Docker.")
        print("   Start Docker or rerun without --agent-scripts (direct-by-default).\n")
        sys.exit(2)
    
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

