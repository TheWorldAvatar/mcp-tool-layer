from __future__ import annotations

import asyncio
import json
import multiprocessing
from pathlib import Path
from types import SimpleNamespace

import pytest

from models import llm_call_telemetry as telemetry


def _events(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _append_worker(path: str, start: int, count: int) -> None:
    for index in range(start, start + count):
        telemetry.append_cost_event(
            {"event": "probe", "call_id": str(index)}, path
        )


@pytest.fixture
def journal(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    path = tmp_path / "costs.jsonl"
    monkeypatch.setenv("TWA_LLM_COST_JOURNAL", str(path))
    return path


def test_default_journal_tracks_isolated_data_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("TWA_LLM_COST_JOURNAL", raising=False)
    monkeypatch.setenv("TWA_AGENTIC_DATA_DIR", str(tmp_path))
    assert telemetry.journal_path() == tmp_path / "reports" / "openrouter_costs.jsonl"


def test_inline_cost_is_recorded_without_generation_lookup(
    monkeypatch: pytest.MonkeyPatch, journal: Path
) -> None:
    monkeypatch.setattr(
        telemetry,
        "_generation_lookup",
        lambda *_args, **_kwargs: pytest.fail("lookup must not run"),
    )
    response = {
        "id": "gen-inline",
        "model": "vendor/model",
        "usage": {
            "prompt_tokens": 10,
            "completion_tokens": 2,
            "total_tokens": 12,
            "cost": 0.0123,
        },
    }
    event = telemetry.record_response(
        response,
        call_id="inline",
        transport="test",
        base_url="https://openrouter.ai/api/v1",
    )
    assert event["actual_cost_usd"] == 0.0123
    assert event["cost_source"] == "response_usage"
    assert event["token_usage"]["total_tokens"] == 12


def test_generation_api_fallback_and_provider(
    monkeypatch: pytest.MonkeyPatch, journal: Path
) -> None:
    monkeypatch.setattr(
        telemetry,
        "_generation_lookup",
        lambda *_args, **_kwargs: {
            "total_cost": 0.2,
            "usage": {"prompt_tokens": 4, "completion_tokens": 3},
            "provider_name": "Provider X",
        },
    )
    event = telemetry.record_response(
        {"id": "gen-fallback"},
        call_id="fallback",
        transport="test",
        base_url="https://openrouter.ai/api/v1",
    )
    assert event["cost_status"] == "resolved"
    assert event["provider"] == "Provider X"
    assert event["token_usage"]["total_tokens"] == 7


def test_process_cost_cap_rejects_the_next_llm_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TWA_LLM_PROCESS_COST_CAP_USD", "10")
    monkeypatch.setattr(telemetry, "_PROCESS_COST_USD", 10.0)

    with pytest.raises(telemetry.LLMCostCapExceeded, match="cap reached"):
        telemetry._enforce_process_cost_cap()


def test_pending_reconcile_appends_immutable_resolution(
    monkeypatch: pytest.MonkeyPatch, journal: Path
) -> None:
    monkeypatch.setattr(telemetry, "_generation_lookup", lambda *_a, **_k: None)
    telemetry.record_response(
        {"id": "gen-pending"},
        call_id="pending",
        transport="test",
        base_url="https://openrouter.ai/api/v1",
    )
    original = journal.read_text(encoding="utf-8")
    monkeypatch.setattr(
        telemetry,
        "_generation_lookup",
        lambda *_a, **_k: {
            "total_cost": "0.7",
            "usage": {"total_tokens": 9},
            "provider_name": "P",
        },
    )
    assert telemetry.reconcile_pending_costs(journal) == {
        "reconciled": 1,
        "still_pending": 0,
    }
    assert journal.read_text(encoding="utf-8").startswith(original)
    assert _events(journal)[-1]["event"] == "cost_resolved"
    assert telemetry.summarize_costs(journal)["actual_cost_usd"] == 0.7


def test_langchain_callback_sync_and_parent_metadata(journal: Path) -> None:
    callback = telemetry.OpenRouterCostCallback(
        model="m", base_url="https://openrouter.ai/api/v1"
    )
    result = SimpleNamespace(
        llm_output={"model_name": "m"},
        generations=[
            [
                SimpleNamespace(
                    message=SimpleNamespace(
                        id="gen-lc",
                        usage_metadata={
                            "input_tokens": 2,
                            "output_tokens": 1,
                            "total_tokens": 3,
                        },
                        response_metadata={
                            "token_usage": {"cost": 0.03},
                            "provider_name": "P",
                        },
                    )
                )
            ]
        ],
    )
    with telemetry.telemetry_context("agent-run", {"component": "BaseAgent"}):
        callback.on_chat_model_start({}, [[]], run_id="run-1")
        callback.on_llm_end(result, run_id="run-1")
    event = _events(journal)[0]
    assert event["parent_call_id"] == "agent-run"
    assert event["context"]["component"] == "BaseAgent"
    summary = telemetry.summarize_costs(journal, "agent-run")
    assert summary["actual_cost_usd"] == 0.03
    assert summary["generation_ids"] == ["gen-lc"]


def test_langchain_callback_async_call_context(journal: Path) -> None:
    callback = telemetry.OpenRouterCostCallback(
        model="m", base_url="https://not-openrouter.test/v1"
    )
    result = SimpleNamespace(
        llm_output={},
        generations=[
            [
                SimpleNamespace(
                    message=SimpleNamespace(
                        id="async-gen",
                        usage_metadata={"total_tokens": 1},
                        response_metadata={},
                    )
                )
            ]
        ],
    )

    async def invoke() -> None:
        with telemetry.telemetry_context("async-parent"):
            callback.on_chat_model_start({}, [[]], run_id="async-run")
            await asyncio.sleep(0)
            callback.on_llm_end(result, run_id="async-run")

    asyncio.run(invoke())
    event = _events(journal)[0]
    assert event["parent_call_id"] == "async-parent"
    assert event["cost_status"] == "unavailable"


class _FakeClient:
    def __init__(self, chat_create, responses_create=None, base_url="https://openrouter.ai/api/v1"):
        self.base_url = base_url
        self.api_key = "secret"
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=chat_create)
        )
        if responses_create is not None:
            self.responses = SimpleNamespace(create=responses_create)


def test_sdk_chat_and_responses_wrapper(journal: Path) -> None:
    seen: dict[str, object] = {}

    def chat_create(**kwargs):
        seen.update(kwargs)
        return {
            "id": "chat-gen",
            "usage": {"total_tokens": 2, "cost": 0.1},
        }

    client = telemetry.instrument_openai_client(
        _FakeClient(
            chat_create,
            lambda **_kwargs: {
                "id": "response-gen",
                "usage": {"total_tokens": 3, "cost": 0.2},
            },
        )
    )
    assert client.chat.completions.create(model="m")["id"] == "chat-gen"
    assert client.responses.create(model="m")["id"] == "response-gen"
    events = _events(journal)
    assert {event["transport"] for event in events} == {
        "openai_sdk_chat_completions",
        "openai_sdk_responses",
    }
    assert seen["extra_body"]["usage"]["include"] is True
    assert telemetry.summarize_costs(journal)["actual_cost_usd"] == 0.3
    assert "secret" not in journal.read_text(encoding="utf-8")


def test_usage_include_is_openrouter_only() -> None:
    openrouter = telemetry.apply_openrouter_usage_include(
        {"temperature": 0},
        base_url="https://openrouter.ai/api/v1",
    )
    local = telemetry.apply_openrouter_usage_include(
        {"temperature": 0},
        base_url="http://localhost:8000/v1",
    )
    assert openrouter["usage"]["include"] is True
    assert "usage" not in local


def test_sdk_async_and_exception_paths(journal: Path) -> None:
    async def async_create(**_kwargs):
        return {"id": "async-sdk", "usage": {"cost": 0.4}}

    async_client = telemetry.instrument_openai_client(_FakeClient(async_create))
    assert asyncio.run(async_client.chat.completions.create(model="m"))["id"] == "async-sdk"

    def fail(**_kwargs):
        raise TimeoutError("late")

    failed_client = telemetry.instrument_openai_client(_FakeClient(fail))
    with pytest.raises(TimeoutError):
        failed_client.chat.completions.create(model="m")
    events = _events(journal)
    assert events[-1]["event"] == "timed_out"
    assert events[-1]["billable"] is False


def test_retries_are_distinct_billable_calls(journal: Path) -> None:
    ids = iter(["retry-1", "retry-2"])
    client = telemetry.instrument_openai_client(
        _FakeClient(
            lambda **_kwargs: {
                "id": next(ids),
                "usage": {"total_tokens": 1, "cost": 0.05},
            }
        )
    )
    client.chat.completions.create(model="m")
    client.chat.completions.create(model="m")
    summary = telemetry.summarize_costs(journal)
    assert summary["billable_calls"] == 2
    assert summary["actual_cost_usd"] == 0.1


def test_parallel_multiprocess_append_is_valid_jsonl(tmp_path: Path) -> None:
    path = tmp_path / "parallel.jsonl"
    processes = [
        multiprocessing.Process(target=_append_worker, args=(str(path), index * 25, 25))
        for index in range(4)
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(10)
        assert process.exitcode == 0
    events = _events(path)
    assert len(events) == 100
    assert len({event["call_id"] for event in events}) == 100


def test_summary_deduplicates_callback_replay(journal: Path) -> None:
    event = {
        "event": "completed",
        "billable": True,
        "call_id": "same",
        "attempt": 1,
        "generation_id": "same-gen",
        "cost_status": "resolved",
        "actual_cost_usd": 1.5,
        "token_usage": {
            "input_tokens": 2,
            "output_tokens": 3,
            "total_tokens": 5,
        },
    }
    telemetry.append_cost_event(event)
    telemetry.append_cost_event(event)
    summary = telemetry.summarize_costs(journal)
    assert summary["billable_calls"] == 1
    assert summary["actual_cost_usd"] == 1.5
    assert summary["total_tokens"] == 5


def test_llm_creator_requests_openrouter_usage_include(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class FakeChatOpenAI:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setenv("REMOTE_BASE_URL", "https://openrouter.ai/api/v1")
    monkeypatch.setenv("REMOTE_API_KEY", "test-key")
    monkeypatch.setattr("models.LLMCreator.ChatOpenAI", FakeChatOpenAI)
    from models.LLMCreator import LLMCreator

    LLMCreator(model="gpt-4o-mini").setup_llm()
    extra_body = captured.get("extra_body")
    assert isinstance(extra_body, dict)
    assert extra_body["usage"]["include"] is True


def test_llm_creator_deepseek_path_disables_thinking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class FakeChatOpenAI:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setenv("REMOTE_BASE_URL", "https://openrouter.ai/api/v1")
    monkeypatch.setenv("REMOTE_API_KEY", "test-key")
    monkeypatch.delenv("TWA_ENABLE_DEEPSEEK_THINKING", raising=False)
    monkeypatch.setenv("TWA_REASONING_EFFORT", "high")
    monkeypatch.setattr("models.LLMCreator.ChatOpenAI", FakeChatOpenAI)
    from models.LLMCreator import LLMCreator

    LLMCreator(model="deepseek/deepseek-v4-flash-0731").setup_llm()
    extra_body = captured.get("extra_body")
    assert isinstance(extra_body, dict)
    assert extra_body["reasoning"]["enabled"] is False
    assert extra_body["thinking"]["type"] == "disabled"
    assert extra_body["provider"]["require_parameters"] is True


def test_non_openrouter_is_unavailable_without_lookup(
    monkeypatch: pytest.MonkeyPatch, journal: Path
) -> None:
    monkeypatch.setattr(
        telemetry,
        "_generation_lookup",
        lambda *_a, **_k: pytest.fail("must not query OpenRouter"),
    )
    event = telemetry.record_response(
        {"id": "local"},
        call_id="local",
        transport="test",
        base_url="http://localhost:8000/v1",
    )
    assert event["cost_status"] == "unavailable"
    assert event["base_url_host"] == "localhost"
