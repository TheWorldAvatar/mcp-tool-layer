"""Domain-neutral configuration and stage contracts for semantic MCP loops."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence


CONFIG_SCHEMA_VERSION = "semantic-loop-config.v1"


def _resolve(root: Path, value: str) -> Path:
    path = Path(str(value or "").strip())
    if not str(path):
        raise ValueError("semantic loop config contains an empty path")
    return path if path.is_absolute() else root / path


@dataclass(frozen=True)
class UnitSystemPolicy:
    """Optional units vocabulary and its generic reasoner diagnostics."""

    id: str
    tbox_paths: tuple[Path, ...]
    reasoner_violation_keys: tuple[str, ...]
    repair_guidance: tuple[str, ...]


@dataclass(frozen=True)
class SemanticLoopConfig:
    """Repository-resolved configuration for one domain adapter."""

    domain_id: str
    ontology_name: str
    meta_task_config: Path
    tbox_paths: tuple[Path, ...]
    output_root: Path
    published_output_dir: str
    required_coverage: tuple[str, ...]
    unit_system: UnitSystemPolicy


def load_semantic_loop_config(path: Path, *, repository_root: Path) -> SemanticLoopConfig:
    """Load and strictly validate a semantic-loop adapter configuration."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"semantic loop config must be an object: {path}")
    if raw.get("schema_version") != CONFIG_SCHEMA_VERSION:
        raise ValueError(
            f"unsupported semantic loop config schema: {raw.get('schema_version')!r}"
        )
    units = raw.get("unit_system")
    if not isinstance(units, dict):
        raise ValueError("semantic loop config requires a unit_system object")
    tbox_paths = tuple(
        _resolve(repository_root, str(value)) for value in raw.get("tbox_paths") or []
    )
    if not tbox_paths:
        raise ValueError("semantic loop config requires at least one T-Box path")
    return SemanticLoopConfig(
        domain_id=str(raw.get("domain_id") or "").strip(),
        ontology_name=str(raw.get("ontology_name") or "").strip(),
        meta_task_config=_resolve(repository_root, str(raw.get("meta_task_config") or "")),
        tbox_paths=tbox_paths,
        output_root=_resolve(repository_root, str(raw.get("output_root") or "")),
        published_output_dir=str(raw.get("published_output_dir") or "").strip(),
        required_coverage=tuple(
            str(value).strip()
            for value in raw.get("required_coverage") or []
            if str(value).strip()
        ),
        unit_system=UnitSystemPolicy(
            id=str(units.get("id") or "none").strip(),
            tbox_paths=tuple(
                _resolve(repository_root, str(value))
                for value in units.get("tbox_paths") or []
            ),
            reasoner_violation_keys=tuple(
                str(value).strip()
                for value in units.get("reasoner_violation_keys") or []
                if str(value).strip()
            ),
            repair_guidance=tuple(
                str(value).strip()
                for value in units.get("repair_guidance") or []
                if str(value).strip()
            ),
        ),
    )


@dataclass(frozen=True)
class ReactBuildRequest:
    """Inputs accepted by the production-shaped semantic-loop core."""

    artifact_root: Path
    meta_task_config: Path
    document_text: str
    abox_path: Path
    runtime_root: Path
    doi: str


class ReactPipelineAdapter(Protocol):
    """Only production-shaped ReAct execution is part of the new core boundary."""

    def build_abox(self, request: ReactBuildRequest) -> Mapping[str, Any]:
        """Run extraction/KG stages and return an exported A-Box report."""


def run_react_stage(
    adapter: ReactPipelineAdapter,
    request: ReactBuildRequest,
) -> dict[str, Any]:
    """Run one adapter without exposing legacy generated orchestration shortcuts."""
    result = adapter.build_abox(request)
    if not isinstance(result, Mapping):
        raise TypeError("React pipeline adapter must return a mapping")
    return dict(result)
