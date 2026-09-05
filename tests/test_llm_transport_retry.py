import asyncio

import pytest

from src.pipelines.utils.llm_transport_retry import (
    is_llm_transport_error,
    retry_async_on_transport,
    transport_retry_wait_seconds,
)


def test_detects_openrouter_504_abort() -> None:
    exc = ValueError("{'message': 'The operation was aborted', 'code': 504}")
    assert is_llm_transport_error(exc) is True


def test_detects_rate_limit_and_timeout() -> None:
    assert is_llm_transport_error(TimeoutError("read timeout")) is True
    assert is_llm_transport_error(ConnectionError("connection reset")) is True
    assert is_llm_transport_error(RuntimeError("429 Too Many Requests")) is True


def test_detects_exception_group_leaf() -> None:
    leaf = ValueError("{'message': 'The operation was aborted', 'code': 504}")
    grouped = ExceptionGroup("unhandled errors in a TaskGroup", [leaf])
    assert is_llm_transport_error(grouped) is True


def test_semantic_errors_are_not_transport() -> None:
    assert is_llm_transport_error(ValueError("required MCP tool missing")) is False
    assert is_llm_transport_error(
        RuntimeError("KG agent failed structured tool/artifact validation")
    ) is False


def test_backoff_grows_without_jitter() -> None:
    first = transport_retry_wait_seconds(0, jitter=False)
    second = transport_retry_wait_seconds(1, jitter=False)
    third = transport_retry_wait_seconds(2, jitter=False)
    assert first == 5.0
    assert second == 10.0
    assert third == 20.0


def test_transport_retry_does_not_raise_until_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TWA_LLM_TRANSPORT_MAX_RETRIES", "3")
    monkeypatch.setenv("TWA_LLM_TRANSPORT_BASE_WAIT", "0")
    calls = {"n": 0}

    async def flaky() -> str:
        calls["n"] += 1
        if calls["n"] < 3:
            raise ValueError("{'message': 'The operation was aborted', 'code': 504}")
        return "ok"

    assert asyncio.run(retry_async_on_transport(flaky, what="test")) == "ok"
    assert calls["n"] == 3


def test_non_transport_raises_immediately(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TWA_LLM_TRANSPORT_MAX_RETRIES", "5")
    calls = {"n": 0}

    async def semantic() -> str:
        calls["n"] += 1
        raise ValueError("required MCP tool missing")

    with pytest.raises(ValueError, match="required MCP tool missing"):
        asyncio.run(retry_async_on_transport(semantic, what="test"))
    assert calls["n"] == 1


def test_restore_runs_before_transport_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TWA_LLM_TRANSPORT_MAX_RETRIES", "2")
    monkeypatch.setenv("TWA_LLM_TRANSPORT_BASE_WAIT", "0")
    restored = {"n": 0}
    calls = {"n": 0}

    async def flaky() -> str:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("504 gateway timeout")
        return "ok"

    def restore() -> None:
        restored["n"] += 1

    assert asyncio.run(
        retry_async_on_transport(flaky, restore=restore, what="test")
    ) == "ok"
    assert restored["n"] == 1
