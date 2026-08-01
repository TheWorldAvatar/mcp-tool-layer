"""Tool-less pure-LLM prompt editing."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.agents.scripts_and_prompts_generation.exact_edit_editor import (
    apply_exact_edit_payload,
)
from src.agents.scripts_and_prompts_generation.llm_artifact_editor import (
    run_llm_artifact_editor,
)
from src.agents.scripts_and_prompts_generation.unified_diff_editor import (
    apply_llm_unified_diff,
)


def _editor_prompt(
    diagnosis: dict[str, Any], targets: list[Path], contract: dict[str, Any]
) -> str:
    payload = {
        "diagnosis": diagnosis,
        "contract": contract,
    }
    return (
        "You are a prompt editor. Decide and implement the prompt changes required by "
        "the redacted diagnosis using only general ontology/T-Box rules. "
        "Edit only files that genuinely need a change; it is not necessary to edit every "
        "Do not include fixture entities, gold values, DOI values, scripts, or new files. "
        "Preserve rules identified by the diagnosis as already correct. "
        "At least one real prompt change is required.\n\n"
        + json.dumps(payload, ensure_ascii=False)
    )


def apply_structured_edits(
    *,
    output_root: Path,
    targets: list[Path],
    response: dict[str, Any],
) -> dict[str, Any]:
    """Apply exact edits while accepting the legacy unified-diff envelope."""
    patch = response.get("patch_unified_diff")
    if isinstance(patch, str):
        report = apply_llm_unified_diff(
            output_root=output_root,
            targets=targets,
            patch_unified_diff=patch,
        )
        return {
            **report,
            "changed_prompts": list(report.get("changed_files") or []),
            "backend": "pure_llm_unified_diff",
        }
    if response.get("schema_version") != "exact-edits.v1":
        legacy_edits = response.get("edits")
        if isinstance(legacy_edits, list):
            files = []
            for index, item in enumerate(legacy_edits):
                path = Path(str(item.get("path") or ""))
                resolved = path.resolve()
                if not resolved.is_relative_to(output_root.resolve()):
                    return {
                        "ok": False,
                        "failures": [f"outside_diagnosis:{path}"],
                        "changed_prompts": [],
                    }
                files.append(
                    {
                        "path": resolved.relative_to(output_root.resolve()).as_posix()
                        if resolved.is_relative_to(output_root.resolve())
                        else str(path),
                        "expected_sha256": __import__("hashlib").sha256(
                            resolved.read_bytes()
                        ).hexdigest()
                        if resolved.is_file()
                        else "",
                        "operations": [
                            {
                                "edit_id": f"legacy-{index}-{replacement_index}",
                                "kind": "replace_exact",
                                "old_text": replacement.get("old"),
                                "new_text": replacement.get("new"),
                            }
                            for replacement_index, replacement in enumerate(
                                item.get("replacements") or []
                            )
                        ],
                    }
                )
            response = {"schema_version": "exact-edits.v1", "files": files}
    report = apply_exact_edit_payload(
        output_root=output_root,
        targets=targets,
        edit_payload=response,
    )
    failures = []
    for failure in report.get("failures") or []:
        if isinstance(failure, dict):
            code = str(failure.get("code") or "edit_failure")
            if code == "exact_edit_ambiguous_match":
                failures.append(f"match_count:{failure}")
            else:
                failures.append(f"{code}:{failure}")
        else:
            failures.append(str(failure))
    return {
        **report,
        "failures": failures,
        "changed_prompts": list(report.get("changed_files") or []),
        "backend": "pure_llm_exact_edits",
    }


def run_structured_prompt_editor(
    *,
    model_name: str,
    output_root: Path,
    targets: list[Path],
    diagnosis: dict[str, Any],
    contract: dict[str, Any],
    max_attempts: int = 5,
    edit_backend: str = "exact_edits",
) -> dict[str, Any]:
    """Generate and apply prompt changes through a plain LLM edit call."""
    report = run_llm_artifact_editor(
        model_name=model_name,
        output_root=output_root,
        targets=targets,
        task_prompt=_editor_prompt(diagnosis, targets, contract),
        max_attempts=max_attempts,
        edit_backend=edit_backend,
    )
    return {
        **report,
        "changed_prompts": list(report.get("changed_files") or []),
    }
