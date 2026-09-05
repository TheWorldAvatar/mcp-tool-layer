"""Behavior tests for the fixed OM-2 runtime infrastructure."""

from __future__ import annotations

from rdflib import Graph, RDF, RDFS, URIRef

from src.agents.scripts_and_prompts_generation.fixed_om2_runtime import (
    OM2,
    find_or_create_om2_quantity_from_label,
    parse_om2_quantity_label,
    resolve_om2_unit,
)


def test_fixed_om2_runtime_creates_distinct_equivalent_quantity_occurrences() -> None:
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

    assert first != second
    assert len(minted) == 2
    assert (first, RDF.type, OM2.Temperature) in graph
    assert (second, RDF.type, OM2.Temperature) in graph
    assert len(list(graph.objects(first, OM2.hasNumericalValue))) == 1
    assert len(list(graph.objects(first, OM2.hasUnit))) == 1


def test_fixed_om2_runtime_accepts_ascii_temperature_rate_units() -> None:
    assert resolve_om2_unit("C/min") == OM2.degreeCelsiusPerMinute
    assert resolve_om2_unit("C/h") == OM2.degreeCelsiusPerHour
    assert resolve_om2_unit("degC/min") == OM2.degreeCelsiusPerMinute
    assert resolve_om2_unit("degC/h") == OM2.degreeCelsiusPerHour
    assert resolve_om2_unit("deg/min") == OM2.degreeCelsiusPerMinute
    assert resolve_om2_unit("deg/h") == OM2.degreeCelsiusPerHour
    assert resolve_om2_unit("degC h-1") == OM2.degreeCelsiusPerHour
    assert resolve_om2_unit("om-2:degreeCelsiusPerHour") == OM2.degreeCelsiusPerHour
    assert resolve_om2_unit("degreeCelsius") == OM2.degreeCelsius


def test_fixed_om2_runtime_canonicalizes_source_temperature_variants() -> None:
    assert resolve_om2_unit("oC") == OM2.degreeCelsius
    assert resolve_om2_unit("ºC") == OM2.degreeCelsius
    assert resolve_om2_unit("\u030aC") == OM2.degreeCelsius
    assert resolve_om2_unit("deg C") == OM2.degreeCelsius
    assert resolve_om2_unit("degC / h") == OM2.degreeCelsiusPerHour
    assert resolve_om2_unit("oC / h") == OM2.degreeCelsiusPerHour
    assert resolve_om2_unit("degC per hour") == OM2.degreeCelsiusPerHour

    graph = Graph()
    minted: list[URIRef] = []

    def mint_iri(prefix: str, label: str) -> URIRef:
        iri = URIRef(f"https://example.com/{prefix}/{len(minted)}")
        minted.append(iri)
        return iri

    temperature = find_or_create_om2_quantity_from_label(
        graph,
        quantity_class=OM2.Temperature,
        label="60 oC",
        mint_iri=mint_iri,
    )
    rate = find_or_create_om2_quantity_from_label(
        graph,
        quantity_class=OM2.TemperatureRate,
        label="4 degC / h",
        mint_iri=mint_iri,
    )
    exponent_rate = find_or_create_om2_quantity_from_label(
        graph,
        quantity_class=OM2.TemperatureRate,
        label="10 degC h-1",
        mint_iri=mint_iri,
    )
    qualified_yield = find_or_create_om2_quantity_from_label(
        graph,
        quantity_class=OM2.DimensionOne,
        label="22 % based on H2BDC-NH2",
        mint_iri=mint_iri,
    )

    assert (temperature, OM2.hasUnit, OM2.degreeCelsius) in graph
    assert (rate, OM2.hasUnit, OM2.degreeCelsiusPerHour) in graph
    assert (exponent_rate, OM2.hasUnit, OM2.degreeCelsiusPerHour) in graph
    assert (qualified_yield, OM2.hasUnit, OM2.percent) in graph


def test_fixed_om2_runtime_tolerates_descriptive_unit_qualifiers() -> None:
    assert parse_om2_quantity_label("28% yield based on H2BPDC") == (28.0, "%")
    assert parse_om2_quantity_label("22 % based upon H2BDC-NH2") == (22.0, "%")
    assert parse_om2_quantity_label("125 °C under N2") == (125.0, "°c")
    assert parse_om2_quantity_label("1.5e-3 MPa approximately") == (
        1.5e-3,
        "mpa",
    )


def test_fixed_om2_runtime_keeps_percent_yield_over_parenthetical_amounts() -> None:
    assert parse_om2_quantity_label(
        "18% yield (15 mg, 0.009 mmol) based on H2bdc"
    ) == (18.0, "%")
    assert parse_om2_quantity_label(
        "40% yield (22 mg, 0.001 mmol) based on H3TATB"
    ) == (40.0, "%")
    assert parse_om2_quantity_label(
        "30% yield based on H3BTC (33 mg, 0.002 mmol)"
    ) == (30.0, "%")
    assert parse_om2_quantity_label("0.023 g (52% based on H2DCPP)") == (
        52.0,
        "%",
    )
    assert parse_om2_quantity_label("80 °C (50% power)") == (80.0, "°c")
    assert resolve_om2_unit("% yield (15 mg, 0.009 mmol) based on H2bdc") == (
        OM2.percent
    )

    graph = Graph()
    minted: list[URIRef] = []

    def mint_iri(prefix: str, label: str) -> URIRef:
        iri = URIRef(f"https://example.com/{prefix}/{len(minted)}")
        minted.append(iri)
        return iri

    quantity = find_or_create_om2_quantity_from_label(
        graph,
        quantity_class=OM2.AmountOfSubstanceFraction,
        label="18% yield (15 mg, 0.009 mmol) based on H2bdc",
        mint_iri=mint_iri,
    )
    assert (quantity, OM2.hasUnit, OM2.percent) in graph
    values = list(graph.objects(quantity, OM2.hasNumericalValue))
    assert len(values) == 1
    assert float(values[0]) == 18.0


def test_fixed_om2_runtime_does_not_truncate_compound_quantities() -> None:
    assert parse_om2_quantity_label("2 h 30 min") == (2.0, "h 30 min")


def test_fixed_om2_runtime_accepts_unicode_and_extended_unit_aliases() -> None:
    assert resolve_om2_unit("℃") == OM2.degreeCelsius
    assert resolve_om2_unit("degC h⁻¹") == OM2.degreeCelsiusPerHour
    assert resolve_om2_unit("K / hour") == OM2.kelvinPerHour
    assert resolve_om2_unit("wt.%") == OM2.percent
    assert resolve_om2_unit("dm³") == OM2.litre


def test_fixed_om2_runtime_preserves_room_temperature_as_qualitative() -> None:
    graph = Graph()
    minted: list[URIRef] = []

    def mint_iri(prefix: str, label: str) -> URIRef:
        iri = URIRef(f"https://example.com/{prefix}/{len(minted)}")
        minted.append(iri)
        return iri

    first = find_or_create_om2_quantity_from_label(
        graph,
        quantity_class=OM2.Temperature,
        label="room temperature",
        mint_iri=mint_iri,
    )
    second = find_or_create_om2_quantity_from_label(
        graph,
        quantity_class=OM2.Temperature,
        label="ambient temp",
        mint_iri=mint_iri,
    )

    assert first != second
    assert len(minted) == 2
    assert (first, RDF.type, OM2.Temperature) in graph
    assert (second, RDF.type, OM2.Temperature) in graph
    assert (first, RDFS.label, None) in graph
    assert list(graph.objects(first, OM2.hasNumericalValue)) == []
    assert list(graph.objects(first, OM2.hasUnit)) == []


def test_fixed_om2_runtime_preserves_overnight_as_qualitative_duration() -> None:
    graph = Graph()

    duration = find_or_create_om2_quantity_from_label(
        graph,
        quantity_class=OM2.Duration,
        label="overnight",
        mint_iri=lambda prefix, label: URIRef(
            f"https://example.com/{prefix}/{label}"
        ),
    )

    assert (duration, RDF.type, OM2.Duration) in graph
    assert (duration, RDFS.label, None) in graph
    assert list(graph.objects(duration, OM2.hasNumericalValue)) == []
    assert list(graph.objects(duration, OM2.hasUnit)) == []


def test_fixed_om2_runtime_preserves_controlled_qualitative_pressure() -> None:
    graph = Graph()
    pressure = find_or_create_om2_quantity_from_label(
        graph,
        quantity_class=OM2.Pressure,
        label="under vacuum",
        mint_iri=lambda prefix, label: URIRef(
            f"https://example.com/{prefix}/{label}"
        ),
    )

    assert (pressure, RDF.type, OM2.Pressure) in graph
    assert (pressure, RDFS.label, None) in graph
    assert list(graph.objects(pressure, OM2.hasNumericalValue)) == []
    assert list(graph.objects(pressure, OM2.hasUnit)) == []


def test_fixed_om2_runtime_preserves_several_weeks_duration() -> None:
    graph = Graph()
    duration = find_or_create_om2_quantity_from_label(
        graph,
        quantity_class=OM2.Duration,
        label="period of several weeks",
        mint_iri=lambda prefix, label: URIRef(
            f"https://example.com/{prefix}/{label}"
        ),
    )

    assert (duration, RDF.type, OM2.Duration) in graph
    assert (duration, RDFS.label, None) in graph
    assert list(graph.objects(duration, OM2.hasNumericalValue)) == []


def test_fixed_om2_runtime_preserves_brief_as_qualitative_duration() -> None:
    graph = Graph()
    duration = find_or_create_om2_quantity_from_label(
        graph,
        quantity_class=OM2.Duration,
        label="brief",
        mint_iri=lambda prefix, label: URIRef(
            f"https://example.com/{prefix}/{label}"
        ),
    )

    assert (duration, RDF.type, OM2.Duration) in graph
    assert list(graph.objects(duration, OM2.hasNumericalValue)) == []
    assert list(graph.objects(duration, OM2.hasUnit)) == []


def test_fixed_om2_runtime_preserves_until_endpoint_as_qualitative_duration() -> None:
    graph = Graph()
    duration = find_or_create_om2_quantity_from_label(
        graph,
        quantity_class=OM2.Duration,
        label="until completely dissolved",
        mint_iri=lambda prefix, label: URIRef(
            f"https://example.com/{prefix}/{label.replace(' ', '-')}"
        ),
    )

    assert (duration, RDF.type, OM2.Duration) in graph
    assert list(graph.objects(duration, OM2.hasNumericalValue)) == []
    assert list(graph.objects(duration, OM2.hasUnit)) == []


def test_fixed_om2_runtime_supports_extended_unit_aliases() -> None:
    assert resolve_om2_unit("μL") == OM2.microlitre
    assert resolve_om2_unit("cm³") == OM2.cubicCentimetre
    assert resolve_om2_unit("MPa") == OM2.megapascal
    assert resolve_om2_unit("day") == OM2.day
    assert resolve_om2_unit("weeks") == OM2.week


def test_fixed_om2_runtime_parses_word_number_durations() -> None:
    graph = Graph()
    minted: list[URIRef] = []

    def mint_iri(prefix: str, label: str) -> URIRef:
        iri = URIRef(f"https://example.com/{prefix}/{len(minted)}")
        minted.append(iri)
        return iri

    three_days = find_or_create_om2_quantity_from_label(
        graph,
        quantity_class=OM2.Duration,
        label="three days",
        mint_iri=mint_iri,
    )
    one_week = find_or_create_om2_quantity_from_label(
        graph,
        quantity_class=OM2.Duration,
        label="one week",
        mint_iri=mint_iri,
    )

    assert (three_days, OM2.hasNumericalValue, None) in graph
    assert (three_days, OM2.hasUnit, OM2.day) in graph
    assert (one_week, OM2.hasNumericalValue, None) in graph
    assert (one_week, OM2.hasUnit, OM2.week) in graph
