"""Retry policy helpers shared by LLM artifact editors."""

from __future__ import annotations

import hashlib
import json
from typing import Any

FIELD_SCHEMA_ERROR = "FIELD_SCHEMA_ERROR"

_NON_SEMANTIC_CANDIDATE_KEYS = {
    "edit_id",
    "expected_sha256",
    "summary",
}


def has_field_schema_error(validation: Any) -> bool:
    """Return whether validation explicitly classified a field-schema error."""
    try:
        rendered = json.dumps(validation, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        rendered = str(validation)
    return FIELD_SCHEMA_ERROR in rendered


def semantic_candidate_fingerprint(payload: Any) -> str:
    """Hash candidate semantics while ignoring per-attempt IDs and rationale."""

    def normalize(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                str(key): normalize(item)
                for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
                if str(key) not in _NON_SEMANTIC_CANDIDATE_KEYS
            }
        if isinstance(value, list):
            return [normalize(item) for item in value]
        return value

    rendered = json.dumps(
        normalize(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()
