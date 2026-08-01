from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

from src.agents.scripts_and_prompts_generation.agentic_generation_context import (
    AgenticGenerationContext,
    build_agentic_generation_context,
)
from src.agents.scripts_and_prompts_generation.domain_generation_config import (
    DomainGenerationConfig,
    load_domain_generation_config,
)
from src.agents.scripts_and_prompts_generation.domain_semantic_planner import (
    JsonPlanner,
    plan_domain_semantics,
)
from src.agents.scripts_and_prompts_generation.iteration_plan_compiler import (
    compile_iteration_plan,
)


def _json_digest(value: Any) -> str:
    payload = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _runtime_blueprint(
    config: DomainGenerationConfig, decisions: dict[str, Any]
) -> dict[str, Any]:
    """Combine LLM-owned semantics with config-owned pipeline wiring."""
    semantic_iterations = (
        decisions.get("iteration_decomposition") or {}
    ).get("iterations") or []
    runtime_slots = list((config.runtime.get("workflow") or {}).get("iterations") or [])
    if len(runtime_slots) != len(semantic_iterations):
        raise ValueError(
            "domain config workflow iteration slots must match the selected workflow profile"
        )

    default_mcp = config.mcp_capabilities.get("kg_building") or {}
    external_mcp = config.mcp_capabilities.get("external_enrichment") or {}
    compiled: list[dict[str, Any]] = []
    for index, (semantic, runtime) in enumerate(
        zip(semantic_iterations, runtime_slots, strict=True), start=2
    ):
        if not isinstance(semantic, dict) or not isinstance(runtime, dict):
            raise ValueError("semantic and runtime iteration entries must be objects")
        planned_number = int(semantic.get("iteration_number") or index)
        if planned_number != index:
            raise ValueError(
                f"iteration planner must number main iterations contiguously from 2; got {planned_number}"
            )
        iteration = {
            "iteration_number": index,
            "name": str(semantic.get("name") or f"iteration_{index}"),
            "description": str(semantic.get("description") or ""),
            "responsibilities": dict(semantic.get("responsibilities") or {}),
            "model_config_key": str(runtime.get("model_config_key") or f"iter{index}_hints"),
            "per_entity": True,
            "use_agent": bool(runtime.get("use_agent", False)),
            "inputs": dict(runtime.get("inputs") or {"source": "stitched_paper"}),
            "outputs": {
                "hints_file": f"mcp_run/iter{index}_hints_{{entity_safe}}.txt",
                "prompt_file": f"prompts/iter{index}_extraction/{{entity_safe}}.md",
                "response_file": f"responses/iter{index}_extraction/{{entity_safe}}.md",
                **dict(runtime.get("additional_outputs") or {}),
            },
            "mcp_set_name": str(
                default_mcp.get("set_name") or "run_created_mcp.json"
            ),
            "mcp_tools": list(default_mcp.get("tools") or ["llm_created_mcp"]),
        }
        if iteration["use_agent"] and external_mcp:
            iteration["extraction_mcp_set_name"] = str(
                external_mcp.get("set_name") or ""
            )
            iteration["extraction_mcp_tools"] = list(
                external_mcp.get("tools") or []
            )

        enrichment_focus = list(semantic.get("enrichment_focus") or [])
        enrichment_slots = list(runtime.get("enrichment") or [])
        if len(enrichment_focus) != len(enrichment_slots):
            raise ValueError(
                f"iteration {index} enrichment runtime slots do not match planner output"
            )
        if semantic.get("requires_pre_extraction"):
            iteration["has_pre_extraction"] = True
            iteration["pre_extraction_model_key"] = str(
                runtime.get("pre_extraction_model_key") or "advanced_model"
            )
            iteration["inputs"] = {
                "pre_extraction_source": "stitched_paper",
                "extraction_source": "pre_extracted_text",
            }
            iteration["outputs"].update(
                {
                    "pre_extraction_file": "pre_extraction/entity_text_{entity_safe}.txt",
                    "pre_extraction_prompt_file": f"prompts/iter{index}_pre_extraction/{{entity_safe}}.md",
                    "pre_extraction_response_file": f"responses/iter{index}_pre_extraction/{{entity_safe}}.md",
                }
            )
        if enrichment_focus:
            iteration["sub_iterations"] = []
            for sub_index, (focus, slot) in enumerate(
                zip(enrichment_focus, enrichment_slots, strict=True), start=1
            ):
                sub_number = float(f"{index}.{sub_index}")
                iteration["sub_iterations"].append(
                    {
                        "iteration_number": sub_number,
                        "name": str(focus.get("name") or f"enrichment_{sub_index}"),
                        "description": str(focus.get("description") or ""),
                        "enriches": index,
                        "model_config_key": str(
                            slot.get("model_config_key")
                            or f"iter{index}_{sub_index}_enrichment"
                        ),
                        "per_entity": True,
                        "use_agent": bool(slot.get("use_agent", False)),
                        "inputs": {
                            "base_hints": f"mcp_run/iter{index}_hints_{{entity_safe}}.txt",
                            "pre_extracted_text": "pre_extraction/entity_text_{entity_safe}.txt",
                        },
                        "outputs": {
                            **dict(slot.get("outputs") or {}),
                            "hints_file": f"mcp_run/iter{index}_hints_{{entity_safe}}.txt",
                            "prompt_file": f"prompts/iter{index}.{sub_index}_enrichment/{{entity_safe}}.md",
                            "response_file": f"responses/iter{index}.{sub_index}_enrichment/{{entity_safe}}.md",
                            "done_marker": f"mcp_run/iter{index}_{index}.{sub_index}_done_{{entity_safe}}.marker",
                        },
                    }
                )
        compiled.append(iteration)
    return {
        "ontology": config.ontology_name,
        "description": "",
        "iterations": compiled,
    }


def _legacy_adapter(
    config: DomainGenerationConfig, *, blueprint_path: Path | None
) -> dict[str, Any]:
    """Render the legacy shape required by current pipeline consumers."""
    output = config.runtime.get("output") or {}
    return {
        "ontologies": {
            "main": {
                "name": config.ontology_name,
                "description": config.domain_id,
                "ttl_file": str(config.primary_tbox),
                "complex_pipeline": config.workflow_profile == "complex",
                "runtime_policies": (
                    {
                        "iteration_plan": {
                            "iterations_blueprint_path": str(blueprint_path)
                        }
                    }
                    if blueprint_path is not None
                    else {}
                ),
                "output": {
                    "dir": str(output.get("dir") or "{ontology_name}_output"),
                    "top_ttl_name": str(output.get("top_ttl_name") or "top.ttl"),
                    "entity_ttl_pattern": str(
                        output.get("entity_ttl_pattern") or "{entity_safe}.ttl"
                    ),
                },
                "mcp_set_name": str(
                    (config.mcp_capabilities.get("kg_building") or {}).get(
                        "set_name"
                    )
                    or "run_created_mcp.json"
                ),
                "mcp_list": list(
                    (config.mcp_capabilities.get("kg_building") or {}).get(
                        "tools"
                    )
                    or ["llm_created_mcp"]
                ),
            },
            "extensions": [],
        }
    }


def build_domain_generation_context(
    *,
    domain_config_path: str | Path,
    output_root: str | Path,
    repository_root: str | Path,
    write_files: bool = True,
    planner: JsonPlanner | None = None,
) -> AgenticGenerationContext:
    """Build generation context from exactly T-Box bundle plus domain config."""
    config = load_domain_generation_config(
        domain_config_path, repository_root=repository_root
    )
    root = Path(output_root)
    derived = root / "derived_inputs" / config.ontology_name
    derived.mkdir(parents=True, exist_ok=True)
    adapter_path = derived / "meta_task_adapter.json"
    adapter_path.write_text(
        json.dumps(
            _legacy_adapter(config, blueprint_path=None),
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    base = build_agentic_generation_context(
        ontology_name=config.ontology_name,
        meta_task_config_path=adapter_path,
        output_root=root,
        write_files=False,
    )
    decisions = plan_domain_semantics(
        config=config,
        parsed=base.parsed,
        contract=base.contract,
        planner=planner,
    )
    base.contract["top_entity"] = dict(decisions["top_entity"])
    runtime_blueprint = _runtime_blueprint(config, decisions)
    plan = compile_iteration_plan(
        blueprint=runtime_blueprint,
        parsed=base.parsed,
        contract=base.contract,
        ontology_name=config.ontology_name,
        blueprint_provenance={
            "source": "gpt-5_tbox_semantic_decomposition",
            "model": "gpt-5",
            "sha256": _json_digest(decisions["iteration_decomposition"]),
        },
    )
    blueprint_path = derived / "iteration_blueprint.json"
    blueprint_path.write_text(
        json.dumps(runtime_blueprint, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    context = build_agentic_generation_context(
        ontology_name=config.ontology_name,
        meta_task_config_path=adapter_path,
        output_root=root,
        write_files=write_files,
    )
    context.contract["top_entity"] = dict(decisions["top_entity"])
    context = replace(
        context,
        contract=context.contract,
        iteration_blueprint=plan,
        config_provenance={
            **context.config_provenance,
            "domain_config": {
                "source": "manual_domain_runtime_input",
                "path": str(config.path),
                "sha256": hashlib.sha256(config.path.read_bytes()).hexdigest(),
            },
            "semantic_decisions": {
                "source": "gpt-5_active_tbox",
                "sha256": _json_digest(decisions),
            },
            "boundary": {
                "manual_inputs": ["tbox_bundle", "domain_config"],
                "top_entity": "gpt-5_active_tbox",
                "iteration_decomposition": "gpt-5_active_tbox",
                "runtime_wiring": "domain_config",
            },
        },
    )
    manifest = {
        "schema_version": "domain-artifact-manifest.v1",
        "domain_config_sha256": hashlib.sha256(
            config.path.read_bytes()
        ).hexdigest(),
        "tbox_sha256": {
            str(path): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in (config.primary_tbox, *config.supporting_tboxes)
        },
        "semantic_decisions": decisions,
        "compiled_iteration_plan_sha256": _json_digest(plan),
        "planning_model": "gpt-5",
    }
    if write_files:
        structure_dir = Path(context.ontology_structure_dir)
        structure_dir.mkdir(parents=True, exist_ok=True)
        Path(context.contract_path).write_text(
            json.dumps(context.contract, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        Path(context.config_provenance_path).write_text(
            json.dumps(context.config_provenance, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        (structure_dir / "domain_artifact_manifest.json").write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    return context
