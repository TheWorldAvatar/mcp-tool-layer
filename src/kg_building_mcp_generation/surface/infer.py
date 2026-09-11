"""Discover, judge leftovers, compile, and persist the occurrence surface."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from src.kg_building_mcp_generation.surface.candidates import (
    discover_occurrence_surface_candidates,
    install_membership_only_operation_units,
)
from src.kg_building_mcp_generation.surface.compile import (
    compile_fallback_instruction,
    compile_occurrence_surface,
)
from src.kg_building_mcp_generation.surface.helpers import (
    DECISION_SCHEMA,
    INSTRUCTION_SCHEMA,
)
from src.kg_building_mcp_generation.surface.judge import (
    JsonPlanner,
    _deterministic_decisions,
    _facet_prompt,
    _inherit_ordered_sibling_decisions,
    _input_sha256,
    _judge_missing,
    _judged_payload,
    _partition_candidates,
    _prior_bundles,
    _reuse_judged_decisions,
)

def _attach_compiled(
    *,
    parsed: Mapping[str, Any],
    contract: dict[str, Any],
    iteration_plan: Mapping[str, Any],
    decision_bundle: dict[str, Any],
) -> dict[str, Any]:
    contract["occurrence_surface_decisions"] = decision_bundle
    compiled = compile_occurrence_surface(
        parsed=parsed,
        contract=contract,
        iteration_plan=iteration_plan,
    )
    instruction_bundle = {
        "schema_version": INSTRUCTION_SCHEMA,
        "text": compiled.get("instruction") or compile_fallback_instruction(compiled),
        "source": "compiled_operational_instruction",
    }
    compiled["instruction"] = str(instruction_bundle["text"] or "")
    contract["occurrence_surface_instruction"] = instruction_bundle
    contract["occurrence_surface_units"] = compiled
    primitive_units = contract.get("materialization_operation_units") or {}
    primitive_units["merged_predicate_locals"] = sorted(
        {
            *(primitive_units.get("merged_predicate_locals") or []),
            *(compiled.get("bundled_predicate_locals") or []),
        }
    )
    contract["materialization_operation_units"] = primitive_units
    decision_bundle["instruction"] = instruction_bundle
    return compiled

def infer_occurrence_surface(
    context: Any,
    *,
    planner: JsonPlanner,
    model: str,
    checkpoint_path: str | Path | None = None,
    prior_decisions: Mapping[str, Any] | None = None,
    judge_batch_size: int | None = None,
) -> dict[str, Any]:
    """Discover, adjudicate remaining facets, compile, and persist the occurrence surface."""
    parsed = getattr(context, "parsed", {}) or {}
    contract = getattr(context, "contract", {}) or {}
    iteration_plan = getattr(context, "iteration_blueprint", {}) or {}
    install_membership_only_operation_units(
        parsed=parsed,
        contract=contract,
        iteration_plan=iteration_plan,
    )
    candidates = discover_occurrence_surface_candidates(
        parsed=parsed,
        contract=contract,
        iteration_plan=iteration_plan,
    )
    contract["occurrence_surface_candidates"] = candidates
    deterministic, judged = _partition_candidates(candidates)
    judged_payload = _judged_payload(candidates)
    input_sha = _input_sha256(candidates)
    judged_sha = _input_sha256(judged_payload)
    judge_prompt = _facet_prompt(judged_payload)
    judge_prompt_sha = hashlib.sha256(judge_prompt.encode("utf-8")).hexdigest()
    checkpoint = Path(checkpoint_path) if checkpoint_path is not None else None
    priors = _prior_bundles(checkpoint, prior_decisions)
    for cached in priors:
        if (
            cached.get("schema_version") == DECISION_SCHEMA
            and cached.get("judged_input_sha256") == judged_sha
            and cached.get("judge_model") == model
            and cached.get("judge_prompt_sha256") == judge_prompt_sha
            and cached.get("decisions")
            and cached.get("fallback") != "separate_optional_facets"
        ):
            compiled = _attach_compiled(
                parsed=parsed,
                contract=contract,
                iteration_plan=iteration_plan,
                decision_bundle=cached,
            )
            if not compiled.get("instruction"):
                compiled["instruction"] = compile_fallback_instruction(compiled)
            return cached

    reused, missing = _reuse_judged_decisions(judged, priors)
    reused, missing = _inherit_ordered_sibling_decisions(
        all_candidates=list(candidates.get("candidates") or []),
        reused=reused,
        missing=missing,
    )
    validation_errors: list[str] = []
    used_llm = False
    if not missing:
        judged_decisions = reused
    else:
        used_llm = True
        judged_decisions, validation_errors, judge_prompt_sha = _judge_missing(
            planner,
            model,
            missing,
            judged_payload,
            batch_size=judge_batch_size,
        )
        judged_decisions = reused + judged_decisions

    decision_bundle: dict[str, Any] = {
        "schema_version": DECISION_SCHEMA,
        "input_sha256": input_sha,
        "judged_input_sha256": judged_sha,
        "judge_model": model,
        "judge_prompt_sha256": judge_prompt_sha,
        "decisions": _deterministic_decisions(deterministic) + judged_decisions,
        "reused_prior_decision_count": len(reused),
        "llm_judged_count": 0 if not used_llm else len(missing),
    }
    if validation_errors:
        decision_bundle["fallback"] = "invalid_candidates_only"
        decision_bundle["validation_errors"] = validation_errors

    _attach_compiled(
        parsed=parsed,
        contract=contract,
        iteration_plan=iteration_plan,
        decision_bundle=decision_bundle,
    )
    if checkpoint is not None:
        checkpoint.parent.mkdir(parents=True, exist_ok=True)
        checkpoint.write_text(
            json.dumps(decision_bundle, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    return decision_bundle

