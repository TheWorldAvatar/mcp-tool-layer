"""Derive a domain-neutral orchestration config from a legacy domain config."""

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
        "models",
        "mcp_capabilities",
        "reuse_policy",
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
    }
)
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
    {"role", "execution_channel", "upstream_ontology", "upstream_tbox"}
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
    runtime = derived.get("runtime") or {}
    binding = runtime.get("binding")
    if isinstance(binding, dict) and "upstream_scope" in binding:
        binding.pop("upstream_scope", None)
        removed.append("runtime.binding.upstream_scope")
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
            from src.agents.scripts_and_prompts_generation.enrichment_target_sparql import (
                validate_enrichment_target_declaration,
            )

            validate_enrichment_target_declaration(
                enrichment, prefix="runtime.enrichment_target"
            )
        for index, extension in enumerate(runtime.get("extensions") or []):
            if isinstance(extension, Mapping):
                reject_unknown(
                    extension,
                    ALLOWED_EXTENSION_KEYS,
                    f"runtime.extensions[{index}]",
                )
                if isinstance(extension.get("enrichment_target"), Mapping):
                    from src.agents.scripts_and_prompts_generation.enrichment_target_sparql import (
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


def _local_name(iri: str) -> str:
    text = str(iri or "").rstrip("/")
    return text.rsplit("#", 1)[-1] if "#" in text else text.rsplit("/", 1)[-1]


def build_candidate_reuse_policy(
    *,
    summary: Mapping[str, Any],
    representative_trial: Mapping[str, Any],
) -> dict[str, Any]:
    """Compile unanimous binary/scope decisions; leave free text for review."""
    if summary.get("passed_10_of_10_gate") is not True:
        raise ValueError("reuse 10/10 stability gate has not passed")
    response = representative_trial.get("parsed_response") or {}
    if not isinstance(response, Mapping):
        raise ValueError("representative trial has no parsed_response")
    classes: list[dict[str, Any]] = []
    for item in response.get("reusable_classes") or []:
        class_iri = str(item.get("class_iri") or "").strip()
        classes.append(
            {
                "class_iri": class_iri,
                "class_local": _local_name(class_iri),
                "reusable": True,
                "reuse_scope": str(item.get("reuse_scope") or "").strip(),
                "match_basis": str(item.get("match_basis") or "").strip(),
                "rationale": str(item.get("false_merge_risk") or "").strip(),
                "confidence": str(item.get("confidence") or "").strip(),
                "review": "pending_match_basis_review",
                "tbox_evidence": list(item.get("tbox_evidence") or []),
                "pipeline_evidence": list(item.get("pipeline_evidence") or []),
            }
        )
    for item in response.get("non_reusable_classes") or []:
        class_iri = str(item.get("class_iri") or "").strip()
        classes.append(
            {
                "class_iri": class_iri,
                "class_local": _local_name(class_iri),
                "reusable": False,
                "reuse_scope": "occurrence_local",
                "match_basis": "not applicable",
                "rationale": str(item.get("reason") or "").strip(),
                "confidence": str(item.get("confidence") or "").strip(),
                "review": "not_required_non_reusable",
                "tbox_evidence": list(item.get("tbox_evidence") or []),
                "pipeline_evidence": list(item.get("pipeline_evidence") or []),
            }
        )
    expected = {
        str(item.get("class_iri") or "")
        for item in summary.get("class_stability") or []
    }
    observed = {str(item["class_iri"]) for item in classes}
    if observed != expected:
        raise ValueError(
            "representative trial class coverage differs from stable inventory"
        )
    return {
        "schema_version": "binary-class-reuse-review.v0",
        "status": "pending_match_basis_review",
        "generated_candidate": True,
        "derivation": {
            "semantic_authority": "tbox_bundle_only",
            "stability_gate": "10_of_10_unanimous_binary_and_scope",
            "free_text_gate": "manual_match_basis_review",
        },
        "default_on_ambiguity": (
            "Fail closed: reject generic reuse until match_basis is manually accepted."
        ),
        "classes": sorted(classes, key=lambda item: str(item["class_iri"])),
    }


def build_fail_closed_reuse_policy(
    *, summary: Mapping[str, Any]
) -> dict[str, Any]:
    """Produce a safe runtime policy while reusable match bases await review."""
    if summary.get("requested_trials") != 10 or not summary.get("all_trials_valid"):
        raise ValueError("fail-closed policy requires 10 valid source trials")
    classes = []
    for item in summary.get("class_stability") or []:
        class_iri = str(item.get("class_iri") or "").strip()
        if not class_iri:
            raise ValueError("class stability entry is missing class_iri")
        if item.get("unanimous") and item.get("majority_decision") == "non_reusable":
            review = "stable_non_reusable"
            rationale = "Ten valid trials unanimously rejected generic reuse."
        elif item.get("unanimous"):
            review = "fail_closed_pending_match_basis_review"
            rationale = (
                "LLM reuse decision is stable, but free-text match_basis has not "
                "been manually accepted."
            )
        else:
            review = "fail_closed_unstable_decision"
            rationale = "Ten valid trials did not reach a unanimous reuse decision."
        classes.append(
            {
                "class_iri": class_iri,
                "class_local": _local_name(class_iri),
                "reusable": False,
                "reuse_scope": "occurrence_local",
                "match_basis": "not applicable while fail-closed",
                "rationale": rationale,
                "confidence": "high",
                "review": review,
            }
        )
    return {
        "schema_version": "binary-class-reuse-review.v0",
        "status": "approved_for_runtime",
        "generated_candidate": True,
        "derivation": {
            "semantic_authority": "tbox_bundle_only",
            "mode": "fail_closed_until_match_basis_review",
        },
        "default_on_ambiguity": "Generic reuse is disabled.",
        "classes": sorted(classes, key=lambda item: str(item["class_iri"])),
    }


def promote_reviewed_reuse_policy(
    *, source_path: str | Path, output_path: str | Path
) -> dict[str, Any]:
    """Promote a fully reviewed candidate to the runtime policy artifact."""
    source = Path(source_path)
    payload = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("reviewed reuse candidate must be a JSON object")
    if payload.get("status") != "approved_for_runtime":
        raise ValueError("reviewed reuse candidate is not approved_for_runtime")
    pending = [
        str(item.get("class_iri") or "")
        for item in payload.get("classes") or []
        if isinstance(item, dict)
        and item.get("reusable") is True
        and item.get("review") != "approved_match_basis"
    ]
    if pending:
        raise ValueError(
            "reusable classes still lack approved match_basis: " + ", ".join(pending)
        )
    from src.agents.scripts_and_prompts_generation.reuse_policy import (
        load_reuse_policy,
    )

    load_reuse_policy(source)
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return {
        "source": str(source),
        "output": str(output),
        "class_count": len(payload.get("classes") or []),
        "reusable_count": sum(
            item.get("reusable") is True
            for item in payload.get("classes") or []
            if isinstance(item, dict)
        ),
    }
