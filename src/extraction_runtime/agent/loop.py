"""ReAct message helpers and tool-activity summaries."""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage

from src.extraction_runtime.agent.react_hooks import (
    project_react_history_to_receipts,
    stop_repeated_committed_output_calls,
    summarize_react_tool_activity,
)

__all__ = [
    "best_text_and_meta_from_react_messages",
    "is_trivial_agent_reply",
    "normalize_ai_message_content",
    "project_react_history_to_receipts",
    "required_final_call_satisfied",
    "stop_repeated_committed_output_calls",
    "summarize_react_tool_activity",
]


def normalize_ai_message_content(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        if "text" in content:
            return str(content["text"])
        return ""
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and "text" in block:
                parts.append(str(block["text"]))
            else:
                parts.append(str(block))
        return "".join(parts)
    return str(content)


def is_trivial_agent_reply(text: str) -> bool:
    token = (text or "").strip()
    if not token or token in ("{}", "[]"):
        return True
    return len(token) <= 3 and token.lower().rstrip(".") in {"ok", "yes", "no"}


def best_text_and_meta_from_react_messages(
    messages: list[Any],
) -> tuple[str, dict[str, Any]]:
    if not messages:
        return "", {}
    ai_messages = [item for item in messages if isinstance(item, AIMessage)]
    if not ai_messages:
        last = messages[-1]
        meta = getattr(last, "response_metadata", {}) or {}
        return normalize_ai_message_content(getattr(last, "content", None)), meta
    for message in reversed(ai_messages):
        text = normalize_ai_message_content(message.content)
        if text.strip() and not is_trivial_agent_reply(text):
            return text, getattr(message, "response_metadata", {}) or {}
    last_ai = ai_messages[-1]
    return (
        normalize_ai_message_content(last_ai.content),
        getattr(last_ai, "response_metadata", {}) or {},
    )


def required_final_call_satisfied(
    tool_activity: dict[str, Any],
    *,
    tool_name: str,
    required_arguments: dict[str, Any] | None,
) -> bool:
    outputs = list(tool_activity.get("tool_outputs") or [])
    if not outputs:
        return False
    final_output = outputs[-1] or {}
    if str(final_output.get("name") or "").strip() != tool_name:
        return False
    if required_arguments is None:
        return True
    final_call_id = str(final_output.get("tool_call_id") or "")
    if not final_call_id:
        return False
    for planned_call in reversed(tool_activity.get("planned_tool_calls") or []):
        if str((planned_call or {}).get("id") or "") != final_call_id:
            continue
        return (planned_call or {}).get("args") == required_arguments
    return False
