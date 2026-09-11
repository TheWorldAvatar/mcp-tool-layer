"""Collect human-declared extension bridge class IRIs from domain wiring.

The bridge class is the only allowed non-T-Box class fact for an extension.
Incoming object properties are derived later from T-Box domain/range.
See compile/README.md.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def collect_extension_bridge_class_iris(
    *,
    contract: Mapping[str, Any] | None = None,
    runtime: Mapping[str, Any] | None = None,
) -> list[str]:
    """Collect human-declared extension bridge class IRIs.

    The only allowed non-T-Box domain fact is the extension's bridge class.
    Incoming object properties are derived later from T-Box domain/range.
    """
    found: list[str] = []
    seen: set[str] = set()

    def add(value: Any) -> None:
        iri = str(value or "").strip()
        if iri.startswith(("http://", "https://")) and iri not in seen:
            seen.add(iri)
            found.append(iri)

    payload = contract or {}
    for value in payload.get("extension_bridge_class_iris") or []:
        add(value)
    for source in (runtime, payload.get("runtime")):
        if not isinstance(source, Mapping):
            continue
        for item in source.get("extensions") or []:
            if not isinstance(item, Mapping):
                continue
            add(item.get("bridge_class_iri") or item.get("target_class_iri"))
            policies = item.get("runtime_policies") or {}
            if isinstance(policies, Mapping):
                target = policies.get("enrichment_target") or {}
                if isinstance(target, Mapping):
                    add(target.get("target_class_iri"))
    return found
