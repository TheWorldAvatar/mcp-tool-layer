#!/usr/bin/env python3
"""
task_extraction_prompt_creation_agent.py

Two modes:
1) Legacy: Generate extraction scope prompts from a task division plan JSON
2) Iterations-driven: Read iterations.json for selected ontologies and generate
   all referenced extraction prompts (including pre-extraction and sub-iterations)
   into ai_generated_contents_candidate/prompts

SPECIAL HANDLING FOR ITER1:
- ITER1 is generated SEPARATELY by analyzing the T-Box TTL directly
- The agent identifies the top entity class and extracts strict identification rules
- ITER1 focuses ONLY on entity identification (names/identifiers), NOT detailed properties
- This happens BEFORE processing iterations.json for ITER2+

SPECIAL HANDLING FOR PRE-EXTRACTION:
- Pre-extraction prompts are generated using a DIFFERENT template than regular extraction prompts
- Pre-extraction prompts ask ONLY for verbatim text extraction (no ontology constraints)
- They focus on faithfully retrieving relevant text spans from papers
- They do NOT apply entity classification, step typing, or ontology-driven structuring
- Pre-extraction is detected when iteration config has "has_pre_extraction": true
"""

import os
import json
import argparse
import asyncio
from pathlib import Path
from typing import Dict, List, Any, Tuple
from dotenv import load_dotenv

# -------- Meta-Prompt Loader --------
def load_meta_prompt(prompt_path: str) -> str:
    """Load meta-prompt from ape_generated_contents/meta_prompts/"""
    full_path = Path(f"ape_generated_contents/meta_prompts/{prompt_path}")
    if not full_path.exists():
        raise FileNotFoundError(f"Meta-prompt not found: {full_path}")
    return full_path.read_text(encoding='utf-8')

from models.LLMCreator import LLMCreator
from models.ModelConfig import ModelConfig

# -------- Config --------
PLAN_PATH = "configs/task_division_plan.json"
TBOX_PATH = "data/ontologies/ontosynthesis.ttl"
OUTPUT_DIR_BASE = "sandbox/extraction_scopes"
MODEL = os.environ.get("EXTRACTION_PROMPT_CREATION_MODEL", "gpt-4.1")
ITERATIONS_BASE = "ai_generated_contents_candidate/iterations"
PROMPTS_CANDIDATE_BASE = "ai_generated_contents_candidate/prompts"
MAX_RETRIES = 3

# -------- Load environment --------
load_dotenv(override=True)

# -------- Hardcoded Generic Specifications --------

EXTRACTION_CORE_TEMPLATE = r'''Core extraction principles:

- Extract ONLY information explicitly stated in the source document
- Use exact wording from the source when possible
- Do not infer, calculate, or backfill missing information
- Preserve units, notation, and formatting as written
- Normalize whitespace and remove control characters
- Use plain text representations (no LaTeX/TeX notation)
- When uncertain, EXCLUDE rather than guess

Evidence requirements:
- Include an entity only if sufficient evidence exists in the source document
- For inherited or referenced entities, mark them clearly with provenance
- Cross-document references must be explicit in the text

Normalization rules:
- Remove formatting artifacts (control characters, extra whitespace)
- Keep domain-specific terminology verbatim
- Preserve author wording and units
- Use consistent identifier formats
'''

# -------- System Prompt (Legacy mode) --------
SYSTEM_PROMPT = """You are an information extraction expert specializing in creating precise extraction scope prompts.

Given a step from a task division plan, you will generate an extraction scope prompt that:
1. Instructs an extraction agent to identify and extract specific entities from a source document
2. References the T-Box ontology for entity definitions and constraints
3. Is domain-agnostic but uses T-Box rdfs:comment guidance for specifics
4. Follows a clear, structured format
5. Be explict and specific about the extraction, also about the class/property specific rules.

Your output must be ONLY the extraction prompt text. No markdown, no explanations, no JSON."""

# -------- User Prompt Template --------
USER_PROMPT_TEMPLATE = """You are generating an extraction scope prompt for information extraction.

## Input

**T-Box Ontology:**
```turtle
{tbox}
```

**Step from Task Division Plan:**
```json
{step_json}
```

## Task

Generate an extraction scope prompt for extraction phase {step_number} that instructs an extraction agent to:

1. **Goal**: {goal}

2. **Identify instances** of:
{instances_summary}

3. **Extract information**:
{extraction_summary}

4. **Follow constraints**:
{constraints_summary}

## Prompt Structure Requirements

The prompt MUST follow this structure:

```
Task:
[Clear statement of what to extract, what entity types to identify]

Scope:
[Define what is in-scope vs out-of-scope for this extraction]

{EXTRACTION_CORE}

Ontology-anchored constraints:
[Bullet list of constraints from T-Box rdfs:comment that define valid instances]

Inclusion rules:
[Bullet list of when to include an entity, derived from step constraints and T-Box comments]

Exclusion rules:
[Bullet list of when to exclude an entity, derived from step constraints and T-Box comments]

Field requirements:
[For each entity type, list what fields/properties must be extracted]

Output format:
[Specify the exact output format - plain text, structured bullets, JSON, etc.]
```

## Important Guidelines

1. **Use T-Box rdfs:comment** fields to extract all entity definitions, constraints, examples, and validation rules
2. **Be specific about extraction targets**: "Extract all X instances where Y" not "Find X"
3. **Reference cardinality** from T-Box comments (e.g., "exactly one output per procedure")
4. **Add validation rules** from T-Box comments (e.g., required fields, allowed values)
5. **Specify output format** clearly (plain text list, structured fields, JSON schema)
6. **Include deduplication rules** where appropriate from T-Box comments
7. **Map plan constraints** to actionable extraction rules
8. **CRITICAL**: Do NOT include domain-specific examples in the prompt itself; ALL domain knowledge must come from T-Box rdfs:comment fields

## Output Format

Output ONLY the extraction prompt text. Do NOT include:
- Markdown code fences
- Explanations
- JSON
- Any preamble or postamble

Start directly with: "Task:"

Generate the extraction prompt now:
"""

# -------- Helper Functions --------

def load_tbox(tbox_path: Path) -> str:
    """Load T-Box TTL file."""
    with open(tbox_path, "r", encoding="utf-8") as f:
        return f.read()


def load_plan(plan_path: Path) -> Dict[str, Any]:
    """Load task division plan JSON."""
    with open(plan_path, "r", encoding="utf-8") as f:
        return json.load(f)


def format_instances_summary(instances: List[Dict]) -> str:
    """Format instances_to_create for prompt."""
    lines = []
    for inst in instances:
        lines.append(f"   - {inst['class']}: {inst['description']} (cardinality: {inst['cardinality']})")
    return "\n".join(lines)


def format_extraction_summary(extractions: List[str]) -> str:
    """Format information_to_extract for prompt."""
    return "\n".join(f"   - {item}" for item in extractions)


def format_constraints_summary(constraints: List[str]) -> str:
    """Format constraints for prompt."""
    return "\n".join(f"   - {item}" for item in constraints)


async def generate_extraction_prompt_for_step(
    step: Dict[str, Any],
    tbox_text: str,
    model: str,
    output_dir: Path,
    version: int
) -> bool:
    """Generate a single extraction prompt for one step."""
    
    step_number = step["step_number"]
    step_name = step["step_name"]
    
    print(f"  [{step_number}] Generating extraction prompt for: {step_name}")
    
    # Build user prompt
    user_prompt = USER_PROMPT_TEMPLATE.format(
        tbox=tbox_text,
        step_json=json.dumps(step, indent=2),
        step_number=step_number,
        goal=step["goal"],
        instances_summary=format_instances_summary(step["instances_to_create"]),
        extraction_summary=format_extraction_summary(step["information_to_extract"]),
        constraints_summary=format_constraints_summary(step["constraints"]),
        EXTRACTION_CORE=EXTRACTION_CORE_TEMPLATE
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
            extraction_text = content.strip()
            if extraction_text.startswith("```"):
                lines = extraction_text.split("\n")
                lines = lines[1:]  # Remove first line
                if lines and lines[-1].strip() == "```":
                    lines = lines[:-1]  # Remove last line
                extraction_text = "\n".join(lines)
            
            # Wrap with core template
            full_prompt = (
                f"'''{extraction_text}\n\n"
                f"{EXTRACTION_CORE_TEMPLATE}\n'''"
            )
            
            # Save to file
            output_file = output_dir / f"EXTRACTION_SCOPE_{step_number}.txt"
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


async def generate_all_extraction_prompts(
    plan: Dict[str, Any],
    tbox_text: str,
    model: str,
    version: int,
    max_parallel: int = 3
) -> bool:
    """Generate all extraction prompts in parallel with concurrency limit."""
    
    output_dir = Path(OUTPUT_DIR_BASE) / str(version)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    steps = plan["steps"]
    total = len(steps)
    
    print(f"\n📋 Generating {total} extraction scope prompts (version {version})")
    print(f"   Output directory: {output_dir}")
    print(f"   Max parallel: {max_parallel}\n")
    
    # Create semaphore for concurrency control
    semaphore = asyncio.Semaphore(max_parallel)
    
    async def generate_with_limit(step):
        async with semaphore:
            return await generate_extraction_prompt_for_step(step, tbox_text, model, output_dir, version)
    
    # Run all tasks
    tasks = [generate_with_limit(step) for step in steps]
    results = await asyncio.gather(*tasks)
    
    # Check results
    success_count = sum(results)
    
    if success_count == total:
        print(f"\n✅ All {total} extraction prompts generated successfully!")
    else:
        print(f"\n⚠️  {success_count}/{total} extraction prompts generated successfully")
    
    # Generate Python file with all extraction prompts
    await generate_python_file(output_dir, total, version)
    
    return success_count == total


async def generate_python_file(output_dir: Path, num_prompts: int, version: int):
    """Generate a Python file with all extraction scope variables."""
    
    print(f"\n📝 Generating Python file...")
    
    py_file = output_dir / f"extraction_scopes_v{version}.py"
    
    with open(py_file, "w", encoding="utf-8") as f:
        f.write(f"# Auto-generated extraction scopes version {version}\n")
        f.write(f"# Generated by task_extraction_prompt_creation_agent.py\n\n")
        
        # Write EXTRACTION_CORE
        f.write("EXTRACTION_CORE = r'''" + EXTRACTION_CORE_TEMPLATE + "'''\n\n")
        
        # Write each extraction scope
        for i in range(1, num_prompts + 1):
            scope_file = output_dir / f"EXTRACTION_SCOPE_{i}.txt"
            if scope_file.exists():
                with open(scope_file, "r", encoding="utf-8") as sf:
                    scope_text = sf.read()
                
                # Replace EXTRACTION_CORE placeholder if present
                scope_text = scope_text.replace(f"'''{EXTRACTION_CORE_TEMPLATE}", "'''{EXTRACTION_CORE}")
                
                f.write(f"EXTRACTION_SCOPE_{i} = {scope_text}\n\n")
    
    print(f"✅ Saved: {py_file}")


# -------- Iterations-driven generation --------

def _ontology_tbox_path(name: str) -> str:
    if name == "ontosynthesis":
        return "data/ontologies/ontosynthesis.ttl"
    if name == "ontomops":
        return "data/ontologies/ontomops-subgraph.ttl"
    if name == "ontospecies":
        return "data/ontologies/ontospecies-subgraph.ttl"
    return "data/ontologies/ontosynthesis.ttl"


def _iterations_path_for(name: str) -> Path:
    return Path(ITERATIONS_BASE) / name / "iterations.json"


def _candidate_prompt_path_from(iter_prompt_path: str) -> Path:
    # Map ai_generated_contents/prompts/... -> ai_generated_contents_candidate/prompts/...
    if iter_prompt_path.startswith("ai_generated_contents/prompts/"):
        return Path(iter_prompt_path.replace("ai_generated_contents/prompts/", f"{PROMPTS_CANDIDATE_BASE}/"))
    # Already candidate or other; default under candidate base
    return Path(PROMPTS_CANDIDATE_BASE) / iter_prompt_path


ITER_SYS = load_meta_prompt("extraction/iter_system.md")

# -------- PRE-EXTRACTION System Prompt --------
PRE_EXTRACTION_SYS = load_meta_prompt("extraction/pre_extraction_system.md")

# -------- ITER1 Special System Prompt --------
ITER1_SYS = load_meta_prompt("extraction/iter1_system.md")

ITER1_USER_TMPL = load_meta_prompt("extraction/iter1_user.md")

# -------- Extension Ontology Prompts --------
EXTENSION_SYS = load_meta_prompt("extraction/extension_system.md")

EXTENSION_USER_TMPL = load_meta_prompt("extraction/extension_user.md")

PRE_EXTRACTION_USER_TMPL = load_meta_prompt("extraction/pre_extraction_user.md")

ITER_USER_TMPL = load_meta_prompt("extraction/iter_user.md")


def _collect_iteration_prompts(iterations_obj: Dict[str, Any], iteration_filter: List[str] = None) -> List[Tuple[str, Dict[str, Any]]]:
    """
    Collect iteration prompts from iterations.json.
    
    Args:
        iterations_obj: The loaded iterations.json object
        iteration_filter: Optional list of iteration numbers to include (e.g., ["1", "2", "3"])
                         If None, all iterations are included.
    
    Returns:
        List of (prompt_path, metadata) tuples
    """
    prompts: List[Tuple[str, Dict[str, Any]]] = []
    iters = iterations_obj.get("iterations", [])
    for it in iters:
        if not isinstance(it, dict):
            continue
        
        # Check if this iteration should be included
        iter_num = str(it.get("iteration_number", ""))
        if iteration_filter is not None and iter_num not in iteration_filter:
            continue
        
        # main extraction prompt
        ep = it.get("extraction_prompt")
        if isinstance(ep, str) and ep:
            prompts.append((ep, it))
        # pre-extraction prompt
        if it.get("has_pre_extraction"):
            pep = it.get("pre_extraction_prompt")
            if isinstance(pep, str) and pep:
                prompts.append((pep, it))
        # sub-iterations
        for sub in it.get("sub_iterations", []) or []:
            if not isinstance(sub, dict):
                continue
            sep = sub.get("extraction_prompt")
            if isinstance(sep, str) and sep:
                prompts.append((sep, sub))
    return prompts


def _generate_and_write_iter1_prompt(llm, tbox_text: str, out_path: Path, ontology_name: str = "ontosynthesis") -> None:
    """
    Generate ITER1 extraction prompt by analyzing TTL.
    
    For extension ontologies (ontomops, ontospecies): uses simple template replacement
    For main ontology (ontosynthesis): generates entity identification prompts via LLM
    """
    # Detect if this is an extension ontology
    is_extension = ontology_name in ["ontomops", "ontospecies"]
    
    if is_extension:
        # Use simple template for extensions - no LLM needed
        template_path = Path("ape_generated_contents/prompts/extension_extraction_template.md")
        if not template_path.exists():
            raise FileNotFoundError(f"Extension extraction template not found: {template_path}")
        
        template = template_path.read_text(encoding='utf-8')
        
        # Map ontology names to display names and placeholder names
        ontology_config = {
            "ontomops": {
                "display_name": "OntoMOPs",
                "tbox_placeholder": "{ontomops_t_box}"
            },
            "ontospecies": {
                "display_name": "OntoSpecies",
                "tbox_placeholder": "{ontospecies_t_box}"
            }
        }
        
        config = ontology_config.get(ontology_name, {
            "display_name": ontology_name,
            "tbox_placeholder": f"{{{ontology_name}_t_box}}"
        })
        
        # Replace template placeholders with ontology-specific values
        text = template.replace("{{ONTOLOGY_NAME}}", ontology_name)
        text = text.replace("{{ONTOLOGY_DISPLAY_NAME}}", config["display_name"])
        text = text.replace("{{TBOX_PLACEHOLDER}}", config["tbox_placeholder"])
        
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"✅ Generated ITER1 prompt: {out_path} (using extension template)")
    else:
        # Use ITER1 meta-prompts for entity identification (main ontology)
        user = ITER1_USER_TMPL.format(tbox=tbox_text)
        resp = llm.invoke([
            {"role": "system", "content": ITER1_SYS},
            {"role": "user", "content": user},
        ])
        
        content = getattr(resp, "content", None)
        if not isinstance(content, str):
            content = str(resp) if resp is not None else ""
        text = content.strip()
        # Strip markdown fences if present
        if text.startswith("```"):
            lines = text.split("\n")
            lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"✅ Generated ITER1 prompt: {out_path} (using main ontology meta-prompts)")


def _generate_and_write_pre_extraction_prompt(llm, tbox_text: str, meta: Dict[str, Any], out_path: Path) -> None:
    """
    Generate a PRE-EXTRACTION prompt (raw text extraction only, no ontology constraints).
    Now includes tbox_text to extract exclusion/inclusion rules.
    """
    # Filter metadata to only include relevant fields for pre-extraction
    # Exclude sub_iterations, outputs, inputs details that aren't needed
    filtered_meta = {
        "iteration_number": meta.get("iteration_number"),
        "name": meta.get("name"),
        "description": meta.get("description"),
        "has_pre_extraction": meta.get("has_pre_extraction"),
        "per_entity": meta.get("per_entity")
    }
    
    user = PRE_EXTRACTION_USER_TMPL.format(
        meta=json.dumps(filtered_meta, ensure_ascii=False, indent=2)
    )
    # Append the T-Box for exclusion/inclusion rule extraction
    user += f"\n\nOntology T-Box (review for EXCLUSION/INCLUSION rules in rdfs:comment sections):\n```\n{tbox_text}\n```"
    resp = llm.invoke([
        {"role": "system", "content": PRE_EXTRACTION_SYS},
        {"role": "user", "content": user},
    ])
    content = getattr(resp, "content", None)
    if not isinstance(content, str):
        content = str(resp) if resp is not None else ""
    text = content.strip()
    # Strip markdown fences if present
    if text.startswith("```"):
        lines = text.split("\n")
        lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(text)
    print(f"✅ Generated PRE-EXTRACTION prompt: {out_path}")


def _generate_input_variables_section(meta: Dict[str, Any]) -> str:
    """
    Generate the input variables section based on iteration metadata's 'inputs' field.
    Returns the formatted string to append to the prompt.
    
    Note: Enrichment iterations (those with "enriches" field) do NOT get variable placeholders
    because the pipeline manually appends data for them.
    """
    if not isinstance(meta, dict):
        return ""
    
    # Enrichment iterations don't use variable placeholders - pipeline appends data manually
    if "enriches" in meta:
        return ""
    
    inputs = meta.get("inputs", {})
    if not isinstance(inputs, dict):
        return ""
    
    # Determine which variables to include based on inputs field
    has_source = "source" in inputs
    has_extraction_source = "extraction_source" in inputs
    
    # Build the variables section
    lines = ["---", ""]
    lines.append("entity_label: {entity_label}")
    
    # For iterations using pre-extracted text (context)
    if has_extraction_source:
        lines.append("context:")
        lines.append("<<<")
        lines.append("{context}")
        lines.append(">>>")
    # For regular iterations with full paper
    elif has_source:
        lines.append("paper:")
        lines.append("<<<")
        lines.append("{paper_content}")
        lines.append(">>>")
    
    return "\n".join(lines)


def _generate_and_write_prompt(llm, tbox_text: str, meta: Dict[str, Any], out_path: Path) -> None:
    # Add fidelity guidance for sub-iterations (enrichment) in a generic way
    extra = ""
    try:
        if isinstance(meta, dict) and ("enriches" in meta):
            extra = (
                "\nFidelity to previous results:"\
                "\n- Treat outputs from the enriched parent iteration as authoritative for step lists/order/count."\
                "\n- Do NOT add, remove, or re-type steps; ONLY enrich with additional details (e.g., vessels, environments, parameters)."\
                "\n- Maintain one-to-one alignment with prior steps and preserve contiguous ordering."\
            )
    except Exception:
        pass
    user = ITER_USER_TMPL.format(
        tbox=tbox_text,
        meta=json.dumps(meta, ensure_ascii=False, indent=2),
        extra_guidance=extra,
    )
    resp = llm.invoke([
        {"role": "system", "content": ITER_SYS},
        {"role": "user", "content": user},
    ])
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
    
    # Append input variables section programmatically
    variables_section = _generate_input_variables_section(meta)
    if variables_section:
        text = text + "\n\n" + variables_section
    
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(text)


def generate_prompts_from_iterations(ontologies: List[str], iteration_filter: List[str] = None, pre_extraction_only: bool = False) -> bool:
    """
    Generate extraction prompts from iterations.json for specified ontologies.
    
    Args:
        ontologies: List of ontology names (e.g., ["ontosynthesis", "ontomops"])
        iteration_filter: Optional list of iteration numbers to generate (e.g., ["1", "2", "3"])
                         If None, all iterations are generated.
        pre_extraction_only: If True, only generate pre-extraction prompts (skip ITER1 and regular extraction prompts)
    
    Returns:
        True if all prompts generated successfully, False otherwise
    """
    ok = True
    for name in ontologies:
        iterations_path = _iterations_path_for(name)
        if not iterations_path.exists():
            print(f"Skipping {name}: iterations.json not found at {iterations_path}")
            ok = False
            continue
        try:
            iterations_obj = json.loads(iterations_path.read_text(encoding="utf-8"))
            tbox_path = Path(_ontology_tbox_path(name))
            tbox_text = tbox_path.read_text(encoding="utf-8")

            prompts = _collect_iteration_prompts(iterations_obj, iteration_filter=iteration_filter)
            
            print(f"\n=== Generating prompts for ontology: {name} ===")
            print(f"Iterations file: {iterations_path}")
            print(f"T-Box: {tbox_path}")
            if iteration_filter:
                print(f"Iteration filter: {', '.join(iteration_filter)}")

            llm = LLMCreator(
                model=MODEL,
                remote_model=True,
                model_config=ModelConfig(temperature=0, top_p=1.0),
                structured_output=False,
            ).setup_llm()

            # SPECIAL HANDLING: Generate ITER1 first by analyzing TTL directly
            # Only if iteration 1 is requested (or no filter is specified) AND not pre-extraction-only mode
            if not pre_extraction_only and (iteration_filter is None or "1" in iteration_filter):
                print(f"\n🎯 Generating ITER1 for {name} (analyzing TTL)")
                iter1_path = Path(PROMPTS_CANDIDATE_BASE) / name / "EXTRACTION_ITER_1.md"
                try:
                    _generate_and_write_iter1_prompt(llm, tbox_text, iter1_path, ontology_name=name)
                except Exception as e:
                    print(f"⚠️  Warning: Failed to generate ITER1 for {name}: {e}")
                    ok = False
            elif pre_extraction_only:
                print(f"\n⏭️  Skipping ITER1 (pre-extraction-only mode)")
            else:
                print(f"\n⏭️  Skipping ITER1 (not in filter)")

            # Now process remaining iterations from iterations.json (ITER2+)
            if not prompts:
                print(f"No additional prompts found in {iterations_path} matching filter")
                continue

            print(f"\nGenerating remaining prompts from iterations.json: {len(prompts)}")
            for iter_prompt_path, meta in prompts:
                target = _candidate_prompt_path_from(iter_prompt_path)
                # Derive a friendly label
                iter_no = meta.get("iteration_number", "?")
                
                # Skip ITER1 if it's in the iterations.json (we already generated it)
                if iter_no == 1 and "ITER_1" in str(iter_prompt_path):
                    print(f"  -> [{name}] iter 1 - SKIPPED (already generated via TTL analysis)")
                    continue
                
                # Determine if this is a pre-extraction prompt
                is_pre_extraction = False
                label = "main"
                try:
                    if isinstance(meta, dict):
                        if meta.get("pre_extraction_prompt") == iter_prompt_path:
                            label = "pre"
                            is_pre_extraction = True
                        elif "enriches" in meta:
                            label = "sub"
                except Exception:
                    pass
                
                # Skip non-pre-extraction prompts if in pre-extraction-only mode
                if pre_extraction_only and not is_pre_extraction:
                    print(f"  -> [{name}] iter {iter_no} [{label}] - SKIPPED (not a pre-extraction prompt)")
                    continue
                
                print(f"  -> [{name}] iter {iter_no} [{label}] source prompt path: {iter_prompt_path}")
                print(f"     output prompt path: {target}")
                
                # Use different generation function based on prompt type
                if is_pre_extraction:
                    _generate_and_write_pre_extraction_prompt(llm, tbox_text, meta, target)
                else:
                    _generate_and_write_prompt(llm, tbox_text, meta, target)
                
                print(f"✅ Wrote prompt file: {target}")
        except Exception as e:
            print(f"Error processing {name}: {e}")
            import traceback
            traceback.print_exc()
            ok = False
    return ok


# -------- Main --------

async def main_async(args):
    """Main async function."""
    
    # If flags for ontologies are provided, run iterations-driven mode and exit
    selected: List[str] = []
    if getattr(args, "ontosynthesis", False):
        selected.append("ontosynthesis")
    if getattr(args, "ontomops", False):
        selected.append("ontomops")
    if getattr(args, "ontospecies", False):
        selected.append("ontospecies")

    if selected:
        success_iter = generate_prompts_from_iterations(selected)
        return success_iter

    # Legacy mode: Load T-Box and Plan
    tbox_path = Path(args.tbox)
    if not tbox_path.exists():
        raise FileNotFoundError(f"T-Box file not found: {tbox_path}")
    
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
    
    # Generate extraction prompts
    print("\n" + "=" * 60)
    print("GENERATING EXTRACTION SCOPE PROMPTS")
    print("=" * 60)
    
    success = await generate_all_extraction_prompts(
        plan=plan,
        tbox_text=tbox_text,
        model=args.model,
        version=args.version,
        max_parallel=args.parallel
    )
    
    if success:
        print("\n" + "=" * 60)
        print("✅ EXTRACTION PROMPT GENERATION COMPLETE")
        print("=" * 60)
    else:
        print("\n" + "=" * 60)
        print("⚠️  EXTRACTION PROMPT GENERATION INCOMPLETE")
        print("=" * 60)
    
    return success


def main():
    parser = argparse.ArgumentParser(
        description="Generate extraction prompts: either from iterations.json (by ontology flags) or legacy plan mode"
    )
    parser.add_argument("--ontosynthesis", action="store_true", help="Generate prompts for OntoSynthesis from iterations.json")
    parser.add_argument("--ontomops", action="store_true", help="Generate prompts for OntoMOPs from iterations.json")
    parser.add_argument("--ontospecies", action="store_true", help="Generate prompts for OntoSpecies from iterations.json")
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
        help="Maximum number of parallel extraction prompt generations (default: 3)"
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



