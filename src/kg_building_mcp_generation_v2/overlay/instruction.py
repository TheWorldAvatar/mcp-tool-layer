"""Generic instruction templates plus T-Box-filled quantity tables."""

from __future__ import annotations

from typing import Any, Mapping

from src.kg_building_mcp_generation_v2.overlay.quantity_surface import (
    class_short_example_lines,
    facet_table_lines,
)
from src.kg_building_mcp_generation_v2.surface.compile import (
    compile_fallback_instruction,
)

COMPACT_QUANTITY_POLICY = (
    "Only the compiled quantity arguments listed below take compact source text "
    "on their owning create_* call. Numeric labels use '<number> <unit>'. "
    "The unit must be one compiled alias; source sentences are rejected. "
    "Do not substitute a sibling quantity class. Do not pass an IRI into those "
    "listed arguments. "
    "Measured values that are not in that compiled list still follow nested "
    "ownership paths; mint them with create_om2_quantity when that is the compiled "
    "path. "
    "create_om2_quantity remains available to mint a standalone quantity individual; "
    "its returned IRI is not a listed create_* argument. "
    "If a compact lexeme is invalid for a listed argument's class, the whole create_* "
    "is rejected (skippable false). Read expected_class_local, example_labels, "
    "allowed_unit_aliases, and move_value_to_facets from the error, correct the "
    "string, and retry the same create_* call. Never omit a listed facet that the "
    "heading supports, and never skip a quantity rejection. If create_* already "
    "returned already_committed, retry that same create_* with the missing compact "
    "labels so they can be attached. "
    "link_om2_quantity(owner_iri, facet, quantity_iri) is only for attaching an "
    "already-minted IRI: owner_iri is the create_* occurrence IRI except facets "
    "compiled as bound-root quantities, which use the bound root."
)

QUANTITY_SKIP_POLICY = (
    "Never skip a rejected owner creation or a quantity rejection."
)

TOOL_QUANTITY_SUFFIX = (
    " Compiled quantity fields on this same call take compact source text: {rows}. "
    "Do not pass an IRI. A lexeme that is not valid for that field rejects the "
    "whole {tool_name}; read example_labels and retry. If this occurrence already "
    "exists, retry this call with the missing compact labels."
)

CREATE_OM2_DOC = (
    "Mint a standalone quantity individual. Use this for measured values that are "
    "not compiled compact arguments on create_*. Listed create_* quantity fields "
    "still want compact labels, not this IRI. Numeric labels use '<number> <unit>'. "
    "Allowed classes and examples are compiled from the T-Box range table. "
    "Listed create_* quantity fields reject this IRI; use link_om2_quantity only "
    "after the occurrence already exists. Repeating this call with the same class "
    "and label while the IRI is still unattached returns the same IRI and is not "
    "progress. If the parser rejects the label, revise it from the structured "
    "error; do not skip."
)

CREATE_OM2_FRESH_MESSAGE = (
    "Standalone quantity minted. Listed create_* quantity arguments still want "
    "compact labels, not this IRI. For measured values that are not those compiled "
    "arguments, keep this IRI and attach it through the compiled nested path. "
    "Use link_om2_quantity only if that occurrence already exists and you must "
    "attach this IRI. Repeating create_om2_quantity with the same class and label "
    "is not progress."
)

CREATE_OM2_REPLAY_MESSAGE = (
    "This unattached quantity IRI already exists. Do not mint again. Listed "
    "create_* quantity arguments still want compact labels, not this IRI. "
    "For other measured values, keep this IRI."
)

CREATE_OM2_RECOVERY_HINT = (
    "Numeric labels use '<number> <unit>'. Use an allowed unit alias from the "
    "error message. Do not skip this failure. Compact text is only for compiled "
    "create_* quantity arguments; other measured values still use this mint."
)

LINK_OM2_DOC = (
    "Attach one create_om2_quantity IRI to an occurrence. owner_iri is the "
    "create_* occurrence IRI except facets compiled as bound-root quantities, "
    "which use the bound root. Do not pass a compact quantity label; mint first "
    "with create_om2_quantity."
)


def _without_original_quantity_skip(instruction: str) -> str:
    drop_from = "A failed optional quantity facet is omitted"
    drop_to = "Never skip a rejected owner creation."
    start = instruction.find(drop_from)
    end = instruction.find(drop_to)
    if start < 0 or end < 0 or end < start:
        return instruction
    kept_tail = instruction[end:]
    prefix = instruction[:start].rstrip()
    return (prefix + " " + kept_tail).strip()


def compile_overlay_instruction(
    compiled: Mapping[str, Any],
    surface: Mapping[str, Any],
) -> str:
    operational = _without_original_quantity_skip(compile_fallback_instruction(compiled))
    operational = operational.replace(
        "Never skip a rejected owner creation.",
        QUANTITY_SKIP_POLICY,
    )
    if not (surface.get("facets") or {}):
        return operational
    table = "; ".join(facet_table_lines(surface))
    examples = "; ".join(class_short_example_lines(surface))
    owner_example = str(surface.get("compact_example_call") or "").strip()
    example_note = f" Derived example: {owner_example}." if owner_example else ""
    bound_root = [
        name
        for name, spec in (surface.get("facets") or {}).items()
        if spec.get("attach_to") == "bound_root"
    ]
    bound_note = ""
    if bound_root:
        bound_note = (
            " Bound-root quantity facets: "
            + ", ".join(f"`{name}`" for name in bound_root)
            + "."
        )
    quantity_block = (
        f"{COMPACT_QUANTITY_POLICY} Compiled compact arguments: {table}."
        f"{example_note} Range examples: {examples}.{bound_note} "
    )
    return quantity_block + operational


def tool_quantity_suffix(tool: Mapping[str, Any], surface: Mapping[str, Any]) -> str:
    name = str(tool.get("name") or "")
    classes = surface.get("classes") or {}
    rows: list[str] = []
    for group in ("quantities", "parent_quantities"):
        for item in tool.get(group) or []:
            facet = str(item.get("parameter") or "")
            range_iri = str(item.get("range_iri") or "")
            if not facet:
                continue
            info = classes.get(range_iri) or {}
            local = str(info.get("class_local") or range_iri.rsplit("/", 1)[-1])
            example = (info.get("example_labels") or ["<number> <unit>"])[0]
            rows.append(f"{facet}={example!r} ({local})")
    if not rows:
        return ""
    return TOOL_QUANTITY_SUFFIX.format(rows="; ".join(rows), tool_name=name)
