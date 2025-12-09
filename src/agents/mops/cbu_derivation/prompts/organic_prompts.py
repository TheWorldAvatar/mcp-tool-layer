organic_prompt_doi_found = """
You are provided context (paper extraction). Use this information to identify the relevant organic species and ground it to our organic CBU database, returning one best match.

Workflow:
1) Search and identify
   - Use enhanced_websearch to find authoritative info about the species name from the paper (synonyms, expanded names, vendor names, etc.).
   - Prefer identifiers (CAS) when possible; otherwise use high-confidence identifiers.
   - Use docling sparingly on the most relevant links.
   - Usually, the organic ligand is the species name, and use the name before deprotonation. 

2) Get canonical SMILES (organic only)
   - Use pubchem with CAS to obtain SMILES; if CAS not found, use chemistry tool query as fallback.
   - Canonicalize SMILES via the chemistry tool (canonicalize_smiles) before matching.

3) Ground to organic CBU database
   - Use fuzzy_smiles_search to find the single best matching ORGANIC CBU.
   - Return exactly one match; if none is defensible, return best-effort with Low confidence.

4) Usually you should find exact match. If you can not find exact match, you should 
use a different species name to try again. In many cases, both names before and after deprotonation are provided. 
As a result, you might need to try both or more. 

5) If your Confidence is Low or Medium after searches (no exact match), you may propose a CBU formula yourself based on your interpretation of the paper context. To do so:
   - Perform an additional search attempt using alternate names/synonyms inferred from the paper.
   - Sample and review a few representative organic CBU formulas from the database (format/style only) to internalize canonical formatting.
   - Construct a plausible organic CBU formula consistent with the paper context and the canonical formatting you observed.
   - Then retry the search and matching once more using any newly inferred names or fragments.

Guidelines:
- Use the provided paper context for disambiguation only. Do not derive metal CBUs.
- Do not attempt to infer metal-containing formulas; focus on organic ligands/species.
- Be concise and deterministic in tool usage to minimize noise.

Output format:
- Organic Species: "[Organic Species Name]"
- CBU Match: [Formula]
- Confidence: [High/Medium/Low]
- Reasoning: [Brief, focused]
- Chemical Information: [CAS, SMILES, canonical SMILES]

Strict formatting rules for CBU Match:
- The CBU Match value must be ONLY the bracketed empirical formula, e.g. [(C14H8)(CO2)2]
- Do not include any commentary, parenthetical notes, qualifiers, or alternative names after the formula
- Do not wrap the formula in quotes; do not append text like "(proposed)" or similar
- ASCII only; no charges/oxidation states/μ-labels; integers as subscripts, omit 1


- Paper extraction text (entity-related):

{paper_content}

 
Append-only reference (useful for disambiguation only; do not extract atoms directly):

RES file (SHELXL .res):

{res_content}

Format-only samples of organic CBU formulas (hints on conventions only; do not copy semantics):
- [(C10H6)(CO2)2]
- [(C10H6)(CO2)]
- [(C14H8)(CO2)2]
- [(C16H12)(CO2)2]
- [(C18H10)(CO2)4]
- [(C6H3)(CO2)3]
- [(C6H3)2(CO2)2]
- [(C6H4)(CO2)2]
- [(C6H4)2(CO2)2]
- [(C8H8)(C6H4)2(CO2)2]

Important: These samples demonstrate bracketed empirical formula style and grouping order only. They do not imply chemical relevance to this paper.

Important: If similarity to any existing CBU is low, directly output the explicit organic CBU you derive. Still follow the strict formatting rules above for CBU Match (bracketed formula only, no commentary).
"""

organic_prompt_doi_not_found = """
You are provided context (paper extraction). Use this information to identify the relevant organic species and ground it to our organic CBU database, returning one best match.

Workflow:
1) Search and identify
   - Use enhanced_websearch to find authoritative info about the species name from the paper (synonyms, expanded names, vendor names, etc.).
   - Prefer identifiers (CAS) when possible; otherwise use high-confidence identifiers.
   - Use docling sparingly on the most relevant links.
   - Usually, the organic ligand is the species name, and use the name before deprotonation. 

2) Get canonical SMILES (organic only)
   - Use pubchem with CAS to obtain SMILES; if CAS not found, use chemistry tool query as fallback.
   - Canonicalize SMILES via the chemistry tool (canonicalize_smiles) before matching.

3) Ground to organic CBU database
   - Use fuzzy_smiles_search to find the single best matching ORGANIC CBU.
   - Return exactly one match; if none is defensible, return best-effort with Low confidence.

4) Usually you should find exact match. If you can not find exact match, you should 
use a different species name to try again. In many cases, both names before and after deprotonation are provided. 
As a result, you might need to try both or more. 

5) If your Confidence is Low or Medium after searches (no exact match), you may propose a CBU formula yourself based on your interpretation of the paper context. To do so:
   - Perform an additional search attempt using alternate names/synonyms inferred from the paper.
   - Sample and review a few representative organic CBU formulas from the database (format/style only) to internalize canonical formatting.
   - Construct a plausible organic CBU formula consistent with the paper context and the canonical formatting you observed.
   - Then retry the search and matching once more using any newly inferred names or fragments.

Guidelines:
- Use the provided paper context for disambiguation only. Do not derive metal CBUs.
- Do not attempt to infer metal-containing formulas; focus on organic ligands/species.
- Be concise and deterministic in tool usage to minimize noise.

Output format:
- Organic Species: "[Organic Species Name]"
- CBU Match: [Formula]
- Confidence: [High/Medium/Low]
- Reasoning: [Brief, focused]
- Chemical Information: [CAS, SMILES, canonical SMILES]


- Paper extraction text (entity-related):

{paper_content}

 
Append-only reference (useful for disambiguation only; do not extract atoms directly):

RES file (SHELXL .res):

{res_content}

Important: If similarity to any existing CBU is low, directly output the explicit organic CBU you derive.
"""


