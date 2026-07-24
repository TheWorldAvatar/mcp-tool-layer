"""Replay a recorded MOF workflow offline with limits removed or raised."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from mini_marie.mop_mof.mof.workflow_engine import (
    load_run,
    load_workflow,
    load_workflow_path,
    resolve_workflow_for_replay,
    run_workflow,
    save_run,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Offline replay of a recorded MOF workflow")
    parser.add_argument("--recording", type=Path, help="Online recording JSON from run_workflow.py")
    parser.add_argument("--workflow", help="Workflow name under workflows/")
    parser.add_argument("--workflow-path", type=Path, help="Path to workflow JSON file")
    parser.add_argument("--offline-cap", type=int, default=500_000)
    parser.add_argument("--json-out", help="Output path for offline run results")
    args = parser.parse_args()

    if args.recording:
        recorded = load_run(args.recording)
        workflow, source = resolve_workflow_for_replay(
            recorded,
            workflow_name=args.workflow,
            workflow_path=args.workflow_path,
        )
        offline_cap = args.offline_cap or recorded.get("offline_cap", 500_000)
        workflow_name = args.workflow or recorded.get("workflow_name")
    elif args.workflow_path:
        workflow = load_workflow_path(args.workflow_path)
        source = f"path:{args.workflow_path}"
        offline_cap = args.offline_cap
        workflow_name = args.workflow_path.stem
    elif args.workflow:
        workflow = load_workflow(args.workflow)
        source = f"name:{args.workflow}"
        offline_cap = args.offline_cap
        workflow_name = args.workflow
    else:
        raise SystemExit("Provide --recording, --workflow, or --workflow-path")

    result = run_workflow(
        workflow,
        mode="offline",
        offline_cap=offline_cap,
        workflow_name=workflow_name,
    )
    result["replayed_from"] = str(args.recording.resolve()) if args.recording else None
    result["workflow_source"] = source
    path = save_run(result, args.json_out)
    print(
        json.dumps(
            {
                "status": result["status"],
                "mode": "offline",
                "output": str(path),
                "workflow_source": source,
                "answer": result["answer"],
                "row_counts": [s.get("row_count") for s in result.get("call_trace", [])],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
