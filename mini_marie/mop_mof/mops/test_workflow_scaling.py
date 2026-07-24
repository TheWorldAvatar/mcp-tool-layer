"""Integration test: MOPs workflow online + offline replay (requires merged_tll data)."""

from __future__ import annotations

import re
import sys
from mini_marie.cache_paths import merged_tll_dir

from mini_marie.mop_mof.mops.workflow_mcp import (
    MCP_ONLINE_LIMIT,
    replay_workflow_offline,
    run_workflow_online,
)


def _parse_field(text: str, key: str) -> str:
    for line in text.splitlines():
        if line.startswith(f"{key}\t"):
            return line.split("\t", 1)[1]
    return ""


def _require_data() -> None:
    data = merged_tll_dir()
    if not data.exists():
        raise SystemExit(f"SKIP: missing local MOPs data at {data}")


def test_list_mops() -> None:
    _require_data()
    print("=== MOPs list_mops (online) ===")
    online_out = run_workflow_online("list_mops_catalog", online_limit=MCP_ONLINE_LIMIT)
    print(online_out[:1200].encode("ascii", errors="replace").decode("ascii"))
    status = _parse_field(online_out, "status")
    recording = _parse_field(online_out, "recording_path")
    assert status == "pass", f"online status={status}"
    assert recording

    print("\n=== MOPs offline replay ===")
    offline_out = replay_workflow_offline(recording, offline_cap=50_000)
    status = _parse_field(offline_out, "status")
    assert status == "pass", f"offline status={status}"
    m = re.search(r"^1\tsparql_plan\tlist_mops\tpass\t(\d+)", offline_out, re.M)
    assert m, "missing list_mops step in offline output"
    offline_rows = int(m.group(1))
    online_m = re.search(r"^1\tsparql_plan\tlist_mops\tpass\t(\d+)", online_out, re.M)
    online_rows = int(online_m.group(1)) if online_m else 0
    assert offline_rows >= online_rows, "offline should return at least as many rows as online"
    print(f"OK offline list_mops rows={offline_rows} (online={online_rows})")


if __name__ == "__main__":
    try:
        test_list_mops()
        print("\nALL MOPS WORKFLOW TESTS PASSED")
    except SystemExit:
        raise
    except Exception as exc:
        print(f"\nFAILED: {exc}", file=sys.stderr)
        raise
