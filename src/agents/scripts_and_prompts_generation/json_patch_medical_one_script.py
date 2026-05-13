from __future__ import annotations

import argparse
import ast
import importlib.util
import json
import subprocess
import sys
import textwrap
import time
import types
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from models.LLMCreator import LLMCreator
from models.ModelConfig import ModelConfig
from src.agents.scripts_and_prompts_generation.agentic_generation_context import (
    build_agentic_generation_context,
)
from src.agents.scripts_and_prompts_generation.agentic_generation_runner import (
    generate_deterministic_script_slice,
)


DEFAULT_OUTPUT_ROOT = Path("ai_generated_contents_agent_candidate_json_medical_one_script")
TARGET_FILE = "medical_creation_checks.py"


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
    llm = LLMCreator(
        model=model,
        remote_model=True,
        model_config=ModelConfig(max_tokens=12000, timeout=300, temperature=0, top_p=0.1),
    ).setup_llm()
    started = time.perf_counter()
    response = llm.invoke(prompt)
    elapsed = time.perf_counter() - started
    return LLMJsonResult(
        data=_extract_json_object(_response_text(response)),
        elapsed_seconds=elapsed,
        token_usage=_token_usage(response),
    )


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


def _prepare_scripts(output_root: Path) -> tuple[Path, list[str]]:
    context = build_agentic_generation_context(
        ontology_name="medical",
        output_root=output_root,
        write_files=True,
    )
    generate_deterministic_script_slice(context)
    scripts_dir = Path(context.scripts_dir).resolve()
    if not (scripts_dir / ".git").exists():
        _run_command(["git", "init", "-q"], cwd=scripts_dir)
    classes = sorted((context.parsed.get("classes") or {}).keys())
    return scripts_dir, classes


def _generation_prompt(classes: list[str]) -> str:
    class_lines = "\n".join(f"- {name}" for name in classes)
    return textwrap.dedent(
        f"""
        You are the coding agent for one real generated medical ontology script.

        Return only a valid JSON object with this exact shape:
        {{"python_source": "<complete Python source for {TARGET_FILE}>"}}

        Generate {TARGET_FILE}. This is a real script in the generated medical MCP package.
        Use only the class locals listed below, which come from the medical TTL T-Box.
        Do not add domain knowledge from outside this list.

        Required implementation:
        - Include `from __future__ import annotations`.
        - Import `json`.
        - Import `GRAPH`, `NS`, and `RDF` from `.medical_creation_base`.
        - For every class local below, define exactly one function named `check_existing_<py_name>s`.
        - Every checker function signature must include an explicit return annotation: `-> str`.
        - `<py_name>` is the class local converted to a Python identifier by replacing non-alphanumeric characters with `_`.
        - Each function must return `json.dumps({{"status": "ok", "class": "<ClassLocal>", "iris": iris}})`.
        - Each function must set `iris = [str(s) for s in GRAPH.subjects(RDF.type, NS["<ClassLocal>"])]`.

        Class locals:
        {class_lines}

        Do not include Markdown fences, explanations, or extra keys.
        """
    ).strip()


def _repair_prompt(feedback: str) -> str:
    return textwrap.dedent(
        f"""
        You are repairing one real generated medical ontology script.

        Return only a valid JSON object with this exact shape:
        {{"patch_unified_diff": "<unified diff usable by git apply>"}}

        Requirements:
        - Patch {TARGET_FILE}.
        - Use standard unified diff with a/{TARGET_FILE} and b/{TARGET_FILE} paths.
        - The patch must start with `diff --git a/{TARGET_FILE} b/{TARGET_FILE}`.
        - Do not use Cursor/ApplyPatch format. Never output `*** Begin Patch`, `*** Update File`, or `*** End Patch`.
        - Prefer the smallest possible patch.
        - For a one-line semantic mismatch, replace the exact broken return line with the correct return line.
        - Do not include Markdown fences, explanations, or extra keys.

        Example accepted format:
        diff --git a/{TARGET_FILE} b/{TARGET_FILE}
        --- a/{TARGET_FILE}
        +++ b/{TARGET_FILE}
        @@ -1,1 +1,1 @@
        -return "wrong"
        +return "right"

        Validation feedback:
        {feedback}
        """
    ).strip()


def _write_script(scripts_dir: Path, python_source: str) -> Path:
    target = scripts_dir / TARGET_FILE
    target.write_text(python_source.rstrip() + "\n", encoding="utf-8")
    return target


def _exercise_repair_defect(target: Path, classes: list[str]) -> None:
    if not classes:
        return
    text = target.read_text(encoding="utf-8")
    first_class = classes[0]
    defect = first_class + "_BROKEN"
    target.write_text(text.replace(f'"class": "{first_class}"', f'"class": "{defect}"', 1), encoding="utf-8")


def _py_name(text: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in text).strip("_") or "item"


def _import_module(scripts_dir: Path, target: Path) -> tuple[Any | None, str]:
    package_name = "_json_medical_one_script"
    for name in list(sys.modules):
        if name == package_name or name.startswith(package_name + "."):
            del sys.modules[name]
    package = types.ModuleType(package_name)
    package.__path__ = [str(scripts_dir)]  # type: ignore[attr-defined]
    sys.modules[package_name] = package
    module_name = f"{package_name}.{target.stem}"
    try:
        spec = importlib.util.spec_from_file_location(module_name, target)
        if spec is None or spec.loader is None:
            return None, "Could not create import spec"
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        return module, ""
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"


def _semantic_checks(scripts_dir: Path, target: Path, classes: list[str]) -> CheckResult:
    module, error = _import_module(scripts_dir, target)
    if module is None:
        return CheckResult(name="semantic import", ok=False, stderr=error)

    failures: list[str] = []
    tree = ast.parse(target.read_text(encoding="utf-8"), filename=str(target))
    function_nodes = {
        node.name: node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
    }
    for class_local in classes:
        fn_name = f"check_existing_{_py_name(class_local)}s"
        node = function_nodes.get(fn_name)
        if node is None:
            failures.append(f"Missing checker function: {fn_name}")
            continue
        if not isinstance(node.returns, ast.Name) or node.returns.id != "str":
            failures.append(f"{fn_name} must declare an explicit `-> str` return annotation")
        fn = getattr(module, fn_name, None)
        if not callable(fn):
            failures.append(f"Missing checker function: {fn_name}")
            continue
        try:
            payload = json.loads(fn())
        except Exception as exc:
            failures.append(f"{fn_name} returned invalid JSON: {type(exc).__name__}: {exc}")
            continue
        if payload.get("status") != "ok":
            failures.append(f"{fn_name} status is not ok: {payload!r}")
        if payload.get("class") != class_local:
            failures.append(f"{fn_name} class mismatch: {payload.get('class')!r} != {class_local!r}")
        if not isinstance(payload.get("iris"), list):
            failures.append(f"{fn_name} iris field is not a list")

    return CheckResult(
        name="semantic checker surface",
        ok=not failures,
        stdout=f"Validated {len(classes)} checker functions.",
        stderr="\n".join(failures),
    )


def _run_checks(scripts_dir: Path, target: Path, classes: list[str]) -> list[CheckResult]:
    return [
        _run_command([sys.executable, "-m", "ruff", "format", target.name], cwd=scripts_dir),
        _run_command([sys.executable, "-m", "ruff", "check", target.name], cwd=scripts_dir),
        _run_command([sys.executable, "-m", "py_compile", target.name], cwd=scripts_dir),
        _semantic_checks(scripts_dir, target, classes),
    ]


def _feedback(results: list[CheckResult], target: Path) -> str:
    return "\n\n".join(
        [
            f"Current file contents:\n```python\n{target.read_text(encoding='utf-8')}\n```",
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


def _apply_patch(scripts_dir: Path, patch_unified_diff: str) -> CheckResult:
    patch_path = scripts_dir / "repair.patch"
    patch_path.write_text(patch_unified_diff.rstrip() + "\n", encoding="utf-8")
    return _run_command(
        ["git", "apply", "--recount", "--whitespace=nowarn", patch_path.name],
        cwd=scripts_dir,
    )


def run_medical_one_script(
    *,
    model: str,
    output_root: Path,
    max_repairs: int,
    exercise_repair: bool,
) -> dict[str, Any]:
    script_started = time.perf_counter()
    scripts_dir, classes = _prepare_scripts(output_root)
    llm_calls: list[dict[str, Any]] = []
    generation = _invoke_json(model, _generation_prompt(classes))
    llm_calls.append(
        {
            "phase": "initial_generation",
            "elapsed_seconds": round(generation.elapsed_seconds, 3),
            "token_usage": generation.token_usage,
        }
    )
    python_source = generation.data.get("python_source")
    if not isinstance(python_source, str) or not python_source.strip():
        raise ValueError("First LLM response did not contain non-empty `python_source`")
    target = _write_script(scripts_dir, python_source)
    if exercise_repair:
        _exercise_repair_defect(target, classes)

    history: list[dict[str, Any]] = []
    results = _run_checks(scripts_dir, target, classes)
    history.append({"phase": "initial", "ok": all(item.ok for item in results)})
    repairs = 0
    while not all(item.ok for item in results) and repairs < max_repairs:
        repairs += 1
        repair = _invoke_json(model, _repair_prompt(_feedback(results, target)))
        llm_calls.append(
            {
                "phase": f"repair_{repairs}",
                "elapsed_seconds": round(repair.elapsed_seconds, 3),
                "token_usage": repair.token_usage,
            }
        )
        patch_unified_diff = repair.data.get("patch_unified_diff")
        if not isinstance(patch_unified_diff, str) or not patch_unified_diff.strip():
            raise ValueError("Repair LLM response did not contain non-empty `patch_unified_diff`")
        patch_result = _apply_patch(scripts_dir, patch_unified_diff)
        if not patch_result.ok:
            results = [*results, patch_result]
            history.append({"phase": f"repair_{repairs}", "patch_applied": False, "ok": False})
            continue
        results = _run_checks(scripts_dir, target, classes)
        history.append({"phase": f"repair_{repairs}", "patch_applied": True, "ok": all(item.ok for item in results)})

    return {
        "ok": all(item.ok for item in results),
        "model": model,
        "output_root": str(output_root.resolve()),
        "scripts_dir": str(scripts_dir),
        "target_file": str(target),
        "class_count": len(classes),
        "repairs": repairs,
        "exercise_repair": exercise_repair,
        "script_elapsed_seconds": round(time.perf_counter() - script_started, 3),
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run one medical script through JSON generation and patch repair.")
    parser.add_argument("--model", default="gpt-5.2", help="Remote LLM model name.")
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT), help="Isolated output root.")
    parser.add_argument("--max-repairs", type=int, default=2, help="Maximum LLM patch repair attempts.")
    parser.add_argument(
        "--exercise-repair",
        action="store_true",
        help="Inject a controlled semantic defect after first generation so patch repair is exercised.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    output_root = Path(args.output_root)
    summary = run_medical_one_script(
        model=args.model,
        output_root=output_root,
        max_repairs=args.max_repairs,
        exercise_repair=args.exercise_repair,
    )
    metrics_path = output_root / "reports" / "medical_creation_checks_metrics.json"
    metrics_path.parent.mkdir(parents=True, exist_ok=True)
    metrics_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    summary["metrics_path"] = str(metrics_path.resolve())
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
