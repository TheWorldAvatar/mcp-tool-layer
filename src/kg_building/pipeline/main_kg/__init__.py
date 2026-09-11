"""Main-ontology KG building via generated MCP tools."""

from __future__ import annotations

from typing import Any

__all__ = ["run_step"]


def __getattr__(name: str) -> Any:
    if name == "run_step":
        from src.kg_building.pipeline.main_kg.run import run_step

        return run_step
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
