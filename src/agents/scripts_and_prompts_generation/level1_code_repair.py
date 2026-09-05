"""Shared Level-1 (ruff / compile / machine-validation) repair helpers.

Used by the medical semantic MCP closed loop and reusable by JSON-patch flows.
"""

from __future__ import annotations

import json
import hashlib
import os
import re
import subprocess
import sys
import textwrap
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from models.LLMCreator import LLMCreator
from models.ModelConfig import ModelConfig
from models.llm_call_telemetry import (
    journal_path,
    summarize_costs,
    telemetry_context,
)
from src.agents.scripts_and_prompts_generation.agentic_generation_validation import (
    build_validation_report,
)
from src.agents.scripts_and_prompts_generation.llm_invocation_runtime import (
    LLMInvocationTimeout,
    append_invocation_event,
    invoke_with_hard_timeout,
    new_call_id,
)

_SCRIPT_FILE_RE = re.compile(r"^([A-Za-z0-9_.-]+\.(?:md|py)):\s*(.+)$", re.DOTALL)


@dataclass
class CheckResult:
    name: str
    ok: bool
    returncode: int = 0
    stdout: str = ""
    stderr: str = ""


@dataclass
class LLMJsonResult:
    data: dict[str, Any]
    elapsed_seconds: float
    token_usage: dict[str, Any]
    raw_response: str = ""
    actual_cost_usd: float | None = None
    generation_ids: list[str] | None = None
    cost_status: str = ""


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def escape_nul_bytes(text: str) -> str:
    return text.replace("\x00", "\\x00")


def run_command(
    args: list[str],
    cwd: Path,
    timeout: int = 120,
    env: dict[str, str] | None = None,
) -> CheckResult:
    completed = subprocess.run(
        args,
        cwd=str(cwd),
        text=True,
        capture_output=True,
        timeout=timeout,
        env=env,
    )
    return CheckResult(
        name=" ".join(args),
        ok=completed.returncode == 0,
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


def ensure_git_repo(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    if not (directory / ".git").exists():
        run_command(["git", "init", "-q"], cwd=directory)


def group_validation_failures(failures: list[str]) -> dict[str, list[str]]:
    """Map basename (e.g. main.py) -> machine validation messages."""
    by_file: dict[str, list[str]] = {}
    for raw in failures or []:
        line = raw.strip()
        if not line:
            continue
        match = _SCRIPT_FILE_RE.match(line)
        if match:
            by_file.setdefault(match.group(1), []).append(match.group(2).strip())
            continue
        prefix = "Foreign ontology symbols found:"
        if line.startswith(prefix):
            rest = line[len(prefix) :].strip()
            for segment in rest.split(";"):
                segment = segment.strip()
                if ": " not in segment:
                    continue
                fn, msg = segment.split(": ", 1)
                fn = fn.strip()
                if fn.endswith((".py", ".md")):
                    by_file.setdefault(fn, []).append(
                        f"foreign symbols / leakage: {msg.strip()}"
                    )
    return by_file


def _response_text(response: Any) -> str:
    content = getattr(response, "content", response)
    if isinstance(content, list):
        return "\n".join(str(item) for item in content)
    return str(content)


def _token_usage(response: Any) -> dict[str, Any]:
    usage = getattr(response, "usage_metadata", None)
    if isinstance(usage, dict) and usage:
        return usage
    metadata = getattr(response, "response_metadata", None)
    if isinstance(metadata, dict):
        token_usage = metadata.get("token_usage")
        if isinstance(token_usage, dict):
            return token_usage
    return {}


def _cost_telemetry_reference(
    response: Any, *, parent_call_id: str | None = None
) -> dict[str, Any]:
    """Attach billed OpenRouter cost from the completion and the cost journal."""
    try:
        from models.llm_call_telemetry import extract_response_metadata

        metadata = extract_response_metadata(response)
        costs = (
            summarize_costs(journal_path(), parent_call_id)
            if parent_call_id
            else {"actual_cost_usd": None, "generation_ids": [], "pending_calls": 0}
        )
        generation_ids = list(costs.get("generation_ids") or [])
        generation_id = metadata.get("generation_id") or None
        if generation_id and generation_id not in generation_ids:
            generation_ids.append(generation_id)
        actual_cost = (
            costs.get("actual_cost_usd")
            if costs.get("billable_calls")
            else metadata.get("inline_cost")
        )
        if costs.get("pending_calls"):
            status = "pending"
        elif actual_cost is not None:
            status = "resolved"
        else:
            status = "unavailable"
        return {
            "generation_id": generation_id,
            "generation_ids": generation_ids,
            "actual_cost_usd": actual_cost,
            "cost_status": status,
            "cost_journal": str(journal_path()),
        }
    except Exception:
        return {
            "generation_id": None,
            "generation_ids": [],
            "actual_cost_usd": None,
            "cost_status": "unavailable",
            "cost_journal": str(journal_path()),
        }


def extract_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start >= 0 and end > start:
            data = json.loads(stripped[start : end + 1])
        else:
            # Models sometimes emit the object body without the outer braces.
            data = json.loads("{" + stripped + "}")
    if not isinstance(data, dict):
        raise ValueError("LLM response must be a JSON object")
    return data


def _generation_timeout_disabled() -> bool:
    return os.environ.get("TWA_DISABLE_GENERATION_TIMEOUT", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def invoke_json(
    model: str,
    prompt: str,
    *,
    timeout_seconds: int | None = None,
    max_attempts: int = 3,
    provider_max_retries: int | None = None,
    temperature: float = 0.0,
) -> LLMJsonResult:
    last_detail = ""
    attempts = max(1, max_attempts)
    invocation_id = new_call_id()
    timeout_disabled = _generation_timeout_disabled()
    # HTTP client still needs a bound; 24h is effectively unlimited for generation.
    http_timeout = (
        86400
        if timeout_disabled
        else (timeout_seconds or _env_int("TWA_GENERATION_TIMEOUT", 600))
    )
    effective_timeout = None if timeout_disabled else http_timeout
    for attempt in range(1, attempts + 1):
        llm = LLMCreator(
            model=model,
            remote_model=True,
            model_config=ModelConfig(
                timeout=http_timeout,
                temperature=temperature,
                top_p=0.1,
                max_retries=provider_max_retries,
            ),
        ).setup_llm()
        effective_prompt = prompt
        if attempt > 1:
            effective_prompt = (
                prompt
                + "\n\nPrevious response was not parseable JSON. Return only one valid JSON object "
                + "with the exact requested keys. Escape all newlines and quotes inside JSON string values. "
                + f"Previous parse error detail: {last_detail}"
            )
        started = time.perf_counter()
        append_invocation_event(
            {
                "call_id": invocation_id,
                "event": "started",
                "attempt": attempt,
                "model": model,
                "timeout_seconds": effective_timeout,
                "prompt_sha256": hashlib.sha256(
                    effective_prompt.encode("utf-8")
                ).hexdigest(),
            }
        )
        try:
            with telemetry_context(
                invocation_id,
                {"component": "invoke_json", "model": model, "attempt": attempt},
            ):
                response = invoke_with_hard_timeout(
                    lambda: llm.invoke(effective_prompt),
                    timeout_seconds=effective_timeout,
                )
        except LLMInvocationTimeout:
            append_invocation_event(
                {
                    "call_id": invocation_id,
                    "event": "timed_out",
                    "attempt": attempt,
                    "model": model,
                    "elapsed_seconds": time.perf_counter() - started,
                }
            )
            raise
        except BaseException as exc:
            append_invocation_event(
                {
                    "call_id": invocation_id,
                    "event": "failed",
                    "attempt": attempt,
                    "model": model,
                    "error_type": type(exc).__name__,
                    "error": str(exc)[:1000],
                    "elapsed_seconds": time.perf_counter() - started,
                }
            )
            raise
        elapsed = time.perf_counter() - started
        raw = _response_text(response)
        cost_reference = _cost_telemetry_reference(
            response, parent_call_id=invocation_id
        )
        try:
            data = extract_json_object(raw)
        except (json.JSONDecodeError, ValueError) as exc:
            preview = (raw or "").replace("\r", "")[:800]
            last_detail = f"len={len(raw or '')} preview={preview!r}"
            append_invocation_event(
                {
                    "call_id": invocation_id,
                    "event": "invalid_response",
                    "attempt": attempt,
                    "model": model,
                    "elapsed_seconds": elapsed,
                    "detail": last_detail,
                    **cost_reference,
                }
            )
            if attempt == attempts:
                raise RuntimeError(
                    f"LLM did not return a JSON object ({last_detail})"
                ) from exc
            continue
        result = LLMJsonResult(
            data=data,
            elapsed_seconds=elapsed,
            token_usage=_token_usage(response),
            raw_response=raw,
            actual_cost_usd=cost_reference.get("actual_cost_usd"),
            generation_ids=list(cost_reference.get("generation_ids") or []),
            cost_status=str(cost_reference.get("cost_status") or ""),
        )
        append_invocation_event(
            {
                "call_id": invocation_id,
                "event": "completed",
                "attempt": attempt,
                "model": model,
                "elapsed_seconds": elapsed,
                "token_usage": result.token_usage,
                **cost_reference,
            }
        )
        return result
    raise RuntimeError("LLM did not return a JSON object")


def check_python_file(path: Path) -> list[CheckResult]:
    scripts_dir = path.parent.resolve()
    return [
        run_command(
            [sys.executable, "-m", "ruff", "format", "--check", path.name],
            cwd=scripts_dir,
        ),
        run_command(
            [sys.executable, "-m", "ruff", "check", path.name], cwd=scripts_dir
        ),
        run_command(
            [
                sys.executable,
                "-c",
                (
                    "import ast,pathlib;"
                    f"ast.parse(pathlib.Path({path.name!r}).read_text(encoding='utf-8'))"
                ),
            ],
            cwd=scripts_dir,
        ),
    ]


def apply_unified_diff(directory: Path, file_name: str, patch_unified_diff: str) -> CheckResult:
    ensure_git_repo(directory)
    patch_path = directory / f"{file_name}.repair.patch"
    patch_path.write_text(
        escape_nul_bytes(patch_unified_diff).rstrip() + "\n", encoding="utf-8"
    )
    return run_command(
        ["git", "apply", "--recount", "--whitespace=nowarn", patch_path.name],
        cwd=directory,
    )


def _repair_prompt(file_name: str, feedback: str) -> str:
    return textwrap.dedent(
        f"""
        You are repairing `{file_name}` in the Level-1 code repair loop.

        Return only a valid JSON object with this exact shape:
        {{"patch_unified_diff": "<unified diff usable by git apply>"}}

        Requirements:
        - Patch `{file_name}` only.
        - Use standard unified diff with a/{file_name} and b/{file_name} paths.
        - The patch must start with `diff --git a/{file_name} b/{file_name}`.
        - Do not use Cursor/ApplyPatch format. Never output `*** Begin Patch`.
        - Prefer the smallest possible patch that fixes ruff/import/syntax/contract issues.
        - Return only JSON. No Markdown fences, no explanations, no extra keys.

        Validation feedback:
        {feedback}
        """
    ).strip()


def _file_feedback(results: list[CheckResult], path: Path) -> str:
    return "\n\n".join(
        [
            f"Current file contents for {path.name}:\n```\n"
            f"{path.read_text(encoding='utf-8', errors='replace')}\n```",
            "Check results:",
            *[
                json.dumps(
                    {
                        "name": result.name,
                        "ok": result.ok,
                        "returncode": result.returncode,
                        "stdout": result.stdout[-4000:],
                        "stderr": result.stderr[-4000:],
                    },
                    indent=2,
                )
                for result in results
            ],
        ]
    )


def repair_python_file_with_llm(
    *,
    model: str,
    path: Path,
    max_repairs: int,
    sticky_feedback: str,
) -> dict[str, Any]:
    """LLM unified-diff repair until ruff/compile pass or budget exhausted."""
    started = time.perf_counter()
    directory = path.parent
    ensure_git_repo(directory)
    llm_calls: list[dict[str, Any]] = []
    history: list[dict[str, Any]] = []
    results = check_python_file(path)
    history.append({"phase": "baseline", "ok": all(item.ok for item in results)})
    repairs = 0
    while not all(item.ok for item in results) and repairs < max_repairs:
        repairs += 1
        prompt = _repair_prompt(
            path.name, sticky_feedback + "\n\n" + _file_feedback(results, path)
        )
        repair = invoke_json(model, prompt)
        llm_calls.append(
            {
                "phase": f"repair_{repairs}",
                "elapsed_seconds": round(repair.elapsed_seconds, 3),
                "token_usage": repair.token_usage,
            }
        )
        patch_unified_diff = repair.data.get("patch_unified_diff")
        if not isinstance(patch_unified_diff, str) or not patch_unified_diff.strip():
            history.append(
                {"phase": f"repair_{repairs}", "patch_applied": False, "ok": False}
            )
            break
        patch_result = apply_unified_diff(directory, path.name, patch_unified_diff)
        if not patch_result.ok:
            results = [*results, patch_result]
            history.append(
                {"phase": f"repair_{repairs}", "patch_applied": False, "ok": False}
            )
            continue
        results = check_python_file(path)
        history.append(
            {
                "phase": f"repair_{repairs}",
                "patch_applied": True,
                "ok": all(item.ok for item in results),
            }
        )
    return {
        "file": str(path),
        "ok": all(item.ok for item in results),
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "repairs": repairs,
        "llm_calls": llm_calls,
        "history": history,
        "final_checks": [
            {
                "name": result.name,
                "ok": result.ok,
                "returncode": result.returncode,
                "stdout": result.stdout[-2000:],
                "stderr": result.stderr[-2000:],
            }
            for result in results
        ],
    }


def _semantic_repair_prompt(file_name: str, feedback: str) -> str:
    return textwrap.dedent(
        f"""
        You are repairing `{file_name}` for a **semantic / reasoner** failure.
        The file may already pass ruff and py_compile — that is not enough.

        Return only a valid JSON object with this exact shape:
        {{"patch_unified_diff": "<unified diff usable by git apply>"}}

        Requirements:
        - Patch `{file_name}` only.
        - Use standard unified diff with a/{file_name} and b/{file_name} paths.
        - The patch must start with `diff --git a/{file_name} b/{file_name}`.
        - Do not use Cursor/ApplyPatch format. Never output `*** Begin Patch`.
        - Fix the ontology/property defect described in the feedback (restore valid
          T-Box property locals; remove invented property names).
        - Keep the file ruff/py_compile clean after the patch.
        - Prefer the smallest possible patch.
        - Return only JSON. No Markdown fences, no explanations, no extra keys.

        Semantic / reasoner feedback:
        {feedback}
        """
    ).strip()


def repair_python_file_with_llm_for_goal(
    *,
    model: str,
    path: Path,
    max_repairs: int,
    sticky_feedback: str,
    goal_met: Callable[[Path], bool],
) -> dict[str, Any]:
    """Force LLM patches until ``goal_met(path)`` even when ruff already passes.

    Used for non-trivial semantic defects that do not fail Level-1 lint/compile.
    """
    started = time.perf_counter()
    directory = path.parent
    ensure_git_repo(directory)
    llm_calls: list[dict[str, Any]] = []
    history: list[dict[str, Any]] = []
    results = check_python_file(path)
    met = bool(goal_met(path))
    history.append(
        {
            "phase": "baseline",
            "ruff_ok": all(item.ok for item in results),
            "goal_met": met,
        }
    )
    repairs = 0
    while (not met or not all(item.ok for item in results)) and repairs < max_repairs:
        repairs += 1
        prompt = _semantic_repair_prompt(
            path.name,
            sticky_feedback
            + "\n\n"
            + _file_feedback(results, path)
            + f"\n\nGoal currently met: {met}",
        )
        repair = invoke_json(model, prompt)
        llm_calls.append(
            {
                "phase": f"repair_{repairs}",
                "elapsed_seconds": round(repair.elapsed_seconds, 3),
                "token_usage": repair.token_usage,
            }
        )
        patch_unified_diff = repair.data.get("patch_unified_diff")
        if not isinstance(patch_unified_diff, str) or not patch_unified_diff.strip():
            history.append(
                {
                    "phase": f"repair_{repairs}",
                    "patch_applied": False,
                    "goal_met": met,
                    "ok": False,
                }
            )
            break
        patch_result = apply_unified_diff(directory, path.name, patch_unified_diff)
        if not patch_result.ok:
            results = [*results, patch_result]
            history.append(
                {
                    "phase": f"repair_{repairs}",
                    "patch_applied": False,
                    "goal_met": met,
                    "ok": False,
                }
            )
            continue
        results = check_python_file(path)
        met = bool(goal_met(path))
        history.append(
            {
                "phase": f"repair_{repairs}",
                "patch_applied": True,
                "ruff_ok": all(item.ok for item in results),
                "goal_met": met,
                "ok": met and all(item.ok for item in results),
            }
        )
    return {
        "file": str(path),
        "ok": met and all(item.ok for item in results),
        "goal_met": met,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "repairs": repairs,
        "llm_calls": llm_calls,
        "history": history,
        "final_checks": [
            {
                "name": result.name,
                "ok": result.ok,
                "returncode": result.returncode,
                "stdout": result.stdout[-2000:],
                "stderr": result.stderr[-2000:],
            }
            for result in results
        ],
    }


def run_ruff_on_scripts(scripts_dir: Path) -> dict[str, Any]:
    """Lint/compile every script; report formatting differences as advisory."""
    results: list[dict[str, Any]] = []
    ok = True
    for path in sorted(scripts_dir.glob("*.py")):
        if path.name.startswith("main_part_") or "_attempt_" in path.name:
            continue
        file_results = check_python_file(path)
        blocking_results = [
            item for item in file_results if "ruff format --check" not in item.name
        ]
        format_results = [
            item for item in file_results if "ruff format --check" in item.name
        ]
        file_ok = all(item.ok for item in blocking_results)
        format_ok = all(item.ok for item in format_results)
        ok = ok and file_ok
        results.append(
            {
                "file": path.name,
                "ok": file_ok,
                "format_ok": format_ok,
                "format_advisory_only": True,
                "checks": [
                    {
                        "name": item.name,
                        "ok": item.ok,
                        "blocking": "ruff format --check" not in item.name,
                        "stdout": item.stdout[-1000:],
                        "stderr": item.stderr[-1000:],
                    }
                    for item in file_results
                ],
            }
        )
    return {"ok": ok, "files": results}


def autofix_ruff_on_scripts(scripts_dir: Path) -> dict[str, Any]:
    """Non-LLM Level-1: ruff format + ruff check --fix on all package scripts."""
    applied: list[dict[str, Any]] = []
    for path in sorted(scripts_dir.glob("*.py")):
        if path.name.startswith("main_part_") or "_attempt_" in path.name:
            continue
        fmt = run_command(
            [sys.executable, "-m", "ruff", "format", path.name], cwd=scripts_dir
        )
        fix = run_command(
            [sys.executable, "-m", "ruff", "check", "--fix", path.name],
            cwd=scripts_dir,
        )
        applied.append(
            {
                "file": path.name,
                "format_ok": fmt.ok,
                "fix_ok": fix.ok,
                "fix_stdout": fix.stdout[-1000:],
                "fix_stderr": fix.stderr[-1000:],
            }
        )
    recheck = run_ruff_on_scripts(scripts_dir)
    return {"applied": applied, "recheck": recheck}


def level1_repair_loop(
    *,
    context: Any,
    model: str,
    max_ruff_repairs: int,
    allow_llm: bool,
    log: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Run ruff/compile then machine validation with optional LLM patch repairs."""
    _log = log or (lambda _msg: None)
    scripts_dir = Path(context.scripts_dir).resolve()
    prompts_dir = Path(context.prompts_dir).resolve()
    ensure_git_repo(scripts_dir)
    if prompts_dir.is_dir():
        ensure_git_repo(prompts_dir)

    history: list[dict[str, Any]] = []
    ruff_report = run_ruff_on_scripts(scripts_dir)
    history.append({"phase": "ruff_initial", "ok": ruff_report["ok"], "report": ruff_report})

    if not ruff_report["ok"] and allow_llm and max_ruff_repairs > 0:
        from src.agents.scripts_and_prompts_generation.llm_artifact_editor import (
            run_llm_artifact_editor,
        )

        targets = [
            path
            for path in sorted(scripts_dir.glob("*.py"))
            if not path.name.startswith("main_part_") and "_attempt_" not in path.name
        ]
        _log("[level1] plain LLM decides transactional lint/syntax repair")
        repair = run_llm_artifact_editor(
            model_name=model,
            output_root=Path(context.output_root),
            targets=targets,
            task_prompt=(
                "Diagnose and repair all Python formatting, lint, import, and syntax failures "
                "shown below. Decide which files need changes. Preserve generated ontology "
                "behavior and make the smallest coherent patch. No formatter or scripted "
                "autofix will modify content for you.\n\n"
                + json.dumps(ruff_report, ensure_ascii=False)
            ),
            max_attempts=5,
        )
        history.append({"phase": "ruff_llm_repair", **repair})
        ruff_report = run_ruff_on_scripts(scripts_dir)

    validation_report = build_validation_report(
        context, foreign_contracts=None, write_report=True
    )
    history.append(
        {
            "phase": "validation_initial",
            "ok": bool(validation_report.get("ok")),
            "failures": list(validation_report.get("failures") or []),
        }
    )

    repairs_done = 0
    while (
        not validation_report.get("ok")
        and allow_llm
        and repairs_done < max_ruff_repairs
    ):
        failures = list(validation_report.get("failures") or [])
        repairs_done += 1
        full_failures = "\n".join(failures)
        fb = validation_report.get("feedback") or {}
        extra = "\n".join(
            (fb.get("prompt_agent") or []) + (fb.get("coding_agent") or [])
        )
        from src.agents.scripts_and_prompts_generation.llm_artifact_editor import (
            run_llm_artifact_editor,
        )

        targets = [
            path
            for path in (
                *sorted(scripts_dir.glob("*.py")),
                *sorted(prompts_dir.glob("*.md")),
            )
            if path.is_file()
            and not path.name.startswith("main_part_")
            and "_attempt_" not in path.name
        ]
        _log("[level1] plain LLM decides bundle-validation repair targets")
        round_repair = run_llm_artifact_editor(
            model_name=model,
            output_root=Path(context.output_root),
            targets=targets,
            task_prompt=(
                "Diagnose the complete machine-validation bundle and decide which generated "
                "scripts or prompts require changes. Produce the smallest coherent edits. "
                "Do not use filename matching or assume every file needs an edit.\n\n"
                "Full failures:\n"
                + full_failures
                + "\n\nStructured feedback:\n"
                + extra
            ),
            max_attempts=5,
        )
        history.append(
            {"phase": f"validation_repair_{repairs_done}", "repair": round_repair}
        )
        ruff_report = run_ruff_on_scripts(scripts_dir)
        history.append(
            {
                "phase": f"ruff_after_validation_{repairs_done}",
                "ok": ruff_report["ok"],
            }
        )
        validation_report = build_validation_report(
            context, foreign_contracts=None, write_report=True
        )
        history.append(
            {
                "phase": f"validation_after_repair_{repairs_done}",
                "ok": bool(validation_report.get("ok")),
                "failures": list(validation_report.get("failures") or []),
            }
        )

    final_ruff = run_ruff_on_scripts(scripts_dir)
    # Ruff remains diagnostic evidence, while syntax/import/contract/runtime
    # failures are enforced by the generation validation report below. This
    # prevents style-only findings from blocking behavioral evaluation.
    ok = bool(validation_report.get("ok"))
    return {
        "ok": ok,
        "ruff": final_ruff,
        "ruff_advisory_only": True,
        "validation": {
            "ok": bool(validation_report.get("ok")),
            "failures": list(validation_report.get("failures") or []),
            "warnings": list(validation_report.get("warnings") or []),
            "report_path": getattr(context, "report_path", None),
        },
        "history": history,
    }
