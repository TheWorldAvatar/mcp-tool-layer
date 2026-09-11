"""Static scan for generic RDF mutation bypasses in generated scripts.

Flags `add_object_property` / `create_individual` outside the fixed runtime.
See README.md.
"""

from __future__ import annotations

import ast
from pathlib import Path

from src.extraction_prompt_generation.validate.report.runtime_hygiene.accumulator import (
    HygieneState,
)


def record_generic_mutation_warnings(scripts_dir: Path, state: HygieneState) -> None:
    bypass_patterns = {
        "generic object-property mutation": (
            "add_object_property",
            "add_object_triple",
        ),
        "generic entity mutation": (
            "create_individual",
            "add_type",
        ),
    }
    bypasses: list[str] = []
    for script_path in sorted(scripts_dir.glob("*.py")):
        if script_path.name in {
            "_fixed_rdf_runtime.py",
            "_fixed_om2_runtime.py",
            "_reuse_pair_judge.py",
        }:
            continue
        try:
            source = script_path.read_text(encoding="utf-8")
        except OSError:
            continue
        try:
            tree = ast.parse(source, filename=str(script_path))
        except SyntaxError:
            tree = None
        if tree is not None:
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "add"
                ):
                    bypasses.append(
                        f"{script_path.name}: direct graph mutation at line "
                        f"{getattr(node, 'lineno', '?')}"
                    )
        for category, patterns in bypass_patterns.items():
            for pattern in patterns:
                if pattern in source:
                    bypasses.append(
                        f"{script_path.name}: {category} via `{pattern}`"
                    )
    if bypasses:
        state.warnings.append(
            "Generated package contains internal generic RDF mutation paths; "
            "MCP exposure validation owns the hard security boundary: "
            + "; ".join(bypasses[:12])
        )
