"""Lightweight structured-output checks for extraction pipeline stages."""

from __future__ import annotations

import json
import re
from typing import Any


def is_marker_only_optional_output(text: str) -> bool:
    """Return True for marker-only optional extraction outputs like `[FooExtraction]`."""
    return bool(re.fullmatch(r"\[[A-Za-z0-9_ -]*Extraction\]", str(text or "").strip()))


def parse_json_payload(text: str) -> Any:
    """Parse JSON payloads while preserving a clear ValueError for callers."""
    try:
        return json.loads(text)
    except Exception as e:
        raise ValueError(f"Invalid JSON extraction payload: {e}") from e


def validate_top_entity_lines(content: str, prefixes: list[str] | tuple[str, ...]) -> tuple[bool, list[str]]:
    """Validate normalized top-entity line shape: `<Prefix>-<n> [<label>]`."""
    allowed = tuple(p for p in prefixes if p) or ("Entity",)
    errors: list[str] = []
    lines = [x.strip() for x in str(content or "").splitlines() if x.strip()]
    if not lines:
        return False, ["top-entity output is empty"]
    for line in lines:
        if not any(line.startswith(prefix) for prefix in allowed):
            errors.append(f"line does not start with an allowed prefix: {line}")
            continue
        if not re.match(r"^[A-Za-z][A-Za-z0-9_]*-\d+\s+\[.+\]$", line):
            errors.append(f"line is not normalized as '<Prefix>-<n> [label]': {line}")
    return not errors, errors


def validate_hint_payload(content: str, *, allow_empty: bool = False) -> tuple[bool, list[str]]:
    """Validate generic extraction hints without assuming ontology-specific fields."""
    text = str(content or "").strip()
    if not text:
        return (allow_empty, [] if allow_empty else ["hint payload is empty"])
    if is_marker_only_optional_output(text):
        return True, []
    if text in {"{}", "[]"}:
        return (allow_empty, [] if allow_empty else ["empty JSON payload is not allowed here"])
    if text.startswith("{") or text.startswith("["):
        parsed = parse_json_payload(text)
        if parsed in ({}, []):
            return (allow_empty, [] if allow_empty else ["empty JSON payload is not allowed here"])
        return True, []
    if "SECTION:" in text or "rdf:type" in text:
        return True, []
    return True, []
