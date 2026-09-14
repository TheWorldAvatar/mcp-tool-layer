"""Live OM-2 compact matcher for source tests and OX export.

Generated MCP packages do not import this module. The v2 generator emits
``_fixed_om2_runtime.py`` from ``overlay/om2_compact.py`` plus compiled tables.
"""

from __future__ import annotations

from src.kg_building_mcp_generation_v2.overlay import om2_compact
from src.kg_building_mcp_generation_v2.overlay.quantity_surface import (
    compile_om2_runtime_tables,
)

om2_compact.OM2_UNIT_MAP, om2_compact._UNIT_QUANTITY_CLASSES = (
    compile_om2_runtime_tables()
)

from src.kg_building_mcp_generation_v2.overlay.om2_compact import (  # noqa: E402
    OM2,
    find_or_create_om2_quantity,
    find_or_create_om2_quantity_from_label,
    parse_om2_quantity_label,
    resolve_om2_unit,
    resolve_qualitative_quantity_preset,
)

OM2_UNIT_MAP = om2_compact.OM2_UNIT_MAP
_UNIT_QUANTITY_CLASSES = om2_compact._UNIT_QUANTITY_CLASSES

__all__ = [
    "OM2",
    "OM2_UNIT_MAP",
    "_UNIT_QUANTITY_CLASSES",
    "find_or_create_om2_quantity",
    "find_or_create_om2_quantity_from_label",
    "parse_om2_quantity_label",
    "resolve_om2_unit",
    "resolve_qualitative_quantity_preset",
]
