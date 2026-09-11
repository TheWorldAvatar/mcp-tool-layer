"""Build compile context, then attach the occurrence-surface contract.

Calls the existing prompt-generation compiler. Does not change that package.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.extraction_prompt_generation.compile.artifact_compiler import (
    build_domain_generation_context,
)
from src.extraction_prompt_generation.compile.context import (
    AgenticGenerationContext,
    runtime_publish_contract,
)
from src.extraction_prompt_generation.compile.generation_contracts import (
    write_generation_contract_bundle,
)
from src.extraction_prompt_generation.config.domain_config import (
    load_domain_generation_config,
)
from src.kg_building_mcp_generation.surface.helpers import (
    collect_extension_bridge_class_iris,
)
from src.kg_building_mcp_generation.surface.infer import infer_occurrence_surface
from src.kg_building_mcp_generation.surface.judge import invoke_occurrence_judge


def _occurrence_model(domain_config_path: Path, repository_root: Path) -> str:
    domain = load_domain_generation_config(
        domain_config_path, repository_root=repository_root
    )
    return str(
        domain.models.get("operation_planning")
        or domain.models.get("script_generation")
        or "gpt-5"
    ).strip() or "gpt-5"


def _attach_upstream_bridge_iris(
    context: AgenticGenerationContext,
    *,
    domain_config_path: Path,
    repository_root: Path,
) -> None:
    """Copy the parent domain's declared bridge class onto an extension contract.

    Parent ``runtime.extensions[].bridge_class_iri`` is the human-declared hop
    target. The compiler uses it together with planned ``extension_focus``;
    it does not special-case ontology names.
    """
    domain = load_domain_generation_config(
        domain_config_path, repository_root=repository_root
    )
    if domain.role != "extension":
        return
    binding = domain.runtime.get("binding") or {}
    upstream = str(binding.get("upstream_ontology") or "").strip()
    if not upstream:
        return
    parent_path = Path(repository_root) / "configs" / "domains" / f"{upstream}.json"
    if not parent_path.is_file():
        return
    parent = load_domain_generation_config(
        parent_path, repository_root=repository_root
    )
    declared: list[str] = []
    for item in parent.runtime.get("extensions") or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("name") or "").strip() != domain.ontology_name:
            continue
        iri = str(item.get("bridge_class_iri") or "").strip()
        if iri.startswith(("http://", "https://", "urn:")):
            declared.append(iri)
    existing = collect_extension_bridge_class_iris(contract=context.contract)
    merged = list(dict.fromkeys([*existing, *declared]))
    context.contract["extension_bridge_class_iris"] = merged


def persist_occurrence_context(context: AgenticGenerationContext) -> None:
    """Rewrite compile artifacts after occurrence units are attached."""
    Path(context.contract_path).write_text(
        json.dumps(context.contract, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    write_generation_contract_bundle(context.contract, Path(context.contract_path))
    Path(context.config_provenance_path).write_text(
        json.dumps(context.config_provenance, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    scripts_dir = Path(context.scripts_dir)
    scripts_dir.mkdir(parents=True, exist_ok=True)
    (scripts_dir / "_relationship_contract.json").write_text(
        json.dumps(
            runtime_publish_contract(context.contract),
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )


def build_occurrence_generation_context(
    *,
    domain_config_path: str | Path,
    output_root: str | Path,
    repository_root: str | Path,
    write_files: bool = True,
    selected_top_entity: dict[str, Any] | None = None,
    planner: Any = None,
    operation_planner: Any = None,
) -> AgenticGenerationContext:
    """Compile T-Box context, then infer the occurrence MCP surface."""
    config_path = Path(domain_config_path)
    context = build_domain_generation_context(
        domain_config_path=config_path,
        output_root=output_root,
        repository_root=repository_root,
        write_files=write_files,
        selected_top_entity=selected_top_entity,
    )
    _attach_upstream_bridge_iris(
        context,
        domain_config_path=config_path,
        repository_root=Path(repository_root),
    )
    ontology = context.ontology.name
    infer_occurrence_surface(
        context,
        planner=operation_planner or planner or invoke_occurrence_judge,
        model=_occurrence_model(config_path, Path(repository_root)),
        checkpoint_path=(
            Path(output_root)
            / "semantic_planning"
            / ontology
            / "occurrence_surface_decisions.json"
        ),
    )
    context.config_provenance["materialization_operation_planning"] = {
        "mode": "occurrence_surface",
        "candidate_source": "deterministic_tbox_occurrence_facets",
        "decision_source": "deterministic_tbox_structure",
        "primitive_membership": "deterministic_unique_ordered_membership",
        "instruction_source": "compiled_operational_instruction",
        "deterministic_parent_link": "unique_incoming_parent",
        "extension_adopted_focus": (
            "planned_extension_focus_and_parent_declared_bridge"
        ),
    }
    if write_files:
        persist_occurrence_context(context)
    return context
