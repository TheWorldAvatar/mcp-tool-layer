"""Fail-soft policy: exhaust retries, keep partial output, do not abort the paper."""

from __future__ import annotations

from pathlib import Path

SHAPE_RETRY_ATTEMPTS = 5

EXTRACTION_STEP_MARKERS = {
    "top_entity_extraction": "top_entities.txt",
    "main_ontology_extractions": ".main_ontology_extractions_done",
    "extensions_extractions": ".extensions_extractions_done",
}


def warn_continue(scope: str, detail: str) -> None:
    print(f"    [WARN] {scope}: {detail}; continuing with partial output")


def extraction_steps_complete(doi_folder: str | Path, steps: list[str]) -> bool:
    folder = Path(doi_folder)
    for step in steps:
        marker = EXTRACTION_STEP_MARKERS.get(step)
        if not marker:
            continue
        path = folder / marker
        if not path.is_file() or path.stat().st_size <= 0:
            return False
    return True
