"""Shared mutable state for one stage-artifact validation pass.

`StageProbe` is the bag every stage checker mutates. See stage/README.md.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from rdflib import Graph

from src.extraction_prompt_generation.compile.context import (
    AgenticGenerationContext,
)


@dataclass
class StageProbe:
    context: AgenticGenerationContext
    path: Path
    relative: str
    text: str
    failures: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    imported_module: Any = None
    artifact_tree: ast.Module | None = None
    package_name: str = ""
    runtime: Any = None
    graph: Graph | None = None
    canonical_registry_key: str | None = None
    probe_registry_key: str | None = None
    created: dict[str, str] = field(default_factory=dict)
    creators: dict[str, Any] = field(default_factory=dict)
    class_by_local: dict[str, str] = field(default_factory=dict)
    class_iris: set[str] = field(default_factory=set)
    publish_contract: dict[str, Any] = field(default_factory=dict)
    external_creator_specs: list[Any] = field(default_factory=list)
    creator_contract_by_local: dict[str, Any] = field(default_factory=dict)
    om2_range_iris: set[str] = field(default_factory=set)

    @property
    def name(self) -> str:
        return self.path.name

    def fail(self, message: str) -> None:
        self.failures.append(message)

    def warn(self, message: str) -> None:
        self.warnings.append(message)
