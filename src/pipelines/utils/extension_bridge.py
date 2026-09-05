"""Domain-neutral enrichment-target bridging for extension A-Boxes.

Extension KG prompts must not mint a replacement identity for a pipeline-bound
target IRI. The bound individual still has to exist, with its declared class,
inside the extension graph so domain/range `add_*` checks can succeed.

This module seeds those IRIs and, when a T-Box is available, attaches unlinked
range individuals to the unique bound subject of the matching domain class.
"""

from __future__ import annotations

from typing import Any, Iterable

from rdflib import Graph, URIRef
from rdflib.namespace import OWL, RDF, RDFS


def normalize_enrichment_targets(targets: Iterable[Any] | None) -> list[dict[str, str]]:
    """Keep only absolute target IRIs that declare a class."""
    normalized: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in targets or []:
        if not isinstance(item, dict):
            continue
        target_iri = str(item.get("target_iri") or "").strip()
        class_iri = str(item.get("class_iri") or "").strip()
        if not target_iri.startswith(("http://", "https://", "urn:")):
            continue
        if not class_iri.startswith(("http://", "https://", "urn:")):
            continue
        key = (target_iri, class_iri)
        if key in seen:
            continue
        seen.add(key)
        normalized.append({"target_iri": target_iri, "class_iri": class_iri})
    return normalized


def seed_enrichment_targets(
    graph: Graph,
    targets: Iterable[Any] | None,
) -> dict[str, Any]:
    """Type each bound target IRI in `graph`. Existing types are left in place."""
    seeded: list[str] = []
    already_present: list[str] = []
    for item in normalize_enrichment_targets(targets):
        subject = URIRef(item["target_iri"])
        class_ref = URIRef(item["class_iri"])
        triple = (subject, RDF.type, class_ref)
        if triple in graph:
            already_present.append(item["target_iri"])
            continue
        graph.add(triple)
        seeded.append(item["target_iri"])
    return {
        "seeded": seeded,
        "already_present": already_present,
        "target_count": len(seeded) + len(already_present),
    }


def _object_properties_for_domain(tbox: Graph, class_iri: str) -> list[tuple[URIRef, set[URIRef]]]:
    class_ref = URIRef(class_iri)
    properties: list[tuple[URIRef, set[URIRef]]] = []
    seen: set[str] = set()
    for predicate in tbox.subjects(RDFS.domain, class_ref):
        if not isinstance(predicate, URIRef):
            continue
        if (predicate, RDF.type, OWL.ObjectProperty) not in tbox and (
            predicate,
            RDF.type,
            RDF.Property,
        ) not in tbox:
            # Still accept domain/range-declared predicates without an explicit type.
            if not any(tbox.objects(predicate, RDFS.range)):
                continue
        key = str(predicate)
        if key in seen:
            continue
        ranges = {
            value
            for value in tbox.objects(predicate, RDFS.range)
            if isinstance(value, URIRef)
        }
        if not ranges:
            continue
        seen.add(key)
        properties.append((predicate, ranges))
    return properties


def attach_unlinked_range_objects(
    graph: Graph,
    targets: Iterable[Any] | None,
    tbox: Graph | None,
) -> dict[str, Any]:
    """Link unattached range individuals to the unique bound subject of their domain.

    Skips a class when more than one enrichment target shares that class IRI.
    Never invents nodes; only writes object-property edges already licensed by
    the T-Box domain/range.
    """
    if tbox is None:
        return {"attached": 0, "skipped_ambiguous_class": [], "used_properties": []}

    by_class: dict[str, list[str]] = {}
    for item in normalize_enrichment_targets(targets):
        by_class.setdefault(item["class_iri"], []).append(item["target_iri"])

    attached = 0
    skipped_ambiguous: list[str] = []
    used_properties: list[str] = []
    for class_iri, subject_iris in by_class.items():
        unique_subjects = sorted(set(subject_iris))
        if len(unique_subjects) != 1:
            skipped_ambiguous.append(class_iri)
            continue
        subject = URIRef(unique_subjects[0])
        if (subject, RDF.type, URIRef(class_iri)) not in graph:
            graph.add((subject, RDF.type, URIRef(class_iri)))
        for predicate, ranges in _object_properties_for_domain(tbox, class_iri):
            for range_iri in ranges:
                for obj in graph.subjects(RDF.type, range_iri):
                    if not isinstance(obj, URIRef) or obj == subject:
                        continue
                    if any(True for _ in graph.subjects(predicate, obj)):
                        continue
                    graph.add((subject, predicate, obj))
                    attached += 1
                    predicate_iri = str(predicate)
                    if predicate_iri not in used_properties:
                        used_properties.append(predicate_iri)
    return {
        "attached": attached,
        "skipped_ambiguous_class": skipped_ambiguous,
        "used_properties": used_properties,
    }


def load_tbox_graph(ttl_path: str | None) -> Graph | None:
    if not ttl_path:
        return None
    try:
        graph = Graph()
        graph.parse(ttl_path, format="turtle")
    except Exception:
        return None
    return graph


def apply_extension_bridge(
    content: str,
    targets: Iterable[Any] | None,
    *,
    tbox: Graph | None = None,
    tbox_path: str | None = None,
) -> tuple[str, dict[str, Any]]:
    """Seed bound identities and attach unlinked range objects in a TTL string."""
    if not str(content or "").strip():
        return content, {"status": "skipped", "reason": "empty_content"}
    normalized = normalize_enrichment_targets(targets)
    if not normalized:
        return content, {"status": "skipped", "reason": "no_targets"}
    try:
        graph = Graph()
        graph.parse(data=content, format="turtle")
    except Exception as exc:
        return content, {"status": "skipped", "reason": f"parse_failed:{exc}"}

    tbox_graph = tbox if tbox is not None else load_tbox_graph(tbox_path)
    seed_report = seed_enrichment_targets(graph, normalized)
    attach_report = attach_unlinked_range_objects(graph, normalized, tbox_graph)
    report = {
        "status": "applied",
        "seed": seed_report,
        "attach": attach_report,
    }
    return graph.serialize(format="turtle"), report
