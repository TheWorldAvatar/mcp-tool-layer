"""Offline replay: full-tier cache reads + local joins (no remote SPARQL)."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from mini_marie.mop_mof.mof.competency_workflow_engine import (
    load_run,
    load_workflow,
    replay_competency_from_recording,
    save_run,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Offline competency replay (cache + full limits)")
    parser.add_argument("--recording", type=Path, help="Online recording JSON")
    parser.add_argument("--workflow", help="Workflow id (if no recording)")
    parser.add_argument("--json-out", type=Path, help="Output path")
    args = parser.parse_args()

    if not args.recording:
        raise SystemExit("Offline replay requires --recording from an online probe run")

    recorded = load_run(args.recording)
    embedded = recorded.get("workflow_definition")
    if embedded and embedded.get("steps"):
        workflow = embedded
        workflow_name = args.workflow or recorded.get("workflow_id")
    elif args.workflow:
        workflow = load_workflow(args.workflow)
        workflow_name = args.workflow
    elif recorded.get("workflow_id"):
        workflow = load_workflow(recorded["workflow_id"])
        workflow_name = recorded["workflow_id"]
    else:
        raise SystemExit("Recording missing probed_sequence / workflow_definition")

    result = replay_competency_from_recording(recorded, workflow)
    result["replayed_from"] = str(args.recording.resolve()) if args.recording else None
    path = save_run(result, args.json_out)
    print(
        json.dumps(
            {
                "status": result["status"],
                "mode": "offline",
                "output": str(path),
                "workflow_id": workflow_name,
                "answer": result.get("answer"),
                "cache_stats": result.get("cache_stats"),
                "row_counts": [s.get("row_count") for s in result.get("call_trace", [])],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
