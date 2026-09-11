"""Unused pipeline formatters preserved after the package split.

Do not add new callers. Active renderers live in `formatters.py`.
See pipeline/README.md.
"""

from __future__ import annotations

import re
from typing import Any

from src.extraction_prompt_generation.compile.context import (
    AgenticGenerationContext,
)
from src.extraction_prompt_generation.compile.operation_units import (
    owned_entity_tool_contracts as _owned_entity_tool_contracts,
)
from src.extraction_prompt_generation.compile.reuse_policy import (
    existing_entity_check_contracts,
)
from src.extraction_prompt_generation.pipeline.formatters import (
    _generated_create_tool_fields,
    _ordered_member_classes,
    _ordering_datatype_properties,
    _prompt_class_is_filtered,
    _py_name,
)


def _namespace_uri(context: AgenticGenerationContext) -> str:
    ns = str(context.contract.get("namespace_uri") or "").strip()
    if ns:
        return ns
    classes = context.parsed.get("classes") or {}
    for cls in classes.values():
        iri = str((cls or {}).get("iri") or "")
        if iri:
            return iri.rstrip("/#").rsplit("/", 1)[0] + "/"
    from src.extraction_prompt_generation.config.namespace import load_namespace_config

    return load_namespace_config()["generated_graph_iri"]


def _normalized_symbol(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(text or "").lower())


def _predicate_target_stem(predicate_local: str) -> str:
    text = str(predicate_local or "").strip()
    for prefix in ("has", "is"):
        if text.startswith(prefix) and len(text) > len(prefix):
            return text[len(prefix) :]
    return text


def _format_create_tool_contract(
    context: AgenticGenerationContext,
    *,
    allowed_classes: set[str] | None = None,
) -> str:
    rows: list[str] = []
    for class_local in sorted((context.parsed.get("classes") or {}).keys()):
        if allowed_classes is not None and class_local not in allowed_classes:
            continue
        fields = _generated_create_tool_fields(context, class_local)
        if _prompt_class_is_filtered(context, class_local) and fields == ["label"]:
            continue
        rows.append(
            f"- `create_{_py_name(class_local)}` accepts only: "
            + ", ".join(f"`{field}`" for field in fields)
        )
    return "\n".join(rows)


def _format_atomic_operation_contract(
    context: AgenticGenerationContext,
    *,
    allowed_classes: set[str] | None = None,
) -> str:
    rows: list[str] = []
    for creator in _owned_entity_tool_contracts(context):
        class_local = str(creator.get("class_local") or "")
        if allowed_classes is not None and class_local not in allowed_classes:
            continue
        tool_name = str(creator.get("public_tool") or "")
        for edge in creator.get("required_edges") or []:
            predicate = str(edge.get("predicate_local") or "")
            if edge.get("target_resolution") == "existing_iri_parameter":
                parameter = str(edge.get("parameter_name") or "")
                rows.append(
                    f"- `{tool_name}` owns `{predicate}`: pass the existing scoped parent "
                    f"IRI as `{parameter}` in the creator call. The creator writes this edge; "
                    f"never call `add_{predicate}`."
                )
            elif edge.get("target_resolution") == "same_operation_create":
                label_parameter = str(edge.get("label_parameter") or "")
                datatype_parameters = [
                    str(item.get("parameter_name") or "")
                    for item in edge.get("datatype_inputs") or []
                    if str(item.get("parameter_name") or "")
                ]
                parameter_text = ", ".join(
                    f"`{value}`"
                    for value in [label_parameter, *datatype_parameters]
                    if value
                )
                rows.append(
                    f"- `{tool_name}` owns `{predicate}` and its fresh "
                    f"`{edge.get('dependent_class_local')}` target: pass the target data through "
                    f"{parameter_text or 'the declared owned-dependent parameters'} in the same "
                    f"creator call. Do not call a second creator or `add_{predicate}` for it."
                )
    return (
        "\n".join(rows)
        if rows
        else "- No creator-owned object-property edges are declared for this iteration."
    )


def _format_ordered_member_contract(context: AgenticGenerationContext) -> str:
    profile = context.contract.get("ordered_member_profile") or {}
    ordered_classes = sorted(_ordered_member_classes(context))
    ordering_props = sorted(_ordering_datatype_properties(context))
    member_props = sorted(
        str(x)
        for x in profile.get("individually_linked_object_properties", []) or []
        if str(x).strip()
    )
    subclass_targets = profile.get("most_specific_subclass_targets") or {}
    lines: list[str] = []
    if ordered_classes:
        lines.append(
            f"- Ordered member classes from T-Box: {', '.join(f'`{x}`' for x in ordered_classes)}."
        )
    if ordering_props:
        lines.append(
            f"- Ordering scalar properties from T-Box: {', '.join(f'`{x}`' for x in ordering_props)}."
        )
        lines.append(
            "- Ordering scalar values must be unique positive integers starting at 1, with no duplicates, gaps, zero, negative, decimal, fractional, or between-step values; renumber ordered members as consecutive integers in source order when inserting or refining steps."
        )
    if member_props:
        lines.append(
            f"- Parent-to-member link properties from T-Box: {', '.join(f'`{x}`' for x in member_props)}."
        )
    for parent, children in sorted(subclass_targets.items()):
        child_list = ", ".join(f"`{x}`" for x in sorted(children or []))
        if child_list:
            lines.append(
                f"- When evidence supports a specific subclass of `{parent}`, create that subclass and preserve the `{parent}` type assertion."
            )
    if not lines:
        return "- No ordered-member integrity contract declared in the T-Box."
    atomic_members: dict[str, tuple[str, str, str]] = {}
    for creator in _owned_entity_tool_contracts(context):
        class_local = str(creator.get("class_local") or "")
        for edge in creator.get("required_edges") or []:
            if edge.get("role") != "container_membership":
                continue
            atomic_members[class_local] = (
                str(creator.get("public_tool") or ""),
                str(edge.get("predicate_local") or ""),
                str(edge.get("parameter_name") or ""),
            )
    if atomic_members:
        for class_local, (tool_name, predicate, parameter) in sorted(
            atomic_members.items()
        ):
            lines.append(
                f"- For each hinted `{class_local}`, call `{tool_name}` with its positive-integer "
                f"ordering value and the scoped parent IRI in `{parameter}`. This atomic creator "
                f"writes `{predicate}`; never follow it with `add_{predicate}`."
            )
        standalone_classes = sorted(set(ordered_classes) - set(atomic_members))
        if standalone_classes:
            merged_predicates = {
                str(value)
                for value in (
                    (
                        context.contract.get("materialization_operation_units") or {}
                    ).get("merged_predicate_locals")
                    or []
                )
                if str(value)
            }
            if merged_predicates.intersection(member_props):
                lines.append(
                    "- Ordered classes without a creator-owned membership edge "
                    f"({', '.join(f'`{value}`' for value in standalone_classes)}) are not "
                    "materializable through this closed atomic surface; do not create them or "
                    "invent a removed parent-to-member tool."
                )
            else:
                lines.append(
                    "- For ordered classes without a creator-owned membership edge "
                    f"({', '.join(f'`{value}`' for value in standalone_classes)}), use the exposed "
                    "parent-to-member relationship tool after creation."
                )
    else:
        lines.append(
            "- For every hinted ordered member, call the specific `create_*` tool with its positive-integer ordering scalar value, then link it individually to the scoped top entity using the configured parent-to-member link."
        )
    lines.append(
        "- Label ordered members deterministically as the current entity label, class local name, and order value when the hint does not provide a better source label."
    )
    lines.append(
        "- Do not satisfy an ordered-member required link by reusing a shell or placeholder member from another top entity."
    )
    return "\n".join(lines)


def _format_reuse_tool_contract(
    context: AgenticGenerationContext,
    *,
    allowed_classes: set[str] | None = None,
) -> str:
    checks = existing_entity_check_contracts(
        parsed=context.parsed,
        contract=context.contract,
    )
    if allowed_classes is not None:
        checks = [
            item
            for item in checks
            if str(item.get("class_local") or "").strip() in allowed_classes
        ]
    if not checks:
        return (
            "- No class exposes an existing-entity lookup in this generated tool surface."
        )
    lines = [
        "- Existing-entity lookup and generic reuse are separate permissions.",
        "- Checks with `lookup_scope=central` inspect only ontology-wide candidates; checks with `lookup_scope=document` inspect only the current DOI's document memory. Pass the complete proposed entity hint as `proposed_entity_json`; an independent LLM identity judge evaluates every visible candidate.",
        "- A central or document check returns only candidates authorized by that judge. Each returned candidate includes a scope-bound `reuse_authorization_token`; pass that exact token to every `add_*` call that uses the returned IRI.",
        "- Class-level eligibility never authorizes a particular candidate by itself. If the judge rejects, fails, or returns no candidate, create a new entity.",
        "- Checks with `lookup_scope=scoped` are for non-reusable occurrence classes. They inspect only the current retained scoped graph and may resolve an exact occurrence created in an earlier iteration.",
        "- A scoped check never authorizes deduplication, cross-occurrence reuse, cross-top-entity reuse, or cross-document reuse. Its returned IRI may only be referenced when all occurrence identity details match the current hint.",
        "- Every check returns structured JSON containing `iri`, `labels`, `types`, "
        "`datatype_values`, `outgoing_relations`, `incoming_relations`, lookup metadata, and central provenance when applicable.",
    ]
    for item in checks:
        if item.get("reuse_authorized"):
            lines.append(
                f"- `{item['class_local']}`: call `{item['public_tool']}` first; "
                f"pass the proposed hint JSON; lookup_scope=`{item['lookup_scope']}`; candidate reuse "
                f"requires an LLM authorization token under policy scope=`{item['reuse_scope']}`; "
                f"match basis: {item['match_basis']}"
            )
        else:
            lines.append(
                f"- `{item['class_local']}`: call `{item['public_tool']}` for exact "
                f"occurrence reference resolution; lookup_scope=`scoped`; generic reuse "
                f"forbidden; occurrence basis: {item['match_basis']}"
            )
    return "\n".join(lines)
