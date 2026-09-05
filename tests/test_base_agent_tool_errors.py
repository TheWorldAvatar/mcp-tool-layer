from __future__ import annotations

import asyncio
import json
from builtins import BaseExceptionGroup
from types import SimpleNamespace

from models.BaseAgent import (
    _call_required_mcp_tool,
    exception_details,
    _flatten_exception_group,
    _merge_mcp_server_environment,
    _mcp_result_content,
    _required_final_call_satisfied,
    _tool_error_text,
)


def test_pipeline_runtime_env_overrides_stale_mcp_config() -> None:
    merged = _merge_mcp_server_environment(
        {
            "TWA_AGENTIC_DATA_DIR": "stale/runtime",
            "PYTHONIOENCODING": "utf-8",
        },
        {
            "TWA_AGENTIC_DATA_DIR": "active/runtime",
            "PATH": "inherited-path",
        },
    )

    assert merged["TWA_AGENTIC_DATA_DIR"] == "active/runtime"
    assert merged["PYTHONIOENCODING"] == "utf-8"
    assert merged["PATH"] == "inherited-path"


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


def test_required_final_call_requires_exact_canonical_arguments() -> None:
    canonical = {
        "doi": "abc123",
        "top_level_entity_name": "Structural transformation from A to B",
    }
    activity = {
        "planned_tool_calls": [
            {
                "id": "call-1",
                "name": "export_memory",
                "args": {
                    "doi": "abc123",
                    "top_level_entity_name": "B",
                },
            }
        ],
        "tool_outputs": [
            {
                "tool_call_id": "call-1",
                "name": "export_memory",
            }
        ],
    }

    assert not _required_final_call_satisfied(
        activity,
        tool_name="export_memory",
        required_arguments=canonical,
    )

    activity["planned_tool_calls"][0]["args"] = dict(canonical)
    assert _required_final_call_satisfied(
        activity,
        tool_name="export_memory",
        required_arguments=canonical,
    )


def test_required_final_call_without_arguments_keeps_name_only_contract() -> None:
    activity = {
        "planned_tool_calls": [],
        "tool_outputs": [
            {
                "tool_call_id": "call-1",
                "name": "export_memory",
            }
        ],
    }

    assert _required_final_call_satisfied(
        activity,
        tool_name="export_memory",
        required_arguments=None,
    )


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


def test_required_tool_unwraps_taskgroup_failure() -> None:
    class _ExplodingSession:
        async def call_tool(self, name: str, arguments: dict) -> object:
            raise BaseExceptionGroup(
                "unhandled errors in a TaskGroup (1 sub-exception)",
                [RuntimeError("BOUND_ROOT_NOT_MATERIALIZED")],
            )

    async def exercise() -> None:
        await _call_required_mcp_tool(
            _ExplodingSession(),
            tool_name="export_memory",
            arguments={"doi": "abc123", "top_level_entity_name": "top"},
            phase="final",
        )

    try:
        asyncio.run(exercise())
    except RuntimeError as exc:
        assert "Required final MCP tool `export_memory` failed" in str(exc)
        assert "BOUND_ROOT_NOT_MATERIALIZED" in str(exc)
        assert "TaskGroup" not in str(exc)
    else:
        raise AssertionError("TaskGroup export_memory failure was not unwrapped")


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
