"""MCP tool-surface, relationship-parameter, and ordered-member checks.

Applies when generated scripts are present. See report/README.md.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from src.extraction_prompt_generation.compile.context import (
    AgenticGenerationContext,
)
from src.extraction_prompt_generation.generate.artifact_surface import (
    derive_main_surface_contract,
)
from src.extraction_prompt_generation.validate.report.common import (
    _fastmcp_tools,
    _import_generated_main_module,
    _read_texts,
    _semantic_obligation,
)


def _expected_tool_surface_report(
    context: AgenticGenerationContext,
) -> tuple[list[str], list[str], list[dict[str, Any]]]:
    """Check MCP tool names, relationship parameters, and ordered-member tools."""
    failures: list[str] = []
    warnings: list[str] = []
    obligations: list[dict[str, Any]] = []
    scripts_dir = Path(context.scripts_dir)

    def fail(subject_key: str, message: str, **evidence: Any) -> None:
        failures.append(message)
        obligations.append(
            _semantic_obligation(
                subject_key=subject_key,
                failures=[message],
                observed_artifacts=[str(scripts_dir)],
                evidence=evidence,
                message=message,
            )
        )

    script_text = "\n".join(_read_texts(scripts_dir, "*.py").values())
    if not script_text.strip():
        warnings.append("No generated scripts found for tool-surface validation")
        return failures, warnings, obligations
    forbidden_extension_imports = sorted(
        set(re.findall(r"src\.[A-Za-z_][A-Za-z0-9_]*_extension", script_text))
    )
    if context.ontology.role == "extension":
        used = forbidden_extension_imports
        if used:
            fail(
                "policy:extension-server-independence",
                "Generated extension MCP scripts must be T-Box-derived and must not wrap handwritten extension servers: "
                + ", ".join(used),
                subject_kind="policy",
            )

    main_path = scripts_dir / "main.py"
    if not main_path.is_file():
        warnings.append("main.py not present; exact MCP surface validation skipped")
        return failures, warnings, obligations
    try:
        main_tree = ast.parse(main_path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, SyntaxError) as exc:
        fail(
            "artifact:main.py#stdio-entry-point",
            f"main.py could not be parsed for stdio entry-point validation: "
            f"{type(exc).__name__}: {exc}",
            subject_kind="artifact",
            artifact="main.py",
        )
    else:
        has_stdio_entry_point = any(
            isinstance(node, ast.If)
            and isinstance(node.test, ast.Compare)
            and isinstance(node.test.left, ast.Name)
            and node.test.left.id == "__name__"
            and len(node.test.ops) == 1
            and isinstance(node.test.ops[0], ast.Eq)
            and len(node.test.comparators) == 1
            and isinstance(node.test.comparators[0], ast.Constant)
            and node.test.comparators[0].value == "__main__"
            and any(
                isinstance(child, ast.Expr)
                and isinstance(child.value, ast.Call)
                and isinstance(child.value.func, ast.Attribute)
                and isinstance(child.value.func.value, ast.Name)
                and child.value.func.value.id == "mcp"
                and child.value.func.attr == "run"
                for child in node.body
            )
            for node in main_tree.body
        )
        if not has_stdio_entry_point:
            fail(
                "artifact:main.py#stdio-entry-point",
                "main.py must keep stdio service alive with "
                "`if __name__ == '__main__': mcp.run()`",
                subject_kind="artifact",
                artifact="main.py",
            )
    try:
        surface_contract = derive_main_surface_contract(scripts_dir)
    except Exception as exc:
        fail(
            "artifact:main.py#mcp-surface-contract",
            "Could not derive the MCP surface from generated sibling manifests: "
            f"{type(exc).__name__}: {exc}",
            subject_kind="artifact",
            artifact="main.py",
        )
        return failures, warnings, obligations
    expected_tools = set(surface_contract["expected_mcp_tools"])
    probe_inventories: list[set[str]] = []
    probe_tools: list[dict[str, Any]] = []
    for probe_index in range(3):
        try:
            module = _import_generated_main_module(scripts_dir, context.ontology.name)
            registry = getattr(module, "mcp", None)
            inventory = _fastmcp_tools(registry)
            exposed = {str(name) for name in inventory}
            probe_inventories.append(exposed)
            probe_tools.append(dict(inventory))
        except Exception as exc:
            fail(
                f"artifact:main.py#mcp-surface-probe-{probe_index + 1}",
                "main.py MCP tool-surface probe failed on independent startup "
                f"{probe_index + 1}/3: {type(exc).__name__}: {exc}",
                subject_kind="artifact",
                artifact="main.py",
                probe_index=probe_index + 1,
            )
    if probe_inventories and any(
        inventory != probe_inventories[0] for inventory in probe_inventories[1:]
    ):
        fail(
            "artifact:main.py#mcp-surface-stability",
            "main.py exposes an unstable MCP tool surface across three independent starts",
            subject_kind="artifact",
            artifact="main.py",
            probe_inventories=[sorted(value) for value in probe_inventories],
        )
    for probe_index, actual_tools in enumerate(probe_inventories, start=1):
        missing = sorted(expected_tools - actual_tools)
        extra = sorted(actual_tools - expected_tools)
        if missing or extra:
            fail(
                f"artifact:main.py#mcp-surface-equality-{probe_index}",
                "main.py MCP tool registry differs from the runtime-derived closed "
                f"surface on startup {probe_index}/3: missing={missing}, extra={extra}",
                subject_kind="artifact",
                artifact="main.py",
                probe_index=probe_index,
                expected_tools=sorted(expected_tools),
                actual_tools=sorted(actual_tools),
                missing_tools=missing,
                extra_tools=extra,
            )

    allowed_modules = {
        Path(owner).stem for owner in surface_contract["tool_owners"].values()
    }
    lifecycle_tools = set(surface_contract["lifecycle_tools"])
    fixed_runtime_tools = {
        *lifecycle_tools,
        *(
            {"create_om2_quantity"}
            if "create_om2_quantity" in surface_contract["expected_mcp_tools"]
            else set()
        ),
    }
    if probe_tools:
        for tool_name, tool in probe_tools[0].items():
            handler = getattr(tool, "fn", None)
            module_name = str(getattr(handler, "__module__", ""))
            owner_module = module_name.rsplit(".", 1)[-1]
            commit_gated_export = (
                tool_name == "export_memory"
                and bool(
                    (
                        context.contract.get("materialization_operation_units")
                        or {}
                    ).get("merged_predicate_locals")
                )
            )
            if commit_gated_export:
                provenance_ok = handler is not None and owner_module == "main"
            elif tool_name in fixed_runtime_tools:
                provenance_ok = handler is not None and owner_module == "_fixed_rdf_runtime"
            else:
                provenance_ok = (
                    handler is not None
                    and owner_module in allowed_modules
                    and Path(surface_contract["tool_owners"].get(tool_name, "")).stem
                    == owner_module
                )
            if not provenance_ok:
                fail(
                    f"tool:{tool_name}#handler-provenance",
                    f"MCP tool {tool_name!r} has disallowed handler provenance "
                    f"{module_name!r}",
                    subject_kind="tool",
                    tool_name=tool_name,
                    handler_module=module_name,
                )
            parameters = getattr(tool, "parameters", None)
            properties = (
                parameters.get("properties")
                if isinstance(parameters, Mapping)
                else None
            )
            if not isinstance(properties, Mapping):
                fail(
                    f"tool:{tool_name}#parameter-schema",
                    f"MCP tool {tool_name!r} does not expose an inspectable object schema",
                    subject_kind="tool",
                    tool_name=tool_name,
                )
                continue
            forbidden_parameters = sorted(
                str(name)
                for name in properties
                if re.search(
                    r"(?:^|_)(?:predicate|triple|graph|file|path|turtle)(?:_|$)",
                    str(name),
                    flags=re.IGNORECASE,
                )
                or (
                    re.search(
                        r"(?:^|_)class(?:_|$)",
                        str(name),
                        flags=re.IGNORECASE,
                    )
                    and str(name) != "quantity_class_iri"
                )
            )
            if forbidden_parameters:
                fail(
                    f"tool:{tool_name}#parameter-schema",
                    f"MCP tool {tool_name!r} exposes caller-selected generic parameters: "
                    f"{forbidden_parameters}",
                    subject_kind="tool",
                    tool_name=tool_name,
                    forbidden_parameters=forbidden_parameters,
                )

    for tool_name in sorted(expected_tools):
        if probe_inventories and tool_name not in probe_inventories[0]:
            fail(
                f"tool:{tool_name}#declared",
                f"Missing runtime-derived MCP tool: {tool_name}",
                subject_kind="tool",
                tool_name=tool_name,
            )

    return failures, warnings, obligations


def _ordered_member_contract_report(
    context: AgenticGenerationContext,
) -> tuple[list[str], list[str], list[dict[str, Any]]]:
    """Defer ordering semantics to runtime graph probes, not source-code shape."""
    profile = context.contract.get("ordered_member_profile") or {}
    if not (
        profile.get("ordered_member_classes")
        and profile.get("single_valued_ordering_properties")
    ):
        return [], [], []
    return [], [
        "Ordered-member implementation shape is not statically constrained; "
        "runtime graph validation owns datatype, positivity, uniqueness, and parent typing."
    ], []


def _relationship_param_description_report(
    context: AgenticGenerationContext,
) -> tuple[list[str], list[str]]:
    failures: list[str] = []
    warnings: list[str] = []
    scripts_dir = Path(context.scripts_dir)
    rel_paths = sorted(scripts_dir.glob("*_creation_relationships.py"))
    if not rel_paths:
        warnings.append(
            "Relationship parameter description validation skipped because relationships module is missing"
        )
        return failures, warnings

    def _py_name_local(name: str) -> str:
        out = re.sub(r"\W+", "_", str(name or "")).strip("_")
        if not out:
            out = "unnamed"
        if out[:1].isdigit():
            out = "_" + out
        return out

    from src.extraction_prompt_generation.compile.operation_units import (
        standalone_relationship_tool_contracts,
    )

    object_props = standalone_relationship_tool_contracts(
        context.contract.get("relationship_tool_contracts") or {
        name: {"predicate_local": name}
        for name, prop in (context.parsed.get("properties") or {}).items()
        if (prop or {}).get("kind") == "object"
        },
        context.contract.get("materialization_operation_units") or {},
    )
    rel_source = rel_paths[0]
    try:
        tree = ast.parse(rel_source.read_text(encoding="utf-8", errors="replace"))
    except (OSError, SyntaxError) as exc:
        failures.append(f"{rel_source.name}: AST parse failed: {type(exc).__name__}: {exc}")
        return failures, warnings
    if any(
        isinstance(node, ast.ImportFrom)
        and node.module == "__future__"
        and any(alias.name == "annotations" for alias in node.names)
        for node in tree.body
    ):
        failures.append(
            f"{rel_source.name}: must not enable deferred annotations because the "
            "installed FastMCP/Pydantic runtime cannot resolve imported Field metadata"
        )

    fndefs: dict[str, ast.FunctionDef] = {
        node.name: node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
    }
    for prop_local, spec in object_props.items():
        fn_name = f"add_{_py_name_local(prop_local)}"
        fndef = fndefs.get(fn_name)
        if not fndef:
            # other validator covers missing tool surface
            continue
        subject_param = next(
            (p for p in fndef.args.args if p.arg == "subject_iri"),
            None,
        )
        obj_param = next(
            (p for p in fndef.args.args if p.arg == "object_iri"),
            None,
        )
        all_params = [*fndef.args.args, *fndef.args.kwonlyargs]
        token_param = next(
            (
                parameter
                for parameter in all_params
                if parameter.arg == "reuse_authorization_token"
            ),
            None,
        )
        if token_param is None:
            failures.append(
                f"{rel_source.name}: {fn_name} must expose optional "
                "reuse_authorization_token and pass it to the fixed relationship writer"
            )
        if subject_param is None:
            failures.append(
                f"{rel_source.name}: {fn_name} missing subject_iri parameter"
            )
        if obj_param is None:
            failures.append(
                f"{rel_source.name}: {fn_name} missing object_iri parameter"
            )
    return failures, warnings
