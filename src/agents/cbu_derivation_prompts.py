INSTRUCTION_PROMPT = """
    Guidance for Deriving a Minimal Metal Building Unit (CBU)

    Objective
    From the provided structure data and literature description, derive the smallest chemically complete, symmetry-distinct building unit (CBU) that captures the core composition of a metal-containing fragment.

    ** Top priority: ** : Strictly output and only output the Metal CBU formula, nothing else, especially no commentary or explanation.

    Scope and Exclusions

    Include:
    - All metal atoms forming the core of the cluster.
    - Ligands or atoms that:
        - Bridge two or more metal centers, or
        - Are terminal but conserved across all symmetry-equivalent sites in the cluster, or
        - Are required to complete the minimal chemically meaningful unit.


    Exclude:
    - Any ligands, anions, or groups that:
    - Bind to only one metal center and do not repeat throughout the cluster,
    - Are purely decorative or peripheral,
    - Are non-repeating decorations or spectators not essential for defining the core structure.
    - Solvents, counterions, and non-coordinating guests.

    Component Classification

    - Metals: Include all metal atoms directly bonded in the cluster core.
    - Ligands and nonmetals: Include only if they meet the inclusion criteria above.
    - Detect and group chemically identical ligands or subunits (e.g. OCHx, SOx, COx, OH, H₂O) as molecular groups, not as raw atoms.
    - If a ligand type (e.g., SO₄, NO₃, CO₃) appears multiple times in the structure, include only those that are structurally part of the core metal cluster. Exclude equivalents that serve as counterions, linkers, or external decorations even if they are metal-bound. Only include one instance if all others are symmetry-related or structurally redundant.

        
    Metal CBU Rules:

    - When deriving the metal CBU, exclude any ligand fragments that belong to the external linkers rather than the inorganic core, even if they appear coordinated in the vertex description. Only include groups intrinsic to the metal cluster itself, not groups that connect to external organic linkers.
    
    - Do not omit neutral or non-coordinating organic substituents that are covalently bonded to ligand atoms. The full molecular identity of each ligand group must be preserved in the formula, even if the substituent does not directly bind to the metal center.
    
    - Include terminal ligands (e.g., neutral donors) if they are chemically bonded to the metal center and appear repeatedly or symmetrically across equivalent metal sites. Do not omit terminal groups that contribute to the primary coordination environment of the metal.
    
    - If the identified fragment originates from a larger symmetric assembly, reduce the component counts to the smallest valid integer unit that reflects the chemically complete, symmetry-distinct subunit. Do not report per-assembly totals; report only the per-unit formula for the minimal building block.
    
    - If a neutral ligand (e.g., H₂O) is coordinated but only weakly bound or partially occupied, consider whether it should be treated as part of the oxo/hydroxo network instead of a discrete water molecule. Use bonding environment and structural symmetry to decide whether to retain or absorb it into the total oxygen count.
    
    - Do not include explicit solvent molecules (e.g., H₂O) in the metal CBU unless they are symmetry-conserved, tightly bound, and essential to the cluster’s identity. If ambiguous, prefer representing them via their oxygen contribution (e.g., as O or OH).
    
    - Do not include coordinated solvent molecules (e.g. H₂O) in the metal CBU if they are weakly bound, asymmetrically coordinated, or absent in symmetry-equivalent positions. Unless explicitly required to complete the core connectivity or appear consistently across the cluster, treat such groups as external and omit them from the final formula.
    
    - **Critical**: Exclude weakly bound or non-bridging solvent molecules (e.g., H₂O, CH₃OH, DEF) from the metal CBU unless they are fully occupied, symmetrically present at every equivalent site, and essential to the core metal coordination. When in doubt, assign such oxygen atoms to the core oxo count rather than as neutral solvent molecules.
    
    - If multiple identical capping or counter anionic groups are present around a metal cluster (e.g., SO4, PO4, NO3) but the cluster acts as a single node or vertex in a larger assembly, include only the minimal number of such groups needed to represent the chemically distinct building unit. Do not list all symmetry-equivalent or redundant anions from the full cluster formula; reduce to the smallest chemically complete fragment.


    **Critical**: Reduction to CBU
    You must reduce the cluster to its smallest valid repeating fragment using the following process:
    0. **Vital**: If the paper provides a full cluster formula (e.g., with multiple equivalent ligands or polyhedral nodes), do **not** assume all components are part of the minimal CBU. Instead, use the structural role described (e.g., vertex in polyhedron, repeating subunit) to infer whether the formula includes symmetry-equivalent or redundant groups. Only retain components essential to the smallest symmetry-distinct subunit of the building block. If ambiguity remains, favor reducing the formula to the minimal chemically valid unit consistent with the core topology. Don't full rely on the paper's formula, trust your own judgement.


    1. Count the total number of metal atoms, μ3-O atoms, μ2-OH groups, and caps (e.g. Cp).
    
    2. Find the greatest common divisor (GCD) across all nonzero counts.
    
    3. Divide all component counts by this GCD to obtain the smallest integer unit preserving internal ratios.
    
    4. Ensure the final unit reflects the same metal:cap:oxo:hydroxo ratios as the original cluster.
    
    5. Do not report full clusters if they can be reduced. Always return the minimal formula that preserves stoichiometry.
    
    6. If the structure contains multiple topologies with equivalent reduced forms, return the shared minimal CBU.


    Fragment Grouping Rules

    - Identify and group atoms into chemically meaningful ligand units (e.g. OCHx, SOx) where applicable.
    - Count the number of occurrences of each group.
    - Do not return flattened atomic formulas (e.g. avoid raw atom listings like CxHyOz).
    - If a group appears multiple times in the cluster, express it once with its subscript .

    Stoichiometry and Reduction

    1. Identify all atoms and grouped subunits that satisfy the inclusion rules.
    2. Count the metal atoms and grouped ligands.
    3. If the formula is a multiple of a smaller repeating unit, reduce it by the greatest common divisor (GCD).
    4. Preserve internal stoichiometric ratios. Do not reduce out meaningful chemical structure.
    5. Exclude any groups that are not structurally or chemically required for the core definition.

    Canonical Representation Rules

    - **Critical**: No synonymous name should be used in the output, only the standard molecular formula should be used.
    - Represent all CBUs as empirical formulas in bracketed form.
    - Use chemical groupings for ligands , not raw element counts.
    - Use correct element order and grouping:
    - Metals first, followed by grouped ligand units.
    - Subscripts must be integers. Omit subscript 1.
    - Do not include charges, μ-labels, oxidation states, or coordination descriptors.
    - Normalize all synonymous groups to a standard molecular formula.
    - Output formula must be compact and syntactically normalized. No spaces between element/group tokens. For example, use [Fe3O(SO4)3(C5H5N)3] instead of spaced variants. Group order: metals first, then oxo/hydroxo, then neutral or anionic ligands.


    Hydrogen and Partial Occupancy Handling

    - If hydrogen atoms are refined, include them.
    - If missing, infer based on bonding patterns and coordination.
    - Round disordered or fractional occupancies to chemically reasonable integers.

    **Critical**: Output Format

    - Return only the Metal CBU in this format:
    Metal CBU:   [empirical_formula_with_groups]
    - Do not return commentary or explanation.
    - Do not return flattened element counts.

    The RES file is below:

    {res_content}

    The MOL2 file is below:

    {mol2_content}

    The concentrated paper content is below:

    {paper_content}
    """


INSTRUCTION_PROMPT_ENHANCED_1 = """
Guidance for Deriving a Minimal Metal Building Unit (CBU)

Objective  
From the provided structure data and literature description, derive the smallest chemically complete, symmetry-distinct building unit (CBU) that captures the core composition of a metal-containing fragment.

**Top priority:** Strictly output and only output the Metal CBU formula, nothing else — no commentary, no explanation.

---------------------------------------------------------------------------------------
Scope and Exclusions

Include:
- All metal atoms forming the core of the cluster.
- Ligands or atoms that:
    - Bridge two or more metal centers, or
    - Are terminal but conserved across all symmetry-equivalent sites in the cluster, or
    - Are required to complete the minimal chemically meaningful unit.

Exclude any ligands, anions, or groups that:
- Serve to bridge between the metal cluster and external organic ligands or framework.
- Bind to only one metal center and do not repeat across the core structure.
- Appear in only one symmetry-inequivalent site.
- Are externally-oriented, non-chelating, or clearly organic in nature (e.g. CO₂, COO⁻, aromatic spacers).
- Are used to connect clusters together (e.g., carboxylate bridges), even if they are coordinated to the metal — these belong to the **organic CBU**, not the metal CBU.
- Solvents, counterions, and non-coordinating guests.

In particular, always exclude: Organic linker fragments of any kind.

These are always part of the **organic CBU**, even if coordinated to metal centers.

---------------------------------------------------------------------------------------
Component Classification

- Metals: Include all metal atoms directly bonded in the cluster core.
- Ligands and nonmetals: Include only if they meet the inclusion criteria above.
- Detect and group chemically identical ligands or subunits (e.g., OCHx, SOx, COx, OH, H₂O) as molecular groups, not as raw atoms.
- Count only those groups that are **symmetry-conserved** and intrinsic to the inorganic core.

Do not rely on inclusion just by coordination — ligands must be essential to the core cluster itself.

---------------------------------------------------------------------------------------
Metal CBU Rules

- Exclude any ligand fragments that belong to external organic linkers, even if coordinated.
- Preserve full molecular identity of ligand groups (e.g., OCH₃, SO₄), even if not directly coordinated.
- Include terminal ligands only if they appear symmetrically and repeatedly across all metal sites.
- Reduce component counts to the **smallest chemically complete subunit**, not the full assembly.
- Weakly bound solvent-like ligands (H₂O, CH₃OH) are excluded unless fully occupied, essential, and conserved across symmetry.
- Assign uncertain oxygen groups to oxo/hydroxo if possible, not H₂O.

---------------------------------------------------------------------------------------
**Critical**: Reduction to CBU  
You must reduce the cluster to its smallest valid repeating fragment using the following process:

0. **Vital**: If the paper provides a full cluster formula, do **not** assume all components are part of the CBU. Use the paper’s structural description (e.g., "vertex of polyhedron", "tetrameric unit") to infer whether redundancy exists. Always favor the smallest symmetry-distinct chemically complete unit.

1. Count total metal atoms, μ₃-O atoms, μ₂-OH groups, and caps (e.g., Cp).
2. Compute the greatest common divisor (GCD) across all nonzero counts.
3. Divide all counts by GCD to reduce to smallest unit.
4. Ensure final unit preserves correct internal stoichiometric ratios.
5. Always prefer minimal formula — never output full clusters if they can be reduced.
6. If multiple topologies yield equivalent reduced units, return the shared minimal CBU.

---------------------------------------------------------------------------------------
Fragment Grouping Rules

- Identify and group atoms into chemically meaningful ligand units (e.g., OCH₃, SO₄).
- Express each group as a single token with subscript.
- Never return flattened atomic formulas (e.g., avoid CₓHᵧO_z).
- Represent chemically standard groups by canonical names (e.g., SO₄ not S₁O₄).

---------------------------------------------------------------------------------------
Stoichiometry and Reduction

1. Identify all atoms and groups based on the above inclusion/exclusion rules.
2. Count only those needed to represent the **inorganic cluster**.
3. Reduce component counts if they are clearly multiples of a smaller unit.
4. Exclude groups not essential to the inorganic core or repeated across the structure.
5. Use judgement to separate core vs. connecting groups.

---------------------------------------------------------------------------------------
Canonical Representation Rules

- Return the CBU in normalized empirical formula using brackets.
- Group order: **Metals → Oxo/Hydroxo → Neutral/Anionic Ligands**
- Subscripts must be integers (omit 1).
- No charges, μ-labels, oxidation states, or commentary.
- No line breaks, no explanation, no extra lines — only the final bracketed formula.

---------------------------------------------------------------------------------------
Hydrogen and Occupancy Handling

- If H atoms are refined, include them.
- If missing, infer based on coordination.
- Round disordered or fractional occupancies to nearest reasonable integer.

---------------------------------------------------------------------------------------
Final Safety Rule

If the derived formula includes **organic groups** (e.g., CO₂, pyridyls, PhPO₃, C₆H₅, C₆H₄) and also a known **core inorganic cluster**, then:
- > Strip the organic parts from the CBU. These belong to the **organic CBU**, not the metal CBU.

---------------------------------------------------------------------------------------
Output Format

- Return only the Metal CBU in this format:
  `Metal CBU:   [empirical_formula_with_groups]`

- Do not include commentary, explanation, or raw atomic counts.
- Do not return any organic CBU or linker fragments.

---------------------------------------------------------------------------------------
The RES file is below:
{res_content}

The MOL2 file is below:
{mol2_content}

The concentrated paper content is below:
{paper_content}
"""

INSTRUCTION_PROMPT_ENHANCED_2 = """
Guidance for Deriving a Minimal Metal Building Unit (CBU)

Objective  
From the provided structure data and literature description, derive the smallest chemically complete, symmetry-distinct building unit (CBU) that captures the core composition of a metal-containing fragment.

**Top priority:** Strictly output and only output the Metal CBU formula, nothing else — no commentary or explanation.

Scope and Exclusions

Include:
- All metal atoms forming the core of the cluster.
- Ligands or atoms that:
    - Bridge two or more metal centers, or
    - Are terminal but conserved across all symmetry-equivalent sites in the cluster, or
    - Are required to complete the minimal chemically meaningful unit.

Exclude:
- Any ligands, anions, or groups that:
    - Bind to only one metal center and do not repeat throughout the cluster,
    - Are purely decorative or peripheral,
    - Are non-repeating decorations or spectators not essential for defining the core structure.
- Solvents, counterions, and non-coordinating guests.

Component Classification

- **Metals**: Include all metal atoms directly bonded in the cluster core.
- **Ligands and nonmetals**: Include only if they meet inclusion criteria.
- **Group ligands**: Detect and group chemically meaningful ligands or fragments (e.g., alkoxides, oxo groups, anionic capping groups).
- If a ligand type appears multiple times in the structure, include only those that are structurally part of the core. Exclude equivalents that act as linkers or external decorations, even if metal-bound.

Metal CBU Rules

- Exclude any ligand fragments that belong to external linkers.
- Retain full molecular identity of each ligand (including organic substituents) if part of the functional group.
- Retain terminal ligands only if:
    - Chemically bonded to the metal center,
    - Present symmetrically or repeatedly across the cluster.
- Exclude solvents unless tightly bound, fully occupied, and symmetry-required.
- If any neutral ligand or molecule (e.g., solvent) is coordinated but weakly or asymmetrically, prefer absorbing its contribution (e.g., as O or OH) instead of retaining it.
- Reduce any duplicated substructure to its smallest chemically complete unit — avoid repeating the full cluster.

Ligand Classification and Inclusion Criteria

1. **Inorganic oxo or hydroxo groups**  
   - Always retain if they bridge metal centers or define cluster geometry.

2. **Functionalized inorganic ligands**  
   - Retain full group, including attached organic substituents (e.g., phenyl, methyl).  
   - Do not truncate or generalize these groups.

3. **Terminal neutral donors (e.g., N-, O-heterocycles, solvents)**  
   - Retain only if directly coordinated and fully conserved across equivalent sites.  
   - Exclude if weakly bound, partially occupied, or inconsistent.

4. **Organic anionic ligands**  
   - Retain only if clearly intrinsic to the metal core.  
   - If they are part of organic linker scaffolds or span between clusters, exclude them from metal CBU.

5. **Capping groups or monoanionic organics**  
   - Retain if directly bound to metals and symmetrically required.  
   - Represent them using full chemical formulas — never abbreviations.

6. **Solvent molecules**  
   - Exclude unless fully occupied, symmetry-equivalent, and essential to the coordination environment.  
   - Otherwise absorb them into generic oxygen/hydrogen contribution if needed.

7. **Organic substituents on ligands**  
   - Retain only if covalently attached to functionalized ligand atoms (e.g., organic-phosphonate).  
   - Exclude if they are part of extended organic frameworks or side chains.

8. **Extended organic linkers (e.g., carboxylate-connected polyaromatics)**  
   - Always assign to the organic CBU — exclude from the metal CBU.

Reduction to CBU

You must reduce the cluster to its smallest valid repeating fragment:

0. **Critical**: Do not assume the full formula in the paper is already reduced. Use symmetry and function in the crystal (e.g., as a vertex, edge, or repeating unit) to determine which portion is the minimal chemically meaningful unit.
1. Count all included metals, oxo/hydroxo groups, and organic ligand units.
2. Compute the greatest common divisor (GCD) across component counts.
3. Divide by the GCD to find the smallest unit preserving stoichiometry.
4. Ensure that internal ratios of ligand types are maintained.
5. Do not report total units from a larger polyhedral or supramolecular structure.
6. If multiple equivalent topologies exist, report the shared minimal form.

Fragment Grouping

- Express ligands as grouped chemical units (e.g., alkoxides, sulfonates, phosphonates).
- Avoid raw element-by-element listing.  
- Do not flatten to atomic counts — use chemical groups.

Output Representation

- Always return empirical formula using:
  - Metals first, then grouped inorganic ligands (oxo, hydroxo), then organic or functional ligands.
- Subscripts must be integers; omit subscript 1.
- Do not include:
  - Charges, oxidation states, μ-labels, or coordination descriptions.
  - Abbreviations like Cp, Py, etc. — always expand to full molecular formula.
- Output format must be compact and fully normalized:  
  - Use square brackets, no spaces, and correct group ordering.

Hydrogen and Partial Occupancy Handling

- Include H atoms if they are explicitly present in the model.
- If absent, infer based on bonding and typical valency.
- Round disordered or fractional occupancy to nearest chemically reasonable integer.

**Critical: Output Format**
- Return the final result as:
Metal CBU:   [<empirical_formula_with_groups>]

Do not return:
- Any other commentary
- Flattened atom counts
- Structural analysis

The RES file is below:

{res_content}

The MOL2 file is below:

{mol2_content}

The concentrated paper content is below:

{paper_content}
"""
INSTRUCTION_PROMPT_ENHANCED_3 = """
Guidance for Deriving a Minimal Metal Building Unit (CBU)

Objective:
Derive the smallest chemically complete and symmetry-distinct metal-based building unit (CBU) from the provided molecular structure files and supporting literature description. This CBU should represent the essential metal-containing cluster that serves as a structural unit in the larger assembly.

OUTPUT REQUIREMENT:
Return strictly and only the Metal CBU formula in normalized empirical format — no commentary, no additional notes, no alternative names, no structural labels.

Sources of Truth:
- Use all three inputs:
  - RES file: definitive crystallographic bonding and occupancy
  - MOL2 file: chemical group identities and connectivity
  - Concentrated paper content: semantic and functional role assignments

Information Hierarchy:
1. Use **RES** file to determine atomic positions, occupancy, coordination environment, and symmetry.
2. Use **MOL2** file to identify molecular fragments, ligand identity, and bonding patterns.
3. Use **Concentrated Paper** only to interpret structural roles (e.g., node, core, linker) and symmetry grouping — not to extract formulae directly.

=== COMPONENT INCLUSION RULES ===

Include:
- All metal atoms that participate in a bonded cluster.
- Atoms or groups that:
  - Bridge multiple metal atoms.
  - Are terminal ligands repeated across symmetry-equivalent sites.
  - Are required to complete a chemically meaningful core structure.

Exclude:
- Any group that:
  - Binds to only one metal atom without recurrence.
  - Appears to serve as a connection point to external linkers.
  - Is structurally peripheral, asymmetrically bound, or not conserved across equivalent sites.
  - Is part of solvent, counterion, or external decorations based on occupancy or symmetry absence.

=== FILTERING ORGANIC AND AMBIGUOUS GROUPS ===

Classification Logic:
- Use chemical bonding and topology, not naming conventions or appearances, to classify atoms and ligands.
- When identifying potential organic groups:
  - Assess whether the group extends into large π-systems, rigid aromatic linkers, or covalent scaffolds.
  - If the group contributes to external framework connectivity (via carboxylate, phosphonate, or related groups), exclude it from the metal CBU.
  - If the group is covalently attached but primarily decorative, exclude it unless it is repeated at all equivalent metal centers.

Disambiguation Rule:
- If a ligand is ambiguous (could be either a terminal group or an external connector), defer to:
  - Repetition and symmetry from RES file.
  - Grouping and identity from MOL2 file.
  - Functional role as described in the paper.

Do not classify groups by name or identifier — always rely on chemical context.

=== REDUCTION TO MINIMAL CBU ===

Step-by-step Reduction:
1. Count all atoms and molecular groups satisfying inclusion rules.
2. Normalize the structure by removing duplicated symmetry-equivalent parts.
3. Reduce counts by greatest common divisor (GCD) if all components are in fixed ratios.
4. Ensure the resulting formula represents a standalone chemically complete unit.

Do NOT:
- Report per-unit cell formulas.
- Include full symmetric aggregates when a smaller unit exists.

=== GROUPING AND REPRESENTATION RULES ===

- Express ligands as molecular groups, not as isolated atoms.
- Maintain intact functional groups (e.g., methoxy, hydroxo, phosphonato, etc.).
- Do not abbreviate (e.g., no short names like Py, Cp, Ph).
- Represent all ligand and substituent groups by their full molecular formula.

=== OUTPUT FORMAT RULES ===

1. Structure:
   - Use square brackets for the entire formula.
   - Begin with metals, followed by core oxo/hydroxo ligands, then additional groups.
   - Use alphabetical order within categories when possible.
   - Subscripts must be integers. Omit subscript 1.

2. Syntax:
   - No charges, oxidation states, μ-labels, or structural descriptors.
   - No commentary or extra explanation.
   - No non-ASCII formatting (e.g., subscripts, superscripts, unicode).

3. Return format:
Metal CBU:   [EmpiricalFormulaInBracketedForm]

Example output format only (not to be used verbatim):
Metal CBU:   [MxLy(Gz)n]

The RES file is below:
{res_content}

The MOL2 file is below:
{mol2_content}

The concentrated paper content is below:
{paper_content}
"""

INSTRUCTION_PROMPT_ENHANCED_4 = """
Guidance for Deriving a Minimal Metal Building Unit (CBU)

Objective
Derive the smallest chemically complete, symmetry-distinct metal-based building unit (CBU) from the provided RES, MOL2, and concentrated paper content. The CBU must capture only the intrinsic inorganic cluster that serves as a structural node.

OUTPUT REQUIREMENT (strict)
Return strictly and only the Metal CBU formula in normalized empirical form — no commentary, no notes, no labels, no charges, no μ-descriptors, no nicknames, no Unicode subscripts/superscripts.
Return format (exact):
Metal CBU:   [EmpiricalFormulaInBracketedForm]

Sources of Truth & Priority
1) RES file: definitive crystallographic bonding/topology, occupancy, symmetry, conserved coordination environments.
2) MOL2 file: chemical group identities and covalent connectivity graph.
3) Concentrated paper: use only to infer structural roles (node/core vs. linker/counterion/guest) and symmetry relationships; do not copy formulae from it.

Component Inclusion Rules
Include:
- All metal atoms belonging to the bonded cluster core.
- Atoms or groups that:
  • Bridge two or more metal atoms, or
  • Are terminal but conserved across all symmetry-equivalent metal sites, or
  • Are necessary to complete a chemically meaningful core unit.

Exclude:
- Any group binding to only one metal without repetition on symmetry-equivalent sites.
- Groups serving as connection points to external organic linkers or extended frameworks.
- Peripheral, weakly bound, partially occupied, or asymmetrically present ligands.
- Solvents, counterions, lattice guests.

Water/Solvent Handling
- Do not include neutral solvent molecules unless fully occupied, symmetry-conserved at all equivalent sites, and essential to the core identity. If ambiguous, absorb their oxygen into the core oxo/hydroxo count only if crystallographically justified; otherwise omit entirely.

Filtering Organic and Ambiguous Groups (principled, graph-based)
- Classify by bonding/topology, not by names or textual labels.
- A donor group is LINKER-DERIVED (thus excluded) if, in the MOL2 connectivity graph, the donor’s α-atom (e.g., carboxylate C or P for phosphonate/sulfonate) is covalently attached to an organic fragment that:
  (a) contains any aromatic or sp2 carbon, OR
  (b) continues for two or more additional C–C bonds beyond the α-atom.
- If a heteroanion bears an organic substituent satisfying (a) or (b), treat that substituent as linker-derived. Retain only the intrinsic inorganic fragment if it is cluster-defining; otherwise drop the whole unit if it primarily serves as a linker connection.
- Terminal donors that remain after this test must be symmetry-conserved across equivalent metal sites to be included.

Residual–Carboxylate Sanity Check
- If any CO2/COO/O2CR motif remains in the candidate metal CBU, verify that its α-carbon has degree 1 with no C–C continuation (i.e., not part of an extended organic fragment) AND that it is symmetry-conserved at equivalent metal sites. Otherwise exclude it.

DETERMINING WHAT TO DROP (operative rules)
- Use RES symmetry + occupancy to eliminate asymmetric, partially occupied, or site-specific decorations.
- Use MOL2 graph traversal from every donor atom (O, N, P, S, etc.) to decide if the group feeds into a larger organic scaffold; if yes, classify as linker-derived and exclude.
- Never decide inclusion/exclusion by name recognition alone; always confirm with connectivity and symmetry.

LINKER EXCISION CHECKLIST (mandatory before reduction)
1) Remove all donor groups failing the Organic-linker donor test.
2) Remove any residual CO2/COO/O2CR groups failing the Residual–Carboxylate sanity check.
3) Normalize heteroanion multiplicity: if multiple identical outer heteroanions surround the cluster, keep only the symmetry-distinct number intrinsic to the core (drop assembly/counterion copies).
4) Verify that no solvent tokens (e.g., water/alcohols/amides) remain unless they passed the solvent rule above.

Reduction to Minimal CBU
0) Confirm that only intrinsic core components remain after the Linker Excision Checklist.
1) Count metals, core oxo/hydroxo ligands, and retained intrinsic groups.
2) Reduce all counts by the greatest common divisor when components are in fixed ratios across symmetry-equivalent sites.
3) Report the smallest symmetry-distinct, chemically complete unit (not per-unit-cell totals; not full supramolecular aggregates).

Grouping & Representation Rules
- Represent ligands as molecular groups (e.g., alkoxy, hydroxo, oxo, heteroanions) rather than flattened atom counts.
- Preserve complete molecular identities of retained groups; do not mix raw atoms unless the group is intrinsically atomic (e.g., oxo).
- Do not emit placeholders such as “CO2/COO/O2CR” unless they passed the linker tests; otherwise exclude entirely.
- Use one canonical notation for equivalent groups (e.g., choose a single orientation for alkoxy and apply consistently).
- Do not abbreviate with nicknames (no Cp, Py, Ph, etc.). Always use explicit chemical formulas.

Canonical Output Rules
- Bracket the whole formula. Order tokens with metals first, then core oxo/hydroxo, then other retained groups.
- Use integers as subscripts; omit subscript 1.
- No charges, oxidation states, μ-labels, or structural descriptors.
- ASCII characters only; no superscripts/subscripts.

=== ADDITIONAL FILTERING RULES FOR NEUTRAL / ASYMMETRIC LIGANDS ===

- Do not include neutral, monodentate ligands (e.g., H2O, MeOH) unless:
  - They are symmetry-equivalent across all metal centers in the cluster, or
  - They are essential to complete the core coordination geometry of every metal atom.

- Treat such ligands as "solvates" or "capping agents" by default and exclude them unless confirmed to be integral to the repeating metal cluster.

=== REFINING ORGANIC/BRIDGING GROUP EXCLUSION ===

- Exclude any carboxylate, phosphonate, or aromatic group that:
  - Extends outward to link clusters into a larger assembly,
  - Is identified in the paper as a linker, spacer, or bridging ligand between nodes,
  - Appears as a large π-system (e.g., BDC, aromatic amines) connected via carboxylate arms.

- Include only bridging groups internal to the metal cluster that:
  - Directly connect two or more metals *within the same cluster*,
  - Are repeated identically on all symmetry-related sites.

=== OXO COUNT AND NORMALIZATION ===

- When metal–oxo or metal–vanadate groups are present (e.g., VO4, V=O):
  - Normalize their oxygen count consistently.
  - Do not double-count oxygens if they are part of a described vanadate fragment.
  - Prefer the “simplest chemically meaningful” formula where VO3/VO4 groups are expressed as part of the metal-oxo framework.

=== FINAL EXCLUSION CHECK ===

- Before finalizing the CBU formula, run a last pass to:
  - Remove any ligands that bind to a single metal atom and are not symmetry repeated.
  - Ensure no external linker groups are retained in the metal CBU.
  - Express neutral donors (e.g., pyridine, H2O) only if they are symmetry-required.

=== Patches ===

If a specific ligand (e.g., SO₄, H₂O, CO₂) appears multiple times per cluster due to coordination at symmetry-equivalent positions, include only the minimal symmetry-distinct count of that ligand.

Do not apply a global formula reduction (i.e., GCD) unless all components are repeated uniformly.
Apply reduction per group, based on symmetry logic derived from the RES file.

External Carboxylate Filter:
Any carboxylate group extending into a large organic fragment → exclude from metal CBU even if directly coordinated.

μ‑O vs H₂O Distinction:

If an oxygen is bridging two or more metals → classify as μ‑O, not H₂O.
Only call H₂O if it’s monodentate and not symmetry-repeated.

Return format (exact):
Metal CBU:   [EmpiricalFormulaInBracketedForm]

The RES file is below:
{res_content}

The MOL2 file is below:
{mol2_content}

The concentrated paper content is below:
{paper_content}
"""

# INSTRUCTION_PROMPT = """
# Guidance for Deriving a Minimal Metal Building Unit (CBU)

# Objective  
# Derive the smallest chemically complete, symmetry-distinct building unit (CBU) from the provided structure data and literature description, representing only the intrinsic metal-containing fragment.

# Scope and Exclusions  

# Include only:
# - All metal atoms forming the core cluster.
# - Ligands or atoms that:
#     - Bridge two or more metal centers, or
#     - Are terminal but appear at every symmetry-equivalent site in the cluster, or
#     - Are chemically essential to complete the minimal unit.

# Exclude without exception:
# - Any ligands, anions, or groups that bind to a single metal and do not repeat,
# - Decorative or peripheral groups not essential for defining the core,
# - Non-repeating decorations or spectators,
# - Solvents, counterions, and non-coordinating guests,
# - Any coordinated solvent molecule (e.g. H₂O, alcohols, amines) that is weakly bound, partially occupied, or absent at symmetry-equivalent sites.  
# If a solvent-like group contributes oxygen but is not symmetry-mandatory, absorb its atom(s) into the oxo count rather than listing it as a discrete molecule.

# Component Classification  

# - Metals: include all metal atoms directly bonded in the core.
# - Ligands and nonmetals: include only if they meet the inclusion rules above.
# - Group identical ligand types into molecular subunits (e.g., OCHx, SOx, POx) rather than raw atoms.
# - If a ligand type appears multiple times, include only those required to define the core. Do not repeat symmetry-equivalent or redundant copies.

# Metal CBU Derivation Rules  

# - Focus strictly on the intrinsic metal cluster, not external linkers or counterions.
# - Preserve the full molecular identity of each ligand group, including neutral substituents covalently attached to coordinating atoms.
# - Terminal ligands are only included if symmetrically present at all equivalent sites and essential to the cluster identity.  
# - Weakly bound neutral ligands or solvents must be omitted or merged into the oxo/hydroxo count if their presence is not symmetry-conserved.  
# - If multiple anionic capping groups (e.g. sulfate-like or phosphate-like groups) are present but are symmetry-equivalent or redundant, reduce to the minimal number needed to represent the chemically distinct building unit.  
# - Always derive the smallest valid repeating unit; do not report the full crystal formula.

# Reduction to CBU  

# 1. Count metal atoms, bridging oxo/hydroxo groups, and all ligand subunits.
# 2. If all counts share a common divisor, divide to obtain the smallest integer formula preserving internal ratios.
# 3. Never reduce out chemically distinct or asymmetric features.
# 4. Never include full-cluster totals; always return the per-unit minimal CBU.

# Canonical Representation  

# - Use only standard empirical molecular formulas.
# - No synonyms, names, charges, μ-labels, oxidation states, or coordination descriptors.
# - Format must be compact and normalized: metals first, then oxo/hydroxo, then neutral or anionic ligands.
# - Subscripts must be integers. Omit subscript 1.
# - Do not return raw atom counts or flattened formulas.

# Hydrogen and Partial Occupancy  

# - Include hydrogen if refined.
# - Infer missing H from bonding patterns only if necessary.
# - Round disordered or fractional occupancies to chemically reasonable integers.

# Output Format  

# Return only the Metal CBU in this format:  
# Metal CBU:   [empirical_formula_with_groups]

# Do not return commentary, explanation, or any additional text.

# The RES file is below:

# {res_content}

# The MOL2 file is below:

# {mol2_content}

# The concentrated paper content is below:

# {paper_content}
# """


CONCENTRATION_PROMPT = """
You are tasked with extracting only the chemically relevant information necessary to derive the **Metal CBU (Cluster Building Unit)** from the following full paper content.

This is not a summary. You must extract only facts and relationships that are directly useful for identifying the smallest chemically complete, symmetry-distinct unit of a **metal-containing cluster**.

## Your Output Must:
- Be written as a clean markdown file.
- Omit all non-structural content (e.g., applications, properties, surface area, porosity).
- Include key details about:
  - The metal atoms and their coordination.
  - All ligands directly bonded to the metal.
  - Structural formulas provided in the text (but **with clear notes** if they represent full units, not reduced fragments).
  - Symmetry, packing, or unit-cell content that implies how many ligands or groups are symmetry-equivalent.

## Rules for Extraction:

1. If the paper gives a full molecular formula (e.g. [M₆XyZz(L)ₙ]), extract it, but add a **clear note** if the formula includes multiple symmetry-equivalent ligands (e.g., 4 SO₄), especially if only one would be needed for the minimal repeating unit.

2. Emphasize **connectivity and geometry** over crystallographic totals:
   - For example, say: “The cluster is used as a vertex in a polyhedron built from 4 such units,” rather than “The unit cell contains 4 clusters.”

3. Do **not** repeat molecular formulas verbatim without qualification.
   - If a formula like [V₆O₆(OCH₃)₉(SO₄)₄] appears, state clearly that the sulfate count may include symmetry-equivalent ligands and **does not necessarily reflect the minimal CBU**.

4. Clarify the cluster's **role in assembly** (e.g. vertex, edge, face) and if it appears **as a repeat unit** in polyhedral cages, reticular networks, etc.

5. Highlight:
   - Whether the cluster can be reduced (e.g., 4 sulfates → 1 symmetry-equivalent SO₄).
   - If authors mention symmetry, equivalent positions, or duplication in unit cell or cluster.

6. Suppress misleading text:
   - Do not include statements that rigidly define the full cluster formula as the minimal unit.
   - Blur such claims with language like “reported formula” or “molecular unit may include redundant ligands.”

## Output Format:
Organize extracted content under these sections if relevant:

1. Identity and Composition of the Metal Cluster
2. Role in Supramolecular Assembly
3. Structural Features
4. Crystallographic Observations
5. Notes for Metal CBU Derivation
6. Clarification or Ambiguities
7. Final Guidance for Agent (optional)

This is the paper content:

{paper_content}
"""
CONCENTRATION_PROMPT_2 = """
You are extracting only the information from the paper that is necessary for a separate agent to later derive the **Metal CBU (Cluster Building Unit)**. 
**Do not perform any derivation, reduction, or interpretation beyond faithfully reporting the paper's facts.**

GOAL
- Produce a clean, neutral **markdown** document that captures all paper facts relevant to identifying the smallest chemically complete, symmetry-distinct metal cluster used as a building unit.
- Include all potentially relevant structural details; exclude distracting or misleading context.

STRICT NON-DERIVATION
- Do **not** compute, infer, or propose any minimal unit.
- Do **not** decide what belongs in or out of a CBU.
- Do **not** reconcile inconsistencies; instead, flag them.

WHAT TO INCLUDE (from the paper only)
- Metal cluster identity and composition as **reported by the authors** (use the paper's own wording for names/formulas; if shorthand is used, provide a normalized chemical formula name alongside it when possible, but do not invent counts).
- Ligand types directly coordinated to the metal (names and formulas as given), their stated roles (e.g., "cap," "bridging," "terminal," "linker-connecting"), and coordination descriptors if reported.
- Statements about the cluster's **role in assembly** (node/vertex/edge/face), the number of clusters per assembly object, and any connectivity descriptions to organic linkers or other clusters.
- Symmetry and duplication cues: mentions of equivalent positions, repeated ligands around a core, multiplicities tied to symmetry, and any per-unit-cell contents vs per-cluster contents.
- Occupancy/disorder notes: partial occupancy, alternative positions, weakly bound donors, coordinated solvent mentions.
- Any author-reported formulas, stoichiometries, or counts relevant to the cluster, linkers, or assembled objects.

WHAT TO SUPPRESS OR BLUR
- Applications, properties, porosity, surface area, catalysis, spectroscopy details unrelated to composition/connectivity.
- Claims that could be **misleading for minimal CBU** determination must be kept but **tagged** to avoid misinterpretation:
  - Mark as **[ASSEMBLY-LEVEL]** if the count clearly refers to an entire cage/network/polyhedron rather than a single metal cluster.
  - Mark as **[POTENTIALLY NON-MINIMAL]** if a reported formula/count likely includes symmetry-equivalent duplicates or per-assembly totals.
  - Mark as **[AMBIGUOUS]** where the text is unclear about whether a group is a core cap vs a linker connection.
  - Mark as **[AUTHOR-REPORTED]** for any verbatim stoichiometry the paper presents without reduction.

NAMING / FORMULA HANDLING
- Use chemical formulas and full group names; avoid bare abbreviations (record both if the paper uses shorthand).
- Do **not** invent or normalize numerical counts that the paper does not explicitly state.
- If multiple notations appear (e.g., shorthand vs spelled-out), list them together and identify that they are synonyms.

CONFLICTS
- If the paper gives inconsistent counts across sections, include both and tag with **[CONFLICT]**; do not resolve.

ORGANIZATION (include sections that apply; keep concise and factual)
1. Identity and Composition of the Metal Cluster  
   - Author-reported names/formulas **[AUTHOR-REPORTED]** (+ tags such as **[POTENTIALLY NON-MINIMAL]** or **[ASSEMBLY-LEVEL]** when warranted).
2. Directly Metal-Bound Ligands  
   - List ligand types as reported; note roles (cap/bridging/terminal/linker-connecting) and any multiplicities/symmetry cues.
3. Role in Assembly and Connectivity  
   - How clusters connect to linkers/other clusters; counts per assembly object; node/vertex/edge/face description.
4. Symmetry, Multiplicity, and Unit-Cell Statements  
   - Any mentions implying duplication/equivalence; clarify per-cluster vs per-cell vs per-assembly with tags.
5. Occupancy / Disorder / Solvent Notes  
   - Fully vs partially occupied donors; weakly bound or labile groups as stated by the authors.
6. Author-Reported Stoichiometries (Verbatim with Tags)  
   - Quote short formula lines with **[AUTHOR-REPORTED]** and add **[POTENTIALLY NON-MINIMAL]** or **[ASSEMBLY-LEVEL]** if appropriate.
7. Ambiguities and Caveats  
   - List **[AMBIGUOUS]** and **[CONFLICT]** statements without resolving them.

STYLE
- Bullet/short sentences; neutral tone; no conjecture.
- No equations, no derived minimal units, no extra commentary beyond tagging.
- Do not add content from outside the provided paper.

Full paper content:
{paper_content}
"""




INSTRUCTION_PROMPT_ENHANCED_3_WITH_CBU = """
Guidance for Deriving a Minimal Metal Building Unit (CBU)

Objective:
Derive the smallest chemically complete and symmetry-distinct metal-based building unit (CBU) from the provided molecular structure files and supporting literature description. This CBU should represent the essential metal-containing cluster that serves as a structural unit in the larger assembly.

OUTPUT REQUIREMENT:
Return strictly and only the Metal CBU formula in normalized empirical format — no commentary, no additional notes, no alternative names, no structural labels.

Sources of Truth:
- Use all three inputs:
  - RES file: definitive crystallographic bonding and occupancy
  - MOL2 file: chemical group identities and connectivity
  - Concentrated paper content: semantic and functional role assignments

Information Hierarchy:
1. Use **RES** file to determine atomic positions, occupancy, coordination environment, and symmetry.
2. Use **MOL2** file to identify molecular fragments, ligand identity, and bonding patterns.
3. Use **Concentrated Paper** only to interpret structural roles (e.g., node, core, linker) and symmetry grouping — not to extract formulae directly.

=== EXISTING METAL CBU DATABASE ===

**CRITICAL GUIDANCE ON REUSING EXISTING CBUs:**

You are provided with a database of known **INORGANIC/METAL CBUs ONLY** below. **It is VERY COMMON and HIGHLY LIKELY that the metal CBU you are deriving already exists in this database.** The vast majority of metal-organic frameworks and coordination polymers reuse well-established metal cluster building units.

**Your workflow should be:**
1. First, derive the metal CBU using the standard rules below.
2. Then, **carefully compare** your derived CBU with the existing CBU database.
3. If you find a matching or very similar CBU in the database:
   - **PREFER the existing CBU formula** from the database.
   - Use the exact formula notation from the database.
   - Only deviate if you are ABSOLUTELY CERTAIN the cluster is chemically distinct.
4. **ONLY propose a new CBU** (not in the database) if you are **VERY, VERY CONFIDENT** that:
   - The metal composition is genuinely novel, or
   - The ligand coordination pattern is substantially different from all existing CBUs, or
   - The stoichiometry cannot be reasonably matched to any existing entry.
   - You are *absolutely sure* that every group, especially organic linkers/groups, is a must keep group in the CBU. (This is very important as it is a very common mistake the derived CBU contains some groups that should not be in the CBU.)
   - **Critical**: In some cases, a part of the group is to be dropped where some atoms are preserved. e.g., a H2O group should be 
   dropped but the O atom should be preserved, adding an extra O atom to some other group in the CBU, changing the element count in the CBUs. 
   Therefore, some CBU in the database may not be directly comparable to the one you derived, but you can still use the database to check whether the CBU you derived is very similar to one in the database.
   - Then you should carefully reflect on whether the CBU you derived should drop some groups or part of the group.
   - **Critical**: Another common mistake is the that the CBU reduction is not done correctly, if the derived CBU is a multiple of an existing CBU in the database, you should use the one in the database.

**Critical**: A common mistake is to derive a CBU with extra groups that are not part of the core cluster.
Therefore, the existing CBU database is very useful to check whether the CBU you derived made that particular mistake. 

**Think of existing CBUs as your "vocabulary" — most structures use words from this vocabulary. Creating a new word should be rare and well-justified.**

In most of the cases, the metal CBU you are deriving already exists in the database. As a result, 
I would suggest you derive the metal CBU first, then rescan through the database to see whether a very similar CBU already exist.

If there is a direct match (ignore different order of groups), you can just return the CBU from the database.

If there is no direct match but something very similar, it is highly likely that you derivation is slightly wrong and 
I suggest you use the one in the database.

In very rare cases, there is a genuinely new metal CBU that is not in the database. In this case, you can propose a new CBU.

{existing_cbu_database}

=== COMPONENT INCLUSION RULES ===

Include:
- All metal atoms that participate in a bonded cluster.
- Atoms or groups that:
  - Bridge multiple metal atoms.
  - Are terminal ligands repeated across symmetry-equivalent sites.
  - Are required to complete a chemically meaningful core structure.

Exclude:
- Any group that:
  - Binds to only one metal atom without recurrence.
  - Appears to serve as a connection point to external linkers.
  - Is structurally peripheral, asymmetrically bound, or not conserved across equivalent sites.
  - Is part of solvent, counterion, or external decorations based on occupancy or symmetry absence.

=== FILTERING ORGANIC AND AMBIGUOUS GROUPS ===

Classification Logic:
- Use chemical bonding and topology, not naming conventions or appearances, to classify atoms and ligands.
- When identifying potential organic groups:
  - Assess whether the group extends into large π-systems, rigid aromatic linkers, or covalent scaffolds.
  - If the group contributes to external framework connectivity (via carboxylate, phosphonate, or related groups), exclude it from metal CBU.
  - If the group is covalently attached but primarily decorative, exclude it unless it is repeated at all equivalent metal centers.

Disambiguation Rule:
- If a ligand is ambiguous (could be either a terminal group or an external connector), defer to:
  - Repetition and symmetry from RES file.
  - Grouping and identity from MOL2 file.
  - Functional role as described in the paper.

Do not classify groups by name or identifier — always rely on chemical context.

=== REDUCTION TO MINIMAL CBU ===

Step-by-step Reduction:
1. Count all atoms and molecular groups satisfying inclusion rules.
2. Normalize the structure by removing duplicated symmetry-equivalent parts.
3. Reduce counts by greatest common divisor (GCD) if all components are in fixed ratios.
4. Ensure the resulting formula represents a standalone chemically complete unit.

Do NOT:
- Report per-unit cell formulas.
- Include full symmetric aggregates when a smaller unit exists.

=== GROUPING AND REPRESENTATION RULES ===

- Express ligands as molecular groups, not as isolated atoms.
- Maintain intact functional groups (e.g., methoxy, hydroxo, phosphonato, etc.).
- Do not abbreviate (e.g., no short names like Py, Cp, Ph).
- Represent all ligand and substituent groups by their full molecular formula.

=== OUTPUT FORMAT RULES ===

1. Structure:
   - Use square brackets for the entire formula.
   - Begin with metals, followed by core oxo/hydroxo ligands, then additional groups.
   - Use alphabetical order within categories when possible.
   - Subscripts must be integers. Omit subscript 1.

2. Syntax:
   - No charges, oxidation states, μ-labels, or structural descriptors.
   - No commentary or extra explanation.
   - No non-ASCII formatting (e.g., subscripts, superscripts, unicode).

3. Return format:
Metal CBU:   [EmpiricalFormulaInBracketedForm]

Example output format only (not to be used verbatim):
Metal CBU:   [MxLy(Gz)n]

The RES file is below:
{res_content}

The MOL2 file is below:
{mol2_content}

The concentrated paper content is below:
{paper_content}
"""
