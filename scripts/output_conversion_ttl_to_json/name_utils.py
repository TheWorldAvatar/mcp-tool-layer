"""Shared normalization helpers for RDF-to-JSON chemical names."""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any


_HASHED_ARTIFACT_LABEL = re.compile(r".+--[0-9a-f]{8,16}$", re.IGNORECASE)


def is_hashed_artifact_label(label: str) -> bool:
    """True for pipeline export stems such as ``Synthesis_of_Co24_...--8816a66c81dd``."""
    text = str(label or "").strip()
    return bool(text) and bool(_HASHED_ARTIFACT_LABEL.fullmatch(text))


def prefer_synthesis_label(labels: Iterable[Any]) -> str:
    """Pick the human ChemicalSynthesis label over a hashed filename alias."""
    cleaned = [str(value).strip() for value in labels if str(value or "").strip()]
    human = [label for label in cleaned if not is_hashed_artifact_label(label)]
    if human:
        return max(human, key=len)
    return cleaned[0] if cleaned else ""


def collapse_labeled_syntheses(rows: Iterable[tuple[Any, Any]]) -> list[dict[str, str]]:
    """Collapse SPARQL ``(uri, label)`` rows so one ChemicalSynthesis IRI is one synthesis."""
    by_uri: dict[str, list[str]] = {}
    for uri, label in rows:
        key = str(uri or "").strip()
        if not key:
            continue
        text = str(label or "").strip()
        by_uri.setdefault(key, [])
        if text and text not in by_uri[key]:
            by_uri[key].append(text)
    collapsed = [
        {"uri": uri, "label": prefer_synthesis_label(labels) or uri}
        for uri, labels in by_uri.items()
    ]
    collapsed.sort(key=lambda item: (item["label"].casefold(), item["uri"]))
    return collapsed


def filter_product_names(names: Iterable[Any]) -> list[str]:
    """Drop hashed filename aliases from productNames, keeping a fallback if needed."""
    cleaned: list[str] = []
    for value in names:
        text = str(value or "").strip()
        if text and text not in cleaned:
            cleaned.append(text)
    human = [name for name in cleaned if not is_hashed_artifact_label(name)]
    return human or cleaned


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
