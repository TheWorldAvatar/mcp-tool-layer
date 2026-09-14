"""Emit argument-ownership and loop-guard sidecars."""

from __future__ import annotations

import json
from typing import Any, Mapping

from src.extraction_prompt_generation.compile.operation_units import (
    _python_name,
)
from src.kg_building_mcp_generation_v2.overlay import ACTIVE_SURFACE
from src.kg_building_mcp_generation_v2.overlay.instruction import tool_quantity_suffix
from src.kg_building_mcp_generation_v2.surface.compile import (
    compile_loop_guard_contract,
)

def _ontology_symbol(name: str) -> str:
    return _python_name(name)

def _signature(tool: Mapping[str, Any]) -> str:
    required = ["label: str"]
    required_kw: list[str] = []
    optional: list[str] = []
    if tool.get("parent_parameter"):
        required.append(f"{tool['parent_parameter']}: str")
    ordering = str(tool.get("ordering_property_local") or "")
    if ordering:
        required.append(f"{_python_name(ordering)}: int")
    seen = {"label", str(tool.get("parent_parameter") or ""), _python_name(ordering)}
    for item in tool.get("datatype_inputs") or []:
        name = _python_name(str(item.get("property_local") or ""))
        if not name or name in seen or name == _python_name(ordering):
            continue
        python_type = str(item.get("python_type") or "str")
        if item.get("required"):
            required.append(f"{name}: {python_type}")
        else:
            optional.append(f"{name}: {python_type} | None = None")
        seen.add(name)
    for item in list(tool.get("quantities") or []) + list(
        tool.get("parent_quantities") or []
    ):
        name = str(item.get("parameter") or "")
        if name and name not in seen:
            optional.append(f"{name}: str | None = None")
            seen.add(name)
    for item in tool.get("fresh_dependents") or []:
        label_name = str(item.get("label_parameter") or "")
        if label_name and label_name not in seen:
            if item.get("required_bridge_link"):
                required_kw.append(f"{label_name}: str")
            else:
                optional.append(f"{label_name}: str | None = None")
            seen.add(label_name)
        for datatype in item.get("datatype_inputs") or []:
            name = str(datatype.get("parameter_name") or "")
            python_type = str(datatype.get("python_type") or "str")
            if name and name not in seen:
                optional.append(f"{name}: {python_type} | None = None")
                seen.add(name)
    for item in tool.get("reusable_links") or []:
        name = str(item.get("label_parameter") or "")
        if name and name not in seen:
            if item.get("required_bridge_link"):
                required_kw.append(f"{name}: str")
            else:
                optional.append(f"{name}: str | None = None")
            seen.add(name)
        for datatype in item.get("datatype_inputs") or []:
            dt_name = str(datatype.get("parameter_name") or "")
            python_type = str(datatype.get("python_type") or "str")
            if dt_name and dt_name not in seen:
                optional.append(f"{dt_name}: {python_type} | None = None")
                seen.add(dt_name)
    for item in tool.get("nested_reusable_links") or []:
        name = str(item.get("label_parameter") or "")
        if name and name not in seen:
            optional.append(f"{name}: str | None = None")
            seen.add(name)
    parts = required
    if required_kw or optional:
        parts.append("*")
        parts.extend(required_kw)
        parts.extend(optional)
    return ", ".join(parts)

def _forward_datatypes(tool: Mapping[str, Any]) -> str:
    ordering = _python_name(str(tool.get("ordering_property_local") or ""))
    names: list[str] = []
    for item in tool.get("datatype_inputs") or []:
        name = _python_name(str(item.get("property_local") or ""))
        if name and name != ordering:
            names.append(f"{name}={name}")
    if not names:
        return ""
    return ", " + ", ".join(names)

def _optional_label_parameters(tool: Mapping[str, Any]) -> list[str]:
    names = [
        str(item.get("parameter") or "")
        for item in list(tool.get("quantities") or [])
        + list(tool.get("parent_quantities") or [])
    ]
    for group in ("fresh_dependents", "reusable_links", "nested_reusable_links"):
        names.extend(
            str(item.get("label_parameter") or "") for item in tool.get(group) or []
        )
    return list(dict.fromkeys(name for name in names if name))

def _identity_kind(tool: Mapping[str, Any]) -> str:
    return str((tool.get("identity_contract") or {}).get("kind") or "")

def _defaults_label_from_owner(
    tool: Mapping[str, Any], item: Mapping[str, Any], *, fresh: bool
) -> bool:
    flagged = item.get("default_label_from_owner")
    if flagged is False:
        return False
    if flagged is True:
        return True
    if item.get("required_bridge_link"):
        return True
    if _identity_kind(tool) != "unique_parent":
        return False
    if not str(item.get("label_parameter") or "") or not str(
        item.get("create_tool") or ""
    ):
        return False
    if fresh:
        return True
    return bool(item.get("create_fresh_with_datatypes"))

def _defaulted_owner_links(
    tool: Mapping[str, Any],
) -> list[tuple[str, Mapping[str, Any]]]:
    items: list[tuple[str, Mapping[str, Any]]] = []
    for item in tool.get("fresh_dependents") or []:
        if _defaults_label_from_owner(tool, item, fresh=True):
            items.append(("fresh_dependent", item))
    for item in tool.get("reusable_links") or []:
        if _defaults_label_from_owner(tool, item, fresh=False):
            items.append(("reusable_link", item))
    return items

def _owner_facet_links(
    tool: Mapping[str, Any],
) -> list[tuple[str, Mapping[str, Any]]]:
    """Owner object-facets that adopt/reuse must materialize when args are passed.

    Unlike `_defaulted_owner_links`, this does not copy the owner label onto
    missing nested labels. Empty / sentinel optional args stay absent.
    """
    items: list[tuple[str, Mapping[str, Any]]] = []
    for kind, group in (
        ("fresh_dependent", "fresh_dependents"),
        ("reusable_link", "reusable_links"),
    ):
        for item in tool.get(group) or []:
            if not str(item.get("label_parameter") or "") or not str(
                item.get("predicate_iri") or ""
            ):
                continue
            if not str(item.get("create_tool") or "") and not str(
                item.get("target_class_local") or ""
            ):
                continue
            items.append((kind, item))
    return items

def _link_create_extra_args(item: Mapping[str, Any]) -> str:
    extra = []
    for datatype in item.get("datatype_inputs") or []:
        parameter = str(datatype.get("parameter_name") or "")
        property_local = _python_name(str(datatype.get("property_local") or ""))
        if parameter and property_local:
            extra.append(f"{property_local}={parameter}")
    return (", " + ", ".join(extra)) if extra else ""

def _emit_materialize_owner_link(
    item: Mapping[str, Any],
    *,
    indent: str,
    bind_resolved: bool,
) -> list[str]:
    label_name = str(item.get("label_parameter") or "")
    predicate = str(item.get("predicate_local") or "")
    create_tool = str(item.get("create_tool") or "")
    extra_args = _link_create_extra_args(item)
    created = "dependent" if not item.get("create_fresh_with_datatypes") else "represented"
    lines = [
        f"{indent}{created} = _payload(entities.{create_tool}(label={label_name}{extra_args}))",
        f"{indent}linked_iri = str({created}['iri'])",
    ]
    if bind_resolved:
        lines.append(f"{indent}resolved[{predicate!r}] = linked_iri")
    lines.append(
        f"{indent}_link(relationships.add_{predicate}, owner_iri, linked_iri)"
    )
    return lines


def _emit_ensure_owner_link(item: Mapping[str, Any], *, indent: str) -> list[str]:
    if str(item.get("create_tool") or ""):
        return _emit_materialize_owner_link(item, indent=indent, bind_resolved=False)
    label_name = str(item.get("label_parameter") or "")
    predicate = str(item.get("predicate_local") or "")
    target_local = str(item.get("target_class_local") or "")
    return [
        f"{indent}object_iri, _ = _resolve_or_create({target_local!r}, {label_name})",
        f"{indent}_link(relationships.add_{predicate}, owner_iri, object_iri)",
    ]


def _argument_ownership(tool: Mapping[str, Any]) -> dict[str, Any]:
    """Compile the flat API's ownership paths without changing its signature."""
    parameters: dict[str, dict[str, Any]] = {
        "label": {"role": "occurrence_label", "owner_path": "self"}
    }
    parent = str(tool.get("parent_parameter") or "")
    if parent:
        parameters[parent] = {"role": "parent_iri", "owner_path": "parent"}
    ordering = _python_name(str(tool.get("ordering_property_local") or ""))
    if ordering:
        parameters[ordering] = {
            "role": "ordering",
            "owner_path": "self",
            "property_local": str(tool.get("ordering_property_local") or ""),
        }
    for item in tool.get("datatype_inputs") or []:
        name = _python_name(str(item.get("property_local") or ""))
        if name:
            parameters[name] = {
                "role": "datatype",
                "owner_path": "self",
                "property_local": str(item.get("property_local") or ""),
            }
    for group, owner_path in (
        ("quantities", "self"),
        ("parent_quantities", "parent"),
    ):
        for item in tool.get(group) or []:
            name = str(item.get("parameter") or "")
            if name:
                parameters[name] = {
                    "role": "quantity_label",
                    "owner_path": owner_path,
                    "property_local": str(item.get("predicate_local") or ""),
                }
    for group, role in (
        ("fresh_dependents", "fresh_dependent"),
        ("reusable_links", "reusable_link"),
    ):
        for item in tool.get(group) or []:
            predicate = str(item.get("predicate_local") or "")
            label_name = str(item.get("label_parameter") or "")
            if label_name:
                parameters[label_name] = {
                    "role": f"{role}_label",
                    "owner_path": f"self.{predicate}",
                    "property_local": predicate,
                    **(
                        {"required": True}
                        if item.get("required_bridge_link")
                        else {}
                    ),
                }
            for datatype in item.get("datatype_inputs") or []:
                name = str(datatype.get("parameter_name") or "")
                if name:
                    parameters[name] = {
                        "role": "nested_datatype",
                        "owner_path": f"self.{predicate}",
                        "property_local": str(
                            datatype.get("property_local") or ""
                        ),
                        "requires": [label_name] if label_name else [],
                    }
    for item in tool.get("nested_reusable_links") or []:
        parent_predicate = str(item.get("parent_predicate_local") or "")
        child_predicate = str(item.get("child_predicate_local") or "")
        label_name = str(item.get("label_parameter") or "")
        parent_labels = [
            str(link.get("label_parameter") or "")
            for group in ("fresh_dependents", "reusable_links")
            for link in tool.get(group) or []
            if str(link.get("predicate_local") or "") == parent_predicate
        ]
        if label_name:
            parameters[label_name] = {
                "role": "nested_reusable_label",
                "owner_path": f"self.{parent_predicate}.{child_predicate}",
                "property_local": child_predicate,
                "requires": [value for value in parent_labels if value],
            }
    return {
        "name": str(tool.get("name") or ""),
        "identity_arguments": list(
            (tool.get("identity_contract") or {}).get("identity_args") or []
        ),
        "allowed_arguments": list(parameters),
        "parameters": parameters,
        "compatibility": {
            "shape": "flat_prefixed",
            "nested_object_arguments": False,
        },
    }

def _create_description(tool: Mapping[str, Any]) -> str:
    contract = _argument_ownership(tool)
    parent = str(tool.get("parent_parameter") or "")
    identity_kind = str((tool.get("identity_contract") or {}).get("kind") or "")
    if identity_kind == "adopted_focus":
        opening = (
            "Adopt the pipeline-seeded enrichment individual for this owner class; "
            "do not mint a second IRI. Optional datatypes fill missing facts on that IRI."
        )
    elif not parent:
        opening = "Create one ledger occurrence from the supplied heading."
    elif tool.get("parent_binds_to_session_root", True):
        opening = (
            "Create one ledger occurrence; parent_iri must be the exact bound root IRI."
        )
    else:
        opening = (
            "Create one ledger occurrence; parent_iri must be the returned IRI of the "
            "parent occurrence that owns this child, not the session bound root."
        )
    ownership = [
        f"{name} -> {item.get('owner_path')}"
        + (
            f" (property {item.get('property_local')})"
            if item.get("property_local")
            else ""
        )
        for name, item in contract["parameters"].items()
        if item.get("owner_path") not in {"self", "parent"}
    ]
    detail = (
        " Nested ownership: " + "; ".join(ownership) + "."
        if ownership
        else ""
    )
    return (
        f"{opening} Allowed arguments: {', '.join(contract['allowed_arguments'])}."
        f"{detail} Do not pass bare ontology property names unless they appear "
        "exactly in the allowed-arguments list."
        + tool_quantity_suffix(tool, ACTIVE_SURFACE)
    )

def emit_occurrence_argument_ownership(
    compiled: Mapping[str, Any] | None = None,
) -> str:
    """Serialize the flat parameter ownership contract for agents and audits."""
    units = compiled or {}
    payload = {
        "schema_version": "occurrence-argument-ownership.v1",
        "compatibility": {
            "current_shape": "flat_prefixed",
            "nested_object_arguments": False,
            "note": (
                "Nested objects are intentionally not enabled; changing the public "
                "tool schema requires a compatibility migration."
            ),
        },
        "tools": [
            _argument_ownership(tool)
            for tool in units.get("public_tools") or []
        ],
    }
    return json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n"

def emit_occurrence_loop_guard(
    compiled: Mapping[str, Any] | None = None,
) -> str:
    """Serialize the host loop-guard contract compiled from the public surface."""
    units = compiled or {}
    contract = units.get("loop_guard") or compile_loop_guard_contract(units)
    return json.dumps(contract, indent=2, ensure_ascii=False, sort_keys=True) + "\n"

