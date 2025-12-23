#!/usr/bin/env python3
"""
iteration_creation_agent.py

An LLM-backed agent to create iterations.json files directly from T-Box TTL(s).

Requirements:
- Use LLMCreator (gpt-5) and domain-generic prompts only.
- Inputs are the given T-Box TTL files; no hardcoded task specifics.
- Let the model infer ontology name/structure and produce JSON.

Usage (CLI):
  # Generate for selected ontologies; outputs to ai_generated_contents_candidate/iterations/<ontology>/iterations.json
  python -m src.agents.scripts_and_prompts_generation.iteration_creation_agent --ontosynthesis --ontomops --ontospecies
"""

from __future__ import annotations

import os
import sys
import json
import argparse
from pathlib import Path
from typing import Any, Dict, Optional, Tuple, List

from models.LLMCreator import LLMCreator
from models.ModelConfig import ModelConfig
from src.utils.global_logger import get_logger


LOGGER = get_logger("agent", "IterationCreationAgent")


def _read_text_file(file_path: Path) -> str:
    """Read a text file in UTF-8, returning empty string if missing."""
    try:
        return file_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


def _infer_ontology_name_from_ttl(ttl_text: str, default: str = "ontology") -> str:
    """Infer ontology short name from TTL prefix declarations or known markers."""
    lowered = ttl_text.lower()
    if "@prefix ontosyn:" in ttl_text:
        return "ontosynthesis"
    if "@prefix ontomops:" in ttl_text or "kg/ontomops/" in lowered:
        return "ontomops"
    if "@prefix ontospecies:" in ttl_text or "/ontospecies/" in lowered:
        return "ontospecies"
    return default


def _compose_prompt(ttl_bundle_text: str) -> str:
    PROMPT_HEADER = """Produce ONE JSON object (iterations.json) that configures a multi-iteration extraction and KG-building pipeline aligned with the provided T-Box schema. Keep the plan domain-agnostic and non-prescriptive about environment details.

    Strict output rules:
    - Output MUST be valid JSON only (no markdown, no comments).
    - Include top-level keys: 'ontology' (lowercase short name), 'description', 'iterations'.
    - 'iterations' is an array of objects with pragmatic fields commonly used in such configs (e.g., iteration_number, name, description,
    optional pre-extraction flags/paths, extraction/kg prompts, model_config_key, per_entity, use_agent, optional MCP tool settings,
    inputs/outputs objects, and optional sub_iterations that enrich a parent iteration via an 'enriches' field).
    - Use generic placeholders for any paths or file names and allow tokens like '{entity_safe}'. Details can be refined by scripts later..
    - Do NOT include dataset-specific details.
    - It is recommended to do multiple iterations for complex ontologies and single iteration for simple ontologies.
    - For complex part of certain ontologies, it is recommended to use mulitple sub-iterations to enrich the parent iteration.
    - CRITICAL CONSTRAINT: ONLY ONE iteration can have the complex pre-extraction mechanism (has_pre_extraction: true, pre_extraction_prompt, pre_extraction_model_key).
    Choose the most complex iteration (typically the one extracting detailed sub-components or steps) to have pre-extraction.
    All other iterations should use simple direct extraction from the full paper content without pre-extraction.
    - IMPORTANT: For pre_extraction_prompt, always use a file path format like "ai_generated_contents/prompts/{ontology}/PRE_EXTRACTION_ITER_{N}.md",
    NOT a description. The actual prompt content will be generated later by a separate script.

    Return ONLY the JSON.

    T-Box :
    """
    body = ttl_bundle_text
    return PROMPT_HEADER + body


def _generate_with_llm(ttl_bundle_text: str) -> dict:
    """Generate iterations.json purely via LLMCreator using a domain-generic prompt."""
    prompt = _compose_prompt(ttl_bundle_text)
    print("🧠 Invoking LLM to create iterations.json ...", flush=True)
    llm = LLMCreator(
        model="gpt-4o",
        remote_model=True,
        model_config=ModelConfig(temperature=0, top_p=1.0),
        structured_output=False,
    ).setup_llm()
    try:
        resp_obj = llm.invoke(prompt)
    except Exception as e:
        print(f"❌ LLM invocation failed: {e}", flush=True)
        raise
    content = getattr(resp_obj, "content", None)
    if not isinstance(content, str):
        content = str(resp_obj) if resp_obj is not None else ""
    text = content.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        if lines and lines[0].startswith("```json"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines)
    return json.loads(text)


def _load_meta_task_config() -> Dict[str, Any]:
    """Load meta task config JSON if present."""
    cfg_path = Path("configs/meta_task/meta_task_config.json")
    try:
        if cfg_path.exists():
            return json.loads(cfg_path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _role_info_for(meta_cfg: Dict[str, Any], ontology_name: str) -> Dict[str, Any]:
    """Return role and known settings for the ontology from meta config."""
    info: Dict[str, Any] = {"role": None, "complex_pipeline": None, "mcp_set_name": None, "mcp_tools": None, "agent_model": None, "description": None}
    try:
        ont = meta_cfg.get("ontologies", {})
        main = ont.get("main", {})
        if isinstance(main, dict) and main.get("name") == ontology_name:
            info["role"] = "main"
            info["complex_pipeline"] = bool(main.get("complex_pipeline", False))
            info["mcp_set_name"] = main.get("mcp_set_name")
            info["mcp_tools"] = main.get("mcp_list")
            info["description"] = main.get("description")
            return info
        for ext in ont.get("extensions", []) or []:
            if isinstance(ext, dict) and ext.get("name") == ontology_name:
                info["role"] = "extension"
                info["complex_pipeline"] = bool(ext.get("complex_pipeline", False))
                info["mcp_set_name"] = ext.get("mcp_set_name")
                info["mcp_tools"] = ext.get("mcp_list")
                info["agent_model"] = ext.get("agent_model")
                info["description"] = ext.get("description")
                return info
    except Exception:
        pass
    return info


def _postprocess_iterations_json(data: Dict[str, Any], ontology: str, role_info: Dict[str, Any]) -> Dict[str, Any]:
    """Adjust the LLM output using explicit info from meta_task_config.json."""
    if not isinstance(data, dict):
        return data

    data.setdefault("ontology", ontology)
    data["ontology"] = ontology

    if role_info.get("description"):
        data["description"] = role_info["description"]

    iterations = data.get("iterations")
    if not isinstance(iterations, list) or not iterations:
        return data

    def _iter_num_str(n: Any) -> str:
        try:
            # 3 or 3.1 → '3' or '3.1'
            return str(n)
        except Exception:
            return "1"

    def _as_int(n: Any) -> int:
        try:
            return int(float(n))
        except Exception:
            return 1

    def _ensure_extraction_prompt(obj: Dict[str, Any]) -> None:
        n_str = _iter_num_str(obj.get("iteration_number"))
        safe_n = n_str.replace(".", "_")
        # Sub-iterations should use underscore in filename to match style (e.g., 3_1)
        use = safe_n
        obj.setdefault(
            "extraction_prompt",
            f"ai_generated_contents/prompts/{ontology}/EXTRACTION_ITER_{use}.md"
        )
        # If it's not md, coerce
        if not str(obj.get("extraction_prompt", "")).endswith(".md"):
            obj["extraction_prompt"] = f"ai_generated_contents/prompts/{ontology}/EXTRACTION_ITER_{use}.md"

    def _ensure_extension_outputs(obj: Dict[str, Any], ontology_name: str) -> None:
        """Hardcode extension-specific output file paths to match MCP agent behavior.
        
        These paths are FIXED and script-controlled, not LLM-generated.
        """
        outputs = obj.setdefault("outputs", {})
        if not isinstance(outputs, dict):
            outputs = {}
            obj["outputs"] = outputs
        
        # Hardcoded extension file paths (matching actual MCP agent output locations)
        # Extraction file: where extraction results are stored
        # Format: mcp_run_{ontology}/extraction_{entity_safe}.txt
        outputs["extraction_file"] = f"mcp_run_{ontology_name}/extraction_{{entity_safe}}.txt"
        
        # Extension prompt file: where formatted KG building prompt is saved
        # Format: prompts/{ontology}_kg_building/{entity_safe}.md
        outputs["extension_prompt_file"] = f"prompts/{ontology_name}_kg_building/{{entity_safe}}.md"
        
        # Output TTL: where MCP agent saves the extension TTL
        # HARDCODED patterns matching actual MCP agent behavior:
        # - OntoMOPs: {ontology}_output/{ontology}_extension_{entity_name}.ttl (entity_name has spaces)
        # - OntoSpecies: {ontology}_output/{slugified_entity_name}.ttl (slugified = spaces->hyphens, no prefix)
        # Pipeline will search in {ontology}_output/ directory for the actual file
        outputs["output_ttl_dir"] = f"{ontology_name}_output"
        if ontology_name == "ontomops":
            # Pattern: ontomops_output/ontomops_extension_{entity_name}.ttl
            outputs["output_ttl"] = f"{ontology_name}_output/{ontology_name}_extension_{{entity_name}}.ttl"
        elif ontology_name == "ontospecies":
            # Pattern: ontospecies_output/{slugified_entity_name}.ttl
            outputs["output_ttl"] = f"{ontology_name}_output/{{entity_slugified}}.ttl"
        else:
            # Generic fallback
            outputs["output_ttl"] = f"{ontology_name}_output/{ontology_name}_extension_{{entity_safe}}.ttl"
        
        # Also add recursion_limit if missing (hardcoded default)
        obj.setdefault("recursion_limit", 500)
        
        # Ensure extension_prompt path exists (for loading the template)
        # This is the KG building prompt template path
        if "extension_prompt" not in obj:
            obj["extension_prompt"] = f"ai_generated_contents/prompts/{ontology_name}/EXTENSION.md"
        
        # Ensure kg_building_prompt is set (used by pipeline to load template)
        if "kg_building_prompt" not in obj:
            obj["kg_building_prompt"] = f"ai_generated_contents/prompts/{ontology_name}/KG_BUILDING_ITER_1.md"

    def _ensure_outputs_txt(obj: Dict[str, Any], iter_num: int, suffix: str) -> None:
        # Standardize textual outputs to .txt; prompts/responses to .md
        outputs = obj.setdefault("outputs", {})
        if not isinstance(outputs, dict):
            outputs = {}
            obj["outputs"] = outputs
        # Hints file
        outputs.setdefault("hints_file", f"mcp_run/iter{iter_num}_hints_{{entity_safe}}.txt")
        if str(outputs.get("hints_file", "")).endswith(".json"):
            outputs["hints_file"] = f"mcp_run/iter{iter_num}_hints_{{entity_safe}}.txt"
        # Prompt/response files (md)
        outputs.setdefault("prompt_file", f"prompts/iter{iter_num}{suffix}/{'{'}entity_safe{'}'}.md")
        outputs.setdefault("response_file", f"responses/iter{iter_num}{suffix}/{'{'}entity_safe{'}'}.md")
        # Force extensions
        if not str(outputs.get("prompt_file", "")).endswith(".md"):
            outputs["prompt_file"] = f"prompts/iter{iter_num}{suffix}/{'{'}entity_safe{'}'}.md"
        if not str(outputs.get("response_file", "")).endswith(".md"):
            outputs["response_file"] = f"responses/iter{iter_num}{suffix}/{'{'}entity_safe{'}'}.md"
        # Remove non-standard json outputs e.g., input_output_file
        allowed_keys = {
            "hints_file",
            "prompt_file",
            "response_file",
            "pre_extraction_file",
            "pre_extraction_prompt_file",
            "pre_extraction_response_file",
            "done_marker",
        }
        for k in list(outputs.keys()):
            if k not in allowed_keys:
                try:
                    # keep only allowed outputs
                    outputs.pop(k, None)
                except Exception:
                    pass

    def _ensure_pre_extraction(parent: Dict[str, Any], iter_num: int) -> None:
        parent["has_pre_extraction"] = True
        # FORCE overwrite pre_extraction_prompt to ensure it's always a file path
        # (LLM sometimes generates a description instead)
        parent["pre_extraction_prompt"] = f"ai_generated_contents/prompts/{ontology}/PRE_EXTRACTION_ITER_{iter_num}.md"
        parent.setdefault("pre_extraction_model_key", f"iter{iter_num}_pre_extraction")
        # Inputs
        inputs = parent.setdefault("inputs", {})
        if isinstance(inputs, dict):
            inputs.setdefault("pre_extraction_source", "stitched_paper")
            # If pre_extraction is present, remove generic 'source'
            if "source" in inputs:
                try:
                    inputs.pop("source", None)
                except Exception:
                    pass
        # Outputs for pre-extraction
        outputs = parent.setdefault("outputs", {})
        if isinstance(outputs, dict):
            outputs.setdefault("pre_extraction_file", "pre_extraction/entity_text_{entity_safe}.txt")
            outputs.setdefault("pre_extraction_prompt_file", f"prompts/iter{iter_num}_pre_extraction/{{entity_safe}}.md")
            outputs.setdefault("pre_extraction_response_file", f"responses/iter{iter_num}_pre_extraction/{{entity_safe}}.md")

    def _ensure_sub_iter_io(sub: Dict[str, Any], parent_iter_num: int, sub_idx_str: str) -> None:
        # Inputs
        inputs = sub.setdefault("inputs", {})
        if not isinstance(inputs, dict):
            inputs = {}
            sub["inputs"] = inputs
        inputs.setdefault("base_hints", f"mcp_run/iter{parent_iter_num}_hints_{{entity_safe}}.txt")
        inputs.setdefault("pre_extracted_text", "pre_extraction/entity_text_{entity_safe}.txt")
        # Remove any other extraneous input keys (e.g., steps_input, enriched_steps_input)
        allowed_in_keys = {"base_hints", "pre_extracted_text"}
        for k in list(inputs.keys()):
            if k not in allowed_in_keys:
                try:
                    inputs.pop(k, None)
                except Exception:
                    pass
        # Outputs
        outputs = sub.setdefault("outputs", {})
        if not isinstance(outputs, dict):
            outputs = {}
            sub["outputs"] = outputs
        outputs.setdefault("hints_file", f"mcp_run/iter{parent_iter_num}_hints_{{entity_safe}}.txt")
        outputs.setdefault("prompt_file", f"prompts/iter{parent_iter_num}.{sub_idx_str}_enrichment/{{entity_safe}}.md")
        outputs.setdefault("response_file", f"responses/iter{parent_iter_num}.{sub_idx_str}_enrichment/{{entity_safe}}.md")
        outputs.setdefault("done_marker", f"mcp_run/iter{parent_iter_num}_{parent_iter_num}.{sub_idx_str}_done_{{entity_safe}}.marker")

    def _ensure_kg_building_prompt(obj: Dict[str, Any]) -> None:
        n_str = _iter_num_str(obj.get("iteration_number"))
        if "." in n_str:
            # sub-iterations: skip kg_building_prompt
            return
        obj.setdefault(
            "kg_building_prompt",
            f"ai_generated_contents/prompts/{ontology}/KG_BUILDING_ITER_{n_str}.md"
        )

    def _ensure_default_source_if_no_pre_extraction(obj: Dict[str, Any]) -> None:
        if obj.get("has_pre_extraction"):
            return
        inputs = obj.setdefault("inputs", {})
        if isinstance(inputs, dict):
            inputs.setdefault("source", "stitched_paper")

    def _ensure_use_agent_rule(obj: Dict[str, Any]) -> None:
        """Enforce use_agent rule: Only ITER 2 should have use_agent=true, all others false."""
        it_num = _as_int(obj.get("iteration_number", 0))
        if it_num == 2:
            # ITER 2: use_agent should be true
            obj["use_agent"] = True
        else:
            # All other iterations: use_agent should be false
            obj["use_agent"] = False

    def _ensure_extraction_mcp(obj: Dict[str, Any]) -> None:
        # If the iteration uses agent mode for extraction, provide default extraction MCP settings
        if bool(obj.get("use_agent")):
            obj.setdefault("extraction_mcp_set_name", "chemistry.json")
            obj.setdefault("extraction_mcp_tools", ["pubchem", "enhanced_websearch", "ccdc"])

    complex_pipeline = role_info.get("complex_pipeline")
    mcp_set_name = role_info.get("mcp_set_name")
    mcp_tools = role_info.get("mcp_tools")
    agent_model = role_info.get("agent_model")

    if complex_pipeline is False:
        # Extension: force single concise iteration
        first = iterations[0]
        # Strip sub_iterations
        if isinstance(first, dict) and "sub_iterations" in first:
            first.pop("sub_iterations", None)
        # Prefer per-entity
        if isinstance(first, dict):
            first.setdefault("per_entity", True)
            # Enforce use_agent rule: extensions should use false (only ITER 2 uses true)
            _ensure_use_agent_rule(first)
            if mcp_set_name:
                first.setdefault("mcp_set_name", mcp_set_name)
            if mcp_tools:
                first.setdefault("mcp_tools", mcp_tools)
            if agent_model:
                first.setdefault("agent_model", agent_model)
            # Ensure extraction_prompt (md) and outputs (txt/md)
            _ensure_extraction_prompt(first)
            _ensure_outputs_txt(first, _as_int(first.get("iteration_number", 1)), "_extraction")
            _ensure_kg_building_prompt(first)
            _ensure_default_source_if_no_pre_extraction(first)
            _ensure_extraction_mcp(first)
            # CRITICAL: Hardcode extension-specific output paths (not LLM-generated)
            _ensure_extension_outputs(first, ontology)
        data["iterations"] = [first]
        return data

    # Main ontology or unknown: add MCP hints if missing
    # If known main 'ontosynthesis', align closer to GT structure
    if ontology == "ontosynthesis":
        # Drop iter1 if present
        iterations = [it for it in iterations if _as_int(it.get("iteration_number", 0)) != 1]
        data["iterations"] = iterations

    for it in iterations:
        if not isinstance(it, dict):
            continue
        if mcp_set_name and "mcp_set_name" not in it:
            it["mcp_set_name"] = mcp_set_name
        if mcp_tools and "mcp_tools" not in it:
            it["mcp_tools"] = mcp_tools
        # Ensure extraction prompt and outputs formatting
        _ensure_extraction_prompt(it)
        it_num = _as_int(it.get("iteration_number", 1))
        # Enforce use_agent rule: Only ITER 2 should have use_agent=true
        _ensure_use_agent_rule(it)
        _ensure_outputs_txt(it, it_num, "_extraction")
        _ensure_kg_building_prompt(it)
        _ensure_default_source_if_no_pre_extraction(it)
        _ensure_extraction_mcp(it)
        # If has sub-iterations, ensure pre-extraction fields on parent and fix sub-iteration IO
        sub_iters = it.get("sub_iterations")
        if isinstance(sub_iters, list) and sub_iters:
            _ensure_pre_extraction(it, it_num)
            # fix sub-iterations
            for sub in sub_iters:
                if not isinstance(sub, dict):
                    continue
                # ensure sub extraction prompt (md)
                _ensure_extraction_prompt(sub)
                # derive sub idx from iteration_number decimal part, fallback to sequence index+1
                sub_num = sub.get("iteration_number")
                sub_str = _iter_num_str(sub_num)
                # extract suffix after decimal, else use whole if decimal exists
                if "." in sub_str:
                    sub_idx_str = sub_str.split(".", 1)[1]
                else:
                    sub_idx_str = sub_str
                _ensure_sub_iter_io(sub, it_num, sub_idx_str)

        # OntoSynthesis-specific alignment for names/config keys and inputs
        if ontology == "ontosynthesis":
            if it_num == 2:
                it["name"] = "inputs_outputs"
                it["model_config_key"] = "iter2_hints"
                # ITER 2: use_agent should be true (already enforced by _ensure_use_agent_rule)
                it["use_agent"] = True
                # Inputs: keep only source
                inputs = it.setdefault("inputs", {})
                if isinstance(inputs, dict):
                    for k in list(inputs.keys()):
                        if k != "source":
                            inputs.pop(k, None)
                    inputs["source"] = "stitched_paper"
            elif it_num == 3:
                it["name"] = "synthesis_steps"
                it["model_config_key"] = "iter3_hints"
                # ITER 3: use_agent should be false (already enforced by _ensure_use_agent_rule)
                it["use_agent"] = False
                # Inputs: pre_extraction_source + extraction_source
                inputs = it.setdefault("inputs", {})
                if isinstance(inputs, dict):
                    for k in list(inputs.keys()):
                        if k not in ("pre_extraction_source", "extraction_source"):
                            inputs.pop(k, None)
                    inputs["pre_extraction_source"] = "stitched_paper"
                    inputs["extraction_source"] = "pre_extracted_text"
                # Sub-iteration naming and configs
                sub_iters = it.get("sub_iterations")
                if isinstance(sub_iters, list):
                    # sort by iteration_number to determine 3.1 then 3.2
                    def _key(si):
                        return float(str(si.get("iteration_number", "3.9")).replace(" ", "")) if isinstance(si, dict) else 9.9
                    sub_iters.sort(key=_key)
                    for idx, sub in enumerate(sub_iters, start=1):
                        if not isinstance(sub, dict):
                            continue
                        sub["name"] = "step_enrichment" if idx == 1 else "vessel_enrichment"
                        sub["model_config_key"] = "iter3_1_enrichment" if idx == 1 else "iter3_2_enrichment"
                        # enforce sub prompt filenames with underscore
                        n_str = _iter_num_str(sub.get("iteration_number"))
                        sub["extraction_prompt"] = f"ai_generated_contents/prompts/{ontology}/EXTRACTION_ITER_{n_str.replace('.', '_')}.md"
                        # Sub-iterations: use_agent should be false
                        _ensure_use_agent_rule(sub)
            elif it_num == 4:
                it["name"] = "yield_extraction"
                it["model_config_key"] = "iter4_hints"
                # ITER 4: use_agent should be false (already enforced by _ensure_use_agent_rule)
                it["use_agent"] = False
    return data


def create_iterations_json(ttl_paths: List[Path], output_dir: Path, meta_cfg: Optional[Dict[str, Any]] = None) -> Path:
    """
    Main entry: create an iterations.json for the ontology described by ttl_paths
    and write it under output_dir/<ontology>/iterations.json. The ontology short
    name is inferred from the first TTL content if possible.
    """
    if not ttl_paths:
        raise ValueError("No T-Box files provided")

    texts = []
    for p in ttl_paths:
        print(f"📖 Reading T-Box: {p}", flush=True)
        txt = _read_text_file(p)
        if not txt:
            raise FileNotFoundError(f"T-Box file not found or empty: {p}")
        texts.append(txt)

    ttl_bundle_text = "\n\n\n".join(texts)
    ontology = _infer_ontology_name_from_ttl(texts[0])
    print(f"🔎 Inferred ontology: {ontology}", flush=True)

    data = _generate_with_llm(ttl_bundle_text)
    role_info = _role_info_for(meta_cfg or {}, ontology)
    data = _postprocess_iterations_json(data, ontology, role_info)

    # Write to target path
    target_dir = output_dir / ontology
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / "iterations.json"
    target_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(f"✅ Wrote iterations file: {target_path}", flush=True)
    return target_path


def _default_output_dir() -> Path:
    return Path("ai_generated_contents_candidate") / "iterations"


def main():
    parser = argparse.ArgumentParser(
        description="Create iterations.json via LLM (gpt-5) from domain-generic prompts + hardcoded T-Box TTLs."
    )
    parser.add_argument("--ontosynthesis", action="store_true", help="Generate for OntoSynthesis (data/ontologies/ontosynthesis.ttl)")
    parser.add_argument("--ontomops", action="store_true", help="Generate for OntoMOPs (data/ontologies/ontomops-subgraph.ttl)")
    parser.add_argument("--ontospecies", action="store_true", help="Generate for OntoSpecies (data/ontologies/ontospecies-subgraph.ttl)")

    args = parser.parse_args()

    requested: List[Tuple[str, List[Path]]] = []
    if args.ontosynthesis:
        requested.append(("ontosynthesis", [Path("data/ontologies/ontosynthesis.ttl")]))
    if args.ontomops:
        requested.append(("ontomops", [Path("data/ontologies/ontomops-subgraph.ttl")]))
    if args.ontospecies:
        requested.append(("ontospecies", [Path("data/ontologies/ontospecies-subgraph.ttl")]))

    if not requested:
        print("No ontology selected. Use --ontosynthesis, --ontomops, and/or --ontospecies.")
        sys.exit(1)

    output_dir = _default_output_dir()
    meta_cfg = _load_meta_task_config()
    print("🚀 Starting iterations generation", flush=True)
    print(f"Output base: {output_dir}", flush=True)
    print(f"Selected ontologies: {[name for name, _ in requested]}", flush=True)
    ok = True
    for name, ttl_list in requested:
        try:
            print(f"\n=== Ontology: {name} ===", flush=True)
            create_iterations_json(ttl_list, output_dir, meta_cfg=meta_cfg)
        except Exception as exc:
            LOGGER.exception("Failed to create iterations.json")
            print(f"Error: {exc}")
            ok = False

    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()


