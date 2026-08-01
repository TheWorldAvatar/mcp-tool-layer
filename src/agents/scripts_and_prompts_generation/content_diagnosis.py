"""Structured GPT diagnosis support for prompt-content enhancement."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any


DIAGNOSIS_STATUS = {
    "actionable",
    "mixed",
    "script_actionable",
    "insufficient_evidence",
    "non_prompt_root_cause",
    "ambiguous_targets",
}
REPAIR_KINDS = {"prompt", "script", "mixed", "none", "adjudicate"}
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


def repair_artifact_inventory(
    *,
    prompts_dir: Path,
    scripts_dir: Path,
    evidence_paths: list[Path] | None = None,
    max_chars: int = 6000,
) -> list[dict[str, Any]]:
    """Inventory editable prompts/scripts and read-only runtime evidence."""
    inventory = prompt_inventory(prompts_dir, max_chars=max_chars)
    inventory.extend(
        {
            "path": path.resolve().as_posix(),
            "name": path.name,
            "kind": "script",
            "editable": not path.name.startswith("_fixed_"),
            "content": path.read_text(encoding="utf-8", errors="replace")[:max_chars],
        }
        for path in sorted(scripts_dir.glob("*.py"))
        if path.is_file()
    )
    for item in inventory:
        item.setdefault("kind", "prompt")
        item.setdefault("editable", True)
    inventory.extend(
        {
            "path": path.resolve().as_posix(),
            "name": path.name,
            "kind": "runtime_evidence",
            "editable": False,
            "content": path.read_text(encoding="utf-8", errors="replace")[:max_chars],
        }
        for path in sorted(evidence_paths or [])
        if path.is_file()
    )
    return inventory


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


def validate_repair_diagnosis(
    diagnosis: dict[str, Any],
    inventory: list[dict[str, Any]],
) -> dict[str, Any]:
    """Validate LLM-selected prompt/script/mixed repair targets."""
    raw_status = str(diagnosis.get("status") or "").strip().casefold()
    if raw_status not in DIAGNOSIS_STATUS:
        raise ValueError(f"Unsupported diagnosis status: {raw_status!r}")
    repair_kind = str(diagnosis.get("repair_kind") or "none").strip().casefold()
    if repair_kind not in REPAIR_KINDS:
        raise ValueError(f"Unsupported repair kind: {repair_kind!r}")
    by_path = {str(item["path"]): item for item in inventory}
    by_name = {
        str(item["name"]): item
        for item in inventory
        if sum(1 for other in inventory if other["name"] == item["name"]) == 1
    }
    raw_targets = diagnosis.get("target_artifacts") or []
    if isinstance(raw_targets, (str, dict)):
        raw_targets = [raw_targets]
    targets: list[str] = []
    for raw in raw_targets:
        value = (
            raw.get("path") or raw.get("file") or raw.get("name")
            if isinstance(raw, dict)
            else raw
        )
        text = str(value or "")
        item = by_path.get(text) or by_name.get(Path(text).name)
        if item is None:
            raise ValueError(f"Diagnosis selected artifact outside inventory: {text}")
        if not item.get("editable"):
            raise ValueError(f"Diagnosis selected read-only evidence for editing: {text}")
        targets.append(str(item["path"]))
    if repair_kind in {"prompt", "script", "mixed"} and not targets:
        raise ValueError(f"Repair kind {repair_kind!r} requires target artifacts")
    kinds = {str(by_path[target].get("kind")) for target in targets}
    if repair_kind == "prompt" and kinds - {"prompt"}:
        raise ValueError("Prompt diagnosis selected non-prompt targets")
    if repair_kind == "script" and kinds - {"script"}:
        raise ValueError("Script diagnosis selected non-script targets")
    if repair_kind == "mixed" and not {"prompt", "script"}.issubset(kinds):
        raise ValueError("Mixed diagnosis must select prompt and script targets")
    findings = diagnosis.get("causal_findings")
    if repair_kind in {"prompt", "script", "mixed"} and not isinstance(findings, list):
        raise ValueError("Actionable repair diagnosis requires causal findings")
    diagnosis["status"] = raw_status
    diagnosis["repair_kind"] = repair_kind
    diagnosis["target_artifacts"] = list(dict.fromkeys(targets))
    return diagnosis


def validate_single_prompt_focus(
    diagnosis: dict[str, Any],
) -> dict[str, Any]:
    """Require one prompt owner for a bounded content-enhancement iteration."""
    if diagnosis.get("repair_kind") != "prompt":
        raise ValueError(
            "Prompt enhancement focus requires repair_kind='prompt'; "
            f"actual={diagnosis.get('repair_kind')!r}"
        )
    targets = list(diagnosis.get("target_artifacts") or [])
    if len(targets) != 1:
        raise ValueError(
            "Prompt enhancement focus must select exactly one prompt artifact; "
            f"actual={targets}"
        )
    target = Path(targets[0])
    match = re.fullmatch(
        r"(EXTRACTION|KG_BUILDING|PRE_EXTRACTION)_ITER_(\d+(?:_\d+)?)\.md",
        target.name,
    )
    if match is None:
        raise ValueError(
            "Prompt enhancement focus target must be an iteration prompt artifact; "
            f"actual={target.name!r}"
        )
    layer = (
        "extraction"
        if match.group(1) in {"EXTRACTION", "PRE_EXTRACTION"}
        else "kg_building"
    )
    findings = [
        finding
        for finding in diagnosis.get("causal_findings") or []
        if isinstance(finding, dict)
    ]
    if not findings:
        raise ValueError("Prompt enhancement focus requires one causal finding")
    return {
        **diagnosis,
        "focus": {
            "owner_layer": layer,
            "artifact": targets[0],
            "iteration": match.group(2).replace("_", "."),
            "failure_mode": str(findings[0].get("cause") or "").strip(),
            "must_preserve": list(diagnosis.get("must_preserve") or []),
        },
    }


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
    document = str(fixture.get("document_md") or "")
    values.update(re.findall(r"\b10\.\d{4,9}/[^\s]+", document))
    values.update(
        line.strip().removeprefix("#").strip()
        for line in document.splitlines()
        if line.strip().startswith("#") and len(line.strip()) >= 3
    )
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


def redact_fixture_evidence(value: Any, forbidden_literals: set[str]) -> Any:
    """Remove fixture-specific strings before evidence reaches the repair agent."""
    text = json.dumps(value, ensure_ascii=False)
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
