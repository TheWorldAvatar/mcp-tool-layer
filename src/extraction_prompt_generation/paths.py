"""Resolve repository paths without depending on the process working directory.

The CLI may be launched from any folder. All paths are relative to the
clone root. See README.md in this package.
"""

from __future__ import annotations

import json
from pathlib import Path

from models.generated_layout import (
    generated_home,
    resolve_generation_output_root,
    resolve_generated_package_root,
)
from models.locations import (
    CONFIGS_DIR,
    ROOT_DIR,
    discover_repository_root,
    resolve_under_repository,
)


def repository_root() -> Path:
    """Clone root (the directory that contains `pyproject.toml` and `configs/`)."""
    return discover_repository_root()


def default_output_root() -> Path:
    """Untracked generation home: `<repo>/generated` (parent of `runs/`)."""
    return generated_home(repository_root())


def default_domain_config_dir() -> Path:
    """Human domain JSON directory: `configs/domains/`."""
    return Path(CONFIGS_DIR) / "domains"


def default_domain_config_path(ontology_name: str) -> Path:
    """Conventional config path for a CLI ontology name."""
    name = str(ontology_name or "").strip()
    if not name:
        raise ValueError("ontology name is required")
    return default_domain_config_dir() / f"{name}.json"


def available_ontologies() -> list[str]:
    """Ontology names that have a `configs/domains/<name>.json` file."""
    folder = default_domain_config_dir()
    if not folder.is_dir():
        return []
    return sorted(path.stem for path in folder.glob("*.json") if path.is_file())


def read_domain_ontology_name(domain_config_path: str | Path) -> str:
    raw = json.loads(Path(domain_config_path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"domain config must be a JSON object: {domain_config_path}")
    name = str(raw.get("ontology_name") or raw.get("domain_id") or "").strip()
    if not name:
        raise ValueError(
            f"domain config is missing ontology_name: {domain_config_path}"
        )
    return name


def resolve_domain_config_path(
    domain_config: str | Path | None = None,
    *,
    ontology_name: str = "",
) -> Path:
    """Resolve a domain config from an explicit path or the conventional location."""
    if domain_config:
        path = resolve_under_repository(domain_config)
        if not path.is_file():
            raise FileNotFoundError(f"domain config not found: {domain_config}")
        return path
    path = default_domain_config_path(ontology_name)
    if not path.is_file():
        known = ", ".join(available_ontologies()) or "(none found)"
        raise FileNotFoundError(
            f"no domain config for {ontology_name!r} at {path}. Known: {known}"
        )
    return path


def package_prompt_path(ontology_name: str, filename: str) -> str:
    """Return an output-root-relative prompt path for compiled iteration specs."""
    return f"prompts/{ontology_name}/{filename}"


__all__ = [
    "ROOT_DIR",
    "available_ontologies",
    "default_domain_config_dir",
    "default_domain_config_path",
    "default_output_root",
    "package_prompt_path",
    "read_domain_ontology_name",
    "repository_root",
    "resolve_domain_config_path",
    "resolve_generated_package_root",
    "resolve_generation_output_root",
]
