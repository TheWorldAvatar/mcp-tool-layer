"""Publish per-entity TTL after a successful KG agent run."""

from __future__ import annotations

from pathlib import Path

from src.extraction_runtime.domain_binding import RuntimeDomain
from src.extraction_runtime.names import entity_artifact_name
from src.extraction_runtime.publish.publish import publish_ttl


def output_dir_name(config: dict, ontology_name: str) -> str:
    domain = config.get("domain")
    if isinstance(domain, RuntimeDomain):
        return str((domain.runtime.get("output") or {}).get("dir") or f"{ontology_name}_output")
    return f"{ontology_name}_output"


def entity_ttl_path(
    doi_folder: str | Path,
    *,
    ontology_name: str,
    entity_label: str,
    config: dict,
) -> Path:
    domain = config.get("domain")
    pattern = "{entity_safe}.ttl"
    if isinstance(domain, RuntimeDomain):
        pattern = str(
            (domain.runtime.get("output") or {}).get("entity_ttl_pattern") or pattern
        )
    safe = entity_artifact_name(entity_label)
    name = pattern.replace("{entity_safe}", safe).replace("{entity_name}", safe)
    return Path(doi_folder) / output_dir_name(config, ontology_name) / name


def publish_entity_ttl(source: Path, destination: Path) -> Path:
    return publish_ttl(source, destination)
