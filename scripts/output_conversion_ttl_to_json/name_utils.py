"""Shared normalization helpers for RDF-to-JSON chemical names."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any


def split_alternative_names(value: Any) -> list[str]:
    """Split an OntoSyn semicolon-delimited alias literal into semantic names."""
    text = str(value or "").strip()
    if not text:
        return []
    return [part.strip() for part in text.split(";") if part.strip()]


def extend_unique_names(target: list[str], values: Iterable[Any], *, split: bool = False) -> None:
    """Append non-empty names while preserving their first-seen order."""
    for value in values:
        names = split_alternative_names(value) if split else [str(value or "").strip()]
        for name in names:
            if name and name not in target:
                target.append(name)
