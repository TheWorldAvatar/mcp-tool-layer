"""Stable OM-2 graph helpers shared by generated ontology packages."""

from __future__ import annotations

import re
from collections.abc import Callable

from rdflib import Graph, Literal, Namespace, RDF, RDFS, URIRef
from rdflib.namespace import XSD

OM2 = Namespace("http://www.ontology-of-units-of-measure.org/resource/om-2/")

OM2_UNIT_MAP: dict[str, URIRef] = {
    "c": OM2.degreeCelsius,
    "°c": OM2.degreeCelsius,
    "degc": OM2.degreeCelsius,
    "degree celsius": OM2.degreeCelsius,
    "degrees celsius": OM2.degreeCelsius,
    "k": OM2.kelvin,
    "kelvin": OM2.kelvin,
    "c/min": OM2.degreeCelsiusPerMinute,
    "°c/min": OM2.degreeCelsiusPerMinute,
    "°c per min": OM2.degreeCelsiusPerMinute,
    "degree celsius per minute": OM2.degreeCelsiusPerMinute,
    "c/h": OM2.degreeCelsiusPerHour,
    "°c/h": OM2.degreeCelsiusPerHour,
    "°c per h": OM2.degreeCelsiusPerHour,
    "degree celsius per hour": OM2.degreeCelsiusPerHour,
    "s": OM2.second,
    "sec": OM2.second,
    "second": OM2.second,
    "seconds": OM2.second,
    "min": OM2.minute,
    "mins": OM2.minute,
    "minute": OM2.minute,
    "minutes": OM2.minute,
    "h": OM2.hour,
    "hr": OM2.hour,
    "hrs": OM2.hour,
    "hour": OM2.hour,
    "hours": OM2.hour,
    "pa": OM2.pascal,
    "kpa": OM2.kilopascal,
    "bar": OM2.bar,
    "mbar": OM2.millibar,
    "atm": OM2.standardAtmosphere,
    "torr": OM2.torr,
    "l": OM2.litre,
    "liter": OM2.litre,
    "litre": OM2.litre,
    "ml": OM2.millilitre,
    "ul": OM2.microlitre,
    "µl": OM2.microlitre,
    "%": OM2.percent,
    "percent": OM2.percent,
}


def normalize_om2_unit_alias(unit: str) -> str:
    """Normalize a source unit label before fixed-map lookup."""
    text = str(unit or "").strip().casefold()
    return re.sub(r"\s+", " ", text)


def resolve_om2_unit(unit: str) -> URIRef:
    """Resolve a supported source unit label to an OM-2 unit IRI."""
    if str(unit or "").strip().startswith(("http://", "https://")):
        return URIRef(str(unit).strip())
    normalized = normalize_om2_unit_alias(unit)
    resolved = OM2_UNIT_MAP.get(normalized)
    if resolved is None:
        raise ValueError(
            f"Unsupported OM-2 unit label {unit!r}; allowed aliases: "
            + ", ".join(sorted(OM2_UNIT_MAP))
        )
    return resolved


def parse_om2_quantity_label(label: str) -> tuple[float, str]:
    """Parse a compact source quantity such as ``150 °C``."""
    text = str(label or "").strip()
    match = re.fullmatch(r".*?([-+]?\d+(?:\.\d+)?)\s*([^,;0-9]+)", text)
    if not match:
        raise ValueError(f"OM-2 quantity label must contain a number and unit: {label!r}")
    return float(match.group(1)), match.group(2).strip()


def find_or_create_om2_quantity(
    graph: Graph,
    *,
    quantity_class: URIRef,
    label: str,
    value: int | float | str,
    unit: str,
    mint_iri: Callable[[str, str], URIRef],
) -> URIRef:
    """Reuse or create a quantity by class, numeric value, and resolved unit."""
    numeric_value = float(value)
    unit_iri = resolve_om2_unit(unit)
    numeric_literal = Literal(numeric_value, datatype=XSD.double)
    for subject in graph.subjects(RDF.type, quantity_class):
        if (
            (subject, OM2.hasNumericalValue, numeric_literal) in graph
            and (subject, OM2.hasUnit, unit_iri) in graph
        ):
            return URIRef(subject)

    iri = mint_iri(str(quantity_class).rsplit("/", 1)[-1], str(label))
    graph.add((iri, RDF.type, quantity_class))
    graph.add((iri, RDFS.label, Literal(str(label).strip())))
    graph.add((iri, OM2.hasNumericalValue, numeric_literal))
    graph.add((iri, OM2.hasUnit, unit_iri))
    return iri


def find_or_create_om2_quantity_from_label(
    graph: Graph,
    *,
    quantity_class: URIRef,
    label: str,
    mint_iri: Callable[[str, str], URIRef],
) -> URIRef:
    """Convenience adapter for generated code that carries compact labels."""
    value, unit = parse_om2_quantity_label(label)
    return find_or_create_om2_quantity(
        graph,
        quantity_class=quantity_class,
        label=label,
        value=value,
        unit=unit,
        mint_iri=mint_iri,
    )
