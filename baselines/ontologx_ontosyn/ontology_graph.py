"""Build an OntoLogX-style ontology GraphDocument from ontosynthesis.ttl."""

from __future__ import annotations

from pathlib import Path

from rdflib import OWL, RDF, RDFS, Graph, URIRef
from rdflib.collection import Collection

from graph_types import Document, GraphDocument, Node, Relationship

ONTOSYN = "https://www.theworldavatar.com/kg/OntoSyn/"
OM2 = "http://www.ontology-of-units-of-measure.org/resource/om-2/"
ONTOMOPS = "https://www.theworldavatar.com/kg/ontomops/"
ONTOSPECIES = "http://www.theworldavatar.com/ontology/ontospecies/OntoSpecies.owl#"
PERIODIC = "http://www.daml.org/2003/01/periodictable/PeriodicTable#"
RDFS_NS = "http://www.w3.org/2000/01/rdf-schema#"
XSD = "http://www.w3.org/2001/XMLSchema#"

MEASURE_CLASSES = {
    f"{OM2}Temperature": ["om-2:hasNumericalValue", "om-2:hasUnit", "rdfs:label"],
    f"{OM2}Duration": ["om-2:hasNumericalValue", "om-2:hasUnit", "rdfs:label"],
    f"{OM2}Pressure": ["om-2:hasNumericalValue", "om-2:hasUnit", "rdfs:label"],
    f"{OM2}Volume": ["om-2:hasNumericalValue", "om-2:hasUnit", "rdfs:label"],
    f"{OM2}TemperatureRate": ["om-2:hasNumericalValue", "om-2:hasUnit", "rdfs:label"],
    f"{OM2}AmountOfSubstanceFraction": ["om-2:hasNumericalValue", "om-2:hasUnit", "rdfs:label"],
}

INCLUDED_ONTOMOPS = {
    f"{ONTOMOPS}MetalOrganicPolyhedron": ["ontomops:hasCCDCNumber", "rdfs:label"],
}
BIBO_DOCUMENT = "http://purl.org/ontology/bibo/Document"

STEP_SUBCLASSES = {
    f"{ONTOSYN}Add",
    f"{ONTOSYN}Stir",
    f"{ONTOSYN}HeatChill",
    f"{ONTOSYN}Evaporate",
    f"{ONTOSYN}Sonicate",
    f"{ONTOSYN}Crystallize",
    f"{ONTOSYN}Transfer",
    f"{ONTOSYN}Separate",
    f"{ONTOSYN}Filter",
    f"{ONTOSYN}Dry",
}


def curie(iri: str) -> str:
    if iri.startswith(ONTOSYN):
        return f"ontosyn:{iri[len(ONTOSYN):]}"
    if iri.startswith(OM2):
        return f"om-2:{iri[len(OM2):]}"
    if iri.startswith(ONTOMOPS):
        return f"ontomops:{iri[len(ONTOMOPS):]}"
    if iri.startswith(ONTOSPECIES):
        return f"ontospecies:{iri[len(ONTOSPECIES):]}"
    if iri.startswith(PERIODIC):
        return f"periodic:{iri[len(PERIODIC):]}"
    if iri.startswith(RDFS_NS):
        return f"rdfs:{iri[len(RDFS_NS):]}"
    if iri.startswith("https://www.theworldavatar.com/kg/OntoLab/"):
        return f"ontolab:{iri.rsplit('/', 1)[-1]}"
    if iri == BIBO_DOCUMENT:
        return "bibo:Document"
    return iri


def _expand_union(graph: Graph, node) -> list[URIRef]:
    if node is None:
        return []
    union = graph.value(node, OWL.unionOf)
    if union is not None:
        return [item for item in Collection(graph, union) if isinstance(item, URIRef)]
    if isinstance(node, URIRef):
        return [node]
    return []


def load_ontology_graph(ontology_path: str | Path) -> GraphDocument:
    name = Path(ontology_path).name.lower()
    if "ontospecies" in name:
        return load_ontospecies_ontology_graph(ontology_path)
    if "ontomops" in name:
        return load_ontomops_ontology_graph(ontology_path)
    return load_ontosynthesis_ontology_graph(ontology_path)


def load_ontosynthesis_ontology_graph(ontology_path: str | Path) -> GraphDocument:
    graph = Graph()
    graph.parse(str(ontology_path), format="turtle")

    class_iris: set[str] = set()
    for cls in graph.subjects(RDF.type, OWL.Class):
        if isinstance(cls, URIRef) and str(cls).startswith(ONTOSYN):
            class_iris.add(str(cls))
    class_iris.update(MEASURE_CLASSES)
    class_iris.update(INCLUDED_ONTOMOPS)
    class_iris.add(BIBO_DOCUMENT)
    class_iris.discard(f"{ONTOSYN}ExecutionPoint")

    props_by_class: dict[str, dict[str, str]] = {iri: {"rdfs:label": "label"} for iri in class_iris}
    for iri, extra in MEASURE_CLASSES.items():
        for item in extra:
            props_by_class[iri][item] = item.split(":")[-1]
    for iri, extra in INCLUDED_ONTOMOPS.items():
        for item in extra:
            props_by_class[iri][item] = item.split(":")[-1]

    object_triples: list[tuple[str, str, str]] = []
    structural: list[tuple[str, str, str]] = []

    for cls_iri in class_iris:
        for parent in graph.objects(URIRef(cls_iri), RDFS.subClassOf):
            if isinstance(parent, URIRef) and str(parent) in class_iris:
                structural.append((curie(cls_iri), "rdfs:subClassOf", curie(str(parent))))

    for pred_type in (OWL.ObjectProperty, OWL.DatatypeProperty):
        for prop in graph.subjects(RDF.type, pred_type):
            if not isinstance(prop, URIRef):
                continue
            iri = str(prop)
            if not iri.startswith(ONTOSYN) and not iri.startswith(ONTOMOPS):
                continue
            domains = []
            for domain in graph.objects(prop, RDFS.domain):
                domains.extend(str(item) for item in _expand_union(graph, domain))
            ranges = []
            for rng in graph.objects(prop, RDFS.range):
                ranges.extend(str(item) for item in _expand_union(graph, rng))
            if pred_type == OWL.DatatypeProperty:
                for domain in domains or class_iris:
                    if domain in props_by_class:
                        props_by_class[domain][curie(iri)] = _local_name(iri)
            else:
                for domain in domains:
                    if domain not in class_iris:
                        continue
                    for rng in ranges:
                        mapped_range = rng
                        if rng.startswith("https://www.theworldavatar.com/kg/OntoLab/"):
                            mapped_range = f"{ONTOSYN}Equipment"
                        if mapped_range not in class_iris:
                            continue
                        object_triples.append((curie(domain), curie(iri), curie(mapped_range)))

    nodes = [
        Node(id=iri, type=curie(iri), properties=props_by_class[iri])
        for iri in sorted(class_iris)
    ]
    nodes_by_type = {node.type: node for node in nodes}
    relationships = []
    for source, rel, target in object_triples + structural:
        if source not in nodes_by_type or target not in nodes_by_type:
            continue
        relationships.append(
            Relationship(source=nodes_by_type[source], target=nodes_by_type[target], type=rel)
        )

    return GraphDocument(
        nodes=nodes,
        relationships=relationships,
        source=Document(page_content="OntoSynthesis ontology", metadata={"ontology_uri": ONTOSYN}),
    )


def load_ontospecies_ontology_graph(ontology_path: str | Path) -> GraphDocument:
    """Structured-output T-Box for the OntoSpecies extension pass."""
    graph = Graph()
    graph.parse(str(ontology_path), format="turtle")

    class_iris: set[str] = set()
    for cls in graph.subjects(RDF.type, OWL.Class):
        if not isinstance(cls, URIRef):
            continue
        iri = str(cls)
        if iri.startswith(ONTOSPECIES) or iri.startswith(PERIODIC):
            class_iris.add(iri)
    class_iris.add(f"{ONTOSYN}ChemicalSynthesis")

    props_by_class: dict[str, dict[str, str]] = {iri: {"rdfs:label": "label"} for iri in class_iris}
    object_triples: list[tuple[str, str, str]] = []
    structural: list[tuple[str, str, str]] = []

    for cls_iri in class_iris:
        for parent in graph.objects(URIRef(cls_iri), RDFS.subClassOf):
            if isinstance(parent, URIRef) and str(parent) in class_iris:
                structural.append((curie(cls_iri), "rdfs:subClassOf", curie(str(parent))))

    for pred_type in (OWL.ObjectProperty, OWL.DatatypeProperty):
        for prop in graph.subjects(RDF.type, pred_type):
            if not isinstance(prop, URIRef):
                continue
            iri = str(prop)
            if not iri.startswith(ONTOSPECIES):
                continue
            domains = []
            for domain in graph.objects(prop, RDFS.domain):
                domains.extend(str(item) for item in _expand_union(graph, domain))
            ranges = []
            for rng in graph.objects(prop, RDFS.range):
                ranges.extend(str(item) for item in _expand_union(graph, rng))
            if pred_type == OWL.DatatypeProperty:
                for domain in domains or class_iris:
                    if domain in props_by_class:
                        props_by_class[domain][curie(iri)] = _local_name(iri)
            else:
                for domain in domains:
                    if domain not in class_iris:
                        continue
                    for rng in ranges:
                        if rng not in class_iris:
                            continue
                        object_triples.append((curie(domain), curie(iri), curie(rng)))

    # Cross-ontology attach: inherited ChemicalSynthesis → Species product.
    object_triples.append(
        (
            "ontosyn:ChemicalSynthesis",
            "ontosyn:hasChemicalOutput",
            "ontospecies:Species",
        )
    )
    props_by_class[f"{ONTOSYN}ChemicalSynthesis"]["rdfs:label"] = "label"

    nodes = [
        Node(id=iri, type=curie(iri), properties=props_by_class[iri])
        for iri in sorted(class_iris)
    ]
    nodes_by_type = {node.type: node for node in nodes}
    relationships = []
    for source, rel, target in object_triples + structural:
        if source not in nodes_by_type or target not in nodes_by_type:
            continue
        relationships.append(
            Relationship(source=nodes_by_type[source], target=nodes_by_type[target], type=rel)
        )

    return GraphDocument(
        nodes=nodes,
        relationships=relationships,
        source=Document(page_content="OntoSpecies ontology", metadata={"ontology_uri": ONTOSPECIES}),
    )


def load_ontomops_ontology_graph(ontology_path: str | Path) -> GraphDocument:
    """Structured-output T-Box for the OntoMOPs extension pass."""
    graph = Graph()
    graph.parse(str(ontology_path), format="turtle")

    class_iris: set[str] = set()
    for cls in graph.subjects(RDF.type, OWL.Class):
        if not isinstance(cls, URIRef):
            continue
        iri = str(cls)
        if iri.startswith(ONTOMOPS):
            class_iris.add(iri)
    class_iris.add(f"{ONTOSYN}ChemicalSynthesis")
    class_iris.add(f"{ONTOSYN}ChemicalOutput")

    props_by_class: dict[str, dict[str, str]] = {iri: {"rdfs:label": "label"} for iri in class_iris}
    object_triples: list[tuple[str, str, str]] = []
    structural: list[tuple[str, str, str]] = []

    for cls_iri in class_iris:
        for parent in graph.objects(URIRef(cls_iri), RDFS.subClassOf):
            if isinstance(parent, URIRef) and str(parent) in class_iris:
                structural.append((curie(cls_iri), "rdfs:subClassOf", curie(str(parent))))

    for pred_type in (OWL.ObjectProperty, OWL.DatatypeProperty):
        for prop in graph.subjects(RDF.type, pred_type):
            if not isinstance(prop, URIRef):
                continue
            iri = str(prop)
            if not iri.startswith(ONTOMOPS):
                continue
            domains = []
            for domain in graph.objects(prop, RDFS.domain):
                domains.extend(str(item) for item in _expand_union(graph, domain))
            ranges = []
            for rng in graph.objects(prop, RDFS.range):
                ranges.extend(str(item) for item in _expand_union(graph, rng))
            if pred_type == OWL.DatatypeProperty:
                for domain in domains or class_iris:
                    if domain in props_by_class:
                        props_by_class[domain][curie(iri)] = _local_name(iri)
            else:
                for domain in domains:
                    if domain not in class_iris:
                        continue
                    for rng in ranges:
                        if rng not in class_iris:
                            continue
                        object_triples.append((curie(domain), curie(iri), curie(rng)))

    object_triples.extend(
        [
            (
                "ontosyn:ChemicalSynthesis",
                "ontosyn:hasChemicalOutput",
                "ontosyn:ChemicalOutput",
            ),
            (
                "ontosyn:ChemicalOutput",
                "ontosyn:isRepresentedBy",
                "ontomops:MetalOrganicPolyhedron",
            ),
        ]
    )

    nodes = [
        Node(id=iri, type=curie(iri), properties=props_by_class[iri])
        for iri in sorted(class_iris)
    ]
    nodes_by_type = {node.type: node for node in nodes}
    relationships = []
    for source, rel, target in object_triples + structural:
        if source not in nodes_by_type or target not in nodes_by_type:
            continue
        relationships.append(
            Relationship(source=nodes_by_type[source], target=nodes_by_type[target], type=rel)
        )

    return GraphDocument(
        nodes=nodes,
        relationships=relationships,
        source=Document(page_content="OntoMOPs ontology", metadata={"ontology_uri": ONTOMOPS}),
    )


def _local_name(iri: str) -> str:
    return iri.rsplit("/", 1)[-1].rsplit("#", 1)[-1]
