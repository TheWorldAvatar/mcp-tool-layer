"""SHACL validation helpers used as OntoLogX correction feedback."""

from __future__ import annotations

from pathlib import Path

from rdflib import URIRef, Graph

from graph_types import GraphDocument
from ttl_export import graph_to_rdflib


def validate_graph(
    graph_doc: GraphDocument,
    ontology_path: str | Path,
    shacl_path: str | Path,
    paper_hash: str,
) -> tuple[bool, list[str], float]:
    try:
        import pyshacl
    except ImportError as exc:
        raise RuntimeError("pyshacl is required for OntoLogX SHACL correction/metrics") from exc

    data_graph = graph_to_rdflib(graph_doc, paper_hash)
    ont_graph = Graph()
    ont_graph.parse(str(ontology_path), format="turtle")
    om2_path = Path(ontology_path).with_name("om2.ttl")
    if om2_path.exists():
        # pyshacl sh:class only sees types in the data graph. Unit individuals
        # live in om2.ttl, so load them there as well as into ont_graph.
        data_graph.parse(str(om2_path), format="turtle")
        ont_graph.parse(str(om2_path), format="turtle")
    shacl_graph = Graph()
    shacl_graph.parse(str(shacl_path), format="turtle")

    conforms, results_graph, results_text = pyshacl.validate(
        data_graph,
        shacl_graph=shacl_graph,
        ont_graph=ont_graph,
        inference="rdfs",
        abort_on_first=False,
        allow_infos=True,
        allow_warnings=True,
    )
    messages = [line.strip() for line in str(results_text).splitlines() if line.strip()]
    constraint_count = len(list(shacl_graph.subject_objects()))
    # OntoLogX reports violations / property-constraint count.
    property_constraints = list(
        shacl_graph.subject_objects(URIRef("http://www.w3.org/ns/shacl#property"))
    )
    n_constraints = max(len(property_constraints), 1)
    n_violations = str(results_text).count("Constraint Violation")
    ratio = n_violations / n_constraints
    return bool(conforms), messages[:40], ratio
