"""
Complex multi-step competency replay: cold SPARQL chain vs warm cache replay.

Workflow CQ07: identity -> same-topology sample -> count -> local joins (offline).
Workflow CQ_D3: refcodes -> synthesis -> synthesis-by-refcodes local join (offline).
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

from mini_marie.mop_mof.mof.competency_workflow_engine import load_workflow, run_competency_workflow

REPORT = Path(__file__).resolve().parent / "competency_runs" / "complex_replay_benchmark.json"

COMPLEX_WORKFLOWS = [
    "CQ07_SAME_TOPO_ZIF8",
    "CQ_D3_ZIF8_REFCODES_SYNTH",
]


def _step_report(result: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows = []
    for s in result.get("call_trace", []):
        rows.append(
            {
                "step": s.get("step"),
                "type": s.get("step_type"),
                "name": s.get("tool") or s.get("join"),
                "status": s.get("status"),
                "row_count": s.get("row_count"),
                "elapsed_ms": s.get("elapsed_ms"),
                "from_cache": (s.get("cache_meta") or {}).get("from_cache"),
            }
        )
    return rows


def run_pair(workflow_id: str, *, offline_cap: int = 500_000) -> Dict[str, Any]:
    wf = load_workflow(workflow_id)
    print(f"\n{'='*60}\n{workflow_id}: {wf.get('question')}\n{'='*60}")

    print("COLD (force_refresh, full SPARQL chain)...", flush=True)
    t0 = time.perf_counter()
    cold = run_competency_workflow(
        wf, mode="offline", offline_cap=offline_cap, force_refresh=True
    )
    cold_ms = round((time.perf_counter() - t0) * 1000)

    print("WARM (replay from cache, same steps)...", flush=True)
    t0 = time.perf_counter()
    warm = run_competency_workflow(
        wf, mode="offline", offline_cap=offline_cap, force_refresh=False
    )
    warm_ms = round((time.perf_counter() - t0) * 1000)

    cold_steps = _step_report(cold)
    warm_steps = _step_report(warm)
    tool_cold = [s for s in cold_steps if s["type"] == "tool"]
    tool_warm = [s for s in warm_steps if s["type"] == "tool"]
    joins_warm = [s for s in warm_steps if s["type"] == "local_join"]

    cache_hits = sum(1 for s in tool_warm if s.get("from_cache"))
    speedup = round(cold_ms / warm_ms, 1) if warm_ms else None

    print(f"  cold={cold_ms}ms  warm={warm_ms}ms  speedup={speedup}x")
    print(f"  tool cache hits: {cache_hits}/{len(tool_warm)}")
    print(f"  local_join steps (warm): {len(joins_warm)}")
    print(f"  answer (warm): {warm.get('answer', '')[:120]}")
    print("\n  Step timing (cold -> warm):")
    for c, w in zip(cold_steps, warm_steps):
        name = c["name"] or c["type"]
        fc = "cache" if w.get("from_cache") else ("join" if w["type"] == "local_join" else "sparql")
        print(
            f"    {c['step']}. {name}: "
            f"{c['elapsed_ms']}ms -> {w['elapsed_ms']}ms [{fc}] rows={w['row_count']}"
        )

    if cache_hits < len(tool_warm):
        print("  WARN: not all tool steps served from cache", file=sys.stderr)
    if cold["status"] != "pass" or warm["status"] != "pass":
        raise AssertionError(f"status cold={cold['status']} warm={warm['status']}")

    return {
        "workflow_id": workflow_id,
        "cold_ms": cold_ms,
        "warm_ms": warm_ms,
        "speedup": speedup,
        "cache_hits": cache_hits,
        "tool_steps": len(tool_warm),
        "local_join_steps": len(joins_warm),
        "answer_warm": warm.get("answer"),
        "cold_steps": cold_steps,
        "warm_steps": warm_steps,
    }


def main() -> None:
    results = []
    for wf_id in COMPLEX_WORKFLOWS:
        results.append(run_pair(wf_id))
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps({"workflows": results}, indent=2), encoding="utf-8")
    print(f"\nWrote {REPORT}")
    print("COMPLEX MOF REPLAY TESTS PASSED")


if __name__ == "__main__":
    main()
