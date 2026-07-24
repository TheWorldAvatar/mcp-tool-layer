"""Offline replay of chemistry competency workflow recordings (cache-only)."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from mini_marie.marie.chemistry.chemistry_workflow_engine import replay_from_recording, save_run


def main() -> None:
    parser = argparse.ArgumentParser(description="Offline chemistry competency replay")
    parser.add_argument("recording", type=Path, help="Path to online probe JSON recording")
    args = parser.parse_args()

    result = replay_from_recording(args.recording)
    path = save_run(result)
    print(
        json.dumps(
            {
                "status": result["status"],
                "offline_recording": str(path),
                "row_counts": [s.get("row_count") for s in result.get("call_trace", [])],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
