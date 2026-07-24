"""
Multi-scenario tests for dynamic workflow discovery and offline replay resolution.

Unit tests run offline (no SPARQL). Integration tests hit the KL SPARQL endpoint
with small limits/caps to keep runtime reasonable.
"""

from __future__ import annotations

import json
import re
import sys
import tempfile
import time
from pathlib import Path

from mini_marie.zaha.twa_city.workflow_engine import (
    discover_workflow_catalog,
    find_workflow_names_by_id,
    load_run,
    load_workflow,
    resolve_workflow_for_replay,
    run_workflow,
    save_run,
    RUNS_DIR,
    WORKFLOWS_DIR,
)
from mini_marie.zaha.twa_city.workflow_mcp import list_workflows_text, replay_workflow_offline, run_workflow_online

LEGACY_RECORDING = Path(
    "mini_marie/zaha/twa_city/workflow_runs/WF_TOP50_NON_DOMESTIC_LOCATIONS_online_1780311331.json"
)


def _mini_kl_workflow(wf_id: str = "WF_TEST_RESOLUTION_MINI") -> dict:
    """Small 1-step workflow for fast integration tests."""
    return {
        "id": wf_id,
        "question": "Mini test: rank top buildings in KL",
        "city": "kaiserslautern",
        "top_n": 3,
        "online_limit": 5,
        "offline_cap": 200,
        "steps": [
            {
                "tool": "rank_buildings_by_height",
                "args": {"city": "$city"},
                "extract": {"ranked_rows": {"pick": "all_rows"}},
            }
        ],
        "final_summary": "Ranked $top_n probe in $city",
    }


def test_catalog_discovers_all_preset_workflows() -> None:
    catalog = discover_workflow_catalog()
    names = set(catalog)
    expected = {
        "top10_buildings_locations_bremen",
        "top10_buildings_locations_kl",
        "top50_non_domestic_locations_bremen",
    }
    assert expected.issubset(names), f"missing workflows: {expected - names}"
    for name in expected:
        assert catalog[name]["id"], f"{name} missing id"
        assert catalog[name]["city"], f"{name} missing city"
    text = list_workflows_text()
    assert "top10_buildings_locations_kl" in text
    print("  [pass] catalog auto-discovery")


def test_resolve_legacy_recording_by_workflow_id() -> None:
    """Old recording: no workflow_definition / workflow_name → scan workflows/ by id."""
    recorded = load_run(LEGACY_RECORDING)
    assert "workflow_definition" not in recorded
    assert "workflow_name" not in recorded
    wf, source = resolve_workflow_for_replay(recorded)
    assert wf["id"] == recorded["workflow_id"]
    assert source.startswith("id:")
    print(f"  [pass] legacy id scan -> {source}")


def test_resolve_embedded_definition_only() -> None:
    """Recording carries full workflow JSON — no file lookup needed."""
    wf = _mini_kl_workflow("WF_EMBEDDED_ONLY_XYZ")
    recorded = {
        "workflow_id": "WF_DOES_NOT_EXIST_ON_DISK",
        "workflow_definition": wf,
    }
    resolved, source = resolve_workflow_for_replay(recorded)
    assert resolved["steps"] == wf["steps"]
    assert source.startswith("embedded:")
    print(f"  [pass] embedded-only -> {source}")


def test_resolve_recording_workflow_name() -> None:
    recorded = {
        "workflow_id": "WF_TOP10_LOCATIONS_DIRECT",
        "workflow_name": "top10_buildings_locations_kl",
    }
    wf, source = resolve_workflow_for_replay(recorded)
    assert wf["city"] == "kaiserslautern"
    assert source == "recording_name:top10_buildings_locations_kl"
    print(f"  [pass] recording workflow_name -> {source}")


def test_resolve_explicit_name_override() -> None:
    recorded = load_run(LEGACY_RECORDING)
    wf, source = resolve_workflow_for_replay(
        recorded,
        workflow_name="top50_non_domestic_locations_bremen",
    )
    assert wf["usage_type"] == "Non-Domestic"
    assert source == "name:top50_non_domestic_locations_bremen"
    print(f"  [pass] explicit --workflow override -> {source}")


def test_resolve_external_workflow_path() -> None:
    wf = _mini_kl_workflow("WF_ADHOC_EXTERNAL")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "custom_adhoc_workflow.json"
        path.write_text(json.dumps(wf), encoding="utf-8")
        recorded = {"workflow_id": "WF_IRRELEVANT"}
        resolved, source = resolve_workflow_for_replay(recorded, workflow_path=path)
        assert resolved["id"] == "WF_ADHOC_EXTERNAL"
        assert str(path) in source
    print("  [pass] external --workflow-path")


def test_resolve_unknown_id_raises() -> None:
    recorded = {"workflow_id": "WF_TOTALLY_UNKNOWN_999"}
    try:
        resolve_workflow_for_replay(recorded)
        raise AssertionError("expected ValueError")
    except ValueError as exc:
        assert "Cannot resolve workflow" in str(exc)
    print("  [pass] unknown workflow_id raises ValueError")


def test_resolve_duplicate_id_raises() -> None:
    wf = _mini_kl_workflow("WF_TOP10_LOCATIONS_DIRECT")
    with tempfile.TemporaryDirectory() as tmp:
        dup_path = Path(tmp) / "duplicate_id.json"
        dup_path.write_text(json.dumps(wf), encoding="utf-8")
        # Temporarily add duplicate to discovery by writing into workflows/
        # Use a unique temp copy approach: patch find by writing to workflows dir
        target = WORKFLOWS_DIR / "_test_duplicate_id_tmp.json"
        try:
            target.write_text(json.dumps(wf), encoding="utf-8")
            matches = find_workflow_names_by_id("WF_TOP10_LOCATIONS_DIRECT")
            assert len(matches) >= 2, f"expected duplicate ids, got {matches}"
            recorded = {"workflow_id": "WF_TOP10_LOCATIONS_DIRECT"}
            try:
                resolve_workflow_for_replay(recorded)
                raise AssertionError("expected ValueError for duplicate id")
            except ValueError as exc:
                assert "multiple workflow files" in str(exc)
        finally:
            if target.exists():
                target.unlink()
    print("  [pass] duplicate workflow_id raises ValueError")


def test_online_recording_embeds_definition() -> None:
    """New online runs must store workflow_name + workflow_definition for replay."""
    with tempfile.TemporaryDirectory() as tmp:
        wf = _mini_kl_workflow("WF_TEST_EMBED_ONLINE")
        out = Path(tmp) / "online.json"
        result = run_workflow(wf, mode="online", online_limit=5, workflow_name="adhoc_mini")
        path = save_run(result, out)
        recorded = load_run(path)
        assert recorded.get("workflow_name") == "adhoc_mini"
        assert recorded.get("workflow_definition", {}).get("id") == "WF_TEST_EMBED_ONLINE"
        assert recorded["status"] == "pass"
        print(f"  [pass] online embeds definition ({recorded['call_trace'][0]['row_count']} rows)")


def test_replay_from_embedded_recording() -> None:
    """Offline replay using embedded workflow_definition (file-independent)."""
    wf = _mini_kl_workflow("WF_TEST_EMBED_REPLAY")
    online = run_workflow(wf, mode="online", online_limit=5, workflow_name="adhoc_replay")
    recording_path = save_run(online)
    recorded = load_run(recording_path)
    # Simulate missing preset file: only embedded def remains useful
    recorded.pop("workflow_name", None)
    assert "workflow_definition" in recorded

    offline = run_workflow(
        recorded["workflow_definition"],
        mode="offline",
        offline_cap=200,
    )
    online_rows = online["call_trace"][0]["row_count"]
    offline_rows = offline["call_trace"][0]["row_count"]
    assert offline_rows > online_rows, f"offline {offline_rows} should exceed online {online_rows}"
    print(f"  [pass] embedded replay online={online_rows} offline={offline_rows}")


def test_join_offline_save_is_bounded() -> None:
    """Offline join workflow uses SQL join — compact sidecar, no 13M-row location pool."""
    rec_path = RUNS_DIR / "WF_TOP10_LOCATIONS_JOIN_online_1781116745.json"
    if not rec_path.exists():
        rec_path = next(iter(sorted(RUNS_DIR.glob("WF_TOP10_LOCATIONS_JOIN_online_*.json"))), None)
    if not rec_path or not Path(rec_path).exists():
        print("  [skip] JOIN online recording not found")
        return
    from mini_marie.zaha.twa_city.workflow_mcp import replay_workflow_offline

    tsv = replay_workflow_offline(str(rec_path.resolve()))
    assert tsv.startswith("status\tpass"), tsv.splitlines()[0]
    offline_path = None
    for line in tsv.splitlines():
        if line.startswith("recording_path\t"):
            offline_path = Path(line.split("\t", 1)[1].strip())
            break
    assert offline_path and offline_path.exists()
    size_mb = offline_path.stat().st_size / (1024 * 1024)
    assert size_mb < 5, f"offline recording too large: {size_mb:.1f} MB"
    recorded = load_run(offline_path)
    assert recorded.get("rows_on_disk") == "sidecar_ndjson"
    sidecar = recorded.get("sidecar") or {}
    artifacts = sidecar.get("artifacts") or []
    assert artifacts, "expected NDJSON sidecar artifacts for JOIN offline run"
    loc_art = next((a for a in artifacts if a.get("name") == "location_join_rows"), None)
    assert loc_art, f"missing location_join_rows sidecar: {artifacts}"
    assert 0 < loc_art["row_count"] < 50_000, loc_art["row_count"]
    all_loc = next((a for a in artifacts if a.get("name") == "all_location_rows"), None)
    assert all_loc is None, "full location pool sidecar should not be written"
    pool = (recorded.get("variables") or {}).get("location_join_rows")
    assert isinstance(pool, dict) and pool.get("_row_count") == loc_art["row_count"], pool
    print(
        f"  [pass] JOIN offline json={size_mb:.2f} MB "
        f"location_join_rows={loc_art['row_count']}"
    )


def test_mcp_replay_legacy_recording() -> None:
    """End-to-end MCP helper: legacy recording → offline via id scan."""
    if not LEGACY_RECORDING.exists():
        print("  [skip] legacy recording not found")
        return
    out = replay_workflow_offline(str(LEGACY_RECORDING.resolve()), offline_cap=500_000)
    status = re.search(r"^status\t(\w+)", out, re.M)
    assert status and status.group(1) == "pass"
    m = re.search(r"^1\tsparql_plan\tbuildings_by_usage\tpass\t(\d+)", out, re.M)
    assert m and int(m.group(1)) > 10
    print(f"  [pass] MCP legacy replay rank pool={m.group(1)}")


def test_mcp_online_then_offline_mini() -> None:
    """Ad-hoc workflow file outside catalog: path-based run + embedded replay."""
    with tempfile.TemporaryDirectory() as tmp:
        wf = _mini_kl_workflow("WF_MCP_MINI_CHAIN")
        wf_path = Path(tmp) / "mcp_mini.json"
        wf_path.write_text(json.dumps(wf), encoding="utf-8")

        # Run via engine directly (MCP catalog only lists workflows/ dir)
        online = run_workflow(
            json.loads(wf_path.read_text(encoding="utf-8")),
            mode="online",
            online_limit=5,
            workflow_name=wf_path.stem,
        )
        rec_path = save_run(online, Path(tmp) / "online_rec.json")

        offline = run_workflow(
            json.loads(wf_path.read_text(encoding="utf-8")),
            mode="offline",
            offline_cap=200,
        )
        assert offline["status"] == "pass"
        assert offline["call_trace"][0]["row_count"] > online["call_trace"][0]["row_count"]
        print(
            "  [pass] ad-hoc workflow online="
            f"{online['call_trace'][0]['row_count']} offline="
            f"{offline['call_trace'][0]['row_count']} rec={rec_path.name}"
        )


def run_all() -> None:
    tests = [
        ("Catalog discovery", test_catalog_discovers_all_preset_workflows),
        ("Legacy id scan", test_resolve_legacy_recording_by_workflow_id),
        ("Embedded definition", test_resolve_embedded_definition_only),
        ("Recording workflow_name", test_resolve_recording_workflow_name),
        ("Explicit name override", test_resolve_explicit_name_override),
        ("External workflow path", test_resolve_external_workflow_path),
        ("Unknown id error", test_resolve_unknown_id_raises),
        ("Duplicate id error", test_resolve_duplicate_id_raises),
        ("Online embeds definition", test_online_recording_embeds_definition),
        ("Embedded offline replay", test_replay_from_embedded_recording),
        ("JOIN offline save bounded", test_join_offline_save_is_bounded),
        ("MCP legacy replay", test_mcp_replay_legacy_recording),
        ("Ad-hoc workflow chain", test_mcp_online_then_offline_mini),
    ]
    print("=== Workflow resolution multi-scenario tests ===\n")
    failed = []
    started = time.perf_counter()
    for label, fn in tests:
        print(f">> {label}")
        try:
            fn()
        except Exception as exc:
            failed.append((label, exc))
            print(f"  [FAIL] {exc}")
        print()
    elapsed = round(time.perf_counter() - started, 1)
    if failed:
        print(f"FAILED {len(failed)}/{len(tests)} in {elapsed}s")
        for label, exc in failed:
            print(f"  - {label}: {exc}")
        sys.exit(1)
    print(f"ALL {len(tests)} SCENARIOS PASSED in {elapsed}s")


if __name__ == "__main__":
    run_all()
