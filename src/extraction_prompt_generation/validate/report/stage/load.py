"""Load the active stage artifact and open its retained-graph probe session.

Resolves the newest authored file, imports it if Python, and opens the
retained graph for runtime probes. See stage/README.md.
"""

from __future__ import annotations

import ast
import importlib
import importlib.util
import sys
import types
from pathlib import Path

from src.extraction_prompt_generation.compile.context import (
    AgenticGenerationContext,
)
from src.extraction_prompt_generation.validate.report.stage.context import (
    StageProbe,
)


def resolve_stage_artifact(
    context: AgenticGenerationContext,
    active_artifacts: list[str],
) -> tuple[StageProbe | None, tuple[list[str], list[str], list[str]] | None]:
    """Return the probe, or an early report when the artifact cannot be loaded."""
    if not active_artifacts:
        return None, (["Stage validation requires at least one active artifact"], [], [])
    relative = Path(active_artifacts[-1]).as_posix()
    root = Path(
        getattr(
            context,
            "output_root",
            Path(context.scripts_dir).resolve().parents[1],
        )
    )
    path = root / relative
    if not path.is_file():
        return None, ([f"Active stage artifact is missing: {relative}"], [], [relative])
    text = path.read_text(encoding="utf-8", errors="replace")
    probe = StageProbe(context=context, path=path, relative=relative, text=text)
    if not text.strip():
        probe.fail(f"{path.name}: generated artifact is empty")
        return None, (probe.failures, probe.warnings, [relative])
    return probe, None


def import_stage_python(probe: StageProbe) -> None:
    """Import the generated artifact with frozen sibling dependencies."""
    package_name = (
        f"_agentic_stage_{probe.context.ontology.name}_"
        f"{abs(hash(str(probe.path.parent.resolve())))}"
    )
    probe.package_name = package_name
    for module_name in list(sys.modules):
        if module_name == package_name or module_name.startswith(package_name + "."):
            del sys.modules[module_name]
    package = types.ModuleType(package_name)
    package.__path__ = [str(probe.path.parent.resolve())]  # type: ignore[attr-defined]
    sys.modules[package_name] = package
    imported_module_name = f"{package_name}.{probe.path.stem}"
    try:
        spec = importlib.util.spec_from_file_location(imported_module_name, probe.path)
        if spec is None or spec.loader is None:
            raise ImportError("could not create module spec")
        imported_module = importlib.util.module_from_spec(spec)
        sys.modules[imported_module_name] = imported_module
        exec(compile(probe.text, str(probe.path), "exec"), imported_module.__dict__)
        probe.imported_module = imported_module
    except Exception as exc:
        probe.fail(
            f"{probe.name}: active artifact import failed with frozen dependencies: "
            f"{type(exc).__name__}: {exc}"
        )
        probe.imported_module = None
    try:
        probe.artifact_tree = ast.parse(probe.text, filename=str(probe.path))
    except SyntaxError:
        probe.artifact_tree = None


def check_public_tool_signatures(probe: StageProbe) -> None:
    """Require explicit publishable signatures on public create_/add_ tools."""
    if probe.artifact_tree is None:
        return
    public_prefixes = ("create_", "add_")
    for node in probe.artifact_tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not node.name.startswith(public_prefixes):
            continue
        if node.args.vararg is not None or node.args.kwarg is not None:
            probe.fail(
                f"{probe.name}: public tool `{node.name}` uses *args/**kwargs; "
                "the actual definition must have an explicit publishable signature"
            )
        if probe.imported_module is not None and not callable(
            getattr(probe.imported_module, node.name, None)
        ):
            probe.fail(
                f"{probe.name}: public tool `{node.name}` is not callable after real import"
            )


def begin_retained_graph(probe: StageProbe) -> bool:
    """Isolate the retained graph for this stage probe. False if it cannot start."""
    try:
        runtime = importlib.import_module(f"{probe.package_name}._fixed_rdf_runtime")
        canonical_registry_key = runtime._REGISTRY_KEY
        probe_registry_key = (
            f"{canonical_registry_key}::stage-probe::{probe.package_name}"
        )
        runtime._REGISTRY_KEY = probe_registry_key
        graph = runtime.retained_graph()
        runtime.reset_graph(graph)
    except Exception as exc:
        probe.fail(
            f"{probe.name}: retained-graph behavior probe could not start: "
            f"{type(exc).__name__}: {exc}"
        )
        return False
    probe.runtime = runtime
    probe.graph = graph
    probe.canonical_registry_key = canonical_registry_key
    probe.probe_registry_key = probe_registry_key
    return True


def end_retained_graph(probe: StageProbe) -> None:
    """Restore the isolated retained-graph registry after probes finish."""
    runtime = probe.runtime
    graph = probe.graph
    if runtime is None or graph is None:
        return
    try:
        runtime.reset_graph(graph)
    finally:
        runtime._graph_registry().pop(probe.probe_registry_key, None)
        runtime._REGISTRY_KEY = probe.canonical_registry_key
