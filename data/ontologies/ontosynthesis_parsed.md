# Ontology Schema - Structured Property Mapping

## Class: `Add`

**Parent Classes:** `SynthesisStep`

**Comment:** [Definition]
A synthesis step that introduces exactly one explicitly named chemical material or component.

[Core atomicity — mandatory]
- One Add represents one source-grounded introduction occurrence of one named material.
- Each Add links to exactly one fresh step-local ChemicalInput.
- Keep the material identity, amount, role, and qualifiers on that one occurrence. Never split one introduction by identity, amount, or role.

[Unbounded list cardinality — mandatory]
- If one grammatical clause explicitly names N distinct materials participating in the introduction, emit exactly N Add occurrences; N may be 1, 2, 3, 4, 5, or any larger value.
- This cardinality is determined by named material occurrences, not by the number of verbs, sentences, conjunctions, or physical charging events.
- Simultaneous, together, combined, all reagents, all components, or comparable collective wording never collapses explicitly named materials into one Add.
- A representation such as one Add labelled "simultaneous addition of A, B, C, ..." or one Add carrying an array/list of several named materials is invalid.
- The same verbatim source span may ground several Add occurrences when that span names several materials; this required atomization is not duplication.

[Explicit solute and solvent atomization — mandatory]
- Every explicitly named solute and every explicitly named solvent is its own Add occurrence.
- 'A dissolved in X' requires Add(A) and Add(X) when both A and X are explicit.
- 'A dissolved in X and B in Y' requires four Adds in source order: Add(A), Add(X), Add(B), Add(Y).
- 'A, B, and C were added to a mixture of X and Y' requires five Adds in source listing order: Add(A), Add(B), Add(C), Add(X), Add(Y).
- A solvent introduced through wording such as 'in X', 'dissolved in X', or 'a solution of A in X' must not be omitted merely because the grammar foregrounds the solute.
- Explicit components of a starting or receiving medium, including wording such as 'added to a mixture of X and Y', remain independent Add occurrences even when the source does not repeat the verb for X and Y.
- Attach each explicit solvent volume or amount to that solvent's Add occurrence, not to the solute Add.
- If the same solvent is explicitly used in two distinct solution preparations or introduction occurrences, create two fresh solvent Adds even when the solvent name is identical.
- Named mixed-solvent components and explicitly named chemical-list members each require their own Add.

[Mixing boundary]
- Generic mixed, combined, or mixing wording does not imply Stir.
- 'A dissolved in X and B in Y were mixed/combined' is addition-only unless explicit stirring/agitation or a genuine existing-stream vessel transfer is independently stated.
- Emit the required component Adds and emit neither Stir nor Transfer solely for generic mixed/combined wording.

[Mixture fallback and non-duplication]
- A single solution or mixture ChemicalInput is allowed only when the source does not identify its chemical components.
- Never emit both a mixture meta-Add and duplicate component Adds for the same introduction.
- Allocate a total mixture amount only when an unambiguous ratio permits deterministic component allocation; otherwise use N/A.

[Operation boundaries]
- Initial introduction or charge of neat material is Add.
- Movement of an already-existing mixture or aliquot to a distinct vessel is Transfer only when the Transfer evidence threshold is independently met.
- Retained-solid washing solvent belongs to Filter, not Add.
- A newly introduced soak or exchange solvent is Add. The following hold is a separate HeatChill under the parent clause-to-sequence patterns, not a second type on this Add.
- Every named material participating in a later Stir must first have its own Add.

[Exclusions]
- Do not create Add for implicit materials, atmospheres, equipment, vessels, or milling media such as steel balls.

[Atmosphere]
- If the step's verbatim evidence explicitly names an atmosphere, including 'in air', nitrogen/N2, argon/Ar, inert atmosphere, or vacuum, preserve it and link the corresponding VesselEnvironment via ontosyn:hasVesselEnvironment.
- Never drop an explicit atmosphere and never infer one when unstated.

### Datatype Properties

| Property | Range |
|----------|-------|
| `hasOrder` | `integer` |
| `hasParameter` | `string` |
| `hasTargetPh` | `double` |
| `isLayered` | `boolean` |
| `isStirred` | `boolean` |

### Object Properties

| Property | Range (Target Class) |
|----------|----------------------|
| `hasAddedChemicalInput` | `ChemicalInput` |
| `hasStepDuration` | `Duration` |
| `hasVessel` | `Vessel` |
| `hasVesselEnvironment` | `VesselEnvironment` |
| `usesEquipment` | `Equipment` |

---

## Class: `ChemicalInput`

**Comment:** [Definition]
A contextual occurrence of a substance used in synthesis, including reactants, precursors, ligands, reagents, catalysts, acids or bases, and solvents.

[Exclusions]
- Do not create ChemicalInput for the synthesis product, atmosphere, equipment, or analytical material.

[Naming]
- The label contains only a source-grounded chemical identity, never amount, unit, purity, supplier, or role text.

[Identity resolution]
- Search the complete source for alternate names and identifiers of the same substance.
- When available chemistry lookup tools can resolve an exact identity, call them to verify formula and aliases; do not accept ambiguous hits or species with different hydrate, charge, stoichiometry, counterion, or stereochemistry.
- Preserve verified aliases with hasAlternativeNames and carry exact same-material aliases across source passages, including when a later input names a previously identified substance.

[Ownership layers]
- ChemicalInput has two distinct ownership layers.
- The synthesis-level layer is linked directly from ChemicalSynthesis by hasChemicalInput and includes reactants, reagents, and catalysts, but excludes a pure solvent used only as process medium, washing solvent, or separation solvent.
- The step-local layer creates a fresh ChemicalInput occurrence for every explicitly introduced chemical and every explicitly named process, washing, or separation solvent, linked from its owning Add, Filter, or Separate step by the corresponding T-Box property.

[Step-local Add cardinality]
- One Add owns exactly one step-local ChemicalInput occurrence.
- A clause naming N explicit introduced materials therefore requires N Add owners and N fresh step-local ChemicalInput occurrences, without any upper bound on N.
- Collective wording or simultaneous introduction does not authorize a multi-material ChemicalInput occurrence.

[Pre-prepared input boundary]
- A ChemicalInput may itself have been produced by a distinct preparation workflow described elsewhere.
- Using, citing, or identifying that pre-prepared material as an input does not import its creation or preparation operations into the current ChemicalSynthesis.
- Keep those operations with the distinct workflow that produced the material. The current ChemicalSynthesis contains only operations belonging to its own physical preparation workflow.

[Atomicity]
- Each layer and each distinct use has its own ChemicalInput occurrence.
- Canonical identity and aliases may match across occurrences; ontosyn:referencesMaterial may point those occurrences at the same material.

[Properties]
- Attach amount, formula, role description, purity, and supplier when grounded.

### Datatype Properties

| Property | Range |
|----------|-------|
| `hasAlternativeNames` | `string` |
| `hasAmount` | `string` |
| `hasChemicalDescription` | `string` |
| `hasChemicalFormula` | `string` |
| `hasPurity` | `string` |

### Object Properties

| Property | Range (Target Class) |
|----------|----------------------|
| `isSuppliedBy` | `Supplier` |
| `referencesMaterial` | `Material` |

---

## Class: `ChemicalOutput`

**Parent Classes:** `Species`

**Comment:** [Definition]
The Metal Organic Polyhedron (MOP) product of this ChemicalSynthesis.
Must be a specific MOP compound identified in this paper by name, code, or formula.

[Cardinality]
- Exactly one ChemicalOutput per ChemicalSynthesis.

[Scope]
- Must be a Metal Organic Polyhedron (discrete 3D molecular cage, may be called 'cage', 'molecular cage', 'coordination cage', or 'polyhedron').
- Do NOT create for Metal Organic Frameworks (MOFs - extended 2D/3D networks).
- Use the MOP identifier exactly as written in the paper (prefer short codes like 'MOP-1', 'Cage 2', 'UMC-1').

[Different forms]
- Different forms of the same nominal MOP (different polymorphs, solvates, crystal systems, colors) are DIFFERENT outputs requiring separate ChemicalOutput instances.

[Linking and CCDC identity]
- Must link to parent ChemicalSynthesis via ontosyn:hasChemicalOutput.
- Should link to MetalOrganicPolyhedron via ontosyn:isRepresentedBy.
- Must include CCDC number via ontomops:hasCCDCNumber if available.
- For a directional transformation such as alpha→beta, the ChemicalOutput is the destination product beta, never the transformation procedure label.
- Assign the destination product's established CCDC number to that ChemicalOutput and its represented MetalOrganicPolyhedron.
- If the transformation paragraph omits the number but the paper explicitly maps the destination form to a CCDC number elsewhere, reuse that exact paper-established mapping; do not leave the transformation output unanchored and do not assign the source form's CCDC number.

[Properties]
- Include ontosyn:hasAlternativeNames (alternative names, abbreviations), ontosyn:hasChemicalFormula (molecular formula), ontosyn:hasChemicalDescription (compositional description).

### Datatype Properties

| Property | Range |
|----------|-------|
| `hasAlternativeNames` | `string` |
| `hasChemicalDescription` | `string` |
| `hasChemicalFormula` | `string` |

### Object Properties

| Property | Range (Target Class) |
|----------|----------------------|
| `isRepresentedBy` | `MetalOrganicPolyhedron` |

---

## Class: `ChemicalSynthesis`

**Comment:** [Definition]
Represents one standalone synthetic procedure whose primary purpose is to produce a Metal Organic Polyhedron (MOP) as a chemical product (not merely to prepare a sample of an already-made MOP for measurements).
Create a ChemicalSynthesis instance for each distinct MOP synthesis procedure reported in the paper.

[Scope]
- Only Metal Organic Polyhedra (discrete 3D molecular cages).
- MOPs may be called 'cages', 'molecular cages', 'coordination cages', or 'polyhedra' in papers, and may also be described using related terms such as 'metal-organic cage', 'metal-organic polyhedron', 'nanocage', 'nanocapsule', 'nanoball', or 'nanosphere'.
- Treat these names as examples: any wording that clearly denotes a discrete metal-organic cage/polyhedron (finite, non-extended species) SHOULD be treated as a MOP.
- Do NOT create for: Metal Organic Frameworks (MOFs - extended 2D/3D networks), purely organic ligands, precursors, other non-MOP compounds, intermediates, side products, decomposition products, or amorphous/ill-defined solids.

[Host-guest inclusion compounds]
- Do NOT treat host-guest inclusion / co-crystal forms as synthesis products.
- Any material written as Host-n(Guest), or described as a guest-loaded composite, inclusion compound, or co-crystal of an already identified host, is not a standalone ChemicalSynthesis, even if it has its own experimental heading, deposit number, or guest added in the same preparation.
- Count only the guest-free host.
- If the host is never separately prepared, still do not treat the inclusion composite as a ChemicalSynthesis.
- A guest-induced change that the paper treats as a new cage/MOP is a transformation, not an inclusion exception.

[Different forms]
- MOPs with different forms (polymorphs, crystal systems, space groups, solvates, colors) are considered DIFFERENT MOP products ONLY when the paper reports a distinct synthesis or transformation procedure that produces each form.
- Do NOT create separate ChemicalSynthesis instances solely because different formulas, solvate contents, or crystal data are reported for the same experimental batch from a single preparation.
- Alternate handling-state or measurement-state labels for the same already-made product (for example fresh versus dried versus activated versus after analysis) are not different forms, unless the paper both reports a structural change that defines a new MOP and describes an independent producing workflow for that form.

[Different methods]
- The same MOP product obtained by different synthetic methods or routes requires separate ChemicalSynthesis instances per route.
- A route exists only when the source assigns it to that product: it names the alternative starting materials and/or conditions for that product, or states an explicit substitution against a referenced procedure.
- Do not construct an unstated route by crossing a condition change written for one product with an alternative starting material written only for another product.
- Wording that only reuses a preceding procedure plus a stated change copies that procedure and that change. It does not copy every alternative mentioned earlier in the paper.
- A passing remark that other starting materials or conditions can also afford the same product is not itself a route. Create a separate ChemicalSynthesis only when the source assigns that alternative its own procedure, or states a substitution that it treats as a distinct producing workflow.

[Transformations between different MOPs]
- Transformations between two different MOPs are considered as separate ChemicalSynthesis instances.
- IMPORTANT: Transformation from one MOP form to another (e.g., XXX-alpha to XXX-beta, or vice versa) is a separate ChemicalSynthesis instance.
- This includes structural transformations such as alpha↔beta interconversion, desolvation/resolvation that changes structure, guest-induced cage-to-cage transformations, and polymorph transformations.
- Each directional transformation (alpha→beta and beta→alpha) requires its own ChemicalSynthesis instance when described.
- The transformation ChemicalSynthesis label identifies the procedure, but its single ChemicalOutput must identify the destination MOP form.
- The source MOP belongs in the ChemicalInput set.
- Anchor the destination output to the destination form's CCDC number whenever that form-to-CCDC mapping is explicitly established anywhere in the paper; alpha→beta uses beta's CCDC and beta→alpha uses alpha's CCDC.
- Never use the transformation label itself as the product identity, never copy the source CCDC to the destination, and never omit a known destination CCDC merely because the transformation paragraph does not repeat it.
- Create a ChemicalSynthesis instance for each transformation procedure when: (a) the authors explicitly describe the product as a new cage/MOP/coordination cage/polyhedron or as a new crystalline form (e.g., 'new polymorph', 'new phase', 'beta-form', 'guest-induced cage'); OR (b) there is clear structural evidence (a form-defining new crystal structure, a distinct PXRD pattern assigned to a new phase, or equivalent) that the paper uses to define a new MOP.
- A later structure determination, refinement, or database deposit for the same cage after handling, activation, desolvation that retains the structure, or measurement is not by itself a transformation and does not satisfy (b).

[Excluded treatments]
- Do NOT create separate instances for post-synthetic treatments of the same MOP whose purpose is sample preparation for analysis (e.g., activation, drying, grinding, soaking, solvent exchange for characterization, guest removal for measurement) when they do not yield a new well-defined MOP structure.
- Treatments that lead only to loss of solvent, changes in guest content, activation, or loss of crystallinity/amorphization, without a clearly reported new MOP, MUST NOT create new ChemicalSynthesis instances, even if new labels are introduced (e.g., '1a', '1b').
- Do NOT create a ChemicalSynthesis for a measurement-state or handling-state alias of an already-made crystal or product, even when that alias has its own diffraction experiment, crystal-data table, or deposit number.
- Additional structure determinations of an unchanged product after sample handling or analysis remain characterizations of the original ChemicalSynthesis.

[Cardinality]
- Create at most one ChemicalSynthesis per physical preparation route.
- If several characterizations (including multiple structural models, refinements, deposit numbers, or crystal-data tables) all refer to the same experimental batch or the same already-made product after handling, they SHARE a single ChemicalSynthesis instance.

[Required linking]
- Must link to bibo:Document via ontosyn:retrievedFrom.
- Must have exactly one ontosyn:ChemicalOutput via ontosyn:hasChemicalOutput.
- May have multiple ontosyn:ChemicalInput via ontosyn:hasChemicalInput.
- May have document context (section headings, paragraph labels) via ontosyn:hasDocumentContext.

[Context filter]
- When identifying ChemicalSynthesis instances, focus on explicit synthetic descriptions in Experimental/Synthesis/Preparation sections.
- Ignore sample-handling steps described only under characterization sections (e.g., TGA, PXRD, gas sorption, NMR, AFM) unless the text clearly states that these steps produce a new cage/MOP/coordination cage/polyhedron that satisfies rule (5).

[Conservative behaviour]
- When it is ambiguous whether a procedure produces a new MOP or merely modifies an existing one, DO NOT create a ChemicalSynthesis instance; ambiguous or weakly supported cases must be excluded rather than included.

[Critical exclusions for extraction]
- Do NOT extract text that appears under a dedicated Activation, Characterization, or analysis heading. Those headings treat an already-made product and are outside this ChemicalSynthesis.
- Do NOT extract an operation that the source itself frames as activation or as preparation of a measurement-ready or activated sample, even when that sentence sits at the end of an Experimental passage.
- Do NOT extract post-synthesis analysis procedures or measurement-only sample preparation.
- Solvent exchange, soaking, cooling, flash-freezing, isolation, and brief washing that remain inside the Experimental, Synthesis, or Preparation passage of this procedure stay in scope. They are not excluded merely because the product already exists.
- Opening a ChemicalSynthesis still requires a product-forming procedure. That requirement does not strip in-procedure workup from an instance that is already opened.

[No generic or umbrella entities]
- Every ChemicalSynthesis must identify one source-supported physical preparation workflow.
- Never create a family-level, section-level, plural, or otherwise generic synthesis entity that summarizes multiple products, routes, or preparations.
- When a passage reports several products or several routes, resolve each explicitly described workflow separately; if the evidence does not support a specific workflow identity, omit the entity instead of emitting a generic placeholder.

[Split outcomes]
- When one continuous source passage produces a shared intermediate and then names N distinct outcomes, create exactly N ChemicalSynthesis instances — one per named outcome.
- Do not also keep a parent or family-level entity for the unsplit prefix.
- Each outcome's workflow is the unsplit prefix plus that outcome's exclusive continuation. Sibling exclusive continuations belong only to their own outcome.

[Precursor boundary]
- A procedure whose intended output is a ligand, salt, building unit, intermediate, or any other non-MOP precursor is not a ChemicalSynthesis, even when that precursor is later consumed by a valid MOP synthesis.
- Mentioning a precursor inside a MOP procedure does not turn the precursor preparation into an additional MOP synthesis.

[Route identity]
- Treat procedures as distinct when the paper explicitly presents independent preparation workflows distinguished by method, route, starting-material strategy, operation sequence, or separately reported experimental protocol, even if they target the same named MOP.
- Do not merge such routes into one umbrella entity.
- Conversely, minor parameter variants within one reported physical preparation are not separate entities unless the paper presents them as independently executable product-forming workflows.

[Post-synthetic product formation]
- A post-synthetic operation is a separate ChemicalSynthesis only when it is an independently described workflow whose intended result is an explicitly identified MOP product that differs in composition, connectivity, structure, or product identity from its MOP input.
- A treatment that merely handles, activates, exchanges solvent in, characterizes, or conditions an existing product is not a ChemicalSynthesis.
- A later diffraction experiment or deposit number on an already-made product is not sufficient to create a ChemicalSynthesis.

[Identity-bearing label]
- The ChemicalSynthesis label must be the most specific source-supported procedure identity.
- Preserve an explicit product identifier and any route, method, direction, or transformation qualifier needed to distinguish the workflow from every other procedure in the same document.
- Never use a label that could refer collectively to several procedures.
- Do not invent qualifiers that are absent from the source.

[Producing workflow]
- A ChemicalSynthesis denotes the complete producing workflow of its outcome, not merely the first span that names that outcome.
- The first span that names the outcome is an identity anchor, not a start bound.
- The workflow includes any earlier same-source operations that later in-scope sentences consume, the identifying span, and this outcome's exclusive continuation until the workflow ends.
- Do not redirect the procedure to a nearby product or route, and do not merge it with another procedure merely because labels, inputs, or products overlap.

### Object Properties

| Property | Range (Target Class) |
|----------|----------------------|
| `hasChemicalInput` | `ChemicalInput` |
| `hasChemicalOutput` | `ChemicalOutput` |
| `hasDocumentContext` | `DocumentContext` |
| `hasEquipment` | `LabEquipment` |
| `hasSynthesisStep` | `SynthesisStep` |
| `hasYield` | `AmountOfSubstanceFraction` |
| `retrievedFrom` | `Document` |
| `usesEquipment` | `Equipment` |

---

## Class: `DocumentContext`

**Comment:** [Definition]
Location in the source paper of a procedure description: a section title, subsection heading, or paragraph label/number, written verbatim. This class is a document location, not a procedure, step, or quantity.

[Positive evidence]
- EXAMPLES: section titles ('Experimental Section', 'Synthesis of Compound 1'), subsection headings, paragraph labels or numbers.

[Identity]
- Identity is the verbatim location string within one paper.
- Two procedures that cite the same heading name the same location.

[Cardinality]
- Attach at most one location per ChemicalSynthesis when a distinct heading is identifiable; otherwise omit.
- That limit is mention count on that procedure, not a requirement that each procedure mint a new location individual.

[Naming]
- Use exact headings/labels as written in the paper.

[Linking]
- Point to the location from ChemicalSynthesis via ontosyn:hasDocumentContext.

[Exclusions]
- Capture only structural document markers (sections, subsections, paragraph IDs), not narrative content or procedural text.

---

## Class: `Dry`

**Parent Classes:** `SynthesisStep`

**Comment:** [Definition]
A product-workflow step that explicitly removes guest solvent, process solvent, or moisture by drying or evacuation.

[Positive evidence]
- Use for 'dried under vacuum', 'evacuated to remove solvent/guest', oven drying, or comparable explicit drying when it belongs to the product-producing synthesis or workup.

[Operation boundaries]
- Vacuum alone does not imply Evaporate: drying or evacuation for guest/solvent removal is Dry, while Evaporate requires an explicit evaporation, concentration, or solvent-removal process.

[Properties]
- Attach explicit drying agent, temperature, pressure, duration, and vacuum evidence.

[Exclusions]
- Storage, standing, aging, soaking, and keeping a product in solvent are not Dry.
- Exclude evacuation or drying that the source frames as activation or as measurement-sample preparation, and exclude dedicated Activation or Characterization sections.
- Do not use this exclusion to drop Experimental-passage soaking, cooling, or freeze; those belong to HeatChill.

[Atmosphere]
- If the Dry evidence explicitly says 'dried in air', 'air-dried', 'under nitrogen/N2', 'under argon/Ar', 'under an inert atmosphere', 'under vacuum', or otherwise names an atmosphere, the extraction ledger MUST preserve it and the KG MUST link the corresponding VesselEnvironment via ontosyn:hasVesselEnvironment.
- Never drop an explicit drying atmosphere and never infer one when unstated.

### Datatype Properties

| Property | Range |
|----------|-------|
| `hasOrder` | `integer` |
| `hasParameter` | `string` |

### Object Properties

| Property | Range (Target Class) |
|----------|----------------------|
| `hasDryingAgent` | `ChemicalInput` |
| `hasDryingPressure` | `Pressure` |
| `hasDryingTemperature` | `Temperature` |
| `hasStepDuration` | `Duration` |
| `hasVessel` | `Vessel` |
| `hasVesselEnvironment` | `VesselEnvironment` |
| `usesEquipment` | `Equipment` |

---

## Class: `Equipment`

**Parent Classes:** `LabEquipment`

**Comment:** [Definition]
Process equipment explicitly used during synthesis (e.g., autoclave, oven, ultrasonic bath, centrifuge, vacuum line, heat/chill device).

[Positive evidence]
- INCLUDE: autoclave, oven, furnace, ultrasonic bath, sonicator, centrifuge, vacuum line, vacuum pump, hotplate, magnetic stirrer, heating mantle, rotary evaporator.

[Exclusions]
- Exclude analytical instruments (e.g., NMR, IR, PXRD).
- EXCLUDE: analytical instruments (NMR, IR, PXRD, GC-MS, HPLC, XRD, TGA, DSC, SEM, TEM), characterization equipment, general glassware.

[Cardinality]
- Multiple per paper.

[Linking]
- Link to steps via ontosyn:usesEquipment or to ChemicalSynthesis via ontosyn:hasEquipment for global equipment.

[Naming]
- Use verbatim equipment names/brands if stated.

---

## Class: `Evaporate`

**Parent Classes:** `SynthesisStep`

**Comment:** [Definition]
A synthesis step for an explicit evaporation or concentration process.

[Positive evidence]
- Use only when the source states evaporation, concentration, solvent removal as a process, rotary evaporation/rotavap, or equivalent explicit volatile-removal wording.
- An explicitly open or uncapped vessel maintained for an extended interval to concentrate a solution or form product by volatile loss supports Evaporate even when the word "evaporate" is omitted.

[Operation boundaries]
- Slow evaporation that produces crystals remains Evaporate and carries its explicit duration; do not add a passive HeatChill for that same interval.
- When an open or uncapped extended hold yields crystals, prefer Evaporate over HeatChill if volatile loss is the supported operative mechanism; never duplicate the same interval as both operations.
- Reduced pressure or vacuum alone is not Evaporate: drying or evacuation for guest/solvent removal is Dry when in product-workflow scope.

[Properties]
- Attach explicit rotary-evaporator, temperature, pressure, target-volume, removed-species, and duration properties.

[Atmosphere]
- If this step's verbatim evidence explicitly names an atmosphere, including 'in air', nitrogen/N2, argon/Ar, inert atmosphere, or vacuum, the extraction ledger MUST preserve it and the KG MUST link the corresponding VesselEnvironment via ontosyn:hasVesselEnvironment.
- Never drop an explicit atmosphere and never infer one when unstated.

### Datatype Properties

| Property | Range |
|----------|-------|
| `hasOrder` | `integer` |
| `hasParameter` | `string` |
| `hasRotaryEvaporator` | `boolean` |

### Object Properties

| Property | Range (Target Class) |
|----------|----------------------|
| `hasEvaporationPressure` | `Pressure` |
| `hasEvaporationTemperature` | `Temperature` |
| `hasStepDuration` | `Duration` |
| `hasVessel` | `Vessel` |
| `hasVesselEnvironment` | `VesselEnvironment` |
| `isEvaporatedToVolume` | `Volume` |
| `removesSpecies` | `ChemicalInput` |
| `usesEquipment` | `Equipment` |

---

## Class: `ExecutionPoint`

---

## Class: `Filter`

**Parent Classes:** `SynthesisStep`

**Comment:** [Definition]
A distinct solid-liquid workup operation evidenced by explicit filtration or by explicit washing of retained or isolated solid material.

[Type identity vs sequences]
- Clause-to-sequence patterns live on SynthesisStep. Do not assign Filter as the sole type when the parent sequence also requires HeatChill or Add.
- Filter type identity still requires explicit filtration or brief retained-solid washing that is not a duration-bearing soak or exchange hold.

[Positive evidence]
- Create Filter for phrases such as 'filtered', 'collected by filtration', or 'the crystals/precipitate/powder were washed with X', unless the parent clause-to-sequence patterns assign that same interval to a duration-bearing HeatChill hold.
- The retained-solid washing statement is itself sufficient Filter-workup evidence even when filtration is not independently named, except when that statement is a duration-bearing soak or solvent-exchange hold.
- Collection, isolation, or 'crystals were obtained' alone, without filtration or washing, is insufficient.

[Atomicity]
- Create one Filter per distinct filtration or retained-solid washing event and keep separately stated events separate.
- Attach every washing solvent for one event with a separate hasWashingSolvent relation; do not split the Filter merely because multiple solvents or repeated washes are stated.

[Properties]
- Record explicit vacuum filtration, repetition count, and operation duration.

[Exclusions]
- Never create Add for a washing solvent.
- If the same sentence explicitly states a different Separate operation, retain Separate and create Filter as well only when independent filtration or retained-solid washing evidence is present.
- Do not create Filter from 'isolated', 'collected', or 'obtained' when those words only report that crystals formed or were taken for diffraction/analysis.
- Do not create Filter for ligand, salt, or other non-MOP precursor isolations that occur before the target MOP procedure.
- A duration-bearing solvent exchange or soak is not a synthesis-step Filter; apply the parent clause-to-sequence patterns.

[Atmosphere]
- If this Filter or retained-solid washing evidence explicitly names an atmosphere, including 'in air', nitrogen/N2, argon/Ar, inert atmosphere, or vacuum, the extraction ledger MUST preserve it and the KG MUST link the corresponding VesselEnvironment via ontosyn:hasVesselEnvironment.
- Never drop an explicit atmosphere and never infer one when unstated.

### Datatype Properties

| Property | Range |
|----------|-------|
| `hasOrder` | `integer` |
| `hasParameter` | `string` |
| `isRepeated` | `integer` |
| `isVacuumFiltration` | `boolean` |

### Object Properties

| Property | Range (Target Class) |
|----------|----------------------|
| `hasStepDuration` | `Duration` |
| `hasVessel` | `Vessel` |
| `hasVesselEnvironment` | `VesselEnvironment` |
| `hasWashingSolvent` | `ChemicalInput` |
| `usesEquipment` | `Equipment` |

---

## Class: `HeatChill`

**Parent Classes:** `SynthesisStep`

**Comment:** 【Warning】
[Definition]
A controlled heating or cooling transition, an explicit heating phase, an explicit cooling or flash-freezing phase, or a passive duration-bearing hold when no more specific operation owns that interval.

[Type identity vs sequences]
- Clause-to-sequence patterns live on SynthesisStep. When a parent sequence includes HeatChill together with Add or Filter, emit HeatChill as that member and do not consume the other members.
- HeatChill type identity still decides whether a member is HeatChill: a thermal transition, an explicit heating, cooling, or flash-freeze phase, or a residual passive duration-bearing hold.
- An explicit cooling or flash-freeze phase remains HeatChill even when the same sentence reports isolation or a crystalline result.

[Positive evidence]
- Controlled heating or cooling between thermal states is HeatChill.
- Explicit heating, cooling, or flash freezing is HeatChill.
- Passive duration-bearing standing, aging, soaking, immersion, or equilibration at a stated or ambient temperature is HeatChill when no more specific operation owns the duration.

[Operation ownership]
- If Evaporate, Dry, Stir, Filter, Sonicate, Separate, Transfer, or Add is explicitly stated to last for the interval, attach the duration to that operation and do not add a passive HeatChill for the same interval.
- HeatChill may own a duration only as a residual class: use it when no more specific allowed class already owns that interval. It is not a retyping override for an already supported Stir, Add, Transfer, Sonicate, Evaporate, or Dry.
- "Passive" means the source names only a hold (stand, age, soak, immerse, equilibrate) and supplies no more specific operation language. Permissive or continuative wording around an already explicit operation does not make that interval a passive HeatChill.
- A duration-bearing soak, standing, aging, immersion, or solvent-exchange hold has HeatChill type identity and overrides Filter or Separate only, even when the source uses washed, exchanged, or separated wording for that same interval. It never overrides Stir or any other explicitly named allowed class. Whether a preceding Add is also required is decided by the parent clause-to-sequence patterns.
- A controlled thermal transition is not Transfer merely because a vessel is named or sealed.
- Explicit milling, ball milling, grinding, or comparable mechanochemical agitation is Stir under the allowed type set, not HeatChill.
- A duration, ambient or room temperature, closed or sealed vessel, vacuum, or equipment mention does not independently establish HeatChill and must not retype an interval that already has explicit stirring or agitation language. Sealing and vacuum may be attached only after thermal-transition, explicit heating/cooling, or true passive-hold evidence has established the operation type.

[Atomicity]
- One continuous heat-to-temperature-and-hold phase is one HeatChill, not separate ramp and hold steps.
- Explicit cooling, flash freezing, or another distinct thermal phase is a separate HeatChill and must not be merged with later heating.

[Properties]
- Attach target temperature, rate, duration, vacuum, stirring, and device only when supported.
- Sealing follows ontosyn:isSealed: HeatChill heating defaults to sealed=true unless the source explicitly says the vessel is open or unsealed; cooling inherits the preceding heating seal unless the mixture is moved to a new vessel.

[Atmosphere]
- If the HeatChill evidence explicitly names an atmosphere, including 'in air', nitrogen/N2, argon/Ar, inert atmosphere, or vacuum, preserve it and link the corresponding VesselEnvironment via ontosyn:hasVesselEnvironment.
- Never drop an explicit thermal-phase atmosphere and never infer one when unstated.

### Datatype Properties

| Property | Range |
|----------|-------|
| `hasOrder` | `integer` |
| `hasParameter` | `string` |
| `hasVacuum` | `boolean` |
| `isSealed` | `boolean` |
| `isStirredHeatChill` | `boolean` |

### Object Properties

| Property | Range (Target Class) |
|----------|----------------------|
| `hasHeatChillDevice` | `HeatChillDevice` |
| `hasStepDuration` | `Duration` |
| `hasTargetTemperature` | `Temperature` |
| `hasTemperatureRate` | `TemperatureRate` |
| `hasVessel` | `Vessel` |
| `hasVesselEnvironment` | `VesselEnvironment` |
| `usesEquipment` | `Equipment` |

---

## Class: `HeatChillDevice`

**Parent Classes:** `LabEquipment`

**Comment:** A heating or cooling device used by a HeatChill step (e.g., oven, furnace, heating mantle, cold bath).

---

## Class: `MetalOrganicPolyhedron`

**Parent Classes:** `CoordinationCage`, `MolecularCage`

**Comment:** A discrete 3D molecular cage structure formed by metal nodes/clusters coordinated with organic linkers. Also referred to as 'cage', 'molecular cage', 'coordination cage', or 'polyhedron' in literature. CRITICAL: MetalOrganicPolyhedron refers ONLY to discrete molecular cages, NOT Metal Organic Frameworks (MOFs), which are extended 2D/3D coordination networks/polymers. MOPs are finite, self-assembled structures with defined stoichiometry, while MOFs are infinite extended networks.

### Datatype Properties

| Property | Range |
|----------|-------|
| `hasCCDCNumber` | `string` |

---

## Class: `Separate`

**Parent Classes:** `SynthesisStep`

**Comment:** [Definition]
A synthesis step that physically separates components based on their properties.

[Positive evidence]
- **CRITICAL: EXPLICIT LANGUAGE REQUIRED.**
- Create Separate only from explicit physical-separation language or an explicit object-removal construction.
- Valid evidence includes 'separated', 'phase was separated', 'decanted', 'extracted', 'partitioned', and explicit removal of a physical component such as precipitate, excess solvent, supernatant, or a liquid phase.
- The object must identify the separated component; collection or isolation alone is insufficient.

[Exclusions]
- Do not infer Separate from crystals forming or being obtained.
- Never duplicate one source operation as both Separate and Filter.

[Properties]
- Use hasSeparationSolvent only for explicitly named separation media.

[Operation boundaries]
- If decanting only removes a component without a new vessel, use Separate; add Transfer only for independently stated movement to a distinct vessel.
- A duration-bearing soak or solvent-exchange hold is not Separate. Apply the parent SynthesisStep clause-to-sequence patterns. Brief retained-solid washing belongs to Filter.

[Atmosphere]
- If this step's verbatim evidence explicitly names an atmosphere, including 'in air', nitrogen/N2, argon/Ar, inert atmosphere, or vacuum, the extraction ledger MUST preserve it and the KG MUST link the corresponding VesselEnvironment via ontosyn:hasVesselEnvironment.
- Never drop an explicit atmosphere and never infer one when unstated.

### Datatype Properties

| Property | Range |
|----------|-------|
| `hasOrder` | `integer` |
| `hasParameter` | `string` |

### Object Properties

| Property | Range (Target Class) |
|----------|----------------------|
| `hasSeparationSolvent` | `ChemicalInput` |
| `hasStepDuration` | `Duration` |
| `hasVessel` | `Vessel` |
| `hasVesselEnvironment` | `VesselEnvironment` |
| `isSeparationType` | `SeparationType` |
| `usesEquipment` | `Equipment` |

---

## Class: `SeparationType`

**Comment:** A descriptor for the explicit technique used by a Separate step. Link it from Separate via ontosyn:isSeparationType. It is not an operational SynthesisStep and must not carry ontosyn:hasOrder.

---

## Class: `Sonicate`

**Parent Classes:** `SynthesisStep`

**Comment:** [Definition]
A synthesis step that uses ultrasonic energy to mix, disperse, dissolve, or break down materials.

[Positive evidence]
- Use for explicit ultrasonic treatments such as 'sonicate', 'sonicated', 'subjected to ultrasonication', or 'treated in an ultrasonic bath'.

[Properties]
- Represent explicit sonication durations using ontosyn:hasStepDuration.
- Attach the vessel and atmosphere using ontosyn:hasVessel and ontosyn:hasVesselEnvironment when stated.

[Exclusions]
- Do NOT infer Sonicate from generic mentions of mixing or agitation without explicit ultrasonic wording.

[Atmosphere]
- If this step's verbatim evidence explicitly says 'in air', 'under nitrogen/N2', 'under argon/Ar', 'under an inert atmosphere', 'under vacuum', or otherwise names an atmosphere, the extraction ledger MUST preserve it and the KG MUST link the corresponding VesselEnvironment via ontosyn:hasVesselEnvironment.
- Never drop an explicit atmosphere and never infer one when unstated.

### Datatype Properties

| Property | Range |
|----------|-------|
| `hasOrder` | `integer` |
| `hasParameter` | `string` |

### Object Properties

| Property | Range (Target Class) |
|----------|----------------------|
| `hasStepDuration` | `Duration` |
| `hasVessel` | `Vessel` |
| `hasVesselEnvironment` | `VesselEnvironment` |
| `usesEquipment` | `Equipment` |

---

## Class: `Stir`

**Parent Classes:** `SynthesisStep`

**Comment:** 【Warning】
[Definition]
A distinct stirring operation supported by explicit stirring or agitation language.

[Positive evidence]
- Use Stir only when the source explicitly states stirring, agitation, or an equivalent stirring action.
- Treat explicit milling, ball milling, grinding, or comparable mechanochemical agitation as Stir when no more specific allowed mechanical-treatment step type exists. Preserve the source wording and duration; do not retype the interval as HeatChill merely because it has a duration or occurs in a closed vessel.
- All materials involved in an actual Stir must first be introduced by Add steps in source order.

[Exclusions]
- Do not infer Stir from mixing, standing, aging, soaking, or the passage of time.
- 'A dissolved in X and B in Y were mixed/combined' is an addition-only pattern unless explicit stirring is independently stated. Emit the required component Add steps and do not emit Stir for generic 'mixed/combined' wording.

[Waiting boundary]
- A hold with no stirring or agitation language is not Stir.
- A duration-bearing hold at a stated or ambient temperature is HeatChill only when that hold has no explicit stirring, agitation, or equivalent mechanical-treatment language.
- Permissive or continuative framing of an explicit stirring action still supports Stir. Duration, ambient or room temperature, and a closed vessel do not retype that Stir as HeatChill.
- Use isWait only for an explicitly described waiting phase within an otherwise explicit Stir operation. Never use isWait to retype passive standing or aging as Stir.

[Properties]
- Attach explicit stirring temperature and duration when stated.
- When stirring occurs during Add or HeatChill, use those step-specific stirring properties instead of creating a duplicate Stir.

[Atmosphere]
- If the step's verbatim evidence explicitly names an atmosphere, including 'in air', nitrogen/N2, argon/Ar, inert atmosphere, or vacuum, preserve it and link the corresponding VesselEnvironment via ontosyn:hasVesselEnvironment.
- Never drop an explicit atmosphere and never infer one when unstated.

### Datatype Properties

| Property | Range |
|----------|-------|
| `hasOrder` | `integer` |
| `hasParameter` | `string` |
| `isWait` | `boolean` |

### Object Properties

| Property | Range (Target Class) |
|----------|----------------------|
| `hasStepDuration` | `Duration` |
| `hasStirringTemperature` | `Temperature` |
| `hasVessel` | `Vessel` |
| `hasVesselEnvironment` | `VesselEnvironment` |
| `usesEquipment` | `Equipment` |

---

## Class: `Supplier`

**Comment:** [Definition]
A supplier of chemical inputs, only include the supplier is explicitly mentioned in the article.

[Positive evidence]
- CREATE ONLY when explicitly mentioned in paper.
- EXAMPLES OF VALID SUPPLIERS: 'Sigma-Aldrich', 'Alfa Aesar', 'TCI', 'Merck', 'Fisher Scientific', 'Acros Organics', 'Strem Chemicals', 'Tokyo Chemical Industry'.

[Exclusions]
- Do NOT create for: implied suppliers, generic 'commercial sources', or unstated suppliers.

[Cardinality]
- Zero or more per paper.

[Linking]
- Link to ChemicalInput via ontosyn:isSuppliedBy.

[Placeholder policy]
- If supplier unknown, use the label 'N/A'.
- Do not loop on 'N/A' attachments.

---

## Class: `SynthesisStep`

**Comment:** [Definition]
A concrete operational action within a ChemicalSynthesis.
Allowed types only: Add, Stir, HeatChill, Evaporate, Sonicate, Transfer, Separate, Filter, Dry.
Avoid generic placeholders (e.g., Prepare, Work up).

[Subclass typing]
- CRITICAL SUBCLASS TYPING: SynthesisStep is an abstract parent for ordered-member semantics, not an output step label when a concrete subclass applies.
- During extraction and KG construction, apply [Clause-to-sequence] first, then assign each emitted member the most specific allowed subclass supported by operation evidence. Strong ownership links may confirm an already supported type: hasAddedChemicalInput -> Add; hasWashingSolvent/isVacuumFiltration/isRepeated -> Filter, except when a parent clause-to-sequence pattern assigns that interval to HeatChill; isTransferedTo/hasTransferedAmount -> Transfer; drying properties -> Dry; evaporation properties -> Evaporate; sonication wording -> Sonicate; separation wording/properties -> Separate.
- hasTargetTemperature may support HeatChill only together with explicit heating, cooling, thermal-transition, or true passive-hold evidence. isSealed, hasVacuum, a duration, a vessel, or equipment never establishes HeatChill by itself.
- Explicit stirring, agitation, milling, ball milling, grinding, or comparable mechanochemical treatment maps to Stir when no more specific allowed mechanical-treatment subclass exists. isWait supports Stir only within an independently explicit Stir operation; it does not retype passive waiting as Stir.
- HeatChill's duration-bearing residual assignment and its Filter/Separate soak override must not retype an interval that already has explicit stirring or agitation language.
- Do NOT output or materialize generic SynthesisStep entries such as 'SynthesisStep 1' when any allowed subclass rule matches.

[Occurrence decomposition]
- Step occurrences follow the ontology's subclass-specific atomicity, not grammatical sentence or verb count.
- One source clause may ground multiple SynthesisStep occurrences when it names multiple independently owned operations or materials.
- Reusing the same verbatim span as evidence for those distinct occurrences is permitted and is not duplication.
- In particular, a clause naming N explicit introduced materials requires N Add occurrences, with no upper bound on N, even when the source describes one simultaneous physical charging event.
- Likewise, separately supported operations in one clause, such as retained-solid washing followed by drying, remain distinct Filter and Dry occurrences.
- When a clause matches a [Clause-to-sequence] pattern, emit every member of that sequence as a distinct occurrence before applying subclass-only winner selection.

[Clause-to-sequence — mandatory]
A source clause maps to an ordered sequence of allowed SynthesisStep subclasses, not to a single winning type. When a pattern below matches, emit every member of that sequence as a distinct occurrence, in the stated order. Do not substitute one member for another. Reusing the same verbatim span as evidence for those members is permitted and is not duplication. Apply the first matching pattern. If none match, fall back to subclass-specific atomicity.

1. Newly introduced contacting solvent with a stated hold
   Source: a solvent or exchange medium is newly named as the contacting liquid, and the same clause or the immediately following clause states a hold duration, standing, soaking, aging, immersion, equilibration, or periodic replacement during that hold.
   Sequence: Add(that solvent), then HeatChill(the stated hold).
   Periodic replacement of the same contacting solvent during one stated hold remains one HeatChill. Do not unroll it into repeated Filter or Add cycles.

2. Already-present contacting solvent with a stated hold
   Source: the contacting solvent is already in the vessel; the clause only states a timed soak, stand, age, immerse, equilibrate, or repeated replacement of that same solvent.
   Sequence: HeatChill only.

3. Brief retained-solid wash or explicit filtration
   Source: filtration, or brief washing of retained or isolated solid, without a duration-bearing soak, stand, age, immerse, equilibrate, or solvent-exchange hold.
   Sequence: Filter only.
   The washing liquid is hasWashingSolvent on that Filter, never Add.

4. Distinct named thermal phases
   Source: the clause names more than one distinct thermal action among flash-freeze, heating, and cooling.
   Sequence: one HeatChill per named distinct thermal phase, in source order.
   Evacuate, seal, or vacuum language is a property of the owning thermal HeatChill, not a Separate.
   One continuous heat-to-temperature-and-hold phase is a single HeatChill.
   Do not invent a cooling HeatChill when the source names only heating or hold.
   If the same sentence then states filtration or retained-solid washing, append Filter after the thermal HeatChill members.

5. Explicit cooling or freeze followed by isolation wording
   Source: exactly one explicit cooling, return-to-ambient, or flash-freeze phase is named, and the same sentence continues with filtered, washed, isolated, collected, or crystals obtained.
   Sequence: HeatChill for that thermal phase, then Filter only when independent filtration or retained-solid washing evidence is present. Isolation or crystal-obtained wording alone does not add Filter and does not cancel the HeatChill.
   Later isolation words are not a sequencing cue that retypes cooling as Filter.

6. Passive duration-bearing hold that yields a crystalline result
   Source: stand, age, soak, immerse, or equilibrate with a duration or ambient hold, and a crystalline result is reported, with no more specific allowed operation owning that interval.
   Sequence: HeatChill only. Do not emit a separate crystallization operation for the crystalline result.

7. Named introductions then explicit stirring
   Source: named materials are introduced and an independent stirring or agitation action is stated.
   Sequence: one Add per named introduced material, then Stir.
   Generic mixed or combined wording without explicit stirring does not add Stir.

These sequences stay in scope when they appear in the Experimental, Synthesis, or Preparation passage of an already opened ChemicalSynthesis. They are not dropped because the product already exists. Dedicated Activation, Characterization, or analysis sections, and operations the source itself frames as activation or measurement-sample preparation, remain excluded.

[Ordering]
- Each step MUST have ontosyn:hasOrder starting at 1 and increasing by 1.
- Each step MUST have ontosyn:hasOrder (xsd:integer) starting at 1 and strictly increasing by 1 with no gaps per ChemicalSynthesis.

[Properties]
- Each step SHOULD state vessel, atmosphere (ontosyn:VesselEnvironment), duration, equipment, and step-specific parameters as written.
- Should attach ontosyn:hasVessel (verbatim container), ontosyn:hasVesselEnvironment (atmosphere only: N2, Ar, air, vacuum - **ONLY when explicitly stated; use 'N/A' or omit when not stated**), ontosyn:hasStepDuration (om-2:Duration as written), step-specific parameters via ontosyn:hasParameter (free text key-value).
- May link to process Equipment via ontosyn:usesEquipment (exclude analytical instruments).

[Exclusions]
- ALLOWED TYPES STRICT: Do NOT create generic placeholders ('Prepare', 'Work up', 'Mix'), narrative descriptions, analytical steps (NMR, IR, PXRD), characterization procedures, or generic SynthesisStep labels when a concrete subclass can be selected.
- CRITICAL EXCLUSIONS: Do NOT extract steps from a dedicated Activation, Characterization, or analysis section, or from an operation the source itself frames as activation or as preparation of a measurement-ready or activated sample.
- Do NOT extract post-synthesis analysis procedures or measurement-only sample preparation.
- Soak, cooling, flash-freeze, solvent exchange, isolation, and brief washing that remain inside the Experimental, Synthesis, or Preparation passage stay in scope. They are not excluded merely because the product already exists.
- "Creates the product" limits which ChemicalSynthesis instances to open. It does not strip in-procedure workup from an already opened instance.

[Cardinality]
- Multiple per ChemicalSynthesis as stated in procedure.

[Linking]
- Must link to parent ChemicalSynthesis via ontosyn:hasSynthesisStep.

[Atmosphere]
- **CRITICAL ATMOSPHERE RULE**: Do NOT create hasVesselEnvironment link or assume 'air' when atmosphere is not explicitly mentioned in the text - use 'N/A' for atmosphere field or leave hasVesselEnvironment unlinked.
- Evacuation followed by sealing is not an explicit atmosphere. Do not emit VesselEnvironment=Vacuum from that wording; attach isSealed and hasVacuum on the sealing or heating step only.

[Not step types]
- CRITICAL - NOT STEP TYPES: The following are NOT valid SynthesisStep types and MUST be modeled using the [Clause-to-sequence] patterns: 'Soak', 'Immerse', 'Stand', 'Wash' (as standalone), 'Activate', 'Age', 'Equilibrate'.
- Soak, Immerse, Stand, Age, and Equilibrate use patterns 1, 2, or 6.
- Standalone Wash uses pattern 3, unless the same wording is a duration-bearing hold (then pattern 1 or 2).
- Activate is never a step type; apply the Activation-section exclusion instead of inventing an Activate step.

[Stir precondition]
- For any Stir to be created, Add step for the material to be stirred must be added first, as in pattern 7.

### Datatype Properties

| Property | Range |
|----------|-------|
| `hasOrder` | `integer` |
| `hasParameter` | `string` |

### Object Properties

| Property | Range (Target Class) |
|----------|----------------------|
| `hasStepDuration` | `Duration` |
| `hasVessel` | `Vessel` |
| `hasVesselEnvironment` | `VesselEnvironment` |
| `usesEquipment` | `Equipment` |

---

## Class: `Transfer`

**Parent Classes:** `SynthesisStep`

**Comment:** 【Warning】
[Definition]
A physical movement of an already-existing mixture, solution, suspension, filtrate, supernatant, aliquot, or other material stream from one vessel state into a distinct destination vessel state.

[Required evidence]
- A genuine source-to-destination vessel transition is required.
- The source must independently identify the already-existing stream, its source vessel/state, and movement to a distinct destination.

[Exclusions]
- Initial placement or charge of reactants, reagents, or solvents into the reaction vessel is Add.
- Sealing, containing, or heating that vessel does not retroactively create Transfer.
- Vessel mention, closure, thermal treatment, or phase separation without evidence that the pre-existing stream moved between distinct vessel states is insufficient.
- 'A dissolved in X and B in Y were mixed/combined and placed in a vial' does not by itself establish Transfer. Model the named introductions with Add only unless the required source-to-destination evidence is independently stated.

[Separate boundary]
- If decanting only removes a component without a destination vessel, use Separate.

[Properties]
- Attach moved amount, layered-transfer status, and destination only when stated.

[Atmosphere]
- If the step's verbatim evidence explicitly names an atmosphere, including 'in air', nitrogen/N2, argon/Ar, inert atmosphere, or vacuum, preserve it and link the corresponding VesselEnvironment via ontosyn:hasVesselEnvironment.
- Never drop an explicit atmosphere and never infer one when unstated.

### Datatype Properties

| Property | Range |
|----------|-------|
| `hasOrder` | `integer` |
| `hasParameter` | `string` |
| `isLayeredTransfer` | `boolean` |

### Object Properties

| Property | Range (Target Class) |
|----------|----------------------|
| `hasStepDuration` | `Duration` |
| `hasTransferedAmount` | `Volume` |
| `hasVessel` | `Vessel` |
| `hasVesselEnvironment` | `VesselEnvironment` |
| `isTransferedTo` | `Vessel` |
| `usesEquipment` | `Equipment` |

---

## Class: `Vessel`

**Parent Classes:** `LabEquipment`

**Comment:** [Definition]
Physical container used in a step (e.g., Teflon-lined stainless-steel vessel, round-bottom flask).
Keep descriptors verbatim (material, volume).

[Positive evidence]
- EXAMPLES: 'Teflon-lined stainless-steel autoclave', '100 mL round-bottom flask', '23 mL vial', 'Parr reactor'.

[Naming]
- Use verbatim descriptors exactly as written in paper, including material, volume, and identifying features.

[Cardinality]
- One or more per ChemicalSynthesis. The same physical container may be named in multiple steps.

[Linking]
- Link to step via ontosyn:hasVessel.
- May link to VesselType via ontosyn:hasVesselType if type classification explicit.

[Exclusions]
- Do NOT use generic terms like 'vessel', 'container', 'flask' without specific descriptors.

### Object Properties

| Property | Range (Target Class) |
|----------|----------------------|
| `hasVesselType` | `VesselType` |

---

## Class: `VesselEnvironment`

**Comment:** [Definition]
Atmosphere for a vessel during a step (e.g., Nitrogen atmosphere, Argon atmosphere, Ambient air, Vacuum).
Do not use for non-atmospheric contexts.

[Allowed values]
- Nitrogen atmosphere, N2, Argon atmosphere, Ar, Ambient air, Air, Vacuum, Inert atmosphere.
- EXAMPLES OF VALID USE: 'under nitrogen', 'argon atmosphere', 'in air', 'under vacuum'.
- Sealing after evacuation is not a valid VesselEnvironment value and does not authorize Vacuum as an atmosphere.

[Exclusions]
- CRITICAL CONSTRAINT: VesselEnvironment MUST be atmosphere only; NEVER use for solvents, solvent mixtures, reaction mixtures, or liquid media.
- EXAMPLES OF INVALID USE: 'methanol/water mixture' (this is a solvent system, NOT a VesselEnvironment), 'DMF solution' (this is a reaction medium, NOT atmosphere), 'the reaction mixture' (NOT an atmosphere).
- Evacuation followed by sealing or flame-sealing is not atmosphere evidence. Do not create VesselEnvironment=Vacuum from that wording, and do not inherit Vacuum onto charging, mixing, or other non-sealing steps.

[Positive evidence]
- **CRITICAL - DO NOT CREATE FOR UNSTATED ATMOSPHERES**: Do NOT create VesselEnvironment for unstated atmospheres or implicit conditions.
- Explicit evidence includes both a local operation statement and an explicit shared-context statement that semantically quantifies an atmosphere over a procedure, named procedure family, section, or document.
- A statement such as all syntheses of a named family being performed in an argon-filled glovebox explicitly governs every compatible step of those syntheses; it is not an inference from silence.

[Global-context resolution and inheritance]
- **GLOBAL-CONTEXT RESOLUTION AND INHERITANCE**: Resolve shared context from the complete source before per-entity extraction.
- Preserve the exact statement as a source dependency of every covered target.
- Inherit the stated environment onto all compatible SynthesisStep occurrences within the statement's declared scope, unless a narrower explicit statement overrides it or an explicit exception excludes an occurrence.
- A shared-context statement inherits as atmosphere only when it names an atmosphere that governs the covered steps. A shared sealing or evacuation method is not an atmosphere and does not inherit as VesselEnvironment.
- Never propagate to siblings outside that scope.

[Linking]
- Materialize inherited context as step-local hasVesselEnvironment links; do not invent a context-only operation and do not attach the property to ChemicalSynthesis.
- Link to each covered step via ontosyn:hasVesselEnvironment.

[Atmosphere]
- **CRITICAL - DO NOT DEFAULT TO 'AIR'**: Do NOT assert Air or Ambient air unless local or applicable shared-context text explicitly states air.
- Silence, sealing, or ordinary handling does not imply air.

[Practical patch]
- For capped vials explicitly stated to be in air, set environment='air' and do not infer vacuum/inert.
- Evacuation followed by sealing or flame-sealing is a sealing operation, not a step atmosphere. On the relevant HeatChill or other thermal/hold step, set isSealed=true and hasVacuum=true. Do not create VesselEnvironment=Vacuum from that wording, and do not inherit Vacuum onto earlier charging or mixing steps.
- If an inert gas fill before sealing is explicit, set inert=true and keep hasVacuum=false unless evacuation is maintained.
- A maintained named atmosphere remains VesselEnvironment.

[Cardinality]
- Zero or one per SynthesisStep; zero is required when neither local nor applicable shared-context atmosphere evidence exists.

---

## Class: `VesselType`

**Comment:** A vessel-shape or vessel-class descriptor (e.g., round-bottom flask, autoclave, vial), not the physical container itself.

---
