"""Shared workflow step helpers (extract, cache presence)."""

from __future__ import annotations

from typing import Any, Callable, Dict

ExtractFn = Callable[[Dict[str, Any], Dict[str, Any]], Any]


def apply_step_extract(
    step: Dict[str, Any],
    result: Dict[str, Any],
    variables: Dict[str, Any],
    extract_fn: ExtractFn,
) -> None:
    """Run step `extract` specs into workflow variables."""
    if result.get("status") in ("skipped", "error"):
        return
    if not result.get("rows"):
        return
    for var_name, extract_spec in (step.get("extract") or {}).items():
        variables[var_name] = extract_fn(extract_spec, result)
