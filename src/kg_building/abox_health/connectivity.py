"""Graph-theoretic orphan and connectivity checks."""

from __future__ import annotations

from collections import defaultdict, deque
from typing import Iterable

from rdflib import Graph, Literal, URIRef

from .graph_index import is_vocabulary_iri, typed_instance_nodes
from .report import Finding


def _neighbors(graph: Graph, instances: set[URIRef]) -> dict[URIRef, set[URIRef]]:
    outgoing: dict[URIRef, set[URIRef]] = defaultdict(set)
    for subject, _, obj in graph:
        if (
            isinstance(subject, URIRef)
            and subject in instances
            and isinstance(obj, URIRef)
            and obj in instances
        ):
            outgoing[subject].add(obj)
    return outgoing


def _bfs(start: Iterable[URIRef], edges: dict[URIRef, set[URIRef]]) -> set[URIRef]:
    reachable: set[URIRef] = set()
    queue: deque[URIRef] = deque()
    for node in start:
        if node not in reachable:
            reachable.add(node)
            queue.append(node)
    while queue:
        node = queue.popleft()
        for nxt in edges.get(node, ()):
            if nxt not in reachable:
                reachable.add(nxt)
                queue.append(nxt)
    return reachable


def _undirected_edges(
    directed: dict[URIRef, set[URIRef]],
) -> dict[URIRef, set[URIRef]]:
    undirected: dict[URIRef, set[URIRef]] = defaultdict(set)
    for source, targets in directed.items():
        for target in targets:
            undirected[source].add(target)
            undirected[target].add(source)
    return undirected


def check_connectivity(
    abox: Graph,
    *,
    roots: list[str],
    vocab: set[str],
) -> list[Finding]:
    """Find typed nodes that are isolated, reverse-attached, or unreachable."""
    findings: list[Finding] = []
    instances = typed_instance_nodes(abox, vocab)
    if not roots:
        findings.append(
            Finding(
                code="BOUND_ROOT_MISSING",
                check="connectivity",
                message="No bound root could be inferred; pass --root or an identity sidecar.",
            )
        )
        return findings

    root_nodes = [URIRef(iri) for iri in roots if str(iri).strip()]
    missing_roots = [str(node) for node in root_nodes if node not in instances]
    if missing_roots:
        findings.append(
            Finding(
                code="BOUND_ROOT_NOT_MATERIALIZED",
                check="connectivity",
                message="Bound root is absent from the typed instance graph.",
                iris=tuple(missing_roots),
            )
        )

    directed = _neighbors(abox, instances)
    undirected = _undirected_edges(directed)
    present_roots = [node for node in root_nodes if node in instances]
    directed_reachable = _bfs(present_roots, directed)
    weak_reachable = _bfs(present_roots, undirected)

    isolated = [
        node
        for node in instances
        if node not in present_roots
        and not directed.get(node)
        and not undirected.get(node)
    ]
    weakly_disconnected = [
        node
        for node in instances
        if node not in weak_reachable and node not in isolated
    ]
    incoming_only = [
        node
        for node in instances
        if node in weak_reachable and node not in directed_reachable
    ]
    directed_unreachable = [
        node
        for node in instances
        if node not in directed_reachable
    ]

    if directed_unreachable:
        findings.append(
            Finding(
                code="DIRECTED_UNREACHABLE",
                check="connectivity",
                message=(
                    "Typed instance nodes are not reachable from the bound root "
                    "by outgoing edges (Pipeline export would prune these)."
                ),
                iris=tuple(str(node) for node in sorted(directed_unreachable, key=str)),
            )
        )
    if incoming_only:
        findings.append(
            Finding(
                code="INCOMING_ONLY",
                check="connectivity",
                message=(
                    "Typed instance nodes are weakly attached but not reachable "
                    "from the bound root; they are linked only by incoming edges."
                ),
                iris=tuple(str(node) for node in sorted(incoming_only, key=str)),
            )
        )
    if weakly_disconnected:
        findings.append(
            Finding(
                code="WEAKLY_DISCONNECTED",
                check="connectivity",
                message="Typed instance nodes sit in a separate undirected component from the bound root.",
                iris=tuple(str(node) for node in sorted(weakly_disconnected, key=str)),
            )
        )
    if isolated:
        findings.append(
            Finding(
                code="ISOLATED_TYPED_NODE",
                check="connectivity",
                message="Typed instance nodes have no instance edges at all.",
                iris=tuple(str(node) for node in sorted(isolated, key=str)),
            )
        )

    dangling: list[str] = []
    seen: set[str] = set()
    for _, _, obj in abox:
        if not isinstance(obj, URIRef):
            continue
        iri = str(obj)
        if iri in seen or is_vocabulary_iri(iri, vocab):
            continue
        seen.add(iri)
        if obj in instances:
            continue
        if any(isinstance(value, (URIRef, Literal)) for value in abox.objects(obj, None)):
            continue
        dangling.append(iri)
    if dangling:
        findings.append(
            Finding(
                code="DANGLING_OBJECT_IRI",
                check="connectivity",
                message="Object IRIs are neither typed instances nor T-Box vocabulary.",
                iris=tuple(sorted(dangling)),
            )
        )
    return findings
