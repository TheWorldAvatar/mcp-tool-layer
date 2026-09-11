"""Create empty extraction-prompt slots. KG-building files are never written.

Delegates to `pipeline.prompts.generate_deterministic_prompt_slice`.
See extraction_prompts/README.md.
"""

from __future__ import annotations

from typing import Any


def write_extraction_prompt_slots(context: Any) -> list[str]:
    """Write EXTRACTION / PRE_EXTRACTION slots and compiled companions."""
    from src.extraction_prompt_generation.pipeline.prompts import (
        generate_deterministic_prompt_slice,
    )

    return generate_deterministic_prompt_slice(context)
