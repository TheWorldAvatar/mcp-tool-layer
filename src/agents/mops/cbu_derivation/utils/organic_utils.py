from models.BaseAgent import BaseAgent
from models.ModelConfig import ModelConfig
from models.LLMCreator import LLMCreator


PROMPT = """
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

Fallback mechanism: 

In some rare case that, after you spared no effort to find more information about the organic speices, you can still not find 
sufficient information to find the smiles strings and do conversion, in this case, you will have to derive the CBU formula 
according to the RES file directly. 

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

"""


async def cbu_grounding_agent(res_content: str, paper_content: str, ttl_content: str) -> str:
    model_config = ModelConfig(temperature=0.2, top_p=0.02)
    # The `chemistry` MCP server depends on RDKit. On some Windows setups RDKit may be unavailable,
    # which would otherwise hard-fail the whole organic derivation. Degrade gracefully by dropping it.
    mcp_tools = ["pubchem", "enhanced_websearch", "chemistry"]
    try:
        import rdkit  # type: ignore  # noqa: F401
    except Exception:
        mcp_tools = ["pubchem", "enhanced_websearch"]
    agent = BaseAgent(model_name="gpt-5", model_config=model_config, remote_model=True, mcp_tools=mcp_tools, mcp_set_name="chemistry.json")

    # Preserve RES content exactly as loaded (no trimming) to provide full content
    instruction = PROMPT.format(
        res_content=res_content,
        paper_content=paper_content.strip(),
        ttl_content=ttl_content.strip(),
    )
    response, _metadata = await agent.run(instruction, recursion_limit=200)
    return response


# Backward-compatible alias expected by organic_cbu_derivation_agent.py
async def organic_cbu_grounding_agent(res_content: str, paper_content: str, ttl_content: str) -> str:
    return await cbu_grounding_agent(res_content=res_content, paper_content=paper_content, ttl_content=ttl_content)


def _extract_formula_exact(agent_output: str) -> str:
    try:
        lines = (agent_output or "").splitlines()
        for line in lines:
            ls = line.strip()
            lsl = ls.lower()
            if lsl.startswith("- cbu match:") or lsl.startswith("cbu match:"):
                parts = ls.split(":", 1)
                if len(parts) == 2:
                    raw = parts[1].strip()
                    # Return only the first bracketed formula if present
                    import re as _re
                    m = _re.search(r"\[[^\]]+\]", raw)
                    return m.group(0).strip() if m else raw
        return ""
    except Exception:
        return ""


def _classify_is_organic(agent_output: str) -> bool:
    llm = LLMCreator(model="gpt-4o-mini", remote_model=True, model_config=ModelConfig(), structured_output=False)
    model = llm.setup_llm()
    prompt = (
        "You will be given an agent output describing a Chemical Building Unit (CBU).\n"
        "Classify the CBU as either ORGANIC or METAL/metal-containing. Common solvents should be ignored as well. Simply classify solvents as 'Metal'.\n"
        "Respond with exactly one word: 'Organic' or 'Metal'. No extra text.\n\n"
        f"Agent Output:\n{agent_output}\n\n"
        "Your response:"
    )
    try:
        resp = (model.invoke(prompt).content or "").strip().lower()
        return resp == "organic"
    except Exception:
        return False


def extract_formula_and_classify(agent_output: str) -> str:
    formula_raw = _extract_formula_exact(agent_output)
    if not formula_raw:
        return ""
    is_organic = _classify_is_organic(agent_output)
    # Ensure only bracketed empirical formula is returned for downstream use
    try:
        import re as _re
        m = _re.search(r"\[[^\]]+\]", formula_raw)
        bracketed = m.group(0).strip() if m else formula_raw.strip()
    except Exception:
        bracketed = formula_raw.strip()
    if not is_organic:
        return "Ignore"
    # Some agent outputs may return an unbracketed formula; normalize to [ ... ].
    try:
        b = (bracketed or "").strip()
        if b and not b.startswith("["):
            b = f"[{b}]"
        return b
    except Exception:
        return bracketed


