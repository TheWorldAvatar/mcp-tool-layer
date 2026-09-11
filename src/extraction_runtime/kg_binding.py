"""Compatibility wrapper. Binding text lives in src.kg_building.pipeline.binding."""

from src.kg_building.pipeline.binding import (
    NO_CONTRACT_USER,
    bind_kg_runtime_context,
    pin_entity_context,
)

__all__ = ["NO_CONTRACT_USER", "bind_kg_runtime_context", "pin_entity_context"]
