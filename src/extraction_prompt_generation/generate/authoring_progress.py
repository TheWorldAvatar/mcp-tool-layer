"""Terminal progress helpers and focused validation projection.

Keeps `[pure_llm]` lines short and avoids calling a stage review “success”.
See generate/README.md.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def _progress_summary(value: Any, *, limit: int = 50) -> str:
    """Render a concise, one-line progress objective for terminal output."""
    text = " ".join(str(value or "").split())
    return text[:limit] + ("…" if len(text) > limit else "")


def _progress_paths(paths: Any, *, limit: int = 3) -> str:
    """Render a bounded list of artifact basenames for terminal progress."""
    values = [Path(str(path)).name for path in (paths or [])]
    suffix = ",…" if len(values) > limit else ""
    return ",".join(values[:limit]) + suffix


def _progress_validation(report: dict[str, Any]) -> str:
    """Summarise stage review without misleadingly calling it full success."""
    observations = report.get("observations") or []
    statuses: dict[str, list[str]] = {}
    for observation in observations:
        if not isinstance(observation, dict):
            continue
        stage = str(observation.get("stage") or "unspecified")
        statuses.setdefault(stage, []).append(
            str(observation.get("status") or "unknown")
        )
    gates = ",".join(
        f"{stage}:{'fail' if 'fail' in values else 'pass'}"
        for stage, values in sorted(statuses.items())
    )
    failures = report.get("failures") or []
    failure_text = (
        _progress_summary(
            next(
                (
                    item.get("code") if isinstance(item, dict) else item
                    for item in failures
                ),
                "",
            )
        )
        if failures
        else ""
    )
    return (
        f"stage_ok={bool(report.get('stage_ok'))} "
        f"report_ok={bool(report.get('ok'))}"
        + (f" gates={gates}" if gates else "")
        + (f" first_failure={failure_text}" if failure_text else "")
    )


def _failure_count(report: dict[str, Any]) -> int:
    return len(report.get("failures") or [])


def _observation_key(observation: dict[str, Any]) -> str:
    check_id = str(observation.get("check_id") or "").strip()
    subject_key = str(observation.get("subject_key") or "").strip()
    return f"{check_id}::{subject_key}" if subject_key else check_id


def _focused_validation_projection(
    report: dict[str, Any],
    focus: dict[str, Any] | None,
    *,
    max_items: int = 8,
    max_text: int = 1200,
) -> dict[str, Any]:
    """Bound repair evidence to the selected observations and their direct blockers."""
    focus_ids = set((focus or {}).get("observation_ids") or [])
    observations = report.get("observations") or []
    selected = [
        observation
        for observation in observations
        if not focus_ids or _observation_key(observation) in focus_ids
    ]
    blocker_ids = {
        str(blocker)
        for observation in selected
        for blocker in observation.get("blocked_by") or []
    }
    selected.extend(
        observation
        for observation in observations
        if _observation_key(observation) in blocker_ids and observation not in selected
    )

    def clip(value: Any) -> str:
        text = str(value or "")
        return text if len(text) <= max_text else text[:max_text] + "…[truncated]"

    projected_observations = []
    for observation in selected[:max_items]:
        evidence = observation.get("evidence") or {}
        projected_observations.append(
            {
                "observation_id": _observation_key(observation),
                "status": observation.get("status"),
                "stage": observation.get("stage"),
                "message": clip(observation.get("message")),
                "failures": [
                    clip(item) for item in (evidence.get("failures") or [])[:max_items]
                ],
                "structured_evidence": {
                    key: value
                    for key, value in evidence.items()
                    if key
                    in {
                        "phase",
                        "contract_inputs",
                        "signature",
                        "missing_inputs",
                        "valid_call",
                        "invalid_call",
                        "repair_hint",
                        "target_artifact",
                    }
                },
                "observed_artifacts": observation.get("observed_artifacts") or [],
                "blocked_by": observation.get("blocked_by") or [],
            }
        )
    return {
        "failure_count": _failure_count(report),
        "focus_observations": projected_observations,
        "failure_summary": [
            clip(item) for item in (report.get("failures") or [])[:max_items]
        ],
    }
