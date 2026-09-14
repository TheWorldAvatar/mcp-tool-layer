"""Emit flattened ``_fixed_om2_runtime.py`` from a compiled quantity surface."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Mapping

from rdflib import URIRef

from src.kg_building_mcp_generation_v2.overlay.quantity_surface import (
    OM2_NS,
    compile_om2_runtime_tables,
)

_COMPACT_SOURCE = Path(__file__).resolve().parent / "om2_compact.py"
_TABLES_RE = re.compile(
    r"# EMIT_TABLES_BEGIN\r?\n[\s\S]*?# EMIT_TABLES_END\r?\n",
)


def _local(iri: object) -> str:
    text = str(iri or "")
    if "#" in text:
        return text.rsplit("#", 1)[-1]
    return text.rsplit("/", 1)[-1]


def _tables_from_surface(
    surface: Mapping[str, Any],
) -> tuple[dict[str, URIRef], dict[URIRef, frozenset[URIRef]]]:
    aliases = {
        str(alias): str(target)
        for alias, target in (surface.get("source_unit_aliases") or {}).items()
        if str(alias).strip() and str(target).strip()
    }
    unit_local_to_class = {
        str(local): str(class_iri)
        for local, class_iri in (surface.get("unit_local_to_class") or {}).items()
        if str(local).strip() and str(class_iri).strip()
    }
    if not aliases or not unit_local_to_class:
        return compile_om2_runtime_tables()
    missing = sorted(
        {target for target in aliases.values() if target not in unit_local_to_class}
    )
    if missing:
        raise ValueError(
            "OM-2 alias table names unit individuals that are not in the T-Box: "
            + ", ".join(missing)
        )
    unit_map = {alias: URIRef(OM2_NS + target) for alias, target in aliases.items()}
    quantity_classes = {
        URIRef(OM2_NS + local): frozenset({URIRef(class_iri)})
        for local, class_iri in unit_local_to_class.items()
    }
    return unit_map, quantity_classes


def emit_om2_table_source(surface: Mapping[str, Any]) -> str:
    unit_map, quantity_classes = _tables_from_surface(surface)
    alias_rows = sorted(
        ((alias, _local(iri)) for alias, iri in unit_map.items()),
        key=lambda item: (item[1], item[0]),
    )
    class_rows = sorted(
        (
            _local(unit_iri),
            _local(next(iter(classes))),
        )
        for unit_iri, classes in quantity_classes.items()
    )
    lines = [
        "OM2_UNIT_MAP: dict[str, URIRef] = {",
        *[f"    {alias!r}: OM2.{target}," for alias, target in alias_rows],
        "}",
        "",
        "_UNIT_QUANTITY_CLASSES: dict[URIRef, frozenset[URIRef]] = {",
        *[
            f"    OM2.{unit_local}: frozenset({{OM2.{class_local}}}),"
            for unit_local, class_local in class_rows
        ],
        "}",
        "",
    ]
    return "\n".join(lines)


def emit_om2_runtime(surface: Mapping[str, Any]) -> str:
    """Inline compiled tables into the generic compact matcher."""
    text = _COMPACT_SOURCE.read_text(encoding="utf-8")
    if _TABLES_RE.search(text) is None:
        raise RuntimeError(f"{_COMPACT_SOURCE} is missing EMIT_TABLES markers.")
    replacement = "# EMIT_TABLES_BEGIN\n" + emit_om2_table_source(surface) + "# EMIT_TABLES_END\n"
    text = _TABLES_RE.sub(replacement, text, count=1)
    leftover = (
        "from .om2_compile import",
        "compile_om2_runtime_tables",
        "extraction_prompt_generation.runtime_support.om2",
        "OM2_UNIT_MAP: dict[str, URIRef] = {}",
    )
    for token in leftover:
        if token in text:
            raise RuntimeError(f"{_COMPACT_SOURCE} still contains {token} after flattening.")
    return text
