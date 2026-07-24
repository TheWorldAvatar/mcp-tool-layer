"""Integration test: online (LIMIT 10) + offline replay via MCP helpers."""

from __future__ import annotations

import re
import sys

from mini_marie.zaha.twa_city.workflow_mcp import (
    MCP_ONLINE_LIMIT,
    replay_workflow_offline,
    run_workflow_online,
)


def _parse_field(text: str, key: str) -> str:
    for line in text.splitlines():
        if line.startswith(f"{key}\t"):
            return line.split("\t", 1)[1]
    return ""


def test_kl_workflow() -> None:
    print("=== ONLINE (limit=10) ===")
    online_out = run_workflow_online("top10_buildings_locations_kl", online_limit=MCP_ONLINE_LIMIT)
    print(online_out[:2000])
    status = _parse_field(online_out, "status")
    recording = _parse_field(online_out, "recording_path")
    assert status == "pass", f"online status={status}"
    assert recording, "missing recording_path"
    print(f"OK online recording={recording}")

    print("\n=== OFFLINE REPLAY ===")
    offline_out = replay_workflow_offline(recording, offline_cap=500_000)
    print(offline_out[:2000])
    status = _parse_field(offline_out, "status")
    assert status == "pass", f"offline status={status}"
    # Step 1 should have more rows than online (104k heights in KL)
    m = re.search(r"^1\tsparql_plan\trank_buildings_by_height\tpass\t(\d+)", offline_out, re.M)
    assert m and int(m.group(1)) > MCP_ONLINE_LIMIT, "offline rank should exceed online limit"
    print(f"OK offline rank rows={m.group(1)}")


def test_bremen_workflow() -> None:
    print("\n=== BREMEN ONLINE ===")
    online_out = run_workflow_online("top10_buildings_locations_bremen", online_limit=MCP_ONLINE_LIMIT)
    status = _parse_field(online_out, "status")
    recording = _parse_field(online_out, "recording_path")
    assert status == "pass", f"bremen online status={status}"
    print(f"OK bremen online recording={recording}")


if __name__ == "__main__":
    try:
        test_kl_workflow()
        test_bremen_workflow()
        print("\nALL TESTS PASSED")
    except Exception as exc:
        print(f"\nFAILED: {exc}", file=sys.stderr)
        raise
