"""Stable machine-routable facts emitted by generation validators.

`build_validation_observation` is the only public shape. Checkers must not
invent ad-hoc report records. See compile/README.md.
"""

from __future__ import annotations

from typing import Any


def build_validation_observation(
    *,
    check_id: str,
    subject_key: str,
    stage: str,
    failures: list[str] | None = None,
    warnings: list[str] | None = None,
    observed_artifacts: list[str] | None = None,
    blocked_by: list[str] | None = None,
    evidence: dict[str, Any] | None = None,
    message: str | None = None,
) -> dict[str, Any]:
    """Build a stable, machine-routable fact emitted by a generation validator."""
    failure_items = [str(item) for item in (failures or [])]
    warning_items = [str(item) for item in (warnings or [])]
    blockers = [str(item) for item in (blocked_by or [])]
    if blockers:
        status = "blocked"
    elif failure_items:
        status = "fail"
    else:
        status = "pass"
    observation_evidence = dict(evidence or {})
    if failure_items:
        observation_evidence.setdefault("failures", failure_items)
    if warning_items:
        observation_evidence.setdefault("warnings", warning_items)
    return {
        "check_id": str(check_id),
        "subject_key": str(subject_key),
        "stage": str(stage),
        "status": status,
        "observed_artifacts": [
            str(artifact) for artifact in (observed_artifacts or [])
        ],
        "blocked_by": blockers,
        "evidence": observation_evidence,
        "message": message
        or (
            f"{check_id} found {len(failure_items)} failure(s)"
            if failure_items
            else f"{check_id} completed with {len(warning_items)} warning(s)"
            if warning_items
            else f"{check_id} passed"
        ),
    }
