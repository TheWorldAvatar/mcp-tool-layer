"""
Top entity extraction pipeline step.

This module extracts top-level entities (e.g., ChemicalSynthesis) from papers
using prompts defined in the main ontology configuration.
"""

import os
import sys
import json
import re
from pathlib import Path
from typing import List, Optional
from rdflib import Graph, URIRef  # type: ignore[reportMissingImports]
from rdflib.namespace import RDFS  # type: ignore[reportMissingImports]

# Add project root to path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from src.utils.global_logger import get_logger
from src.utils.extraction_models import get_extraction_model
from src.pipelines.structured_extraction import validate_top_entity_lines
from models.LLMCreator import LLMCreator
from models.ModelConfig import ModelConfig
import asyncio

logger = get_logger("pipeline", "top_entity_extraction")

def resolve_generated_file(path: str) -> str:
    """
    Resolve a generated artifact path.

    Generation in this repo typically writes to `ai_generated_contents_candidate/`,
    while older pipeline code may reference `ai_generated_contents/`.
    This resolver prefers candidate if available, then falls back.
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


def load_meta_config(config_path: str = "configs/meta_task/meta_task_config.json") -> dict:
    """Load the meta task configuration."""
    with open(config_path, 'r', encoding='utf-8') as f:
        return json.load(f)


def load_extraction_prompt(ontology_name: str, iteration: int = 1) -> str:
    """
    Load extraction prompt from markdown file.
    
    Args:
        ontology_name: Name of the ontology (e.g., 'ontosynthesis')
        iteration: Iteration number (default: 1)
        
    Returns:
        The prompt text
    """
    prompt_path = resolve_generated_file(
        f"ai_generated_contents/prompts/{ontology_name}/EXTRACTION_ITER_{iteration}.md"
    )

    if not os.path.exists(prompt_path):
        raise FileNotFoundError(f"Extraction prompt not found: {prompt_path}")

    with open(prompt_path, 'r', encoding='utf-8') as f:
        return f.read()


def _top_entities_txt_is_stale(existing: str, invalidate_substrings: list) -> bool:
    """True if cached top_entities.txt should be discarded (wrong domain / placeholder)."""
    if not (existing or "").strip():
        return True
    low = existing.lower()
    for sub in invalidate_substrings or []:
        if sub and str(sub).lower() in low:
            return True
    return False


def _count_hint_lines(content: str, prefixes: Optional[List[str]]) -> List[str]:
    prefixes = tuple(p for p in (prefixes or []) if p)
    if not prefixes:
        prefixes = ("Entity",)
    out: List[str] = []
    for line in content.split("\n"):
        s = line.strip()
        if s and any(s.startswith(p) for p in prefixes):
            out.append(s)
    return out


def _normalize_top_entity_output(
    content: str,
    *,
    line_prefixes: Optional[List[str]] = None,
    identifier_code_regex: Optional[str] = None,
) -> str:
    """Normalize verbose top-entity lines to stable concise identifiers when possible."""
    normalized: list[str] = []
    seen: set[str] = set()
    prefixes = tuple(p for p in (line_prefixes or []) if p)
    if not prefixes:
        prefixes = ("Entity",)
    try:
        code_re = re.compile(identifier_code_regex or r"\b[A-Z][A-Z0-9]{1,}(?:[-_]\d+[A-Za-z0-9]*)\b")
    except re.error:
        code_re = re.compile(r"\b[A-Z][A-Z0-9]{1,}(?:[-_]\d+[A-Za-z0-9]*)\b")
    for raw_line in (content or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        matched_prefix = next((p for p in prefixes if line.startswith(p)), "")
        if not matched_prefix:
            normalized.append(line)
            continue
        candidates = code_re.findall(line)
        code = candidates[-1] if candidates else ""
        if code:
            key = code.upper()
            if key in seen:
                continue
            seen.add(key)
            normalized.append(f"{matched_prefix}-{len(seen)} [{code}]")
        else:
            line_match = re.match(
                rf"^{re.escape(matched_prefix)}-(\d+)\s+(?!\[)(.+?)\s*$",
                line,
            )
            if line_match:
                label = line_match.group(2).strip()
                key = f"{matched_prefix}:{label}"
                if key in seen:
                    continue
                seen.add(key)
                normalized.append(f"{matched_prefix}-{len(seen)} [{label}]")
                continue
            if line in seen:
                continue
            seen.add(line)
            normalized.append(line)
    return "\n".join(normalized).strip() + ("\n" if normalized else "")


def _local_name(iri: str) -> str:
    text = str(iri or "").strip()
    if "#" in text:
        return text.rsplit("#", 1)[-1]
    return text.rstrip("/").rsplit("/", 1)[-1]


def _load_top_entity_contract(meta_config: dict, main_ontology: dict) -> tuple[str, str]:
    policies = (main_ontology.get("runtime_policies") or {}) if isinstance(main_ontology, dict) else {}
    shell_validation = ((policies.get("main_entity_kg") or {}).get("shell_validation") or {})
    top_class_iri = str(shell_validation.get("top_entity_class_iri") or "").strip()
    ttl_file = str(main_ontology.get("ttl_file") or "").strip()
    if not top_class_iri or not ttl_file or not os.path.exists(ttl_file):
        return top_class_iri, ""
    try:
        graph = Graph()
        graph.parse(ttl_file, format="turtle")
        comment = "\n".join(str(c or "") for c in graph.objects(URIRef(top_class_iri), RDFS.comment)).strip()
        return top_class_iri, comment
    except Exception:
        return top_class_iri, ""


async def _revise_top_entities_against_tbox(
    *,
    llm,
    candidate_text: str,
    source_text: str,
    top_class_iri: str,
    top_class_comment: str,
    line_prefixes: List[str],
    identifier_code_regex: Optional[str],
) -> str:
    top_local = _local_name(top_class_iri) or (line_prefixes[0] if line_prefixes else "Entity")
    if not top_class_comment.strip():
        return candidate_text
    revision_prompt = f"""You are the validation agent for top-entity extraction.

Revise the candidate top-entity list using ONLY the T-Box class contract and source text below.

T-Box top class:
- IRI: {top_class_iri}
- Local name: {top_local}

T-Box class contract:
<<<TBOX
{top_class_comment}
TBOX
>>>

Validation rules:
- Keep a candidate only if it satisfies the T-Box class contract.
- If the T-Box excludes a candidate category, remove that candidate even if the source has a heading, title, table row, or procedure-like section for it.
- If a candidate is ambiguous under the T-Box class contract, remove it.
- Preserve the normalized output format exactly.
- Return only the corrected top-entity lines. No JSON, no markdown fences, no explanation.

Candidate top entities:
<<<CANDIDATES
{candidate_text}
CANDIDATES
>>>

Source text:
<<<SOURCE
{source_text}
SOURCE
>>>
"""
    result = await llm.ainvoke(revision_prompt)
    revised = result.content if hasattr(result, "content") else str(result)
    revised = _normalize_top_entity_output(
        revised,
        line_prefixes=line_prefixes,
        identifier_code_regex=identifier_code_regex,
    )
    ok, errors = validate_top_entity_lines(revised, list(line_prefixes or []))
    if ok and revised.strip():
        return revised
    logger.warning(
        "⚠️  Top-entity validation agent returned unusable output; keeping original extraction: %s",
        "; ".join(errors[:3]),
    )
    return candidate_text


async def extract_top_entities(
    doi_hash: str,
    data_dir: str,
    ontology_name: str,
    *,
    invalidate_top_entities_txt_substrings: Optional[List[str]] = None,
    count_lines_starting_with: Optional[List[str]] = None,
    identifier_code_regex: Optional[str] = None,
    top_class_iri: str = "",
    top_class_comment: str = "",
) -> bool:
    """
    Extract top-level entities from the stitched markdown.
    
    Args:
        doi_hash: DOI hash identifier
        data_dir: Base data directory
        ontology_name: Name of the ontology to use
        
    Returns:
        True if extraction succeeded
    """
    doi_dir = os.path.join(data_dir, doi_hash)
    stitched_md = os.path.join(doi_dir, f"{doi_hash}_stitched.md")
    text_md = os.path.join(doi_dir, f"{doi_hash}_text.md")
    raw_md = os.path.join(doi_dir, f"{doi_hash}.md")
    output_file = os.path.join(doi_dir, "top_entities.txt")
    
    # Check if already exists (skip only when content looks valid for this ontology).
    if os.path.exists(output_file):
        try:
            existing = Path(output_file).read_text(encoding="utf-8")
        except Exception:
            existing = ""
        low = existing.lower()
        placeholder_doc = "provide the document" in low
        stale_wrong_domain = _top_entities_txt_is_stale(existing, invalidate_top_entities_txt_substrings or [])
        ok_existing, _ = validate_top_entity_lines(
            existing,
            list(count_lines_starting_with or []),
        )
        if existing.strip() and ok_existing and not placeholder_doc and not stale_wrong_domain:
            logger.info(f"⏭️  Top entities already extracted: {output_file}")
            return True
        if stale_wrong_domain:
            logger.warning(
                f"⚠️  Stale/wrong-domain top_entities.txt; re-running extraction: {output_file}"
            )
        else:
            logger.warning(
                f"⚠️  Existing top_entities.txt looks invalid/placeholder; re-running extraction: {output_file}"
            )
    
    # Read paper content (robust fallback chain) and append supporting information
    # when available; synthesis procedures are often specified only in SI.
    vision_md = os.path.join(doi_dir, f"{doi_hash}_vision.md")
    paper_content = ""
    candidates = [vision_md, stitched_md, text_md, raw_md]
    chosen = None
    for p in candidates:
        if not os.path.exists(p):
            continue
        try:
            c = Path(p).read_text(encoding="utf-8")
        except Exception:
            c = ""
        if c and c.strip():
            paper_content = c
            chosen = p
            break
    if not paper_content.strip():
        logger.error(
            f"❌ No usable paper content found. Tried: {', '.join([p for p in candidates if p])}"
        )
        return False
    logger.info(f"📄 Using paper content from: {chosen}")

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
            si_text = Path(si_path).read_text(encoding="utf-8")
        except Exception as e:
            logger.warning(f"Failed to read {si_path}: {e}")
            continue
        if si_text and si_text.strip():
            paper_content += f"\n\n# Supporting Information: {si_name}\n\n{si_text}"
            logger.info(f"📎 Appended supporting information from: {si_path}")
    
    # Load extraction prompt
    logger.info(f"📋 Loading extraction prompt for {ontology_name} iteration 1")
    try:
        extraction_prompt = load_extraction_prompt(ontology_name, iteration=1)
    except FileNotFoundError as e:
        logger.error(f"❌ {e}")
        return False
    
    # Build full prompt
    full_prompt = f"{extraction_prompt}\n\n{paper_content}"
    
    # Save full prompt for reproducibility
    prompt_save_path = os.path.join(doi_dir, "iter1_full_prompt.md")
    os.makedirs(doi_dir, exist_ok=True)
    with open(prompt_save_path, 'w', encoding='utf-8') as f:
        f.write(full_prompt)
    logger.info(f"💾 Full prompt saved to: {prompt_save_path}")
    
    # Get model from config
    model_key = "iter1_hints"
    model_name = get_extraction_model(model_key)
    logger.info(f"🤖 Using model: {model_name} (from {model_key})")
    
    # Create LLM
    llm = LLMCreator(
        model=model_name,
        model_config=ModelConfig(temperature=0, top_p=1.0),
        remote_model=True,
    ).setup_llm()
    
    # Extract with retries
    max_retries = 3
    for attempt in range(max_retries):
        try:
            logger.info(f"🔍 Extracting top entities (attempt {attempt + 1}/{max_retries})...")
            result = await llm.ainvoke(full_prompt)
            
            # Extract content
            content = result.content if hasattr(result, 'content') else str(result)
            content = _normalize_top_entity_output(
                content,
                line_prefixes=count_lines_starting_with or [],
                identifier_code_regex=identifier_code_regex,
            )
            content = await _revise_top_entities_against_tbox(
                llm=llm,
                candidate_text=content,
                source_text=paper_content,
                top_class_iri=top_class_iri,
                top_class_comment=top_class_comment,
                line_prefixes=list(count_lines_starting_with or []),
                identifier_code_regex=identifier_code_regex,
            )
            ok_lines, line_errors = validate_top_entity_lines(
                content,
                list(count_lines_starting_with or []),
            )
            if not ok_lines:
                raise ValueError(
                    "Top entity extraction failed normalized line validation: "
                    + "; ".join(line_errors[:3])
                )
            
            # Save result
            os.makedirs(doi_dir, exist_ok=True)
            with open(output_file, 'w', encoding='utf-8') as f:
                f.write(content)
            
            logger.info(f"✅ Top entities saved to: {output_file}")
            
            # Log extracted entities
            lines = _count_hint_lines(content, count_lines_starting_with or [])
            logger.info(f"   Found {len(lines)} top-level entity line(s) (prefix filter)")
            for line in lines[:5]:  # Show first 5
                logger.info(f"   - {line[:80]}...")
            if len(lines) > 5:
                logger.info(f"   ... and {len(lines) - 5} more")
            
            return True
            
        except Exception as e:
            if attempt < max_retries - 1:
                logger.warning(f"⚠️  Attempt {attempt + 1} failed: {e}, retrying...")
                await asyncio.sleep(5 * (attempt + 1))
            else:
                logger.error(f"❌ Extraction failed after {max_retries} attempts: {e}")
                return False
    
    return False


def run_step(doi_hash: str, config: dict) -> bool:
    """
    Main entry point for the top entity extraction pipeline step.
    
    Args:
        doi_hash: The DOI hash to process
        config: Pipeline configuration dictionary
        
    Returns:
        True if extraction succeeded
    """
    data_dir = config.get("data_dir", "data")
    
    logger.info(f"▶️  Top Entity Extraction: {doi_hash}")
    
    # Load meta config to get main ontology
    try:
        meta_config = load_meta_config(config.get("meta_task_config", "configs/meta_task/meta_task_config.json"))
        main_ontology = meta_config.get("ontologies", {}).get("main", {})
        ontology_name = main_ontology.get("name", "ontosynthesis")
        logger.info(f"   Using ontology: {ontology_name}")
        top_class_iri, top_class_comment = _load_top_entity_contract(meta_config, main_ontology)
        policies = (main_ontology.get("runtime_policies") or {}) if isinstance(main_ontology, dict) else {}
        te_pol = (policies.get("top_entity_extraction") or {}) if isinstance(policies, dict) else {}
        invalidate_subs = te_pol.get("invalidate_top_entities_txt_substrings") or []
        count_prefixes = te_pol.get("count_lines_starting_with")
        if not count_prefixes:
            iter1_pol = (policies.get("iter1_top_entity_kg") or {}) if isinstance(policies, dict) else {}
            iter1_rules = (iter1_pol.get("prompt_rules") or {}) if isinstance(iter1_pol, dict) else {}
            top_name = str(iter1_rules.get("top_level_entity_name") or "").strip()
            count_prefixes = [top_name] if top_name else []
        identifier_code_regex = te_pol.get("identifier_code_regex")
    except Exception as e:
        logger.error(f"❌ Failed to load meta config: {e}")
        return False
    
    # Run extraction
    try:
        success = asyncio.run(
            extract_top_entities(
                doi_hash,
                data_dir,
                ontology_name,
                invalidate_top_entities_txt_substrings=invalidate_subs,
                count_lines_starting_with=count_prefixes,
                identifier_code_regex=identifier_code_regex,
                top_class_iri=top_class_iri,
                top_class_comment=top_class_comment,
            )
        )
        
        if success:
            logger.info(f"✅ Top Entity Extraction completed: {doi_hash}")
        else:
            logger.error(f"❌ Top Entity Extraction failed: {doi_hash}")
        
        return success
        
    except Exception as e:
        logger.error(f"❌ Top Entity Extraction failed with exception: {e}")
        return False


if __name__ == "__main__":
    # Test mode
    if len(sys.argv) > 1:
        test_hash = sys.argv[1]
        test_config = {"data_dir": "data"}
        print(f"Running top entity extraction for DOI hash: {test_hash}")
        success = run_step(test_hash, test_config)
        print(f"Top entity extraction {'succeeded' if success else 'failed'}.")
    else:
        print("Usage: python -m src.pipelines.top_entity_extraction.extract <doi_hash>")

