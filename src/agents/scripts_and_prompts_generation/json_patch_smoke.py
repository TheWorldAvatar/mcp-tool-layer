from __future__ import annotations

import argparse
import json
import subprocess
import sys
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from models.LLMCreator import LLMCreator
from models.ModelConfig import ModelConfig


DEFAULT_SANDBOX = Path("ai_generated_contents_agent_candidate_smoke/json_patch_sandbox")
TARGET_FILE = "generated_hello.py"
EXPECTED_STDOUT = "hello from json patch loop"


@dataclass
class CheckResult:
    name: str
    ok: bool
    returncode: int
    stdout: str
    stderr: str


def _response_text(response: Any) -> str:
    content = getattr(response, "content", response)
    if isinstance(content, list):
        return "\n".join(str(item) for item in content)
    return str(content)


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


def _invoke_json(model: str, prompt: str) -> dict[str, Any]:
    llm = LLMCreator(
        model=model,
        remote_model=True,
        model_config=ModelConfig(max_tokens=8000, timeout=300, temperature=0, top_p=0.1),
    ).setup_llm()
    response = llm.invoke(prompt)
    return _extract_json_object(_response_text(response))


def _run_command(args: list[str], cwd: Path, timeout: int = 60) -> CheckResult:
    completed = subprocess.run(
        args,
        cwd=str(cwd),
        text=True,
        capture_output=True,
        timeout=timeout,
    )
    return CheckResult(
        name=" ".join(args),
        ok=completed.returncode == 0,
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


def _write_initial_script(sandbox: Path, python_source: str) -> Path:
    sandbox.mkdir(parents=True, exist_ok=True)
    if not (sandbox / ".git").exists():
        _run_command(["git", "init", "-q"], cwd=sandbox)
    script_path = sandbox / TARGET_FILE
    script_path.write_text(python_source.rstrip() + "\n", encoding="utf-8")
    return script_path


def _exercise_repair_defect(script_path: Path) -> None:
    text = script_path.read_text(encoding="utf-8")
    if EXPECTED_STDOUT in text:
        text = text.replace(EXPECTED_STDOUT, EXPECTED_STDOUT + " broken", 1)
    else:
        text = text.rstrip() + '\nprint("repair sentinel")\n'
    script_path.write_text(text.rstrip() + "\n", encoding="utf-8")


def _run_checks(sandbox: Path, script_path: Path) -> list[CheckResult]:
    results = [
        _run_command([sys.executable, "-m", "ruff", "format", str(script_path.name)], cwd=sandbox),
        _run_command([sys.executable, "-m", "ruff", "check", str(script_path.name)], cwd=sandbox),
    ]
    execution = _run_command([sys.executable, str(script_path.name)], cwd=sandbox)
    if execution.stdout.strip() != EXPECTED_STDOUT:
        execution = CheckResult(
            name=execution.name,
            ok=False,
            returncode=execution.returncode,
            stdout=execution.stdout,
            stderr=(
                execution.stderr
                + f"\nExpected stdout exactly: {EXPECTED_STDOUT!r}; got: {execution.stdout.strip()!r}\n"
            ),
        )
    results.append(execution)
    return results


def _feedback(results: list[CheckResult], script_path: Path) -> str:
    blocks = []
    for result in results:
        blocks.append(
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
        )
    return "\n\n".join(
        [
            f"Target file: {TARGET_FILE}",
            f"Current file contents:\n```python\n{script_path.read_text(encoding='utf-8')}\n```",
            "Check results:\n" + "\n".join(blocks),
        ]
    )


def _apply_patch(sandbox: Path, patch_unified_diff: str) -> CheckResult:
    patch_path = sandbox / "repair.patch"
    patch_path.write_text(patch_unified_diff.rstrip() + "\n", encoding="utf-8")
    return _run_command(
        ["git", "apply", "--recount", "--whitespace=nowarn", str(patch_path.name)],
        cwd=sandbox,
    )


def _generation_prompt() -> str:
    return textwrap.dedent(
        f"""
        You are the first-pass coding agent in a minimal JSON contract smoke test.

        Return only a valid JSON object with this exact shape:
        {{"python_source": "<complete Python script>"}}

        The script must be a small standalone Python script named {TARGET_FILE} once written.
        It must print exactly this single line to stdout:
        {EXPECTED_STDOUT}

        Do not include Markdown fences, explanations, or extra keys.
        """
    ).strip()


def _repair_prompt(feedback: str) -> str:
    return textwrap.dedent(
        f"""
        You are the repair agent in a minimal JSON contract smoke test.

        The orchestrator wrote {TARGET_FILE}, ran ruff format, ruff check, and executed it.
        The checks failed. Return only a valid JSON object with this exact shape:
        {{"patch_unified_diff": "<unified diff usable by git apply>"}}

        Requirements:
        - The diff must patch {TARGET_FILE}.
        - Use standard unified diff format with a/ and b/ paths.
        - Prefer the smallest possible single-line replacement.
        - Make hunk context and line counts exactly match the current file.
        - Do not include an index line.
        - Do not include Markdown fences, explanations, or extra keys.
        - After the patch, running the script must print exactly: {EXPECTED_STDOUT}

        Example patch shape:
        diff --git a/{TARGET_FILE} b/{TARGET_FILE}
        --- a/{TARGET_FILE}
        +++ b/{TARGET_FILE}
        @@ -1,1 +1,1 @@
        -print("wrong")
        +print("right")

        Feedback from the orchestrator:
        {feedback}
        """
    ).strip()


def run_smoke(
    *,
    model: str,
    sandbox: Path,
    max_repairs: int,
    exercise_repair: bool,
) -> dict[str, Any]:
    sandbox = sandbox.resolve()
    generation = _invoke_json(model, _generation_prompt())
    python_source = generation.get("python_source")
    if not isinstance(python_source, str) or not python_source.strip():
        raise ValueError("First LLM response did not contain non-empty `python_source`")

    script_path = _write_initial_script(sandbox, python_source)
    if exercise_repair:
        _exercise_repair_defect(script_path)

    history: list[dict[str, Any]] = []
    results = _run_checks(sandbox, script_path)
    history.append({"phase": "initial", "ok": all(item.ok for item in results)})

    repairs = 0
    while not all(item.ok for item in results) and repairs < max_repairs:
        repairs += 1
        repair = _invoke_json(model, _repair_prompt(_feedback(results, script_path)))
        patch_unified_diff = repair.get("patch_unified_diff")
        if not isinstance(patch_unified_diff, str) or not patch_unified_diff.strip():
            raise ValueError("Repair LLM response did not contain non-empty `patch_unified_diff`")
        patch_result = _apply_patch(sandbox, patch_unified_diff)
        if not patch_result.ok:
            history.append(
                {
                    "phase": f"repair_{repairs}",
                    "patch_applied": False,
                    "patch_stdout": patch_result.stdout,
                    "patch_stderr": patch_result.stderr,
                }
            )
            results = [
                *results,
                CheckResult(
                    name=patch_result.name,
                    ok=False,
                    returncode=patch_result.returncode,
                    stdout=patch_result.stdout,
                    stderr="Patch application failed:\n" + patch_result.stderr,
                ),
            ]
            continue
        results = _run_checks(sandbox, script_path)
        history.append(
            {
                "phase": f"repair_{repairs}",
                "patch_applied": True,
                "ok": all(item.ok for item in results),
            }
        )

    return {
        "ok": all(item.ok for item in results),
        "model": model,
        "sandbox": str(sandbox),
        "script_path": str(script_path),
        "repairs": repairs,
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a minimal LLM JSON script/patch smoke loop.")
    parser.add_argument("--model", default="gpt-5.2", help="Remote LLM model name.")
    parser.add_argument("--sandbox", default=str(DEFAULT_SANDBOX), help="Directory for generated script files.")
    parser.add_argument("--max-repairs", type=int, default=2, help="Maximum LLM patch repair attempts.")
    parser.add_argument(
        "--exercise-repair",
        action="store_true",
        help="Inject a controlled post-generation defect so the patch iteration is exercised.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    summary = run_smoke(
        model=args.model,
        sandbox=Path(args.sandbox),
        max_repairs=args.max_repairs,
        exercise_repair=args.exercise_repair,
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
