"""AST checks for generated `init_memory` / `export_memory` adapters.

See README.md.
"""

from __future__ import annotations

import ast
from pathlib import Path

from src.extraction_prompt_generation.compile.context import (
    AgenticGenerationContext,
)
from src.extraction_prompt_generation.validate.report.common import (
    _init_memory_ast_evidence,
)
from src.extraction_prompt_generation.validate.report.runtime_hygiene.accumulator import (
    HygieneState,
)


def inspect_lifecycle_adapters(
    context: AgenticGenerationContext,
    main_path: Path,
    state: HygieneState,
) -> None:
    try:
        main_tree = ast.parse(
            main_path.read_text(encoding="utf-8"), filename=str(main_path)
        )
    except (OSError, SyntaxError):
        main_tree = None
    if main_tree is None:
        return
    runtime_tool_names = {"init_memory", "export_memory"}
    commit_gate = (
        (getattr(context, "contract", {}) or {}).get("materialization_operation_units")
        or {}
    )
    commit_gate_enabled = bool(commit_gate.get("merged_predicate_locals"))
    defined_runtime_tools = {
        node.name
        for node in main_tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name in runtime_tool_names
    }
    forbidden_defined_runtime_tools = set(defined_runtime_tools)
    if commit_gate_enabled:
        forbidden_defined_runtime_tools.discard("export_memory")
    if forbidden_defined_runtime_tools:
        state.fail(
            "runtime-policy:lifecycle-tools#fixed-provenance",
            "main.py must import tested lifecycle tools from _fixed_rdf_runtime, not "
            "define them: " + ", ".join(sorted(forbidden_defined_runtime_tools)),
            subject_kind="runtime-policy",
            tool_names=sorted(forbidden_defined_runtime_tools),
        )
    for node in main_tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name not in runtime_tool_names:
            continue
        if node.args.vararg is not None or node.args.kwarg is not None:
            state.fail(
                f"tool:{node.name}#fastmcp-publishable-signature",
                f"main.py: runtime adapter `{node.name}` uses *args/**kwargs; "
                "FastMCP tools require explicit publishable parameters",
                subject_kind="tool",
                tool_name=node.name,
            )
            continue
        parameter_names = [
            arg.arg for arg in list(node.args.posonlyargs) + list(node.args.args)
        ]
        if (
            node.name == "init_memory"
            and node.args.vararg is None
            and node.args.kwarg is None
        ):
            expected_names = ["doi", "top_level_entity_name"]
            if parameter_names != expected_names:
                state.fail(
                    "tool:init_memory#open-or-resume-signature",
                    "main.py: init_memory must accept exactly "
                    "(doi, top_level_entity_name), with no caller-selected lifecycle mode",
                    subject_kind="tool",
                    tool_name=node.name,
                    expected_parameters=expected_names,
                    actual_parameters=parameter_names,
                )
            lifecycle_evidence = _init_memory_ast_evidence(node)
            if lifecycle_evidence["destructive_calls"]:
                state.fail(
                    "tool:init_memory#no-destructive-operation",
                    "main.py: init_memory must never reset, clear, or replace graph state",
                    subject_kind="tool",
                    tool_name=node.name,
                    calls=lifecycle_evidence["destructive_calls"],
                )
            if not lifecycle_evidence["guarded_initializers"]:
                state.fail(
                    "tool:init_memory#canonical-persistence-resume",
                    "main.py: init_memory must call fixed-runtime "
                    "initialize_retained_graph(source_path=str(<scoped memory path>)) "
                    "inside that path's is_file() guard",
                    subject_kind="tool",
                    tool_name=node.name,
                    lifecycle_evidence=lifecycle_evidence,
                )
            if not lifecycle_evidence["path_variables"]:
                state.fail(
                    "tool:init_memory#canonical-persistence-location",
                    "main.py: init_memory must unpack the first result of "
                    "rdf_runtime.scoped_memory_paths(doi, top_level_entity_name); "
                    "variable naming is unrestricted, but path normalization must not be reimplemented",
                    subject_kind="tool",
                    tool_name=node.name,
                    lifecycle_evidence=lifecycle_evidence,
                )
        if (
            node.name == "export_memory"
            and node.args.vararg is None
            and node.args.kwarg is None
        ):
            source = ast.get_source_segment(
                main_path.read_text(encoding="utf-8"), node
            ) or ""
            if commit_gate_enabled:
                if "check_ordered_members" not in source:
                    state.fail(
                        "tool:export_memory#commit-gate",
                        "main.py: export_memory must call the generated integrity "
                        "check before delegating to fixed export",
                        subject_kind="tool",
                        tool_name=node.name,
                    )
                if "export_memory" not in source.replace("def export_memory", ""):
                    state.fail(
                        "tool:export_memory#fixed-delegate",
                        "main.py: commit-gated export_memory must delegate successful "
                        "graphs to fixed-runtime export_memory",
                        subject_kind="tool",
                        tool_name=node.name,
                    )
            elif (
                "export_graph_result" not in source
                and "export_memory_wrapper" not in source
            ):
                state.fail(
                    "tool:export_memory#abox-projection",
                    "main.py: export_memory must use fixed-runtime "
                    "export_graph_result so schema is excluded by default",
                    subject_kind="tool",
                    tool_name=node.name,
                )
            if "doi" not in parameter_names or "top_level_entity_name" not in parameter_names:
                state.fail(
                    "tool:export_memory#scoped-persistence",
                    "main.py: export_memory must pass DOI and scope to fixed-runtime "
                    "export_graph_result so the canonical memory and export artifacts "
                    "are persisted",
                    subject_kind="tool",
                    tool_name=node.name,
                    actual_parameters=parameter_names,
                )
    if any(
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "materialize_hints"
        for node in main_tree.body
    ):
        state.fail(
            "tool:materialize_hints#forbidden-aggregate-tool",
            "main.py: aggregate `materialize_hints` is forbidden; the KG agent and prompt "
            "must orchestrate atomic create/add/export tools",
            subject_kind="tool",
            tool_name="materialize_hints",
        )
