"""Retry LLM provider/transport failures in place.

These errors are not KG or extraction attempt failures. A 504, 429, or
aborted completion should wait and repeat the same call without consuming
``kg_max_attempts`` or semantic retry budget.
"""

from __future__ import annotations

import asyncio
import os
import random
from collections.abc import Awaitable, Callable
from typing import TypeVar

T = TypeVar("T")

TRANSPORT_ERROR_MARKERS = (
    "connection error",
    "connection reset",
    "connection aborted",
    "connect timeout",
    "read timeout",
    "write timeout",
    "timed out",
    "timeout",
    "temporarily unavailable",
    "service unavailable",
    "too many requests",
    "rate limit",
    "rate_limit",
    "overloaded",
    "the operation was aborted",
    "operation was aborted",
    "429",
    "502",
    "503",
    "504",
)

_DEFAULT_MAX_TRIES = 8
_DEFAULT_BASE_WAIT = 5.0
_DEFAULT_MAX_WAIT = 60.0


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return max(1, int(raw))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return max(0.0, float(raw))
    except ValueError:
        return default


def transport_max_tries() -> int:
    return _env_int("TWA_LLM_TRANSPORT_MAX_RETRIES", _DEFAULT_MAX_TRIES)


def _leaf_exceptions(exc: BaseException) -> list[BaseException]:
    children = getattr(exc, "exceptions", None)
    if not children:
        return [exc]
    leaves: list[BaseException] = []
    for child in children:
        leaves.extend(_leaf_exceptions(child))
    return leaves


def _text_is_transport(text: str) -> bool:
    folded = text.casefold()
    return any(marker in folded for marker in TRANSPORT_ERROR_MARKERS)


def is_llm_transport_error(error: BaseException) -> bool:
    """True for provider/gateway/network failures, not semantic or tool-contract errors."""
    if isinstance(error, (ConnectionError, TimeoutError)):
        return True
    seen: set[int] = set()
    stack: list[BaseException] = list(_leaf_exceptions(error))
    while stack:
        item = stack.pop()
        ident = id(item)
        if ident in seen:
            continue
        seen.add(ident)
        if isinstance(item, (ConnectionError, TimeoutError)):
            return True
        if _text_is_transport(str(item)):
            return True
        for linked in (item.__cause__, item.__context__):
            if linked is not None:
                stack.append(linked)
    return False


def transport_retry_wait_seconds(
    attempt: int,
    *,
    jitter: bool = True,
) -> float:
    """Backoff after transport failure ``attempt`` (0-based)."""
    base = _env_float("TWA_LLM_TRANSPORT_BASE_WAIT", _DEFAULT_BASE_WAIT)
    cap = _env_float("TWA_LLM_TRANSPORT_MAX_WAIT", _DEFAULT_MAX_WAIT)
    wait = min(cap, base * (2 ** max(0, attempt)))
    if jitter and wait > 0:
        wait *= random.uniform(0.8, 1.2)
    return wait


async def retry_async_on_transport(
    operation: Callable[[], Awaitable[T]],
    *,
    restore: Callable[[], None] | None = None,
    logger: object | None = None,
    what: str = "LLM call",
) -> T:
    """Await ``operation``; on transport errors wait and retry the same call."""
    max_tries = transport_max_tries()
    last: BaseException | None = None
    for attempt in range(max_tries):
        try:
            return await operation()
        except Exception as exc:
            if not is_llm_transport_error(exc):
                raise
            last = exc
            if attempt >= max_tries - 1:
                if logger is not None:
                    logger.error(
                        "    LLM transport retries exhausted for %s after %d tries: %s",
                        what,
                        max_tries,
                        exc,
                    )
                raise
            if restore is not None:
                restore()
            wait = transport_retry_wait_seconds(attempt)
            if logger is not None:
                logger.warning(
                    "    LLM transport error on %s (try %d/%d, not a KG/extraction attempt): %s",
                    what,
                    attempt + 1,
                    max_tries,
                    exc,
                )
                logger.info("    Waiting %.1fs before in-place transport retry...", wait)
            await asyncio.sleep(wait)
    assert last is not None
    raise last
