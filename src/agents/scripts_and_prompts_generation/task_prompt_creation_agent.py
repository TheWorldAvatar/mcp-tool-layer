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
from typing import Dict, List, Any, Optional
from dotenv import load_dotenv
from rdflib import Graph, OWL, RDF, RDFS, URIRef

from models.LLMCreator import LLMCreator
from models.ModelConfig import ModelConfig
from src.agents.scripts_and_prompts_generation.ttl_parser import (
    detect_super_flat_ontology,
    extract_ontology_integrity_profile,
    format_ontology_integrity_guidance,
)

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
MODEL = os.environ.get("PROMPT_CREATION_MODEL", "gpt-5.2")
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


def _load_meta_task_config(config_path: str = "configs/meta_task/meta_task_config.json") -> Dict[str, Any]:
    try:
        cfg_path = Path(config_path)
        if cfg_path.exists():
            return json.loads(cfg_path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _ontology_role(ontology_name: str, config_path: str = "configs/meta_task/meta_task_config.json") -> str:
    cfg = _load_meta_task_config(config_path)
    ontologies = (cfg.get("ontologies", {}) or {})
    main = ontologies.get("main", {})
    if isinstance(main, dict) and main.get("name") == ontology_name:
        return "main"
    for ext in ontologies.get("extensions", []) or []:
        if isinstance(ext, dict) and ext.get("name") == ontology_name:
            return "extension"
    return "main"


def _is_extension_ontology(ontology_name: str, config_path: str = "configs/meta_task/meta_task_config.json") -> bool:
    return _ontology_role(ontology_name, config_path=config_path) == "extension"


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


def _resolve_kg_generation_model(ontology: str) -> str:
    """Resolve KG-prompt generation model without ontology-specific fallbacks."""
    return os.environ.get("PROMPT_CREATION_MODEL", MODEL)


def _load_ontology_integrity_guidance(tbox_path: Path) -> str:
    """Build generic ontology-derived integrity guidance for prompt assembly."""
    try:
        profile = extract_ontology_integrity_profile(str(tbox_path))
        return format_ontology_integrity_guidance(profile, include_machine_readable=True)
    except Exception:
        return ""


def _rdf_list_items(graph: Graph, node: Any) -> List[Any]:
    items: List[Any] = []
    current = node
    while current and current != RDF.nil:
        first = graph.value(current, RDF.first)
        if first is not None:
            items.append(first)
        current = graph.value(current, RDF.rest)
    return items


def _domain_iris(graph: Graph, domain: Any) -> List[str]:
    if isinstance(domain, URIRef):
        return [str(domain)]
    out: List[str] = []
    for union_node in graph.objects(domain, OWL.unionOf):
        out.extend(str(x) for x in _rdf_list_items(graph, union_node) if isinstance(x, URIRef))
    return out


def _local_name(iri: Any) -> str:
    text = str(iri or "").strip()
    if not text:
        return ""
    return text.rstrip("/#").rsplit("/", 1)[-1].rsplit("#", 1)[-1]


def _step_scoped_object_property_guidance(tbox_path: Path) -> str:
    """
    Derive construction rules for object properties whose domain is an ordered
    synthesis step class. These are not publish-time repairs: the prompt tells
    the KG agent which relationship tools must be used during graph construction.
    """
    try:
        profile = extract_ontology_integrity_profile(str(tbox_path))
        ordered_classes = {
            str(x).strip()
            for x in (profile.get("ordered_member_classes") or [])
            if str(x).strip()
        }
        if not ordered_classes:
            return ""
        graph = Graph()
        graph.parse(str(tbox_path), format="turtle")
    except Exception:
        return ""

    rows: List[tuple[str, str, str]] = []
    for prop in graph.subjects(RDF.type, OWL.ObjectProperty):
        if not isinstance(prop, URIRef):
            continue
        domains: List[str] = []
        for domain in graph.objects(prop, RDFS.domain):
            domains.extend(_domain_iris(graph, domain))
        ranges = [str(r) for r in graph.objects(prop, RDFS.range) if isinstance(r, URIRef)]
        for domain_iri in domains:
            domain_local = _local_name(domain_iri)
            if domain_local not in ordered_classes:
                continue
            for range_iri in ranges:
                if "ontology-of-units-of-measure.org" in range_iri:
                    continue
                rows.append((domain_local, _local_name(prop), _local_name(range_iri)))

    if not rows:
        return ""

    lines = [
        "Step-scoped object-property contract (ontology-derived):",
        "- For every ordered step individual, materialize step-scoped object properties during the same KG-building iteration; do not leave them only as synthesis-level links.",
        "- When the MCP constructor exposes a matching label/IRI argument for a step-scoped object, pass it when creating the step. Otherwise call the matching relationship tool before export.",
        "- Export is invalid if an extracted step-scoped relation is represented only by a parent-level relation.",
    ]
    for domain_local, prop_local, range_local in sorted(set(rows)):
        lines.append(
            f"- `{domain_local}` -> `{prop_local}` -> `{range_local}`: "
            f"when a `{domain_local}` step is created from hints that identify a `{range_local}`, "
            f"attach that `{range_local}` to the step via `{prop_local}` before export."
        )
    return "\n".join(lines)


def _ordered_member_placeholder_guidance(tbox_path: Path) -> str:
    """Add generic one-placeholder-to-one-individual guidance for ordered members."""
    try:
        profile = extract_ontology_integrity_profile(str(tbox_path))
    except Exception:
        return ""

    if not (profile.get("ordered_member_classes") and profile.get("individually_linked_object_properties")):
        return ""

    return (
        "Ordered-member placeholder mapping:\n"
        "- When extracted hints use placeholder member tokens such as `<member1>`, `<member2>`, `<step1>`, or similar IDs, keep a stable one-to-one mapping from each distinct placeholder token to exactly one created individual IRI.\n"
        "- Never merge two different placeholder tokens into the same individual, even if they share a class, vessel, duration, or similar labels.\n"
        "- Apply every triple attached to a placeholder token only to that placeholder's own individual.\n"
        "- If a placeholder needs a concrete subclass, infer the narrowest compatible class from its attached properties while preserving one individual per placeholder token.\n"
    )


def _ordered_member_workflow_example_guidance(tbox_path: Path) -> str:
    """Add a generic create-parse-attach example for ordered members."""
    try:
        profile = extract_ontology_integrity_profile(str(tbox_path))
    except Exception:
        return ""

    if not (profile.get("ordered_member_classes") and profile.get("individually_linked_object_properties")):
        return ""

    return (
        "Generic ordered-member workflow example:\n"
        "- Create one ordered member at a time, parse the returned JSON, capture its `iri`, and attach it immediately before moving to the next member.\n"
        "- Example pattern (replace placeholder tool names with the actual ontology tools available in this server):\n"
        "  1. call `create_<ConcreteOrderedMember>(label=\"member 1\", hasOrder=1, ...)`\n"
        "  2. parse the JSON result and capture `member_iri`\n"
        "  3. call `add_<ParentMembershipRelation>(<scoped_top_entity_iri>, member_iri)` immediately\n"
        "  4. repeat for order 2, 3, ... in strict sequence\n"
        "- Do not create a batch of ordered members first and postpone all parent links until later.\n"
        "- When the extracted hints give properties for an ordered member, materialize those properties on that same member before export; do not stop after creating only the ordered member type and order.\n"
        "- For quantity-like property tools, pass the extracted numeric value and the closest supported unit label from the available tool schema; do not omit the property only because the source used an abbreviation.\n"
        "- If `check_orphan_entities` is available and it reports orphan ordered members, attach those members before export.\n"
        "- Do not create placeholder, dummy, sample, or example ordered members to satisfy required links; only create members supported by source content.\n"
        "- Export only after the ordered members are both created and linked back to the scoped top-level entity.\n"
    )


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


def _scoped_top_entity_integrity_rules() -> str:
    """
    Append generic scoped-entity integrity rules for iterations that already operate
    on a provided top-level entity. This stays domain-agnostic while preventing the
    LLM from drifting into duplicate top-entity creation or incomplete linking.
    """
    return (
        "Scoped top-level entity integrity:\n"
        "- Treat the provided top-level entity IRI as authoritative for this iteration and reuse it exactly.\n"
        "- Do not create, switch to, or export around a second top-level entity for the same scope.\n"
        "- Every entity created or reused in this iteration must be linked back to the scoped top-level entity through the required ontology relations before export.\n"
        "- Do not terminate or export memory until those required links are complete.\n"
    )


def _super_flat_prompt_rules(shape_info: Dict[str, Any], iter_number: int) -> str:
    if not shape_info.get("is_super_flat"):
        return ""

    top_class = shape_info.get("top_level_class")
    if not top_class:
        return ""

    if iter_number == 1:
        return (
            "\n\nSuper-flat ontology rules:\n"
            f"- The ontology has a single main class `{top_class}` with datatype fields only.\n"
            f"- For this iteration, use ONLY `create_{top_class}_top_only` to create the top entity.\n"
            "- Pass only the minimal identifier/label needed to create the entity.\n"
            "- Do NOT pass detailed datatype fields during iteration 1.\n"
            "- Do NOT create duplicate top entities if one with the same label already exists.\n"
        )

    return (
        "\n\nSuper-flat ontology rules:\n"
        f"- The scoped top-level `{top_class}` entity already exists for this iteration.\n"
        f"- Do NOT create another `{top_class}` instance.\n"
        "- First call `init_memory` with the DOI and the provided entity label so you work in the correct entity-scoped memory.\n"
        "- The `paper_content` for this iteration contains human-readable extracted fields (key/value + evidence).\n"
        f"- For each extracted field, call the matching atomic setter tool `set_{top_class}_<PropertyName>(entity_iri, value)` — one tool call per property.\n"
        f"  Example: to set a field, call `set_{top_class}_<ExactPropertyName>(entity_iri=<iri>, value='<encoded_or_text_value>')`.\n"
        "- Every available setter is named exactly `set_{TopClass}_{ExactPropertyName}` — use the exact ontology property name as the suffix.\n"
        "- Use the provided `entity_uri` as the `entity_iri` argument for every setter call.\n"
        "- If the extracted hints contain any non-empty values, at least one successful setter call is mandatory before termination.\n"
        "- After all setters have been called, call `export_memory` before emitting `run_status: done`.\n"
        "- Do NOT terminate immediately after reading the hints; initialization, setter calls, and export are the required sequence for this iteration.\n"
    )


def _extension_iter_specific_rules(ontology: str, iter_number: int) -> str:
    """Return concrete runtime guidance for extension ontologies in entity-scoped iterations."""
    if iter_number not in [2, 3, 4]:
        return ""

    return (
        "\n\nExtension ontology rules (entity-scoped iterations):\n"
        "- Call `init_memory` with the provided DOI and entity label so writes go to the correct scoped graph.\n"
        "- Treat the provided `entity_uri` as the only scoped top-level root for this iteration; do not mint a second root for the same scope.\n"
        "- Use only tools whose names appear in the bundled MCP main script; call existing-instance checks when such tools exist before creating new individuals.\n"
        "- Add at most one new individual for each explicitly evidenced scoped fact unless the ontology or extracted hints require multiple individuals.\n"
        "- Map structured fields in `paper_content` to the constructors and relationship tools that appear in the MCP script, using only values supported by the text.\n"
        "- Do not pull in optional external resources or heavy tool calls unless the task explicitly requires them.\n"
        "- Avoid duplicate individuals for the same fact: use stable human-readable labels, and do not append decorative or instruction-like suffixes to labels.\n"
        "- If a technique or value is not evidenced in `paper_content`, omit that node; do not fill gaps with placeholder values.\n"
        "- Do not loop on repeated checks: when a check shows a required node is missing, create it in the next tool call.\n"
        "- After the required graph updates, call `export_memory()` and terminate with `{{\"run_status\":\"done\"}}`.\n"
    )


def _generate_and_write_kg_iter1_prompt(
    llm,
    tbox_text: str,
    out_path: Path,
    ontology: str = "",
    shape_info: Optional[Dict[str, Any]] = None,
    integrity_guidance: str = "",
) -> None:
    """Generate ITER1 KG building prompt.
    
    For extension ontologies: generates comprehensive A-Box building prompts
    For main ontology: generates ITER1-specific prompts
    """
    is_extension = _is_extension_ontology(ontology)
    
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
    super_flat_rules = _super_flat_prompt_rules(shape_info or {}, 1)

    parts: List[str] = [text]
    if integrity_guidance:
        parts.append(integrity_guidance.strip())

    if is_extension:
        final_text = "\n\n".join(part for part in parts if part)
    else:
        # Programmatically append input variables section for ITER1 (main ontology)
        input_vars_section = _generate_kg_input_variables_section(1)
        if super_flat_rules:
            parts.append(super_flat_rules.strip())
        parts.append(input_vars_section.strip())
        final_text = "\n\n".join(part for part in parts if part)
    
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(final_text)
    
    print(f"   (using {'extension' if is_extension else 'main ontology'} meta-prompts)")


def _generate_and_write_kg_prompt(
    llm,
    tbox_text: str,
    iter_meta: Dict[str, Any],
    out_path: Path,
    ontology: str = "ontosynthesis",
    shape_info: Optional[Dict[str, Any]] = None,
    integrity_guidance: str = "",
) -> None:
    """Generate KG building prompt using hardcoded template for ITER 2, 3, 4.
    
    For ITER 2, 3, 4: Uses hardcoded template directly (no LLM generation).
    For other iterations: Falls back to LLM generation (if needed in future).
    """
    iter_number = iter_meta.get("iteration_number", 1)
    super_flat_rules = _super_flat_prompt_rules(shape_info or {}, iter_number)
    extension_rules = _extension_iter_specific_rules(ontology, iter_number)
    
    # For ITER 2, 3, 4: Use the shared hardcoded template directly.
    if iter_number in [2, 3, 4]:
        # Load and format the hardcoded template
        template_text = KG_BUILDING_ITER_TEMPLATE
        
        # Replace template placeholders
        final_text = template_text.replace("{PROMPT_CORE}", PROMPT_CORE_TEMPLATE)
        final_text = final_text.replace("{IDENTIFICATION_HEADER}", IDENTIFICATION_HEADER)
        footer_parts = []
        if extension_rules:
            footer_parts.append(extension_rules.strip())
        if integrity_guidance:
            footer_parts.append(integrity_guidance.strip())
        if super_flat_rules:
            footer_parts.append(super_flat_rules.strip())
        footer_parts.append(FOOTER_WITH_ENTITY)
        footer_text = "\n\n".join(part for part in footer_parts if part)
        final_text = final_text.replace("{FOOTER_WITH_ENTITY}", footer_text)
        
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
        trailing_parts = []
        if integrity_guidance:
            trailing_parts.append(integrity_guidance.strip())
        if super_flat_rules:
            trailing_parts.append(super_flat_rules.strip())
        trailing_parts.append(input_vars_section.strip())
        final_text = text + "\n\n" + "\n\n".join(part for part in trailing_parts if part)
    
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(final_text)


def generate_kg_prompts_from_iterations(ontology: str, *, tbox_path: str | Path | None = None) -> bool:
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
        resolved_tbox = Path(tbox_path) if tbox_path else Path(_ontology_tbox_path(ontology))
        tbox_text = load_tbox(resolved_tbox)
        shape_info = detect_super_flat_ontology(str(resolved_tbox))
        integrity_guidance = _load_ontology_integrity_guidance(resolved_tbox)
        placeholder_guidance = _ordered_member_placeholder_guidance(resolved_tbox)
        workflow_example_guidance = _ordered_member_workflow_example_guidance(resolved_tbox)
        step_scoped_property_guidance = _step_scoped_object_property_guidance(resolved_tbox)
        if placeholder_guidance or workflow_example_guidance or step_scoped_property_guidance:
            integrity_guidance = "\n\n".join(
                part
                for part in [
                    integrity_guidance.strip(),
                    placeholder_guidance.strip(),
                    workflow_example_guidance.strip(),
                    step_scoped_property_guidance.strip(),
                ]
                if part
            )
        
        print(f"\n=== Generating KG prompts for ontology: {ontology} ===")
        print(f"Iterations file: {iterations_path}")
        print(f"T-Box: {resolved_tbox}")
        
        llm = LLMCreator(
            model=_resolve_kg_generation_model(ontology),
            remote_model=True,
            model_config=ModelConfig(temperature=0, top_p=1.0),
            structured_output=False,
        ).setup_llm()
        
        # ALWAYS generate ITER1 KG prompt separately (not from iterations.json)
        iter1_path = _candidate_prompt_path_from(f"ai_generated_contents/prompts/{ontology}/KG_BUILDING_ITER_1.md")
        print(f"  -> [{ontology}] ITER 1 KG prompt (generated separately)")
        print(f"     output path: {iter1_path}")
        _generate_and_write_kg_iter1_prompt(
            llm,
            tbox_text,
            iter1_path,
            ontology,
            shape_info,
            integrity_guidance,
        )
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
                    _generate_and_write_kg_prompt(
                        llm,
                        tbox_text,
                        it,
                        target,
                        ontology,
                        shape_info,
                        integrity_guidance,
                    )
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

