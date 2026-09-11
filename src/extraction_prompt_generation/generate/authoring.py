"""Shared GPT-5 exact-edits runner for one generation track at a time.

`run_pure_llm_generation_rounds` is the loop. Prompt-only callers set
`generate_prompts=True`. See generate/README.md.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from models.locked_llm import LOCKED_GENERATION_MODEL
from src.extraction_prompt_generation.compile.context import (
    AgenticGenerationContext,
)
from src.extraction_prompt_generation.generate.artifact_state import (
    ArtifactStateStore,
)
from src.extraction_prompt_generation.generate.authoring_parallel import (
    _generate_artifact_wave,
    _parallel_generation_wave,
    _publish_isolated_candidate,
)
from src.extraction_prompt_generation.generate.authoring_progress import (
    _progress_paths,
    _progress_summary,
    _progress_validation,
)
from src.extraction_prompt_generation.generate.authoring_task import (
    _generation_task,
)
from src.extraction_prompt_generation.generate.extraction_prompts.contracts import (
    _detach_deterministic_tbox_from_pre_prompt,
    _detach_nested_owned_scalar_from_prompt,
    _repair_generated_prompt_semantics,
    _validate_generated_prompt,
    _validate_generated_prompt_hard_gates,
    _write_materializable_prompt_component,
)
from src.extraction_prompt_generation.llm.artifact_editor import (
    EditBackend,
    run_llm_artifact_editor,
)
from src.extraction_prompt_generation.llm.invocation_journal import (
    configure_llm_invocation_journal,
)
from src.extraction_prompt_generation.validate.report import (
    build_validation_report,
)

run_llm_unified_diff_editor = run_llm_artifact_editor


def _editable_artifacts(context: AgenticGenerationContext) -> list[Path]:
    """Return extraction-prompt markdown only. KG-building files stay out."""
    return [
        path
        for path in sorted(Path(context.prompts_dir).glob("*.md"))
        if not path.name.startswith("KG_BUILDING_")
    ]


def _fixed_artifact_dependency_order(
    *,
    root: Path,
    targets: list[Path],
) -> list[str]:
    """Apply the pipeline's fixed architecture order without semantic planning."""

    def order_key(path: Path) -> tuple[int, int, str]:
        name = path.name
        match = re.fullmatch(r"EXTRACTION_ITER_(\d+)\.md", name)
        if match:
            return (5, int(match.group(1)), name)
        match = re.fullmatch(r"PRE_EXTRACTION_ITER_(\d+)\.md", name)
        if match:
            return (5, int(match.group(1)), name)
        return (8, 0, path.as_posix())

    return [
        path.resolve().relative_to(root.resolve()).as_posix()
        for path in sorted(targets, key=order_key)
    ]


def run_pure_llm_generation_rounds(
    context: AgenticGenerationContext,
    *,
    model_name: str = LOCKED_GENERATION_MODEL,
    foreign_contracts: list[dict[str, Any]] | None = None,
    generate_prompts: bool = True,
    parallel_generation: bool = True,
    max_generation_workers: int = 5,
    edit_backend: EditBackend = "exact_edits",
    target_artifacts: list[str] | None = None,
    protected_artifacts: dict[Path, bytes] | None = None,
) -> dict[str, Any]:
    """Author one track of empty slots with GPT-5 exact-edits."""
    model_name = LOCKED_GENERATION_MODEL
    configure_llm_invocation_journal(context.output_root)
    if max_generation_workers < 1:
        raise ValueError("max_generation_workers must be at least 1")
    if not generate_prompts:
        raise ValueError("set generate_prompts")

    all_editable_targets = _editable_artifacts(context)
    targets = list(all_editable_targets)
    protected_target_snapshots: dict[Path, bytes] = dict(protected_artifacts or {})
    if target_artifacts:
        requested = {str(item).replace("\\", "/") for item in target_artifacts}
        root = Path(context.output_root).resolve()
        selected = [
            path
            for path in targets
            if path.name in requested
            or path.resolve().relative_to(root).as_posix() in requested
        ]
        matched = {
            requested_name
            for requested_name in requested
            if any(
                path.name == requested_name
                or path.resolve().relative_to(root).as_posix() == requested_name
                for path in selected
            )
        }
        unknown = sorted(requested - matched)
        if unknown:
            return {
                "mode": "pure_llm_targeted",
                "model": model_name,
                "ok": False,
                "failures": ["unknown_target_artifacts: " + ", ".join(unknown)],
                "history": [],
            }
        targets = selected
        selected_resolved = {path.resolve() for path in selected}
        protected_target_snapshots.update(
            {
                path: path.read_bytes()
                for path in all_editable_targets
                if path.resolve() not in selected_resolved and path.is_file()
            }
        )

    artifact_states = ArtifactStateStore(context.output_root, context.ontology.name)
    artifact_states.initialize(targets)
    artifact_states.recover_interrupted()
    resumed_passed_targets = [
        path for path in targets if artifact_states.is_matching_passed(path)
    ]
    if resumed_passed_targets:
        resumed_set = {path.resolve() for path in resumed_passed_targets}
        protected_target_snapshots.update(
            {path: path.read_bytes() for path in resumed_passed_targets}
        )
        targets = [path for path in targets if path.resolve() not in resumed_set]
        print(
            "[pure_llm] phase=checkpoint-resume action=skip-passed "
            f"targets={_progress_paths(resumed_passed_targets)}",
            flush=True,
        )

    for prompt_target in targets:
        if prompt_target.suffix == ".md":
            _write_materializable_prompt_component(context, prompt_target)
            _detach_deterministic_tbox_from_pre_prompt(prompt_target)
            _detach_nested_owned_scalar_from_prompt(prompt_target)

    def _restore_non_target_artifacts() -> None:
        for path, content in protected_target_snapshots.items():
            if not path.is_file() or path.read_bytes() != content:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)

    if not targets and resumed_passed_targets:
        report = build_validation_report(
            context,
            foreign_contracts=foreign_contracts,
            write_report=True,
            prompts_required=generate_prompts,
            include_prompt_checks=generate_prompts,
        )
        return {
            "mode": "pure_llm_checkpoint_resume",
            "model": model_name,
            "ok": bool(report.get("ok")),
            "failures": list(report.get("failures") or []),
            "history": [],
            "resumed_passed": [str(path) for path in resumed_passed_targets],
            "final_report": report,
            "checkpoint_preserved": True,
        }
    if not targets:
        return {
            "mode": "pure_llm_generation",
            "model": model_name,
            "ok": False,
            "failures": ["no_editable_generation_targets"],
            "history": [],
        }

    generation_snapshots = {path: path.read_bytes() for path in targets}
    history: list[dict[str, Any]] = []
    targeted_active_artifacts = (
        [
            path.resolve()
            .relative_to(Path(context.output_root).resolve())
            .as_posix()
            for path in targets
        ]
        if target_artifacts
        else None
    )
    report = build_validation_report(
        context,
        foreign_contracts=foreign_contracts,
        write_report=True,
        prompts_required=generate_prompts,
        include_prompt_checks=generate_prompts,
        active_artifacts=targeted_active_artifacts,
    )
    initial_files: list[dict[str, Any]] = []

    def _append_artifact_record(record: dict[str, Any]) -> None:
        target_path = Path(record["target"])
        patch_ok = bool((record.get("patch") or {}).get("ok"))
        stage_clean = bool(record.get("stage_clean", patch_ok))
        validation = record.get("stage_validation")
        artifact_states.transition(
            target_path,
            "passed" if patch_ok and stage_clean else "failed",
            reason=None if patch_ok and stage_clean else "artifact_generation_failed",
            validation=validation if isinstance(validation, dict) else None,
        )
        initial_files.append(record)

    artifact_dependency_order = _fixed_artifact_dependency_order(
        root=Path(context.output_root),
        targets=targets,
    )
    target_by_relative = {
        path.resolve().relative_to(Path(context.output_root).resolve()).as_posix(): path
        for path in targets
    }
    generation_targets = [
        target_by_relative[relative] for relative in artifact_dependency_order
    ]
    parallel_patches: dict[Path, dict[str, Any]] = {}
    for target_index, target in enumerate(generation_targets, start=1):
        artifact_states.transition(target, "generating")
        print(
            f"[pure_llm] artifact={target_index}/{len(generation_targets)} "
            f"phase=generate target={target.name} "
            f'objective="{_progress_summary("Generate the artifact against its ontology contract")}"',
            flush=True,
        )
        queued_parallel_patch = parallel_patches.pop(target, None)
        if queued_parallel_patch is not None:
            patch_report = _publish_isolated_candidate(target, queued_parallel_patch)
        elif parallel_generation:
            wave = [
                candidate
                for candidate in _parallel_generation_wave(target, generation_targets)
                if candidate == target
                or (
                    not candidate.read_text(encoding="utf-8", errors="replace").strip()
                    and candidate not in parallel_patches
                )
            ]
            if len(wave) > 1:
                print(
                    f"[pure_llm] phase=parallel-generation workers="
                    f"{min(max_generation_workers, len(wave))} "
                    f"targets={','.join(item.name for item in wave)}",
                    flush=True,
                )
                parallel_patches.update(
                    _generate_artifact_wave(
                        context=context,
                        report=report,
                        targets=wave,
                        model_name=model_name,
                        edit_backend=edit_backend,
                        max_workers=max_generation_workers,
                    )
                )
                queued = parallel_patches.pop(target, None)
                if queued is None:
                    patch_report = run_llm_unified_diff_editor(
                        model_name=model_name,
                        output_root=Path(context.output_root),
                        targets=[target],
                        task_prompt=_generation_task(
                            context=context,
                            report=report,
                            round_index=1,
                            generate_prompts=True,
                            target=target,
                        ),
                        max_attempts=10,
                        validate=(
                            (lambda target=target: _validate_generated_prompt_hard_gates(target, context))
                            if target.suffix == ".md"
                            else None
                        ),
                        progress=lambda message: print(f"[pure_llm] {message}", flush=True),
                        edit_backend=edit_backend,
                    )
                else:
                    patch_report = _publish_isolated_candidate(target, queued)
            else:
                patch_report = run_llm_unified_diff_editor(
                    model_name=model_name,
                    output_root=Path(context.output_root),
                    targets=[target],
                    task_prompt=_generation_task(
                        context=context,
                        report=report,
                        round_index=1,
                        generate_prompts=True,
                        target=target,
                    ),
                    max_attempts=10,
                    validate=(
                        (lambda target=target: _validate_generated_prompt_hard_gates(target, context))
                        if target.suffix == ".md"
                        else None
                    ),
                    progress=lambda message: print(f"[pure_llm] {message}", flush=True),
                    edit_backend=edit_backend,
                )
        else:
            patch_report = run_llm_unified_diff_editor(
                model_name=model_name,
                output_root=Path(context.output_root),
                targets=[target],
                task_prompt=_generation_task(
                    context=context,
                    report=report,
                    round_index=1,
                    generate_prompts=True,
                    target=target,
                ),
                max_attempts=10,
                validate=(
                    (lambda target=target: _validate_generated_prompt_hard_gates(target, context))
                    if target.suffix == ".md"
                    else None
                ),
                progress=lambda message: print(f"[pure_llm] {message}", flush=True),
                edit_backend=edit_backend,
            )
        artifact_record: dict[str, Any] = {
            "target": str(target),
            "patch": patch_report,
        }
        if not patch_report.get("ok"):
            _append_artifact_record(artifact_record)
            break
        _restore_non_target_artifacts()
        if target.suffix == ".md":
            _write_materializable_prompt_component(context, target)
            artifact_states.transition(target, "validating")
            print(
                f"[pure_llm] artifact={target_index}/{len(generation_targets)} "
                "phase=artifact-review scope=prompt-hard-gates action=start",
                flush=True,
            )
            stage_report = _validate_generated_prompt(
                model_name=model_name,
                context=context,
                target=target,
                foreign_contracts=foreign_contracts,
            )
            artifact_record["hard_gate_validation"] = patch_report.get("validation", {})
            artifact_record["semantic_validation"] = stage_report
            artifact_record["stage_clean"] = bool(stage_report.get("stage_ok"))
            print(
                f"[pure_llm] artifact={target_index}/{len(generation_targets)} "
                "phase=artifact-review scope=prompt-semantic "
                + _progress_validation(stage_report),
                flush=True,
            )
            semantic_repairs: list[dict[str, Any]] = []
            if not stage_report.get("stage_ok"):
                artifact_states.transition(target, "repairing")
                print(
                    f"[pure_llm] artifact={target_index}/{len(generation_targets)} "
                    "phase=repair scope=prompt-semantic action=start",
                    flush=True,
                )
                stage_report, semantic_patch = _repair_generated_prompt_semantics(
                    model_name=model_name,
                    context=context,
                    target=target,
                    foreign_contracts=foreign_contracts,
                    report=stage_report,
                    edit_backend=edit_backend,
                )
                semantic_repairs.append(semantic_patch)
                _write_materializable_prompt_component(context, target)
            artifact_record["semantic_repairs"] = semantic_repairs
            artifact_record["stage_validation"] = stage_report
            artifact_record["stage_clean"] = bool(stage_report.get("stage_ok"))
            print(
                f"[pure_llm] artifact={target_index}/{len(generation_targets)} "
                "phase=artifact-review scope=prompt-final "
                + _progress_validation(stage_report),
                flush=True,
            )
            _restore_non_target_artifacts()
            if not artifact_record["stage_clean"]:
                _append_artifact_record(artifact_record)
                break
            protected_target_snapshots[target] = target.read_bytes()
        _append_artifact_record(artifact_record)

    report = build_validation_report(
        context,
        foreign_contracts=foreign_contracts,
        write_report=True,
        prompts_required=generate_prompts,
        include_prompt_checks=generate_prompts,
        extra_failures=[
            failure
            for item in initial_files
            for failure in (
                (item["patch"].get("failures") or [])
                + (
                    (item.get("stage_validation") or {}).get("failures") or []
                    if not item.get("stage_clean", True)
                    else []
                )
            )
        ],
    )
    history.append(
        {
            "round": 1,
            "mode": "per_file_initial_generation",
            "files": initial_files,
            "validation": report,
        }
    )
    generation_ok = bool(initial_files) and all(
        bool(item.get("stage_clean", True)) and bool((item.get("patch") or {}).get("ok"))
        for item in initial_files
    )
    result = {
        "mode": "pure_llm_generation",
        "model": model_name,
        "ok": bool(report.get("ok")) and generation_ok,
        "final_report": report,
        "history": history,
        "generation_complete": generation_ok,
        "checkpoint_preserved": True,
    }
    targeted_checkpoint_clean = bool(target_artifacts) and generation_ok
    if result["ok"]:
        _restore_non_target_artifacts()
    if not result["ok"] and not targeted_checkpoint_clean:
        passed_targets = {
            Path(item["target"]).resolve()
            for item in initial_files
            if item.get("stage_clean") and (item.get("patch") or {}).get("ok")
        }
        passed_targets.update(path.resolve() for path in resumed_passed_targets)
        rolled_any = False
        for path, content in generation_snapshots.items():
            if path.resolve() in passed_targets:
                continue
            path.write_bytes(content)
            rolled_any = True
        if rolled_any:
            result["rolled_back"] = True
    return result
