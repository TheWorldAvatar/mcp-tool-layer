from rdflib import Graph, Literal, Namespace, RDF, RDFS

from scripts.output_conversion_ttl_to_json.ontosynthesis_chemicals_conversion import (
    get_namespaces,
    query_all_ontomops_data,
    query_synthesis_outputs,
)
from scripts.output_conversion_ttl_to_json.ontosynthesis_step_conversion import (
    query_outputs,
)


ONTOSYN = Namespace("https://www.theworldavatar.com/kg/OntoSyn/")
ONTOMOPS = Namespace("https://www.theworldavatar.com/kg/ontomops/")
ONTOSPECIES = Namespace(
    "http://www.theworldavatar.com/ontology/ontospecies/OntoSpecies.owl#"
)
EX = Namespace("https://example.test/")


def _transformation_graph() -> Graph:
    graph = Graph()
    graph.bind("ontosyn", ONTOSYN)
    graph.bind("ontomops", ONTOMOPS)
    graph.bind("ontospecies", ONTOSPECIES)
    graph.add((EX.synthesis, ONTOSYN.hasChemicalOutput, EX.output))
    graph.add((EX.output, RDF.type, ONTOSYN.ChemicalOutput))
    graph.add((EX.output, RDF.type, ONTOSPECIES.Species))
    graph.add((EX.output, RDFS.label, Literal("VMOP-alpha")))
    graph.add((EX.output, ONTOSYN.isRepresentedBy, EX.mop))
    graph.add((EX.mop, RDF.type, ONTOMOPS.MetalOrganicPolyhedron))
    graph.add((EX.mop, RDFS.label, Literal("VMOP-alpha")))
    graph.add((EX.mop, ONTOMOPS.hasCCDCNumber, Literal("1590349")))
    return graph


def test_step_conversion_follows_output_representation_for_ccdc() -> None:
    result = query_outputs(_transformation_graph(), str(EX.synthesis))

    assert result["ccdc"] == ["1590349"]


def test_chemical_conversion_follows_dual_typed_output_for_ccdc() -> None:
    graph = _transformation_graph()
    namespaces = get_namespaces(graph)
    ontomops_data = query_all_ontomops_data(graph, namespaces)

    outputs = query_synthesis_outputs(
        graph,
        namespaces,
        str(EX.synthesis),
        ontomops_data,
    )

    assert outputs[0]["CCDCNumber"] == "1590349"
