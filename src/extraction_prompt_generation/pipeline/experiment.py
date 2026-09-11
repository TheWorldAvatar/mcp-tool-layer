"""End-to-end generation orchestrator used by the CLI.

Compile context, write empty prompt slots, blank those `.md` files, then
let exact-edits author them. `--stage context` stops before authoring.

See pipeline/README.md.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from models.locked_llm import LOCKED_GENERATION_MODEL
from src.extraction_prompt_generation.compile.artifact_compiler import (
    build_domain_generation_context,
)
from src.extraction_prompt_generation.compile.context import (
    AgenticGenerationContext,
    build_contexts_for_ontologies,
)
from src.extraction_prompt_generation.generate.artifact_state import (
    ArtifactStateStore,
)
from src.extraction_prompt_generation.generate.extraction_prompts import (
    run_extraction_prompt_generation,
    write_extraction_prompt_slots,
)
from src.extraction_prompt_generation.validate.report import (
    build_validation_report,
)


def _resumable_artifact_snapshots(
    context: AgenticGenerationContext,
    requested_target_names: set[str],
) -> dict[Path, bytes]:
    """Capture journal-backed bytes before deterministic scaffolding runs."""
    artifact_state = ArtifactStateStore(
        context.output_root, context.ontology.name
    )
    snapshots: dict[Path, bytes] = {}
    for artifact_dir, suffix in (
        (Path(context.prompts_dir), ".md"),
    ):
        if not artifact_dir.is_dir():
            continue
        for artifact in artifact_dir.glob(f"*{suffix}"):
            if (
                (not requested_target_names or artifact.name in requested_target_names)
                and artifact_state.should_preserve_existing(artifact)
            ):
                snapshots[artifact] = artifact.read_bytes()
    return snapshots


def run_agentic_generation_experiment(
    ontology_names: list[str],
    *,
    meta_task_config_path: str | Path | None = None,
    domain_config_path: str | Path | None = None,
    output_root: str | Path | None = None,
    generate_prompts: bool = False,
    llm_agent_generation: bool = False,
    generation_model: str = LOCKED_GENERATION_MODEL,
    parallel_generation: bool = True,
    max_generation_workers: int = 5,
    edit_backend: str = "exact_edits",
    target_artifacts: list[str] | None = None,
    write_context_files: bool = True,
    operation_mode: str = "legacy",
    selected_top_entity: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run compile and, when requested, model prompt authoring for one or more ontologies.

    `generate_prompts` writes scaffolds. `llm_agent_generation` then empties
    those markdown files and authors them with exact-edits. Targeted reruns
    replay the existing compiled plan instead of calling the planner again.
    """
    from src.extraction_prompt_generation.paths import (
        default_output_root,
        repository_root,
    )

    generation_model = LOCKED_GENERATION_MODEL
    repo = repository_root()
    output_path = Path(output_root) if output_root is not None else default_output_root()
    output_root = output_path
    targeted_snapshot = (
        {
            path: path.read_bytes()
            for path in output_path.rglob("*")
            if path.is_file()
        }
        if target_artifacts and output_path.is_dir()
        else {}
    )
    targeted_existing_paths = set(targeted_snapshot)

    planner = None
    if domain_config_path is not None and target_artifacts:
        ontology_name = ontology_names[0] if len(ontology_names) == 1 else ""
        compiled_plan_path = output_path / "iterations" / ontology_name / "iterations.json"
        contract_path = (
            output_path
            / "ontology_structures"
            / ontology_name
            / "generation_contract.json"
        )
        if compiled_plan_path.is_file() and contract_path.is_file():
            compiled_plan = json.loads(
                compiled_plan_path.read_text(encoding="utf-8")
            )
            compiled_contract = json.loads(contract_path.read_text(encoding="utf-8"))
            assignments: list[dict[str, Any]] = []
            enrichment_focus: dict[str, str] = {}
            for iteration in compiled_plan.get("iterations") or []:
                if not isinstance(iteration, dict):
                    continue
                iteration_number = str(iteration.get("iteration_number") or "")
                responsibilities = iteration.get("responsibilities") or {}
                assignments.append(
                    {
                        "slot": f"iter{iteration_number}",
                        "classes": list(responsibilities.get("classes") or []),
                        "object_properties": list(
                            responsibilities.get("object_properties") or []
                        ),
                        "rationale": str(iteration.get("description") or ""),
                    }
                )
                for sub_iteration in iteration.get("sub_iterations") or []:
                    if isinstance(sub_iteration, dict):
                        sub_number = str(
                            sub_iteration.get("iteration_number") or ""
                        )
                        enrichment_focus[f"iter{sub_number}"] = str(
                            sub_iteration.get("description") or ""
                        )
            top_entity = dict(compiled_contract.get("top_entity") or {})
            publish_contract = compiled_contract.get("ontology_publish_contract") or {}
            extension_focus = publish_contract.get("extension_focus") or {}
            if top_entity.get("owned_by_extension") is False and extension_focus:
                top_entity = dict(extension_focus)
            top_entity.setdefault("status", "known")
            top_entity.setdefault("source", "existing_compiled_contract")
            top_entity.setdefault("model", "compiled")
            top_entity.setdefault(
                "rationale", "Preserved from the existing compiled generation contract."
            )
            top_entity.setdefault(
                "evidence", [str(top_entity.get("class_local") or "")]
            )
            planner_answers = iter(
                [
                    top_entity,
                    {
                        "assignments": assignments,
                        "enrichment_focus": enrichment_focus,
                    },
                ]
            )

            def planner(_model: str, _prompt: str) -> dict[str, Any]:
                return next(planner_answers)

    if domain_config_path is not None:
        if len(ontology_names) != 1:
            raise ValueError("domain_config_path requires exactly one ontology")
        contexts = [
            build_domain_generation_context(
                domain_config_path=domain_config_path,
                output_root=output_root,
                repository_root=repo,
                write_files=write_context_files,
                planner=planner,
                operation_mode=operation_mode,
                selected_top_entity=selected_top_entity,
            )
        ]
        if contexts[0].ontology.name != ontology_names[0]:
            raise ValueError(
                "domain config ontology does not match requested ontology: "
                f"{contexts[0].ontology.name!r} != {ontology_names[0]!r}"
            )
    else:
        contexts = build_contexts_for_ontologies(
            ontology_names,
            meta_task_config_path=meta_task_config_path,
            output_root=output_root,
            write_files=write_context_files,
        )
    if targeted_snapshot:
        for path in [
            candidate
            for candidate in output_path.rglob("*")
            if candidate.is_file() and candidate not in targeted_existing_paths
        ]:
            path.unlink()
        for path, content in targeted_snapshot.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
    all_contracts = [ctx.contract for ctx in contexts]
    reports = []
    for context in contexts:
        requested_target_names = {
            Path(item).name for item in (target_artifacts or [])
        }
        protected_artifacts: dict[Path, bytes] = {}
        if requested_target_names:
            for artifact_dir, suffix in (
                (Path(context.prompts_dir), ".md"),
            ):
                if not artifact_dir.is_dir():
                    continue
                for artifact in artifact_dir.glob(f"*{suffix}"):
                    if artifact.name not in requested_target_names:
                        protected_artifacts[artifact] = artifact.read_bytes()
            prompts_dir = Path(context.prompts_dir)
            if prompts_dir.is_dir():
                for component in prompts_dir.glob("*.materializable.inc"):
                    prompt_name = component.name.removesuffix(
                        ".materializable.inc"
                    ) + ".md"
                    if prompt_name not in requested_target_names:
                        protected_artifacts[component] = component.read_bytes()
        resumable_artifacts = (
            _resumable_artifact_snapshots(context, requested_target_names)
            if llm_agent_generation and hasattr(context, "output_root")
            else {}
        )
        written: list[str] = []
        if generate_prompts:
            written.extend(write_extraction_prompt_slots(context))
        for resumable_path, resumable_content in resumable_artifacts.items():
            resumable_path.parent.mkdir(parents=True, exist_ok=True)
            resumable_path.write_bytes(resumable_content)
        for protected_path, protected_content in protected_artifacts.items():
            protected_path.write_bytes(protected_content)
        if llm_agent_generation:
            # Deterministic generation establishes only the required artifact slots.
            # Remove its semantic content so every final line is authored by the LLM.
            for raw_path in written:
                path = Path(raw_path)
                if (
                    path.is_file()
                    and path.suffix == ".md"
                    and path not in resumable_artifacts
                    and (
                        not requested_target_names
                        or path.name in requested_target_names
                    )
                ):
                    path.write_text("", encoding="utf-8")
        foreign = [
            bundle
            for bundle in all_contracts
            if bundle.get("ontology_name") != context.ontology.name
        ]
        repair_history: list[dict[str, Any]] = []
        llm_agent_run: dict[str, Any] | None = None
        if llm_agent_generation and generate_prompts:
            tracks: list[dict[str, Any]] = []
            tracks.append(
                run_extraction_prompt_generation(
                    context,
                    model_name=generation_model,
                    foreign_contracts=foreign,
                    max_generation_workers=max_generation_workers,
                    target_artifacts=target_artifacts,
                    protected_artifacts=protected_artifacts,
                )
            )
            llm_agent_run = {
                "tracks": tracks,
                "ok": all(item.get("ok") for item in tracks),
                "final_report": next(
                    (
                        item.get("final_report")
                        for item in reversed(tracks)
                        if isinstance(item.get("final_report"), dict)
                    ),
                    None,
                ),
            }
            final_report = llm_agent_run.get("final_report")
            report = (
                dict(final_report)
                if isinstance(final_report, dict)
                else build_validation_report(
                    context, foreign_contracts=foreign, write_report=True
                )
            )
            report["ok"] = bool(report.get("ok")) and bool(llm_agent_run.get("ok"))
        else:
            report = build_validation_report(
                context, foreign_contracts=foreign, write_report=True
            )
        repair_history.append(
            {
                "iteration": 0,
                "ok": report.get("ok"),
                "failures": report.get("failures") or [],
                "feedback": report.get("feedback") or {},
                "repaired_files": [],
            }
        )
        report["written_files"] = written
        report["generation_mode"] = (
            "pure_llm_generation" if llm_agent_generation else "deterministic_scaffold"
        )
        report["llm_agent_run"] = llm_agent_run
        report["repair_history"] = repair_history
        reports.append(report)

    summary = {
        "ok": all(report.get("ok") for report in reports),
        "output_root": str(output_root),
        "reports": reports,
    }
    root = Path(output_root)
    (root / "reports").mkdir(parents=True, exist_ok=True)
    (root / "reports" / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return summary
