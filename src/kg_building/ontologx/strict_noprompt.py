"""OX-native rendering of the official Pipeline occurrence surface.

This is the strict no-prompt system text: original OntoLogX generic graph
rules plus ownership / attachment / occurrence protocol. It must not add
TBox comments or construction recipes that the official Pipeline KG agent
does not see in its MCP instruction + tool descriptions.

OntoSynthesis occurrence is hardcoded as whole-graph hops (the Pipeline MCP
expander's dual encoding). generic-strict, generic-noprompt, and with-prompt
share that text. It does not switch on protocol name. Medical and extension
surfaces keep the literal occurrence copy. The locked JSON is unchanged.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from prompt_builder import OX_GENERIC_GRAPH_RULES, generic_graph_rules


HERE = Path(__file__).resolve().parent
SURFACE_PATH = HERE / "resources" / "pipeline_occurrence_surface_ox.json"
ONTOSPECIES_SURFACE_PATH = HERE / "resources" / "ontospecies_occurrence_surface_ox.json"
ONTOMOPS_SURFACE_PATH = HERE / "resources" / "ontomops_occurrence_surface_ox.json"
MEDICAL_SURFACE_PATH = HERE / "resources" / "medical_occurrence_surface_ox.json"

_MAIN_FRAME = """# Role
Materialize one ChemicalSynthesis graph from the supplied SEMANTIC_HINTS ledger.
Emit the graph through the SynthesisGraph tool (nodes + relationships).
Do not emit MCP tool calls, SEMANTIC_HINTS ledgers, or prose outside the tool call.
There is no paper body in this session.

"""

_EXTENSION_FRAME = """# Role
Materialize the extension layer for the bound ChemicalSynthesis from the supplied ledger and inherited main TTL.
Emit the graph through the SynthesisGraph tool (nodes + relationships).
Do not emit MCP tool calls, SEMANTIC_HINTS ledgers, or prose outside the tool call.
Reuse seeded target ids from the enrichment binding and inherited TTL. Do not mint a replacement root or rebuild synthesis steps.

"""

_MEDICAL_FRAME = """# Role
Materialize one MedicalCase graph from the supplied ledger.
Emit the graph through the SynthesisGraph tool (nodes + relationships).
Do not emit MCP tool calls, SEMANTIC_HINTS ledgers, or prose outside the tool call.
There is no paper body in this session.

"""

_OCCURRENCE_PROTOCOL = """# Occurrence protocol
This is the OntoLogX form of the official Pipeline occurrence surface.
Read each occurrence heading in the ledger exactly once and emit one node of that heading's owner class.
Owner classes: {owner_classes}.
Headings of different owner classes remain distinct occurrences even when their labels match.
Put every supported detail from that heading onto that same occurrence through the ownership map below. Do not split those details onto a later node.
Empty or sentinel optional labels mean that facet is absent.
The bound {bound_local} IRI in the human message is the parent/root for every owner occurrence that attaches to the root. Do not treat a child occurrence as that root.
Take each order value from the heading. Do not invent order positions.
A unique parent-owned occurrence is created once.
Occurrence owners and non-reusable dependents are always fresh.

# Reusable descriptors
Reusable classes are resolved from the human-message inventories when used: {reusable_classes}.
If a listed reusable entity is used, keep its listed id. Do not mint a second id for that same reusable thing.

# Root-level links
The only root-level label-resolved links are: {root_linkers}.
Their subject is the bound {bound_local}. Object labels come from the heading or inventory.

# Whole-graph emission
The human message may group complementary views of one bound {bound_local}.
Emit the complete graph for this {bound_local} in every SynthesisGraph call, including every correction round.
Both top-level fields, nodes and relationships, must always be present.
"""

_OCCURRENCE_PROTOCOL_HOPS = """# Occurrence protocol
This is the OntoLogX form of the official Pipeline occurrence surface, rewritten for whole-graph hops.
Read each occurrence heading in the ledger exactly once and emit one node of that heading's owner class.
Owner classes: {owner_classes}.
Headings of different owner classes remain distinct occurrences even when their labels match.
Put only datatype self-facets on this occurrence's node. Nested hops must be a child node plus a relationship. Do split those hops onto later nodes; do not copy hop names onto this occurrence's properties.
A ledger key such as hasAddedChemicalInput, hasWashingSolvent, or hasTargetTemperature is the hop / relationship name even without a _label suffix. Emit the nested node and that relationship.
Empty or sentinel optional labels mean that facet is absent.
The bound {bound_local} IRI in the human message is the parent/root for every owner occurrence that attaches to the root. Do not treat a child occurrence as that root.
Take each order value from the heading. Do not invent order positions.
A unique parent-owned occurrence is created once.
Occurrence owners and non-reusable dependents are always fresh.

# Reusable descriptors
Reusable classes are resolved from the human-message inventories when used: {reusable_classes}.
If a listed reusable entity is used, keep its listed id. Do not mint a second id for that same reusable thing.

# Root-level links
Root-level links include: {root_linkers}.
Label-resolved reusable objects use inventory ids. hasYield is a measure hop from the bound {bound_local} to om-2:AmountOfSubstanceFraction, never a ChemicalOutput property.

# Whole-graph emission
The human message may group complementary views of one bound {bound_local}.
Emit the complete graph for this {bound_local} in every SynthesisGraph call, including every correction round.
Both top-level fields, nodes and relationships, must always be present.
"""


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


def render_ownership_map(
    surface: dict[str, Any] | None = None,
    *,
    hops_style: bool = False,
) -> str:
    payload = surface or load_pipeline_surface()
    bound_local = str(payload.get("bound_root") or "ontosyn:ChemicalSynthesis").split(":")[-1]
    intro = [
        "# Ownership and attachment",
        "This map is the official Pipeline tool-description surface, rewritten for whole-graph emission.",
        "Attach a listed facet only when the heading supplies it.",
        "Nested ownership means: emit the related node and the relationship from this occurrence.",
    ]
    if hops_style:
        intro.append(
            "A bare ledger predicate is a hop. Emit the child node and that "
            "relationship. Do not put that predicate on this occurrence's properties."
        )
    else:
        intro.append(
            "Do not pass a bare ontology property name unless that exact name is listed as a facet on this occurrence."
        )
    blocks: list[str] = intro
    for owner in payload.get("owner_occurrences") or []:
        name = str(owner.get("owner_class") or "")
        lines = [f"## {name}"]
        if owner.get("parent_is_bound_root") and owner.get("parent_predicate"):
            lines.append(
                f"Parent: attach this occurrence to the bound {bound_local} root via {owner['parent_predicate']}."
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
                qty_items = [
                    item
                    for item in items
                    if str(item.get("role") or "") == "nested_quantity"
                ]
                other_items = [
                    item
                    for item in items
                    if str(item.get("role") or "") != "nested_quantity"
                ]
                for item in qty_items:
                    range_class = str(item.get("range_class") or "om-2 measure")
                    prop = str(item.get("property") or hop)
                    if str(item.get("subject") or "") == "bound_root":
                        lines.append(
                            f"- {hop}: related {range_class} node; relationship {prop} "
                            f"from the bound {bound_local}, not from this {name}. "
                            f"Never put {hop} on this {name}'s properties."
                        )
                        continue
                    lines.append(
                        f"- {hop}: related {range_class} node owned by this {name}; "
                        f"relationship {prop} from this {name} to that node. "
                        "Put the ledger value on the measure node's rdfs:label "
                        "(and om-2:hasNumericalValue / om-2:hasUnit when numeric). "
                        f"Never put {hop} or the measure class name on this {name}'s properties."
                    )
                if other_items:
                    props = []
                    for item in other_items:
                        prop = str(item.get("property") or "")
                        if prop and prop not in props:
                            props.append(prop)
                    if hops_style:
                        from hops_ownership import CHILD_DATATYPES, NESTED_HOP_RANGES

                        range_class = next(
                            (
                                str(item.get("range_class") or "")
                                for item in other_items
                                if str(item.get("property") or "") == hop
                                and item.get("range_class")
                            ),
                            "",
                        ) or NESTED_HOP_RANGES.get(hop, "related node")
                        child_props = [item for item in props if item in CHILD_DATATYPES]
                        heading_keys = []
                        for item in other_items:
                            for key in item.get("heading_keys_on_child") or []:
                                if key not in heading_keys:
                                    heading_keys.append(str(key))
                        extra = ""
                        if heading_keys:
                            extra = (
                                f" Ledger keys {', '.join(heading_keys)} on this {name} "
                                f"heading belong on that {range_class}, not on this {name}'s properties."
                            )
                        child = (
                            f" Child datatype properties: {', '.join(child_props)}."
                            if child_props
                            else ""
                        )
                        lines.append(
                            f"- {hop}: emit a {range_class} node and relationship "
                            f"{name} --{hop}--> that node.{child}{extra} "
                            f"Never put {hop} on this {name}'s properties."
                        )
                    else:
                        lines.append(
                            f"- {hop}: related node owned by this {name}; "
                            f"properties on that node: {', '.join(props)}."
                        )
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def _render_frame(
    surface: dict[str, Any],
    *,
    role_frame: str,
    rules: str,
    protocol_template: str | None = None,
) -> str:
    reusable = ", ".join(str(item) for item in surface.get("reusable_classes") or [])
    linkers = ", ".join(
        str(item.get("predicate") or "")
        for item in surface.get("root_linkers") or []
        if item.get("predicate")
    )
    owners = ", ".join(
        str(item.get("owner_class") or "")
        for item in surface.get("owner_occurrences") or []
        if item.get("owner_class")
    )
    bound_local = str(surface.get("bound_root") or "ontosyn:ChemicalSynthesis").split(":")[-1]
    protocol = (protocol_template or _OCCURRENCE_PROTOCOL).format(
        owner_classes=owners or "(none)",
        bound_local=bound_local,
        reusable_classes=reusable or "(none)",
        root_linkers=linkers or "(none)",
    )
    return role_frame + rules + "\n\n" + protocol.strip()


def _ontosynthesis_hops_surface(path: Path | None = None) -> dict[str, Any]:
    """Locked MCP argument JSON rewritten for whole-graph hops. JSON file stays frozen."""
    from hops_ownership import apply_hops_ownership

    surface = apply_hops_ownership(load_pipeline_surface(path))
    if "bound_root" not in surface:
        surface = {**surface, "bound_root": "ontosyn:ChemicalSynthesis"}
    return surface


def build_strict_noprompt_system_prompt(path: Path | None = None) -> str:
    surface = _ontosynthesis_hops_surface(path)
    frame = _render_frame(
        surface,
        role_frame=_MAIN_FRAME,
        rules=OX_GENERIC_GRAPH_RULES,
        protocol_template=_OCCURRENCE_PROTOCOL_HOPS,
    )
    return frame + "\n\n" + render_ownership_map(surface, hops_style=True) + "\n"


def build_strict_noprompt_extension_prompt(domain: str) -> str:
    if domain == "ontospecies":
        path = ONTOSPECIES_SURFACE_PATH
        prefixes = "ontospecies:, periodic:, ontosyn:, rdfs:label"
        example = "ontospecies:Species instead of a generic product node"
    elif domain == "ontomops":
        path = ONTOMOPS_SURFACE_PATH
        prefixes = "ontomops:, ontosyn:, rdfs:label"
        example = "ontomops:MetalOrganicPolyhedron instead of a generic cage node"
    else:
        raise ValueError(f"Unsupported extension domain: {domain}")
    surface = load_pipeline_surface(path)
    rules = generic_graph_rules(
        bound_root="ontosyn:ChemicalSynthesis",
        prefixes=prefixes,
        specific_example=example,
    )
    frame = _render_frame(surface, role_frame=_EXTENSION_FRAME, rules=rules)
    return frame + "\n\n" + render_ownership_map(surface) + "\n"


def build_strict_noprompt_medical_prompt(path: Path | None = None) -> str:
    surface = load_pipeline_surface(path or MEDICAL_SURFACE_PATH)
    rules = generic_graph_rules(
        bound_root="medical:MedicalCase",
        prefixes="medical:, rdfs:label",
        specific_example="medical:Diagnosis instead of a generic finding node",
    )
    frame = _render_frame(surface, role_frame=_MEDICAL_FRAME, rules=rules)
    return frame + "\n\n" + render_ownership_map(surface) + "\n"


def build_generic_noprompt_system_prompt(path: Path | None = None) -> str:
    """Strict occurrence surface plus the shared Graph rules and T-Box handbook."""
    from src.kg_building.generic_noprompt_graph_rules import append_generic_noprompt_context

    return append_generic_noprompt_context(
        build_strict_noprompt_system_prompt(path),
        "generic-noprompt",
        ontology="ontosynthesis",
    )


def build_with_prompt_system_prompt(path: Path | None = None) -> str:
    """Strict occurrence surface plus frozen guidance and the T-Box handbook."""
    from src.kg_building.with_prompt_guidance import append_with_prompt_context

    return append_with_prompt_context(
        build_strict_noprompt_system_prompt(path),
        "with-prompt",
        ontology="ontosynthesis",
    )


def build_with_prompt_qty_system_prompt(path: Path | None = None) -> str:
    """with-prompt (already hops occurrence) plus the quantity dual-coding appendix."""
    from src.kg_building.with_prompt_guidance import append_with_prompt_context

    return append_with_prompt_context(
        build_strict_noprompt_system_prompt(path),
        "with-prompt-qty",
        ontology="ontosynthesis",
    )


def build_with_prompt_hops_system_prompt(path: Path | None = None) -> str:
    """with-prompt (already hops occurrence) plus the hops dual-coding appendix."""
    from src.kg_building.with_prompt_guidance import append_with_prompt_context

    return append_with_prompt_context(
        build_strict_noprompt_system_prompt(path),
        "with-prompt-hops",
        ontology="ontosynthesis",
    )


def build_generic_noprompt_medical_prompt(path: Path | None = None) -> str:
    """Medical occurrence surface plus medical Graph rules and T-Box handbook."""
    from src.kg_building.generic_noprompt_graph_rules import append_generic_noprompt_context

    return append_generic_noprompt_context(
        build_strict_noprompt_medical_prompt(path),
        "generic-noprompt",
        ontology="medical",
    )


def build_with_prompt_medical_prompt(path: Path | None = None) -> str:
    """Medical occurrence surface plus frozen OntoMed guidance and handbook."""
    from src.kg_building.with_prompt_guidance import append_with_prompt_context

    return append_with_prompt_context(
        build_strict_noprompt_medical_prompt(path),
        "with-prompt",
        ontology="medical",
    )
