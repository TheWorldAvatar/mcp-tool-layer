"""Runtime slot contract for extension extraction prompts.

Extension extraction prompts receive only the parent-entity identity the
pipeline already selected. They must not ask the model to re-read the paper
or a parent A-Box through extra placeholders. See compile/README.md.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


EXTENSION_EXTRACTION_RUNTIME_SLOTS = (
    "{entity_label}",
    "{entity_uri}",
)

FORBIDDEN_EXTENSION_EXTRACTION_RUNTIME_SLOTS = (
    "{paper_content}",
    "{iteration_hints}",
    "{top_entities}",
    "{hints}",
    "{main_ontology_a_box}",
)


def extension_extraction_runtime_policy() -> dict[str, Any]:
    """In-code slot contract. No external meta-prompt files are loaded."""
    return {
        "canonical_runtime_slots": list(EXTENSION_EXTRACTION_RUNTIME_SLOTS),
        "forbidden_runtime_slots": list(FORBIDDEN_EXTENSION_EXTRACTION_RUNTIME_SLOTS),
    }


def is_extraction_prompt(path: Path | str) -> bool:
    name = Path(path).name
    return name.startswith("EXTRACTION_ITER_") and name.endswith(".md")
