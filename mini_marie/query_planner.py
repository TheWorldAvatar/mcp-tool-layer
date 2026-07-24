"""
Lightweight query planner: map competency workflows to execution plans.

Full SPARQL → plan decomposition is heuristic; workflows remain source of truth.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from mini_marie.materialized_catalog import RESIDUAL_TOOL, TRANSFORMS, build_domain_catalog
from mini_marie.warm_manifest import collect_atomic_specs_from_workflow, merge_warm_specs


def plan_from_workflow(workflow: Dict[str, Any]) -> Dict[str, Any]:
    """Structured plan from workflow JSON (atomics + transforms + sparql + local_join)."""
    steps_out: List[Dict[str, Any]] = []
    warm_specs: List[Dict[str, Any]] = []

    for i, step in enumerate(workflow.get("steps") or [], start=1):
        stype = step.get("type") or "tool"
        entry: Dict[str, Any] = {"step": i, "step_type": stype}
        if stype == "tool":
            entry["tool"] = step.get("tool")
            entry["args"] = step.get("args")
            warm_specs.append({"tool": step["tool"], "args": step.get("args") or {}})
        elif stype == "transform":
            entry["transform"] = step.get("transform")
            entry["offline_only"] = step.get("offline_only", False)
        elif stype == "sparql":
            entry["residual"] = True
            entry["offline_only"] = step.get("offline_only", False)
            if step.get("query"):
                warm_specs.append(
                    {"tool": RESIDUAL_TOOL, "args": {"query": step["query"]}}
                )
        elif stype == "local_join":
            entry["join"] = step.get("join")
            entry["offline_only"] = step.get("offline_only", False)
        steps_out.append(entry)

    return {
        "workflow_id": workflow.get("id"),
        "question": workflow.get("question"),
        "steps": steps_out,
        "warm_specs": merge_warm_specs(collect_atomic_specs_from_workflow(workflow), warm_specs),
        "catalog": build_domain_catalog(domain="mof", workflows=[workflow]),
    }


def classify_step_coverage(step: Dict[str, Any]) -> str:
    """Return coverage class: atomic | transform | facet_join | residual."""
    stype = step.get("type") or "tool"
    if stype == "sparql":
        return "residual"
    if stype == "local_join":
        return "facet_join"
    if stype == "transform":
        t = step.get("transform", "")
        if t in {x["id"] for x in TRANSFORMS}:
            return "transform"
        return "unknown_transform"
    return "atomic"
