"""Load instance graphs and T-Box vocabularies without domain names."""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Iterable

from rdflib import BNode, Graph, URIRef
from rdflib.collection import Collection
from rdflib.namespace import OWL, RDF, RDFS, XSD

from src.extraction_prompt_generation.runtime_support.rdf.registry import (
    abox_graph,
)

VOCAB_PREFIXES = (
    "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "http://www.w3.org/2000/01/rdf-schema#",
    "http://www.w3.org/2002/07/owl#",
    "http://www.w3.org/2001/XMLSchema#",
    "http://www.w3.org/ns/shacl#",
    "urn:twa:semantic-mutation:",
)

SCHEMA_TYPES = {
    OWL.Class,
    RDFS.Class,
    OWL.ObjectProperty,
    OWL.DatatypeProperty,
    OWL.AnnotationProperty,
    OWL.Ontology,
    RDF.Property,
    OWL.Restriction,
    OWL.FunctionalProperty,
    OWL.InverseFunctionalProperty,
    OWL.NamedIndividual,
}


def load_ttl(path: Path) -> Graph:
    graph = Graph()
    graph.parse(path, format="turtle")
    return graph


def load_tbox(paths: Iterable[Path]) -> Graph:
    graph = Graph()
    for path in paths:
        resolved = Path(path)
        if not resolved.is_file():
            raise FileNotFoundError(f"T-Box not found: {resolved}")
        graph.parse(str(resolved), format="turtle")
    return graph


def instance_graph(graph: Graph) -> Graph:
    """Drop schema and runtime bookkeeping from a published graph."""
    return abox_graph(graph)


def _is_vocab_prefix(iri: str) -> bool:
    return any(iri.startswith(prefix) for prefix in VOCAB_PREFIXES)


def vocabulary_iris(tbox: Graph) -> set[str]:
    """IRIs that belong to the T-Box or supporting vocab, not the A-Box."""
    found: set[str] = set()
    for prefix in VOCAB_PREFIXES:
        found.add(prefix)
    for subject, predicate, obj in tbox:
        for node in (subject, predicate, obj):
            if isinstance(node, URIRef):
                found.add(str(node))
    return found


def is_vocabulary_iri(iri: str, vocab: set[str]) -> bool:
    text = str(iri or "").strip()
    if not text:
        return True
    if _is_vocab_prefix(text) or text in vocab:
        return True
    return False


def typed_instance_nodes(abox: Graph, vocab: set[str]) -> set[URIRef]:
    """Typed URI subjects that are not T-Box/schema individuals."""
    nodes: set[URIRef] = set()
    for subject, _, obj in abox.triples((None, RDF.type, None)):
        if not isinstance(subject, URIRef):
            continue
        iri = str(subject)
        if is_vocabulary_iri(iri, vocab):
            continue
        if isinstance(obj, URIRef) and obj in SCHEMA_TYPES:
            continue
        nodes.add(subject)
    return nodes


def subclass_superclasses(tbox: Graph) -> dict[str, set[str]]:
    """Map each class IRI to itself plus transitive named superclasses."""
    parents: dict[str, set[str]] = defaultdict(set)
    declared: set[str] = set()
    for cls in tbox.subjects(RDF.type, OWL.Class):
        if isinstance(cls, URIRef):
            declared.add(str(cls))
    for child, _, parent in tbox.triples((None, RDFS.subClassOf, None)):
        if isinstance(child, URIRef) and isinstance(parent, URIRef):
            parents[str(child)].add(str(parent))
            declared.add(str(child))
            declared.add(str(parent))
    closure: dict[str, set[str]] = {iri: {iri} for iri in declared}
    for iri, supers in parents.items():
        closure.setdefault(iri, {iri}).update(supers)
    changed = True
    while changed:
        changed = False
        for iri, supers in list(closure.items()):
            extra: set[str] = set()
            for parent in supers:
                extra.update(closure.get(parent, {parent}))
            if not extra <= supers:
                closure[iri].update(extra)
                changed = True
    return closure


def expand_class_expression(tbox: Graph, node) -> list[URIRef]:
    """Flatten a named class or owl:unionOf into IRI members."""
    if node is None:
        return []
    if isinstance(node, URIRef):
        union = tbox.value(node, OWL.unionOf)
        if union is None:
            return [node]
        return [
            member
            for member in Collection(tbox, union)
            if isinstance(member, URIRef)
        ]
    if isinstance(node, BNode):
        union = tbox.value(node, OWL.unionOf)
        if union is not None:
            return [
                member
                for member in Collection(tbox, union)
                if isinstance(member, URIRef)
            ]
    return []


def types_of(
    node: URIRef,
    *,
    abox: Graph,
    tbox: Graph,
    closure: dict[str, set[str]],
) -> set[str]:
    raw = {
        str(value)
        for graph in (abox, tbox)
        for value in graph.objects(node, RDF.type)
        if isinstance(value, URIRef)
    }
    expanded: set[str] = set()
    for type_iri in raw:
        expanded.update(closure.get(type_iri, {type_iri}))
    return expanded


def compatible_type(
    actual: set[str],
    expected: Iterable[str],
    closure: dict[str, set[str]],
) -> bool:
    wanted = {str(item) for item in expected if str(item).strip()}
    if not wanted:
        return True
    if not actual:
        return False
    for type_iri in actual:
        supers = closure.get(type_iri, {type_iri})
        if wanted & supers:
            return True
        if type_iri in wanted:
            return True
    return False


def never_range_class_iris(tbox: Graph, closure: dict[str, set[str]]) -> set[str]:
    """Named classes whose superclass chain is never an object-property range.

    A step subclass such as ``Add`` is a range *shape* because ``SynthesisStep``
    is a range, even though ``Add`` itself is never written as rdfs:range.
    """
    classes = {
        str(cls)
        for cls in tbox.subjects(RDF.type, OWL.Class)
        if isinstance(cls, URIRef)
    }
    ranges: set[str] = set()
    for prop in tbox.subjects(RDF.type, OWL.ObjectProperty):
        for rng in tbox.objects(prop, RDFS.range):
            for member in expand_class_expression(tbox, rng):
                ranges.add(str(member))
    top: set[str] = set()
    for iri in classes:
        supers = closure.get(iri, {iri})
        if not (supers & ranges):
            top.add(iri)
    return top


def infer_root_iris(
    abox: Graph,
    *,
    tbox: Graph,
    vocab: set[str],
    closure: dict[str, set[str]],
) -> list[str]:
    """Pick bound roots from T-Box shape: instances of never-range classes."""
    instances = typed_instance_nodes(abox, vocab)
    top_classes = never_range_class_iris(tbox, closure)
    if not top_classes:
        return _incoming_free_roots(abox, instances, vocab)
    roots: list[str] = []
    for node in sorted(instances, key=str):
        types = types_of(node, abox=abox, tbox=tbox, closure=closure)
        if types & top_classes:
            roots.append(str(node))
    if roots:
        return roots
    return _incoming_free_roots(abox, instances, vocab)


def _incoming_free_roots(
    abox: Graph,
    instances: set[URIRef],
    vocab: set[str],
) -> list[str]:
    del vocab
    pointed_at: set[URIRef] = set()
    for subject, _, obj in abox:
        if (
            isinstance(subject, URIRef)
            and subject in instances
            and isinstance(obj, URIRef)
            and obj in instances
        ):
            pointed_at.add(obj)
    return [str(node) for node in sorted(instances - pointed_at, key=str)]


def load_identity_root(ttl_path: Path) -> str | None:
    sidecar = ttl_path.with_name(f"{ttl_path.stem}.identity.json")
    if not sidecar.is_file():
        return None
    try:
        payload = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    identity = payload.get("identity") if isinstance(payload.get("identity"), dict) else payload
    if not isinstance(identity, dict):
        return None
    uri = str(identity.get("uri") or "").strip()
    return uri or None


def quantity_class_iris(tbox: Graph) -> set[str]:
    """OM-2 Quantity subclasses present in the supporting T-Box, if any."""
    roots = [
        URIRef("http://www.ontology-of-units-of-measure.org/resource/om-2/Quantity")
    ]
    found: set[str] = set()
    for root in roots:
        if (root, RDF.type, OWL.Class) not in tbox and not any(
            tbox.triples((None, RDFS.subClassOf, root))
        ):
            continue
        found.add(str(root))
        queue = [root]
        while queue:
            current = queue.pop()
            for child, _, parent in tbox.triples((None, RDFS.subClassOf, None)):
                if parent != current or not isinstance(child, URIRef):
                    continue
                text = str(child)
                if text not in found:
                    found.add(text)
                    queue.append(child)
    found.discard("http://www.ontology-of-units-of-measure.org/resource/om-2/Quantity")
    return found


XSD_INTEGER = {
    str(XSD.integer),
    str(XSD.int),
    str(XSD.long),
    str(XSD.nonNegativeInteger),
    str(XSD.positiveInteger),
}
