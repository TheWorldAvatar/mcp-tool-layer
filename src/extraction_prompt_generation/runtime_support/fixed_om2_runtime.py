"""Stable OM-2 graph helpers shared by generated ontology packages."""

from __future__ import annotations

import re
from collections.abc import Callable

from rdflib import Graph, Literal, Namespace, RDF, RDFS, URIRef
from rdflib.namespace import XSD

OM2 = Namespace("http://www.ontology-of-units-of-measure.org/resource/om-2/")

QUALITATIVE_TEMPERATURE_PRESETS: dict[str, str] = {
    "room temperature": "room temperature",
    "room temp": "room temperature",
    "ambient temperature": "room temperature",
    "ambient temp": "room temperature",
    "ambient": "room temperature",
    "rt": "room temperature",
}
QUALITATIVE_DURATION_PRESETS: dict[str, str] = {
    "overnight": "overnight",
    "brief": "brief",
    "briefly": "brief",
    "several weeks": "several weeks",
    "period of several weeks": "several weeks",
}
QUALITATIVE_PRESSURE_PRESETS: dict[str, str] = {
    "vacuum": "vacuum",
    "under vacuum": "vacuum",
    "reduced pressure": "reduced pressure",
    "under reduced pressure": "reduced pressure",
    "ambient pressure": "ambient pressure",
    "atmospheric pressure": "ambient pressure",
}
NUMBER_WORD_VALUES: dict[str, float] = {
    "a": 1.0,
    "an": 1.0,
    "one": 1.0,
    "two": 2.0,
    "three": 3.0,
    "four": 4.0,
    "five": 5.0,
    "six": 6.0,
    "seven": 7.0,
    "eight": 8.0,
    "nine": 9.0,
    "ten": 10.0,
    "eleven": 11.0,
    "twelve": 12.0,
}

OM2_UNIT_MAP: dict[str, URIRef] = {
    "c": OM2.degreeCelsius,
    "°c": OM2.degreeCelsius,
    "degc": OM2.degreeCelsius,
    "degree celsius": OM2.degreeCelsius,
    "degrees celsius": OM2.degreeCelsius,
    "f": OM2.degreeFahrenheit,
    "°f": OM2.degreeFahrenheit,
    "degf": OM2.degreeFahrenheit,
    "degree fahrenheit": OM2.degreeFahrenheit,
    "degrees fahrenheit": OM2.degreeFahrenheit,
    "k": OM2.kelvin,
    "kelvin": OM2.kelvin,
    "k/min": OM2.kelvinPerMinute,
    "k/h": OM2.kelvinPerHour,
    "c/min": OM2.degreeCelsiusPerMinute,
    "°c/min": OM2.degreeCelsiusPerMinute,
    "deg/min": OM2.degreeCelsiusPerMinute,
    "degc/min": OM2.degreeCelsiusPerMinute,
    "°c per min": OM2.degreeCelsiusPerMinute,
    "degree celsius per minute": OM2.degreeCelsiusPerMinute,
    "c/h": OM2.degreeCelsiusPerHour,
    "°c/h": OM2.degreeCelsiusPerHour,
    "deg/h": OM2.degreeCelsiusPerHour,
    "degc/h": OM2.degreeCelsiusPerHour,
    "°c per h": OM2.degreeCelsiusPerHour,
    "degree celsius per hour": OM2.degreeCelsiusPerHour,
    "c h-1": OM2.degreeCelsiusPerHour,
    "°c h-1": OM2.degreeCelsiusPerHour,
    "degc h-1": OM2.degreeCelsiusPerHour,
    "deg h-1": OM2.degreeCelsiusPerHour,
    "degc/h-1": OM2.degreeCelsiusPerHour,
    "cel/h": OM2.degreeCelsiusPerHour,
    "cel.h-1": OM2.degreeCelsiusPerHour,
    "cel h-1": OM2.degreeCelsiusPerHour,
    "c min-1": OM2.degreeCelsiusPerMinute,
    "°c min-1": OM2.degreeCelsiusPerMinute,
    "degc min-1": OM2.degreeCelsiusPerMinute,
    "deg min-1": OM2.degreeCelsiusPerMinute,
    "cel/min": OM2.degreeCelsiusPerMinute,
    "cel.min-1": OM2.degreeCelsiusPerMinute,
    "s": OM2.second,
    "sec": OM2.second,
    "second": OM2.second,
    "seconds": OM2.second,
    "ms": OM2.millisecond,
    "millisecond": OM2.millisecond,
    "milliseconds": OM2.millisecond,
    "min": OM2.minute,
    "mins": OM2.minute,
    "minute": OM2.minute,
    "minutes": OM2.minute,
    "h": OM2.hour,
    "hr": OM2.hour,
    "hrs": OM2.hour,
    "hour": OM2.hour,
    "hours": OM2.hour,
    "wk": OM2.week,
    "wks": OM2.week,
    "yr": OM2.year,
    "year": OM2.year,
    "years": OM2.year,
    "pa": OM2.pascal,
    "kpa": OM2.kilopascal,
    "mpa": OM2.megapascal,
    "bar": OM2.bar,
    "mbar": OM2.millibar,
    "atm": OM2.standardAtmosphere,
    "torr": OM2.torr,
    "mmhg": OM2.millimetreOfMercury,
    "mm hg": OM2.millimetreOfMercury,
    "psi": OM2.poundForcePerSquareInch,
    "l": OM2.litre,
    "liter": OM2.litre,
    "litre": OM2.litre,
    "ml": OM2.millilitre,
    "cc": OM2.cubicCentimetre,
    "cm3": OM2.cubicCentimetre,
    "cm³": OM2.cubicCentimetre,
    "dm3": OM2.litre,
    "dm³": OM2.litre,
    "ul": OM2.microlitre,
    "µl": OM2.microlitre,
    "μl": OM2.microlitre,
    "nl": OM2.nanolitre,
    "d": OM2.day,
    "day": OM2.day,
    "days": OM2.day,
    "week": OM2.week,
    "weeks": OM2.week,
    "month": OM2.month,
    "months": OM2.month,
    "%": OM2.percent,
    "percent": OM2.percent,
    "mol%": OM2.percent,
    "mol %": OM2.percent,
    "wt%": OM2.percent,
    "wt.%": OM2.percent,
    "weight%": OM2.percent,
    "vol%": OM2.percent,
    "v/v%": OM2.percent,
    "w/w%": OM2.percent,
}


def normalize_om2_unit_alias(unit: str) -> str:
    """Normalize a source unit label before fixed-map lookup."""
    text = (
        str(unit or "")
        .strip()
        .casefold()
        .replace("º", "°")
        .replace("\u030a", "°")
        .replace("℃", "°c")
        .replace("℉", "°f")
        .replace("−", "-")
        .replace("–", "-")
        .replace("⁻¹", "-1")
    )
    text = re.sub(r"%\s*(?=yield\b)", "% ", text)
    text = re.sub(r"\s*/\s*", "/", text)
    text = re.sub(r"/(?:hr|hrs|hour|hours)\b", "/h", text)
    text = re.sub(r"/(?:mins|minute|minutes)\b", "/min", text)
    text = re.sub(r"\b(?:hr|hrs|hour|hours)\s*-\s*1\b", "h-1", text)
    text = re.sub(r"\b(?:mins|minute|minutes)\s*-\s*1\b", "min-1", text)
    text = re.sub(r"\bdeg\s+c\b", "degc", text)
    text = re.sub(r"\bo\s*c\b", "°c", text)
    text = re.sub(r"\b(c|degc)\s+per\s+(?:h|hour)\b", r"\1/h", text)
    text = re.sub(r"\b(c|degc)\s+per\s+(?:min|minute)\b", r"\1/min", text)
    text = re.sub(r"(°c)\s+per\s+(?:h|hour)\b", r"\1/h", text)
    text = re.sub(r"(°c)\s+per\s+(?:min|minute)\b", r"\1/min", text)
    return re.sub(r"\s+", " ", text)


_NUMBER_TOKEN = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"
_UNIT_BOUNDARY = " \t()[]{}:-"
_GROUPING_CLAUSE_RE = re.compile(r"\([^()]*\)|\[[^\[\]]*\]")


def _strip_grouping_clauses(text: str) -> str:
    """Drop one level of ``(...)`` / ``[...]`` so nested amounts stay qualifiers."""
    return _GROUPING_CLAUSE_RE.sub(" ", text)


def _leading_unit_alias(unit: str) -> str | None:
    """Return a known unit token that begins ``unit``, if any."""
    normalized = normalize_om2_unit_alias(unit)
    for alias in sorted(OM2_UNIT_MAP, key=len, reverse=True):
        if normalized == alias:
            return alias
        if not normalized.startswith(alias):
            continue
        remainder = normalized[len(alias) :]
        if not remainder or remainder[0] in _UNIT_BOUNDARY or remainder[0] == "/":
            return alias
    return None


def _is_descriptive_unit_qualifier(remainder: str) -> bool:
    """True when leftover text is source prose, not a second quantity."""
    if not remainder:
        return True
    if remainder[0] not in _UNIT_BOUNDARY:
        return False
    core = re.sub(r"\s+", " ", _strip_grouping_clauses(remainder)).strip(
        _UNIT_BOUNDARY
    )
    if not core:
        return True
    if re.match(r"^(?:yield\s+)?based\s+(?:on|upon)\b", core):
        return True
    if re.match(r"^yield\b", core):
        after_yield = core[5:].strip()
        if not after_yield or re.match(r"^based\s+(?:on|upon)\b", after_yield):
            return True
        return re.search(rf"(?<![a-z]){_NUMBER_TOKEN}", after_yield) is None
    return re.search(rf"(?<![a-z]){_NUMBER_TOKEN}", core) is None


def _recognized_source_unit(unit: str) -> str | None:
    """Return a known unit while tolerating a descriptive source qualifier."""
    normalized = normalize_om2_unit_alias(unit)
    for alias in sorted(OM2_UNIT_MAP, key=len, reverse=True):
        if normalized == alias:
            return alias
        if not normalized.startswith(alias):
            continue
        remainder = normalized[len(alias) :]
        if _is_descriptive_unit_qualifier(remainder):
            return alias
    return None


def normalize_qualitative_quantity_label(label: str) -> str:
    """Normalize a controlled qualitative quantity term."""
    text = str(label or "").strip().casefold().replace("_", " ")
    text = re.sub(r"[-–—]+", " ", text)
    return re.sub(r"\s+", " ", text).strip(" .")


def resolve_qualitative_quantity_preset(
    quantity_class: URIRef, label: str
) -> str | None:
    """Resolve supported non-numeric terms without inventing a numeric value."""
    normalized = normalize_qualitative_quantity_label(label)
    if URIRef(quantity_class) == OM2.Temperature:
        return QUALITATIVE_TEMPERATURE_PRESETS.get(normalized)
    if URIRef(quantity_class) == OM2.Duration:
        preset = QUALITATIVE_DURATION_PRESETS.get(normalized)
        if preset is not None:
            return preset
        if normalized.startswith("until ") and len(normalized.split()) >= 2:
            return normalized
    if URIRef(quantity_class) == OM2.Pressure:
        return QUALITATIVE_PRESSURE_PRESETS.get(normalized)
    return None


def resolve_om2_unit(unit: str) -> URIRef:
    """Resolve a supported source unit label to an OM-2 unit IRI."""
    text = str(unit or "").strip()
    if text.startswith(("http://", "https://")):
        return URIRef(text)
    if text.lower().startswith(("om-2:", "om2:")):
        return OM2[text.split(":", 1)[1]]
    recognized = _recognized_source_unit(text)
    if recognized is not None:
        return OM2_UNIT_MAP[recognized]
    normalized = normalize_om2_unit_alias(unit)
    resolved = OM2_UNIT_MAP.get(normalized)
    if resolved is None:
        compact = re.sub(r"[^a-z0-9]", "", normalized)
        by_local = {
            str(iri).rsplit("/", 1)[-1].lower(): iri for iri in OM2_UNIT_MAP.values()
        }
        resolved = by_local.get(compact)
    if resolved is None:
        raise ValueError(
            f"Unsupported OM-2 unit label {unit!r}; allowed aliases: "
            + ", ".join(sorted(OM2_UNIT_MAP))
        )
    return resolved


def parse_om2_quantity_label(label: str) -> tuple[float, str]:
    """Parse a compact source quantity such as ``150 °C``.

    Prefer the leftmost recognized unit. Parenthetical mass/amount clauses
    such as ``(15 mg, 0.009 mmol)`` stay qualifiers, so a yield like
    ``18% yield (15 mg, 0.009 mmol) based on H2bdc`` keeps ``18 %`` instead
    of the later millimole fragment. A later percent is used only when the
    earlier number has no known unit, e.g. ``0.023 g (52% based on H2DCPP)``.
    Compound remainders such as ``2 h 30 min`` are still not truncated.
    """
    text = str(label or "").strip()
    first_unrecognized: tuple[float, str] | None = None
    for match in re.finditer(rf"({_NUMBER_TOKEN})", text):
        rest = text[match.end() :].strip()
        if not rest:
            continue
        value = float(match.group(1))
        alias = _recognized_source_unit(rest)
        if alias is not None:
            return value, alias
        if _leading_unit_alias(rest) is not None:
            return value, rest
        if first_unrecognized is None:
            first_unrecognized = (value, rest)
    if first_unrecognized is not None:
        return first_unrecognized
    word_pattern = "|".join(
        sorted(NUMBER_WORD_VALUES, key=len, reverse=True)
    )
    word_match = re.fullmatch(
        rf".*?\b({word_pattern})\s+([^,;0-9]+)",
        text,
        flags=re.IGNORECASE,
    )
    if word_match:
        unit = word_match.group(2).strip()
        alias = _recognized_source_unit(unit)
        return (
            NUMBER_WORD_VALUES[word_match.group(1).casefold()],
            alias or unit,
        )
    raise ValueError(
        f"OM-2 quantity label must contain a number and unit: {label!r}"
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
    """Create one occurrence-local quantity.

    The historical function name is retained for generated-package
    compatibility. Quantity values are descriptors of an owning occurrence,
    not globally reusable identities: equal class/value/unit combinations
    therefore still receive distinct IRIs.
    """
    numeric_value = float(value)
    unit_iri = resolve_om2_unit(unit)
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
    """Create one occurrence-local numeric or qualitative quantity.

    The historical function name is retained for generated-package
    compatibility; this function never searches for or reuses an existing
    quantity IRI.
    """
    qualitative_label = resolve_qualitative_quantity_preset(quantity_class, label)
    if qualitative_label is not None:
        iri = mint_iri(
            str(quantity_class).rsplit("/", 1)[-1],
            f"qualitative:{qualitative_label}",
        )
        graph.add((iri, RDF.type, quantity_class))
        graph.add((iri, RDFS.label, Literal(qualitative_label)))
        return iri

    value, unit = parse_om2_quantity_label(label)
    return find_or_create_om2_quantity(
        graph,
        quantity_class=quantity_class,
        label=label,
        value=value,
        unit=unit,
        mint_iri=mint_iri,
    )
