"""Deterministic splice of extension facts onto the inherited main graph.

The strict no-prompt system text stays generic (same family as OntoSyn).
Seed reuse, unique Species/MOP, and formula nodes are enforced here so
scoring sees one spliced ChemicalSynthesis per entity.
"""

from __future__ import annotations

from graph_merge import copy_node, reattach_detached_species_facts
from graph_types import GraphDocument, Node, Relationship

_STEP_TYPES = {
    "ontosyn:SynthesisStep",
    "ontosyn:Add",
    "ontosyn:HeatChill",
    "ontosyn:Sonicate",
    "ontosyn:Filter",
    "ontosyn:Stir",
    "ontosyn:Wash",
    "ontosyn:Dry",
    "ontosyn:Evaporate",
    "ontosyn:Centrifuge",
    "ontosyn:Transfer",
    "ontosyn:Remove",
    "ontosyn:Collect",
}
_PRODUCT_TYPES = {
    "ontosyn:ChemicalOutput",
    "ontospecies:Species",
    "ontomops:MetalOrganicPolyhedron",
}
_STEP_PROPS = {"ontosyn:hasOrder"}
_STEP_LABEL_MARKERS = (
    "heatchill",
    "sonicate",
    "add ",
    "filter",
    "stir",
    "wash",
    "dry",
    "evaporate",
    "centrifuge",
    "transfer",
)

_FORMULA_KEYS = {
    "ontosyn:hasChemicalFormula": (
        "ontospecies:ChemicalFormula",
        "ontospecies:hasChemicalFormula",
        "ontospecies:hasChemicalFormulaValue",
        "formula",
    ),
    "ontosyn:hasMolecularFormula": (
        "ontospecies:MolecularFormula",
        "ontospecies:hasMolecularFormula",
        "ontospecies:hasMolecularFormulaValue",
        "molform",
    ),
    "ontosyn:hasCCDCNumber": (
        "ontospecies:CCDCNumber",
        "ontospecies:hasCCDCNumber",
        "ontospecies:hasCCDCNumberValue",
        "ccdc",
    ),
}


def _types(node: Node) -> set[str]:
    return {node.type, *(node.extra_types or [])}


def _is_mangled_duplicate(node_id: str, seeded: set[str]) -> bool:
    text = str(node_id)
    if text in seeded:
        return False
    for seed in seeded:
        if seed and seed in text and text != seed:
            return True
    return text.startswith("https_") or text.startswith("MetalOrganicPolyhedron-")


def _move_edges(
    relationships: list[Relationship],
    *,
    source_from: str,
    source_to: Node,
    existing: set[tuple[str, str, str]],
) -> list[Relationship]:
    out = []
    for rel in relationships:
        if rel.source.id != source_from and rel.target.id != source_from:
            out.append(rel)
            continue
        source = source_to if rel.source.id == source_from else rel.source
        target = source_to if rel.target.id == source_from else rel.target
        key = (source.id, rel.type, target.id)
        if key in existing:
            continue
        out.append(Relationship(source=source, target=target, type=rel.type))
        existing.add(key)
    return out


def _product_labels(value) -> list[str] | object:
    if isinstance(value, list):
        kept = [
            item
            for item in value
            if str(item).strip()
            and not str(item).strip().lower().startswith(_STEP_LABEL_MARKERS)
        ]
        return kept or value[:1]
    if isinstance(value, str) and value.strip().lower().startswith(_STEP_LABEL_MARKERS):
        return value
    return value


def peel_product_step_types(graph: GraphDocument | None) -> GraphDocument | None:
    """Stop ChemicalOutput / Species / MOP nodes from also being synthesis steps.

    Official merge drops every extension triple whose subject is a SynthesisStep.
    If the product is also typed as HeatChill/Sonicate, CCDC and CBU facts never
    reach convert/score.
    """
    if graph is None:
        return None
    mashed = {
        node.id
        for node in graph.nodes
        if (_types(node) & _PRODUCT_TYPES) and (_types(node) & _STEP_TYPES)
    }
    if not mashed:
        return graph
    nodes = []
    for node in graph.nodes:
        if node.id not in mashed:
            nodes.append(node)
            continue
        remaining = [
            item
            for item in (node.type, *(node.extra_types or []))
            if item not in _STEP_TYPES
        ]
        props = {
            key: _product_labels(value) if key == "rdfs:label" else value
            for key, value in (node.properties or {}).items()
            if key not in _STEP_PROPS
        }
        nodes.append(
            copy_node(
                node,
                type=remaining[0] if remaining else node.type,
                extra_types=remaining[1:],
                properties=props,
            )
        )
    relationships = [
        rel
        for rel in graph.relationships
        if not (rel.type == "ontosyn:hasSynthesisStep" and rel.target.id in mashed)
    ]
    return GraphDocument(nodes=nodes, relationships=relationships, source=graph.source)


def collapse_duplicate_species_outputs(graph: GraphDocument | None) -> GraphDocument | None:
    """Keep one Species-typed ChemicalOutput per synthesis (the inherited id)."""
    if graph is None:
        return None
    by_id = {node.id: node for node in graph.nodes}
    outputs: dict[str, list[Node]] = {}
    for rel in graph.relationships:
        if rel.type != "ontosyn:hasChemicalOutput":
            continue
        target = by_id.get(rel.target.id)
        if target is None or "ontospecies:Species" not in _types(target):
            continue
        outputs.setdefault(rel.source.id, []).append(target)
    drop: set[str] = set()
    nodes = {node.id: node for node in graph.nodes}
    relationships = list(graph.relationships)
    existing = {(rel.source.id, rel.type, rel.target.id) for rel in relationships}
    for _synth, species_nodes in outputs.items():
        if len(species_nodes) < 2:
            continue
        seeded = [node for node in species_nodes if not _is_mangled_duplicate(node.id, set())]
        keep = seeded[0] if seeded else species_nodes[0]
        keep_props = dict(keep.properties or {})
        for extra in species_nodes:
            if extra.id == keep.id:
                continue
            for key, value in (extra.properties or {}).items():
                if key.startswith("ontospecies:") and not keep_props.get(key):
                    keep_props[key] = value
            extras = list(keep.extra_types or [])
            for item in _types(extra):
                if item not in (keep.type, *extras):
                    extras.append(item)
            nodes[keep.id] = copy_node(keep, properties=keep_props, extra_types=extras)
            keep = nodes[keep.id]
            relationships = _move_edges(
                relationships,
                source_from=extra.id,
                source_to=keep,
                existing=existing,
            )
            drop.add(extra.id)
    if not drop:
        return graph
    kept_nodes = [nodes.get(node.id, node) for node in graph.nodes if node.id not in drop]
    kept_rels = [
        rel
        for rel in relationships
        if rel.source.id not in drop and rel.target.id not in drop
    ]
    return GraphDocument(nodes=kept_nodes, relationships=kept_rels, source=graph.source)


def _merge_mop_onto(
    keep: Node,
    extra: Node,
    *,
    nodes: dict[str, Node],
    relationships: list[Relationship],
    existing: set[tuple[str, str, str]],
    drop: set[str],
) -> tuple[Node, list[Relationship]]:
    keep_props = dict(keep.properties or {})
    extras = list(keep.extra_types or [])
    def _better(key: str, incoming) -> bool:
        current = keep_props.get(key)
        if incoming in (None, ""):
            return False
        if current in (None, ""):
            return True
        if key == "ontomops:hasCCDCNumber":
            inc_num = str(incoming).isdigit()
            cur_num = str(current).isdigit()
            return inc_num and not cur_num
        return False

    for key, value in (extra.properties or {}).items():
        if key.startswith("ontomops:") and _better(key, value):
            keep_props[key] = value
        elif key == "rdfs:label" and not keep_props.get(key):
            keep_props[key] = value
    for item in _types(extra):
        if item not in (keep.type, *extras):
            extras.append(item)
    nodes[keep.id] = copy_node(keep, properties=keep_props, extra_types=extras)
    keep = nodes[keep.id]
    relationships = _move_edges(
        relationships,
        source_from=extra.id,
        source_to=keep,
        existing=existing,
    )
    drop.add(extra.id)
    return keep, relationships


def collapse_duplicate_mops(graph: GraphDocument | None) -> GraphDocument | None:
    """Keep the seeded MOP per ChemicalOutput; merge facts from minted copies."""
    if graph is None:
        return None
    by_id = {node.id: node for node in graph.nodes}
    groups: dict[str, list[Node]] = {}
    for rel in graph.relationships:
        if rel.type != "ontosyn:isRepresentedBy":
            continue
        mop = by_id.get(rel.target.id)
        if mop is None or "ontomops:MetalOrganicPolyhedron" not in _types(mop):
            continue
        groups.setdefault(rel.source.id, []).append(mop)
    all_mops = [
        node for node in graph.nodes if "ontomops:MetalOrganicPolyhedron" in _types(node)
    ]
    linked = {node.id for rows in groups.values() for node in rows}
    unlinked = [node for node in all_mops if node.id not in linked]
    if unlinked and len(groups) == 1:
        next(iter(groups.values())).extend(unlinked)
    elif unlinked:
        for extra in unlinked:
            for rows in groups.values():
                if any(seed.id and seed.id in extra.id for seed in rows):
                    rows.append(extra)
                    break
    drop: set[str] = set()
    nodes = {node.id: node for node in graph.nodes}
    relationships = list(graph.relationships)
    existing = {(rel.source.id, rel.type, rel.target.id) for rel in relationships}
    for _output, mops in groups.items():
        unique = []
        seen_ids: set[str] = set()
        for node in mops:
            if node.id in seen_ids:
                continue
            seen_ids.add(node.id)
            unique.append(node)
        if len(unique) < 2:
            continue
        seeded = [
            node
            for node in unique
            if not _is_mangled_duplicate(node.id, set())
            and not str(node.id).startswith("MetalOrganicPolyhedron-")
        ]
        keep = seeded[0] if seeded else unique[0]
        for extra in unique:
            if extra.id == keep.id:
                continue
            keep, relationships = _merge_mop_onto(
                keep,
                extra,
                nodes=nodes,
                relationships=relationships,
                existing=existing,
                drop=drop,
            )
    if not drop:
        return graph
    kept_nodes = [nodes.get(node.id, node) for node in graph.nodes if node.id not in drop]
    kept_rels = [
        rel
        for rel in relationships
        if rel.source.id not in drop and rel.target.id not in drop
    ]
    return GraphDocument(nodes=kept_nodes, relationships=kept_rels, source=graph.source)


def lift_species_literals_to_nodes(graph: GraphDocument | None) -> GraphDocument | None:
    """Rewrite ontosyn formula/CCDC literals into occurrence-owned value nodes."""
    if graph is None:
        return None
    nodes = list(graph.nodes)
    relationships = list(graph.relationships)
    existing = {(rel.source.id, rel.type, rel.target.id) for rel in relationships}
    by_id = {node.id: node for node in nodes}
    changed = False
    for node in list(nodes):
        if "ontospecies:Species" not in _types(node):
            continue
        props = dict(node.properties or {})
        for literal_key, (cls, edge, value_prop, stem) in _FORMULA_KEYS.items():
            raw = props.get(literal_key)
            if raw in (None, ""):
                continue
            already = any(
                rel.source.id == node.id and rel.type == edge for rel in relationships
            )
            if already:
                props.pop(literal_key, None)
                changed = True
                continue
            local = str(node.id).rsplit("/", 1)[-1]
            if local.startswith("https_"):
                local = local.rsplit("_", 1)[-1] or local
            child_id = f"{stem}-{local}"
            if child_id not in by_id:
                child = Node(
                    id=child_id,
                    type=cls,
                    properties={"rdfs:label": str(raw), value_prop: str(raw)},
                )
                nodes.append(child)
                by_id[child_id] = child
            key = (node.id, edge, child_id)
            if key not in existing:
                relationships.append(
                    Relationship(source=node, target=by_id[child_id], type=edge)
                )
                existing.add(key)
            props.pop(literal_key, None)
            changed = True
        if changed:
            by_id[node.id] = copy_node(node, properties=props)
    if not changed:
        return graph
    nodes = [by_id.get(node.id, node) for node in nodes]
    return GraphDocument(nodes=nodes, relationships=relationships, source=graph.source)


def splice_extension_layer(graph: GraphDocument | None, domain: str) -> GraphDocument | None:
    """Apply the post-LLM splice for one extension domain."""
    if graph is None:
        return None
    graph = peel_product_step_types(graph)
    if domain == "ontospecies":
        graph = reattach_detached_species_facts(graph)
        graph = collapse_duplicate_species_outputs(graph)
        graph = lift_species_literals_to_nodes(graph)
    elif domain == "ontomops":
        graph = collapse_duplicate_mops(graph)
    return peel_product_step_types(graph)
