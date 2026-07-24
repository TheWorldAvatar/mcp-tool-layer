"""
Complex multi-step city replay: list/rank -> transform top-N -> fetch locations.

Compares cold offline chain vs warm cache replay on top50 non-domestic Bremen workflow.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List

from mini_marie.zaha.twa_city.workflow_engine import load_workflow, run_workflow

REPORT = Path(__file__).resolve().parent / "workflow_runs" / "complex_replay_benchmark.json"
WORKFLOW = "top50_non_domestic_locations_bremen"


def _step_report(result: Dict[str, Any]) -> List[Dict[str, Any]]:
    out = []
    for s in result.get("call_trace", []):
        out.append(
            {
                "step": s.get("step"),
                "type": s.get("step_type"),
                "name": s.get("tool") or s.get("join") or s.get("name"),
                "status": s.get("status"),
                "row_count": s.get("row_count"),
                "elapsed_ms": s.get("elapsed_ms"),
                "from_cache": (s.get("cache_meta") or {}).get("from_cache"),
            }
        )
    return out


def main() -> None:
    wf = load_workflow(WORKFLOW)
    print(f"Workflow: {WORKFLOW}")
    print(f"Q: {wf.get('question')}")
    print(f"Steps: {len(wf.get('steps', []))} (usage filter -> top-50 -> locations)\n")

    print("COLD offline (force_refresh)...", flush=True)
    t0 = time.perf_counter()
    cold = run_workflow(wf, mode="offline", workflow_name=WORKFLOW, force_refresh=True)
    cold_ms = round((time.perf_counter() - t0) * 1000)

    print("WARM offline (cached replay)...", flush=True)
    t0 = time.perf_counter()
    warm = run_workflow(wf, mode="offline", workflow_name=WORKFLOW, force_refresh=False)
    warm_ms = round((time.perf_counter() - t0) * 1000)

    cold_steps = _step_report(cold)
    warm_steps = _step_report(warm)
    sparql_warm = [s for s in warm_steps if s["type"] == "sparql_plan"]
    hits = sum(1 for s in sparql_warm if s.get("from_cache"))

    print(f"\ncold={cold_ms}ms warm={warm_ms}ms speedup={round(cold_ms/warm_ms,1) if warm_ms else '-'}x")
    print(f"SPARQL cache hits: {hits}/{len(sparql_warm)}")
    print(f"answer: {warm.get('answer', '')[:100]}")
    print("\nPer-step (cold -> warm):")
    for c, w in zip(cold_steps, warm_steps):
        tag = "CACHE" if w.get("from_cache") else ("XFORM" if w["type"] == "transform" else "SPARQL")
        print(
            f"  {c['step']}. {c['name']}: {c['elapsed_ms']}ms -> {w['elapsed_ms']}ms "
            f"[{tag}] rows={w['row_count']}"
        )

    assert cold["status"] == "pass" and warm["status"] == "pass"
    assert hits == len(sparql_warm), f"expected all SPARQL from cache, got {hits}/{len(sparql_warm)}"

    top_n = warm.get("variables", {}).get("top_n")
    locs = warm.get("variables", {}).get("location_rows") or []
    assert top_n == 50
    assert len(locs) > 0, "expected location_rows from chained replay"

    report = {
        "workflow": WORKFLOW,
        "cold_ms": cold_ms,
        "warm_ms": warm_ms,
        "location_row_count": len(locs),
        "cold_steps": cold_steps,
        "warm_steps": warm_steps,
    }
    REPORT.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nWrote {REPORT}")
    print("COMPLEX CITY REPLAY TEST PASSED")


if __name__ == "__main__":
    main()
