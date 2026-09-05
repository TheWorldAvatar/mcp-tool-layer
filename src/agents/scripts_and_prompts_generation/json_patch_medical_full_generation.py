from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import textwrap
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from models.LLMCreator import LLMCreator
from models.ModelConfig import ModelConfig
from src.agents.scripts_and_prompts_generation.agentic_generation_context import (
    build_agentic_generation_context,
)
from src.agents.scripts_and_prompts_generation.agentic_generation_runner import (
    generate_deterministic_prompt_slice,
    generate_deterministic_script_slice,
)
from src.agents.scripts_and_prompts_generation.agentic_generation_validation import (
    build_validation_report,
)
from src.agents.scripts_and_prompts_generation.json_patch_medical_one_script import (
    _run_checks as _run_checks_golden_script,
)


DEFAULT_OUTPUT_ROOT = Path("ai_generated_contents_agent_candidate_json_medical_full")
_SCRIPT_FILE_RE = re.compile(r"^([A-Za-z0-9_.-]+\.(?:md|py)):\s*(.+)$", re.DOTALL)
SCRIPT_FILES = (
    "medical_creation_base.py",
    "medical_creation_checks.py",
    "medical_creation_entities.py",
    "medical_creation_relationships.py",
    "main.py",
)


def _log(msg: str) -> None:
    """Progress to stderr so stdout can stay JSON-only for piping."""
    print(msg, file=sys.stderr, flush=True)


def _escape_nul_bytes(text: str) -> str:
    """Keep generated text source-safe when an LLM emits a literal NUL."""
    return text.replace("\x00", "\\x00")


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def _group_validation_failures(failures: list[str]) -> dict[str, list[str]]:
    """Map basename (e.g. KG_BUILDING_ITER_1.md) -> machine validation messages."""
    by_file: dict[str, list[str]] = {}
    for raw in failures or []:
        line = raw.strip()
        if not line:
            continue
        m = _SCRIPT_FILE_RE.match(line)
        if m:
            by_file.setdefault(m.group(1), []).append(m.group(2).strip())
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
                    by_file.setdefault(fn, []).append(f"foreign symbols / leakage: {msg.strip()}")
    return by_file


def _validation_repair_instruction(path: Path, feedback: str) -> str:
    return textwrap.dedent(
        f"""
        You are repairing `{path.name}` after **machine validation** of the full medical artifact bundle failed.

        Return only a valid JSON object with this exact shape:
        {{"patch_unified_diff": "<unified diff usable by git apply>"}}

        Requirements:
        - Patch `{path.name}` only. Fix every issue described in the validation feedback below.
        - Use standard unified diff with a/{path.name} and b/{path.name} paths.
        - The patch must start with `diff --git a/{path.name} b/{path.name}`.
        - Do not use Cursor/ApplyPatch format.
        - Prefer the smallest patch that makes prompts free of forbidden template residue and restores required contract phrases.
        IMPORTANT for markdown:
        - Remove TODO/FIXME, `{{{{...}}}}`, and angle-bracket shims like `<case_label>` / `<json>`; use plain prose or concrete example values.
        - Keep required contracts from the scaffold (Materializable Hint Contract, medical CSV round-trip block where applicable, etc.).
        - Return only JSON. No Markdown fences, no explanations, no extra keys.

        Validation feedback:
        {feedback}
        """
    ).strip()


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


def _extract_json_object(text: str) -> dict[str, Any]:
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
        if start < 0 or end <= start:
            raise
        data = json.loads(stripped[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError("LLM response must be a JSON object")
    return data


def _invoke_json(model: str, prompt: str) -> LLMJsonResult:
    last_detail = ""
    for attempt in range(1, 4):
        llm = LLMCreator(
            model=model,
            remote_model=True,
            model_config=ModelConfig(
                max_tokens=_env_int("TWA_GENERATION_MAX_TOKENS", 32000),
                timeout=_env_int("TWA_GENERATION_TIMEOUT", 600),
                temperature=0,
                top_p=0.1,
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
        response = llm.invoke(effective_prompt)
        elapsed = time.perf_counter() - started
        raw = _response_text(response)
        try:
            data = _extract_json_object(raw)
        except (json.JSONDecodeError, ValueError) as exc:
            preview = (raw or "").replace("\r", "")[:800]
            last_detail = f"len={len(raw or '')} preview={preview!r}"
            if attempt == 3:
                raise RuntimeError(f"LLM did not return a JSON object ({last_detail})") from exc
            continue
        return LLMJsonResult(
            data=data,
            elapsed_seconds=elapsed,
            token_usage=_token_usage(response),
        )
    raise RuntimeError("LLM did not return a JSON object")


def _run_command(args: list[str], cwd: Path, timeout: int = 120, env: dict[str, str] | None = None) -> CheckResult:
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


def _prepare_scaffolds(output_root: Path, meta_task_config_path: Path | None = None):
    _log(f"[scaffold] ontology structures + deterministic slices → {output_root}")
    context = build_agentic_generation_context(
        ontology_name="medical",
        meta_task_config_path=meta_task_config_path,
        output_root=output_root,
        write_files=True,
    )
    generate_deterministic_script_slice(context)
    generate_deterministic_prompt_slice(context)
    scripts_dir = Path(context.scripts_dir).resolve()
    if not (scripts_dir / ".git").exists():
        _run_command(["git", "init", "-q"], cwd=scripts_dir)
    prompts_dir = Path(context.prompts_dir).resolve()
    if not (prompts_dir / ".git").exists():
        _run_command(["git", "init", "-q"], cwd=prompts_dir)
    return context


def _context_brief(context) -> str:
    classes = sorted((context.parsed.get("classes") or {}).keys())
    properties = sorted((context.parsed.get("properties") or {}).keys())
    return json.dumps(
        {
            "ontology": context.ontology.name,
            "ttl_file": context.ontology.ttl_file,
            "namespace_uri": context.contract.get("namespace_uri"),
            "top_entity": context.contract.get("top_entity"),
            "class_count": len(classes),
            "property_count": len(properties),
            "classes": classes,
            "properties": properties,
        },
        indent=2,
        ensure_ascii=False,
    )


def _script_generation_prompt(context, file_name: str, scaffold: str) -> str:
    return textwrap.dedent(
        f"""
        You are the coding agent for full medical generated MCP script generation.

        Return only a valid JSON object with this exact shape:
        {{"python_source": "<complete Python source for {file_name}>"}}

        Generate `{file_name}` for the medical ontology package. Use only the TTL-derived context below and the
        scaffold source as the implementation shape. Do not add domain facts outside the TTL-derived context.

        Hard requirements:
        - Preserve the public API surface expected by the scaffold unless validation feedback explicitly requires a fix.
        - Use package-relative imports compatible with the generated medical package.
        - `medical_creation_relationships.py` must pass `ruff check` with no unused-import (F401) violations.
        - Keep functions deterministic and T-Box-driven.
        - Include `from __future__ import annotations` for non-empty Python files.
        - For `medical_creation_checks.py`, every checker must have an explicit `-> str` return annotation.
        - Return only JSON. No Markdown fences, no explanations, no extra keys.

        TTL-derived context:
        { _context_brief(context) }

        Scaffold source for `{file_name}`:
        ```python
        {scaffold}
        ```
        """
    ).strip()


def _prompt_generation_prompt(context, file_name: str, scaffold: str) -> str:
    return textwrap.dedent(
        f"""
        You are the prompt agent for full medical prompt generation.

        Return only a valid JSON object with this exact shape:
        {{"markdown_source": "<complete Markdown source for {file_name}>"}}

        Generate `{file_name}` for the medical ontology pipeline. Use only the TTL-derived context below and the
        scaffold prompt as the implementation shape. Do not add domain facts outside the TTL-derived context.

        Hard requirements:
        - Preserve all machine-facing contracts already present in the scaffold.
        - Preserve the `T-Box Comment Fidelity Contract:` section when present in the scaffold, and keep
          T-Box class/property `comment=` rows as normative extraction constraints.
        - Preserve `Datatype Properties:` and `Object Properties:` sections when present in the scaffold; do not
          summarize away property comments.
        - Preserve the pipeline output format required by the scaffold.
        - Keep instructions T-Box-driven and domain-agnostic beyond ontology symbols/comments from the TTL.
        - No TODO/FIXME/template placeholders.
        - Return only JSON. No Markdown fences, no explanations, no extra keys.

        TTL-derived context:
        { _context_brief(context) }

        Scaffold prompt for `{file_name}`:
        ```markdown
        {scaffold}
        ```
        """
    ).strip()


def _repair_prompt(file_name: str, feedback: str) -> str:
    return textwrap.dedent(
        f"""
        You are repairing `{file_name}` in the full medical generation loop.

        Return only a valid JSON object with this exact shape:
        {{"patch_unified_diff": "<unified diff usable by git apply>"}}

        Requirements:
        - Patch `{file_name}`.
        - Use standard unified diff with a/{file_name} and b/{file_name} paths.
        - The patch must start with `diff --git a/{file_name} b/{file_name}`.
        - Do not use Cursor/ApplyPatch format. Never output `*** Begin Patch`, `*** End Patch`, or `*** Update File`.
        - Prefer the smallest possible patch.
        - Return only JSON. No Markdown fences, no explanations, no extra keys.

        Validation feedback:
        {feedback}
        """
    ).strip()


def _check_python_file(context, path: Path) -> list[CheckResult]:
    scripts_dir = Path(context.scripts_dir).resolve()
    results = [
        _run_command([sys.executable, "-m", "ruff", "format", path.name], cwd=scripts_dir),
        _run_command([sys.executable, "-m", "ruff", "check", path.name], cwd=scripts_dir),
        _run_command([sys.executable, "-m", "py_compile", path.name], cwd=scripts_dir),
    ]
    if path.name == "medical_creation_checks.py":
        classes = sorted((context.parsed.get("classes") or {}).keys())
        results.extend(_run_checks_golden_script(scripts_dir, path, classes))
    return results


def _check_prompt_file(path: Path) -> list[CheckResult]:
    text = path.read_text(encoding="utf-8", errors="replace")
    failures = []
    if not text.strip():
        failures.append("prompt is empty")
    for marker in ("TODO", "FIXME", "{{", "}}"):
        if marker in text:
            failures.append(f"prompt contains template residue marker `{marker}`")
    return [
        CheckResult(
            name=f"prompt basic check {path.name}",
            ok=not failures,
            stdout=f"{len(text)} chars",
            stderr="\n".join(failures),
        )
    ]


def _feedback(results: list[CheckResult], path: Path) -> str:
    return "\n\n".join(
        [
            f"Current file contents for {path.name}:\n```\n{path.read_text(encoding='utf-8', errors='replace')}\n```",
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


def _apply_patch(directory: Path, file_name: str, patch_unified_diff: str) -> CheckResult:
    patch_path = directory / f"{file_name}.repair.patch"
    patch_text = _escape_nul_bytes(patch_unified_diff)
    patch_path.write_text(patch_text.rstrip() + "\n", encoding="utf-8")
    return _run_command(
        ["git", "apply", "--recount", "--whitespace=nowarn", patch_path.name],
        cwd=directory,
    )


def _repair_one_file_after_machine_validation(
    *,
    model: str,
    context,
    path: Path,
    kind: str,
    max_repairs: int,
    sticky_machine_feedback: str,
) -> dict[str, Any]:
    """Patch a file for machine-validation feedback, even when local checks pass.

    Bundle-level validation can fail on contracts that the lightweight per-file
    checks do not know about. Always attempt at least one patch for mapped
    machine-validation failures, then use local checks to catch bad patches.
    """
    started = time.perf_counter()
    check_fn = (lambda: _check_python_file(context, path)) if kind == "script" else (lambda: _check_prompt_file(path))
    directory = path.parent
    llm_calls: list[dict[str, Any]] = []
    history: list[dict[str, Any]] = []
    results = check_fn()
    history.append({"phase": "validation_machine_baseline", "ok": all(item.ok for item in results)})
    repairs = 0
    while (repairs == 0 or not all(item.ok for item in results)) and repairs < max_repairs:
        repairs += 1
        prompt = (
            _validation_repair_instruction(path, sticky_machine_feedback)
            if repairs == 1
            else _repair_prompt(path.name, sticky_machine_feedback + "\n\n" + _feedback(results, path))
        )
        repair = _invoke_json(model, prompt)
        llm_calls.append(
            {
                "phase": f"validation_bundle_repair_{repairs}",
                "elapsed_seconds": round(repair.elapsed_seconds, 3),
                "token_usage": repair.token_usage,
            }
        )
        patch_unified_diff = repair.data.get("patch_unified_diff")
        if not isinstance(patch_unified_diff, str) or not patch_unified_diff.strip():
            raise ValueError(f"Validation repair for {path.name} did not contain `patch_unified_diff`")
        patch_result = _apply_patch(directory, path.name, patch_unified_diff)
        if not patch_result.ok:
            results = [*results, patch_result]
            history.append({"phase": f"validation_bundle_repair_{repairs}", "patch_applied": False, "ok": False})
            continue
        results = check_fn()
        history.append({"phase": f"validation_bundle_repair_{repairs}", "patch_applied": True, "ok": all(item.ok for item in results)})
    return {
        "file": str(path),
        "kind": kind,
        "repair_kind": "validation_machine",
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
                "stdout": result.stdout,
                "stderr": result.stderr,
            }
            for result in results
        ],
    }


def _generate_file(
    *,
    model: str,
    context,
    path: Path,
    kind: str,
    max_repairs: int,
) -> dict[str, Any]:
    started = time.perf_counter()
    _log(f"[gen] START {kind} {path.name}")
    scaffold = path.read_text(encoding="utf-8", errors="replace")
    prompt = (
        _script_generation_prompt(context, path.name, scaffold)
        if kind == "script"
        else _prompt_generation_prompt(context, path.name, scaffold)
    )
    llm_calls: list[dict[str, Any]] = []
    _log(f"[gen] LLM initial_generation → {path.name}")
    generation = _invoke_json(model, prompt)
    llm_calls.append(
        {
            "phase": "initial_generation",
            "elapsed_seconds": round(generation.elapsed_seconds, 3),
            "token_usage": generation.token_usage,
        }
    )
    key = "python_source" if kind == "script" else "markdown_source"
    source = generation.data.get(key)
    if not isinstance(source, str):
        raise ValueError(f"LLM response for {path.name} did not contain `{key}`")
    source = _escape_nul_bytes(source)
    path.write_text(source.rstrip() + "\n", encoding="utf-8")

    check_fn = (lambda: _check_python_file(context, path)) if kind == "script" else (lambda: _check_prompt_file(path))
    results = check_fn()
    history: list[dict[str, Any]] = [{"phase": "initial", "ok": all(item.ok for item in results)}]
    repairs = 0
    directory = path.parent
    while not all(item.ok for item in results) and repairs < max_repairs:
        repairs += 1
        _log(f"[gen] repair {repairs}/{max_repairs} → {path.name}")
        repair = _invoke_json(model, _repair_prompt(path.name, _feedback(results, path)))
        llm_calls.append(
            {
                "phase": f"repair_{repairs}",
                "elapsed_seconds": round(repair.elapsed_seconds, 3),
                "token_usage": repair.token_usage,
            }
        )
        patch_unified_diff = repair.data.get("patch_unified_diff")
        if not isinstance(patch_unified_diff, str) or not patch_unified_diff.strip():
            raise ValueError(f"Repair LLM response for {path.name} did not contain `patch_unified_diff`")
        patch_result = _apply_patch(directory, path.name, patch_unified_diff)
        if not patch_result.ok:
            results = [*results, patch_result]
            history.append({"phase": f"repair_{repairs}", "patch_applied": False, "ok": False})
            continue
        results = check_fn()
        history.append({"phase": f"repair_{repairs}", "patch_applied": True, "ok": all(item.ok for item in results)})

    ok = all(item.ok for item in results)
    _log(f"[gen] DONE {kind} {path.name} ok={ok} repairs={repairs} ({round(time.perf_counter() - started, 1)}s)")
    return {
        "file": str(path),
        "kind": kind,
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
                "stdout": result.stdout,
                "stderr": result.stderr,
            }
            for result in results
        ],
    }


def _run_golden_tests(
    scripts_dir: Path, meta_task_config_path: Path | None = None
) -> CheckResult:
    env = os.environ.copy()
    env["MEDICAL_CHECKS_SCRIPT_DIR"] = str(scripts_dir)
    if meta_task_config_path is not None:
        env["MEDICAL_META_TASK_CONFIG"] = str(meta_task_config_path)
    return _run_command(
        [sys.executable, "-m", "unittest", "tests.test_json_patch_medical_golden"],
        cwd=Path.cwd(),
        timeout=120,
        env=env,
    )


def _token_total(file_reports: list[dict[str, Any]], validation_repair_rounds: list[dict[str, Any]] | None = None) -> dict[str, int]:
    totals = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    for report in file_reports:
        for call in report.get("llm_calls") or []:
            usage = call.get("token_usage") or {}
            for key in totals:
                value = usage.get(key)
                if isinstance(value, int):
                    totals[key] += value
    for vr in validation_repair_rounds or []:
        for rep in vr.get("repairs") or []:
            for call in rep.get("llm_calls") or []:
                usage = call.get("token_usage") or {}
                for key in totals:
                    value = usage.get(key)
                    if isinstance(value, int):
                        totals[key] += value
    return totals


def _load_resume_ok_paths(metrics_path: Path | None) -> set[str]:
    if metrics_path is None or not metrics_path.is_file():
        return set()
    try:
        data = json.loads(metrics_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return set()
    return {
        str(Path(fr["file"]).resolve())
        for fr in (data.get("file_reports") or [])
        if fr.get("ok") and fr.get("file")
    }


def run_full_medical_generation(
    *,
    model: str,
    output_root: Path,
    max_repairs: int,
    meta_task_config_path: Path | None = None,
    resume_from: Path | None = None,
    max_validation_rounds: int = 8,
    validation_repair_only: bool = False,
) -> dict[str, Any]:
    started = time.perf_counter()
    resume_ok = _load_resume_ok_paths(resume_from)
    if resume_ok:
        _log(f"[resume] will skip {len(resume_ok)} artifact(s) marked ok in {resume_from}")

    file_reports: list[dict[str, Any]]
    if validation_repair_only:
        _log("[mode] validation-repair-only (no full-file LLM regeneration)")
        context = build_agentic_generation_context(
            ontology_name="medical",
            meta_task_config_path=meta_task_config_path,
            output_root=output_root,
            write_files=False,
        )
        file_reports = []
    else:
        context = _prepare_scaffolds(output_root, meta_task_config_path)
        scripts_dir = Path(context.scripts_dir).resolve()
        prompts_dir = Path(context.prompts_dir).resolve()
        file_reports = []
        for name in SCRIPT_FILES:
            path = scripts_dir / name
            rpath = str(path.resolve())
            if rpath in resume_ok and path.is_file():
                _log(f"[resume] skip (previous ok): {name}")
                file_reports.append(
                    {
                        "file": rpath,
                        "kind": "script",
                        "ok": True,
                        "skipped": True,
                        "elapsed_seconds": 0.0,
                        "repairs": 0,
                        "llm_calls": [],
                        "history": [{"phase": "resume_skip", "ok": True}],
                        "final_checks": [],
                    }
                )
                continue
            file_reports.append(
                _generate_file(
                    model=model,
                    context=context,
                    path=path,
                    kind="script",
                    max_repairs=max_repairs,
                )
            )
        for path in sorted(prompts_dir.glob("*.md")):
            rpath = str(path.resolve())
            if rpath in resume_ok and path.is_file():
                _log(f"[resume] skip (previous ok): {path.name}")
                file_reports.append(
                    {
                        "file": rpath,
                        "kind": "prompt",
                        "ok": True,
                        "skipped": True,
                        "elapsed_seconds": 0.0,
                        "repairs": 0,
                        "llm_calls": [],
                        "history": [{"phase": "resume_skip", "ok": True}],
                        "final_checks": [],
                    }
                )
                continue
            file_reports.append(
                _generate_file(
                    model=model,
                    context=context,
                    path=path,
                    kind="prompt",
                    max_repairs=max_repairs,
                )
            )

    scripts_dir = Path(context.scripts_dir).resolve()
    prompts_dir = Path(context.prompts_dir).resolve()

    validation_repair_history: list[dict[str, Any]] = []
    validation_report: dict[str, Any] = {}
    for v_round in range(1, max(1, max_validation_rounds) + 1):
        _log(f"[validation] machine report round {v_round}/{max_validation_rounds}")
        validation_report = build_validation_report(context, foreign_contracts=None, write_report=True)
        if validation_report.get("ok"):
            _log("[validation] machine report OK")
            break
        failures = validation_report.get("failures") or []
        _log(f"[validation] failures: {len(failures)} — targeted LLM repairs")
        grouped = _group_validation_failures(failures)
        if not grouped:
            _log("[validation] could not map failures to .py/.md basenames; stopping repair loop")
            validation_repair_history.append({"round": v_round, "repairs": [], "note": "unmapped_failures"})
            break
        full_failures = "\n".join(failures)
        fb = validation_report.get("feedback") or {}
        extra = "\n".join((fb.get("prompt_agent") or []) + (fb.get("coding_agent") or []))
        round_repairs: list[dict[str, Any]] = []
        for fname, msgs in sorted(grouped.items()):
            path = scripts_dir / fname if (scripts_dir / fname).is_file() else prompts_dir / fname
            if not path.is_file():
                _log(f"[validation repair] skip (not found): {fname}")
                continue
            kind = "script" if fname.endswith(".py") else "prompt"
            sticky = (
                "Machine validation reported the following for this file:\n"
                + "\n".join(f"- {m}" for m in msgs)
                + "\n\nFull failure bundle:\n"
                + full_failures
                + "\n\nStructured feedback:\n"
                + extra
            )
            _log(f"[validation repair] LLM patch → {fname}")
            round_repairs.append(
                _repair_one_file_after_machine_validation(
                    model=model,
                    context=context,
                    path=path,
                    kind=kind,
                    max_repairs=max_repairs,
                    sticky_machine_feedback=sticky,
                )
            )
        validation_repair_history.append({"round": v_round, "repairs": round_repairs})
    else:
        _log(f"[validation] exhausted {max_validation_rounds} repair round(s) without bundle OK")

    validation_report = build_validation_report(context, foreign_contracts=None, write_report=True)
    if validation_report.get("ok"):
        _log("[validation] final machine report OK")
    else:
        nfail = len(validation_report.get("failures") or [])
        _log(f"[validation] final machine report still failing ({nfail} issues)")

    golden_repair_history: list[dict[str, Any]] = []
    golden_result = CheckResult(name="golden tests", ok=False)
    for g_round in range(1, max(1, max_validation_rounds) + 1):
        _log(f"[golden] running tests.test_json_patch_medical_golden round {g_round}/{max_validation_rounds}")
        golden_result = _run_golden_tests(scripts_dir, meta_task_config_path)
        if golden_result.ok:
            _log("[golden] tests OK")
            break
        target = scripts_dir / "medical_creation_checks.py"
        if not target.is_file():
            _log("[golden] medical_creation_checks.py missing; stopping golden repair loop")
            golden_repair_history.append({"round": g_round, "repairs": [], "note": "missing_medical_creation_checks"})
            break
        sticky = (
            "Golden tests failed for medical_creation_checks.py. Repair the script so it matches the "
            "checks contract: future import, json import, base import, and one public "
            "check_existing_<Class>s function per ontology class. Shared helpers are allowed if every "
            "public checker returns JSON with status='ok', class=<Class>, and iris=<list of IRIs>.\n\n"
            "Golden test stderr:\n"
            + golden_result.stderr
        )
        _log("[golden repair] LLM patch → medical_creation_checks.py")
        golden_repair_history.append(
            {
                "round": g_round,
                "repairs": [
                    _repair_one_file_after_machine_validation(
                        model=model,
                        context=context,
                        path=target,
                        kind="script",
                        max_repairs=max_repairs,
                        sticky_machine_feedback=sticky,
                    )
                ],
            }
        )
    else:
        _log(f"[golden] exhausted {max_validation_rounds} repair round(s)")

    per_file_ok = all(report.get("ok") for report in file_reports)
    ok = per_file_ok and validation_report.get("ok") and golden_result.ok
    summary = {
        "ok": bool(ok),
        "model": model,
        "meta_task_config_path": str(meta_task_config_path) if meta_task_config_path else None,
        "output_root": str(output_root.resolve()),
        "scripts_dir": str(scripts_dir),
        "prompts_dir": str(prompts_dir),
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "token_totals": _token_total(file_reports, [*validation_repair_history, *golden_repair_history]),
        "file_reports": file_reports,
        "validation_repair_rounds": validation_repair_history,
        "golden_repair_rounds": golden_repair_history,
        "validation_report": validation_report,
        "golden_tests": {
            "ok": golden_result.ok,
            "stdout": golden_result.stdout,
            "stderr": golden_result.stderr,
        },
    }
    reports_dir = output_root / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    metrics_path = reports_dir / "full_medical_generation_metrics.json"
    metrics_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    summary["metrics_path"] = str(metrics_path.resolve())
    _log(f"[done] metrics → {metrics_path}")
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run full medical script/prompt JSON generation.")
    parser.add_argument("--model", default="gpt-5.2", help="Remote LLM model name.")
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT), help="Isolated output root.")
    parser.add_argument(
        "--meta-task-config",
        default=None,
        help="Optional medical meta-task config path; defaults to non-flat v3.",
    )
    parser.add_argument("--max-repairs", type=int, default=2, help="Maximum patch repair attempts per artifact.")
    parser.add_argument(
        "--resume-from",
        type=str,
        default=None,
        help="Path to full_medical_generation_metrics.json; skip LLM regen for artifacts previously ok.",
    )
    parser.add_argument(
        "--max-validation-rounds",
        type=int,
        default=8,
        help="Outer loops: machine validation → targeted file repairs → re-validate.",
    )
    parser.add_argument(
        "--validation-repair-only",
        action="store_true",
        help="Skip regeneration; only run validation repair loop on existing output tree.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    summary = run_full_medical_generation(
        model=args.model,
        output_root=Path(args.output_root),
        max_repairs=args.max_repairs,
        meta_task_config_path=Path(args.meta_task_config) if args.meta_task_config else None,
        resume_from=Path(args.resume_from) if args.resume_from else None,
        max_validation_rounds=args.max_validation_rounds,
        validation_repair_only=args.validation_repair_only,
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
