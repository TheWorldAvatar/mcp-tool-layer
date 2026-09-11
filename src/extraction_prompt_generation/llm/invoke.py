"""JSON LLM calls used by planning, exact-edits, and prompt semantic review.

Wraps `models.LLMCreator`. Timeouts and the durable journal live in
`invocation_journal.py`. See llm/README.md.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from typing import Any

from models.LLMCreator import LLMCreator
from models.ModelConfig import ModelConfig
from models.locked_llm import LOCKED_LLM_SEED, LOCKED_TEMPERATURE
from models.llm_call_telemetry import (
    journal_path,
    summarize_costs,
    telemetry_context,
)
from src.extraction_prompt_generation.llm.invocation_journal import (
    LLMInvocationTimeout,
    append_invocation_event,
    invoke_with_hard_timeout,
    new_call_id,
)


@dataclass
class LLMJsonResult:
    data: dict[str, Any]
    elapsed_seconds: float
    token_usage: dict[str, Any]
    raw_response: str = ""
    actual_cost_usd: float | None = None
    generation_ids: list[str] | None = None
    cost_status: str = ""


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def _generation_timeout_disabled() -> bool:
    return os.environ.get("TWA_DISABLE_GENERATION_TIMEOUT", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _response_text(response: Any) -> str:
    content = getattr(response, "content", response)
    if isinstance(content, list):
        return "\n".join(str(item) for item in content)
    return str(content)


def _token_usage(response: Any) -> dict[str, Any]:
    usage = getattr(response, "usage_metadata", None)
    if isinstance(usage, dict) and usage:
        return usage
    metadata = getattr(response, "response_metadata", None)
    if isinstance(metadata, dict):
        token_usage = metadata.get("token_usage")
        if isinstance(token_usage, dict):
            return token_usage
    return {}


def _cost_telemetry_reference(
    response: Any, *, parent_call_id: str | None = None
) -> dict[str, Any]:
    """Attach billed OpenRouter cost from the completion and the cost journal."""
    try:
        from models.llm_call_telemetry import extract_response_metadata

        metadata = extract_response_metadata(response)
        costs = (
            summarize_costs(journal_path(), parent_call_id)
            if parent_call_id
            else {"actual_cost_usd": None, "generation_ids": [], "pending_calls": 0}
        )
        generation_ids = list(costs.get("generation_ids") or [])
        generation_id = metadata.get("generation_id") or None
        if generation_id and generation_id not in generation_ids:
            generation_ids.append(generation_id)
        actual_cost = (
            costs.get("actual_cost_usd")
            if costs.get("billable_calls")
            else metadata.get("inline_cost")
        )
        if costs.get("pending_calls"):
            status = "pending"
        elif actual_cost is not None:
            status = "resolved"
        else:
            status = "unavailable"
        return {
            "generation_id": generation_id,
            "generation_ids": generation_ids,
            "actual_cost_usd": actual_cost,
            "cost_status": status,
            "cost_journal": str(journal_path()),
        }
    except Exception:
        return {
            "generation_id": None,
            "generation_ids": [],
            "actual_cost_usd": None,
            "cost_status": "unavailable",
            "cost_journal": str(journal_path()),
        }


def extract_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start >= 0 and end > start:
            data = json.loads(stripped[start : end + 1])
        else:
            data = json.loads("{" + stripped + "}")
    if not isinstance(data, dict):
        raise ValueError("LLM response must be a JSON object")
    return data


def invoke_json(
    model: str,
    prompt: str,
    *,
    timeout_seconds: int | None = None,
    max_attempts: int = 3,
    provider_max_retries: int | None = None,
    temperature: float = LOCKED_TEMPERATURE,
) -> LLMJsonResult:
    """Call the welded remote model and parse one JSON object.

    `temperature` is accepted for call-site compatibility and ignored.
    GPT-5 omits temperature; other models send 0. Seed is always 42.
    """
    del temperature
    last_detail = ""
    attempts = max(1, max_attempts)
    invocation_id = new_call_id()
    timeout_disabled = _generation_timeout_disabled()
    http_timeout = (
        86400
        if timeout_disabled
        else (timeout_seconds or _env_int("TWA_GENERATION_TIMEOUT", 1800))
    )
    effective_timeout = None if timeout_disabled else http_timeout
    for attempt in range(1, attempts + 1):
        llm = LLMCreator(
            model=model,
            remote_model=True,
            model_config=ModelConfig(
                timeout=http_timeout,
                temperature=LOCKED_TEMPERATURE,
                top_p=0.1,
                max_retries=provider_max_retries,
                seed=LOCKED_LLM_SEED,
            ),
        ).setup_llm()
        effective_prompt = prompt
        if attempt > 1:
            effective_prompt = (
                prompt
                + "\n\nPrevious response was not parseable JSON. Return only one valid JSON object "
                + "with the exact requested keys. Escape all newlines and quotes inside JSON string values. "
                + f"Previous parse error detail: {last_detail}"
            )
        started = time.perf_counter()
        append_invocation_event(
            {
                "call_id": invocation_id,
                "event": "started",
                "attempt": attempt,
                "model": model,
                "timeout_seconds": effective_timeout,
                "prompt_sha256": hashlib.sha256(
                    effective_prompt.encode("utf-8")
                ).hexdigest(),
            }
        )
        try:
            with telemetry_context(
                invocation_id,
                {"component": "invoke_json", "model": model, "attempt": attempt},
            ):
                response = invoke_with_hard_timeout(
                    lambda: llm.invoke(effective_prompt),
                    timeout_seconds=effective_timeout,
                )
        except LLMInvocationTimeout:
            append_invocation_event(
                {
                    "call_id": invocation_id,
                    "event": "timed_out",
                    "attempt": attempt,
                    "model": model,
                    "elapsed_seconds": time.perf_counter() - started,
                }
            )
            raise
        except BaseException as exc:
            append_invocation_event(
                {
                    "call_id": invocation_id,
                    "event": "failed",
                    "attempt": attempt,
                    "model": model,
                    "error_type": type(exc).__name__,
                    "error": str(exc)[:1000],
                    "elapsed_seconds": time.perf_counter() - started,
                }
            )
            raise
        elapsed = time.perf_counter() - started
        raw = _response_text(response)
        cost_reference = _cost_telemetry_reference(
            response, parent_call_id=invocation_id
        )
        try:
            data = extract_json_object(raw)
        except (json.JSONDecodeError, ValueError) as exc:
            preview = (raw or "").replace("\r", "")[:800]
            last_detail = f"len={len(raw or '')} preview={preview!r}"
            append_invocation_event(
                {
                    "call_id": invocation_id,
                    "event": "invalid_response",
                    "attempt": attempt,
                    "model": model,
                    "elapsed_seconds": elapsed,
                    "detail": last_detail,
                    **cost_reference,
                }
            )
            if attempt == attempts:
                raise RuntimeError(
                    f"LLM did not return a JSON object ({last_detail})"
                ) from exc
            continue
        result = LLMJsonResult(
            data=data,
            elapsed_seconds=elapsed,
            token_usage=_token_usage(response),
            raw_response=raw,
            actual_cost_usd=cost_reference.get("actual_cost_usd"),
            generation_ids=list(cost_reference.get("generation_ids") or []),
            cost_status=str(cost_reference.get("cost_status") or ""),
        )
        append_invocation_event(
            {
                "call_id": invocation_id,
                "event": "completed",
                "attempt": attempt,
                "model": model,
                "elapsed_seconds": elapsed,
                "token_usage": result.token_usage,
                **cost_reference,
            }
        )
        return result
    raise RuntimeError("LLM did not return a JSON object")
