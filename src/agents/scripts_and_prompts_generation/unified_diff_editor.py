"""Pure-LLM unified-diff editing with deterministic safety checks.

The LLM owns every content decision. This module only validates target paths,
applies the returned patch atomically, and reports mechanical failures.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from src.agents.scripts_and_prompts_generation.level1_code_repair import invoke_json

ValidationCallback = Callable[[], dict[str, Any]]

_DIFF_HEADER_RE = re.compile(r"^diff --git a/(.+?) b/(.+?)$", re.MULTILINE)
_FILE_HEADER_RE = re.compile(r"^(?:--- a/|\+\+\+ b/)(.+)$", re.MULTILINE)
_HUNK_HEADER_RE = re.compile(
    r"^@@ -(?P<old_start>\d+)(?:,(?P<old_count>\d+))? "
    r"\+(?P<new_start>\d+)(?:,(?P<new_count>\d+))? @@(?: .*)?$"
)


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def _exact_diff_template(relative: str, content: str) -> str:
    """Build a mechanically exact small-hunk example from current target content."""
    lines = content.splitlines()
    if not lines:
        return (
            f"--- a/{relative}\n+++ b/{relative}\n"
            "@@ -0,0 +1,2 @@\n"
            "+<first new line>\n+<second new line>"
        )
    anchor_index = min(max(len(lines) // 2, 0), len(lines) - 1)
    start_index = max(0, anchor_index - 2)
    end_index = min(len(lines), anchor_index + 3)
    snippet = lines[start_index:end_index]
    changed_index = anchor_index - start_index
    new_snippet = list(snippet)
    new_snippet[changed_index] = "<replacement line; do not copy this placeholder>"
    old_start = start_index + 1
    old_count = len(snippet)
    body: list[str] = []
    for index, line in enumerate(snippet):
        if index == changed_index:
            body.extend([f"-{line}", f"+{new_snippet[index]}"])
        else:
            body.append(f" {line}")
    return (
        f"--- a/{relative}\n+++ b/{relative}\n"
        f"@@ -{old_start},{old_count} +{old_start},{old_count} @@\n"
        + "\n".join(body)
    )


def _normalise_relative(path: str) -> str:
    candidate = Path(path.replace("\\", "/"))
    if candidate.is_absolute() or candidate.drive:
        raise ValueError(f"Absolute patch path is forbidden: {path}")
    parts = candidate.parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise ValueError(f"Unsafe patch path is forbidden: {path}")
    return candidate.as_posix()


def _patch_paths(patch: str) -> set[str]:
    forbidden_headers = (
        "new file mode ",
        "deleted file mode ",
        "old mode ",
        "new mode ",
        "rename from ",
        "rename to ",
        "copy from ",
        "copy to ",
        "GIT binary patch",
        "Binary files ",
    )
    if any(
        line.startswith(forbidden_headers)
        for line in patch.splitlines()
    ):
        raise ValueError("Patch create/delete/rename/mode/binary operations are forbidden")
    paths: set[str] = set()
    for left, right in _DIFF_HEADER_RE.findall(patch):
        if left != right:
            raise ValueError(f"Patch rename is forbidden: {left} -> {right}")
        paths.add(_normalise_relative(left))
    for path in _FILE_HEADER_RE.findall(patch):
        if path == "/dev/null":
            raise ValueError("Patch create/delete operations are forbidden")
        paths.add(_normalise_relative(path))
    return paths


def _hunk_protocol_failures(
    patch: str, *, target_line_counts: dict[str, int]
) -> list[str]:
    """Require enough exact unchanged context for deterministic hunk placement."""
    failures: list[str] = []
    hunk_index = 0
    context_count = 0
    required_context = 0
    in_hunk = False
    current_path = ""

    def finish_hunk() -> None:
        if in_hunk and context_count < required_context:
            failures.append(
                f"unified_diff_hunk_requires_context:hunk={hunk_index}:"
                f"found={context_count}:required={required_context}"
            )

    for line in patch.splitlines():
        if line.startswith("+++ b/"):
            current_path = line.removeprefix("+++ b/").strip()
            continue
        if line.startswith("@@"):
            finish_hunk()
            hunk_index += 1
            context_count = 0
            in_hunk = True
            match = _HUNK_HEADER_RE.fullmatch(line)
            if match is None:
                failures.append(
                    f"invalid_numeric_hunk_header:hunk={hunk_index}:{line}"
                )
                required_context = 1
            else:
                old_count = int(match.group("old_count") or "1")
                old_start = int(match.group("old_start"))
                whole_file = (
                    old_start == 1
                    and old_count == target_line_counts.get(current_path, -1)
                )
                required_context = 1 if old_count > 1 and not whole_file else 0
            continue
        if in_hunk and (
            line.startswith("diff --git ")
            or line.startswith("--- ")
            or line.startswith("+++ ")
        ):
            finish_hunk()
            in_hunk = False
            continue
        if in_hunk and line.startswith(" "):
            context_count += 1
    finish_hunk()
    if hunk_index == 0:
        failures.append("unified_diff_has_no_hunks")
    return failures


def _hunk_mismatch_diagnostics(
    patch: str, *, allowed: dict[str, Path]
) -> list[str]:
    """Explain why a syntactically valid hunk does not match the supplied revision."""
    diagnostics: list[str] = []
    current_path = ""
    header = ""
    old_start = 1
    old_lines: list[str] = []

    def finish_hunk() -> None:
        if not header or current_path not in allowed:
            return
        source_lines = allowed[current_path].read_text(encoding="utf-8").splitlines()
        expected_index = max(old_start - 1, 0)
        expected = source_lines[expected_index : expected_index + len(old_lines)]
        if expected == old_lines:
            diagnostics.append(
                f"hunk_old_side_matches_exact_revision:{current_path}:{header}"
            )
            return
        exact_locations = [
            index + 1
            for index in range(max(len(source_lines) - len(old_lines) + 1, 0))
            if source_lines[index : index + len(old_lines)] == old_lines
        ]
        if exact_locations:
            diagnostics.append(
                f"hunk_header_location_mismatch:{current_path}:{header}:"
                f"exact_old_side_starts_at={exact_locations[:5]}"
            )
            return
        mismatch_index = next(
            (
                index
                for index, (actual, proposed) in enumerate(zip(expected, old_lines))
                if actual != proposed
            ),
            min(len(expected), len(old_lines)),
        )
        actual = expected[mismatch_index] if mismatch_index < len(expected) else "<missing>"
        proposed = (
            old_lines[mismatch_index] if mismatch_index < len(old_lines) else "<missing>"
        )
        diagnostics.append(
            f"hunk_old_context_not_verbatim:{current_path}:{header}:"
            f"source_line={old_start + mismatch_index}:"
            f"supplied={proposed!r}:current={actual!r}"
        )

    for line in patch.splitlines():
        if line.startswith("+++ b/"):
            current_path = line.removeprefix("+++ b/").strip()
            continue
        if line.startswith("@@"):
            finish_hunk()
            header = line
            old_lines = []
            match = _HUNK_HEADER_RE.fullmatch(line)
            old_start = int(match.group("old_start")) if match else 1
            continue
        if header and (
            line.startswith("diff --git ")
            or line.startswith("--- ")
            or line.startswith("+++ ")
        ):
            finish_hunk()
            header = ""
            old_lines = []
            continue
        if header and line.startswith((" ", "-")):
            old_lines.append(line[1:])
    finish_hunk()
    return diagnostics


def _rebase_uniquely_located_hunks(
    patch: str, *, allowed: dict[str, Path]
) -> tuple[str, list[dict[str, Any]], list[str]]:
    """Correct hunk coordinates only when the complete old side has one exact location."""
    lines = patch.splitlines()
    current_path = ""
    records: list[dict[str, Any]] = []
    failures: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if line.startswith("+++ b/"):
            current_path = line.removeprefix("+++ b/").strip()
            index += 1
            continue
        match = _HUNK_HEADER_RE.fullmatch(line)
        if match is None or current_path not in allowed:
            index += 1
            continue

        end = index + 1
        old_lines: list[str] = []
        while end < len(lines):
            body_line = lines[end]
            if body_line.startswith(("@@", "diff --git ", "--- ", "+++ ")):
                break
            if body_line.startswith((" ", "-")):
                old_lines.append(body_line[1:])
            end += 1
        if not old_lines:
            index = end
            continue

        source_lines = allowed[current_path].read_text(encoding="utf-8").splitlines()
        locations = [
            candidate + 1
            for candidate in range(max(len(source_lines) - len(old_lines) + 1, 0))
            if source_lines[candidate : candidate + len(old_lines)] == old_lines
        ]
        old_start = int(match.group("old_start"))
        if len(locations) == 1 and locations[0] != old_start:
            actual_start = locations[0]
            new_start = int(match.group("new_start")) + (actual_start - old_start)
            old_count_text = (
                f",{match.group('old_count')}" if match.group("old_count") is not None else ""
            )
            new_count_text = (
                f",{match.group('new_count')}" if match.group("new_count") is not None else ""
            )
            suffix = line[match.end() :]
            rebased_header = (
                f"@@ -{actual_start}{old_count_text} "
                f"+{new_start}{new_count_text} @@{suffix}"
            )
            lines[index] = rebased_header
            records.append(
                {
                    "path": current_path,
                    "original_header": line,
                    "rebased_header": rebased_header,
                    "reason": "complete old-side sequence has one exact location",
                }
            )
        elif locations and old_start not in locations and len(locations) > 1:
            failures.append(
                f"ambiguous_hunk_coordinate_rebase:{current_path}:{line}:"
                f"exact_old_side_starts_at={locations[:10]}"
            )
        index = end
    return "\n".join(lines), records, failures


def _run_git_apply(root: Path, patch_path: Path, *, check: bool) -> subprocess.CompletedProcess[str]:
    args = [
        "git",
        "apply",
        "--recount",
        "--whitespace=nowarn",
        "--ignore-space-change",
    ]
    if check:
        args.append("--check")
    args.append(str(patch_path))
    return subprocess.run(
        args,
        cwd=str(root),
        text=True,
        capture_output=True,
        timeout=120,
    )


def apply_llm_unified_diff(
    *,
    output_root: Path,
    targets: list[Path],
    patch_unified_diff: str,
    max_changed_files: int | None = None,
) -> dict[str, Any]:
    """Mechanically validate and atomically apply one LLM-authored unified diff."""
    root = output_root.resolve()
    allowed: dict[str, Path] = {}
    for target in targets:
        resolved = target.resolve()
        if not resolved.is_file():
            return {"ok": False, "failures": [f"missing_target:{resolved}"]}
        try:
            relative = resolved.relative_to(root).as_posix()
        except ValueError:
            return {"ok": False, "failures": [f"target_outside_output_root:{resolved}"]}
        allowed[relative] = resolved

    patch = str(patch_unified_diff or "").strip()
    if not patch:
        return {
            "ok": False,
            "failures": ["empty_unified_diff"],
            "rejected_patch_unified_diff": patch,
        }
    forbidden_residue = [
        marker
        for marker in ("*** Begin Patch", "*** End Patch", "```diff", "```patch", "```")
        if marker in patch
    ]
    if forbidden_residue:
        return {
            "ok": False,
            "failures": [
                "unified_diff_contains_non_diff_residue:"
                + ",".join(forbidden_residue)
            ],
            "rejected_patch_unified_diff": patch,
        }
    protocol_failures = _hunk_protocol_failures(
        patch,
        target_line_counts={
            relative: len(path.read_text(encoding="utf-8").splitlines())
            for relative, path in allowed.items()
        },
    )
    if protocol_failures:
        return {
            "ok": False,
            "failures": protocol_failures,
            "rejected_patch_unified_diff": patch,
        }
    try:
        touched = _patch_paths(patch)
    except ValueError as exc:
        return {
            "ok": False,
            "failures": [str(exc)],
            "rejected_patch_unified_diff": patch,
        }
    if not touched:
        return {
            "ok": False,
            "failures": ["unified_diff_has_no_file_headers"],
            "rejected_patch_unified_diff": patch,
        }
    if max_changed_files is not None and len(touched) > max_changed_files:
        return {
            "ok": False,
            "failures": [
                f"patch_target_limit_exceeded:{len(touched)}>{max_changed_files}"
            ],
            "rejected_patch_unified_diff": patch,
        }
    unauthorized = sorted(touched - set(allowed))
    if unauthorized:
        return {
            "ok": False,
            "failures": [f"unauthorised_patch_targets:{','.join(unauthorized)}"],
            "rejected_patch_unified_diff": patch,
        }

    original_patch = patch
    patch, coordinate_rebases, coordinate_rebase_failures = _rebase_uniquely_located_hunks(
        patch, allowed=allowed
    )
    if coordinate_rebase_failures:
        return {
            "ok": False,
            "failures": coordinate_rebase_failures,
            "rejected_patch_unified_diff": original_patch,
        }
    snapshots = {path: path.read_bytes() for path in allowed.values()}
    touched_paths = [allowed[relative] for relative in touched]
    for path in touched_paths:
        content = path.read_bytes()
        if b"\r\n" in content:
            path.write_bytes(content.replace(b"\r\n", b"\n"))
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=root,
        prefix=".llm_candidate_",
        suffix=".patch",
        delete=False,
    ) as patch_file:
        patch_file.write(patch + "\n")
        patch_path = Path(patch_file.name)
    try:
        check = _run_git_apply(root, patch_path, check=True)
        if check.returncode != 0:
            restore_targets(snapshots)
            return {
                "ok": False,
                "failures": [
                    f"git_apply_check_failed:{check.stderr.strip() or check.stdout.strip()}",
                    *_hunk_mismatch_diagnostics(patch, allowed=allowed),
                ],
                "rejected_patch_unified_diff": patch,
            }
        applied = _run_git_apply(root, patch_path, check=False)
        if applied.returncode != 0:
            for path, content in snapshots.items():
                path.write_bytes(content)
            return {
                "ok": False,
                "failures": [f"git_apply_failed:{applied.stderr.strip() or applied.stdout.strip()}"],
                "rejected_patch_unified_diff": patch,
            }
        changed = sorted(
            relative
            for relative, path in allowed.items()
            if path.read_bytes() != snapshots[path]
        )
        if not changed:
            restore_targets(snapshots)
            return {
                "ok": False,
                "failures": ["no_file_diff"],
                "changed_files": [],
                "rejected_patch_unified_diff": patch,
            }
        return {
            "ok": True,
            "failures": [],
            "changed_files": changed,
            "patch_unified_diff": patch,
            "original_patch_unified_diff": (
                original_patch if coordinate_rebases else None
            ),
            "coordinate_rebases": coordinate_rebases,
        }
    finally:
        patch_path.unlink(missing_ok=True)


def restore_targets(snapshots: dict[Path, bytes]) -> None:
    """Restore a candidate after a rejected LLM patch."""
    for path, content in snapshots.items():
        path.write_bytes(content)


def run_llm_unified_diff_editor(
    *,
    model_name: str,
    output_root: Path,
    targets: list[Path],
    task_prompt: str,
    max_attempts: int = 3,
    validate: ValidationCallback | None = None,
    require_all_targets_changed: bool = False,
    max_targets: int | None = None,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Request, apply, and validate LLM patches; fail closed after retries."""
    root = output_root.resolve()
    resolved_targets = [path.resolve() for path in targets]
    if max_targets is not None and len(set(resolved_targets)) > max_targets:
        return {
            "ok": False,
            "backend": "pure_llm_unified_diff",
            "failures": [
                f"editor_target_limit_exceeded:{len(set(resolved_targets))}>{max_targets}"
            ],
            "attempts": [],
        }
    snapshots = {path: path.read_bytes() for path in resolved_targets if path.is_file()}
    target_listing = [
        {
            "path": path.relative_to(root).as_posix(),
            "content": path.read_text(encoding="utf-8"),
            "line_count": len(path.read_text(encoding="utf-8").splitlines()),
            "exact_small_hunk_template": _exact_diff_template(
                path.relative_to(root).as_posix(),
                path.read_text(encoding="utf-8"),
            ),
            "empty_file_diff_skeleton": (
                "--- a/"
                + path.relative_to(root).as_posix()
                + "\n+++ b/"
                + path.relative_to(root).as_posix()
                + "\n@@ -0,0 +1,N @@\n+first generated line\n"
                if not path.read_text(encoding="utf-8")
                else None
            ),
        }
        for path in resolved_targets
    ]
    base_prompt = (
        task_prompt.strip()
        + "\n\nReturn only one JSON object with exactly this shape:\n"
        + '{"patch_unified_diff":"<standard unified diff>","summary":"<brief rationale>"}\n'
        + "PATCH PROTOCOL (mechanically enforced): The diff must use paths relative to "
        + "output_root, with `a/` and `b/` prefixes. "
        + "A `diff --git` line is optional, but the exact `--- a/path`, `+++ b/path`, and "
        + "numeric `@@ -old_start,old_count +new_start,new_count @@` headers are mandatory. "
        + "Bare `@@` headers are invalid. Each editable file includes an "
        + "`exact_small_hunk_template` built from that file's current content. Copy its path, "
        + "numeric header style, and unchanged context-line format; replace only the indicated "
        + "placeholder with your semantic edit and adjust counts if your edit changes line count. "
        + "Every context line must be copied byte-for-byte from the supplied current content; "
        + "every removed `-` line is also old text and must be copied byte-for-byte. Never "
        + "reconstruct, paraphrase, or copy old/context lines from memory or a prior rejected "
        + "candidate. Before responding, search `editable_files.content` and verify that each "
        + "hunk's complete old-side sequence (all ` ` and `-` lines, without their diff prefix) "
        + "occurs verbatim and contiguously. For an empty target, follow its supplied "
        + "empty_file_diff_skeleton and replace N with the exact number of added `+` lines. "
        + "Do not replace a non-empty whole file. Use the smallest symbol-level hunk with "
        + "at least 2 exact unchanged context lines around nontrivial edits. A hunk with only "
        + "removed/added lines and no leading ` ` context is invalid. Re-read the supplied "
        + "content after drafting and "
        + "verify each old/context line and numeric hunk count before responding. "
        + "It may modify only the editable files below. Do not create, delete, or rename files. "
        + "At least one real change is required. Return standard unified diff only inside the "
        + "JSON string. Never include Markdown fences or ApplyPatch sentinels such as "
        + "`*** Begin Patch` / `*** End Patch`.\n\n"
        + json.dumps(
            {"output_root": root.as_posix(), "editable_files": target_listing},
            ensure_ascii=False,
        )
    )
    feedback: dict[str, Any] = {}
    attempts: list[dict[str, Any]] = []
    emit = progress or (lambda _message: None)

    for attempt in range(1, max(1, max_attempts) + 1):
        restore_targets(snapshots)
        prompt = base_prompt
        if feedback:
            prompt += (
                "\n\nThe previous candidate was rejected. Use this mechanical validation "
                "feedback to produce a fresh complete diff:\n"
                + json.dumps(feedback, ensure_ascii=False)
            )
        if attempt > 1:
            delay_seconds = min(
                _env_int("TWA_PATCH_RETRY_BASE_DELAY", 5) * (2 ** (attempt - 2)),
                _env_int("TWA_PATCH_RETRY_MAX_DELAY", 30),
            )
            emit(
                f"waiting {delay_seconds}s before plain LLM retry "
                f"{attempt}/{max(1, max_attempts)}"
            )
            time.sleep(delay_seconds)
        started = time.monotonic()
        emit(
            f"plain LLM unified-diff attempt {attempt}/{max(1, max_attempts)} "
            f"for {len(resolved_targets)} target(s)"
        )
        try:
            timeout_seconds = _env_int("TWA_PATCH_CALL_TIMEOUT", 300)
            response = invoke_json(
                model_name,
                prompt,
                timeout_seconds=timeout_seconds,
                max_attempts=1,
                provider_max_retries=0,
            )
            patch = response.data.get("patch_unified_diff")
            if not isinstance(patch, str):
                report: dict[str, Any] = {
                    "ok": False,
                    "failures": ["missing_patch_unified_diff"],
                }
            else:
                report = apply_llm_unified_diff(
                    output_root=root,
                    targets=resolved_targets,
                    patch_unified_diff=patch,
                    max_changed_files=max_targets,
                )
                if report.get("ok") and require_all_targets_changed:
                    expected = {
                        path.relative_to(root).as_posix() for path in resolved_targets
                    }
                    changed = set(report.get("changed_files") or [])
                    missing = sorted(expected - changed)
                    if missing:
                        report["ok"] = False
                        report["failures"] = [
                            f"llm_did_not_generate_all_targets:{','.join(missing)}"
                        ]
                if report.get("ok") and validate is not None:
                    validation = validate()
                    report["validation"] = validation
                    if not validation.get("ok"):
                        report["ok"] = False
                        report["failures"] = list(validation.get("failures") or ["validation_failed"])
                        restore_targets(snapshots)
            attempt_record = {
                "attempt": attempt,
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "token_usage": response.token_usage,
                **report,
            }
        except Exception as exc:
            attempt_record = {
                "attempt": attempt,
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "ok": False,
                "failures": [f"{type(exc).__name__}:{exc}"],
            }
        attempts.append(attempt_record)
        emit(
            f"plain LLM unified-diff attempt {attempt} "
            f"ok={bool(attempt_record.get('ok'))}"
        )
        if attempt_record.get("ok"):
            return {
                **attempt_record,
                "backend": "pure_llm_unified_diff",
                "attempts": attempts,
            }
        feedback = {
            "attempt": attempt,
            "failures": list(
                attempt_record.get("failures") or ["unknown_patch_failure"]
            ),
            "validation": attempt_record.get("validation") or {},
            "diff_protocol_reminder": (
                "Regenerate a SMALL patch against the original editable_files content. "
                "Every section needs exact numeric @@ -old_start,old_count "
                "+new_start,new_count @@ coordinates; bare @@ is invalid. Copy all old/context "
                "lines byte-for-byte and include unchanged ` ` context around nontrivial edits. "
                "Treat editable_files.content—not the rejected diff—as the sole current revision. "
                "Verify that all ` ` and `-` lines form one verbatim contiguous sequence in it. "
                "Do not use a full-file replacement. Use a separate "
                "`---` and `+++` section for each changed file."
            ),
        }

    restore_targets(snapshots)
    return {
        "ok": False,
        "backend": "pure_llm_unified_diff",
        "failures": list(feedback.get("failures") or ["no_attempt"]),
        "attempts": attempts,
    }
