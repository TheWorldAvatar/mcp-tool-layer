"""Actual-cost telemetry for LangChain, OpenAI SDK, and raw HTTP LLM calls.

The journal deliberately contains request metadata only: prompts, messages, and API
keys are never serialized.
"""
from __future__ import annotations

import inspect
import json
import os
import re
import sys
import threading
import time
import warnings
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterator, Mapping
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen
from uuid import uuid4

try:
    from langchain_core.callbacks import BaseCallbackHandler
except Exception:  # pragma: no cover - optional dependency
    class BaseCallbackHandler:  # type: ignore[no-redef]
        pass


SCHEMA_VERSION = "1.0"
_THREAD_LOCK = threading.RLock()
_PARENT_CALL_ID: ContextVar[str | None] = ContextVar("llm_parent_call_id", default=None)
_CALL_CONTEXT: ContextVar[dict[str, Any]] = ContextVar("llm_call_context", default={})
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_SENSITIVE_KEYS = {"api_key", "prompt", "messages", "input", "instructions"}
_PROCESS_COST_USD = 0.0


class LLMCostCapExceeded(RuntimeError):
    """Raised before an LLM call after this case process reaches its cost cap."""


def _configured_process_cost_cap_usd() -> float:
    raw = os.getenv("TWA_LLM_PROCESS_COST_CAP_USD", "").strip()
    if not raw:
        return 0.0
    try:
        return max(0.0, float(raw))
    except ValueError:
        return 0.0


def _enforce_process_cost_cap() -> None:
    cap = _configured_process_cost_cap_usd()
    with _THREAD_LOCK:
        spent = _PROCESS_COST_USD
    if cap > 0 and spent >= cap:
        raise LLMCostCapExceeded(
            f"Per-case LLM cost cap reached: spent=${spent:.6f}, cap=${cap:.6f}"
        )


def _record_process_cost(value: Any) -> None:
    global _PROCESS_COST_USD
    try:
        cost = float(value or 0.0)
    except (TypeError, ValueError):
        return
    if cost <= 0:
        return
    with _THREAD_LOCK:
        _PROCESS_COST_USD += cost


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def journal_path() -> Path:
    explicit = os.getenv("TWA_LLM_COST_JOURNAL", "").strip()
    if explicit:
        return Path(explicit).expanduser().resolve()
    run_root = os.getenv("TWA_AGENTIC_DATA_DIR", "").strip()
    if run_root:
        return Path(run_root).expanduser().resolve() / "reports" / "openrouter_costs.jsonl"
    return _PROJECT_ROOT / "data" / "reports" / "openrouter_costs.jsonl"


def current_parent_call_id() -> str | None:
    return _PARENT_CALL_ID.get()


@contextmanager
def telemetry_context(
    parent_call_id: str | None = None, context: Mapping[str, Any] | None = None
) -> Iterator[str]:
    """Associate nested LLM completions with one higher-level operation."""
    parent = parent_call_id or uuid4().hex
    parent_token = _PARENT_CALL_ID.set(parent)
    context_token = _CALL_CONTEXT.set(dict(context or {}))
    try:
        yield parent
    finally:
        _CALL_CONTEXT.reset(context_token)
        _PARENT_CALL_ID.reset(parent_token)


def _warn_write_failure(path: Path, exc: BaseException) -> None:
    message = f"LLM cost telemetry write failed for {path}: {type(exc).__name__}: {exc}"
    warnings.warn(message, RuntimeWarning, stacklevel=2)
    try:
        print(message, file=sys.stderr, flush=True)
    except Exception:
        pass


def _sanitize(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _sanitize(item)
            for key, item in value.items()
            if str(key).lower() not in _SENSITIVE_KEYS
            and "api_key" not in str(key).lower()
        }
    if isinstance(value, (list, tuple)):
        return [_sanitize(item) for item in value]
    return value


def _safe_error(error: BaseException) -> dict[str, str]:
    message = str(error).replace("\r", " ").replace("\n", " ")[:500]
    for name in ("OPENROUTER_API_KEY", "REMOTE_API_KEY", "OPENAI_API_KEY"):
        secret = os.getenv(name)
        if secret:
            message = message.replace(secret, "[redacted]")
    message = re.sub(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]+", "Bearer [redacted]", message)
    if any(marker in message.lower() for marker in ('"messages"', "'messages'", "prompt=")):
        message = "[redacted request-bearing provider error]"
    return {"type": type(error).__name__, "message": message}


def _cross_process_write(path: Path, data: bytes) -> None:
    """One locked append. The sidecar lock makes Windows/POSIX processes cooperate."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(path.suffix + ".lock")
    with _THREAD_LOCK:
        lock_file = open(lock_path, "a+b")
        try:
            if os.name == "nt":
                import msvcrt

                lock_file.seek(0)
                if lock_file.tell() == 0:
                    lock_file.write(b"\0")
                    lock_file.flush()
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl

                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            descriptor = os.open(
                path,
                os.O_APPEND | os.O_CREAT | os.O_WRONLY | getattr(os, "O_BINARY", 0),
                0o644,
            )
            try:
                os.write(descriptor, data)
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        finally:
            try:
                if os.name == "nt":
                    lock_file.seek(0)
                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            finally:
                lock_file.close()


def append_cost_event(event: Mapping[str, Any], path: str | Path | None = None) -> bool:
    """Append a sanitized event; report failures loudly without breaking the LLM call."""
    target = Path(path) if path else journal_path()
    record = {
        "schema_version": SCHEMA_VERSION,
        "timestamp": _utc_now(),
        "pid": os.getpid(),
        **dict(event),
    }
    record = _sanitize(record)
    try:
        data = (json.dumps(record, ensure_ascii=False, default=str) + "\n").encode("utf-8")
        _cross_process_write(target, data)
        return True
    except BaseException as exc:
        _warn_write_failure(target, exc)
        return False


def _value(obj: Any, *names: str) -> Any:
    for name in names:
        if isinstance(obj, Mapping) and name in obj:
            return obj[name]
        value = getattr(obj, name, None)
        if value is not None:
            return value
    return None


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if value is None:
        return {}
    for method in ("model_dump", "dict"):
        fn = getattr(value, method, None)
        if callable(fn):
            try:
                result = fn()
                if isinstance(result, Mapping):
                    return dict(result)
            except Exception:
                pass
    return {
        key: getattr(value, key)
        for key in (
            "prompt_tokens",
            "completion_tokens",
            "total_tokens",
            "input_tokens",
            "output_tokens",
            "cost",
            "cost_details",
        )
        if getattr(value, key, None) is not None
    }


def normalize_token_usage(usage: Any) -> dict[str, int]:
    raw = _as_dict(usage)

    def integer(value: Any) -> int:
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    prompt = integer(raw.get("prompt_tokens", raw.get("input_tokens", 0)))
    completion = integer(raw.get("completion_tokens", raw.get("output_tokens", 0)))
    total = integer(raw.get("total_tokens", prompt + completion))
    return {
        "input_tokens": prompt,
        "output_tokens": completion,
        "total_tokens": total,
    }


def _cost_from_usage(usage: Any) -> float | None:
    raw = _as_dict(usage)
    cost = raw.get("cost")
    if cost is None:
        details = _as_dict(raw.get("cost_details"))
        cost = details.get("total_cost", details.get("cost"))
        if cost is None and details:
            parts = [
                value
                for key, value in details.items()
                if key.endswith("_cost") and isinstance(value, (int, float))
            ]
            cost = sum(parts) if parts else None
    try:
        return float(cost) if cost is not None else None
    except (TypeError, ValueError):
        return None


def base_url_host(base_url: Any) -> str:
    value = str(base_url or "")
    return (urlparse(value).hostname or "").lower()


def _is_openrouter(base_url: Any) -> bool:
    host = base_url_host(base_url)
    return host == "openrouter.ai" or host.endswith(".openrouter.ai")


def merge_openrouter_usage_include(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    """Ask OpenRouter to return the billed cost on the completion itself."""
    merged = dict(payload or {})
    usage = dict(merged.get("usage") or {})
    usage["include"] = True
    merged["usage"] = usage
    return merged


def apply_openrouter_usage_include(
    payload: Mapping[str, Any] | None, *, base_url: Any
) -> dict[str, Any]:
    """Add usage.include only for OpenRouter; leave other providers unchanged."""
    body = dict(payload or {})
    if not _is_openrouter(base_url):
        return body
    return merge_openrouter_usage_include(body)


def _generation_lookup(
    generation_id: str,
    *,
    api_key: str | None = None,
    retries: int = 4,
    timeout: float = 8.0,
) -> dict[str, Any] | None:
    if not generation_id:
        return None
    token = api_key or os.getenv("OPENROUTER_API_KEY") or os.getenv("REMOTE_API_KEY")
    if not token:
        return None
    url = "https://openrouter.ai/api/v1/generation?" + urlencode({"id": generation_id})
    for attempt in range(max(1, min(int(retries), 5))):
        try:
            request = Request(url, headers={"Authorization": f"Bearer {token}"})
            with urlopen(request, timeout=max(0.1, timeout)) as response:
                payload = json.loads(response.read().decode("utf-8"))
            data = payload.get("data", payload)
            return dict(data) if isinstance(data, Mapping) else None
        except Exception:
            if attempt + 1 < retries:
                # Generation stats are often not queryable for the first second.
                time.sleep(min(0.5 * (2**attempt), 2.0))
    return None


def extract_response_metadata(response: Any) -> dict[str, Any]:
    metadata = _as_dict(_value(response, "response_metadata"))
    usage = _value(response, "usage", "usage_metadata")
    metadata_usage = metadata.get("token_usage") or metadata.get("usage")
    if usage is None:
        usage = metadata_usage
    generation_id = (
        metadata.get("generation_id")
        or metadata.get("id")
        or metadata.get("response_id")
        or _value(response, "id")
    )
    model = _value(response, "model") or metadata.get("model_name") or metadata.get("model")
    return {
        "generation_id": str(generation_id or ""),
        "model": str(model or ""),
        "token_usage": normalize_token_usage(usage),
        "inline_cost": (
            _cost_from_usage(usage)
            if _cost_from_usage(usage) is not None
            else _cost_from_usage(metadata_usage)
        ),
        "provider": metadata.get("provider") or metadata.get("provider_name"),
    }


def _resolve_response_cost(
    response: Any, *, base_url: Any, api_key: str | None = None
) -> dict[str, Any]:
    meta = extract_response_metadata(response)
    if meta["inline_cost"] is not None:
        return {
            **meta,
            "actual_cost_usd": meta["inline_cost"],
            "cost_source": "response_usage",
            "cost_status": "resolved",
        }
    if not _is_openrouter(base_url):
        return {
            **meta,
            "actual_cost_usd": None,
            "cost_source": "not_openrouter",
            "cost_status": "unavailable",
        }
    lookup = _generation_lookup(meta["generation_id"], api_key=api_key)
    if lookup:
        cost = lookup.get("total_cost", lookup.get("cost"))
        try:
            cost = float(cost) if cost is not None else None
        except (TypeError, ValueError):
            cost = None
        usage = lookup.get("usage")
        if isinstance(usage, (int, float)):
            usage = {"total_tokens": int(usage)}
        return {
            **meta,
            "token_usage": normalize_token_usage(usage) if usage else meta["token_usage"],
            "provider": lookup.get("provider_name") or meta["provider"],
            "actual_cost_usd": cost,
            "cost_source": "openrouter_generation_api",
            "cost_status": "resolved" if cost is not None else "pending",
        }
    return {
        **meta,
        "actual_cost_usd": None,
        "cost_source": "openrouter_generation_api",
        "cost_status": "pending",
    }


def record_response(
    response: Any,
    *,
    call_id: str,
    attempt: int = 1,
    transport: str,
    model: str = "",
    base_url: Any = "",
    elapsed: float | None = None,
    parent_call_id: str | None = None,
    context: Mapping[str, Any] | None = None,
    api_key: str | None = None,
) -> dict[str, Any]:
    resolved = _resolve_response_cost(response, base_url=base_url, api_key=api_key)
    event = {
        "event": "completed",
        "billable": True,
        "call_id": call_id,
        "parent_call_id": parent_call_id or current_parent_call_id(),
        "attempt": attempt,
        "provider_attempt": attempt,
        "transport": transport,
        "model": resolved.get("model") or model,
        "base_url_host": base_url_host(base_url),
        "generation_id": resolved.get("generation_id") or None,
        "token_usage": resolved.get("token_usage") or normalize_token_usage(None),
        "actual_cost_usd": resolved.get("actual_cost_usd"),
        "cost_source": resolved.get("cost_source"),
        "cost_status": resolved.get("cost_status"),
        "provider": resolved.get("provider"),
        "elapsed": elapsed,
        "error": None,
        "context": {**_CALL_CONTEXT.get(), **dict(context or {})},
    }
    append_cost_event(event)
    _record_process_cost(event.get("actual_cost_usd"))
    return event


def record_error(
    error: BaseException,
    *,
    call_id: str,
    attempt: int = 1,
    transport: str,
    model: str = "",
    base_url: Any = "",
    elapsed: float | None = None,
    parent_call_id: str | None = None,
    context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    status = "pending" if _is_openrouter(base_url) else "unavailable"
    event = {
        "event": "timed_out" if isinstance(error, TimeoutError) else "failed",
        "billable": False,
        "call_id": call_id,
        "parent_call_id": parent_call_id or current_parent_call_id(),
        "attempt": attempt,
        "provider_attempt": attempt,
        "transport": transport,
        "model": model,
        "base_url_host": base_url_host(base_url),
        "generation_id": None,
        "token_usage": normalize_token_usage(None),
        "actual_cost_usd": None,
        "cost_source": "provider_response_unavailable",
        "cost_status": status,
        "provider": None,
        "elapsed": elapsed,
        "error": _safe_error(error),
        "context": {**_CALL_CONTEXT.get(), **dict(context or {})},
    }
    append_cost_event(event)
    return event


class OpenRouterCostCallback(BaseCallbackHandler):
    """LangChain callback: one billable journal event per returned completion."""

    run_inline = True
    raise_error = False
    ignore_llm = False
    ignore_chat_model = False

    def __init__(self, *, model: str = "", base_url: Any = "", api_key: str | None = None):
        self.model = model
        self.base_url = str(base_url or "")
        self.api_key = api_key
        self._runs: dict[str, tuple[float, str | None, dict[str, Any]]] = {}
        self._lock = threading.Lock()
        self.raise_error = _configured_process_cost_cap_usd() > 0

    def on_llm_start(self, serialized: Any, prompts: Any, *, run_id: Any, **kwargs: Any) -> None:
        del serialized, prompts, kwargs
        _enforce_process_cost_cap()
        with self._lock:
            self._runs[str(run_id)] = (
                time.perf_counter(),
                current_parent_call_id(),
                dict(_CALL_CONTEXT.get()),
            )

    def on_chat_model_start(
        self, serialized: Any, messages: Any, *, run_id: Any, **kwargs: Any
    ) -> None:
        self.on_llm_start(serialized, messages, run_id=run_id, **kwargs)

    def _pop(self, run_id: Any) -> tuple[float, str | None, dict[str, Any]]:
        with self._lock:
            return self._runs.pop(
                str(run_id),
                (time.perf_counter(), current_parent_call_id(), dict(_CALL_CONTEXT.get())),
            )

    @staticmethod
    def _response_object(result: Any) -> Any:
        generations = getattr(result, "generations", None) or []
        for group in generations:
            for generation in group:
                message = getattr(generation, "message", None)
                if message is not None:
                    llm_output = getattr(result, "llm_output", None) or {}
                    message_meta = dict(getattr(message, "response_metadata", None) or {})
                    if llm_output.get("token_usage") and "token_usage" not in message_meta:
                        message_meta["token_usage"] = llm_output["token_usage"]
                    if llm_output.get("model_name") and "model_name" not in message_meta:
                        message_meta["model_name"] = llm_output["model_name"]
                    for key in ("id", "generation_id", "response_id", "provider", "provider_name"):
                        if llm_output.get(key) is not None and key not in message_meta:
                            message_meta[key] = llm_output[key]
                    return SimpleNamespace(
                        id=getattr(message, "id", None),
                        usage_metadata=getattr(message, "usage_metadata", None),
                        response_metadata=message_meta,
                    )
        return SimpleNamespace(response_metadata=getattr(result, "llm_output", None) or {})

    def on_llm_end(self, response: Any, *, run_id: Any, **kwargs: Any) -> None:
        del kwargs
        started, parent, context = self._pop(run_id)
        record_response(
            self._response_object(response),
            call_id=str(run_id),
            transport="langchain",
            model=self.model,
            base_url=self.base_url,
            elapsed=time.perf_counter() - started,
            parent_call_id=parent,
            context=context,
            api_key=self.api_key,
        )

    def on_llm_error(self, error: BaseException, *, run_id: Any, **kwargs: Any) -> None:
        del kwargs
        started, parent, context = self._pop(run_id)
        record_error(
            error,
            call_id=str(run_id),
            transport="langchain",
            model=self.model,
            base_url=self.base_url,
            elapsed=time.perf_counter() - started,
            parent_call_id=parent,
            context=context,
        )


class _CreateProxy:
    def __init__(self, create: Any, owner: "_OpenAIClientProxy", transport: str):
        self._create = create
        self._owner = owner
        self._transport = transport

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        _enforce_process_cost_cap()
        call_id = uuid4().hex
        started = time.perf_counter()
        kwargs = dict(kwargs)
        if _is_openrouter(self._owner.base_url):
            kwargs["extra_body"] = merge_openrouter_usage_include(
                kwargs.get("extra_body")
            )
        model = str(kwargs.get("model") or "")
        try:
            result = self._create(*args, **kwargs)
        except BaseException as exc:
            try:
                record_error(
                    exc,
                    call_id=call_id,
                    transport=self._transport,
                    model=model,
                    base_url=self._owner.base_url,
                    elapsed=time.perf_counter() - started,
                )
            except BaseException as telemetry_exc:
                _warn_write_failure(journal_path(), telemetry_exc)
            raise
        if inspect.isawaitable(result):
            async def finish() -> Any:
                try:
                    response = await result
                except BaseException as exc:
                    try:
                        record_error(
                            exc,
                            call_id=call_id,
                            transport=self._transport,
                            model=model,
                            base_url=self._owner.base_url,
                            elapsed=time.perf_counter() - started,
                        )
                    except BaseException as telemetry_exc:
                        _warn_write_failure(journal_path(), telemetry_exc)
                    raise
                try:
                    record_response(
                        response,
                        call_id=call_id,
                        transport=self._transport,
                        model=model,
                        base_url=self._owner.base_url,
                        elapsed=time.perf_counter() - started,
                        api_key=self._owner.api_key,
                    )
                except BaseException as telemetry_exc:
                    _warn_write_failure(journal_path(), telemetry_exc)
                return response
            return finish()
        try:
            record_response(
                result,
                call_id=call_id,
                transport=self._transport,
                model=model,
                base_url=self._owner.base_url,
                elapsed=time.perf_counter() - started,
                api_key=self._owner.api_key,
            )
        except BaseException as telemetry_exc:
            _warn_write_failure(journal_path(), telemetry_exc)
        return result


class _NamespaceProxy:
    def __init__(self, wrapped: Any, **overrides: Any):
        self._wrapped = wrapped
        self.__dict__.update(overrides)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._wrapped, name)


class _OpenAIClientProxy:
    def __init__(self, client: Any, base_url: Any = None):
        self._client = client
        self.base_url = str(base_url or getattr(client, "base_url", "") or "")
        raw_key = getattr(client, "api_key", None)
        self.api_key = str(raw_key) if raw_key else None
        chat = getattr(client, "chat", None)
        completions = getattr(chat, "completions", None)
        if completions is not None and callable(getattr(completions, "create", None)):
            wrapped_completions = _NamespaceProxy(
                completions,
                create=_CreateProxy(
                    completions.create, self, "openai_sdk_chat_completions"
                ),
            )
            self.chat = _NamespaceProxy(chat, completions=wrapped_completions)
        responses = getattr(client, "responses", None)
        if responses is not None and callable(getattr(responses, "create", None)):
            self.responses = _NamespaceProxy(
                responses,
                create=_CreateProxy(responses.create, self, "openai_sdk_responses"),
            )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)

    def __enter__(self) -> "_OpenAIClientProxy":
        enter = getattr(self._client, "__enter__", None)
        if callable(enter):
            enter()
        return self

    def __exit__(self, *args: Any) -> Any:
        return self._client.__exit__(*args)

    async def __aenter__(self) -> "_OpenAIClientProxy":
        enter = getattr(self._client, "__aenter__", None)
        if callable(enter):
            await enter()
        return self

    async def __aexit__(self, *args: Any) -> Any:
        return await self._client.__aexit__(*args)


def instrument_openai_client(client: Any, *, base_url: Any = None) -> Any:
    if isinstance(client, _OpenAIClientProxy):
        return client
    return _OpenAIClientProxy(client, base_url=base_url)


def record_httpx_response(
    response: Any,
    *,
    model: str,
    base_url: str,
    call_id: str | None = None,
    elapsed: float | None = None,
) -> dict[str, Any]:
    """Record an already-returned OpenAI-compatible HTTP JSON response."""
    try:
        payload = response.json() if callable(getattr(response, "json", None)) else response
    except Exception:
        payload = {}
    return record_response(
        payload,
        call_id=call_id or uuid4().hex,
        transport="httpx_chat_completions",
        model=model,
        base_url=base_url,
        elapsed=elapsed,
    )


def _read_events(path: str | Path) -> list[dict[str, Any]]:
    target = Path(path)
    if not target.is_file():
        return []
    events = []
    for line in target.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            value = json.loads(line)
            if isinstance(value, dict):
                events.append(value)
        except json.JSONDecodeError:
            continue
    return events


def reconcile_pending_costs(path: str | Path) -> dict[str, int]:
    """Append immutable cost_resolved rows for resolvable pending completions."""
    events = _read_events(path)
    resolved_keys = {
        (e.get("call_id"), e.get("attempt"), e.get("generation_id"))
        for e in events
        if e.get("event") == "cost_resolved"
    }
    reconciled = 0
    still_pending = 0
    for event in events:
        key = (event.get("call_id"), event.get("attempt"), event.get("generation_id"))
        if (
            event.get("event") != "completed"
            or event.get("cost_status") != "pending"
            or key in resolved_keys
        ):
            continue
        generation_id = str(event.get("generation_id") or "")
        lookup = _generation_lookup(generation_id)
        cost = lookup.get("total_cost", lookup.get("cost")) if lookup else None
        try:
            cost = float(cost) if cost is not None else None
        except (TypeError, ValueError):
            cost = None
        if cost is None:
            still_pending += 1
            continue
        append_cost_event(
            {
                "event": "cost_resolved",
                "billable": False,
                "call_id": event.get("call_id"),
                "parent_call_id": event.get("parent_call_id"),
                "attempt": event.get("attempt"),
                "provider_attempt": event.get("provider_attempt"),
                "transport": event.get("transport"),
                "model": event.get("model"),
                "base_url_host": event.get("base_url_host"),
                "generation_id": generation_id,
                "token_usage": normalize_token_usage(lookup.get("usage")),
                "actual_cost_usd": cost,
                "cost_source": "openrouter_generation_api_reconcile",
                "cost_status": "resolved",
                "provider": lookup.get("provider_name"),
                "elapsed": None,
                "error": None,
                "context": event.get("context") or {},
            },
            path,
        )
        resolved_keys.add(key)
        reconciled += 1
    return {"reconciled": reconciled, "still_pending": still_pending}


def summarize_costs(
    path: str | Path | None = None, parent_call_id: str | None = None
) -> dict[str, Any]:
    """Deduplicate billable completions and aggregate actual provider costs."""
    events = _read_events(path or journal_path())
    resolutions: dict[tuple[Any, Any, Any], dict[str, Any]] = {}
    for event in events:
        if event.get("event") == "cost_resolved":
            key = (event.get("call_id"), event.get("attempt"), event.get("generation_id"))
            resolutions[key] = event
    calls: dict[tuple[Any, Any, Any], dict[str, Any]] = {}
    for event in events:
        if event.get("event") != "completed" or not event.get("billable", True):
            continue
        if parent_call_id is not None and event.get("parent_call_id") != parent_call_id:
            continue
        key = (event.get("call_id"), event.get("attempt"), event.get("generation_id"))
        calls.setdefault(key, event)
    totals = {
        "actual_cost_usd": 0.0,
        "resolved_calls": 0,
        "pending_calls": 0,
        "unavailable_calls": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "generation_ids": [],
    }
    for key, original in calls.items():
        event = resolutions.get(key, original)
        status = event.get("cost_status") or "unavailable"
        if status == "resolved":
            totals["resolved_calls"] += 1
            totals["actual_cost_usd"] += float(event.get("actual_cost_usd") or 0.0)
        elif status == "pending":
            totals["pending_calls"] += 1
        else:
            totals["unavailable_calls"] += 1
        original_usage = normalize_token_usage(original.get("token_usage"))
        resolved_usage = normalize_token_usage(event.get("token_usage"))
        usage = {
            name: resolved_usage[name] or original_usage[name]
            for name in ("input_tokens", "output_tokens", "total_tokens")
        }
        for name in ("input_tokens", "output_tokens", "total_tokens"):
            totals[name] += usage[name]
        generation_id = event.get("generation_id")
        if generation_id and generation_id not in totals["generation_ids"]:
            totals["generation_ids"].append(generation_id)
    totals["actual_cost_usd"] = round(totals["actual_cost_usd"], 12)
    totals["billable_calls"] = len(calls)
    return totals


__all__ = [
    "OpenRouterCostCallback",
    "append_cost_event",
    "apply_openrouter_usage_include",
    "current_parent_call_id",
    "instrument_openai_client",
    "journal_path",
    "merge_openrouter_usage_include",
    "reconcile_pending_costs",
    "record_error",
    "record_httpx_response",
    "record_response",
    "summarize_costs",
    "telemetry_context",
]
