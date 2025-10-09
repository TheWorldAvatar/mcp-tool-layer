import asyncio
import time
from models.LLMCreator import LLMCreator
from models.ModelConfig import ModelConfig
from src.utils.global_logger import get_logger

# Focused extraction. When entity is provided, extract ONLY content for that single top-level entity.
EXTRACTION_PROMPT = """
You are a domain-agnostic extractor. Extract only what is needed for the EXTACTION_SCOPE, no derivations.

CONSTRAINTS
- ASCII only.
- Include exact source text fragments where useful.
- Provide enough context to trace to the top-level entity.
- Prefer inclusion. Avoid omission.
- T-Box is a reference for what may be relevant.

{focus_block}

EXTACTION_SCOPE
{goal}

SOURCE
{paper_content}

T-BOX
{t_box}
""".strip()

FOCUS_BLOCK_WITH_ENTITY = """
FOCUS ENTITY
- entity_label: {entity_label}
- entity_uri: {entity_uri}
- Output MUST be scoped to this entity only. Ignore other entities.
""".strip()

FOCUS_BLOCK_GLOBAL = """
FOCUS
- No specific entity scope provided. Extract globally for iteration 1 only.
""".strip()


async def extract_content(
    paper_content: str,
    goal: str,
    t_box: str = "",
    entity_label: str | None = None,
    entity_uri: str | None = None,
) -> str:
    """Extract content per EXTACTION_SCOPE. If entity info is provided, scope strictly to that entity."""
    logger = get_logger("agent", "Extractor")

    focus_block = (
        FOCUS_BLOCK_WITH_ENTITY.format(entity_label=entity_label or "", entity_uri=entity_uri or "")
        if entity_label and entity_uri else
        FOCUS_BLOCK_GLOBAL
    )

    prompt = EXTRACTION_PROMPT.format(
        focus_block=focus_block,
        goal=goal or "",
        paper_content=paper_content or "",
        t_box=t_box or "",
    )

    llm_creator = LLMCreator(
        model="gpt-4.1",
        model_config=ModelConfig(temperature=0.1, top_p=0.2),
        remote_model=True,
    )
    llm = llm_creator.setup_llm()

    retries = 0
    max_retries = 2
    start = time.time()
    while retries < max_retries:
        try:
            resp = llm.invoke(prompt).content
            return str(resp).strip()
        except Exception as e:
            logger.error(f"Extraction error: {e}")
            retries += 1
            time.sleep(8)

    dur = time.time() - start
    logger.error(f"Extraction failed after retries. Duration {dur:.2f}s")
    raise RuntimeError("Failed to extract content after maximum retries.")


if __name__ == "__main__":
    with open("data/10.1021.acs.chemmater.0c01965/10.1021.acs.chemmater.0c01965_stitched.md", "r") as f:
        paper_content = f.read()
    goal = "Extract all chemical synthesis procedures described in the article. For each procedure, extract: the name or identifier of the chemical synthesis, the explicit chemical output produced, and the document context (e.g., section or paragraph) where the synthesis is described. State clearly that the top-level entity type is ChemicalSynthesis. For each chemical output, extract its name, description, and any explicit representation as a MetalOrganicPolyhedron if mentioned. Ensure that each chemical synthesis instance is linked to only one chemical output, and that every output mentioned in the article is covered by a corresponding chemical synthesis instance."
    t_box = "https://www.theworldavatar.com/kg/OntoSyn/OntoSyn.ttl"
    entity_label = "ChemicalSynthesis"
    entity_uri = "https://www.theworldavatar.com/kg/OntoSyn/instance/ChemicalSynthesis/synthesis-of-13t"
    resp = asyncio.run(extract_content(paper_content, goal, t_box, entity_label, entity_uri))
    print(resp)
    with open("data/test_extraction.txt", "w") as f:
        f.write(resp)