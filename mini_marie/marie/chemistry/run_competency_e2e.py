"""End-to-end chemistry competency: offline corpus replay + optional online probe."""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from mini_marie.marie.chemistry.chemistry_cache import db_path
from mini_marie.marie.chemistry.chemistry_workflow_engine import (
    RUNS_DIR,
    load_manifest,
    load_workflow,
    list_workflow_ids,
    replay_from_recording,
    run_competency_workflow,
    save_run,
)
from mini_marie.marie.chemistry.limits import DEFAULT_ONLINE_PROBE_LIMIT

REPORT_JSON = Path(__file__).resolve().parent / "competency_runs" / "e2e_report.json"
REPORT_MD = Path(__file__).resolve().parent / "competency_runs" / "e2e_report.md"


def _latest_online_recording(workflow_id: str) -> Optional[Path]:
    pattern = f"{workflow_id}_online_*.json"
    files = sorted(RUNS_DIR.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0] if files else None


def run_e2e(
    *,
    workflow_ids: Optional[List[str]] = None,
    skip_offline_direct: bool = False,
    skip_replay: bool = False,
    run_online: bool = False,
    online_limit: int = DEFAULT_ONLINE_PROBE_LIMIT,
    force_online: bool = False,
) -> Dict[str, Any]:
    manifest = load_manifest()
    workflows = manifest.get("workflows", [])
    if workflow_ids:
        wanted = set(workflow_ids)
        workflows = [w for w in workflows if w.get("id") in wanted]

    suite_started = time.perf_counter()
    cases: List[Dict[str, Any]] = []

    print(f"E2E: {len(workflows)} workflows | cache DB: {db_path()}\n")

    for i, wf in enumerate(workflows, 1):
        wf_id = wf.get("id", "?")
        print(f"[{i}/{len(workflows)}] {wf_id}", flush=True)
        case: Dict[str, Any] = {"workflow_id": wf_id, "title": wf.get("title", ""), "offline_direct": None, "offline_replay": None, "online": None}

        if not skip_offline_direct:
            t0 = time.perf_counter()
            offline_result = run_competency_workflow(wf, mode="offline")
            offline_path = save_run(offline_result)
            case["offline_direct"] = {
                "status": offline_result["status"],
                "wall_ms": round((time.perf_counter() - t0) * 1000),
                "recording": str(offline_path),
                "row_count": len(offline_result.get("answer") or []),
                "step_counts": [s.get("row_count") for s in offline_result.get("call_trace", [])],
            }
            print(f"  offline direct: {offline_result['status']} rows={case['offline_direct']['row_count']}", flush=True)

        if not skip_replay:
            rec = _latest_online_recording(wf_id)
            if rec:
                t0 = time.perf_counter()
                replay_result = replay_from_recording(rec)
                replay_path = save_run(replay_result)
                case["offline_replay"] = {
                    "status": replay_result["status"],
                    "wall_ms": round((time.perf_counter() - t0) * 1000),
                    "source_recording": str(rec),
                    "recording": str(replay_path),
                    "row_count": len(replay_result.get("answer") or []),
                }
                print(f"  offline replay: {replay_result['status']} from {rec.name}", flush=True)
            else:
                case["offline_replay"] = {"status": "skipped", "reason": "no online recording"}

        if run_online:
            t0 = time.perf_counter()
            online_result = run_competency_workflow(
                wf,
                mode="online",
                online_limit=online_limit,
                force_refresh=force_online,
            )
            online_path = save_run(online_result)
            case["online"] = {
                "status": online_result["status"],
                "wall_ms": round((time.perf_counter() - t0) * 1000),
                "recording": str(online_path),
                "row_count": len(online_result.get("answer") or []),
            }
            print(f"  online: {online_result['status']}", flush=True)

        cases.append(case)

    passed = sum(1 for c in cases if (c.get("offline_direct") or {}).get("status") == "pass")
    empty = sum(1 for c in cases if (c.get("offline_direct") or {}).get("status") == "empty")
    errors = sum(1 for c in cases if (c.get("offline_direct") or {}).get("status") == "error")

    report: Dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "cache_db": str(db_path()),
        "workflow_count": len(cases),
        "offline_direct_pass": passed,
        "offline_direct_empty": empty,
        "offline_direct_error": errors,
        "wall_ms": round((time.perf_counter() - suite_started) * 1000),
        "cases": cases,
    }

    REPORT_JSON.parent.mkdir(parents=True, exist_ok=True)
    REPORT_JSON.write_text(json.dumps(report, indent=2), encoding="utf-8")

    lines = [
        "# Chemistry competency E2E report",
        "",
        f"Generated: {report['generated_at']}",
        f"Workflows: {report['workflow_count']} | pass: {passed} | empty: {empty} | error: {errors}",
        "",
        "| workflow | offline direct | rows | replay |",
        "|----------|----------------|------|--------|",
    ]
    for c in cases:
        od = c.get("offline_direct") or {}
        orp = c.get("offline_replay") or {}
        lines.append(
            f"| {c['workflow_id']} | {od.get('status', '-')} | {od.get('row_count', '-')} | {orp.get('status', '-')} |"
        )
    REPORT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"\nReport: {REPORT_JSON}")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Chemistry competency E2E (offline corpus-first)")
    parser.add_argument("--workflow", action="append", help="Limit to workflow id(s)")
    parser.add_argument("--skip-offline-direct", action="store_true")
    parser.add_argument("--skip-replay", action="store_true")
    parser.add_argument("--online", action="store_true", help="Also run online probe per workflow")
    parser.add_argument("--online-limit", type=int, default=DEFAULT_ONLINE_PROBE_LIMIT)
    parser.add_argument("--force", action="store_true", help="Bypass cache on online probe")
    parser.add_argument("--list", action="store_true", help="List workflow ids")
    args = parser.parse_args()

    if args.list:
        for wf_id in list_workflow_ids():
            print(wf_id)
        return

    report = run_e2e(
        workflow_ids=args.workflow,
        skip_offline_direct=args.skip_offline_direct,
        skip_replay=args.skip_replay,
        run_online=args.online,
        online_limit=args.online_limit,
        force_online=args.force,
    )
    print(json.dumps({k: report[k] for k in ("workflow_count", "offline_direct_pass", "offline_direct_empty", "offline_direct_error", "wall_ms")}, indent=2))


if __name__ == "__main__":
    main()
