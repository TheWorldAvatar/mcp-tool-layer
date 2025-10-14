import os
from typing import List, Tuple
from rdflib import Graph, Namespace, URIRef
from rdflib.namespace import RDF, RDFS


def load_graph_from_file(path: str) -> Graph:
    g = Graph()
    if os.path.exists(path):
        g.parse(path, format="turtle")
    return g


def load_top_entities_from_output_top(hash_value: str) -> List[Tuple[str, str]]:
    """
    Load labels and IRIs of top-level entities from output_top.ttl under data/<hash>.
    Returns list of (label, iri).
    """
    from models.locations import DATA_DIR
    top_path = os.path.join(DATA_DIR, hash_value, "output_top.ttl")
    g = load_graph_from_file(top_path)
    results: List[Tuple[str, str]] = []
    for s, _, _ in g.triples((None, RDF.type, None)):
        lbl = None
        for o in g.objects(s, RDFS.label):
            lbl = str(o)
            break
        if lbl:
            results.append((lbl, str(s)))
    return results


def load_ontomops_extension_ttl(hash_value: str) -> str:
    """Load ontomops_extension.ttl content for context."""
    from models.locations import DATA_DIR
    path = os.path.join(DATA_DIR, hash_value, "ontomops_extension.ttl")
    if not os.path.exists(path):
        return ""
    with open(path, "r", encoding="utf-8") as f:
        return f.read()

