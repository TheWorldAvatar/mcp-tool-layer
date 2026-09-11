"""Reject leftover semantic inventories in human domain JSON.

Domain config may only orchestrate: T-Box paths, workflow slots, MCP wiring,
and runtime binding. Class lists, SPARQL, and prompt bodies are compiled
elsewhere. See config/README.md.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Mapping


SEMANTIC_CONFIG_PATHS = (
    ("runtime", "ordered_member_contracts"),
    ("runtime", "required_link_bindings"),
)
ITERATION_SEMANTIC_KEYS = frozenset(
    {
        "classes",
        "description",
        "name",
        "object_properties",
        "responsibilities",
        "linked_materialization_classes",
        "required_materialization",
        "require_members_when_source_matches",
        "forbid_generic_ordered_member_types",
    }
)
ALLOWED_TOP_LEVEL_KEYS = frozenset(
    {
        "schema_version",
        "domain_id",
        "ontology_name",
        "execution_profile",
        "workflow_profile",
        "tbox",
        "reuse_policy",
        "models",
        "mcp_capabilities",
        "runtime",
        "agents",
        "derivation",
    }
)
ALLOWED_RUNTIME_KEYS = frozenset(
    {
        "output",
        "extensions",
        "workflow",
        "binding",
        "external_identity_bindings",
        "enrichment_target",
        "scenario_domain",
        "vision_required",
        "extra_steps",
        "derivation",
    }
)
ALLOWED_RUNTIME_DERIVATION_KEYS = frozenset({"agents"})
ALLOWED_EXTENSION_KEYS = frozenset(
    {
        "name",
        "description",
        "ttl_file",
        "complex_pipeline",
        "output",
        "mcp_set_name",
        "mcp_list",
        "agent_model",
        "bridge_class_iri",
        "enrichment_target",
    }
)
ALLOWED_BINDING_KEYS = frozenset(
    {"role", "upstream_ontology", "upstream_tbox"}
)
ALLOWED_WORKFLOW_KEYS = frozenset({"pipeline_iteration_number", "iterations"})
ALLOWED_ITERATION_KEYS = frozenset(
    {
        "model_config_key",
        "pre_extraction_model_key",
        "hint_representation",
        "inputs",
        "pre_extraction_validation",
        "use_agent",
        "enrichment",
        "max_attempts",
    }
)


def _drop_path(payload: dict[str, Any], path: tuple[str, ...]) -> None:
    current: Any = payload
    for key in path[:-1]:
        if not isinstance(current, dict):
            return
        current = current.get(key)
    if isinstance(current, dict):
        current.pop(path[-1], None)


def derive_orchestration_config(
    raw: Mapping[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    """Remove ontology-semantic decisions while preserving execution choices."""
    derived = copy.deepcopy(dict(raw))
    removed: list[str] = []
    for path in SEMANTIC_CONFIG_PATHS:
        current: Any = raw
        present = True
        for key in path:
            if not isinstance(current, Mapping) or key not in current:
                present = False
                break
            current = current[key]
        if present:
            _drop_path(derived, path)
            removed.append(".".join(path))

    iterations = (
        ((derived.get("runtime") or {}).get("workflow") or {}).get("iterations")
        or []
    )
    for index, iteration in enumerate(iterations):
        if not isinstance(iteration, dict):
            continue
        for key in sorted(ITERATION_SEMANTIC_KEYS):
            if key in iteration:
                iteration.pop(key, None)
                removed.append(f"runtime.workflow.iterations[{index}].{key}")
        if "extraction_validation" in iteration:
            iteration.pop("extraction_validation", None)
            removed.append(
                f"runtime.workflow.iterations[{index}].extraction_validation"
            )
    derived["schema_version"] = "domain-generation-config.v1"
    derived["derivation"] = {
        "schema_version": "domain-orchestration-boundary.v1",
        "semantic_authority": "tbox_bundle_only",
    }
    return derived, removed


def write_orchestration_config(
    *,
    source_path: str | Path,
    output_path: str | Path,
) -> dict[str, Any]:
    source = Path(source_path)
    output = Path(output_path)
    raw = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("source domain config must be a JSON object")
    derived, removed = derive_orchestration_config(raw)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(derived, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return {
        "schema_version": "orchestration-config-derivation.v1",
        "source": str(source),
        "output": str(output),
        "removed_semantic_paths": removed,
    }


def validate_tbox_only_orchestration_config(raw: Mapping[str, Any]) -> None:
    """Fail closed when a certified orchestration config regains semantic priors."""
    marker = raw.get("derivation") or {}
    if not isinstance(marker, Mapping):
        raise ValueError("derivation must be an object")
    if marker.get("semantic_authority") != "tbox_bundle_only":
        return
    _, removed = derive_orchestration_config(raw)
    if removed:
        raise ValueError(
            "T-Box-only orchestration config contains forbidden semantic priors: "
            + ", ".join(removed)
        )
    runtime = raw.get("runtime") or {}
    workflow = runtime.get("workflow") or {}
    unknown: list[str] = []

    def reject_unknown(
        value: Mapping[str, Any], allowed: frozenset[str], prefix: str
    ) -> None:
        unknown.extend(
            f"{prefix}.{key}" if prefix else str(key)
            for key in value
            if key not in allowed
        )

    reject_unknown(raw, ALLOWED_TOP_LEVEL_KEYS, "")
    if isinstance(runtime, Mapping):
        reject_unknown(runtime, ALLOWED_RUNTIME_KEYS, "runtime")
        binding = runtime.get("binding") or {}
        if isinstance(binding, Mapping):
            reject_unknown(binding, ALLOWED_BINDING_KEYS, "runtime.binding")
        enrichment = runtime.get("enrichment_target")
        if isinstance(enrichment, Mapping):
            from src.extraction_prompt_generation.compile.enrichment_sparql import (
                validate_enrichment_target_declaration,
            )

            validate_enrichment_target_declaration(
                enrichment, prefix="runtime.enrichment_target"
            )
        declared_derivation = runtime.get("derivation")
        if isinstance(declared_derivation, Mapping):
            reject_unknown(
                declared_derivation,
                ALLOWED_RUNTIME_DERIVATION_KEYS,
                "runtime.derivation",
            )
        for index, extension in enumerate(runtime.get("extensions") or []):
            if isinstance(extension, Mapping):
                reject_unknown(
                    extension,
                    ALLOWED_EXTENSION_KEYS,
                    f"runtime.extensions[{index}]",
                )
                if isinstance(extension.get("enrichment_target"), Mapping):
                    from src.extraction_prompt_generation.compile.enrichment_sparql import (
                        validate_enrichment_target_declaration,
                    )

                    validate_enrichment_target_declaration(
                        extension.get("enrichment_target"),
                        prefix=f"runtime.extensions[{index}].enrichment_target",
                    )
    if isinstance(workflow, Mapping):
        reject_unknown(workflow, ALLOWED_WORKFLOW_KEYS, "runtime.workflow")
        for index, iteration in enumerate(workflow.get("iterations") or []):
            if isinstance(iteration, Mapping):
                reject_unknown(
                    iteration,
                    ALLOWED_ITERATION_KEYS,
                    f"runtime.workflow.iterations[{index}]",
                )
    if unknown:
        raise ValueError(
            "T-Box-only orchestration config contains non-allowlisted fields: "
            + ", ".join(sorted(unknown))
        )
