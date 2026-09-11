# With-prompt-hops OX dual-coding

This appendix is additive on the frozen with-prompt guidance. It does not
replace that file. generic-strict / generic-noprompt / with-prompt /
with-prompt-qty must not receive it.

Pipeline MCP expands string arguments into nodes and edges internally.
OntoLogX has no expander. A ledger key that is an OWL ObjectProperty is a
relationship to a child node. Putting that name, or a range class name, on
`nodes[].properties` makes Pydantic reject the entire SynthesisGraph call.

The locked occurrence map was copied from Pipeline tool arguments. This
overlay rewrites it for whole-graph emission.

## Slot rule

SynthesisGraph `properties` may only use datatype names: `rdfs:label`,
`hasOrder`, `hasParameter`, `isSealed`, `hasVacuum`, `isStirred`,
`isStirredHeatChill`, `isWait`, `isLayered`, `isLayeredTransfer`,
`isRepeated`, `isVacuumFiltration`, `hasTargetPh`, `hasRotaryEvaporator`,
`hasAmount`, `hasAlternativeNames`, `hasChemicalDescription`,
`hasChemicalFormula`, `hasPurity`, `om-2:hasNumericalValue`, `om-2:hasUnit`,
`ontomops:hasCCDCNumber`.

Everything else in the ledger is a node type, a relationship type, or a
value that belongs on a child node.

If a previous call was rejected because a property type was not in the enum,
those names are relationships. Re-emit the complete graph with child nodes
and edges.

## Do split nested hops

Ignore the locked wording "put every detail on that same occurrence" and
"do not split onto a later node". For OntoLogX you must split: parent node
keeps only its datatype facets; each hop is a distinct child plus a
relationship.

A ledger line `hasAddedChemicalInput: Cp2ZrCl2` is not an Add property. A
line `hasTargetTemperature: 65 oC` is not a HeatChill property. The MCP
suffix `_label` and the bare ontology name are the same hop.

## Chemical hops (fresh ChemicalInput)

Same four-tuple as frozen with-prompt Add, applied to every chemical hop:

1. the step node
2. a distinct `ontosyn:ChemicalInput` (fresh; do not reuse a synthesis-level
   input merely because labels match)
3. the hop relationship
4. `ChemicalSynthesis --hasSynthesisStep-->` the step

- Add: `hasAddedChemicalInput`. `hasAmount`, `hasAlternativeNames`,
  `hasChemicalDescription`, `hasChemicalFormula`, `hasPurity` on that Add
  heading belong on the child ChemicalInput, not on the Add node.
- Filter: `hasWashingSolvent` (wash of retained solid)
- Separate: `hasSeparationSolvent`
- Dry: `hasDryingAgent`
- Evaporate: `removesSpecies` when the ledger names a removed species

An Add with type, label, and order but no `hasAddedChemicalInput`
relationship is invalid.

## Quantity hops

Emit a measure node and the relationship. Put the ledger text on the
measure `rdfs:label`. When a number and unit are explicit, also set
`om-2:hasNumericalValue` and `om-2:hasUnit` on the measure node
(`om-2:degreeCelsius`, `om-2:hour`, ...; never free text such as "degC").
Qualitative values (room temperature, overnight) are label-only.

- `hasTargetTemperature` → `om-2:Temperature`
- `hasStepDuration` → `om-2:Duration`
- `hasTemperatureRate` → `om-2:TemperatureRate`
- `hasStirringTemperature` → `om-2:Temperature`
- `hasDryingTemperature` → `om-2:Temperature`
- `hasDryingPressure` → `om-2:Pressure`
- `hasEvaporationTemperature` → `om-2:Temperature`
- `hasEvaporationPressure` → `om-2:Pressure`
- `isEvaporatedToVolume` → `om-2:Volume`
- `hasTransferedAmount` → `om-2:Volume`

## Other object hops

Vessel, atmosphere, equipment, HeatChillDevice, transfer destination,
supplier, material, and MOP identity are child nodes plus the named
relationship (`hasVessel`, `hasVesselEnvironment`, `usesEquipment`,
`hasHeatChillDevice`, `isTransferedTo`, `isSuppliedBy`,
`referencesMaterial`, `isRepresentedBy`). Reuse inventory ids when the
human message lists them.

## Yield

`ontosyn:hasYield` is an ObjectProperty from the bound
`ontosyn:ChemicalSynthesis` to `om-2:AmountOfSubstanceFraction` (at most
one, only if stated). Never put `hasYield` on ChemicalOutput.properties.

## Pipeline (unchanged)

Keep string / `_label` arguments on the same `create_*` call. The MCP
server still expands them.
