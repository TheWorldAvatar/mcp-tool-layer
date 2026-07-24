"""
Comprehensive full-tier warm specs for every TWA city SPARQL atomic plan.

Each city gets a full building-height pool and full location facet (batched WKT warm).
"""

from __future__ import annotations

from typing import Any, Dict, List

from mini_marie.zaha.twa_city.sparql_plans import PLAN_BUILDERS
from mini_marie.warm_manifest import merge_warm_specs

CITIES: List[str] = ["bremen", "kaiserslautern"]

# Usage types observed in city workflows / probes
USAGE_TYPES: List[str] = ["Non-Domestic", "Domestic"]


def comprehensive_warm_specs(*, include_usage: bool = True) -> List[Dict[str, Any]]:
    """One uncapped warm per (plan tool, city[, usage]) — not workflow-selective."""
    specs: List[Dict[str, Any]] = []
    seen: set[str] = set()

    def add(tool: str, args: Dict[str, Any]) -> None:
        if tool not in PLAN_BUILDERS:
            return
        key = f"{tool}:{sorted(args.items())}"
        if key in seen:
            return
        seen.add(key)
        specs.append({"tool": tool, "args": args})

    for city in CITIES:
        add("list_buildings_with_height", {"city": city})
        add("rank_buildings_by_height", {"city": city})
        if include_usage:
            for usage in USAGE_TYPES:
                add("buildings_by_usage", {"city": city, "usage_type": usage})

    return specs


def cities_for_comprehensive_warm() -> List[str]:
    return list(CITIES)


def workflow_driven_warm_specs(*, include_usage: bool = True) -> List[Dict[str, Any]]:
    """Comprehensive city plans plus atomics from every workflows/*.json file."""
    from pathlib import Path

    from mini_marie.zaha.twa_city.workflow_engine import WORKFLOWS_DIR, resolve_value
    from mini_marie.warm_manifest import collect_atomic_specs_from_workflow_dir

    workflow_specs = collect_atomic_specs_from_workflow_dir(
        WORKFLOWS_DIR, resolve=resolve_value
    )
    return merge_warm_specs(
        comprehensive_warm_specs(include_usage=include_usage),
        workflow_specs,
    )
