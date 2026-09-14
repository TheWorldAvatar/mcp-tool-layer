"""Write deterministic creation modules plus the occurrence MCP surface."""

from __future__ import annotations

import json
from pathlib import Path

from src.extraction_prompt_generation.compile.context import (
    AgenticGenerationContext,
)
from src.kg_building_mcp_generation_v2.emit.creation_base import _base_script
from src.kg_building_mcp_generation_v2.emit.creation_checks import _checks_script
from src.kg_building_mcp_generation_v2.emit.creation_entities import _entities_script
from src.kg_building_mcp_generation_v2.emit.creation_helpers import _py_name
from src.kg_building_mcp_generation_v2.emit.creation_relationships import (
    _relationships_script,
)
from src.kg_building_mcp_generation_v2.emit.main import emit_occurrence_main
from src.kg_building_mcp_generation_v2.emit.operations import emit_occurrence_operations
from src.kg_building_mcp_generation_v2.emit.sidecars import (
    emit_occurrence_argument_ownership,
    emit_occurrence_loop_guard,
)
from src.kg_building_mcp_generation_v2.overlay import ACTIVE_SURFACE
from src.kg_building_mcp_generation_v2.overlay.om2_runtime_emit import emit_om2_runtime
from src.kg_building_mcp_generation_v2.overlay.quantity_surface import (
    compile_quantity_surface,
)
from src.kg_building_mcp_generation_v2.surface.helpers import (
    ARGUMENT_OWNERSHIP_FILENAME,
    LOOP_GUARD_FILENAME,
)


def generate_deterministic_script_slice(
    context: AgenticGenerationContext,
) -> list[str]:
    """Write creation modules and overwrite main.py with the occurrence surface."""
    scripts_dir = Path(context.scripts_dir)
    scripts_dir.mkdir(parents=True, exist_ok=True)
    ontology = _py_name(context.ontology.name)
    files = {
        "__init__.py": "",
        f"{ontology}_creation_base.py": _base_script(context),
        f"{ontology}_creation_checks.py": _checks_script(context),
        f"{ontology}_creation_entities.py": _entities_script(context),
        f"{ontology}_creation_relationships.py": _relationships_script(context),
    }
    occurrence = (context.contract.get("occurrence_surface_units") or {})
    files[f"{ontology}_occurrence_operations.py"] = emit_occurrence_operations(
        context, occurrence
    )
    files["main.py"] = emit_occurrence_main(context, occurrence)
    files[LOOP_GUARD_FILENAME] = emit_occurrence_loop_guard(occurrence)
    files[ARGUMENT_OWNERSHIP_FILENAME] = emit_occurrence_argument_ownership(
        occurrence
    )
    surface = ACTIVE_SURFACE or compile_quantity_surface(occurrence, context=context)
    files["_om2_quantity_surface.json"] = (
        json.dumps(surface, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    )
    files["_fixed_om2_runtime.py"] = emit_om2_runtime(surface)
    written: list[str] = []
    for name, content in files.items():
        path = scripts_dir / name
        path.write_text(content, encoding="utf-8")
        written.append(str(path))
    return written
