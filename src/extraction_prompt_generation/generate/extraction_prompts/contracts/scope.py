"""Iteration spec, owned scope, and enrichment helpers.

Maps one prompt filename onto the compiled iteration and its semantic
scope. See contracts/README.md.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from src.extraction_prompt_generation.compile.context import (
    AgenticGenerationContext,
)


def _iteration_has_semantic_scope(iteration: dict[str, Any]) -> bool:
    semantic_scope = iteration.get("semantic_scope") or {}
    if any(
        isinstance(item, dict) and str(item.get("local") or "").strip()
        for item in (semantic_scope.get("classes") or [])
    ):
        return True
    responsibilities = iteration.get("responsibilities") or {}
    return any(str(item).strip() for item in (responsibilities.get("classes") or []))


def _enrich_iteration_spec_with_compiled_scope(
    context: AgenticGenerationContext, iteration: dict[str, Any]
) -> dict[str, Any]:
    """Attach compiled semantic scope when runtime iterations omit it."""
    if _iteration_has_semantic_scope(iteration):
        return iteration
    compiled_iterations = [
        item
        for item in (getattr(context, "iteration_blueprint", {}) or {}).get(
            "iterations"
        )
        or []
        if isinstance(item, dict) and _iteration_has_semantic_scope(item)
    ]
    if not compiled_iterations:
        return iteration
    semantic_source = next(
        (
            item
            for item in compiled_iterations
            if item.get("iteration_number") == iteration.get("iteration_number")
        ),
        compiled_iterations[0] if len(compiled_iterations) == 1 else None,
    )
    if semantic_source is None:
        return iteration
    enriched = dict(iteration)
    if semantic_source.get("responsibilities"):
        enriched["responsibilities"] = dict(semantic_source["responsibilities"])
    if semantic_source.get("semantic_scope"):
        enriched["semantic_scope"] = dict(semantic_source["semantic_scope"])
    return enriched


def _canonical_iteration_filename_token(iteration_number: Any) -> str:
    """Stable EXTRACTION_ITER / PRE_EXTRACTION_ITER suffix for one slot number."""
    raw = str(iteration_number if iteration_number is not None else "").strip()
    if not raw:
        return ""
    try:
        number = float(raw)
    except ValueError:
        return raw.replace(".", "_")
    if number.is_integer():
        return str(int(number))
    return raw.replace(".", "_")


def _iteration_number_tokens(iteration_number: Any) -> set[str]:
    """Filename tokens that can name one iteration, including JSON 2 vs 2.0."""
    raw = str(iteration_number if iteration_number is not None else "").strip()
    tokens: set[str] = set()
    if raw:
        tokens.add(raw)
        tokens.add(raw.replace(".", "_"))
    canonical = _canonical_iteration_filename_token(iteration_number)
    if canonical:
        tokens.add(canonical)
    return {token for token in tokens if token}


def _is_extraction_prompt_filename(name: str) -> bool:
    upper = str(name or "").upper()
    return upper.endswith(".MD") and upper.startswith(
        ("EXTRACTION_ITER_", "PRE_EXTRACTION_ITER_")
    )


def _prompt_uses_iter1_top_entity_contract(
    context: AgenticGenerationContext, target: Path
) -> bool:
    role = str(getattr(getattr(context, "ontology", None), "role", "") or "")
    return target.name == "EXTRACTION_ITER_1.md" and role != "extension"


def _iteration_plans_for_lookup(
    context: AgenticGenerationContext,
) -> list[dict[str, Any]]:
    """Prefer on-disk runtime iterations, then the in-memory compiled blueprint.

    A partial package can leave `iterations.json` empty, truncated, or stale.
    The current compile still has `iteration_blueprint`, so lookup must not
    stop at a hollow disk file.
    """
    plans: list[dict[str, Any]] = []
    plan_path = (
        Path(context.output_root)
        / "iterations"
        / context.ontology.name
        / "iterations.json"
    )
    if plan_path.is_file():
        try:
            disk_plan = json.loads(plan_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            disk_plan = None
        if isinstance(disk_plan, dict) and disk_plan.get("iterations"):
            plans.append(disk_plan)
    blueprint = getattr(context, "iteration_blueprint", None) or {}
    if isinstance(blueprint, dict) and blueprint.get("iterations"):
        if not plans or blueprint is not plans[0]:
            plans.append(blueprint)
    return plans


def _match_prompt_iteration_spec(
    context: AgenticGenerationContext,
    plan: dict[str, Any],
    stem: str,
) -> dict[str, Any]:
    for iteration in plan.get("iterations") or []:
        if not isinstance(iteration, dict):
            continue
        stems = {
            f"{prefix}_{token}"
            for token in _iteration_number_tokens(iteration.get("iteration_number"))
            for prefix in ("EXTRACTION_ITER", "PRE_EXTRACTION_ITER")
        }
        if stem in stems:
            return _enrich_iteration_spec_with_compiled_scope(
                context,
                {
                    key: value
                    for key, value in iteration.items()
                    if key not in {"sub_iterations"}
                },
            )
        for sub_iteration in iteration.get("sub_iterations") or []:
            if not isinstance(sub_iteration, dict):
                continue
            sub_stems = {
                f"EXTRACTION_ITER_{token}"
                for token in _iteration_number_tokens(
                    sub_iteration.get("iteration_number")
                )
            }
            if stem in sub_stems:
                parent = _enrich_iteration_spec_with_compiled_scope(
                    context,
                    {
                        key: value
                        for key, value in iteration.items()
                        if key not in {"sub_iterations"}
                    },
                )
                return {
                    "parent_iteration": parent,
                    "sub_iteration": dict(sub_iteration),
                }
    return {}


def _iteration_plan_containing(
    context: AgenticGenerationContext, iteration_number: Any
) -> dict[str, Any]:
    """Return a lookup plan that lists this iteration, preferring disk then blueprint."""
    wanted = _iteration_number_tokens(iteration_number)
    fallback: dict[str, Any] = {}
    for plan in _iteration_plans_for_lookup(context):
        if not fallback:
            fallback = plan
        for iteration in plan.get("iterations") or []:
            if not isinstance(iteration, dict):
                continue
            if _iteration_number_tokens(iteration.get("iteration_number")) & wanted:
                return plan
            for sub_iteration in iteration.get("sub_iterations") or []:
                if not isinstance(sub_iteration, dict):
                    continue
                if (
                    _iteration_number_tokens(sub_iteration.get("iteration_number"))
                    & wanted
                ):
                    return plan
    return fallback


def _prompt_iteration_spec(
    context: AgenticGenerationContext, target: Path
) -> dict[str, Any]:
    """Return the exact iteration/sub-iteration specification owned by a prompt."""
    stem = target.stem
    for plan in _iteration_plans_for_lookup(context):
        matched = _match_prompt_iteration_spec(context, plan, stem)
        if matched:
            return matched
    return {}


def _prompt_can_build_generation_contract(
    context: AgenticGenerationContext, target: Path
) -> bool:
    """True when this markdown file is a planned extraction/pre-extraction slot."""
    if not _is_extraction_prompt_filename(target.name):
        return False
    if _prompt_uses_iter1_top_entity_contract(context, target):
        return True
    return bool(_prompt_iteration_spec(context, target))


def _planned_extraction_prompt_paths(
    context: AgenticGenerationContext,
) -> list[Path]:
    """EXTRACTION / PRE_EXTRACTION files that belong to the current iteration plan."""
    prompts_dir = Path(context.prompts_dir)
    if not prompts_dir.is_dir():
        return []
    return [
        path
        for path in sorted(prompts_dir.glob("*.md"))
        if _prompt_can_build_generation_contract(context, path)
    ]


def _unplanned_prompt_artifact_paths(
    context: AgenticGenerationContext,
) -> list[Path]:
    """Leftover markdown in the prompt dir that is not a current generation slot."""
    prompts_dir = Path(context.prompts_dir)
    if not prompts_dir.is_dir():
        return []
    return [
        path
        for path in sorted(prompts_dir.glob("*.md"))
        if not _prompt_can_build_generation_contract(context, path)
    ]


def _iteration_owned_scope(
    iteration_spec: dict[str, Any],
    *,
    materializable_class_locals: set[str] | None = None,
) -> dict[str, list[str]]:
    """Return the exact compiled class/property ownership surface for one prompt."""
    scope_owner = (
        iteration_spec.get("parent_iteration")
        if isinstance(iteration_spec.get("parent_iteration"), dict)
        else iteration_spec
    )
    if not isinstance(scope_owner, dict):
        scope_owner = {}
    semantic_scope = scope_owner.get("semantic_scope") or {}
    classes = [
        str(item.get("local") or "").strip()
        for item in semantic_scope.get("classes") or []
        if isinstance(item, dict) and str(item.get("local") or "").strip()
    ]
    object_properties = [
        str(item.get("local") or "").strip()
        for item in semantic_scope.get("object_properties") or []
        if isinstance(item, dict) and str(item.get("local") or "").strip()
    ]
    responsibilities = scope_owner.get("responsibilities") or {}
    if not classes:
        classes = [
            str(value).strip()
            for value in responsibilities.get("classes") or []
            if str(value).strip()
        ]
    if not object_properties:
        object_properties = [
            str(value).strip()
            for value in responsibilities.get("object_properties") or []
            if str(value).strip()
        ]
    materialization_classes = [
        str(value).strip()
        for value in scope_owner.get("linked_materialization_classes") or []
        if str(value).strip()
    ]
    if materializable_class_locals is not None:
        from src.extraction_prompt_generation.compile.materialization_closure import (
            restrict_classes_to_creator_surface,
        )

        classes = restrict_classes_to_creator_surface(
            classes, materializable_class_locals
        )
        materialization_classes = restrict_classes_to_creator_surface(
            materialization_classes, materializable_class_locals
        )
    return {
        "classes": list(dict.fromkeys(classes)),
        "object_properties": list(dict.fromkeys(object_properties)),
        "linked_materialization_classes": list(
            dict.fromkeys(materialization_classes)
        ),
    }


def _is_enrichment_iteration_spec(iteration_spec: Mapping[str, Any] | None) -> bool:
    """True only for compiled sub-iteration / enrichment prompt targets."""
    spec = iteration_spec or {}
    return bool(spec.get("parent_iteration")) or bool(spec.get("sub_iteration"))
