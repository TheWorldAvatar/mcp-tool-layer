"""Independent per-item/per-aspect LLM panels for RDF framework integrity."""

from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any, Callable

from rdflib import Graph, Literal, RDF, URIRef

from src.agents.scripts_and_prompts_generation.level1_code_repair import (
    LLMJsonResult,
)


_ABSOLUTE_IRI = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:[^\s<>]*$")
_ORDER_HINT = re.compile(r"(?:^|[_-])(order|index|sequence)(?:$|[_-])", re.I)
_ITEM_HEADER = re.compile(r"^(?P<class>.+?)(?:\s*\((?P<marker>[^()]*)\))?$")


def _local_name(value: str) -> str:
    text = str(value or "").strip()
    return re.split(r"[/#]", text)[-1] if text else ""


@dataclass(frozen=True)
class SourceItem:
    item_id: str
    class_hint: str
    marker: str
    evidence: str
    fields: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class Aspect:
    aspect_id: str
    kind: str
    field_name: str = ""
    field_value: str = ""


def _parse_ref_entity_relation_items(payload: dict[str, Any]) -> list[SourceItem] | None:
    """Turn ref-entity-relations.v1 JSON into the shared hint-item ledger."""
    entities = payload.get("entities")
    relations = payload.get("relations")
    if not isinstance(entities, list) and not isinstance(relations, list):
        return None
    items: list[SourceItem] = []
    for index, raw in enumerate(entities if isinstance(entities, list) else [], start=1):
        if not isinstance(raw, dict):
            continue
        ref = str(raw.get("ref") or "").strip()
        class_hint = str(raw.get("class") or "").strip()
        label = str(raw.get("label") or "").strip()
        datatype_properties = raw.get("datatype_properties") or {}
        fields: list[tuple[str, str]] = []
        if isinstance(datatype_properties, dict):
            for key, value in datatype_properties.items():
                key_text = str(key).strip()
                value_text = "" if value is None else str(value).strip()
                if key_text and value_text:
                    fields.append((key_text, value_text))
        items.append(
            SourceItem(
                item_id=ref or label or f"entity-{index}",
                class_hint=class_hint,
                marker=ref or label,
                evidence=json.dumps(raw, ensure_ascii=False),
                fields=tuple(fields),
            )
        )
    # Relation rows stay in the raw JSON for the judge, but they are not
    # closed-list items: subject_ref/object_ref are interchange tokens, not
    # A-Box IRIs, and treating them as items makes presence fail after a
    # successful add_has* call.
    return items or None


def _unique_source_items(items: list[SourceItem]) -> list[SourceItem]:
    counts: dict[str, int] = {}
    unique_items: list[SourceItem] = []
    for item in items:
        counts[item.item_id] = counts.get(item.item_id, 0) + 1
        occurrence = counts[item.item_id]
        unique_items.append(
            item
            if occurrence == 1
            else SourceItem(
                item_id=f"{item.item_id} [occurrence {occurrence}]",
                class_hint=item.class_hint,
                marker=item.marker,
                evidence=item.evidence,
                fields=item.fields,
            )
        )
    return unique_items


def parse_semantic_hint_items(document_text: str) -> list[SourceItem]:
    """Split generic semantic-text.v1 headings and key/value fields."""
    text = str(document_text or "").strip()
    text = re.sub(r"^```[A-Za-z0-9_-]*\s*", "", text, count=1)
    if text.startswith("{"):
        try:
            payload, _ = json.JSONDecoder().raw_decode(text)
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict):
            json_items = _parse_ref_entity_relation_items(payload)
            if json_items:
                return _unique_source_items(json_items)
    if text.startswith("SEMANTIC_HINTS_V1"):
        text = text[len("SEMANTIC_HINTS_V1") :].lstrip()
    blocks = [
        block.strip()
        for block in re.split(r"\n\s*\n+", text)
        if block.strip()
    ]
    items: list[SourceItem] = []
    for index, block in enumerate(blocks, start=1):
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if not lines:
            continue
        header = lines[0]
        class_hint = ""
        marker = ""
        field_lines = lines[1:]
        if ":" in header:
            first_key, first_value = (part.strip() for part in header.split(":", 1))
            if first_key and first_key[:1].isupper():
                class_hint = first_key
                marker = first_value
            else:
                field_lines = lines
        else:
            match = _ITEM_HEADER.fullmatch(header)
            class_hint = match.group("class").strip() if match else header
            marker = (
                match.group("marker").strip()
                if match and match.group("marker")
                else ""
            )
        if not class_hint and field_lines == lines:
            for line in lines:
                if ":" not in line:
                    continue
                key, value = (part.strip() for part in line.split(":", 1))
                if not key or not value:
                    continue
                items.append(
                    SourceItem(
                        item_id=line,
                        class_hint="",
                        marker="",
                        evidence=line,
                        fields=((key, value),),
                    )
                )
            continue
        fields: list[tuple[str, str]] = []
        for line in field_lines:
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            if key.strip() and value.strip():
                fields.append((key.strip(), value.strip()))
        items.append(
            SourceItem(
                item_id=header or f"source-item-{index}",
                class_hint=class_hint,
                marker=marker,
                evidence=block,
                fields=tuple(fields),
            )
        )
    if items:
        return _unique_source_items(items)
    return [
        SourceItem(
            item_id="source-item-1",
            class_hint="",
            marker="",
            evidence=str(document_text or "").strip(),
            fields=(),
        )
    ]


def aspects_for_item(
    item: SourceItem,
    ontology_contract: dict[str, Any],
) -> list[Aspect]:
    """Create generic, independently judgeable structural aspects."""
    aspects = (
        [Aspect(aspect_id="entity_presence", kind="entity_presence")]
        if item.class_hint
        else []
    )
    ordering_locals = {
        _local_name(value).casefold()
        for value in (
            (ontology_contract.get("ordered_member_profile") or {}).get(
                "single_valued_ordering_properties", []
            )
            or []
        )
    }
    is_ordered = False
    for key, value in item.fields:
        local = _local_name(key)
        aspects.append(
            Aspect(
                aspect_id=f"field:{local}",
                kind="explicit_field",
                field_name=key,
                field_value=value,
            )
        )
        normalized = local.casefold()
        is_ordered = (
            is_ordered
            or normalized in ordering_locals
            or bool(_ORDER_HINT.search(normalized))
            or normalized in {"hasorder", "sequenceindex"}
        )
    if is_ordered:
        aspects.append(Aspect(aspect_id="owner_integration", kind="owner_integration"))
    return aspects


def _matching_nodes(graph: Graph, item: SourceItem) -> set[URIRef]:
    class_local = _local_name(item.class_hint).casefold()
    if not class_local:
        return set()
    nodes = {
        subject
        for subject, type_iri in graph.subject_objects(RDF.type)
        if isinstance(subject, URIRef)
        and _local_name(str(type_iri)).casefold() == class_local
    }
    marker = item.marker.strip().casefold()
    if not nodes and marker:
        nodes = {
            subject
            for subject, predicate, value in graph
            if isinstance(subject, URIRef)
            and isinstance(value, Literal)
            and _local_name(str(predicate)).casefold() == "label"
            and str(value).strip().casefold() == marker
        }
    order_fields = [
        (key, value)
        for key, value in item.fields
        if _local_name(key).casefold() in {"hasorder", "sequenceindex", "order", "index"}
    ]
    if not order_fields:
        if not marker:
            return nodes
        labelled = {
            node
            for node in nodes
            if any(
                isinstance(value, Literal)
                and str(value).strip().casefold() == marker
                for predicate, value in graph.predicate_objects(node)
                if _local_name(str(predicate)).casefold() == "label"
            )
        }
        return labelled or nodes
    expected_values = {value.strip() for _, value in order_fields}
    narrowed = {
        node
        for node in nodes
        if any(
            isinstance(value, Literal) and str(value).strip() in expected_values
            for predicate, value in graph.predicate_objects(node)
            if _local_name(str(predicate)).casefold()
            in {"hasorder", "sequenceindex", "order", "index"}
        )
    }
    return narrowed or nodes


def project_item_neighborhood(
    graph: Graph,
    item: SourceItem,
    aspect: Aspect | None = None,
) -> str:
    """Render the candidate item and one-hop incoming/outgoing RDF evidence."""
    selected = Graph()
    nodes = _matching_nodes(graph, item)
    if not nodes and aspect is not None and aspect.field_name:
        field_local = _local_name(aspect.field_name).casefold()
        for subject, predicate, obj in graph:
            if _local_name(str(predicate)).casefold() != field_local:
                continue
            selected.add((subject, predicate, obj))
            for descriptor in graph.triples((subject, RDF.type, None)):
                selected.add(descriptor)
            if isinstance(obj, URIRef):
                for descriptor in graph.triples((obj, RDF.type, None)):
                    selected.add(descriptor)
                for descriptor in graph.triples((obj, None, None)):
                    if isinstance(descriptor[2], Literal):
                        selected.add(descriptor)
    for node in nodes:
        for triple in graph.triples((node, None, None)):
            selected.add(triple)
            obj = triple[2]
            if isinstance(obj, URIRef):
                for descriptor in graph.triples((obj, RDF.type, None)):
                    selected.add(descriptor)
                for descriptor in graph.triples((obj, None, None)):
                    if isinstance(descriptor[2], Literal):
                        selected.add(descriptor)
        for triple in graph.triples((None, None, node)):
            selected.add(triple)
            subject = triple[0]
            if isinstance(subject, URIRef):
                for descriptor in graph.triples((subject, RDF.type, None)):
                    selected.add(descriptor)
    if not selected:
        for subject, type_iri in graph.subject_objects(RDF.type):
            if _local_name(str(type_iri)).casefold() == _local_name(
                item.class_hint
            ).casefold():
                selected.add((subject, RDF.type, type_iri))
    return str(selected.serialize(format="turtle"))


def project_contract(
    ontology_contract: dict[str, Any],
    item: SourceItem,
    aspect: Aspect,
) -> dict[str, Any]:
    """Slice the machine contract to the class/property relevant to one aspect."""
    class_local = _local_name(item.class_hint).casefold()
    field_local = _local_name(aspect.field_name).casefold()
    class_entries = [
        entry
        for entry in ontology_contract.get("classes", []) or []
        if isinstance(entry, dict)
        and _local_name(entry.get("class_iri", "")).casefold() == class_local
    ]
    if class_local and not class_entries:
        class_entries = [
            entry
            for entry in ontology_contract.get("classes", []) or []
            if isinstance(entry, dict)
            and (
                _local_name(entry.get("class_iri", "")).casefold().endswith(
                    class_local
                )
                or class_local.endswith(
                    _local_name(entry.get("class_iri", "")).casefold()
                )
            )
        ]
    return {
        "classes": class_entries,
        "object_properties": [
            entry
            for entry in ontology_contract.get("object_properties", []) or []
            if isinstance(entry, dict)
            and (
                _local_name(entry.get("property_iri", "")).casefold() == field_local
                or aspect.kind == "owner_integration"
            )
        ],
        "datatype_properties": [
            entry
            for entry in ontology_contract.get("datatype_properties", []) or []
            if isinstance(entry, dict)
            and _local_name(entry.get("property_iri", "")).casefold() == field_local
        ],
        "required_links": ontology_contract.get("required_links", []) or [],
        "ordered_member_profile": ontology_contract.get("ordered_member_profile", {})
        or {},
    }


def build_microjudge_prompt(
    *,
    item: SourceItem,
    aspect: Aspect,
    contract_slice: dict[str, Any],
    abox_neighborhood: str,
    confirmation: bool = False,
) -> str:
    """Build one intentionally narrow integrity decision prompt."""
    task = {
        "entity_presence": (
            "Decide only whether this exact source-supported occurrence is materialized "
            "as the intended typed RDF node."
        ),
        "explicit_field": (
            "Decide only whether the explicit source field is faithfully materialized "
            "on the correct occurrence. Do not judge any other field."
        ),
        "owner_integration": (
            "Decide only whether this ordered occurrence is connected to its correct "
            "scoped owner/root when source or a genuine cardinality/integrity contract "
            "requires that integration."
        ),
    }[aspect.kind]
    return (
        "You are one independent RDF integrity microjudge. You have no access to any "
        "other judge's answer. Make one narrow decision only.\n\n"
        f"TASK: {task}\n"
        f"CONFIRMATION ROUND: {str(confirmation).lower()}\n"
        "Decision meanings:\n"
        "- pass: the single requested aspect is satisfied, or the alleged relationship "
        "is not actually required.\n"
        "- fail: concrete supplied evidence proves this single requested aspect is "
        "required and missing/wrong.\n"
        "- uncertain: supplied local evidence is insufficient.\n"
        "RDF domain/range compatibility never creates a required edge. Do not inspect, "
        "report, or repair any aspect outside TASK. Search the complete supplied local "
        "Turtle before claiming absence.\n\n"
        "Return JSON with exactly these keys:\n"
        '{"decision":"pass|fail|uncertain","source_item_id":"","aspect_id":"",'
        '"summary":"","source_evidence":"","ontology_evidence":"",'
        '"abox_evidence":"","confidence":0.0}\n\n'
        f"SOURCE ITEM ID: {item.item_id}\n"
        f"SOURCE ITEM:\n{item.evidence}\n\n"
        f"ASPECT ID: {aspect.aspect_id}\n"
        f"FIELD NAME: {aspect.field_name}\n"
        f"FIELD VALUE: {aspect.field_value}\n\n"
        "RELEVANT ONTOLOGY CONTRACT:\n"
        f"{json.dumps(contract_slice, ensure_ascii=False, sort_keys=True)}\n\n"
        f"LOCAL A-BOX NEIGHBORHOOD:\n{abox_neighborhood}\n"
    )


def _validate_microvote(
    data: dict[str, Any],
    *,
    item: SourceItem,
    aspect: Aspect,
) -> dict[str, Any]:
    required = {
        "decision",
        "source_item_id",
        "aspect_id",
        "summary",
        "source_evidence",
        "ontology_evidence",
        "abox_evidence",
        "confidence",
    }
    if set(data) != required:
        raise ValueError("microvote keys differ from required schema")
    if data["decision"] not in {"pass", "fail", "uncertain"}:
        raise ValueError("microvote decision is invalid")
    if str(data["source_item_id"]) != item.item_id:
        raise ValueError("microvote source_item_id changed")
    if str(data["aspect_id"]) != aspect.aspect_id:
        raise ValueError("microvote aspect_id changed")
    confidence = float(data["confidence"])
    if not 0.0 <= confidence <= 1.0:
        raise ValueError("microvote confidence must be within [0, 1]")
    normalized = dict(data)
    normalized["confidence"] = confidence
    for key in required - {"confidence"}:
        normalized[key] = str(normalized[key])
    return normalized


def _invoke_microvote(
    *,
    invoke: Callable[..., LLMJsonResult],
    model: str,
    prompt: str,
    item: SourceItem,
    aspect: Aspect,
    max_validation_attempts: int = 3,
) -> tuple[dict[str, Any], list[LLMJsonResult]]:
    results: list[LLMJsonResult] = []
    current_prompt = prompt
    errors: list[str] = []
    for _ in range(max_validation_attempts):
        result = invoke(model, current_prompt, max_attempts=3)
        results.append(result)
        try:
            return _validate_microvote(result.data, item=item, aspect=aspect), results
        except (TypeError, ValueError) as exc:
            errors.append(str(exc))
            current_prompt = (
                prompt
                + "\n\nYour previous JSON failed the mechanical schema validator: "
                + str(exc)
                + "\nReturn only a corrected JSON object for the same single aspect."
            )
    return (
        {
            "decision": "uncertain",
            "source_item_id": item.item_id,
            "aspect_id": aspect.aspect_id,
            "summary": "Microjudge schema remained invalid.",
            "source_evidence": "",
            "ontology_evidence": "",
            "abox_evidence": "",
            "confidence": 0.0,
            "schema_errors": errors,
        },
        results,
    )


def _run_parallel_panel(
    *,
    invoke: Callable[..., LLMJsonResult],
    models: list[str],
    prompt: str,
    item: SourceItem,
    aspect: Aspect,
) -> tuple[list[dict[str, Any]], list[LLMJsonResult]]:
    votes: list[dict[str, Any]] = []
    results: list[LLMJsonResult] = []
    with ThreadPoolExecutor(max_workers=len(models)) as pool:
        futures = {
            pool.submit(
                _invoke_microvote,
                invoke=invoke,
                model=model,
                prompt=prompt,
                item=item,
                aspect=aspect,
            ): (index, model)
            for index, model in enumerate(models)
        }
        ordered: list[tuple[int, str, dict[str, Any], list[LLMJsonResult]]] = []
        for future in as_completed(futures):
            index, model = futures[future]
            vote, calls = future.result()
            ordered.append((index, model, vote, calls))
        for _, model, vote, calls in sorted(ordered):
            votes.append({"model": model, **vote})
            results.extend(calls)
    return votes, results


def _unanimous_decision(votes: list[dict[str, Any]]) -> str | None:
    decisions = {str(vote.get("decision")) for vote in votes}
    return next(iter(decisions)) if len(decisions) == 1 else None


def _build_repair_prompt(
    *,
    item: SourceItem,
    aspect: Aspect,
    contract_slice: dict[str, Any],
    abox_neighborhood: str,
    confirmed_votes: list[dict[str, Any]],
) -> str:
    return (
        "Plan exactly one atomic repair for one independently confirmed RDF integrity "
        "finding. Do not diagnose other issues and do not invent an existing IRI. Copy "
        "existing IRIs verbatim from the local A-Box and predicates/classes from the "
        "contract. For datatype/literal fixes or a repair that cannot be expressed as one "
        "exact RDF relationship, use operation=other and leave IRI fields empty.\n"
        "Return JSON with exactly these keys:\n"
        '{"operation":"add_relationship|remove_relationship|create_entity|reuse_identity|'
        'set_type|other","tool_name":"","subject_iri":"","predicate_iri":"",'
        '"object_iri":"","class_iri":"","action":""}\n\n'
        f"SOURCE ITEM:\n{item.evidence}\n"
        f"ASPECT: {aspect.aspect_id} {aspect.field_name}: {aspect.field_value}\n"
        "CONFIRMED VOTES:\n"
        f"{json.dumps(confirmed_votes, ensure_ascii=False)}\n"
        "RELEVANT CONTRACT:\n"
        f"{json.dumps(contract_slice, ensure_ascii=False, sort_keys=True)}\n"
        f"LOCAL A-BOX:\n{abox_neighborhood}\n"
    )


def _validate_repair(data: dict[str, Any]) -> dict[str, str]:
    required = {
        "operation",
        "tool_name",
        "subject_iri",
        "predicate_iri",
        "object_iri",
        "class_iri",
        "action",
    }
    if set(data) != required:
        raise ValueError("repair keys differ from required schema")
    normalized = {key: str(data[key]).strip() for key in required}
    operation = normalized["operation"]
    if operation not in {
        "add_relationship",
        "remove_relationship",
        "create_entity",
        "reuse_identity",
        "set_type",
        "other",
    }:
        raise ValueError("repair operation is invalid")
    if operation in {"add_relationship", "remove_relationship"}:
        for key in ("subject_iri", "predicate_iri", "object_iri"):
            if not _ABSOLUTE_IRI.fullmatch(normalized[key]):
                raise ValueError(f"{key} must be an absolute IRI")
    if operation in {"create_entity", "set_type"} and not _ABSOLUTE_IRI.fullmatch(
        normalized["class_iri"]
    ):
        raise ValueError("class_iri must be an absolute IRI")
    if not normalized["action"]:
        raise ValueError("repair action is required")
    return normalized


def _invoke_repair(
    *,
    invoke: Callable[..., LLMJsonResult],
    model: str,
    prompt: str,
) -> tuple[dict[str, str] | None, list[LLMJsonResult]]:
    results: list[LLMJsonResult] = []
    current_prompt = prompt
    for _ in range(3):
        result = invoke(model, current_prompt, max_attempts=3)
        results.append(result)
        try:
            return _validate_repair(result.data), results
        except (TypeError, ValueError) as exc:
            current_prompt = (
                prompt
                + "\n\nPrevious JSON failed the mechanical repair validator: "
                + str(exc)
                + "\nReturn a corrected repair for the same one confirmed finding."
            )
    return None, results


def _repair_key(repair: dict[str, str]) -> tuple[str, ...]:
    return tuple(
        repair[key]
        for key in (
            "operation",
            "tool_name",
            "subject_iri",
            "predicate_iri",
            "object_iri",
            "class_iri",
        )
    )


def _run_repair_panel(
    *,
    invoke: Callable[..., LLMJsonResult],
    models: list[str],
    prompt: str,
) -> tuple[dict[str, str] | None, list[dict[str, Any]], list[LLMJsonResult]]:
    raw: list[dict[str, Any]] = []
    results: list[LLMJsonResult] = []
    with ThreadPoolExecutor(max_workers=len(models)) as pool:
        futures = {
            pool.submit(_invoke_repair, invoke=invoke, model=model, prompt=prompt): (
                index,
                model,
            )
            for index, model in enumerate(models)
        }
        ordered = []
        for future in as_completed(futures):
            index, model = futures[future]
            repair, calls = future.result()
            ordered.append((index, model, repair, calls))
        repairs: list[dict[str, str]] = []
        for _, model, repair, calls in sorted(ordered):
            raw.append({"model": model, "repair": repair})
            results.extend(calls)
            if repair is not None:
                repairs.append(repair)
    if len(repairs) == len(models) and len({_repair_key(item) for item in repairs}) == 1:
        return repairs[0], raw, results
    return None, raw, results


def run_microjudge_integrity(
    *,
    document_text: str,
    ontology_contract: dict[str, Any],
    graph: Graph,
    models: list[str],
    invoke: Callable[..., LLMJsonResult],
) -> dict[str, Any]:
    """Run granular independent panels and deterministically aggregate them."""
    if len(models) != 3:
        raise ValueError("microjudge integrity requires exactly three panel models")
    items = parse_semantic_hint_items(document_text)
    panels: list[dict[str, Any]] = []
    all_results: list[LLMJsonResult] = []
    confirmed_failures: list[dict[str, Any]] = []

    for item in items:
        for aspect in aspects_for_item(item, ontology_contract):
            neighborhood = project_item_neighborhood(graph, item, aspect)
            contract_slice = project_contract(ontology_contract, item, aspect)
            detection_prompt = build_microjudge_prompt(
                item=item,
                aspect=aspect,
                contract_slice=contract_slice,
                abox_neighborhood=neighborhood,
            )
            detection_votes, calls = _run_parallel_panel(
                invoke=invoke,
                models=models,
                prompt=detection_prompt,
                item=item,
                aspect=aspect,
            )
            all_results.extend(calls)
            detection_decision = _unanimous_decision(detection_votes)
            escalation_votes: list[dict[str, Any]] = []
            if detection_decision in {None, "uncertain"}:
                escalation_votes, calls = _run_parallel_panel(
                    invoke=invoke,
                    models=models,
                    prompt=detection_prompt
                    + "\n\nThis is a fresh escalation panel after disagreement. Judge from "
                    "the supplied evidence only; prior panel outputs are unavailable.",
                    item=item,
                    aspect=aspect,
                )
                all_results.extend(calls)
                detection_decision = _unanimous_decision(escalation_votes)

            confirmation_votes: list[dict[str, Any]] = []
            confirmed = False
            if detection_decision == "fail":
                confirmation_prompt = build_microjudge_prompt(
                    item=item,
                    aspect=aspect,
                    contract_slice=contract_slice,
                    abox_neighborhood=neighborhood,
                    confirmation=True,
                )
                confirmation_votes, calls = _run_parallel_panel(
                    invoke=invoke,
                    models=models,
                    prompt=confirmation_prompt,
                    item=item,
                    aspect=aspect,
                )
                all_results.extend(calls)
                confirmed = _unanimous_decision(confirmation_votes) == "fail"

            repair = None
            repair_votes: list[dict[str, Any]] = []
            if confirmed:
                repair_prompt = _build_repair_prompt(
                    item=item,
                    aspect=aspect,
                    contract_slice=contract_slice,
                    abox_neighborhood=neighborhood,
                    confirmed_votes=confirmation_votes,
                )
                repair, repair_votes, calls = _run_repair_panel(
                    invoke=invoke,
                    models=models,
                    prompt=repair_prompt,
                )
                all_results.extend(calls)
                if repair is None:
                    repair, second_votes, calls = _run_repair_panel(
                        invoke=invoke,
                        models=models,
                        prompt=repair_prompt
                        + "\n\nThis is a fresh repair-consensus panel. Return only the "
                        "single safest atomic operation supported by the supplied evidence.",
                    )
                    repair_votes.extend(second_votes)
                    all_results.extend(calls)
                confirmed = repair is not None

            panel = {
                "source_item_id": item.item_id,
                "aspect_id": aspect.aspect_id,
                "kind": aspect.kind,
                "detection_votes": detection_votes,
                "escalation_votes": escalation_votes,
                "confirmation_votes": confirmation_votes,
                "repair_votes": repair_votes,
                "decision": "fail" if confirmed else (
                    detection_decision if detection_decision in {"pass", "uncertain"} else "uncertain"
                ),
                "confirmed": confirmed,
                "repair": repair,
            }
            panels.append(panel)
            if confirmed and repair is not None:
                confirmed_failures.append(
                    {
                        "item": item,
                        "aspect": aspect,
                        "votes": confirmation_votes,
                        "repair": repair,
                    }
                )

    return {
        "items": items,
        "panels": panels,
        "confirmed_failures": confirmed_failures,
        "llm_results": all_results,
    }
