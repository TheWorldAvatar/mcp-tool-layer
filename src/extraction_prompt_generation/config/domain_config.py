"""Human domain-config schema, workflow slot shapes, and the JSON loader.

`WORKFLOW_PROFILES` is the only source of iteration slot geometry. Domain
JSON may choose `simple`, `simple_semantic`, or `complex`; it must not list classes or SPARQL.
See config/README.md and docs/CONFIGS.md.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from models.locked_llm import LOCKED_GENERATION_MODEL


_SIMPLE_SLOT = {
    "id": "iter2",
    "kind": "main",
    "slot_kind": "simple_all",
    "iteration_number": 2,
    "requires_pre_extraction": False,
    "semantic_enrichment_slots": [],
}

WORKFLOW_PROFILES: dict[str, dict[str, Any]] = {
    "simple": {
        "slots": [
            {
                **_SIMPLE_SLOT,
                "hint_representation": "ref-entity-relations.v1",
            }
        ],
    },
    "simple_semantic": {
        "slots": [
            {
                **_SIMPLE_SLOT,
                "hint_representation": "semantic-text.v1",
            }
        ],
    },
    "complex": {
        "slots": [
            {
                "id": "iter2",
                "kind": "main",
                "slot_kind": "foundation",
                "iteration_number": 2,
                "requires_pre_extraction": False,
                "hint_representation": "semantic-text.v1",
                "semantic_enrichment_slots": [],
                "semantic_role": (
                    "foundation entities: materialize non-ordered inputs, outputs, "
                    "provenance/context, and stable entities that later ordered "
                    "operations must reference"
                ),
            },
            {
                "id": "iter3",
                "kind": "main",
                "slot_kind": "ordered",
                "iteration_number": 3,
                "requires_pre_extraction": True,
                "hint_representation": "semantic-text.v1",
                "semantic_enrichment_slots": [],
                "semantic_role": (
                    "ordered operations: materialize ordered-member concrete action "
                    "classes and operation-local assets/properties; reference foundation "
                    "inputs and outputs created by the preceding slot"
                ),
            },
            {
                "id": "iter4",
                "kind": "main",
                "slot_kind": "remainder",
                "iteration_number": 4,
                "requires_pre_extraction": False,
                "hint_representation": "semantic-text.v1",
                "semantic_enrichment_slots": [],
                "semantic_role": (
                    "remainder summary facts: materialize remaining top-level summary "
                    "facts that are neither foundation identities nor ordered operations"
                ),
            },
        ],
    },
}

DEFAULT_MODEL = LOCKED_GENERATION_MODEL
PLANNING_MODEL = DEFAULT_MODEL
EXECUTION_PROFILES = {"complex_main", "simple_main", "simple_extension"}
LOCKED_EXTENSION_WORKFLOW_PROFILE = "simple_semantic"
LOCKED_EXTENSION_HINT_REPRESENTATION = "semantic-text.v1"
GENERATION_MODEL_KEYS = (
    "top_entity_planning",
    "extension_focus_planning",
    "iteration_planning",
    "script_generation",
    "prompt_generation",
    "semantic_review",
    "repair",
    "reuse_judgment",
    "semantic_scoring",
)


def apply_generation_model_override(
    models: dict[str, str],
    override: str | None = None,
) -> dict[str, str]:
    """Force planning / authoring / repair onto the welded GPT-5 snapshot.

    CLI `--model`, `TWA_GENERATION_MODEL`, and the `override` argument are
    ignored. Runtime extraction / KG keys stay as declared in domain JSON.
    """
    del override
    updated = dict(models)
    for key in GENERATION_MODEL_KEYS:
        updated[key] = DEFAULT_MODEL
    return updated


@dataclass(frozen=True)
class DomainGenerationConfig:
    """Validated domain-generation-config.v1. No class or SPARQL inventories."""

    path: Path
    repository_root: Path
    schema_version: str
    domain_id: str
    ontology_name: str
    execution_profile: str
    workflow_profile: str
    primary_tbox: Path
    supporting_tboxes: tuple[Path, ...]
    reuse_policy_path: Path | None
    models: dict[str, str]
    mcp_capabilities: dict[str, dict[str, Any]]
    runtime: dict[str, Any]
    agents: dict[str, Any]

    @property
    def profile(self) -> dict[str, Any]:
        """Slot geometry for `simple`, `simple_semantic`, or `complex`."""
        return dict(WORKFLOW_PROFILES[self.workflow_profile])

    @property
    def role(self) -> str:
        """`main` or `extension` from `runtime.binding.role`."""
        binding = self.runtime.get("binding") or {}
        return str(binding.get("role") or "main").strip()


def require_locked_extension_prompt_method(
    execution_profile: str,
    workflow_profile: str,
) -> None:
    """Refuse JSON identity interchange for OntoMOPs / OntoSpecies.

    `simple_extension` is locked to `simple_semantic` (`semantic-text.v1` /
    `SEMANTIC_HINTS_V1`). Medical `simple_main` keeps `simple`.
    """
    if execution_profile != "simple_extension":
        return
    if workflow_profile != LOCKED_EXTENSION_WORKFLOW_PROFILE:
        raise ValueError(
            "execution_profile=simple_extension is locked to "
            f"workflow_profile={LOCKED_EXTENSION_WORKFLOW_PROFILE} "
            f"({LOCKED_EXTENSION_HINT_REPRESENTATION} / SEMANTIC_HINTS_V1); "
            "JSON ref-entity-relations.v1 (`simple`) is forbidden for extensions"
        )


def _resolve(repository_root: Path, value: str) -> Path:
    path = Path(str(value or "").strip())
    return path if path.is_absolute() else (repository_root / path).resolve()


def load_domain_generation_config(
    path: str | Path,
    *,
    repository_root: str | Path | None = None,
    generation_model: str | None = None,
) -> DomainGenerationConfig:
    """Load the only non-T-Box, manually maintained domain input."""
    from models.locations import discover_repository_root, resolve_under_repository

    raw_path = Path(path)
    config_path = (
        raw_path.resolve()
        if raw_path.is_absolute()
        else resolve_under_repository(raw_path)
    )
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("domain config must be a JSON object")
    from src.extraction_prompt_generation.config.derivation import (
        validate_tbox_only_orchestration_config,
    )

    validate_tbox_only_orchestration_config(raw)
    if raw.get("schema_version") != "domain-generation-config.v1":
        raise ValueError("unsupported domain config schema_version")

    domain_id = str(raw.get("domain_id") or "").strip()
    ontology_name = str(raw.get("ontology_name") or domain_id).strip()
    execution_profile = str(raw.get("execution_profile") or "").strip()
    workflow_profile = str(raw.get("workflow_profile") or "").strip()
    if not domain_id or not ontology_name:
        raise ValueError("domain_id and ontology_name are required")
    if workflow_profile not in WORKFLOW_PROFILES:
        raise ValueError(f"workflow_profile must be one of {sorted(WORKFLOW_PROFILES)}")
    if execution_profile not in EXECUTION_PROFILES:
        raise ValueError(
            "execution_profile must be one of " + str(sorted(EXECUTION_PROFILES))
        )
    require_locked_extension_prompt_method(execution_profile, workflow_profile)

    tbox = raw.get("tbox") or {}
    root = (
        Path(repository_root).resolve()
        if repository_root is not None
        else discover_repository_root()
    )
    primary = _resolve(root, str(tbox.get("primary") or ""))
    supporting = tuple(
        _resolve(root, str(value))
        for value in tbox.get("supporting") or []
        if str(value).strip()
    )
    if not primary.is_file():
        raise FileNotFoundError(f"primary T-Box not found: {primary}")
    missing_supporting = [str(item) for item in supporting if not item.is_file()]
    if missing_supporting:
        raise FileNotFoundError(
            "supporting T-Box files not found: " + ", ".join(missing_supporting)
        )
    # Optional debug override. Default generation does not require this pin.
    reuse_policy_config = raw.get("reuse_policy") or {}
    if not isinstance(reuse_policy_config, dict):
        raise ValueError("reuse_policy must be an object")
    reuse_policy_value = str(reuse_policy_config.get("path") or "").strip()
    reuse_policy_path = (
        _resolve(root, reuse_policy_value) if reuse_policy_value else None
    )
    if reuse_policy_path is not None and not reuse_policy_path.is_file():
        raise FileNotFoundError(f"reuse policy not found: {reuse_policy_path}")
    models = {
        str(key): str(value).strip()
        for key, value in (raw.get("models") or {}).items()
        if str(key).strip() and str(value).strip()
    }
    planner_keys = (
        ("top_entity_planning", "iteration_planning")
        if execution_profile in {"complex_main", "simple_main"}
        else ("extension_focus_planning", "iteration_planning")
    )
    for planner_key in planner_keys:
        models[planner_key] = models.get(planner_key) or DEFAULT_MODEL
    models["reuse_judgment"] = models.get("reuse_judgment") or DEFAULT_MODEL
    models.setdefault("prompt_generation", DEFAULT_MODEL)
    models = apply_generation_model_override(models, generation_model)

    mcp_capabilities = raw.get("mcp_capabilities") or {}
    runtime = raw.get("runtime") or {}
    agents = raw.get("agents") or {}
    if not all(
        isinstance(value, dict) for value in (mcp_capabilities, runtime, agents)
    ):
        raise ValueError("mcp_capabilities, runtime, and agents must be objects")
    binding = runtime.get("binding") or {}
    if not isinstance(binding, dict):
        raise ValueError("runtime.binding must be an object")
    from src.extraction_prompt_generation.tbox.runtime_contracts import (
        validate_external_identity_bindings,
    )

    validate_external_identity_bindings(runtime.get("external_identity_bindings"))
    extra_steps = runtime.get("extra_steps")
    if extra_steps is not None:
        if not isinstance(extra_steps, list) or not all(
            isinstance(item, str) and str(item).strip() for item in extra_steps
        ):
            raise ValueError("runtime.extra_steps must be an array of step names")
    if runtime.get("vision_required") is not None and not isinstance(
        runtime.get("vision_required"), bool
    ):
        raise ValueError("runtime.vision_required must be a boolean")
    if runtime.get("scenario_domain") is not None and not str(
        runtime.get("scenario_domain") or ""
    ).strip():
        raise ValueError("runtime.scenario_domain must be a non-empty string")
    declared_derivation = runtime.get("derivation")
    if declared_derivation is not None:
        if not isinstance(declared_derivation, dict):
            raise ValueError("runtime.derivation must be an object")
        unknown = sorted(
            str(key)
            for key in declared_derivation
            if str(key) not in {"agents"}
        )
        if unknown:
            raise ValueError(
                "runtime.derivation contains unknown fields: " + ", ".join(unknown)
            )
        derivation_agents = declared_derivation.get("agents") or []
        if not isinstance(derivation_agents, list) or not all(
            isinstance(item, str) and str(item).strip() for item in derivation_agents
        ):
            raise ValueError("runtime.derivation.agents must be an array of module paths")
    if runtime.get("enrichment_target") is not None:
        from src.extraction_prompt_generation.compile.enrichment_sparql import (
            validate_enrichment_target_declaration,
        )

        validate_enrichment_target_declaration(
            runtime.get("enrichment_target"),
            prefix="runtime.enrichment_target",
        )
        if execution_profile != "simple_extension":
            raise ValueError(
                "runtime.enrichment_target is only valid when "
                "execution_profile=simple_extension"
            )
    role = str(binding.get("role") or "main").strip()
    expected_role = "extension" if execution_profile == "simple_extension" else "main"
    if role != expected_role:
        raise ValueError(
            f"execution_profile={execution_profile!r} requires runtime.binding.role={expected_role!r}"
        )
    if role == "extension":
        if not str(binding.get("upstream_ontology") or "").strip():
            raise ValueError(
                "extension role requires runtime.binding.upstream_ontology"
            )
        if not str(binding.get("upstream_tbox") or "").strip():
            raise ValueError("extension role requires runtime.binding.upstream_tbox")

    profile_slots = list(WORKFLOW_PROFILES[workflow_profile]["slots"])
    runtime_slots = list((runtime.get("workflow") or {}).get("iterations") or [])
    if len(runtime_slots) != len(profile_slots):
        raise ValueError(
            "runtime.workflow.iterations must match the selected workflow profile"
        )
    for profile_slot, runtime_slot in zip(profile_slots, runtime_slots, strict=True):
        if not isinstance(runtime_slot, dict):
            raise ValueError("runtime.workflow.iterations entries must be objects")
        slot_id = str(profile_slot["id"])
        expected_hint_mode = str(
            profile_slot.get("hint_representation") or "ref-entity-relations.v1"
        )
        actual_hint_mode = str(
            runtime_slot.get("hint_representation") or expected_hint_mode
        )
        if actual_hint_mode != expected_hint_mode:
            raise ValueError(
                f"{slot_id} hint_representation must be {expected_hint_mode!r}"
            )
        declared_enrichment_slots = list(
            profile_slot.get("semantic_enrichment_slots") or []
        )
        runtime_enrichment = runtime_slot.get("enrichment") or []
        if not isinstance(runtime_enrichment, list):
            raise ValueError(f"{slot_id} runtime enrichment must be an array")
        if len(runtime_enrichment) != len(declared_enrichment_slots):
            raise ValueError(
                f"{slot_id} runtime enrichment does not match semantic profile slots"
            )
        if (
            str(profile_slot.get("slot_kind") or "") == "ordered"
            and actual_hint_mode == "semantic-text.v1"
            and not declared_enrichment_slots
            and not bool(
                (
                    (runtime_slot.get("pre_extraction_validation") or {}).get(
                        "closed_ledger"
                    )
                    or {}
                ).get("enabled")
            )
        ):
            raise ValueError(
                f"{slot_id} semantic ordered slot without enrichment requires "
                "pre_extraction_validation.closed_ledger.enabled=true"
            )

    forbidden = {
        "classes",
        "properties",
        "responsibilities",
        "top_entity",
        "required_links",
        "required_link_bindings",
        "ordered_member_contracts",
        "linked_materialization_classes",
        "extraction_validation",
        "required_executed_tool_groups",
        "prompt_field_allowlist",
        "prompt_profiles",
        "shell_validation",
        "execution_channel",
        "upstream_scope",
    }

    def walk(value: Any, path_parts: tuple[str, ...] = ()) -> None:
        if isinstance(value, dict):
            for key, nested in value.items():
                if str(key) in forbidden:
                    raise ValueError(
                        "domain config contains forbidden semantic field: "
                        + ".".join((*path_parts, str(key)))
                    )
                walk(nested, (*path_parts, str(key)))
        elif isinstance(value, list):
            for index, nested in enumerate(value):
                walk(nested, (*path_parts, str(index)))

    walk(raw)
    return DomainGenerationConfig(
        path=config_path,
        repository_root=root,
        schema_version=str(raw["schema_version"]),
        domain_id=domain_id,
        ontology_name=ontology_name,
        execution_profile=execution_profile,
        workflow_profile=workflow_profile,
        primary_tbox=primary,
        supporting_tboxes=supporting,
        reuse_policy_path=reuse_policy_path,
        models=models,
        mcp_capabilities=dict(mcp_capabilities),
        runtime=dict(runtime),
        agents=dict(agents),
    )
