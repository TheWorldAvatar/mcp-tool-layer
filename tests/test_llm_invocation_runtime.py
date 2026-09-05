from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.agents.scripts_and_prompts_generation import level1_code_repair
from src.agents.scripts_and_prompts_generation.llm_invocation_runtime import (
    LLMInvocationTimeout,
    append_invocation_event,
    configure_llm_invocation_journal,
    invoke_with_hard_timeout,
    recover_incomplete_invocations,
)


def _events(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_hard_timeout_returns_control_when_callback_hangs() -> None:
    started = time.perf_counter()

    with pytest.raises(LLMInvocationTimeout):
        invoke_with_hard_timeout(
            lambda: time.sleep(0.2),
            timeout_seconds=0.01,
        )

    assert time.perf_counter() - started < 0.15


def test_journal_recovers_call_without_terminal_event(tmp_path: Path) -> None:
    path = configure_llm_invocation_journal(tmp_path)
    append_invocation_event(
        {"call_id": "unfinished", "event": "started", "attempt": 1}
    )
    append_invocation_event(
        {"call_id": "finished", "event": "started", "attempt": 1}
    )
    append_invocation_event(
        {"call_id": "finished", "event": "completed", "attempt": 1}
    )

    assert recover_incomplete_invocations(path) == ["unfinished"]
    recovered = _events(path)[-1]
    assert recovered["call_id"] == "unfinished"
    assert recovered["event"] == "interrupted"


def test_invoke_json_records_completed_call(monkeypatch, tmp_path: Path) -> None:
    path = configure_llm_invocation_journal(tmp_path)

    class FakeCreator:
        def __init__(self, **_kwargs):
            pass

        def setup_llm(self):
            return SimpleNamespace(
                invoke=lambda _prompt: SimpleNamespace(
                    content='{"ok": true}',
                    usage_metadata={"total_tokens": 3},
                )
            )

    monkeypatch.setattr(level1_code_repair, "LLMCreator", FakeCreator)
    result = level1_code_repair.invoke_json(
        "fake-model", "secret prompt", timeout_seconds=1, max_attempts=1
    )

    assert result.data == {"ok": True}
    assert result.actual_cost_usd is None
    assert result.generation_ids == []
    events = _events(path)
    assert [event["event"] for event in events] == ["started", "completed"]
    assert events[0]["prompt_sha256"]
    assert events[1]["cost_journal"]
    assert "secret prompt" not in path.read_text(encoding="utf-8")


def test_invoke_json_records_hard_timeout(monkeypatch, tmp_path: Path) -> None:
    path = configure_llm_invocation_journal(tmp_path)

    class SlowCreator:
        def __init__(self, **_kwargs):
            pass

        def setup_llm(self):
            return SimpleNamespace(invoke=lambda _prompt: time.sleep(0.2))

    monkeypatch.setattr(level1_code_repair, "LLMCreator", SlowCreator)
    with pytest.raises(LLMInvocationTimeout):
        level1_code_repair.invoke_json(
            "slow-model",
            "prompt",
            timeout_seconds=0.01,
            max_attempts=1,
        )

    assert [event["event"] for event in _events(path)] == [
        "started",
        "timed_out",
    ]
