from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from src.agents.scripts_and_prompts_generation.domain_artifact_compiler import (
    build_domain_generation_context,
)


def _canonical_bytes(payload: Any) -> bytes:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Derive and byte-check a T-Box iteration blueprint."
    )
    parser.add_argument("--domain-config", required=True)
    parser.add_argument("--top-summary", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--repository-root", default=".")
    parser.add_argument("--reuse-policy")
    parser.add_argument("--repeats", type=int, default=10)
    args = parser.parse_args()
    if args.repeats != 10:
        raise ValueError("production blueprint gate requires exactly 10 repeats")

    top_summary = json.loads(Path(args.top_summary).read_text(encoding="utf-8"))
    if not top_summary.get("passed_10_of_10_gate"):
        raise ValueError("top-entity 10/10 gate has not passed")
    decision = dict(top_summary["results"][0]["decision"])
    planner_result = {
        "class_local": decision["class_local"],
        "rationale": decision["rationale"],
        "evidence": decision["evidence"],
    }
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    hashes: list[str] = []
    plans: list[dict[str, Any]] = []
    for repeat in range(1, args.repeats + 1):
        context = build_domain_generation_context(
            domain_config_path=args.domain_config,
            output_root=output_dir / f"repeat_{repeat:02d}",
            repository_root=args.repository_root,
            write_files=False,
            planner=lambda _model, _prompt: dict(planner_result),
            operation_mode="legacy",
            derived_reuse_policy_path=args.reuse_policy,
        )
        plan = dict(context.iteration_blueprint)
        digest = hashlib.sha256(_canonical_bytes(plan)).hexdigest()
        hashes.append(digest)
        plans.append(plan)

    canonical_plan = plans[0]
    (output_dir / "iteration_blueprint.json").write_text(
        json.dumps(canonical_plan, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    summary = {
        "schema_version": "iteration-blueprint-derivation.v1",
        "requested_repeats": args.repeats,
        "valid_repeats": len(plans),
        "unique_canonical_hashes": sorted(set(hashes)),
        "passed_10_of_10_gate": len(plans) == 10 and len(set(hashes)) == 1,
        "top_entity": {
            "class_iri": decision["class_iri"],
            "class_local": decision["class_local"],
        },
    }
    (output_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0 if summary["passed_10_of_10_gate"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
