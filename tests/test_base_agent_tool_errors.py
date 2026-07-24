from __future__ import annotations

import json
from builtins import BaseExceptionGroup

from models.BaseAgent import _flatten_exception_group, _tool_error_text


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


def test_tool_error_handler_returns_recoverable_json() -> None:
    payload = json.loads(_tool_error_text(FileNotFoundError("missing prompt")))
    assert payload["ok"] is False
    assert payload["errors"] == [
        {"type": "FileNotFoundError", "message": "missing prompt"}
    ]
    assert "retry" in payload["instruction"].lower()
