"""Deterministic occurrence-surface prompt templates. Do not edit by hand."""

from __future__ import annotations

import json
from pathlib import Path
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
from src.extraction_prompt_generation.generate.extraction_prompts.contracts import (
    _prompt_tbox_slice,
    _subclass_decision_contract,
    _write_materializable_prompt_component,
)
from src.extraction_prompt_generation.paths import package_prompt_path
from src.extraction_prompt_generation.pipeline.plan import (
    _iteration_plan as _compiled_iteration_plan,
)
from src.extraction_prompt_generation.pipeline.runtime_support import (
    generate_runtime_support_slice,
)
from src.kg_building_mcp_generation_v2.emit.creation_helpers import (
    _class_ancestors,
    _ordered_member_classes,
    _ordering_datatype_properties,
    _py_name,
    _step_scoped_object_properties_for_class,
)


def _configured_prompt_addon(context: AgenticGenerationContext) -> str:
    return ""


def _occurrence_iteration_plan(context: AgenticGenerationContext) -> dict[str, Any]:
    """Reuse the cleaned iteration projector and attach KG-building prompt paths."""
    plan = _compiled_iteration_plan(context)
    for iteration in plan.get("iterations") or []:
        if not isinstance(iteration, dict):
            continue
        iter_num = iteration.get("iteration_number")
        if not iter_num:
            continue
        ontology = context.ontology.name
        iteration["kg_building_prompt"] = package_prompt_path(
            ontology, f"KG_BUILDING_ITER_{iter_num}.md"
        )
        if context.ontology.role != "extension":
            try:
                number = int(iter_num)
            except (TypeError, ValueError):
                number = 0
            if number >= 2:
                iteration["kg_building_onepass_prompt"] = package_prompt_path(
                    ontology, f"KG_BUILDING_ITER_{iter_num}_ONEPASS.md"
                )
    return plan


def _format_property_rows(context: AgenticGenerationContext, *, kind: str) -> str:
    rows: list[str] = []
    for name, prop in sorted((context.parsed.get("properties") or {}).items()):
        if (prop or {}).get("kind") != kind:
            continue
        if not _prompt_includes_property(context, name, prop or {}, kind=kind):
            continue
        domains = ", ".join(str(x) for x in ((prop or {}).get("domains") or []))
        rng = str((prop or {}).get("range") or "")
        comment = str((prop or {}).get("comment") or "").strip()
        value_kind = str((prop or {}).get("value_kind") or "").strip()
        row = f"- `{name}`: domains=[{domains}], range=`{rng}`"
        if value_kind:
            row += f", value_kind=`{value_kind}`"
        if comment:
            row += f"; comment={comment}"
        rows.append(row)
    return "\n".join(rows) if rows else "- None declared in the T-Box."

def _format_value_kind_priority_contract(context: AgenticGenerationContext) -> str:
    """Emit a T-Box-derived value-kind inventory and domain-agnostic priority rules."""
    properties = context.parsed.get("properties") or {}
    by_kind: dict[str, list[str]] = {}
    for name, prop in sorted(properties.items()):
        if (prop or {}).get("kind") != "datatype":
            continue
        if not _prompt_includes_property(context, name, prop or {}, kind="datatype"):
            continue
        value_kind = str((prop or {}).get("value_kind") or "").strip()
        if not value_kind:
            continue
        by_kind.setdefault(value_kind, []).append(name)

    if not by_kind:
        return ""

    lines = [
        "Value-Kind Priority Contract:",
        "- When the T-Box marks a datatype field with `value_kind`, treat that annotation as authoritative for extraction priority and value shape.",
        "- `binary_checklist` fields outrank `free_text_fallback` and ordinary free-text fields for the same source fact: if a binary/canonical field fits, emit that field and do not park the same fact only in a catch-all/free-text fallback.",
        "- For every `binary_checklist` field listed below, evaluate source evidence before returning JSON; emit the T-Box-configured active checklist value when supported, otherwise omit the field.",
        "- For every `free_text_fallback` field listed below, emit text only for explicit source items that no binary/canonical sibling field covers; do not use fallbacks to restate facts already captured by binary fields.",
        "- For `derived` fields, do not invent values from prose alone when the T-Box says they are computed from other extracted fields.",
        "- Do not skip emitting a class section merely because only binary checklist fields are supported for that class; if any binary field is source-supported, emit the class section with those fields.",
    ]
    preferred_order = [
        "binary_checklist",
        "free_text_fallback",
        "free_text",
        "derived",
    ]
    for kind in preferred_order:
        names = by_kind.get(kind) or []
        if not names:
            continue
        joined = ", ".join(f"`{name}`" for name in names)
        lines.append(f"- value_kind=`{kind}` fields: {joined}")
    for kind, names in sorted(by_kind.items()):
        if kind in preferred_order:
            continue
        joined = ", ".join(f"`{name}`" for name in names)
        lines.append(f"- value_kind=`{kind}` fields: {joined}")
    return "\n".join(lines)

def _prompt_field_allowlist(context: AgenticGenerationContext) -> dict[str, set[str]]:
    """T-Box fields are exhaustive; runtime config cannot hide semantic fields."""
    return {}

def _prompt_class_is_filtered(
    context: AgenticGenerationContext, class_local: str
) -> bool:
    return class_local in _prompt_field_allowlist(context)

def _prompt_field_allowed(
    context: AgenticGenerationContext, class_local: str, field: str
) -> bool:
    allowlist = _prompt_field_allowlist(context)
    if class_local not in allowlist:
        return True
    return field == "label" or field in allowlist[class_local]

def _filter_prompt_datatype_props(
    context: AgenticGenerationContext, class_local: str, props: set[str]
) -> set[str]:
    return {
        prop
        for prop in props
        if _prompt_field_allowed(context, class_local, _py_name(prop))
    }

def _filter_prompt_object_props(
    context: AgenticGenerationContext, class_local: str, props: set[str]
) -> set[str]:
    return {
        prop
        for prop in props
        if _prompt_field_allowed(context, class_local, f"{_py_name(prop)}_ref")
    }

def _prompt_includes_property(
    context: AgenticGenerationContext, name: str, prop: dict[str, Any], *, kind: str
) -> bool:
    allowlist = _prompt_field_allowlist(context)
    if not allowlist:
        return True
    domains = [str(x) for x in (prop.get("domains") or []) if str(x)]
    if kind == "datatype":
        field = _py_name(name)
        return any(_prompt_field_allowed(context, domain, field) for domain in domains)
    if kind == "object":
        field = f"{_py_name(name)}_ref"
        return any(_prompt_field_allowed(context, domain, field) for domain in domains)
    return True

def _format_class_rows(context: AgenticGenerationContext) -> str:
    rows: list[str] = []
    ordered_classes = _ordered_member_classes(context)
    ordering_props = _ordering_datatype_properties(context)
    for name, cls in sorted((context.parsed.get("classes") or {}).items()):
        data_prop_set = set(((cls or {}).get("datatype_properties") or {}).keys())
        if name in ordered_classes:
            data_prop_set.update(ordering_props)
        data_prop_set = _filter_prompt_datatype_props(context, name, data_prop_set)
        data_props = ", ".join(sorted(data_prop_set)) or "none"
        object_prop_set = set(((cls or {}).get("object_properties") or {}).keys())
        object_prop_set.update(_step_scoped_object_properties_for_class(context, name))
        object_prop_set = _filter_prompt_object_props(context, name, object_prop_set)
        if (
            _prompt_class_is_filtered(context, name)
            and not data_prop_set
            and not object_prop_set
        ):
            continue
        obj_props = ", ".join(sorted(object_prop_set)) or "none"
        parents = (
            ", ".join(_class_ancestors(context.parsed.get("classes") or {}, name))
            or "none"
        )
        comment = str((cls or {}).get("comment") or "").strip()
        row = f"- `{name}`: parents=[{parents}], datatype_properties=[{data_props}], object_properties=[{obj_props}]"
        if comment:
            row += f"; comment={comment}"
        rows.append(row)
    return "\n".join(rows)

def _tbox_comment_fidelity_contract() -> str:
    return """T-Box Comment Fidelity Contract:
- Treat every `comment=` value in the Classes and Properties, Datatype Properties, and Object Properties sections as a binding extraction rule and normative extraction constraint, not as background prose.
- When a class/property comment narrows evidence, defines normalization, gives positive/negative examples, or states conditional gates, apply those rules before emitting any field.
- A comment containing `【Warning】` marks a high-risk choice boundary. Before selecting or excluding any class or field governed by such a comment, compare the source against that complete comment and every applicable warning-marked alternative, including positive thresholds, exclusions, priority, and non-duplication rules.
- The warning marker changes attention only. Never infer a domain-specific trigger, example, priority, or exception from this generic instruction; all choice semantics must come from the marked T-Box comments.
- Treat negation, prevention, avoidance, risk-only, planned-but-not-performed, and rule-out contexts as negative evidence unless the T-Box comment explicitly says they count as positive evidence.
- If source evidence conflicts with or falls short of a T-Box comment requirement, omit the field rather than filling a plausible value."""

def _generated_create_tool_fields(
    context: AgenticGenerationContext, class_local: str
) -> list[str]:
    """Return fields owned by the class-specific atomic create tool."""
    creator = next(
        (
            item
            for item in _owned_entity_tool_contracts(context)
            if str(item.get("class_local") or "") == class_local
        ),
        None,
    )
    if creator is None:
        return _generated_owner_scalar_fields(context, class_local)
    fields = ["label"]
    fields.extend(
        str(item.get("property_local") or "")
        for item in creator.get("datatype_inputs") or []
        if str(item.get("property_local") or "")
    )
    for edge in creator.get("required_edges") or []:
        if edge.get("target_resolution") == "existing_iri_parameter":
            fields.append(str(edge.get("parameter_name") or ""))
        elif edge.get("target_resolution") == "same_operation_create":
            fields.append(str(edge.get("label_parameter") or ""))
            fields.extend(
                str(item.get("parameter_name") or "")
                for item in edge.get("datatype_inputs") or []
                if str(item.get("parameter_name") or "")
            )
    return list(dict.fromkeys(field for field in fields if field))

def _generated_owner_scalar_fields(
    context: AgenticGenerationContext, class_local: str
) -> list[str]:
    """Return only source-ledger fields, excluding graph-wiring parameters."""
    classes = context.parsed.get("classes") or {}
    cls = classes.get(class_local) or {}
    data_props = set((cls.get("datatype_properties") or {}).keys())
    if class_local in _ordered_member_classes(context):
        data_props.update(_ordering_datatype_properties(context))
    data_props = _filter_prompt_datatype_props(context, class_local, data_props)
    return ["label", *sorted(_py_name(prop) for prop in data_props)]

def _generated_hint_fields(
    context: AgenticGenerationContext, class_local: str
) -> list[str]:
    """Return extraction fields with stable refs and ref-only relationships."""
    classes = context.parsed.get("classes") or {}
    cls = classes.get(class_local) or {}
    fields = ["ref", *_generated_owner_scalar_fields(context, class_local)]
    required_by_domain: dict[str, list[dict[str, Any]]] = {}
    for spec in context.contract.get("required_step_scoped_object_properties") or []:
        domain = str((spec or {}).get("domain_local") or "").strip()
        if domain:
            required_by_domain.setdefault(domain, []).append(spec)
    required_prop_names = {
        str((spec or {}).get("predicate_local") or "").strip()
        for spec in required_by_domain.get(class_local, [])
    }

    object_props = {
        str(prop): str(range_local or "").strip()
        for prop, range_local in ((cls.get("object_properties") or {}).items())
        if str(prop).strip() and str(range_local or "").strip()
    }
    object_props.update(_step_scoped_object_properties_for_class(context, class_local))
    object_props = {
        prop: range_local
        for prop, range_local in object_props.items()
        if _prompt_field_allowed(context, class_local, f"{_py_name(prop)}_ref")
    }
    for prop in sorted(object_props):
        if prop not in required_prop_names:
            fields.append(f"{_py_name(prop)}_ref")
    for spec in required_by_domain.get(class_local, []):
        prop = str((spec or {}).get("predicate_local") or "target").strip()
        if prop:
            fields.append(f"{_py_name(prop)}_ref")
    return list(dict.fromkeys(fields))

def _format_subclass_comment_contract(
    context: AgenticGenerationContext,
    *,
    allowed_classes: set[str] | None = None,
) -> str:
    """Render subclass comments directly from the parsed T-Box."""
    rows: list[str] = []
    classes = context.parsed.get("classes") or {}
    for class_local, cls in sorted(classes.items()):
        if allowed_classes is not None and class_local not in allowed_classes:
            continue
        parent_classes = sorted(
            {
                str(parent).strip()
                for parent in (cls or {}).get("parent_classes") or []
                if str(parent).strip() in classes
            }
        )
        comment = str((cls or {}).get("comment") or "").strip()
        if parent_classes and comment:
            rows.append(
                f"- Subclass comment for `{class_local}` "
                f"(parents: {', '.join(f'`{parent}`' for parent in parent_classes)}): "
                + comment
            )
    return "\n".join(rows)

def _format_materializable_hint_contract(
    context: AgenticGenerationContext,
    *,
    allowed_classes: set[str] | None = None,
    allowed_object_properties: set[str] | None = None,
    lexical_object_properties: set[str] | None = None,
) -> str:
    rows: list[str] = []
    emitted_relations: set[tuple[str, str, str]] = set()
    lexical_properties = set(lexical_object_properties or set())
    merged_properties = {
        str(value)
        for value in (
            (
                context.contract.get("materialization_operation_units") or {}
            ).get("merged_predicate_locals")
            or []
        )
        if str(value)
    }
    for class_local in sorted((context.parsed.get("classes") or {}).keys()):
        if allowed_classes is not None and class_local not in allowed_classes:
            continue
        classes = context.parsed.get("classes") or {}
        cls = classes.get(class_local) or {}
        object_props = {
            str(prop): str(range_local or "").strip()
            for prop, range_local in ((cls.get("object_properties") or {}).items())
            if str(prop).strip() and str(range_local or "").strip()
        }
        object_props.update(
            _step_scoped_object_properties_for_class(context, class_local)
        )
        datatype_fields = [
            field
            for field in _generated_owner_scalar_fields(context, class_local)
            if field != "label"
        ]
        datatype_fields.extend(
            prop for prop in sorted(object_props) if prop in lexical_properties
        )
        datatype_fields = list(dict.fromkeys(datatype_fields))
        if _prompt_class_is_filtered(context, class_local) and not datatype_fields:
            continue
        fields = (
            ", ".join(f"`{field}`" for field in datatype_fields)
            or "none"
        )
        rows.append(
            f"- Entity class `{class_local}` -> `datatype_properties` accepts: {fields}"
        )
        property_contracts = context.parsed.get("properties") or {}
        for field in datatype_fields:
            comment = str(
                (property_contracts.get(field) or {}).get("comment") or ""
            ).strip()
            if comment:
                rows.append(
                    f"  - Field `{field}` semantic contract: {comment}"
                )
        for prop, range_local in sorted(object_props.items()):
            if prop in lexical_properties or prop in merged_properties:
                continue
            if (
                allowed_object_properties is not None
                and prop not in allowed_object_properties
            ):
                continue
            emitted_relations.add((prop, class_local, range_local))
            rows.append(
                f"- Relation `{prop}`: `subject_ref` class `{class_local}` -> "
                f"`object_ref` class `{range_local}`"
            )
    if allowed_object_properties is not None:
        top_entity_local = str(
            (context.contract.get("top_entity") or {}).get("class_local") or ""
        ).strip()
        allowed_subject_classes = (
            set(allowed_classes or set()) | ({top_entity_local} if top_entity_local else set())
            if allowed_classes is not None
            else None
        )
        for prop in sorted(allowed_object_properties):
            if prop in lexical_properties or prop in merged_properties:
                continue
            spec = (context.parsed.get("properties") or {}).get(prop) or {}
            if str(spec.get("kind") or "") != "object":
                continue
            range_local = str(spec.get("range") or "").strip()
            domains = [
                str(value).strip()
                for value in (spec.get("domains") or [spec.get("domain")])
                if str(value or "").strip()
            ]
            for domain_local in domains:
                if (
                    allowed_subject_classes is not None
                    and domain_local not in allowed_subject_classes
                ):
                    continue
                relation = (prop, domain_local, range_local)
                if relation in emitted_relations:
                    continue
                emitted_relations.add(relation)
                rows.append(
                    f"- Relation `{prop}`: `subject_ref` class `{domain_local}` -> "
                    f"`object_ref` class `{range_local}`"
                )
    integrity_contract = _format_subclass_comment_contract(
        context,
        allowed_classes=allowed_classes,
    )
    if integrity_contract:
        rows.extend(integrity_contract.splitlines())
    return "\n".join(rows)

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

def _format_linked_target_scalar_contract(context: AgenticGenerationContext) -> str:
    classes = context.parsed.get("classes") or {}
    rows: list[str] = []
    for source_class, cls in sorted(classes.items()):
        object_props = {
            str(prop): str(range_local or "").strip()
            for prop, range_local in (
                (cls or {}).get("object_properties") or {}
            ).items()
            if str(prop).strip() and str(range_local or "").strip()
        }
        object_props.update(
            _step_scoped_object_properties_for_class(context, source_class)
        )
        for prop, target_class in sorted(object_props.items()):
            target_fields = [
                field
                for field in _generated_create_tool_fields(context, target_class)
                if field != "label" and not field.endswith("_ref")
            ]
            if target_fields:
                rows.append(
                    f"- `{source_class}.{_py_name(prop)}_ref` links to `{target_class}`; "
                    f"if source text states scalar attributes for that linked target, also emit a `{target_class}` object "
                    f"with the exact referenced `ref`, a canonical `label`, and supported scalar fields: {', '.join(f'`{field}`' for field in target_fields)}."
                )
    if not rows:
        return "- No object-reference targets with scalar fields are declared in the generated tool contract."
    return "\n".join(rows)

def _format_required_step_scoped_object_contract(
    context: AgenticGenerationContext,
) -> str:
    rows: list[str] = []
    merged_properties = {
        str(value)
        for value in (
            (
                context.contract.get("materialization_operation_units") or {}
            ).get("merged_predicate_locals")
            or []
        )
        if str(value)
    }
    for spec in context.contract.get("required_step_scoped_object_properties") or []:
        domain = str((spec or {}).get("domain_local") or "").strip()
        prop = str((spec or {}).get("predicate_local") or "").strip()
        target = str((spec or {}).get("range_local") or "").strip()
        if domain and prop and target and prop not in merged_properties:
            rows.append(
                f"- `{domain}` requires `{_py_name(prop)}_ref` linking to `{target}`. "
                f"When an extracted `{domain}` names a source-supported `{target}` already present in the hints, "
                f"emit the exact stable `{target}` ref in `{_py_name(prop)}_ref` so identity and scalar fields remain separate."
            )
    if not rows:
        return (
            "- No required ordered-member object-reference links are declared in the T-Box."
        )
    return "\n".join(rows)

def _format_mutually_exclusive_property_contract(
    context: AgenticGenerationContext,
) -> str:
    return ""


def _format_required_links(context: AgenticGenerationContext) -> str:
    rows: list[str] = []
    for spec in context.contract.get("required_links") or []:
        pred = (
            str((spec or {}).get("predicate_iri") or "").rstrip("/#").rsplit("/", 1)[-1]
        )
        target = (
            str((spec or {}).get("target_class_iri") or "")
            .rstrip("/#")
            .rsplit("/", 1)[-1]
        )
        min_count = (spec or {}).get("min_count", 1)
        rows.append(f"- `{pred}` -> `{target}` with min_count={min_count}")
    return (
        "\n".join(rows)
        if rows
        else "- No required top-level links declared in the runtime contract."
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

def _top_entity_selection_contract(context: AgenticGenerationContext) -> str:
    top = context.contract.get("top_entity") or {}
    top_local = str(top.get("class_local") or "Entity").strip() or "Entity"
    top_class = (context.parsed.get("classes") or {}).get(top_local) or {}
    top_comment = str((top_class or {}).get("comment") or "").strip()
    rules = [
        f"- Candidate labels must satisfy the T-Box class comment for `{top_local}`.",
        "- Apply the class-level inclusion/exclusion rules before accepting headings, captions, tables, or SI procedure titles.",
        "- If a candidate is disallowed by the T-Box comment or runtime policy, omit it even when the source contains an explicit procedure-like heading.",
        "- If the source is ambiguous under the T-Box rules, omit the candidate rather than expanding recall.",
        f"- Never output the runtime context label `top`, the class local name `{top_local}`, or generated shell labels such as `{top_local}-1` inside brackets; the bracket value must be the source-supported entity identifier, e.g. `{top_local}-1 [UMC-1]`.",
    ]
    if top_comment:
        rules.append(f"- T-Box class comment for `{top_local}`: {top_comment}")
    return "\n".join(rules)

def _extraction_prompt(context: AgenticGenerationContext) -> str:
    top = context.contract.get("top_entity") or {}
    top_local = str(top.get("class_local") or "Entity").strip() or "Entity"
    return f"""# Extraction Prompt: {context.ontology.name}

Task:
Extract only information explicitly supported by the source document for the selected ontology.

Ontology Source:
- T-Box TTL: `{context.ontology.ttl_file}`
- Top entity class: `{top.get("class_local") or ""}`

Source Document:
{{paper_content}}

Core Rules:
- Do not infer missing values.
- Preserve source wording for labels and scalar values.
- Use only classes, properties, and constraints derived from the T-Box.
- If evidence is ambiguous, omit the field rather than guessing.

Top-Entity Selection Contract:
{_top_entity_selection_contract(context)}

{_tbox_comment_fidelity_contract()}

Classes and Properties:
{_format_class_rows(context)}

Datatype Properties:
{_format_property_rows(context, kind="datatype")}

Object Properties:
{_format_property_rows(context, kind="object")}

Required Top-Level Links:
{_format_required_links(context)}

Output:
Return only normalized top-entity lines, with no JSON, markdown fences, bullets, or explanatory text.
Each line must use this exact shape:
{top_local}-1 [source-supported label]

If multiple top entities are explicitly present, increment the number:
{top_local}-2 [source-supported label]

The label inside brackets should be the shortest stable source-supported identifier for the selected top entity.
"""

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

def _format_reusable_label_contract(context: AgenticGenerationContext) -> str:
    checks = existing_entity_check_contracts(
        parsed=context.parsed,
        contract=context.contract,
    )
    class_locals = sorted(
        {
            str(item.get("class_local") or "").strip()
            for item in checks
            if item.get("reuse_authorized") is True
            if str(item.get("class_local") or "").strip()
        }
    )
    if not class_locals:
        return "- No class is authorized for generic reuse."
    return "\n".join(
        [
            "- Reusable classes: " + ", ".join(f"`{name}`" for name in class_locals) + ".",
            "- For every entity of a reusable class, use the shortest stable source-supported identity label. The label must be independent of the Current Target Entity.",
            "- Never add the Current Target Entity label as a prefix, suffix, parenthetical qualifier, or phrase such as `for <Current Target Entity>` merely to encode processing scope.",
            "- Represent document/top-entity scope only through object-property links, runtime provenance, and the entity IRI; never encode scope by changing a reusable entity's canonical label.",
            "- This label rule does not authorize reuse by label. Reuse requires a positive independent LLM pair judgement and its runtime authorization token.",
        ]
    )

def _kg_prompt(context: AgenticGenerationContext) -> str:
    top = context.contract.get("top_entity") or {}
    top_local = str(top.get("class_local") or "").strip()
    return f"""# KG Building Prompt: {context.ontology.name} Iteration 1

Task:
Export the pipeline-seeded top-entity A-Box scope without creating a root.

Runtime Inputs:
- Document identifier: `{{doi}}`
- Source document: `{{paper_content}}`
- Extracted top entities/hints: `{{top_entities}}`

Identity Contract:
- The Iteration-1 identity lock/dossier is the sole authority for `{top_local}` identity.
- First call `init_memory(doi, top_level_entity_name)` using the orchestrator-supplied scope.
- Bind each source-supported bracketed label to the exact locked URI restored by `init_memory`.
- Never call a top-root creator, mint a replacement URI, deduplicate locked scopes, or retype a locked root.
- If a hinted label is absent from the lock/dossier, report an upstream identity blocker.
- Attach only source-supported facts explicitly allowed by the active T-Box and this pass when such an exposed tool is available.
- Finally call `export_memory(doi, top_level_entity_name)` with the same scope. Do not call another tool afterward.
- Do not claim success unless export returns non-empty scoped A-Box Turtle.

Ontology Source:
- T-Box TTL: `{context.ontology.ttl_file}`
- Locked top entity class: `{top_local}`
"""
    required_tool_lines: list[str] = []
    if context.ontology.role == "main" and top_local:
        required_tool_lines.append(
            f"- Top-entity KG pass only: bind each `{top_local}` label to the exact URI "
            "already seeded in the pipeline identity lock; do not call a top-root creator."
        )
    else:
        for spec in context.contract.get("required_links") or []:
            pred = (
                str((spec or {}).get("predicate_iri") or "")
                .rstrip("/#")
                .rsplit("/", 1)[-1]
            )
            target = (
                str((spec or {}).get("target_class_iri") or "")
                .rstrip("/#")
                .rsplit("/", 1)[-1]
            )
            if bool((spec or {}).get("ordered_member")):
                continue
            if pred and target:
                required_tool_lines.append(
                    f"- Only when the extraction hints contain a concrete source-supported `{target}` object, call `create_{_py_name(target)}` for its label, then call `add_{_py_name(pred)}` from the `{top_local}` IRI to that target IRI."
                )
    required_tool_text = (
        "\n".join(required_tool_lines)
        if required_tool_lines
        else "- No required link tools are declared."
    )
    integrity = json.dumps(context.integrity_profile, indent=2, ensure_ascii=False)
    top_tool_rules = (
        f"- Bind every source-supported `{top_local}` label to its exact URI in the "
        "pipeline-seeded identity lock/dossier. Never call a top-root creator, mint a "
        "replacement, or retype the locked root."
        if top_local
        else "- The T-Box declares no machine-readable top role; do not guess or create one."
    )
    reuse_tool_contract = _format_reuse_tool_contract(context)
    return f"""# KG Building Prompt: {context.ontology.name}

Task:
Create RDF triples using the generated MCP tools for this ontology only.

Runtime Inputs:
- Document identifier: `{{doi}}`
- Source document: `{{paper_content}}`
- Extracted top entities/hints: `{{top_entities}}`

Ontology Source:
- T-Box TTL: `{context.ontology.ttl_file}`
- Top entity class: `{top.get("class_local") or ""}`

Tool-Use Rules:
- You must call tools. A prose-only answer is a failed run.
- First call `init_memory(doi, top_level_entity_name)` with the current document identifier and configured top-level entity context.
{top_tool_rules}
- Reuse the scoped top entity when the contract says top-entity reuse is required.
- Maintain a runtime map from every hint `ref` to exactly one materialized IRI.
- For reusable classes, call the class's central `check_existing_*` tool with the complete proposed entity hint serialized as JSON. Reuse only a returned LLM-authorized candidate, and preserve its `reuse_authorization_token` for relationship calls.
- Whenever an `add_*` relation uses a central candidate IRI, pass that candidate's `reuse_authorization_token`; an absent, mismatched, or stale token is a hard rejection.
- For non-reusable classes, call the class's scoped `check_existing_*` tool only when a ref denotes an exact occurrence already created in this scoped run. A result may resolve that prior ref for linking; it never authorizes deduplicating a newly declared occurrence.
- A matching label alone never proves either reusable identity or occurrence identity.
- In the top-entity KG pass, do not create placeholder/shell targets for required links. In particular, do not create generic ordered-member targets just to satisfy a required link; later per-entity iterations must create source-supported members with concrete subclasses and ordering values.
- Do not materialize generic ordered-member parent hints as placeholder members when specific ordered-member subclasses exist in the T-Box.
- Do not create two ordered-member individuals for the same source operation, label, or ordering value; choose the single most specific class supported by the T-Box class comments.
- Do not export duplicate same-class labels for the same extracted entity, and do not leave unreachable typed nodes outside the graph reachable from the scoped top entity.
- Create one RDF individual per extracted entity unless the ontology-derived integrity profile says reuse is allowed.
- Add object-property links only when both subject and object are supported by extracted evidence.
- Hint `ref`, `class`, and relation fields are orchestration metadata; never pass them to a `create_*` tool.
- A `create_*` tool creates exactly one individual and accepts only that class's label and datatype fields. It never creates or links an object-property target.
- Materialize entity records before relation records. Pass each entity's `label` plus `datatype_properties` to its class creator and bind the returned IRI to its `ref`.
- For each relation, resolve `subject_ref` and `object_ref` through that map, then call the matching `add_*` tool.
- Never derive datatype values from a label or ref.
- If a referenced target has no source-supported companion entity and no eligible existing instance, do not invent it merely to satisfy a relationship.
- Never alter a canonical label to encode the scoped top entity or any numerical/context payload.
- Enforce every configured mutually exclusive property group before materializing hints; never assert more than one active property from the same group on one entity instance.
- Before export, ensure only source-supported required top-level links below are present; absence of a concrete target in the extraction hints is not permission to invent one.
- Do not introduce classes or properties that are not present in the T-Box context.
- Finish by calling `export_memory(doi, top_level_entity_name)` with the same scope; do not claim success until this tool returns successfully.

Required Tool Sequence:
{required_tool_text}

Existing Entity Lookup Contract:
{reuse_tool_contract}

Required Top-Level Links:
{_format_required_links(context)}

Materializable Hint Contract:
{_format_materializable_hint_contract(context)}

Atomic Create Tool Contract:
{_format_create_tool_contract(context)}

Required Ordered-Member Object-Link Contract:
{_format_required_step_scoped_object_contract(context)}

{_format_mutually_exclusive_property_contract(context)}
Ontology-Derived Integrity Profile:
```json
{integrity}
```
{_configured_prompt_addon(context)}
Export:
After entity creation and linking, call the export tool and ensure the emitted Turtle parses successfully.
"""

def _iteration_extraction_prompt(
    context: AgenticGenerationContext, iteration: dict[str, Any]
) -> str:
    responsibilities = iteration.get("responsibilities") or {}
    owned_classes = ", ".join(
        str(value)
        for value in responsibilities.get("classes") or []
        if str(value).strip()
    ) or "none"
    owned_properties = ", ".join(
        str(value)
        for value in responsibilities.get("object_properties") or []
        if str(value).strip()
    ) or "none"
    external_tools = {
        str(name).strip()
        for name in (
            iteration.get("extraction_mcp_tools") or iteration.get("mcp_tools") or []
        )
        if str(name).strip()
    }
    enrichment_profile = (
        iteration.get("external_enrichment_profile")
        if isinstance(iteration.get("external_enrichment_profile"), dict)
        else {}
    )
    allowed_classes = set((context.parsed.get("classes") or {}).keys())
    allowed_properties = set((context.parsed.get("properties") or {}).keys())
    target_class = str(enrichment_profile.get("target_class") or "").strip()
    field_map = {
        str(source): str(target)
        for source, target in (enrichment_profile.get("field_map") or {}).items()
        if str(source).strip()
        and str(target).strip()
        and str(target) in allowed_properties
    }
    enrichment_provider = str(enrichment_profile.get("provider") or "").strip()
    enrichment_lines: list[str] = []
    if (
        enrichment_provider
        and enrichment_provider in external_tools
        and target_class in allowed_classes
        and field_map
    ):
        enrichment_lines = [
            "External enrichment contract:",
            f"- Use `{enrichment_provider}` only for source-supported `{target_class}` identities.",
            "- Preserve the source label and map provider values only to these T-Box fields:",
            *(
                f"  - `{source}` -> `{target}`"
                for source, target in sorted(field_map.items())
            ),
            "- External results may enrich an existing source-supported identity, but never prove participation or create a new source fact.",
        ]
    external_enrichment = "\n".join(enrichment_lines)
    reusable_label_contract = _format_reusable_label_contract(context)
    if str(iteration.get("hint_representation") or "").strip() == "semantic-text.v1":
        from src.extraction_prompt_generation.generate.extraction_prompts.contracts import (
            _semantic_text_natural_ledger_rules,
        )

        ledger_rules = "\n".join(
            f"- {rule}" for rule in _semantic_text_natural_ledger_rules()
        )
        return f"""# Extraction Prompt: {context.ontology.name} Iteration {iteration.get("iteration_number")}

Task:
Extract source-supported semantic-text.v1 hints for this iteration only.

Iteration Scope:
- Name: {iteration.get("name") or ""}
- Description: {iteration.get("description") or ""}
- Stage-owned classes: {owned_classes}
- Stage-owned object properties: {owned_properties}

Current Target Entity:
- Label: {{entity_label}}
- IRI: {{entity_uri}}

Rules:
- Return only a natural-language ledger headed exactly `SEMANTIC_HINTS_V1`.
- Do not emit JSON, RDF, refs, IRIs, endpoint IDs, quantity nodes, tool calls, or graph layout.
{ledger_rules}
- Preserve exact source quantity lexemes in the owning occurrence, either in the prose or as `<predicate_local>: <lexeme>`.
- Leave entity identity resolution and graph construction to the KG-building agent.
- Treat accumulated prior hints as read-only context, not a completeness mask for this iteration.

{external_enrichment}
T-Box-Derived Subclass Integrity Contract:
{_format_subclass_comment_contract(context)}

Reusable Entity Label Contract:
{reusable_label_contract}

{_tbox_comment_fidelity_contract()}

Source:
{{paper_content}}
"""
    return f"""# Extraction Prompt: {context.ontology.name} Iteration {iteration.get("iteration_number")}

Task:
Extract source-supported hints for this iteration only.

Iteration Scope:
- Name: {iteration.get("name") or ""}
- Description: {iteration.get("description") or ""}
- Stage-owned classes: {owned_classes}
- Stage-owned object properties: {owned_properties}

Current Target Entity:
- Label: {{entity_label}}
- IRI: {{entity_uri}}

Rules:
- Use only the source text and the ontology-derived class/property context below.
- Treat every T-Box `comment=` value below as a binding extraction rule for the corresponding class or property.
- Extract hints for the current target entity only.
- Treat the pipeline-injected accumulated prior hints as a read-only semantic identity registry.
- Every semantic entity must have one stable `ref`. Reuse the exact prior `ref`, class, and canonical label when a new stage-owned relation references an entity already present in accumulated prior hints.
- Do not re-emit a prior semantic entity under a new `ref` or altered label, and do not repeat a prior relation.
- Emit entity records only for stage-owned classes, except for the minimal Current Target Entity identity record required to attach new stage-owned relations.
- If a source fact is already represented in accumulated prior hints, omit it from this iteration rather than restating it.
- Prior existence never satisfies a new stage-owned property by itself. For every stage-owned property, compare the source with the accumulated hints and emit each source-supported property that is not already represented.
- When a new stage-owned property links two prior entities, emit only the relation record and reuse both exact prior refs. A ref-only relation is not a duplicate entity.
- Do not emit classes described as reserved, future, optional placeholders, or execution anchors unless the source explicitly contains an instance required by this iteration.
- Prefer source spans whose headings, labels, or local context match the current target entity label.
- Do not copy fields from another top entity when the current target entity has distinct source evidence.
- Before emitting JSON, find the class section whose `label` equals the Current Target Entity label; for that section, scan the Materializable Hint Contract and include every source-supported datatype field accepted by that class. Identifier values in lists or tables paired with the target label are source-supported evidence.
- Emit only fields that are both source-supported and listed in the Materializable Hint Contract below.
- Omit missing, uncertain, or unsupported fields.
- Treat structured source labels, table headers, bullet labels, and nearby section labels as strong evidence for matching datatype fields when their normalized words match the field local name, CSV-style display label, or T-Box comment wording.
- For explicit structured source sections, tables, or bullet lists, evaluate every listed item before returning JSON; do not stop after the first matching field in the section.
- If a T-Box comment contains an explicit convention, positive example, negative example, priority instruction, exception, or "do not" rule, apply that instruction literally and before using general keyword matching.
- If a property comment says a field is not supported by a source phrase, requires a more specific source condition, or prefers a sibling/canonical field for that phrase, follow that comment even when the phrase contains words that otherwise resemble the field name.
- If a property comment defines normalization examples or says which modifiers to keep or remove, emit the normalized value required by the comment rather than preserving the full source phrase.
- If a class exposes a catch-all, other, note, or free-text datatype field and its T-Box comment says to use it for explicit items not covered by canonical fields, collect all source-listed unmatched items for that class in that field.
- If the T-Box marks datatype fields with `value_kind=binary_checklist`, evaluate every such field for the relevant class against the source before returning JSON; binary/canonical checklist fields have higher priority than free-text or catch-all fallback fields for the same fact.
- If the T-Box marks datatype fields with `value_kind=free_text_fallback`, use them only for explicit source items that no binary/canonical sibling field covers; never use a fallback to replace or hide a source-supported binary field.
- When a linked class has only binary checklist fields supported by the source, still emit its entity record and the corresponding relation record; do not drop it because no free-text field was filled.
- For identification or demographic fields, inspect source header blocks and nearby header tables before the main body when the class or property comments say those fields come from identifying or administrative source regions.
- For short acronym-like datatype fields, if the exact acronym/token appears in source text in a relevant diagnosis, indication, observation, or field-value context and the T-Box comment says that token activates the field, emit the configured active checklist value for that field.
- If source evidence supports any datatype field for a class linked from the current top entity, emit both its entity record and the relation record from the top entity. After emitting one datatype field, scan the same structured source region for all other accepted fields of that class.
- Use exact class local names in each entity record's `class` field and exact property local names in each relation record's `property` field.
- Each entity record must separate `ref`, `class`, canonical `label`, and `datatype_properties`. Datatype values belong only inside `datatype_properties`.
- Emit a class section only when the current target label or a source-supported linked target actually denotes an instance of that class; do not coerce one semantic category into another merely because both classes are available.
- For ordered-member classes, ordering fields listed in the Materializable Hint Contract must be unique positive integers starting at 1. Do not emit duplicate, skipped, decimal, fractional, zero, negative, or between-step order values such as 1.5; renumber the ordered members as consecutive integers in source order.
- Do not emit the same source operation, label, or ordering value as two different ordered-member class sections; choose the single most specific class supported by the T-Box class comments.
- Do not emit a generic ordered-member parent class as a placeholder when source evidence supports one of its specific subclasses in the Materializable Hint Contract; emit the specific subclass section and its supported fields instead.
- If the T-Box declares an ordered-member class whose comment says it introduces a linked target object, emit one ordered member per source-supported linked target in source order.
- Labels are identity names only. Never append amounts, concentrations, durations, temperatures, roles, or other scalar/context payload to `label`; put those values in `datatype_properties`.
- Relations must use `subject_ref`, `property`, and `object_ref`; never use a label as an object reference. If the linked target is already present in prior hints, reuse its exact ref.
- For every entity class, never invent a scoped label such as `<source label> for <Current Target Entity>` or `<source label> (<Current Target Entity>)` unless that complete phrase is explicitly the entity's own name in the source. Contextual participation must be represented by links, not label text.
- A `ref` identifies exactly one semantic entity occurrence and must not be shared by different class instances. Repeated occurrences of a non-reusable class require different refs even when their canonical labels match.
- For ordered-member relations whose target has scalar fields, point to the same target ref whose entity record carries those scalar fields; never mint a second ref merely to transport values.
- For every required ordered-member object link declared below, emit a relation record when the source identifies a target object present in current or prior hints.
- For multiple linked targets, emit one relation record per target ref and one entity record per newly introduced target.
- If a linked target label also explicitly supplies a value accepted by a target scalar field, preserve that source-supported value in the scalar field.
- Follow object-property comments when they require source-supported links to target objects; do not omit a required link merely because another scalar field was filled.
- If a general parameter string contains a scalar value that has a dedicated generated field in the Materializable Hint Contract, also emit the dedicated field instead of leaving the scalar only inside the parameter string.
- For classes listed in the Mutually Exclusive Property Contract, emit at most one active property from each group for one entity instance. Use source evidence and T-Box comments to choose the best-supported one; omit the rest.
- When a T-Box comment distinguishes final, confirmed, provisional, preliminary, intermediate, or subordinate evidence, follow that evidence priority exactly; do not promote provisional or intermediate source statements into final/confirmed fields unless the source explicitly makes them final or confirmed.
- If the T-Box exposes a procedure-inheritance object field and the source says the current procedure follows the same, similar, or previous conditions as another source-supported procedure, treat the referenced procedure text as source-supported context for this target: carry over its ordered members and linked targets, then apply only the explicit modifications stated for the current target.
- Existing refs are not source evidence for adding new relations; emit a relation only when the Source text explicitly supports it.
- If evidence belongs to a more specific class whose generated tool lists the relevant field, emit that specific class instead of a generic parent/container class.
- Never emit schema placeholders or routing fields; use only the class sections and field names listed below.
- Output only a compact JSON object, with no markdown fences or explanatory prose.

{external_enrichment}
Reusable Entity Label Contract:
{reusable_label_contract}

Materializable Hint Contract:
{_format_materializable_hint_contract(context)}

{_format_value_kind_priority_contract(context)}

Linked Target Scalar Contract:
{_format_linked_target_scalar_contract(context)}

Required Ordered-Member Object-Link Contract:
{_format_required_step_scoped_object_contract(context)}

{_format_mutually_exclusive_property_contract(context)}
{_tbox_comment_fidelity_contract()}

Expected JSON Shape:
Hint Schema: ref-entity-relations.v1
- Return exactly one JSON object with top-level arrays `entities` and `relations`.
- Every entity is `{{"ref": "...", "class": "<exact class local>", "label": "<canonical identity label>", "datatype_properties": {{...}}}}`.
- Every relation is `{{"subject_ref": "...", "property": "<exact object-property local>", "object_ref": "..."}}`.
- Every ref used by a relation must resolve to exactly one entity in current or accumulated prior hints.
- Never encode a datatype value in `ref`, `label`, `subject_ref`, or `object_ref`.

Classes and Properties:
{_format_class_rows(context)}

Datatype Properties:
{_format_property_rows(context, kind="datatype")}

Object Properties:
{_format_property_rows(context, kind="object")}

{_configured_prompt_addon(context)}
Source:
{{paper_content}}
"""

def _iteration_kg_prompt(
    context: AgenticGenerationContext, iteration: dict[str, Any]
) -> str:
    top = context.contract.get("top_entity") or {}
    top_local = str(top.get("class_local") or "TopEntity").strip() or "TopEntity"
    semantic_scope = iteration.get("semantic_scope") or {}
    owned_classes = [
        str(item.get("local") or "").strip()
        for item in semantic_scope.get("classes") or []
        if isinstance(item, dict) and str(item.get("local") or "").strip()
    ] or [
        str(value).strip()
        for value in (iteration.get("responsibilities") or {}).get("classes") or []
        if str(value).strip()
    ]
    owned_object_properties = [
        str(item.get("local") or "").strip()
        for item in semantic_scope.get("object_properties") or []
        if isinstance(item, dict) and str(item.get("local") or "").strip()
    ] or [
        str(value).strip()
        for value in (iteration.get("responsibilities") or {}).get(
            "object_properties"
        )
        or []
        if str(value).strip()
    ]
    owned_class_set = set(owned_classes)
    owned_property_set = set(owned_object_properties)
    reuse_tool_contract = _format_reuse_tool_contract(
        context,
        allowed_classes=owned_class_set,
    )
    linked_classes = {
        str(value).strip()
        for value in iteration.get("linked_materialization_classes") or []
        if str(value).strip()
    }
    target_contract_lines: list[str] = []
    relationship_contracts = context.contract.get("relationship_tool_contracts") or {}
    merged_predicates = {
        str(value)
        for value in (
            (
                context.contract.get("materialization_operation_units") or {}
            ).get("merged_predicate_locals")
            or []
        )
    }
    for property_local in owned_object_properties:
        if property_local in merged_predicates:
            continue
        contract = relationship_contracts.get(property_local) or {}
        handling = str(contract.get("target_handling") or "untyped_existing_iri")
        creators = [
            str(value).strip()
            for value in contract.get("creator_tools") or []
            if str(value).strip()
        ]
        ranges = [
            str(value).strip()
            for value in (
                contract.get("materialization_target_locals")
                or contract.get("range_locals")
                or []
            )
            if str(value).strip()
        ]
        if handling == "fixed_runtime_creator":
            instruction = (
                "create the source-supported target with `create_om2_quantity`, then pass "
                "its returned IRI to the relationship tool"
            )
        elif creators:
            instruction = (
                "create a source-supported target with "
                + " or ".join(f"`{tool}`" for tool in creators)
                + ", then pass its returned IRI to the relationship tool"
            )
        else:
            instruction = (
                "use only an existing absolute IRI satisfying the declared range; no target "
                "creator is available"
            )
        target_contract_lines.append(
            f"- `{property_local}` -> [{', '.join(ranges)}] ({handling}): {instruction}."
        )
    target_contract_text = (
        "\n".join(target_contract_lines)
        if target_contract_lines
        else "- No iteration-owned relationship target contract is declared."
    )
    creator_owned_properties = [
        value for value in owned_object_properties if value in merged_predicates
    ]
    owned_classes_text = ", ".join(owned_classes)
    owned_properties_text = ", ".join(owned_object_properties)
    creator_owned_properties_text = ", ".join(creator_owned_properties)
    linked_text = ", ".join(sorted(linked_classes))
    if str(iteration.get("hint_representation") or "").strip() == "semantic-text.v1":
        return f"""# KG Building Prompt: {context.ontology.name} Iteration {iteration.get("iteration_number")}

Task:
Use the generated MCP tools to materialize semantic-text.v1 iteration hints for this iteration.

Rules:
- Iteration-owned classes: [{owned_classes_text}]
- Iteration-owned object_properties: [{owned_properties_text}]
- Creator-owned atomic object_properties: [{creator_owned_properties_text}]
- Linked materialization classes: [{linked_text}]
- Consume the Iteration Hints block below as a SEMANTIC_HINTS_V1 natural-language semantic ledger. Do not require JSON entities/relations, refs, or datatype_properties objects.
- Derive grounded individuals and relations from the ledger, T-Box comments, and the closed tool surface.
- You must call tools. A prose-only answer is a failed run.
- Reuse the scoped top entity URI supplied by the runtime.
- Treat every additional parameter in the Atomic Create Tool Contract as part of one indivisible business operation. Pass the scoped parent IRI and any source-grounded owned-dependent fields in that creator call; never repeat a creator-owned edge through a separate relationship tool.
- For lexical quantity predicates, recover the complete exact lexeme from the owning occurrence's standalone `P: <lexeme>` line and materialize it via the fixed quantity creator before asserting P.
- Finish by calling `export_memory`; do not claim success until export succeeds.

Mandatory Tool Sequence:
1. Call `init_memory` with the current document identifier and scoped top entity label.
2. Bind the scoped top entity IRI restored by `init_memory`; do not call a top-entity creator in this iteration.
3. Materialize owned entities and links justified by the semantic ledger and relationship target contract.
4. Call `export_memory` and base the final response only on the returned tool result.

Relationship Target Handling Contract:
{target_contract_text}

Scoped Top Entity:
- Document identifier value: `{{doi}}`
- Class: `{top_local}`
- Entity label value: `{{entity_label}}`
- Entity URI value: `{{entity_uri}}`

Required Top-Level Links:
{_format_required_links(context)}

Existing Entity Lookup Contract:
{reuse_tool_contract}

Materializable Hint Contract:
{_format_materializable_hint_contract(context, allowed_classes=owned_class_set, allowed_object_properties=owned_property_set)}

Atomic Create Tool Contract:
{_format_create_tool_contract(context, allowed_classes=owned_class_set | linked_classes)}

Creator-Owned Atomic Edge Contract:
{_format_atomic_operation_contract(context, allowed_classes=owned_class_set)}

{_format_value_kind_priority_contract(context)}

Ordered-Member Integrity Contract:
{_format_ordered_member_contract(context)}

Required Ordered-Member Object-Link Contract:
{_format_required_step_scoped_object_contract(context)}

{_format_mutually_exclusive_property_contract(context)}
{_tbox_comment_fidelity_contract()}

{_configured_prompt_addon(context)}
Iteration Hints:
{{iteration_hints}}
"""
    return f"""# KG Building Prompt: {context.ontology.name} Iteration {iteration.get("iteration_number")}

Task:
Use the generated MCP tools to materialize the extracted hints for this iteration.

Rules:
- Iteration-owned classes: [{owned_classes_text}]
- Iteration-owned object_properties: [{owned_properties_text}]
- Creator-owned atomic object_properties: [{creator_owned_properties_text}]
- Linked materialization classes: [{linked_text}]
- The iteration-owned lists above are closed and authoritative for primary facts.
  A relationship target may additionally be created only through the exact creator
  named in the Relationship Target Handling Contract below, even when that target
  class is absent from Iteration-owned classes. A referenced prior entity outside
  these paths may be resolved and linked, but must not be recreated.
- You must call tools. A prose-only answer is a failed run.
- Reuse the scoped top entity URI supplied by the runtime.
- Maintain a runtime map from each hint `ref` to exactly one materialized IRI.
- Bind an exact prior IRI from the pipeline identity dossier directly when its explicit fact matches the hinted/ref relation. `init_memory` restores this one-hop prior neighborhood into scoped memory; do not require central lookup or a reuse token for that already-scoped prior IRI.
- For a reusable class newly proposed by this iteration without an exact dossier IRI, call its central `check_existing_*` with the complete proposed entity hint serialized as JSON. Reuse only an LLM-authorized returned IRI and retain its `reuse_authorization_token`.
- Whenever an `add_*` relation uses a central candidate IRI, pass that candidate's `reuse_authorization_token`; without a valid scope-bound token the runtime rejects the link.
- For a non-reusable class, its `check_existing_*` reads only current scoped memory and is reference-resolution-only. Use it to recover the exact IRI for a prior occurrence ref; never use it to merge a newly declared occurrence, even when labels match.
- If an `object_ref` names a prior non-reusable occurrence but no scoped candidate satisfies its class, canonical label, datatype values, and existing relations, report an upstream identity/materialization blocker. Do not create a replacement occurrence merely to satisfy the relation.
- Use exact class/property local names from the extracted hints and T-Box.
- Treat the Materializable Hint Contract as the authoritative extraction schema. Treat the Atomic Create Tool Contract as the authoritative `create_*` parameter schema.
- For datatype values, pass supported scalar fields directly into the relevant `create_*` tool parameters.
- If a `create_*` tool exposes scalar parameters inherited from a parent class, pass the hinted values there; do not omit them because the concrete class is more specific than the parent.
- For ordered-member tools, pass only unique positive integer ordering values starting at 1; if enrichment adds a member between existing members, renumber the full ordered sequence with consecutive integers instead of using duplicate values, gaps, or decimals such as 1.5.
- Do not create two ordered-member individuals for the same source operation, label, or ordering value; choose the single most specific class supported by the T-Box class comments.
- Do not export duplicate same-class labels for the same extracted entity, and do not leave unreachable typed nodes outside the graph reachable from the scoped top entity.
- Do not materialize generic ordered-member parent hints as placeholder members when specific ordered-member subclass hints are available for the same source segment.
- Hint refs, classes, and relation records are orchestration metadata, not `create_*` parameters.
- Each `create_*` tool creates its primary individual and exactly the additional targets or links declared by the Atomic Create Tool Contract. Treat those declared effects as complete when the creator succeeds; never repeat them through separate creator or relationship calls.
- Materialize every standalone entity record first using only its canonical `label` and `datatype_properties`; creator-owned dependent records must instead be passed through the owning atomic creator. Bind each successful returned IRI to its stable `ref`.
- For relation records not listed as creator-owned atomic effects, resolve both endpoints by `subject_ref` and `object_ref`, then call the matching exposed `add_*` tool. Never call or invent an `add_*` tool for a creator-owned effect.
- Never derive datatype values from a label or ref, and never append the scoped top entity or scalar payload to a canonical label.
- Enforce every configured mutually exclusive property group before materializing hints; never assert more than one active property from the same group on one entity instance.
- For object links, call the relevant `add_*` tools after creating/reusing both endpoints.
- Finish by calling `export_memory`; do not claim success until export succeeds.

Mandatory Tool Sequence:
1. Call `init_memory` with the current document identifier and scoped top entity label.
2. Bind the scoped top entity IRI restored by `init_memory`; do not call a top-entity creator in this iteration.
3. Bind exact dossier-provided prior IRIs first. Then materialize each owned entity record once. Newly proposed reusable entities may select only a central candidate returned with a positive LLM judgement and authorization token; each newly declared non-reusable occurrence must call `create_*` and receive a fresh IRI.
4. For every standalone relation record, resolve both refs. Use scoped `check_existing_*` only to resolve an exact prior non-reusable occurrence, then call the matching exposed `add_*` tool. Skip creator-owned effects already completed by a successful atomic creator.
5. For every required top-level link that remains standalone, call the matching exposed `add_*` tool from the scoped top entity to each created target. Creator-owned top-level links stay inside their atomic creator call.
6. For every ordered member, follow the Ordered-Member Integrity Contract below; creator-owned membership edges must stay inside their atomic creator call.
7. Call `export_memory` and base the final response only on the returned tool result.

Failure Condition:
- If you have not called `export_memory`, or if no export result is available, state that KG building failed instead of saying the RDF graph was created or exported.

Relationship Target Handling Contract:
{target_contract_text}

Scoped Top Entity:
- Document identifier value: `{{doi}}`
- Class: `{top_local}`
- Entity label value: `{{entity_label}}`
- Entity URI value: `{{entity_uri}}`

Required Top-Level Links:
{_format_required_links(context)}

Existing Entity Lookup Contract:
{reuse_tool_contract}

Materializable Hint Contract:
{_format_materializable_hint_contract(context, allowed_classes=owned_class_set, allowed_object_properties=owned_property_set)}

Atomic Create Tool Contract:
{_format_create_tool_contract(context, allowed_classes=owned_class_set)}

Creator-Owned Atomic Edge Contract:
{_format_atomic_operation_contract(context, allowed_classes=owned_class_set)}

{_format_value_kind_priority_contract(context)}

Ordered-Member Integrity Contract:
{_format_ordered_member_contract(context)}

Required Ordered-Member Object-Link Contract:
{_format_required_step_scoped_object_contract(context)}

{_format_mutually_exclusive_property_contract(context)}
{_configured_prompt_addon(context)}
Authoritative Extracted Iteration Hints:
- Materialize only the facts in this injected channel for the current entity and iteration.
- Do not read, request, or re-extract raw paper content. Source text is not a KG Iteration 2+ input.
{{iteration_hints}}
"""

def _pre_extraction_prompt(
    context: AgenticGenerationContext, iteration: dict[str, Any]
) -> str:
    tbox_scope = _prompt_tbox_slice(context, iteration)
    subclass_contract = _subclass_decision_contract(tbox_scope)
    return f"""# Pre-Extraction Prompt: {context.ontology.name} Iteration {iteration.get("iteration_number")}

Task:
Build a closed ledger of the shortest source spans relevant to this iteration scope and classify
each operation only under the supplied T-Box-derived scope.

Iteration Scope:
- Name: {iteration.get("name") or ""}
- Description: {iteration.get("description") or ""}

Rules:
- Treat every class/property comment and integrity annotation in T-Box-Derived Scope as binding.
- For each in-scope operation atom, apply every relevant Subclass Decision Checklist decision
  point before choosing one most-specific candidate class.
- A T-Box comment containing `【Warning】` marks a high-risk choice boundary. Before selecting
  or excluding a governed candidate, compare the source against the complete marked comment
  and all applicable warning-marked alternatives. The marker changes attention only and adds
  no domain semantics beyond those comments.
- Record one explicit disposition for every operation atom. If no candidate reaches its
  T-Box-derived evidence threshold, mark the atom unresolved instead of omitting it.
- Do not invent domain rules, triggers, examples, or exceptions outside T-Box-Derived Scope.
- Preserve source order and keep distinct source operations distinct.

T-Box-Derived Scope:
{json.dumps(tbox_scope, indent=2, ensure_ascii=False)}

Subclass Decision Checklist:
{json.dumps(subclass_contract, indent=2, ensure_ascii=False)}

Return only the closed evidence ledger, with verbatim source spans and nearby headings.

Source:
{{paper_content}}
"""

def _sub_iteration_extraction_prompt(
    context: AgenticGenerationContext,
    iteration: dict[str, Any],
    sub_iteration: dict[str, Any],
) -> str:
    reusable_label_contract = _format_reusable_label_contract(context)
    return f"""# Enrichment Extraction Prompt: {context.ontology.name} Iteration {sub_iteration.get("iteration_number")}

Task:
Refine or enrich the existing extraction hints for this sub-iteration only.

Parent Iteration:
- Name: {iteration.get("name") or ""}
- Description: {iteration.get("description") or ""}

Sub-Iteration Scope:
- Name: {sub_iteration.get("name") or ""}
- Description: {sub_iteration.get("description") or ""}

Current Target Entity:
- Label: {{entity_label}}
- IRI: {{entity_uri}}

Rules:
- Use only the provided source text and existing hints.
- Treat every T-Box `comment=` value below as a binding extraction rule for the corresponding class or property.
- Preserve the `ref`, class, and canonical label of every entity already present in the existing hints. Enrichment must update the matching ref, never create a renamed duplicate.
- Preserve exact class and field names from the Materializable Hint Contract below.
- Omit unsupported additions and any key that is not listed for its class.
- Treat structured source labels, table headers, bullet labels, and nearby section labels as strong evidence for matching datatype fields when their normalized words match the field local name, CSV-style display label, or T-Box comment wording.
- For explicit structured source sections, tables, or bullet lists, evaluate every listed item before returning JSON; do not stop after the first matching field in the section.
- If a T-Box comment contains an explicit convention, positive example, negative example, priority instruction, exception, or "do not" rule, apply that instruction literally and before using general keyword matching.
- If a property comment says a field is not supported by a source phrase, requires a more specific source condition, or prefers a sibling/canonical field for that phrase, follow that comment even when the phrase contains words that otherwise resemble the field name.
- If a property comment defines normalization examples or says which modifiers to keep or remove, emit the normalized value required by the comment rather than preserving the full source phrase.
- If a class exposes a catch-all, other, note, or free-text datatype field and its T-Box comment says to use it for explicit items not covered by canonical fields, collect all source-listed unmatched items for that class in that field.
- For identification or demographic fields, inspect source header blocks and nearby header tables before the main body when the class or property comments say those fields come from identifying or administrative source regions.
- For short acronym-like datatype fields, if the exact acronym/token appears in source text in a relevant diagnosis, indication, observation, or field-value context and the T-Box comment says that token activates the field, emit the configured active checklist value for that field.
- If source evidence supports datatype fields for a linked class, emit or update its entity record and emit the relation by refs. Scan the same source region for all accepted datatype fields before returning JSON.
- Emit a class section only when the current target label, an existing hinted member, or a source-supported linked target actually denotes an instance of that class; do not coerce one semantic category into another merely because both classes are available.
- For ordered-member classes, ordering fields listed in the Materializable Hint Contract must be unique positive integers starting at 1. Do not emit duplicate, skipped, decimal, fractional, zero, negative, or between-step order values such as 1.5; renumber the ordered members as consecutive integers in source order.
- Do not emit the same source operation, label, or ordering value as two different ordered-member class sections; choose the single most specific class supported by the T-Box class comments.
- Do not emit a generic ordered-member parent class as a placeholder when source evidence supports one of its specific subclasses in the Materializable Hint Contract; emit the specific subclass section and its supported fields instead.
- If the T-Box declares an ordered-member class whose comment says it introduces a linked target object, emit one ordered member per source-supported linked target in source order.
- Labels are identity names only. Put all scalar or role payload in `datatype_properties`, never in labels or refs.
- Relations must use `subject_ref`, exact property local name, and `object_ref`. Reuse exact existing refs.
- For every entity class, never invent a scoped label such as `<source label> for <Current Target Entity>` or `<source label> (<Current Target Entity>)` unless that complete phrase is explicitly the entity's own name in the source. Contextual participation must be represented by links, not label text.
- One ref identifies one semantic entity occurrence. Distinct non-reusable occurrences require distinct refs even when their canonical labels match.
- For ordered-member relations, link to the same target ref whose entity record carries the scalar fields.
- Emit one relation record per source-supported target ref; do not collapse multiple targets into one label string.
- If a linked target label also explicitly supplies a value accepted by a target scalar field, preserve that source-supported value in the scalar field.
- Follow object-property comments when they require source-supported links to target objects; do not omit a required link merely because another scalar field was filled.
- If a general parameter string contains a scalar value that has a dedicated generated field in the Materializable Hint Contract, also emit the dedicated field instead of leaving the scalar only inside the parameter string.
- For classes listed in the Mutually Exclusive Property Contract, emit at most one active property from each group for one entity instance. Use source evidence and T-Box comments to choose the best-supported one; omit the rest.
- When a T-Box comment distinguishes final, confirmed, provisional, preliminary, intermediate, or subordinate evidence, follow that evidence priority exactly; do not promote provisional or intermediate source statements into final/confirmed fields unless the source explicitly makes them final or confirmed.
- If the T-Box exposes a procedure-inheritance object field and the source says the current procedure follows the same, similar, or previous conditions as another source-supported procedure, treat the referenced procedure text as source-supported context for this target: carry over its ordered members and linked targets, then apply only the explicit modifications stated for the current target.
- Existing refs are not source evidence for adding new relations; only source-supported relations may be emitted.
- If enrichment evidence belongs to a more specific class whose generated tool lists the relevant field, emit that specific class instead of a generic parent/container class.
- Return only a compact JSON object, with no markdown fences, bullets, checklists, or explanatory prose.
- The JSON must be directly mergeable into the existing `entities` and `relations` arrays.
- To enrich an existing entity, repeat its exact `ref`, `class`, and `label`, then add only supported `datatype_properties`.
- For source-supported links shared by ordered members, emit one relation per affected `subject_ref`.

Reusable Entity Label Contract:
{reusable_label_contract}

Materializable Hint Contract:
{_format_materializable_hint_contract(context)}

Linked Target Scalar Contract:
{_format_linked_target_scalar_contract(context)}

Required Ordered-Member Object-Link Contract:
{_format_required_step_scoped_object_contract(context)}

{_format_mutually_exclusive_property_contract(context)}
{_tbox_comment_fidelity_contract()}

Expected JSON Shape:
Hint Schema: ref-entity-relations.v1
- Top-level arrays are `entities` and `relations`.
- Entity records contain exact `ref`, `class`, canonical `label`, and `datatype_properties`.
- Relation records contain exact `subject_ref`, `property`, and `object_ref`.

Classes and Properties:
{_format_class_rows(context)}

Datatype Properties:
{_format_property_rows(context, kind="datatype")}

Object Properties:
{_format_property_rows(context, kind="object")}

{_configured_prompt_addon(context)}
Source and Existing Hints:
{{paper_content}}
"""

def _iteration_kg_onepass_prompt(
    context: AgenticGenerationContext, iteration: dict[str, Any]
) -> str:
    """Seed a focused, composable KG fragment for whole-graph one-pass use."""
    top = context.contract.get("top_entity") or {}
    top_local = str(top.get("class_local") or "TopEntity").strip() or "TopEntity"
    iteration_number = iteration.get("iteration_number")
    return f"""# KG Building One-Pass Fragment: {context.ontology.name} Iteration {iteration_number}

Task:
Describe the positive materialization responsibilities contributed by Iteration {iteration_number}
to a combined whole-graph KG-building session. This is a composable fragment, not a
standalone iteration run.

Composition rules:
- Preserve this iteration's focused T-Box semantics, occurrence boundaries, creator contracts,
  relationship directions, reuse rules, and materializable fields.
- Treat iteration ownership as the source of this fragment's positive responsibilities, not as
  a session-wide prohibition on operations contributed by other one-pass fragments.
- Do not open, close, export, or independently declare completion of retained memory.
- Do not defer work to another iteration, ignore another hint section, or prohibit creators
  merely because they are owned by another iteration.

Scoped Top Entity:
- Document identifier value: `{{doi}}`
- Class: `{top_local}`
- Entity label value: `{{entity_label}}`
- Entity URI value: `{{entity_uri}}`

Iteration Hint Section:
{{iteration_hints}}
"""

def generate_deterministic_prompt_slice(context: AgenticGenerationContext) -> list[str]:
    prompts_dir = Path(context.prompts_dir)
    prompts_dir.mkdir(parents=True, exist_ok=True)
    files = (
        {}
        if context.ontology.role == "extension"
        else {
            "EXTRACTION_ITER_1.md": _extraction_prompt(context),
            "KG_BUILDING_ITER_1.md": _kg_prompt(context),
        }
    )
    iterations = _occurrence_iteration_plan(context)
    for iteration in iterations.get("iterations") or []:
        iter_num = iteration.get("iteration_number")
        files[f"EXTRACTION_ITER_{iter_num}.md"] = _iteration_extraction_prompt(
            context, iteration
        )
        files[f"KG_BUILDING_ITER_{iter_num}.md"] = _iteration_kg_prompt(
            context, iteration
        )
        if context.ontology.role != "extension" and int(iter_num) >= 2:
            files[f"KG_BUILDING_ITER_{iter_num}_ONEPASS.md"] = (
                _iteration_kg_onepass_prompt(context, iteration)
            )
        if iteration.get("has_pre_extraction"):
            files[f"PRE_EXTRACTION_ITER_{iter_num}.md"] = _pre_extraction_prompt(
                context, iteration
            )
        for sub_iteration in iteration.get("sub_iterations") or []:
            if not isinstance(sub_iteration, dict):
                continue
            sub_num = str(sub_iteration.get("iteration_number") or "").replace(".", "_")
            if sub_num:
                files[f"EXTRACTION_ITER_{sub_num}.md"] = (
                    _sub_iteration_extraction_prompt(
                        context,
                        iteration,
                        sub_iteration,
                    )
                )
    written: list[str] = []
    for name, content in files.items():
        if name.upper().startswith(("EXTRACTION_ITER_", "PRE_EXTRACTION_ITER_")):
            continue
        path = prompts_dir / name
        path.write_text(content, encoding="utf-8")
        written.append(str(path))
    written.extend(generate_runtime_support_slice(context, iterations=iterations))
    for name in files:
        if name.upper().startswith(("EXTRACTION_ITER_", "PRE_EXTRACTION_ITER_")):
            continue
        path = prompts_dir / name
        component = _write_materializable_prompt_component(context, path)
        if component is not None:
            written.append(str(component))
    return written

