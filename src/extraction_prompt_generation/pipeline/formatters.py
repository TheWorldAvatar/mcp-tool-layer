"""Live `_format_*` contract text for scaffolds and `*.materializable.inc`.

These renderers read the compiled context. They do not invent class or
property lists. See pipeline/README.md.
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


def _py_name(name: str) -> str:
    out = re.sub(r"\W+", "_", str(name or "")).strip("_")
    if not out:
        out = "unnamed"
    if out[:1].isdigit():
        out = "_" + out
    return out


def _local_name(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if "#" in text:
        return text.rsplit("#", 1)[-1]
    return text.rstrip("/").rsplit("/", 1)[-1]


def _configured_prompt_addon(context: AgenticGenerationContext) -> str:
    """Keep non-T-Box runtime configuration out of generated prompt semantics."""
    return ""


def _mutually_exclusive_property_groups(
    context: AgenticGenerationContext,
) -> list[dict[str, Any]]:
    return []


def _format_mutually_exclusive_property_contract(
    context: AgenticGenerationContext,
) -> str:
    groups = _mutually_exclusive_property_groups(context)
    if not groups:
        return ""
    lines = [
        "Mutually Exclusive Property Contract:",
        "- Treat each configured group as an ontology-derived integrity rule. For one entity instance, emit at most one active value from the listed datatype properties.",
        '- Values such as empty string, `"-"`, `"0"`, `false`, `False`, `no`, or `none` are inactive; any other source-supported value is active.',
        "- If the source supports multiple candidates in one group, choose the single best-supported property according to the T-Box comments and local source evidence; omit the rest.",
        "- Do not emit a plausible default for a mutually exclusive property when source evidence only supports a different property in the same group.",
    ]
    for group in groups:
        props = ", ".join(f"`{prop}`" for prop in group["properties"])
        lines.append(
            f"- `{group['target_class']}`: at most one active value among {props}."
        )
    return "\n".join(lines) + "\n"


def _class_ancestors(classes: dict[str, Any], class_name: str) -> list[str]:
    """Return T-Box parent classes, nearest first, without hardcoded ontology names."""
    out: list[str] = []
    queue = list(((classes.get(class_name) or {}).get("parent_classes") or []))
    while queue:
        parent = str(queue.pop(0) or "").strip()
        if not parent or parent in out:
            continue
        out.append(parent)
        queue.extend((classes.get(parent) or {}).get("parent_classes") or [])
    return out


def _ordered_member_classes(context: AgenticGenerationContext) -> set[str]:
    profile = context.contract.get("ordered_member_profile") or {}
    return {
        str(x).strip()
        for x in profile.get("ordered_member_classes", []) or []
        if str(x).strip()
    }


def _ordering_datatype_properties(context: AgenticGenerationContext) -> set[str]:
    profile = context.contract.get("ordered_member_profile") or {}
    return {
        str(x).strip()
        for x in profile.get("single_valued_ordering_properties", []) or []
        if str(x).strip()
    }


def _step_scoped_object_properties_for_class(
    context: AgenticGenerationContext, class_local: str
) -> dict[str, str]:
    """Return T-Box object properties that may be attached to this ordered-member class."""
    props: dict[str, str] = {}
    classes = context.parsed.get("classes") or {}
    class_family = {class_local, *_class_ancestors(classes, class_local)}

    for spec in context.contract.get("step_scoped_object_properties") or []:
        domain = str((spec or {}).get("domain_local") or "").strip()
        prop = str((spec or {}).get("predicate_local") or "").strip()
        range_local = str((spec or {}).get("range_local") or "").strip()
        if domain in class_family and prop and range_local:
            props[prop] = range_local

    property_defs = context.parsed.get("properties") or {}
    for prop, spec in (
        context.contract.get("relationship_domain_contracts") or {}
    ).items():
        prop_local = str(prop or "").strip()
        if not prop_local:
            continue
        members = {
            _local_name(member)
            for member in ((spec or {}).get("union_members") or [])
            if str(member or "").strip()
        }
        preferred = str((spec or {}).get("preferred_domain_local") or "").strip()
        if class_local not in members and preferred not in class_family:
            continue
        range_local = str(
            ((property_defs.get(prop_local) or {}).get("range") or "")
        ).strip()
        if range_local:
            props[prop_local] = range_local
    return props


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
        row = f"- `{name}`: domains=[{domains}], range=`{rng}`"
        if comment:
            row += f"; comment={comment}"
        rows.append(row)
    return "\n".join(rows) if rows else "- None declared in the T-Box."


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
