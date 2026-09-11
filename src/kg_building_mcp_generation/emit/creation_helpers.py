"""Helpers shared by deterministic creation-script emitters."""

from __future__ import annotations

import re
from typing import Any

from src.extraction_prompt_generation.compile.context import (
    AgenticGenerationContext,
)

def _py_name(name: str) -> str:
    out = re.sub(r"\W+", "_", str(name or "")).strip("_")
    if not out:
        out = "unnamed"
    if out[:1].isdigit():
        out = "_" + out
    return out

def _local_name(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if "#" in text:
        return text.rsplit("#", 1)[-1]
    return text.rstrip("/").rsplit("/", 1)[-1]

def _namespace_uri(context: AgenticGenerationContext) -> str:
    ns = str(context.contract.get("namespace_uri") or "").strip()
    if ns:
        return ns
    classes = context.parsed.get("classes") or {}
    for cls in classes.values():
        iri = str((cls or {}).get("iri") or "")
        if iri:
            return iri.rstrip("/#").rsplit("/", 1)[0] + "/"
    return "https://www.theworldavatar.com/kg/generated/"

def _class_ancestors(classes: dict[str, Any], class_name: str) -> list[str]:
    """Return T-Box parent classes, nearest first, without hardcoded ontology names."""
    out: list[str] = []
    queue = list(((classes.get(class_name) or {}).get("parent_classes") or []))
    while queue:
        parent = str(queue.pop(0) or "").strip()
        if not parent or parent in out:
            continue
        out.append(parent)
        queue.extend((classes.get(parent) or {}).get("parent_classes") or [])
    return out

def _normalized_symbol(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(text or "").lower())

def _predicate_target_stem(predicate_local: str) -> str:
    text = str(predicate_local or "").strip()
    for prefix in ("has", "is"):
        if text.startswith(prefix) and len(text) > len(prefix):
            return text[len(prefix) :]
    return text

def _ordered_member_classes(context: AgenticGenerationContext) -> set[str]:
    profile = context.contract.get("ordered_member_profile") or {}
    return {
        str(x).strip()
        for x in profile.get("ordered_member_classes", []) or []
        if str(x).strip()
    }

def _ordering_datatype_properties(context: AgenticGenerationContext) -> set[str]:
    profile = context.contract.get("ordered_member_profile") or {}
    return {
        str(x).strip()
        for x in profile.get("single_valued_ordering_properties", []) or []
        if str(x).strip()
    }

def _step_scoped_object_properties_for_class(
    context: AgenticGenerationContext, class_local: str
) -> dict[str, str]:
    """Return T-Box object properties that may be attached to this ordered-member class."""
    props: dict[str, str] = {}
    classes = context.parsed.get("classes") or {}
    class_family = {class_local, *_class_ancestors(classes, class_local)}

    for spec in context.contract.get("step_scoped_object_properties") or []:
        domain = str((spec or {}).get("domain_local") or "").strip()
        prop = str((spec or {}).get("predicate_local") or "").strip()
        range_local = str((spec or {}).get("range_local") or "").strip()
        if domain in class_family and prop and range_local:
            props[prop] = range_local

    property_defs = context.parsed.get("properties") or {}
    for prop, spec in (
        context.contract.get("relationship_domain_contracts") or {}
    ).items():
        prop_local = str(prop or "").strip()
        if not prop_local:
            continue
        members = {
            _local_name(member)
            for member in ((spec or {}).get("union_members") or [])
            if str(member or "").strip()
        }
        preferred = str((spec or {}).get("preferred_domain_local") or "").strip()
        if class_local not in members and preferred not in class_family:
            continue
        range_local = str(
            ((property_defs.get(prop_local) or {}).get("range") or "")
        ).strip()
        if range_local:
            props[prop_local] = range_local
    return props

