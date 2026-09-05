"""Domain-neutral hint revision and post-publish repair for extension A-Boxes.

Reuses the main-ontology contract validators. Bound enrichment-target IRIs are
the extension analogue of a published subject: they must be typed in the graph,
and hinted object properties on a unique bound subject must be present.
"""

from __future__ import annotations

import json
from typing import Any, Iterable

from rdflib import Graph, URIRef
from rdflib.namespace import RDF

from src.pipelines.main_kg_building.build import (
    _decode_structured_hint_prefix,
    _local_name,
    _validate_entity_ttl_structure,
    _validate_hint_relation_contract,
)
from src.pipelines.utils.extension_bridge import normalize_enrichment_targets


def hint_revision_prompt_block(revision_feedback: str) -> str:
    """Authoritative correction wrapper used by extension extraction."""
    raw = str(revision_feedback or "").strip()
    if not raw:
        return ""
    try:
        revision_payload = json.loads(raw)
    except json.JSONDecodeError:
        revision_payload = {
            "schema_version": "kg-hint-contract-revision.v1",
            "violations": [raw],
        }
    correction_block = json.dumps(
        {
            "mode": "full-authoritative-replacement",
            "reported_errors": revision_payload,
            "required_action": (
                "Return the complete corrected hint payload. Preserve valid "
                "content and remove every reported invalid relation."
            ),
        },
        ensure_ascii=False,
        indent=2,
    )
    return (
        "---- PIPELINE KG CONTRACT CORRECTION: BEGIN ----\n"
        "This is a correction run, not an additive enrichment patch. The "
        "reported invalid relations already occur in the current hints. "
        "Omitting them from a delta is insufficient: return one complete "
        "authoritative replacement payload in which they are absent.\n"
        + correction_block
        + "\n---- PIPELINE KG CONTRACT CORRECTION: END ----\n\n"
        "FINAL CORRECTION REMINDER: return the complete corrected payload, "
        "not a patch. Every relation listed in the correction block must be "
        "absent.\n"
    )


def collect_hint_violations(
    hints_content: str,
    ontology_contract: dict[str, Any] | None,
    *,
    iteration: int | None = None,
) -> list[dict[str, Any]]:
    """Return immutable T-Box relation-contract violations in extension hints."""
    return _validate_hint_relation_contract(
        hints_content=hints_content,
        ontology_contract=ontology_contract,
        iteration=iteration,
    )


def _graph_from_ttl(ttl_path: str) -> Graph | None:
    try:
        graph = Graph()
        graph.parse(ttl_path, format="turtle")
    except Exception:
        return None
    return graph


def missing_bound_target_messages(
    graph: Graph,
    targets: Iterable[Any] | None,
) -> list[str]:
    """Require each bound enrichment-target IRI to exist with its declared class."""
    messages: list[str] = []
    for item in normalize_enrichment_targets(targets):
        subject = URIRef(item["target_iri"])
        class_ref = URIRef(item["class_iri"])
        if (subject, RDF.type, class_ref) not in graph:
            messages.append(
                "Missing bound enrichment target "
                f"{item['target_iri']} typed as {item['class_iri']}"
            )
    return messages


def missing_hinted_links_on_bound_subjects(
    *,
    hints_content: str,
    graph: Graph,
    targets: Iterable[Any] | None,
    ontology_contract: dict[str, Any] | None,
) -> list[str]:
    """Fail when a unique bound subject is missing a hinted object property."""
    payload = _decode_structured_hint_prefix(hints_content)
    entities = payload.get("entities")
    relations = payload.get("relations")
    if not isinstance(entities, list) or not isinstance(relations, list):
        return []

    ref_classes: dict[str, str] = {}
    for entity in entities:
        if not isinstance(entity, dict):
            continue
        ref = str(entity.get("ref") or "").strip()
        class_local = _local_name(str(entity.get("class") or ""))
        if ref and class_local:
            ref_classes[ref] = class_local

    class_iris = {
        _local_name(str(item.get("class_iri") or "")): str(
            item.get("class_iri") or ""
        ).strip()
        for item in (ontology_contract or {}).get("classes", []) or []
        if str(item.get("class_iri") or "").strip()
    }
    property_iris = {
        _local_name(str(item.get("property_iri") or "")): str(
            item.get("property_iri") or ""
        ).strip()
        for item in (ontology_contract or {}).get("object_properties", []) or []
        if str(item.get("property_iri") or "").strip()
    }

    by_class: dict[str, list[str]] = {}
    for item in normalize_enrichment_targets(targets):
        by_class.setdefault(item["class_iri"], []).append(item["target_iri"])

    messages: list[str] = []
    seen: set[tuple[str, str]] = set()
    for relation in relations:
        if not isinstance(relation, dict):
            continue
        property_local = _local_name(str(relation.get("property") or ""))
        predicate_iri = property_iris.get(property_local, "")
        if not predicate_iri:
            continue
        subject_class_local = ref_classes.get(
            str(relation.get("subject_ref") or "").strip(), ""
        )
        subject_class_iri = class_iris.get(subject_class_local, "")
        if not subject_class_iri:
            continue
        unique = sorted(set(by_class.get(subject_class_iri) or []))
        if len(unique) != 1:
            continue
        subject = URIRef(unique[0])
        if (subject, RDF.type, URIRef(subject_class_iri)) not in graph:
            continue
        key = (str(subject), predicate_iri)
        if key in seen:
            continue
        seen.add(key)
        if any(graph.objects(subject, URIRef(predicate_iri))):
            continue
        messages.append(
            f"Missing hinted object property {predicate_iri} on bound subject {subject}"
        )
    return messages


def collect_extension_structural_messages(
    *,
    ttl_path: str,
    entity_uri: str,
    entity_label: str,
    ontology_contract: dict[str, Any],
    enrichment_targets: Iterable[Any] | None,
    hints_content: str = "",
) -> list[str]:
    """Combine publish-contract structure with bound-target and hinted-link checks."""
    bound = normalize_enrichment_targets(enrichment_targets)
    unique_bound = sorted({item["target_iri"] for item in bound})
    structure_subject = unique_bound[0] if len(unique_bound) == 1 else entity_uri
    ok_struct, struct_msgs = _validate_entity_ttl_structure(
        ttl_path=ttl_path,
        entity_uri=structure_subject,
        entity_label=entity_label,
        ontology_contract=ontology_contract,
    )
    messages = list(struct_msgs)
    if not ok_struct and messages == ["Ontology publish contract is unavailable"]:
        return messages
    graph = _graph_from_ttl(ttl_path)
    if graph is None:
        if not messages:
            messages.append(f"Failed to parse TTL for extension validation: {ttl_path}")
        return messages
    messages.extend(missing_bound_target_messages(graph, enrichment_targets))
    if hints_content:
        messages.extend(
            missing_hinted_links_on_bound_subjects(
                hints_content=hints_content,
                graph=graph,
                targets=enrichment_targets,
                ontology_contract=ontology_contract,
            )
        )
    return messages


def revision_attempt_limits(config: dict[str, Any] | None) -> tuple[int, int]:
    """Return (hint_revision_max_attempts, post_publish_structural_retries)."""
    payload = config or {}
    if payload.get("disable_kg_revisions"):
        return 0, 0
    try:
        hint_revisions = max(0, int(payload.get("kg_hint_revision_max_attempts", 2)))
    except (TypeError, ValueError):
        hint_revisions = 2
    try:
        structural_retries = max(
            0, int(payload.get("post_publish_structural_retries", 2))
        )
    except (TypeError, ValueError):
        structural_retries = 2
    return hint_revisions, structural_retries
