#!/usr/bin/env python3
"""
MCP Main Script Creation Agent

This agent generates the main MCP interface script (main.py) that:
1. Exposes MCP underlying functions as tools
2. Creates clear, helpful instructions for each tool
3. Follows FastMCP patterns and best practices
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
from pathlib import Path
import time
import os


def load_design_principles() -> str:
    """Load the universal design principles documentation."""
    design_principles_path = "src/agents/mops/prompts/universal_mcp_underlying_script_design_principles.md"
    try:
        with open(design_principles_path, 'r', encoding='utf-8') as f:
            return f.read()
    except FileNotFoundError:
        # Fallback for environments / branches where the file is missing.
        return (
            "Universal MCP design principles (fallback):\n"
            "- Main MCP script should expose underlying functions as tools\n"
            "- Tool docs must be clear and usage-focused\n"
            "- Keep server startup minimal and robust\n"
        )


def load_prompt_template(prompt_name: str) -> str:
    """Load a prompt template from ape_generated_contents/meta_prompts/mcp_scripts/."""
    prompt_path = f"ape_generated_contents/meta_prompts/mcp_scripts/{prompt_name}"
    
    with open(prompt_path, 'r', encoding='utf-8') as f:
        return f.read()


def load_ontology(ontology_path: str) -> str:
    """Load the T-Box ontology from TTL file."""
    with open(ontology_path, 'r', encoding='utf-8') as f:
        return f.read()


def load_underlying_script(script_path: str) -> str:
    """Load the underlying MCP script."""
    with open(script_path, 'r', encoding='utf-8') as f:
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
        "gpt-4.1"  # Default model for main
    ))
    
    # Add extension ontologies
    for ext in config["ontologies"]["extensions"]:
        ontologies.append((
            ext["name"],
            ext["ttl_file"],
            ext.get("agent_model", "gpt-4.1")  # Use specified model or default
        ))
    
    return ontologies


# Prompt template will be loaded from markdown file at runtime:
# - main_script_prompt.md: ape_generated_contents/mcp_script_creation/main_script_prompt.md


async def create_main_script_with_retry(
    ontology_path: str,
    underlying_script_path: str,
    model_name: str = "gpt-4.1",
    ontology_short: str | None = None,
    max_retries: int = 3,
    retry_delay: int = 5
) -> str:
    """
    Create an MCP main interface script with retry logic for connection errors.
    
    Args:
        ontology_path: Path to the T-Box TTL file
        underlying_script_path: Path to the underlying MCP script
        model_name: LLM model to use (default: gpt-4.1)
        ontology_short: Short name of ontology (e.g., 'ontospecies')
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
            
            return await create_main_script(
                ontology_path=ontology_path,
                underlying_script_path=underlying_script_path,
                model_name=model_name,
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


async def create_main_script(
    ontology_path: str,
    underlying_script_path: str,
    model_name: str = "gpt-4o",
    ontology_short: str | None = None
) -> str:
    """
    Create an MCP main interface script for the given ontology.
    
    Args:
        ontology_path: Path to the T-Box TTL file
        underlying_script_path: Path to the underlying MCP script
        model_name: LLM model to use (default: gpt-4o)
        ontology_short: Short name of ontology (e.g., 'ontospecies')
    
    Returns:
        Response from the agent
    """
    # Compute output dir by ontology - same folder as underlying script
    ont = (ontology_short or "ontosynthesis").strip()
    # Get the directory of the underlying script
    underlying_dir = str(Path(underlying_script_path).parent.resolve())
    output_dir = underlying_dir
    # Include ontology name in script_name for correct routing in code_output
    script_name = f"{ont}_main.py"
    
    print(f"\n{'='*60}")
    print(f"Creating MCP Main Interface Script: {script_name}")
    print(f"Ontology: {ont}")
    print(f"Output Dir: {output_dir}")
    print(f"{'='*60}")
    
    # Load all required documents
    design_principles = load_design_principles()
    ontology_ttl = load_ontology(ontology_path)
    underlying_script = load_underlying_script(underlying_script_path)
    
    # Load prompt template from markdown file
    prompt_template = load_prompt_template("main_script_prompt.md")
    
    # Construct the prompt
    prompt = prompt_template.format(
        ontology_name=ont,
        script_name=script_name,
        output_dir=output_dir,
        design_principles=design_principles,
        ontology_ttl=ontology_ttl,
        underlying_script=underlying_script
    )
    
    # Initialize the agent with MCP tools
    model_config = ModelConfig(max_tokens=16000)
    
    agent = BaseAgent(
        model_name=model_name,
        remote_model=True,
        model_config=model_config,
        mcp_tools=["generic_operations"],  # Use generic file operations for script generation
        mcp_set_name="mcp_creation_mcp_configs.json"
    )
    
    try:
        # Run the agent with increased recursion limit
        print(f"📤 Sending prompt to LLM (this may take a few minutes)...")
        response = await agent.run(prompt, recursion_limit=50)
        
        # Extract text response from tuple (response, metadata)
        text_response = response[0] if isinstance(response, tuple) else response

        # Keep console output concise by default. Opt-in to verbose printing with:
        #   export GENERATION_VERBOSE=1
        if os.environ.get("GENERATION_VERBOSE", "").strip() in ("1", "true", "TRUE", "yes", "YES"):
            print(f"\n{'='*60}")
            print("AGENT RESPONSE (verbose):")
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
        raise


def main():
    """Main entry point for the script."""
    import argparse
    
    parser = argparse.ArgumentParser(description="Create MCP main interface scripts from underlying scripts")
    parser.add_argument("--model", default=None, help="LLM model to use (default: gpt-4.1 or from meta_task_config.json)")
    parser.add_argument("--ontology", default=None, help="Ontology short name (ontosynthesis, ontomops, ontospecies). If not provided, generates for all ontologies.")
    parser.add_argument("--underlying-script", help="Path to underlying script (auto-detected if not provided)")
    parser.add_argument("--all", action="store_true", help="Generate main.py for all ontologies from meta_task_config.json")
    
    args = parser.parse_args()
    
    # Determine which ontologies to process
    if args.all or args.ontology is None:
        # Generate for all ontologies from meta_task_config.json
        print(f"\n{'='*60}")
        print(f"MCP Main Script Creation - ALL ONTOLOGIES")
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
            
            # Auto-detect underlying script
            candidate_path = f"ai_generated_contents_candidate/scripts/{ont_name}/{ont_name}_creation.py"
            fallback_path = f"scripts/{ont_name}/{ont_name}_creation.py"
            
            if os.path.exists(candidate_path):
                underlying_script = candidate_path
            elif os.path.exists(fallback_path):
                underlying_script = fallback_path
            else:
                print(f"❌ Underlying script not found for {ont_name}. Tried:")
                print(f"   - {candidate_path}")
                print(f"   - {fallback_path}")
                print(f"⚠️  Skipping {ont_name}...\n")
                continue
            
            print(f"Model: {model_to_use}")
            print(f"Ontology: {ont_name}")
            print(f"TTL File: {ttl_file}")
            print(f"Underlying Script: {underlying_script}\n")
            
            if not os.path.exists(ttl_file):
                print(f"❌ T-Box not found: {ttl_file}")
                print(f"⚠️  Skipping {ont_name}...\n")
                continue
            
            try:
                asyncio.run(create_main_script_with_retry(
                    ontology_path=ttl_file,
                    underlying_script_path=underlying_script,
                    model_name=model_to_use,
                    ontology_short=ont_name,
                    max_retries=3,
                    retry_delay=5
                ))
                
                underlying_dir = str(Path(underlying_script).parent)
                print(f"\n✅ Successfully generated main.py for {ont_name}")
                print(f"📁 Output: {underlying_dir}/main.py\n")
            except Exception as e:
                print(f"\n❌ Failed to generate main.py for {ont_name} after retries: {e}")
                print(f"⚠️  Continuing with next ontology...\n")
                continue
        
        print(f"\n{'='*60}")
        print(f"✅ All ontologies processed!")
        print(f"{'='*60}\n")
    else:
        # Single ontology mode (original behavior)
        ont_key = args.ontology
        
        # Use specified model or default
        model_to_use = args.model if args.model else "gpt-4.1"
        
        # Resolve ontology short name to actual TTL path
        resolved_ontology = _ontology_tbox_path(ont_key)
        if not os.path.exists(resolved_ontology):
            print(f"❌ T-Box not found: {resolved_ontology}")
            sys.exit(1)
        
        # Auto-detect underlying script if not provided
        if args.underlying_script:
            underlying_script = args.underlying_script
        else:
            # Try ai_generated_contents_candidate first, then scripts
            candidate_path = f"ai_generated_contents_candidate/scripts/{ont_key}/{ont_key}_creation.py"
            fallback_path = f"scripts/{ont_key}/{ont_key}_creation.py"
            
            if os.path.exists(candidate_path):
                underlying_script = candidate_path
            elif os.path.exists(fallback_path):
                underlying_script = fallback_path
            else:
                print(f"❌ Underlying script not found. Tried:")
                print(f"   - {candidate_path}")
                print(f"   - {fallback_path}")
                print(f"\nPlease specify --underlying-script explicitly.")
                sys.exit(1)
        
        if not os.path.exists(underlying_script):
            print(f"❌ Underlying script not found: {underlying_script}")
            sys.exit(1)
        
        print(f"\n{'='*60}")
        print(f"MCP Main Script Creation")
        print(f"{'='*60}")
        print(f"Model: {model_to_use}")
        print(f"Ontology: {ont_key}")
        print(f"Underlying Script: {underlying_script}")
        print(f"{'='*60}\n")
        
        asyncio.run(create_main_script_with_retry(
            ontology_path=resolved_ontology,
            underlying_script_path=underlying_script,
            model_name=model_to_use,
            ontology_short=ont_key,
            max_retries=3,
            retry_delay=5
        ))
        
        underlying_dir = str(Path(underlying_script).parent)
        print(f"\n{'='*60}")
        print(f"✅ Main script generation complete!")
        print(f"📁 Output file: {underlying_dir}/main.py")
        print(f"   (Same directory as {Path(underlying_script).name})")
        print(f"{'='*60}\n")


if __name__ == "__main__":
    main()

