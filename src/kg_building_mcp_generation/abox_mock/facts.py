"""Project occurrence-surface A-Box facts independently of generated MCP."""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from rdflib import Graph, Literal, URIRef
from rdflib.namespace import RDF, RDFS

from src.extraction_prompt_generation.compile.operation_units import _python_name
from src.kg_building_mcp_generation.abox_mock.instantiate import (
    NESTED_SENTINEL,
    ROOT_SENTINEL,
    MockCall,
    MockScript,
    decode_nested_ref,
)

TYPE = str(RDF.type)
LABEL = str(RDFS.label)


def encode_key(parts: tuple[Any, ...]) -> str:
    return repr(parts)


def _parent_key(script: MockScript, call: MockCall, compiled: Mapping[str, Any]) -> str:
    tools = {str(item.get("name")): item for item in compiled.get("public_tools") or []}
    tool = tools.get(call.name) or {}
    parent = str(tool.get("parent_parameter") or "")
    raw = call.arguments.get(parent) if parent else None
    if raw == ROOT_SENTINEL or parent == "":
        return encode_key(("root",))
    if isinstance(raw, str) and raw.startswith("$call:"):
        name = raw.split(":", 1)[1]
        for item in script.calls:
            if item.kind == "create" and item.name == name:
                return encode_key(item.identity_key)
    if isinstance(raw, str) and raw.startswith(NESTED_SENTINEL):
        host, predicate_iri, label = decode_nested_ref(raw)
        return encode_key(("nested", host, predicate_iri, label))
    return encode_key(("root",))


def expected_facts(
    script: MockScript,
    compiled: Mapping[str, Any],
) -> set[tuple[str, str, str]]:
    """Occurrence-surface facts implied by the mock script. Does not call MCP."""
    tools = {str(item.get("name")): item for item in compiled.get("public_tools") or []}
    linkers = {str(item.get("name")): item for item in compiled.get("public_linkers") or []}
    facts: set[tuple[str, str, str]] = set()
    root = encode_key(("root",))
    if script.top_class_iri:
        facts.add((root, TYPE, script.top_class_iri))
    facts.add((root, LABEL, script.root_label))
    for call in script.calls:
        if call.kind != "create":
            continue
        tool = tools.get(call.name) or {}
        owner = encode_key(call.identity_key)
        class_iri = str(tool.get("owner_class_iri") or "")
        if class_iri:
            facts.add((owner, TYPE, class_iri))
        facts.add((owner, LABEL, str(call.arguments.get("label") or "")))
        parent_pred = str(tool.get("parent_predicate_iri") or "")
        if parent_pred and tool.get("parent_parameter"):
            facts.add(( _parent_key(script, call, compiled), parent_pred, owner))
        ordering = _python_name(str(tool.get("ordering_property_local") or ""))
        ordering_iri = str(tool.get("ordering_property_iri") or "")
        if ordering and ordering_iri and ordering in call.arguments:
            facts.add((owner, ordering_iri, str(call.arguments[ordering])))
        for item in tool.get("datatype_inputs") or []:
            name = _python_name(str(item.get("property_local") or ""))
            pred = str(item.get("property_iri") or "")
            if name and pred and name != ordering and name in call.arguments:
                facts.add((owner, pred, _literal_text(call.arguments[name])))
        for item in list(tool.get("quantities") or []) + list(
            tool.get("parent_quantities") or []
        ):
            name = str(item.get("parameter") or "")
            pred = str(item.get("predicate_iri") or "")
            range_iri = str(item.get("range_iri") or "")
            lexeme = call.arguments.get(name)
            if not name or not pred or not lexeme:
                continue
            attach = owner
            if item in (tool.get("parent_quantities") or []):
                attach = _parent_key(script, call, compiled)
            qkey = encode_key(("quantity", owner, pred, str(lexeme)))
            facts.add((attach, pred, qkey))
            if range_iri:
                facts.add((qkey, TYPE, range_iri))
            facts.add((qkey, LABEL, str(lexeme)))
        for item in tool.get("fresh_dependents") or []:
            _add_dependent_facts(facts, call, owner, item)
        for item in tool.get("reusable_links") or []:
            _add_dependent_facts(facts, call, owner, item, reusable=True)
        for item in tool.get("nested_reusable_links") or []:
            label_name = str(item.get("label_parameter") or "")
            pred = str(item.get("child_predicate_iri") or "")
            parent_pred = str(item.get("parent_predicate_local") or "")
            lexeme = call.arguments.get(label_name)
            if not label_name or not pred or not lexeme:
                continue
            parent_item = next(
                (
                    link
                    for group in ("fresh_dependents", "reusable_links")
                    for link in tool.get(group) or []
                    if str(link.get("predicate_local") or "") == parent_pred
                ),
                None,
            )
            host = owner
            if parent_item:
                host_label = call.arguments.get(str(parent_item.get("label_parameter") or ""))
                host = encode_key(
                    (
                        "dependent",
                        owner,
                        str(parent_item.get("predicate_iri") or ""),
                        str(host_label),
                    )
                )
            nested = encode_key(("nested", owner, pred, str(lexeme)))
            facts.add((host, pred, nested))
            facts.add((nested, LABEL, str(lexeme)))
    for call in script.calls:
        if call.kind != "link":
            continue
        linker = linkers.get(call.name) or {}
        pred = str(linker.get("predicate_iri") or "")
        class_iri = str(linker.get("object_class_iri") or "")
        label = str(call.arguments.get("object_label") or "")
        if not pred or not label:
            continue
        obj = encode_key(("linked", class_iri, label))
        facts.add((root, pred, obj))
        if class_iri:
            facts.add((obj, TYPE, class_iri))
        facts.add((obj, LABEL, label))
    return facts


def _add_dependent_facts(
    facts: set[tuple[str, str, str]],
    call: MockCall,
    owner: str,
    item: Mapping[str, Any],
    *,
    reusable: bool = False,
) -> None:
    del reusable
    label_name = str(item.get("label_parameter") or "")
    pred = str(item.get("predicate_iri") or "")
    class_iri = str(item.get("target_class_iri") or "")
    lexeme = call.arguments.get(label_name)
    if not label_name or not pred or not lexeme:
        return
    dep = encode_key(("dependent", owner, pred, str(lexeme)))
    facts.add((owner, pred, dep))
    if class_iri:
        facts.add((dep, TYPE, class_iri))
    facts.add((dep, LABEL, str(lexeme)))
    for datatype in item.get("datatype_inputs") or []:
        name = str(datatype.get("parameter_name") or "")
        dt_pred = str(datatype.get("property_iri") or "")
        if name and dt_pred and name in call.arguments:
            facts.add((dep, dt_pred, _literal_text(call.arguments[name])))


def _literal_text(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, Literal):
        python_value = value.toPython() if hasattr(value, "toPython") else value
        if isinstance(python_value, bool):
            return "true" if python_value else "false"
        return str(python_value)
    return str(value)


def _match_object_by_label(graph: Graph, owner: URIRef, predicate: str, label: str) -> str | None:
    wanted = " ".join(str(label).casefold().split())
    for obj in graph.objects(owner, URIRef(predicate)):
        if not isinstance(obj, URIRef):
            continue
        labels = [_literal_text(item) for item in graph.objects(obj, RDFS.label)]
        if any(" ".join(item.casefold().split()) == wanted for item in labels):
            return str(obj)
    return None


def actual_facts(
    script: MockScript,
    compiled: Mapping[str, Any],
    graph: Graph,
    *,
    iri_by_call: Mapping[str, str],
    root_iri: str,
) -> set[tuple[str, str, str]]:
    """Project the same occurrence-surface facts from a materialized graph."""
    tools = {str(item.get("name")): item for item in compiled.get("public_tools") or []}
    linkers = {str(item.get("name")): item for item in compiled.get("public_linkers") or []}
    facts: set[tuple[str, str, str]] = set()
    root_key = encode_key(("root",))
    root = URIRef(root_iri)
    for type_iri in graph.objects(root, RDF.type):
        facts.add((root_key, TYPE, str(type_iri)))
    labels = [_literal_text(item) for item in graph.objects(root, RDFS.label)]
    if labels:
        facts.add((root_key, LABEL, labels[0]))
    elif script.root_label:
        facts.add((root_key, LABEL, script.root_label))
    for call in script.calls:
        if call.kind != "create":
            continue
        iri = iri_by_call.get(call.name)
        if not iri:
            continue
        tool = tools.get(call.name) or {}
        owner_key = encode_key(call.identity_key)
        subject = URIRef(iri)
        for type_iri in graph.objects(subject, RDF.type):
            facts.add((owner_key, TYPE, str(type_iri)))
        for label in graph.objects(subject, RDFS.label):
            facts.add((owner_key, LABEL, _literal_text(label)))
            break
        parent_pred = str(tool.get("parent_predicate_iri") or "")
        if parent_pred:
            facts.add((_parent_key(script, call, compiled), parent_pred, owner_key))
        ordering = _python_name(str(tool.get("ordering_property_local") or ""))
        ordering_iri = str(tool.get("ordering_property_iri") or "")
        if ordering_iri:
            for value in graph.objects(subject, URIRef(ordering_iri)):
                facts.add((owner_key, ordering_iri, str(value.toPython() if hasattr(value, "toPython") else value)))
                break
        for item in tool.get("datatype_inputs") or []:
            name = _python_name(str(item.get("property_local") or ""))
            pred = str(item.get("property_iri") or "")
            if not pred or name == ordering:
                continue
            for value in graph.objects(subject, URIRef(pred)):
                facts.add((owner_key, pred, _literal_text(value)))
                break
        for item in list(tool.get("quantities") or []) + list(
            tool.get("parent_quantities") or []
        ):
            name = str(item.get("parameter") or "")
            pred = str(item.get("predicate_iri") or "")
            lexeme = call.arguments.get(name)
            if not pred or not lexeme:
                continue
            attach_iri = iri
            attach_key = owner_key
            if item in (tool.get("parent_quantities") or []):
                attach_iri = root_iri if _parent_key(script, call, compiled) == root_key else iri_by_call.get(
                    str(call.arguments.get(str(tool.get("parent_parameter") or ""))).split(":")[-1],
                    root_iri,
                )
                attach_key = _parent_key(script, call, compiled)
            found = _match_object_by_label(graph, URIRef(attach_iri), pred, str(lexeme))
            if not found:
                continue
            qkey = encode_key(("quantity", owner_key, pred, str(lexeme)))
            facts.add((attach_key, pred, qkey))
            node = URIRef(found)
            for type_iri in graph.objects(node, RDF.type):
                facts.add((qkey, TYPE, str(type_iri)))
            for label in graph.objects(node, RDFS.label):
                facts.add((qkey, LABEL, _literal_text(label)))
                break
        for item in tool.get("fresh_dependents") or []:
            _project_dependent(facts, graph, call, owner_key, subject, item)
        for item in tool.get("reusable_links") or []:
            _project_dependent(facts, graph, call, owner_key, subject, item)
        for item in tool.get("nested_reusable_links") or []:
            label_name = str(item.get("label_parameter") or "")
            pred = str(item.get("child_predicate_iri") or "")
            parent_pred = str(item.get("parent_predicate_local") or "")
            lexeme = call.arguments.get(label_name)
            if not pred or not lexeme:
                continue
            parent_item = next(
                (
                    link
                    for group in ("fresh_dependents", "reusable_links")
                    for link in tool.get(group) or []
                    if str(link.get("predicate_local") or "") == parent_pred
                ),
                None,
            )
            host = subject
            host_key = owner_key
            if parent_item:
                parent_label = call.arguments.get(str(parent_item.get("label_parameter") or ""))
                parent_pred_iri = str(parent_item.get("predicate_iri") or "")
                found_host = _match_object_by_label(
                    graph, subject, parent_pred_iri, str(parent_label)
                )
                if found_host:
                    host = URIRef(found_host)
                    host_key = encode_key(
                        ("dependent", owner_key, parent_pred_iri, str(parent_label))
                    )
            found = _match_object_by_label(graph, host, pred, str(lexeme))
            if not found:
                continue
            nested = encode_key(("nested", owner_key, pred, str(lexeme)))
            facts.add((host_key, pred, nested))
            for label in graph.objects(URIRef(found), RDFS.label):
                facts.add((nested, LABEL, _literal_text(label)))
                break
    for call in script.calls:
        if call.kind != "link":
            continue
        linker = linkers.get(call.name) or {}
        pred = str(linker.get("predicate_iri") or "")
        class_iri = str(linker.get("object_class_iri") or "")
        label = str(call.arguments.get("object_label") or "")
        if not pred or not label:
            continue
        found = _match_object_by_label(graph, root, pred, label)
        if not found:
            continue
        obj = encode_key(("linked", class_iri, label))
        facts.add((root_key, pred, obj))
        node = URIRef(found)
        for type_iri in graph.objects(node, RDF.type):
            facts.add((obj, TYPE, str(type_iri)))
        for text in graph.objects(node, RDFS.label):
            facts.add((obj, LABEL, _literal_text(text)))
            break
    return facts


def _project_dependent(
    facts: set[tuple[str, str, str]],
    graph: Graph,
    call: MockCall,
    owner_key: str,
    subject: URIRef,
    item: Mapping[str, Any],
) -> None:
    label_name = str(item.get("label_parameter") or "")
    pred = str(item.get("predicate_iri") or "")
    lexeme = call.arguments.get(label_name)
    if not pred or not lexeme:
        return
    found = _match_object_by_label(graph, subject, pred, str(lexeme))
    if not found:
        return
    dep = encode_key(("dependent", owner_key, pred, str(lexeme)))
    facts.add((owner_key, pred, dep))
    node = URIRef(found)
    for type_iri in graph.objects(node, RDF.type):
        facts.add((dep, TYPE, str(type_iri)))
    for text in graph.objects(node, RDFS.label):
        facts.add((dep, LABEL, _literal_text(text)))
        break
    for datatype in item.get("datatype_inputs") or []:
        dt_pred = str(datatype.get("property_iri") or "")
        if not dt_pred:
            continue
        for value in graph.objects(node, URIRef(dt_pred)):
            facts.add((dep, dt_pred, _literal_text(value)))
            break


def compare_facts(
    expected: Iterable[tuple[str, str, str]],
    actual: Iterable[tuple[str, str, str]],
) -> dict[str, list[tuple[str, str, str]]]:
    expected_set = set(expected)
    actual_set = set(actual)
    missing = sorted(expected_set - actual_set)
    extra = sorted(actual_set - expected_set)
    return {"missing": missing, "extra": extra}
