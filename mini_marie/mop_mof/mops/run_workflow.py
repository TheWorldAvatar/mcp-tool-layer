"""Run a MOPs TWA workflow online (limited SPARQL) and record the call trace for offline replay."""

from __future__ import annotations

import argparse
import json

from mini_marie.mop_mof.mops.workflow_engine import load_workflow, run_workflow, save_run


def main() -> None:
    parser = argparse.ArgumentParser(description="Run MOPs TWA workflow (online probe + record)")
    parser.add_argument("--workflow", required=True, help="Workflow name under workflows/ (no .json)")
    parser.add_argument("--online-limit", type=int, help="Override default online LIMIT")
    parser.add_argument("--json-out", help="Optional path for recording JSON")
    args = parser.parse_args()

    workflow = load_workflow(args.workflow)
    result = run_workflow(
        workflow,
        mode="online",
        online_limit=args.online_limit,
        workflow_name=args.workflow,
    )
    path = save_run(result, args.json_out)
    print(json.dumps({"status": result["status"], "recording": str(path), "answer": result["answer"]}, indent=2))


if __name__ == "__main__":
    main()
