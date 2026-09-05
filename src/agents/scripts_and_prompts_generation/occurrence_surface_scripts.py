"""Deterministically emit occurrence operations and the public MCP main module."""

from __future__ import annotations

import json
from typing import Any, Mapping

from src.agents.scripts_and_prompts_generation.materialization_operation_units import (
    _python_name,
)
from src.agents.scripts_and_prompts_generation.occurrence_surface_units import (
    compile_fallback_instruction,
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
    if not parent:
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
    required_nested = [
        name
        for name, item in contract["parameters"].items()
        if item.get("required")
    ]
    required_text = (
        " Required representation labels: " + ", ".join(required_nested) + "."
        if required_nested
        else ""
    )
    return (
        f"{opening} Allowed arguments: {', '.join(contract['allowed_arguments'])}."
        f"{detail}{required_text} Do not pass bare ontology property names unless they appear "
        "exactly in the allowed-arguments list."
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


def emit_occurrence_operations(
    context: Any,
    compiled: Mapping[str, Any] | None = None,
) -> str:
    units = compiled or (getattr(context, "contract", {}) or {}).get(
        "occurrence_surface_units"
    ) or {}
    ontology = _ontology_symbol(context.ontology.name)
    reusable_map_items = []
    for item in units.get("reusable_classes") or []:
        local = str(item.get("class_local") or "")
        iri = str(item.get("class_iri") or "")
        tool = str(item.get("create_tool") or "")
        if local and iri and tool:
            reusable_map_items.append(
                f"    {local!r}: ({iri!r}, entities.{tool}),"
            )
    reusable_block = "\n".join(reusable_map_items) or "    # no reusable classes"
    ordered_contracts = {
        str(tool.get("owner_class_local") or ""): {
            "class_iri": str(tool.get("owner_class_iri") or ""),
            "parent_predicate_iri": str(
                tool.get("parent_predicate_iri") or ""
            ),
            "ordering_property_iri": str(
                tool.get("ordering_property_iri") or ""
            ),
        }
        for tool in units.get("public_tools") or []
        if tool.get("ordered_member")
        and str(tool.get("owner_class_local") or "")
    }
    functions: list[str] = []
    for tool in units.get("public_tools") or []:
        functions.append(_emit_create_function(tool, ontology))
    for linker in units.get("public_linkers") or []:
        functions.append(_emit_linker_function(linker))
    header = (
        "from __future__ import annotations\n\n"
        '"""Generated occurrence MCP operations. Do not edit by hand."""\n\n'
        "import hashlib\n"
        "import json\n"
        "from typing import Callable\n\n"
        "from rdflib import Literal, RDF, RDFS, URIRef\n\n"
        "from . import _fixed_rdf_runtime as rdf_runtime\n"
        f"from . import {ontology}_creation_entities as entities\n"
        f"from . import {ontology}_creation_relationships as relationships\n\n\n"
        f"_REUSABLE = {{\n{reusable_block}\n}}\n"
        f"_ORDERED_MEMBER_CONTRACTS = {ordered_contracts!r}\n"
        '_MARKER_BASE = "urn:twa:semantic-mutation:"\n'
        '_MARKER_FINGERPRINT = URIRef(_MARKER_BASE + "fingerprint")\n'
        '_MARKER_RESULT = URIRef(_MARKER_BASE + "result")\n'
        '_ABSENT_LABELS = frozenset({"", "n/a", "na", "unknown", "not specified"})\n\n\n'
        "class _Rejected(Exception):\n"
        "    def __init__(self, payload: str):\n"
        "        super().__init__(payload)\n"
        "        self.payload = payload\n\n\n"
        "def _payload(value: str) -> dict:\n"
        "    parsed = json.loads(value)\n"
        '    if str(parsed.get("status", "")).lower() != "ok":\n'
        "        raise _Rejected(value)\n"
        "    return parsed\n\n\n"
        "def _rejection(exc: Exception, fingerprint: str = '', *, allow_skip: bool = False, tool_name: str = '') -> str:\n"
        "    if isinstance(exc, _Rejected):\n"
        "        parsed = json.loads(str(exc.payload))\n"
        "    else:\n"
        "        parsed = json.loads(rdf_runtime.error_json(code='OCCURRENCE_REJECTED', message=str(exc)))\n"
        '    parsed.setdefault("already_committed", False)\n'
        '    parsed.setdefault("graph_changed", False)\n'
        "    if tool_name:\n"
        '        parsed.setdefault("tool_name", tool_name)\n'
        "    if fingerprint:\n"
        '        parsed.setdefault("semantic_fingerprint", fingerprint)\n'
        '        authorized = bool(allow_skip and parsed.get("skippable") is True)\n'
        '        parsed["skippable"] = authorized\n'
        "        rdf_runtime.register_semantic_rejection(fingerprint, parsed, skippable=authorized)\n"
        "    return json.dumps(parsed, ensure_ascii=False, sort_keys=True)\n\n\n"
        "def _normalized(value: object) -> str:\n"
        '    return " ".join(str(value).casefold().split())\n\n\n'
        "def _optional_label(value: str | None) -> str | None:\n"
        "    if value is None:\n"
        "        return None\n"
        "    text = str(value).strip()\n"
        "    return None if _normalized(text) in _ABSENT_LABELS else text\n\n\n"
        "def _canonical(value: object) -> object:\n"
        "    if isinstance(value, dict):\n"
        "        return {str(key): _canonical(item) for key, item in sorted(value.items())}\n"
        "    if isinstance(value, (list, tuple)):\n"
        "        return [_canonical(item) for item in value]\n"
        "    if isinstance(value, str):\n"
        "        return _normalized(value)\n"
        "    return value\n\n\n"
        "def _fingerprint(tool: str, identity: dict[str, object]) -> str:\n"
        "    payload = json.dumps(\n"
        '        {"tool": tool, "identity": _canonical(identity)},\n'
        "        ensure_ascii=False,\n"
        "        sort_keys=True,\n"
        '        separators=(",", ":"),\n'
        "    )\n"
        '    return hashlib.sha256(payload.encode("utf-8")).hexdigest()\n\n\n'
        "def _marker(fingerprint: str) -> URIRef:\n"
        "    return URIRef(_MARKER_BASE + fingerprint)\n\n\n"
        "def _existing_fingerprint(fingerprint: str) -> str | None:\n"
        "    graph = rdf_runtime.retained_graph()\n"
        "    marker = _marker(fingerprint)\n"
        "    if (marker, _MARKER_FINGERPRINT, Literal(fingerprint)) not in graph:\n"
        "        return None\n"
        "    return next((str(value) for value in graph.objects(marker, _MARKER_RESULT)), '')\n\n\n"
        "def _commit_fingerprint(fingerprint: str, result_iri: str) -> None:\n"
        "    graph = rdf_runtime.retained_graph()\n"
        "    marker = _marker(fingerprint)\n"
        "    graph.add((marker, _MARKER_FINGERPRINT, Literal(fingerprint)))\n"
        "    graph.add((marker, _MARKER_RESULT, URIRef(result_iri)))\n\n\n"
        "def _graph_revision() -> int:\n"
        "    graph = rdf_runtime.retained_graph()\n"
        "    return len(set(graph.subjects(_MARKER_FINGERPRINT, None)))\n\n\n"
        "def _replay(iri: str, fingerprint: str, **metadata: object) -> str:\n"
        "    return rdf_runtime.success_json(\n"
        "        iri=iri,\n"
        "        semantic_fingerprint=fingerprint,\n"
        "        already_committed=True,\n"
        "        graph_changed=False,\n"
        "        graph_revision=_graph_revision(),\n"
        '        message="Semantic mutation is already committed; continue.",\n'
        "        **metadata,\n"
        "    )\n\n\n"
        "def _apply_missing_datatypes(owner_iri: str, pairs: list) -> bool:\n"
        "    graph = rdf_runtime.retained_graph()\n"
        "    subject = URIRef(owner_iri)\n"
        "    changed = False\n"
        "    for predicate_iri, value in pairs:\n"
        "        if value is None:\n"
        "            continue\n"
        "        text = str(value).strip()\n"
        "        if not text or _normalized(text) in _ABSENT_LABELS:\n"
        "            continue\n"
        "        pred = URIRef(str(predicate_iri))\n"
        "        if any(graph.objects(subject, pred)):\n"
        "            continue\n"
        "        graph.add((subject, pred, Literal(value)))\n"
        "        changed = True\n"
        "    return changed\n\n\n"
        "def _has_outgoing(owner_iri: str, predicate_iri: str) -> bool:\n"
        "    graph = rdf_runtime.retained_graph()\n"
        "    return any(graph.objects(URIRef(owner_iri), URIRef(predicate_iri)))\n\n\n"
        "def _reuse_unique_parent(iri: str, fingerprint: str, datatype_pairs: list, ensure: Callable[[str], None] | None = None, **metadata: object) -> str:\n"
        "    before = set(rdf_runtime.retained_graph())\n"
        "    with rdf_runtime.atomic_graph_transaction():\n"
        "        _apply_missing_datatypes(iri, datatype_pairs)\n"
        "        if ensure is not None:\n"
        "            ensure(iri)\n"
        "        if _existing_fingerprint(fingerprint) is None:\n"
        "            _commit_fingerprint(fingerprint, iri)\n"
        "    changed = set(rdf_runtime.retained_graph()) != before\n"
        "    return rdf_runtime.success_json(\n"
        "        iri=iri,\n"
        "        semantic_fingerprint=fingerprint,\n"
        "        already_committed=True,\n"
        "        graph_changed=changed,\n"
        "        graph_revision=_graph_revision(),\n"
        "        message=(\n"
        '            "Unique parent occurrence reused; missing owner facets filled."\n'
        "            if changed\n"
        '            else "Semantic mutation is already committed; continue."\n'
        "        ),\n"
        "        **metadata,\n"
        "    )\n\n\n"
        "def skip_semantic_obligation(obligation_id: str, reason: str) -> str:\n"
        '    """Resolve only a rejection that explicitly authorizes a policy skip."""\n'
        "    return rdf_runtime.resolve_semantic_skip(obligation_id, reason)\n\n\n"
        "def _resolve_or_create(class_local: str, label: str) -> tuple[str, bool]:\n"
        "    class_iri, creator = _REUSABLE[class_local]\n"
        "    graph = rdf_runtime.retained_graph()\n"
        "    wanted = _normalized(label)\n"
        "    for subject in graph.subjects(RDF.type, URIRef(class_iri)):\n"
        "        labels = [str(item) for item in graph.objects(subject, RDFS.label)]\n"
        "        if any(_normalized(item) == wanted for item in labels):\n"
        "            return str(subject), True\n"
        "    created = _payload(creator(label))\n"
        '    return str(created["iri"]), False\n\n\n'
        "def _link(call: Callable[..., str], subject_iri: str, object_iri: str) -> None:\n"
        "    _payload(call(subject_iri, object_iri))\n\n\n"
        "def _attach_quantity(subject_iri: str, predicate_local: str, predicate_iri: str, range_iri: str, label: str) -> None:\n"
        '    writer = getattr(relationships, f"add_{predicate_local}")\n'
        "    if _existing_link(subject_iri, predicate_iri, range_iri, label) is not None:\n"
        "        return\n"
        "    created = _payload(rdf_runtime.create_om2_quantity(range_iri, label))\n"
        '    _link(writer, subject_iri, str(created["iri"]))\n\n\n'
        "def _try_attach_quantity(subject_iri: str, predicate_local: str, predicate_iri: str, range_iri: str, label: str) -> dict | None:\n"
        "    try:\n"
        "        with rdf_runtime.atomic_graph_transaction():\n"
        "            _attach_quantity(subject_iri, predicate_local, predicate_iri, range_iri, label)\n"
        "    except Exception as exc:\n"
        "        payload = json.loads(_rejection(exc))\n"
        "        obligation_id = hashlib.sha256(f'{subject_iri}|{predicate_iri}|{label}'.encode('utf-8')).hexdigest()\n"
        '        skippable = str(payload.get("code") or "") == "INVALID_OM2_QUANTITY"\n'
        "        warning = {\n"
        '            "facet": predicate_local,\n'
        '            "omitted_facet": True,\n'
        '            "code": str(payload.get("code") or "QUANTITY_FACET_OMITTED"),\n'
        '            "message": str(payload.get("message") or exc),\n'
        '            "source_value": str(label),\n'
        '            "obligation_id": obligation_id,\n'
        '            "retryable": True,\n'
        '            "skippable": skippable,\n'
        '            "recovery": {"action": "retry_corrected_facet_or_skip_if_parser_verified_unrepresentable"},\n'
        "        }\n"
        "        rdf_runtime.register_semantic_rejection(obligation_id, warning, skippable=skippable)\n"
        "        return warning\n"
        "    return None\n\n\n"
        "def _existing_parent_member(parent_iri: str, predicate_iri: str) -> str | None:\n"
        "    graph = rdf_runtime.retained_graph()\n"
        "    found = list(graph.objects(URIRef(parent_iri), URIRef(predicate_iri)))\n"
        "    return str(found[0]) if found else None\n\n\n"
        "def _existing_ordered_member(parent_iri: str, parent_predicate_iri: str, ordering_iri: str, order: int, class_iri: str) -> str | None:\n"
        "    graph = rdf_runtime.retained_graph()\n"
        "    for member in graph.objects(URIRef(parent_iri), URIRef(parent_predicate_iri)):\n"
        "        if (member, RDF.type, URIRef(class_iri)) not in graph:\n"
        "            continue\n"
        "        if any(value.toPython() == order for value in graph.objects(member, URIRef(ordering_iri))):\n"
        "            return str(member)\n"
        "    return None\n\n\n"
        "def check_ordered_members() -> str:\n"
        "    return rdf_runtime.success_json(violations=[])\n\n\n"
        "def prepare_export_graph() -> str:\n"
        "    extra_keep_roots = [\n"
        "        str(value)\n"
        "        for value in rdf_runtime.retained_graph().objects(None, _MARKER_RESULT)\n"
        "    ]\n"
        "    return json.dumps(\n"
        "        rdf_runtime.prepare_graph_for_export(\n"
        "            _ORDERED_MEMBER_CONTRACTS,\n"
        "            extra_keep_roots=extra_keep_roots,\n"
        "        ),\n"
        "        ensure_ascii=False,\n"
        "        sort_keys=True,\n"
        "    )\n\n\n"
        "def _existing_link(subject_iri: str, predicate_iri: str, object_class_iri: str, label: str) -> str | None:\n"
        "    graph = rdf_runtime.retained_graph()\n"
        "    wanted = _normalized(label)\n"
        "    for obj in graph.objects(URIRef(subject_iri), URIRef(predicate_iri)):\n"
        "        if object_class_iri and (obj, RDF.type, URIRef(object_class_iri)) not in graph:\n"
        "            continue\n"
        "        if any(_normalized(value) == wanted for value in graph.objects(obj, RDFS.label)):\n"
        "            return str(obj)\n"
        "    return None\n\n\n"
    )
    return header + "\n".join(functions)


def _emit_create_function(tool: Mapping[str, Any], ontology: str) -> str:
    del ontology
    name = str(tool.get("name") or "")
    primitive = str(tool.get("primitive_tool") or name)
    parent = str(tool.get("parent_parameter") or "")
    ordering = _python_name(str(tool.get("ordering_property_local") or ""))
    signature = _signature(tool)
    create_args = ["label=label"]
    parent_via_primitive = bool(tool.get("parent_via_primitive"))
    parent_predicate = str(tool.get("parent_predicate_local") or "")
    if parent and parent_via_primitive:
        create_args.append(f"parent_iri={parent}")
    if ordering:
        create_args.append(f"{ordering}={ordering}")
    create_args_text = ", ".join(create_args) + _forward_datatypes(tool)
    lines = ["def " + name + "(" + signature + ") -> str:"]
    lines.append(f"    {_create_description(tool)!r}")
    binds_root = bool(tool.get("parent_binds_to_session_root", True))
    if parent and binds_root:
        lines.extend(
            [
                f"    root_binding = rdf_runtime.bind_root_argument({parent})",
                f"    {parent} = str(root_binding['effective_root_iri'])",
            ]
        )
    elif parent:
        lines.extend(
            [
                f"    root_binding = rdf_runtime.bind_parent_occurrence_argument({parent})",
                "    if not root_binding.get('effective_root_iri'):",
                "        return rdf_runtime.error_json(",
                "            code='PARENT_OCCURRENCE_UNBOUND',",
                "            message=str(root_binding.get('message') or 'parent_iri is not a parent occurrence'),",
                "            requested_root_iri=root_binding.get('requested_root_iri'),",
                "            enrichment_targets=root_binding.get('enrichment_targets'),",
                "            graph_changed=False,",
                "        )",
                f"    {parent} = str(root_binding['effective_root_iri'])",
            ]
        )
    parent_predicate_iri = str(tool.get("parent_predicate_iri") or "")
    ordering_iri = str(tool.get("ordering_property_iri") or "")
    identity = tool.get("identity_contract") or {}
    identity_kind = str(identity.get("kind") or "semantic_occurrence")
    identity_args = [
        str(value) for value in identity.get("identity_args") or [] if str(value)
    ]
    for parameter in _optional_label_parameters(tool):
        lines.append(f"    {parameter} = _optional_label({parameter})")
    defaulted_links = _defaulted_owner_links(tool)
    for _kind, item in defaulted_links:
        label_name = str(item.get("label_parameter") or "")
        if label_name:
            lines.append(f"    {label_name} = {label_name} or _optional_label(label)")
    identity_items = ", ".join(f"{arg!r}: {arg}" for arg in identity_args)
    replay_kwargs = ", root_binding=root_binding" if parent else ""
    datatype_pairs = []
    for item in tool.get("datatype_inputs") or []:
        parameter = _python_name(str(item.get("property_local") or ""))
        predicate_iri = str(item.get("property_iri") or "")
        if parameter and predicate_iri and parameter != ordering:
            datatype_pairs.append(f"({predicate_iri!r}, {parameter})")
    pairs_literal = "[" + ", ".join(datatype_pairs) + "]"
    ensure_kwarg = ", ensure=_ensure_default_links" if defaulted_links else ""
    if defaulted_links:
        lines.append("    def _ensure_default_links(owner_iri: str) -> None:")
        emitted_ensure = False
        for _kind, item in defaulted_links:
            label_name = str(item.get("label_parameter") or "")
            predicate_iri = str(item.get("predicate_iri") or "")
            if not label_name or not predicate_iri:
                continue
            emitted_ensure = True
            lines.append(
                f"        if {label_name} and not _has_outgoing(owner_iri, {predicate_iri!r}):"
            )
            lines.extend(
                _emit_materialize_owner_link(item, indent="            ", bind_resolved=False)
            )
        if not emitted_ensure:
            lines.append("        return")
    if identity_kind == "unique_parent":
        committed_return = (
            f"        return _reuse_unique_parent(committed_iri, fingerprint, {pairs_literal}{ensure_kwarg}{replay_kwargs})"
        )
    else:
        committed_return = f"        return _replay(committed_iri, fingerprint{replay_kwargs})"
    lines.extend(
        [
            f"    fingerprint = _fingerprint({name!r}, {{{identity_items}}})",
            "    committed_iri = _existing_fingerprint(fingerprint)",
            "    if committed_iri is not None:",
            committed_return,
        ]
    )
    if identity_kind == "ordered" and parent and ordering and parent_predicate_iri and ordering_iri:
        lines.extend(
            [
                f"    evidenced_iri = _existing_ordered_member({parent}, {parent_predicate_iri!r}, {ordering_iri!r}, {ordering}, {str(tool.get('owner_class_iri') or '')!r})",
                "    if evidenced_iri is not None:",
                f"        return _replay(evidenced_iri, fingerprint{replay_kwargs})",
            ]
        )
    elif identity_kind == "unique_parent" and parent and parent_predicate_iri:
        lines.extend(
            [
                f"    evidenced_iri = _existing_parent_member({parent}, {parent_predicate_iri!r})",
                "    if evidenced_iri is not None:",
                f"        return _reuse_unique_parent(evidenced_iri, fingerprint, {pairs_literal}{ensure_kwarg}{replay_kwargs})",
            ]
        )
    lines.extend(
        [
            "    before = set(rdf_runtime.retained_graph())",
            "    facet_warnings: list[dict] = []",
            "    try:",
            "        with rdf_runtime.atomic_graph_transaction():",
            "            committed_iri = _existing_fingerprint(fingerprint)",
            "            if committed_iri is not None:",
            (
                f"                return _reuse_unique_parent(committed_iri, fingerprint, {pairs_literal}{ensure_kwarg}{replay_kwargs})"
                if identity_kind == "unique_parent"
                else f"                return _replay(committed_iri, fingerprint{replay_kwargs})"
            ),
            "            resolved: dict[str, str] = {}",
            f"            created = _payload(entities.{primitive}({create_args_text}))",
            '            owner_iri = str(created["iri"])',
        ]
    )
    if parent and parent_predicate and not parent_via_primitive:
        lines.extend(
            [
                f"            _link(relationships.add_{parent_predicate}, {parent}, owner_iri)",
            ]
        )
    for item in tool.get("quantities") or []:
        parameter = str(item.get("parameter") or "")
        predicate = str(item.get("predicate_local") or "")
        predicate_iri = str(item.get("predicate_iri") or "")
        range_iri = str(item.get("range_iri") or "")
        lines.extend(
            [
                f"            if {parameter}:",
                f"                warning = _try_attach_quantity(owner_iri, {predicate!r}, {predicate_iri!r}, {range_iri!r}, {parameter})",
                "                if warning is not None:",
                "                    facet_warnings.append(warning)",
            ]
        )
    for item in tool.get("parent_quantities") or []:
        parameter = str(item.get("parameter") or "")
        predicate = str(item.get("predicate_local") or "")
        predicate_iri = str(item.get("predicate_iri") or "")
        range_iri = str(item.get("range_iri") or "")
        if parent:
            lines.extend(
                [
                    f"            if {parameter}:",
                    f"                warning = _try_attach_quantity({parent}, {predicate!r}, {predicate_iri!r}, {range_iri!r}, {parameter})",
                    "                if warning is not None:",
                    "                    facet_warnings.append(warning)",
                ]
            )
    for item in tool.get("fresh_dependents") or []:
        label_name = str(item.get("label_parameter") or "")
        predicate = str(item.get("predicate_local") or "")
        create_tool = str(item.get("create_tool") or "")
        extra = []
        for datatype in item.get("datatype_inputs") or []:
            parameter = str(datatype.get("parameter_name") or "")
            property_local = _python_name(str(datatype.get("property_local") or ""))
            if parameter and property_local:
                extra.append(f"{property_local}={parameter}")
        extra_args = (", " + ", ".join(extra)) if extra else ""
        lines.extend(
            [
                f"            if {label_name}:",
                f"                dependent = _payload(entities.{create_tool}(label={label_name}{extra_args}))",
                f"                resolved[{predicate!r}] = str(dependent['iri'])",
                f"                _link(relationships.add_{predicate}, owner_iri, resolved[{predicate!r}])",
            ]
        )
    for item in tool.get("reusable_links") or []:
        label_name = str(item.get("label_parameter") or "")
        predicate = str(item.get("predicate_local") or "")
        target_local = str(item.get("target_class_local") or "")
        create_tool = str(item.get("create_tool") or "")
        datatype_inputs = list(item.get("datatype_inputs") or [])
        if item.get("create_fresh_with_datatypes") and create_tool:
            extra = []
            for datatype in datatype_inputs:
                parameter = str(datatype.get("parameter_name") or "")
                property_local = _python_name(str(datatype.get("property_local") or ""))
                if parameter and property_local:
                    extra.append(f"{property_local}={parameter}")
            extra_args = (", " + ", ".join(extra)) if extra else ""
            lines.extend(
                [
                    f"            if {label_name}:",
                    f"                represented = _payload(entities.{create_tool}(label={label_name}{extra_args}))",
                    f"                resolved[{predicate!r}] = str(represented['iri'])",
                    f"                _link(relationships.add_{predicate}, owner_iri, resolved[{predicate!r}])",
                ]
            )
        else:
            lines.extend(
                [
                    f"            if {label_name}:",
                    f"                object_iri, _ = _resolve_or_create({target_local!r}, {label_name})",
                    f"                resolved[{predicate!r}] = object_iri",
                    f"                _link(relationships.add_{predicate}, owner_iri, object_iri)",
                ]
            )
    for item in tool.get("nested_reusable_links") or []:
        label_name = str(item.get("label_parameter") or "")
        parent_pred = str(item.get("parent_predicate_local") or "")
        child_pred = str(item.get("child_predicate_local") or "")
        target_local = str(item.get("target_class_local") or "")
        lines.extend(
            [
                f"            if {label_name} and resolved.get({parent_pred!r}):",
                f"                nested_iri, _ = _resolve_or_create({target_local!r}, {label_name})",
                f"                _link(relationships.add_{child_pred}, resolved[{parent_pred!r}], nested_iri)",
            ]
        )
    lines.extend(
        [
            "            _commit_fingerprint(fingerprint, owner_iri)",
            "        return rdf_runtime.success_json(",
            "            iri=owner_iri,",
            "            semantic_fingerprint=fingerprint,",
            "            already_committed=False,",
            "            graph_changed=set(rdf_runtime.retained_graph()) != before,",
            "            graph_revision=_graph_revision(),",
            "            facet_warnings=facet_warnings,",
            "            omitted_facet=bool(facet_warnings),",
            '            message="Occurrence created.",',
        ]
    )
    if parent:
        lines.append("            root_binding=root_binding,")
    lines.append("        )")
    lines.extend(
        [
            "    except Exception as exc:",
            f"        return _rejection(exc, fingerprint, tool_name={name!r})",
            "",
        ]
    )
    return "\n".join(lines)


def _emit_linker_function(linker: Mapping[str, Any]) -> str:
    name = str(linker.get("name") or "")
    predicate = str(linker.get("predicate_local") or "")
    predicate_iri = str(linker.get("predicate_iri") or "")
    target_local = str(linker.get("object_class_local") or "")
    target_iri = str(linker.get("object_class_iri") or "")
    quantity_range = str(linker.get("quantity_range_iri") or "")
    if quantity_range:
        return f'''def {name}(subject_iri: str, object_label: str) -> str:
    """Link from the exact bound root IRI; never pass a child occurrence handle."""
    root_binding = rdf_runtime.bind_root_argument(subject_iri)
    subject_iri = str(root_binding["effective_root_iri"])
    object_label = _optional_label(object_label)
    if object_label is None:
        return rdf_runtime.error_json(
            code="MISSING_OBJECT_DESCRIPTOR",
            message="A semantic object descriptor is required.",
            already_committed=False,
            graph_changed=False,
        )
    fingerprint = _fingerprint({name!r}, {{"subject_iri": subject_iri, "object_label": object_label}})
    committed_iri = _existing_fingerprint(fingerprint)
    if committed_iri is not None:
        return _replay(committed_iri, fingerprint, root_binding=root_binding)
    evidenced_iri = _existing_link(subject_iri, {predicate_iri!r}, {quantity_range!r}, object_label)
    if evidenced_iri is not None:
        return _replay(subject_iri, fingerprint, root_binding=root_binding)
    before = set(rdf_runtime.retained_graph())
    try:
        with rdf_runtime.atomic_graph_transaction():
            committed_iri = _existing_fingerprint(fingerprint)
            if committed_iri is not None:
                return _replay(committed_iri, fingerprint, root_binding=root_binding)
            _attach_quantity(subject_iri, {predicate!r}, {predicate_iri!r}, {quantity_range!r}, object_label)
            _commit_fingerprint(fingerprint, subject_iri)
        return rdf_runtime.success_json(
            iri=subject_iri,
            semantic_fingerprint=fingerprint,
            already_committed=False,
            graph_changed=set(rdf_runtime.retained_graph()) != before,
            graph_revision=_graph_revision(),
            root_binding=root_binding,
            message="Root quantity linked.",
        )
    except Exception as exc:
        return _rejection(exc, fingerprint, allow_skip=True, tool_name={name!r})
'''
    return f'''def {name}(subject_iri: str, object_label: str) -> str:
    """Link from the exact bound root IRI; never pass a child occurrence handle."""
    root_binding = rdf_runtime.bind_root_argument(subject_iri)
    subject_iri = str(root_binding["effective_root_iri"])
    object_label = _optional_label(object_label)
    if object_label is None:
        return rdf_runtime.error_json(
            code="MISSING_OBJECT_DESCRIPTOR",
            message="A semantic object descriptor is required.",
            already_committed=False,
            graph_changed=False,
        )
    fingerprint = _fingerprint({name!r}, {{"subject_iri": subject_iri, "object_label": object_label}})
    committed_iri = _existing_fingerprint(fingerprint)
    if committed_iri is not None:
        return _replay(committed_iri, fingerprint, root_binding=root_binding)
    evidenced_iri = _existing_link(subject_iri, {predicate_iri!r}, {target_iri!r}, object_label)
    if evidenced_iri is not None:
        return _replay(subject_iri, fingerprint, root_binding=root_binding)
    before = set(rdf_runtime.retained_graph())
    try:
        with rdf_runtime.atomic_graph_transaction():
            committed_iri = _existing_fingerprint(fingerprint)
            if committed_iri is not None:
                return _replay(committed_iri, fingerprint, root_binding=root_binding)
            object_iri, reused = _resolve_or_create({target_local!r}, object_label)
            _link(relationships.add_{predicate}, subject_iri, object_iri)
            _commit_fingerprint(fingerprint, subject_iri)
        return rdf_runtime.success_json(
            iri=subject_iri,
            object_iri=object_iri,
            reused=reused,
            semantic_fingerprint=fingerprint,
            already_committed=False,
            graph_changed=set(rdf_runtime.retained_graph()) != before,
            graph_revision=_graph_revision(),
            root_binding=root_binding,
            message="Reusable object linked.",
        )
    except Exception as exc:
        return _rejection(exc, fingerprint, allow_skip=True, tool_name={name!r})
'''


def emit_occurrence_main(
    context: Any,
    compiled: Mapping[str, Any] | None = None,
) -> str:
    units = compiled or (getattr(context, "contract", {}) or {}).get(
        "occurrence_surface_units"
    ) or {}
    ontology = _ontology_symbol(context.ontology.name)
    instruction = str(units.get("instruction") or "").strip() or compile_fallback_instruction(
        units
    )
    instruction_literal = json.dumps(instruction)
    create_regs = "\n".join(
        f"mcp.tool(name={str(item.get('name') or '')!r})("
        f"rdf_runtime.wrap_public_tool(operations.{item.get('name')}))"
        for item in units.get("public_tools") or []
    )
    link_regs = "\n".join(
        f"mcp.tool(name={str(item.get('name') or '')!r})("
        f"rdf_runtime.wrap_public_tool(operations.{item.get('name')}))"
        for item in units.get("public_linkers") or []
    )
    return f'''from __future__ import annotations

"""Generated occurrence MCP surface. Do not edit by hand."""

import json

from fastmcp import FastMCP

from . import _fixed_rdf_runtime as rdf_runtime
from . import {ontology}_occurrence_operations as operations


mcp = FastMCP(name={context.ontology.name + "-occurrence-surface"!r})


@mcp.prompt(name="instruction")
def instruction() -> str:
    return {instruction_literal}


def export_memory(doi: str, top_level_entity_name: str) -> str:
    result_json = operations.prepare_export_graph()
    try:
        parsed = json.loads(result_json)
    except Exception:
        return rdf_runtime.error_json(
            code="ordered_member_integrity_failed",
            message="Invalid ordered-member check result.",
        )
    if str(parsed.get("status", "")).lower() != "ok":
        return json.dumps(parsed)
    exported = json.loads(rdf_runtime.export_memory(doi, top_level_entity_name))
    exported["export_repairs"] = parsed
    return json.dumps(exported, ensure_ascii=False, sort_keys=True)


mcp.tool(name="init_memory")(rdf_runtime.init_memory)
mcp.tool(name="inspect_ordered_members")(operations.check_ordered_members)
mcp.tool(name="export_memory")(export_memory)
mcp.tool(name="skip_semantic_obligation")(operations.skip_semantic_obligation)
{create_regs}
{link_regs}


if __name__ == "__main__":
    mcp.run()
'''
