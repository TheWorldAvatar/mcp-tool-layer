"""Project compiled iterations into the runtime `iterations.json` shape.

Main ontologies keep compiled slot numbers. Extensions may renumber via
`runtime.workflow.pipeline_iteration_number` while still using the single
profile slot (`iter2`).

See pipeline/README.md.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.extraction_prompt_generation.compile.context import (
    AgenticGenerationContext,
)
from src.extraction_prompt_generation.generate.extraction_prompts.contracts.scope import (
    _canonical_iteration_filename_token,
)


def _iteration_plan(context: AgenticGenerationContext) -> dict[str, Any]:
    """Build the runtime iteration list, including MCP wiring and prompt paths."""
    if context.ontology.role == "extension":
        ontology = context.ontology.name
        meta_cfg = json.loads(
            Path(context.ontology.meta_task_config_path).read_text(encoding="utf-8")
        )
        extension_cfg = next(
            (
                item
                for item in (
                    (meta_cfg.get("ontologies", {}) or {}).get("extensions", []) or []
                )
                if str((item or {}).get("name") or "").strip() == ontology
            ),
            {},
        )
        mcp_tools = [
            str(tool).strip()
            for tool in (extension_cfg.get("mcp_list") or [])
            if str(tool).strip()
        ]
        mcp_set_name = str(extension_cfg.get("mcp_set_name") or "extension.json")
        from src.extraction_prompt_generation.paths import package_prompt_path
        domain_runtime = context.config_provenance.get("domain_config") or {}
        iter_num = int(domain_runtime.get("pipeline_iteration_number") or 1)
        # Simple extensions keep a single compiled semantic iteration (profile slot
        # iter2) while runtime may renumber via pipeline_iteration_number.
        compiled_iterations = [
            item
            for item in (context.iteration_blueprint.get("iterations") or [])
            if isinstance(item, dict)
        ]
        if len(compiled_iterations) != 1:
            raise ValueError(
                f"simple_extension {ontology} requires exactly one compiled semantic "
                f"iteration; got {len(compiled_iterations)}"
            )
        semantic_source = compiled_iterations[0]
        responsibilities = dict(semantic_source.get("responsibilities") or {})
        semantic_scope = dict(semantic_source.get("semantic_scope") or {})
        scope_classes = [
            item
            for item in (semantic_scope.get("classes") or [])
            if isinstance(item, dict) and str(item.get("local") or "").strip()
        ]
        if not scope_classes:
            raise ValueError(
                f"simple_extension {ontology} compiled iteration is missing a non-empty "
                "semantic_scope.classes; refusing to materialize hollow runtime iterations"
            )
        output_cfg = (
            extension_cfg.get("output")
            if isinstance(extension_cfg.get("output"), dict)
            else {}
        )
        output_dir = str(
            output_cfg.get("dir") or f"{ontology}_output"
        ).replace("{ontology_name}", ontology)
        entity_pattern = str(
            output_cfg.get("entity_ttl_pattern") or "{entity_safe}.ttl"
        ).replace("{ontology_name}", ontology)
        output_ttl = f"{output_dir.rstrip('/')}/{entity_pattern}"
        iteration = {
            "iteration_number": iter_num,
            "name": f"{ontology}_extension",
            "description": str(
                semantic_source.get("description")
                or f"T-Box-driven {ontology} extension extraction and KG building."
            ),
            "model_config_key": f"extension_{ontology}",
            "use_agent": False,
            "per_entity": True,
            "responsibilities": responsibilities,
            "semantic_scope": semantic_scope,
            "inputs": {
                "source": "stitched_paper",
                "tbox_path": context.ontology.ttl_file,
            },
            "outputs": {
                "hints_file": f"mcp_run/iter{iter_num}_hints_{{entity_safe}}.txt",
                "prompt_file": f"prompts/iter{iter_num}_extraction/{{entity_safe}}.md",
                "response_file": f"responses/iter{iter_num}_extraction/{{entity_safe}}.md",
                "extraction_file": f"mcp_run_{ontology}/extraction_{{entity_safe}}.txt",
                "extension_prompt_file": f"prompts/{ontology}_kg_building/{{entity_safe}}.md",
                "output_ttl_dir": output_dir,
                "output_ttl": output_ttl,
            },
            "mcp_set_name": mcp_set_name,
            "mcp_tools": mcp_tools,
            "agent_model": str(extension_cfg.get("agent_model") or "gpt-4o"),
            "extraction_prompt": package_prompt_path(
                ontology, f"EXTRACTION_ITER_{iter_num}.md"
            ),
            "recursion_limit": 500,
        }
        hint_representation = str(
            semantic_source.get("hint_representation") or ""
        ).strip()
        if hint_representation:
            iteration["hint_representation"] = hint_representation
        return {"iterations": [iteration]}

    iterations = list(context.iteration_blueprint.get("iterations") or [])

    main_cfg = {
        "mcp_set_name": "run_created_mcp.json",
        "mcp_tools": [f"{context.ontology.name}_mcp"],
    }
    materialized: list[dict[str, Any]] = []
    for raw in iterations:
        if not isinstance(raw, dict):
            continue
        iteration = dict(raw)
        iter_num = iteration.get("iteration_number")
        if not iter_num:
            continue
        from src.extraction_prompt_generation.paths import package_prompt_path

        token = _canonical_iteration_filename_token(iter_num)
        iteration["extraction_prompt"] = package_prompt_path(
            context.ontology.name, f"EXTRACTION_ITER_{token}.md"
        )
        iteration.setdefault("mcp_set_name", main_cfg["mcp_set_name"])
        iteration.setdefault("mcp_tools", main_cfg["mcp_tools"])
        if iteration.get("has_pre_extraction"):
            iteration["pre_extraction_prompt"] = package_prompt_path(
                context.ontology.name, f"PRE_EXTRACTION_ITER_{token}.md"
            )
        for sub_iteration in iteration.get("sub_iterations") or []:
            if not isinstance(sub_iteration, dict):
                continue
            sub_num = _canonical_iteration_filename_token(
                sub_iteration.get("iteration_number")
            )
            if sub_num:
                sub_iteration["extraction_prompt"] = package_prompt_path(
                    context.ontology.name, f"EXTRACTION_ITER_{sub_num}.md"
                )
        materialized.append(iteration)
    return {"iterations": materialized}
