"""OX-native rendering of the official Pipeline occurrence surface.

This is the strict no-prompt system text: original OntoLogX generic graph
rules plus ownership / attachment / occurrence protocol. It must not add
TBox comments or construction recipes that the official Pipeline KG agent
does not see in its MCP instruction + tool descriptions.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from prompt_builder import OX_GENERIC_GRAPH_RULES


HERE = Path(__file__).resolve().parent
SURFACE_PATH = HERE / "resources" / "pipeline_occurrence_surface_ox.json"

_OPERATIONAL_FRAME = """# Role
Materialize one ChemicalSynthesis graph from the supplied SEMANTIC_HINTS ledger.
Emit the graph through the SynthesisGraph tool (nodes + relationships).
Do not emit MCP tool calls, SEMANTIC_HINTS ledgers, or prose outside the tool call.
There is no paper body in this session.

""" + OX_GENERIC_GRAPH_RULES + """

# Occurrence protocol
This is the OntoLogX form of the official Pipeline occurrence surface.
Read each occurrence heading in the ledger exactly once and emit one node of that heading's owner class.
Owner classes: Add, ChemicalInput, ChemicalOutput, Crystallize, Dry, Evaporate, ExecutionPoint, Filter, HeatChill, Separate, Sonicate, Stir, Transfer.
Headings of different owner classes remain distinct occurrences even when their labels match.
Put every supported detail from that heading onto that same occurrence through the ownership map below. Do not split those details onto a later node.
Empty or sentinel optional labels mean that facet is absent.
The bound ChemicalSynthesis IRI in the human message is the parent/root for every owner occurrence that attaches to the root. Do not treat a child occurrence as that root.
Take each order value from the heading. Do not invent order positions.
A unique parent-owned occurrence is created once.
Occurrence owners and non-reusable dependents are always fresh.

# Reusable descriptors
Reusable classes are resolved from the human-message inventories when used: {reusable_classes}.
If a listed reusable entity is used, keep its listed id. Do not mint a second id for that same reusable thing.

# Root-level links
The only root-level label-resolved links are: {root_linkers}.
Their subject is the bound ChemicalSynthesis. Object labels come from the heading or inventory.

# Whole-graph emission
The human message may group complementary ITER2 / ITER3 / ITER4 views of one bound ChemicalSynthesis.
Emit the complete graph for this ChemicalSynthesis in every SynthesisGraph call, including every correction round.
Both top-level fields, nodes and relationships, must always be present.
""".strip()


def load_pipeline_surface(path: Path | None = None) -> dict[str, Any]:
    payload = json.loads((path or SURFACE_PATH).read_text(encoding="utf-8"))
    if str(payload.get("schema_version") or "") != "ox-strict-noprompt-surface.v1":
        raise RuntimeError(f"Unexpected occurrence surface schema: {path or SURFACE_PATH}")
    return payload


def _group_nested(items: list[dict[str, Any]]) -> list[tuple[str, list[dict[str, Any]]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    order: list[str] = []
    for item in items:
        path = str(item.get("owner_path") or "")
        hop = path.split(".", 1)[-1].split(".", 1)[0] if path.startswith("self.") else path
        if hop not in grouped:
            grouped[hop] = []
            order.append(hop)
        grouped[hop].append(item)
    return [(key, grouped[key]) for key in order]


def render_ownership_map(surface: dict[str, Any] | None = None) -> str:
    payload = surface or load_pipeline_surface()
    blocks: list[str] = [
        "# Ownership and attachment",
        "This map is the official Pipeline tool-description surface, rewritten for whole-graph emission.",
        "Attach a listed facet only when the heading supplies it.",
        "Nested ownership means: emit the related node and the relationship from this occurrence.",
        "Do not pass a bare ontology property name unless that exact name is listed as a facet on this occurrence.",
    ]
    for owner in payload.get("owner_occurrences") or []:
        name = str(owner.get("owner_class") or "")
        lines = [f"## {name}"]
        if owner.get("parent_is_bound_root") and owner.get("parent_predicate"):
            lines.append(
                f"Parent: attach this occurrence to the bound ChemicalSynthesis root via {owner['parent_predicate']}."
            )
        elif owner.get("parent_predicate"):
            lines.append(
                f"Parent: attach this occurrence to the owner named in the heading via {owner['parent_predicate']}."
            )
        else:
            lines.append("Parent: this occurrence has no parent attachment.")
        if owner.get("ordered"):
            lines.append("Identity of this occurrence includes its hasOrder from the heading.")
        facets = [str(item) for item in owner.get("self_facets") or [] if item]
        if facets:
            lines.append("Facets on this occurrence: " + ", ".join(facets) + ".")
        nested = _group_nested(list(owner.get("nested_ownership") or []))
        if nested:
            lines.append("Nested ownership:")
            for hop, items in nested:
                props = []
                for item in items:
                    prop = str(item.get("property") or "")
                    if prop and prop not in props:
                        props.append(prop)
                lines.append(
                    f"- {hop}: related node owned by this {name}; "
                    f"properties on that node: {', '.join(props)}."
                )
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def build_strict_noprompt_system_prompt(path: Path | None = None) -> str:
    surface = load_pipeline_surface(path)
    reusable = ", ".join(str(item) for item in surface.get("reusable_classes") or [])
    linkers = ", ".join(
        str(item.get("predicate") or "")
        for item in surface.get("root_linkers") or []
        if item.get("predicate")
    )
    frame = _OPERATIONAL_FRAME.format(
        reusable_classes=reusable or "(none)",
        root_linkers=linkers or "(none)",
    )
    return frame + "\n\n" + render_ownership_map(surface) + "\n"
