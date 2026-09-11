"""Operation-unit consistency checks against generated manifests.

Compares compiled units to generated `__all__` and signatures.
See report/README.md.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

from src.extraction_prompt_generation.compile.context import (
    AgenticGenerationContext,
)
from src.extraction_prompt_generation.generate.artifact_surface import (
    _literal_all_manifest,
)


def _operation_unit_contract_report(
    context: AgenticGenerationContext,
) -> tuple[list[str], list[str], list[dict[str, Any]]]:
    """Validate operation units against generated signatures and public manifests."""
    failures: list[str] = []
    warnings: list[str] = []
    obligations: list[dict[str, Any]] = []
    compiled = context.contract.get("materialization_operation_units") or {}
    errors = [str(item) for item in compiled.get("errors") or []]
    failures.extend(errors)
    candidates = context.contract.get("materialization_operation_candidates") or {}
    decisions = context.contract.get("materialization_operation_decisions") or {}
    if candidates or decisions:
        raise ValueError(
            "inferred atomic operation decisions are not supported in this package"
        )
    merged = {
        str(value)
        for value in compiled.get("merged_predicate_locals") or []
        if str(value)
    }
    edge_counts: dict[str, int] = {}
    for unit in compiled.get("units") or []:
        creator = (unit or {}).get("creator_contract") or {}
        for edge in creator.get("required_edges") or []:
            local = str(edge.get("predicate_local") or "")
            if local:
                edge_counts[local] = edge_counts.get(local, 0) + 1
    for local in sorted(merged):
        if edge_counts.get(local, 0) < 1:
            failures.append(
                f"Merged predicate {local} is not owned by any operation unit"
            )

    scripts_dir = Path(context.scripts_dir)
    relationship_paths = sorted(scripts_dir.glob("*_creation_relationships.py"))
    if len(relationship_paths) == 1:
        try:
            manifest = set(_literal_all_manifest(relationship_paths[0]))
        except (OSError, SyntaxError, ValueError) as exc:
            warnings.append(
                "Could not validate merged-edge exclusivity because the relationship "
                f"manifest is invalid: {type(exc).__name__}: {exc}"
            )
        else:
            leaked = sorted(
                f"add_{local}" for local in merged if f"add_{local}" in manifest
            )
            if leaked:
                failures.append(
                    "Creator-owned predicates remain publicly exposed: "
                    + ", ".join(leaked)
                )

    entity_paths = sorted(scripts_dir.glob("*_creation_entities.py"))
    if len(entity_paths) == 1:
        try:
            tree = ast.parse(entity_paths[0].read_text(encoding="utf-8"))
        except Exception:
            tree = None
        if tree is not None:
            functions = {
                node.name: node
                for node in tree.body
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
            for unit in compiled.get("units") or []:
                creator_contract = (unit or {}).get("creator_contract") or {}
                required_edges = creator_contract.get("required_edges") or []
                if not required_edges:
                    continue
                tool_name = str(creator_contract.get("public_tool") or "")
                tool = functions.get(tool_name)
                if tool is None:
                    continue
                parameter_names = {
                    argument.arg
                    for argument in [
                        *tool.args.posonlyargs,
                        *tool.args.args,
                        *tool.args.kwonlyargs,
                    ]
                }
                required_names = set()
                for edge in required_edges:
                    if edge.get("target_resolution") == "existing_iri_parameter":
                        required_names.add(str(edge.get("parameter_name") or ""))
                    if edge.get("target_resolution") == "same_operation_create":
                        required_names.add(str(edge.get("label_parameter") or ""))
                        required_names.update(
                            str(item.get("parameter_name") or "")
                            for item in edge.get("datatype_inputs") or []
                            if item.get("required")
                        )
                missing = sorted(
                    name for name in required_names if name and name not in parameter_names
                )
                if missing:
                    failures.append(
                        f"{tool_name} omits required atomic-operation parameters: {missing}"
                    )
    return failures, warnings, obligations
