# Prompt accounting: generic rules + strict no-prompt

Paper-ready note. Construction comparison only: same extraction ledgers, `gpt-4o`, OntoSynthesis, matched per-paper token budget. Profile: **generic graph rules + strict no-prompt**. Neither constructor receives the paper body, T-Box comment handbook, ONEPASS contract, or inherited Pipeline Turtle.

## Matched information

Both constructors see the same iter-2/3/4 `SEMANTIC_HINTS` ledger, the bound synthesis label and IRI, the occurrence protocol (one ledger heading maps to one owner; order is taken from the heading; empty or sentinel facets are absent), the same reusable-class list, and the same three root-level linkers (`hasDocumentContext`, `hasEquipment`, `retrievedFrom`). Iter-1, source PDF/markdown, and thick `KG_BUILDING_*.md` recipes are withheld from both.

## Channels excluded from the prompt ledger

We do not treat the following as prompt content. They are the mechanisms under test.

- **MCP.** Typed `create_*` tools, ReAct receipts, structured rejections, and the argument firewall.
- **OntoLogX.** The generated `SynthesisGraph` schema (admissible types, properties, triples) and SHACL repair rounds.

## Residual prompt extras, and who they can help

| Residual text | Who has it | Usable by the other constructor? |
|---|---|---|
| Sibling top-entity IRI manifest (all syntheses in the paper) | MCP user | **No.** OntoLogX is scoped to one `ChemicalSynthesis` and must not emit siblings; synthesis IRIs are not in its reuse inventory. |
| “Do not downgrade an explicit canonical field” | MCP user | **No.** That sentence guards parallel tool arguments (`hasAddedChemicalInput` versus aliases). OntoLogX has no such slots. |
| Seven generic graph rules (one bound root, most-specific type, prefixes, connectivity, …) | OntoLogX system | **Mostly no.** MCP encodes the same constraints in tool signatures, which we have already excluded from this ledger. |
| Per-class ownership/attachment map as prose | OntoLogX system | **Mostly no.** MCP’s counterpart is the per-tool argument list, again excluded here. |
| Same-paper / cross-document reusable-entity inventory | OntoLogX human | **Mostly no.** MCP resolves reusable classes inside the tool transaction rather than from a printed list. |
| Whole-graph re-emission instruction | OntoLogX system | **No.** MCP commits incrementally; the sentence is an OntoLogX emission rule, not extra source evidence. |

Two MCP-only sentences therefore do not advantage OntoLogX. Three OntoLogX-only blocks restate, in prose, constraints that MCP already compiles into tools. They are a written-contract surplus for OntoLogX, not additional source facts.

## Judgement

The comparison is **fair to OntoLogX**, and slightly generous on prompt text: MCP’s unused extras do not transfer, while OntoLogX still sees generic rules, an ownership map, and a reuse list in natural language. That surplus is acceptable. The intended contrast is the construction interface—interaction-based, compile-time constraint enforcement versus schema-guided whole-graph emission plus post-assembly validation—not a contest over who received more source text.

This setting can therefore be used as a **fair environment for mechanism difference**, provided the residual OntoLogX prose is disclosed and MCP tool chatter is not counted as withheld evidence.
