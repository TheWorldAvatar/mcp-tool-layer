from __future__ import annotations

import argparse
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable

from src.agents.scripts_and_prompts_generation.agentic_generation_runner import (
    _iteration_plan,
    generate_runtime_support_slice,
)
from src.agents.scripts_and_prompts_generation.domain_artifact_compiler import (
    build_domain_generation_context,
)
from src.agents.scripts_and_prompts_generation.domain_semantic_planner import (
    JsonPlanner,
)


PlannerFactory = Callable[[int], JsonPlanner | None]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _normalized_ownership(plan: dict[str, Any]) -> dict[str, dict[str, int]]:
    classes: dict[str, int] = {}
    properties: dict[str, int] = {}
    for iteration in plan.get("iterations") or []:
        number = int(iteration["iteration_number"])
        responsibilities = iteration.get("responsibilities") or {}
        classes.update(
            {
                str(local): number
                for local in responsibilities.get("classes") or []
            }
        )
        properties.update(
            {
                str(local): number
                for local in responsibilities.get("object_properties") or []
            }
        )
    return {
        "classes": dict(sorted(classes.items())),
        "object_properties": dict(sorted(properties.items())),
    }


def _run_once(
    *,
    index: int,
    output_root: Path,
    domain_config_path: Path,
    repository_root: Path,
    planner_factory: PlannerFactory | None,
) -> dict[str, Any]:
    run_root = output_root / f"run{index}"
    planner = planner_factory(index) if planner_factory is not None else None
    context = build_domain_generation_context(
        domain_config_path=domain_config_path,
        output_root=run_root,
        repository_root=repository_root,
        write_files=True,
        planner=planner,
    )
    runtime_plan = _iteration_plan(context)
    generate_runtime_support_slice(context, iterations=runtime_plan)
    ontology = context.ontology.name
    blueprint_path = (
        run_root / "derived_inputs" / ontology / "iteration_blueprint.json"
    )
    iterations_path = run_root / "iterations" / ontology / "iterations.json"
    accepted_path = (
        run_root
        / "semantic_planning"
        / ontology
        / "accepted_semantic_plan.json"
    )
    accepted = json.loads(accepted_path.read_text(encoding="utf-8"))
    return {
        "run": index,
        "root": str(run_root),
        "top_entity": (accepted.get("top_entity") or {}).get("class_local"),
        "ownership_sha256": (
            (accepted.get("assignments") or {}).get("ownership_sha256")
        ),
        "normalized_ownership": _normalized_ownership(runtime_plan),
        "iteration_blueprint_sha256": _sha256(blueprint_path),
        "iterations_sha256": _sha256(iterations_path),
    }


def run_stability_experiment(
    *,
    domain_config_path: str | Path,
    output_root: str | Path,
    repository_root: str | Path,
    runs: int = 5,
    workers: int | None = None,
    planner_factory: PlannerFactory | None = None,
) -> dict[str, Any]:
    """Run isolated planners concurrently and enforce byte-stable ownership output."""
    if runs < 2:
        raise ValueError("stability experiment requires at least two runs")
    root = Path(output_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    config_path = Path(domain_config_path).resolve()
    repo = Path(repository_root).resolve()
    records: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=workers or runs) as executor:
        futures = [
            executor.submit(
                _run_once,
                index=index,
                output_root=root,
                domain_config_path=config_path,
                repository_root=repo,
                planner_factory=planner_factory,
            )
            for index in range(1, runs + 1)
        ]
        for future in as_completed(futures):
            records.append(future.result())
    records.sort(key=lambda item: item["run"])

    compared_fields = (
        "top_entity",
        "ownership_sha256",
        "normalized_ownership",
        "iteration_blueprint_sha256",
        "iterations_sha256",
    )
    stable_fields = {
        field: len(
            {
                json.dumps(record[field], sort_keys=True, ensure_ascii=False)
                for record in records
            }
        )
        == 1
        for field in compared_fields
    }
    report = {
        "schema_version": "iteration-ownership-stability.v1",
        "runs": runs,
        "stable": all(stable_fields.values()),
        "stable_fields": stable_fields,
        "records": records,
    }
    (root / "stability_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return report


def publish_stable_iteration_artifacts(
    *,
    report: dict[str, Any],
    publish_root: str | Path,
    ontology_name: str,
) -> list[str]:
    """Publish only generated iteration artifacts after the stability gate passes."""
    if not report.get("stable"):
        raise ValueError("refusing to publish an unstable ownership experiment")
    records = report.get("records") or []
    if not records:
        raise ValueError("stability report has no generated run records")
    source_root = Path(str(records[0]["root"]))
    destination_root = Path(publish_root).resolve()
    relative_paths = [
        Path("derived_inputs") / ontology_name / "iteration_blueprint.json",
        Path("iterations") / ontology_name / "iterations.json",
    ]
    written: list[str] = []
    for relative in relative_paths:
        source = source_root / relative
        destination = destination_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(destination.suffix + ".tmp")
        temporary.write_bytes(source.read_bytes())
        temporary.replace(destination)
        written.append(str(destination))
    expected = {
        "iteration_blueprint.json": records[0]["iteration_blueprint_sha256"],
        "iterations.json": records[0]["iterations_sha256"],
    }
    for path in map(Path, written):
        if _sha256(path) != expected[path.name]:
            raise ValueError(f"published artifact digest mismatch: {path}")
    return written


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run isolated real top-entity planning and deterministic ownership."
    )
    parser.add_argument(
        "--domain-config",
        default="configs/domains/ontosynthesis.json",
    )
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--repository-root", default=".")
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--workers", type=int)
    parser.add_argument(
        "--publish-root",
        help="Publish only iteration_blueprint.json and iterations.json after a stable run.",
    )
    args = parser.parse_args()
    report = run_stability_experiment(
        domain_config_path=args.domain_config,
        output_root=args.output_root,
        repository_root=args.repository_root,
        runs=args.runs,
        workers=args.workers,
    )
    if args.publish_root and report["stable"]:
        config = json.loads(Path(args.domain_config).read_text(encoding="utf-8"))
        report["published_files"] = publish_stable_iteration_artifacts(
            report=report,
            publish_root=args.publish_root,
            ontology_name=str(config["ontology_name"]),
        )
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report["stable"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
