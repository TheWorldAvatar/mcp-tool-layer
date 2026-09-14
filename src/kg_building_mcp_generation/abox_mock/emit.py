"""Emit a generated occurrence MCP package into a work directory."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from src.extraction_prompt_generation.compile.context import (
    AgenticGenerationContext,
    OntologySpec,
    runtime_publish_contract,
)
from src.kg_building_mcp_generation.emit.runtime import write_runtime_support
from src.kg_building_mcp_generation.emit.scripts import generate_deterministic_script_slice


def _context(
    *,
    ontology_name: str,
    parsed: Mapping[str, Any],
    contract: dict[str, Any],
    scripts_dir: Path,
    output_root: Path,
) -> AgenticGenerationContext:
    output_root.mkdir(parents=True, exist_ok=True)
    scripts_dir.mkdir(parents=True, exist_ok=True)
    dummy = output_root / "_unused"
    return AgenticGenerationContext(
        ontology=OntologySpec(
            name=ontology_name,
            ttl_file="",
            meta_task_config_path="",
            role="main",
        ),
        output_root=str(output_root),
        ontology_structure_dir=str(output_root),
        scripts_dir=str(scripts_dir),
        prompts_dir=str(output_root / "prompts"),
        parsed_summary_path=str(dummy.with_suffix(".parsed.json")),
        parsed_markdown_path=str(dummy.with_suffix(".md")),
        contract_path=str(output_root / "generation_contract.json"),
        integrity_profile_path=str(dummy.with_suffix(".integrity.json")),
        report_path=str(output_root / "report.json"),
        config_provenance_path=str(output_root / "config_provenance.json"),
        parsed=dict(parsed),
        contract=contract,
        integrity_profile={},
        pipeline_runtime_policies={},
        iteration_blueprint={},
        config_provenance={},
    )


def emit_occurrence_package(
    *,
    ontology_name: str,
    parsed: Mapping[str, Any],
    contract: dict[str, Any],
    scripts_dir: Path,
    output_root: Path,
) -> list[str]:
    """Write creation modules, occurrence operations, and flattened RDF runtime."""
    context = _context(
        ontology_name=ontology_name,
        parsed=parsed,
        contract=contract,
        scripts_dir=scripts_dir,
        output_root=output_root,
    )
    written = list(write_runtime_support(context))
    written.extend(generate_deterministic_script_slice(context))
    contract_path = scripts_dir / "_relationship_contract.json"
    payload = runtime_publish_contract(contract)
    publish = contract.get("ontology_publish_contract") or {}
    for key in ("reuse_policy", "top_entity", "ontology_name", "object_properties", "classes"):
        if key in publish and key not in payload:
            payload[key] = publish[key]
    if contract.get("reuse_policy") and "reuse_policy" not in payload:
        payload["reuse_policy"] = contract["reuse_policy"]
    if contract.get("top_entity") and "top_entity" not in payload:
        payload["top_entity"] = contract["top_entity"]
    payload.setdefault("ontology_name", ontology_name)
    contract_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    if str(contract_path) not in written:
        written.append(str(contract_path))
    return written
