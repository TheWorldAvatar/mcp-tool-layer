from __future__ import annotations

import argparse
import importlib.util
import json
import re
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


DEFAULT_OUTPUT_ROOT = Path("ai_generated_contents_agent_candidate_json_one_script")


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


def _py_name(text: str) -> str:
    return re.sub(r"\W+", "_", str(text)).strip("_") or "item"


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
    decoder = json.JSONDecoder()
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("{")
        if start < 0:
            raise
        data, _ = decoder.raw_decode(stripped[start:])
    if not isinstance(data, dict):
        raise ValueError("LLM response must be a JSON object")
    return data


def _invoke_json(model: str, prompt: str) -> LLMJsonResult:
    llm = LLMCreator(
        model=model,
        remote_model=True,
        model_config=ModelConfig(
            max_tokens=24000, timeout=900, temperature=0, top_p=0.1
        ),
    ).setup_llm()
    started = time.perf_counter()
    response = llm.invoke(prompt)
    return LLMJsonResult(
        data=_extract_json_object(_response_text(response)),
        elapsed_seconds=time.perf_counter() - started,
        token_usage=_token_usage(response),
    )


def _run_command(args: list[str], cwd: Path, timeout: int = 120) -> CheckResult:
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


def _default_target(ontology: str) -> str:
    return f"{_py_name(ontology)}_creation_entities.py"


def _prepare_scripts(*, ontology: str, output_root: Path, meta_task_config: str | None):
    context = build_agentic_generation_context(
        ontology_name=ontology,
        meta_task_config_path=meta_task_config,
        output_root=output_root,
        write_files=True,
    )
    scripts_dir = Path(context.scripts_dir).resolve()
    ontology_py = _py_name(ontology)
    expected_scripts = {
        "__init__.py",
        f"{ontology_py}_creation_base.py",
        f"{ontology_py}_creation_checks.py",
        f"{ontology_py}_creation_entities.py",
        f"{ontology_py}_creation_relationships.py",
        "main.py",
    }
    if not all((scripts_dir / name).is_file() for name in expected_scripts):
        generate_deterministic_script_slice(context)
    if not (scripts_dir / ".git").exists():
        _run_command(["git", "init", "-q"], cwd=scripts_dir)
    return context, scripts_dir


def _context_brief(context) -> str:
    classes = sorted((context.parsed.get("classes") or {}).keys())
    properties = sorted((context.parsed.get("properties") or {}).keys())
    return json.dumps(
        {
            "ontology": context.ontology.name,
            "role": context.ontology.role,
            "ttl_file": context.ontology.ttl_file,
            "namespace_uri": context.contract.get("namespace_uri"),
            "top_entity": context.contract.get("top_entity"),
            "class_count": len(classes),
            "property_count": len(properties),
            "classes": classes,
            "properties": properties,
            "ordered_member_profile": context.contract.get("ordered_member_profile"),
            "required_links": context.contract.get("required_links"),
            "om2_quantity_properties": context.contract.get("om2_quantity_properties"),
        },
        indent=2,
        ensure_ascii=False,
    )


def _generation_prompt(context, target_file: str, scaffold: str) -> str:
    return textwrap.dedent(
        f"""
        You are the coding agent for one generated ontology MCP script.

        Return only a valid JSON object with this exact shape:
        {{"python_source": "<complete Python source for {target_file}>"}}

        Generate `{target_file}` for the `{context.ontology.name}` ontology package.
        Use the scaffold below as the package/API contract, not as a suggestion to ignore.

        Hard requirements:
        - Preserve the scaffold's import style and sibling-module API.
        - Import only Python standard library, installed dependencies already used by the scaffold, and sibling files in this generated package.
        - Do not import `universal_utils`, `sandbox`, old candidate packages, or any helper that is not present in the scaffold package.
        - Do not invent new files.
        - Keep all public function names expected by the scaffold target.
        - Keep code domain-generic except for ontology symbols/comments from the T-Box context.
        - Return only JSON. No Markdown fences, no explanations, no extra keys.

        TTL-derived context:
        {_context_brief(context)}

        Current scaffold for `{target_file}`:
        ```python
        {scaffold}
        ```
        """
    ).strip()


def _repair_prompt(target_file: str, feedback: str) -> str:
    return textwrap.dedent(
        f"""
        You are repairing one generated ontology MCP script.

        Return only a valid JSON object with this exact shape:
        {{"patch_unified_diff": "<unified diff usable by git apply>"}}

        Requirements:
        - Patch `{target_file}` only.
        - Use standard unified diff with a/{target_file} and b/{target_file} paths.
        - The patch must start with `diff --git a/{target_file} b/{target_file}`.
        - Do not use Cursor/ApplyPatch format.
        - Prefer the smallest possible patch.
        - Do not add imports from nonexistent package helpers.
        - Do not include Markdown fences, explanations, or extra keys.

        Validation feedback:
        {feedback}
        """
    ).strip()


def _import_module(scripts_dir: Path, target: Path) -> tuple[Any | None, str]:
    package_name = f"_json_one_script_{target.parent.name}"
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


def _semantic_checks(scripts_dir: Path, target: Path, context) -> CheckResult:
    text = target.read_text(encoding="utf-8")
    failures: list[str] = []
    if "universal_utils" in text:
        failures.append(
            "Script imports or references nonexistent universal_utils helpers"
        )
    module, error = _import_module(scripts_dir, target)
    if module is None:
        failures.append(f"Import failed: {error}")

    if target.name.endswith("_creation_entities.py"):
        for class_local in sorted((context.parsed.get("classes") or {}).keys()):
            fn_name = f"create_{_py_name(class_local)}"
            if fn_name not in text:
                failures.append(f"Missing expected entity factory `{fn_name}`")

    return CheckResult(
        name="semantic package checks",
        ok=not failures,
        stdout=f"Validated package import and target shape for {target.name}.",
        stderr="\n".join(failures),
    )


def _run_checks(scripts_dir: Path, target: Path, context) -> list[CheckResult]:
    return [
        _run_command(
            [sys.executable, "-m", "ruff", "format", target.name], cwd=scripts_dir
        ),
        _run_command(
            [sys.executable, "-m", "ruff", "check", target.name], cwd=scripts_dir
        ),
        _run_command(
            [sys.executable, "-m", "py_compile", target.name], cwd=scripts_dir
        ),
        _semantic_checks(scripts_dir, target, context),
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


def _apply_patch(
    scripts_dir: Path, target_file: str, patch_unified_diff: str
) -> CheckResult:
    patch_path = scripts_dir / f"{target_file}.repair.patch"
    patch_path.write_text(patch_unified_diff.rstrip() + "\n", encoding="utf-8")
    return _run_command(
        ["git", "apply", "--recount", "--whitespace=nowarn", patch_path.name],
        cwd=scripts_dir,
    )


def run_one_script_generation(
    *,
    ontology: str,
    target_file: str | None,
    model: str,
    output_root: Path,
    meta_task_config: str | None,
    max_repairs: int,
) -> dict[str, Any]:
    started = time.perf_counter()
    target_file = target_file or _default_target(ontology)
    context, scripts_dir = _prepare_scripts(
        ontology=ontology,
        output_root=output_root,
        meta_task_config=meta_task_config,
    )
    target = scripts_dir / target_file
    if not target.is_file():
        raise FileNotFoundError(f"Target scaffold not found: {target}")
    scaffold = target.read_text(encoding="utf-8")

    llm_calls: list[dict[str, Any]] = []
    generation = _invoke_json(model, _generation_prompt(context, target_file, scaffold))
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
    target.write_text(python_source.rstrip() + "\n", encoding="utf-8")

    history: list[dict[str, Any]] = []
    results = _run_checks(scripts_dir, target, context)
    history.append({"phase": "initial", "ok": all(item.ok for item in results)})
    repairs = 0
    while not all(item.ok for item in results) and repairs < max_repairs:
        repairs += 1
        repair = _invoke_json(
            model, _repair_prompt(target_file, _feedback(results, target))
        )
        llm_calls.append(
            {
                "phase": f"repair_{repairs}",
                "elapsed_seconds": round(repair.elapsed_seconds, 3),
                "token_usage": repair.token_usage,
            }
        )
        patch_unified_diff = repair.data.get("patch_unified_diff")
        if not isinstance(patch_unified_diff, str) or not patch_unified_diff.strip():
            raise ValueError(
                "Repair LLM response did not contain non-empty `patch_unified_diff`"
            )
        patch_result = _apply_patch(scripts_dir, target_file, patch_unified_diff)
        if not patch_result.ok:
            results = [*results, patch_result]
            history.append(
                {"phase": f"repair_{repairs}", "patch_applied": False, "ok": False}
            )
            continue
        results = _run_checks(scripts_dir, target, context)
        history.append(
            {
                "phase": f"repair_{repairs}",
                "patch_applied": True,
                "ok": all(item.ok for item in results),
            }
        )

    summary = {
        "ok": all(item.ok for item in results),
        "ontology": ontology,
        "model": model,
        "output_root": str(output_root.resolve()),
        "scripts_dir": str(scripts_dir),
        "target_file": str(target),
        "repairs": repairs,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
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
    metrics_path = (
        output_root / "reports" / ontology / f"{Path(target_file).stem}_metrics.json"
    )
    metrics_path.parent.mkdir(parents=True, exist_ok=True)
    metrics_path.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    summary["metrics_path"] = str(metrics_path.resolve())
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run one ontology script through JSON generation and patch repair."
    )
    parser.add_argument(
        "--ontology", required=True, help="Ontology short name, e.g. ontosynthesis."
    )
    parser.add_argument(
        "--target-file", default=None, help="Script filename under scripts/<ontology>."
    )
    parser.add_argument("--model", default="gpt-5.2", help="Remote LLM model name.")
    parser.add_argument(
        "--output-root", default=str(DEFAULT_OUTPUT_ROOT), help="Isolated output root."
    )
    parser.add_argument(
        "--meta-task-config", default=None, help="Optional meta-task config path."
    )
    parser.add_argument(
        "--max-repairs", type=int, default=2, help="Maximum LLM patch repair attempts."
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    summary = run_one_script_generation(
        ontology=args.ontology,
        target_file=args.target_file,
        model=args.model,
        output_root=Path(args.output_root),
        meta_task_config=args.meta_task_config,
        max_repairs=args.max_repairs,
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
