"""
Offline CQ_D3 using join_rows (requires warmed ZIF-8 synthesis + refcodes in cache).
"""

from __future__ import annotations

import sys

from mini_marie.mop_mof.mof.competency_cache import CompetencyCache, db_path
from mini_marie.mop_mof.mof.competency_workflow_engine import load_workflow, run_competency_workflow


def main() -> int:
    wf = load_workflow("CQ_D3_ZIF8_REFCODES_SYNTH")
    cache = CompetencyCache()
    try:
        if not cache.has_full("get_synthesis_by_mof_name", {"mof_name": "ZIF-8"}):
            print(f"SKIP: no full cache for ZIF-8 synthesis ({db_path()})")
            return 0
    finally:
        cache.close()

    result = run_competency_workflow(wf, mode="offline", use_cache=True)
    trace = result.get("call_trace") or []
    join_step = next((s for s in trace if s.get("transform") == "join_rows"), None)
    rows = (result.get("variables") or {}).get("filtered_synthesis_rows") or []

    print(f"status={result['status']} join_rows={join_step and join_step.get('row_count')} filtered={len(rows)}")
    if result["status"] != "pass" or not rows:
        return 1
    print("CQ_D3 OFFLINE JOIN_ROWS OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
