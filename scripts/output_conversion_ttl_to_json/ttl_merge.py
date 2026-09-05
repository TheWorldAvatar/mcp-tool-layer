"""
Utilities to merge TTL files for a given dataset hash and add linking triples
between `ontosyn:ChemicalSynthesis` instances (in root output TTLs) and
`ontomops:MetalOrganicPolyhedron` instances (in `cbu_derivation/integrated`),
via `ontosyn:hasChemicalOutput` and `ontosyn:isRepresentedBy`.

This module uses rdflib for parsing and merging.
"""

from __future__ import annotations

import os
from typing import Dict, Iterable, List, Tuple, Set
import hashlib

from rdflib import BNode, Graph, Namespace, RDF, RDFS, URIRef, Literal
from rdflib.namespace import OWL

from scripts.output_conversion_ttl_to_json.name_utils import is_hashed_artifact_label


# Namespaces
RDF_NS = RDF
RDFS_NS = RDFS
ONTOSYN = Namespace("https://www.theworldavatar.com/kg/OntoSyn/")
ONTOMOPS = Namespace("https://www.theworldavatar.com/kg/ontomops/")
ONTOSPECIES = Namespace("http://www.theworldavatar.com/ontology/ontospecies/OntoSpecies.owl#")


def _list_ttl_files(path: str) -> List[str]:
    from src.pipelines.utils.runtime_paths import list_runtime_files, runtime_path_exists

    if not runtime_path_exists(path):
        return []
    return [
        candidate
        for candidate in list_runtime_files(path, suffix=".ttl")
        if candidate.lower().endswith(".ttl")
    ]


def _gather_files_for_hash(hash_dir: str) -> Tuple[List[str], List[str], List[str], List[str]]:
    """
    Return (main_ontology_ttls, ontospecies_ttls, ontomops_ttls, integrated_ttls) for a given hash directory.
    - main_ontology_ttls: prefer files under `ontosynthesis_output/` excluding `top.ttl`,
      then also include legacy hash-root `output_*.ttl` excluding `output_top.ttl`
    - ontospecies_ttls: files under ontospecies_output
    - ontomops_ttls: files under ontomops_output
    - integrated_ttls: files under cbu_derivation/integrated
    """
    root_files = _list_ttl_files(hash_dir)
    legacy_root_output = [
        f
        for f in root_files
        if os.path.basename(f).startswith("output_")
        and os.path.basename(f) != "output_top.ttl"
    ]

    ontosynthesis_dir = os.path.join(hash_dir, "ontosynthesis_output")
    ontosynthesis_files = [
        f
        for f in _list_ttl_files(ontosynthesis_dir)
        if os.path.basename(f).lower() != "top.ttl"
    ]

    main_ontology_files = ontosynthesis_files + [
        f for f in legacy_root_output if f not in ontosynthesis_files
    ]

    ontospecies_dir = os.path.join(hash_dir, "ontospecies_output")
    ontospecies_files = _list_ttl_files(ontospecies_dir)

    ontomops_dir = os.path.join(hash_dir, "ontomops_output")
    ontomops_files = _list_ttl_files(ontomops_dir)

    integrated_dir = os.path.join(hash_dir, "cbu_derivation", "integrated")
    integrated_files = _list_ttl_files(integrated_dir)

    return main_ontology_files, ontospecies_files, ontomops_files, integrated_files


def _parse_into_graph(graph: Graph, ttl_files: Iterable[str]) -> None:
    from src.pipelines.utils.runtime_paths import read_runtime_text

    for path in ttl_files:
        graph.parse(data=read_runtime_text(path), format="turtle")


def _merge_ontospecies_files_without_step_subgraph(
    graph: Graph,
    ttl_files: Iterable[str],
) -> None:
    """
    Merge extension TTLs while excluding their procedural step subgraph.

    `ontosynthesis_output/*.ttl` is the canonical source for synthesis procedure structure.
    Extension outputs may repeat an alternative step graph for the same synthesis URI; if we
    union those graphs directly, the merged TTL ends up with duplicate `hasSynthesisStep`
    members and duplicated `hasOrder` sequences.

    We therefore keep non-step extension data (Species, formulas, outputs, etc.) but strip:
    - all `ontosyn:hasSynthesisStep` edges
    - all triples whose subject or object is a step node typed as `ontosyn:SynthesisStep`
    """
    from src.pipelines.utils.runtime_paths import read_runtime_text

    for path in ttl_files:
        fg = Graph()
        fg.parse(data=read_runtime_text(path), format="turtle")

        step_nodes: Set[URIRef] = {
            step
            for step in fg.subjects(RDF_NS.type, ONTOSYN.SynthesisStep)
            if isinstance(step, URIRef)
        }

        cleaned = Graph()
        _bind_prefixes(cleaned)
        for prefix, ns in fg.namespaces():
            cleaned.bind(prefix, ns)

        for s, p, o in fg:
            if p == ONTOSYN.hasSynthesisStep:
                continue
            if isinstance(s, URIRef) and s in step_nodes:
                continue
            if isinstance(o, URIRef) and o in step_nodes:
                continue
            if _is_extension_synthesis_label_alias(graph, fg, s, p, o):
                continue
            cleaned.add((s, p, o))

        for triple in cleaned:
            graph.add(triple)


def _is_chemical_synthesis(graph: Graph, node: object) -> bool:
    return isinstance(node, URIRef) and (node, RDF_NS.type, ONTOSYN.ChemicalSynthesis) in graph


def _is_extension_synthesis_label_alias(
    merged: Graph,
    extension: Graph,
    subject: object,
    predicate: object,
    obj: object,
) -> bool:
    """Drop extension ChemicalSynthesis labels that collide with the main-graph identity.

    Occurrence-surface extension TTLs often stamp the Windows-truncated export stem
    (``Name--<12-hex>.ttl``) as ``rdfs:label`` on the same synthesis IRI. Unioning
    that alias makes SPARQL ``SELECT ?synthesis ?synthesisLabel`` emit a second
    synthesis and the scorer treats the hashed name as Pred-only.
    """
    if predicate != RDFS_NS.label or not isinstance(subject, URIRef):
        return False
    if not (
        _is_chemical_synthesis(extension, subject)
        or _is_chemical_synthesis(merged, subject)
    ):
        return False
    label = str(obj).strip()
    if is_hashed_artifact_label(label):
        return True
    return any(merged.objects(subject, RDFS_NS.label))


def _drop_hashed_synthesis_alias_labels(graph: Graph) -> Graph:
    """Remove hashed filename labels when a human ChemicalSynthesis label exists."""
    doomed = []
    for synth in graph.subjects(RDF_NS.type, ONTOSYN.ChemicalSynthesis):
        labels = [str(value) for value in graph.objects(synth, RDFS_NS.label)]
        if not any(not is_hashed_artifact_label(label) for label in labels):
            continue
        for literal in graph.objects(synth, RDFS_NS.label):
            if is_hashed_artifact_label(str(literal)):
                doomed.append((synth, RDFS_NS.label, literal))
    for triple in doomed:
        graph.remove(triple)
    return graph


def _normalize_synthesis_label_from_filename(base_name: str) -> str:
    """
    Convert a file base name like "Synthesis_of_VMOC-1" to label "Synthesis of VMOC-1".
    Keeps other characters intact (e.g., middle dot).
    """
    return base_name.replace("_", " ").strip()


def _normalize_label_for_match(label: str) -> str:
    """
    Normalize labels for robust matching across files.
    - casefold
    - replace underscores with spaces
    - replace middle dot with a dot
    - collapse multiple whitespace
    - strip
    """
    s = label.casefold()
    s = s.replace("_", " ")
    s = s.replace("·", ".")
    s = " ".join(s.split())
    return s.strip()


def _find_existing_output_for_mop(g: Graph, synthesis: str, mop) -> Tuple[bool, List]:
    """
    Check if there exists a ChemicalOutput that represents the given MOP and is linked
    from the given synthesis via hasChemicalOutput.
    Returns (exists, existing_outputs).
    """
    existing = []
    for chem_out in g.subjects(predicate=ONTOSYN.isRepresentedBy, object=mop):
        # optional type check
        if (chem_out, RDF_NS.type, ONTOSYN.ChemicalOutput) in g:
            # if already linked to this synthesis, we consider it existing usage
            if (synthesis, ONTOSYN.hasChemicalOutput, chem_out) in g:
                return True, [chem_out]
            existing.append(chem_out)
    return (len(existing) > 0, existing)


def _bind_prefixes(g: Graph) -> None:
    g.bind("rdf", str(RDF_NS))
    g.bind("rdfs", str(RDFS_NS))
    g.bind("ontosyn", str(ONTOSYN))
    g.bind("ontomops", str(ONTOMOPS))
    g.bind("ontospecies", str(ONTOSPECIES))


def _choose_preferred_node(g: Graph, nodes: List[URIRef]) -> URIRef | None:
    if not nodes:
        return None

    def _score(node: URIRef) -> Tuple[int, str]:
        outgoing = sum(1 for _ in g.triples((node, None, None)))
        incoming = sum(1 for _ in g.triples((None, None, node)))
        return (outgoing + incoming, str(node))

    return sorted(nodes, key=_score, reverse=True)[0]


def _remap_nodes(g: Graph, remap: Dict[URIRef, URIRef]) -> Graph:
    if not remap:
        return g

    rewritten = Graph()
    _bind_prefixes(rewritten)
    for s, p, o in g:
        new_s = remap.get(s, s) if isinstance(s, URIRef) else s
        new_o = remap.get(o, o) if isinstance(o, URIRef) else o
        rewritten.add((new_s, p, new_o))
    return rewritten


def _dedupe_synthesis_nodes_by_label(g: Graph) -> Graph:
    """Collapse duplicate ChemicalSynthesis nodes that only differ by URI."""
    by_label: Dict[str, List[URIRef]] = {}
    for synth in g.subjects(RDF_NS.type, ONTOSYN.ChemicalSynthesis):
        if not isinstance(synth, URIRef):
            continue
        labels = [str(v) for v in g.objects(synth, RDFS_NS.label)]
        for label in labels:
            norm = _normalize_label_for_match(label)
            if norm:
                by_label.setdefault(norm, []).append(synth)

    remap: Dict[URIRef, URIRef] = {}
    for nodes in by_label.values():
        unique_nodes = sorted(set(nodes), key=str)
        if len(unique_nodes) < 2:
            continue
        canonical = _choose_preferred_node(g, unique_nodes)
        if canonical is None:
            continue
        for node in unique_nodes:
            if node != canonical:
                remap[node] = canonical

    return _remap_nodes(g, remap)


def _synthesis_label_map(g: Graph) -> Dict[str, URIRef]:
    mapping: Dict[str, URIRef] = {}
    for synth in g.subjects(RDF_NS.type, ONTOSYN.ChemicalSynthesis):
        if not isinstance(synth, URIRef):
            continue
        labels = [str(v) for v in g.objects(synth, RDFS_NS.label)]
        for label in labels:
            norm = _normalize_label_for_match(label)
            if norm and norm not in mapping:
                mapping[norm] = synth
    return mapping


def _attach_steps_from_ontospecies_files(g: Graph, ontospecies_files: Iterable[str]) -> None:
    """
    Reattach detailed step nodes from `ontospecies_output/*.ttl` onto the canonical synthesis URI.

    These extension TTLs may contain rich `Add`/`HeatChill`/`Transfer` nodes, but we only
    trust step members that are already explicitly linked from a source synthesis via
    `ontosyn:hasSynthesisStep`. This avoids reattaching stale/orphan step nodes that merely
    happen to be typed as `ontosyn:SynthesisStep`.
    """
    label_map = _synthesis_label_map(g)
    if not label_map:
        return

    for path in ontospecies_files:
        fg = Graph()
        try:
            fg.parse(path, format="turtle")
        except Exception:
            continue

        source_synths: list[URIRef] = []
        typed_synths = [
            synth
            for synth in fg.subjects(RDF_NS.type, ONTOSYN.ChemicalSynthesis)
            if isinstance(synth, URIRef)
        ]
        for synth in typed_synths:
            labels = [str(v) for v in fg.objects(synth, RDFS_NS.label)]
            if not labels and "ChemicalSynthesis/" in str(synth):
                labels = [str(v) for _, _, v in fg.triples((synth, RDFS_NS.label, None))]
            if any(label_map.get(_normalize_label_for_match(label)) is not None for label in labels):
                source_synths.append(synth)

        if not source_synths:
            unlabeled_synths = [
                synth
                for synth in fg.subjects(None, ONTOSYN.hasSynthesisStep)
                if isinstance(synth, URIRef) and "ChemicalSynthesis/" in str(synth)
            ]
            for synth in unlabeled_synths:
                labels = [str(v) for v in fg.objects(synth, RDFS_NS.label)]
                if any(label_map.get(_normalize_label_for_match(label)) is not None for label in labels):
                    source_synths.append(synth)

        for source_synth in source_synths:
            canonical_synth: URIRef | None = None
            for label in [str(v) for v in fg.objects(source_synth, RDFS_NS.label)]:
                canonical_synth = label_map.get(_normalize_label_for_match(label))
                if canonical_synth is not None:
                    break
            if canonical_synth is None:
                continue
            if any(True for _ in g.objects(canonical_synth, ONTOSYN.hasSynthesisStep)):
                # Main ontosynthesis output is canonical when it already provides steps.
                continue

            step_nodes = sorted(
                {
                    step
                    for step in fg.objects(source_synth, ONTOSYN.hasSynthesisStep)
                    if isinstance(step, URIRef)
                },
                key=str,
            )
            for step in step_nodes:
                g.add((canonical_synth, ONTOSYN.hasSynthesisStep, step))


def merge_for_hash(
    hash_value: str,
    data_root: str,
    add_links: bool = True,
    enable_heuristic_linking: bool = True,
) -> Graph:
    """
    Merge TTLs for a given hash and optionally add linking triples.

    - Reads:
      - data/<hash>/output_*.ttl (excluding output_top.ttl)
      - data/<hash>/ontospecies_output/*.ttl
      - data/<hash>/cbu_derivation/integrated/*.ttl

    - Linking logic (when add_links=True):
      For each integrated file (e.g., "Synthesis_of_VMOC-1.ttl"),
      1) find all subjects of type ontomops:MetalOrganicPolyhedron in that file,
      2) find an ontosyn:ChemicalSynthesis in the merged graph whose rdfs:label
         equals the normalized filename label (underscores -> spaces),
      3) connect the synthesis to a ontosyn:ChemicalOutput via ontosyn:hasChemicalOutput;
         reuse an existing ChemicalOutput that ontosyn:isRepresentedBy the MOP if present,
         else create a new blank-node ChemicalOutput and assert ontosyn:isRepresentedBy to the MOP.
    """
    hash_dir = os.path.join(data_root, hash_value)

    main_ontology_files, ontospecies_files, ontomops_files, integrated_files = _gather_files_for_hash(hash_dir)

    g = Graph()
    _bind_prefixes(g)

    # Parse and merge all files without any additional alignment/linking
    _parse_into_graph(g, main_ontology_files)
    _merge_ontospecies_files_without_step_subgraph(g, ontospecies_files)
    _merge_ontospecies_files_without_step_subgraph(g, ontomops_files)
    _parse_into_graph(g, integrated_files)
    g = _dedupe_synthesis_nodes_by_label(g)
    g = _drop_hashed_synthesis_alias_labels(g)
    _attach_steps_from_ontospecies_files(g, ontospecies_files)

    return g


def build_link_graph(merged_graph: Graph) -> Graph:
    """
    Build a debugging subgraph containing only selected instance types and
    their direct connections. The selected instance types are:
    - ontospecies:Species
    - ontosyn:ChemicalSynthesis
    - ontomops:MetalOrganicPolyhedron
    - ontomops:ChemicalBuildingUnit

    Connections preserved:
    - Any triple where both subject and object are selected instances
    - For convenience, also include the specific bridging pattern:
      ChemicalSynthesis --ontosyn:hasChemicalOutput--> _:x --ontosyn:isRepresentedBy--> MetalOrganicPolyhedron
      (without asserting the type of the blank node)
    - Include rdf:type and rdfs:label of selected instance nodes for readability
    """
    g = merged_graph
    lg = Graph()
    _bind_prefixes(lg)

    allowed_types: Tuple[URIRef, ...] = (
        ONTOSPECIES.Species,
        ONTOSYN.ChemicalSynthesis,
        ONTOMOPS.MetalOrganicPolyhedron,
        ONTOMOPS.ChemicalBuildingUnit,
    )

    # Identify selected instance nodes
    selected: Set = set()
    for t in allowed_types:
        for s in g.subjects(RDF_NS.type, t):
            selected.add(s)

    # Add type and label for selected nodes
    for s in selected:
        for t in g.objects(s, RDF_NS.type):
            if t in allowed_types:
                lg.add((s, RDF_NS.type, t))
        for lab in g.objects(s, RDFS_NS.label):
            lg.add((s, RDFS_NS.label, lab))

    # Add direct connections among selected nodes
    for (s, p, o) in g.triples((None, None, None)):
        if s in selected and o in selected:
            lg.add((s, p, o))

    # Add bridging synthesis->ChemicalOutput->MOP connections
    for synth in [n for n in selected if (n, RDF_NS.type, ONTOSYN.ChemicalSynthesis) in g]:
        # Include hasChemicalInput connections for debugging visibility
        for chem_input in g.objects(synth, ONTOSYN.hasChemicalInput):
            lg.add((synth, ONTOSYN.hasChemicalInput, chem_input))
            # include minimal info for ChemicalInput nodes
            if (chem_input, RDF_NS.type, ONTOSYN.ChemicalInput) in g:
                lg.add((chem_input, RDF_NS.type, ONTOSYN.ChemicalInput))
            for lab in g.objects(chem_input, RDFS_NS.label):
                lg.add((chem_input, RDFS_NS.label, lab))

        for chem_out in g.objects(synth, ONTOSYN.hasChemicalOutput):
            # Only bridge to MOP if the object is a selected MOP
            for mop in g.objects(chem_out, ONTOSYN.isRepresentedBy):
                if mop in selected and (mop, RDF_NS.type, ONTOMOPS.MetalOrganicPolyhedron) in g:
                    # Skolemize blank ChemicalOutput nodes for readability in debug graph
                    skolem_out = chem_out
                    if isinstance(chem_out, BNode):
                        synth_id = str(synth)
                        mop_id = str(mop)
                        h = hashlib.sha1((synth_id + "|" + mop_id).encode("utf-8")).hexdigest()
                        skolem_out = URIRef(
                            f"https://www.theworldavatar.com/kg/OntoSyn/instance/ChemicalOutput/{h}"
                        )
                    lg.add((synth, ONTOSYN.hasChemicalOutput, skolem_out))
                    lg.add((skolem_out, ONTOSYN.isRepresentedBy, mop))

    return lg

    # Note: Below code will not execute due to earlier return; keep additions above


def remove_orphan_entities(merged_graph: Graph) -> Graph:
    """
    Remove orphan entities: instances that are not connected to any other node
    via a non-rdf:type edge. Datatype-only properties (literals) do not count as
    connections. Incoming or outgoing edges to other resources (URIRef/BNode) via
    predicates other than rdf:type count as connections.

    Returns a new pruned Graph.
    """
    g = merged_graph

    # Compute connectivity counts for resources
    connected: Set = set()

    for (s, p, o) in g.triples((None, None, None)):
        if p == RDF_NS.type:
            continue
        # Only count connections to other resources (not literals)
        if isinstance(o, (URIRef, BNode)):
            connected.add(s)
            connected.add(o)

    # Orphans are subjects that never appear in `connected`
    subjects = set(s for (s, _, _) in g.triples((None, None, None)))
    orphans = subjects - connected

    pruned = Graph()
    _bind_prefixes(pruned)
    for (s, p, o) in g.triples((None, None, None)):
        if s in orphans:
            continue
        pruned.add((s, p, o))

    return pruned


__all__ = [
    "merge_for_hash",
    "build_link_graph",
    "remove_orphan_entities",
]


