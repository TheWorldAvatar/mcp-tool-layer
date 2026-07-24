"""
E2E: CQ02C_ZN_CORE_GROUP_RESIDUAL — filter + group_aggregate + residual SPARQL.

Online: 2 tool-equivalent steps (metal list + transforms); residual skipped.
Offline: full pool, filter, local GROUP BY, cached residual COUNT.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from mini_marie.mop_mof.mof.competency_cache import CompetencyCache, invoke_residual_sparql
from mini_marie.mop_mof.mof.competency_workflow_engine import (
    load_workflow,
    replay_competency_from_recording,
    run_competency_workflow,
    save_run,
)
from mini_marie.query_planner import plan_from_workflow

WORKFLOW_ID = "CQ02C_ZN_CORE_GROUP_RESIDUAL"
RESIDUAL_Q = (
    "PREFIX mofs: <https://www.theworldavatar.com/kg/ontomofs_vkg/>\n"
    "SELECT (COUNT(DISTINCT ?mof) AS ?count) WHERE { ?mof a mofs:MetalOrganicFramework . }"
)


def _summary(trace: list) -> list:
    return [
        {
            "step": s.get("step"),
            "type": s.get("step_type"),
            "name": s.get("tool") or s.get("transform") or "sparql",
            "status": s.get("status"),
            "rows": s.get("row_count"),
            "ms": s.get("elapsed_ms"),
            "from_cache": (s.get("cache_meta") or {}).get("from_cache"),
        }
        for s in trace
    ]


def main() -> int:
    wf = load_workflow(WORKFLOW_ID)
    plan = plan_from_workflow(wf)
    print(f"Plan warm specs: {len(plan['warm_specs'])} (incl. residual)\n")

    cache = CompetencyCache()
    try:
        print("Warming residual SPARQL (full tier)...", flush=True)
        invoke_residual_sparql(RESIDUAL_Q, mode="warm", use_cache=True, cache=cache)
    finally:
        cache.close()

    print(f"=== ONLINE: {WORKFLOW_ID} ===\n")
    t0 = time.perf_counter()
    online = run_competency_workflow(wf, mode="online", online_limit=10, use_cache=True)
    rec = save_run(online)
    print(json.dumps(_summary(online.get("call_trace") or []), indent=2))
    groups = online.get("variables", {}).get("core_zn_by_source") or []
    print(
        f"\nonline: status={online['status']} wall={round((time.perf_counter()-t0)*1000)}ms "
        f"groups={len(groups)} total_mof_count={online.get('variables', {}).get('total_mof_count')}"
    )
    print(f"recording: {rec}\n")
    if online["status"] != "pass":
        return 1

    print("=== OFFLINE ===\n")
    t0 = time.perf_counter()
    recorded = json.loads(Path(rec).read_text(encoding="utf-8"))
    offline = replay_competency_from_recording(recorded, wf)
    save_run(offline)
    groups_off = offline.get("variables", {}).get("core_zn_by_source") or []
    total = offline.get("variables", {}).get("total_mof_count")
    print(json.dumps(_summary(offline.get("call_trace") or []), indent=2))
    print(
        f"\noffline: status={offline['status']} wall={round((time.perf_counter()-t0)*1000)}ms "
        f"groups={len(groups_off)} total_mof_count={total}"
    )

    if offline["status"] != "pass":
        return 1
    if len(groups_off) < 2:
        print("FAIL: expected multiple CoRE source groups offline")
        return 1
    if total is None:
        print("FAIL: missing total_mof_count from residual SPARQL")
        return 1
    residual_step = next(
        (s for s in offline.get("call_trace", []) if s.get("step_type") == "sparql"),
        None,
    )
    if not residual_step or not (residual_step.get("cache_meta") or {}).get("from_cache"):
        print("FAIL: residual step should be cache-only offline")
        return 1

    print("\nMOF ADVANCED WORKFLOW E2E OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
