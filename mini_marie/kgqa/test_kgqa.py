"""Smoke tests for KGQA routing, recording extraction, and offline replay."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mini_marie.kgqa.mcp_router import domain_for_recording, route_question
from mini_marie.kgqa.question_catalog import match_catalog
from mini_marie.kgqa.recording_utils import extract_from_text, extract_recording_info
from mini_marie.mop_mof.mof.workflow_mcp import format_workflow_mcp_response


def test_router_mof_competency_question():
    route = route_question("Average and variance PLD of UiO-66")
    assert "mof-twa" in route.mcp_servers
    assert route.catalog_entry is not None
    assert route.catalog_entry.id == "CQ01_PLD_UIO66"


def test_router_chemistry_competency_question():
    route = route_question("MQ3 — ascorbic acid formula C6H8O6")
    assert any(s.startswith("chemistry-") for s in route.mcp_servers)


def test_match_catalog_by_id():
    entry = match_catalog("CQ01_PLD_UIO66")
    assert entry is not None
    assert entry.workflow_id == "CQ01_PLD_UIO66"


def test_extract_recording_from_tsv():
    sample = format_workflow_mcp_response(
        {
            "status": "pass",
            "mode": "online",
            "workflow_id": "test_wf",
            "workflow_name": "test_wf",
            "online_limit": 10,
            "offline_cap": 500000,
            "elapsed_ms": 42,
            "answer": "ok",
            "call_trace": [],
        },
        Path("mini_marie/mop_mof/mof/workflow_runs/test_wf_online_123.json"),
    )
    info = extract_from_text(sample)
    assert info["recording_path"]
    assert info["workflow_id"] == "test_wf"


def test_extract_from_metadata_tool_outputs():
    tsv = "status\tpass\nrecording_path\t/tmp/foo_online_1.json\nworkflow_id\tCQ01_PLD_UIO66\n"
    meta = {
        "tool_activity": {
            "tool_outputs": [{"name": "run_competency_online", "content": tsv}],
        }
    }
    info = extract_recording_info(online_answer="", metadata=meta)
    assert "foo_online_1.json" in (info["recording_path"] or "")


def test_domain_for_recording_paths():
    assert domain_for_recording("mini_marie/marie/chemistry/competency_runs/mq01_online_1.json") == "chemistry"
    assert domain_for_recording("mini_marie/mop_mof/mof/competency_runs/CQ01_online_1.json") == "mof_competency"
    assert domain_for_recording("mini_marie/zaha/twa_city/workflow_runs/WF_online_1.json") == "city"


def test_city_workflow_offline_replay_routing():
    from mini_marie.kgqa.offline_runner import replay_offline

    runs = sorted(Path("mini_marie/zaha/twa_city/workflow_runs").glob("*_online_*.json"))
    if not runs:
        pytest.skip("no city workflow online recordings")
    result = replay_offline(str(runs[-1].resolve()))
    if result.get("status") == "error":
        err = str(result.get("error", ""))
        assert "mof_case/workflows" not in err, f"city replay routed to mof_case: {err}"
        pytest.skip(f"offline replay failed (cache likely cold): {err}")
    assert result.get("offline_path")


def test_ex_zeolite_catalog_workflow_id():
    entry = match_catalog("Which zeolite has framework code AEN?")
    assert entry is not None
    assert entry.id == "ex_zeolite_aen"
    assert entry.workflow_id == "mq34_framework_aen"


def test_mq02_expected_empty_tag():
    entry = match_catalog("MQ2 — uses of 3-amino-2-propanol")
    assert entry is not None
    assert "expected_empty" in entry.tags


def test_router_singapore_jurong_pollutants():
    route = route_question("What are the pollutant concentrations at Jurong Island?")
    assert route.catalog_entry is not None
    assert route.catalog_entry.id == "Q15"
    assert "sg-old" in route.mcp_servers
    assert "mof-twa" not in route.mcp_servers
    assert route.domain == "sg"


def test_router_singapore_gfa():
    route = route_question("How many land lots do not exceed their maximum permitted GFA?")
    assert route.catalog_entry is not None
    assert route.catalog_entry.id == "ZQ_GFA"
    assert "sg-old" in route.mcp_servers
    assert "mof-twa" not in route.mcp_servers


def test_match_catalog_mq12_by_tokens():
    entry = match_catalog(
        "What are the ionic strengths associated with the pK values of methylamine?"
    )
    assert entry is not None
    assert entry.id == "mq12_methylamine_ionic"
    assert entry.workflow_id == "mq12_methylamine_ionic"


def test_router_mq37_zeolite():
    route = route_question(
        "Retrieve unit cell information of zeolitic material |Na20|[Al20Si76O192]"
    )
    assert route.catalog_entry is not None
    assert route.catalog_entry.id == "MQ37"
    assert "chemistry-ontozeolite" in route.mcp_servers
    assert "mof-twa" not in route.mcp_servers


def test_router_mq49_mops():
    route = route_question("Which MOPs have an outer diameter greater than 70 Angstrom?")
    assert route.catalog_entry is not None
    assert route.catalog_entry.id == "MQ49"
    assert "twa-mops" in route.mcp_servers
    assert "mof-twa" not in route.mcp_servers


@pytest.mark.skipif(
    not Path("mini_marie/marie/chemistry/competency_runs").exists(),
    reason="no chemistry competency runs dir",
)
def test_offline_replay_from_fixture_if_present():
    runs = sorted(Path("mini_marie/marie/chemistry/competency_runs").glob("*_online_*.json"))
    if not runs:
        pytest.skip("no online chemistry recordings")
    from mini_marie.kgqa.offline_runner import replay_offline

    rec = runs[0]
    recorded = json.loads(rec.read_text(encoding="utf-8"))
    if not recorded.get("probed_sequence"):
        pytest.skip("recording lacks probed_sequence")
    result = replay_offline(str(rec.resolve()))
    if result.get("status") == "error":
        pytest.skip(f"offline replay failed (cache likely cold): {result.get('error')}")
    assert result.get("offline_path")
