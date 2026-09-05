"""Dependency-ordered, repeated LLM generation experiments for one artifact layer."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

from src.agents.scripts_and_prompts_generation.agentic_generation_context import (
    AgenticGenerationContext,
    build_agentic_generation_context,
)
from src.agents.scripts_and_prompts_generation.agentic_generation_runner import (
    generate_deterministic_prompt_slice,
    generate_deterministic_script_slice,
)
from src.agents.scripts_and_prompts_generation.agentic_generation_validation import (
    build_validation_report,
)
from src.agents.scripts_and_prompts_generation.llm_artifact_editor import (
    EditBackend,
    run_llm_artifact_editor,
)
from src.agents.scripts_and_prompts_generation.pure_llm_generation import (
    _artifact_dependency_constraints,
    _editable_artifacts,
    _fixed_artifact_dependency_order,
    _generation_task,
    _run_stage_focused_repair,
)
from src.agents.scripts_and_prompts_generation.semantic_script_review import (
    review_generated_artifact_semantics_with_llm,
    review_mcp_semantics_with_llm,
)


def _relative(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _prepare_slots(
    context: AgenticGenerationContext,
    *,
    generate_scripts: bool,
    generate_prompts: bool,
) -> list[Path]:
    written: list[str] = []
    if generate_scripts:
        written.extend(generate_deterministic_script_slice(context))
    if generate_prompts:
        written.extend(generate_deterministic_prompt_slice(context))
    for raw_path in written:
        path = Path(raw_path)
        if path.is_file() and path.suffix in {".py", ".md"}:
            path.write_text("", encoding="utf-8")
    return _editable_artifacts(
        context,
        generate_scripts=generate_scripts,
        generate_prompts=generate_prompts,
    )


def _copy_frozen_prefix(
    *,
    experiment_root: Path,
    attempt_root: Path,
    completed_artifacts: list[str],
) -> None:
    frozen_root = experiment_root / "frozen"
    for relative in completed_artifacts:
        source = frozen_root / relative
        destination = attempt_root / relative
        if not source.is_file():
            raise FileNotFoundError(f"Frozen dependency is missing: {source}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _read_only_dependency_context(
    *, attempt_root: Path, completed_artifacts: list[str]
) -> str:
    if not completed_artifacts:
        return "There are no upstream generated dependencies for this first layer."
    sections = [
        "Read-only upstream dependency context follows. You may inspect and import these "
        "artifacts, but the edit protocol permits changing only the current target."
    ]
    for relative in completed_artifacts:
        path = attempt_root / relative
        sections.extend(
            [
                f"\n--- {relative} (sha256={_sha256(path)}) ---",
                path.read_text(encoding="utf-8", errors="replace"),
            ]
        )
    return "\n".join(sections)


def run_next_dependency_stage(
    *,
    ontology_name: str,
    meta_task_config_path: str | Path,
    experiment_root: str | Path,
    model_name: str,
    attempts: int = 3,
    max_repair_rounds: int = 3,
    generate_scripts: bool = True,
    generate_prompts: bool = False,
    edit_backend: EditBackend = "exact_edits",
    phase: str = "baseline",
) -> dict[str, Any]:
    """Generate one baseline candidate or run three independent stability trials."""
    if phase not in {"single", "baseline", "repair", "stability"}:
        raise ValueError("phase must be single, baseline, repair, or stability")
    if phase in {"single", "baseline"}:
        attempts = 1
    elif phase == "repair":
        attempts = 0
    elif attempts < 3:
        raise ValueError("Stability experiments require at least three attempts")
    root = Path(experiment_root)
    manifest_path = root / "dependency_stage_manifest.json"
    planning_root = root / "planning"
    planning_context = build_agentic_generation_context(
        ontology_name=ontology_name,
        meta_task_config_path=meta_task_config_path,
        output_root=planning_root,
        write_files=True,
    )
    planning_targets = _prepare_slots(
        planning_context,
        generate_scripts=generate_scripts,
        generate_prompts=generate_prompts,
    )
    planning_inventory = {
        _relative(path, planning_root): path for path in planning_targets
    }

    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        dependency_order = list(manifest.get("dependency_order") or [])
        if set(dependency_order) != set(planning_inventory):
            raise ValueError("Artifact inventory changed after dependency plan was frozen")
        positions = {
            artifact: index for index, artifact in enumerate(dependency_order)
        }
        for constraint in _artifact_dependency_constraints(
            [Path(relative) for relative in dependency_order]
        ):
            if positions[constraint["before"]] >= positions[constraint["after"]]:
                raise ValueError(
                    "Frozen dependency plan violates the current architecture contract: "
                    f"{constraint['before']} must precede {constraint['after']}"
                )
    else:
        dependency_order = _fixed_artifact_dependency_order(
            root=planning_root,
            targets=planning_targets,
        )
        manifest = {
            "schema_version": "dependency-stage-experiment.v1",
            "ontology": ontology_name,
            "model": model_name,
            "dependency_order": dependency_order,
            "completed_artifacts": [],
            "stages": [],
        }
        _write_json(manifest_path, manifest)

    completed = list(manifest.get("completed_artifacts") or [])
    if dependency_order[: len(completed)] != completed:
        raise ValueError("Frozen artifacts are not a prefix of the dependency plan")
    if len(completed) >= len(dependency_order):
        return {
            "ok": True,
            "complete": True,
            "manifest": str(manifest_path),
            "completed_artifacts": completed,
        }

    stage_index = len(completed)
    current_relative = dependency_order[stage_index]
    pending_baseline = manifest.get("pending_baseline")
    if phase in {"single", "baseline"} and pending_baseline:
        raise ValueError(
            "A baseline candidate is already awaiting direct review; "
            "run the stability phase or remove it explicitly"
        )
    if phase == "stability":
        if not isinstance(pending_baseline, dict):
            raise ValueError("Stability phase requires a directly reviewed baseline candidate")
        if pending_baseline.get("artifact") != current_relative:
            raise ValueError("Pending baseline does not match the current dependency layer")
    if phase == "repair":
        if not isinstance(pending_baseline, dict):
            raise ValueError("Repair phase requires a baseline candidate")
        candidate_root = Path(str(pending_baseline["candidate_path"])).parents[2]
        context = build_agentic_generation_context(
            ontology_name=ontology_name,
            meta_task_config_path=meta_task_config_path,
            output_root=candidate_root,
            write_files=False,
        )
        current_target = Path(str(pending_baseline["candidate_path"]))
        active_artifacts = dependency_order[: stage_index + 1]
        stage_report = build_validation_report(
            context,
            write_report=True,
            prompts_required=False,
            active_artifacts=active_artifacts,
        )
        repairs: list[dict[str, Any]] = []
        for _ in range(max_repair_rounds):
            if stage_report.get("stage_ok"):
                break
            stage_report, repair = _run_stage_focused_repair(
                model_name=model_name,
                context=context,
                targets=[current_target],
                report=stage_report,
                foreign_contracts=None,
                active_artifacts=active_artifacts,
                max_focus_targets=1,
                edit_backend=edit_backend,
            )
            repairs.append(repair)
            if not repair.get("accepted"):
                break
        semantic_reviews: list[dict[str, Any]] = []
        if stage_report.get("stage_ok"):
            semantic_review = review_generated_artifact_semantics_with_llm(
                context=context,
                artifact_path=current_target,
                model_name=model_name,
            )
            semantic_reviews.append(semantic_review)
            for _ in range(max_repair_rounds):
                if semantic_review.get("decision") == "pass":
                    break
                semantic_report = build_validation_report(
                    context,
                    write_report=True,
                    prompts_required=False,
                    active_artifacts=active_artifacts,
                    extra_failures=[
                        "LLM artifact semantic review requires repair:\n"
                        + json.dumps(semantic_review, ensure_ascii=False)
                    ],
                )
                stage_report, repair = _run_stage_focused_repair(
                    model_name=model_name,
                    context=context,
                    targets=[current_target],
                    report=semantic_report,
                    foreign_contracts=None,
                    active_artifacts=active_artifacts,
                    max_focus_targets=1,
                    edit_backend=edit_backend,
                )
                repairs.append(repair)
                if not repair.get("accepted"):
                    break
                semantic_review = review_generated_artifact_semantics_with_llm(
                    context=context,
                    artifact_path=current_target,
                    model_name=model_name,
                )
                semantic_reviews.append(semantic_review)
        semantic_passed = bool(
            semantic_reviews
            and semantic_reviews[-1].get("decision") == "pass"
        )
        passed = bool(stage_report.get("stage_ok") and semantic_passed)
        repair_report = {
            "artifact": current_relative,
            "phase": "repair",
            "passed": passed,
            "repairs": repairs,
            "soft_semantic_reviews": semantic_reviews,
            "stage_validation": stage_report,
            "artifact_sha256": _sha256(current_target),
        }
        _write_json(candidate_root / "baseline_repair_report.json", repair_report)
        if passed:
            pending_baseline["artifact_sha256"] = _sha256(current_target)
            pending_baseline["direct_review_required"] = True
            manifest["pending_baseline"] = pending_baseline
            _write_json(manifest_path, manifest)
        return {
            "ok": passed,
            "awaiting_direct_review": passed,
            "artifact": current_relative,
            "candidate_path": str(current_target),
            "repair": repair_report,
            "manifest": str(manifest_path),
        }
    stage_root = root / "stages" / f"{stage_index + 1:02d}" / phase
    attempt_reports: list[dict[str, Any]] = []
    passing_attempts: list[tuple[Path, dict[str, Any]]] = []

    for attempt_index in range(1, attempts + 1):
        attempt_root = stage_root / f"attempt_{attempt_index:02d}"
        if attempt_root.exists():
            shutil.rmtree(attempt_root)
        context = build_agentic_generation_context(
            ontology_name=ontology_name,
            meta_task_config_path=meta_task_config_path,
            output_root=attempt_root,
            write_files=True,
        )
        targets = _prepare_slots(
            context,
            generate_scripts=generate_scripts,
            generate_prompts=generate_prompts,
        )
        by_relative = {_relative(path, attempt_root): path for path in targets}
        _copy_frozen_prefix(
            experiment_root=root,
            attempt_root=attempt_root,
            completed_artifacts=completed,
        )
        current_target = by_relative[current_relative]
        active_artifacts = dependency_order[: stage_index + 1]
        initial_report = build_validation_report(
            context,
            write_report=True,
            prompts_required=False,
            active_artifacts=active_artifacts,
        )
        dependency_context = _read_only_dependency_context(
            attempt_root=attempt_root,
            completed_artifacts=completed,
        )
        generation_prompt = (
            _generation_task(
                context=context,
                report=initial_report,
                round_index=1,
                generate_scripts=current_target.suffix == ".py",
                generate_prompts=current_target.suffix == ".md",
                target=current_target,
            )
            + "\n\n"
            + dependency_context
        )
        patch = run_llm_artifact_editor(
            model_name=model_name,
            output_root=attempt_root,
            targets=[current_target],
            task_prompt=generation_prompt,
            max_attempts=5,
            edit_backend=edit_backend,
            progress=lambda message, n=attempt_index: print(
                f"[dependency-stage attempt {n}/{attempts}] {message}", flush=True
            ),
        )
        stage_report = build_validation_report(
            context,
            write_report=True,
            prompts_required=False,
            active_artifacts=active_artifacts,
            extra_failures=list(patch.get("failures") or []),
        )
        repairs: list[dict[str, Any]] = []
        if patch.get("ok") and not stage_report.get("stage_ok"):
            for _ in range(max_repair_rounds):
                stage_report, repair = _run_stage_focused_repair(
                    model_name=model_name,
                    context=context,
                    targets=[current_target],
                    report=stage_report,
                    foreign_contracts=None,
                    active_artifacts=active_artifacts,
                    max_focus_targets=1,
                    edit_backend=edit_backend,
                )
                repairs.append(repair)
                if stage_report.get("stage_ok") or not repair.get("accepted"):
                    break
        semantic_reviews: list[dict[str, Any]] = []
        if patch.get("ok") and stage_report.get("stage_ok"):
            artifact_semantic_review = (
                review_generated_artifact_semantics_with_llm(
                    context=context,
                    artifact_path=current_target,
                    model_name=model_name,
                )
            )
            semantic_reviews.append(artifact_semantic_review)
            if artifact_semantic_review.get("decision") == "repair":
                semantic_report = build_validation_report(
                    context,
                    write_report=True,
                    prompts_required=False,
                    active_artifacts=active_artifacts,
                    extra_failures=[
                        "LLM artifact semantic review requires repair:\n"
                        + json.dumps(artifact_semantic_review, ensure_ascii=False)
                    ],
                )
                for _ in range(max_repair_rounds):
                    stage_report, repair = _run_stage_focused_repair(
                        model_name=model_name,
                        context=context,
                        targets=[current_target],
                        report=semantic_report,
                        foreign_contracts=None,
                        active_artifacts=active_artifacts,
                        max_focus_targets=1,
                        edit_backend=edit_backend,
                    )
                    repairs.append(repair)
                    if not repair.get("accepted"):
                        break
                    artifact_semantic_review = (
                        review_generated_artifact_semantics_with_llm(
                            context=context,
                            artifact_path=current_target,
                            model_name=model_name,
                        )
                    )
                    semantic_reviews.append(artifact_semantic_review)
                    if (
                        stage_report.get("stage_ok")
                        and artifact_semantic_review.get("decision") == "pass"
                    ):
                        break
                    semantic_report = build_validation_report(
                        context,
                        write_report=True,
                        prompts_required=False,
                        active_artifacts=active_artifacts,
                        extra_failures=[
                            "LLM artifact semantic review requires repair:\n"
                            + json.dumps(
                                artifact_semantic_review,
                                ensure_ascii=False,
                            )
                        ],
                    )
        if (
            patch.get("ok")
            and stage_report.get("stage_ok")
            and current_target.name == "main.py"
            and semantic_reviews[-1].get("decision") == "pass"
        ):
            for _ in range(max_repair_rounds + 1):
                semantic_review = review_mcp_semantics_with_llm(
                    context=context,
                    model_name=model_name,
                )
                semantic_reviews.append(semantic_review)
                if semantic_review.get("decision") == "pass":
                    break
                semantic_report = build_validation_report(
                    context,
                    write_report=True,
                    prompts_required=False,
                    active_artifacts=active_artifacts,
                    extra_failures=[
                        "LLM soft semantic review requires repair:\n"
                        + json.dumps(semantic_review, ensure_ascii=False)
                    ],
                )
                stage_report, repair = _run_stage_focused_repair(
                    model_name=model_name,
                    context=context,
                    targets=[current_target],
                    report=semantic_report,
                    foreign_contracts=None,
                    active_artifacts=active_artifacts,
                    max_focus_targets=1,
                    edit_backend=edit_backend,
                )
                repairs.append(repair)
                if not repair.get("accepted"):
                    break
        semantic_passed = (
            semantic_reviews
            and all(review.get("decision") == "pass" for review in semantic_reviews)
        )
        passed = bool(
            patch.get("ok") and stage_report.get("stage_ok") and semantic_passed
        )
        attempt_report = {
            "attempt": attempt_index,
            "artifact": current_relative,
            "passed": passed,
            "patch": patch,
            "repairs": repairs,
            "soft_semantic_reviews": semantic_reviews,
            "stage_validation": stage_report,
            "artifact_sha256": _sha256(current_target) if current_target.is_file() else "",
        }
        _write_json(attempt_root / "dependency_stage_attempt.json", attempt_report)
        attempt_reports.append(attempt_report)
        if passed:
            passing_attempts.append((current_target, attempt_report))

    selected = passing_attempts[0] if passing_attempts else None
    stage_summary = {
        "stage_index": stage_index,
        "artifact": current_relative,
        "phase": phase,
        "attempt_count": attempts,
        "passing_attempt_count": len(passing_attempts),
        "attempts": [
            {
                "attempt": item["attempt"],
                "passed": item["passed"],
                "artifact_sha256": item["artifact_sha256"],
                "repair_count": len(item["repairs"]),
            }
            for item in attempt_reports
        ],
    }
    if selected is None or (phase == "stability" and len(passing_attempts) != attempts):
        stage_summary["selected_attempt"] = None
        manifest["stages"].append(stage_summary)
        _write_json(manifest_path, manifest)
        return {
            "ok": False,
            "complete": False,
            "artifact": current_relative,
            "stage": stage_summary,
            "manifest": str(manifest_path),
        }

    selected_path, selected_report = selected
    if phase == "baseline":
        manifest["pending_baseline"] = {
            "artifact": current_relative,
            "candidate_path": str(selected_path),
            "artifact_sha256": selected_report["artifact_sha256"],
            "direct_review_required": True,
        }
        manifest.setdefault("baseline_trials", []).append(stage_summary)
        _write_json(manifest_path, manifest)
        return {
            "ok": True,
            "complete": False,
            "awaiting_direct_review": True,
            "artifact": current_relative,
            "candidate_path": str(selected_path),
            "stage": stage_summary,
            "manifest": str(manifest_path),
        }

    if phase == "single":
        baseline_path = selected_path
    else:
        baseline_path = Path(str(pending_baseline["candidate_path"]))
        if not baseline_path.is_file():
            raise FileNotFoundError(f"Reviewed baseline candidate is missing: {baseline_path}")
        if _sha256(baseline_path) != pending_baseline.get("artifact_sha256"):
            raise ValueError("Reviewed baseline candidate changed before stability testing")
    frozen_path = root / "frozen" / current_relative
    frozen_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(baseline_path, frozen_path)
    init_path = frozen_path.parent / "__init__.py"
    if not init_path.exists():
        init_path.write_text(
            '"""Frozen generated ontology package boundary."""\n',
            encoding="utf-8",
        )
    baseline_package_dir = baseline_path.parent
    frozen_package_dir = frozen_path.parent
    for infrastructure_name in (
        "__init__.py",
        "_fixed_rdf_runtime.py",
        "_fixed_om2_runtime.py",
        "_reuse_pair_judge.py",
        "_relationship_contract.json",
    ):
        infrastructure_source = baseline_package_dir / infrastructure_name
        if infrastructure_source.is_file():
            shutil.copy2(
                infrastructure_source,
                frozen_package_dir / infrastructure_name,
            )
    stage_summary["selected_attempt"] = (
        "single_accepted_candidate" if phase == "single" else "reviewed_baseline"
    )
    stage_summary["frozen_sha256"] = _sha256(frozen_path)
    manifest["completed_artifacts"] = [*completed, current_relative]
    manifest["stages"].append(stage_summary)
    manifest["pending_baseline"] = None
    _write_json(manifest_path, manifest)
    return {
        "ok": True,
        "complete": len(manifest["completed_artifacts"]) == len(dependency_order),
        "artifact": current_relative,
        "stage": stage_summary,
        "manifest": str(manifest_path),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run repeated LLM generation/repair for the next dependency layer."
    )
    parser.add_argument("--ontology", required=True)
    parser.add_argument("--meta-task-config", required=True)
    parser.add_argument("--experiment-root", required=True)
    parser.add_argument("--model", default="gpt-5")
    parser.add_argument("--attempts", type=int, default=3)
    parser.add_argument(
        "--phase",
        choices=["single", "baseline", "repair", "stability"],
        default="single",
    )
    parser.add_argument("--max-repair-rounds", type=int, default=3)
    parser.add_argument(
        "--edit-backend",
        choices=["exact_edits", "unified_diff"],
        default="exact_edits",
    )
    parser.add_argument("--include-prompts", action="store_true")
    args = parser.parse_args()
    report = run_next_dependency_stage(
        ontology_name=args.ontology,
        meta_task_config_path=args.meta_task_config,
        experiment_root=args.experiment_root,
        model_name=args.model,
        attempts=args.attempts,
        max_repair_rounds=args.max_repair_rounds,
        generate_scripts=True,
        generate_prompts=args.include_prompts,
        edit_backend=args.edit_backend,
        phase=args.phase,
    )
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
