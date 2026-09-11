"""Turtle export parse checks.

Skipped when no `*.ttl` exports exist yet. See report/README.md.
"""

from __future__ import annotations

from pathlib import Path

from rdflib import Graph

from src.extraction_prompt_generation.compile.context import (
    AgenticGenerationContext,
)


def _ttl_export_report(
    context: AgenticGenerationContext,
) -> tuple[list[str], list[str]]:
    """Parse generated Turtle exports; skip when none exist yet."""
    failures: list[str] = []
    warnings: list[str] = []
    ttl_files = sorted(
        Path(context.output_root).glob(f"**/{context.ontology.name}*.ttl")
    )
    if not ttl_files:
        warnings.append("No generated TTL exports found; TTL parse validation skipped")
        return failures, warnings
    for ttl_file in ttl_files:
        try:
            Graph().parse(ttl_file, format="turtle")
        except Exception as exc:
            failures.append(f"{ttl_file}: Turtle parse failed: {exc}")
    return failures, warnings
