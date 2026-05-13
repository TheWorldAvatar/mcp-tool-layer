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
import re
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
    """Infer ontology short name from TTL content without hardcoded domain mappings."""
    ignored_prefixes = {
        "rdf", "rdfs", "owl", "xsd", "xml", "sh", "skos", "prov", "foaf", "dc", "dcterms"
    }
    try:
        prefix_matches = re.findall(r"@prefix\s+([A-Za-z][\w-]*)\s*:\s*<([^>]+)>", ttl_text)
        for prefix, iri in prefix_matches:
            prefix_l = str(prefix).strip().lower()
            if prefix_l and prefix_l not in ignored_prefixes:
                return prefix_l
            kg_match = re.search(r"/kg/([^/\s>#]+)/", str(iri), flags=re.IGNORECASE)
            if kg_match:
                candidate = kg_match.group(1).strip().lower()
                if candidate and candidate not in ignored_prefixes:
                    return candidate
        lowered = ttl_text.lower()
        kg_match = re.search(r"/kg/([^/\s>#]+)/", lowered)
        if kg_match:
            candidate = kg_match.group(1).strip().lower()
            if candidate and candidate not in ignored_prefixes:
                return candidate
    except Exception:
        pass
    return default


def _infer_ontology_name(ttl_paths: List[Path], ttl_text: str, meta_cfg: Optional[Dict[str, Any]] = None) -> str:
    """Prefer config-based ontology identity; fall back to generic TTL inference."""
    try:
        resolved_inputs = {str(Path(p).resolve()) for p in ttl_paths if p}
        ont_cfg = (meta_cfg or {}).get("ontologies", {}) or {}
        candidates: List[Dict[str, Any]] = []
        main = ont_cfg.get("main")
        if isinstance(main, dict):
            candidates.append(main)
        for ext in ont_cfg.get("extensions", []) or []:
            if isinstance(ext, dict):
                candidates.append(ext)
        for cfg in candidates:
            ttl_file = cfg.get("ttl_file")
            name = str(cfg.get("name") or "").strip()
            if not ttl_file or not name:
                continue
            try:
                resolved_ttl = str(Path(ttl_file).resolve())
            except Exception:
                resolved_ttl = str(ttl_file)
            if resolved_ttl in resolved_inputs:
                return name
    except Exception:
        pass
    return _infer_ontology_name_from_ttl(ttl_text)


def _compose_prompt(ttl_bundle_text: str, extra_constraints: str = "") -> str:
    PROMPT_HEADER = """Produce ONE JSON object (iterations.json) that configures a multi-iteration extraction and KG-building pipeline aligned with the provided T-Box schema. Keep the plan domain-agnostic and non-prescriptive about environment details.

    Strict output rules:
    - Output MUST be valid JSON only (no markdown, no comments).
    - Include top-level keys: 'ontology' (lowercase short name), 'description', 'iterations'.
    - 'iterations' is an array of objects with pragmatic fields commonly used in such configs (e.g., iteration_number, name, description,
    optional pre-extraction flags/paths, extraction/kg prompts, model_config_key, per_entity, use_agent, optional MCP tool settings,
    inputs/outputs objects, and optional sub_iterations that enrich a parent iteration via an 'enriches' field).
    - Use generic placeholders for any paths or file names and allow tokens like '{entity_safe}'. Details can be refined by scripts later..
    - Do NOT include dataset-specific details.
    - Prefer FEW, BROAD iterations over many narrow ones: for small-to-medium T-Boxes, aim for about 2–4 main iterations (iteration_number 2..N) that each cover a coherent subgraph (e.g. demographics + timeline, then procedures + approach, then diagnosis + outcomes). Avoid emitting 6+ peer iterations unless the T-Box is truly huge and deeply modular.
    - It is recommended to do multiple iterations for complex ontologies and single iteration for simple ontologies.
    - For complex part of certain ontologies, prefer a small number of sub-iterations to enrich a parent iteration instead of inventing many separate top-level iterations.
    - CRITICAL CONSTRAINT: ONLY ONE iteration can have the complex pre-extraction mechanism (has_pre_extraction: true, pre_extraction_prompt, pre_extraction_model_key).
    Choose the most complex iteration (typically the one extracting detailed sub-components or steps) to have pre-extraction.
    All other iterations should use simple direct extraction from the full paper content without pre-extraction.
    - IMPORTANT: For pre_extraction_prompt, always use a file path format like "ai_generated_contents/prompts/{ontology}/PRE_EXTRACTION_ITER_{N}.md",
    NOT a description. The actual prompt content will be generated later by a separate script.

    Return ONLY the JSON.

    """
    tail = "\n    Additional constraints from project configuration (may be empty):\n    " + (
        extra_constraints.strip() or "(none)"
    )
    body = ttl_bundle_text
    return PROMPT_HEADER + tail + "\n\n    T-Box :\n    " + body


def _generate_with_llm(ttl_bundle_text: str, extra_constraints: str = "") -> dict:
    """Generate iterations.json purely via LLMCreator using a domain-generic prompt."""
    prompt = _compose_prompt(ttl_bundle_text, extra_constraints=extra_constraints)
    print("🧠 Invoking LLM to create iterations.json ...", flush=True)
    model_name = (
        os.environ.get("ITERATION_CREATION_MODEL")
        or "gpt-5.2"
    )
    llm = LLMCreator(
        model=model_name,
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


def _iteration_plan_for_main(meta_cfg: Dict[str, Any], ontology_name: str) -> Dict[str, Any]:
    """Return runtime_policies.iteration_plan for the main ontology when names match."""
    try:
        main = meta_cfg.get("ontologies", {}).get("main", {})
        if not isinstance(main, dict) or main.get("name") != ontology_name:
            return {}
        pol = main.get("runtime_policies") or {}
        if not isinstance(pol, dict):
            return {}
        ip = pol.get("iteration_plan")
        return ip if isinstance(ip, dict) else {}
    except Exception:
        return {}


def _load_iteration_blueprint(blueprint_path: str) -> List[Dict[str, Any]]:
    """Load a checked-in iteration blueprint from JSON config."""
    try:
        p = Path(str(blueprint_path or "").strip())
        if not p.exists():
            return []
        data = json.loads(p.read_text(encoding="utf-8"))
        iterations = data.get("iterations")
        if isinstance(iterations, list):
            return [it for it in iterations if isinstance(it, dict)]
    except Exception:
        pass
    return []


def _cap_and_renumber_iterations(
    iterations: List[Dict[str, Any]],
    max_count: int,
) -> List[Dict[str, Any]]:
    """
    Keep the first *max_count* iterations after sorting by iteration_number; renumber to 2..max_count+1.
    Drops sub_iterations on kept rows to avoid broken numbering after merge.
    """
    flat = [it for it in iterations if isinstance(it, dict)]
    if not flat or max_count <= 0 or len(flat) <= max_count:
        return flat

    def _sort_key(it: Dict[str, Any]) -> float:
        try:
            return float(it.get("iteration_number", 0))
        except Exception:
            return 0.0

    flat.sort(key=_sort_key)
    kept = flat[:max_count]
    for i, it in enumerate(kept):
        it["iteration_number"] = 2 + i
        if it.get("sub_iterations"):
            it.pop("sub_iterations", None)
    print(
        f"📉 Capped iterations: kept {len(kept)} (of {len(flat)}); "
        f"renumbered iteration_number to {[2 + i for i in range(len(kept))]}",
        flush=True,
    )
    return kept


def _role_info_for(meta_cfg: Dict[str, Any], ontology_name: str) -> Dict[str, Any]:
    """Return role and known settings for the ontology from meta config."""
    info: Dict[str, Any] = {
        "role": None,
        "complex_pipeline": None,
        "mcp_set_name": None,
        "mcp_tools": None,
        "agent_model": None,
        "description": None,
        "max_main_iterations": None,
        "output": None,
        "iteration_postprocess_overrides": None,
        "iteration_blueprint_path": None,
        "forbid_sub_iterations": False,
    }
    try:
        ont = meta_cfg.get("ontologies", {})
        main = ont.get("main", {})
        if isinstance(main, dict) and main.get("name") == ontology_name:
            info["role"] = "main"
            info["complex_pipeline"] = bool(main.get("complex_pipeline", False))
            info["mcp_set_name"] = main.get("mcp_set_name")
            info["mcp_tools"] = main.get("mcp_list")
            info["description"] = main.get("description")
            info["output"] = main.get("output")
            plan = _iteration_plan_for_main(meta_cfg, ontology_name)
            mm = plan.get("max_main_iterations")
            if isinstance(mm, int) and mm > 0:
                info["max_main_iterations"] = mm
            info["forbid_sub_iterations"] = bool(plan.get("forbid_sub_iterations", False))
            overrides = plan.get("postprocess_overrides")
            if isinstance(overrides, dict):
                info["iteration_postprocess_overrides"] = overrides
            blueprint_path = str(plan.get("iterations_blueprint_path") or "").strip()
            if blueprint_path:
                info["iteration_blueprint_path"] = blueprint_path
            return info
        for ext in ont.get("extensions", []) or []:
            if isinstance(ext, dict) and ext.get("name") == ontology_name:
                info["role"] = "extension"
                info["complex_pipeline"] = bool(ext.get("complex_pipeline", False))
                info["mcp_set_name"] = ext.get("mcp_set_name")
                info["mcp_tools"] = ext.get("mcp_list")
                info["agent_model"] = ext.get("agent_model")
                info["description"] = ext.get("description")
                info["output"] = ext.get("output")
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

    def _normalize_tool_list(value: Any) -> Optional[List[str]]:
        if isinstance(value, list):
            tools = [str(item).strip() for item in value if str(item).strip()]
            return tools or None
        return None

    def _lookup_iteration_override(iter_num: int) -> Dict[str, Any]:
        if not isinstance(iteration_postprocess_overrides, dict):
            return {}
        for key in (str(iter_num), iter_num):
            value = iteration_postprocess_overrides.get(key)
            if isinstance(value, dict):
                return value
        return {}

    def _apply_input_override(inputs: Dict[str, Any], override: Dict[str, Any]) -> None:
        keep_only = override.get("keep_only")
        if isinstance(keep_only, list):
            allowed = {str(item).strip() for item in keep_only if str(item).strip()}
            for key in list(inputs.keys()):
                if key not in allowed:
                    inputs.pop(key, None)
        set_values = override.get("set")
        if isinstance(set_values, dict):
            for key, value in set_values.items():
                key_str = str(key).strip()
                if not key_str:
                    continue
                inputs[key_str] = value

    def _apply_sub_iteration_overrides(sub_iters: List[Dict[str, Any]], override: Dict[str, Any]) -> None:
        sub_override = override.get("sub_iterations")
        if not isinstance(sub_override, dict):
            return
        ordered = sub_override.get("ordered_overrides")
        if not isinstance(ordered, list):
            return

        def _sort_key(si: Dict[str, Any]) -> float:
            try:
                return float(str(si.get("iteration_number", "9.9")).replace(" ", ""))
            except Exception:
                return 9.9

        sub_iters.sort(key=_sort_key)
        for idx, sub in enumerate(sub_iters):
            if not isinstance(sub, dict) or idx >= len(ordered):
                continue
            item = ordered[idx]
            if not isinstance(item, dict):
                continue
            if str(item.get("name", "")).strip():
                sub["name"] = item["name"]
            if str(item.get("model_config_key", "")).strip():
                sub["model_config_key"] = item["model_config_key"]
            if "use_agent" in item:
                sub["use_agent"] = bool(item.get("use_agent"))
            _ensure_use_agent_rule(sub)

    def _ensure_extraction_prompt(obj: Dict[str, Any]) -> None:
        n_str = _iter_num_str(obj.get("iteration_number"))
        safe_n = n_str.replace(".", "_")
        # Sub-iterations should use underscore in filename to match style (e.g., 3_1)
        use = safe_n
        expected = f"ai_generated_contents/prompts/{ontology}/EXTRACTION_ITER_{use}.md"
        current = str(obj.get("extraction_prompt", "") or "").strip()
        if (
            not current
            or not current.endswith(".md")
            or f"/prompts/{ontology}/" not in current.replace("\\", "/")
            or f"EXTRACTION_ITER_{use}.md" not in current
        ):
            obj["extraction_prompt"] = expected
        elif current != expected and current.replace("\\", "/").endswith(f"EXTRACTION_ITER_{use}.md"):
            obj["extraction_prompt"] = expected

    def _normalize_prompt_path(value: str, expected: str) -> str:
        current = str(value or "").strip().replace("\\", "/")
        return expected if current != expected else current

    def _ensure_pre_extraction_prompt(parent: Dict[str, Any], iter_num: int) -> None:
        expected = f"ai_generated_contents/prompts/{ontology}/PRE_EXTRACTION_ITER_{iter_num}.md"
        parent["pre_extraction_prompt"] = _normalize_prompt_path(parent.get("pre_extraction_prompt", ""), expected)

    def _ensure_kg_building_prompt(obj: Dict[str, Any]) -> None:
        n_str = _iter_num_str(obj.get("iteration_number"))
        if "." in n_str:
            # sub-iterations: skip kg_building_prompt
            return
        expected = f"ai_generated_contents/prompts/{ontology}/KG_BUILDING_ITER_{n_str}.md"
        current = str(obj.get("kg_building_prompt", "") or "").strip().replace("\\", "/")
        if (
            not current
            or f"/prompts/{ontology}/" not in current
            or f"KG_BUILDING_ITER_{n_str}.md" not in current
        ):
            obj["kg_building_prompt"] = expected
        else:
            obj["kg_building_prompt"] = expected

    def _ensure_pre_extraction(parent: Dict[str, Any], iter_num: int) -> None:
        parent["has_pre_extraction"] = True
        _ensure_pre_extraction_prompt(parent, iter_num)
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

    def _render_ontology_template(value: Any, ontology_name: str) -> str:
        return str(value or "").replace("{ontology_name}", ontology_name)

    def _ensure_extension_outputs(obj: Dict[str, Any], ontology_name: str, output_cfg: Optional[Dict[str, Any]]) -> None:
        """Derive extension output file paths from configuration."""
        outputs = obj.setdefault("outputs", {})
        if not isinstance(outputs, dict):
            outputs = {}
            obj["outputs"] = outputs

        outputs["extraction_file"] = f"mcp_run_{ontology_name}/extraction_{{entity_safe}}.txt"
        outputs["extension_prompt_file"] = f"prompts/{ontology_name}_kg_building/{{entity_safe}}.md"

        normalized_output_cfg = output_cfg if isinstance(output_cfg, dict) else {}
        output_dir = _render_ontology_template(
            normalized_output_cfg.get("dir", "{ontology_name}_output"),
            ontology_name,
        ).strip("/") or f"{ontology_name}_output"
        entity_pattern = _render_ontology_template(
            normalized_output_cfg.get("entity_ttl_pattern", "{ontology_name}_extension_{entity_safe}.ttl"),
            ontology_name,
        ).lstrip("/")
        outputs["output_ttl_dir"] = output_dir
        outputs["output_ttl"] = f"{output_dir}/{entity_pattern}"

        obj.setdefault("recursion_limit", 500)

        if "extension_prompt" not in obj:
            obj["extension_prompt"] = f"ai_generated_contents/prompts/{ontology_name}/EXTENSION.md"

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
        hints_file = str(outputs.get("hints_file", "") or "")
        if (
            hints_file.endswith(".json")
            or "{entity_safe}" not in hints_file
            or not hints_file.startswith(f"mcp_run/iter{iter_num}_hints_")
        ):
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

    def _ensure_default_source_if_no_pre_extraction(obj: Dict[str, Any]) -> None:
        if obj.get("has_pre_extraction"):
            return
        inputs = obj.setdefault("inputs", {})
        if isinstance(inputs, dict):
            inputs.setdefault("source", "stitched_paper")

    def _ensure_use_agent_rule(obj: Dict[str, Any]) -> None:
        """Ensure use_agent is always present without ontology-specific defaults."""
        if "use_agent" not in obj:
            obj["use_agent"] = False

    def _ensure_extraction_mcp(obj: Dict[str, Any]) -> None:
        if bool(obj.get("use_agent")):
            fallback_set = obj.get("mcp_set_name") or mcp_set_name
            fallback_tools = _normalize_tool_list(obj.get("mcp_tools")) or _normalize_tool_list(mcp_tools)
            if fallback_set and not obj.get("extraction_mcp_set_name"):
                obj["extraction_mcp_set_name"] = fallback_set
            if fallback_tools and not obj.get("extraction_mcp_tools"):
                obj["extraction_mcp_tools"] = list(fallback_tools)

    complex_pipeline = role_info.get("complex_pipeline")
    mcp_set_name = role_info.get("mcp_set_name")
    mcp_tools = role_info.get("mcp_tools")
    agent_model = role_info.get("agent_model")
    role = role_info.get("role")
    output_cfg = role_info.get("output")
    iteration_postprocess_overrides = role_info.get("iteration_postprocess_overrides")
    iteration_blueprint_path = role_info.get("iteration_blueprint_path")
    forbid_sub_iterations = bool(role_info.get("forbid_sub_iterations"))

    if (role == "extension") and (complex_pipeline is False):
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
                first["mcp_set_name"] = mcp_set_name
            normalized_role_tools = _normalize_tool_list(mcp_tools)
            if normalized_role_tools:
                first["mcp_tools"] = normalized_role_tools
            if agent_model:
                first["agent_model"] = agent_model
            # Ensure extraction_prompt (md) and outputs (txt/md)
            _ensure_extraction_prompt(first)
            _ensure_outputs_txt(first, _as_int(first.get("iteration_number", 1)), "_extraction")
            _ensure_kg_building_prompt(first)
            _ensure_default_source_if_no_pre_extraction(first)
            _ensure_extraction_mcp(first)
            # CRITICAL: Hardcode extension-specific output paths (not LLM-generated)
            _ensure_extension_outputs(first, ontology, output_cfg)
        data["iterations"] = [first]
        return data

    if (role == "main") and isinstance(iteration_blueprint_path, str) and iteration_blueprint_path.strip():
        blueprint_iterations = _load_iteration_blueprint(iteration_blueprint_path)
        if blueprint_iterations:
            iterations = blueprint_iterations
            data["iterations"] = iterations

    # Simple main ontology (non-ontosynthesis): force a minimal runtime iteration plan (ITER2 only).
    # ITER1 is handled by pipeline steps top_entity_extraction + top_entity_kg_building and should
    # not be included here (main_ontology_extractions/main_kg_building skip non-per-entity and skip iter1).
    if (role == "main") and (complex_pipeline is False) and (ontology != "ontosynthesis"):
        it2: Dict[str, Any] = {
            "iteration_number": 2,
            "name": "entity_details",
            "description": "Extract key per-entity fields/properties for the top entity and prepare hints for KG building.",
            "per_entity": True,
            "use_agent": False,
        }
        # Ensure extraction_prompt (md) and outputs (txt/md)
        _ensure_extraction_prompt(it2)
        _ensure_outputs_txt(it2, 2, "_extraction")
        _ensure_kg_building_prompt(it2)
        _ensure_default_source_if_no_pre_extraction(it2)
        _ensure_use_agent_rule(it2)
        _ensure_extraction_mcp(it2)
        if mcp_set_name:
            it2["mcp_set_name"] = mcp_set_name
        normalized_role_tools = _normalize_tool_list(mcp_tools)
        if normalized_role_tools:
            it2["mcp_tools"] = normalized_role_tools
        if agent_model:
            it2["agent_model"] = agent_model

        data["iterations"] = [it2]
        return data

    # Complex main ontologies: iter1 is handled by top_entity_extraction/top_entity_kg_building,
    # so downstream main_ontology_extractions/main_kg_building should only see per-entity
    # iterations starting from iter2. This applies to ontosynthesis and any other main ontology
    # marked as complex_pipeline=true in meta_task_config.
    if (role == "main") and (complex_pipeline is True):
        iterations = [it for it in iterations if _as_int(it.get("iteration_number", 0)) != 1]
        max_main = role_info.get("max_main_iterations")
        if isinstance(max_main, int) and max_main > 0 and len(iterations) > max_main:
            iterations = _cap_and_renumber_iterations(iterations, max_main)
        if forbid_sub_iterations:
            for it in iterations:
                if isinstance(it, dict):
                    it.pop("sub_iterations", None)
        data["iterations"] = iterations

    for it in iterations:
        if not isinstance(it, dict):
            continue
        if mcp_set_name:
            it["mcp_set_name"] = mcp_set_name
        normalized_role_tools = _normalize_tool_list(mcp_tools)
        if normalized_role_tools:
            it["mcp_tools"] = normalized_role_tools
        if agent_model:
            it["agent_model"] = agent_model
        # Ensure extraction prompt and outputs formatting
        _ensure_extraction_prompt(it)
        it_num = _as_int(it.get("iteration_number", 1))
        if (role == "main") and (complex_pipeline is True) and it_num >= 2:
            # Complex main ontologies are consumed by per-entity extraction / KG building.
            # If the LLM emits non-per-entity iterations, the pipeline will skip them entirely.
            it["per_entity"] = True
        # Enforce use_agent rule: Only ITER 2 should have use_agent=true
        _ensure_use_agent_rule(it)
        _ensure_outputs_txt(it, it_num, "_extraction")
        _ensure_kg_building_prompt(it)
        _ensure_default_source_if_no_pre_extraction(it)
        _ensure_extraction_mcp(it)
        if bool(it.get("has_pre_extraction")):
            _ensure_pre_extraction(it, it_num)
            inputs = it.setdefault("inputs", {})
            if isinstance(inputs, dict):
                inputs.pop("source", None)
                inputs["pre_extraction_source"] = "stitched_paper"
            # Use a known, configured model key for pre-extraction.
            if str(it.get("pre_extraction_model_key", "")).strip() in {"", "complex_model"}:
                it["pre_extraction_model_key"] = "advanced_model"
        if (role == "main") and (complex_pipeline is True):
            inputs = it.setdefault("inputs", {})
            if isinstance(inputs, dict) and not it.get("has_pre_extraction"):
                inputs["source"] = "stitched_paper"
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

        iter_override = _lookup_iteration_override(it_num)
        if iter_override:
            if str(iter_override.get("name", "")).strip():
                it["name"] = iter_override["name"]
            if str(iter_override.get("model_config_key", "")).strip():
                it["model_config_key"] = iter_override["model_config_key"]
            if str(iter_override.get("pre_extraction_model_key", "")).strip():
                it["pre_extraction_model_key"] = iter_override["pre_extraction_model_key"]
            if "use_agent" in iter_override:
                it["use_agent"] = bool(iter_override.get("use_agent"))
            inputs = it.setdefault("inputs", {})
            if isinstance(inputs, dict):
                input_override = iter_override.get("inputs")
                if isinstance(input_override, dict):
                    _apply_input_override(inputs, input_override)
            sub_iters = it.get("sub_iterations")
            if isinstance(sub_iters, list):
                _apply_sub_iteration_overrides(sub_iters, iter_override)
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
    ontology = _infer_ontology_name(ttl_paths, texts[0], meta_cfg=meta_cfg)
    print(f"🔎 Inferred ontology: {ontology}", flush=True)

    role_info = _role_info_for(meta_cfg or {}, ontology)
    plan = _iteration_plan_for_main(meta_cfg or {}, ontology)
    llm_extra = (plan.get("llm_prompt_hint") or "").strip()
    blueprint_path = str(role_info.get("iteration_blueprint_path") or "").strip()
    blueprint_iterations = _load_iteration_blueprint(blueprint_path) if blueprint_path else []
    if blueprint_iterations:
        print(f"📘 Using configured iteration blueprint: {blueprint_path}", flush=True)
        data = {
            "ontology": ontology,
            "description": role_info.get("description") or "",
            "iterations": blueprint_iterations,
        }
    else:
        data = _generate_with_llm(ttl_bundle_text, extra_constraints=llm_extra)
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


