"""Derive generated-package public surfaces from explicit artifact manifests."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any


LIFECYCLE_TOOL_NAMES = ("init_memory", "export_memory")
_SIBLING_SUFFIXES = (
    "_creation_entities.py",
    "_creation_relationships.py",
    "_creation_checks.py",
)


def _literal_all_manifest(path: Path) -> list[str]:
    """Read a module's literal ``__all__`` without importing generated code."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    manifests: list[list[str]] = []
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if not any(isinstance(target, ast.Name) and target.id == "__all__" for target in targets):
            continue
        value = ast.literal_eval(node.value)
        if not isinstance(value, (list, tuple)) or not all(
            isinstance(item, str) and item.strip() for item in value
        ):
            raise ValueError(f"{path.name}: __all__ must be a literal list of names")
        manifests.append(list(value))
    if len(manifests) != 1:
        raise ValueError(f"{path.name}: expected exactly one literal __all__ manifest")
    if len(manifests[0]) != len(set(manifests[0])):
        raise ValueError(f"{path.name}: __all__ contains duplicate names")
    return manifests[0]


def derive_main_surface_contract(scripts_dir: str | Path) -> dict[str, Any]:
    """Derive the closed MCP surface from this run's validated sibling manifests."""
    root = Path(scripts_dir)
    sources: dict[str, str] = {}
    owners: dict[str, str] = {}
    sibling_tools: list[str] = []
    for suffix in _SIBLING_SUFFIXES:
        matches = sorted(root.glob(f"*{suffix}"))
        if len(matches) != 1:
            raise ValueError(
                f"Expected exactly one generated sibling matching *{suffix}; "
                f"found {[path.name for path in matches]}"
            )
        path = matches[0]
        manifest = _literal_all_manifest(path)
        sources[path.name] = "literal __all__"
        for name in manifest:
            if name in owners:
                raise ValueError(
                    f"Public tool {name!r} is claimed by both {owners[name]} and {path.name}"
                )
            owners[name] = path.name
            sibling_tools.append(name)

    lifecycle_tools = list(LIFECYCLE_TOOL_NAMES)
    return {
        "surface_policy": "closed_world_exact_manifest_surface",
        "lifecycle_tools": lifecycle_tools,
        "sibling_tools": sorted(sibling_tools),
        "expected_mcp_tools": sorted(lifecycle_tools + sibling_tools),
        "tool_owners": owners,
        "manifest_sources": sources,
        "derivation": (
            "Lifecycle names are domain-generic architecture; every domain-dependent "
            "tool is read from this run's generated sibling literal __all__ manifest."
        ),
        "fastmcp_framework": {
            "import": "from fastmcp import FastMCP",
            "server": "mcp = FastMCP(name=<ontology name>)",
            "registration": "mcp.tool(name=<exact manifest name>)(callable)",
            "prohibited": (
                "custom registry, registry facade, tool dictionary exposed as mcp, "
                "or locally implemented FastMCP replacement"
            ),
        },
    }
