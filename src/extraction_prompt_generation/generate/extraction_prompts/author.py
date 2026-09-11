"""Run GPT-5 exact-edits for extraction prompts only.

Track entry used by `pipeline.experiment`. Does not touch MCP scripts.
See extraction_prompts/README.md.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from models.locked_llm import LOCKED_GENERATION_MODEL
from src.extraction_prompt_generation.compile.context import (
    AgenticGenerationContext,
)


def run_extraction_prompt_generation(
    context: AgenticGenerationContext,
    *,
    model_name: str = LOCKED_GENERATION_MODEL,
    foreign_contracts: list[dict[str, Any]] | None = None,
    max_generation_workers: int = 5,
    target_artifacts: list[str] | None = None,
    protected_artifacts: dict[Path, bytes] | None = None,
) -> dict[str, Any]:
    """Author EXTRACTION / PRE_EXTRACTION prompts. Does not touch MCP scripts."""
    from src.extraction_prompt_generation.generate.authoring import (
        run_pure_llm_generation_rounds,
    )

    return run_pure_llm_generation_rounds(
        context,
        model_name=LOCKED_GENERATION_MODEL,
        foreign_contracts=foreign_contracts,
        generate_prompts=True,
        parallel_generation=True,
        max_generation_workers=max_generation_workers,
        edit_backend="exact_edits",
        target_artifacts=target_artifacts,
        protected_artifacts=protected_artifacts,
    )
