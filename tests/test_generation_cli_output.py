"""Tests for generation CLI report output."""

from __future__ import annotations

from src.agents.scripts_and_prompts_generation.agentic_generation_main import (
    _print_utf8,
)


def test_print_utf8_handles_unicode(capsys) -> None:
    _print_utf8('{"unit":"μL"}')
    assert "μL" in capsys.readouterr().out
