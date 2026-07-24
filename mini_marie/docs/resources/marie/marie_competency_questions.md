# Marie competency / example questions

Natural-language example questions from the Marie demo ([theworldavatar.io/demos/marie](https://theworldavatar.io/demos/marie)). Grouped as on the homepage **Example Questions** tabs. Each maps to a chemistry Blazegraph namespace and MCP tools derived from the local T-Box.

**Refresh:** `python -m mini_marie.marie.chemistry.collect_marie_questions`

---

## Chemical Species (general)

- **Namespace:** `ontospecies`
- **MCP:** `chemistry-ontospecies`
- **Tools:** `lookup_individuals`, `get_linked_values`, `filter_by_literal`, `count_instances` (online LIMIT 5)

### MQ1

How many hydrogen bonds can an ethylene glycol molecule accept and donate?

### MQ2

Find all uses of 3-amino-2-propanol

### MQ3

Show me all species with molecular formula C6H8O6

## Chemical Species (acid-base ionisation constants)

- **Namespace:** `ontospecies`
- **MCP:** `chemistry-ontospecies`
- **Tools:** `lookup_individuals`, `get_linked_values`, `filter_by_literal`, `count_instances` (online LIMIT 5)

### MQ4

What are the pKa values for species with InChI string InChI=1S/C6H15N/c1-5(2)7-6(3)4/h5-7H,1-4H3?

### MQ5

What pKa values are reported for propenoic acid?

### MQ6

What are the pKa values of acid CCCC(=O)O?

### MQ7

What pK values are reported for InChI=1S/C2H6N2/c1-2(3)4/h1H3,(H3,3,4)?

### MQ8

What is the temperature dependence of the pKa of acetic acid?

### MQ9

For perrin2, what is the temperature dependence of pKaH1?

### MQ10

What species have pKa values reported at high pressures?

### MQ11

What species have the most pKaH values?

### MQ12

What are the ionic strengths associated with the pK values of methylamine?

### MQ13

List all compounds with pK values that appear across multiple data sources.

### MQ14

Which pK measurement methods are associated with “Uncertain” reliability assessments most often?

### MQ15

List all compounds that have both pKa and pKb measurements.

### MQ16

Which pK measurements are tagged Uncertain?

### MQ17

Which provenance references contribute to measurements for methylamine (InChI=1S/CH5N/c1-2/h2H2,1H3)?

### MQ18

List all compounds studied by Perrin together with their counts of pK measurements.

### MQ19

Which pK measurements carry an acidity label of “AH”?

## Gas-Phase Reaction Mechanisms

- **Namespace:** `ontokin`
- **MCP:** `chemistry-ontokin`
- **Tools:** `lookup_individuals`, `get_linked_values`, `filter_by_literal`, `count_instances`, `traverse_mechanism_reactions`

### MQ20

List all mechanisms available

### MQ21

Find mechanisms that include the reactions H + OH + M = H2O + M and HO2 + H = O + H2O

### MQ22

List all mechanisms that involve O2 and Ar

### MQ23

List all reactions involved in the mechanism linked to www.osti.gov/servlets/purl/90098-26Ev73/webviewable/

### MQ24

List all the reactions that consume H2O2 in the mechanism https://doi.org/10.1002/kin.20026

### MQ25

What are the reactions in which H2 reacts to form OH radical?

### MQ26

Please compare the rate constant parameters of the reaction H2 + OH = H2O + H across all mechanisms it appears in.

### MQ27

What is the kinetic model of the chemical reaction H2O2 + OH = HO2 + H2O described in www.osti.gov/servlets/purl/90098-26Ev73/webviewable/?

### MQ28

Show all transport models described in https://doi.org/10.1016/j.combustflame.2007.10.024

### MQ29

Compare thermodynamic models of O2 across all the mechanisms in which it appears

### MQ30

Compare thermodynamic models of species classified as organic radical across all the mechanisms in which it appears

## Quantum Chemistry Computations

- **Namespace:** `ontocompchem`
- **MCP:** `chemistry-ontocompchem`
- **Tools:** `lookup_individuals`, `get_linked_values`, `filter_by_literal`, `count_instances`, `query_calculation_results`

### MQ31

Compare zero-point energy of Ar calculated using CC-pVTZ vs CC-pVQZ basis set

### MQ32

What are the LUMO and HOMO energies of H calculated at UB3LYP level of theory? Please also specify the basis set in the results.

### MQ33

What are the rotational constants for H2O calculated at the RB3LYP level and CC-pVDZ basis set?

## Zeolites

- **Namespace:** `ontozeolite`
- **MCP:** `chemistry-ontozeolite`
- **Tools:** `lookup_individuals`, `get_linked_values`, `filter_by_literal`, `count_instances`, `query_zeolite_property`

### MQ34

List all zeolitic materials recorded for framework code AEN

### MQ35

What is the reference zeolite for SFN?

### MQ36

What is the zeolite framework of |(Quin)|[Si34O68]?

### MQ37

Retrieve unit cell information of zeolitic material |Na20|[Al20Si76O192]

### MQ38

Show the tile information of zeolite UOZ

### MQ39

What is the occupiable area per cell of zeolite AFY?

### MQ40

Find zeolitic materials with triclinic lattice system

### MQ41

Show me all zeolites with accessible area per cell greater than 500 Å² and occupiable volume per cell less than 200 Å³.

### MQ42

Give me a list of all guest species that have been recorded for zeolite framework FAU

### MQ43

What are zeolitic materials that take H2S as guest species?

### MQ44

Show me zeolite frameworks incorporating tetraethylammonium

### MQ45

Find zeolitic materials built by Ta and N

### MQ46

Find zeolite frameworks made up of zinc and phosphorus only

### MQ47

Show me the species incorporated by zeolites made of Al and P elements

### MQ48

Find me the provenance of zeolitic material |(EDA)2(H)4|[Mg4P4O16]

## Metal-Organic Polyhedra

- **Namespace:** `ontomops`
- **MCP:** `chemistry-ontomops`
- **Tools:** `lookup_individuals`, `get_linked_values`, `filter_by_literal`, `count_instances`, `ontomops_instance_routing` → use `twa-mops` / `mof-twa` for instances

### MQ49

Which MOPs have an outer diameter greater than 70 Angstrom?

### MQ50

What MOPs are based on the CBU [(C6H3)O(CH2)13CH3(CO2)2] and what are their inner sphere diameters?

### MQ51

What type of AM has the MOP with the largest pore size diameter?

### MQ52

Are there MOPs have a molecular weight greater than 30,000?

### MQ53

What are all the MOPs using the CBU with formula [Mg4C56H76O12S4]?

### MQ54

What assembly models are representative of icosahedral geometry?

### MQ55

What is the provenance of the information about MOP [V3O2(OH)2(HCO2)3]4[(C6H4)(C3H2N2)2]6?

### MQ56

What MOPs are associated with DOI 10.1016/j.chempr.2017.02.002?

### MQ57

Which chemical building units are used as 2-linear generic building units?

### MQ58

Which MOPs have calculated RMSD to the initial geometry greater than 1.5?

### MQ59

Which MOPs have a HOMO-LUMO gap less than 1 eV?

### MQ60

Which MOPs were calculated using the xTB software and a DMF implicit solvation model?

### MQ61

What calculation parameters (name, numeric value, unit) were used for MOP  [Zr3O(OH)3(C5H5)3]4[(C6H4)(CO2)2]6?

---

## Tool design notes (T-Box → MCP)

| Namespace | T-Box anchors | Generic MCP tools |
|-----------|---------------|-------------------|
| `ontospecies` | `Species`, `hasMolecularFormula`, `hasDissociationConstants`, `hasHydrogenBondDonorCount`, `hasUse` | `lookup_individuals`, `get_linked_values`, `filter_by_literal`, `count_instances` |
| `ontokin` | `ReactionMechanism`, `GasPhaseReaction`, `hasReaction`, `hasEquation` | + `traverse_mechanism_reactions` |
| `ontocompchem` | `HOMOEnergy`, `LUMOEnergy`, `ZeroPointEnergy`, `RotationalConstants` | + `query_calculation_results` |
| `ontozeolite` | `ZeoliteFramework`, `hasFrameworkCode`, `hasZeoliticMaterial`, `isReferenceZeolite` | + `query_zeolite_property` |
| `ontomops` | `MetalOrganicPolyhedron`, CBU (T-box only) | routing → `twa-mops` / `mof-twa` |
