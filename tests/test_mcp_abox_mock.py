from __future__ import annotations

import os
from pathlib import Path

import pytest

from src.kg_building_mcp_generation.abox_mock.harness import (
    run_covering_abox_mock,
    run_covering_abox_mock_on_package,
    run_invalid_identity_abox_mock,
)
from test_kg_building_mcp_generation import _fixture

FROZEN_PACKS = Path(
    os.environ.get("TWA_ABOX_MOCK_PACKS")
    or r"C:\Users\xz378\Documents\GitHub\MCP-enhanced-MOPs-Extraction_Reproduction"
    r"\experiments\s1_om2_quantity_overlay\packs"
)
FROZEN_SHARDS = ("s1", "s2", "s3", "s4")
FROZEN_ONTOLOGIES = ("ontosynthesis", "ontomops", "ontospecies", "medical")


def test_covering_abox_mock_matches_expected_facts(tmp_path) -> None:
    parsed, contract = _fixture()
    result = run_covering_abox_mock(
        parsed=parsed,
        contract=contract,
        ontology_name="example",
        work_dir=tmp_path,
    )
    assert result["compiled"]["errors"] == []
    assert result["executed"]["ok"] is True
    assert result["missing"] == []
    assert result["extra"] == []
    assert result["ok"] is True
    created = {
        item["name"]
        for item in result["executed"]["receipts"]
        if item["kind"] == "create"
    }
    public = {item["name"] for item in result["compiled"]["public_tools"]}
    assert created == public
    export = result["executed"]["export"] or {}
    assert str(export.get("status") or "").lower() == "ok"
    assert int(export.get("triple_count") or 0) > 0


def test_invalid_identity_abox_mock_is_rejected(tmp_path) -> None:
    parsed, contract = _fixture()
    result = run_invalid_identity_abox_mock(
        parsed=parsed,
        contract=contract,
        ontology_name="example",
        work_dir=tmp_path,
    )
    assert result["rejected"]
    assert result["ok"] is True
    first = result["rejected"][0]["result"]
    assert str(first.get("status") or "").lower() != "ok"


def _pack_failure(result: dict) -> str:
    receipts = result.get("executed", {}).get("receipts") or []
    failed = [
        item
        for item in receipts
        if str((item.get("result") or {}).get("status") or "").lower() != "ok"
    ]
    export = result.get("executed", {}).get("export") or {}
    parts = [
        f"ok={result.get('ok')}",
        f"executed_ok={result.get('executed', {}).get('ok')}",
        f"export={export.get('status')}",
        f"missing={len(result.get('missing') or [])}",
        f"extra={len(result.get('extra') or [])}",
    ]
    if failed:
        first = failed[0]
        payload = first.get("result") or {}
        parts.append(
            f"first_failure={first.get('name')} {payload.get('code')} {payload.get('message')}"
        )
    if result.get("missing"):
        parts.append(f"missing_sample={result['missing'][:3]!r}")
    if result.get("extra"):
        parts.append(f"extra_sample={result['extra'][:3]!r}")
    return "; ".join(parts)


@pytest.mark.parametrize("ontology", FROZEN_ONTOLOGIES)
@pytest.mark.parametrize("shard", FROZEN_SHARDS)
def test_covering_abox_mock_frozen_s1_s4_packs(
    tmp_path, shard: str, ontology: str
) -> None:
    pack = FROZEN_PACKS / shard
    main_py = pack / "scripts" / ontology / "main.py"
    if not main_py.is_file():
        pytest.skip(f"frozen pack missing: {main_py}")
    result = run_covering_abox_mock_on_package(
        pack_root=pack,
        ontology_name=ontology,
        work_dir=tmp_path,
        root_iri=f"https://example.test/abox-mock/{shard}/{ontology}/root",
        doi=f"abox-mock-{shard}-{ontology}",
    )
    created = {
        item["name"]
        for item in result["executed"]["receipts"]
        if item["kind"] == "create"
    }
    public = {item["name"] for item in result["compiled"]["public_tools"]}
    assert result["compiled"].get("errors") in ([], None)
    assert public, "compiled surface has no public create_* tools"
    assert result["ok"] is True, _pack_failure(result)
    assert result["executed"]["ok"] is True, _pack_failure(result)
    assert result["missing"] == []
    assert result["extra"] == []
    assert created == public
    export = result["executed"]["export"] or {}
    assert str(export.get("status") or "").lower() == "ok"
    assert int(export.get("triple_count") or 0) > 0
