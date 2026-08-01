"""
Main Ontology Extractions Pipeline Step

This module handles iterations 2+ for ontology-driven extractions.
It ONLY performs extraction (hints generation), NOT KG building.

It processes each top-level entity through multiple iterations:
- Iteration 2: Chemical inputs/outputs (uses agent with MCP tools)
- Iteration 3: Synthesis steps (with pre-extraction, uses simple LLM)
- Iteration 3.1: Step enrichment
- Iteration 3.2: Vessel enrichment  
- Iteration 4: Yield extraction
"""
import os
import sys
import json
import asyncio
import re
import unicodedata
from difflib import get_close_matches
from pathlib import Path
from typing import Any, List, Dict, TYPE_CHECKING, Tuple

# Add project root to path for imports
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from models.ModelConfig import ModelConfig
from models.LLMCreator import LLMCreator
from src.utils.global_logger import get_logger
from src.utils.extraction_models import get_extraction_model
from src.pipelines.structured_extraction import (
    is_marker_only_optional_output,
    validate_hint_payload,
)
from src.pipelines.utils.ttl_publisher import load_meta_task_config, get_main_ontology_name

if TYPE_CHECKING:
    from models.BaseAgent import BaseAgent

logger = get_logger("pipeline", "main_ontology_extractions")

# Yield-only / single-predicate hints are often one short TTL line (< 50 chars) but still valid.
_MIN_EXTRACTION_CHARS = 20


def _get_base_agent():
    """Import BaseAgent lazily so simple-LLM runs do not require agent dependencies."""
    from models.BaseAgent import BaseAgent

    return BaseAgent


def _normalize_llm_content(content_or_message: object) -> str:
    """
    Normalize LangChain message / raw content (``str | list | dict``) to a plain string.

    Avoids ``str([]) -> \"[]\"`` and similar pitfalls when the provider returns block lists.
    """
    from models.BaseAgent import _normalize_ai_message_content

    raw = content_or_message.content if hasattr(content_or_message, "content") else content_or_message
    return _normalize_ai_message_content(raw)

def resolve_generated_file(path: str) -> str:
    """
    Resolve a generated artifact path.

    Prefer `ai_generated_contents_candidate/` (where generation writes in this repo),
    then fall back to `ai_generated_contents/` if present.
    """
    path = (path or "").replace("\\", "/")
    candidates: list[str] = []
    override_root = os.environ.get("TWA_GENERATED_ARTIFACT_ROOT", "").strip().replace("\\", "/").rstrip("/")
    strict_root = os.environ.get("TWA_REQUIRE_GENERATED_ARTIFACT_ROOT") == "1"
    if path.startswith("ai_generated_contents/"):
        if override_root:
            candidates.append(path.replace("ai_generated_contents", override_root, 1))
        if not strict_root:
            candidates.append(path.replace("ai_generated_contents/", "ai_generated_contents_candidate/", 1))
            candidates.append(path)
    elif path.startswith("ai_generated_contents_candidate/"):
        if override_root:
            candidates.append(path.replace("ai_generated_contents_candidate", override_root, 1))
        if not strict_root:
            candidates.append(path)
            candidates.append(path.replace("ai_generated_contents_candidate/", "ai_generated_contents/", 1))
    else:
        candidates.append(path)

    for p in candidates:
        if p and os.path.exists(p):
            return p
    if strict_root:
        raise FileNotFoundError(f"Required generated artifact is missing: {candidates[0]}")
    return candidates[0]


def _safe_name(label: str) -> str:
    """Convert entity label to safe filename."""
    s = unicodedata.normalize("NFKC", label or "entity")
    # Normalize common colon variants (including private-use glyphs seen in some PDFs)
    for ch in [":", "：", "﹕", "∶", "꞉", "︰", "\uf03a"]:
        s = s.replace(ch, ":")
    # German transliteration for stable ASCII filenames
    s = (
        s.replace("Ä", "Ae")
        .replace("Ö", "Oe")
        .replace("Ü", "Ue")
        .replace("ä", "ae")
        .replace("ö", "oe")
        .replace("ü", "ue")
        .replace("ß", "ss")
        .replace("α", "alpha")
        .replace("β", "beta")
        .replace("γ", "gamma")
        .replace("δ", "delta")
        .replace("Α", "Alpha")
        .replace("Β", "Beta")
        .replace("Γ", "Gamma")
        .replace("Δ", "Delta")
    )
    # Keep only filename-safe ASCII chars
    s = re.sub(r"[^A-Za-z0-9._-]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s or "entity"


def resolve_file_path(path_template: str, doi_hash: str, entity_safe: str, data_dir: str = "data") -> str:
    """
    Resolve a file path template with placeholders.
    
    Args:
        path_template: Template with {entity_safe} placeholder
        doi_hash: DOI hash
        entity_safe: Safe entity name
        data_dir: Data directory root
        
    Returns:
        Resolved absolute file path
    """
    # Replace placeholder
    resolved = path_template.replace("{entity_safe}", entity_safe)
    # Build full path
    return os.path.join(data_dir, doi_hash, resolved)


def _write_text_with_parent(path: str, content: str) -> None:
    """Write text while tolerating concurrent runtime-directory cleanup."""
    parent = os.path.dirname(path)
    for attempt in range(2):
        os.makedirs(parent, exist_ok=True)
        try:
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(content)
            return
        except FileNotFoundError:
            if attempt:
                raise


def _strip_code_fences_block(text: str) -> str:
    stripped = (text or "").strip()
    if stripped.startswith("```") and stripped.endswith("```"):
        stripped = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", stripped, count=1)
        stripped = re.sub(r"\s*```$", "", stripped, count=1)
    return stripped.strip()


def _parse_structured_hint_payload(text: str):
    cleaned = _strip_code_fences_block(text)
    if not cleaned:
        return None
    try:
        return json.loads(cleaned)
    except Exception:
        pass
    try:
        import yaml  # type: ignore

        return yaml.safe_load(cleaned)
    except Exception:
        return None


def _hint_item_merge_key(item) -> tuple[str, object] | None:
    if not isinstance(item, dict):
        return None
    for key in ("hasOrder", "order", "stepNumber", "label"):
        value = item.get(key)
        if value not in (None, ""):
            return key, value
    return None


def _merge_structured_hint_payloads(base, update):
    if isinstance(base, dict) and isinstance(update, dict):
        merged = dict(base)
        for key, value in update.items():
            if key in merged:
                merged[key] = _merge_structured_hint_payloads(merged[key], value)
            else:
                merged[key] = value
        return merged
    if isinstance(base, list) and isinstance(update, list):
        merged = list(base)
        index = {
            key: pos
            for pos, item in enumerate(merged)
            if (key := _hint_item_merge_key(item)) is not None
        }
        for item in update:
            merge_key = _hint_item_merge_key(item)
            if merge_key is not None and merge_key in index:
                merged[index[merge_key]] = _merge_structured_hint_payloads(merged[index[merge_key]], item)
            else:
                merged.append(item)
        return merged
    return update


def _merge_structured_hint_text(base_text: str, update_text: str) -> str | None:
    base_payload = _parse_structured_hint_payload(base_text)
    update_payload = _parse_structured_hint_payload(update_text)
    if not isinstance(base_payload, dict) or not isinstance(update_payload, dict):
        return None
    merged_payload = _merge_structured_hint_payloads(base_payload, update_payload)
    return json.dumps(merged_payload, ensure_ascii=False, indent=2)


def _looks_like_patch_enrichment_output(text: str) -> bool:
    low = (text or "").strip().lower()
    if not low:
        return False
    return (
        "patch_triples" in low
        or "kg_patch_triples" in low
        or "done_marker=true" in low
        or "done_marker: true" in low
    )


def _iter_base_hint_snapshot_path(base_hint_file: str, *, enriches: int | str, entity_safe: str) -> str:
    return os.path.join(
        os.path.dirname(base_hint_file),
        f"iter{enriches}_base_hints_{entity_safe}.txt",
    )


def _sub_iteration_patch_output_path(base_hint_file: str, *, enriches: int | str, sub_iter_num: int | str, entity_safe: str) -> str:
    return os.path.join(
        os.path.dirname(base_hint_file),
        f"iter{enriches}_{sub_iter_num}_patch_{entity_safe}.txt",
    )


def load_iterations_config(ontology_name: str) -> dict:
    """Load the iterations configuration for the ontology."""
    config_path = get_iterations_config_path(ontology_name)
    
    if not os.path.exists(config_path):
        logger.error(f"Iterations config not found: {config_path}")
        return {}
    
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Failed to load iterations config: {e}")
        return {}


def get_iterations_config_path(ontology_name: str) -> str:
    """Resolve the iterations.json path for an ontology."""
    return resolve_generated_file(
        f"ai_generated_contents/iterations/{ontology_name}/iterations.json"
    )


def load_top_entities(doi_hash: str, data_dir: str = "data") -> List[Dict]:
    """Load the top entities JSON from iteration 1."""
    json_path = os.path.join(data_dir, doi_hash, "mcp_run", "iter1_top_entities.json")
    
    if not os.path.exists(json_path):
        logger.error(f"Top entities JSON not found: {json_path}")
        return []
    
    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Failed to load top entities: {e}")
        return []


def _artifact_is_current(path: str, dependency_paths: List[str] | None = None) -> bool:
    """Return True if artifact exists, is non-empty, and is not older than dependencies."""
    if not os.path.exists(path):
        return False
    try:
        if os.path.getsize(path) <= 0:
            return False
        artifact_mtime = os.path.getmtime(path)
    except Exception:
        return False

    dep_mtimes: list[float] = []
    for dep in dependency_paths or []:
        if not dep:
            continue
        dep_resolved = dep if os.path.exists(dep) else resolve_generated_file(dep)
        if os.path.exists(dep_resolved):
            try:
                dep_mtimes.append(os.path.getmtime(dep_resolved))
            except Exception:
                pass
    if dep_mtimes and artifact_mtime < max(dep_mtimes):
        return False
    return True


def _expected_hint_files_exist(
    doi_hash: str,
    iterations: List[Dict],
    top_entities: List[Dict],
    data_dir: str = "data",
    iterations_config_path: str | None = None,
) -> bool:
    """
    Return True only if every per-entity iteration that should emit hints has
    produced a non-empty hints file for every top entity.
    """
    for iteration in iterations:
        if not isinstance(iteration, dict):
            continue
        if not iteration.get("per_entity", False):
            continue
        iter_num = iteration.get("iteration_number")
        outputs = iteration.get("outputs", {}) or {}
        hint_file_template = outputs.get("hints_file", f"mcp_run/iter{iter_num}_hints_{{entity_safe}}.txt")
        for entity in top_entities:
            entity_label = entity.get("label", "")
            safe = _safe_name(entity_label)
            hint_file = resolve_file_path(hint_file_template, doi_hash, safe, data_dir)
            freshness_deps = [iterations_config_path or ""]
            freshness_deps.append(iteration.get("extraction_prompt", ""))
            if iteration.get("has_pre_extraction"):
                freshness_deps.append(iteration.get("pre_extraction_prompt", ""))
            if not _artifact_is_current(hint_file, freshness_deps):
                return False
    return True


def load_prompt(prompt_path: str) -> str:
    """Load a prompt from a markdown file."""
    prompt_path = resolve_generated_file(prompt_path)
    if not os.path.exists(prompt_path):
        logger.error(f"Prompt file not found: {prompt_path}")
        return ""
    
    try:
        with open(prompt_path, 'r', encoding='utf-8') as f:
            return f.read()
    except Exception as e:
        logger.error(f"Failed to load prompt: {e}")
        return ""


def load_paper_content_with_sources(doi_hash: str, data_dir: str = "data") -> Tuple[str, List[str]]:
    """Load the best-available paper content and supplemental context.

    Priority order:
      1. {hash}_vision.md  — vision LLM transcription (highest fidelity for medical PDFs)
      2. {hash}_stitched.md — section-filtered / stitched content
      3. {hash}_text.md    — plain text extraction
      4. {hash}.md         — raw combined output

    If supporting-information markdown exists, append it after the selected main
    paper text. OntoSynthesis procedures are often only fully specified in SI,
    while the main paper uses labels such as VMOP-α/VMOP-β narratively.
    """
    doi_dir = os.path.join(data_dir, doi_hash)
    vision_md = os.path.join(doi_dir, f"{doi_hash}_vision.md")
    stitched = os.path.join(doi_dir, f"{doi_hash}_stitched.md")
    text_md = os.path.join(doi_dir, f"{doi_hash}_text.md")
    raw_md = os.path.join(doi_dir, f"{doi_hash}.md")

    main_text = ""
    source_paths: List[str] = []
    for p in (vision_md, stitched, text_md, raw_md):
        if not os.path.exists(p):
            continue
        try:
            txt = Path(p).read_text(encoding="utf-8")
        except Exception as e:
            logger.warning(f"Failed to read {p}: {e}")
            continue
        if txt and txt.strip():
            main_text = txt
            source_paths.append(p)
            break

    if main_text:
        parts = [main_text]
        for si_name in (
            f"{doi_hash}_si_text.md",
            f"{doi_hash}_si_vision.md",
            f"{doi_hash}_si.md",
            f"{doi_hash}_si_tables.md",
        ):
            si_path = os.path.join(doi_dir, si_name)
            if not os.path.exists(si_path):
                continue
            try:
                si_txt = Path(si_path).read_text(encoding="utf-8")
            except Exception as e:
                logger.warning(f"Failed to read {si_path}: {e}")
                continue
            if si_txt and si_txt.strip():
                parts.append(f"\n\n# Supporting Information: {si_name}\n\n{si_txt}")
                source_paths.append(si_path)
        return "".join(parts), source_paths

    logger.error(
        f"No usable paper content found for {doi_hash}. Tried stitched/text/raw markdown."
    )
    return "", []


def load_paper_content(doi_hash: str, data_dir: str = "data") -> str:
    """Load paper content for callers that do not need source dependency paths."""
    content, _ = load_paper_content_with_sources(doi_hash, data_dir)
    return content


def bind_runtime_context(
    prompt_template: str,
    *,
    doi_hash: str = "",
    entity_label: str,
    entity_uri: str,
    source_text: str,
    iteration_input: str = "",
) -> str:
    """Bind the complete pipeline-owned extraction runtime envelope."""
    declared_doi = "{doi}" in prompt_template or "{hash}" in prompt_template
    declared_label = "{entity_label}" in prompt_template
    declared_uri = "{entity_uri}" in prompt_template
    prompt = prompt_template.replace("{doi}", doi_hash).replace("{hash}", doi_hash)
    prompt = prompt.replace("{entity_label}", entity_label)
    prompt = prompt.replace("{entity_uri}", entity_uri)
    declared_source = "{paper_content}" in prompt or "{context}" in prompt
    prompt = prompt.replace("{paper_content}", source_text)
    prompt = prompt.replace("{context}", source_text)
    declared_iteration_input = "{iteration_input}" in prompt
    prompt = prompt.replace("{iteration_input}", iteration_input)

    additions: list[str] = []
    missing_identity: list[str] = []
    if doi_hash and not declared_doi:
        missing_identity.append(f"Document DOI/hash: {doi_hash}")
    if not declared_label:
        missing_identity.append(f"Current entity label: {entity_label}")
    if not declared_uri:
        missing_identity.append(f"Current entity exact URI: {entity_uri}")
    if missing_identity:
        additions.extend(
            [
                "---- PIPELINE-INJECTED ENTITY RUNTIME CONTEXT: BEGIN ----",
                *missing_identity,
                "---- PIPELINE-INJECTED ENTITY RUNTIME CONTEXT: END ----",
            ]
        )
    if not declared_source:
        additions.extend(
            [
                "---- PIPELINE-INJECTED SOURCE TEXT: BEGIN ----",
                source_text,
                "---- PIPELINE-INJECTED SOURCE TEXT: END ----",
            ]
        )
    if iteration_input and not declared_iteration_input:
        additions.extend(
            [
                "---- PIPELINE-INJECTED ITERATION INPUT: BEGIN ----",
                iteration_input,
                "---- PIPELINE-INJECTED ITERATION INPUT: END ----",
            ]
        )
    return prompt.rstrip() + ("\n\n" + "\n".join(additions) + "\n" if additions else "")


async def run_pre_extraction(
    doi_hash: str,
    entity_label: str,
    entity_uri: str,
    paper_content: str,
    prompt_template: str,
    model_key: str,
    iter_num: int,
    data_dir: str = "data",
    freshness_paths: List[str] | None = None,
) -> str:
    """
    Run pre-extraction for an entity (e.g., iteration 3 pre-extraction).
    
    Returns:
        Extracted text content
    """
    safe = _safe_name(entity_label)
    output_dir = os.path.join(data_dir, doi_hash, "pre_extraction")
    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, f"entity_text_{safe}.txt")
    
    # Check if already exists
    if _artifact_is_current(output_path, freshness_paths):
        logger.info(f"    ⏭️  Pre-extraction already exists for '{entity_label}'")
        with open(output_path, 'r', encoding='utf-8') as f:
            return f.read()
    if os.path.exists(output_path):
        logger.info(f"    🔁 Pre-extraction is stale for '{entity_label}', regenerating")
    
    logger.info(f"    🔍 Running pre-extraction for '{entity_label}'...")
    
    prompt = bind_runtime_context(
        prompt_template,
        doi_hash=doi_hash,
        entity_label=entity_label,
        entity_uri=entity_uri,
        source_text=paper_content,
    )
    
    # Save full prompt for debugging in organized subfolder
    prompts_dir = os.path.join(data_dir, doi_hash, "prompts", f"iter{iter_num}_pre_extraction")
    os.makedirs(prompts_dir, exist_ok=True)
    prompt_file = os.path.join(prompts_dir, f"{safe}.md")
    try:
        with open(prompt_file, 'w', encoding='utf-8') as f:
            f.write(f"# Iteration {iter_num} Pre-Extraction Prompt\n\n")
            f.write(f"**Entity**: {entity_label}\n\n")
            f.write(f"**Entity URI**: {entity_uri}\n\n")
            f.write(f"**Model**: {get_extraction_model(model_key)}\n\n")
            f.write("---\n\n")
            f.write(prompt)
        logger.info(f"    💾 Saved pre-extraction prompt to: {prompt_file}")
    except Exception as e:
        logger.warning(f"    ⚠️  Failed to save pre-extraction prompt: {e}")
    
    # Get model
    model_name = get_extraction_model(model_key)
    llm = LLMCreator(
        model=model_name,
        model_config=ModelConfig(temperature=0, top_p=1.0),
        remote_model=True,
    ).setup_llm()
    
    # Extract with retries (increased to 5 attempts with validation)
    max_retries = 5
    for attempt in range(max_retries):
        try:
            logger.info(f"    🔍 Running pre-extraction (attempt {attempt + 1}/{max_retries})")
            result = await llm.ainvoke(prompt)
            content = _normalize_llm_content(result)
            
            # CRITICAL VALIDATION: Check if content is meaningful
            if not content or not content.strip():
                raise ValueError(f"LLM returned empty content for pre-extraction of '{entity_label}'")
            
            if len(content.strip()) < _MIN_EXTRACTION_CHARS:
                logger.error(
                    "    Pre-extraction too short: type=%s len(raw)=%s repr=%s",
                    type(content).__name__,
                    len(content) if content is not None else None,
                    repr(content)[:500],
                )
                raise ValueError(
                    f"LLM returned suspiciously short content ({len(content)} chars) for pre-extraction of '{entity_label}'"
                )
            
            # Save result to pre_extraction folder
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(content)
            
            # CRITICAL: Verify file was actually written
            if not os.path.exists(output_path):
                raise IOError(f"Failed to write pre-extraction file: {output_path}")
            
            # Verify file has content
            with open(output_path, 'r', encoding='utf-8') as f:
                written_content = f.read()
            if not written_content or not written_content.strip():
                raise IOError(f"Pre-extraction file was created but is empty: {output_path}")
            
            # Also save response in responses folder for tracking
            responses_dir = os.path.join(data_dir, doi_hash, "responses", f"iter{iter_num}_pre_extraction")
            os.makedirs(responses_dir, exist_ok=True)
            response_file = os.path.join(responses_dir, f"{safe}.md")
            with open(response_file, 'w', encoding='utf-8') as f:
                f.write(f"# Iteration {iter_num} Pre-Extraction Response\n\n")
                f.write(f"**Entity**: {entity_label}\n\n")
                f.write(f"**Model**: {model_name}\n\n")
                f.write("---\n\n")
                f.write(content)
            
            logger.info(f"    ✅ Pre-extraction completed ({len(content)} chars) - file verified")
            return content
            
        except Exception as e:
            if attempt < max_retries - 1:
                wait_time = 5 * (attempt + 1)  # Exponential backoff: 5s, 10s, 15s, 20s
                logger.warning(f"    ⚠️  Pre-extraction attempt {attempt + 1}/{max_retries} failed: {e}")
                logger.info(f"    ⏳ Waiting {wait_time}s before retry...")
                await asyncio.sleep(wait_time)
            else:
                logger.error(f"    ❌ Pre-extraction failed after {max_retries} attempts: {e}")
                raise RuntimeError(f"Failed to pre-extract for entity '{entity_label}' after {max_retries} attempts. Last error: {e}")
    
    # Should never reach here due to raise above, but just in case
    raise RuntimeError(f"Failed to pre-extract for entity '{entity_label}' after {max_retries} attempts")


async def run_extraction(
    doi_hash: str,
    entity_label: str,
    entity_uri: str,
    source_text: str,
    prompt_template: str,
    model_key: str,
    hints_file: str,
    iter_num: int,
    use_agent: bool = False,
    mcp_tools: list = None,
    mcp_set_name: str = None,
    freshness_paths: List[str] | None = None,
    extraction_validation: dict | None = None,
    iteration_input: str = "",
) -> str:
    """
    Run extraction (hints generation) for an entity.
    Can use either a simple LLM or an agent with MCP tools.
    
    Returns:
        Extracted hints content
    """
    freshness_inputs = list(freshness_paths or [])
    freshness_inputs.append(__file__)

    # Check if already exists
    if _artifact_is_current(hints_file, freshness_inputs):
        logger.info(f"    ⏭️  Extraction already exists for '{entity_label}'")
        with open(hints_file, 'r', encoding='utf-8') as f:
            return f.read()
    if os.path.exists(hints_file):
        logger.info(f"    🔁 Existing extraction is stale for '{entity_label}', regenerating")
    
    logger.info(f"    🔍 Running extraction for '{entity_label}'...")
    
    prompt = bind_runtime_context(
        prompt_template,
        doi_hash=doi_hash,
        entity_label=entity_label,
        entity_uri=entity_uri,
        source_text=source_text,
        iteration_input=iteration_input,
    )
    
    # Save full prompt for debugging in organized subfolder
    safe = _safe_name(entity_label)
    # Determine the prompt directory based on iteration type
    prompts_dir = os.path.join(os.path.dirname(os.path.dirname(hints_file)), "prompts", f"iter{iter_num}_extraction")
    os.makedirs(prompts_dir, exist_ok=True)
    prompt_file = os.path.join(prompts_dir, f"{safe}.md")
    try:
        mode = (
            f"**Mode**: Agent with MCP tools\n\n"
            f"**MCP Tools**: {mcp_tools}\n\n"
            f"**MCP Set**: {mcp_set_name}\n\n"
            if use_agent
            else "**Mode**: Simple LLM\n\n"
        )
        _write_text_with_parent(
            prompt_file,
            f"# Iteration {iter_num} Extraction Prompt\n\n"
            f"**Entity**: {entity_label}\n\n"
            f"**Entity URI**: {entity_uri}\n\n"
            f"**Model**: {get_extraction_model(model_key)}\n\n"
            f"{mode}---\n\n{prompt}",
        )
        logger.info(f"    💾 Saved prompt to: {prompt_file}")
    except Exception as e:
        logger.warning(f"    ⚠️  Failed to save prompt: {e}")
    
    # Get model
    model_name = get_extraction_model(model_key)

    def _build_revision_prompt(*, original_prompt: str, original_source: str, draft_output: str) -> str:
        return (
            "You are revising an extraction draft so it strictly complies with the original extraction prompt.\n\n"
            "Requirements:\n"
            "- Follow the ORIGINAL EXTRACTION PROMPT exactly.\n"
            "- Use ONLY the ORIGINAL SOURCE TEXT and the ORIGINAL EXTRACTION PROMPT as authority.\n"
            "- Keep only fields/assertions that are explicitly supported by the source text or explicitly allowed as derived values by the original prompt.\n"
            "- Remove weak guesses, speculative inferences, prophylactic/preventive interpretations, and any field not clearly justified by the prompt + source.\n"
            "- If the source text presents mutually exclusive alternatives (for example, branches joined by 'or', 'alternatively', 'either', or similar wording), do NOT serialize those alternatives as consecutive events in one linear output unless the ORIGINAL EXTRACTION PROMPT explicitly asks for branching.\n"
            "- When a mutually exclusive alternative must be reduced to one linear path and the ORIGINAL EXTRACTION PROMPT gives no tie-breaker, keep the first explicit branch and drop later alternative branches.\n"
            "- Treat exclusion rules, NOT/ONLY conditions, and conflict-resolution rules in the original prompt as higher priority than tentative positive matches in the draft.\n"
            "- If a field is mentioned only in prevention/risk/avoidance, setup/closure, historical/background, or otherwise excluded context, DROP that field unless the original prompt explicitly allows it.\n"
            "- If the original prompt provides allowed concrete instance types, do NOT keep generic parent/container labels as emitted instance types when a concrete type can be selected from the prompt.\n"
            "- Output ONLY the final extraction hints, with no explanations, no reasoning, no summary, no markdown code fences, and no missing-value commentary.\n"
            "- Preserve the exact field/property names and exact marker tokens required by the original prompt.\n"
            "- If the draft contains narrative sections, convert them into the strict final hint format required by the original prompt.\n"
            "- Omit unsupported fields entirely unless the original prompt explicitly requires a fixed negative/positive marker token.\n\n"
            "ORIGINAL EXTRACTION PROMPT:\n"
            "<<<PROMPT\n"
            f"{original_prompt}\n"
            "PROMPT\n>>>\n\n"
            "ORIGINAL SOURCE TEXT:\n"
            "<<<SOURCE\n"
            f"{original_source}\n"
            "SOURCE\n>>>\n\n"
            "DRAFT OUTPUT TO REVISE:\n"
            "<<<DRAFT\n"
            f"{draft_output}\n"
            "DRAFT\n>>>\n\n"
            "Return ONLY the revised final hints.\n"
        )

    def _build_support_audit_prompt(*, original_prompt: str, original_source: str, candidate_output: str) -> str:
        return (
            "You are auditing extraction hints for strict evidential support.\n\n"
            "Task:\n"
            "- Review EVERY field currently present in the CANDIDATE OUTPUT.\n"
            "- Keep a field ONLY if it is directly supported by the ORIGINAL SOURCE TEXT or explicitly allowed as a derived value by the ORIGINAL EXTRACTION PROMPT.\n"
            "- If the CANDIDATE OUTPUT linearizes mutually exclusive alternatives from the source into multiple simultaneous fields/events, reduce it to ONE canonical branch unless the ORIGINAL EXTRACTION PROMPT explicitly requests branching.\n"
            "- When reducing mutually exclusive alternatives to one canonical branch and the ORIGINAL EXTRACTION PROMPT gives no tie-breaker, keep the first explicit branch from the source and drop later alternatives.\n"
            "- If exclusion rules in the ORIGINAL EXTRACTION PROMPT conflict with a positive-looking mention, the exclusion rule wins.\n"
            "- Be conservative: if support is ambiguous, indirect, preventive, prophylactic, historical, setup-related, or otherwise excluded, DROP the field.\n"
            "- Return valid JSON with exactly two top-level keys: `supported_output` and `dropped_fields`.\n"
            "- `supported_output` must contain only the final supported extraction structure.\n"
            "- `dropped_fields` must be a list of objects with keys `field` and `reason`.\n"
            "- Do not include markdown fences, explanations, or any extra keys.\n\n"
            "ORIGINAL EXTRACTION PROMPT:\n"
            "<<<PROMPT\n"
            f"{original_prompt}\n"
            "PROMPT\n>>>\n\n"
            "ORIGINAL SOURCE TEXT:\n"
            "<<<SOURCE\n"
            f"{original_source}\n"
            "SOURCE\n>>>\n\n"
            "CANDIDATE OUTPUT:\n"
            "<<<CANDIDATE\n"
            f"{candidate_output}\n"
            "CANDIDATE\n>>>\n"
        )

    def _strip_code_fences(text: str) -> str:
        stripped = (text or "").strip()
        if stripped.startswith("```") and stripped.endswith("```"):
            stripped = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", stripped, count=1)
            stripped = re.sub(r"\s*```$", "", stripped, count=1)
        return stripped.strip()

    def _extract_expected_leaf_property_names(prompt_text: str) -> set[str]:
        """
        Best-effort extraction of canonical leaf property names from the generated prompt.

        We only validate leaf keys (actual emitted properties), not container section names
        like `PatientInfo` or `Procedure`.
        """
        expected: set[str] = set()
        for match in re.finditer(r"^- ([A-Za-z0-9_]+)\s+\(xsd:[^)]+\):", prompt_text or "", re.MULTILINE):
            expected.add(match.group(1))
        return expected

    def _iter_leaf_json_keys(value) -> list[str]:
        keys: list[str] = []
        if isinstance(value, dict):
            for key, child in value.items():
                if isinstance(child, (dict, list)):
                    keys.extend(_iter_leaf_json_keys(child))
                else:
                    keys.append(str(key))
        elif isinstance(value, list):
            for item in value:
                keys.extend(_iter_leaf_json_keys(item))
        return keys

    def _parse_structured_output(text: str):
        cleaned = _strip_code_fences(text)
        if not cleaned:
            return None
        try:
            return json.loads(cleaned)
        except Exception:
            pass
        try:
            import yaml  # type: ignore

            return yaml.safe_load(cleaned)
        except Exception:
            return None

    def _generic_ordered_member_type_errors(text: str, validation_cfg: dict | None) -> list[str]:
        """Detect configured ordered-member outputs that used generic container labels."""
        cfg = validation_cfg or {}
        rule = cfg.get("forbid_generic_ordered_member_types", {}) if isinstance(cfg, dict) else {}
        if not isinstance(rule, dict) or not bool(rule.get("enabled")):
            return []
        payload = _parse_structured_output(text)
        if payload is None:
            return []
        generic_labels = {
            re.sub(r"[^a-z]", "", str(label or "").strip().lower())
            for label in (rule.get("generic_labels") or [])
        }
        generic_labels = {label for label in generic_labels if label}
        if not generic_labels:
            return []
        generic_key_patterns = [
            re.compile(str(pattern), flags=re.IGNORECASE)
            for pattern in (rule.get("generic_key_patterns") or [])
            if str(pattern or "").strip()
        ]
        type_keys = set(rule.get("type_keys") or ["rdf:type", "type", "class"])
        errors: list[str] = []

        def has_concrete_step_type(item: Any) -> bool:
            if not isinstance(item, dict):
                return False
            for type_key in type_keys:
                raw_type = item.get(type_key)
                if raw_type is None:
                    continue
                type_norm = re.sub(r"[^a-z]", "", str(raw_type).split(":")[-1].lower())
                return bool(type_norm and type_norm not in generic_labels)
            return False

        def visit(value, path: str = "$") -> None:
            if isinstance(value, dict):
                for key, child in value.items():
                    key_text = str(key or "").strip()
                    key_norm = re.sub(r"[^a-z]", "", key_text.lower())
                    key_matches_pattern = any(pattern.match(key_text) for pattern in generic_key_patterns)
                    if (key_norm in generic_labels or key_matches_pattern) and isinstance(child, dict) and not has_concrete_step_type(child):
                        errors.append(f"{path}.{key_text}: generic step key")
                    if key_text in type_keys:
                        child_norm = re.sub(r"[^a-z]", "", str(child or "").strip().lower())
                        if child_norm in generic_labels:
                            errors.append(f"{path}.{key_text}: generic step type `{child}`")
                    visit(child, f"{path}.{key_text}")
            elif isinstance(value, list):
                for idx, item in enumerate(value):
                    visit(item, f"{path}[{idx}]")

        visit(payload)
        return errors

    def _configured_required_member_errors(text: str, source: str, validation_cfg: dict | None) -> list[str]:
        """Validate configured source-triggered required member types/properties."""
        cfg = validation_cfg or {}
        rules = cfg.get("require_members_when_source_matches", []) if isinstance(cfg, dict) else []
        if not isinstance(rules, list) or not rules:
            return []
        payload = _parse_structured_output(text)
        if payload is None:
            return []
        errors: list[str] = []

        def _type_locals(item: Any) -> set[str]:
            if not isinstance(item, dict):
                return set()
            raw = item.get("rdf:type") or item.get("type") or item.get("class") or []
            values = raw if isinstance(raw, list) else [raw]
            return {
                re.sub(r"[^a-z]", "", str(value).split(":")[-1].lower())
                for value in values
                if str(value or "").strip()
            }

        for idx, raw_rule in enumerate(rules):
            if not isinstance(raw_rule, dict) or not bool(raw_rule.get("enabled", True)):
                continue
            patterns = [str(p) for p in raw_rule.get("source_patterns", []) or [] if str(p or "").strip()]
            if patterns and not any(re.search(pattern, source or "", flags=re.IGNORECASE | re.DOTALL) for pattern in patterns):
                continue
            section_name = str(raw_rule.get("section_name") or "SynthesisStepList").strip()
            members = payload.get(section_name) if isinstance(payload, dict) else None
            if not isinstance(members, list):
                errors.append(f"configured required member rule {idx}: missing list section `{section_name}`")
                continue
            expected_type = re.sub(r"[^a-z]", "", str(raw_rule.get("expected_type") or "").split(":")[-1].lower())
            required_properties = [
                str(prop).strip()
                for prop in raw_rule.get("required_properties", []) or []
                if str(prop or "").strip()
            ]
            matching_members = [
                member
                for member in members
                if not expected_type or expected_type in _type_locals(member)
            ]
            if not matching_members:
                errors.append(
                    f"configured required member rule {idx}: source evidence requires `{raw_rule.get('expected_type')}`"
                )
                continue
            missing_props = [
                prop
                for prop in required_properties
                if not any(isinstance(member, dict) and prop in member for member in matching_members)
            ]
            if missing_props:
                errors.append(
                    f"configured required member rule {idx}: `{raw_rule.get('expected_type')}` missing properties {missing_props}"
                )
        return errors

    def _iter_dicts(value):
        if isinstance(value, dict):
            yield value
            for child in value.values():
                yield from _iter_dicts(child)
        elif isinstance(value, list):
            for item in value:
                yield from _iter_dicts(item)

    def _has_supported_amount(value) -> bool:
        if isinstance(value, dict):
            for key, child in value.items():
                key_text = str(key or "").lower()
                if "amount" in key_text:
                    child_text = str(child or "").strip().lower()
                    if child_text and child_text not in {"n/a", "na", "none", "null"}:
                        return True
                if _has_supported_amount(child):
                    return True
        elif isinstance(value, list):
            return any(_has_supported_amount(item) for item in value)
        return False

    def _candidate_input_labels(label: str) -> list[str]:
        text = re.sub(r"^ChemicalInput::", "", str(label or "").strip())
        if not text:
            return []
        candidates = [text]
        head = re.split(r"\s*\(", text, maxsplit=1)[0].strip()
        if head and head != text:
            candidates.append(head)
        for token in re.findall(r"[A-Za-z][A-Za-z0-9]*(?:[-_][A-Za-z0-9]+)*", text):
            if len(token) >= 3:
                candidates.append(token)
        out: list[str] = []
        seen: set[str] = set()
        for candidate in candidates:
            key = candidate.lower()
            if key not in seen:
                seen.add(key)
                out.append(candidate)
        return out

    def _source_has_amount_near_label(source: str, label: str) -> bool:
        source_text = str(source or "")
        if not source_text.strip():
            return False
        amount_re = re.compile(
            r"\b\d+(?:\.\d+)?\s*(?:mg|g|kg|µg|μg|ug|mmol|mol|µmol|μmol|umol|mL|ml|L|l|drops?)\b",
            re.IGNORECASE,
        )
        for candidate in _candidate_input_labels(label):
            if len(candidate) < 2:
                continue
            try:
                pattern = re.compile(re.escape(candidate), re.IGNORECASE)
            except re.error:
                continue
            for match in pattern.finditer(source_text):
                start = max(0, match.start() - 90)
                end = min(len(source_text), match.end() + 120)
                if amount_re.search(source_text[start:end]):
                    return True
        return False

    def _contains_amount_text(text: str) -> bool:
        return bool(
            re.search(
                r"\b\d+(?:\.\d+)?\s*(?:mg|g|kg|µg|μg|ug|mmol|mol|µmol|μmol|umol|mL|ml|L|l|drops?)\b",
                str(text or ""),
                re.IGNORECASE,
            )
        )

    def _add_input_amount_errors(text: str, source: str) -> list[str]:
        """Detect Add-step ChemicalInput amounts that are explicit in source but missing in hints."""
        payload = _parse_structured_output(text)
        if payload is None:
            return []

        errors: list[str] = []
        for item in _iter_dicts(payload):
            rdf_type = str(item.get("rdf:type") or item.get("type") or item.get("class") or "").strip()
            if rdf_type not in {"ontosyn:Add", "Add"}:
                continue
            input_label = ""
            for key, child in item.items():
                if str(key).endswith("hasAddedChemicalInput"):
                    input_label = str(child or "").strip()
                    break
            if not input_label or _has_supported_amount(item):
                continue
            if _source_has_amount_near_label(source, input_label):
                order = item.get("ontosyn:hasOrder") or item.get("hasOrder") or item.get("order") or "?"
                if _contains_amount_text(input_label):
                    errors.append(
                        f"Add order {order} puts the amount inside `ontosyn:hasAddedChemicalInput` as `{input_label}`. "
                        "Split it into two fields exactly like "
                        '`"ontosyn:hasAddedChemicalInput": "<chemical name only>", '
                        '`"ChemicalInput.ontosyn:hasAmount": "<amount exactly as written>"`.'
                    )
                    continue
                errors.append(
                    f"Add order {order} for `{input_label}` is missing ChemicalInput.ontosyn:hasAmount "
                    "although an explicit amount appears near that material in the source text. "
                    "Use the exact sibling field "
                    '`"ChemicalInput.ontosyn:hasAmount": "<amount exactly as written>"` '
                    "on that Add object; do not put the amount inside the chemical label."
                )
        return errors

    def _truthy_hint_value(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        return str(value or "").strip().lower() in {"true", "1", "yes", "y"}

    def _has_hint_key(item: dict, local_name: str) -> bool:
        wanted = str(local_name or "").lower()
        return any(str(key or "").lower().endswith(wanted) for key in item.keys())

    def _hint_value(item: dict, local_name: str) -> Any:
        wanted = str(local_name or "").lower()
        for key, value in item.items():
            if str(key or "").lower().endswith(wanted):
                return value
        return None

    def _heat_chill_sealing_inheritance_errors(text: str) -> list[str]:
        """Require explicit inherited sealing for cooling HeatChill steps in iter3 hints."""
        payload = _parse_structured_output(text)
        if not isinstance(payload, dict):
            return []
        steps = payload.get("SynthesisStepList")
        if not isinstance(steps, list):
            return []

        ordered: list[dict[str, Any]] = []
        for item in steps:
            if not isinstance(item, dict):
                continue
            rdf_type = str(item.get("rdf:type") or item.get("type") or item.get("class") or "").strip()
            if rdf_type not in {"ontosyn:HeatChill", "HeatChill"}:
                continue
            raw_order = item.get("ontosyn:hasOrder") or item.get("hasOrder") or item.get("order")
            try:
                order = int(raw_order)
            except Exception:
                continue
            ordered.append({"order": order, "item": item})
        ordered.sort(key=lambda entry: entry["order"])

        errors: list[str] = []
        previous_heat: dict[str, Any] | None = None
        for entry in ordered:
            item = entry["item"]
            temp = str(_hint_value(item, "hasTargetTemperature") or "").strip().lower()
            is_cooling = "room temperature" in temp or re.search(r"\b25\s*(?:°c|c|degree)", temp)
            if (
                is_cooling
                and previous_heat is not None
                and _truthy_hint_value(_hint_value(previous_heat, "isSealed"))
                and not _has_hint_key(item, "isSealed")
            ):
                errors.append(
                    f"HeatChill order {entry['order']} cools after a sealed HeatChill step but omits `ontosyn:isSealed`. "
                    "The T-Box sealing rule says cooling inherits the preceding heating step's sealed status; "
                    "emit `ontosyn:isSealed: true` on this cooling step unless the source explicitly says it was unsealed."
                )
            previous_heat = item
        return errors

    def _canonical_iter3_property_name(key: str) -> str | None:
        raw = str(key or "").strip()
        if not raw or raw.upper() == "STEP":
            return None
        lower = raw.lower()
        if lower in {"rdf:type", "type", "class", "step_type", "steptype"}:
            return "rdf:type"
        if lower in {"ontosyn:hasorder", "hasorder", "order"}:
            return "ontosyn:hasOrder"

        linked_property_map = {
            "hasaddedchemicalinput.name": "ontosyn:hasAddedChemicalInput",
            "hasaddedchemicalinput.hasamount": "ChemicalInput.ontosyn:hasAmount",
            "chemicalinput.ontosyn:hasamount": "ChemicalInput.ontosyn:hasAmount",
            "haswashingsolvent.name": "ontosyn:hasWashingSolvent",
            "haswashingsolvent.hasamount": "WashingSolvent.ontosyn:hasAmount",
        }
        if lower in linked_property_map:
            return linked_property_map[lower]

        known_ontosyn_properties = {
            "hasaddedchemicalinput",
            "hasstepduration",
            "hastargettemperature",
            "issealed",
            "hasvessel",
            "istransferedto",
            "haswashingsolvent",
            "heatingcoolingrate",
            "undervacuum",
            "stir",
        }
        local = re.sub(r"[^a-z]", "", lower.split(":")[-1])
        if raw.startswith("ontosyn:") or local in known_ontosyn_properties:
            return raw if raw.startswith("ontosyn:") else f"ontosyn:{raw}"
        return raw

    def _canonicalize_iter3_hints(text: str) -> str:
        """Normalize common LLM step-list variants into the expected SynthesisStepList contract."""
        payload = _parse_structured_output(text)
        if not isinstance(payload, dict) or isinstance(payload.get("SynthesisStepList"), list):
            return text

        candidate_steps = None
        for key in ("SECTION: STEPS", "STEPS", "steps", "SynthesisSteps"):
            value = payload.get(key)
            if isinstance(value, list):
                candidate_steps = value
                break
        if not isinstance(candidate_steps, list):
            return text

        canonical_steps: list[dict[str, Any]] = []
        for raw_step in candidate_steps:
            if not isinstance(raw_step, dict):
                continue
            canonical: dict[str, Any] = {}
            raw_order = (
                raw_step.get("ontosyn:hasOrder")
                or raw_step.get("hasOrder")
                or raw_step.get("order")
                or raw_step.get("STEP")
            )
            if raw_order is not None:
                canonical["ontosyn:hasOrder"] = raw_order
            for key, value in raw_step.items():
                canonical_key = _canonical_iter3_property_name(str(key))
                if canonical_key is None:
                    continue
                canonical[canonical_key] = value
            if "rdf:type" in canonical and "ontosyn:hasOrder" in canonical:
                canonical_steps.append(canonical)
        if not canonical_steps:
            return text

        normalized = dict(payload)
        normalized.pop("SECTION: STEPS", None)
        normalized.pop("STEPS", None)
        normalized.pop("steps", None)
        normalized.pop("SynthesisSteps", None)
        normalized["SynthesisStepList"] = canonical_steps
        return _dump_json_compact(normalized)

    def _dump_json_compact(value) -> str:
        return json.dumps(value, ensure_ascii=False, indent=2)

    def _has_near_miss_property_names(text: str, prompt_text: str) -> bool:
        """
        Detect JSON drafts whose leaf property names are suspiciously close to, but not
        exactly equal to, canonical property names from the prompt. These often look valid
        at a glance but silently break downstream KG materialization.
        """
        expected = _extract_expected_leaf_property_names(prompt_text)
        if not expected or "{" not in (text or ""):
            return False

        try:
            parsed = json.loads(_strip_code_fences(text))
        except Exception:
            return False

        for key in _iter_leaf_json_keys(parsed):
            if key in expected:
                continue
            close = get_close_matches(key, list(expected), n=1, cutoff=0.9)
            if close:
                logger.info(
                    "    🧭 Detected near-miss extraction property '%s' (closest canonical: '%s')",
                    key,
                    close[0],
                )
                return True
        return False

    def _needs_revision(text: str) -> bool:
        lowered = (text or "").lower()
        markers = [
            "### ",
            "summary",
            "to extract",
            "here's the extracted information",
            "these extractions adhere",
            "```json",
            "\"medicalcase-1\"",
            "therefore",
            "we will",
            "the operation",
        ]
        return any(marker in lowered for marker in markers)

    def _required_tool_activity_errors(metadata: dict | None) -> list[str]:
        validation = extraction_validation or {}
        groups = validation.get("required_executed_tool_groups") or []
        if not groups:
            return []
        activity = (metadata or {}).get("tool_activity") or {}
        executed = {
            str(name).strip()
            for name in (activity.get("executed_tool_names") or [])
            if str(name).strip()
        }
        errors: list[str] = []
        for group in groups:
            if not isinstance(group, dict):
                continue
            candidates = {
                str(name).strip()
                for name in (group.get("any_of") or [])
                if str(name).strip()
            }
            if candidates and executed.isdisjoint(candidates):
                errors.append(
                    f"{group.get('name') or 'required MCP lookup'} requires one of "
                    f"{sorted(candidates)}; executed={sorted(executed)}"
                )
        return errors
    
    # Extract with retries (increased to 5 attempts)
    max_retries = 5
    agent = None  # Initialize agent once outside retry loop
    allow_agent_fallback = True  # if MCP tool sessions fail, fall back to simple LLM
    llm = None
    
    last_validation_error = ""
    for attempt in range(max_retries):
        try:
            effective_prompt = prompt
            if last_validation_error:
                effective_prompt = (
                    prompt
                    + "\n\nVALIDATION FEEDBACK FROM PREVIOUS ATTEMPT:\n"
                    + last_validation_error
                    + "\nReturn corrected extraction hints only. Preserve all source-supported fields required by the feedback.\n"
                )
            if use_agent and mcp_tools and mcp_set_name:
                # Use agent with MCP tools (e.g., for iter2)
                # Create agent only once on first attempt, reuse for retries
                if agent is None:
                    logger.info(f"    🤖 Initializing agent with MCP tools: {mcp_tools}")
                    BaseAgent = _get_base_agent()
                    agent = BaseAgent(
                        model_name=model_name,
                        model_config=ModelConfig(temperature=0, top_p=1.0),
                        remote_model=True,
                        mcp_tools=mcp_tools,
                        mcp_set_name=mcp_set_name
                    )
                
                logger.info(f"    🔍 Running agent extraction (attempt {attempt + 1}/{max_retries})")
                result, agent_meta = await agent.run(effective_prompt, recursion_limit=600)
                content = _normalize_llm_content(result)
                tool_activity_errors = _required_tool_activity_errors(agent_meta)
                if tool_activity_errors:
                    raise ValueError(
                        "Agent did not execute required MCP lookup tools: "
                        + "; ".join(tool_activity_errors)
                    )
            else:
                # Use simple LLM (e.g., for iter3, iter4)
                logger.info(f"    🔍 Running simple LLM extraction (attempt {attempt + 1}/{max_retries})")
                if llm is None:
                    llm = LLMCreator(
                        model=model_name,
                        model_config=ModelConfig(temperature=0, top_p=1.0),
                        remote_model=True,
                    ).setup_llm()
                result = await llm.ainvoke(effective_prompt)
                content = _normalize_llm_content(result)
                agent_meta = {}
                # Preserve the model's representation. Semantic quality is assessed later by
                # the format-independent LLM extraction judge, not by shape-normalizing passes.
                legacy_diagnostics = {
                    "generic_ordered_member_types": _generic_ordered_member_type_errors(
                        content, extraction_validation
                    ),
                    "configured_required_members": _configured_required_member_errors(
                        content, source_text, extraction_validation
                    ),
                    "add_input_amounts": (
                        _add_input_amount_errors(content, source_text)
                        if iter_num == 3
                        else []
                    ),
                    "heat_chill_sealing_inheritance": (
                        _heat_chill_sealing_inheritance_errors(content)
                        if iter_num == 3
                        else []
                    ),
                }
                for diagnostic_name, findings in legacy_diagnostics.items():
                    if findings:
                        logger.warning(
                            "    Non-blocking legacy extraction diagnostic %s: %s",
                            diagnostic_name,
                            "; ".join(findings[:5]),
                        )
            
            # CRITICAL VALIDATION: Check if content is meaningful
            if not content or not content.strip():
                raise ValueError(f"LLM returned empty content for entity '{entity_label}'")

            short_marker = is_marker_only_optional_output(content)
            if short_marker:
                logger.warning(
                    "    Non-blocking marker-only extraction diagnostic %r for '%s'",
                    content.strip(),
                    entity_label,
                )

            try:
                ok_hint_payload, hint_errors = validate_hint_payload(
                    content, allow_empty=short_marker
                )
            except ValueError as exc:
                ok_hint_payload, hint_errors = False, [str(exc)]
            if not ok_hint_payload:
                logger.warning(
                    "    Non-blocking extraction representation diagnostic for '%s': %s",
                    entity_label,
                    "; ".join(hint_errors[:3]),
                )
            
            # Save result to hints file
            _write_text_with_parent(hints_file, content)
            
            # CRITICAL: Verify file was actually written
            if not os.path.exists(hints_file):
                raise IOError(f"Failed to write hints file: {hints_file}")
            
            # Verify file has content
            with open(hints_file, 'r', encoding='utf-8') as f:
                written_content = f.read()
            if not written_content or not written_content.strip():
                raise IOError(f"Hints file was created but is empty: {hints_file}")
            
            # Also save response in responses folder for tracking
            responses_dir = os.path.join(os.path.dirname(os.path.dirname(hints_file)), "responses", f"iter{iter_num}_extraction")
            os.makedirs(responses_dir, exist_ok=True)
            response_file = os.path.join(responses_dir, f"{safe}.md")
            with open(response_file, 'w', encoding='utf-8') as f:
                f.write(f"# Iteration {iter_num} Extraction Response\n\n")
                f.write(f"**Entity**: {entity_label}\n\n")
                f.write(f"**Model**: {model_name}\n\n")
                if use_agent:
                    f.write(f"**Mode**: Agent with MCP tools\n\n")
                    f.write(f"**MCP Tools**: {mcp_tools}\n\n")
                    tool_activity = (agent_meta or {}).get("tool_activity") or {}
                    f.write(
                        "**Executed MCP Tools**: "
                        f"{tool_activity.get('executed_tool_name_set') or []}\n\n"
                    )
                    f.write(
                        "**MCP Tool Calls**: "
                        f"{tool_activity.get('tool_message_count') or 0}\n\n"
                    )
                else:
                    f.write(f"**Mode**: Simple LLM\n\n")
                f.write("---\n\n")
                f.write(content)
            
            logger.info(f"    ✅ Extraction completed ({len(content)} chars) - hints file verified")
            return content
            
        except Exception as e:
            last_validation_error = str(e)
            # If MCP toolchain can't start (common on Windows without Docker / missing binaries),
            # fall back to simple LLM so we still produce extraction hint files.
            if (
                allow_agent_fallback
                and use_agent
                and (mcp_tools and mcp_set_name)
                and any(
                    s in str(e)
                    for s in (
                        "Could not open MCP session",
                        "unhandled errors in a TaskGroup",
                        "FileNotFoundError",
                        "WinError 2",
                        "Docker is not running",
                    )
                )
            ):
                logger.warning(
                    f"    ⚠️  MCP tools unavailable for iter{iter_num} extraction; "
                    f"falling back to simple LLM for '{entity_label}'. Error was: {e}"
                )
                # Disable agent path for subsequent retries in this extraction
                use_agent = False
                agent = None
                allow_agent_fallback = False
                # Retry immediately (no backoff) using simple LLM branch
                continue

            if attempt < max_retries - 1:
                wait_time = 5 * (attempt + 1)  # Exponential backoff: 5s, 10s, 15s, 20s
                logger.warning(f"    ⚠️  Extraction attempt {attempt + 1}/{max_retries} failed: {e}")
                logger.info(f"    ⏳ Waiting {wait_time}s before retry...")
                await asyncio.sleep(wait_time)
            else:
                logger.error(f"    ❌ Extraction failed after {max_retries} attempts: {e}")
                raise RuntimeError(f"Failed to extract hints for entity '{entity_label}' after {max_retries} attempts. Last error: {e}")
    
    # Should never reach here due to raise above, but just in case
    raise RuntimeError(f"Failed to extract hints for entity '{entity_label}' after {max_retries} attempts")


# KG building has been moved to a separate pipeline step
# This module ONLY handles extraction (hints generation)


def _write_extraction_completion_marker(marker_file: str) -> None:
    """Write the step marker while preserving its non-fatal failure semantics."""
    try:
        with open(marker_file, 'w') as f:
            f.write("completed\n")
        logger.info("  📌 Created completion marker")
    except Exception as e:
        logger.warning(f"  ⚠️  Failed to create completion marker: {e}")


def _run_extractions_entity_first(
    doi_hash: str,
    config: dict,
    top_entities: list,
    marker_file: str,
) -> bool:
    """Process every main iteration and enrichment for one entity at a time."""
    successful_writes: list[int] = []
    all_ok = True
    for entity in top_entities:
        child_config = dict(config)
        child_config["_entity_first_entity_safe"] = _safe_name(
            entity.get("label", "")
        )
        child_config["_entity_first_successful_writes"] = successful_writes
        if not run_step(doi_hash, child_config):
            all_ok = False

    if not all_ok:
        return False
    if sum(successful_writes) <= 0:
        logger.error(
            "❌ Main ontology extractions produced no hints files; "
            "refusing to create completion marker"
        )
        return False

    _write_extraction_completion_marker(marker_file)
    logger.info(f"✅ Main Ontology Extractions completed for {doi_hash}")
    return True


def run_step(doi_hash: str, config: dict) -> bool:
    """
    Main entry point for the main ontology extractions pipeline step.
    
    This step processes iterations 2+ for all top-level entities.
    
    Args:
        doi_hash: The DOI hash to process
        config: Pipeline configuration dictionary
        
    Returns:
        True if all extractions succeeded
    """
    data_dir = config.get("data_dir", "data")
    doi_folder = os.path.join(data_dir, doi_hash)
    
    logger.info(f"▶️  Main Ontology Extractions for {doi_hash}")
    
    meta_config = load_meta_task_config(config.get("meta_task_config", "configs/meta_task/meta_task_config.json"))
    main_ontology = meta_config.get("ontologies", {}).get("main", {})
    ontology_name = get_main_ontology_name(meta_config, default="ontosynthesis")
    mcp_set_name = main_ontology.get("mcp_set_name", "run_created_mcp.json")
    mcp_tools = main_ontology.get("mcp_list", ["llm_created_mcp"])
    
    # Override with test MCP config if provided
    if "test_mcp_config" in config:
        test_mcp_config = config["test_mcp_config"]
        logger.info(f"  🧪 Using test MCP config: {test_mcp_config}")
        mcp_set_name = test_mcp_config
    
    logger.info(f"  📋 Ontology: {ontology_name}")
    logger.info(f"  🔧 MCP Config: {mcp_set_name}")
    
    # Load iterations config
    iterations_config_path = get_iterations_config_path(ontology_name)
    iterations_config = load_iterations_config(ontology_name)
    if not iterations_config:
        logger.error("❌ Failed to load iterations configuration")
        return False
    
    iterations = iterations_config.get("iterations", [])
    logger.info(f"  📊 Found {len(iterations)} iterations to process")
    
    # Load top entities
    top_entities = load_top_entities(doi_hash, data_dir)
    if not top_entities:
        logger.error("❌ No top entities found")
        return False

    selected_entity_safe = config.get("_entity_first_entity_safe")
    if selected_entity_safe:
        top_entities = [
            entity
            for entity in top_entities
            if _safe_name(entity.get("label", "")) == selected_entity_safe
        ]
        if not top_entities:
            logger.error(
                "❌ Selected entity not found for entity-first extraction: %s",
                selected_entity_safe,
            )
            return False
    
    logger.info(f"  🎯 Processing {len(top_entities)} top-level entities")

    # Check if step is already completed. Marker is only trusted if the current
    # per-entity iteration set has actually produced all expected hints files.
    marker_file = os.path.join(doi_folder, ".main_ontology_extractions_done")
    if not selected_entity_safe and os.path.exists(marker_file):
        if _expected_hint_files_exist(doi_hash, iterations, top_entities, data_dir, iterations_config_path):
            logger.info(f"  ⏭️  Main ontology extractions already completed (marker exists)")
            return True
        logger.warning("  🔁 Marker exists but required hints are missing; re-running main ontology extractions")

    if not selected_entity_safe:
        return _run_extractions_entity_first(
            doi_hash=doi_hash,
            config=config,
            top_entities=top_entities,
            marker_file=marker_file,
        )
    
    # Load paper content
    paper_content, paper_source_paths = load_paper_content_with_sources(doi_hash, data_dir)
    if not paper_content:
        logger.error("❌ Failed to load paper content")
        return False
    
    # Get skip extraction flags from config
    skip_iter2 = config.get("skip_iter2_extraction", False)
    skip_iter3 = config.get("skip_iter3_extraction", False)
    skip_iter4 = config.get("skip_iter4_extraction", False)
    successful_hint_writes = 0
    
    # Process each iteration
    for iteration in iterations:
        iter_num = iteration.get("iteration_number")
        iter_name = iteration.get("name", f"iteration_{iter_num}")
        per_entity = iteration.get("per_entity", False)
        use_agent = iteration.get("use_agent", False)
        has_pre_extraction = iteration.get("has_pre_extraction", False)
        
        logger.info(f"\n  🔄 Iteration {iter_num}: {iter_name}")
        
        # Check if this iteration should be skipped
        if iter_num == 2 and skip_iter2:
            logger.info(f"    ⏭️  Skipping iteration 2 extraction (--skip-iter2-extraction)")
            continue
        if iter_num == 3 and skip_iter3:
            logger.info(f"    ⏭️  Skipping iteration 3 extraction (--skip-iter3-extraction)")
            continue
        if iter_num == 4 and skip_iter4:
            logger.info(f"    ⏭️  Skipping iteration 4 extraction (--skip-iter4-extraction)")
            continue
        
        if not per_entity:
            logger.warning(f"    ⚠️  Iteration {iter_num} is not per-entity, skipping")
            continue
        
        # Get paths and config
        pre_extraction_prompt_path = iteration.get("pre_extraction_prompt")
        extraction_prompt_path = iteration.get("extraction_prompt")
        model_key = iteration.get("model_config_key", f"iter{iter_num}_hints")
        pre_extraction_model_key = iteration.get("pre_extraction_model_key", "iter3_pre_extraction")
        
        # Process each entity
        for entity in top_entities:
            entity_label = entity.get("label", "")
            entity_uri = entity.get("uri", "")
            safe = _safe_name(entity_label)
            
            logger.info(f"  📌 Entity: {entity_label}")
            
            # Get output paths from config (with fallback to defaults)
            outputs = iteration.get("outputs", {})
            hint_file_template = outputs.get("hints_file", f"mcp_run/iter{iter_num}_hints_{{entity_safe}}.txt")
            hint_file = resolve_file_path(hint_file_template, doi_hash, safe, data_dir)
            
            # Ensure directory exists
            os.makedirs(os.path.dirname(hint_file), exist_ok=True)
            
            # Step 1: Pre-extraction (if needed)
            source_text = paper_content
            if has_pre_extraction and pre_extraction_prompt_path:
                logger.info(f"    🔍 Pre-extraction for iteration {iter_num}")
                pre_extraction_prompt = load_prompt(pre_extraction_prompt_path)
                if pre_extraction_prompt:
                    try:
                        pre_freshness = [
                            iterations_config_path,
                            pre_extraction_prompt_path,
                            __file__,
                            *paper_source_paths,
                        ]
                        pre_extracted_text = asyncio.run(run_pre_extraction(
                            doi_hash, entity_label, entity_uri, paper_content,
                            pre_extraction_prompt, pre_extraction_model_key, iter_num, data_dir,
                            freshness_paths=pre_freshness,
                        ))
                        if pre_extracted_text:
                            source_text = pre_extracted_text
                    except Exception as e:
                        logger.error(f"    ❌ Pre-extraction failed: {e}")
            
            # Step 2: Extraction (hints generation)
            # For iter2: extraction uses agent with MCP tools
            # For iter3/4: extraction uses simple LLM
            if extraction_prompt_path:
                logger.info(f"    📝 Extraction for iteration {iter_num}")
                extraction_prompt = load_prompt(extraction_prompt_path)
                if extraction_prompt:
                    iteration_input = ""
                    iteration_input_template = (
                        (iteration.get("inputs") or {}).get("file_path")
                        if isinstance(iteration.get("inputs"), dict)
                        else None
                    )
                    if iteration_input_template:
                        iteration_input_path = resolve_file_path(
                            str(iteration_input_template),
                            doi_hash,
                            safe,
                            data_dir,
                        )
                        try:
                            iteration_input = Path(iteration_input_path).read_text(
                                encoding="utf-8"
                            )
                        except FileNotFoundError:
                            logger.warning(
                                "    ⚠️  Configured iteration input is missing for "
                                f"'{entity_label}': {iteration_input_path}"
                            )
                    # Determine if this iteration uses agent for extraction
                    # (iter2 uses agent, iter3/4 use simple LLM)
                    extraction_uses_agent = use_agent and (
                        iteration.get("extraction_mcp_tools") is not None or 
                        iteration.get("mcp_tools") is not None
                    )
                    
                    # Get MCP configuration for extraction (if using agent)
                    # Use extraction-specific config if available, otherwise fall back to general config
                    extraction_mcp_set = iteration.get("extraction_mcp_set_name") or iteration.get("mcp_set_name") if extraction_uses_agent else None
                    extraction_mcp_tools = iteration.get("extraction_mcp_tools") or iteration.get("mcp_tools") if extraction_uses_agent else None
                    
                    # If test MCP config is provided, override the set name for generated
                    # ontology tools while leaving external extraction tools unchanged.
                    if (
                        "test_mcp_config" in config
                        and extraction_mcp_tools
                        and set(extraction_mcp_tools).issubset(set(mcp_tools or []))
                    ):
                        extraction_mcp_set = config["test_mcp_config"]
                    
                    try:
                        hint_freshness = [
                            iterations_config_path,
                            extraction_prompt_path,
                            __file__,
                            *paper_source_paths,
                        ]
                        if has_pre_extraction and pre_extraction_prompt_path:
                            hint_freshness.append(pre_extraction_prompt_path)
                        hints = asyncio.run(run_extraction(
                            doi_hash, entity_label, entity_uri, source_text,
                            extraction_prompt, model_key, hint_file, iter_num,
                            use_agent=extraction_uses_agent,
                            mcp_tools=extraction_mcp_tools,
                            mcp_set_name=extraction_mcp_set,
                            freshness_paths=hint_freshness,
                            extraction_validation=iteration.get("extraction_validation") or {},
                            iteration_input=iteration_input,
                        ))
                    except Exception as e:
                        logger.error(f"    ❌ Extraction failed: {e}")
                        continue
                    if hints and str(hints).strip() and os.path.exists(hint_file):
                        try:
                            if os.path.getsize(hint_file) > 0:
                                successful_hint_writes += 1
                        except Exception:
                            successful_hint_writes += 1
        
        # Handle sub-iterations (enrichment steps like 3.1, 3.2)
        sub_iterations = iteration.get("sub_iterations", [])
        for sub_iter in sub_iterations:
            sub_iter_num = sub_iter.get("iteration_number")
            sub_iter_name = sub_iter.get("name", f"iteration_{sub_iter_num}")
            enriches = sub_iter.get("enriches")
            
            logger.info(f"\n  🔄 Sub-iteration {sub_iter_num}: {sub_iter_name} (enriches iter {enriches})")
            
            sub_extraction_prompt_path = sub_iter.get("extraction_prompt")
            sub_model_key = sub_iter.get("model_config_key", f"iter{sub_iter_num}_enrichment")
            
            if not sub_extraction_prompt_path:
                logger.warning(f"    ⚠️  No extraction prompt for sub-iteration {sub_iter_num}")
                continue
            
            sub_extraction_prompt = load_prompt(sub_extraction_prompt_path)
            if not sub_extraction_prompt:
                continue
            
            # Process each entity for enrichment
            for entity in top_entities:
                entity_label = entity.get("label", "")
                safe = _safe_name(entity_label)
                
                logger.info(f"  📌 Entity: {entity_label}")
                
                # Get input/output paths from config
                sub_inputs = sub_iter.get("inputs", {})
                sub_outputs = sub_iter.get("outputs", {})
                
                # Resolve done marker path
                done_marker_template = sub_outputs.get("done_marker", f"mcp_run/iter{enriches}_{sub_iter_num}_done_{{entity_safe}}.marker")
                done_marker = resolve_file_path(done_marker_template, doi_hash, safe, data_dir)
                
                if os.path.exists(done_marker):
                    logger.info(f"    ⏭️  Sub-iteration {sub_iter_num} already completed")
                    continue
                
                # Resolve base hints file path
                base_hints_template = sub_inputs.get("base_hints", f"mcp_run/iter{enriches}_hints_{{entity_safe}}.txt")
                base_hint_file = resolve_file_path(base_hints_template, doi_hash, safe, data_dir)
                
                if not os.path.exists(base_hint_file):
                    logger.warning(f"    ⚠️  Base hints file not found: {base_hint_file}")
                    continue
                
                base_hint_snapshot_file = _iter_base_hint_snapshot_path(
                    base_hint_file,
                    enriches=enriches,
                    entity_safe=safe,
                )
                refresh_snapshot = not os.path.exists(base_hint_snapshot_file)
                if not refresh_snapshot:
                    try:
                        refresh_snapshot = os.path.getmtime(base_hint_snapshot_file) < os.path.getmtime(base_hint_file)
                    except Exception:
                        refresh_snapshot = True

                # Keep a stable copy of the authoritative base iter hints so later
                # enrichment passes never read their own patch-style output as input.
                if refresh_snapshot:
                    try:
                        with open(base_hint_file, 'r', encoding='utf-8') as src:
                            base_hints = src.read()
                        with open(base_hint_snapshot_file, 'w', encoding='utf-8') as dst:
                            dst.write(base_hints)
                    except Exception as e:
                        logger.warning(f"    ⚠️  Failed to refresh base hint snapshot: {e}")
                        base_hints = ""
                else:
                    with open(base_hint_snapshot_file, 'r', encoding='utf-8') as f:
                        base_hints = f.read()

                if not base_hints.strip():
                    logger.warning(f"    ⚠️  Base hints snapshot is empty: {base_hint_snapshot_file}")
                    continue
                
                # Resolve pre-extracted text path
                pre_extracted_template = sub_inputs.get("pre_extracted_text", f"llm_based_results/entity_text_{{entity_safe}}.txt")
                entity_text_path = resolve_file_path(pre_extracted_template, doi_hash, safe, data_dir)
                
                if os.path.exists(entity_text_path):
                    with open(entity_text_path, 'r', encoding='utf-8') as f:
                        source_text = f.read()
                else:
                    source_text = paper_content
                
                # Format enrichment prompt
                enrichment_prompt = bind_runtime_context(
                    sub_extraction_prompt,
                    doi_hash=doi_hash,
                    entity_label=entity_label,
                    entity_uri=str(entity.get("uri") or ""),
                    source_text=source_text,
                    iteration_input=base_hints,
                )
                
                # Save enrichment prompt in organized subfolder
                prompts_dir = os.path.join(data_dir, doi_hash, "prompts", f"iter{sub_iter_num}_enrichment")
                os.makedirs(prompts_dir, exist_ok=True)
                prompt_file = os.path.join(prompts_dir, f"{safe}.md")
                try:
                    with open(prompt_file, 'w', encoding='utf-8') as f:
                        f.write(f"# Sub-iteration {sub_iter_num} Enrichment Prompt\n\n")
                        f.write(f"**Entity**: {entity_label}\n\n")
                        f.write(f"**Enriches**: Iteration {enriches}\n\n")
                        f.write(f"**Model**: {get_extraction_model(sub_model_key)}\n\n")
                        f.write("---\n\n")
                        f.write(enrichment_prompt)
                    logger.info(f"    💾 Saved enrichment prompt to: {prompt_file}")
                except Exception as e:
                    logger.warning(f"    ⚠️  Failed to save enrichment prompt: {e}")
                
                logger.info(f"    🔍 Running enrichment for sub-iteration {sub_iter_num}")
                
                # Run enrichment extraction with retry logic
                max_retries = 3
                enriched_content = None
                for attempt in range(max_retries):
                    try:
                        async def _run_enrichment():
                            model_name = get_extraction_model(sub_model_key)
                            llm = LLMCreator(
                                model=model_name,
                                model_config=ModelConfig(temperature=0, top_p=1.0),
                                remote_model=True,
                            ).setup_llm()
                            
                            result = await llm.ainvoke(enrichment_prompt)
                            enriched_content = _normalize_llm_content(result)
                            return enriched_content
                        
                        logger.info(f"    Enrichment attempt {attempt + 1}/{max_retries}")
                        enriched_content = asyncio.run(_run_enrichment())
                        
                        if enriched_content and enriched_content.strip():
                            logger.info(f"    ✅ Enrichment succeeded on attempt {attempt + 1}")
                            break
                        else:
                            logger.warning(f"    ⚠️  Empty enrichment result on attempt {attempt + 1}")
                            if attempt < max_retries - 1:
                                wait_time = 5 * (attempt + 1)
                                logger.info(f"    Waiting {wait_time}s before retry...")
                                import time
                                time.sleep(wait_time)
                    except Exception as e:
                        logger.error(f"    ❌ Enrichment attempt {attempt + 1}/{max_retries} failed: {e}")
                        if attempt < max_retries - 1:
                            wait_time = 5 * (attempt + 1)
                            logger.info(f"    Waiting {wait_time}s before retry...")
                            import time
                            time.sleep(wait_time)
                        else:
                            raise RuntimeError(f"Enrichment failed after {max_retries} attempts. Last error: {e}")
                
                if not enriched_content or not enriched_content.strip():
                    logger.error(f"    ❌ Enrichment returned empty content after {max_retries} attempts")
                    continue
                
                # Get output hints file path (legacy configs often point this back to
                # the base iter hints file; in that case we preserve the base JSON and
                # store patch-style enrichments separately instead of overwriting it).
                output_hints_template = sub_outputs.get("hints_file", base_hints_template)
                output_hints_file = resolve_file_path(output_hints_template, doi_hash, safe, data_dir)
                patch_output_file = _sub_iteration_patch_output_path(
                    base_hint_file,
                    enriches=enriches,
                    sub_iter_num=sub_iter_num,
                    entity_safe=safe,
                )

                merged_hint_text = _merge_structured_hint_text(base_hints, enriched_content)
                wrote_hint_artifact = False

                if merged_hint_text is not None:
                    os.makedirs(os.path.dirname(output_hints_file), exist_ok=True)
                    with open(output_hints_file, 'w', encoding='utf-8') as f:
                        f.write(merged_hint_text)
                    wrote_hint_artifact = True
                    logger.info(
                        "    ✅ Merged structured enrichment into iter%s hints for '%s'",
                        enriches,
                        entity_label,
                    )
                else:
                    os.makedirs(os.path.dirname(patch_output_file), exist_ok=True)
                    with open(patch_output_file, 'w', encoding='utf-8') as f:
                        f.write(enriched_content)
                    wrote_hint_artifact = True
                    if output_hints_file == base_hint_file and _looks_like_patch_enrichment_output(enriched_content):
                        logger.info(
                            "    💾 Preserved authoritative iter%s hints and saved patch-style enrichment to: %s",
                            enriches,
                            os.path.basename(patch_output_file),
                        )
                    else:
                        logger.info(
                            "    💾 Saved non-mergeable enrichment output to: %s",
                            os.path.basename(patch_output_file),
                        )

                if wrote_hint_artifact:
                    successful_hint_writes += 1
                
                # Save response in responses folder for tracking
                responses_dir = os.path.join(data_dir, doi_hash, "responses", f"iter{sub_iter_num}_enrichment")
                os.makedirs(responses_dir, exist_ok=True)
                response_file = os.path.join(responses_dir, f"{safe}.md")
                with open(response_file, 'w', encoding='utf-8') as f:
                    f.write(f"# Sub-iteration {sub_iter_num} Enrichment Response\n\n")
                    f.write(f"**Entity**: {entity_label}\n\n")
                    f.write(f"**Enriches**: Iteration {enriches}\n\n")
                    f.write(f"**Model**: {get_extraction_model(sub_model_key)}\n\n")
                    f.write("---\n\n")
                    f.write(enriched_content)
                
                # Create done marker
                os.makedirs(os.path.dirname(done_marker), exist_ok=True)
                with open(done_marker, 'w', encoding='utf-8') as f:
                    f.write("done")
                
                logger.info(f"    ✅ Enrichment completed for sub-iteration {sub_iter_num}")
    
    successful_writes = config.get("_entity_first_successful_writes")
    if isinstance(successful_writes, list):
        successful_writes.append(successful_hint_writes)
        return True

    if successful_hint_writes <= 0:
        logger.error("❌ Main ontology extractions produced no hints files; refusing to create completion marker")
        return False

    # Create completion marker
    _write_extraction_completion_marker(marker_file)
    
    logger.info(f"✅ Main Ontology Extractions completed for {doi_hash}")
    return True


if __name__ == "__main__":
    # Example usage for standalone testing
    if len(sys.argv) > 1:
        test_doi_hash = sys.argv[1]
        test_config = {
            "data_dir": "data"
        }
        print(f"Running main ontology extractions step for DOI hash: {test_doi_hash}")
        success = run_step(test_doi_hash, test_config)
        print(f"Main ontology extractions step {'succeeded' if success else 'failed'}.")
    else:
        print("Usage: python -m src.pipelines.main_ontology_extractions.extract <doi_hash>")

