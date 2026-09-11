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


def _prompt_iteration_spec(
    context: AgenticGenerationContext, target: Path
) -> dict[str, Any]:
    """Return the exact iteration/sub-iteration specification owned by a prompt."""
    plan_path = (
        Path(context.output_root)
        / "iterations"
        / context.ontology.name
        / "iterations.json"
    )
    try:
        plan = (
            json.loads(plan_path.read_text(encoding="utf-8"))
            if plan_path.is_file()
            else getattr(context, "iteration_blueprint", {})
        )
    except (OSError, json.JSONDecodeError):
        return {}

    stem = target.stem
    for iteration in plan.get("iterations") or []:
        if not isinstance(iteration, dict):
            continue
        iter_token = str(iteration.get("iteration_number") or "").replace(".", "_")
        candidates = {
            f"EXTRACTION_ITER_{iter_token}",
            f"PRE_EXTRACTION_ITER_{iter_token}",
        }
        if stem in candidates:
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
            sub_token = str(sub_iteration.get("iteration_number") or "").replace(
                ".", "_"
            )
            if stem == f"EXTRACTION_ITER_{sub_token}":
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
