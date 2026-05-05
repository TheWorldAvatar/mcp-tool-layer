"""
Top Entity KG Building Pipeline Step

This step extracts top-level entities from the stitched markdown and builds
a knowledge graph using an LLM agent with MCP tools.
"""
import os
import sys
import json
import asyncio
import tempfile
import re
import unicodedata
import hashlib
from pathlib import Path
from typing import List, Dict
from filelock import FileLock
from rdflib import Graph, URIRef
from rdflib.namespace import RDF, RDFS

# Add project root to path for imports
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from models.BaseAgent import BaseAgent
from models.ModelConfig import ModelConfig
from src.utils.global_logger import get_logger
from src.pipelines.utils.ttl_publisher import publish_top_ttl

logger = get_logger("pipeline", "top_entity_kg_building")


def _first_label(g: Graph, node: URIRef) -> str:
    for value in g.objects(node, RDFS.label):
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _normalize_entity_label_key(text: str) -> str:
    raw = unicodedata.normalize("NFKC", str(text or "")).strip()
    bracket_match = re.match(r"^[A-Za-z][A-Za-z0-9_]*-\d+\s+\[(.+)\]\s*$", raw)
    if bracket_match:
        raw = bracket_match.group(1).strip()
    normalized = raw.lower()
    greek_names = {
        "α": "alpha",
        "β": "beta",
        "γ": "gamma",
        "δ": "delta",
    }
    for symbol, name in greek_names.items():
        normalized = normalized.replace(symbol, name)
    for token in (" synthesis",):
        if normalized.endswith(token):
            normalized = normalized[: -len(token)]
    normalized = normalized.replace("·", "").replace("•", "").replace(".", "")
    normalized = re.sub(r"[^a-z0-9]+", "", normalized)
    return normalized


def _choose_preferred_typed_target(g: Graph, typed_targets: list[URIRef]) -> URIRef | None:
    if not typed_targets:
        return None

    def _score(node: URIRef) -> tuple[int, str]:
        outgoing = sum(1 for _ in g.triples((node, None, None)))
        incoming = sum(1 for _ in g.triples((None, None, node)))
        return (outgoing + incoming, str(node))

    return sorted(typed_targets, key=_score, reverse=True)[0]


def _canonicalize_parsed_top_entities(*, g: Graph, entities: list[dict]) -> list[dict]:
    if not isinstance(entities, list) or not entities:
        return entities

    by_label: dict[str, list[dict]] = {}
    for entity in entities:
        if not isinstance(entity, dict):
            continue
        label_key = _normalize_entity_label_key(str(entity.get("label") or ""))
        if label_key:
            by_label.setdefault(label_key, []).append(entity)

    canonical_uris: dict[str, str] = {}
    for label_key, group in by_label.items():
        uri_candidates = []
        for entity in group:
            uri = str(entity.get("uri") or "").strip()
            if uri:
                uri_ref = URIRef(uri)
                if any(g.triples((uri_ref, None, None))):
                    uri_candidates.append(uri_ref)
        chosen = _choose_preferred_typed_target(g, sorted(set(uri_candidates), key=str))
        if chosen is not None:
            canonical_uris[label_key] = str(chosen)

    normalized_entities: list[dict] = []
    seen_uris: set[str] = set()
    for entity in entities:
        if not isinstance(entity, dict):
            continue
        updated = dict(entity)
        label_key = _normalize_entity_label_key(str(entity.get("label") or ""))
        canonical_uri = canonical_uris.get(label_key)
        if canonical_uri:
            updated["uri"] = canonical_uri
        uri = str(updated.get("uri") or "").strip()
        if not uri or uri in seen_uris:
            continue
        seen_uris.add(uri)
        normalized_entities.append(updated)
    return normalized_entities


def _local_name(iri: str) -> str:
    text = str(iri or "").strip()
    if "#" in text:
        return text.rsplit("#", 1)[-1]
    return text.rstrip("/").rsplit("/", 1)[-1]


def _get_top_entity_class_iri(meta_config: dict, default: str = "https://www.theworldavatar.com/kg/OntoSyn/ChemicalSynthesis") -> str:
    policies = _get_runtime_policies(meta_config)
    value = (
        ((policies.get("main_entity_kg", {}) or {}).get("shell_validation", {}) or {})
        .get("top_entity_class_iri")
    )
    return str(value or default).strip()


def _mint_top_entity_iri(label: str, top_class_iri: str = "https://www.theworldavatar.com/kg/OntoSyn/ChemicalSynthesis") -> str:
    digest = hashlib.sha1(str(label or "").strip().encode("utf-8")).hexdigest()
    return f"https://www.theworldavatar.com/kg/instance/{_local_name(top_class_iri) or 'TopEntity'}/{digest}"


def _top_entities_from_txt(doi_folder: str, top_class_iri: str = "https://www.theworldavatar.com/kg/OntoSyn/ChemicalSynthesis") -> list[dict]:
    """Fallback structured top-entity JSON from the simple text extraction output."""
    txt_path = os.path.join(doi_folder, "top_entities.txt")
    if not os.path.exists(txt_path):
        return []
    try:
        with open(txt_path, "r", encoding="utf-8") as f:
            lines = [line.strip(" \t\r\n-•") for line in f if line.strip()]
    except Exception:
        return []

    entities: list[dict] = []
    seen: set[str] = set()
    for label in lines:
        bracket_match = re.match(r"^[A-Za-z][A-Za-z0-9_]*-\d+\s+\[(.+)\]\s*$", label)
        if bracket_match:
            label = bracket_match.group(1)
        label = re.sub(r"^\s*(?:ChemicalSynthesis\s*[—:-]\s*)", "", label, flags=re.IGNORECASE).strip()
        if not label:
            continue
        key = _normalize_entity_label_key(label)
        if not key or key in seen:
            continue
        seen.add(key)
        entities.append({"uri": _mint_top_entity_iri(label, top_class_iri), "label": label, "types": []})
    return entities


def _merge_txt_top_entity_fallback(
    doi_folder: str,
    entities: list[dict],
    top_class_iri: str = "https://www.theworldavatar.com/kg/OntoSyn/ChemicalSynthesis",
) -> list[dict]:
    """Supplement parser output with labels from top_entities.txt when SPARQL/TTL is incomplete."""
    txt_entities = _top_entities_from_txt(doi_folder, top_class_iri)
    if not txt_entities:
        return entities or []
    merged: list[dict] = []
    seen: set[str] = set()
    for entity in (entities or []) + txt_entities:
        if not isinstance(entity, dict):
            continue
        key = _normalize_entity_label_key(str(entity.get("label") or ""))
        if not key or key in seen:
            continue
        seen.add(key)
        merged.append(entity)
    return merged


def _materialize_supplemented_top_entities(
    g: Graph,
    entities: list[dict],
    top_class_iri: str = "https://www.theworldavatar.com/kg/OntoSyn/ChemicalSynthesis",
) -> bool:
    """Ensure txt-fallback top entities are also present in the iter1 TTL graph."""
    changed = False
    top_class = URIRef(top_class_iri)
    for entity in entities or []:
        if not isinstance(entity, dict):
            continue
        uri = str(entity.get("uri") or "").strip()
        label = str(entity.get("label") or "").strip()
        if not uri or not label:
            continue
        node = URIRef(uri)
        if (node, RDF.type, top_class) not in g:
            g.add((node, RDF.type, top_class))
            changed = True
        if (node, RDFS.label, None) not in g:
            from rdflib import Literal

            g.add((node, RDFS.label, Literal(label)))
            changed = True
    return changed


def resolve_generated_file(path: str) -> str:
    """
    Resolve a generated artifact path.

    Prefer `ai_generated_contents_candidate/` (where generation writes in this repo),
    then fall back to `ai_generated_contents/` if present.
    """
    path = (path or "").replace("\\", "/")
    candidates: list[str] = []
    if path.startswith("ai_generated_contents/"):
        candidates.append(path.replace("ai_generated_contents/", "ai_generated_contents_candidate/", 1))
        candidates.append(path)
    elif path.startswith("ai_generated_contents_candidate/"):
        candidates.append(path)
        candidates.append(path.replace("ai_generated_contents_candidate/", "ai_generated_contents/", 1))
    else:
        candidates.append(path)

    for p in candidates:
        if p and os.path.exists(p):
            return p
    return candidates[0]

# -------------------- Global state writer --------------------
GLOBAL_STATE_DIR = "data"
GLOBAL_STATE_JSON = os.path.join(GLOBAL_STATE_DIR, "global_state.json")
GLOBAL_STATE_LOCK = os.path.join(GLOBAL_STATE_DIR, "global_state.lock")


def _get_runtime_policies(meta_config: dict) -> dict:
    """Return runtime policy block from meta config."""
    return (
        (meta_config or {})
        .get("ontologies", {})
        .get("main", {})
        .get("runtime_policies", {})
        or {}
    )


def _get_iter1_entity_context_name(meta_config: dict, default: str = "top") -> str:
    """Resolve the configured iter1 entity context name."""
    policies = _get_runtime_policies(meta_config)
    value = (
        (policies.get("iter1_top_entity_kg", {}) or {}).get("global_state_entity_name")
        or default
    )
    return str(value).strip() or default


def _get_iter1_entity_context_aliases(meta_config: dict, default: str = "top") -> list[str]:
    """
    Return acceptable ITER1 persistence context names.

    Some older/generated ITER1 prompts initialized memory with the concrete top-level
    class name (e.g. ``MedicalCase``) instead of the configured shared context name
    (e.g. ``top``). During fallback recovery, accept both names so existing runs can
    still be recovered deterministically.
    """
    primary = _get_iter1_entity_context_name(meta_config, default=default)
    policies = _get_runtime_policies(meta_config)
    iter1_cfg = (policies.get("iter1_top_entity_kg", {}) or {})
    prompt_rules = (iter1_cfg.get("prompt_rules", {}) or {})
    shell_validation = ((policies.get("main_entity_kg", {}) or {}).get("shell_validation", {}) or {})

    top_level_entity_name = str(prompt_rules.get("top_level_entity_name") or "").strip()
    top_entity_class_iri = str(shell_validation.get("top_entity_class_iri") or "").strip()
    top_entity_class_local = ""
    if top_entity_class_iri:
        top_entity_class_local = top_entity_class_iri.rsplit("/", 1)[-1].rsplit("#", 1)[-1].strip()

    aliases: list[str] = []
    for name in (primary, top_level_entity_name, top_entity_class_local, "MedicalCase", "top"):
        clean = str(name or "").strip()
        if clean and clean not in aliases:
            aliases.append(clean)
    return aliases


def _apply_identifier_runtime_env(meta_config: dict) -> None:
    """
    Export config-derived identifier handling rules to environment variables so
    generated MCP utility modules can normalize doi/hash arguments consistently.
    """
    policies = _get_runtime_policies(meta_config)
    identifier = policies.get("identifier_handling", {}) or {}

    def _set_or_unset(key: str, value: str | None) -> None:
        if value is None or str(value).strip() == "":
            os.environ.pop(key, None)
        else:
            os.environ[key] = str(value)

    prefer = identifier.get("prefer_global_state_for_nonhash")
    _set_or_unset(
        "TWA_MCP_IDENTIFIER_PREFER_GLOBAL_STATE_FOR_NONHASH",
        "1" if bool(prefer) else None,
    )
    _set_or_unset(
        "TWA_MCP_IDENTIFIER_PRESERVE_HASH_REGEX",
        identifier.get("preserve_hash_regex"),
    )
    patterns = identifier.get("fallback_to_global_state_patterns")
    _set_or_unset(
        "TWA_MCP_IDENTIFIER_FALLBACK_PATTERNS_JSON",
        json.dumps(patterns, ensure_ascii=False) if isinstance(patterns, list) else None,
    )


def _augment_iter1_prompt_with_runtime_rules(prompt_template: str, doi_hash: str, meta_config: dict) -> str:
    """
    Append config-derived runtime rules to the ITER1 prompt.
    This keeps the policy outside code while still making the agent behavior explicit.
    """
    policies = _get_runtime_policies(meta_config)
    iter1 = policies.get("iter1_top_entity_kg", {}) or {}
    prompt_rules = iter1.get("prompt_rules", {}) or {}
    lines: list[str] = []

    doi_source = str(prompt_rules.get("doi_argument_source") or "").strip()
    top_name = str(prompt_rules.get("top_level_entity_name") or "").strip()
    forbid_label_as_doi = bool(prompt_rules.get("forbid_human_readable_label_as_doi"))

    if doi_source or top_name or forbid_label_as_doi:
        lines.append("Config-derived runtime rules:")
        if doi_source:
            lines.append(
                f"- When calling `init_memory`, pass the current document identifier value `{doi_hash}` as the `doi` argument."
            )
        if top_name:
            lines.append(
                f"- When calling `init_memory`, set `top_level_entity_name` to `{top_name}`."
            )
        if forbid_label_as_doi:
            lines.append(
                "- Never pass a human-readable case label, title, or extracted description as the `doi` argument."
            )
            lines.append(
                "- Use human-readable text only for entity labels or descriptive fields, never for document identifiers."
            )

    if not lines:
        return prompt_template
    return prompt_template.rstrip() + "\n\n" + "\n".join(lines) + "\n"

def write_global_state(doi: str, top_level_entity_name: str, top_level_entity_iri: str | None = None):
    """Write global state atomically with file lock for MCP server to read."""
    os.makedirs(GLOBAL_STATE_DIR, exist_ok=True)
    lock = FileLock(GLOBAL_STATE_LOCK)
    lock.acquire(timeout=30.0)
    try:
        state = {"doi": doi, "top_level_entity_name": top_level_entity_name}
        if top_level_entity_iri:
            state["top_level_entity_iri"] = top_level_entity_iri
        fd, tmp = tempfile.mkstemp(dir=GLOBAL_STATE_DIR, suffix=".json.tmp")
        os.close(fd)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
        os.replace(tmp, GLOBAL_STATE_JSON)
        logger.info(f"Global state written: doi={doi}, entity={top_level_entity_name}")
    finally:
        lock.release()


def load_meta_config(config_path: str = "configs/meta_task/meta_task_config.json") -> dict:
    """Load the meta task configuration."""
    if not os.path.exists(config_path):
        logger.error(f"Meta config not found: {config_path}")
        return {}
    
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Failed to load meta config: {e}")
        return {}


def load_extraction_prompt(prompt_path: str) -> str:
    """Load the extraction prompt from a markdown file."""
    if not os.path.exists(prompt_path):
        logger.error(f"Prompt file not found: {prompt_path}")
        return ""
    
    try:
        with open(prompt_path, 'r', encoding='utf-8') as f:
            return f.read()
    except Exception as e:
        logger.error(f"Failed to load prompt: {e}")
        return ""


def load_extraction_hints(doi_hash: str, data_dir: str = "data") -> str:
    """Load the extraction hints from the top_entity_extraction step."""
    hints_path = os.path.join(data_dir, doi_hash, "top_entities.txt")
    
    if not os.path.exists(hints_path):
        logger.error(f"Extraction hints not found: {hints_path}")
        return ""
    
    try:
        with open(hints_path, 'r', encoding='utf-8') as f:
            return f.read()
    except Exception as e:
        logger.error(f"Failed to load extraction hints: {e}")
        return ""


def load_paper_content(doi_hash: str, data_dir: str = "data") -> str:
    """
    Load the best-available paper content for KG building.

    Prefer stitched markdown, then text-only markdown, then raw markdown. Append
    supporting-information markdown when present because top-level synthesis
    procedures may only be named in the SI experimental section.
    """
    doi_dir = os.path.join(data_dir, doi_hash)
    stitched = os.path.join(doi_dir, f"{doi_hash}_stitched.md")
    text_md = os.path.join(doi_dir, f"{doi_hash}_text.md")
    raw_md = os.path.join(doi_dir, f"{doi_hash}.md")

    main_text = ""
    for p in (stitched, text_md, raw_md):
        if not os.path.exists(p):
            continue
        try:
            txt = Path(p).read_text(encoding="utf-8")
        except Exception as e:
            logger.warning(f"Failed to read {p}: {e}")
            continue
        if txt and txt.strip():
            main_text = txt
            break
    if not main_text:
        return ""

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
    return "".join(parts)


async def run_kg_building_agent(
    doi_hash: str,
    prompt_template: str,
    hints: str,
    paper_content: str,
    mcp_tools: List[str],
    mcp_set_name: str,
    model_name: str = "gpt-4o",
    temperature: float = 0.1,
    top_p: float = 0.1,
    entity_context_name: str = "top",
) -> tuple[str, dict]:
    """
    Run the KG building agent with the given configuration.
    
    Args:
        doi_hash: DOI hash identifier
        prompt_template: Prompt template for the agent
        hints: Extracted hints from previous step
        mcp_tools: List of MCP tool names to use
        mcp_set_name: Name of the MCP set configuration file
        model_name: LLM model name
        temperature: Model temperature
        top_p: Model top_p parameter
        
    Returns:
        Tuple of (response, metadata)
    """
    # Format the prompt robustly.
    # `{paper_content}` MUST be the document text, not the extracted top-entity list.
    instruction = prompt_template
    replacements: dict[str, str] = {
        "doi": doi_hash,
        "hash": doi_hash,
        "paper_content": paper_content or "",
        "top_entities": hints or "",
        "hints": hints or "",
    }
    for k, v in replacements.items():
        instruction = instruction.replace("{" + k + "}", v)

    # Last-resort: never leave placeholders behind.
    instruction = instruction.replace("{paper_content}", paper_content or "")

    # Append content defensively if template doesn't include it.
    if paper_content and paper_content.strip() and paper_content.strip() not in instruction:
        instruction = instruction.rstrip() + "\n\n" + paper_content.strip() + "\n"
    if hints and hints.strip() and hints.strip() not in instruction:
        instruction = (
            instruction.rstrip()
            + "\n\nTop-entity list (from previous step):\n"
            + hints.strip()
            + "\n"
        )
    
    # Write global state for MCP server using the configured iter1 entity context name.
    logger.info(f"📝 Writing global state for MCP server")
    write_global_state(doi_hash, entity_context_name)
    
    # Create agent with MCP tools
    agent = BaseAgent(
        model_name=model_name,
        model_config=ModelConfig(temperature=temperature, top_p=top_p),
        remote_model=True,
        mcp_tools=mcp_tools,
        mcp_set_name=mcp_set_name
    )
    
    logger.info(f"🚀 Running KG building agent for {doi_hash}")
    logger.info(f"   Model: {model_name}, MCP: {mcp_set_name}, Tools: {mcp_tools}")
    
    # Retry mechanism for agent execution
    max_retries = 3
    retry_delays = [5, 10, 15]  # Progressive backoff in seconds
    
    for attempt in range(max_retries):
        try:
            if attempt > 0:
                logger.info(f"🔄 Retry attempt {attempt + 1}/{max_retries}")
            
            response, metadata = await agent.run(instruction, recursion_limit=600)
            logger.info(f"✅ Agent completed successfully on attempt {attempt + 1}")
            return response, metadata
            
        except Exception as e:
            import traceback
            logger.error(f"❌ Agent execution failed on attempt {attempt + 1}/{max_retries}: {e}")
            logger.error(traceback.format_exc())
            
            if attempt < max_retries - 1:
                delay = retry_delays[attempt]
                logger.info(f"⏳ Waiting {delay}s before retry...")
                await asyncio.sleep(delay)
            else:
                logger.error(f"❌ All {max_retries} attempts failed for KG building agent")
                raise


def save_agent_response(doi_hash: str, response: str, data_dir: str = "data") -> None:
    """Save the agent response to a file."""
    output_dir = os.path.join(data_dir, doi_hash, "kg_building")
    os.makedirs(output_dir, exist_ok=True)
    
    response_path = os.path.join(output_dir, "iter1_response.md")
    
    try:
        with open(response_path, 'w', encoding='utf-8') as f:
            f.write(f"# Iteration 1 - Top Entity KG Building\n\n")
            f.write(f"## Response\n\n{response}")
        logger.info(f"✅ Saved agent response to {response_path}")
    except Exception as e:
        logger.error(f"Failed to save agent response: {e}")


def save_full_prompt(doi_hash: str, prompt: str, data_dir: str = "data") -> None:
    """Save the full prompt for reproducibility/debugging."""
    output_dir = os.path.join(data_dir, doi_hash, "kg_building")
    os.makedirs(output_dir, exist_ok=True)
    prompt_path = os.path.join(output_dir, "iter1_full_prompt.md")
    try:
        Path(prompt_path).write_text(prompt or "", encoding="utf-8")
    except Exception as e:
        logger.warning(f"Failed to save full prompt: {e}")


def copy_output_ttl(
    doi_hash: str,
    data_dir: str = "data",
    test_mode: bool = False,
    ontology_name: str = "ontosynthesis",
    meta_task_config_path: str = "configs/meta_task/meta_task_config.json",
) -> bool:
    """
    Copy the output.ttl to iteration_1.ttl.
    
    In normal mode:
        - Looks for: output.ttl or output_top.ttl in doi_hash root
    
    In test mode:
        - Looks for: top.ttl in `{ontology_name}_output/` directory
    """
    meta_cfg = load_meta_config(meta_task_config_path)
    iter1_entity_name = _get_iter1_entity_context_name(meta_cfg, default="top")
    iter1_entity_aliases = _get_iter1_entity_context_aliases(meta_cfg, default="top")
    doi_folder = os.path.join(data_dir, doi_hash)
    iteration_1_ttl = os.path.join(doi_folder, "iteration_1.ttl")
    
    if test_mode:
        # Test mode: Look for top.ttl in `{ontology_name}_output/`
        test_output_dir = os.path.join(doi_folder, f"{ontology_name}_output")
        test_candidates = [
            os.path.join(test_output_dir, "top.ttl"),
            os.path.join(test_output_dir, "Top.ttl"),
        ]
        
        for candidate in test_candidates:
            if os.path.exists(candidate):
                try:
                    import shutil
                    shutil.copy2(candidate, iteration_1_ttl)
                    logger.info(f"✅ [TEST MODE] Saved iteration_1.ttl from {os.path.basename(candidate)}")
                    _ = publish_top_ttl(
                        doi_hash=doi_hash,
                        ontology_name=ontology_name,
                        data_dir=data_dir,
                        meta_cfg=meta_cfg,
                        src_candidates=[iteration_1_ttl, candidate],
                    )
                    return True
                except Exception as e:
                    logger.error(f"Failed to copy {candidate}: {e}")

        # Fallbacks: candidate-first MCP servers in this repo often persist the working graph under
        # data/<hash>/memory/<entity_context>.ttl and/or export snapshots under data/<hash>/exports/<entity_context>_*.ttl.
        exports_dir = os.path.join(doi_folder, "exports")
        for alias in iter1_entity_aliases:
            memory_top_ttl = os.path.join(doi_folder, "memory", f"{alias}.ttl")
            if not os.path.exists(memory_top_ttl):
                continue
            try:
                import shutil
                shutil.copy2(memory_top_ttl, iteration_1_ttl)
                logger.info(f"✅ [TEST MODE] Saved iteration_1.ttl from memory/{alias}.ttl")
                _ = publish_top_ttl(
                    doi_hash=doi_hash,
                    ontology_name=ontology_name,
                    data_dir=data_dir,
                    meta_cfg=meta_cfg,
                    src_candidates=[iteration_1_ttl, memory_top_ttl],
                )
                return True
            except Exception as e:
                logger.error(f"Failed to copy memory/{alias}.ttl: {e}")
                return False

        try:
            if os.path.isdir(exports_dir):
                export_candidates = []
                for alias in iter1_entity_aliases:
                    export_candidates.extend(
                        os.path.join(exports_dir, f)
                        for f in os.listdir(exports_dir)
                        if f.lower().startswith(f"{alias.lower()}_") and f.lower().endswith(".ttl")
                    )
                if export_candidates:
                    export_candidates = sorted(set(export_candidates))
                    export_candidates.sort(key=lambda p: os.path.getmtime(p), reverse=True)
                    latest = export_candidates[0]
                    import shutil
                    shutil.copy2(latest, iteration_1_ttl)
                    logger.info(
                        f"✅ [TEST MODE] Saved iteration_1.ttl from latest export: {os.path.basename(latest)}"
                    )
                    _ = publish_top_ttl(
                        doi_hash=doi_hash,
                        ontology_name=ontology_name,
                        data_dir=data_dir,
                        meta_cfg=meta_cfg,
                        src_candidates=[iteration_1_ttl, latest],
                    )
                    return True
        except Exception as e:
            logger.warning(f"⚠️  [TEST MODE] Failed scanning exports fallback: {e}")

        logger.warning(
            f"⚠️  [TEST MODE] No top.ttl found in {test_output_dir} and no configured memory/export fallback found"
        )
        return False
    else:
        # Normal mode: Look for output.ttl or output_top.ttl
        output_ttl = os.path.join(doi_folder, "output.ttl")
        output_top_ttl = os.path.join(doi_folder, "output_top.ttl")
        # Candidate-first MCP servers in this repo persist the working graph under memory/
        # and (optionally) export snapshots under exports/. They DO NOT necessarily write
        # output.ttl/output_top.ttl into the DOI folder root.
        exports_dir = os.path.join(doi_folder, "exports")
        
        if os.path.exists(output_ttl):
            try:
                import shutil
                shutil.copy2(output_ttl, iteration_1_ttl)
                logger.info(f"✅ Saved iteration_1.ttl from output.ttl")
                _ = publish_top_ttl(
                    doi_hash=doi_hash,
                    ontology_name=ontology_name,
                    data_dir=data_dir,
                    meta_cfg=meta_cfg,
                    src_candidates=[iteration_1_ttl, output_ttl],
                )
                return True
            except Exception as e:
                logger.error(f"Failed to copy output.ttl: {e}")
                return False
        elif os.path.exists(output_top_ttl):
            try:
                import shutil
                shutil.copy2(output_top_ttl, iteration_1_ttl)
                logger.info(f"✅ Saved iteration_1.ttl from output_top.ttl")
                _ = publish_top_ttl(
                    doi_hash=doi_hash,
                    ontology_name=ontology_name,
                    data_dir=data_dir,
                    meta_cfg=meta_cfg,
                    src_candidates=[iteration_1_ttl, output_top_ttl],
                )
                return True
            except Exception as e:
                logger.error(f"Failed to copy output_top.ttl: {e}")
                return False
        else:
            for alias in iter1_entity_aliases:
                memory_top_ttl = os.path.join(doi_folder, "memory", f"{alias}.ttl")
                if not os.path.exists(memory_top_ttl):
                    continue
                # Fallback: use persisted memory graph with the configured iter1 entity context name
                # or a compatible legacy alias such as ``MedicalCase``.
                try:
                    import shutil
                    shutil.copy2(memory_top_ttl, iteration_1_ttl)
                    logger.info(f"✅ Saved iteration_1.ttl from memory/{alias}.ttl")
                    _ = publish_top_ttl(
                        doi_hash=doi_hash,
                        ontology_name=ontology_name,
                        data_dir=data_dir,
                        meta_cfg=meta_cfg,
                        src_candidates=[iteration_1_ttl, memory_top_ttl],
                    )
                    return True
                except Exception as e:
                    logger.error(f"Failed to copy memory/{alias}.ttl: {e}")
                    return False
            # Last-resort fallback: try the latest exported snapshot for the configured
            # iter1 entity context or a compatible legacy alias.
            try:
                if os.path.isdir(exports_dir):
                    export_candidates = []
                    for alias in iter1_entity_aliases:
                        export_candidates.extend(
                            os.path.join(exports_dir, f)
                            for f in os.listdir(exports_dir)
                            if f.lower().startswith(f"{alias.lower()}_") and f.lower().endswith(".ttl")
                        )
                    if export_candidates:
                        export_candidates = sorted(set(export_candidates))
                        export_candidates.sort(key=lambda p: os.path.getmtime(p), reverse=True)
                        latest = export_candidates[0]
                        import shutil
                        shutil.copy2(latest, iteration_1_ttl)
                        logger.info(f"✅ Saved iteration_1.ttl from latest export: {os.path.basename(latest)}")
                        _ = publish_top_ttl(
                            doi_hash=doi_hash,
                            ontology_name=ontology_name,
                            data_dir=data_dir,
                            meta_cfg=meta_cfg,
                            src_candidates=[iteration_1_ttl, latest],
                        )
                        return True
            except Exception as e:
                logger.warning(f"⚠️  Failed scanning exports fallback: {e}")

            logger.warning("⚠️  No output.ttl/output_top.ttl and no memory/export fallback found")
            return False


def parse_top_entities_from_ttl(
    doi_hash: str,
    ontology_name: str,
    data_dir: str = "data",
    meta_task_config_path: str = "configs/meta_task/meta_task_config.json",
) -> bool:
    """
    Parse the iteration_1.ttl using SPARQL to extract top entities and save as JSON.
    
    Args:
        doi_hash: DOI hash identifier
        ontology_name: Name of the ontology (e.g., "ontosynthesis")
        data_dir: Base data directory
        
    Returns:
        True if parsing succeeded
    """
    try:
        doi_folder = os.path.join(data_dir, doi_hash)
        meta_config = load_meta_config(meta_task_config_path)
        top_class_iri = _get_top_entity_class_iri(meta_config)
        ttl_path = os.path.join(doi_folder, "iteration_1.ttl")
        sparql_path = resolve_generated_file(
            f"ai_generated_contents/sparqls/{ontology_name}/top_entity_parsing.sparql"
        )
        output_json_path = os.path.join(doi_folder, "mcp_run", "iter1_top_entities.json")
        
        # Check if TTL exists
        if not os.path.exists(ttl_path):
            logger.error(f"❌ TTL file not found: {ttl_path}")
            return False
        
        # Check if SPARQL query exists
        if not os.path.exists(sparql_path):
            logger.error(f"❌ SPARQL query not found: {sparql_path}")
            return False
        
        # Load SPARQL query
        with open(sparql_path, 'r', encoding='utf-8') as f:
            sparql_query = f.read()
        
        # Parse TTL
        logger.info(f"📊 Parsing TTL with SPARQL query")
        g = Graph()
        g.parse(ttl_path, format="turtle")
        
        # Execute SPARQL query
        results = g.query(sparql_query)
        
        # Convert results to JSON format
        # NOTE: We do not assume any ontology-specific variable names here.
        # The SPARQL is expected to bind a top-entity variable (e.g. ?entity or ?synthesis)
        # and optionally ?label. We fall back to the first binding if needed.
        entities = []
        for row in results:
            # Prefer a generic ?entity variable if present, otherwise fall back to ?synthesis,
            # then finally to the first column of the row.
            if hasattr(row, "entity"):
                uri = str(row.entity)
            elif hasattr(row, "synthesis"):
                uri = str(row.synthesis)
            else:
                uri = str(row[0])

            label = (
                str(row.label)
                if hasattr(row, "label") and row.label
                else uri.split("/")[-1]
            )
            
            entities.append({
                "uri": uri,
                "label": label,
                # Type information can be inferred downstream; we keep this generic here.
                "types": []
            })
        
        entities = _canonicalize_parsed_top_entities(g=g, entities=entities)
        supplemented_entities = _merge_txt_top_entity_fallback(doi_folder, entities, top_class_iri)
        if len(supplemented_entities) > len(entities):
            logger.warning(
                "⚠️  Supplemented top-entity JSON from top_entities.txt: %s -> %s",
                len(entities),
                len(supplemented_entities),
            )
            if _materialize_supplemented_top_entities(g, supplemented_entities, top_class_iri):
                g.serialize(destination=ttl_path, format="turtle")
                logger.warning("⚠️  Materialized supplemented top entities into iteration_1.ttl")
        entities = supplemented_entities

        # CRITICAL VALIDATION: Check if entities list is empty
        if not entities or len(entities) == 0:
            logger.error(f"❌ CRITICAL: Parsed 0 entities from TTL - KG building failed to create any entities!")
            logger.error(f"   This usually means the agent didn't properly use the MCP tools")
            # Save empty JSON anyway for debugging
            os.makedirs(os.path.dirname(output_json_path), exist_ok=True)
            with open(output_json_path, 'w', encoding='utf-8') as f:
                json.dump(entities, f, indent=2)
            return False  # Signal failure so we can retry
        
        # Save to JSON
        os.makedirs(os.path.dirname(output_json_path), exist_ok=True)
        with open(output_json_path, 'w', encoding='utf-8') as f:
            json.dump(entities, f, indent=2)
        
        logger.info(f"✅ Parsed {len(entities)} canonical top entities from TTL")
        logger.info(f"   Saved to: {output_json_path}")
        
        # Log first few entities
        for entity in entities[:3]:
            logger.info(f"   - {entity['label']}")
        if len(entities) > 3:
            logger.info(f"   ... and {len(entities) - 3} more")
        
        return True
        
    except Exception as e:
        logger.error(f"❌ Failed to parse TTL: {e}")
        return False


def run_step(doi_hash: str, config: dict) -> bool:
    """
    Main entry point for the top entity KG building pipeline step.
    
    This step:
    1. Loads the meta task configuration to determine ontology and MCP settings
    2. Loads the extraction hints from the previous step
    3. Loads the KG building prompt
    4. Runs an LLM agent with MCP tools to build the knowledge graph
    5. Saves the output TTL as iteration_1.ttl
    
    Args:
        doi_hash: The DOI hash to process
        config: Pipeline configuration dictionary
        
    Returns:
        True if KG building succeeded
    """
    data_dir = config.get("data_dir", "data")
    doi_folder = os.path.join(data_dir, doi_hash)
    
    logger.info(f"▶️  Top Entity KG Building for {doi_hash}")

    # Load meta task configuration
    meta_task_config_path = config.get("meta_task_config", "configs/meta_task/meta_task_config.json")
    meta_config = load_meta_config(meta_task_config_path)
    if not meta_config:
        logger.error("❌ Failed to load meta task configuration")
        return False
    
    # Get main ontology configuration
    main_ontology = meta_config.get("ontologies", {}).get("main", {})
    ontology_name = main_ontology.get("name", "ontosynthesis")
    mcp_set_name = main_ontology.get("mcp_set_name", "run_created_mcp.json")
    mcp_tools = main_ontology.get("mcp_list", ["llm_created_mcp"])
    agent_model = main_ontology.get("agent_model") or "gpt-4o"
    iter1_entity_context_name = _get_iter1_entity_context_name(meta_config, default="top")
    _apply_identifier_runtime_env(meta_config)

    # Check if iteration_1.ttl already exists
    iteration_1_ttl = os.path.join(doi_folder, "iteration_1.ttl")
    if os.path.exists(iteration_1_ttl):
        logger.info(f"  ⏭️  iteration_1.ttl already exists; refreshing iter1_top_entities.json")
        return parse_top_entities_from_ttl(
            doi_hash,
            ontology_name,
            data_dir,
            meta_task_config_path=meta_task_config_path,
        )
    
    # Override with test MCP config if provided
    if "test_mcp_config" in config:
        mcp_set_name = config["test_mcp_config"]
        logger.info(f"  🧪 Using test MCP config")
    
    logger.info(f"  📋 Ontology: {ontology_name}")
    logger.info(f"  🔧 MCP Set: {mcp_set_name}")
    logger.info(f"  🛠️  MCP Tools: {mcp_tools}")
    logger.info(f"  🤖 Agent model: {agent_model}")
    
    # Load extraction hints from previous step
    hints = load_extraction_hints(doi_hash, data_dir)
    if not hints:
        logger.error("❌ Failed to load extraction hints")
        return False
    
    logger.info(f"  ✓ Loaded extraction hints ({len(hints)} chars)")
    
    # Load KG building prompt
    prompt_path = resolve_generated_file(
        f"ai_generated_contents/prompts/{ontology_name}/KG_BUILDING_ITER_1.md"
    )
    prompt_template = load_extraction_prompt(prompt_path)
    if not prompt_template:
        logger.error(f"❌ Failed to load prompt from {prompt_path}")
        return False
    prompt_template = _augment_iter1_prompt_with_runtime_rules(prompt_template, doi_hash, meta_config)
    
    logger.info(f"  ✓ Loaded KG building prompt")
    
    # Run the agent with retry logic for empty entity lists
    max_kg_retries = 3
    test_mode = "test_mcp_config" in config
    
    for kg_attempt in range(max_kg_retries):
        try:
            if kg_attempt > 0:
                logger.info(f"  🔄 KG Building retry attempt {kg_attempt + 1}/{max_kg_retries}")
                # Clean up previous failed attempt
                if os.path.exists(iteration_1_ttl):
                    os.remove(iteration_1_ttl)
                    logger.info(f"  🗑️  Removed failed iteration_1.ttl from previous attempt")
            
            paper_content = load_paper_content(doi_hash, data_dir)
            if not paper_content:
                logger.error("❌ Failed to load paper content for KG building")
                return False

            # Save full prompt for reproducibility/debugging
            try:
                preview_prompt = (
                    prompt_template
                    .replace("{doi}", doi_hash)
                    .replace("{paper_content}", paper_content)
                    .replace("{top_entities}", hints)
                    .replace("{hints}", hints)
                )
                save_full_prompt(doi_hash, preview_prompt, data_dir)
            except Exception:
                pass

            response, metadata = asyncio.run(
                run_kg_building_agent(
                    doi_hash=doi_hash,
                    prompt_template=prompt_template,
                    hints=hints,
                    paper_content=paper_content,
                    mcp_tools=mcp_tools,
                    mcp_set_name=mcp_set_name,
                    model_name=agent_model,
                    temperature=0.1,
                    top_p=0.1,
                    entity_context_name=iter1_entity_context_name,
                )
            )
            
            # Save agent response
            save_agent_response(doi_hash, response, data_dir)
            
            # Copy output TTL to iteration_1.ttl
            if not copy_output_ttl(
                doi_hash,
                data_dir,
                test_mode=test_mode,
                ontology_name=ontology_name,
                meta_task_config_path=meta_task_config_path,
            ):
                logger.warning("⚠️  Failed to save iteration_1.ttl")
                if kg_attempt < max_kg_retries - 1:
                    logger.info(f"  ⏳ Waiting 5s before retry...")
                    import time
                    time.sleep(5)
                    continue
                else:
                    return False
            
            # Parse TTL to extract top entities as JSON
            logger.info(f"  📊 Parsing top entities from TTL")
            parse_success = parse_top_entities_from_ttl(
                doi_hash,
                ontology_name,
                data_dir,
                meta_task_config_path=meta_task_config_path,
            )
            
            if not parse_success:
                # Parsing failed or returned empty entities list
                logger.error(f"  ❌ KG building attempt {kg_attempt + 1}/{max_kg_retries} produced no entities")
                if kg_attempt < max_kg_retries - 1:
                    logger.info(f"  ⏳ Waiting 5s before retry...")
                    import time
                    time.sleep(5)
                    continue
                else:
                    logger.error(f"  ❌ All {max_kg_retries} KG building attempts failed to produce entities")
                    return False
            
            # Success! Entities were created
            logger.info(f"✅ Top Entity KG Building completed for {doi_hash}")
            return True
            
        except Exception as e:
            logger.error(f"❌ KG building attempt {kg_attempt + 1}/{max_kg_retries} failed: {e}")
            if kg_attempt < max_kg_retries - 1:
                logger.info(f"  ⏳ Waiting 5s before retry...")
                import time
                time.sleep(5)
            else:
                logger.error(f"❌ All {max_kg_retries} KG building attempts failed")
                return False
    
    return False


if __name__ == "__main__":
    # Example usage for standalone testing
    if len(sys.argv) > 1:
        test_doi_hash = sys.argv[1]
        test_config = {
            "data_dir": "data"
        }
        print(f"Running top entity KG building step for DOI hash: {test_doi_hash}")
        success = run_step(test_doi_hash, test_config)
        print(f"Top entity KG building step {'succeeded' if success else 'failed'}.")
    else:
        print("Usage: python -m src.pipelines.top_entity_kg_building.build <doi_hash>")

