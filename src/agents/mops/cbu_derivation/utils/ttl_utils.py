import os
from typing import List, Tuple
from rdflib import Graph, Namespace
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


def load_cbu_labels_from_ontomops_extension(hash_value: str) -> list[str]:
    """Query ontomops_extension.ttl for all ontomops:ChemicalBuildingUnit labels."""
    from models.locations import DATA_DIR
    path = os.path.join(DATA_DIR, hash_value, "ontomops_extension.ttl")
    g = load_graph_from_file(path)
    ONTOMOPS = Namespace("https://www.theworldavatar.com/kg/ontomops/")
    g.bind("ontomops", ONTOMOPS)
    labels: list[str] = []
    try:
        q = (
            """
            SELECT DISTINCT ?label WHERE {
              ?s a ontomops:ChemicalBuildingUnit ; rdfs:label ?label .
            }
            """
        )
        for row in g.query(q, initNs={"ontomops": ONTOMOPS, "rdfs": RDFS}):
            lbl = str(row[0])
            if lbl:
                labels.append(lbl)
    except Exception:
        # Fallback without SPARQL if needed
        for s in g.subjects(RDF.type, ONTOMOPS.ChemicalBuildingUnit):
            for o in g.objects(s, RDFS.label):
                labels.append(str(o))
    # Deduplicate while preserving order
    seen = set()
    unique_labels = []
    for l in labels:
        if l not in seen:
            seen.add(l)
            unique_labels.append(l)
    return unique_labels


def load_cbu_label_iri_pairs_from_ontomops_extension(hash_value: str) -> list[tuple[str, str]]:
    """Return list of (label, iri) for all ontomops:ChemicalBuildingUnit individuals."""
    from models.locations import DATA_DIR
    path = os.path.join(DATA_DIR, hash_value, "ontomops_extension.ttl")
    g = load_graph_from_file(path)
    ONTOMOPS = Namespace("https://www.theworldavatar.com/kg/ontomops/")
    g.bind("ontomops", ONTOMOPS)
    pairs: list[tuple[str, str]] = []
    try:
        q = (
            """
            SELECT DISTINCT ?s ?label WHERE {
              ?s a ontomops:ChemicalBuildingUnit ; rdfs:label ?label .
            }
            """
        )
        for row in g.query(q, initNs={"ontomops": ONTOMOPS, "rdfs": RDFS}):
            iri = str(row[0])
            lbl = str(row[1])
            if lbl and iri:
                pairs.append((lbl, iri))
    except Exception:
        for s in g.subjects(RDF.type, ONTOMOPS.ChemicalBuildingUnit):
            iri = str(s)
            for o in g.objects(s, RDFS.label):
                lbl = str(o)
                if lbl:
                    pairs.append((lbl, iri))
    # Deduplicate by label keeping first seen
    seen = set()
    out: list[tuple[str, str]] = []
    for lbl, iri in pairs:
        if lbl not in seen:
            seen.add(lbl)
            out.append((lbl, iri))
    return out


def load_cbu_label_iri_pairs_from_ontomops_output(hash_value: str) -> list[tuple[str, str]]:
    """Aggregate (label, iri) pairs for ontomops:ChemicalBuildingUnit from all TTLs under ontomops_output.

    Looks into data/<hash>/ontomops_output/*.ttl and merges results, preserving first-seen label order.
    """
    from models.locations import DATA_DIR
    base_dir = os.path.join(DATA_DIR, hash_value, "ontomops_output")
    ONTOMOPS = Namespace("https://www.theworldavatar.com/kg/ontomops/")
    pairs: list[tuple[str, str]] = []
    if not os.path.isdir(base_dir):
        return pairs
    for name in sorted(os.listdir(base_dir)):
        if not name.endswith(".ttl"):
            continue
        path = os.path.join(base_dir, name)
        g = load_graph_from_file(path)
        g.bind("ontomops", ONTOMOPS)
        try:
            q = (
                """
                SELECT DISTINCT ?s ?label WHERE {
                  ?s a ontomops:ChemicalBuildingUnit ; rdfs:label ?label .
                }
                """
            )
            for row in g.query(q, initNs={"ontomops": ONTOMOPS, "rdfs": RDFS}):
                iri = str(row[0])
                lbl = str(row[1])
                if lbl and iri:
                    pairs.append((lbl, iri))
        except Exception:
            for s in g.subjects(RDF.type, ONTOMOPS.ChemicalBuildingUnit):
                iri = str(s)
                for o in g.objects(s, RDFS.label):
                    lbl = str(o)
                    if lbl:
                        pairs.append((lbl, iri))
    # Deduplicate by label keeping first seen
    seen = set()
    out: list[tuple[str, str]] = []
    for lbl, iri in pairs:
        if lbl not in seen:
            seen.add(lbl)
            out.append((lbl, iri))
    return out
