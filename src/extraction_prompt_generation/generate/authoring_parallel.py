"""Isolated ProcessPool wave generation for authoring candidates.

Independent empty `.md` slots may be authored in one wave. Each worker
writes a candidate; the parent publishes it. See generate/README.md.
"""

from __future__ import annotations

import json
import multiprocessing
import os
import shutil
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
import tempfile
from typing import Any

from src.extraction_prompt_generation.compile.context import (
    AgenticGenerationContext,
)
from src.extraction_prompt_generation.generate.authoring_task import (
    _generation_task,
)
from src.extraction_prompt_generation.generate.extraction_prompts.contracts import (
    _detach_deterministic_tbox_from_pre_prompt,
    _detach_nested_owned_scalar_from_prompt,
    _validate_generated_prompt_hard_gates,
    _write_materializable_prompt_component,
)
from src.extraction_prompt_generation.llm.artifact_editor import (
    EditBackend,
    run_llm_artifact_editor,
)

run_llm_unified_diff_editor = run_llm_artifact_editor


def _parallel_generation_wave(
    target: Path,
    generation_targets: list[Path],
) -> list[Path]:
    """Return independent targets that may be authored in the same DAG wave."""
    if target.suffix == ".md":
        return [
            candidate for candidate in generation_targets if candidate.suffix == ".md"
        ]
    return [target]


def _isolated_worker_context(
    context: AgenticGenerationContext,
    worker_root: Path,
) -> AgenticGenerationContext:
    """Remap output paths while preserving ontology and semantic inputs."""
    source_root = Path(context.output_root).resolve()

    def remap(raw_path: str) -> str:
        relative = Path(raw_path).resolve().relative_to(source_root)
        return str(worker_root / relative)

    return replace(
        context,
        output_root=str(worker_root),
        ontology_structure_dir=remap(context.ontology_structure_dir),
        scripts_dir=remap(context.scripts_dir),
        prompts_dir=remap(context.prompts_dir),
        parsed_summary_path=remap(context.parsed_summary_path),
        parsed_markdown_path=remap(context.parsed_markdown_path),
        contract_path=remap(context.contract_path),
        integrity_profile_path=remap(context.integrity_profile_path),
        report_path=remap(context.report_path),
        config_provenance_path=remap(context.config_provenance_path),
    )


def _generate_isolated_artifact_worker(
    payload: tuple[
        AgenticGenerationContext,
        dict[str, Any],
        str,
        str,
        EditBackend,
    ],
) -> tuple[str, dict[str, Any], bytes]:
    """Generate one candidate inside a process-local copied output tree."""
    context, report, relative, model_name, edit_backend = payload
    target = Path(context.output_root) / relative
    if target.suffix == ".md":
        _write_materializable_prompt_component(context, target)
        _detach_deterministic_tbox_from_pre_prompt(target)
        _detach_nested_owned_scalar_from_prompt(target)
    patch = run_llm_unified_diff_editor(
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
            (lambda: _validate_generated_prompt_hard_gates(target, context))
            if target.suffix == ".md"
            else None
        ),
        edit_backend=edit_backend,
    )
    if patch.get("ok") and target.suffix == ".md":
        _write_materializable_prompt_component(context, target)
    candidate = target.read_bytes() if patch.get("ok") else b""
    return relative, patch, candidate


def _persist_parallel_candidate_attempts(
    *,
    output_root: Path,
    ontology_name: str,
    artifact: str,
    patch: dict[str, Any],
) -> None:
    """Persist worker attempt validation before its temporary tree is deleted."""
    attempts = list(patch.get("attempts") or [])
    if not attempts:
        attempts = [
            {
                "attempt": None,
                "ok": bool(patch.get("ok")),
                "failures": list(patch.get("failures") or []),
                "validation": patch.get("validation") or {},
                "elapsed_seconds": patch.get("elapsed_seconds"),
                "token_usage": patch.get("token_usage") or {},
            }
        ]
    log_path = (
        output_root
        / "reports"
        / ontology_name
        / "parallel_candidate_attempts.jsonl"
    )
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8", newline="\n") as stream:
        for attempt in attempts:
            validation = attempt.get("validation") or {}
            record = {
                "schema_version": 1,
                "recorded_at": datetime.now(timezone.utc).isoformat(),
                "ontology": ontology_name,
                "artifact": artifact,
                "attempt": attempt.get("attempt"),
                "ok": bool(attempt.get("ok")),
                "failures": list(attempt.get("failures") or []),
                "validation_failures": list(validation.get("failures") or []),
                "validation": validation,
                "rollback_performed": bool(attempt.get("rollback_performed")),
                "changed_files": list(attempt.get("changed_files") or []),
                "elapsed_seconds": attempt.get("elapsed_seconds"),
                "token_usage": attempt.get("token_usage") or {},
            }
            stream.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def _generate_artifact_wave(
    *,
    context: AgenticGenerationContext,
    report: dict[str, Any],
    targets: list[Path],
    model_name: str,
    edit_backend: EditBackend,
    max_workers: int,
) -> dict[Path, dict[str, Any]]:
    """Author candidates in isolation without publishing them to the shared tree."""
    root = Path(context.output_root).resolve()
    target_by_relative = {
        target.resolve().relative_to(root).as_posix(): target for target in targets
    }
    workspaces: list[tempfile.TemporaryDirectory[str]] = []
    payloads = []
    for relative in target_by_relative:
        workspace = tempfile.TemporaryDirectory(prefix="artifact_candidate_")
        workspaces.append(workspace)
        worker_root = Path(workspace.name) / "output"
        shutil.copytree(root, worker_root)
        worker_context = _isolated_worker_context(context, worker_root)
        payloads.append((worker_context, report, relative, model_name, edit_backend))

    generated: dict[Path, dict[str, Any]] = {}
    try:
        with ProcessPoolExecutor(
            max_workers=min(max_workers, len(targets)),
            mp_context=multiprocessing.get_context("spawn"),
        ) as executor:
            futures = [
                executor.submit(_generate_isolated_artifact_worker, payload)
                for payload in payloads
            ]
            for future in as_completed(futures):
                relative, patch, candidate = future.result()
                _persist_parallel_candidate_attempts(
                    output_root=root,
                    ontology_name=context.ontology.name,
                    artifact=relative,
                    patch=patch,
                )
                target = target_by_relative[relative]
                if patch.get("ok"):
                    patch = {**patch, "_isolated_candidate_bytes": candidate}
                generated[target] = patch
    finally:
        for workspace in workspaces:
            workspace.cleanup()
    return generated


def _publish_isolated_candidate(target: Path, patch: dict[str, Any]) -> dict[str, Any]:
    """Publish one accepted worker candidate when its serial review begins."""
    candidate = patch.pop("_isolated_candidate_bytes", None)
    if patch.get("ok"):
        if not isinstance(candidate, bytes):
            return {
                **patch,
                "ok": False,
                "failures": [
                    *list(patch.get("failures") or []),
                    "isolated_candidate_bytes_missing",
                ],
            }
        target.write_bytes(candidate)
    return patch
