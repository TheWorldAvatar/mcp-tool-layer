# OntoSpecies T-Box (extension surface)

Use only these classes and properties. Do not invent characterization that
the ledger does not list.

## Classes

- `ontospecies:Species` — chemical product. In this runner it is the same
  individual as the inherited `ontosyn:ChemicalOutput`. Reuse that id.
- `ontospecies:MolecularFormula` — compact elemental formula (`C12H8O4`).
- `ontospecies:ChemicalFormula` — extended formula including solvates.
- `ontospecies:CCDCNumber` — Cambridge Crystallographic Data Centre number.
- `ontospecies:CharacterizationSession` — one multi-technique session.
- `ontospecies:HNMRData`, `ontospecies:ChemicalShift`, `ontospecies:Solvent`
- `ontospecies:InfraredSpectroscopyData`, `ontospecies:InfraredBand`, `ontospecies:Material`
- `ontospecies:ElementalAnalysisData`, `ontospecies:WeightPercentage`
- `ontospecies:Device`, `ontospecies:HNMRDevice`,
  `ontospecies:ElementalAnalysisDevice`, `ontospecies:InfraredSpectroscopyDevice`
- `periodic:Element`, `ontospecies:AtomicWeight`

Bridge class (already in the main graph; reference by id, do not mint a second one):

- `ontosyn:ChemicalSynthesis`

## Object properties

- `ontosyn:hasChemicalOutput` — ChemicalSynthesis → Species. Already present
  on the inherited ChemicalOutput. Re-emit it only if you mint a new Species
  (do not). Prefer typing the existing output id as Species.
- `ontospecies:hasMolecularFormula` — Species → MolecularFormula (0..1)
- `ontospecies:hasChemicalFormula` — Species → ChemicalFormula (0..1)
- `ontospecies:hasCCDCNumber` — Species → CCDCNumber (0..1)
- `ontospecies:hasCharacterizationSession` — Species → CharacterizationSession (0..1)
- `ontospecies:hasHNMRData` — Species → HNMRData
- `ontospecies:hasInfraredSpectroscopyData` — Species → InfraredSpectroscopyData
- `ontospecies:hasElementalAnalysisData` — Species → ElementalAnalysisData
- `ontospecies:usesDevice` — CharacterizationSession → Device
- `ontospecies:hasHNMRDevice` / `hasElementalAnalysisDevice` / `hasInfraredSpectroscopyDevice`
- `ontospecies:usesSolvent` — HNMRData → Solvent
- `ontospecies:hasChemicalShift` — HNMRData → ChemicalShift
- `ontospecies:usesMaterial` — InfraredSpectroscopyData → Material
- `ontospecies:hasInfraredBand` — InfraredSpectroscopyData → InfraredBand
- `ontospecies:hasWeightPercentageCalculated` / `hasWeightPercentageExperimental`
- `ontospecies:hasElement` — WeightPercentage → Element
- `ontospecies:hasAtomicWeight` — Element → AtomicWeight

## Datatype properties

- `rdfs:label` on every created node
- `ontospecies:hasProductName` on Species
- `ontospecies:hasMolecularFormulaValue` (required if MolecularFormula exists)
- `ontospecies:hasChemicalFormulaValue` (required if ChemicalFormula exists)
- `ontospecies:hasCCDCNumberValue` (required if CCDCNumber exists)
- `ontospecies:hasShifts`, `hasTemperature` on HNMRData
- `ontospecies:hasBands` on InfraredSpectroscopyData
- `ontospecies:hasSolventName`, `hasMaterialName`, `hasDeviceName`, `hasFrequency`
- `ontospecies:hasPercentageValue` (float), `hasWeightPercentageCalculatedValue`,
  `hasWeightPercentageExperimentalValue`
- `ontospecies:hasElementName`, `hasElementSymbol`
- `ontospecies:hasAtomicWeightValue` (float)
