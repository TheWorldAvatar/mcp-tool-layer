metal_prompt_doi_found = """
Guidance for Deriving a Minimal Metal Building Unit (CBU)

Objective:
Derive the smallest chemically complete and symmetry-distinct metal-based building unit (CBU) from the provided molecular structure files and supporting literature description. This CBU should represent the essential metal-containing cluster that serves as a structural unit in the larger assembly.

OUTPUT REQUIREMENT:
Return strictly and only the Metal CBU formula in normalized empirical format — no commentary, no additional notes, no alternative names, no structural labels.

Sources of Truth:
- Use all three inputs:
  - RES file: definitive crystallographic bonding and occupancy
  - CIF file: crystallographic structure and connectivity context
  - Concentrated paper content: semantic and functional role assignments

Information Hierarchy:
1. Use RES to determine atomic positions, occupancy, coordination environment, and symmetry.
2. Use CIF to identify molecular fragments, ligand identity, and bonding patterns.
3. Use Concentrated Paper only to interpret structural roles (e.g., node, core, linker) and symmetry grouping — not to extract formulae directly.

EXISTING METAL CBU DATABASE (very likely to contain the target):
1) Derive the metal CBU using the rules below.
2) Compare the derived CBU to the existing database entries.
3) If a matching or highly similar CBU exists, prefer the database's exact formula notation.
4) Only propose a new CBU if you are very certain the cluster is genuinely novel and your reduction is correct.

Existing Metal CBU Database:
{existing_cbu_database}

COMPONENT INCLUSION RULES
Include:
- All metal atoms that participate in a bonded cluster.
- Atoms or groups that:
  - Bridge multiple metal atoms,
  - Are terminal ligands repeated across symmetry-equivalent sites,
  - Are required to complete a chemically meaningful core structure.

Exclude:
- Any group that:
  - Binds to only one metal atom without recurrence,
  - Serves as a connection point to external linkers,
  - Is peripheral, weakly bound, or not conserved across equivalent sites,
  - Is solvent/counterion/guest.

FILTERING ORGANIC AND AMBIGUOUS GROUPS
- Classify by bonding and topology rather than names.
- Exclude groups that feed into extended organic scaffolds (linkers).
- Retain groups that are intrinsic, symmetry-conserved parts of the inorganic core.

REDUCTION TO MINIMAL CBU
1) Count all atoms and groups that satisfy inclusion rules.
2) Remove duplicated symmetry-equivalent parts.
3) Reduce counts by greatest common divisor when all components are in fixed ratios.
4) Ensure the result is a standalone chemically complete unit.

GROUPING AND REPRESENTATION RULES
- Express ligands as molecular groups (not raw atoms).
- Preserve complete molecular identities of retained groups (no nicknames).

OUTPUT FORMAT RULES
- Use square brackets for the entire formula.
- Order: Metals → Oxo/Hydroxo → Additional retained groups.
- Subscripts are integers; omit 1.
- ASCII only; no charges, oxidation states, μ-labels.
- No commentary, parenthetical notes, qualifiers, or alternative names after the formula.
- Return format (exact), with nothing appended after the bracketed formula:
Metal CBU:   [EmpiricalFormulaInBracketedForm]

RES file (SHELXL .res):
{res_content}

CIF file:
{cif_content}

Concentrated paper content:
{paper_content}

Important: If the similarity between any existing CBU and the target is low, you must directly output the explicit metal CBU you derive. Still follow the output format rules above strictly (no commentary after the formula).
"""

metal_prompt_doi_not_found = """
Guidance for Deriving a Minimal Metal Building Unit (CBU)

Objective:
Derive the smallest chemically complete and symmetry-distinct metal-based building unit (CBU) from the provided molecular structure files and supporting literature description. This CBU should represent the essential metal-containing cluster that serves as a structural unit in the larger assembly.

OUTPUT REQUIREMENT:
Return strictly and only the Metal CBU formula in normalized empirical format — no commentary, no additional notes, no alternative names, no structural labels.

Sources of Truth:
- Use all three inputs:
  - RES file: definitive crystallographic bonding and occupancy
  - CIF file: crystallographic structure and connectivity context
  - Concentrated paper content: semantic and functional role assignments

Assumption for this task: It is unlikely that an exact database match exists. You should derive a properly formatted metal CBU formula from the provided context.

Format-only samples of metal CBU formulas (hints on conventions only; do not copy semantics):
- [Fe3O(SO4)3(C5H5N)3]
- [V6O6(OCH3)9(SO4)]
- [V5O10]
- [V3O3]
- [Mo2]
- [Cu2]
- [Zr3O(OH)3(C5H5)3]
- [Ni4O12(SO4)4]
- [Rh2]
- [Ru2]

Important: These samples demonstrate bracketed empirical formula style and grouping order only. They do not imply chemical relevance to this paper.

COMPONENT INCLUSION RULES
Include metals and only those groups that are intrinsic, repeated (symmetry-conserved), or required to complete the core.
Exclude single-site, linker-connecting, peripheral, solvent/counterion groups.

REDUCTION TO MINIMAL CBU
Normalize, deduplicate symmetry-equivalent parts, and reduce by greatest common divisor when applicable.

OUTPUT FORMAT RULES
- Bracket the whole formula. Order: Metals → Oxo/Hydroxo → Other retained groups.
- Integers as subscripts; omit 1. ASCII only. No charges/μ-labels.
- No commentary, parenthetical notes, qualifiers, or alternative names after the formula.
- Return format (exact), with nothing appended after the bracketed formula:
Metal CBU:   [EmpiricalFormulaInBracketedForm]

RES file (SHELXL .res):
{res_content}

CIF file:
{cif_content}

Concentrated paper content:
{paper_content}

Important: If the similarity between any existing CBU and the target is low, you must directly output the explicit metal CBU you derive. Still follow the output format rules above strictly (no commentary after the formula).
"""


