from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.agents.scripts_and_prompts_generation.domain_artifact_compiler import (
    build_domain_generation_context,
)
from src.agents.scripts_and_prompts_generation.materialization_operation_units import (
    discover_materialization_operation_candidates,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Derive fixed operation candidates from generated config artifacts."
    )
    parser.add_argument("--domain-config", required=True)
    parser.add_argument("--top-summary", required=True)
    parser.add_argument("--reuse-policy", required=True)
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--output-contract", required=True)
    parser.add_argument("--repository-root", default=".")
    args = parser.parse_args()
    top = json.loads(Path(args.top_summary).read_text(encoding="utf-8"))
    if not top.get("passed_10_of_10_gate"):
        raise ValueError("top-entity gate has not passed")
    decision = top["results"][0]["decision"]
    planner_result = {
        "class_local": decision["class_local"],
        "rationale": decision["rationale"],
        "evidence": decision["evidence"],
    }
    context = build_domain_generation_context(
        domain_config_path=args.domain_config,
        output_root=args.output_root,
        repository_root=args.repository_root,
        write_files=False,
        planner=lambda _model, _prompt: dict(planner_result),
        operation_mode="legacy",
        derived_reuse_policy_path=args.reuse_policy,
    )
    candidates = discover_materialization_operation_candidates(
        parsed=context.parsed,
        contract=context.contract,
        iteration_plan=context.iteration_blueprint,
    )
    contract = dict(context.contract)
    contract["materialization_operation_candidates"] = candidates
    output = Path(args.output_contract)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(contract, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "output": str(output),
                "candidate_count": len(candidates.get("candidates") or []),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
