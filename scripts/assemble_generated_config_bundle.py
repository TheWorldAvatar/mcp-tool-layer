from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from src.agents.scripts_and_prompts_generation.domain_artifact_compiler import (
    build_domain_generation_context,
)
from src.agents.scripts_and_prompts_generation.materialization_operation_units import (
    compile_materialization_operation_units,
)


def _read(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON artifact must be an object: {path}")
    return payload


def _write(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Assemble validated config derivation artifacts."
    )
    parser.add_argument("--domain", required=True)
    parser.add_argument("--domain-config", required=True)
    parser.add_argument("--top-summary", required=True)
    parser.add_argument("--blueprint-summary", required=True)
    parser.add_argument("--blueprint", required=True)
    parser.add_argument("--reuse-summary", required=True)
    parser.add_argument("--reuse-policy", required=True)
    parser.add_argument("--operation-contract", required=True)
    parser.add_argument("--operation-summary", required=True)
    parser.add_argument("--operation-run", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--work-root", required=True)
    parser.add_argument("--repository-root", default=".")
    args = parser.parse_args()

    top_summary = _read(args.top_summary)
    blueprint_summary = _read(args.blueprint_summary)
    reuse_summary = _read(args.reuse_summary)
    operation_summary = _read(args.operation_summary)
    if not top_summary.get("passed_10_of_10_gate"):
        raise ValueError("top-entity gate has not passed")
    if not blueprint_summary.get("passed_10_of_10_gate"):
        raise ValueError("iteration-blueprint gate has not passed")
    if not operation_summary.get("passed_10_of_10_gate"):
        raise ValueError("operation-boundary gate has not passed")

    top_entity = dict(top_summary["results"][0]["decision"])
    context = build_domain_generation_context(
        domain_config_path=args.domain_config,
        output_root=args.work_root,
        repository_root=args.repository_root,
        write_files=False,
        operation_mode="legacy",
        derived_reuse_policy_path=args.reuse_policy,
        selected_top_entity=top_entity,
    )
    supplied_blueprint = _read(args.blueprint)
    if context.iteration_blueprint != supplied_blueprint:
        raise ValueError("recompiled blueprint differs from gated blueprint")

    operation_contract = _read(args.operation_contract)
    operation_run = _read(args.operation_run)
    candidates = operation_contract["materialization_operation_candidates"]
    decisions = operation_run["final_normalized"]
    context.contract["materialization_operation_candidates"] = candidates
    context.contract["materialization_operation_decisions"] = decisions
    units = compile_materialization_operation_units(
        parsed=context.parsed,
        contract=context.contract,
        iteration_plan=context.iteration_blueprint,
    )
    context.contract["materialization_operation_units"] = units

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    prefix = args.domain
    paths = {
        "orchestration_config": Path(args.domain_config).resolve(),
        "top_entity": output_dir / f"{prefix}_top_entity.json",
        "iteration_blueprint": output_dir / f"{prefix}_iteration_blueprint.json",
        "reuse_policy": output_dir / f"{prefix}_reuse_policy.json",
        "operation_candidates": output_dir / f"{prefix}_operation_candidates.json",
        "operation_decisions": output_dir / f"{prefix}_operation_decisions.json",
        "operation_units": output_dir / f"{prefix}_operation_units.json",
        "generation_contract": output_dir / f"{prefix}_generation_contract.json",
        "meta_task_adapter": output_dir / f"{prefix}_meta_task_adapter.json",
    }
    _write(paths["top_entity"], top_entity)
    _write(paths["iteration_blueprint"], supplied_blueprint)
    _write(paths["reuse_policy"], _read(args.reuse_policy))
    _write(paths["operation_candidates"], candidates)
    _write(paths["operation_decisions"], decisions)
    _write(paths["operation_units"], units)
    _write(paths["generation_contract"], context.contract)

    adapter_source = (
        Path(args.work_root)
        / "derived_inputs"
        / context.ontology.name
        / "meta_task_adapter.json"
    )
    adapter = _read(adapter_source)
    main = ((adapter.get("ontologies") or {}).get("main") or {})
    policies = main.get("runtime_policies") or {}
    plan_policy = policies.get("iteration_plan") or {}
    plan_policy["iterations_blueprint_path"] = paths[
        "iteration_blueprint"
    ].name
    plan_policy["provenance"] = "generated_tbox_derivation"
    policies["iteration_plan"] = plan_policy
    main["runtime_policies"] = policies
    _write(paths["meta_task_adapter"], adapter)

    manifest = {
        "schema_version": "generated-config-bundle.v1",
        "domain": args.domain,
        "semantic_authority": "tbox_bundle_only",
        "gates": {
            "top_entity_10_of_10": True,
            "iteration_blueprint_byte_stable_10_of_10": True,
            "reuse_10_of_10": bool(reuse_summary.get("passed_10_of_10_gate")),
            "reuse_runtime_mode": _read(args.reuse_policy)
            .get("derivation", {})
            .get("mode", "reviewed"),
            "operation_boundary_10_of_10": True,
        },
        "artifacts": {
            name: {
                "path": os.path.relpath(path, output_dir),
                "sha256": _sha(path),
            }
            for name, path in paths.items()
        },
    }
    manifest_path = output_dir / f"{prefix}_derivation_manifest.json"
    _write(manifest_path, manifest)
    print(json.dumps(manifest, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
