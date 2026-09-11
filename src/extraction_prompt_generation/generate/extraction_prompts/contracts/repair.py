"""Generated-prompt validation and semantic repair loop.

Mechanical gates, then one GPT-5 semantic review, then exact-edits repair.
See contracts/README.md.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

from src.extraction_prompt_generation.compile.context import (
    AgenticGenerationContext,
)
from src.extraction_prompt_generation.generate.extraction_prompts.contracts.generation_contract import (
    _prompt_artifact_generation_contract,
)
from src.extraction_prompt_generation.generate.extraction_prompts.contracts.materializable import (
    _materializable_prompt_component_text,
    _prompt_contains_deterministic_component,
    _write_materializable_prompt_component,
)
from src.extraction_prompt_generation.llm.artifact_editor import (
    EditBackend,
    run_llm_artifact_editor,
)

run_llm_unified_diff_editor = run_llm_artifact_editor


def _validate_generated_prompt(
    *,
    model_name: str,
    context: AgenticGenerationContext,
    target: Path,
    foreign_contracts: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    """Run mechanical gates, then one LLM semantic gate, for one prompt."""
    from src.extraction_prompt_generation.generate.prompt_semantic_review import (
        review_generated_prompt_semantics_with_llm,
    )
    from src.extraction_prompt_generation.validate.report import (
        build_validation_report,
    )

    relative = (
        target.resolve().relative_to(Path(context.output_root).resolve()).as_posix()
    )
    if target.suffix == ".md":
        _write_materializable_prompt_component(context, target)
    mechanical = build_validation_report(
        context,
        foreign_contracts=foreign_contracts,
        write_report=False,
        prompts_required=False,
        active_artifacts=[relative],
    )
    if not mechanical.get("stage_ok"):
        return {**mechanical, "ok": False, "semantic_review": None}
    review_path = target
    review_temp: Any = None
    authored = target.read_text(encoding="utf-8", errors="replace")
    component_text = (
        _materializable_prompt_component_text(context, target)
        if target.name.upper().startswith(("EXTRACTION_ITER_", "PRE_EXTRACTION_ITER_"))
        else ""
    )
    if component_text and not _prompt_contains_deterministic_component(
        authored, component_text
    ):
        review_temp = tempfile.TemporaryDirectory(prefix="composed_prompt_review_")
        review_path = Path(review_temp.name) / target.name
        review_path.write_text(
            authored.rstrip() + "\n\n" + component_text.rstrip() + "\n",
            encoding="utf-8",
        )
    try:
        semantic_review = review_generated_prompt_semantics_with_llm(
            context=context,
            artifact_path=review_path,
            model_name=model_name,
        )
    finally:
        if review_temp is not None:
            review_temp.cleanup()
    if semantic_review.get("decision") == "pass":
        return {
            **mechanical,
            "ok": True,
            "stage_ok": True,
            "semantic_review": semantic_review,
        }
    semantic_failure = "LLM prompt semantic review requires repair:\n" + json.dumps(
        semantic_review, ensure_ascii=False
    )
    semantic_observation = {
        "id": f"prompt:{target.name}#llm-semantic",
        "observation_id": f"prompt:{target.name}#llm-semantic",
        "status": "fail",
        "stage": "prompt_semantic",
        "message": semantic_review.get("summary") or semantic_failure,
        "observed_artifacts": [relative],
        "evidence": {
            "failures": semantic_review.get("critical_errors") or [],
            "semantic_review": semantic_review,
        },
    }
    return {
        **mechanical,
        "ok": False,
        "stage_ok": False,
        "failures": [*(mechanical.get("failures") or []), semantic_failure],
        "observations": [
            *(mechanical.get("observations") or []),
            semantic_observation,
        ],
        "semantic_review": semantic_review,
    }


def _prompt_semantic_repair_task(
    *,
    context: AgenticGenerationContext,
    target: Path,
    report: dict[str, Any],
) -> str:
    """Build bounded T-Box-fidelity repair instructions for one frozen prompt."""
    relative = (
        target.resolve().relative_to(Path(context.output_root).resolve()).as_posix()
    )
    failures = [
        observation
        for observation in report.get("observations") or []
        if observation.get("status") == "fail"
    ]
    prompt_contract = _prompt_artifact_generation_contract(context, target)
    return (
        "Repair exactly one generated runtime prompt after its mechanical runtime-binding "
        "gates have passed. Preserve all required slots, current iteration scope, and valid "
        "instructions. Resolve only the supplied T-Box-fidelity or prompt-semantic failures. "
        "T-Box comments are binding: remove or rewrite instructions that enable a class or "
        "property whose comment forbids that use. Do not add fixture facts, source content, "
        "or new runtime slots. For every failure, use error as the defect, location as the "
        "only place to inspect, and known_correct_fix as the required correction. Do not "
        "invent fallbacks for an upstream-contract blocker; report it as non-repairable in "
        "this prompt. Return the smallest complete patch.\n\n"
        + json.dumps(
            {
                "target": relative,
                "prompt_generation_contract": prompt_contract,
                "active_tbox_scope": dict(prompt_contract.get("tbox_scope") or {}),
                "failing_observations": failures,
            },
            ensure_ascii=False,
        )
    )


def _repair_generated_prompt_semantics(
    *,
    model_name: str,
    context: AgenticGenerationContext,
    target: Path,
    foreign_contracts: list[dict[str, Any]] | None,
    report: dict[str, Any],
    edit_backend: EditBackend,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Repair one hard-gate-clean prompt against its targeted full validation evidence."""
    upstream_blockers = [
        failure
        for failure in report.get("failures") or []
        if "repairability=not repairable in the prompt file" in str(failure)
    ]
    if upstream_blockers:
        return report, {
            "ok": False,
            "failure_class": "upstream_contract",
            "repairability": "not_repairable_in_prompt",
            "failures": upstream_blockers,
            "feedback": {
                "error": "The prompt requires an upstream contract value that is absent.",
                "location": "context.contract.top_entity.class_local",
                "known_correct_fix": (
                    "Populate the top-entity class from the active T-Box and regenerate "
                    "EXTRACTION_ITER_1.md so the selected root class is rendered."
                ),
            },
        }
    current_report = report

    def validate() -> dict[str, Any]:
        nonlocal current_report
        current_report = _validate_generated_prompt(
            model_name=model_name,
            context=context,
            target=target,
            foreign_contracts=foreign_contracts,
        )
        return current_report

    patch = run_llm_unified_diff_editor(
        model_name=model_name,
        output_root=Path(context.output_root),
        targets=[target],
        task_prompt=_prompt_semantic_repair_task(
            context=context,
            target=target,
            report=report,
        ),
        max_attempts=10,
        validate=validate,
        max_targets=1,
        progress=lambda message: print(
            f"[prompt_semantic_repair] {message}", flush=True
        ),
        edit_backend=edit_backend,
    )
    return current_report if patch.get("ok") else report, patch
