"""Structured GPT diagnosis support for prompt-content enhancement."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any


DIAGNOSIS_STATUS = {
    "actionable",
    "insufficient_evidence",
    "non_prompt_root_cause",
    "ambiguous_targets",
}
_STATUS_ALIASES = {
    "needs_revision": "actionable",
    "prompt_revision_required": "actionable",
    "no_prompt_change": "non_prompt_root_cause",
}


def json_digest(payload: Any) -> str:
    """Return a stable SHA-256 digest for an auditable JSON payload."""
    encoded = json.dumps(
        payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def prompt_inventory(prompts_dir: Path, *, max_chars: int = 6000) -> list[dict[str, Any]]:
    """Describe real prompt artifacts without assigning ontology-specific ownership."""
    return [
        {
            "path": path.resolve().as_posix(),
            "name": path.name,
            "content": path.read_text(encoding="utf-8", errors="replace")[:max_chars],
        }
        for path in sorted(prompts_dir.glob("*.md"))
        if path.is_file()
    ]


def parse_json_object(text: str) -> dict[str, Any]:
    """Extract the first JSON object from an LLM response."""
    decoder = json.JSONDecoder()
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", str(text or "").strip())
    for index, char in enumerate(cleaned):
        if char != "{":
            continue
        try:
            value, _ = decoder.raw_decode(cleaned[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise ValueError("Diagnosis response did not contain a JSON object")


def validate_diagnosis(
    diagnosis: dict[str, Any], inventory: list[dict[str, Any]]
) -> dict[str, Any]:
    """Validate GPT-selected targets against the supplied prompt inventory."""
    raw_status = str(diagnosis.get("status") or "").strip().casefold()
    status = _STATUS_ALIASES.get(raw_status, raw_status)
    if status not in DIAGNOSIS_STATUS:
        raise ValueError(f"Unsupported diagnosis status: {raw_status!r}")
    diagnosis["status"] = status
    allowed = {str(item["path"]) for item in inventory}
    by_name = {
        str(item["name"]): str(item["path"])
        for item in inventory
        if sum(1 for candidate in inventory if candidate["name"] == item["name"]) == 1
    }
    raw_targets = diagnosis.get("target_prompt_set") or []
    if isinstance(raw_targets, (str, dict)):
        raw_targets = [raw_targets]
    targets: list[str] = []
    for value in raw_targets:
        if isinstance(value, dict):
            raw = (
                value.get("path")
                or value.get("prompt")
                or value.get("file")
                or value.get("name")
            )
        else:
            raw = value
        text = str(raw or "")
        name = Path(text).name
        mapped = by_name.get(name)
        if mapped:
            targets.append(mapped)
            continue
        if name.casefold() in {"ontosynthesis", "all", "all_prompts"}:
            targets.extend(sorted(allowed))
            continue
        targets.append(text)
    if status == "actionable" and not targets:
        raise ValueError("Actionable diagnosis must select at least one prompt")
    invalid = sorted(set(targets) - allowed)
    if invalid:
        raise ValueError(f"Diagnosis selected prompts outside inventory: {invalid}")
    issues = diagnosis.get("issues")
    if status == "actionable" and not isinstance(issues, list):
        raise ValueError("Actionable diagnosis must include an issues list")
    diagnosis["target_prompt_set"] = list(dict.fromkeys(targets))
    return diagnosis


def fixture_literals(fixture: dict[str, Any]) -> set[str]:
    """Collect instance strings that must not reach editable prompt guidance."""
    values: set[str] = set()

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
        elif isinstance(value, str):
            text = value.strip()
            if len(text) >= 3:
                values.add(text)

    visit(fixture.get("hints") or {})
    visit((fixture.get("content_gt") or {}).get("hints") or {})
    return values


def redact_diagnosis(
    diagnosis: dict[str, Any], forbidden_literals: set[str]
) -> dict[str, Any]:
    """Project diagnosis to generic edit intent with fixture literals removed."""
    projected = {
        "schema_version": diagnosis.get("schema_version", "content-diagnosis.v1"),
        "status": diagnosis.get("status"),
        "summary": diagnosis.get("summary"),
        "issues": [],
        "target_prompt_set": diagnosis.get("target_prompt_set") or [],
    }
    for issue in diagnosis.get("issues") or []:
        if not isinstance(issue, dict):
            continue
        projected["issues"].append(
            {
                "issue_id": issue.get("issue_id"),
                "category": issue.get("category"),
                "stage": issue.get("stage"),
                "root_cause": issue.get("root_cause"),
                "target_prompts": issue.get("target_prompts") or [],
                "must_preserve": issue.get("must_preserve") or [],
                "suggested_change": issue.get("suggested_change"),
            }
        )
    text = json.dumps(projected, ensure_ascii=False)
    for literal in sorted(forbidden_literals, key=len, reverse=True):
        if literal:
            text = re.sub(re.escape(literal), "[INSTANCE_REDACTED]", text, flags=re.I)
    return json.loads(text)


def artifact_manifest(root: Path) -> dict[str, str]:
    """Hash all runtime-relevant artifacts for lineage and mutation checks."""
    manifest: dict[str, str] = {}
    artifact_patterns = (
        ("prompts", "*.md"),
        ("scripts", "*.py"),
        ("sparqls", "*.sparql"),
        ("iterations", "*.json"),
        ("ontology_structures", "*"),
    )
    for folder, pattern in artifact_patterns:
        base = root / folder
        if not base.exists():
            continue
        for path in sorted(base.rglob(pattern)):
            if not path.is_file():
                continue
            manifest[path.relative_to(root).as_posix()] = hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
    return manifest
