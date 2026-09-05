from __future__ import annotations

import json
import threading

import pytest

from src.mcp_servers.enhanced_websearch.operations import (
    docling_fetch,
    pubchem_compact,
    serper_search,
)
from src.mcp_servers.enhanced_websearch.operations.timeout import (
    call_with_retry,
    env_float,
    env_int,
    run_with_timeout,
)


def test_env_helpers_use_defaults_and_minimums(monkeypatch) -> None:
    monkeypatch.delenv("WEBSEARCH_TEST_FLOAT", raising=False)
    monkeypatch.delenv("WEBSEARCH_TEST_INT", raising=False)
    assert env_float("WEBSEARCH_TEST_FLOAT", 12.0) == 12.0
    assert env_int("WEBSEARCH_TEST_INT", 3) == 3
    monkeypatch.setenv("WEBSEARCH_TEST_FLOAT", "0.1")
    monkeypatch.setenv("WEBSEARCH_TEST_INT", "0")
    assert env_float("WEBSEARCH_TEST_FLOAT", 12.0) == 1.0
    assert env_int("WEBSEARCH_TEST_INT", 3) == 1


def test_call_with_retry_then_succeeds(monkeypatch) -> None:
    monkeypatch.setattr(
        "src.mcp_servers.enhanced_websearch.operations.timeout.time.sleep",
        lambda _seconds: None,
    )
    calls = {"n": 0}

    def flaky() -> str:
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("transient")
        return "ok"

    assert call_with_retry("probe", flaky, attempts=3) == "ok"
    assert calls["n"] == 3


def test_run_with_timeout_raises_on_hang() -> None:
    def hang() -> str:
        threading.Event().wait(1)
        return "late"

    with pytest.raises(TimeoutError, match="exceeded 0.2"):
        run_with_timeout(hang, 0.2)


def test_call_with_retry_times_out_each_attempt(monkeypatch) -> None:
    monkeypatch.setattr(
        "src.mcp_servers.enhanced_websearch.operations.timeout.time.sleep",
        lambda _seconds: None,
    )
    calls = {"n": 0}

    def hang() -> str:
        calls["n"] += 1
        threading.Event().wait(1)
        return "late"

    with pytest.raises(TimeoutError, match="exceeded 0.2"):
        call_with_retry("docling", hang, timeout_seconds=0.2, attempts=3)
    assert calls["n"] == 3


def test_url_to_markdown_surfaces_retry_timeout(monkeypatch) -> None:
    import sys
    import types

    monkeypatch.setattr(docling_fetch, "_timeout_seconds", lambda: 25.0)
    monkeypatch.setattr(docling_fetch, "_attempts", lambda: 3)

    def fail(*_args, **_kwargs):
        raise TimeoutError("request exceeded 25 seconds")

    monkeypatch.setattr(docling_fetch, "call_with_retry", fail)
    fake_dc = types.SimpleNamespace(DocumentConverter=object)
    monkeypatch.setitem(sys.modules, "docling", types.ModuleType("docling"))
    monkeypatch.setitem(sys.modules, "docling.document_converter", fake_dc)

    result = docling_fetch.url_to_markdown("https://example.com/ligand")
    assert result.startswith("Error fetching the URL:")
    assert "exceeded 25" in result


def test_single_search_retries_then_errors(monkeypatch) -> None:
    monkeypatch.setattr(serper_search, "_timeout_seconds", lambda: 0.2)
    monkeypatch.setattr(serper_search, "_attempts", lambda: 3)
    monkeypatch.setattr(
        "src.mcp_servers.enhanced_websearch.operations.timeout.time.sleep",
        lambda _seconds: None,
    )
    calls = {"n": 0}

    class FakeConn:
        def __init__(self, *_args, **_kwargs) -> None:
            calls["n"] += 1

        def request(self, *_args, **_kwargs) -> None:
            threading.Event().wait(1)

        def getresponse(self):
            raise AssertionError("should have been timed out")

        def close(self) -> None:
            return None

    monkeypatch.setattr(serper_search.http.client, "HTTPSConnection", FakeConn)
    payload = json.loads(serper_search._single_search("btc ligand", "key", 1))
    assert "error" in payload
    assert "exceeded 0.2" in payload["error"]
    assert calls["n"] == 3


def test_pubchem_compact_retries_server_errors(monkeypatch) -> None:
    monkeypatch.setattr(pubchem_compact, "_ATTEMPTS", 3)
    monkeypatch.setattr(
        "src.mcp_servers.enhanced_websearch.operations.timeout.time.sleep",
        lambda _seconds: None,
    )
    calls = {"n": 0}

    class FakeResponse:
        status_code = 503

        def json(self):
            raise AssertionError("5xx should retry, not parse")

    def fake_get(_url: str, timeout: float):
        calls["n"] += 1
        return FakeResponse()

    monkeypatch.setattr(pubchem_compact._SESSION, "get", fake_get)
    assert pubchem_compact._get_json("https://example.test/cid") is None
    assert calls["n"] == 3


def test_pubchem_compact_does_not_retry_not_found(monkeypatch) -> None:
    monkeypatch.setattr(pubchem_compact, "_ATTEMPTS", 3)
    calls = {"n": 0}

    class FakeResponse:
        status_code = 404

        def json(self):
            raise AssertionError("404 should not parse JSON")

    def fake_get(_url: str, timeout: float):
        calls["n"] += 1
        return FakeResponse()

    monkeypatch.setattr(pubchem_compact._SESSION, "get", fake_get)
    assert pubchem_compact._get_json("https://example.test/missing") is None
    assert calls["n"] == 1
