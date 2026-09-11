"""Mutable accumulator for runtime graph-hygiene observations.

`HygieneState` collects failures, warnings, and obligations. See README.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.extraction_prompt_generation.validate.report.common import (
    _semantic_obligation,
)


@dataclass
class HygieneState:
    failures: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    obligations: list[dict[str, Any]] = field(default_factory=list)

    def fail(self, subject_key: str, message: str, **evidence: Any) -> None:
        self.failures.append(message)
        self.obligations.append(
            _semantic_obligation(
                subject_key=subject_key,
                failures=[message],
                observed_artifacts=["main.py"],
                evidence=evidence,
                message=message,
            )
        )

    def as_tuple(self) -> tuple[list[str], list[str], list[dict[str, Any]]]:
        return self.failures, self.warnings, self.obligations
