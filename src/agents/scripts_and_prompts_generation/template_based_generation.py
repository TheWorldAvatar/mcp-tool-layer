#!/usr/bin/env python3
"""
template_based_generation.py

Deprecated shim for the old template-only generator.

This module previously contained ontology- and domain-specific assumptions that
violated the repository rule that domain knowledge must come only from the
provided T-Box TTL input. To avoid silently reintroducing a second knowledge
source, the old implementation has been removed.

Use the direct generation pipeline in `direct_script_generation.py` instead,
which derives generation behavior from parsed ontology structure and injected
templates rather than hardcoded domain examples.
"""

from pathlib import Path
from typing import Any, Dict, List, Optional


_DEPRECATION_MESSAGE = (
    "template_based_generation.py is intentionally disabled. "
    "The previous implementation encoded domain-specific assumptions in the "
    "generator itself. Use the direct generation pipeline instead."
)


def _disabled(*args: Any, **kwargs: Any) -> None:
    """Raise a deterministic error for deprecated template-based generation."""
    raise RuntimeError(_DEPRECATION_MESSAGE)


def parse_function_signature(sig_text: str) -> Dict[str, Any]:
    """Deprecated entry point."""
    _disabled(sig_text=sig_text)


def parse_concise_signatures(concise_md_path: Path) -> List[Dict[str, Any]]:
    """Deprecated entry point."""
    _disabled(concise_md_path=concise_md_path)


def generate_create_function(func_info: Dict[str, Any], ontology_name: str) -> str:
    """Deprecated entry point."""
    _disabled(func_info=func_info, ontology_name=ontology_name)


def generate_entity_script_from_template(
    concise_md_path: Path,
    ontology_name: str,
    output_path: Path,
    class_subset: Optional[List[str]] = None,
) -> Path:
    """Deprecated entry point."""
    _disabled(
        concise_md_path=concise_md_path,
        ontology_name=ontology_name,
        output_path=output_path,
        class_subset=class_subset,
    )


def generate_checks_script_from_template(
    concise_structure: Dict[str, Any],
    ontology_name: str,
    output_path: Path,
) -> Path:
    """Deprecated entry point."""
    _disabled(
        concise_structure=concise_structure,
        ontology_name=ontology_name,
        output_path=output_path,
    )


def generate_relationships_script_from_template(
    concise_structure: Dict[str, Any],
    ontology_name: str,
    output_path: Path,
) -> Path:
    """Deprecated entry point."""
    _disabled(
        concise_structure=concise_structure,
        ontology_name=ontology_name,
        output_path=output_path,
    )
