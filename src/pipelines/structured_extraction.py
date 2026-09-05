"""Lightweight structured-output checks for extraction pipeline stages."""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urlparse


def is_marker_only_optional_output(text: str) -> bool:
    """Return True for marker-only optional extraction outputs like `[FooExtraction]`."""
    return bool(re.fullmatch(r"\[[A-Za-z0-9_ -]*Extraction\]", str(text or "").strip()))


def parse_json_payload(text: str) -> Any:
    """Parse JSON payloads while preserving a clear ValueError for callers."""
    try:
        return json.loads(text)
    except Exception as e:
        preview = repr(str(text or "")[:240])
        raise ValueError(
            f"Invalid JSON extraction payload: {e}; payload preview={preview}"
        ) from e


def validate_top_entity_lines(
    content: str, prefixes: list[str] | tuple[str, ...]
) -> tuple[bool, list[str]]:
    """Validate normalized top-entity line shape: `<Prefix>-<n> [<label>]`."""
    allowed = tuple(p for p in prefixes if p) or ("Entity",)
    errors: list[str] = []
    lines = [x.strip() for x in str(content or "").splitlines() if x.strip()]
    if not lines:
        return False, ["top-entity output is empty"]
    for line in lines:
        if not any(line.startswith(prefix) for prefix in allowed):
            errors.append(f"line does not start with an allowed prefix: {line}")
            continue
        if not re.match(r"^[A-Za-z][A-Za-z0-9_]*-\d+\s+\[.+\]$", line):
            errors.append(f"line is not normalized as '<Prefix>-<n> [label]': {line}")
    return not errors, errors


def validate_hint_payload(
    content: str,
    *,
    allow_empty: bool = False,
    accumulated_hints: str = "",
    expected_schema: str | None = None,
    allowed_entity_iris: set[str] | None = None,
) -> tuple[bool, list[str]]:
    """Validate generic extraction hints without assuming ontology-specific fields."""
    text = str(content or "").strip()
    if not text:
        return (allow_empty, [] if allow_empty else ["hint payload is empty"])
    if expected_schema is None and is_marker_only_optional_output(text):
        return True, []

    try:
        parsed = parse_json_payload(text)
    except ValueError:
        if expected_schema == "ref-entity-relations.v1":
            raise
        return True, []

    is_ref_entity_relations = isinstance(parsed, dict) and (
        "entities" in parsed or "relations" in parsed
    )
    if expected_schema == "ref-entity-relations.v1" or is_ref_entity_relations:
        if parsed in ({}, []):
            return (
                allow_empty,
                [] if allow_empty else ["empty JSON payload is not allowed here"],
            )
        if not isinstance(parsed, dict):
            return False, ["ref-entity-relations payload must be a JSON object"]

        errors: list[str] = []
        entities = parsed.get("entities")
        relations = parsed.get("relations")
        if not isinstance(entities, list):
            errors.append("ref-entity-relations entities must be an array")
            entities = []
        if not isinstance(relations, list):
            errors.append("ref-entity-relations relations must be an array")
            relations = []

        current_refs: set[str] = set()
        entities_by_ref: dict[str, dict[str, Any]] = {}
        for index, entity in enumerate(entities):
            if not isinstance(entity, dict):
                errors.append(f"entities[{index}] must be an object")
                continue
            ref = str(entity.get("ref") or "").strip()
            class_local = str(entity.get("class") or "").strip()
            label = str(entity.get("label") or "").strip()
            datatype_properties = entity.get("datatype_properties")
            if not ref:
                errors.append(f"entities[{index}].ref must be non-empty")
                continue
            if not class_local:
                errors.append(f"entities[{index}].class must be non-empty")
            if not label:
                errors.append(f"entities[{index}].label must be non-empty")
            if not isinstance(datatype_properties, dict):
                errors.append(
                    f"entities[{index}].datatype_properties must be an object"
                )
            if ref in current_refs:
                errors.append(f"duplicate entity ref: {ref}")
            current_refs.add(ref)
            entities_by_ref[ref] = entity

        prior_refs: set[str] = set()
        prior_text = str(accumulated_hints or "").strip()
        if prior_text:
            prior_payload = parse_json_payload(prior_text)
            if not isinstance(prior_payload, dict):
                errors.append(
                    "accumulated ref-entity-relations hints must be a JSON object"
                )
            else:
                def collect_entity_refs(value: Any) -> set[str]:
                    refs: set[str] = set()
                    if isinstance(value, dict):
                        entities_value = value.get("entities")
                        if isinstance(entities_value, list):
                            refs.update(
                                str(entity.get("ref") or "").strip()
                                for entity in entities_value
                                if isinstance(entity, dict)
                                and str(entity.get("ref") or "").strip()
                            )
                        for child in value.values():
                            refs.update(collect_entity_refs(child))
                    elif isinstance(value, list):
                        for child in value:
                            refs.update(collect_entity_refs(child))
                    return refs

                prior_refs = collect_entity_refs(prior_payload)

        known_refs = current_refs | prior_refs
        allowed_absolute_refs = {
            str(value).strip()
            for value in (allowed_entity_iris or set())
            if str(value).strip()
        }
        for ref in current_refs:
            if (
                urlparse(ref).scheme in {"http", "https", "urn"}
                and ref not in prior_refs
                and ref not in allowed_absolute_refs
            ):
                errors.append(
                    f"new entity ref must be an opaque ref, not a fabricated absolute IRI: {ref}"
                )
        seen_relations: set[tuple[str, str, str]] = set()
        for index, relation in enumerate(relations):
            if not isinstance(relation, dict):
                errors.append(f"relations[{index}] must be an object")
                continue
            subject = str(relation.get("subject_ref") or "").strip()
            prop = str(relation.get("property") or "").strip()
            obj = str(relation.get("object_ref") or "").strip()
            if not subject or not prop or not obj:
                errors.append(
                    f"relations[{index}] requires subject_ref, property, and object_ref"
                )
                continue
            triple = (subject, prop, obj)
            if triple in seen_relations:
                errors.append(f"duplicate relation: {subject} -{prop}-> {obj}")
            seen_relations.add(triple)
            for role, endpoint in (("subject_ref", subject), ("object_ref", obj)):
                if urlparse(endpoint).scheme in {"http", "https", "urn"}:
                    continue
                if endpoint not in known_refs:
                    errors.append(
                        f"relations[{index}].{role} uses unresolved ref: {endpoint}"
                    )
            source_entity = entities_by_ref.get(subject)
            lexical_fields = (
                source_entity.get("datatype_properties")
                if isinstance(source_entity, dict)
                else {}
            )
            if (
                subject == obj
                and isinstance(lexical_fields, dict)
                and prop in lexical_fields
            ):
                errors.append(
                    f"relations[{index}] turns lexical object-property evidence "
                    f"`{prop}` into a self-link on `{subject}`"
                )
        return not errors, errors

    if parsed in ({}, []):
        return (
            allow_empty,
            [] if allow_empty else ["empty JSON payload is not allowed here"],
        )
    return True, []
