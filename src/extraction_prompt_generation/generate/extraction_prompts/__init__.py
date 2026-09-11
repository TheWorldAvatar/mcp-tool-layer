"""Extraction-prompt authoring (default model: gpt-5). No KG-building or ONEPASS track.

`author.py` writes prompts; `slots.py` creates empty files. See README.md.
"""

from src.extraction_prompt_generation.generate.extraction_prompts.author import (
    run_extraction_prompt_generation,
)
from src.extraction_prompt_generation.generate.extraction_prompts.slots import (
    write_extraction_prompt_slots,
)

__all__ = [
    "run_extraction_prompt_generation",
    "write_extraction_prompt_slots",
]
