"""Stage checks for generated `main.py` runtime adapters.

Require `init_memory` / `export_memory`. Forbid `materialize_hints`.
See stage/README.md.
"""

from __future__ import annotations

import ast

from src.extraction_prompt_generation.validate.report.common import (
    _import_generated_main_module,
)
from src.extraction_prompt_generation.validate.report.stage.context import (
    StageProbe,
)


def validate_stage_main(probe: StageProbe) -> None:
    """Require imported lifecycle adapters and forbid aggregate materialize_hints."""
    runtime_tool_names = ("init_memory", "export_memory")
    for tool_name in runtime_tool_names:
        if tool_name not in probe.text:
            probe.fail(f"{probe.name}: missing runtime adapter `{tool_name}`")
    try:
        main_tree = ast.parse(probe.text, filename=str(probe.path))
    except SyntaxError:
        main_tree = None
    if main_tree is not None:
        for node in main_tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if node.name not in runtime_tool_names:
                continue
            if node.args.vararg is not None or node.args.kwarg is not None:
                probe.fail(
                    f"{probe.name}: runtime adapter `{node.name}` uses *args/**kwargs; "
                    "FastMCP tools require explicit publishable parameters"
                )
        if any(
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "materialize_hints"
            for node in main_tree.body
        ):
            probe.fail(
                f"{probe.name}: aggregate `materialize_hints` is forbidden; expose atomic "
                "create/add tools instead"
            )
    try:
        _import_generated_main_module(probe.path.parent, probe.context.ontology.name)
    except Exception as exc:
        probe.fail(
            f"{probe.name}: runtime adapter import smoke failed: "
            f"{type(exc).__name__}: {exc}"
        )
