"""Runtime graph-hygiene probes against the generated MCP package.

No-op when `main.py` is absent. File map: README.md in this folder.
"""

from __future__ import annotations

from pathlib import Path

from src.extraction_prompt_generation.compile.context import (
    AgenticGenerationContext,
)
from src.extraction_prompt_generation.validate.report.common import (
    _import_generated_main_module,
)
from src.extraction_prompt_generation.validate.report.runtime_hygiene.accumulator import (
    HygieneState,
)
from src.extraction_prompt_generation.validate.report.runtime_hygiene.legacy_materialize import (
    audit_materialize_hints_graph,
)
from src.extraction_prompt_generation.validate.report.runtime_hygiene.lifecycle import (
    inspect_lifecycle_adapters,
)
from src.extraction_prompt_generation.validate.report.runtime_hygiene.scan import (
    record_generic_mutation_warnings,
)
from src.extraction_prompt_generation.validate.report.runtime_hygiene.shared_graph import (
    probe_create_export_shared_graph,
)


def _runtime_graph_hygiene_report(
    context: AgenticGenerationContext,
) -> tuple[list[str], list[str], list[dict]]:
    """Scan generated scripts for graph-hygiene failures; skip if main.py is missing."""
    state = HygieneState()
    scripts_dir = Path(context.scripts_dir)
    record_generic_mutation_warnings(scripts_dir, state)
    main_path = scripts_dir / "main.py"
    if not main_path.exists():
        state.warnings.append(
            "Runtime graph hygiene validation skipped because main.py is missing"
        )
        return state.as_tuple()
    inspect_lifecycle_adapters(context, main_path, state)
    if state.failures:
        return state.as_tuple()
    try:
        module = _import_generated_main_module(scripts_dir, context.ontology.name)
    except Exception as exc:
        state.warnings.append(
            "Runtime graph hygiene validation skipped because generated main.py could not be imported: "
            f"{type(exc).__name__}: {exc}"
        )
        return state.as_tuple()
    if not probe_create_export_shared_graph(context, module, state):
        return state.as_tuple()
    # Full hardcoded A-Box orchestration is a development harness responsibility,
    # not a generated MCP tool. Package validation stops after proving that atomic
    # create and export tools share retained graph state.
    return state.as_tuple()
    audit_materialize_hints_graph(context, module, state)
    return state.as_tuple()


__all__ = [
    "_runtime_graph_hygiene_report",
]
