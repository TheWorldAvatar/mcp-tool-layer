"""Serialize an OntoLogX GraphDocument to OntoSynthesis Turtle."""

from __future__ import annotations

import re
from pathlib import Path

from rdflib import RDF, RDFS, XSD, Graph, Literal, Namespace, URIRef

from graph_types import Document, GraphDocument, Node, Relationship
from src.agents.scripts_and_prompts_generation.fixed_om2_runtime import resolve_om2_unit

ONTOSYN = Namespace("https://www.theworldavatar.com/kg/OntoSyn/")
OM2 = Namespace("http://www.ontology-of-units-of-measure.org/resource/om-2/")
ONTOMOPS = Namespace("https://www.theworldavatar.com/kg/ontomops/")
ONTOSPECIES = Namespace("http://www.theworldavatar.com/ontology/ontospecies/OntoSpecies.owl#")
PERIODIC = Namespace("http://www.daml.org/2003/01/periodictable/PeriodicTable#")
BIBO = Namespace("http://purl.org/ontology/bibo/")
INSTANCE = Namespace("https://www.theworldavatar.com/kg/instance/ontologx/")

STEP_TYPES = {
    "ontosyn:Add",
    "ontosyn:Stir",
    "ontosyn:HeatChill",
    "ontosyn:Evaporate",
    "ontosyn:Sonicate",
    "ontosyn:Crystallize",
    "ontosyn:Transfer",
    "ontosyn:Separate",
    "ontosyn:Filter",
    "ontosyn:Dry",
}

NAMESPACES = {
    "ontosyn": ONTOSYN,
    "om-2": OM2,
    "ontomops": ONTOMOPS,
    "ontospecies": ONTOSPECIES,
    "periodic": PERIODIC,
    "bibo": BIBO,
    "rdfs": RDFS,
}


def _expand(name: str) -> URIRef:
    if name.startswith("http://") or name.startswith("https://"):
        return URIRef(name)
    if ":" not in name:
        return URIRef(str(ONTOSYN) + name)
    prefix, local = name.split(":", 1)
    if prefix not in NAMESPACES:
        raise ValueError(f"Unknown prefix: {prefix}")
    return NAMESPACES[prefix][local]


def _safe_id(node_id: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_\-]+", "_", str(node_id)).strip("_")
    return cleaned or "node"


def _literal(value) -> Literal:
    if isinstance(value, bool):
        return Literal(value, datatype=XSD.boolean)
    if isinstance(value, int) and not isinstance(value, bool):
        return Literal(value, datatype=XSD.integer)
    if isinstance(value, float):
        return Literal(value, datatype=XSD.double)
    text = str(value)
    if text.lower() in {"true", "false"}:
        return Literal(text.lower() == "true", datatype=XSD.boolean)
    return Literal(text, datatype=XSD.string)


def graph_to_rdflib(graph_doc: GraphDocument, paper_hash: str) -> Graph:
    rdf = Graph()
    rdf.bind("ontosyn", ONTOSYN)
    rdf.bind("om-2", OM2)
    rdf.bind("ontomops", ONTOMOPS)
    rdf.bind("ontospecies", ONTOSPECIES)
    rdf.bind("periodic", PERIODIC)
    rdf.bind("bibo", BIBO)
    rdf.bind("rdfs", RDFS)

    uri_by_id = {}
    for node in graph_doc.nodes:
        uri = INSTANCE[f"{paper_hash}/{_safe_id(node.id)}"]
        uri_by_id[node.id] = uri
        rdf.add((uri, RDF.type, _expand(node.type)))
        for extra in node.extra_types or []:
            if extra and extra != node.type:
                rdf.add((uri, RDF.type, _expand(extra)))
        if node.type in STEP_TYPES:
            rdf.add((uri, RDF.type, ONTOSYN.SynthesisStep))
        for key, value in (node.properties or {}).items():
            if value is None or value == "":
                continue
            if key in {"om-2:hasNumericalValue", "hasNumericalValue"}:
                try:
                    rdf.add((uri, _expand(key), Literal(float(value), datatype=XSD.double)))
                    continue
                except (TypeError, ValueError):
                    pass
            if key in {
                "ontospecies:hasPercentageValue",
                "ontospecies:hasAtomicWeightValue",
                "hasPercentageValue",
                "hasAtomicWeightValue",
            }:
                try:
                    rdf.add((uri, _expand(key), Literal(float(value), datatype=XSD.float)))
                    continue
                except (TypeError, ValueError):
                    pass
            if key in {"om-2:hasUnit", "hasUnit"}:
                try:
                    rdf.add((uri, OM2.hasUnit, resolve_om2_unit(str(value))))
                except ValueError:
                    rdf.add((uri, OM2.hasUnit, Literal(str(value), datatype=XSD.string)))
                continue
            rdf.add((uri, _expand(key), _literal(value)))

    for rel in graph_doc.relationships:
        source = uri_by_id.get(rel.source.id)
        target = uri_by_id.get(rel.target.id)
        if source is None or target is None:
            continue
        rdf.add((source, _expand(rel.type), target))
    return rdf


def write_ttl(graph_doc: GraphDocument, paper_hash: str, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    rdf = graph_to_rdflib(graph_doc, paper_hash)
    rdf.serialize(destination=str(path), format="turtle")
    return path


def graph_to_turtle(graph_doc: GraphDocument, paper_hash: str) -> str:
    """Serialize the inherited main graph exactly as Pipeline pastes main_ontology_a_box."""
    return graph_to_rdflib(graph_doc, paper_hash).serialize(format="turtle")


def instance_iri(paper_hash: str, node_id: str) -> str:
    return str(INSTANCE[f"{paper_hash}/{_safe_id(node_id)}"])


_INSTANCE_ROOT = "https://www.theworldavatar.com/kg/instance/ontologx/"
_CURIE_PREFIXES = (
    ("ontosyn:", str(ONTOSYN)),
    ("om-2:", str(OM2)),
    ("ontomops:", str(ONTOMOPS)),
    ("ontospecies:", str(ONTOSPECIES)),
    ("periodic:", str(PERIODIC)),
    ("bibo:", str(BIBO)),
    ("rdfs:", str(RDFS)),
)


def _compact(iri: str) -> str:
    for prefix, ns in _CURIE_PREFIXES:
        if iri.startswith(ns):
            return prefix + iri[len(ns):]
    if iri.startswith("http://www.w3.org/2000/01/rdf-schema#"):
        return "rdfs:" + iri.rsplit("#", 1)[-1]
    return iri


def _node_id_from_uri(uri: str, paper_hash: str) -> str:
    prefix = f"{_INSTANCE_ROOT}{paper_hash}/"
    if uri.startswith(prefix):
        return uri[len(prefix):]
    return uri.rsplit("/", 1)[-1]


def _prefer_primary(types: list[str]) -> tuple[str, list[str]]:
    unique: list[str] = []
    for item in types:
        if item and item not in unique:
            unique.append(item)
    if not unique:
        return "", []
    preferred = next((item for item in unique if item.startswith("ontosyn:")), None)
    if preferred is None:
        return unique[0], unique[1:]
    return preferred, [item for item in unique if item != preferred]


def read_ttl(path: Path, paper_hash: str) -> GraphDocument:
    """Reload an OntoLogX instance TTL into a GraphDocument (ids preserved)."""
    rdf = Graph()
    rdf.parse(str(path), format="turtle")
    types_by_id: dict[str, list[str]] = {}
    for subject, _, type_iri in rdf.triples((None, RDF.type, None)):
        iri = str(subject)
        if not iri.startswith(_INSTANCE_ROOT):
            continue
        compact = _compact(str(type_iri))
        if compact in {"ontosyn:SynthesisStep", "rdfs:Resource"}:
            continue
        types_by_id.setdefault(_node_id_from_uri(iri, paper_hash), []).append(compact)
    nodes: dict[str, Node] = {}
    for node_id, type_names in types_by_id.items():
        primary, extra = _prefer_primary(type_names)
        nodes[node_id] = Node(id=node_id, type=primary, properties={}, extra_types=extra)

    for subject, pred, obj in rdf:
        iri = str(subject)
        if not iri.startswith(_INSTANCE_ROOT):
            continue
        node_id = _node_id_from_uri(iri, paper_hash)
        node = nodes.get(node_id)
        if node is None or pred in {RDF.type}:
            continue
        if not isinstance(obj, Literal):
            continue
        key = _compact(str(pred))
        if key in {"om-2:hasNumericalValue", "ontospecies:hasPercentageValue", "ontospecies:hasAtomicWeightValue"}:
            try:
                node.properties[key] = float(obj)
                continue
            except (TypeError, ValueError):
                pass
        value = obj.toPython()
        node.properties[key] = (
            value if isinstance(value, (bool, int, float)) else str(value)
        )

    relationships: list[Relationship] = []
    seen: set[tuple[str, str, str]] = set()
    for subject, pred, obj in rdf:
        if pred == RDF.type or isinstance(obj, Literal):
            continue
        source_id = _node_id_from_uri(str(subject), paper_hash)
        target_id = _node_id_from_uri(str(obj), paper_hash)
        source = nodes.get(source_id)
        target = nodes.get(target_id)
        if source is None or target is None:
            continue
        rel_type = _compact(str(pred))
        key = (source_id, rel_type, target_id)
        if key in seen:
            continue
        seen.add(key)
        relationships.append(Relationship(source=source, target=target, type=rel_type))

    return GraphDocument(
        nodes=list(nodes.values()),
        relationships=relationships,
        source=Document(page_content=str(path), metadata={"paper_hash": paper_hash}),
    )
