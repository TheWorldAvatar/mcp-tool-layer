"""Pipeline knobs that turn off KG-building revision loops without touching extraction."""

from __future__ import annotations

from typing import Any


def kg_revisions_disabled(config: dict[str, Any] | None) -> bool:
    return bool((config or {}).get("disable_kg_revisions"))


def apply_disable_kg_revisions(config: dict[str, Any] | None) -> dict[str, Any]:
    """Zero KG-only retry/repair loops. Extraction pipelines are not modified."""
    updated = dict(config or {})
    if not kg_revisions_disabled(updated):
        return updated
    updated["kg_max_attempts"] = 1
    updated["kg_hint_revision_max_attempts"] = 0
    updated["post_publish_structural_retries"] = 0
    updated["continuity_audit_retries"] = 0
    continuity = dict(updated.get("continuity_audit") or {})
    continuity["enabled"] = False
    updated["continuity_audit"] = continuity
    presence = dict(updated.get("presence_coverage_audit") or {})
    presence["enabled"] = False
    updated["presence_coverage_audit"] = presence
    return updated


def ensure_kg_norev(
    config: dict[str, Any] | None,
    *,
    default: bool = False,
) -> dict[str, Any]:
    """Default KG-only norev without changing an explicit caller choice."""
    updated = dict(config or {})
    if default and "disable_kg_revisions" not in updated:
        updated["disable_kg_revisions"] = True
    return apply_disable_kg_revisions(updated)


def kg_agent_attempt_limit(config: dict[str, Any] | None) -> int:
    """How many full KG agent runs to attempt. Norev and the default are both 1."""
    applied = apply_disable_kg_revisions(config)
    raw = applied.get("kg_max_attempts")
    try:
        return max(1, int(raw)) if raw is not None else 1
    except (TypeError, ValueError):
        return 1
