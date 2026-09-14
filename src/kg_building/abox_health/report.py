"""Structured A-Box health findings."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Finding:
    """One health violation or skip notice."""

    code: str
    message: str
    severity: str = "error"
    iris: tuple[str, ...] = ()
    check: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "check": self.check,
        }
        if self.iris:
            payload["iris"] = list(self.iris)
        return payload


@dataclass
class HealthReport:
    """Aggregate health result for one A-Box."""

    ok: bool
    findings: list[Finding] = field(default_factory=list)
    roots: list[str] = field(default_factory=list)
    instance_count: int = 0
    source: str = ""
    skipped_checks: list[str] = field(default_factory=list)

    def add(self, finding: Finding) -> None:
        self.findings.append(finding)
        if finding.severity == "error":
            self.ok = False

    def errors(self) -> list[Finding]:
        return [item for item in self.findings if item.severity == "error"]

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "source": self.source,
            "roots": list(self.roots),
            "instance_count": self.instance_count,
            "error_count": len(self.errors()),
            "finding_count": len(self.findings),
            "skipped_checks": list(self.skipped_checks),
            "findings": [item.to_dict() for item in self.findings],
        }
