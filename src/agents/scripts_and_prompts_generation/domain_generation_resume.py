"""Resume domain generation from persisted semantic and artifact checkpoints."""

from __future__ import annotations

import argparse
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

from src.agents.scripts_and_prompts_generation.agentic_generation_context import (
    AgenticGenerationContext,
    build_agentic_generation_context,
)
from src.agents.scripts_and_prompts_generation.iteration_plan_compiler import (
    compile_iteration_plan,
)
from src.agents.scripts_and_prompts_generation.pure_llm_generation import (
    run_pure_llm_generation_rounds,
)


def load_domain_generation_checkpoint(
    *,
    output_root: str | Path,
    ontology_name: str,
) -> AgenticGenerationContext:
    """Rebuild a generation context without repeating semantic planning."""
    root = Path(output_root)
    adapter = (
        root / "derived_inputs" / ontology_name / "meta_task_adapter.json"
    )
    semantic_plan_path = (
        root / "semantic_planning" / ontology_name / "accepted_semantic_plan.json"
    )
    blueprint_path = (
        root / "derived_inputs" / ontology_name / "iteration_blueprint.json"
    )
    for path in (adapter, semantic_plan_path, blueprint_path):
        if not path.is_file():
            raise FileNotFoundError(f"generation checkpoint input not found: {path}")

    context = build_agentic_generation_context(
        ontology_name=ontology_name,
        meta_task_config_path=adapter,
        output_root=root,
        write_files=False,
    )
    decisions = json.loads(semantic_plan_path.read_text(encoding="utf-8"))
    blueprint = json.loads(blueprint_path.read_text(encoding="utf-8"))
    persisted_contract_path = (
        root / "ontology_structures" / ontology_name / "generation_contract.json"
    )
    contract = (
        json.loads(persisted_contract_path.read_text(encoding="utf-8"))
        if persisted_contract_path.is_file()
        else dict(context.contract)
    )
    runtime_contract_path = (
        root / "scripts" / ontology_name / "_relationship_contract.json"
    )
    if runtime_contract_path.is_file() and not contract.get("reuse_policy"):
        runtime_contract = json.loads(runtime_contract_path.read_text(encoding="utf-8"))
        runtime_policy = runtime_contract.get("reuse_policy")
        if runtime_policy:
            contract["reuse_policy"] = runtime_policy
            publish = dict(contract.get("ontology_publish_contract") or {})
            publish["reuse_policy"] = runtime_policy
            contract["ontology_publish_contract"] = publish
    contract["top_entity"] = dict(decisions["top_entity"])
    plan = compile_iteration_plan(
        blueprint=blueprint,
        parsed=context.parsed,
        contract=contract,
        ontology_name=ontology_name,
        blueprint_provenance={
            "source": "accepted_semantic_plan_resume",
            "model": str(decisions.get("model") or "gpt-5"),
        },
    )
    return replace(context, contract=contract, iteration_blueprint=plan)


def resume_domain_generation(
    *,
    output_root: str | Path,
    ontology_name: str,
    model_name: str = "gpt-5",
    max_workers: int = 3,
    max_rounds: int = 2,
) -> dict[str, Any]:
    """Resume pending artifacts while reusing validated non-empty checkpoints."""
    context = load_domain_generation_checkpoint(
        output_root=output_root,
        ontology_name=ontology_name,
    )
    return run_pure_llm_generation_rounds(
        context,
        model_name=model_name,
        max_rounds=max_rounds,
        generate_scripts=True,
        generate_prompts=True,
        focused_repair=True,
        incremental_generation_repair=True,
        parallel_generation=True,
        max_generation_workers=max_workers,
        max_focus_targets=3,
        edit_backend="exact_edits",
    )


def main() -> int:
    """Run resumable bounded-parallel generation from the command line."""
    parser = argparse.ArgumentParser(
        description="Resume domain artifact generation from persisted checkpoints."
    )
    parser.add_argument("--ontology", required=True)
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--generation-model", default="gpt-5")
    parser.add_argument("--max-generation-workers", type=int, default=3)
    parser.add_argument("--max-agent-rounds", type=int, default=2)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if args.max_generation_workers < 1:
        parser.error("--max-generation-workers must be at least 1")

    result = resume_domain_generation(
        output_root=args.output_root,
        ontology_name=args.ontology,
        model_name=args.generation_model,
        max_workers=args.max_generation_workers,
        max_rounds=args.max_agent_rounds,
    )
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
