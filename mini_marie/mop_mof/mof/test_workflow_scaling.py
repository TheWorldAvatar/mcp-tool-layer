"""Integration test: MOF workflow online (LIMIT 10) + offline replay."""

from __future__ import annotations

import re
import sys

from mini_marie.mop_mof.mof.workflow_mcp import (
    MCP_ONLINE_LIMIT,
    replay_workflow_offline,
    run_workflow_online,
)


def _parse_field(text: str, key: str) -> str:
    for line in text.splitlines():
        if line.startswith(f"{key}\t"):
            return line.split("\t", 1)[1]
    return ""


def test_corpus_share_workflow() -> None:
    print("=== MOF corpus share (online) ===")
    out = run_workflow_online("tobassco_co2_corpus_share", online_limit=MCP_ONLINE_LIMIT)
    print(out[:1500])
    status = _parse_field(out, "status")
    recording = _parse_field(out, "recording_path")
    assert status == "pass", f"status={status}"
    assert recording, "missing recording_path"
    print(f"OK recording={recording}")


def test_top10_workflow() -> None:
    print("\n=== MOF top10 CO2 properties (online) ===")
    online_out = run_workflow_online("top10_tobassco_co2_properties", online_limit=MCP_ONLINE_LIMIT)
    status = _parse_field(online_out, "status")
    recording = _parse_field(online_out, "recording_path")
    assert status == "pass", f"online status={status}"
    assert recording

    print("\n=== MOF offline replay ===")
    offline_out = replay_workflow_offline(recording, offline_cap=500_000)
    status = _parse_field(offline_out, "status")
    assert status == "pass", f"offline status={status}"
    m = re.search(r"^1\tsparql_plan\trank_tobassco_co2\tpass\t(\d+)", offline_out, re.M)
    assert m and int(m.group(1)) > MCP_ONLINE_LIMIT, "offline rank should exceed online limit"
    print(f"OK offline rank rows={m.group(1)}")


if __name__ == "__main__":
    try:
        test_corpus_share_workflow()
        test_top10_workflow()
        print("\nALL MOF WORKFLOW TESTS PASSED")
    except Exception as exc:
        print(f"\nFAILED: {exc}", file=sys.stderr)
        raise
