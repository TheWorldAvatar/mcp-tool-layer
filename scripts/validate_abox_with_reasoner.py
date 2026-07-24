#!/usr/bin/env python3
"""Validate A-Box instance graphs against a T-Box using OWL-RL reasoning and SPARQL checks.

This script complements the pipeline's contract/shell validation with ontology-level checks:
- OWL-RL deductive closure (subclass, domain/range, some OWL constructs)
- Unknown class/property usage in the A-Box
- Domain/range violations on asserted triples
- Optional HermiT consistency check via OWLready2 (when installed)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

from rdflib import Graph, Literal, Namespace, URIRef
from rdflib.namespace import OWL, RDF, RDFS, XSD

try:
    from owlrl import DeductiveClosure, OWLRL_Semantics
except ImportError as exc:  # pragma: no cover - runtime dependency guard
    raise SystemExit(
        "Missing dependency 'owlrl'. Install with: python -m pip install owlrl"
    ) from exc


ROOT = Path(__file__).resolve().parents[1]
OWL_BUILTINS = {
    str(OWL.Class),
    str(OWL.ObjectProperty),
    str(OWL.DatatypeProperty),
    str(OWL.AnnotationProperty),
    str(OWL.Ontology),
    str(OWL.Restriction),
    str(OWL.Thing),
    str(OWL.Nothing),
    str(OWL.NamedIndividual),
    str(RDFS.Class),
    str(RDFS.Resource),
    str(RDF.Property),
}
RDF_TYPE = RDF.type
OM2 = Namespace("http://www.ontology-of-units-of-measure.org/resource/om-2/")
KNOWN_PROFILES = {
    "ontosynthesis": {
        "tbox": [
            ROOT / "data/ontologies/ontosynthesis.ttl",
            ROOT / "data/ontologies/ontomops-subgraph.ttl",
            ROOT / "data/ontologies/ontospecies-subgraph.ttl",
            ROOT / "data/ontologies/om2.ttl",
        ],
        "abox": [
            ROOT / "evaluation/data/merged_tll/0c57bac8/0c57bac8.ttl",
        ],
    },
    "medical": {
        "tbox": ROOT / "medical_case/medical_case_schema_de_non_flat_v4.ttl",
        "tbox_v3": ROOT / "medical_case/medical_case_schema_de_non_flat_v3.ttl",
        "tbox_v4": ROOT / "medical_case/medical_case_schema_de_non_flat_v4.ttl",
        "abox": [],
    },
    "medical_v3": {
        "tbox": ROOT / "medical_case/medical_case_schema_de_non_flat_v3.ttl",
        "abox": [],
    },
}


def _local_name(iri: Any) -> str:
    text = str(iri or "").strip()
    if not text:
        return ""
    for sep in ("#", "/"):
        if sep in text:
            text = text.rsplit(sep, 1)[-1]
    return text


def _load_graph(tbox_paths: list[Path], abox_paths: list[Path]) -> Graph:
    graph = Graph()
    for path in [*tbox_paths, *abox_paths]:
        if not path.exists():
            raise FileNotFoundError(f"TTL file not found: {path}")
        graph.parse(str(path), format="turtle")
    return graph


def _tbox_symbols(graph: Graph) -> tuple[set[str], set[str]]:
    classes: set[str] = set()
    properties: set[str] = set()
    for subject, predicate, obj in graph.triples((None, RDF.type, None)):
        if predicate != RDF.type:
            continue
        if obj in (OWL.Class, RDFS.Class):
            classes.add(str(subject))
        elif obj in (OWL.ObjectProperty, OWL.DatatypeProperty, RDF.Property):
            properties.add(str(subject))
    return classes, properties


def _abox_assertions(abox_paths: list[Path]) -> set[tuple[str, str, str]]:
    """Collect asserted triples from A-Box files only."""
    graph = Graph()
    for path in abox_paths:
        graph.parse(str(path), format="turtle")
    assertions: set[tuple[str, str, str]] = set()
    for subject, predicate, obj in graph.triples((None, None, None)):
        if isinstance(subject, Literal):
            continue
        if isinstance(obj, Literal):
            assertions.add((str(subject), str(predicate), str(obj)))
        else:
            assertions.add((str(subject), str(predicate), str(obj)))
    return assertions


def _collect_domain_range(graph: Graph) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    domains: dict[str, set[str]] = {}
    ranges: dict[str, set[str]] = {}
    for prop in set(graph.subjects(RDF.type, OWL.ObjectProperty)) | set(
        graph.subjects(RDF.type, OWL.DatatypeProperty)
    ):
        prop_iri = str(prop)
        domains[prop_iri] = {str(v) for v in graph.objects(prop, RDFS.domain)}
        ranges[prop_iri] = {str(v) for v in graph.objects(prop, RDFS.range)}
    return domains, ranges


def _instance_types(graph: Graph, node: str) -> set[str]:
    return {str(t) for t in graph.objects(URIRef(node), RDF.type)}


def _literal_matches_range(literal: Literal, range_iris: set[str]) -> bool:
    if not range_iris:
        return True
    if str(XSD.string) in range_iris:
        return True
    dt = literal.datatype
    if dt is None:
        return str(XSD.string) in range_iris
    return str(dt) in range_iris


def _check_unknown_symbols(
    graph: Graph,
    tbox_classes: set[str],
    tbox_properties: set[str],
    abox_triples: set[tuple[str, str, str]],
) -> tuple[list[str], list[str]]:
    unknown_types: list[str] = []
    unknown_properties: list[str] = []

    for subject, predicate, obj in abox_triples:
        if predicate == str(RDF.type) and obj not in tbox_classes and obj not in OWL_BUILTINS:
            unknown_types.append(f"{_local_name(subject)} rdf:type {_local_name(obj)}")
        if (
            predicate not in {str(RDF.type), str(RDFS.label)}
            and not predicate.startswith("http://www.w3.org/2000/01/rdf-schema#")
            and predicate not in tbox_properties
            and predicate not in OWL_BUILTINS
        ):
            unknown_properties.append(
                f"{_local_name(subject)} --{_local_name(predicate)}--> {_local_name(obj)}"
            )

    return sorted(set(unknown_types)), sorted(set(unknown_properties))


def _check_domain_range_violations(
    graph: Graph,
    domains: dict[str, set[str]],
    ranges: dict[str, set[str]],
    abox_triples: set[tuple[str, str, str]],
) -> tuple[list[str], list[str]]:
    domain_violations: list[str] = []
    range_violations: list[str] = []

    for subject, predicate, obj in abox_triples:
        if predicate in {str(RDF.type), str(RDFS.label)} or predicate.startswith(str(RDFS)):
            continue
        domain_iris = domains.get(predicate) or set()
        range_iris = ranges.get(predicate) or set()
        if not domain_iris and not range_iris:
            continue

        subject_types = _instance_types(graph, subject)
        if domain_iris and subject_types.isdisjoint(domain_iris):
            domain_violations.append(
                f"{_local_name(subject)} typed {sorted(_local_name(t) for t in subject_types) or ['?']} "
                f"uses {_local_name(predicate)} (domain expects {sorted(_local_name(d) for d in domain_iris)})"
            )

        if not range_iris:
            continue

        if predicate in {str(p) for p in graph.subjects(RDF.type, OWL.DatatypeProperty)}:
            literal = Literal(obj) if not isinstance(obj, str) else None
            if literal is None:
                # Reconstruct literal from serialized abox triple string is unreliable; query graph.
                for o in graph.objects(URIRef(subject), URIRef(predicate)):
                    if isinstance(o, Literal) and not _literal_matches_range(o, range_iris):
                        range_violations.append(
                            f"{_local_name(subject)} {_local_name(predicate)} literal {o} "
                            f"outside range {sorted(_local_name(r) for r in range_iris)}"
                        )
            elif not _literal_matches_range(literal, range_iris):
                range_violations.append(
                    f"{_local_name(subject)} {_local_name(predicate)} literal {literal} "
                    f"outside range {sorted(_local_name(r) for r in range_iris)}"
                )
        else:
            object_types = _instance_types(graph, obj)
            if object_types and range_iris and object_types.isdisjoint(range_iris):
                range_violations.append(
                    f"{_local_name(subject)} --{_local_name(predicate)}--> {_local_name(obj)} "
                    f"typed {sorted(_local_name(t) for t in object_types)} "
                    f"(range expects {sorted(_local_name(r) for r in range_iris)})"
                )

    return sorted(set(domain_violations)), sorted(set(range_violations))


def _check_om2_quantity_structure(
    tbox_graph: Graph,
    abox_paths: list[Path],
) -> list[str]:
    """Require complete value/unit structures for objects of OM-2 range properties."""
    abox_graph = Graph()
    for path in abox_paths:
        abox_graph.parse(str(path), format="turtle")

    quantity_properties: dict[URIRef, set[URIRef]] = {}
    for prop in tbox_graph.subjects(RDF.type, OWL.ObjectProperty):
        if not isinstance(prop, URIRef):
            continue
        # OM-2's own structural properties (notably hasUnit -> Unit) describe
        # quantity internals. They are not domain extraction properties and
        # must not recursively treat unit individuals as quantities.
        if str(prop).startswith(str(OM2)):
            continue
        ranges = {
            value
            for value in tbox_graph.objects(prop, RDFS.range)
            if isinstance(value, URIRef)
            and str(value).startswith(str(OM2))
        }
        if ranges:
            quantity_properties[prop] = ranges

    violations: list[str] = []
    for prop, expected_types in quantity_properties.items():
        for subject, _, quantity in abox_graph.triples((None, prop, None)):
            if not isinstance(quantity, URIRef):
                violations.append(
                    f"{_local_name(subject)} --{_local_name(prop)}--> literal/non-IRI quantity"
                )
                continue
            asserted_types = {
                value for value in abox_graph.objects(quantity, RDF.type)
                if isinstance(value, URIRef)
            }
            if asserted_types.isdisjoint(expected_types):
                violations.append(
                    f"{_local_name(quantity)} for {_local_name(prop)} must be typed "
                    f"{sorted(_local_name(value) for value in expected_types)}"
                )
            numerical_values = list(abox_graph.objects(quantity, OM2.hasNumericalValue))
            units = list(abox_graph.objects(quantity, OM2.hasUnit))
            if len(numerical_values) != 1:
                violations.append(
                    f"{_local_name(quantity)} for {_local_name(prop)} must have exactly "
                    f"one om-2:hasNumericalValue (found {len(numerical_values)})"
                )
            if len(units) != 1 or not isinstance(units[0], URIRef):
                violations.append(
                    f"{_local_name(quantity)} for {_local_name(prop)} must have exactly "
                    "one IRI-valued om-2:hasUnit"
                )
    return sorted(set(violations))


def _try_hermit_consistency(tbox_paths: list[Path], abox_paths: list[Path]) -> dict[str, Any]:
    """Best-effort OWL DL consistency check via OWLready2 + HermiT."""
    try:
        from owlready2 import default_world, get_ontology, sync_reasoner_hermit
    except ImportError:
        return {"available": False, "reason": "owlready2 not installed"}

    graph = Graph()
    for path in [*tbox_paths, *abox_paths]:
        graph.parse(str(path), format="turtle")

    tmp_name = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".owl", delete=False, dir=os.getcwd()) as handle:
            tmp_name = handle.name
        graph.serialize(destination=tmp_name, format="xml")

        onto = get_ontology(tmp_name)
        onto.load()
        with onto:
            sync_reasoner_hermit(infer_property_values=True)
        inconsistent = [str(c) for c in default_world.inconsistent_classes()]
        return {
            "available": True,
            "consistent": len(inconsistent) == 0,
            "inconsistent_classes": inconsistent,
            "individuals_loaded": len(list(onto.individuals())),
            "classes_loaded": len(list(onto.classes())),
        }
    except Exception as exc:  # pragma: no cover - external reasoner variability
        return {"available": True, "error": f"{type(exc).__name__}: {exc}"}
    finally:
        if tmp_name and os.path.exists(tmp_name):
            os.remove(tmp_name)


def validate(
    tbox_paths: list[Path],
    abox_paths: list[Path],
    *,
    run_hermit: bool = True,
) -> dict[str, Any]:
    tbox_graph = Graph()
    for path in tbox_paths:
        tbox_graph.parse(str(path), format="turtle")

    graph = _load_graph(tbox_paths, abox_paths)
    before = len(graph)
    DeductiveClosure(OWLRL_Semantics).expand(graph)
    after = len(graph)

    tbox_classes, tbox_properties = _tbox_symbols(tbox_graph)
    domains, ranges = _collect_domain_range(graph)
    abox_triples = _abox_assertions(abox_paths)

    unknown_types, unknown_properties = _check_unknown_symbols(
        graph, tbox_classes, tbox_properties, abox_triples
    )
    domain_violations, range_violations = _check_domain_range_violations(
        graph, domains, ranges, abox_triples
    )
    om2_quantity_violations = _check_om2_quantity_structure(tbox_graph, abox_paths)

    failures = [
        *unknown_types,
        *unknown_properties,
        *domain_violations,
        *range_violations,
        *om2_quantity_violations,
    ]

    report: dict[str, Any] = {
        "ok": len(failures) == 0,
        "tbox_paths": [str(p) for p in tbox_paths],
        "abox_paths": [str(p) for p in abox_paths],
        "triples": {"before_reasoning": before, "after_owlrl": after, "inferred": after - before},
        "tbox_symbols": {
            "classes": len(tbox_classes),
            "properties": len(tbox_properties),
        },
        "abox_assertions": len(abox_triples),
        "failures": failures,
        "details": {
            "unknown_types": unknown_types,
            "unknown_properties": unknown_properties,
            "domain_violations": domain_violations,
            "range_violations": range_violations,
            "om2_quantity_violations": om2_quantity_violations,
        },
    }

    if run_hermit:
        report["hermit"] = _try_hermit_consistency(tbox_paths, abox_paths)

    return report


def _resolve_profile(name: str) -> tuple[list[Path], list[Path]]:
    profile = KNOWN_PROFILES.get(name)
    if profile is None:
        raise KeyError(f"Unknown profile '{name}'. Known: {sorted(KNOWN_PROFILES)}")
    tbox = profile["tbox"]
    if isinstance(tbox, Path):
        tbox_paths = [tbox]
    else:
        tbox_paths = [Path(p) for p in tbox]
    return tbox_paths, [Path(p) for p in profile.get("abox") or []]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=sorted(KNOWN_PROFILES), help="Built-in T-Box/A-Box profile")
    parser.add_argument("--tbox", action="append", default=[], help="T-Box TTL path (repeatable)")
    parser.add_argument("--abox", action="append", default=[], help="A-Box TTL path (repeatable)")
    parser.add_argument("--no-hermit", action="store_true", help="Skip HermiT consistency attempt")
    parser.add_argument("--json", action="store_true", help="Emit JSON report")
    args = parser.parse_args()

    if args.profile:
        tbox_paths, abox_paths = _resolve_profile(args.profile)
    else:
        tbox_paths = [Path(p) for p in args.tbox]
        abox_paths = [Path(p) for p in args.abox]

    if not tbox_paths:
        parser.error("Provide --tbox or --profile")
    if not abox_paths:
        parser.error("Provide --abox or a profile that includes A-Box paths")

    report = validate(tbox_paths, abox_paths, run_hermit=not args.no_hermit)
    payload = json.dumps(report, indent=2, ensure_ascii=False)
    try:
        sys.stdout.write(payload + "\n")
    except UnicodeEncodeError:
        sys.stdout.buffer.write((payload + "\n").encode("utf-8", errors="replace"))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
