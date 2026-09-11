"""Emit public occurrence MCP main.py."""

from __future__ import annotations

import json
from typing import Any, Mapping

from src.kg_building_mcp_generation.emit.sidecars import _ontology_symbol
from src.kg_building_mcp_generation.overlay import ACTIVE_SURFACE
from src.kg_building_mcp_generation.overlay.instruction import (
    CREATE_OM2_DOC,
    CREATE_OM2_FRESH_MESSAGE,
    CREATE_OM2_RECOVERY_HINT,
    CREATE_OM2_REPLAY_MESSAGE,
    compile_overlay_instruction,
)
from src.kg_building_mcp_generation.overlay.quantity_surface import (
    compile_quantity_surface,
)


def emit_occurrence_main(
    context: Any,
    compiled: Mapping[str, Any] | None = None,
) -> str:
    units = compiled or (getattr(context, "contract", {}) or {}).get(
        "occurrence_surface_units"
    ) or {}
    surface = ACTIVE_SURFACE or compile_quantity_surface(units, context=context)
    if not ACTIVE_SURFACE:
        ACTIVE_SURFACE.clear()
        ACTIVE_SURFACE.update(surface)
    ontology = _ontology_symbol(context.ontology.name)
    instruction = compile_overlay_instruction(units, surface)
    instruction_literal = json.dumps(instruction)
    create_regs = "\n".join(
        f"mcp.tool(name={str(item.get('name') or '')!r})("
        f"rdf_runtime.wrap_public_tool(operations.{item.get('name')}))"
        for item in units.get("public_tools") or []
    )
    link_regs = "\n".join(
        f"mcp.tool(name={str(item.get('name') or '')!r})("
        f"rdf_runtime.wrap_public_tool(operations.{item.get('name')}))"
        for item in units.get("public_linkers") or []
    )
    om2_block = ""
    if surface.get("facets"):
        om2_block = f'''
_FRESH_OM2_MESSAGE = {CREATE_OM2_FRESH_MESSAGE!r}
_REPLAY_OM2_MESSAGE = {CREATE_OM2_REPLAY_MESSAGE!r}
_NEXT_OM2 = "optional_link_om2_quantity_or_use_compact_label_on_create"


def create_om2_quantity(quantity_class_iri: str, label: str) -> str:
    existing = operations.unattached_om2_quantity_iri(quantity_class_iri, label)
    if existing:
        return rdf_runtime.success_json(
            iri=existing,
            occurrence_local=True,
            already_committed=True,
            graph_changed=False,
            message=_REPLAY_OM2_MESSAGE,
            next_action=_NEXT_OM2,
        )
    raw = rdf_runtime.create_om2_quantity(quantity_class_iri, label)
    try:
        parsed = json.loads(raw)
    except Exception:
        return raw
    if str(parsed.get("status") or "").lower() == "ok":
        iri = str(parsed.get("iri") or "").strip()
        if iri:
            operations.remember_unattached_om2_quantity(
                quantity_class_iri, label, iri
            )
        parsed["message"] = _FRESH_OM2_MESSAGE
        parsed["already_committed"] = False
        parsed["graph_changed"] = True
        parsed["next_action"] = _NEXT_OM2
        return json.dumps(parsed, ensure_ascii=False)
    parsed["skippable"] = False
    parsed["retryable"] = True
    parsed.setdefault(
        "recovery",
        {{
            "action": "revise_label_or_unit",
            "hint": {CREATE_OM2_RECOVERY_HINT!r},
        }},
    )
    return json.dumps(parsed, ensure_ascii=False)


create_om2_quantity.__doc__ = {CREATE_OM2_DOC!r}
mcp.tool(name="create_om2_quantity")(
    rdf_runtime.wrap_public_tool(create_om2_quantity)
)
mcp.tool(name="link_om2_quantity")(
    rdf_runtime.wrap_public_tool(operations.link_om2_quantity)
)
'''
    return f'''from __future__ import annotations

"""Generated occurrence MCP surface. Overlay generator: compact quantity lexemes."""

import json

from fastmcp import FastMCP

from . import _fixed_rdf_runtime as rdf_runtime
from . import {ontology}_occurrence_operations as operations


mcp = FastMCP(name={context.ontology.name + "-occurrence-surface"!r})


@mcp.prompt(name="instruction")
def instruction() -> str:
    return {instruction_literal}


def export_memory(doi: str, top_level_entity_name: str) -> str:
    result_json = operations.prepare_export_graph()
    try:
        parsed = json.loads(result_json)
    except Exception:
        return rdf_runtime.error_json(
            code="ordered_member_integrity_failed",
            message="Invalid ordered-member check result.",
        )
    if str(parsed.get("status", "")).lower() != "ok":
        return json.dumps(parsed)
    exported = json.loads(rdf_runtime.export_memory(doi, top_level_entity_name))
    exported["export_repairs"] = parsed
    return json.dumps(exported, ensure_ascii=False, sort_keys=True)


mcp.tool(name="init_memory")(rdf_runtime.init_memory)
mcp.tool(name="inspect_ordered_members")(operations.check_ordered_members)
mcp.tool(name="export_memory")(export_memory)
mcp.tool(name="skip_semantic_obligation")(operations.skip_semantic_obligation)
{om2_block}{create_regs}
{link_regs}


if __name__ == "__main__":
    mcp.run()
'''
