"""Tool-less structured prompt editing for stable prompt enhancement."""

from __future__ import annotations

import difflib
import json
import time
from pathlib import Path
from typing import Any

from models.LLMCreator import LLMCreator
from models.ModelConfig import ModelConfig
from src.agents.scripts_and_prompts_generation.content_diagnosis import (
    artifact_manifest,
    parse_json_object,
)


def _editor_prompt(
    diagnosis: dict[str, Any], targets: list[Path], contract: dict[str, Any]
) -> str:
    files = [
        {"path": path.resolve().as_posix(), "content": path.read_text(encoding="utf-8")}
        for path in targets
    ]
    payload = {
        "diagnosis": diagnosis,
        "contract": contract,
        "editable_files": files,
    }
    return (
        "You are a prompt editor. Apply the redacted diagnosis using only general "
        "ontology/T-Box rules. Return JSON only with this shape:\n"
        '{"edits":[{"path":"exact editable path","replacements":'
        '[{"old":"exact non-empty text occurring once","new":"replacement text"}]}],'
        '"summary":"..."}\n'
        "Edit only files that genuinely need a change; it is not necessary to edit every "
        "diagnosis target. Every old string must be copied exactly and uniquely from its file. "
        "Do not include fixture entities, gold values, DOI values, scripts, or new files. "
        "At least one real replacement is required.\n"
        + json.dumps(payload, ensure_ascii=False)
    )


def apply_structured_edits(
    *,
    output_root: Path,
    targets: list[Path],
    response: dict[str, Any],
) -> dict[str, Any]:
    """Validate and atomically apply exact replacements to selected prompt files."""
    root = output_root.resolve()
    allowed = {path.resolve().as_posix(): path.resolve() for path in targets}
    before_manifest = artifact_manifest(root)
    staged: dict[Path, str] = {}
    failures: list[str] = []

    edits = response.get("edits") or []
    if not isinstance(edits, list) or not edits:
        failures.append("structured_editor_returned_no_edits")
    for edit in edits if isinstance(edits, list) else []:
        if not isinstance(edit, dict):
            failures.append("invalid_edit_record")
            continue
        raw_path = str(edit.get("path") or "")
        path = allowed.get(Path(raw_path).resolve().as_posix())
        if path is None:
            failures.append(f"edit_target_outside_diagnosis:{raw_path}")
            continue
        try:
            path.relative_to(root)
        except ValueError:
            failures.append(f"edit_target_outside_candidate:{raw_path}")
            continue
        text = staged.get(path, path.read_text(encoding="utf-8"))
        replacements = edit.get("replacements") or []
        if not isinstance(replacements, list) or not replacements:
            failures.append(f"empty_replacements:{path.name}")
            continue
        for replacement in replacements:
            if not isinstance(replacement, dict):
                failures.append(f"invalid_replacement:{path.name}")
                continue
            old = str(replacement.get("old") or "")
            new = str(replacement.get("new") or "")
            if not old or old == new:
                failures.append(f"noop_replacement:{path.name}")
                continue
            count = text.count(old)
            if count != 1:
                failures.append(f"replacement_match_count:{path.name}:{count}")
                continue
            text = text.replace(old, new, 1)
        staged[path] = text

    if failures:
        return {"ok": False, "failures": failures, "changed_prompts": []}

    diffs: dict[str, str] = {}
    for path, new_text in staged.items():
        old_text = path.read_text(encoding="utf-8")
        if old_text == new_text:
            continue
        diffs[path.as_posix()] = "".join(
            difflib.unified_diff(
                old_text.splitlines(keepends=True),
                new_text.splitlines(keepends=True),
                fromfile=f"a/{path.name}",
                tofile=f"b/{path.name}",
            )
        )
    if not diffs:
        return {"ok": False, "failures": ["no_prompt_diff"], "changed_prompts": []}

    for path, new_text in staged.items():
        if path.as_posix() in diffs:
            path.write_text(new_text, encoding="utf-8")
    after_manifest = artifact_manifest(root)
    scripts_before = {
        path: digest
        for path, digest in before_manifest.items()
        if path.startswith("scripts/")
    }
    scripts_after = {
        path: digest
        for path, digest in after_manifest.items()
        if path.startswith("scripts/")
    }
    if scripts_before != scripts_after:
        raise RuntimeError("Structured prompt editor changed scripts")
    return {
        "ok": True,
        "failures": [],
        "backend": "structured_llm",
        "changed_prompts": sorted(diffs),
        "target_diffs": diffs,
    }


def run_structured_prompt_editor(
    *,
    model_name: str,
    output_root: Path,
    targets: list[Path],
    diagnosis: dict[str, Any],
    contract: dict[str, Any],
    max_attempts: int = 3,
) -> dict[str, Any]:
    """Generate and apply constrained exact replacements without ReAct or MCP."""
    prompt = _editor_prompt(diagnosis, targets, contract)
    attempts: list[dict[str, Any]] = []
    for attempt in range(1, max_attempts + 1):
        llm = LLMCreator(
            model=model_name,
            remote_model=True,
            model_config=ModelConfig(
                max_tokens=16000, timeout=600, temperature=0, top_p=0.1
            ),
        ).setup_llm()
        started = time.monotonic()
        try:
            response = llm.invoke(prompt)
            parsed = parse_json_object(getattr(response, "content", response))
            report = apply_structured_edits(
                output_root=output_root,
                targets=targets,
                response=parsed,
            )
        except Exception as exc:
            report = {
                "ok": False,
                "failures": [f"{type(exc).__name__}:{exc}"],
            }
        attempts.append(
            {
                "attempt": attempt,
                "elapsed_seconds": round(time.monotonic() - started, 3),
                **report,
            }
        )
        if report.get("ok"):
            return {**report, "attempts": attempts}
        prompt += (
            "\nPrevious response failed mechanical validation. Correct these errors and "
            "return a fresh complete JSON response:\n"
            + json.dumps(report.get("failures") or [], ensure_ascii=False)
        )
    return {
        "ok": False,
        "backend": "structured_llm",
        "failures": attempts[-1].get("failures") if attempts else ["no_attempt"],
        "attempts": attempts,
    }
