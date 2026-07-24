"""Regression tests for the OntoSyn semicolon-delimited alias contract."""

from __future__ import annotations

from rdflib import Graph

from scripts.output_conversion_ttl_to_json.cbu.cbu_formula import (
    query_ccdc_to_cbus,
)
from scripts.output_conversion_ttl_to_json.name_utils import (
    split_alternative_names,
)
from scripts.output_conversion_ttl_to_json.ontosynthesis_cbu_conversion import (
    build_cbu_json_from_graph,
)
from scripts.output_conversion_ttl_to_json.ontosynthesis_chemicals_conversion import (
    get_namespaces,
    query_synthesis_inputs,
)
from scripts.output_conversion_ttl_to_json.ontosynthesis_step_conversion import (
    get_namespaces as get_step_namespaces,
    query_step_chemicals,
)


TTL = """
@prefix ex: <https://example.com/> .
@prefix ontosyn: <https://www.theworldavatar.com/kg/OntoSyn/> .
@prefix ontomops: <https://www.theworldavatar.com/kg/ontomops/> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .

ex:synthesis a ontosyn:ChemicalSynthesis ;
  rdfs:label "Example synthesis" ;
  ontosyn:hasChemicalInput ex:dmf ;
  ontosyn:hasSynthesisStep ex:add .
ex:add a ontosyn:Add ;
  rdfs:label "Add DMF" ;
  ontosyn:hasAddedChemicalInput ex:dmf ;
  ontosyn:hasSolventDissolve ex:dmf ;
  ontosyn:hasWashingSolvent ex:dmf .
ex:dmf rdfs:label "DMF" ;
  ontosyn:hasAlternativeNames "N,N-dimethylformamide; Dimethylformamide ; DMFA" .

ex:mop a ontomops:MetalOrganicPolyhedron ;
  ontomops:hasCCDCNumber "123456" ;
  ontomops:hasChemicalBuildingUnit ex:cbu .
ex:cbu rdfs:label "[C3H7NO]" ;
  ontomops:hasCBUFormula "[C3H7NO]" ;
  ontosyn:hasAlternativeNames "A; B;C" ;
  owl:sameAs ex:dmf .
"""


def _graph() -> Graph:
    graph = Graph()
    graph.parse(data=TTL, format="turtle")
    return graph


def test_split_alternative_names_preserves_commas() -> None:
    assert split_alternative_names(" A; ;B; C ") == ["A", "B", "C"]
    assert split_alternative_names("N,N-dimethylformamide") == [
        "N,N-dimethylformamide"
    ]


def test_chemicals_conversion_flattens_aliases() -> None:
    graph = _graph()
    result = query_synthesis_inputs(
        graph, get_namespaces(graph), "https://example.com/synthesis"
    )
    assert result[0]["chemicalName"] == [
        "DMF",
        "N,N-dimethylformamide",
        "Dimethylformamide",
        "DMFA",
    ]


def test_step_conversion_flattens_all_chemical_aliases() -> None:
    graph = _graph()
    result = query_step_chemicals(
        graph, get_step_namespaces(graph), "https://example.com/add"
    )
    expected = ["DMF", "N,N-dimethylformamide", "Dimethylformamide", "DMFA"]
    assert result["addedChemical"][0]["chemicalName"] == expected
    assert result["solvent"][0]["chemicalName"] == expected
    assert result["washingSolvent"][0]["chemicalName"] == expected


def test_cbu_converters_flatten_direct_and_same_as_aliases() -> None:
    graph = _graph()
    names = build_cbu_json_from_graph(graph)["synthesisProcedures"][0][
        "cbuSpeciesNames1"
    ]
    assert "A" in names
    assert "B" in names
    assert "C" in names
    assert "A; B;C" not in names
    assert "N,N-dimethylformamide" in names
    assert "N,N-dimethylformamide; Dimethylformamide ; DMFA" not in names

    legacy_names = query_ccdc_to_cbus(graph)["123456"]["[C3H7NO]"]
    assert "A" in legacy_names
    assert "B" in legacy_names
    assert "C" in legacy_names
    assert "N,N-dimethylformamide" in legacy_names
