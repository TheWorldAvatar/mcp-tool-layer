#!/usr/bin/env python3
"""
MCP Underlying Script Creation Agent

This agent generates domain-specific MCP underlying scripts from:
1. Universal utility functions (domain-agnostic)
2. Universal design principles (domain-agnostic)
3. T-Box ontology (domain-specific)
"""

import os
import sys

# Add project root to path
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from models.BaseAgent import BaseAgent
from models.ModelConfig import ModelConfig
import asyncio
from typing import Optional, List, Dict
from pathlib import Path
import time


def load_design_principles() -> str:
    """Load the universal design principles documentation."""
    design_principles_path = "src/agents/mops/prompts/universal_mcp_underlying_script_design_principles.md"
    
    with open(design_principles_path, 'r', encoding='utf-8') as f:
        design_principles = f.read()
    
    return design_principles


def load_prompt_template(prompt_name: str) -> str:
    """Load a prompt template from ape_generated_contents/meta_prompts/mcp_scripts/."""
    prompt_path = f"ape_generated_contents/meta_prompts/mcp_scripts/{prompt_name}"
    
    with open(prompt_path, 'r', encoding='utf-8') as f:
        return f.read()


def load_task_division_plan() -> dict:
    """Load the task division plan JSON."""
    plan_path = "configs/task_division_plan.json"
    
    with open(plan_path, 'r', encoding='utf-8') as f:
        import json
        plan = json.load(f)
    
    return plan


def load_ontology(ontology_path: str) -> str:
    """Load the T-Box ontology from TTL file."""
    with open(ontology_path, 'r', encoding='utf-8') as f:
        return f.read()


def _ontology_tbox_path(name_or_path: str) -> str:
    """Map ontology short names to TTL file paths (or return path if already a file)."""
    mapping = {
        "ontosynthesis": "data/ontologies/ontosynthesis.ttl",
        "ontomops": "data/ontologies/ontomops-subgraph.ttl",
        "ontospecies": "data/ontologies/ontospecies-subgraph.ttl",
    }
    return mapping.get(name_or_path, name_or_path)


def load_meta_task_config() -> dict:
    """Load the meta task configuration from ape_generated_contents/meta_task_config.json."""
    config_path = "ape_generated_contents/meta_task_config.json"
    
    with open(config_path, 'r', encoding='utf-8') as f:
        import json
        config = json.load(f)
    
    return config


def get_all_ontologies_from_config() -> list:
    """Get all ontology configurations from meta_task_config.json.
    
    Returns:
        List of tuples: [(name, ttl_file, model), ...]
    """
    config = load_meta_task_config()
    ontologies = []
    
    # Add main ontology
    main = config["ontologies"]["main"]
    ontologies.append((
        main["name"],
        main["ttl_file"],
        "gpt-5"  # Default model for main
    ))
    
    # Add extension ontologies
    for ext in config["ontologies"]["extensions"]:
        ontologies.append((
            ext["name"],
            ext["ttl_file"],
            ext.get("agent_model", "gpt-4o")  # Use specified model or default
        ))
    
    return ontologies


# Prompt templates are now loaded from markdown files at runtime:
# - full_script_prompt.md: ape_generated_contents/mcp_script_creation/full_script_prompt.md
# - step_by_step_prompt.md: ape_generated_contents/mcp_script_creation/step_by_step_prompt.md
#
# This allows easy editing and version control of prompts without modifying Python code.


async def create_step_functions(
    step_data: dict,
    ontology_path: str,
    script_name: str,
    version: int,
    iteration: int = 1,
    model_name: str = "gpt-4.1"
) -> str:
    """
    Create functions for a specific step in the task division plan.
    
    Args:
        step_data: Step information from task_division_plan.json
        ontology_path: Path to the T-Box TTL file
        script_name: Name of the output script
        version: Version number for output directory
        iteration: Iteration number for refinement
        model_name: LLM model to use
    
    Returns:
        Response from the agent
    """
    step_number = step_data['step_number']
    step_name = step_data['step_name']
    
    print(f"\n{'='*60}")
    print(f"Creating Functions for Step {step_number}: {step_name}")
    print(f"Version: {version} | Iteration: {iteration}")
    print(f"{'='*60}")
    
    # Load required documents
    design_principles = load_design_principles()
    ontology_ttl = load_ontology(ontology_path)
    # Derive ontology short name and output dir
    ont_short = "ontosynthesis"
    path_l = ontology_path.lower()
    if "ontomops" in path_l:
        ont_short = "ontomops"
    elif "ontospecies" in path_l:
        ont_short = "ontospecies"
    output_dir = str((Path.cwd() / "scripts" / ont_short).resolve())
    
    # Format step information
    classes_info = "\n".join([
        f"- **{inst['class']}**: {inst['description']} (Cardinality: {inst['cardinality']})"
        for inst in step_data['instances_to_create']
    ])
    
    relations_info = "\n".join([
        f"- **{rel['property']}**: {rel['domain']} → {rel['range']}\n  {rel['description']}"
        for rel in step_data['relations_to_establish']
    ])
    
    extraction_info = "\n".join([f"- {info}" for info in step_data['information_to_extract']])
    constraints_info = "\n".join([f"- {constraint}" for constraint in step_data['constraints']])
    
    # Load prompt template from markdown file
    prompt_template = load_prompt_template("step_by_step_prompt.md")
    
    # Construct the prompt
    prompt = prompt_template.format(
        step_number=step_number,
        step_name=step_name,
        script_name=script_name,
        output_dir=output_dir,
        ontology_name=ont_short,
        goal=step_data['goal'],
        classes_info=classes_info,
        relations_info=relations_info,
        extraction_info=extraction_info,
        constraints_info=constraints_info,
        design_principles=design_principles,
        ontology_ttl=ontology_ttl
    )
    
    # Initialize the agent
    model_config = ModelConfig(max_tokens=16000)
    agent = BaseAgent(
        model_name=model_name,
        remote_model=True,
        model_config=model_config,
        mcp_tools=["generic_operations"],
        mcp_set_name="mcp_creation_mcp_configs.json"
    )
    
    try:
        print(f"📤 Sending prompt to LLM for Step {step_number}...")
        response = await agent.run(prompt, recursion_limit=50)
        
        print(f"\n✅ Successfully created functions for Step {step_number}")
        print(f"📁 Output file: sandbox/code/mcp_underlying_script_creation/{version}/step{step_number}_{script_name}.py")
        
        return response
        
    except Exception as e:
        error_msg = str(e)
        print(f"\n❌ Error creating Step {step_number} functions:")
        print(f"   Error type: {type(e).__name__}")
        print(f"   Error message: {error_msg}")
        raise


async def create_underlying_script_with_retry(
    ontology_path: str,
    script_name: str,
    version: int,
    iteration: int = 1,
    model_name: str = "gpt-5",
    split_by_steps: bool = False,
    ontology_short: str | None = None,
    max_retries: int = 3,
    retry_delay: int = 5
) -> str:
    """
    Create an MCP underlying script with retry logic for connection errors.
    
    Args:
        ontology_path: Path to the T-Box TTL file
        script_name: Name of the output script
        version: Version number for output directory
        iteration: Iteration number for refinement (default: 1)
        model_name: LLM model to use (default: gpt-5)
        split_by_steps: If True, generate functions step-by-step
        ontology_short: Short name of ontology
        max_retries: Maximum number of retry attempts (default: 3)
        retry_delay: Delay in seconds between retries (default: 5)
    
    Returns:
        Response from the agent
    """
    last_exception = None
    
    for attempt in range(1, max_retries + 1):
        try:
            if attempt > 1:
                print(f"\n🔄 Retry attempt {attempt}/{max_retries}...")
                time.sleep(retry_delay)
            
            return await create_underlying_script(
                ontology_path=ontology_path,
                script_name=script_name,
                version=version,
                iteration=iteration,
                model_name=model_name,
                split_by_steps=split_by_steps,
                ontology_short=ontology_short
            )
        except Exception as e:
            last_exception = e
            error_type = type(e).__name__
            
            # Check if it's a retryable error (connection errors, timeouts, etc.)
            retryable_errors = [
                "APIConnectionError",
                "ConnectionError",
                "Timeout",
                "TimeoutError",
                "APITimeoutError",
                "RateLimitError"
            ]
            
            is_retryable = any(err in error_type for err in retryable_errors)
            
            if is_retryable and attempt < max_retries:
                print(f"⚠️  {error_type}: {str(e)}")
                print(f"⏳ Waiting {retry_delay} seconds before retry...")
            else:
                # Not retryable or last attempt - re-raise
                raise
    
    # If we get here, all retries failed
    raise last_exception


async def create_underlying_script(
    ontology_path: str,
    script_name: str,
    version: int,
    iteration: int = 1,
    model_name: str = "gpt-5",
    split_by_steps: bool = False,
    ontology_short: str | None = None
) -> str:
    """
    Create an MCP underlying script for the given ontology.
    
    Args:
        ontology_path: Path to the T-Box TTL file
        script_name: Name of the output script
        version: Version number for output directory
        iteration: Iteration number for refinement (default: 1)
        model_name: LLM model to use (default: gpt-5)
        split_by_steps: If True, generate functions step-by-step according to task_division_plan.json
    
    Returns:
        Path to the generated script
    """
    # Compute output base dir by ontology
    ont = (ontology_short or "ontosynthesis").strip()
    output_dir = str((Path.cwd() / "ai_generated_contents_candidate" / "scripts" / ont).resolve())

    if split_by_steps:
        # Load task division plan and generate step by step
        plan = load_task_division_plan()
        print(f"\n{'='*60}")
        print(f"Creating MCP Script Step-by-Step")
        print(f"Total Steps: {plan['metadata']['total_steps']}")
        print(f"Version: {version} | Iteration: {iteration}")
        print(f"Output Dir: {output_dir}")
        print(f"{'='*60}")
        
        responses = []
        for step in plan['steps']:
            response = await create_step_functions(
                step_data=step,
                ontology_path=ontology_path,
                script_name=f"step{step['step_number']}_{script_name}",
                version=version,
                iteration=iteration,
                model_name=model_name
            )
            responses.append(response)
        
        return "\n\n".join(responses)
    
    # Original full script generation
    print(f"\n{'='*60}")
    print(f"Creating Complete MCP Underlying Script: {script_name}")
    print(f"Version: {version} | Iteration: {iteration}")
    print(f"Output Dir: {output_dir}")
    print(f"{'='*60}")
    
    # Load all required documents
    design_principles = load_design_principles()
    ontology_ttl = load_ontology(ontology_path)
    
    # Load prompt template from markdown file
    prompt_template = load_prompt_template("full_script_prompt.md")
    
    # Construct the prompt
    prompt = prompt_template.format(
        ontology_name=ont,
        script_name=script_name,
        output_dir=output_dir,
        design_principles=design_principles,
        ontology_ttl=ontology_ttl
    )
    
    # Initialize the agent with MCP tools
    # Set max_tokens to maximum to allow unlimited output
    model_config = ModelConfig(max_tokens=16000)  # Maximum allowed by most models
    
    agent = BaseAgent(
        model_name=model_name,
        remote_model=True,
        model_config=model_config,
        mcp_tools=["generic_operations"],  # Use generic file operations for script generation
        mcp_set_name="mcp_creation_mcp_configs.json"
    )
    
    try:
        # Run the agent with increased recursion limit for complex tasks
        print(f"📤 Sending prompt to LLM (this may take several minutes)...")
        response = await agent.run(prompt, recursion_limit=50)
        
        # Extract text response from tuple (response, metadata)
        text_response = response[0] if isinstance(response, tuple) else response
        
        print(f"\n{'='*60}")
        print(f"AGENT RESPONSE:")
        print(f"{'='*60}")
        print(text_response)
        print(f"{'='*60}\n")
        
        print(f"\n✅ Successfully created: {script_name}")
        print(f"📁 Output directory: {output_dir}/")
        
        return response
        
    except Exception as e:
        error_msg = str(e)
        print(f"\n❌ Error creating {script_name}:")
        print(f"   Error type: {type(e).__name__}")
        print(f"   Error message: {error_msg}")
        
        # Check for specific error types
        if "JSONDecodeError" in error_msg or "Expecting value" in error_msg:
            print("\n💡 Suggestion: This appears to be an API response parsing error.")
            print("   Possible causes:")
            print("   - API timeout or rate limit")
            print("   - Response too large")
            print("   - Malformed API response")
            print("\n   Try:")
            print("   1. Wait a few minutes and retry")
            print("   2. Check your API key and quota")
            print("   3. Use a different model (e.g., gpt-4o instead of gpt-5)")
        
        raise




def main():
    """Main entry point for the script."""
    import argparse
    
    parser = argparse.ArgumentParser(description="Create MCP underlying scripts from T-Box ontologies")
    # Version/iteration removed from CLI; use internal defaults
    parser.add_argument("--model", default=None, help="LLM model to use (default: from meta_task_config.json or gpt-5)")
    parser.add_argument("--ontology", default=None, help="Path to T-Box TTL file or ontology short name. If not provided, generates for all ontologies in meta_task_config.json")
    parser.add_argument("--script-name", default=None, help="Output script name (auto-generated if not provided)")
    parser.add_argument("--split", action="store_true", help="Generate functions step-by-step according to task_division_plan.json")
    parser.add_argument("--all", action="store_true", help="Generate scripts for all ontologies from meta_task_config.json")
    
    args = parser.parse_args()
    # Internal defaults
    version = 1
    iteration = 1
    
    # Determine which ontologies to process
    if args.all or args.ontology is None:
        # Generate for all ontologies from meta_task_config.json
        print(f"\n{'='*60}")
        print(f"MCP Underlying Script Creation - ALL ONTOLOGIES")
        print(f"{'='*60}")
        print(f"Loading ontologies from meta_task_config.json...")
        
        try:
            ontologies = get_all_ontologies_from_config()
            print(f"Found {len(ontologies)} ontologies to process:")
            for name, ttl, model in ontologies:
                print(f"  - {name} (model: {model})")
            print()
        except Exception as e:
            print(f"❌ Failed to load meta_task_config.json: {e}")
            sys.exit(1)
        
        # Process each ontology
        for idx, (ont_name, ttl_file, ont_model) in enumerate(ontologies, 1):
            print(f"\n{'='*60}")
            print(f"Processing [{idx}/{len(ontologies)}]: {ont_name}")
            print(f"{'='*60}")
            
            # Use specified model or ontology-specific model
            model_to_use = args.model if args.model else ont_model
            script_name = f"{ont_name}_creation.py"
            
            print(f"Version: {version}")
            print(f"Iteration: {iteration}")
            print(f"Model: {model_to_use}")
            print(f"Ontology: {ont_name}")
            print(f"TTL File: {ttl_file}")
            print(f"Script: {script_name}")
            print(f"Mode: {'Step-by-step' if args.split else 'Full script'}\n")
            
            if not os.path.exists(ttl_file):
                print(f"❌ T-Box not found: {ttl_file}")
                print(f"⚠️  Skipping {ont_name}...\n")
                continue
            
            try:
                asyncio.run(create_underlying_script_with_retry(
                    ontology_path=ttl_file,
                    script_name=script_name,
                    version=version,
                    iteration=iteration,
                    model_name=model_to_use,
                    split_by_steps=args.split,
                    ontology_short=ont_name,
                    max_retries=3,
                    retry_delay=5
                ))
                
                print(f"\n✅ Successfully generated script for {ont_name}")
                print(f"📁 Output: ai_generated_contents_candidate/scripts/{ont_name}/{script_name}\n")
            except Exception as e:
                print(f"\n❌ Failed to generate script for {ont_name} after retries: {e}")
                print(f"⚠️  Continuing with next ontology...\n")
                continue
        
        print(f"\n{'='*60}")
        print(f"✅ All ontologies processed!")
        print(f"{'='*60}\n")
    else:
        # Single ontology mode (original behavior)
        ont_key = args.ontology
        
        # Auto-generate script name if not provided
        if args.script_name:
            auto_script = args.script_name
        else:
            base = ont_key if ont_key in ("ontosynthesis", "ontomops", "ontospecies") else "ontosynthesis"
            auto_script = f"{base}_creation.py"
        
        # Use specified model or default
        model_to_use = args.model if args.model else "gpt-5"
        
        print(f"\n{'='*60}")
        print(f"MCP Underlying Script Creation")
        print(f"{'='*60}")
        print(f"Version: {version}")
        print(f"Iteration: {iteration}")
        print(f"Model: {model_to_use}")
        print(f"Ontology: {ont_key}")
        print(f"Script: {auto_script}")
        print(f"Mode: {'Step-by-step' if args.split else 'Full script'}\n")
        
        # Resolve ontology short name to actual TTL path if needed
        resolved_ontology = _ontology_tbox_path(ont_key)
        if not os.path.exists(resolved_ontology):
            print(f"❌ T-Box not found: {resolved_ontology}")
            sys.exit(1)

        asyncio.run(create_underlying_script_with_retry(
            ontology_path=resolved_ontology,
            script_name=auto_script,
            version=version,
            iteration=iteration,
            model_name=model_to_use,
            split_by_steps=args.split,
            ontology_short=ont_key if ont_key in ("ontosynthesis", "ontomops", "ontospecies") else None,
            max_retries=3,
            retry_delay=5
        ))
        
        print(f"\n{'='*60}")
        print(f"✅ Script generation complete!")
        if args.split:
            print(f"📁 Output files: ai_generated_contents_candidate/scripts/{ont_key}/step*_{auto_script}")
        else:
            print(f"📁 Output file: ai_generated_contents_candidate/scripts/{ont_key}/{auto_script}")
        print(f"{'='*60}\n")


if __name__ == "__main__":
    main()

