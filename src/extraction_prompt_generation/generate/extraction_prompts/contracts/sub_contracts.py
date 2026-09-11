"""Lexical quantity, PRE types, iter1 top-entity, and subclass contracts.

Frozen English fragments attached to `_prompt_artifact_generation_contract`.
Do not rewrite the strings. See contracts/README.md.
"""

from __future__ import annotations

from typing import Any

from src.extraction_prompt_generation.compile.context import (
    AgenticGenerationContext,
)
from src.extraction_prompt_generation.compile.reuse_policy import (
    prohibited_class_locals,
)


def _subclass_decision_contract(tbox_scope: dict[str, Any]) -> dict[str, Any]:
    """Derive a domain-neutral subclass checklist contract from one T-Box slice."""
    classes = tbox_scope.get("classes") or {}
    decision_points: list[dict[str, Any]] = []
    for parent_local, parent_spec in sorted(classes.items()):
        candidates = [
            {
                "class_local": str(child_local),
                "comment": str((child_spec or {}).get("comment") or ""),
            }
            for child_local, child_spec in sorted(classes.items())
            if str(parent_local)
            in {
                str(value)
                for value in (child_spec or {}).get("parent_classes") or []
            }
        ]
        if candidates:
            decision_points.append(
                {
                    "parent_class_local": str(parent_local),
                    "parent_comment": str((parent_spec or {}).get("comment") or ""),
                    "candidate_subclasses": candidates,
                }
            )
    return {
        "schema_version": "tbox-subclass-decision-contract.v1",
        "source": "generation_contract.tbox_scope",
        "decision_points": decision_points,
        "checklist_requirements": [
            "Build the runtime subclass decision checklist only from these decision points and "
            "their verbatim T-Box comments.",
            "For every evidence atom that may instantiate a listed parent, evaluate every "
            "candidate subclass's positive conditions, exclusions, and disambiguation rules.",
            "Select one most-specific supported subclass, or mark the atom unresolved when the "
            "T-Box-derived evidence threshold is not met; never silently omit it.",
            "Do not add ontology-local triggers, examples, priorities, or exceptions from model "
            "knowledge, source fixtures, configuration, or this meta-contract.",
        ],
    }


def _lexical_quantity_hint_contract(
    context: AgenticGenerationContext,
    tbox_scope: dict[str, Any],
    *,
    role: str = "extraction",
    hint_representation: str = "",
) -> dict[str, Any]:
    """Project pipeline-materializable quantity links without domain assumptions."""
    scoped_properties = set((tbox_scope.get("properties") or {}).keys())
    properties = []
    for item in context.contract.get("om2_quantity_properties") or []:
        predicate_local = str((item or {}).get("predicate_local") or "").strip()
        if not predicate_local or predicate_local not in scoped_properties:
            continue
        properties.append(
            {
                "predicate_local": predicate_local,
                "predicate_iri": str((item or {}).get("predicate_iri") or "").strip(),
                "domain_locals": [
                    value.strip()
                    for value in str((item or {}).get("domain_locals") or "").split(",")
                    if value.strip()
                ],
                "range_iris": [
                    value.strip()
                    for value in str((item or {}).get("range_iris") or "").split(",")
                    if value.strip()
                ],
            }
        )
    semantic_text = str(hint_representation or "").strip() == "semantic-text.v1"
    if semantic_text:
        rules = [
            "For every explicit source quantity owned by one listed predicate, preserve the "
            "complete source lexical value as a standalone `<predicate_local>: <lexeme>` "
            "line under the owning occurrence. Prose must not be the sole carrier.",
            "Those lexemes are the pipeline interchange for later quantity-node "
            "materialization; this does not reclassify the T-Box object property as a datatype "
            "property and must not be emitted as JSON datatype_properties.",
            "Do not require or invent a quantity object ref, and do not omit the lexical value "
            "merely because the quantity target class is outside the extraction entity scope.",
            "Preserve qualitative values and complete multiplicity expressions without inventing "
            "a numerical normalization.",
        ]
    else:
        rules = [
            "For every explicit source quantity owned by one listed predicate, preserve the "
            "complete source lexical value under that predicate local in the source entity's "
            "datatype_properties object.",
            "This is a pipeline interchange exception for later deterministic quantity-node "
            "materialization; it does not reclassify the T-Box object property as a datatype property.",
            "Do not require or invent a quantity object ref, and do not omit the lexical value "
            "merely because the quantity target class is outside the extraction entity scope.",
            "Preserve qualitative values and complete multiplicity expressions without inventing "
            "a numerical normalization.",
        ]
    return {
        "schema_version": "pipeline-lexical-quantity-hints.v1",
        "role": role,
        "hint_representation": str(hint_representation or "").strip() or None,
        "properties": properties,
        "rules": rules,
    }


def _pre_extraction_candidate_type_contract(
    context: AgenticGenerationContext,
    tbox_scope: dict[str, Any],
    subclass_decision_contract: dict[str, Any],
) -> dict[str, Any]:
    """Derive the closed PRE ledger type surface from T-Box and reuse policy."""
    scoped_classes = tbox_scope.get("classes") or {}
    parent_classes = {
        str(item.get("parent_class_local") or "").strip()
        for item in subclass_decision_contract.get("decision_points") or []
        if str(item.get("parent_class_local") or "").strip()
    }
    non_reusable = {
        str(item.get("class_local") or "").strip()
        for item in ((context.contract.get("reuse_policy") or {}).get("classes") or [])
        if isinstance(item, dict)
        and item.get("reusable") is False
        and str(item.get("class_local") or "").strip()
    }
    parsed_classes = context.parsed.get("classes") or {}
    ordered_members = {
        str(value).strip()
        for value in (
            (context.contract.get("ordered_member_profile") or {}).get(
                "ordered_member_classes"
            )
            or []
        )
        if str(value).strip() in parsed_classes
    }
    parent_classes.update(
        str(parent).strip()
        for spec in parsed_classes.values()
        for parent in ((spec or {}).get("parent_classes") or [])
        if str(parent).strip() in parsed_classes
    )
    linked_non_reusable_ranges = {
        str((spec or {}).get("range") or "").strip()
        for spec in (tbox_scope.get("properties") or {}).values()
        if str((spec or {}).get("range") or "").strip() in non_reusable
        and str((spec or {}).get("range") or "").strip() in parsed_classes
    }
    candidate_universe = (
        set(scoped_classes)
        | ordered_members
        | linked_non_reusable_ranges
    )
    prohibited = {
        str(local)
        for local in candidate_universe
        for spec in [scoped_classes.get(local) or parsed_classes.get(local) or {}]
        if (spec or {}).get("creatable") is False
    } | prohibited_class_locals(context.contract.get("reuse_policy"))
    allowed = sorted(
        (candidate_universe & non_reusable) - parent_classes - prohibited
    )
    return {
        "schema_version": "tbox-pre-ledger-candidate-types.v1",
        "allowed_candidate_types": allowed,
        "rules": [
            "candidate_types is a closed enumeration: use only allowed_candidate_types.",
            "Do not place reusable context, equipment, environment, supplier, or "
            "classification classes in candidate_types; preserve their wording only inside "
            "verbatim evidence or candidate_properties when the property surface allows it.",
            "Every evidence atom must contain at least one allowed candidate type. A location, "
            "equipment, environment, duration, or other contextual "
            "fact must never become a standalone evidence atom with empty candidate_types. "
            "Attach it as a candidate_property to the nearest source-supported operation whose "
            "property surface permits it; if no owned operation can carry it, retain it only in "
            "scope/verbatim context rather than inventing a type or emitting an empty array.",
            "verbatim_quote must be one contiguous substring copied character-for-character "
            "from the supplied source. When an entity-specific condition is embedded in a "
            "shared sentence, quote the complete unchanged source sentence; never splice, "
            "specialize, normalize, or paraphrase it into an entity-specific sentence.",
        ],
    }


def _iter1_pipeline_top_entity_contract(
    context: AgenticGenerationContext,
) -> dict[str, Any]:
    """Project the pipeline-selected top entity into Iteration 1 generation."""
    policies = getattr(
        context,
        "pipeline_runtime_policies",
        (context.contract.get("runtime_policies") or {}),
    )
    iter1_rules = (
        ((policies.get("iter1_top_entity_kg") or {}).get("prompt_rules") or {})
        if isinstance(policies, dict)
        else {}
    )
    extraction_policy = (
        (policies.get("top_entity_extraction") or {})
        if isinstance(policies, dict)
        else {}
    )
    configured_local = str(iter1_rules.get("top_level_entity_name") or "").strip()
    prefixes = [
        str(value).strip()
        for value in (extraction_policy.get("count_lines_starting_with") or [])
        if str(value).strip()
    ]
    if configured_local and prefixes and prefixes != [configured_local]:
        raise ValueError(
            "Iteration 1 pipeline top-entity policy conflicts with the "
            f"top-entity extraction line prefixes: {configured_local!r} vs {prefixes!r}"
        )
    selected_local = str(
        (context.contract.get("top_entity") or {}).get("class_local") or ""
    ).strip()
    class_local = (
        selected_local
        or configured_local
        or (prefixes[0] if len(prefixes) == 1 else "")
    )
    classes = context.parsed.get("classes") or {}
    class_spec = classes.get(class_local) or {}
    class_iri = str(class_spec.get("iri") or "").strip()
    if not class_local or not class_iri:
        raise ValueError(
            "Iteration 1 requires a pipeline-selected top entity that exists "
            f"in the active T-Box; got {class_local!r}"
        )
    return {
        "class_local": class_local,
        "class_iri": class_iri,
        "source": "pipeline_runtime_policy",
        "line_prefix": class_local,
        "identifier_code_regex": str(
            extraction_policy.get("identifier_code_regex") or ""
        ).strip(),
        "output_contract": (
            f"Return zero or more lines only in the exact form "
            f"`{class_local}-N [<source-supported label or identifier>]`. "
            "Do not return prose, a no-findings sentence, headings, or schema explanations."
        ),
        "empty_result_contract": (
            "If no source-supported top entity exists, return an empty response. "
            "Do not emit an explanatory sentence."
        ),
    }
