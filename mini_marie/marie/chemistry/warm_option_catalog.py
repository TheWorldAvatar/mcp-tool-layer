"""
Full-space warm option catalog for chemistry competency tools.

Each dimension defines tool(base_args) x options — e.g. filter_by_literal(AEN),
filter_by_literal(BEA), … Use warm_chemistry_cache --full-space --missing-only
to warm only uncached variants incrementally (--batch / --offset / --delay).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from mini_marie.marie.chemistry.chemistry_cache import TOOL_REGISTRY
from mini_marie.marie.chemistry.limits import WARM_NAMESPACES
from mini_marie.warm_manifest import spec_key

CATALOG_PATH = Path(__file__).resolve().parent / "warm_option_catalog.json"
DISCOVERED_PATH = Path(__file__).resolve().parent / "warm_option_catalog.discovered.json"

# Fallback when discovered file absent (Marie MQ seeds)
_FRAMEWORK_CODES_SEED = [
    "AEN", "SFN", "FAU", "MFI", "LTA", "BEA", "CHA", "MOR", "FER", "AFY", "UOZ",
]


def load_discovered_options() -> Dict[str, List[str]]:
    if not DISCOVERED_PATH.exists():
        return {}
    data = json.loads(DISCOVERED_PATH.read_text(encoding="utf-8"))
    opts = data.get("options") or {}
    return {k: list(v) for k, v in opts.items() if isinstance(v, list)}


def save_discovered_options(options: Dict[str, List[str]]) -> Path:
    payload = {"version": 1, "options": options}
    DISCOVERED_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return DISCOVERED_PATH


def load_catalog(path: Path | None = None) -> Dict[str, Any]:
    p = path or CATALOG_PATH
    return json.loads(p.read_text(encoding="utf-8"))


def _resolve_options(dim: Dict[str, Any], discovered: Dict[str, List[str]]) -> List[Any]:
    if "options_from" in dim:
        key = str(dim["options_from"])
        if key in discovered and discovered[key]:
            return discovered[key]
        if key == "framework_codes":
            return list(_FRAMEWORK_CODES_SEED)
        return []
    return list(dim.get("options") or [])


def expand_dimension(dim: Dict[str, Any], discovered: Dict[str, List[str]]) -> List[Dict[str, Any]]:
    tool = dim["tool"]
    if tool not in TOOL_REGISTRY:
        return []

    ns = dim.get("namespace") or (dim.get("base_args") or {}).get("namespace", "")
    if ns and ns not in WARM_NAMESPACES:
        return []

    vary = dim.get("vary")
    base = dict(dim.get("base_args") or {})
    if ns and "namespace" not in base:
        base["namespace"] = ns

    specs: List[Dict[str, Any]] = []
    for opt in _resolve_options(dim, discovered):
        args = dict(base)
        if isinstance(opt, dict):
            args.update(opt)
            option_label = json.dumps(opt, sort_keys=True, default=str)
        elif vary:
            args[vary] = opt
            option_label = str(opt)
        else:
            continue
        if not args.get("namespace"):
            continue
        specs.append(
            {
                "tool": tool,
                "args": args,
                "catalog_id": dim.get("id", ""),
                "catalog_option": option_label,
            }
        )
    return specs


def list_dimensions(catalog: Dict[str, Any] | None = None) -> List[Dict[str, Any]]:
    cat = catalog or load_catalog()
    discovered = load_discovered_options()
    out: List[Dict[str, Any]] = []
    for dim in cat.get("dimensions") or []:
        opts = _resolve_options(dim, discovered)
        out.append(
            {
                "id": dim.get("id"),
                "namespace": dim.get("namespace"),
                "tool": dim.get("tool"),
                "option_count": len(opts),
                "vary": dim.get("vary"),
                "options_from": dim.get("options_from"),
            }
        )
    return out


def full_space_warm_specs(
    *,
    namespace: str | None = None,
    tool: str | None = None,
    dimension_id: str | None = None,
    catalog: Dict[str, Any] | None = None,
) -> List[Dict[str, Any]]:
    """Expand every catalog dimension into atomic {tool, args} specs."""
    cat = catalog or load_catalog()
    discovered = load_discovered_options()
    seen: set[str] = set()
    specs: List[Dict[str, Any]] = []

    for dim in cat.get("dimensions") or []:
        if dimension_id and dim.get("id") != dimension_id:
            continue
        if namespace and dim.get("namespace") != namespace:
            continue
        if tool and dim.get("tool") != tool:
            continue
        for spec in expand_dimension(dim, discovered):
            key = spec_key({"tool": spec["tool"], "args": spec["args"]})
            if key in seen:
                continue
            seen.add(key)
            specs.append(
                {
                    "tool": spec["tool"],
                    "args": spec["args"],
                    "catalog_id": spec.get("catalog_id", ""),
                    "catalog_option": spec.get("catalog_option", ""),
                }
            )
    return specs


def coverage_report(
    *,
    has_full: Callable[[str, Dict[str, Any]], bool],
    namespace: str | None = None,
    tool: str | None = None,
    dimension_id: str | None = None,
    missing_limit: int = 20,
) -> Dict[str, Any]:
    """Per-dimension cache coverage: which options (A,B,C,…) are warm vs missing."""
    specs = full_space_warm_specs(namespace=namespace, tool=tool, dimension_id=dimension_id)
    by_dim: Dict[str, Dict[str, Any]] = {}

    for spec in specs:
        dim_id = spec.get("catalog_id") or "unknown"
        bucket = by_dim.setdefault(
            dim_id,
            {
                "dimension_id": dim_id,
                "namespace": spec["args"].get("namespace"),
                "tool": spec["tool"],
                "total": 0,
                "cached": 0,
                "missing": 0,
                "missing_options": [],
            },
        )
        bucket["total"] += 1
        if has_full(spec["tool"], spec["args"]):
            bucket["cached"] += 1
        else:
            bucket["missing"] += 1
            opt = spec.get("catalog_option", "")
            if len(bucket["missing_options"]) < missing_limit:
                bucket["missing_options"].append(opt)

    dimensions = sorted(by_dim.values(), key=lambda d: d["dimension_id"])
    totals = {
        "specs": len(specs),
        "cached": sum(d["cached"] for d in dimensions),
        "missing": sum(d["missing"] for d in dimensions),
    }
    return {"totals": totals, "dimensions": dimensions}


def slice_specs(
    specs: Sequence[Dict[str, Any]],
    *,
    offset: int = 0,
    batch: int | None = None,
) -> List[Dict[str, Any]]:
    if offset < 0:
        offset = 0
    if batch is None or batch < 1:
        return list(specs[offset:])
    return list(specs[offset : offset + batch])
