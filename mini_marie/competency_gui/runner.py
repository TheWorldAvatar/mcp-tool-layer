"""Execute MOF competency or city workflows for the GUI."""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Tuple

from mini_marie.mop_mof.mof.competency_workflow_engine import (
    build_answer_digest,
    list_workflow_ids,
    load_workflow as load_mof_workflow,
    run_competency_workflow,
)
from mini_marie.zaha.twa_city.workflow_engine import (
    build_answer_digest as build_city_digest,
    discover_workflow_catalog,
    load_workflow as load_city_workflow,
    run_workflow,
)


def list_mof_questions() -> List[Dict[str, str]]:
    return [
        {"id": wf_id, "label": f"{wf_id}"}
        for wf_id in list_workflow_ids()
    ]


def list_city_workflows() -> List[Dict[str, str]]:
    catalog = discover_workflow_catalog()
    return [
        {
            "id": meta["id"],
            "name": name,
            "label": f"{name} ({meta.get('city', '')})",
            "city": meta.get("city", ""),
            "description": meta.get("description", ""),
        }
        for name, meta in catalog.items()
    ]


def run_mof(
    workflow_id: str,
    *,
    mode: str = "online",
    online_limit: int = 10,
    offline_cap: int = 500_000,
    force_refresh: bool = False,
) -> Dict[str, Any]:
    wf = load_mof_workflow(workflow_id)
    started = time.perf_counter()
    result = run_competency_workflow(
        wf,
        mode=mode,
        online_limit=online_limit,
        offline_cap=offline_cap,
        force_refresh=force_refresh,
        embed_definition=False,
    )
    result["wall_ms"] = round((time.perf_counter() - started) * 1000)
    if "answer_digest" not in result:
        result["answer_digest"] = build_answer_digest(result)
    return result


def run_city(
    workflow_name: str,
    *,
    mode: str = "online",
    online_limit: int = 10,
    offline_cap: int = 500_000,
    force_refresh: bool = False,
) -> Dict[str, Any]:
    wf = load_city_workflow(workflow_name)
    started = time.perf_counter()
    result = run_workflow(
        wf,
        mode=mode,
        online_limit=online_limit,
        offline_cap=offline_cap,
        workflow_name=workflow_name,
        force_refresh=force_refresh,
        embed_definition=False,
    )
    result["wall_ms"] = round((time.perf_counter() - started) * 1000)
    if "answer_digest" not in result:
        result["answer_digest"] = build_city_digest(result)
    return result


def collect_row_sets(result: Dict[str, Any]) -> List[Tuple[str, List[Dict[str, Any]]]]:
    """Named row sets for visualization (step traces + key variables)."""
    sets: List[Tuple[str, List[Dict[str, Any]]]] = []
    variables = result.get("variables") or {}

    for key in (
        "location_rows",
        "top_building_rows",
        "usage_ranked_rows",
        "building_pool",
        "ranked_rows",
        "filtered_synthesis",
    ):
        val = variables.get(key)
        if isinstance(val, list) and val and isinstance(val[0], dict):
            sets.append((f"var:{key}", val))

    for step in result.get("call_trace", []):
        rows = step.get("rows") or []
        if not rows or not isinstance(rows[0], dict):
            continue
        name = step.get("tool") or step.get("join") or step.get("name") or f"step{step.get('step')}"
        sets.append((f"step:{name}", rows))

    return sets
