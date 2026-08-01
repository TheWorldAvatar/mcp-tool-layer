"""Behavior tests for the fixed OM-2 runtime infrastructure."""

from __future__ import annotations

from rdflib import Graph, RDF, URIRef

from src.agents.scripts_and_prompts_generation.fixed_om2_runtime import (
    OM2,
    find_or_create_om2_quantity_from_label,
    resolve_om2_unit,
)


def test_fixed_om2_runtime_reuses_equivalent_quantity() -> None:
    graph = Graph()
    minted: list[URIRef] = []

    def mint_iri(prefix: str, label: str) -> URIRef:
        iri = URIRef(f"https://example.com/{prefix}/{len(minted)}")
        minted.append(iri)
        return iri

    first = find_or_create_om2_quantity_from_label(
        graph,
        quantity_class=OM2.Temperature,
        label="150 °C",
        mint_iri=mint_iri,
    )
    second = find_or_create_om2_quantity_from_label(
        graph,
        quantity_class=OM2.Temperature,
        label="150 degree celsius",
        mint_iri=mint_iri,
    )

    assert first == second
    assert len(minted) == 1
    assert (first, RDF.type, OM2.Temperature) in graph
    assert len(list(graph.objects(first, OM2.hasNumericalValue))) == 1
    assert len(list(graph.objects(first, OM2.hasUnit))) == 1


def test_fixed_om2_runtime_accepts_ascii_temperature_rate_units() -> None:
    assert resolve_om2_unit("C/min") == OM2.degreeCelsiusPerMinute
    assert resolve_om2_unit("C/h") == OM2.degreeCelsiusPerHour
