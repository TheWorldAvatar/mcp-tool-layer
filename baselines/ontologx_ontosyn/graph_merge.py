"""Merge per-entity OntoLogX graphs into one paper graph."""

from __future__ import annotations

import json
import re
from typing import Any

from graph_types import GraphDocument, Node, Relationship


def copy_node(node: Node, *, node_id: str | None = None, **overrides) -> Node:
    payload = {
        "id": node_id if node_id is not None else node.id,
        "type": node.type,
        "properties": dict(node.properties or {}),
        "extra_types": list(node.extra_types or []),
    }
    payload.update(overrides)
    return Node(**payload)


def merge_node_types(kept: Node, incoming: Node) -> tuple[str, list[str]]:
    ordered: list[str] = []
    for item in (kept.type, *(kept.extra_types or []), incoming.type, *(incoming.extra_types or [])):
        if item and item not in ordered:
            ordered.append(item)
    return (ordered[0] if ordered else kept.type), ordered[1:]


def prefix_graph(graph: GraphDocument, prefix: str) -> GraphDocument:
    id_map = {node.id: f"{prefix}_{node.id}" for node in graph.nodes}
    nodes = [copy_node(node, node_id=id_map[node.id]) for node in graph.nodes]
    by_id = {node.id: node for node in nodes}
    relationships = []
    for rel in graph.relationships:
        source_id = id_map.get(rel.source.id)
        target_id = id_map.get(rel.target.id)
        if source_id is None or target_id is None:
            continue
        relationships.append(
            Relationship(source=by_id[source_id], target=by_id[target_id], type=rel.type)
        )
    return GraphDocument(nodes=nodes, relationships=relationships, source=graph.source)


def _document_label(node: Node) -> str:
    props = node.properties or {}
    return str(props.get("rdfs:label") or props.get("label") or "").strip()


def _dedupe_documents(graph: GraphDocument) -> GraphDocument:
    documents = [node for node in graph.nodes if node.type in {"bibo:Document", "Document"}]
    if len(documents) <= 1:
        return graph
    keep_by_label: dict[str, str] = {}
    drop: set[str] = set()
    replacement: dict[str, str] = {}
    for node in documents:
        label = _document_label(node) or node.id
        kept = keep_by_label.get(label)
        if kept is None:
            keep_by_label[label] = node.id
        else:
            drop.add(node.id)
            replacement[node.id] = kept
    if not drop:
        return graph
    by_id = {node.id: node for node in graph.nodes if node.id not in drop}
    relationships = []
    for rel in graph.relationships:
        source_id = replacement.get(rel.source.id, rel.source.id)
        target_id = replacement.get(rel.target.id, rel.target.id)
        if source_id in drop or target_id in drop:
            continue
        source = by_id.get(source_id)
        target = by_id.get(target_id)
        if source is None or target is None:
            continue
        relationships.append(Relationship(source=source, target=target, type=rel.type))
    return GraphDocument(
        nodes=list(by_id.values()),
        relationships=relationships,
        source=graph.source,
    )


STEP_LOCAL = {
    "add",
    "stir",
    "heatchill",
    "evaporate",
    "sonicate",
    "crystallize",
    "transfer",
    "separate",
    "filter",
    "dry",
    "synthesisstep",
}

# Same-paper reuse (pipeline document + global scopes).
DOCUMENT_SCOPE_REUSE_LOCAL = frozenset(
    {
        "document",
        "bibodocument",
        "equipment",
        "heatchilldevice",
        "labequipment",
        "documentcontext",
        "device",
        "hnmrdevice",
        "elementalanalysisdevice",
        "infraredspectroscopydevice",
    }
)
# Cross-document reuse (pipeline global / global_value). Document stays out.
CROSS_DOCUMENT_REUSE_LOCAL = frozenset(
    {
        "supplier",
        "vesselenvironment",
        "vesseltype",
        "separationtype",
        "material",
        "species",
        "solvent",
    }
)
CROSS_ENTITY_REUSE_LOCAL = DOCUMENT_SCOPE_REUSE_LOCAL | CROSS_DOCUMENT_REUSE_LOCAL


def _norm_type(node: Node) -> str:
    return str(node.type or "").split(":")[-1].strip().casefold()


def _label(node: Node) -> str:
    props = node.properties or {}
    return str(props.get("rdfs:label") or props.get("label") or "").strip()


def _order(node: Node) -> str:
    props = node.properties or {}
    for key in ("ontosyn:hasOrder", "hasOrder"):
        value = props.get(key)
        if value not in (None, ""):
            return str(value).strip()
    return ""


def _ccdc(node: Node) -> str:
    props = node.properties or {}
    for key in ("ontomops:hasCCDCNumber", "hasCCDCNumber"):
        value = props.get(key)
        if value not in (None, ""):
            return str(value).strip()
    return ""


def is_cross_entity_reusable(node: Node) -> bool:
    local = _norm_type(node)
    if local == "metalorganicpolyhedron":
        return bool(_ccdc(node))
    return local in CROSS_ENTITY_REUSE_LOCAL


def is_cross_document_reusable(node: Node) -> bool:
    local = _norm_type(node)
    if local == "metalorganicpolyhedron":
        return bool(_ccdc(node))
    return local in CROSS_DOCUMENT_REUSE_LOCAL


def identity_key(node: Node, *, reuse: str = "layer") -> tuple:
    local = _norm_type(node)
    label = _label(node).casefold()
    if reuse == "paper":
        if not is_cross_entity_reusable(node):
            return ("occ", local, node.id)
        if local in {"document", "bibodocument"}:
            return ("doc", label)
        if local == "metalorganicpolyhedron":
            return ("mop", _ccdc(node).casefold())
        return ("paper", local, label or node.id)
    if local == "chemicalsynthesis":
        return ("cs",)
    if local in {"document", "bibodocument"}:
        return ("doc", label)
    if local in STEP_LOCAL:
        return ("step", local, _order(node) or label)
    if local == "chemicalinput":
        # ChemicalInput is occurrence-local. Only an exact id may carry the
        # same occurrence across layers; equal labels can denote separate
        # additions with different amounts.
        return ("ci-occ", node.id)
    if local == "chemicaloutput":
        return ("co",)
    if local == "supplier":
        return ("supplier", label)
    if local == "vessel":
        return ("vessel", label)
    return ("other", local, label or node.id)


def reusable_subgraph(
    graph: GraphDocument | None,
    *,
    scope: str = "paper",
) -> GraphDocument | None:
    """Filter to reusable classes.

    ``scope="paper"``: document + global (same DOI).
    ``scope="global"``: Supplier / atmosphere / CCDC-MOP / species only.
    """
    if graph is None:
        return None
    predicate = is_cross_document_reusable if scope == "global" else is_cross_entity_reusable
    nodes = [node for node in graph.nodes if predicate(node)]
    keep = {node.id for node in nodes}
    relationships = [
        rel
        for rel in graph.relationships
        if rel.source.id in keep and rel.target.id in keep
    ]
    if not nodes:
        return None
    return GraphDocument(nodes=nodes, relationships=relationships, source=graph.source)


def seed_reusable(
    paper_graph: GraphDocument | None,
    central_graph: GraphDocument | None,
) -> GraphDocument | None:
    """Same-paper reusable nodes plus earlier-paper global individuals."""
    paper = reusable_subgraph(paper_graph, scope="paper")
    central = reusable_subgraph(central_graph, scope="global")
    if paper is None:
        return central
    if central is None:
        return paper
    return attach_subgraph(central, paper, reuse="paper")


def canonicalize_reused(
    graph: GraphDocument | None,
    inventory: GraphDocument | None,
) -> GraphDocument | None:
    """Remap reusable nodes to inventory ids without copying unused inventory."""
    if graph is None or inventory is None:
        return graph
    by_id = {node.id: node for node in inventory.nodes}
    key_to_id = {
        identity_key(node, reuse="paper"): node.id
        for node in inventory.nodes
        if is_cross_entity_reusable(node)
    }
    remap: dict[str, str] = {}
    nodes: dict[str, Node] = {}
    for node in graph.nodes:
        matched = _match_existing(node, by_id, key_to_id, "paper")
        kept_id = matched or node.id
        remap[node.id] = kept_id
        props = dict((by_id.get(kept_id) or node).properties or {})
        props.update({key: value for key, value in (node.properties or {}).items() if value not in (None, "")})
        existing = nodes.get(kept_id)
        if existing is not None:
            merged_props = dict(existing.properties or {})
            merged_props.update(props)
            props = merged_props
        extra = list((by_id.get(kept_id) or node).extra_types or [])
        extra.extend(item for item in (node.extra_types or []) if item not in extra)
        if node.type and node.type != (by_id.get(kept_id) or node).type and node.type not in extra:
            extra.append(node.type)
        nodes[kept_id] = Node(id=kept_id, type=node.type, properties=props, extra_types=extra)

    relationships: list[Relationship] = []
    seen: set[tuple[str, str, str]] = set()
    for rel in graph.relationships:
        source_id = remap.get(rel.source.id, rel.source.id)
        target_id = remap.get(rel.target.id, rel.target.id)
        key = (source_id, rel.type, target_id)
        if key in seen or source_id not in nodes or target_id not in nodes:
            continue
        seen.add(key)
        relationships.append(
            Relationship(source=nodes[source_id], target=nodes[target_id], type=rel.type)
        )
    return GraphDocument(
        nodes=list(nodes.values()),
        relationships=relationships,
        source=graph.source,
    )


def reused_node_ids(seed: GraphDocument | None, result: GraphDocument | None) -> list[str]:
    if seed is None or result is None:
        return []
    seed_ids = {node.id for node in seed.nodes}
    return sorted({node.id for node in result.nodes if node.id in seed_ids})


def graph_inventory(graph: GraphDocument, *, heading: str = "EXISTING_GRAPH_INVENTORY") -> str:
    """Serialize the prior graph so later layers retain ids and node facts."""
    if graph is None:
        return "(empty)"
    lines = [heading]
    for node in graph.nodes:
        properties = dict(node.properties or {})
        extra = f" extra_types={node.extra_types}" if node.extra_types else ""
        suffix = (
            " properties="
            + json.dumps(
                properties,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            )
            if properties
            else ""
        )
        lines.append(f"- id={node.id} type={node.type}{extra}{suffix}")
    for rel in graph.relationships:
        lines.append(f"- {rel.source.id} -[{rel.type}]-> {rel.target.id}")
    return "\n".join(lines)


def layered_graph_inventory(
    existing: GraphDocument | None,
    seed: GraphDocument | None,
) -> str:
    """Show the full current graph and still-unused reusable seed candidates."""
    if existing is None:
        return reusable_inventory(seed)

    parts = [graph_inventory(existing)]
    reusable = reusable_subgraph(seed, scope="paper")
    used_ids = {node.id for node in existing.nodes}
    remaining_nodes = [
        node for node in (reusable.nodes if reusable is not None else []) if node.id not in used_ids
    ]
    remaining_ids = {node.id for node in remaining_nodes}
    visible_ids = used_ids | remaining_ids
    remaining_relationships = [
        rel
        for rel in (reusable.relationships if reusable is not None else [])
        if (rel.source.id in remaining_ids or rel.target.id in remaining_ids)
        and rel.source.id in visible_ids
        and rel.target.id in visible_ids
    ]
    remaining = (
        GraphDocument(
            nodes=remaining_nodes,
            relationships=remaining_relationships,
            source=seed.source if seed is not None else None,
        )
        if remaining_nodes
        else None
    )
    parts.append(
        graph_inventory(
            remaining,
            heading=(
                "UNUSED_REUSABLE_ENTITIES "
                "(still available; reuse these ids instead of minting duplicates)"
            ),
        )
        if remaining is not None
        else "UNUSED_REUSABLE_ENTITIES\n(none)"
    )
    return "\n\n".join(parts)


def reusable_inventory(
    graph: GraphDocument | None,
    *,
    scope: str = "paper",
    heading: str = "REUSABLE_ENTITIES (reuse these ids; do not mint a second copy)",
) -> str:
    sub = reusable_subgraph(graph, scope=scope)
    if sub is None:
        return f"{heading}\n(none yet)"
    return graph_inventory(sub, heading=heading)


def scoped_reuse_inventory(
    paper_graph: GraphDocument | None,
    central_graph: GraphDocument | None,
) -> str:
    """Prompt inventory with paper and cross-document scopes kept explicit."""
    return "\n\n".join(
        [
            reusable_inventory(
                paper_graph,
                scope="paper",
                heading="SAME_PAPER_REUSABLE_ENTITIES",
            ),
            reusable_inventory(
                central_graph,
                scope="global",
                heading="CROSS_DOCUMENT_REUSABLE_ENTITIES",
            ),
        ]
    )


def _rel_type(rel: Any) -> str:
    value = getattr(rel, "type", rel)
    return value.value if hasattr(value, "value") else str(value)


def remove_prior_relationships(
    graph: GraphDocument | None,
    removals: list[Any] | None,
) -> GraphDocument | None:
    """Apply exact, model-requested edge removals to an existing graph."""
    if graph is None or not removals:
        return graph
    keys = {
        (
            str(getattr(rel, "source_id", "") or ""),
            _rel_type(rel),
            str(getattr(rel, "target_id", "") or ""),
        )
        for rel in removals
    }
    relationships = [
        rel
        for rel in graph.relationships
        if (rel.source.id, rel.type, rel.target.id) not in keys
    ]
    return GraphDocument(
        nodes=list(graph.nodes),
        relationships=relationships,
        source=graph.source,
    )


def complete_delta(parsed: Any, existing: GraphDocument | None, event: str, context: dict) -> GraphDocument:
    """Keep relationships that point at prior-layer ids even if those nodes are omitted."""
    delta = parsed.graph(event, context)
    if existing is None:
        return delta
    by_id = {node.id: node for node in list(existing.nodes) + list(delta.nodes)}
    nodes = {node.id: node for node in delta.nodes}
    relationships = list(delta.relationships)
    seen = {(rel.source.id, rel.type, rel.target.id) for rel in relationships}
    for rel in parsed.relationships or []:
        source_id = getattr(rel, "source_id", None)
        target_id = getattr(rel, "target_id", None)
        rel_type = _rel_type(rel)
        if not source_id or not target_id:
            continue
        if (source_id, rel_type, target_id) in seen:
            continue
        source = by_id.get(source_id)
        target = by_id.get(target_id)
        if source is None or target is None:
            continue
        nodes.setdefault(source.id, source)
        nodes.setdefault(target.id, target)
        relationships.append(Relationship(source=source, target=target, type=rel_type))
        seen.add((source_id, rel_type, target_id))
    return GraphDocument(
        nodes=list(nodes.values()),
        relationships=relationships,
        source=delta.source,
    )


def _match_existing(node: Node, by_id: dict[str, Node], key_to_id: dict[tuple, str], reuse: str) -> str | None:
    if reuse == "paper":
        if not is_cross_entity_reusable(node):
            return None
        existing = by_id.get(node.id)
        if (
            existing is not None
            and is_cross_entity_reusable(existing)
            and _norm_type(existing) == _norm_type(node)
        ):
            return node.id
        return key_to_id.get(identity_key(node, reuse=reuse))
    if node.id in by_id:
        return node.id
    return key_to_id.get(identity_key(node, reuse=reuse))


def attach_subgraph(
    base: GraphDocument,
    delta: GraphDocument,
    *,
    reuse: str = "layer",
) -> GraphDocument:
    """Union ``delta`` onto ``base``, reusing prior nodes by id or identity.

    ``reuse="layer"``: same ChemicalSynthesis being enriched (iter2→iter4).
    ``reuse="paper"``: next synthesis on the same paper; only document/global
    classes match, and colliding occurrence-local ids are remapped.
    """
    if base is None:
        return delta
    if delta is None:
        return base
    by_id = {node.id: node for node in base.nodes}
    key_to_id: dict[tuple, str] = {}
    for node in base.nodes:
        key_to_id.setdefault(identity_key(node, reuse=reuse), node.id)

    remap: dict[str, str] = {}
    merged_nodes = {node.id: copy_node(node) for node in base.nodes}

    for node in delta.nodes:
        matched = _match_existing(node, by_id, key_to_id, reuse)
        if matched:
            remap[node.id] = matched
            kept = merged_nodes[matched]
            props = dict(kept.properties or {})
            for key, value in (node.properties or {}).items():
                if value not in (None, ""):
                    props[key] = value
            primary, extra = merge_node_types(kept, node)
            merged_nodes[matched] = Node(
                id=matched,
                type=primary,
                properties=props,
                extra_types=extra,
            )
            continue
        new_id = node.id
        suffix = 2
        while new_id in merged_nodes:
            new_id = f"{node.id}_{suffix}"
            suffix += 1
        remap[node.id] = new_id
        merged_nodes[new_id] = copy_node(node, node_id=new_id)
        key_to_id.setdefault(identity_key(merged_nodes[new_id], reuse=reuse), new_id)

    seen_rels: set[tuple[str, str, str]] = set()
    relationships: list[Relationship] = []

    def _add_rel(rel: Relationship, source_id: str, target_id: str) -> None:
        key = (source_id, rel.type, target_id)
        if key in seen_rels:
            return
        source = merged_nodes.get(source_id)
        target = merged_nodes.get(target_id)
        if source is None or target is None:
            return
        seen_rels.add(key)
        relationships.append(Relationship(source=source, target=target, type=rel.type))

    for rel in base.relationships:
        _add_rel(rel, rel.source.id, rel.target.id)
    for rel in delta.relationships:
        _add_rel(
            rel,
            remap.get(rel.source.id, rel.source.id),
            remap.get(rel.target.id, rel.target.id),
        )

    return GraphDocument(
        nodes=list(merged_nodes.values()),
        relationships=relationships,
        source=delta.source or base.source,
    )


_SPECIES_CODE_RE = re.compile(r"\(([A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)*)\)")
_SPECIES_LEAD_RE = re.compile(r"^([IVXLCDM]+)\b")
_SPECIES_PRODUCT_RE = re.compile(r"\b([A-Za-z]{1,8}-[A-Za-z0-9]+|[A-Za-z]+-\d+)\b")


def _alnum_key(text: str) -> str:
    return "".join(ch for ch in str(text or "").lower() if ch.isalnum())


def _is_product_code(text: str) -> bool:
    """Catalogue-style names such as ZrT-2 / CIAC-113 / TMA-VMOT-3, not (mu2-OH)."""
    return bool(re.fullmatch(r"[A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)*-\d+", text or ""))


def _species_name_keys(node: Node) -> set[str]:
    """Identity keys only: full label, paren-stripped formula, product codes."""
    props = node.properties or {}
    keys: set[str] = set()
    for raw in (props.get("rdfs:label"), props.get("ontospecies:hasProductName")):
        text = str(raw or "").strip()
        if not text:
            continue
        compact = _alnum_key(text)
        if compact:
            keys.add(compact)
        core = _alnum_key(re.sub(r"\s*\([^)]*\)\s*$", "", text))
        if core:
            keys.add(core)
        for item in (*_SPECIES_CODE_RE.findall(text), *_SPECIES_PRODUCT_RE.findall(text)):
            if _is_product_code(item):
                compact_item = _alnum_key(item)
                if compact_item:
                    keys.add(compact_item)
        lead = _SPECIES_LEAD_RE.match(text)
        if lead:
            keys.add(_alnum_key(lead.group(1)))
    return {item for item in keys if item}


def _node_types(node: Node) -> set[str]:
    return {node.type, *(node.extra_types or [])}


def reattach_detached_species_facts(graph: GraphDocument | None) -> GraphDocument | None:
    """Copy OntoSpecies facts from a minted Species onto the inherited ChemicalOutput.

    Conversion only reads Species that sit on hasChemicalOutput. After the
    ChemicalOutput is pre-typed as Species, the LLM often emits a second
    species-* node with EA/IR/CCDC. Move those edges onto the output.
    """
    if graph is None:
        return None
    by_id = {node.id: node for node in graph.nodes}
    output_ids = {
        rel.target.id for rel in graph.relationships if rel.type == "ontosyn:hasChemicalOutput"
    }
    outputs = [by_id[item] for item in output_ids if item in by_id]
    detached = [
        node
        for node in graph.nodes
        if "ontospecies:Species" in _node_types(node) and node.id not in output_ids
    ]
    if not detached or not outputs:
        return graph

    output_key_counts: dict[str, int] = {}
    for node in outputs:
        for key in _species_name_keys(node):
            output_key_counts[key] = output_key_counts.get(key, 0) + 1
    common_keys = {key for key, count in output_key_counts.items() if count >= 2}

    def _match_output(species: Node) -> Node | None:
        names = _species_name_keys(species) - common_keys
        hits = [node for node in outputs if names & (_species_name_keys(node) - common_keys)]
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1:
            def score(node: Node) -> int:
                shared = names & (_species_name_keys(node) - common_keys)
                return max((len(item) for item in shared), default=0)

            ranked = sorted(hits, key=score, reverse=True)
            if score(ranked[0]) > score(ranked[1]):
                return ranked[0]
        if len(outputs) == 1 and len(detached) == 1:
            return outputs[0]
        return None

    existing = {(rel.source.id, rel.type, rel.target.id) for rel in graph.relationships}

    def _output_has_species_facts(node: Node) -> bool:
        return any(
            rel.source.id == node.id and rel.type.startswith("ontospecies:")
            for rel in graph.relationships
        )

    relationships = list(graph.relationships)
    drop_ids: set[str] = set()
    leftover: list[Node] = []
    for species in detached:
        dest = _match_output(species)
        if dest is None:
            leftover.append(species)
            continue
        dest_props = dict(dest.properties or {})
        for key, value in (species.properties or {}).items():
            if key.startswith("ontospecies:") and not dest_props.get(key):
                dest_props[key] = value
        by_id[dest.id] = copy_node(dest, properties=dest_props)
        for rel in graph.relationships:
            if rel.source.id != species.id:
                continue
            if not rel.type.startswith("ontospecies:"):
                continue
            key = (dest.id, rel.type, rel.target.id)
            if key in existing:
                continue
            relationships.append(
                Relationship(source=by_id[dest.id], target=rel.target, type=rel.type)
            )
            existing.add(key)
        drop_ids.add(species.id)

    if leftover:
        bare = [node for node in outputs if node.id not in drop_ids and not _output_has_species_facts(node)]
        if len(leftover) == 1 and len(bare) == 1:
            species = leftover[0]
            dest = bare[0]
            dest_props = dict(dest.properties or {})
            for key, value in (species.properties or {}).items():
                if key.startswith("ontospecies:") and not dest_props.get(key):
                    dest_props[key] = value
            by_id[dest.id] = copy_node(dest, properties=dest_props)
            for rel in graph.relationships:
                if rel.source.id != species.id or not rel.type.startswith("ontospecies:"):
                    continue
                key = (dest.id, rel.type, rel.target.id)
                if key in existing:
                    continue
                relationships.append(
                    Relationship(source=by_id[dest.id], target=rel.target, type=rel.type)
                )
                existing.add(key)
            drop_ids.add(species.id)

    if not drop_ids:
        return graph
    nodes = [by_id.get(node.id, node) for node in graph.nodes if node.id not in drop_ids]
    relationships = [
        rel
        for rel in relationships
        if rel.source.id not in drop_ids and rel.target.id not in drop_ids
    ]
    return GraphDocument(nodes=nodes, relationships=relationships, source=graph.source)


def seed_species_outputs(graph: GraphDocument | None) -> GraphDocument | None:
    """Dual-type each inherited ChemicalOutput as ontospecies:Species (Pipeline seed)."""
    if graph is None:
        return None
    species = "ontospecies:Species"
    nodes = []
    for node in graph.nodes:
        extras = list(node.extra_types or [])
        if node.type == "ontosyn:ChemicalOutput" and species not in (node.type, *extras):
            extras.append(species)
            nodes.append(copy_node(node, extra_types=extras))
        else:
            nodes.append(node)
    return GraphDocument(nodes=nodes, relationships=list(graph.relationships), source=graph.source)


def seed_mop_targets(graph: GraphDocument | None) -> GraphDocument | None:
    """Ensure ChemicalOutput --isRepresentedBy--> MetalOrganicPolyhedron (Pipeline SPARQL)."""
    if graph is None:
        return None
    mop_type = "ontomops:MetalOrganicPolyhedron"
    by_id = {node.id: node for node in graph.nodes}
    linked: set[str] = set()
    for rel in graph.relationships:
        if rel.type != "ontosyn:isRepresentedBy":
            continue
        target = by_id.get(rel.target.id)
        if target is None:
            continue
        types = {target.type, *(target.extra_types or [])}
        if mop_type in types:
            linked.add(rel.source.id)
    nodes = list(graph.nodes)
    relationships = list(graph.relationships)
    for rel in graph.relationships:
        if rel.type != "ontosyn:hasChemicalOutput":
            continue
        output = by_id.get(rel.target.id)
        if output is None or output.id in linked:
            continue
        mop_id = f"MetalOrganicPolyhedron-{output.id}"
        if mop_id not in by_id:
            label = str((output.properties or {}).get("rdfs:label") or output.id)
            mop = Node(id=mop_id, type=mop_type, properties={"rdfs:label": label})
            nodes.append(mop)
            by_id[mop_id] = mop
        relationships.append(
            Relationship(source=output, target=by_id[mop_id], type="ontosyn:isRepresentedBy")
        )
        linked.add(output.id)
    return GraphDocument(nodes=nodes, relationships=relationships, source=graph.source)


def bound_mop_outputs(graph: GraphDocument | None) -> list[dict[str, str]]:
    if graph is None:
        return []
    by_id = {node.id: node for node in graph.nodes}
    rows = []
    for rel in graph.relationships:
        if rel.type != "ontosyn:isRepresentedBy":
            continue
        target = by_id.get(rel.target.id)
        source = by_id.get(rel.source.id)
        if target is None or source is None:
            continue
        types = {target.type, *(target.extra_types or [])}
        if "ontomops:MetalOrganicPolyhedron" not in types:
            continue
        rows.append(
            {
                "output_id": source.id,
                "mop_id": target.id,
                "label": str((target.properties or {}).get("rdfs:label") or target.id),
            }
        )
    return rows


def merge_graphs(graphs: list[GraphDocument], *, reuse: str = "paper") -> GraphDocument | None:
    if not graphs:
        return None
    if reuse == "prefix":
        prefixed = [prefix_graph(graph, f"e{index+1}") for index, graph in enumerate(graphs)]
        nodes: list[Node] = []
        relationships: list[Relationship] = []
        seen_ids: set[str] = set()
        for graph in prefixed:
            for node in graph.nodes:
                if node.id in seen_ids:
                    continue
                seen_ids.add(node.id)
                nodes.append(node)
            relationships.extend(graph.relationships)
        merged = GraphDocument(nodes=nodes, relationships=relationships, source=prefixed[0].source)
        return _dedupe_documents(merged)
    merged = graphs[0]
    for graph in graphs[1:]:
        merged = attach_subgraph(merged, graph, reuse="paper")
    return merged
