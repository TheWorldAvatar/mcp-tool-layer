#!/usr/bin/env python3
"""
task_division_agent.py

Generates a structured JSON plan for building an A-Box from the OntoSynthesis T-Box.
The plan divides the KG construction into multiple steps, where each step specifies:
- What instances to create
- What relations to establish
- What information to extract from papers to support the task

Output: A JSON file that can be read by downstream agents.
"""

import os
import json
import time
import argparse
from pathlib import Path
from typing import Dict, Any
from dotenv import load_dotenv

from models.LLMCreator import LLMCreator
from models.ModelConfig import ModelConfig

# -------- Config --------
TBOX_PATH = "data/ontologies/ontosynthesis.ttl"
OUTPUT_PATH = "configs/task_division_plan.json"
MODEL = os.environ.get("TASK_DIVISION_MODEL", "gpt-5")

# -------- Load environment --------
load_dotenv(override=True)

# -------- System Prompt --------
SYSTEM_PROMPT = """You are a rigorous knowledge graph architect specializing in ontology-driven A-Box construction planning.

Given an OWL/RDF T-Box (ontology schema), you will design a multi-step plan to build a conformant A-Box (instance knowledge graph) from scientific papers.

Your output must be valid JSON only. No markdown, no prose."""

# -------- User Prompt --------
USER_PROMPT = """You are given an OWL/RDF T-Box (ontology schema) for chemical synthesis procedures.

## Objective

Design a complete, multi-step plan to construct an A-Box (instance knowledge graph) from scientific papers that fully conforms to the given T-Box.

**Critical**: You must cover all classes and properties in the T-Box in the steps you design.

## Output Format

Return a JSON object with the following structure:

```json
{{
  "metadata": {{
    "ontology_name": "string",
    "total_steps": integer,
    "top_level_entity": "string (the main class that anchors the entire KG)"
  }},
  "steps": [
    {{
      "step_number": integer,
      "step_name": "string (short descriptive name)",
      "goal": "string (what this step accomplishes)",
      "instances_to_create": [
        {{
          "class": "string (full IRI or prefixed name)",
          "description": "string (what instances of this class represent)",
          "cardinality": "string (e.g., 'one per synthesis', 'multiple per synthesis', 'one', etc.)"
        }}
      ],
      "relations_to_establish": [
        {{
          "property": "string (full IRI or prefixed name)",
          "domain": "string (source class)",
          "range": "string (target class or datatype)",
          "description": "string (what this relation captures)"
        }}
      ],
      "information_to_extract": [
        "string (specific pieces of information needed from the paper)"
      ],
      "constraints": [
        "string (rules, cardinality constraints, validation requirements)"
      ] 
    }}
  ]
}}
```

## Planning Rules

### Top-Level Entity
- Identify the top-level entity class from the T-Box (the root of the instance hierarchy)
- Step 1 MUST create ONLY the top-level entity instances and link them to their source document
- DO NOT create any related entities in Step 1

### Step Design Principles
1. **Incremental complexity**: Start simple (top entities only), progressively add depth
2. **Atomicity**: Each step should have a clear, focused goal
3. **Strict sequencing**: Steps MUST be executed in order (1, 2, 3, ..., N). Each step can assume all previous steps are complete. Do NOT include a `depends_on_steps` field.
4. **Completeness**: The union of all steps must cover ALL classes and properties in the T-Box
5. **Extraction clarity**: Be explicit about what information to extract at each step
6. **Entity focus**: Steps 2+ should scope work to specific top-level entities (one at a time)

### Step Count
- Use 4-6 steps maximum
- Step 1: Top-level entities only
- Steps 2-5: Progressive construction (inputs/outputs, then steps, then metadata)
- Final step: Complete remaining properties (yield, equipment, provenance)

### Information Extraction
For each step, specify:
- Concrete data points to extract (names, formulas, amounts, conditions, etc.)
- Extraction scope (whole paper vs. specific sections)
- Entity context (global vs. per-synthesis)

### Constraints
For each step, include:
- Cardinality rules (e.g., "exactly one ChemicalOutput per ChemicalSynthesis")
- Validation rules (e.g., "do not create ChemicalInput for common solvents")
- Deduplication rules (e.g., "avoid duplicate Supplier instances")
- Stop conditions (e.g., "when all inputs are linked or skipped")

## Important Considerations

1. **Preserve T-Box semantics**: Respect all rdfs:comment constraints in the ontology
2. **No invention**: Extract only what is explicitly stated in papers
3. **Handle uncertainty**: Include rules for handling missing or ambiguous information
4. **Provenance**: Ensure traceability to source documents
5. **Modularity**: Each step should be executable independently given its prerequisites

## T-Box

```turtle
{tbox}
```

## Output

Return ONLY the JSON object. No additional text, markdown fences, or explanations.
"""

# -------- Helper Functions --------

def coerce_json_from_text(txt: str) -> Any:
    """Extract JSON from LLM response, handling markdown fences and malformed JSON."""
    import re
    
    txt = txt.strip()
    
    # Remove markdown code fences if present
    if txt.startswith("```"):
        lines = txt.split("\n")
        # Remove first line (```json or ```)
        lines = lines[1:]
        # Remove last line if it's ```
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        txt = "\n".join(lines)
    
    # Try direct JSON parse
    try:
        return json.loads(txt)
    except json.JSONDecodeError:
        pass
    
    # Try to find JSON object using regex
    match = re.search(r'\{[\s\S]*\}', txt)
    if match:
        json_str = match.group(0)
        try:
            return json.loads(json_str)
        except json.JSONDecodeError:
            pass
    
    # Fallback: find largest {...} block
    first = txt.find("{")
    last = txt.rfind("}")
    
    if first == -1 or last == -1 or last <= first:
        raise ValueError("No JSON object found in response")
    
    while first != -1 and last != -1 and last > first:
        chunk = txt[first:last + 1]
        try:
            return json.loads(chunk)
        except json.JSONDecodeError:
            last = txt.rfind("}", 0, last)
    
    raise ValueError("Failed to parse JSON from model output.")


def validate_plan_structure(plan: Dict[str, Any]) -> None:
    """Validate that the generated plan has the required structure."""
    required_top_keys = ["metadata", "steps"]
    for key in required_top_keys:
        if key not in plan:
            raise ValueError(f"Missing required top-level key: {key}")
    
    metadata = plan["metadata"]
    required_metadata_keys = ["ontology_name", "total_steps", "top_level_entity"]
    for key in required_metadata_keys:
        if key not in metadata:
            raise ValueError(f"Missing required metadata key: {key}")
    
    steps = plan["steps"]
    if not isinstance(steps, list):
        raise ValueError("'steps' must be a list")
    
    if len(steps) != metadata["total_steps"]:
        raise ValueError(f"Number of steps ({len(steps)}) does not match total_steps ({metadata['total_steps']})")
    
    required_step_keys = [
        "step_number", "step_name", "goal", 
        "instances_to_create", "relations_to_establish",
        "information_to_extract", "constraints"
    ]
    
    for i, step in enumerate(steps, 1):
        if step["step_number"] != i:
            raise ValueError(f"Step numbering error: expected {i}, got {step['step_number']}")
        
        for key in required_step_keys:
            if key not in step:
                raise ValueError(f"Step {i} missing required key: {key}")
    
    print("✅ Plan structure validation passed")


# -------- Core Function --------

def generate_task_division_plan(tbox_text: str, model: str = MODEL, max_retries: int = 3) -> Dict[str, Any]:
    """Generate task division plan from T-Box using LLM with automatic retry on JSON parse failures."""
    
    # Create LLM with low temperature for deterministic output
    model_config = ModelConfig(temperature=0.1, top_p=1)
    llm = LLMCreator(
        model=model,
        remote_model=True,
        model_config=model_config,
        structured_output=False
    ).setup_llm()
    
    # Build full prompt (system + user) - explicitly request JSON in prompt
    full_prompt = f"{SYSTEM_PROMPT}\n\nYou MUST respond with ONLY valid JSON. No markdown, no code fences, no explanations.\n\n{USER_PROMPT.format(tbox=tbox_text.strip())}"
    
    for attempt in range(1, max_retries + 1):
        print(f"🤖 Calling LLM (attempt {attempt}/{max_retries}): {model}")
        start = time.time()
        
        try:
            # Invoke LLM
            resp_obj = llm.invoke(full_prompt)
            
            elapsed = time.time() - start
            print(f"✅ LLM response received in {elapsed:.2f}s")
            
            # Extract content from response
            content = getattr(resp_obj, "content", None) if resp_obj is not None else None
            if not isinstance(content, str):
                content = str(resp_obj) if resp_obj is not None else ""
            
            # Parse and validate JSON
            plan = coerce_json_from_text(content)
            validate_plan_structure(plan)
            
            print(f"✅ Plan parsed and validated successfully")
            return plan
            
        except json.JSONDecodeError as e:
            print(f"⚠️  Attempt {attempt} failed: JSON parse error - {e}")
            if attempt < max_retries:
                print("🔄 Retrying...")
                time.sleep(2)
            else:
                raise RuntimeError(f"Failed to parse valid JSON after {max_retries} attempts") from e
        except ValueError as e:
            print(f"⚠️  Attempt {attempt} failed: {e}")
            if attempt < max_retries:
                print("🔄 Retrying...")
                time.sleep(2)
            else:
                raise RuntimeError(f"Failed to generate valid plan after {max_retries} attempts") from e
        except Exception as e:
            print(f"⚠️  Attempt {attempt} failed with unexpected error: {e}")
            if attempt < max_retries:
                print("🔄 Retrying...")
                time.sleep(2)
            else:
                raise RuntimeError(f"Failed to generate valid plan after {max_retries} attempts") from e
    
    raise RuntimeError("Unexpected exit from retry loop")


# -------- Main --------

def main():
    parser = argparse.ArgumentParser(
        description="Generate a structured task division plan for A-Box construction from T-Box"
    )
    parser.add_argument(
        "--tbox",
        type=str,
        default=TBOX_PATH,
        help=f"Path to T-Box TTL file (default: {TBOX_PATH})"
    )
    parser.add_argument(
        "--output",
        type=str,
        default=OUTPUT_PATH,
        help=f"Path to output JSON file (default: {OUTPUT_PATH})"
    )
    parser.add_argument(
        "--model",
        type=str,
        default=MODEL,
        help=f"LLM model to use (default: {MODEL})"
    )
    
    args = parser.parse_args()
    
    # Use the model from args or default
    model_to_use = args.model
    
    # Read T-Box
    tbox_path = Path(args.tbox)
    if not tbox_path.exists():
        raise FileNotFoundError(f"T-Box file not found: {tbox_path}")
    
    print(f"📖 Reading T-Box from: {tbox_path}")
    with open(tbox_path, "r", encoding="utf-8") as f:
        tbox_text = f.read()
    
    print(f"📊 T-Box size: {len(tbox_text)} characters")
    
    # Generate plan
    print("\n" + "=" * 60)
    print("GENERATING TASK DIVISION PLAN")
    print(f"Model: {model_to_use}")
    print("=" * 60 + "\n")
    
    plan = generate_task_division_plan(tbox_text, model=model_to_use)
    
    # Write output
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(plan, f, indent=2, ensure_ascii=False)
    
    print("\n" + "=" * 60)
    print("✅ TASK DIVISION PLAN GENERATED SUCCESSFULLY")
    print("=" * 60)
    print(f"\n📄 Output written to: {output_path.absolute()}")
    print(f"\n📋 Summary:")
    print(f"   - Ontology: {plan['metadata']['ontology_name']}")
    print(f"   - Total steps: {plan['metadata']['total_steps']}")
    print(f"   - Top-level entity: {plan['metadata']['top_level_entity']}")
    print(f"\n📝 Steps:")
    for step in plan["steps"]:
        print(f"   Step {step['step_number']}: {step['step_name']}")
        print(f"      Goal: {step['goal']}")
        print(f"      Creates {len(step['instances_to_create'])} class(es), {len(step['relations_to_establish'])} relation(s)")
    
    print("\n" + "=" * 60 + "\n")


if __name__ == "__main__":
    main()

