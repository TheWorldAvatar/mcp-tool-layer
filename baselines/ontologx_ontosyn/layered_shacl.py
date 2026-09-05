"""Parallel per-iteration SHACL, sliced from the fixed Pipeline surface blueprint.

Default OntoLogX still uses the full-graph ``ontosynthesis_shacl.ttl``.
``--layered-surface-shacl`` swaps the correction oracle to these files and
enforces SHACL on iter2 as well. Paper-level merge validation stays full-graph.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
RESOURCES = HERE / "resources"
FULL_SHACL = RESOURCES / "ontosynthesis_shacl.ttl"
SURFACES = RESOURCES / "iteration_surfaces.json"
MANIFEST = RESOURCES / "layered_shacl_manifest.json"


def layered_shacl_path(layer: int) -> Path:
    path = RESOURCES / f"ontosynthesis_shacl_iter{int(layer)}.ttl"
    if not path.is_file():
        raise FileNotFoundError(f"Missing layered surface SHACL {path}. Run generate_shacl.py.")
    return path


def shacl_path_for_layer(layer: int | None, *, surface: bool) -> Path:
    if surface and layer is not None:
        return layered_shacl_path(int(layer))
    return FULL_SHACL


def write_manifest(written: dict[int, Path] | None = None) -> Path:
    from generate_shacl import (
        _local,
        generate_layer,
        surface_class_iris,
        surface_path_iris,
        write_layered_shapes,
    )
    from iteration_guides import load_iteration_surfaces

    paths = written or write_layered_shapes()
    layers: dict[str, Any] = {}
    for layer, spec in sorted(load_iteration_surfaces().items()):
        text = generate_layer(layer, spec)
        layers[str(layer)] = {
            "slot_kind": spec.get("slot_kind"),
            "path": str(paths[int(layer)].relative_to(HERE)).replace("\\", "/"),
            "owned_classes": sorted(_local(iri) for iri in surface_class_iris(spec)),
            "owned_paths": sorted(_local(iri) for iri in surface_path_iris(spec)),
            "includes_hasSynthesisStep": "hasSynthesisStep" in text,
            "includes_retrievedFrom": "retrievedFrom" in text,
            "includes_hasYield": "hasYield" in text,
            "includes_CoolingNeedsTemperatureShape": "CoolingNeedsTemperatureShape" in text,
        }
    payload = {
        "blueprint": str(SURFACES.relative_to(HERE)).replace("\\", "/"),
        "full_graph": str(FULL_SHACL.relative_to(HERE)).replace("\\", "/"),
        "mode": "per-layer owned surface; not cumulative; merge still uses full_graph",
        "layers": layers,
    }
    MANIFEST.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return MANIFEST
