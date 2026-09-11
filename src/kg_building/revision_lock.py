"""Hard-locked KG: no hint revision, no KG judge loop, no posthoc repair.

This is not a campaign flag. Pipeline KG and OX materialize once from the
extraction ledger. SHACL correction inside OntoLogX parse is graph-format
repair, not hint revision, and is unchanged.
"""

from __future__ import annotations

from typing import Any

KG_REVISION_LOCKED_OFF = True
KG_MAX_ATTEMPTS = 1
KG_HINT_REVISION_MAX_ATTEMPTS = 0
POST_PUBLISH_STRUCTURAL_RETRIES = 0

_FORBIDDEN_ENABLE_KEYS = (
    "kg_revision",
    "enable_kg_revision",
    "hint_revision",
    "kg_hint_revision",
    "posthoc",
    "post_publish_repair",
)

_AUDIT_BLOCKS = ("continuity_audit", "presence_coverage_audit")


def _positive_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def assert_kg_revision_locked_off(config: dict[str, Any] | None = None) -> None:
    """Reject any attempt to turn KG revision back on."""
    if KG_REVISION_LOCKED_OFF is not True:
        raise RuntimeError("KG revision is hard-locked off")
    payload = dict(config or {})
    if payload.get("disable_kg_revisions") is False:
        raise ValueError("KG revision is locked off; cannot set disable_kg_revisions=false")
    if payload.get("kg_revision") is True:
        raise ValueError("KG revision is locked off; cannot set kg_revision=true")
    for key in _FORBIDDEN_ENABLE_KEYS:
        if payload.get(key) is True:
            raise ValueError(f"KG revision is locked off; {key} is forbidden")
    attempts = payload.get("kg_max_attempts")
    if attempts is not None and _positive_int(attempts) > KG_MAX_ATTEMPTS:
        raise ValueError(
            f"KG revision is locked off; kg_max_attempts must be {KG_MAX_ATTEMPTS}"
        )
    hint_attempts = payload.get("kg_hint_revision_max_attempts")
    if hint_attempts is not None and _positive_int(hint_attempts) > 0:
        raise ValueError("KG revision is locked off; kg_hint_revision_max_attempts must be 0")
    structural = payload.get("post_publish_structural_retries")
    if structural is not None and _positive_int(structural) > 0:
        raise ValueError("KG revision is locked off; post_publish_structural_retries must be 0")
    continuity_retries = payload.get("continuity_audit_retries")
    if continuity_retries is not None and _positive_int(continuity_retries) > 0:
        raise ValueError("KG revision is locked off; continuity_audit_retries must be 0")
    for block_name in _AUDIT_BLOCKS:
        block = payload.get(block_name)
        if isinstance(block, dict) and block.get("enabled") is True:
            raise ValueError(f"KG revision is locked off; {block_name}.enabled cannot be true")


def apply_kg_revision_lock(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Zero KG-only retry/repair loops. Extraction revision is not touched."""
    updated = dict(config or {})
    assert_kg_revision_locked_off(updated)
    updated["kg_revision"] = False
    updated["disable_kg_revisions"] = True
    updated["kg_max_attempts"] = KG_MAX_ATTEMPTS
    updated["kg_hint_revision_max_attempts"] = KG_HINT_REVISION_MAX_ATTEMPTS
    updated["post_publish_structural_retries"] = POST_PUBLISH_STRUCTURAL_RETRIES
    updated["continuity_audit_retries"] = 0
    for block_name in _AUDIT_BLOCKS:
        block = dict(updated.get(block_name) or {})
        block["enabled"] = False
        updated[block_name] = block
    for key in _FORBIDDEN_ENABLE_KEYS:
        if key != "kg_revision":
            updated.pop(key, None)
    return updated
