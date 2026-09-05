from __future__ import annotations

from src.agents.scripts_and_prompts_generation.fixed_rdf_runtime import (
    _TOOL_TEXT_MAX_CHARS,
    sanitize_tool_text,
    wrap_public_tool,
)


def test_ordinary_strings_pass_through() -> None:
    text = "room temperature; 2 h"
    assert sanitize_tool_text(text) == text


def test_historical_max_surviving_arg_is_kept() -> None:
    text = "x" * _TOOL_TEXT_MAX_CHARS
    assert sanitize_tool_text(text) == text
    assert _TOOL_TEXT_MAX_CHARS == 46009


def test_overlong_strings_are_cut_to_historical_max() -> None:
    text = "x" * (_TOOL_TEXT_MAX_CHARS + 80)
    cleaned = sanitize_tool_text(text)
    assert cleaned == "x" * _TOOL_TEXT_MAX_CHARS
    assert len(cleaned) == _TOOL_TEXT_MAX_CHARS


def test_explicit_max_override_still_cuts() -> None:
    text = "x" * 900
    cleaned = sanitize_tool_text(text, max_chars=800)
    assert cleaned == "x" * 800


def test_iris_are_not_truncated() -> None:
    iri = "https://www.theworldavatar.com/kg/instance/generated/" + ("A" * 9000)
    assert sanitize_tool_text(iri) == iri


def test_wrap_skips_identity_params_and_caps_other_strings() -> None:
    seen: dict[str, str] = {}

    def create_item(label: str, parent_iri: str, note: str | None = None) -> str:
        seen["label"] = label
        seen["parent_iri"] = parent_iri
        seen["note"] = note or ""
        return "ok"

    wrapped = wrap_public_tool(create_item)
    long_note = "n" * (_TOOL_TEXT_MAX_CHARS + 200)
    long_iri = "https://example.invalid/" + ("B" * 600)
    wrapped("sample", long_iri, note=long_note)
    assert seen["parent_iri"] == long_iri
    assert seen["note"] == "n" * _TOOL_TEXT_MAX_CHARS
    assert seen["label"] == "sample"
