"""Bind a human domain config to runtime topology. Never reads meta_task."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from models.locations import repository_root, resolve_under_repository
from src.extraction_prompt_generation.config.domain_config import (
    DomainGenerationConfig,
    load_domain_generation_config,
)
from src.extraction_runtime.artifact_root import load_generation_contract

COMPLEX_MAIN_STEPS = [
    "pdf_conversion",
    "tbox_slim",
    "top_entity_extraction",
    "top_entity_kg_building",
    "main_ontology_extractions",
    "main_kg_building",
    "extensions_extractions",
    "extensions_kg_building",
]

SIMPLE_MAIN_STEPS = [
    "pdf_conversion",
    "top_entity_extraction",
    "top_entity_kg_building",
    "main_ontology_extractions",
    "main_kg_building",
]

EXTENSION_STEPS = [
    "extensions_extractions",
    "extensions_kg_building",
]


@dataclass(frozen=True)
class RuntimeDomain:
    config: DomainGenerationConfig
    scenario_domain: str
    default_steps: list[str]
    vision_required: bool

    @property
    def ontology_name(self) -> str:
        return self.config.ontology_name

    @property
    def domain_id(self) -> str:
        return self.config.domain_id

    @property
    def execution_profile(self) -> str:
        return self.config.execution_profile

    @property
    def workflow_profile(self) -> str:
        return self.config.workflow_profile

    @property
    def agents(self) -> dict[str, Any]:
        return dict(self.config.agents)

    @property
    def is_extension(self) -> bool:
        return self.config.role == "extension" or self.execution_profile == "simple_extension"

    @property
    def pipeline_main_ontology_name(self) -> str:
        """Ontology that owns the unscoped `memory/` directory for this run."""
        return pipeline_main_ontology_name(self)

    @property
    def mcp_capabilities(self) -> dict[str, Any]:
        return dict(self.config.mcp_capabilities)

    @property
    def runtime(self) -> dict[str, Any]:
        return dict(self.config.runtime)

    @property
    def extensions(self) -> list[dict[str, Any]]:
        raw = self.runtime.get("extensions") or []
        return [item for item in raw if isinstance(item, dict)]

    def mcp_capability(self, name: str) -> tuple[str, list[str]]:
        block = self.mcp_capabilities.get(name) or {}
        set_name = str(block.get("set_name") or "").strip()
        tools = [str(item) for item in (block.get("tools") or []) if str(item).strip()]
        return set_name, tools

    def allows_external_enrichment(self) -> bool:
        if self.agents.get("external_enrichment") is False:
            return False
        set_name, tools = self.mcp_capability("external_enrichment")
        return bool(set_name and tools)


def resolve_domain_config_path(ontology: str, explicit: str | Path | None = None) -> Path:
    if explicit:
        return resolve_under_repository(explicit)
    return resolve_under_repository(Path("configs") / "domains" / f"{ontology}.json")


def extra_steps_from_runtime(runtime: dict[str, Any] | None) -> list[str]:
    return [
        str(item).strip()
        for item in ((runtime or {}).get("extra_steps") or [])
        if str(item).strip()
    ]


def _profile_steps(config: DomainGenerationConfig) -> list[str]:
    if config.execution_profile == "simple_main":
        return list(SIMPLE_MAIN_STEPS)
    if config.role == "extension" or config.execution_profile == "simple_extension":
        return list(EXTENSION_STEPS)
    return list(COMPLEX_MAIN_STEPS)


def load_runtime_domain(
    ontology: str,
    *,
    domain_config: str | Path | None = None,
    repository: Path | None = None,
) -> RuntimeDomain:
    root = repository or repository_root()
    path = resolve_domain_config_path(ontology, domain_config)
    config = load_domain_generation_config(path, repository_root=root)
    runtime = config.runtime
    steps = _profile_steps(config)
    for step in extra_steps_from_runtime(runtime):
        if step not in steps:
            steps.append(step)
    return RuntimeDomain(
        config=config,
        scenario_domain=str(runtime.get("scenario_domain") or config.domain_id).strip(),
        default_steps=steps,
        vision_required=bool(runtime.get("vision_required", False)),
    )


def selected_top_class(ontology_name: str, *, upstream_ontology: str = "") -> dict[str, str]:
    """Read the generated top class. Extensions inherit the parent contract."""
    names = [name for name in (upstream_ontology, ontology_name) if str(name).strip()]
    last_error = ""
    for name in names:
        contract = load_generation_contract(name)
        top = dict(contract.get("top_entity") or {})
        class_local = str(top.get("class_local") or "").strip()
        class_iri = str(top.get("class_iri") or "").strip()
        if class_local or class_iri:
            return {
                "class_local": class_local,
                "class_iri": class_iri,
                "comment": str(top.get("comment") or ""),
                "source_ontology": name,
            }
        last_error = f"generation contract for {name} has no top_entity class"
    raise FileNotFoundError(
        last_error
        or "generated top-entity contract is missing; run prompt generation first"
    )


def runtime_domain_from_config(config: dict[str, Any] | None) -> RuntimeDomain | None:
    domain = (config or {}).get("domain")
    return domain if isinstance(domain, RuntimeDomain) else None


def resolve_upstream_ontology(
    *,
    extension: dict[str, Any] | None = None,
    domain: RuntimeDomain | None = None,
) -> str:
    named = str((extension or {}).get("upstream_ontology") or "").strip()
    if named:
        return named
    if domain is None:
        return ""
    bound = str((domain.runtime.get("binding") or {}).get("upstream_ontology") or "").strip()
    if bound:
        return bound
    if not domain.is_extension:
        return domain.ontology_name
    return ""


def pipeline_main_ontology_name(domain: RuntimeDomain) -> str:
    """Return the pipeline-owned main ontology used for scoped memory paths.

    Extension MCP packages write `memory_<package>/` only when this name differs
    from the package ontology. For an extension domain that is
    `runtime.binding.upstream_ontology`; otherwise the domain's own name.
    """
    if domain.is_extension:
        return resolve_upstream_ontology(domain=domain) or domain.ontology_name
    return domain.ontology_name


def resolve_enrichment_target(
    *,
    extension: dict[str, Any] | None = None,
    domain: RuntimeDomain | None = None,
) -> dict[str, Any]:
    declared = (extension or {}).get("enrichment_target")
    if isinstance(declared, dict) and declared:
        return dict(declared)
    if domain is not None and domain.is_extension:
        return dict(domain.runtime.get("enrichment_target") or {})
    return {}


def resolve_derivation_jobs(config: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Collect post-publish derivation jobs from this domain and its extensions."""
    domain = runtime_domain_from_config(config)
    if domain is None:
        return []
    jobs: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(candidate: RuntimeDomain) -> None:
        agents = [
            str(item).strip()
            for item in ((candidate.runtime.get("derivation") or {}).get("agents") or [])
            if str(item).strip()
        ]
        if not agents or candidate.ontology_name in seen:
            return
        output = candidate.runtime.get("output") or {}
        seen.add(candidate.ontology_name)
        jobs.append(
            {
                "ontology_name": candidate.ontology_name,
                "output_dir": str(output.get("dir") or f"{candidate.ontology_name}_output"),
                "entity_ttl_pattern": str(output.get("entity_ttl_pattern") or "*.ttl"),
                "agents": agents,
            }
        )

    add(domain)
    if not domain.is_extension:
        for item in domain.extensions:
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            try:
                add(load_runtime_domain(name))
            except (FileNotFoundError, OSError, ValueError):
                continue
    return jobs
