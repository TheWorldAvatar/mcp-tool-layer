"""
End-to-end competency test: online probe -> warm full atomics -> offline cache replay.

Times every workflow, writes JSON + Markdown report under competency_runs/.
Offline uses full-tier SQLite cache + local joins only (no remote SPARQL).
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from mini_marie.mop_mof.mof.competency_cache import cache_dir, db_path
from mini_marie.mop_mof.mof.atomic_warm_manifest import workflow_driven_warm_specs
from mini_marie.mop_mof.mof.competency_cache import warm_full_calls
from mini_marie.mop_mof.mof.competency_workflow_engine import (
    DEFAULT_ONLINE_LIMIT,
    build_answer_digest,
    load_manifest,
    replay_competency_from_recording,
    run_competency_workflow,
    save_run,
)

REPORT_JSON = Path(__file__).resolve().parent / "competency_runs" / "e2e_report.json"
REPORT_MD = Path(__file__).resolve().parent / "competency_runs" / "e2e_report.md"


def _fmt_ms(ms: Optional[int]) -> str:
    if ms is None:
        return "-"
    if ms >= 60_000:
        return f"{ms / 1000:.1f}s"
    return f"{ms}ms"


def _answer_short(digest: Dict[str, Any], max_len: int = 120) -> str:
    auth = digest.get("authoritative") or {}
    if auth:
        text = json.dumps(auth, ensure_ascii=False)
    else:
        text = str(digest.get("summary_text") or "")
    if len(text) > max_len:
        return text[: max_len - 3] + "..."
    return text


def run_e2e(
    *,
    online_limit: int = DEFAULT_ONLINE_LIMIT,
    workflow_ids: Optional[List[str]] = None,
    skip_online: bool = False,
    skip_warm: bool = False,
    comprehensive_warm: bool = True,
    skip_offline: bool = False,
    force_online: bool = False,
    force_warm: bool = False,
) -> Dict[str, Any]:
    manifest = load_manifest()
    workflows = manifest.get("workflows", [])
    if workflow_ids:
        wanted = set(workflow_ids)
        workflows = [w for w in workflows if w.get("id") in wanted]

    suite_started = time.perf_counter()
    cases: List[Dict[str, Any]] = []

    if comprehensive_warm and not skip_warm:
        print("Suite comprehensive warm (all atomics)...", flush=True)
        t0 = time.perf_counter()
        warm_full_calls(workflow_driven_warm_specs(), force=force_warm)
        print(f"  comprehensive warm done wall={round((time.perf_counter() - t0) * 1000)}ms\n", flush=True)

    print(f"E2E: {len(workflows)} workflows | online_limit={online_limit}")
    print(f"Cache DB: {db_path()}\n")

    for i, wf in enumerate(workflows, 1):
        wf_id = wf.get("id", "?")
        question = wf.get("question", "")
        print(f"[{i}/{len(workflows)}] {wf_id}", flush=True)

        case: Dict[str, Any] = {
            "workflow_id": wf_id,
            "question": question,
            "online": None,
            "warm": None,
            "offline": None,
        }

        online_recording: Optional[Path] = None
        if not skip_online:
            t0 = time.perf_counter()
            try:
                online_result = run_competency_workflow(
                    wf,
                    mode="online",
                    online_limit=online_limit,
                    use_cache=not force_online,
                    force_refresh=force_online,
                )
                online_ms = round((time.perf_counter() - t0) * 1000)
                online_recording = save_run(online_result)
                case["online"] = {
                    "status": online_result["status"],
                    "elapsed_ms": online_result["elapsed_ms"],
                    "wall_ms": online_ms,
                    "recording": str(online_recording),
                    "answer": online_result.get("answer"),
                    "answer_digest": online_result.get("answer_digest"),
                    "cache_stats": online_result.get("cache_stats"),
                    "step_timings_ms": [
                        s.get("elapsed_ms") for s in online_result.get("call_trace", [])
                    ],
                }
                print(
                    f"  online: {online_result['status']} "
                    f"workflow={online_result['elapsed_ms']}ms wall={online_ms}ms",
                    flush=True,
                )
            except Exception as exc:
                case["online"] = {
                    "status": "error",
                    "error": f"{type(exc).__name__}: {exc}",
                    "wall_ms": round((time.perf_counter() - t0) * 1000),
                }
                print(f"  online: ERROR {exc}", flush=True)

        if not skip_offline:
            t0 = time.perf_counter()
            try:
                if not online_recording:
                    raise RuntimeError("offline replay requires online recording with probed_sequence")
                recorded = json.loads(Path(online_recording).read_text(encoding="utf-8"))
                offline_result = replay_competency_from_recording(recorded, wf)
                offline_ms = round((time.perf_counter() - t0) * 1000)
                offline_recording = save_run(offline_result)
                case["offline"] = {
                    "status": offline_result["status"],
                    "elapsed_ms": offline_result["elapsed_ms"],
                    "wall_ms": offline_ms,
                    "recording": str(offline_recording),
                    "answer": offline_result.get("answer"),
                    "answer_digest": offline_result.get("answer_digest"),
                    "cache_stats": offline_result.get("cache_stats"),
                    "step_timings_ms": [
                        s.get("elapsed_ms") for s in offline_result.get("call_trace", [])
                    ],
                    "replayed_from_online": str(online_recording) if online_recording else None,
                }
                auth = (offline_result.get("answer_digest") or {}).get("authoritative") or {}
                print(
                    f"  offline: {offline_result['status']} "
                    f"workflow={offline_result['elapsed_ms']}ms wall={offline_ms}ms "
                    f"auth={json.dumps(auth, ensure_ascii=False)[:100]}",
                    flush=True,
                )
            except Exception as exc:
                case["offline"] = {
                    "status": "error",
                    "error": f"{type(exc).__name__}: {exc}",
                    "wall_ms": round((time.perf_counter() - t0) * 1000),
                }
                print(f"  offline: ERROR {exc}", flush=True)

        cases.append(case)
        print("", flush=True)

    total_ms = round((time.perf_counter() - suite_started) * 1000)
    online_ok = sum(1 for c in cases if (c.get("online") or {}).get("status") == "pass")
    offline_ok = sum(1 for c in cases if (c.get("offline") or {}).get("status") == "pass")

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "suite_id": manifest.get("id"),
        "online_limit": online_limit,
        "cache_dir": str(cache_dir()),
        "cache_db": str(db_path()),
        "total_wall_ms": total_ms,
        "workflows_tested": len(cases),
        "online_pass": online_ok,
        "offline_pass": offline_ok,
        "cases": cases,
    }
    return report


def write_report(report: Dict[str, Any]) -> None:
    REPORT_JSON.parent.mkdir(parents=True, exist_ok=True)
    REPORT_JSON.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    lines = [
        "# MOF competency E2E report",
        "",
        f"Generated: `{report['generated_at']}`",
        f"Cache: `{report['cache_db']}`",
        f"Online limit: **{report['online_limit']}** | Offline: full-tier cache + local joins",
        f"Total wall time: **{_fmt_ms(report['total_wall_ms'])}**",
        "",
        f"Pass: online **{report['online_pass']}** / offline **{report['offline_pass']}** "
        f"of **{report['workflows_tested']}** workflows",
        "",
        "## Timing and answers",
        "",
        "| Workflow | Online | Offline | Speedup | Offline answer (authoritative) |",
        "|----------|--------|---------|---------|--------------------------------|",
    ]

    for case in report["cases"]:
        wf_id = case["workflow_id"]
        on = case.get("online") or {}
        off = case.get("offline") or {}
        on_ms = on.get("elapsed_ms")
        off_ms = off.get("elapsed_ms")
        speedup = ""
        if on_ms and off_ms and off_ms > 0:
            speedup = f"{on_ms / off_ms:.2f}x" if on_ms > off_ms else f"{off_ms / on_ms:.2f}x slower"
        off_digest = off.get("answer_digest") or {}
        off_short = _answer_short(off_digest, 80)
        lines.append(
            f"| {wf_id} | {_fmt_ms(on_ms)} | {_fmt_ms(off_ms)} | {speedup} | {off_short} |"
        )

    lines.extend(["", "## Per-workflow detail", ""])
    for case in report["cases"]:
        lines.append(f"### {case['workflow_id']}")
        lines.append(f"**Q:** {case['question']}")
        lines.append("")
        for phase in ("online", "warm", "offline"):
            ph = case.get(phase)
            if not ph:
                continue
            lines.append(f"**{phase}:** status=`{ph.get('status')}` elapsed=`{_fmt_ms(ph.get('elapsed_ms'))}` "
                         f"wall=`{_fmt_ms(ph.get('wall_ms'))}`")
            if ph.get("recording"):
                lines.append(f"- recording: `{ph['recording']}`")
            digest = ph.get("answer_digest") or {}
            if digest.get("authoritative"):
                lines.append(f"- authoritative: `{json.dumps(digest['authoritative'], ensure_ascii=False)}`")
            if digest.get("steps"):
                step_line = ", ".join(
                    f"{s['name']}:{s['row_count']}rows/{s.get('elapsed_ms')}ms"
                    for s in digest["steps"]
                    if s.get("status") != "skipped"
                )
                lines.append(f"- steps: {step_line}")
            if ph.get("error"):
                lines.append(f"- error: `{ph['error']}`")
            lines.append("")

    REPORT_MD.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {REPORT_JSON}")
    print(f"Wrote {REPORT_MD}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="MOF competency E2E: online probe -> warm full cache -> offline replay"
    )
    parser.add_argument("--online-limit", type=int, default=DEFAULT_ONLINE_LIMIT)
    parser.add_argument("--workflow", action="append", help="Run subset of workflow ids")
    parser.add_argument("--skip-online", action="store_true")
    parser.add_argument("--skip-warm", action="store_true", help="Skip suite comprehensive warm")
    parser.add_argument("--no-comprehensive-warm", action="store_true", help="Do not warm all atomics up front")
    parser.add_argument("--skip-offline", action="store_true")
    parser.add_argument("--force-warm", action="store_true", help="Re-fetch full atomics remotely")
    parser.add_argument("--force-online", action="store_true")
    args = parser.parse_args()

    report = run_e2e(
        online_limit=args.online_limit,
        workflow_ids=args.workflow,
        skip_online=args.skip_online,
        skip_warm=args.skip_warm,
        comprehensive_warm=not args.no_comprehensive_warm,
        skip_offline=args.skip_offline,
        force_online=args.force_online,
        force_warm=args.force_warm,
    )
    write_report(report)
    print(
        f"\nDone: online_pass={report['online_pass']} offline_pass={report['offline_pass']} "
        f"total={_fmt_ms(report['total_wall_ms'])}"
    )


if __name__ == "__main__":
    main()
