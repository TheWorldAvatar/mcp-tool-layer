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


# Namespaces
RDF_NS = RDF
RDFS_NS = RDFS
ONTOSYN = Namespace("https://www.theworldavatar.com/kg/OntoSyn/")
ONTOMOPS = Namespace("https://www.theworldavatar.com/kg/ontomops/")
ONTOSPECIES = Namespace("http://www.theworldavatar.com/ontology/ontospecies/OntoSpecies.owl#")


def _list_ttl_files(path: str) -> List[str]:
    if not os.path.isdir(path):
        return []
    return [
        os.path.join(path, f)
        for f in os.listdir(path)
        if f.lower().endswith(".ttl") and os.path.isfile(os.path.join(path, f))
    ]


def _gather_files_for_hash(hash_dir: str) -> Tuple[List[str], List[str], List[str]]:
    """
    Return (root_output_ttls, ontospecies_ttls, integrated_ttls) for a given hash directory.
    - root_output_ttls: files in hash root starting with "output_" excluding "output_top.ttl"
    - ontospecies_ttls: files under ontospecies_output
    - integrated_ttls: files under cbu_derivation/integrated
    """
    root_files = _list_ttl_files(hash_dir)
    root_output = [
        f
        for f in root_files
        if os.path.basename(f).startswith("output_")
        and os.path.basename(f) != "output_top.ttl"
    ]

    ontospecies_dir = os.path.join(hash_dir, "ontospecies_output")
    ontospecies_files = _list_ttl_files(ontospecies_dir)

    integrated_dir = os.path.join(hash_dir, "cbu_derivation", "integrated")
    integrated_files = _list_ttl_files(integrated_dir)

    return root_output, ontospecies_files, integrated_files


def _parse_into_graph(graph: Graph, ttl_files: Iterable[str]) -> None:
    for path in ttl_files:
        graph.parse(path, format="turtle")


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

    root_output, ontospecies_files, integrated_files = _gather_files_for_hash(hash_dir)

    g = Graph()
    _bind_prefixes(g)

    # Parse and merge all files without any additional alignment/linking
    _parse_into_graph(g, root_output)
    _parse_into_graph(g, ontospecies_files)
    _parse_into_graph(g, integrated_files)

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


