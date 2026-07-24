"""
Compare cold (SPARQL fetch) vs warm (SQLite cache hit) competency workflow performance.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any, Dict, List

from mini_marie.mop_mof.mof.competency_cache import db_path
from mini_marie.mop_mof.mof.competency_workflow_engine import load_workflow, run_competency_workflow

REPORT_PATH = Path(__file__).resolve().parent / "competency_runs" / "cache_benchmark.json"


def _step_stats(result: Dict[str, Any]) -> List[Dict[str, Any]]:
    out = []
    for s in result.get("call_trace", []):
        out.append(
            {
                "step": s.get("step"),
                "type": s.get("step_type"),
                "name": s.get("tool") or s.get("join"),
                "row_count": s.get("row_count"),
                "elapsed_ms": s.get("elapsed_ms"),
                "from_cache": (s.get("cache_meta") or {}).get("from_cache"),
            }
        )
    return out


def benchmark_workflow(
    workflow_id: str,
    *,
    mode: str,
    online_limit: int = 10,
    offline_cap: int = 500_000,
) -> Dict[str, Any]:
    wf = load_workflow(workflow_id)
    print(f"\n=== {workflow_id} ({mode}) ===")

    print("  cold (force_refresh)...", flush=True)
    t0 = time.perf_counter()
    cold = run_competency_workflow(
        wf,
        mode=mode,
        online_limit=online_limit,
        offline_cap=offline_cap,
        use_cache=True,
        force_refresh=True,
    )
    cold_wall = round((time.perf_counter() - t0) * 1000)

    print("  warm (cache hit)...", flush=True)
    t0 = time.perf_counter()
    warm = run_competency_workflow(
        wf,
        mode=mode,
        online_limit=online_limit,
        offline_cap=offline_cap,
        use_cache=True,
        force_refresh=False,
    )
    warm_wall = round((time.perf_counter() - t0) * 1000)

    speedup = round(cold_wall / warm_wall, 2) if warm_wall > 0 else None
    cache_hits = sum(1 for s in warm.get("call_trace", []) if (s.get("cache_meta") or {}).get("from_cache"))
    tool_steps = sum(1 for s in warm.get("call_trace", []) if s.get("step_type") == "tool")

    print(
        f"  cold={cold_wall}ms warm={warm_wall}ms speedup={speedup}x "
        f"cache_hits={cache_hits}/{tool_steps}"
    )

    return {
        "workflow_id": workflow_id,
        "mode": mode,
        "cold_ms": cold_wall,
        "warm_ms": warm_wall,
        "speedup": speedup,
        "cache_hits": cache_hits,
        "tool_steps": tool_steps,
        "cold_steps": _step_stats(cold),
        "warm_steps": _step_stats(warm),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="MOF competency cache cold vs warm benchmark")
    parser.add_argument("--workflow", default="CQ06_TOPOLOGY_ZIF8")
    parser.add_argument("--modes", default="online,offline")
    parser.add_argument("--online-limit", type=int, default=10)
    parser.add_argument("--offline-cap", type=int, default=500_000)
    args = parser.parse_args()

    modes = [m.strip() for m in args.modes.split(",") if m.strip()]
    results = [benchmark_workflow(args.workflow, mode=m, online_limit=args.online_limit, offline_cap=args.offline_cap) for m in modes]

    report = {"cache_db": str(db_path()), "workflow": args.workflow, "results": results}
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nWrote {REPORT_PATH}")


if __name__ == "__main__":
    main()
