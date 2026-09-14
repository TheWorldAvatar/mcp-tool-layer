"""Export prune keeps only the subgraph reachable from the bound top entity."""

from __future__ import annotations

from rdflib import Literal, Namespace, URIRef
from rdflib.namespace import RDF, RDFS, XSD

from src.extraction_prompt_generation.runtime_support.rdf.lifecycle import (
    init_memory,
    prepare_graph_for_export,
)
from src.extraction_prompt_generation.runtime_support.rdf.registry import (
    reset_retained_graph,
    retained_graph,
)

ONTOSYN = Namespace("https://www.theworldavatar.com/kg/OntoSyn/")

_ADD_CONTRACT = {
    "Add": {
        "class_iri": str(ONTOSYN.Add),
        "parent_predicate_iri": str(ONTOSYN.hasSynthesisStep),
        "ordering_property_iri": str(ONTOSYN.hasOrder),
    }
}


def _typed(graph, node: URIRef, class_iri, label: str) -> None:
    graph.add((node, RDF.type, class_iri))
    graph.add((node, RDFS.label, Literal(label)))


def test_export_prunes_unattached_step_island(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("TWA_AGENTIC_DATA_DIR", str(tmp_path))
    reset_retained_graph()
    root = "https://www.theworldavatar.com/kg/instance/test/syn"
    init_memory("10.test/prune", "UMC-1", root_iri=root)
    graph = retained_graph()
    syn = URIRef(root)
    linked = URIRef("https://www.theworldavatar.com/kg/instance/test/add-linked")
    linked_in = URIRef("https://www.theworldavatar.com/kg/instance/test/water-linked")
    orphan = URIRef("https://www.theworldavatar.com/kg/instance/test/add-orphan")
    orphan_in = URIRef("https://www.theworldavatar.com/kg/instance/test/water-orphan")
    _typed(graph, syn, ONTOSYN.ChemicalSynthesis, "UMC-1")
    _typed(graph, linked, ONTOSYN.Add, "Add DMF")
    _typed(graph, linked, ONTOSYN.SynthesisStep, "Add DMF")
    graph.add((linked, ONTOSYN.hasOrder, Literal(1, datatype=XSD.integer)))
    graph.add((syn, ONTOSYN.hasSynthesisStep, linked))
    _typed(graph, linked_in, ONTOSYN.ChemicalInput, "DMF")
    graph.add((linked, ONTOSYN.hasAddedChemicalInput, linked_in))
    _typed(graph, orphan, ONTOSYN.Add, "Add water")
    _typed(graph, orphan, ONTOSYN.SynthesisStep, "Add water")
    graph.add((orphan, ONTOSYN.hasOrder, Literal(6, datatype=XSD.integer)))
    _typed(graph, orphan_in, ONTOSYN.ChemicalInput, "water")
    graph.add((orphan, ONTOSYN.hasAddedChemicalInput, orphan_in))

    result = prepare_graph_for_export(_ADD_CONTRACT)
    assert result["status"] == "ok"
    assert (syn, ONTOSYN.hasSynthesisStep, linked) in graph
    assert (linked, RDF.type, ONTOSYN.Add) in graph
    assert (linked_in, RDF.type, ONTOSYN.ChemicalInput) in graph
    assert (orphan, RDF.type, ONTOSYN.Add) not in graph
    assert (orphan_in, RDF.type, ONTOSYN.ChemicalInput) not in graph


def test_duplicate_ordered_member_is_unlinked_then_pruned(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("TWA_AGENTIC_DATA_DIR", str(tmp_path))
    reset_retained_graph()
    root = "https://www.theworldavatar.com/kg/instance/test/syn-dup"
    init_memory("10.test/prune-dup", "UMC-1", root_iri=root)
    graph = retained_graph()
    syn = URIRef(root)
    keep = URIRef("https://www.theworldavatar.com/kg/instance/test/add-keep")
    dup = URIRef("https://www.theworldavatar.com/kg/instance/test/add-dup")
    keep_in = URIRef("https://www.theworldavatar.com/kg/instance/test/keep-in")
    dup_in = URIRef("https://www.theworldavatar.com/kg/instance/test/dup-in")
    _typed(graph, syn, ONTOSYN.ChemicalSynthesis, "UMC-1")
    for node, label, inp, inp_label in (
        (keep, "Add DMF", keep_in, "DMF"),
        (dup, "Add water", dup_in, "water"),
    ):
        _typed(graph, node, ONTOSYN.Add, label)
        _typed(graph, node, ONTOSYN.SynthesisStep, label)
        graph.add((node, ONTOSYN.hasOrder, Literal(1, datatype=XSD.integer)))
        graph.add((syn, ONTOSYN.hasSynthesisStep, node))
        _typed(graph, inp, ONTOSYN.ChemicalInput, inp_label)
        graph.add((node, ONTOSYN.hasAddedChemicalInput, inp))

    result = prepare_graph_for_export(_ADD_CONTRACT)
    assert result["status"] == "ok"
    members = list(graph.objects(syn, ONTOSYN.hasSynthesisStep))
    assert members == [keep]
    assert (dup, RDF.type, ONTOSYN.Add) not in graph
    assert (dup_in, RDF.type, ONTOSYN.ChemicalInput) not in graph
    assert (keep, RDF.type, ONTOSYN.Add) in graph
    assert (keep_in, RDF.type, ONTOSYN.ChemicalInput) in graph
