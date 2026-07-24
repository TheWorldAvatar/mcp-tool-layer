"""Replay a recorded online probe: same call sequence, full-tier cache only."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from mini_marie.zaha.twa_city.workflow_engine import (
    load_run,
    load_workflow,
    load_workflow_path,
    replay_workflow_from_recording,
    resolve_workflow_for_replay,
    save_run,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Offline replay of a recorded TWA city workflow")
    parser.add_argument(
        "--recording",
        type=Path,
        help="Online recording JSON from run_workflow.py",
    )
    parser.add_argument(
        "--workflow",
        help="Workflow name under workflows/ (overrides recording resolution)",
    )
    parser.add_argument(
        "--workflow-path",
        type=Path,
        help="Path to a workflow JSON file (overrides --workflow and recording)",
    )
    parser.add_argument("--json-out", help="Output path for offline run results")
    args = parser.parse_args()

    if not args.recording:
        raise SystemExit("Offline replay requires --recording from an online probe run")

    recorded = load_run(args.recording)
    workflow, source = resolve_workflow_for_replay(
        recorded,
        workflow_name=args.workflow,
        workflow_path=args.workflow_path,
    )
    workflow_name = args.workflow or recorded.get("workflow_name")
    result = replay_workflow_from_recording(
        recorded,
        workflow,
        workflow_name=workflow_name,
    )
    result["replayed_from"] = str(args.recording.resolve())
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
