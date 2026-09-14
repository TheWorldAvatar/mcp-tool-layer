"""Table-driven compact OM-2 matcher.

Accepts ``<number><optional space><unit alias>``. Unit lookup is exact, then a
generic whitespace/slash/casefold fold against the compiled alias table.
Quantity-class membership comes from the compiled T-Box map.
"""

from __future__ import annotations

import re
from collections.abc import Callable

from rdflib import Graph, Literal, Namespace, RDF, RDFS, URIRef
from rdflib.namespace import XSD

OM2 = Namespace("http://www.ontology-of-units-of-measure.org/resource/om-2/")

# EMIT_TABLES_BEGIN
OM2_UNIT_MAP: dict[str, URIRef] = {}
_UNIT_QUANTITY_CLASSES: dict[URIRef, frozenset[URIRef]] = {}
# EMIT_TABLES_END

_COMPACT_RE = re.compile(
    r"^\s*([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)\s*(.+?)\s*$"
)


def _normalize_unit(unit: str) -> str:
    text = str(unit or "").strip()
    text = text.replace("\u2212", "-").replace("\u2013", "-").replace("\u2014", "-")
    text = re.sub(r"\s*/\s*", "/", text)
    text = re.sub(r"\s+", " ", text).strip()
    if text.endswith(".") and not text.endswith("%."):
        text = text[:-1].rstrip()
    return text


def _alias_keys(unit: str) -> list[str]:
    raw = str(unit or "").strip()
    folded = _normalize_unit(raw).casefold()
    keys: list[str] = []
    for item in (raw, _normalize_unit(raw), folded):
        if item and item not in keys:
            keys.append(item)
    return keys


def _unit_iri_matches_class(unit_iri: URIRef, quantity_class: URIRef | None) -> bool:
    if quantity_class is None:
        return True
    allowed = _UNIT_QUANTITY_CLASSES.get(URIRef(unit_iri))
    if allowed is None:
        return False
    return URIRef(quantity_class) in allowed


def _alias_matches_class(alias: str, quantity_class: URIRef | None) -> bool:
    if quantity_class is None:
        return True
    unit_iri = OM2_UNIT_MAP.get(alias)
    if unit_iri is None:
        return False
    return _unit_iri_matches_class(unit_iri, quantity_class)


def _lookup_alias(unit: str, quantity_class: URIRef | None = None) -> str | None:
    for key in _alias_keys(unit):
        if key in OM2_UNIT_MAP and _alias_matches_class(key, quantity_class):
            return key
    return None


def _class_mismatch_message(unit_iri: URIRef, quantity_class: URIRef, source: str) -> str:
    allowed = _UNIT_QUANTITY_CLASSES.get(URIRef(unit_iri))
    actual = (
        str(next(iter(allowed))).rsplit("/", 1)[-1] if allowed else "unknown"
    )
    wanted = str(quantity_class).rsplit("/", 1)[-1]
    return (
        f"OM-2 unit {str(unit_iri).rsplit('/', 1)[-1]!r} is {actual}, "
        f"not valid for {wanted} (from {source!r})."
    )


def resolve_qualitative_quantity_preset(
    quantity_class: URIRef, label: str
) -> str | None:
    """Compact matcher has no qualitative presets."""
    return None


def resolve_om2_unit(unit: str, quantity_class: URIRef | None = None) -> URIRef:
    """Resolve a compact unit alias or IRI against the compiled tables."""
    text = str(unit or "").strip()
    resolved: URIRef | None = None
    if text.startswith(("http://", "https://")):
        resolved = URIRef(text)
    elif text.lower().startswith(("om-2:", "om2:")):
        resolved = OM2[text.split(":", 1)[1]]
    else:
        alias = _lookup_alias(text, quantity_class)
        if alias is not None:
            resolved = OM2_UNIT_MAP[alias]
        elif quantity_class is not None:
            unscoped = _lookup_alias(text, None)
            if unscoped is not None:
                raise ValueError(
                    _class_mismatch_message(
                        OM2_UNIT_MAP[unscoped], URIRef(quantity_class), unit
                    )
                )
    if resolved is None:
        raise ValueError(
            f"Unsupported OM-2 unit label {unit!r}; allowed aliases: "
            + ", ".join(sorted(OM2_UNIT_MAP))
        )
    if not _unit_iri_matches_class(resolved, quantity_class):
        raise ValueError(
            _class_mismatch_message(resolved, URIRef(quantity_class), unit)
        )
    return resolved


def parse_om2_quantity_label(
    label: str, quantity_class: URIRef | None = None
) -> tuple[float, str]:
    """Parse a compact ``<number> <unit>`` label."""
    match = _COMPACT_RE.match(str(label or ""))
    if match is None:
        raise ValueError(
            f"OM-2 quantity label must be compact '<number> <unit>': {label!r}"
        )
    value = float(match.group(1))
    unit_raw = match.group(2)
    alias = _lookup_alias(unit_raw, quantity_class)
    if alias is not None:
        return value, alias
    if quantity_class is not None:
        unscoped = _lookup_alias(unit_raw, None)
        if unscoped is not None:
            raise ValueError(
                _class_mismatch_message(
                    OM2_UNIT_MAP[unscoped], URIRef(quantity_class), label
                )
            )
    raise ValueError(
        f"Unsupported OM-2 unit label {unit_raw!r}; allowed aliases: "
        + ", ".join(sorted(OM2_UNIT_MAP))
    )


def find_or_create_om2_quantity(
    graph: Graph,
    *,
    quantity_class: URIRef,
    label: str,
    value: int | float | str,
    unit: str,
    mint_iri: Callable[[str, str], URIRef],
) -> URIRef:
    numeric_value = float(value)
    unit_iri = resolve_om2_unit(unit, quantity_class)
    numeric_literal = Literal(numeric_value, datatype=XSD.double)
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
    value, unit = parse_om2_quantity_label(label, quantity_class)
    return find_or_create_om2_quantity(
        graph,
        quantity_class=quantity_class,
        label=label,
        value=value,
        unit=unit,
        mint_iri=mint_iri,
    )
