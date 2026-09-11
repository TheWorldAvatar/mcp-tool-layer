"""Publish contracts compiled directly from an ontology T-Box.

Classes, properties, and creator metadata the runtime may expose. See
compile/README.md.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from rdflib import Graph, OWL, RDF, RDFS, URIRef  # type: ignore[import-not-found]

from src.extraction_prompt_generation.compile.contract_rdf import (
    _domain_members,
    _literal_int,
    _machine_top_role,
    _restriction_nodes,
    _subclass_closure,
)


def load_meta_task_config(path: str | Path) -> dict[str, Any]:
    """Read the legacy meta-task adapter JSON written by `artifact_compiler`."""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, dict) else {}


def _ontology_config(
    meta_cfg: dict[str, Any], ontology_name: str | None
) -> dict[str, Any]:
    ontologies = meta_cfg.get("ontologies", {}) or {}
    candidates = [ontologies.get("main", {})] + list(
        ontologies.get("extensions", []) or []
    )
    if ontology_name:
        return next(
            (
                candidate
                for candidate in candidates
                if isinstance(candidate, dict)
                and str(candidate.get("name") or "") == ontology_name
            ),
            {},
        )
    main = ontologies.get("main", {}) or {}
    return main if isinstance(main, dict) else {}


def _resolve_tbox_path(
    ttl_file: str, meta_task_config_path: str | Path
) -> Path:
    configured = Path(ttl_file)
    candidates = [configured]
    if not configured.is_absolute():
        from models.locations import discover_repository_root

        config_path = Path(meta_task_config_path).resolve()
        candidates.extend(
            [
                config_path.parent / configured,
                discover_repository_root() / configured,
            ]
        )
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise FileNotFoundError(f"Configured ontology T-Box not found: {ttl_file}")


def build_ontology_publish_contract_from_tbox(
    tbox_path: str | Path,
    *,
    ontology_name: str = "",
    configured_ttl_file: str = "",
) -> dict[str, Any]:
    """Build the semantic publish contract directly from one ontology T-Box."""
    resolved_path = Path(tbox_path).resolve()
    graph = Graph()
    graph.parse(str(resolved_path), format="turtle")
    closure = _subclass_closure(graph)
    evidence_file = str(resolved_path)

    classes = [
        {
            "class_iri": class_iri,
            "source": "tbox",
            "evidence": {
                "ttl_file": evidence_file,
                "triple_pattern": "rdf:type owl:Class/rdfs:Class or referenced class",
            },
        }
        for class_iri in sorted(closure)
    ]
    subclass_closure = [
        {
            "class_iri": class_iri,
            "superclass_iris": sorted(superclasses),
            "source": "tbox",
            "evidence": {
                "ttl_file": evidence_file,
                "predicate_iri": str(RDFS.subClassOf),
            },
        }
        for class_iri, superclasses in sorted(closure.items())
    ]

    object_properties: list[dict[str, Any]] = []
    datatype_properties: list[dict[str, Any]] = []
    structured_constraints: list[dict[str, Any]] = []
    required_links: list[dict[str, Any]] = []
    for prop in sorted(
        {
            node
            for node in graph.subjects(RDF.type, OWL.ObjectProperty)
            if isinstance(node, URIRef)
        },
        key=str,
    ):
        domains: list[str] = []
        for domain in graph.objects(prop, RDFS.domain):
            domains.extend(_domain_members(graph, domain))
        ranges = sorted(
            str(value)
            for value in graph.objects(prop, RDFS.range)
            if isinstance(value, URIRef)
        )
        object_properties.append(
            {
                "property_iri": str(prop),
                "domain_iris": sorted(set(domains)),
                "range_iris": ranges,
                "source": "tbox",
                "evidence": {
                    "ttl_file": evidence_file,
                    "property_type": str(OWL.ObjectProperty),
                    "domain_predicate": str(RDFS.domain),
                    "range_predicate": str(RDFS.range),
                },
            }
        )
    for prop in sorted(
        {
            node
            for node in graph.subjects(RDF.type, OWL.DatatypeProperty)
            if isinstance(node, URIRef)
        },
        key=str,
    ):
        domains: list[str] = []
        for domain in graph.objects(prop, RDFS.domain):
            domains.extend(_domain_members(graph, domain))
        datatype_properties.append(
            {
                "property_iri": str(prop),
                "domain_iris": sorted(set(domains)),
                "range_iris": sorted(
                    str(value)
                    for value in graph.objects(prop, RDFS.range)
                    if isinstance(value, URIRef)
                ),
                "source": "tbox",
                "evidence": {
                    "ttl_file": evidence_file,
                    "property_type": str(OWL.DatatypeProperty),
                    "domain_predicate": str(RDFS.domain),
                    "range_predicate": str(RDFS.range),
                },
            }
        )

    object_property_iris = {item["property_iri"] for item in object_properties}
    for class_iri in sorted(closure):
        class_node = URIRef(class_iri)
        for restriction in _restriction_nodes(graph, class_node):
            prop = graph.value(restriction, OWL.onProperty)
            if not isinstance(prop, URIRef) or str(prop) not in object_property_iris:
                continue
            cardinalities = (
                (OWL.minCardinality, "min_cardinality"),
                (OWL.cardinality, "cardinality"),
                (OWL.minQualifiedCardinality, "min_qualified_cardinality"),
                (OWL.qualifiedCardinality, "qualified_cardinality"),
            )
            for cardinality_predicate, kind in cardinalities:
                count = _literal_int(graph.value(restriction, cardinality_predicate))
                if count is None:
                    continue
                target = graph.value(restriction, OWL.onClass)
                if not isinstance(target, URIRef):
                    target = graph.value(prop, RDFS.range)
                restriction_fingerprint = hashlib.sha256(
                    json.dumps(
                        {
                            "subject_class_iri": class_iri,
                            "predicate_iri": str(prop),
                            "target_class_iri": (
                                str(target) if isinstance(target, URIRef) else ""
                            ),
                            "cardinality_predicate_iri": str(
                                cardinality_predicate
                            ),
                            "count": count,
                            "constraint_kind": kind,
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                ).hexdigest()[:16]
                item = {
                    "subject_class_iri": class_iri,
                    "predicate_iri": str(prop),
                    "target_class_iri": str(target) if isinstance(target, URIRef) else "",
                    "min_count": count,
                    "constraint_kind": kind,
                    "source": "owl_restriction",
                    "evidence": {
                        "ttl_file": evidence_file,
                        "restriction_node": (
                            f"_:deterministic-restriction-{restriction_fingerprint}"
                        ),
                        "cardinality_predicate_iri": str(cardinality_predicate),
                    },
                }
                structured_constraints.append(item)
                if count > 0:
                    required_links.append(item)

    return {
        "ontology_name": ontology_name,
        "ttl_file": configured_ttl_file or str(tbox_path),
        "resolved_ttl_file": evidence_file,
        "top_role": _machine_top_role(graph, evidence_file=evidence_file),
        "classes": classes,
        "subclass_closure": subclass_closure,
        "object_properties": object_properties,
        "datatype_properties": datatype_properties,
        "constraints": structured_constraints,
        "required_links": required_links,
    }


def build_ontology_publish_contract(
    *,
    meta_task_config_path: str | Path,
    ontology_name: str | None = None,
) -> dict[str, Any]:
    """Build a publish contract solely from the configured ontology T-Box."""
    meta_cfg = load_meta_task_config(meta_task_config_path)
    ontology_cfg = _ontology_config(meta_cfg, ontology_name)
    ttl_file = str(ontology_cfg.get("ttl_file") or "").strip()
    if not ttl_file:
        raise FileNotFoundError("Ontology config does not define ttl_file")
    tbox_path = _resolve_tbox_path(ttl_file, meta_task_config_path)

    return build_ontology_publish_contract_from_tbox(
        tbox_path,
        ontology_name=str(ontology_cfg.get("name") or ontology_name or ""),
        configured_ttl_file=ttl_file,
    )
