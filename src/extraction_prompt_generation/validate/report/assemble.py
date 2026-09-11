"""Assemble per-check observations into the validation report.

`build_validation_report` is the public entry. Writes
`generated/reports/<ontology>/generation_report.json`. See report/README.md.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.extraction_prompt_generation.compile.context import (
    AgenticGenerationContext,
)
from src.extraction_prompt_generation.compile.generation_contracts import (
    build_validation_observation,
)
from src.extraction_prompt_generation.compile.materialization_closure import (
    compile_materialization_obligation_graph,
    materialization_closure_failures,
)
from src.extraction_prompt_generation.validate.report.foreign_symbols import (
    _foreign_symbol_report,
)
from src.extraction_prompt_generation.validate.report.operation_units import (
    _operation_unit_contract_report,
)
from src.extraction_prompt_generation.validate.report.prompts import (
    _iteration_prompt_schema_contract_report,
    _prompt_quality_report,
    _prompt_runtime_binding_report,
    _prompt_tbox_fidelity_report,
)
from src.extraction_prompt_generation.validate.report.runtime_hygiene import (
    _runtime_graph_hygiene_report,
)
from src.extraction_prompt_generation.validate.report.stage import (
    _stage_artifact_contract_report,
)
from src.extraction_prompt_generation.validate.report.syntax import (
    _syntax_report,
)
from src.extraction_prompt_generation.validate.report.tool_surface import (
    _expected_tool_surface_report,
    _ordered_member_contract_report,
    _relationship_param_description_report,
)
from src.extraction_prompt_generation.validate.report.ttl import (
    _ttl_export_report,
)


def build_validation_report(
    context: AgenticGenerationContext,
    *,
    foreign_contracts: list[dict[str, Any]] | None = None,
    write_report: bool = True,
    prompts_required: bool = False,
    include_prompt_checks: bool = True,
    extra_failures: list[str] | None = None,
    active_artifacts: list[str] | None = None,
) -> dict[str, Any]:
    scripts_dir = Path(context.scripts_dir)
    prompts_dir = Path(context.prompts_dir)
    failures: list[str] = []
    warnings: list[str] = []
    observations: list[dict[str, Any]] = []
    active_artifact_set = {
        Path(value).as_posix() for value in (active_artifacts or [])
    }
    stage_mode = active_artifacts is not None

    def record(
        *,
        check_id: str,
        stage: str,
        check_failures: list[str] | None = None,
        check_warnings: list[str] | None = None,
        observed_artifacts: list[str] | None = None,
        blocked_by: list[str] | None = None,
        evidence: dict[str, Any] | None = None,
        message: str | None = None,
        check_obligations: list[dict[str, Any]] | None = None,
    ) -> None:
        failure_items = [str(item) for item in (check_failures or [])]
        warning_items = [str(item) for item in (check_warnings or [])]
        failures.extend(failure_items)
        warnings.extend(warning_items)
        observations.append(
            build_validation_observation(
                check_id=check_id,
                subject_key=context.ontology.name,
                stage=stage,
                failures=failure_items,
                warnings=warning_items,
                observed_artifacts=observed_artifacts,
                blocked_by=blocked_by,
                evidence=evidence,
                message=message,
            )
        )
        for obligation in check_obligations or []:
            subject = str(obligation.get("subject_key") or "").strip()
            if not subject:
                raise ValueError(f"{check_id} obligation requires subject_key")
            observations.append(
                build_validation_observation(
                    check_id=check_id,
                    subject_key=f"{context.ontology.name}/{subject}",
                    stage=stage,
                    failures=list(obligation.get("failures") or []),
                    warnings=list(obligation.get("warnings") or []),
                    observed_artifacts=list(
                        obligation.get("observed_artifacts") or observed_artifacts or []
                    ),
                    blocked_by=list(obligation.get("blocked_by") or []),
                    evidence=dict(obligation.get("evidence") or {}),
                    message=obligation.get("message"),
                )
            )

    if extra_failures:
        record(
            check_id="generation.external_failures",
            stage="precondition",
            check_failures=[str(item) for item in extra_failures],
            evidence={"source": "caller"},
        )
    if stage_mode:
        f, w, observed = _stage_artifact_contract_report(
            context, [Path(value).as_posix() for value in (active_artifacts or [])]
        )
        record(
            check_id="generation.stage_artifact_contract",
            stage="artifact",
            check_failures=f,
            check_warnings=w,
            observed_artifacts=observed,
            evidence={
                "active_artifacts": sorted(active_artifact_set),
                "fixed_om2_import_contract": (
                    "Use a package-relative import from ._fixed_om2_runtime; "
                    "do not import fixed_om2_runtime or om2.runtime.fixed."
                ),
                "required_import_example": (
                    "from ._fixed_om2_runtime import "
                    "find_or_create_om2_quantity_from_label"
                ),
                "entity_tool_naming_contract": (
                    "Every class-creation tool must be a module-scope callable named "
                    "exactly create_<class_local> and must be registered/exported."
                ),
                "relationship_tool_naming_contract": (
                    "Every object-property tool must be a module-scope callable named "
                    "exactly add_<predicate_local> and must be registered/exported."
                ),
            },
        )
    active_prompt_paths = [
        (Path(context.output_root) / relative)
        for relative in active_artifact_set
        if relative.endswith(".md")
    ]
    run_prompt_checks = bool(include_prompt_checks) or bool(active_prompt_paths)
    prompt_files = sorted(prompts_dir.glob("*.md")) if prompts_dir.is_dir() else []
    if run_prompt_checks and prompts_required and not prompt_files:
        record(
            check_id="generation.prompt_artifacts_required",
            stage="precondition",
            check_failures=[
                "Prompt enhancement requires existing prompt artifacts; prompt validation cannot be skipped"
            ],
            observed_artifacts=[str(prompts_dir)],
        )
    if run_prompt_checks and prompts_required and prompt_files and not stage_mode:
        # Import lazily because the generation contracts also use this validator.
        # These projections are the same contracts and creator surface shown to
        # the generation agents, so the closure does not reconstruct a second
        # approximation of their capabilities.
        from src.extraction_prompt_generation.generate.extraction_prompts.contracts.generation_contract import (
            _prompt_artifact_generation_contract,
        )
        from src.extraction_prompt_generation.compile.operation_units import (
            owned_entity_tool_contracts as _owned_entity_tool_contracts,
        )

        prompt_contracts = {
            path.name: _prompt_artifact_generation_contract(context, path)
            for path in prompt_files
        }
        closure = compile_materialization_obligation_graph(
            context,
            prompt_generation_contracts=prompt_contracts,
            creator_surface=_owned_entity_tool_contracts(context),
        )
        closure_failures = materialization_closure_failures(closure)
        record(
            check_id="generation.materialization_closure",
            stage="prompt",
            check_failures=closure_failures,
            observed_artifacts=[
                *(str(path) for path in prompt_files),
                str(scripts_dir),
            ],
            evidence={
                "hard_gate": True,
                "schema_version": closure["schema_version"],
                "closure": closure,
            },
            message=(
                "Materialization closure found deterministic contradictions"
                if closure_failures
                else "Materialization closure passed"
            ),
        )

    checks = (
        ("generation.syntax", "syntax", _syntax_report, True),
        (
            "generation.operation_unit_consistency",
            "contract",
            _operation_unit_contract_report,
            True,
        ),
        ("generation.tool_surface", "static", _expected_tool_surface_report, True),
        (
            "generation.relationship_param_description",
            "static",
            _relationship_param_description_report,
            True,
        ),
        (
            "generation.ordered_member_contract",
            "contract",
            _ordered_member_contract_report,
            False,
        ),
        ("generation.ttl_export", "runtime", _ttl_export_report, True),
        ("generation.prompt_quality", "prompt", _prompt_quality_report, False),
        (
            "generation.prompt_tbox_fidelity",
            "prompt",
            _prompt_tbox_fidelity_report,
            True,
        ),
        (
            "generation.prompt_runtime_binding",
            "prompt",
            _prompt_runtime_binding_report,
            True,
        ),
        (
            "generation.iteration_prompt_schema_contract",
            "prompt",
            _iteration_prompt_schema_contract_report,
            True,
        ),
        (
            "generation.runtime_graph_hygiene",
            "runtime",
            _runtime_graph_hygiene_report,
            True,
        ),
    )
    stage_prompt_checks = {
        "generation.prompt_quality",
        "generation.prompt_tbox_fidelity",
        "generation.prompt_runtime_binding",
        "generation.iteration_prompt_schema_contract",
    }
    script_check_ids = {
        "generation.syntax",
        "generation.tool_surface",
        "generation.relationship_param_description",
        "generation.ordered_member_contract",
        "generation.ttl_export",
        "generation.runtime_graph_hygiene",
    }
    package_prompt_check_ids = {
        *stage_prompt_checks,
    }
    for check_id, stage, fn, hard_gate in checks:
        if check_id in script_check_ids:
            continue
        if check_id in package_prompt_check_ids and not run_prompt_checks:
            observations.append(
                build_validation_observation(
                    check_id=check_id,
                    subject_key=context.ontology.name,
                    stage=stage,
                    blocked_by=["generation.prompt_stage_not_requested"],
                    observed_artifacts=[str(prompts_dir)],
                    evidence={"include_prompt_checks": False},
                )
            )
            continue
        if (
            stage_mode
            and check_id != "generation.syntax"
            and not (active_prompt_paths and check_id in stage_prompt_checks)
        ):
            observations.append(
                build_validation_observation(
                    check_id=check_id,
                    subject_key=context.ontology.name,
                    stage=stage,
                    blocked_by=["generation.stage_dependencies_incomplete"],
                    observed_artifacts=sorted(active_artifact_set),
                    evidence={"active_artifacts": sorted(active_artifact_set)},
                )
            )
            continue
        if fn in {
            _prompt_quality_report,
            _prompt_tbox_fidelity_report,
            _prompt_runtime_binding_report,
            _iteration_prompt_schema_contract_report,
        } and stage_mode:
            result = fn(context, active_prompt_paths)
        else:
            if fn is _syntax_report:
                active_python_paths = (
                    [
                        scripts_dir / Path(relative).name
                        for relative in active_artifact_set
                        if relative.endswith(".py")
                    ]
                    if stage_mode
                    else None
                )
                result = fn(scripts_dir, active_python_paths)
            else:
                result = fn(context)
        f, w = result[:2]
        obligations = result[2] if len(result) > 2 else []
        effective_hard_gate = hard_gate or (
            stage_mode and check_id == "generation.prompt_quality"
        )
        if not effective_hard_gate and f:
            w = [
                *(f"Advisory {check_id}: {message}" for message in f),
                *w,
            ]
            f = []
            obligations = []
        record(
            check_id=check_id,
            stage=stage,
            check_failures=f,
            check_warnings=w,
            observed_artifacts=[
                str(prompts_dir) if stage == "prompt" else str(scripts_dir)
            ],
            check_obligations=obligations,
            evidence={"hard_gate": effective_hard_gate},
        )

    if not stage_mode:
        f, w = _foreign_symbol_report(context, foreign_contracts)
        if f or w:
            record(
                check_id="generation.foreign_symbols",
                stage="cross_ontology",
                check_failures=f,
                check_warnings=w,
                observed_artifacts=[str(prompts_dir)],
                evidence={
                    "foreign_ontologies": [
                        str(bundle.get("ontology_name") or "")
                        for bundle in (foreign_contracts or [])
                    ]
                },
            )

    feedback = {
        "coding_agent": [
            msg for msg in failures if "Prompt" not in msg and "prompt" not in msg
        ],
        "prompt_agent": [
            msg
            for msg in failures
            if "Prompt" in msg or "prompt" in msg or "Foreign ontology symbols" in msg
        ],
    }
    report = {
        "ontology": context.ontology.name,
        "ok": not failures,
        "scripts_dir": context.scripts_dir,
        "prompts_dir": context.prompts_dir,
        "failures": failures,
        "warnings": warnings,
        "observations": observations,
        "stage_ok": not any(
            observation.get("status") == "fail"
            for observation in observations
            if observation.get("status") != "blocked"
        ),
        "active_artifacts": sorted(active_artifact_set),
        "feedback": feedback,
    }
    if write_report:
        report_path = Path(context.report_path)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        summary_path = Path(context.output_root) / "reports" / "summary.json"
        try:
            summary = (
                json.loads(summary_path.read_text(encoding="utf-8"))
                if summary_path.is_file()
                else {}
            )
        except (OSError, json.JSONDecodeError):
            summary = {}
        prior_reports = [
            item
            for item in (summary.get("reports") or [])
            if isinstance(item, dict)
            and str(item.get("ontology") or "") != context.ontology.name
        ]
        current_reports = [*prior_reports, report]
        summary = {
            **summary,
            "ok": all(bool(item.get("ok")) for item in current_reports),
            "output_root": str(context.output_root),
            "reports": current_reports,
        }
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(
            json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    return report
