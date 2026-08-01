"""Regression tests for provider-owned LLM context and output limits."""

from __future__ import annotations

from types import SimpleNamespace

from src.agents.scripts_and_prompts_generation import level1_code_repair


def test_invoke_json_does_not_set_token_budget(monkeypatch) -> None:
    captured = {}

    class FakeCreator:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def setup_llm(self):
            return SimpleNamespace(
                invoke=lambda _prompt: SimpleNamespace(
                    content='{"ok":true}',
                    response_metadata={},
                    usage_metadata={},
                )
            )

    monkeypatch.setattr(level1_code_repair, "LLMCreator", FakeCreator)

    result = level1_code_repair.invoke_json(
        "gpt-5",
        "Return JSON.",
        max_attempts=1,
        provider_max_retries=0,
    )

    assert result.data == {"ok": True}
    assert captured["model_config"].max_tokens is None
    assert "max_tokens" not in captured["model_config"].get_config("gpt-5")
