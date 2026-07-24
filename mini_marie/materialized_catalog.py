"""
Materialized view catalog: atomics, facets, transforms, residual SPARQL.

Used by the query planner and warm-manifest tooling.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from mini_marie.warm_manifest import collect_atomic_specs_from_workflow

MOF_FACETS = [
    {"id": "facet_identity", "keys": ["name_lc"], "columns": ["name", "topology", "sourcedb", "refcode", "mofid"]},
    {"id": "facet_topology_mof", "keys": ["topology"], "columns": ["mof", "name", "topology", "sourcedb"]},
    {"id": "facet_synthesis", "keys": ["name_lc", "refcode"], "columns": ["name", "refcode", "method", "solvent"]},
    {"id": "facet_metal_source", "keys": ["metal"], "columns": ["sourcedb", "count"]},
    {"id": "facet_linker", "keys": ["name_lc"], "columns": ["name", "linker"]},
    {"id": "facet_refcodes", "keys": ["name_lc"], "columns": ["name", "refcode", "sourcedb"]},
]

CITY_FACETS = [
    {"id": "facet_building_height", "keys": ["city_lc", "building"], "columns": ["building", "height", "usage_type", "label"]},
    {"id": "facet_building_location", "keys": ["city_lc", "building"], "columns": ["building", "wkt", "height", "label"]},
]

TRANSFORMS = [
    {"id": "filter_rows", "kind": "row_algebra", "sparql_fragment": "FILTER on bound vars (single pool)"},
    {"id": "join_rows", "kind": "row_algebra", "sparql_fragment": "join two pools on key(s)"},
    {"id": "multi_join_rows", "kind": "row_algebra", "sparql_fragment": "chain of join_rows steps"},
    {"id": "group_aggregate", "kind": "row_algebra", "sparql_fragment": "GROUP BY + aggregates"},
    {"id": "top_n_by_field", "kind": "row_algebra", "sparql_fragment": "ORDER BY + LIMIT"},
]

from mini_marie.sparql_utils import RESIDUAL_TOOL


def mof_atomic_catalog() -> List[Dict[str, Any]]:
    from mini_marie.mop_mof.mof.competency_cache import TOOL_REGISTRY

    return [
        {"tool": name, "domain": "mof", "tier": "atomic", "cached": True}
        for name in sorted(TOOL_REGISTRY)
    ]


def build_domain_catalog(
    *,
    domain: str = "mof",
    workflows: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Build catalog document for a domain."""
    wf_specs: List[Dict[str, Any]] = []
    sparql_specs: List[Dict[str, Any]] = []
    if workflows:
        for wf in workflows:
            wf_specs.extend(collect_atomic_specs_from_workflow(wf))
            for step in wf.get("steps") or []:
                if step.get("type") == "sparql" and step.get("query") and "$" not in str(step.get("query")):
                    sparql_specs.append(
                        {"workflow_id": wf.get("id"), "query_preview": str(step["query"])[:120]}
                    )

    return {
        "domain": domain,
        "atomics": mof_atomic_catalog() if domain == "mof" else [],
        "facets": MOF_FACETS if domain == "mof" else CITY_FACETS,
        "transforms": TRANSFORMS,
        "residual_tool": RESIDUAL_TOOL,
        "workflow_atomic_specs_count": len(wf_specs),
        "workflow_residual_queries": sparql_specs,
    }


def write_catalog(path: Path, *, domain: str = "mof") -> Path:
    from mini_marie.mop_mof.mof.competency_workflow_engine import load_manifest

    manifest = load_manifest()
    doc = build_domain_catalog(domain=domain, workflows=manifest.get("workflows"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    return path
