# Frozen OntoSyn KG-building guidance (with-prompt)

This text is the only additive protocol on top of each constructor's strict
no-prompt surface (Pipeline: no-contract envelope + MCP; OntoLogX: occurrence
and ownership map). It is not a T-Box dump and not official ONEPASS. Keep it
verbatim. Apply every rule through the constructor you are using.

Constructor encoding is different; the graph must be the same.

- Pipeline: one `create_*` per ledger occurrence. Nested objects are arguments
  on that same call (`hasAddedChemicalInput_label`, `hasVessel_label`, ...).
- OntoLogX: one `SynthesisGraph` with `nodes` and `relationships`. A nested
  hop is a child node plus a relationship whose predicate is exactly the hop
  name. Emitting the parent node without that relationship is incomplete.

ITER2, ITER3, and ITER4 ledgers are complementary views of one bound
`ontosyn:ChemicalSynthesis`. Materialize the union. Every call, including a
SHACL correction, must emit the complete graph for that bound root. Do not
treat a later iteration as a patch that drops earlier nodes.

## ITER2 — synthesis foundation

- Exactly one `ontosyn:ChemicalSynthesis` (the bound root). Give it `rdfs:label`.
- Exactly one `ontosyn:ChemicalOutput` linked by `ontosyn:hasChemicalOutput`.
  The output label is the product identity only (no amounts, no conditions).
- `ontosyn:retrievedFrom` a `bibo:Document` whose `rdfs:label` is the paper DOI.
- Optional `ontosyn:hasDocumentContext` when the ledger names a section or
  paragraph anchor. A DocumentContext is not a substitute for the DOI Document.
- Synthesis-level `ontosyn:hasChemicalInput` is only for reactants, reagents,
  and catalysts named as synthesis inputs. Do **not** put a pure process /
  wash / separation solvent on this layer.
- Synthesis-level ChemicalInput does **not** discharge an Add. Every Add still
  needs its own step-local input through `hasAddedChemicalInput`.
- When grounded, `ontosyn:isRepresentedBy` an `ontomops:MetalOrganicPolyhedron`,
  keeping `ontomops:hasCCDCNumber` when stated.
- When grounded, `ontosyn:isSuppliedBy` / `ontosyn:referencesMaterial` on the
  relevant ChemicalInput.

## ITER3 — ordered steps

Allowed concrete step types only: `Add`, `Stir`, `HeatChill`, `Evaporate`,
`Sonicate`, `Transfer`, `Separate`, `Filter`, `Dry`. Never emit a node typed
only as `ontosyn:SynthesisStep`. There is no `ontosyn:Crystallize` and no
`hasCrystallizationTargetTemperature`. A crystal-yielding hold is `HeatChill`.

Every step:

- attach from the synthesis with `ontosyn:hasSynthesisStep`;
- set `ontosyn:hasOrder` as one contiguous sequence starting at 1 inside this
  synthesis (not a per-type counter);
- keep `rdfs:label` (conversion and scoring cannot read a typeless/labelless
  step).

### Add and hasAddedChemicalInput (do not skip this edge)

This is the most common constructor failure. The T-Box and SHACL both require
cardinality 1/1.

- One Add owns exactly one fresh step-local `ontosyn:ChemicalInput` through
  `ontosyn:hasAddedChemicalInput`.
- A clause that names N materials is N Add nodes and N ChemicalInput nodes,
  each with its own `hasAddedChemicalInput`.
- Include explicit solvents at this step-local layer even when they were
  excluded from synthesis-level `hasChemicalInput`.
- ChemicalInput `rdfs:label` is identity only. Put the amount on
  `ontosyn:hasAmount`, never in the label.
- Do not reuse a synthesis-level input node, another Add's input, or a wash
  solvent as this Add's `hasAddedChemicalInput` merely because labels match.

Pipeline: `create_Add(..., hasAddedChemicalInput_label=<name>,
hasAddedChemicalInput_hasAmount=<amount>, ...)`. Leaving
`hasAddedChemicalInput_label` empty is invalid when the heading names a
chemical.

OntoLogX: emit (1) the `ontosyn:Add` node, (2) a distinct
`ontosyn:ChemicalInput` node, (3) relationship
`Add --hasAddedChemicalInput--> ChemicalInput`, and (4)
`ChemicalSynthesis --hasSynthesisStep--> Add`. An Add that has type, label,
and order but no `hasAddedChemicalInput` relationship is invalid. Do not
replace that relationship with synthesis-level `hasChemicalInput`.

### Other step types

- Heat-to-temperature-and-hold, including solvothermal "130 °C for 2 days",
  is `HeatChill`, not `Stir`, even if a vessel is named.
- Cooling or return to room temperature is a separate `HeatChill` and must
  keep `ontosyn:hasTargetTemperature`.
- A wash of retained solid is `Filter` with `ontosyn:hasWashingSolvent`, not
  `Add`.
- Evaporation / concentration is `Evaporate`, not a second HeatChill of the
  same interval.
- `Separate` is an explicit physical separation (decant, centrifuge-as-separate
  when the ledger says so) with `ontosyn:hasSeparationSolvent` when a medium
  is named.
- `Transfer` only for an explicit move of an existing stream to a distinct
  destination (`ontosyn:isTransferedTo`).
- `Dry` for explicit drying or evacuation workup.

## ITER4 — vessels, environment, yield, equipment

Attach only source-stated companions:

- `ontosyn:hasVessel` / `hasVesselType` / `hasVesselEnvironment`;
- `ontosyn:usesEquipment` and `hasHeatChillDevice`;
- at most one synthesis-level `ontosyn:hasYield` as
  `om-2:AmountOfSubstanceFraction`;
- synthesis-level `ontosyn:hasEquipment` for explicitly used process equipment.

Do not default atmosphere to air. Inherit a shared atmosphere (for example
argon glovebox for a whole family) onto covered steps via
`hasVesselEnvironment` only when the ledger or supplied context says it
covers those steps. Sealing (`isSealed`) is not an atmosphere.

## Measures

`om-2:Temperature`, `Duration`, `Pressure`, `Volume`, `TemperatureRate`, and
`AmountOfSubstanceFraction` need `rdfs:label`. When a number and unit are
explicit, also set `om-2:hasNumericalValue` and `om-2:hasUnit`.

`om-2:hasUnit` must be an OM-2 unit individual (`om-2:degreeCelsius`,
`om-2:hour`, `om-2:day`, `om-2:minute`, `om-2:degreeCelsiusPerHour`,
`om-2:millilitre`, `om-2:percent`, ...). Never a free-text string such as
"degC" or "days".

Qualitative measures (room temperature, overnight) are label-only; do not
invent a unit or numerical value.

## Completion check

Before returning, verify:

1. ChemicalSynthesis, ChemicalOutput, DOI Document, and every headed step from
   every ledger are present.
2. Every Add has exactly one `hasAddedChemicalInput` to a fresh ChemicalInput.
3. Step types follow the HeatChill / Filter-wash / no-Crystallize rules.
4. Orders are unique, start at 1, and are contiguous.
5. Every node is linked to the bound synthesis through OntoSyn relationships.
6. The graph is a complete replacement, not a delta.
