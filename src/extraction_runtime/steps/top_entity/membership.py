"""Normalize and validate top-entity listing lines."""

from __future__ import annotations

import json
import re

_LISTING_WRAPPER = re.compile(r"^(?:[A-Za-z][\w.-]*-\d+\s+)\[(.*)\]\s*$")


def listing_labels(text: str) -> list[str]:
    """Return the bracketed labels from ITER-1 listing lines."""
    labels: list[str] = []
    for raw in str(text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        match = _LISTING_WRAPPER.match(line)
        labels.append((match.group(1) if match else line).strip())
    return [item for item in labels if item]


def split_outcome_reminder() -> str:
    return (
        "If one continuous passage shares a prefix then names independently "
        "executed outcomes, keep exactly those named outcomes."
    )


def normalize_top_entity_output(
    content: str,
    *,
    line_prefixes: list[str] | None = None,
    identifier_code_regex: str | None = None,
) -> str:
    text = str(content or "").strip()
    if text.startswith("```") and text.endswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    if text[:1] in {"[", "{"}:
        try:
            payload = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            payload = None
        if isinstance(payload, dict) and isinstance(payload.get("entities"), list):
            items = payload["entities"]
        elif isinstance(payload, list):
            items = payload
        else:
            items = []
        structured_lines: list[str] = []
        default_prefix = next(iter(tuple(p for p in (line_prefixes or []) if p)), "Entity")
        for index, item in enumerate(items, start=1):
            if not isinstance(item, dict):
                continue
            prefix = str(
                item.get("class") or item.get("type") or default_prefix
            ).rsplit(":", 1)[-1]
            label = str(
                item.get("entity_label")
                or item.get("label")
                or item.get("name")
                or ""
            ).strip()
            if label:
                structured_lines.append(f"{prefix}-{index} [{label}]")
        if structured_lines:
            text = "\n".join(structured_lines)

    normalized: list[str] = []
    seen: set[str] = set()
    prefixes = tuple(item for item in (line_prefixes or []) if item) or ("Entity",)
    try:
        code_re = re.compile(
            identifier_code_regex or r"\b[A-Z][A-Z0-9]{1,}(?:[-_]\d+[A-Za-z0-9]*)\b"
        )
    except re.error:
        code_re = re.compile(r"\b[A-Z][A-Z0-9]{1,}(?:[-_]\d+[A-Za-z0-9]*)\b")
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        matched_prefix = next((prefix for prefix in prefixes if line.startswith(prefix)), "")
        if not matched_prefix:
            normalized.append(line)
            continue
        candidates = code_re.findall(line)
        code = candidates[-1] if candidates else ""
        if code:
            key = code.upper()
            if key in seen:
                continue
            seen.add(key)
            normalized.append(f"{matched_prefix}-{len(seen)} [{code}]")
            continue
        if line in seen:
            continue
        seen.add(line)
        normalized.append(line)
    return "\n".join(normalized).strip() + ("\n" if normalized else "")


def validate_top_entity_lines(text: str, line_prefixes: list[str]) -> tuple[bool, list[str]]:
    lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
    if not lines:
        return False, ["top-entity listing is empty"]
    prefixes = tuple(item for item in line_prefixes if item)
    if not prefixes:
        return True, []
    errors: list[str] = []
    for line in lines:
        if not any(line.startswith(prefix) for prefix in prefixes):
            errors.append(f"line does not start with the selected top class: {line}")
    return (not errors), errors


def keep_valid_top_entity_lines(text: str, line_prefixes: list[str]) -> str:
    prefixes = tuple(item for item in line_prefixes if item)
    lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
    if not prefixes:
        return "\n".join(lines).strip() + ("\n" if lines else "")
    kept = [line for line in lines if any(line.startswith(prefix) for prefix in prefixes)]
    return "\n".join(kept).strip() + ("\n" if kept else "")


def bind_paper_content(prompt_template: str, paper_content: str) -> str:
    if "{paper_content}" in prompt_template or "{context}" in prompt_template:
        return (
            prompt_template.replace("{paper_content}", paper_content).replace(
                "{context}", paper_content
            )
        )
    return (
        prompt_template.rstrip()
        + "\n\n---- PIPELINE-INJECTED SOURCE TEXT: BEGIN ----\n"
        + paper_content
        + "\n---- PIPELINE-INJECTED SOURCE TEXT: END ----\n"
    )
