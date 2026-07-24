"""Online probe: run competency workflows with small limits and populate local cache."""

from __future__ import annotations

import argparse
import json

from mini_marie.mop_mof.mof.competency_workflow_engine import (
    DEFAULT_ONLINE_LIMIT,
    list_workflow_ids,
    load_workflow,
    run_competency_workflow,
    run_suite_probe,
    save_run,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Online competency probe (cached atomics)")
    parser.add_argument("--workflow", help="Single workflow id from competency_suite.json")
    parser.add_argument("--suite", action="store_true", help="Probe all workflows in manifest")
    parser.add_argument("--online-limit", type=int, default=DEFAULT_ONLINE_LIMIT)
    parser.add_argument("--force", action="store_true", help="Bypass cache reads and refresh entries")
    parser.add_argument("--list", action="store_true", help="List workflow ids")
    args = parser.parse_args()

    if args.list:
        for wf_id in list_workflow_ids():
            print(wf_id)
        return

    if args.suite:
        summary = run_suite_probe(online_limit=args.online_limit, force=args.force)
        print(json.dumps(summary, indent=2))
        return

    if not args.workflow:
        raise SystemExit("Provide --workflow <id> or --suite")

    workflow = load_workflow(args.workflow)
    result = run_competency_workflow(
        workflow,
        mode="online",
        online_limit=args.online_limit,
        force_refresh=args.force,
    )
    path = save_run(result)
    print(
        json.dumps(
            {
                "status": result["status"],
                "mode": "online",
                "recording": str(path),
                "answer": result.get("answer"),
                "cache_stats": result.get("cache_stats"),
                "row_counts": [s.get("row_count") for s in result.get("call_trace", [])],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
