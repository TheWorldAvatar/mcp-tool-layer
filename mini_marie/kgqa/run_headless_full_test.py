"""
Headless full-cycle KGQA test over the question catalog.

Runs online ReAct + offline replay for every catalog entry and writes a JSON/MD report.

Usage:
  python -m mini_marie.kgqa.run_headless_full_test
  python -m mini_marie.kgqa.run_headless_full_test --kind competency
  python -m mini_marie.kgqa.run_headless_full_test --limit 3
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv

from mini_marie.cache_paths import repo_root as REPO_ROOT
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

load_dotenv()

from mini_marie.kgqa.orchestrator import run_offline_phase, run_online_phase
from mini_marie.kgqa.question_catalog import CatalogEntry, filter_catalog, load_catalog

RUNS_DIR = Path(__file__).resolve().parent / "headless_runs"


def _offline_status(result: Dict[str, Any]) -> str:
    offline = result.get("offline") or {}
    if not result.get("recording_path"):
        return "skipped_no_recording"
    if offline.get("skipped"):
        return "skipped_cache"
    return str(offline.get("status") or "missing")


def _tools(result: Dict[str, Any]) -> List[str]:
    meta = result.get("metadata") or {}
    return list(meta.get("tool_activity", {}).get("executed_tool_name_set") or [])


def run_one(
    entry: CatalogEntry,
    *,
    model_name: str,
    recursion_limit: int,
    offline_cap: int,
    auto_offline: bool,
) -> Dict[str, Any]:
    row: Dict[str, Any] = {
        "id": entry.id,
        "kind": entry.kind,
        "domain": entry.domain,
        "question": entry.question,
        "workflow_id": entry.workflow_id,
        "expected_mcps": entry.mcp_servers,
        "status": "pending",
        "error": None,
    }
    t0 = time.perf_counter()
    try:
        partial = run_online_phase(
            entry.question,
            model_name=model_name,
            recursion_limit=recursion_limit,
        )
        row["route"] = partial.get("route")
        row["online_ms"] = (partial.get("timing") or {}).get("online_ms")
        row["recording_path"] = partial.get("recording_path")
        row["tools"] = _tools(partial)
        row["online_answer_preview"] = (partial.get("online_answer") or "")[:400]
        row["llm_tokens"] = (
            (partial.get("metadata") or {}).get("aggregated_usage") or {}
        ).get("total_tokens")

        if auto_offline and partial.get("recording_path"):
            result = run_offline_phase(partial, offline_cap=offline_cap)
        elif auto_offline:
            result = partial
            row["offline_status"] = "skipped_no_recording"
        else:
            result = partial
            row["offline_status"] = "disabled"

        timing = result.get("timing") or {}
        row["offline_ms"] = timing.get("offline_ms", 0)
        row["total_ms"] = timing.get("total_ms", round((time.perf_counter() - t0) * 1000))
        row["offline_recording_path"] = result.get("offline_recording_path")
        if "offline_status" not in row:
            row["offline_status"] = _offline_status(result)
        row["offline_row_count"] = (result.get("offline") or {}).get("row_count")
        expected_empty = "expected_empty" in (entry.tags or [])
        if expected_empty and row.get("offline_status") == "empty":
            row["status"] = "pass"
        elif partial.get("recording_path") and row["offline_status"] not in (
            "pass",
            "skipped_cache",
            "skipped_no_recording",
            "disabled",
        ):
            row["status"] = "fail"
        elif not partial.get("online_answer"):
            row["status"] = "fail"
        else:
            row["status"] = "pass"
    except Exception as exc:
        row["status"] = "error"
        row["error"] = f"{type(exc).__name__}: {exc}"
        row["traceback"] = traceback.format_exc()
        row["total_ms"] = round((time.perf_counter() - t0) * 1000)
    return row


def write_report(rows: List[Dict[str, Any]], path_stem: Path, *, model_name: str) -> None:
    path_stem.parent.mkdir(parents=True, exist_ok=True)
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model": model_name,
        "total": len(rows),
        "pass": sum(1 for r in rows if r["status"] == "pass"),
        "fail": sum(1 for r in rows if r["status"] == "fail"),
        "error": sum(1 for r in rows if r["status"] == "error"),
        "skipped_offline_no_recording": sum(
            1 for r in rows if r.get("offline_status") == "skipped_no_recording"
        ),
        "total_online_ms": sum(r.get("online_ms") or 0 for r in rows),
        "total_offline_ms": sum(r.get("offline_ms") or 0 for r in rows),
        "total_ms": sum(r.get("total_ms") or 0 for r in rows),
    }
    payload = {"summary": summary, "results": rows}
    json_path = path_stem.with_suffix(".json")
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    md_lines = [
        "# KGQA headless full test",
        "",
        f"- Generated: {summary['generated_at']}",
        f"- Model: {model_name}",
        f"- Total: {summary['total']} | pass: {summary['pass']} | fail: {summary['fail']} | error: {summary['error']}",
        f"- Wall time (sum of per-question ms): {summary['total_ms']} ms",
        f"- Online ms sum: {summary['total_online_ms']} | Offline ms sum: {summary['total_offline_ms']}",
        "",
        "| id | domain | status | offline | online_ms | offline_ms | tools |",
        "|----|--------|--------|---------|-----------|------------|-------|",
    ]
    for r in rows:
        md_lines.append(
            "| {id} | {domain} | {status} | {off} | {om} | {of} | {tools} |".format(
                id=r["id"],
                domain=r["domain"],
                status=r["status"],
                off=r.get("offline_status", ""),
                om=r.get("online_ms", ""),
                of=r.get("offline_ms", ""),
                tools=", ".join(r.get("tools") or [])[:40],
            )
        )
    md_path = path_stem.with_suffix(".md")
    md_path.write_text("\n".join(md_lines), encoding="utf-8")
    print(f"Report: {json_path}")
    print(f"Report: {md_path}")
    print(json.dumps(summary, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser(description="Headless KGQA full catalog test")
    parser.add_argument("--kind", choices=["competency", "example", "all"], default="all")
    parser.add_argument("--domain", default="all", help="Domain filter or 'all'")
    parser.add_argument("--limit", type=int, default=0, help="Max questions (0 = all)")
    parser.add_argument(
        "--ids",
        default="",
        help="Comma-separated catalog entry ids to run (overrides --limit when set)",
    )
    parser.add_argument("--model", default="gpt-4o-mini")
    parser.add_argument("--recursion-limit", type=int, default=120)
    parser.add_argument("--offline-cap", type=int, default=500_000)
    parser.add_argument("--no-offline", action="store_true")
    args = parser.parse_args()

    kind = None if args.kind == "all" else args.kind
    domain = None if args.domain == "all" else args.domain
    entries = filter_catalog(domain=domain, kind=kind)
    if args.ids.strip():
        wanted = {x.strip() for x in args.ids.split(",") if x.strip()}
        entries = [e for e in entries if e.id in wanted]
        missing = wanted - {e.id for e in entries}
        if missing:
            print(f"Warning: unknown catalog ids: {sorted(missing)}", flush=True)
    elif args.limit > 0:
        entries = entries[: args.limit]

    if not entries:
        print("No catalog entries match filters.")
        return 1

    ts = int(time.time())
    path_stem = RUNS_DIR / f"full_test_{ts}"
    print(f"Running {len(entries)} questions (model={args.model})…", flush=True)

    rows: List[Dict[str, Any]] = []
    for i, entry in enumerate(entries, 1):
        print(f"[{i}/{len(entries)}] {entry.id} ({entry.domain}) …", flush=True)
        row = run_one(
            entry,
            model_name=args.model,
            recursion_limit=args.recursion_limit,
            offline_cap=args.offline_cap,
            auto_offline=not args.no_offline,
        )
        rows.append(row)
        write_report(rows, path_stem, model_name=args.model)
        print(
            f"  -> {row['status']} | online={row.get('online_ms')}ms "
            f"offline={row.get('offline_status')} | tools={row.get('tools')}",
            flush=True,
        )

    write_report(rows, path_stem, model_name=args.model)
    fails = sum(1 for r in rows if r["status"] != "pass")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
