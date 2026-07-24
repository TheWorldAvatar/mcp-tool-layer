"""Online probe: run chemistry competency workflows with LIMIT 5 and populate cache."""

from __future__ import annotations

import argparse
import json

from mini_marie.marie.chemistry.chemistry_workflow_engine import (
    load_workflow,
    list_workflow_ids,
    run_competency_workflow,
    run_suite_probe,
    save_run,
)
from mini_marie.marie.chemistry.limits import DEFAULT_ONLINE_PROBE_LIMIT


def main() -> None:
    parser = argparse.ArgumentParser(description="Online chemistry competency probe (cached atomics)")
    parser.add_argument("--workflow", help="Workflow id from workflows/competency_suite.json")
    parser.add_argument("--suite", action="store_true", help="Probe all workflows")
    parser.add_argument("--online-limit", type=int, default=DEFAULT_ONLINE_PROBE_LIMIT)
    parser.add_argument("--force", action="store_true", help="Bypass cache reads")
    parser.add_argument("--list", action="store_true", help="List workflow ids")
    args = parser.parse_args()

    if args.list:
        for wf_id in list_workflow_ids():
            print(wf_id)
        return

    if args.suite:
        print(json.dumps(run_suite_probe(online_limit=args.online_limit, force=args.force), indent=2))
        return

    if not args.workflow:
        raise SystemExit("Provide --workflow <id> or --suite")

    result = run_competency_workflow(
        load_workflow(args.workflow),
        mode="online",
        online_limit=args.online_limit,
        force_refresh=args.force,
    )
    path = save_run(result)
    print(json.dumps({"recording": str(path), "status": result["status"]}, indent=2))


if __name__ == "__main__":
    main()
