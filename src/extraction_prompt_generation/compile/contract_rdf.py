"""rdflib helpers for compiling generation contracts from a T-Box.

Local names, RDF lists, restriction nodes, domain members, and subclass
closure. No domain-specific IRIs. See compile/README.md.
"""

from __future__ import annotations

from typing import Any

from rdflib import Graph, OWL, RDF, RDFS, URIRef  # type: ignore[import-not-found]


def _local_name(iri: Any) -> str:
    text = str(iri or "").strip()
    if not text:
        return ""
    return text.rstrip("/#").rsplit("/", 1)[-1].rsplit("#", 1)[-1]


def _namespace_iri(iri: str) -> str:
    text = str(iri or "").strip()
    if "#" in text:
        return text.rsplit("#", 1)[0] + "#"
    if "/" in text:
        return text.rsplit("/", 1)[0] + "/"
    return ""


def _iter_rdf_list(graph: Graph, node: Any) -> list[Any]:
    out: list[Any] = []
    current = node
    while current and current != RDF.nil:
        first = graph.value(current, RDF.first)
        if first is not None:
            out.append(first)
        current = graph.value(current, RDF.rest)
    return out


def _domain_members(graph: Graph, domain: Any) -> list[str]:
    if isinstance(domain, URIRef):
        return [str(domain)]
    members: list[str] = []
    for union_list in graph.objects(domain, OWL.unionOf):
        members.extend(str(x) for x in _iter_rdf_list(graph, union_list) if isinstance(x, URIRef))
    return members


def _subclass_closure(graph: Graph) -> dict[str, set[str]]:
    classes = {
        str(c)
        for class_type in (OWL.Class, RDFS.Class)
        for c in graph.subjects(RDF.type, class_type)
        if isinstance(c, URIRef)
    }
    for child, parent in graph.subject_objects(RDFS.subClassOf):
        if isinstance(child, URIRef):
            classes.add(str(child))
        if isinstance(parent, URIRef):
            classes.add(str(parent))
    for predicate in (RDFS.domain, RDFS.range):
        classes.update(
            str(value)
            for value in graph.objects(None, predicate)
            if isinstance(value, URIRef)
        )
    closure: dict[str, set[str]] = {c: {c} for c in classes}
    changed = True
    while changed:
        changed = False
        for child in list(closure):
            node = URIRef(child)
            for parent in graph.objects(node, RDFS.subClassOf):
                if not isinstance(parent, URIRef):
                    continue
                before = len(closure[child])
                closure[child].add(str(parent))
                closure[child].update(closure.get(str(parent), {str(parent)}))
                changed = changed or len(closure[child]) != before
    return closure


def _choose_union_superclass(members: list[str], closure: dict[str, set[str]]) -> str:
    for candidate in members:
        if all(candidate in closure.get(member, {member}) for member in members):
            return candidate
    return ""


def _restriction_nodes(graph: Graph, class_node: URIRef) -> list[Any]:
    nodes: list[Any] = []
    for predicate in (RDFS.subClassOf, OWL.equivalentClass):
        for candidate in graph.objects(class_node, predicate):
            if (candidate, RDF.type, OWL.Restriction) in graph:
                nodes.append(candidate)
            for member_list in graph.objects(candidate, OWL.intersectionOf):
                nodes.extend(
                    member
                    for member in _iter_rdf_list(graph, member_list)
                    if (member, RDF.type, OWL.Restriction) in graph
                )
    return nodes


def _literal_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


_TOP_ROLE_PREDICATE_LOCALS = {
    "topEntityClass",
    "topEntityRole",
    "publishTopEntityClass",
}


def _machine_top_role(graph: Graph, *, evidence_file: str) -> dict[str, Any]:
    """Read an explicit top-role declaration without inferring one from schema shape."""
    declarations: set[str] = set()
    evidence: list[dict[str, str]] = []
    for subject, predicate, obj in graph:
        if _local_name(predicate) not in _TOP_ROLE_PREDICATE_LOCALS:
            continue
        candidate = ""
        if isinstance(obj, URIRef):
            candidate = str(obj)
        elif isinstance(subject, URIRef) and str(obj).strip().lower() in {"true", "1"}:
            candidate = str(subject)
        if not candidate:
            continue
        declarations.add(candidate)
        evidence.append(
            {
                "ttl_file": evidence_file,
                "subject_iri": str(subject),
                "predicate_iri": str(predicate),
                "object": str(obj),
            }
        )
    if len(declarations) == 1:
        class_iri = next(iter(declarations))
        return {
            "status": "known",
            "class_iri": class_iri,
            "class_local": _local_name(class_iri),
            "source": "tbox",
            "evidence": evidence,
        }
    return {
        "status": "ambiguous" if declarations else "unknown",
        "class_iri": "",
        "class_local": "",
        "source": "tbox",
        "evidence": evidence,
    }
