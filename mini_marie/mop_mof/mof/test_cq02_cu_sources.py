"""Online + offline: Q2 for Cu — MOFs by source database (list_sources)."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from mini_marie.mop_mof.mof.competency_cache import CompetencyCache, invoke_tool, warm_full_calls
from mini_marie.mop_mof.mof.competency_workflow_engine import (
    replay_competency_from_recording,
    run_competency_workflow,
    save_run,
)

WORKFLOW = {
    "id": "CQ02_CU_SOURCES",
    "question": "Which MOFs contain Cu and their sources? (by database)",
    "metal": "Cu",
    "online_limit": 10,
    "steps": [
        {
            "tool": "get_mofs_by_metal",
            "args": {"metal": "$metal", "list_sources": True},
            "extract": {"cu_source_stats": {"pick": "all_rows"}},
        }
    ],
    "final_summary": "Cu MOFs by source database: $cu_source_stats",
}

ARGS = {"metal": "Cu", "list_sources": True}


def main() -> int:
    cache = CompetencyCache()
    try:
        if not cache.has_full("get_mofs_by_metal", ARGS):
            print("Warming get_mofs_by_metal Cu list_sources ...", flush=True)
            warm_full_calls([{"tool": "get_mofs_by_metal", "args": ARGS}])
        else:
            print("Full cache already present for Cu list_sources", flush=True)
    finally:
        cache.close()

    print("\n=== ONLINE ===", flush=True)
    t0 = time.perf_counter()
    online = run_competency_workflow(WORKFLOW, mode="online", online_limit=10)
    rec = save_run(online)
    trace = online.get("call_trace", [{}])[0]
    print(f"status={online['status']} elapsed={online['elapsed_ms']}ms wall={round((time.perf_counter()-t0)*1000)}ms")
    print(f"rows={trace.get('row_count')} from_cache={(trace.get('cache_meta') or {}).get('from_cache')}")
    for row in (trace.get("rows") or []):
        print(f"  {row.get('sourcedb')}: {row.get('count')}")
    print(f"recording: {rec}\n")

    if online["status"] != "pass":
        return 1

    print("=== OFFLINE ===", flush=True)
    t0 = time.perf_counter()
    recorded = json.loads(Path(rec).read_text(encoding="utf-8"))
    offline = replay_competency_from_recording(recorded, WORKFLOW)
    off_path = save_run(offline)
    trace = offline.get("call_trace", [{}])[0]
    wall = round((time.perf_counter() - t0) * 1000)
    print(f"status={offline['status']} elapsed={offline['elapsed_ms']}ms wall={wall}ms")
    print(f"rows={trace.get('row_count')} from_cache={(trace.get('cache_meta') or {}).get('from_cache')}")
    for row in (trace.get("rows") or []):
        print(f"  {row.get('sourcedb')}: {row.get('count')}")
    print(f"output: {off_path}")
    print(f"\nanswer: {offline.get('answer')}")
    return 0 if offline["status"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
