# OntoMOPs T-Box (extension surface)

## Classes
- `ontomops:MetalOrganicPolyhedron` — the cage / MOP product. Linked from
  the inherited `ontosyn:ChemicalOutput` by `ontosyn:isRepresentedBy`.
- `ontomops:ChemicalBuildingUnit` — repeated metal-cluster or linker fragment.

## Bridge from the inherited main graph
- `ontosyn:hasChemicalOutput` — ChemicalSynthesis → ChemicalOutput (already present).
- `ontosyn:isRepresentedBy` — ChemicalOutput → MetalOrganicPolyhedron.
  Reuse the seeded MOP id. Do not mint a second MOP identity.

## Object properties
- `ontomops:hasChemicalBuildingUnit` — MetalOrganicPolyhedron → ChemicalBuildingUnit
- `owl:sameAs` — ChemicalBuildingUnit → source species / reagent / alias label

## Datatype properties
- `ontomops:hasCCDCNumber` (string) on MetalOrganicPolyhedron
- `ontomops:hasMOPFormula` (string) on MetalOrganicPolyhedron
- `ontomops:hasCBUFormula` (string) on ChemicalBuildingUnit
- `rdfs:label` on both classes

Copy CCDC, MOP formula, and CBU fragments verbatim from the ledger.
Do not invent building units that the ledger does not list.
