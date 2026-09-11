"""Compatibility wrapper. Extension KG building lives in src.kg_building."""

from src.kg_building.pipeline.extension import resolve_enrichment_targets, run_step

__all__ = ["resolve_enrichment_targets", "run_step"]
