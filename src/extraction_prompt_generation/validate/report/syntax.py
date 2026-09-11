"""Syntax and generated-package import smoke checks.

No-op when `scripts/` is missing (prompt-only runs). See report/README.md.
"""

from __future__ import annotations

import ast
import importlib.util
import sys
import types
from pathlib import Path
from typing import Any

from src.extraction_prompt_generation.compile.context import (
    AgenticGenerationContext,
)
from src.extraction_prompt_generation.validate.report.common import (
    _semantic_obligation,
)


def _syntax_report(
    scripts_dir: Path,
    paths: list[Path] | None = None,
) -> tuple[list[str], list[str], list[dict[str, Any]]]:
    """Parse generated Python and smoke-import the package when present."""
    failures: list[str] = []
    warnings: list[str] = []
    obligations: list[dict[str, Any]] = []
    if not scripts_dir.exists():
        warnings.append(f"Scripts directory does not exist yet: {scripts_dir}")
        return failures, warnings, obligations
    python_paths = (
        sorted(path for path in paths if path.suffix == ".py")
        if paths is not None
        else sorted(scripts_dir.glob("*.py"))
    )
    for path in python_paths:
        artifact = path.name
        item_failures: list[str] = []
        try:
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as exc:
            message = f"{path.name}: syntax error line {exc.lineno}: {exc.msg}"
            failures.append(message)
            item_failures.append(message)
        obligations.append(
            _semantic_obligation(
                subject_key=f"artifact:{artifact}#python-syntax",
                failures=item_failures,
                observed_artifacts=[artifact],
                evidence={"subject_kind": "artifact", "artifact": artifact},
            )
        )
    return failures, warnings, obligations


def _import_report(
    context: AgenticGenerationContext,
) -> tuple[list[str], list[str], list[dict[str, Any]]]:
    failures: list[str] = []
    warnings: list[str] = []
    import_failures: list[str] = []
    mcp_failures: list[str] = []
    obligations: list[dict[str, Any]] = []
    scripts_dir = Path(context.scripts_dir)
    main_path = scripts_dir / "main.py"
    if not main_path.exists():
        warnings.append("main.py not present; import smoke skipped")
        return failures, warnings, obligations
    package_name = f"_agentic_generated_{context.ontology.name}_{abs(hash(str(scripts_dir.resolve())))}"
    for name in list(sys.modules):
        if name == package_name or name.startswith(package_name + "."):
            del sys.modules[name]
    package = types.ModuleType(package_name)
    package.__path__ = [str(scripts_dir.resolve())]  # type: ignore[attr-defined]
    sys.modules[package_name] = package
    module_name = f"{package_name}.main"
    try:
        spec = importlib.util.spec_from_file_location(module_name, main_path)
        if spec is None or spec.loader is None:
            message = "main.py import failed: could not create import spec"
            failures.append(message)
            import_failures.append(message)
            return failures, warnings, [
                _semantic_obligation(
                    subject_key="artifact:main.py#importable",
                    failures=import_failures,
                    observed_artifacts=["main.py"],
                    evidence={"subject_kind": "artifact", "artifact": "main.py"},
                )
            ]
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        if getattr(module, "mcp", None) is None:
            message = "main.py imports but does not expose `mcp`"
            failures.append(message)
            mcp_failures.append(message)
    except Exception as exc:
        message = f"main.py import failed: {type(exc).__name__}: {exc}"
        failures.append(message)
        import_failures.append(message)
    obligations.extend(
        [
            _semantic_obligation(
                subject_key="artifact:main.py#importable",
                failures=import_failures,
                observed_artifacts=["main.py"],
                evidence={"subject_kind": "artifact", "artifact": "main.py"},
            ),
            _semantic_obligation(
                subject_key="tool:mcp#main-export",
                failures=mcp_failures,
                observed_artifacts=["main.py"],
                evidence={"subject_kind": "tool", "tool_name": "mcp"},
            ),
        ]
    )
    return failures, warnings, obligations
