"""Lock ontosynthesis extract to live PDF→MD (0827-style) then gpt-4.1 T-Box slim."""

from __future__ import annotations

from typing import Any

LOCKED_PREFIX = ("pdf_conversion", "tbox_slim")
LOCKED_EXTRACT_STEPS = [
    "pdf_conversion",
    "tbox_slim",
    "top_entity_extraction",
    "top_entity_kg_building",
    "main_ontology_extractions",
]
SLIM_EXTRACT_STEPS = list(LOCKED_EXTRACT_STEPS)
_EXTRACT_MARKERS = {
    "top_entity_extraction",
    "main_ontology_extractions",
}


def _ensure_locked_prefix(steps: list[str]) -> list[str]:
    out = list(steps)
    if "tbox_slim" not in out:
        insert_at = next(
            (
                index
                for index, name in enumerate(out)
                if name in _EXTRACT_MARKERS or name == "top_entity_kg_building"
            ),
            0,
        )
        prefix = [name for name in LOCKED_PREFIX if name not in out]
        return out[:insert_at] + prefix + out[insert_at:]
    if "pdf_conversion" not in out:
        out.insert(out.index("tbox_slim"), "pdf_conversion")
    return out


def apply_slim_ontosynthesis_extract_defaults(
    config: dict[str, Any],
    *,
    steps_were_explicit: bool = False,
) -> dict[str, Any]:
    """Always convert from PDF, then slim. Do not seed or reuse markdown."""
    del steps_were_explicit
    if str(config.get("ontology") or "") != "ontosynthesis":
        return config
    if str(config.get("experiment_protocol") or "").strip():
        return config
    steps = list(config.get("steps") or [])
    if steps == ["main_kg_building"]:
        return config
    if not steps:
        config["steps"] = list(LOCKED_EXTRACT_STEPS)
        steps = list(config["steps"])
    elif _EXTRACT_MARKERS.intersection(steps):
        config["steps"] = _ensure_locked_prefix(steps)
        steps = list(config["steps"])
    if _EXTRACT_MARKERS.intersection(steps):
        config.pop("reuse_conversion_artifacts_from", None)
        config["reuse_stitched_markdown"] = False
        config["compare_one_shot"] = True
    return config
