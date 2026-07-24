"""
E2E: all workflows under workflows/*.json — online probe then offline full replay (timed).
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from mini_marie.zaha.twa_city.city_cache import db_path
from mini_marie.zaha.twa_city.sparql_plans import DEFAULT_OFFLINE_CAP, DEFAULT_ONLINE_LIMIT
from mini_marie.zaha.twa_city.workflow_engine import (
    discover_workflow_files,
    load_workflow_path,
    run_workflow,
    save_run,
)

REPORT_JSON = Path(__file__).resolve().parent / "workflow_runs" / "city_e2e_report.json"
REPORT_MD = Path(__file__).resolve().parent / "workflow_runs" / "city_e2e_report.md"


def _fmt_ms(ms: Optional[int]) -> str:
    if ms is None:
        return "-"
    if ms >= 60_000:
        return f"{ms / 1000:.1f}s"
    return f"{ms}ms"


def _answer_short(digest: Dict[str, Any], max_len: int = 100) -> str:
    auth = digest.get("authoritative") or {}
    if auth:
        text = json.dumps(auth, ensure_ascii=False)
    else:
        text = str(digest.get("summary_text") or "")
    return text if len(text) <= max_len else text[: max_len - 3] + "..."


def run_e2e(
    *,
    online_limit: int = DEFAULT_ONLINE_LIMIT,
    offline_cap: int = DEFAULT_OFFLINE_CAP,
    workflow_names: Optional[List[str]] = None,
    skip_online: bool = False,
    skip_offline: bool = False,
    force_offline: bool = True,
) -> Dict[str, Any]:
    paths = discover_workflow_files()
    if workflow_names:
        wanted = set(workflow_names)
        paths = [p for p in paths if p.stem in wanted]

    cases: List[Dict[str, Any]] = []
    started = time.perf_counter()
    print(f"City E2E: {len(paths)} workflows | online_limit={online_limit} offline_cap={offline_cap}")
    print(f"Cache DB: {db_path()}\n")

    for i, path in enumerate(paths, 1):
        wf = load_workflow_path(path)
        name = path.stem
        wf_id = wf.get("id", name)
        print(f"[{i}/{len(paths)}] {name} ({wf.get('city')})", flush=True)
        case: Dict[str, Any] = {
            "workflow_name": name,
            "workflow_id": wf_id,
            "city": wf.get("city"),
            "question": wf.get("question"),
            "online": None,
            "offline": None,
        }
        online_path: Optional[Path] = None

        if not skip_online:
            t0 = time.perf_counter()
            try:
                online_result = run_workflow(
                    wf,
                    mode="online",
                    online_limit=online_limit,
                    workflow_name=name,
                    use_cache=True,
                )
                wall = round((time.perf_counter() - t0) * 1000)
                online_path = save_run(online_result)
                case["online"] = _phase_summary(online_result, online_path, wall)
                print(
                    f"  online: {online_result['status']} {online_result['elapsed_ms']}ms "
                    f"rows={[s.get('row_count') for s in online_result.get('call_trace', [])]}",
                    flush=True,
                )
            except Exception as exc:
                case["online"] = {"status": "error", "error": str(exc)}
                print(f"  online ERROR: {exc}", flush=True)

        if not skip_offline:
            t0 = time.perf_counter()
            try:
                offline_result = run_workflow(
                    wf,
                    mode="offline",
                    offline_cap=offline_cap,
                    workflow_name=name,
                    use_cache=not force_offline,
                    force_refresh=force_offline,
                )
                wall = round((time.perf_counter() - t0) * 1000)
                offline_path = save_run(offline_result)
                case["offline"] = _phase_summary(offline_result, offline_path, wall)
                case["offline"]["replayed_from_online"] = str(online_path) if online_path else None
                auth = (offline_result.get("answer_digest") or {}).get("authoritative") or {}
                print(
                    f"  offline: {offline_result['status']} {offline_result['elapsed_ms']}ms "
                    f"auth={json.dumps(auth, ensure_ascii=False)[:80]}",
                    flush=True,
                )
            except Exception as exc:
                case["offline"] = {"status": "error", "error": str(exc)}
                print(f"  offline ERROR: {exc}", flush=True)

        cases.append(case)
        print("", flush=True)

    total_ms = round((time.perf_counter() - started) * 1000)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "online_limit": online_limit,
        "offline_cap": offline_cap,
        "cache_db": str(db_path()),
        "total_wall_ms": total_ms,
        "workflows_tested": len(cases),
        "online_pass": sum(1 for c in cases if (c.get("online") or {}).get("status") == "pass"),
        "offline_pass": sum(1 for c in cases if (c.get("offline") or {}).get("status") == "pass"),
        "cases": cases,
    }


def _phase_summary(result: Dict[str, Any], path: Path, wall_ms: int) -> Dict[str, Any]:
    return {
        "status": result.get("status"),
        "elapsed_ms": result.get("elapsed_ms"),
        "wall_ms": wall_ms,
        "recording": str(path),
        "answer": result.get("answer"),
        "answer_digest": result.get("answer_digest"),
        "cache_stats": result.get("cache_stats"),
        "step_timings_ms": [s.get("elapsed_ms") for s in result.get("call_trace", [])],
        "step_row_counts": [s.get("row_count") for s in result.get("call_trace", [])],
    }


def write_report(report: Dict[str, Any]) -> None:
    REPORT_JSON.parent.mkdir(parents=True, exist_ok=True)
    REPORT_JSON.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    lines = [
        "# TWA City E2E report",
        "",
        f"Generated: `{report['generated_at']}`",
        f"Cache: `{report['cache_db']}`",
        f"Online limit: **{report['online_limit']}** | Offline cap: **{report['offline_cap']}**",
        f"Total wall: **{_fmt_ms(report['total_wall_ms'])}**",
        "",
        f"Pass: online **{report['online_pass']}** / offline **{report['offline_pass']}** "
        f"of **{report['workflows_tested']}**",
        "",
        "| Workflow | City | Online | Offline | Offline summary |",
        "|----------|------|--------|---------|-----------------|",
    ]
    for case in report["cases"]:
        on = case.get("online") or {}
        off = case.get("offline") or {}
        digest = off.get("answer_digest") or {}
        lines.append(
            f"| {case['workflow_name']} | {case.get('city', '')} | "
            f"{_fmt_ms(on.get('elapsed_ms'))} | {_fmt_ms(off.get('elapsed_ms'))} | "
            f"{_answer_short(digest)} |"
        )
    lines.append("")
    REPORT_MD.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {REPORT_JSON}")
    print(f"Wrote {REPORT_MD}")


def main() -> None:
    parser = argparse.ArgumentParser(description="TWA city workflow E2E with cache")
    parser.add_argument("--online-limit", type=int, default=DEFAULT_ONLINE_LIMIT)
    parser.add_argument("--offline-cap", type=int, default=DEFAULT_OFFLINE_CAP)
    parser.add_argument("--workflow", action="append", help="Workflow file stem(s)")
    parser.add_argument("--skip-online", action="store_true")
    parser.add_argument("--skip-offline", action="store_true")
    parser.add_argument("--no-force-offline", action="store_true")
    args = parser.parse_args()

    report = run_e2e(
        online_limit=args.online_limit,
        offline_cap=args.offline_cap,
        workflow_names=args.workflow,
        skip_online=args.skip_online,
        skip_offline=args.skip_offline,
        force_offline=not args.no_force_offline,
    )
    write_report(report)
    print(
        f"Done: online_pass={report['online_pass']} offline_pass={report['offline_pass']} "
        f"total={_fmt_ms(report['total_wall_ms'])}"
    )


if __name__ == "__main__":
    main()
