"""Exact-edit backend for LLM-authored artifact changes."""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from src.agents.scripts_and_prompts_generation.editor_retry_policy import (
    has_field_schema_error,
    semantic_candidate_fingerprint,
)
from src.agents.scripts_and_prompts_generation.level1_code_repair import invoke_json
from src.agents.scripts_and_prompts_generation.llm_invocation_runtime import (
    configure_llm_invocation_journal,
)

ValidationCallback = Callable[[], dict[str, Any]]
SCHEMA_VERSION = "exact-edits.v1"


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default

EDIT_FAILURE_SPECS: dict[str, dict[str, str]] = {
    "field_schema_retry_no_progress": {
        "failure_class": "validation",
        "retry_hint": (
            "The same semantic candidate repeated after FIELD_SCHEMA_ERROR feedback; "
            "stop rather than loop indefinitely."
        ),
    },
    "invalid_target": {"failure_class": "edit_protocol", "retry_hint": "Use only regular, existing editable target files."},
    "target_outside_output_root": {"failure_class": "edit_protocol", "retry_hint": "Use targets located under output_root."},
    "invalid_exact_edit_schema": {"failure_class": "edit_protocol", "retry_hint": f"Set schema_version to {SCHEMA_VERSION}."},
    "missing_exact_edit_files": {"failure_class": "edit_protocol", "retry_hint": "Return one non-empty files array."},
    "invalid_exact_edit_file": {"failure_class": "edit_protocol", "retry_hint": "Each files entry must be an object."},
    "exact_edit_path_outside_root": {"failure_class": "edit_protocol", "retry_hint": "Copy a relative path from editable_files."},
    "exact_edit_invalid_path": {"failure_class": "edit_protocol", "retry_hint": "Copy a valid relative path from editable_files."},
    "unauthorised_exact_edit_target": {"failure_class": "edit_protocol", "retry_hint": "Copy files[].path exactly from editable_files."},
    "duplicate_exact_edit_file": {"failure_class": "edit_protocol", "retry_hint": "Mention each editable file at most once."},
    "stale_exact_edit_file": {"failure_class": "edit_protocol", "retry_hint": "Copy expected_sha256 from the original editable_files entry."},
    "exact_edit_invalid_encoding": {"failure_class": "edit_protocol", "retry_hint": "Edit only UTF-8 source supplied in editable_files."},
    "exact_edit_mixed_line_endings": {"failure_class": "edit_protocol", "retry_hint": "Preserve the supplied file line-ending convention."},
    "missing_exact_edit_operations": {"failure_class": "edit_protocol", "retry_hint": "Provide at least one operation per files entry."},
    "invalid_exact_edit_operation": {"failure_class": "edit_protocol", "retry_hint": "Each operation must be an object."},
    "duplicate_or_missing_edit_id": {"failure_class": "edit_protocol", "retry_hint": "Give every operation a unique non-empty edit_id."},
    "invalid_new_text": {"failure_class": "edit_protocol", "retry_hint": "Set new_text to a JSON string."},
    "replace_entire_file_requires_empty": {"failure_class": "edit_protocol", "retry_hint": "Use replace_exact for non-empty files."},
    "exact_edit_empty_old": {"failure_class": "edit_protocol", "retry_hint": "Copy a non-empty old_text from the original content."},
    "exact_edit_no_op": {"failure_class": "edit_protocol", "retry_hint": "Make new_text differ from old_text."},
    "exact_edit_no_match": {"failure_class": "edit_protocol", "retry_hint": "Copy old_text verbatim from the original content."},
    "exact_edit_ambiguous_match": {"failure_class": "edit_protocol", "retry_hint": "Include more unchanged context so old_text matches once."},
    "unsupported_exact_edit_kind": {"failure_class": "edit_protocol", "retry_hint": "Use replace_exact, or replace_entire_file only for an empty file."},
    "exact_edit_overlap": {"failure_class": "edit_protocol", "retry_hint": "Merge or separate overlapping operations."},
    "exact_edit_target_limit_exceeded": {"failure_class": "edit_protocol", "retry_hint": "Edit no more than the authorized target limit."},
    "exact_edit_operation_limit_exceeded": {"failure_class": "edit_protocol", "retry_hint": "Use fewer, smaller operations focused only on the diagnosed defect."},
    "non_additive_exact_edit": {"failure_class": "edit_protocol", "retry_hint": "Preserve old_text verbatim inside new_text and only insert the minimum new rule."},
    "exact_edit_addition_budget_exceeded": {"failure_class": "edit_protocol", "retry_hint": "Reduce the additive patch to the configured line budget."},
    "no_exact_edit_change": {"failure_class": "edit_protocol", "retry_hint": "Return at least one operation that changes source text."},
    "exact_edit_invalid_newlines": {"failure_class": "edit_protocol", "retry_hint": "Do not place bare carriage returns in new_text."},
    "exact_edit_concurrent_modification": {"failure_class": "edit_protocol", "retry_hint": "Regenerate against the latest supplied snapshot."},
    "candidate_validation_failed": {"failure_class": "candidate_validation", "retry_hint": "Fix the structured findings in validation.failures."},
    "exact_edit_exception": {"failure_class": "internal_error", "retry_hint": "Retry from the original snapshot; inspect detail if repeated."},
    "llm_did_not_edit_all_targets": {"failure_class": "edit_protocol", "retry_hint": "Include an effective edit for every required target."},
    "llm_exact_edit_exception": {"failure_class": "provider_error", "retry_hint": "Retry the structured exact-edit request."},
    "exact_edit_internal_error": {"failure_class": "internal_error", "retry_hint": "Inspect detail; the editor emitted an unregistered internal failure."},
    "no_attempt": {"failure_class": "internal_error", "retry_hint": "Run at least one exact-edit attempt."},
}
EDIT_FAILURE_CODES = frozenset(EDIT_FAILURE_SPECS)


def _edit_failure(code: str, **context: Any) -> dict[str, Any]:
    """Build a failure using only the finite exact-edit error vocabulary."""
    if code in EDIT_FAILURE_CODES:
        return {"code": code, **context}
    return {
        "code": "exact_edit_internal_error",
        "detail": f"unregistered_failure_code:{code}",
        **context,
    }


def _failure_spec(failures: Any) -> dict[str, str]:
    """Resolve deterministic classification and repair guidance."""
    if isinstance(failures, list):
        for failure in failures:
            if isinstance(failure, dict):
                code = str(failure.get("code") or "")
                if code in EDIT_FAILURE_SPECS:
                    return EDIT_FAILURE_SPECS[code]
    return EDIT_FAILURE_SPECS["exact_edit_internal_error"]


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _decode_snapshot(data: bytes) -> tuple[str, dict[str, Any]]:
    bom = data.startswith(b"\xef\xbb\xbf")
    raw = data[3:] if bom else data
    text = raw.decode("utf-8")
    crlf = text.count("\r\n")
    bare_lf = text.count("\n") - crlf
    bare_cr = text.count("\r") - crlf
    if (crlf and bare_lf) or bare_cr:
        raise ValueError("mixed_line_endings")
    line_ending = "crlf" if crlf else "lf"
    return text.replace("\r\n", "\n"), {
        "encoding": "utf-8",
        "bom": bom,
        "line_ending": line_ending,
        "trailing_newline": text.endswith(("\n", "\r")),
    }


def _encode_candidate(text: str, metadata: dict[str, Any]) -> bytes:
    if "\r" in text:
        raise ValueError("bare_carriage_return")
    rendered = text.replace("\n", "\r\n") if metadata["line_ending"] == "crlf" else text
    data = rendered.encode("utf-8")
    return (b"\xef\xbb\xbf" + data) if metadata["bom"] else data


def _normalise_path(raw: str, root: Path) -> str:
    candidate = Path(str(raw).replace("\\", "/"))
    if candidate.is_absolute():
        try:
            candidate = candidate.resolve().relative_to(root)
        except ValueError as exc:
            raise ValueError("exact_edit_path_outside_root") from exc
    if candidate.drive or not candidate.parts or any(
        part in {"", ".", ".."} for part in candidate.parts
    ):
        raise ValueError("exact_edit_invalid_path")
    return candidate.as_posix()


def _locations(text: str, needle: str) -> list[int]:
    locations: list[int] = []
    start = 0
    while True:
        found = text.find(needle, start)
        if found < 0:
            return locations
        locations.append(found)
        start = found + max(len(needle), 1)


def _line_column(text: str, offset: int) -> dict[str, int]:
    return {
        "line": text.count("\n", 0, offset) + 1,
        "column": offset - text.rfind("\n", 0, offset),
    }


def _audit_diff(before: dict[str, str], after: dict[str, str]) -> str:
    output: list[str] = []
    for relative in sorted(after):
        if before[relative] == after[relative]:
            continue
        output.extend(
            difflib.unified_diff(
                before[relative].splitlines(keepends=True),
                after[relative].splitlines(keepends=True),
                fromfile=f"a/{relative}",
                tofile=f"b/{relative}",
                n=3,
                lineterm="\n",
            )
        )
    return "".join(output)


def _brief_text(value: Any, *, limit: int = 50) -> str:
    """Render one log-safe, bounded single-line value."""
    text = " ".join(str(value or "").split())
    return text[:limit] + ("…" if len(text) > limit else "")


def _failure_codes(failures: Any, *, limit: int = 3) -> str:
    """Return a compact list of structured validation or edit failure codes."""
    if not isinstance(failures, list):
        return ""
    codes = [
        _brief_text(item.get("code"), limit=40)
        if isinstance(item, dict)
        else _brief_text(item, limit=40)
        for item in failures
    ]
    values = [code for code in codes if code]
    suffix = ",…" if len(values) > limit else ""
    return ",".join(values[:limit]) + suffix


def _validation_progress(validation: Any) -> str:
    """Summarise validation gates without collapsing them into one boolean."""
    if not isinstance(validation, dict):
        return "validation=not-run"
    if validation.get("skipped"):
        return "validation=skipped scope=edit-transaction"

    observations = validation.get("observations")
    stage_statuses: dict[str, list[str]] = {}
    if isinstance(observations, list):
        for observation in observations:
            if not isinstance(observation, dict):
                continue
            stage = _brief_text(observation.get("stage") or "unspecified", limit=24)
            status = str(observation.get("status") or "unknown")
            stage_statuses.setdefault(stage, []).append(status)
    gates = [
        f"{stage}:{'fail' if 'fail' in statuses else 'pass'}"
        for stage, statuses in sorted(stage_statuses.items())
    ]
    result = (
        "pass"
        if validation.get("ok")
        else "fail"
        if validation.get("ok") is False
        else "unknown"
    )
    detail = f" validation_gates={','.join(gates)}" if gates else ""
    failures = _failure_codes(validation.get("failures"))
    return f"validation={result}{detail}" + (
        f" validation_failures={failures}" if failures else ""
    )


def _attempt_progress(record: dict[str, Any]) -> list[str]:
    """Describe edit application and each available validation outcome."""
    changed = len(record.get("changed_files") or [])
    operations = len(record.get("operations") or [])
    objective = _brief_text((record.get("edit_payload") or {}).get("summary"))
    edit_result = (
        "rolled-back"
        if record.get("rollback_performed")
        else "candidate-written"
        if changed
        else "rejected"
    )
    messages = [
        "phase=edit "
        f"result={edit_result} "
        f"changed_files={changed} operations={operations}"
        + (f' objective="{objective}"' if objective else "")
    ]
    validation = record.get("validation")
    if validation is not None:
        messages.append(f"phase=review {_validation_progress(validation)}")
    failures = _failure_codes(record.get("failures"))
    if failures:
        failure_paths = sorted(
            {
                str(item.get("path") or "")
                for item in (record.get("failures") or [])
                if isinstance(item, dict) and str(item.get("path") or "")
            }
        )
        messages.append(
            f"phase=result ok={bool(record.get('ok'))} failures={failures}"
            + (
                " rejected_paths=" + ",".join(failure_paths)
                if failure_paths
                else ""
            )
        )
    elif record.get("ok"):
        messages.append("phase=result ok=True committed=True")
    return messages


def apply_exact_edit_payload(
    *,
    output_root: Path,
    targets: list[Path],
    edit_payload: dict[str, Any],
    validate: ValidationCallback | None = None,
    max_changed_files: int | None = None,
    additive_only: bool = False,
    max_added_lines: int | None = None,
    max_operations: int | None = None,
) -> dict[str, Any]:
    """Apply one exact-edits.v1 transaction or leave every target byte-identical."""
    root = output_root.resolve()
    allowed: dict[str, Path] = {}
    for target in targets:
        resolved = target.resolve()
        if not resolved.is_file() or resolved.is_symlink():
            return {"ok": False, "failures": [_edit_failure("invalid_target", path=str(target))]}
        try:
            relative = resolved.relative_to(root).as_posix()
        except ValueError:
            return {
                "ok": False,
                "failures": [_edit_failure("target_outside_output_root", path=str(target))],
            }
        allowed[relative] = resolved

    if edit_payload.get("schema_version") != SCHEMA_VERSION:
        return {
            "ok": False,
            "failures": [_edit_failure("invalid_exact_edit_schema")],
            "rejected_edit_payload": edit_payload,
        }
    files = edit_payload.get("files")
    if not isinstance(files, list) or not files:
        return {
            "ok": False,
            "failures": [_edit_failure("missing_exact_edit_files")],
            "rejected_edit_payload": edit_payload,
        }

    snapshots = {relative: path.read_bytes() for relative, path in allowed.items()}
    before_text: dict[str, str] = {}
    metadata: dict[str, dict[str, Any]] = {}
    failures: list[dict[str, Any]] = []
    plans: dict[str, list[dict[str, Any]]] = {}
    seen_paths: set[str] = set()
    seen_ids: set[str] = set()
    operation_count = 0
    added_line_count = 0

    for file_item in files:
        if not isinstance(file_item, dict):
            failures.append(_edit_failure("invalid_exact_edit_file"))
            continue
        try:
            relative = _normalise_path(str(file_item.get("path") or ""), root)
        except ValueError as exc:
            failures.append(_edit_failure(str(exc), path=file_item.get("path")))
            continue
        if relative not in allowed:
            failures.append({"code": "unauthorised_exact_edit_target", "path": relative})
            continue
        if relative in seen_paths:
            failures.append({"code": "duplicate_exact_edit_file", "path": relative})
            continue
        seen_paths.add(relative)
        snapshot = snapshots[relative]
        if file_item.get("expected_sha256") != _sha256(snapshot):
            failures.append({"code": "stale_exact_edit_file", "path": relative})
            continue
        try:
            canonical, file_metadata = _decode_snapshot(snapshot)
        except UnicodeDecodeError as exc:
            failures.append(
                _edit_failure(
                    "exact_edit_invalid_encoding",
                    path=relative,
                    detail=f"{type(exc).__name__}:{exc}",
                )
            )
            continue
        except ValueError as exc:
            code = (
                "exact_edit_mixed_line_endings"
                if str(exc) == "mixed_line_endings"
                else "exact_edit_internal_error"
            )
            failures.append(_edit_failure(code, path=relative, detail=str(exc)))
            continue
        before_text[relative] = canonical
        metadata[relative] = file_metadata
        operations = file_item.get("operations")
        if not isinstance(operations, list) or not operations:
            failures.append({"code": "missing_exact_edit_operations", "path": relative})
            continue
        file_plans: list[dict[str, Any]] = []
        for request_index, operation in enumerate(operations):
            if not isinstance(operation, dict):
                failures.append({"code": "invalid_exact_edit_operation", "path": relative})
                continue
            edit_id = str(operation.get("edit_id") or "")
            if not edit_id or edit_id in seen_ids:
                failures.append({"code": "duplicate_or_missing_edit_id", "edit_id": edit_id})
                continue
            seen_ids.add(edit_id)
            kind = str(operation.get("kind") or "replace_exact")
            new_text = operation.get("new_text")
            if not isinstance(new_text, str):
                failures.append({"code": "invalid_new_text", "edit_id": edit_id})
                continue
            if kind == "replace_entire_file":
                if canonical:
                    failures.append({"code": "replace_entire_file_requires_empty", "edit_id": edit_id})
                    continue
                span = (0, 0)
            elif kind == "replace_exact":
                old_text = operation.get("old_text")
                if not isinstance(old_text, str) or not old_text:
                    failures.append({"code": "exact_edit_empty_old", "edit_id": edit_id})
                    continue
                if old_text == new_text:
                    failures.append({"code": "exact_edit_no_op", "edit_id": edit_id})
                    continue
                if additive_only and old_text not in new_text:
                    failures.append(
                        {
                            "code": "non_additive_exact_edit",
                            "edit_id": edit_id,
                            "path": relative,
                        }
                    )
                    continue
                added_lines = max(
                    0,
                    len(new_text.splitlines()) - len(old_text.splitlines()),
                )
                added_line_count += added_lines
                matches = _locations(canonical, old_text)
                if len(matches) != 1:
                    failures.append(
                        {
                            "code": (
                                "exact_edit_no_match"
                                if not matches
                                else "exact_edit_ambiguous_match"
                            ),
                            "edit_id": edit_id,
                            "path": relative,
                            "match_count": len(matches),
                            "candidate_locations": [
                                _line_column(canonical, offset) for offset in matches[:10]
                            ],
                        }
                    )
                    continue
                span = (matches[0], matches[0] + len(old_text))
            else:
                failures.append({"code": "unsupported_exact_edit_kind", "edit_id": edit_id})
                continue
            file_plans.append(
                {
                    "edit_id": edit_id,
                    "kind": kind,
                    "start": span[0],
                    "end": span[1],
                    "new_text": new_text,
                    "request_index": request_index,
                    "location": _line_column(canonical, span[0]),
                }
            )
            operation_count += 1
        ordered = sorted(file_plans, key=lambda item: (item["start"], item["end"]))
        for left, right in zip(ordered, ordered[1:]):
            if right["start"] < left["end"]:
                failures.append(
                    {
                        "code": "exact_edit_overlap",
                        "path": relative,
                        "edit_ids": [left["edit_id"], right["edit_id"]],
                    }
                )
        plans[relative] = file_plans

    if failures:
        return {
            "ok": False,
            "failures": failures,
            "rejected_edit_payload": edit_payload,
        }
    if max_changed_files is not None and len(plans) > max_changed_files:
        return {
            "ok": False,
            "failures": [{"code": "exact_edit_target_limit_exceeded"}],
            "rejected_edit_payload": edit_payload,
        }
    if max_operations is not None and operation_count > max_operations:
        return {
            "ok": False,
            "failures": [
                {
                    "code": "exact_edit_operation_limit_exceeded",
                    "actual": operation_count,
                    "maximum": max_operations,
                }
            ],
            "rejected_edit_payload": edit_payload,
        }
    if max_added_lines is not None and added_line_count > max_added_lines:
        return {
            "ok": False,
            "failures": [
                {
                    "code": "exact_edit_addition_budget_exceeded",
                    "actual": added_line_count,
                    "maximum": max_added_lines,
                }
            ],
            "rejected_edit_payload": edit_payload,
        }

    after_text = dict(before_text)
    operation_reports: list[dict[str, Any]] = []
    for relative, file_plans in plans.items():
        candidate = before_text[relative]
        for application_order, item in enumerate(
            sorted(file_plans, key=lambda value: value["start"], reverse=True)
        ):
            candidate = (
                candidate[: item["start"]]
                + item["new_text"]
                + candidate[item["end"] :]
            )
            operation_reports.append(
                {
                    key: value
                    for key, value in item.items()
                    if key not in {"new_text"}
                }
                | {"path": relative, "application_order": application_order}
            )
        after_text[relative] = candidate
    changed = sorted(
        relative for relative in after_text if before_text[relative] != after_text[relative]
    )
    if not changed:
        return {
            "ok": False,
            "failures": [{"code": "no_exact_edit_change"}],
            "rejected_edit_payload": edit_payload,
        }

    candidates: dict[str, bytes] = {}
    try:
        candidates = {
            relative: _encode_candidate(after_text[relative], metadata[relative])
            for relative in changed
        }
    except ValueError as exc:
        return {
            "ok": False,
            "failures": [
                _edit_failure(
                    "exact_edit_invalid_newlines"
                    if str(exc) == "bare_carriage_return"
                    else "exact_edit_internal_error",
                    detail=str(exc),
                )
            ],
            "rejected_edit_payload": edit_payload,
        }
    for relative in changed:
        if allowed[relative].read_bytes() != snapshots[relative]:
            return {
                "ok": False,
                "failures": [{"code": "exact_edit_concurrent_modification", "path": relative}],
                "rejected_edit_payload": edit_payload,
            }

    validation: dict[str, Any] = {"ok": True, "failures": []}
    try:
        for relative in changed:
            allowed[relative].write_bytes(candidates[relative])
        validation = validate() if validate is not None else validation
        if not validation.get("ok"):
            for relative, data in snapshots.items():
                allowed[relative].write_bytes(data)
            return {
                "ok": False,
                "failures": [_edit_failure("candidate_validation_failed")],
                "validation": validation,
                "changed_files": changed,
                "operations": operation_reports,
                "edit_payload": edit_payload,
                "patch_unified_diff": _audit_diff(before_text, after_text),
                "rollback_performed": True,
            }
    except Exception as exc:
        for relative, data in snapshots.items():
            allowed[relative].write_bytes(data)
        return {
            "ok": False,
            "failures": [
                _edit_failure(
                    "exact_edit_exception",
                    detail=f"{type(exc).__name__}:{exc}",
                )
            ],
            "rollback_performed": True,
        }

    return {
        "ok": True,
        "backend": "pure_llm_exact_edits",
        "report_schema_version": 2,
        "replay_protocol": SCHEMA_VERSION,
        "schema_version": SCHEMA_VERSION,
        "failures": [],
        "changed_files": changed,
        "edit_payload": edit_payload,
        "operations": operation_reports,
        "files": [
            {
                "path": relative,
                "before_sha256": _sha256(snapshots[relative]),
                "after_sha256": _sha256(candidates[relative]),
                **metadata[relative],
            }
            for relative in changed
        ],
        "patch_unified_diff": _audit_diff(before_text, after_text),
        "validation": validation,
        "rollback_performed": False,
    }


def run_llm_exact_edit_editor(
    *,
    model_name: str,
    output_root: Path,
    targets: list[Path],
    task_prompt: str,
    max_attempts: int = 5,
    validate: ValidationCallback | None = None,
    max_targets: int | None = None,
    require_all_targets_changed: bool = False,
    progress: Callable[[str], None] | None = None,
    additive_only: bool = False,
    max_added_lines: int | None = None,
    max_operations: int | None = None,
) -> dict[str, Any]:
    """Run the isolated exact-edit LLM protocol against a stable snapshot."""
    root = output_root.resolve()
    configure_llm_invocation_journal(root, recover=False)
    snapshots = {path.resolve(): path.resolve().read_bytes() for path in targets}
    editable_files = [
        {
            "path": path.resolve().relative_to(root).as_posix(),
            "sha256": _sha256(snapshots[path.resolve()]),
            "content": _decode_snapshot(snapshots[path.resolve()])[0],
        }
        for path in targets
    ]
    prompt = (
        task_prompt.strip()
        + "\n\nReturn only JSON using schema exact-edits.v1:\n"
        + '{"schema_version":"exact-edits.v1","files":[{"path":"relative/path",'
        + '"expected_sha256":"supplied sha256","operations":[{"edit_id":"unique-id",'
        + '"kind":"replace_exact","old_text":"verbatim unique current text",'
        + '"new_text":"complete replacement"}]}],"summary":"brief"}\n'
        + "Use replace_entire_file only when the supplied target content is empty. "
        + "Every replace_exact old_text must be copied verbatim from the ORIGINAL supplied "
        + "content and match exactly once. Add enough unchanged surrounding text to make it "
        + "unique. Operations must not overlap. Do not output a diff, hunk coordinates, "
        + "Markdown fences, or edits outside editable_files.\n\n"
        + (
            "ADDITIVE-ONLY POLICY: Every operation must preserve old_text verbatim as "
            "one contiguous substring inside new_text. Insert only the minimum new "
            "instructions needed for the diagnosed defect. Do not delete, paraphrase, "
            "reorder, summarize, or rewrite existing text. "
            f"Use at most {max_operations} operations. "
            f"Add at most {max_added_lines} lines in total. "
            "If the repair conflicts with an existing instruction, do not remove that "
            "instruction; return no speculative rewrite.\n\n"
            if additive_only
            else ""
        )
        + json.dumps({"output_root": root.as_posix(), "editable_files": editable_files}, ensure_ascii=False)
    )
    attempts: list[dict[str, Any]] = []
    feedback: dict[str, Any] = {}
    emit = progress or (lambda _message: None)
    normal_attempt_limit = max(1, max_attempts)
    field_schema_candidate_fingerprints: set[str] = set()
    attempt = 0
    while True:
        attempt += 1
        for path, data in snapshots.items():
            path.write_bytes(data)
        attempt_prompt = prompt
        if feedback:
            attempt_prompt += (
                "\n\nPrevious exact edits were rejected. Regenerate a complete operation set "
                "against the original editable_files content:\n"
                + json.dumps(feedback, ensure_ascii=False)
            )
        target_names = ",".join(path.name for path in targets[:3])
        target_suffix = ",…" if len(targets) > 3 else ""
        attempt_limit_label = (
            "schema-feedback"
            if attempt > normal_attempt_limit
            else str(normal_attempt_limit)
        )
        emit(
            f"exact-edit attempt={attempt}/{attempt_limit_label} phase=generate "
            f"targets={target_names}{target_suffix}"
        )
        started = time.monotonic()
        candidate_payload: Any = None
        try:
            response = invoke_json(
                model_name,
                attempt_prompt,
                timeout_seconds=_env_int("TWA_PATCH_CALL_TIMEOUT", 300),
                max_attempts=1,
                provider_max_retries=0,
            )
            candidate_payload = response.data

            def validate_with_progress() -> dict[str, Any]:
                """Expose the otherwise opaque candidate-review interval."""
                emit(
                    f"exact-edit attempt={attempt}/{attempt_limit_label} "
                    "phase=review action=run-candidate-validation"
                )
                if validate is None:
                    return {"ok": True, "failures": [], "skipped": True}
                return validate()

            report = apply_exact_edit_payload(
                output_root=root,
                targets=targets,
                edit_payload=response.data,
                validate=validate_with_progress,
                max_changed_files=max_targets,
                additive_only=additive_only,
                max_added_lines=max_added_lines,
                max_operations=max_operations,
            )
            if report.get("ok") and require_all_targets_changed:
                expected = {
                    path.resolve().relative_to(root).as_posix() for path in targets
                }
                missing = sorted(expected - set(report.get("changed_files") or []))
                if missing:
                    for path, data in snapshots.items():
                        path.write_bytes(data)
                    report = {
                        **report,
                        "ok": False,
                        "failures": [
                            _edit_failure(
                                "llm_did_not_edit_all_targets",
                                paths=missing,
                            )
                        ],
                        "rollback_performed": True,
                    }
            record = {
                "attempt": attempt,
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "token_usage": response.token_usage,
                **report,
            }
        except Exception as exc:
            record = {
                "attempt": attempt,
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "ok": False,
                "failures": [
                    _edit_failure(
                        "llm_exact_edit_exception",
                        detail=f"{type(exc).__name__}:{exc}",
                    )
                ],
            }
        attempts.append(record)
        for message in _attempt_progress(record):
            emit(f"exact-edit attempt={attempt}/{attempt_limit_label} {message}")
        if record.get("ok"):
            return {**record, "attempts": attempts}
        failure_spec = _failure_spec(record.get("failures"))
        feedback = {
            "attempt": attempt,
            "failures": record.get("failures") or [],
            "validation": record.get("validation") or {},
            "failure_class": failure_spec["failure_class"],
            "retry_rules": [
                failure_spec["retry_hint"],
                "Use the original editable_files content, not a rejected candidate.",
                "Copy old_text verbatim and make it match exactly once.",
                "Merge or separate overlapping operations.",
            ],
        }
        unauthorized_paths = sorted(
            {
                str(failure.get("path") or "")
                for failure in (record.get("failures") or [])
                if isinstance(failure, dict)
                and failure.get("code") == "unauthorised_exact_edit_target"
                and str(failure.get("path") or "")
            }
        )
        if unauthorized_paths:
            allowed_paths = [item["path"] for item in editable_files]
            feedback["failure_class"] = "edit_protocol"
            feedback["unauthorized_paths"] = unauthorized_paths
            feedback["allowed_editable_paths"] = allowed_paths
            feedback["retry_rules"].insert(
                0,
                "Set every files[].path to one of allowed_editable_paths exactly, copied "
                "character-for-character. Do not use a basename, output-root-prefixed path, "
                "absolute path, or read-only dependency path.",
            )
        retry_hint = (record.get("validation") or {}).get("retry_hint")
        if retry_hint:
            feedback["retry_rules"].insert(0, str(retry_hint))
        field_schema_error = has_field_schema_error(record.get("validation") or {})
        if field_schema_error:
            fingerprint = semantic_candidate_fingerprint(candidate_payload)
            if fingerprint in field_schema_candidate_fingerprints:
                feedback["failures"] = [
                    _edit_failure(
                        "field_schema_retry_no_progress",
                        detail=(
                            "The same semantic candidate was rejected twice for "
                            "FIELD_SCHEMA_ERROR"
                        ),
                    ),
                    *(feedback.get("failures") or []),
                ]
                emit(
                    "exact-edit field-schema retry stopped: repeated semantic candidate"
                )
                break
            field_schema_candidate_fingerprints.add(fingerprint)
            feedback["retry_rules"].insert(
                0,
                "FIELD_SCHEMA_ERROR retries are not limited by the normal attempt budget. "
                "Apply the exact validator-requested field rename before changing behavior.",
            )
            continue
        if attempt >= normal_attempt_limit:
            break
    for path, data in snapshots.items():
        path.write_bytes(data)
    return {
        "ok": False,
        "backend": "pure_llm_exact_edits",
        "report_schema_version": 2,
        "replay_protocol": SCHEMA_VERSION,
        "failures": feedback.get("failures") or [_edit_failure("no_attempt")],
        "attempts": attempts,
    }
