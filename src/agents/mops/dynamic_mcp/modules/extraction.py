import asyncio
import time
from models.LLMCreator import LLMCreator
from models.ModelConfig import ModelConfig
from src.utils.global_logger import get_logger

# Focused extraction. When entity is provided, extract ONLY content for that single top-level entity.
EXTRACTION_PROMPT = """
You are a domain-agnostic extractor. Extract only what is needed for the EXTACTION_SCOPE, no derivations.

**CRITICAL**: You are only allowed to output markdown content. In no circumstances 
you should output rdf, turtle, or any other format. markdown content ONLY. 

**CRITICAL**: You must strictly follow the Focus on the task and not include 
any other content. Especially when top level entity is provided, you must strictly 
only provide information for the top level entity. ()

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
    previous_extraction: str = "",
    model_name: str | None = None,
    use_raw_prompt: bool = False,
    save_prompt_path: str | None = None,
) -> str:
    """Extract content per EXTACTION_SCOPE. If entity info is provided, scope strictly to that entity.
    
    Args:
        paper_content: Source document content
        goal: Extraction scope/goal
        t_box: T-Box ontology
        entity_label: Label of the entity to extract for (iter >= 2)
        entity_uri: URI of the entity to extract for (iter >= 2)
        previous_extraction: Previous iteration's extraction result for this entity (e.g., iter3 -> iter4)
        model_name: Model name to use for extraction (required, no default)
        use_raw_prompt: If True, use goal directly without EXTRACTION_PROMPT wrapper (for iter3)
        save_prompt_path: If provided, save the full prompt to this file path for comparison
    """
    logger = get_logger("agent", "Extractor")

    # Log extraction context for debugging
    entity_info = f"'{entity_label}'" if entity_label else "global (iteration 1)"
    logger.info(f"Starting extraction for entity: {entity_info}")
    logger.debug(f"Paper content length: {len(paper_content)} chars")
    logger.debug(f"Goal length: {len(goal)} chars")
    logger.debug(f"T-Box length: {len(t_box)} chars")
    if entity_uri:
        logger.debug(f"Entity URI: {entity_uri}")
    if previous_extraction:
        logger.debug(f"Previous extraction provided: {len(previous_extraction)} chars")

    # For iter3 (use_raw_prompt=True), use goal directly without wrapper
    if use_raw_prompt:
        # Append previous extraction if provided
        if previous_extraction:
            prompt = f"""{goal}

---
## PREVIOUS ITERATION EXTRACTION RESULTS

The following content was extracted in a previous iteration for this entity.
Use this as reference/context for the current extraction task:

{previous_extraction}
---

## SOURCE TEXT

{paper_content}
"""
        else:
            prompt = f"{goal}\n\n{paper_content}"
        logger.info("Using raw prompt mode (iter3) - no EXTRACTION_PROMPT wrapper")
    else:
        # Standard mode: use EXTRACTION_PROMPT wrapper
        focus_block = (
            FOCUS_BLOCK_WITH_ENTITY.format(entity_label=entity_label or "", entity_uri=entity_uri or "")
            if entity_label and entity_uri else
            FOCUS_BLOCK_GLOBAL
        )
        
        # Append previous extraction results if provided
        enhanced_paper_content = paper_content
        if previous_extraction:
            enhanced_paper_content = f"""{paper_content}

---
## PREVIOUS ITERATION EXTRACTION RESULTS

The following content was extracted in a previous iteration for this entity.
Use this as reference/context for the current extraction task:

{previous_extraction}
---
"""
            logger.info(f"Enhanced paper content with previous extraction: {len(enhanced_paper_content)} chars")

        prompt = EXTRACTION_PROMPT.format(
            focus_block=focus_block,
            goal=goal or "",
            paper_content=enhanced_paper_content,
            t_box=t_box or "",
        )
    
    prompt_length = len(prompt)
    logger.info(f"Generated prompt length: {prompt_length} chars (~{prompt_length // 4} tokens)")
    if prompt_length > 100000:
        logger.warning(f"⚠️  Very long prompt ({prompt_length} chars) - may exceed model limits or cause timeouts")
    
    # Save prompt if requested (for comparison purposes)
    if save_prompt_path:
        try:
            import os
            os.makedirs(os.path.dirname(save_prompt_path), exist_ok=True)
            with open(save_prompt_path, "w", encoding="utf-8") as f:
                f.write(prompt)
            logger.info(f"Saved prompt to: {save_prompt_path}")
        except Exception as e:
            logger.warning(f"Failed to save prompt to {save_prompt_path}: {e}")

    if not model_name or not str(model_name).strip():
        raise RuntimeError("extract_content requires an explicit model_name; none was provided")
    llm_creator = LLMCreator(
        model=model_name,
        model_config=ModelConfig(temperature=0, top_p=1),
        remote_model=True,
    )
    llm = llm_creator.setup_llm()
    
    # Add timeout configuration to prevent hanging requests
    # LangChain ChatOpenAI uses httpx under the hood with default 60s timeout
    # The timeout might need to be adjusted based on API performance

    retries = 0
    max_retries = 10
    start = time.time()
    attempt_durations = []
    
    while retries < max_retries:
        attempt_start = time.time()
        try:
            logger.info(f"🔄 Attempt {retries + 1}/{max_retries} for entity: {entity_info}")
            resp = llm.invoke(prompt).content
            attempt_duration = time.time() - attempt_start
            
            # Validate that we got a non-empty response
            if not resp or len(str(resp).strip()) == 0:
                raise ValueError("Empty response from LLM")
            
            # Success!
            total_duration = time.time() - start
            logger.info(f"✅ Extraction succeeded on attempt {retries + 1} ({attempt_duration:.2f}s)")
            logger.info(f"Response length: {len(str(resp))} chars")
            if retries > 0:
                logger.info(f"Total time including retries: {total_duration:.2f}s")
            return str(resp).strip()
            
        except Exception as e:
            attempt_duration = time.time() - attempt_start
            attempt_durations.append(attempt_duration)
            retries += 1
            error_type = type(e).__name__
            error_msg = str(e)
            
            # Detailed error logging with context
            logger.error("=" * 80)
            logger.error(f"❌ EXTRACTION FAILED - Attempt {retries}/{max_retries}")
            logger.error(f"Entity: {entity_info}")
            logger.error(f"Error Type: {error_type}")
            logger.error(f"Attempt Duration: {attempt_duration:.2f}s")
            logger.error("-" * 80)
            
            # Check if it's a JSON parsing error (common with API issues)
            if "Expecting value" in error_msg or "JSONDecodeError" in error_type:
                logger.error(f"JSON Parsing Error: {error_msg[:300]}")
                logger.error("🔍 Root Cause Analysis:")
                logger.error("  • API response was truncated or malformed")
                logger.error("  • Possible reasons:")
                logger.error("    - Network timeout or interruption")
                logger.error("    - API backend processing timeout")
                logger.error("    - Response size exceeds buffer limits")
                logger.error("    - Token limit exceeded during generation")
                if attempt_duration > 60:
                    logger.error(f"  • Long duration ({attempt_duration:.0f}s) suggests timeout issue")
                if prompt_length > 80000:
                    logger.error(f"  • Large prompt ({prompt_length} chars) may cause processing delays")
            elif "timeout" in error_msg.lower() or "timed out" in error_msg.lower():
                logger.error(f"Timeout Error: {error_msg[:300]}")
                logger.error("🔍 Root Cause: Request timed out")
                logger.error(f"  • Attempt took {attempt_duration:.2f}s before timeout")
                logger.error(f"  • Prompt size: {prompt_length} chars")
                logger.error("  • Consider: reducing input size or increasing timeout")
            elif "Empty response" in error_msg:
                logger.error("Empty Response Error")
                logger.error("🔍 Root Cause: LLM returned no content")
                logger.error("  • API may have rejected the request")
                logger.error("  • Check API rate limits or quotas")
            else:
                logger.error(f"Unexpected Error: {error_msg[:300]}")
                if len(error_msg) > 300:
                    logger.error(f"  (Full error truncated, see above for first 300 chars)")
            
            # Add context about what's being processed
            logger.error("-" * 80)
            logger.error("📊 Request Context:")
            logger.error(f"  • Prompt length: {prompt_length} chars (~{prompt_length // 4} tokens)")
            logger.error(f"  • Paper length: {len(paper_content)} chars")
            logger.error(f"  • Model: gpt-4o")
            logger.error(f"  • Temperature: 0.1, Top-P: 0.2")
            if entity_uri:
                logger.error(f"  • Entity URI: {entity_uri}")
            
            if retries < max_retries:
                # Progressive backoff: 5s, 10s, 15s
                wait_time = 5 * retries
                logger.error(f"⏳ Retrying in {wait_time}s... ({max_retries - retries} attempts remaining)")
                logger.error("=" * 80)
                time.sleep(wait_time)
            else:
                logger.error("=" * 80)
                logger.error("❌ ALL RETRY ATTEMPTS EXHAUSTED")
                logger.error("=" * 80)

    # Final failure summary
    total_duration = time.time() - start
    logger.error("=" * 80)
    logger.error("💥 EXTRACTION COMPLETELY FAILED")
    logger.error("-" * 80)
    logger.error(f"Entity: {entity_info}")
    if entity_label:
        logger.error(f"Entity Label: {entity_label}")
    if entity_uri:
        logger.error(f"Entity URI: {entity_uri}")
    logger.error(f"Total Duration: {total_duration:.2f}s")
    logger.error(f"Attempts: {max_retries}")
    logger.error(f"Attempt Durations: {', '.join(f'{d:.2f}s' for d in attempt_durations)}")
    logger.error(f"Prompt Size: {prompt_length} chars (~{prompt_length // 4} tokens)")
    logger.error("-" * 80)
    logger.error("🔧 Debugging Steps:")
    logger.error("  1. Check if paper content is too long")
    logger.error("  2. Verify API connectivity and rate limits")
    logger.error("  3. Check API logs for backend errors")
    logger.error("  4. Try reducing input size or splitting extraction")
    logger.error("  5. Increase timeout configuration if available")
    logger.error("=" * 80)
    
    raise RuntimeError(
        f"Failed to extract content for entity '{entity_label or 'global'}' "
        f"after {max_retries} attempts. Total duration: {total_duration:.2f}s. "
        f"Last error type: {error_type}"
    )


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