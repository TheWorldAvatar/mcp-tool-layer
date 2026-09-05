"""Build the OntoLogX system prompt from our OntoSynthesis T-Box, not the log-domain prompt.

Pipeline EXTRACTION/KG_BUILDING files are iteration + MCP + JSON/ledger contracts.
Those conflict with OntoLogX structured-graph output, so the domain text comes from
`data/ontologies/ontosynthesis_parsed.md` (the same T-Box comments the pipeline uses).
"""

from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TBOX = REPO_ROOT / "data" / "ontologies" / "ontosynthesis_parsed.md"
DEFAULT_ONTOLOGX_PROMPT = (
    REPO_ROOT / "third_party" / "ontologx" / "resources" / "prompts" / "main.system.md"
)
FAITHFUL_ONTOLOGX_PROMPT = (
    Path(__file__).resolve().parent / "resources" / "ontosyn_faithful.system.md"
)
OFFICIAL_ONEPASS_OX_CONTRACT = (
    Path(__file__).resolve().parent / "resources" / "official_onepass_ox.contract.md"
)
# Original OntoLogX operational constraints, rewritten for a bound
# ChemicalSynthesis and SynthesisGraph. Shared by official no-prompt and
# strict no-prompt. No domain construction recipes.
OX_GENERIC_GRAPH_RULES = """# Generic OntoLogX graph rules
These are the original OntoLogX operational constraints, rewritten for one bound ChemicalSynthesis.

- Emit exactly one ontosyn:ChemicalSynthesis: the bound root named in the human message. Do not mint a replacement root.
- Use only types, properties, and relationships allowed by the ontology / SynthesisGraph schema. Do not invent new ones.
- Use the most specific type available for nodes and relationships, e.g. ontosyn:Add instead of ontosyn:SynthesisStep.
- Respect the appropriate casing and prefixes for types and properties (ontosyn:, om-2:, ontomops:, rdfs:label).
- Omit properties with empty or sentinel values.
- Infer allowed properties and relationships from the structural relations of each node type.
- The graph must be connected: every emitted node must be reachable from the bound ChemicalSynthesis through ontology relationships.
""".strip()


OUTPUT_CONTRACT = f"""# Role
You extract a complete OntoSynthesis knowledge graph from the provided source.
Use only the OntoSynthesis T-Box below. Ignore any cybersecurity / log-event ontology.

# Output contract for this runner
Emit the graph through the SynthesisGraph tool (nodes + relationships).
Do not emit SEMANTIC_HINTS ledgers, iteration JSON, MCP tool calls, or prose outside the tool call.

{OX_GENERIC_GRAPH_RULES}

Graph rules:
- At least one ontosyn:ChemicalSynthesis. Each is one MOP (discrete cage/polyhedron) producing workflow.
- Every ChemicalSynthesis has exactly one ontosyn:hasChemicalOutput and at least one ontosyn:hasSynthesisStep.
- Every ChemicalSynthesis must ontosyn:retrievedFrom a bibo:Document whose rdfs:label is the paper DOI.
- Allowed step types only: Add, Stir, HeatChill, Evaporate, Sonicate, Crystallize, Transfer, Separate, Filter, Dry.
- Never emit a node typed only as ontosyn:SynthesisStep; always use the concrete subclass.
- Every step (Add, Stir, HeatChill, ...) must have rdfs:label. Conversion and scoring cannot read a step that has type and order but no label.
- ontosyn:hasOrder starts at 1 and is unique and contiguous inside each ChemicalSynthesis.
- One Add owns exactly one ontosyn:hasAddedChemicalInput. A clause that names N materials is N Add nodes.
- ChemicalInput rdfs:label is identity only; put the amount on ontosyn:hasAmount.
- Attach vessels, atmospheres, temperatures, durations, washing solvents, and yields only when the source states them.
- Do not default atmosphere to air. Shared-context atmosphere (for example argon glovebox for a whole family) must be inherited onto covered steps via ontosyn:hasVesselEnvironment.
- A wash of retained solid is Filter with ontosyn:hasWashingSolvent, not Add.
- A heat-to-temperature-and-hold (including solvothermal 130 degC for 2 days) is HeatChill, not Stir, even if a vessel is named.
- Passive duration hold that yields crystals is HeatChill; do not also emit Crystallize for the crystalline result.
- Cooling / return-to-room-temperature is a separate HeatChill and must keep ontosyn:hasTargetTemperature.
- Measure nodes (om-2:Temperature, Duration, Pressure, Volume, TemperatureRate, AmountOfSubstanceFraction) need rdfs:label. When a number and unit are explicit, also set om-2:hasNumericalValue and om-2:hasUnit.
- om-2:hasUnit MUST be an OM-2 unit individual (om-2:degreeCelsius, om-2:hour, om-2:day, om-2:minute, om-2:degreeCelsiusPerHour, om-2:millilitre, om-2:percent, ...). Never a free-text string such as "degC", "degC h-1", or "days".
- Qualitative measures (room temperature, overnight) are label-only; do not invent a unit or numerical value.
- Use prefixed types and properties: ontosyn:*, om-2:*, ontomops:*, rdfs:label, ontomops:hasCCDCNumber.

# Authoritative OntoSynthesis T-Box
The following schema and comments are the same T-Box the project pipeline uses.
"""


PER_ENTITY_CONTRACT = """
# Current target (one ChemicalSynthesis)
The source may describe several related recipes. Extract ONLY the target
ChemicalSynthesis named in the human message.
- Emit exactly one ontosyn:ChemicalSynthesis for that target.
- Do not emit sibling syntheses, even if they share solvents or conditions.
- Shared atmosphere / vessel / solvent context that applies to this recipe
  must still be attached to this synthesis's own steps.
- rdfs:label of the ChemicalSynthesis should identify this target product.
"""

FROM_EXTRACTION_CONTRACT = """
# Task: KG materialization, not paper extraction
The human message is a SEMANTIC_HINTS_V1 ledger from our extraction pipeline.
Turn that ledger into one OntoSynthesis ChemicalSynthesis graph.
- There is no paper body. Do not invent steps, chemicals, vessels, or numbers.
- Do not drop a headed step that appears in the ledger.
- Each headed block (Add / Stir / HeatChill / ...) becomes exactly one step
  of that type. Keep hasOrder from the ledger.
- Chemical identity is hasAddedChemicalInput (or the short name in the heading).
  Do not create extra ChemicalInput nodes from hasAlternativeNames / PubChem lists.
- Map hasAmount (and any legacy hasParameter amount) onto ontosyn:hasAmount;
  preserve every quantity expression. When one input has mass and molar amount,
  keep both in one comma-separated string (for example, "0.045 g, 0.276 mmol");
  never drop the parenthetical quantity.
- Map hasTargetTemperature,
  hasStepDuration, hasWashingSolvent, hasVessel, isSealed, and atmosphere when
  the ledger states them.
- ChemicalInput is occurrence-local. Reuse a prior ChemicalInput id only for the
  exact same occurrence. Distinct additions require distinct ids even when their
  normalized labels are equal (for example, 5 mL DEF followed by 10 mL DEF).
- Ignore comment lines that start with #.
"""


LAYERED_CONTRACT = """
# Incremental subgraph construction (iter2 → iter3 → iter4)
This is end-to-end OntoSynthesis A-Box building, one owned surface at a time.
The human message has an EXISTING_GRAPH_INVENTORY (prior layers)
plus the current iteration's SEMANTIC_HINTS ledger.

Emit a subgraph that ATTACHES to that inventory:
- Every tool call, including every correction, must re-emit a COMPLETE
  replacement snapshot of the CURRENT layer. It is not a patch.
- Always include both top-level fields: nodes and relationships.
- On correction rounds, repeat every current-layer node and relationship that
  should remain, then fix the reported issue. Content omitted from the new
  current-layer snapshot is not carried over from the previous attempt.
- Reuse existing node ids when linking to prior nodes.
- You may omit prior nodes from the nodes list and still use those ids as
  relationship source_id / target_id.
- Add only nodes/relationships this ledger requires.
- Prior layers are editable when correction requires it. To delete an incorrect
  prior edge, list its exact source_id / type / target_id in
  remove_relationships. Omitting a prior edge does NOT delete it.
- Preserve prior content unless the ledger or SHACL report gives a concrete
  reason to change it. You may add or replace properties on an existing id
  by emitting that node with the corrected properties.
- New nodes get new ids.

Layer meaning:
- iter2: ChemicalSynthesis, ChemicalOutput, synthesis-level ChemicalInput,
  retrievedFrom Document. Usually no steps yet.
- iter3: Add/Stir/HeatChill/... steps and their chemicals, amounts,
  temperatures, durations. Link steps to the existing ChemicalSynthesis
  with ontosyn:hasSynthesisStep. Keep hasOrder from the ledger.
- iter4: vessel, yield, leftover equipment/conditions. Attach to existing
  steps or the synthesis.
- The human message lists the closed iteration-owned surface for the
  current layer. Add only those classes and properties this layer owns.
"""


FULL_HINTS_CONTRACT = """
# Whole-graph construction from all iteration hints
The human message contains every available SEMANTIC_HINTS ledger for one
ChemicalSynthesis, grouped as ITER2, ITER3, and ITER4. These are complementary
views of one graph, not separate graphs and not sequential patch requests.

- ITER2 owns the synthesis/output/input/document skeleton.
- ITER3 owns synthesis steps, step chemicals, amounts, temperatures, and durations.
- ITER4 owns vessels, yields, equipment, and remaining conditions.
- Reconcile repeated mentions across sections into the same node. Do not create
  one copy per iteration.
- Emit the COMPLETE graph for this ChemicalSynthesis in every tool call,
  including every correction round.
- Both top-level fields, nodes and relationships, must always be present.
- A correction is a replacement whole-graph candidate, never an edge-only or
  node-only patch. Preserve all unaffected nodes and relationships while fixing
  the reported violation.
- If a listed reusable entity is used, include it in nodes with the listed id
  and connect it normally. Do not copy unused inventory nodes into the graph.
"""


ENTITY_REUSE_CONTRACT = """
# Entity reuse (pipeline check_existing_* equivalent)
The human message may include SAME_PAPER_REUSABLE_ENTITIES and
CROSS_DOCUMENT_REUSABLE_ENTITIES. Reuse the listed id only when it denotes the
same real-world thing.
If you use a listed reusable entity, include that node in nodes with the listed
id and connect it normally. Do not copy unused inventory nodes into the graph.

Same-paper reuse:
- bibo:Document only for the same DOI / document label
- Equipment and HeatChillDevice

Same-paper and cross-document global reuse:
- ontosyn:Supplier (same organization)
- ontosyn:VesselEnvironment (same atmosphere, e.g. argon)
- ontosyn:VesselType, SeparationType, Material, Species, Solvent
- ontomops:MetalOrganicPolyhedron only when the CCDC number is identical

Do NOT reuse across syntheses (mint a new id for each occurrence):
- ontosyn:ChemicalSynthesis, ChemicalInput, ChemicalOutput
- step types (Add, Stir, HeatChill, ...)
- ontosyn:Vessel (only reuse inside the current synthesis)
- OM-2 measure nodes (Temperature, Duration, Volume, ...)

Never reuse a Document across different DOI values. Within one
ChemicalSynthesis, repeated hints still denote that synthesis's own nodes
(same id, same ChemicalInput label, same step order).
"""


EXTENSION_CONTRACT = """
# Task: OntoSpecies extension on an inherited OntoSyn graph
The human message has the same two payloads the Pipeline extension
agent sees for this entity:
1. the canonical main-ontology TTL (complete inherited ChemicalSynthesis)
2. the OntoSpecies extraction ledger (ref-entity-relations.v1 JSON)

There is no separate node inventory. Read the main graph only from that TTL.
Do not rebuild steps.

- Re-emit a COMPLETE replacement snapshot of the CURRENT extension layer
  only (Species, formulas, CCDC, characterization). Always include both
  top-level fields: nodes and relationships.
- Type the inherited ChemicalOutput as ontospecies:Species by re-emitting
  that exact node. SynthesisGraph ids are the local names after
  `/ontologx/{hash}/` in the TTL instance IRIs (for example
  ChemicalOutput-VMOP-alpha). Do not mint a second product identity
  such as species-vmop-16. Put formula / CCDC / characterization edges
  on that ChemicalOutput id.
- You may omit prior main-graph nodes from the nodes list and still use
  those ids as relationship source_id / target_id.
- The ChemicalSynthesis → hasChemicalOutput → that id edge already exists.
  Do not add a second hasChemicalOutput.
- Create MolecularFormula / ChemicalFormula / CCDCNumber / characterization
  nodes only when the ledger lists them. Copy values verbatim.
- MolecularFormula must carry hasMolecularFormulaValue; ChemicalFormula must
  carry hasChemicalFormulaValue; CCDCNumber must carry hasCCDCNumberValue.
- Do not invent NMR / IR / EA / device facts that are absent from the ledger.
- Ignore MCP tool names (create_*, add_*, init_memory). Emit SynthesisGraph.
"""


def main_ttl_extension_suffix(
    turtle: str,
    *,
    enrichment_targets: list[dict] | None = None,
) -> str:
    """Paste the inherited main A-Box the same way Pipeline does."""
    blocks = [
        "The enrichment target binding is authoritative. Enrich each exact "
        "target IRI for its declared class; do not create or select a "
        "replacement identity for that class.",
    ]
    if enrichment_targets:
        blocks.append(
            "Deterministically resolved extension enrichment targets: "
            + json.dumps(enrichment_targets, ensure_ascii=False, indent=2)
        )
    blocks.append("Here is the canonical main-ontology TTL for this upstream entity:")
    blocks.append(turtle.rstrip())
    return "\n\n" + "\n\n".join(blocks) + "\n"


ONTOMOPS_EXTENSION_CONTRACT = """
# Task: OntoMOPs extension on an inherited OntoSyn graph
The human message has the same two payloads the Pipeline extension
agent sees for this entity:
1. the canonical main-ontology TTL (complete inherited ChemicalSynthesis)
2. the OntoMOPs extraction ledger (ref-entity-relations.v1 JSON)

There is no separate node inventory. Read the main graph only from that TTL.
Do not rebuild steps.

- Re-emit a COMPLETE replacement snapshot of the CURRENT extension layer
  only (MetalOrganicPolyhedron, CCDC, MOP formula, ChemicalBuildingUnit).
  Always include both top-level fields: nodes and relationships.
- Reuse the seeded MetalOrganicPolyhedron id from the enrichment target.
  SynthesisGraph ids are the local names after `/ontologx/{hash}/` in the
  TTL instance IRIs. Do not mint a second MOP identity.
- The ChemicalOutput → isRepresentedBy → that MOP edge is already seeded.
  Do not add a second isRepresentedBy.
- Create ChemicalBuildingUnit nodes only when the ledger lists them.
  Copy hasCBUFormula / hasCCDCNumber / hasMOPFormula verbatim.
- Ignore MCP tool names (create_*, add_*, init_memory). Emit SynthesisGraph.
"""


DEFAULT_ONTOSPECIES_TBOX = (
    Path(__file__).resolve().parent / "resources" / "ontospecies_tbox.md"
)
DEFAULT_ONTOMOPS_TBOX = (
    Path(__file__).resolve().parent / "resources" / "ontomops_tbox.md"
)


def build_ontospecies_prompt(tbox_path: Path | None = None) -> str:
    path = tbox_path or DEFAULT_ONTOSPECIES_TBOX
    tbox = path.read_text(encoding="utf-8").strip()
    if not tbox:
        raise RuntimeError(f"Empty OntoSpecies T-Box prompt: {path}")
    prefix = """# Role
You extend an inherited OntoSynthesis graph with OntoSpecies product facts.
Emit the graph through the SynthesisGraph tool (nodes + relationships).
Do not emit MCP tool calls, SEMANTIC_HINTS ledgers, or prose outside the tool call.

Use prefixed types: ontospecies:*, periodic:*, ontosyn:ChemicalSynthesis,
ontosyn:hasChemicalOutput, rdfs:label.
"""
    return prefix + FROM_EXTRACTION_CONTRACT + EXTENSION_CONTRACT + "\n" + tbox + "\n"


def build_ontomops_prompt(tbox_path: Path | None = None) -> str:
    path = tbox_path or DEFAULT_ONTOMOPS_TBOX
    tbox = path.read_text(encoding="utf-8").strip()
    if not tbox:
        raise RuntimeError(f"Empty OntoMOPs T-Box prompt: {path}")
    prefix = """# Role
You extend an inherited OntoSynthesis graph with OntoMOPs cage and CBU facts.
Emit the graph through the SynthesisGraph tool (nodes + relationships).
Do not emit MCP tool calls, SEMANTIC_HINTS ledgers, or prose outside the tool call.

Use prefixed types: ontomops:*, ontosyn:ChemicalSynthesis, ontosyn:ChemicalOutput,
ontosyn:hasChemicalOutput, ontosyn:isRepresentedBy, rdfs:label.
"""
    return prefix + FROM_EXTRACTION_CONTRACT + ONTOMOPS_EXTENSION_CONTRACT + "\n" + tbox + "\n"


def build_system_prompt(
    tbox_path: Path | None = None,
    per_entity: bool = False,
    from_extraction: bool = False,
    layered: bool = False,
    full_hints: bool = False,
    entity_reuse: bool = False,
    official_onepass_guidance: bool = False,
) -> str:
    path = tbox_path or DEFAULT_TBOX
    tbox = path.read_text(encoding="utf-8").strip()
    if not tbox:
        raise RuntimeError(f"Empty OntoSynthesis T-Box prompt: {path}")
    if official_onepass_guidance and not full_hints:
        raise ValueError("Official ONEPASS OX guidance requires full_hints=True")
    prefix = OUTPUT_CONTRACT
    if from_extraction:
        prefix = OUTPUT_CONTRACT + FROM_EXTRACTION_CONTRACT
    if layered:
        prefix = prefix + LAYERED_CONTRACT
    if full_hints:
        if official_onepass_guidance:
            contract = OFFICIAL_ONEPASS_OX_CONTRACT.read_text(encoding="utf-8").strip()
            if not contract:
                raise RuntimeError(
                    f"Empty official ONEPASS OX contract: {OFFICIAL_ONEPASS_OX_CONTRACT}"
                )
            prefix = prefix + "\n\n" + contract + "\n"
        else:
            prefix = prefix + FULL_HINTS_CONTRACT
    if entity_reuse:
        reuse_contract = ENTITY_REUSE_CONTRACT
        if official_onepass_guidance:
            reuse_contract = reuse_contract.replace(
                "# Entity reuse (pipeline check_existing_* equivalent)",
                "# Entity reuse from scoped inventories",
            )
        prefix = prefix + reuse_contract
    if per_entity:
        prefix = prefix + PER_ENTITY_CONTRACT
    return prefix + "\n" + tbox + "\n"


def load_original_ontologx_prompt(path: Path | None = None) -> str:
    """Load OntoLogX's upstream system prompt verbatim for prompt ablations."""
    prompt_path = path or DEFAULT_ONTOLOGX_PROMPT
    prompt = prompt_path.read_text(encoding="utf-8")
    if not prompt.strip():
        raise RuntimeError(f"Empty original OntoLogX prompt: {prompt_path}")
    return prompt


def load_faithful_ontologx_prompt(path: Path | None = None) -> str:
    """Load the short OntoSynthesis analog of OntoLogX main.system.md.

    TBox types and properties stay in the structured-output schema
    (build_dynamic_model), not in this prompt. Do not append
    ontosynthesis_parsed.md or the adapter construction contracts.
    """
    prompt_path = path or FAITHFUL_ONTOLOGX_PROMPT
    prompt = prompt_path.read_text(encoding="utf-8")
    if not prompt.strip():
        raise RuntimeError(f"Empty faithful OntoLogX prompt: {prompt_path}")
    return prompt


def entity_human_suffix(
    entity_key: str,
    entity_label: str,
    layer: int | None = None,
    *,
    entity_uri: str = "",
    identity_dossier: dict | None = None,
    include_iteration_surface: bool = False,
) -> str:
    from iteration_guides import format_identity_block, format_iteration_surface

    layer_line = f"\nCurrent layer: iter{layer}\n" if layer else ""
    uri_line = f"URI: {entity_uri}\n" if entity_uri else ""
    suffix = (
        "\n\nTarget ChemicalSynthesis (extract this one only):\n"
        f"{entity_key} [{entity_label}]\n"
        f"{uri_line}"
        f"{layer_line}"
    )
    suffix += format_identity_block(
        entity_uri=entity_uri,
        identity_dossier=identity_dossier,
    )
    if include_iteration_surface and layer is not None:
        surface = format_iteration_surface(int(layer))
        if surface:
            suffix += "\n" + surface
    return suffix


def existing_graph_suffix(inventory: str) -> str:
    return (
        "\n\nPrior graph (trusted; attach to these ids):\n"
        f"{inventory}\n"
    )
