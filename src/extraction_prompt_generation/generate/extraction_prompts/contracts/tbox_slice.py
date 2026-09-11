"""Iteration T-Box slice and comment/warning projections.

Projects only symbols connected to the current iteration's declared scope.
See contracts/README.md.
"""

from __future__ import annotations

from typing import Any

from src.extraction_prompt_generation.compile.context import (
    AgenticGenerationContext,
)


def _prompt_tbox_slice(
    context: AgenticGenerationContext, iteration_spec: dict[str, Any]
) -> dict[str, Any]:
    """Project T-Box symbols connected to the current iteration's declared scope."""
    parsed = getattr(context, "parsed", {}) or {}
    classes = parsed.get("classes") or {}
    properties = parsed.get("properties") or {}
    scope_owner = (
        iteration_spec.get("parent_iteration")
        if isinstance(iteration_spec.get("parent_iteration"), dict)
        else iteration_spec
    )
    if not isinstance(scope_owner, dict):
        scope_owner = {}
    semantic_scope = scope_owner.get("semantic_scope") or {}
    focus_classes = {
        str(item.get("local") or "").strip()
        for item in semantic_scope.get("classes") or []
        if isinstance(item, dict) and str(item.get("local") or "").strip()
    }
    if bool(scope_owner.get("has_pre_extraction")) or bool(
        scope_owner.get("requires_pre_extraction")
    ):
        focus_classes.update(
            str(value).strip()
            for value in (
                (context.contract.get("ordered_member_profile") or {}).get(
                    "ordered_member_classes"
                )
                or []
            )
            if str(value).strip() in classes
        )
    focus_properties = {
        str(item.get("local") or "").strip()
        for item in semantic_scope.get("object_properties") or []
        if isinstance(item, dict) and str(item.get("local") or "").strip()
    }
    materialization_classes = {
        str(value).strip()
        for value in scope_owner.get("linked_materialization_classes") or []
        if str(value).strip() in classes
    }
    if not focus_classes:
        responsibilities = scope_owner.get("responsibilities") or {}
        focus_classes = {
            str(local).strip()
            for local in responsibilities.get("classes") or []
            if str(local).strip()
        }
        focus_properties = {
            str(local).strip()
            for local in responsibilities.get("object_properties") or []
            if str(local).strip()
        }
    if not focus_classes and not focus_properties:
        raise ValueError(
            "Prompt generation requires a non-empty semantic_scope "
            "(classes or object_properties) for iteration "
            f"{scope_owner.get('iteration_number')!r}; refusing to invent scope from "
            "the top entity fallback"
        )
    changed = True
    while changed:
        changed = False
        for local, spec in classes.items():
            parents = set((spec or {}).get("parent_classes") or [])
            if parents & focus_classes and local not in focus_classes:
                focus_classes.add(local)
                changed = True
    relevant_properties: dict[str, Any] = {}
    for local, spec in properties.items():
        domains = {
            str(value)
            for value in ((spec or {}).get("domains") or [(spec or {}).get("domain")])
            if str(value or "").strip()
        }
        range_local = str((spec or {}).get("range") or "").strip()
        if (
            str(local) in focus_properties
            or domains & focus_classes
            or range_local in focus_classes
        ):
            relevant_properties[str(local)] = {
                "iri": str((spec or {}).get("iri") or ""),
                "kind": str((spec or {}).get("kind") or ""),
                "domains": sorted(domains),
                "range": range_local,
                "comment": str((spec or {}).get("comment") or ""),
            }
    for local, spec in properties.items():
        if str((spec or {}).get("kind") or "") != "datatype":
            continue
        if str(local) in relevant_properties:
            continue
        domains = {
            str(value)
            for value in ((spec or {}).get("domains") or [(spec or {}).get("domain")])
            if str(value or "").strip()
        }
        if not (domains & materialization_classes):
            continue
        relevant_properties[str(local)] = {
            "iri": str((spec or {}).get("iri") or ""),
            "kind": "datatype",
            "domains": sorted(domains),
            "range": str((spec or {}).get("range") or "").strip(),
            "comment": str((spec or {}).get("comment") or ""),
        }
    visible_classes = set(focus_classes) | materialization_classes
    return {
        "classes": {
            str(local): {
                "iri": str((classes.get(local) or {}).get("iri") or ""),
                "parent_classes": list(
                    (classes.get(local) or {}).get("parent_classes") or []
                ),
                "comment": str((classes.get(local) or {}).get("comment") or ""),
            }
            for local in sorted(visible_classes)
        },
        "properties": relevant_properties,
    }


def _warning_marked_tbox_contract(tbox_scope: dict[str, Any]) -> dict[str, Any]:
    """Project high-risk T-Box comments without adding domain-specific semantics."""
    marked: list[dict[str, str]] = []
    for section in ("classes", "properties"):
        for local, spec in sorted((tbox_scope.get(section) or {}).items()):
            comment = str((spec or {}).get("comment") or "")
            if "【Warning】" in comment:
                marked.append(
                    {
                        "section": section,
                        "local": str(local),
                        "comment": comment,
                    }
                )
    return {
        "schema_version": "warning-marked-tbox-contract.v1",
        "marker": "【Warning】",
        "marked_comments": marked,
        "requirements": [
            "Treat every marked comment as a high-risk class or field boundary requiring an "
            "explicit source-to-rule comparison before selecting or excluding that class or field.",
            "Evaluate the complete marked comment, including its positive threshold, exclusions, "
            "priority rules, and non-duplication rules; a lexical resemblance alone is insufficient.",
            "Compare all applicable marked alternatives before making the choice, and prefer "
            "unresolved or omitted output when the marked evidence threshold is not met.",
            "Do not derive any domain-specific trigger, example, class priority, or exception from "
            "this generic marker contract; all semantics must come from the marked T-Box comments.",
        ],
    }


def _subclass_comment_projection(tbox_scope: dict[str, Any]) -> dict[str, Any]:
    """Project every scoped subclass annotation without ontology-specific rules."""
    classes = tbox_scope.get("classes") or {}
    subclass_annotations: list[dict[str, Any]] = []
    for child_local, child_spec in sorted(classes.items()):
        for parent_local in sorted(
            {
                str(value)
                for value in (child_spec or {}).get("parent_classes") or []
                if str(value) in classes
            }
        ):
            subclass_annotations.append(
                {
                    "parent_class_local": parent_local,
                    "subclass_local": str(child_local),
                    "comment": str((child_spec or {}).get("comment") or ""),
                }
            )
    return {
        "schema_version": "tbox-subclass-comment-projection.v1",
        "source": "generation_contract.tbox_scope.classes",
        "subclasses": subclass_annotations,
        "requirements": [
            "Apply every projected subclass comment when deciding "
            "whether a source occurrence belongs to that subclass.",
            "Do not add ontology-local class names, triggers, or exceptions outside this "
            "T-Box-derived projection.",
        ],
    }
