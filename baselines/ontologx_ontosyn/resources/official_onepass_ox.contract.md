# Official ONEPASS semantic guidance — OntoLogX-native form

This is the fixed OntoLogX rendering of the current official Pipeline ONEPASS
construction guidance. It is a whole-graph semantic contract, not an MCP
execution script. Apply it through the `SynthesisGraph` structured output.

## Whole-graph assembly

Read every ITER2, ITER3, and ITER4 ledger before constructing the graph. The
ledgers are complementary views of one bound `ontosyn:ChemicalSynthesis`, not
separate graphs or sequential patch requests.

- Materialize every source-supported entity, datatype value, relationship, and
  ordered occurrence owned by the union of the three iteration profiles.
- Emit one complete replacement graph in every `SynthesisGraph` call, including
  correction calls. Preserve all unaffected nodes and relationships.
- Use the bound ChemicalSynthesis identity supplied in the human message as the
  single graph root. Do not mint a replacement synthesis or emit sibling
  syntheses.
- Reconcile repeated mentions only when they denote the same semantic
  individual. Equal labels do not by themselves establish identity.
- A source-grounded operation occurrence remains independent from every other
  occurrence. Materializing a similar entity in another ownership layer does
  not discharge the current occurrence.
- Every node must be connected to the bound ChemicalSynthesis through supported
  OntoSynthesis relationships.

## ITER2 foundation semantics

Materialize the synthesis/output/input/provenance foundation:

- Create exactly one `ontosyn:ChemicalOutput` for the target synthesis and link
  it with `ontosyn:hasChemicalOutput`. Use the source product identifier as its
  label; do not put amounts or conditions in the label. Preserve grounded
  alternative names, formula, and description.
- When grounded, represent the output with one
  `ontomops:MetalOrganicPolyhedron` through `ontosyn:isRepresentedBy`; preserve
  an explicit CCDC number.
- Create synthesis-level `ontosyn:ChemicalInput` occurrences for reactants,
  reagents, and catalysts and link each through `ontosyn:hasChemicalInput`.
  Exclude a pure solvent used only as process medium, washing solvent, or
  separation solvent from this synthesis-level role.
- Keep distinct synthesis-level input occurrences separate even when their
  labels are equal. Labels contain chemical identity only. Preserve grounded
  amount, formula, description, purity, and aliases on the occurrence.
- When explicitly grounded, link an input to its `ontosyn:Supplier` through
  `ontosyn:isSuppliedBy` and to a canonical `ontosyn:Material` through
  `ontosyn:referencesMaterial`. Do not invent supplier facts.
- Link the synthesis through `ontosyn:retrievedFrom` to exactly the
  DOI-identified `bibo:Document`.
- When a distinct section, subsection, paragraph label, or paragraph number is
  provided, create one document-scoped `ontosyn:DocumentContext` with that exact
  anchor and link it through `ontosyn:hasDocumentContext`. A DocumentContext
  never substitutes for the DOI Document.
- Materialize explicitly used process equipment, excluding analytical
  instruments, and attach it only through a relationship licensed by the
  ontology and the ledgers.

## ITER3 ordered synthesis-step semantics

Materialize every headed operation occurrence in source order using the most
specific supported subclass: `Add`, `Stir`, `HeatChill`, `Evaporate`,
`Sonicate`, `Transfer`, `Separate`, `Filter`, `Dry`, or `Crystallize`.

- Link every step from the ChemicalSynthesis using
  `ontosyn:hasSynthesisStep`.
- Assign `ontosyn:hasOrder` as one unique, contiguous, globally increasing
  integer sequence beginning at 1 inside the synthesis. Type-local headings are
  occurrence labels, not separate order namespaces; reconcile them using the
  procedure order represented across all ledgers.
- Never merge distinct step occurrences. Repeated step labels or repeated
  chemicals do not authorize reuse.
- One `Add` owns exactly one fresh step-local `ontosyn:ChemicalInput` through
  `ontosyn:hasAddedChemicalInput`. A clause that names N independently added
  materials yields N Add occurrences in source order. Explicit solvent
  components are separate Add occurrences.
- Step-local ChemicalInput nodes are occurrence-specific and must not reuse a
  synthesis-level input or an input owned by another step merely because their
  labels match.
- A washing solvent belongs to `Filter` through
  `ontosyn:hasWashingSolvent`; a separation medium belongs to `Separate`
  through `ontosyn:hasSeparationSolvent`. Each explicit solvent occurrence gets
  its own ChemicalInput node.
- Use `HeatChill` for controlled heating/cooling and for a true passive
  duration-bearing hold not owned by a more specific operation. Keep cooling or
  return-to-room-temperature as a distinct HeatChill occurrence when stated.
- Use `Evaporate` for explicit evaporation or concentration and do not duplicate
  the same interval as HeatChill.
- Use `Filter` for filtration and retained-solid washing; use `Separate` for an
  explicitly stated physical separation such as decanting. Do not duplicate one
  event under both classes.
- Use `Transfer` only for an explicitly supported move of an existing stream to
  a distinct destination vessel.
- Use `Dry` for explicit drying or evacuation workup, not for characterization.

Attach every grounded companion fact to its owning occurrence:

- vessel through `ontosyn:hasVessel`, vessel type through
  `ontosyn:hasVesselType`, and explicitly stated or validly inherited atmosphere
  through `ontosyn:hasVesselEnvironment`;
- an explicitly stated sealed/open state through `ontosyn:isSealed`; sealing is
  a vessel condition and never evidence for an atmosphere;
- process equipment through `ontosyn:usesEquipment` and a heat/chill device
  through `ontosyn:hasHeatChillDevice`;
- duration, target/stirring/drying/evaporation/crystallization temperature,
  temperature rate, pressure, transferred amount, or final evaporated volume;
- separation solvent through `ontosyn:hasSeparationSolvent`, separation
  technique through `ontosyn:isSeparationType`, washing solvent through
  `ontosyn:hasWashingSolvent`, and drying agent through
  `ontosyn:hasDryingAgent`;
- each explicitly removed species through `ontosyn:removesSpecies` and an
  explicit transfer destination through `ontosyn:isTransferedTo`.

For each quantity, create the ontology-appropriate OM-2 measure node and connect
it with the predicate stated in the ledger. Preserve the exact source lexeme as
its label. Add numerical value and an OM-2 unit only when both are explicit and
unambiguous. Do not normalize, calculate, or invent values.

Do not infer an atmosphere. Shared context may be inherited only when the ledger
or supplied global context establishes that it covers the owning operation.
Sealing is not an atmosphere.

## ITER4 synthesis-level completion

- Materialize each explicitly grounded `ontosyn:LabEquipment` and link it from
  the ChemicalSynthesis through `ontosyn:hasEquipment`.
- Materialize at most one explicit yield as an
  `om-2:AmountOfSubstanceFraction` and link it from the ChemicalSynthesis through
  `ontosyn:hasYield`.
- Preserve the complete reported yield lexeme. Never derive, calculate, or
  inherit a yield.

## Identity and reuse

Reuse a supplied inventory id only when it denotes the same real-world entity
under the stated scope:

- DOI Document: same DOI only.
- DocumentContext, Equipment, HeatChillDevice: same paper and same grounded
  identity.
- Supplier, VesselEnvironment, VesselType, SeparationType, Material, Species,
  and Solvent: same canonical real-world identity.
- MetalOrganicPolyhedron: reuse only when the CCDC number establishes identity.
- Vessel: reuse only inside the current synthesis when the exact vessel
  occurrence is being referenced.

Never reuse ChemicalSynthesis, ChemicalOutput, ChemicalInput, operation steps,
or OM-2 measure nodes across owner occurrences. If no authorized inventory
identity matches, mint a fresh node.

## Completion check

Before returning, verify that:

1. every representable headed operation occurrence from every ledger is present;
2. every step has the correct concrete type, label, global order, and synthesis
   link;
3. every Add has exactly one fresh owned input;
4. every grounded companion relation and exact lexical value is present on its
   correct owner;
5. output, DOI provenance, ordering, direction, identity, and cardinality
   constraints are satisfied; and
6. both `nodes` and `relationships` contain the complete graph.
