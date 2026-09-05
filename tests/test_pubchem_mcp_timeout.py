from __future__ import annotations

import sys
import threading
from pathlib import Path

import pytest

SERVER_DIR = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "third_party_repos"
    / "PubChem-MCP-Server"
)
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

import pubchem_server as pubchem_mcp  # noqa: E402


def test_call_pubchem_retries_then_succeeds(monkeypatch) -> None:
    monkeypatch.setattr(pubchem_mcp, "_PUBCHEM_TIMEOUT_SECONDS", 1.0)
    calls = {"n": 0}

    def flaky() -> str:
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("transient")
        return "ok"

    assert pubchem_mcp._call_pubchem("probe", flaky) == "ok"
    assert calls["n"] == 3


def test_call_pubchem_times_out_each_attempt(monkeypatch) -> None:
    monkeypatch.setattr(pubchem_mcp, "_PUBCHEM_TIMEOUT_SECONDS", 0.2)
    monkeypatch.setattr(pubchem_mcp.time, "sleep", lambda _seconds: None)
    calls = {"n": 0}

    def hang() -> str:
        calls["n"] += 1
        threading.Event().wait(1)
        return "late"

    with pytest.raises(TimeoutError, match="exceeded 0.2s"):
        pubchem_mcp._call_pubchem("from_cid(91810420)", hang)
    assert calls["n"] == 3


def test_search_by_name_uses_curated_ligand_without_pubchem(monkeypatch) -> None:
    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("curated ligands must not call PubChem")

    monkeypatch.setattr(pubchem_mcp, "_call_pubchem", fail_if_called)
    result = pubchem_mcp.search_by_name("5-butoxyisophthalic acid")
    assert result["canonical_smiles"].startswith("CCCCOc1cc")
    assert result["source"] == "curated-not-in-pubchem"
    assert "cbu_formula" not in result


def test_search_by_cid_returns_error_after_retries(monkeypatch) -> None:
    monkeypatch.setattr(pubchem_mcp, "_PUBCHEM_TIMEOUT_SECONDS", 0.2)
    monkeypatch.setattr(pubchem_mcp.time, "sleep", lambda _seconds: None)

    class FakeCompound:
        @staticmethod
        def from_cid(_cid: int):
            threading.Event().wait(1)
            raise AssertionError("should have been timed out")

    monkeypatch.setattr(pubchem_mcp.pcp, "Compound", FakeCompound)
    result = pubchem_mcp.search_by_cid(91810420)
    assert "error" in result
    assert "exceeded 0.2s" in result["error"]
