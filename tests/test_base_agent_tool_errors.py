from __future__ import annotations

import asyncio
import json
from builtins import BaseExceptionGroup
from types import SimpleNamespace

from models.BaseAgent import (
    _call_required_mcp_tool,
    exception_details,
    _flatten_exception_group,
    _mcp_result_content,
    _tool_error_text,
)


def test_exception_group_is_flattened_for_actionable_logging() -> None:
    grouped = BaseExceptionGroup(
        "tool calls",
        [
            FileNotFoundError("missing prompt"),
            BaseExceptionGroup("nested", [ValueError("bad patch")]),
        ],
    )
    leaves = _flatten_exception_group(grouped)
    assert [type(item).__name__ for item in leaves] == [
        "FileNotFoundError",
        "ValueError",
    ]
    assert exception_details(grouped) == [
        {"type": "FileNotFoundError", "message": "missing prompt"},
        {"type": "ValueError", "message": "bad patch"},
    ]


def test_tool_error_handler_returns_recoverable_json() -> None:
    payload = json.loads(_tool_error_text(FileNotFoundError("missing prompt")))
    assert payload["ok"] is False
    assert payload["errors"] == [
        {"type": "FileNotFoundError", "message": "missing prompt"}
    ]
    assert "retry" in payload["instruction"].lower()


def test_direct_mcp_result_preserves_nested_export_payload() -> None:
    class Result:
        structuredContent = {
            "result": json.dumps(
                {
                    "status": "ok",
                    "ttl": "@prefix ex: <https://example.test/> .",
                }
            )
        }

    content, structured = _mcp_result_content(Result())

    assert json.loads(content)["status"] == "ok"
    assert structured["ttl"].startswith("@prefix")


class _FakeSession:
    def __init__(self, results: list[object]) -> None:
        self.results = list(results)
        self.calls: list[tuple[str, dict]] = []

    async def call_tool(self, name: str, arguments: dict) -> object:
        self.calls.append((name, arguments))
        return self.results.pop(0)


def _structured_result(payload: dict, *, is_error: bool = False) -> object:
    return SimpleNamespace(
        structuredContent={"result": json.dumps(payload)},
        content=[],
        isError=is_error,
    )


def test_required_lifecycle_calls_reuse_fake_session_and_preserve_arguments() -> None:
    session = _FakeSession(
        [
            _structured_result({"status": "ok", "scope": "Entity A"}),
            _structured_result(
                {
                    "status": "ok",
                    "ttl": "@prefix ex: <https://example.test/> .",
                }
            ),
        ]
    )

    async def exercise() -> tuple[dict, dict]:
        initial = await _call_required_mcp_tool(
            session,
            tool_name="init_memory",
            arguments={"doi": "abc123", "top_level_entity_name": "Entity A"},
            phase="initial",
        )
        final = await _call_required_mcp_tool(
            session,
            tool_name="export_memory",
            phase="final",
        )
        return initial, final

    initial, final = asyncio.run(exercise())

    assert session.calls == [
        (
            "init_memory",
            {"doi": "abc123", "top_level_entity_name": "Entity A"},
        ),
        ("export_memory", {}),
    ]
    assert initial["phase"] == "initial"
    assert initial["script_fallback"] is False
    assert final["phase"] == "final"
    assert final["script_fallback"] is True
    assert final["structured_content"]["ttl"].startswith("@prefix")


def test_required_initial_tool_rejects_structured_failure() -> None:
    session = _FakeSession(
        [_structured_result({"status": "rejected", "code": "invalid_scope"})]
    )

    async def exercise() -> None:
        await _call_required_mcp_tool(
            session,
            tool_name="init_memory",
            arguments={"doi": "abc123", "top_level_entity_name": ""},
            phase="initial",
        )

    try:
        asyncio.run(exercise())
    except RuntimeError as exc:
        assert "Required initial MCP tool `init_memory` was rejected" in str(exc)
        assert "invalid_scope" in str(exc)
    else:
        raise AssertionError("structured init_memory rejection was accepted")


def test_required_tool_rejects_ok_false_even_without_transport_error() -> None:
    session = _FakeSession([_structured_result({"ok": False, "error": "bad doi"})])

    async def exercise() -> None:
        await _call_required_mcp_tool(
            session,
            tool_name="init_memory",
            arguments={"doi": ""},
            phase="initial",
        )

    try:
        asyncio.run(exercise())
    except RuntimeError as exc:
        assert "bad doi" in str(exc)
    else:
        raise AssertionError("ok=false init_memory result was accepted")
