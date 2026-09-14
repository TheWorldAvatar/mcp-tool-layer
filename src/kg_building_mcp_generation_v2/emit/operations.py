"""Emit occurrence operations module text."""

from __future__ import annotations

from typing import Any, Mapping

from src.extraction_prompt_generation.compile.operation_units import (
    _python_name,
)
from src.kg_building_mcp_generation_v2.emit.sidecars import (
    _create_description,
    _defaulted_owner_links,
    _emit_ensure_owner_link,
    _forward_datatypes,
    _ontology_symbol,
    _optional_label_parameters,
    _owner_facet_links,
    _signature,
)
from src.kg_building_mcp_generation_v2.overlay import ACTIVE_SURFACE
from src.kg_building_mcp_generation_v2.overlay.quantity_runtime_emit import (
    link_om2_quantity_source,
    overlay_attach_source,
    overlay_replay_source,
    quantity_runtime_source,
)
from src.kg_building_mcp_generation_v2.overlay.quantity_surface import (
    compile_quantity_surface,
)

def emit_occurrence_operations(
    context: Any,
    compiled: Mapping[str, Any] | None = None,
) -> str:
    units = compiled or (getattr(context, "contract", {}) or {}).get(
        "occurrence_surface_units"
    ) or {}
    surface = compile_quantity_surface(units, context=context)
    ACTIVE_SURFACE.clear()
    ACTIVE_SURFACE.update(surface)
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
    if surface.get("facets"):
        functions.append(link_om2_quantity_source())
    header = (
        "from __future__ import annotations\n\n"
        '"""Generated occurrence MCP operations. Do not edit by hand."""\n\n'
        "import hashlib\n"
        "import json\n"
        "import re\n"
        "from typing import Callable\n\n"
        "from rdflib import Literal, RDF, RDFS, URIRef\n\n"
        "from . import _fixed_om2_runtime as om2_runtime\n"
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
        "    return json.dumps(\n"
        "        rdf_runtime.prepare_graph_for_export(\n"
        "            _ORDERED_MEMBER_CONTRACTS,\n"
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
    if surface.get("facets"):
        header = _inject_overlay_runtime(header, surface)
    return header + "\n".join(functions)


def _inject_overlay_runtime(header: str, surface: Mapping[str, Any]) -> str:
    replay_at = header.find("def _replay(")
    apply_at = header.find("def _apply_missing_datatypes(")
    reuse_at = header.find("def _reuse_unique_parent(")
    skip_at = header.find("def skip_semantic_obligation(")
    attach_at = header.find("def _attach_quantity(")
    parent_at = header.find("def _existing_parent_member(")
    if min(replay_at, apply_at, reuse_at, skip_at, attach_at, parent_at) < 0:
        raise RuntimeError("occurrence operations header missing quantity helpers")
    prefix = header[:replay_at]
    apply_block = header[apply_at:reuse_at]
    skip_through_link = header[skip_at:attach_at]
    suffix = header[parent_at:]
    return (
        prefix
        + apply_block
        + quantity_runtime_source(surface).strip()
        + "\n\n\n"
        + overlay_replay_source().strip()
        + "\n\n\n"
        + skip_through_link
        + overlay_attach_source().strip()
        + "\n\n\n"
        + suffix
    )


def _quantity_replay_kwargs(tool: Mapping[str, Any], parent: str) -> str:
    owner = [
        str(item.get("parameter") or "")
        for item in tool.get("quantities") or []
        if str(item.get("parameter") or "")
    ]
    nested = [
        str(item.get("parameter") or "")
        for item in tool.get("parent_quantities") or []
        if str(item.get("parameter") or "")
    ]
    parts: list[str] = []
    if owner:
        literal = "{" + ", ".join(f"{name!r}: {name}" for name in owner) + "}"
        parts.append(f"quantities={literal}")
    if nested:
        literal = "{" + ", ".join(f"{name!r}: {name}" for name in nested) + "}"
        parts.append(f"parent_quantities={literal}")
        if parent:
            parts.append(f"quantity_owner_iri={parent}")
    if not parts:
        return ""
    return ", " + ", ".join(parts)


def _append_ensure_callback(
    lines: list[str],
    *,
    fn_name: str,
    links: list[tuple[str, Mapping[str, Any]]],
) -> str:
    emitted = False
    body = [f"    def {fn_name}(owner_iri: str) -> None:"]
    for _kind, item in links:
        label_name = str(item.get("label_parameter") or "")
        predicate_iri = str(item.get("predicate_iri") or "")
        if not label_name or not predicate_iri:
            continue
        emitted = True
        body.append(
            f"        if {label_name} and not _has_outgoing(owner_iri, {predicate_iri!r}):"
        )
        body.extend(_emit_ensure_owner_link(item, indent="            "))
    if not emitted:
        return ""
    lines.extend(body)
    return f", ensure={fn_name}"


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
    if identity_kind != "adopted_focus":
        for _kind, item in defaulted_links:
            label_name = str(item.get("label_parameter") or "")
            if label_name:
                lines.append(f"    {label_name} = {label_name} or _optional_label(label)")
    identity_items = ", ".join(f"{arg!r}: {arg}" for arg in identity_args)
    replay_kwargs = (
        ", root_binding=root_binding" if parent else ""
    ) + _quantity_replay_kwargs(tool, parent)
    datatype_pairs = []
    for item in tool.get("datatype_inputs") or []:
        parameter = _python_name(str(item.get("property_local") or ""))
        predicate_iri = str(item.get("property_iri") or "")
        if parameter and predicate_iri and parameter != ordering:
            datatype_pairs.append(f"({predicate_iri!r}, {parameter})")
    pairs_literal = "[" + ", ".join(datatype_pairs) + "]"
    if identity_kind == "adopted_focus":
        ensure_kwarg = _append_ensure_callback(
            lines,
            fn_name="_ensure_missing_owner_facets",
            links=_owner_facet_links(tool),
        )
    else:
        ensure_kwarg = _append_ensure_callback(
            lines,
            fn_name="_ensure_default_links",
            links=defaulted_links,
        )
    if identity_kind in {"unique_parent", "adopted_focus"}:
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
    elif identity_kind == "adopted_focus":
        lines.extend(
            [
                f"    evidenced_iri = rdf_runtime.bound_enrichment_target_iri({str(tool.get('owner_class_iri') or '')!r})",
                "    if evidenced_iri:",
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
                if identity_kind in {"unique_parent", "adopted_focus"}
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

