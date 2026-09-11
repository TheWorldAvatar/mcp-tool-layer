"""Mechanical validation reports for generated prompts and MCP scripts.

`build_validation_report` writes `generation_report.json`. See README.md.
"""

from src.extraction_prompt_generation.validate.report.assemble import (
    build_validation_report,
)
from src.extraction_prompt_generation.validate.report.prompts import (
    validate_prompt_runtime_bindings,
)

__all__ = [
    "build_validation_report",
    "validate_prompt_runtime_bindings",
]
