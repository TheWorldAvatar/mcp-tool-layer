"""
E2E: CQ02B_ZN_CORE_FILTER — 2 tool calls + filter_rows, online probe then offline replay.

Question: Zn MOFs whose source database name contains "core" (CoRE MOF 2019/2025),
then compare with corpus-wide Zn source statistics (second tool).
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from mini_marie.mop_mof.mof.competency_cache import CompetencyCache, db_path
from mini_marie.mop_mof.mof.competency_workflow_engine import (
    load_workflow,
    replay_competency_from_recording,
    run_competency_workflow,
    save_run,
)

WORKFLOW_ID = "CQ02B_ZN_CORE_FILTER"


def _step_summary(trace: list) -> list:
    out = []
    for s in trace:
        label = s.get("tool") or s.get("transform") or s.get("join") or s.get("step_type")
        out.append(
            {
                "step": s.get("step"),
                "type": s.get("step_type"),
                "name": label,
                "status": s.get("status"),
                "rows": s.get("row_count"),
                "ms": s.get("elapsed_ms"),
                "summary": s.get("summary"),
            }
        )
    return out


def main() -> int:
    wf = load_workflow(WORKFLOW_ID)
    cache = CompetencyCache()
    try:
        if not cache.has_full("get_mofs_by_metal", {"metal": "Zn"}):
            print(f"WARN: full cache missing for Zn metal list — warm first ({db_path()})")
    finally:
        cache.close()

    print(f"=== ONLINE probe: {WORKFLOW_ID} ===\n")
    t0 = time.perf_counter()
    online = run_competency_workflow(wf, mode="online", online_limit=10, use_cache=True)
    online_ms = round((time.perf_counter() - t0) * 1000)
    rec_path = save_run(online)
    zn_pool = len(online.get("variables", {}).get("zn_pool") or [])
    filtered = len(online.get("variables", {}).get("core_zn_rows") or [])
    print(json.dumps(_step_summary(online.get("call_trace") or []), indent=2))
    print(f"\nonline: status={online['status']} wall={online_ms}ms zn_pool={zn_pool} filtered={filtered}")
    print(f"recording: {rec_path}\n")

    if online["status"] != "pass":
        return 1

    print(f"=== OFFLINE replay (cache + filter_rows) ===\n")
    t0 = time.perf_counter()
    recorded = json.loads(Path(rec_path).read_text(encoding="utf-8"))
    offline = replay_competency_from_recording(recorded, wf)
    offline_ms = round((time.perf_counter() - t0) * 1000)
    save_run(offline)
    zn_full = len(offline.get("variables", {}).get("zn_pool") or [])
    filtered_full = len(offline.get("variables", {}).get("core_zn_rows") or [])
    stats_rows = len(offline.get("variables", {}).get("zn_source_stats") or [])
    print(json.dumps(_step_summary(offline.get("call_trace") or []), indent=2))
    print(
        f"\noffline: status={offline['status']} wall={offline_ms}ms "
        f"zn_pool={zn_full} core_filtered={filtered_full} source_stats={stats_rows}"
    )

    if offline["status"] != "pass":
        return 1
    if zn_full <= 10:
        print("FAIL: offline zn_pool should be >> online LIMIT (full-tier cache)")
        return 1
    if filtered_full <= filtered:
        print("FAIL: offline filtered count should exceed online filtered count")
        return 1
    if stats_rows < 5:
        print("FAIL: offline source stats should have multiple databases")
        return 1

    print("\nMOF FILTER WORKFLOW E2E OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
