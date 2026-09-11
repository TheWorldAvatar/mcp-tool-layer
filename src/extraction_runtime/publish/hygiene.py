"""Light published-graph hygiene. Domain rules stay in generated SPARQL."""

from __future__ import annotations

from rdflib import Graph, URIRef
from rdflib.namespace import RDF, RDFS


def first_label(graph: Graph, node: URIRef) -> str:
    for value in graph.objects(node, RDFS.label):
        text = str(value).strip()
        if text:
            return text
    return ""


def remap_nodes(graph: Graph, remap: dict[URIRef, URIRef]) -> Graph:
    if not remap:
        return graph
    out = Graph()
    for prefix, namespace in graph.namespaces():
        out.bind(prefix, namespace)
    for subject, predicate, obj in graph:
        out.add((remap.get(subject, subject), predicate, remap.get(obj, obj) if isinstance(obj, URIRef) else obj))
    return out


def typed_nodes(graph: Graph) -> set[URIRef]:
    return {node for node in graph.subjects(RDF.type, None) if isinstance(node, URIRef)}
