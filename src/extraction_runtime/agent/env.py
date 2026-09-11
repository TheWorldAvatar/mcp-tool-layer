"""MCP process environment and required-tool session helpers."""

from __future__ import annotations

import json
import os
from typing import Any

from src.kg_building.experiment_protocol import PROTOCOL_ENV

_PIPELINE_OWNED_MCP_ENV = {
    "TWA_AGENTIC_DATA_DIR",
    "TWA_CENTRAL_MEMORY_DIR",
    "TWA_GENERATED_ARTIFACT_ROOT",
    "TWA_MAIN_ONTOLOGY_NAME",
    "TWA_LLM_SEED",
    "TWA_MCP_TOOL_DESCRIPTIONS_ENABLED",
    "TWA_SEMANTIC_OPERATION_SURFACE",
}


def apply_kg_protocol_environment(*, force: bool = False) -> None:
    """Pin official no-contract KG process env. Seed is always 42."""
    del force
    os.environ["TWA_LLM_SEED"] = PROTOCOL_ENV["TWA_LLM_SEED"]
    os.environ["TWA_MCP_TOOL_DESCRIPTIONS_ENABLED"] = PROTOCOL_ENV[
        "TWA_MCP_TOOL_DESCRIPTIONS_ENABLED"
    ]
    os.environ["TWA_SEMANTIC_OPERATION_SURFACE"] = PROTOCOL_ENV[
        "TWA_SEMANTIC_OPERATION_SURFACE"
    ]


def merge_mcp_server_environment(
    configured_env: dict[str, Any],
    inherited_env: dict[str, Any],
) -> dict[str, Any]:
    """Merge MCP env while preserving pipeline-owned runtime locations."""
    merged = dict(inherited_env)
    for key, value in configured_env.items():
        if key in _PIPELINE_OWNED_MCP_ENV and key in inherited_env:
            continue
        text = str(value)
        if text.startswith("${") and text.endswith("}") and len(text) > 3:
            env_key = text[2:-1]
            if env_key in inherited_env:
                merged[key] = inherited_env[env_key]
            continue
        merged[key] = value
    return merged


def flatten_exception_group(exc: BaseException) -> list[BaseException]:
    children = getattr(exc, "exceptions", None)
    if not children:
        return [exc]
    leaves: list[BaseException] = []
    for child in children:
        leaves.extend(flatten_exception_group(child))
    return leaves


def exception_details(exc: BaseException) -> list[dict[str, str]]:
    return [
        {"type": type(item).__name__, "message": str(item)}
        for item in flatten_exception_group(exc)
    ]


def compose_user_message_with_mcp_instruction(
    *,
    task_instruction: str,
    mcp_instruction: str,
    task_continuation: str = "",
) -> str:
    return f"{task_instruction}{mcp_instruction}{task_continuation}"


def mcp_result_content(result: Any) -> tuple[str, Any]:
    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict):
        payload = structured.get("result", structured)
        if isinstance(payload, str):
            try:
                return payload, json.loads(payload)
            except (TypeError, ValueError):
                return payload, None
        return json.dumps(payload, ensure_ascii=False, default=str), payload
    parts = getattr(result, "content", None) or []
    text = "".join(str(getattr(part, "text", "") or "") for part in parts).strip()
    try:
        parsed = json.loads(text) if text else None
    except (TypeError, ValueError):
        parsed = None
    return text, parsed


def is_structured_tool_rejection(result: Any, structured: Any) -> bool:
    if bool(getattr(result, "isError", False)):
        return True
    if not isinstance(structured, dict):
        return False
    return structured.get("ok") is False or str(
        structured.get("status") or ""
    ).casefold() in {"rejected", "error", "failed"}


async def call_required_mcp_tool(
    session: Any,
    *,
    tool_name: str,
    arguments: dict[str, Any] | None = None,
    phase: str,
) -> dict[str, Any]:
    try:
        result = await session.call_tool(tool_name, dict(arguments or {}))
    except BaseException as exc:
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        leaves = exception_details(exc)
        detail = "; ".join(
            f"{item['type']}: {item['message']}" for item in leaves
        ) or str(exc)
        raise RuntimeError(
            f"Required {phase} MCP tool `{tool_name}` failed: {detail}"
        ) from exc
    content, structured = mcp_result_content(result)
    rejected = is_structured_tool_rejection(result, structured)
    output = {
        "tool_call_id": f"pipeline-required-{phase}-tool",
        "name": tool_name,
        "status": "error" if rejected else "success",
        "content": content,
        "structured_content": structured,
        "script_fallback": phase == "final",
        "pipeline_required": True,
        "phase": phase,
    }
    if rejected:
        raise RuntimeError(
            f"Required {phase} MCP tool `{tool_name}` was rejected: {content}"
        )
    return output
