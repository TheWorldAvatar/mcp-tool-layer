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
