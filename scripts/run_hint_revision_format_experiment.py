from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from models.LLMCreator import LLMCreator
from models.ModelConfig import ModelConfig
from src.pipelines.main_ontology_extractions.extract import (
    _kg_revision_relation_errors,
    get_extraction_model,
)
from src.pipelines.structured_extraction import validate_hint_payload


def _typed_refs(*payloads: Any) -> dict[str, str]:
    found: dict[str, str] = {}

    def visit(value: Any) -> None:
        if isinstance(value, list):
            for item in value:
                visit(item)
            return
        if not isinstance(value, dict):
            return
        ref = str(value.get("ref") or "").strip()
        class_local = str(value.get("class") or "").strip()
        if ref and class_local:
            found[ref] = class_local
        for item in value.values():
            visit(item)

    for payload in payloads:
        visit(payload)
    return dict(sorted(found.items()))


async def _invoke(prompt: str, model_name: str) -> str:
    llm = LLMCreator(
        model=model_name,
        model_config=ModelConfig(temperature=0, top_p=1.0),
        remote_model=True,
    ).setup_llm()
    result = await llm.ainvoke(prompt)
    content = getattr(result, "content", result)
    if isinstance(content, list):
        return "".join(
            str(item.get("text") if isinstance(item, dict) else item)
            for item in content
        )
    return str(content)


def _compact_structured_prompt(
    *,
    source: str,
    current_hints: str,
    typed_refs: dict[str, str],
    feedback: str,
) -> str:
    return f"""Correct the extraction hints below using only the source and contract error.

Highest-priority rules:
- Return one complete corrected JSON payload with arrays `entities` and `relations`.
- Preserve valid content and exact ref/class bindings.
- Remove every reported invalid relation.
- Resolve endpoint type only from TYPED REF REGISTRY, never from a ref name or label.
- hasWashingSolvent and hasAddedChemicalInput objects must be typed ChemicalInput.
- If no supported ChemicalInput ref exists, omit the invalid relation. Never use an Add
  as a ChemicalInput and never emit a self-relation.

CONTRACT ERROR:
{feedback}

TYPED REF REGISTRY:
{json.dumps(typed_refs, ensure_ascii=False, indent=2)}

SOURCE:
{source}

CURRENT HINTS TO CORRECT:
{current_hints}

Return only the complete corrected JSON."""


def _unstructured_prompt(
    *,
    source: str,
    typed_refs: dict[str, str],
    feedback: str,
) -> str:
    return f"""Act as a semantic extraction editor. Do not output JSON or follow a schema.
Write a concise, human-readable corrected semantic hint for this synthesis.

Focus on the reported contract error and preserve source meaning:
- State which existing step refs are Add or Filter.
- State which exact ChemicalInput ref, if any, is the washing solvent or added input.
- A ref typed Add is an operation, never a material.
- If the registry has no source-supported ChemicalInput ref for a solvent, explicitly say
  the relation must be omitted until that ChemicalInput is extracted.
- Never describe a self-relation.
- End with a short "Corrections applied" list naming every rejected relation and its fate.

CONTRACT ERROR:
{feedback}

TYPED REF REGISTRY:
{json.dumps(typed_refs, ensure_ascii=False, indent=2)}

SOURCE:
{source}

Return only the natural-language semantic hint."""


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime", required=True)
    parser.add_argument("--case", required=True)
    parser.add_argument("--entity", required=True)
    parser.add_argument("--feedback")
    parser.add_argument("--feedback-json")
    parser.add_argument("--model-key", default="iter3_hints")
    args = parser.parse_args()

    case_root = Path(args.runtime) / args.case
    source_path = case_root / "pre_extraction" / f"entity_text_{args.entity}.txt"
    base_path = case_root / "mcp_run" / f"iter3_base_hints_{args.entity}.txt"
    iter2_path = case_root / "mcp_run" / f"iter2_hints_{args.entity}.txt"
    source = source_path.read_text(encoding="utf-8")
    current_hints = base_path.read_text(encoding="utf-8")
    current_payload = json.loads(current_hints)
    iter2_payload = (
        json.loads(iter2_path.read_text(encoding="utf-8"))
        if iter2_path.exists()
        else {}
    )
    if args.feedback_json:
        feedback = args.feedback_json
    elif args.feedback:
        feedback = Path(args.feedback).read_text(encoding="utf-8")
    else:
        parser.error("one of --feedback or --feedback-json is required")
    refs = _typed_refs(current_payload, iter2_payload)
    model_name = get_extraction_model(args.model_key)

    structured_prompt = _compact_structured_prompt(
        source=source,
        current_hints=current_hints,
        typed_refs=refs,
        feedback=feedback,
    )
    unstructured_prompt = _unstructured_prompt(
        source=source,
        typed_refs=refs,
        feedback=feedback,
    )
    structured, unstructured = await asyncio.gather(
        _invoke(structured_prompt, model_name),
        _invoke(unstructured_prompt, model_name),
    )

    try:
        structured_ok, structured_errors = validate_hint_payload(
            structured,
            accumulated_hints=json.dumps(iter2_payload, ensure_ascii=False),
            expected_schema="ref-entity-relations.v1",
        )
    except ValueError as exc:
        structured_ok, structured_errors = False, [str(exc)]
    retained_errors = _kg_revision_relation_errors(structured, feedback)
    report = {
        "model": model_name,
        "entity": args.entity,
        "input_sizes": {
            "source_chars": len(source),
            "current_hint_chars": len(current_hints),
            "typed_ref_count": len(refs),
            "compact_structured_prompt_chars": len(structured_prompt),
            "unstructured_prompt_chars": len(unstructured_prompt),
        },
        "compact_structured": {
            "output": structured,
            "schema_valid": structured_ok,
            "schema_errors": structured_errors,
            "retained_reported_relation_errors": retained_errors,
        },
        "unstructured": {"output": unstructured},
    }
    output_dir = case_root / "experiments" / "hint_revision_format"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{args.entity}.json"
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"HINT_FORMAT_EXPERIMENT_RESULT {output_path}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
