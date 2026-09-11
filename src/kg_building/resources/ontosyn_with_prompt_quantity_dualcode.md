# With-prompt-qty quantity dual-coding

This appendix is additive on the frozen with-prompt guidance. It does not
replace that file. generic-strict / generic-noprompt / with-prompt must not
receive it.

Ledger lines such as `hasTargetTemperature: 65 oC` and `hasStepDuration: 10
hours` look like fields on the step. They are OWL ObjectProperties. The
OntoLogX SynthesisGraph property enum only allows datatype properties
(`rdfs:label`, `hasOrder`, `isSealed`, `hasAmount`, `om-2:hasNumericalValue`,
...). Putting `ontosyn:hasTargetTemperature`, `ontosyn:hasStepDuration`, or a
measure class name such as `om-2:Temperature` on `nodes[].properties` makes
Pydantic reject the entire tool call. A later retry still has no graph unless
the next call uses relationships instead.

## Constructor encoding

Pipeline (unchanged): keep the string arguments on the same `create_*` call.
The MCP server creates the measure node and the edge. Example:
`create_HeatChill(..., hasTargetTemperature=<string>, hasStepDuration=<string>)`.

OntoLogX: for every quantity facet below, emit (1) the step node, (2) a
distinct measure node of the range class, (3) a relationship whose type is
exactly the predicate, and (4) `ChemicalSynthesis --hasSynthesisStep-->` the
step. Put the ledger text on the measure node's `rdfs:label`. When a number
and unit are explicit, also set `om-2:hasNumericalValue` and `om-2:hasUnit` on
that measure node (`om-2:degreeCelsius`, `om-2:hour`, ...; never free text
such as "degC"). Qualitative values (room temperature, overnight) are
label-only. Never put the predicate or the measure class name on the step's
`properties`.

Datatype step fields stay on the step: `hasOrder`, `hasParameter`,
`isSealed`, `hasVacuum`, `isStirredHeatChill`, `isWait`, and `rdfs:label`.

## Quantity hops (ledger name → measure node → relationship)

- `hasTargetTemperature` → `om-2:Temperature` node → `ontosyn:hasTargetTemperature`
- `hasStepDuration` → `om-2:Duration` node → `ontosyn:hasStepDuration`
- `hasTemperatureRate` → `om-2:TemperatureRate` node → `ontosyn:hasTemperatureRate`
- `hasStirringTemperature` → `om-2:Temperature` node → `ontosyn:hasStirringTemperature`
- `hasDryingTemperature` → `om-2:Temperature` node → `ontosyn:hasDryingTemperature`
- `hasDryingPressure` → `om-2:Pressure` node → `ontosyn:hasDryingPressure`
- `hasEvaporationTemperature` → `om-2:Temperature` node → `ontosyn:hasEvaporationTemperature`
- `hasEvaporationPressure` → `om-2:Pressure` node → `ontosyn:hasEvaporationPressure`
- `isEvaporatedToVolume` → `om-2:Volume` node → `ontosyn:isEvaporatedToVolume`
- `hasTransferedAmount` → `om-2:Volume` node → `ontosyn:hasTransferedAmount`

If a previous SynthesisGraph call was rejected because a property type was
not in the enum, those names are the relationships in this list, not
properties. Re-emit the complete graph with measure nodes and edges.
