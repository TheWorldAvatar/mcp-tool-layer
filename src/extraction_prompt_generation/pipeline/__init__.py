"""Public pipeline entry points. Submodules are imported lazily.

`run_agentic_generation_experiment` is the CLI orchestrator. See README.md.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "generate_deterministic_prompt_slice",
    "generate_runtime_support_slice",
    "run_agentic_generation_experiment",
]


def __getattr__(name: str) -> Any:
    if name == "generate_deterministic_prompt_slice":
        from src.extraction_prompt_generation.pipeline.prompts import (
            generate_deterministic_prompt_slice,
        )

        return generate_deterministic_prompt_slice
    if name == "generate_runtime_support_slice":
        from src.extraction_prompt_generation.pipeline.runtime_support import (
            generate_runtime_support_slice,
        )

        return generate_runtime_support_slice
    if name == "run_agentic_generation_experiment":
        from src.extraction_prompt_generation.pipeline.experiment import (
            run_agentic_generation_experiment,
        )

        return run_agentic_generation_experiment
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
