"""Pipeline identity + per-iteration closed surfaces for OntoLogX prompts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
DEFAULT_SURFACES = HERE / "resources" / "iteration_surfaces.json"


def load_iteration_surfaces(path: Path | None = None) -> dict[int, dict[str, Any]]:
    payload = json.loads((path or DEFAULT_SURFACES).read_text(encoding="utf-8"))
    layers = payload.get("layers") or {}
    return {int(key): dict(value) for key, value in layers.items()}


def format_iteration_surface(layer: int, surfaces: dict[int, dict[str, Any]] | None = None) -> str:
    """Closed class/property guide for one layered iteration. Not used in full-graph mode."""
    spec = (surfaces or load_iteration_surfaces()).get(int(layer))
    if not spec:
        return ""
    classes = ", ".join(spec.get("classes") or []) or "(none; attach to existing nodes)"
    helpers = ", ".join(spec.get("linked_helper_targets") or []) or "(none)"
    obj_props = ", ".join(spec.get("object_properties") or []) or "(none)"
    data_props = ", ".join(spec.get("datatype_properties") or []) or "(none)"
    return (
        f"---- ITERATION-OWNED SURFACE (iter{layer} only): BEGIN ----\n"
        "This layer may create or relate only the classes and properties below.\n"
        "Attach to prior-layer ids from EXISTING_GRAPH_INVENTORY. Do not recreate "
        "prior-layer classes unless they are listed here. Do not mint out-of-scope "
        "classes or properties in this layer.\n"
        f"Owned classes: {classes}\n"
        f"Linked helper targets (create only as objects of in-scope properties): {helpers}\n"
        f"Owned object properties: {obj_props}\n"
        f"Owned datatype / quantity properties: {data_props}\n"
        f"---- ITERATION-OWNED SURFACE (iter{layer} only): END ----\n"
    )


def load_identity_records(runtime_dir: Path) -> list[dict[str, Any]]:
    path = runtime_dir / "mcp_run" / "iter1_top_entities.json"
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        return []
    return [item for item in payload if isinstance(item, dict)]


def match_identity_record(
    records: list[dict[str, Any]],
    *,
    key: str,
    label: str,
) -> dict[str, Any] | None:
    wanted_label = (label or "").strip()
    wanted_key = (key or "").strip()
    for item in records:
        if wanted_label and str(item.get("label") or "").strip() == wanted_label:
            return item
    for item in records:
        dossier = item.get("identity_dossier") or {}
        anchor = str(item.get("source_anchor") or dossier.get("source_anchor") or "")
        if wanted_key and wanted_key in anchor:
            return item
    folded = wanted_label.casefold()
    if folded:
        for item in records:
            if str(item.get("label") or "").strip().casefold() == folded:
                return item
    return None


def attach_pipeline_identity(entity: Any, runtime_dir: Path) -> Any:
    """Copy Pipeline URI + identity dossier onto a hint/top-entity object."""
    record = match_identity_record(
        load_identity_records(runtime_dir),
        key=getattr(entity, "key", ""),
        label=getattr(entity, "label", ""),
    )
    if record is None:
        return entity
    uri = str(record.get("uri") or "").strip()
    dossier = dict(record.get("identity_dossier") or {})
    if not dossier and uri:
        dossier = {
            "uri": uri,
            "label": record.get("label"),
            "types": record.get("types") or [],
            "source_anchor": record.get("source_anchor"),
        }
    updates = {}
    if uri:
        updates["uri"] = uri
    if dossier:
        updates["identity_dossier"] = dossier
    if not updates:
        return entity
    if hasattr(entity, "__dataclass_fields__"):
        from dataclasses import replace

        allowed = {
            name: value
            for name, value in updates.items()
            if name in entity.__dataclass_fields__
        }
        return replace(entity, **allowed) if allowed else entity
    for name, value in updates.items():
        setattr(entity, name, value)
    return entity


def format_identity_block(
    *,
    entity_uri: str = "",
    identity_dossier: dict[str, Any] | None = None,
) -> str:
    parts: list[str] = []
    if entity_uri:
        parts.extend(
            [
                "---- PIPELINE ENTITY URI: BEGIN ----",
                "Reuse this exact ChemicalSynthesis IRI as the node id. Do not mint a replacement.",
                entity_uri,
                "---- PIPELINE ENTITY URI: END ----",
            ]
        )
    if identity_dossier:
        parts.extend(
            [
                "---- PIPELINE ENTITY IDENTITY DOSSIER: BEGIN ----",
                "Authoritative identity scope for the current ChemicalSynthesis.",
                "Materialize only this exact scope. Use explicit dossier fields; do not infer missing identity facts.",
                json.dumps(identity_dossier, ensure_ascii=False, indent=2, sort_keys=True),
                "---- PIPELINE ENTITY IDENTITY DOSSIER: END ----",
            ]
        )
    return ("\n" + "\n".join(parts) + "\n") if parts else ""
