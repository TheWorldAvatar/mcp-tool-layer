"""Shared-base stage checks for the frozen RDF/OM-2 adapter.

`_creation_base.py` must expose only `rdf_runtime`. See stage/README.md.
"""

from __future__ import annotations

import ast

from src.extraction_prompt_generation.validate.report.stage.context import (
    StageProbe,
)


def validate_creation_base(probe: StageProbe) -> None:
    """Require a minimal adapter that does not leak domain tools or bad OM-2 imports."""
    if probe.imported_module is not None:
        if getattr(probe.imported_module, "__all__", None) != ["rdf_runtime"]:
            probe.fail(
                f"{probe.name}: shared base must expose exactly the stable `rdf_runtime` module alias"
            )
        leaked_domain_tools = sorted(
            symbol
            for symbol, value in vars(probe.imported_module).items()
            if symbol.startswith(("create_", "add_")) and callable(value)
        )
        if leaked_domain_tools:
            probe.fail(
                f"{probe.name}: shared base defines domain-bound tools owned by later layers: "
                + ", ".join(leaked_domain_tools)
            )
        wrapper_functions = [
            node.name
            for node in (probe.artifact_tree.body if probe.artifact_tree is not None else [])
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        if wrapper_functions:
            probe.fail(
                f"{probe.name}: minimal fixed-runtime adapter must not duplicate function wrappers: "
                + ", ".join(wrapper_functions)
            )
    helper_names = {
        "find_or_create_om2_quantity",
        "find_or_create_om2_quantity_from_label",
        "resolve_om2_unit",
        "parse_om2_quantity_label",
    }
    has_rel_fixed_from_import = False
    has_bad_fixed_import = False
    imports_any_om2 = False
    references_helpers = False
    try:
        tree = ast.parse(probe.text)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if node.level >= 1 and module == "_fixed_om2_runtime":
                    has_rel_fixed_from_import = True
                    imports_any_om2 = True
                if module in ("fixed_om2_runtime", "om2.runtime.fixed"):
                    has_bad_fixed_import = True
                    imports_any_om2 = True
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in ("fixed_om2_runtime", "om2.runtime.fixed"):
                        has_bad_fixed_import = True
                        imports_any_om2 = True
            if isinstance(node, ast.Name) and node.id in helper_names:
                references_helpers = True
            elif isinstance(node, ast.Attribute) and node.attr in helper_names:
                references_helpers = True
    except SyntaxError:
        has_rel_fixed_from_import = "from ._fixed_om2_runtime import" in probe.text
        has_bad_fixed_import = (
            "from fixed_om2_runtime import" in probe.text
            or "import fixed_om2_runtime" in probe.text
            or "om2.runtime.fixed" in probe.text
        )
        references_helpers = any(name in probe.text for name in helper_names)
        imports_any_om2 = has_rel_fixed_from_import or has_bad_fixed_import

    uses_om2 = imports_any_om2 or references_helpers
    if uses_om2:
        if not has_rel_fixed_from_import:
            probe.fail(
                f"{probe.name}: OM-2 foundation must use a package-relative import "
                "from `._fixed_om2_runtime`"
            )
        if has_bad_fixed_import:
            probe.fail(
                f"{probe.name}: OM-2 foundation uses a non-package fixed-runtime import"
            )
