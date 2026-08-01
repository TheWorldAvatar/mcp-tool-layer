from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


WORKFLOW_PROFILES: dict[str, dict[str, Any]] = {
    "simple": {
        "main_iterations": 1,
        "enrichment_passes": 0,
        "pre_extraction_iterations": 0,
    },
    "complex": {
        "main_iterations": 3,
        "enrichment_passes": 2,
        "pre_extraction_iterations": 1,
    },
}

PLANNING_MODEL = "gpt-5"


@dataclass(frozen=True)
class DomainGenerationConfig:
    path: Path
    schema_version: str
    domain_id: str
    ontology_name: str
    workflow_profile: str
    primary_tbox: Path
    supporting_tboxes: tuple[Path, ...]
    models: dict[str, str]
    mcp_capabilities: dict[str, dict[str, Any]]
    runtime: dict[str, Any]
    agents: dict[str, Any]

    @property
    def profile(self) -> dict[str, Any]:
        return dict(WORKFLOW_PROFILES[self.workflow_profile])


def _resolve(repository_root: Path, value: str) -> Path:
    path = Path(str(value or "").strip())
    return path if path.is_absolute() else (repository_root / path).resolve()


def load_domain_generation_config(
    path: str | Path, *, repository_root: str | Path
) -> DomainGenerationConfig:
    """Load the only non-T-Box, manually maintained domain input."""
    config_path = Path(path).resolve()
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("domain config must be a JSON object")
    if raw.get("schema_version") != "domain-generation-config.v1":
        raise ValueError("unsupported domain config schema_version")

    domain_id = str(raw.get("domain_id") or "").strip()
    ontology_name = str(raw.get("ontology_name") or domain_id).strip()
    workflow_profile = str(raw.get("workflow_profile") or "").strip()
    if not domain_id or not ontology_name:
        raise ValueError("domain_id and ontology_name are required")
    if workflow_profile not in WORKFLOW_PROFILES:
        raise ValueError(
            f"workflow_profile must be one of {sorted(WORKFLOW_PROFILES)}"
        )

    tbox = raw.get("tbox") or {}
    root = Path(repository_root).resolve()
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

    models = {
        str(key): str(value).strip()
        for key, value in (raw.get("models") or {}).items()
        if str(key).strip() and str(value).strip()
    }
    for planner_key in ("top_entity_planning", "iteration_planning"):
        configured = models.get(planner_key, PLANNING_MODEL)
        if configured != PLANNING_MODEL:
            raise ValueError(f"{planner_key} must use {PLANNING_MODEL}")
        models[planner_key] = PLANNING_MODEL

    mcp_capabilities = raw.get("mcp_capabilities") or {}
    runtime = raw.get("runtime") or {}
    agents = raw.get("agents") or {}
    if not all(
        isinstance(value, dict)
        for value in (mcp_capabilities, runtime, agents)
    ):
        raise ValueError("mcp_capabilities, runtime, and agents must be objects")

    forbidden = {
        "classes",
        "properties",
        "responsibilities",
        "top_entity",
        "required_links",
        "prompt_field_allowlist",
        "prompt_profiles",
        "shell_validation",
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
        schema_version=str(raw["schema_version"]),
        domain_id=domain_id,
        ontology_name=ontology_name,
        workflow_profile=workflow_profile,
        primary_tbox=primary,
        supporting_tboxes=supporting,
        models=models,
        mcp_capabilities=dict(mcp_capabilities),
        runtime=dict(runtime),
        agents=dict(agents),
    )
