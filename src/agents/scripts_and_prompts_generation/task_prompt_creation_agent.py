#!/usr/bin/env python3
"""
task_prompt_creation_agent.py

Generates MCP iteration prompts from a task division plan JSON file.
Produces prompts similar to the structure in src/agents/mops/dynamic_mcp/prompts/prompts.py

Each step in the plan becomes one iteration prompt (MCP_PROMPT_ITER_N).
The agent can run multiple prompts in parallel.
"""

import os
import json
import argparse
import asyncio
from pathlib import Path
from typing import Dict, List, Any
from dotenv import load_dotenv

from models.LLMCreator import LLMCreator
from models.ModelConfig import ModelConfig

# -------- Meta-Prompt Loader --------
def load_meta_prompt(prompt_path: str) -> str:
    """Load meta-prompt from ape_generated_contents/meta_prompts/"""
    full_path = Path(f"ape_generated_contents/meta_prompts/{prompt_path}")
    if not full_path.exists():
        raise FileNotFoundError(f"Meta-prompt not found: {full_path}")
    return full_path.read_text(encoding='utf-8')

# -------- Config --------
PLAN_PATH = "configs/task_division_plan.json"
TBOX_PATH = "data/ontologies/ontosynthesis.ttl"
OUTPUT_DIR_BASE = "sandbox/prompts"
MODEL = os.environ.get("PROMPT_CREATION_MODEL", "gpt-4.1")
MAX_RETRIES = 3
ITERATIONS_BASE = "ai_generated_contents_candidate/iterations"
PROMPTS_CANDIDATE_BASE = "ai_generated_contents_candidate/prompts"

# -------- Load environment --------
load_dotenv(override=True)

# -------- Load Generic Templates from Markdown Files --------
PROMPT_CORE_TEMPLATE = load_meta_prompt("kg_building/prompt_core.md")

# Load hardcoded template for ITER 2, 3, 4 KG building prompts
def load_kg_building_iter_template() -> str:
    """Load the hardcoded template for ITER 2, 3, 4 KG building prompts."""
    template_path = Path("ape_generated_contents/prompts/kg_building_iter_template.md")
    if not template_path.exists():
        raise FileNotFoundError(f"Template not found: {template_path}")
    return template_path.read_text(encoding='utf-8')

KG_BUILDING_ITER_TEMPLATE = load_kg_building_iter_template()

# -------- ITER1 KG Building Specific Templates --------
KG_ITER1_SYS = load_meta_prompt("kg_building/iter1_system.md")

KG_ITER1_USER_TMPL = load_meta_prompt("kg_building/iter1_user.md")

# -------- Extension KG Building Templates --------
KG_EXTENSION_SYS = load_meta_prompt("kg_building/extension_system.md")

KG_EXTENSION_USER_TMPL = load_meta_prompt("kg_building/extension_user.md")

IDENTIFICATION_HEADER = load_meta_prompt("kg_building/identification_header.md")

FOOTER_WITHOUT_ENTITY = load_meta_prompt("kg_building/footer_without_entity.md")

FOOTER_WITH_ENTITY = load_meta_prompt("kg_building/footer_with_entity.md")

# -------- System Prompt --------
SYSTEM_PROMPT = load_meta_prompt("kg_building/kg_system.md")

# -------- User Prompt Template --------
USER_PROMPT_TEMPLATE = load_meta_prompt("kg_building/kg_user.md")

# -------- Helper Functions --------

def load_tbox(tbox_path: Path) -> str:
    """Load T-Box TTL file."""
    with open(tbox_path, "r", encoding="utf-8") as f:
        return f.read()


def load_plan(plan_path: Path) -> Dict[str, Any]:
    """Load task division plan JSON."""
    with open(plan_path, "r", encoding="utf-8") as f:
        return json.load(f)


def _ontology_tbox_path(name: str) -> str:
    if name == "ontosynthesis":
        return "data/ontologies/ontosynthesis.ttl"
    if name == "ontomops":
        return "data/ontologies/ontomops-subgraph.ttl"
    if name == "ontospecies":
        return "data/ontologies/ontospecies-subgraph.ttl"
    return name


def _iterations_path_for(name: str) -> Path:
    return Path(ITERATIONS_BASE) / name / "iterations.json"


def _candidate_prompt_path_from(iter_prompt_path: str) -> Path:
    # Map ai_generated_contents/prompts/... -> ai_generated_contents_candidate/prompts/...
    if iter_prompt_path.startswith("ai_generated_contents/prompts/"):
        return Path(iter_prompt_path.replace("ai_generated_contents/prompts/", f"{PROMPTS_CANDIDATE_BASE}/"))
    return Path(PROMPTS_CANDIDATE_BASE) / iter_prompt_path


def _ontology_tbox_path(name: str) -> str:
    """Map ontology short names to TTL file paths."""
    if name == "ontosynthesis":
        return "data/ontologies/ontosynthesis.ttl"
    if name == "ontomops":
        return "data/ontologies/ontomops-subgraph.ttl"
    if name == "ontospecies":
        return "data/ontologies/ontospecies-subgraph.ttl"
    return name


def _load_mcp_main_script(ontology: str) -> str:
    """Load the MCP main.py script for an ontology.
    
    Returns the script content if available, otherwise returns a placeholder message.
    """
    # Try ai_generated_contents first (production)
    main_script_path = Path(f"ai_generated_contents/scripts/{ontology}/main.py")
    if main_script_path.exists():
        try:
            return main_script_path.read_text(encoding='utf-8')
        except Exception as e:
            print(f"Warning: Could not read MCP main script at {main_script_path}: {e}")
    
    # Try ai_generated_contents_candidate (development)
    candidate_path = Path(f"ai_generated_contents_candidate/scripts/{ontology}/main.py")
    if candidate_path.exists():
        try:
            return candidate_path.read_text(encoding='utf-8')
        except Exception as e:
            print(f"Warning: Could not read MCP main script at {candidate_path}: {e}")
    
    # Return placeholder if not found
    print(f"Warning: MCP main script not found for {ontology}. Prompt will be generated without tool information.")
    return "# MCP main script not yet generated for this ontology"  # assume it's already a filepath


def format_instances_summary(instances: List[Dict]) -> str:
    """Format instances_to_create for prompt."""
    lines = []
    for inst in instances:
        lines.append(f"   - {inst['class']}: {inst['description']} (cardinality: {inst['cardinality']})")
    return "\n".join(lines)


def format_relations_summary(relations: List[Dict]) -> str:
    """Format relations_to_establish for prompt."""
    lines = []
    for rel in relations:
        lines.append(f"   - {rel['property']}: {rel['description']}")
    return "\n".join(lines)


def format_extraction_summary(extractions: List[str]) -> str:
    """Format information_to_extract for prompt."""
    return "\n".join(f"   - {item}" for item in extractions)


def format_constraints_summary(constraints: List[str]) -> str:
    """Format constraints for prompt."""
    return "\n".join(f"   - {item}" for item in constraints)


async def generate_prompt_for_step(
    step: Dict[str, Any],
    tbox_text: str,
    model: str,
    output_dir: Path,
    version: int
) -> bool:
    """Generate a single prompt for one step."""
    
    step_number = step["step_number"]
    step_name = step["step_name"]
    
    print(f"  [{step_number}] Generating prompt for: {step_name}")
    
    # Build user prompt
    user_prompt = USER_PROMPT_TEMPLATE.format(
        tbox=tbox_text,
        step_json=json.dumps(step, indent=2),
        step_number=step_number,
        goal=step["goal"],
        instances_summary=format_instances_summary(step["instances_to_create"]),
        relations_summary=format_relations_summary(step["relations_to_establish"]),
        extraction_summary=format_extraction_summary(step["information_to_extract"]),
        constraints_summary=format_constraints_summary(step["constraints"]),
        PROMPT_CORE=PROMPT_CORE_TEMPLATE
    )
    
    # Create LLM
    model_config = ModelConfig(temperature=0.1, top_p=1)
    llm = LLMCreator(
        model=model,
        remote_model=True,
        model_config=model_config,
        structured_output=False
    ).setup_llm()
    
    # Retry logic
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            print(f"    Attempt {attempt}/{MAX_RETRIES}...")
            resp_obj = llm.invoke(user_prompt)
            
            # Extract content
            content = getattr(resp_obj, "content", None) if resp_obj is not None else None
            if not isinstance(content, str):
                content = str(resp_obj) if resp_obj is not None else ""
            
            if not content or len(content.strip()) < 100:
                raise ValueError("Empty or too short response")
            
            # Clean up response (remove markdown fences if present)
            prompt_text = content.strip()
            if prompt_text.startswith("```"):
                lines = prompt_text.split("\n")
                lines = lines[1:]  # Remove first line
                if lines and lines[-1].strip() == "```":
                    lines = lines[:-1]  # Remove last line
                prompt_text = "\n".join(lines)
            
            # Wrap with header and footer
            if step_number == 1:
                # Step 1: no entity focus
                full_prompt = (
                    f"'''Follow these generic rules for any iteration.\n\n"
                    f"{PROMPT_CORE_TEMPLATE}\n\n"
                    f"{IDENTIFICATION_HEADER}"
                    f"{prompt_text}\n"
                    f"{FOOTER_WITHOUT_ENTITY}\n'''"
                )
            else:
                # Steps 2+: with entity focus
                full_prompt = (
                    f"'''Follow these generic rules for any iteration.\n\n"
                    f"{PROMPT_CORE_TEMPLATE}\n\n"
                    f"{IDENTIFICATION_HEADER}"
                    f"{prompt_text}\n"
                    f"{FOOTER_WITH_ENTITY}\n'''"
                )
            
            # Save to file
            output_file = output_dir / f"MCP_PROMPT_ITER_{step_number}.txt"
            with open(output_file, "w", encoding="utf-8") as f:
                f.write(full_prompt)
            
            print(f"    ✅ Saved: {output_file}")
            return True
            
        except Exception as e:
            print(f"    ⚠️  Attempt {attempt} failed: {e}")
            if attempt < MAX_RETRIES:
                await asyncio.sleep(2)
    
    print(f"    ❌ Failed after {MAX_RETRIES} attempts")
    return False


async def generate_all_prompts(
    plan: Dict[str, Any],
    tbox_text: str,
    model: str,
    version: int,
    max_parallel: int = 3
) -> bool:
    """Generate all prompts in parallel with concurrency limit."""
    
    output_dir = Path(OUTPUT_DIR_BASE) / str(version)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    steps = plan["steps"]
    total = len(steps)
    
    print(f"\n📋 Generating {total} prompts (version {version})")
    print(f"   Output directory: {output_dir}")
    print(f"   Max parallel: {max_parallel}\n")
    
    # Create semaphore for concurrency control
    semaphore = asyncio.Semaphore(max_parallel)
    
    async def generate_with_limit(step):
        async with semaphore:
            return await generate_prompt_for_step(step, tbox_text, model, output_dir, version)
    
    # Run all tasks
    tasks = [generate_with_limit(step) for step in steps]
    results = await asyncio.gather(*tasks)
    
    # Check results
    success_count = sum(results)
    
    if success_count == total:
        print(f"\n✅ All {total} prompts generated successfully!")
    else:
        print(f"\n⚠️  {success_count}/{total} prompts generated successfully")
    
    # Generate Python file with all prompts
    await generate_python_file(output_dir, total, version)
    
    return success_count == total


# -------- Iterations-driven KG-building prompt generation --------

def _collect_kg_prompts(iterations_obj: Dict[str, Any]) -> List[Path]:
    out: List[str] = []
    for it in iterations_obj.get("iterations", []) or []:
        if not isinstance(it, dict):
            continue
        p = it.get("kg_building_prompt")
        if isinstance(p, str) and p:
            out.append(p)
    return out


def _generate_kg_input_variables_section(iter_number: int) -> str:
    """Generate the input variables section for KG building prompts.
    
    This section is programmatically appended after LLM generation to ensure
    correct variable placeholders are present.
    
    Args:
        iter_number: The iteration number (1, 2, 3, etc.)
    
    Returns:
        String containing the input variables section with proper placeholders
    """
    # ITER1 uses FOOTER_WITHOUT_ENTITY; other iterations use FOOTER_WITH_ENTITY
    if iter_number == 1:
        return "\n\n" + FOOTER_WITHOUT_ENTITY
    else:
        return "\n\n" + FOOTER_WITH_ENTITY


def _generate_and_write_kg_iter1_prompt(llm, tbox_text: str, out_path: Path, ontology: str = "ontosynthesis") -> None:
    """Generate ITER1 KG building prompt.
    
    For extension ontologies: generates comprehensive A-Box building prompts
    For main ontology: generates ITER1-specific prompts
    """
    # Detect if this is an extension ontology
    is_extension = ontology in ["ontomops", "ontospecies"]
    
    # Load MCP main script for this ontology
    mcp_main_script = _load_mcp_main_script(ontology)
    
    if is_extension:
        # Use extension-specific meta-prompts
        user_prompt = KG_EXTENSION_USER_TMPL.format(tbox=tbox_text, mcp_main_script=mcp_main_script)
        resp = llm.invoke([{"role": "system", "content": KG_EXTENSION_SYS}, {"role": "user", "content": user_prompt}])
    else:
        # Use ITER1 meta-prompts for main ontology
        user_prompt = KG_ITER1_USER_TMPL.format(tbox=tbox_text, mcp_main_script=mcp_main_script)
        resp = llm.invoke([{"role": "system", "content": KG_ITER1_SYS}, {"role": "user", "content": user_prompt}])
    
    content = getattr(resp, "content", None)
    if not isinstance(content, str):
        content = str(resp) if resp is not None else ""
    text = content.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines)
    
    # For extension ontologies, the meta-prompt already includes paper_content placeholder
    # Don't append footer (which would duplicate paper_content)
    # For main ontology ITER1, append footer with paper_content
    if is_extension:
        final_text = text
    else:
        # Programmatically append input variables section for ITER1 (main ontology)
        input_vars_section = _generate_kg_input_variables_section(1)
        final_text = text + input_vars_section
    
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(final_text)
    
    print(f"   (using {'extension' if is_extension else 'main ontology'} meta-prompts)")


def _generate_and_write_kg_prompt(llm, tbox_text: str, iter_meta: Dict[str, Any], out_path: Path, ontology: str = "ontosynthesis") -> None:
    """Generate KG building prompt using hardcoded template for ITER 2, 3, 4.
    
    For ITER 2, 3, 4: Uses hardcoded template directly (no LLM generation).
    For other iterations: Falls back to LLM generation (if needed in future).
    """
    iter_number = iter_meta.get("iteration_number", 1)
    
    # For ITER 2, 3, 4: Use hardcoded template directly (no domain-specific info)
    if iter_number in [2, 3, 4]:
        # Load and format the hardcoded template
        template_text = KG_BUILDING_ITER_TEMPLATE
        
        # Replace template placeholders
        final_text = template_text.replace("{PROMPT_CORE}", PROMPT_CORE_TEMPLATE)
        final_text = final_text.replace("{IDENTIFICATION_HEADER}", IDENTIFICATION_HEADER)
        final_text = final_text.replace("{FOOTER_WITH_ENTITY}", FOOTER_WITH_ENTITY)
        
        # The template already has {entity_label}, {entity_uri}, {doi}, {paper_content} placeholders
        # These will be filled at runtime by the pipeline
        
    else:
        # For other iterations (if any), use LLM generation
        # Load MCP main script for this ontology
        mcp_main_script = _load_mcp_main_script(ontology)
        
        # Reuse SYSTEM_PROMPT + USER_PROMPT_TEMPLATE by fabricating a minimal step
        step = {
            "step_number": iter_number,
            "step_name": iter_meta.get("name", "kg_building"),
            "goal": iter_meta.get("description", "Build KG for this iteration"),
            "instances_to_create": [],
            "relations_to_establish": [],
            "information_to_extract": [],
            "constraints": [],
        }
        user_prompt = USER_PROMPT_TEMPLATE.format(
            tbox=tbox_text,
            mcp_main_script=mcp_main_script,
            step_json=json.dumps(step, indent=2),
            step_number=step["step_number"],
            goal=step["goal"],
            instances_summary="",
            relations_summary="",
            extraction_summary="",
            constraints_summary="",
            PROMPT_CORE=PROMPT_CORE_TEMPLATE
        )
        resp = llm.invoke(user_prompt)
        content = getattr(resp, "content", None)
        if not isinstance(content, str):
            content = str(resp) if resp is not None else ""
        text = content.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines)
        
        # Programmatically append input variables section
        input_vars_section = _generate_kg_input_variables_section(iter_number)
        final_text = text + "\n\n" + input_vars_section
    
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(final_text)


def generate_kg_prompts_from_iterations(ontology: str) -> bool:
    """Generate KG building prompts for an ontology.
    
    ITER1 is handled separately (not from iterations.json), similar to EXTRACTION_ITER_1.
    Other iterations (2, 3, 4, ...) are read from iterations.json.
    """
    iterations_path = _iterations_path_for(ontology)
    if not iterations_path.exists():
        print(f"Skipping {ontology}: iterations.json not found at {iterations_path}")
        return False
    try:
        iterations_obj = json.loads(iterations_path.read_text(encoding="utf-8"))
        tbox_path = Path(_ontology_tbox_path(ontology))
        tbox_text = load_tbox(tbox_path)
        
        print(f"\n=== Generating KG prompts for ontology: {ontology} ===")
        print(f"Iterations file: {iterations_path}")
        print(f"T-Box: {tbox_path}")
        
        llm = LLMCreator(
            model=MODEL,
            remote_model=True,
            model_config=ModelConfig(temperature=0, top_p=1.0),
            structured_output=False,
        ).setup_llm()
        
        # ALWAYS generate ITER1 KG prompt separately (not from iterations.json)
        iter1_path = _candidate_prompt_path_from(f"ai_generated_contents/prompts/{ontology}/KG_BUILDING_ITER_1.md")
        print(f"  -> [{ontology}] ITER 1 KG prompt (generated separately)")
        print(f"     output path: {iter1_path}")
        _generate_and_write_kg_iter1_prompt(llm, tbox_text, iter1_path, ontology)
        print(f"✅ Wrote KG prompt: {iter1_path}")
        
        # Generate KG prompts for iterations 2+ from iterations.json
        kg_paths = _collect_kg_prompts(iterations_obj)
        if kg_paths:
            print(f"Total KG prompts from iterations.json: {len(kg_paths)}")
            for it in iterations_obj.get("iterations", []) or []:
                if not isinstance(it, dict):
                    continue
                p = it.get("kg_building_prompt")
                if isinstance(p, str) and p:
                    target = _candidate_prompt_path_from(p)
                    iter_num = it.get('iteration_number', 0)
                    
                    # Skip ITER1 (already handled above)
                    if iter_num == 1:
                        continue
                    
                    print(f"  -> [{ontology}] iter {iter_num} KG prompt: {p}")
                    print(f"     output path: {target}")
                    if iter_num in [2, 3, 4]:
                        print(f"     (using hardcoded template, no LLM generation)")
                    _generate_and_write_kg_prompt(llm, tbox_text, it, target, ontology)
                    print(f"✅ Wrote KG prompt: {target}")
        else:
            print(f"No additional kg_building_prompt paths found in {iterations_path}")
        
        return True
    except Exception as e:
        print(f"Error generating KG prompts for {ontology}: {e}")
        return False


async def generate_python_file(output_dir: Path, num_prompts: int, version: int):
    """Generate a Python file with all prompt variables."""
    
    print(f"\n📝 Generating Python file...")
    
    py_file = output_dir / f"prompts_v{version}.py"
    
    with open(py_file, "w", encoding="utf-8") as f:
        f.write(f"# Auto-generated prompts version {version}\n")
        f.write(f"# Generated by task_prompt_creation_agent.py\n\n")
        
        # Write PROMPT_CORE
        f.write("PROMPT_CORE = r'''" + PROMPT_CORE_TEMPLATE + "'''\n\n")
        
        # Write each iteration prompt
        for i in range(1, num_prompts + 1):
            prompt_file = output_dir / f"MCP_PROMPT_ITER_{i}.txt"
            if prompt_file.exists():
                with open(prompt_file, "r", encoding="utf-8") as pf:
                    prompt_text = pf.read()
                
                # Replace PROMPT_CORE placeholder
                prompt_text = prompt_text.replace(f"'''{PROMPT_CORE_TEMPLATE}", "'''{PROMPT_CORE}")
                
                f.write(f"MCP_PROMPT_ITER_{i} = {prompt_text}\n\n")
    
    print(f"✅ Saved: {py_file}")


# -------- Main --------

async def main_async(args):
    """Main async function."""
    
    # Iterations-driven mode using ontology short name
    if args.tbox in ("ontosynthesis", "ontomops", "ontospecies"):
        ok = generate_kg_prompts_from_iterations(args.tbox)
        return ok

    # Load T-Box (accept ontology short names like 'ontosynthesis')
    tbox_arg = args.tbox
    mapped_tbox = _ontology_tbox_path(tbox_arg)
    tbox_path = Path(mapped_tbox)
    if not tbox_path.exists():
        raise FileNotFoundError(f"T-Box file not found: {tbox_arg}")
    
    print(f"📖 Reading T-Box from: {tbox_path}")
    tbox_text = load_tbox(tbox_path)
    print(f"   T-Box size: {len(tbox_text)} characters")
    
    # Load Plan
    plan_path = Path(args.plan)
    if not plan_path.exists():
        raise FileNotFoundError(f"Plan file not found: {plan_path}")
    
    print(f"📖 Reading plan from: {plan_path}")
    plan = load_plan(plan_path)
    print(f"   Total steps: {plan['metadata']['total_steps']}")
    
    # Generate prompts
    print("\n" + "=" * 60)
    print("GENERATING MCP ITERATION PROMPTS")
    print("=" * 60)
    
    success = await generate_all_prompts(
        plan=plan,
        tbox_text=tbox_text,
        model=args.model,
        version=args.version,
        max_parallel=args.parallel
    )
    
    if success:
        print("\n" + "=" * 60)
        print("✅ PROMPT GENERATION COMPLETE")
        print("=" * 60)
    else:
        print("\n" + "=" * 60)
        print("⚠️  PROMPT GENERATION INCOMPLETE")
        print("=" * 60)
    
    return success


def main():
    parser = argparse.ArgumentParser(
        description="Generate MCP iteration prompts from task division plan"
    )
    parser.add_argument(
        "--plan",
        type=str,
        default=PLAN_PATH,
        help=f"Path to task division plan JSON (default: {PLAN_PATH})"
    )
    parser.add_argument(
        "--tbox",
        type=str,
        default=TBOX_PATH,
        help=f"Path to T-Box TTL file (default: {TBOX_PATH})"
    )
    parser.add_argument(
        "--version",
        type=int,
        required=True,
        help="Version number for output directory (e.g., 1, 2, 3)"
    )
    parser.add_argument(
        "--model",
        type=str,
        default=MODEL,
        help=f"LLM model to use (default: {MODEL})"
    )
    parser.add_argument(
        "--parallel",
        type=int,
        default=3,
        help="Maximum number of parallel prompt generations (default: 3)"
    )
    
    args = parser.parse_args()
    
    try:
        success = asyncio.run(main_async(args))
        exit(0 if success else 1)
    except KeyboardInterrupt:
        print("\n❌ Interrupted by user")
        exit(1)
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        exit(1)


if __name__ == "__main__":
    main()

