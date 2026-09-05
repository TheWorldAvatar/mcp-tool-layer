"""Production facade for LLM-authored artifact editing."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from src.agents.scripts_and_prompts_generation.exact_edit_editor import (
    run_llm_exact_edit_editor,
)
from src.agents.scripts_and_prompts_generation.unified_diff_editor import (
    run_llm_unified_diff_editor,
)

EditBackend = Literal["exact_edits", "unified_diff"]
DEFAULT_EDIT_BACKEND: EditBackend = "exact_edits"


def format_editor_failures(failures: list[Any]) -> list[str]:
    """Render structured and legacy failures for existing feedback consumers."""
    rendered: list[str] = []
    for failure in failures:
        if isinstance(failure, dict):
            code = str(failure.get("code") or "editor_failure")
            detail = ", ".join(
                f"{key}={value}"
                for key, value in failure.items()
                if key != "code"
            )
            rendered.append(f"{code}:{detail}" if detail else code)
        else:
            rendered.append(str(failure))
    return rendered


def run_llm_artifact_editor(
    *,
    model_name: str,
    output_root: Path,
    targets: list[Path],
    task_prompt: str,
    max_attempts: int = 5,
    validate: Callable[[], dict[str, Any]] | None = None,
    require_all_targets_changed: bool = False,
    max_targets: int | None = None,
    progress: Callable[[str], None] | None = None,
    edit_backend: EditBackend = DEFAULT_EDIT_BACKEND,
    additive_only: bool = False,
    max_added_lines: int | None = None,
    max_operations: int | None = None,
) -> dict[str, Any]:
    """Dispatch to the selected deterministic editing protocol."""
    common = {
        "model_name": model_name,
        "output_root": output_root,
        "targets": targets,
        "task_prompt": task_prompt,
        "max_attempts": max_attempts,
        "validate": validate,
        "require_all_targets_changed": require_all_targets_changed,
        "max_targets": max_targets,
        "progress": progress,
    }
    if edit_backend == "exact_edits":
        report = run_llm_exact_edit_editor(
            **common,
            additive_only=additive_only,
            max_added_lines=max_added_lines,
            max_operations=max_operations,
        )
    elif edit_backend == "unified_diff":
        report = run_llm_unified_diff_editor(**common)
        report.setdefault("report_schema_version", 2)
        report.setdefault("replay_protocol", "unified-diff.v1")
    else:
        raise ValueError(f"Unsupported edit backend: {edit_backend}")
    report["failure_codes"] = [
        (
            str(failure.get("code") or "editor_failure")
            if isinstance(failure, dict)
            else str(failure).split(":", 1)[0]
        )
        for failure in (report.get("failures") or [])
    ]
    report["failure_messages"] = format_editor_failures(
        list(report.get("failures") or [])
    )
    return report
