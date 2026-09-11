"""Classify PubChem lookup failures so the MCP process stays up.

404 is a miss, not a retry. 429/5xx and timeouts back off. The tool layer
must still return a schema-valid payload after the last attempt.
"""

from __future__ import annotations

import logging
import os
import queue
import threading
import time
from typing import Any, Callable, TypeVar

RETRYABLE_HTTP_STATUS = frozenset({429, 500, 502, 503, 504})
_CLIENT_ERROR_START = 400
_CLIENT_ERROR_END = 500

logger = logging.getLogger(__name__)
_T = TypeVar("_T")


def env_int(name: str, default: int, *, minimum: int = 1) -> int:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return max(minimum, int(raw))
    except ValueError:
        return default


def env_float(name: str, default: float, *, minimum: float = 1.0) -> float:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return max(minimum, float(raw))
    except ValueError:
        return default


def http_status_code(exc: BaseException) -> int | None:
    code = getattr(exc, "code", None)
    if isinstance(code, int) and 100 <= code <= 599:
        return code
    return None


def is_not_found_error(exc: BaseException) -> bool:
    if type(exc).__name__ in {"NotFoundError"}:
        return True
    return http_status_code(exc) == 404


def is_retryable_pubchem_error(exc: BaseException) -> bool:
    """Retry NCBI flakes. Do not retry 4xx except 429."""
    if is_not_found_error(exc):
        return False
    status = http_status_code(exc)
    if status is not None:
        if status in RETRYABLE_HTTP_STATUS:
            return True
        if _CLIENT_ERROR_START <= status < _CLIENT_ERROR_END:
            return False
    return True


def backoff_seconds(attempt: int, *, cap: float = 8.0) -> float:
    """Sleep after a failed attempt. attempt is 1-based."""
    return min(float(2 ** max(0, attempt - 1)), cap)


def lookup_error_record(query: str, exc: BaseException) -> dict[str, Any]:
    label = str(query or "").strip() or "the given query"
    return {
        "ok": False,
        "matched": False,
        "error": f"PubChem lookup failed for {label!r}: {exc}",
        "query": label,
        "instruction": (
            "Lookup did not complete. Leave the lookup unresolved "
            "rather than inventing values."
        ),
    }


def run_with_timeout(callback: Callable[[], _T], timeout_seconds: float) -> _T:
    """Run a blocking PubChem call; raise if it exceeds timeout_seconds."""
    result_queue: queue.Queue[tuple[bool, object]] = queue.Queue(maxsize=1)

    def run() -> None:
        try:
            result_queue.put((True, callback()))
        except BaseException as exc:
            result_queue.put((False, exc))

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    worker.join(timeout_seconds)
    if worker.is_alive():
        raise TimeoutError(f"PubChem request exceeded {timeout_seconds:g}s")
    ok, value = result_queue.get_nowait()
    if ok:
        return value  # type: ignore[return-value]
    raise value  # type: ignore[misc]


def call_pubchem(
    label: str,
    callback: Callable[[], _T],
    *,
    timeout_seconds: float,
    attempts: int,
    sleep: Callable[[float], None] = time.sleep,
) -> _T | None:
    """Timeout + retry a PubChem NCBI call. 404 returns None; 429/5xx back off."""
    last_error: Exception | None = None
    total = max(1, int(attempts))
    for attempt in range(1, total + 1):
        try:
            return run_with_timeout(callback, timeout_seconds)
        except Exception as exc:
            last_error = exc
            if is_not_found_error(exc):
                logger.info("PubChem %s: no compound matched", label)
                return None
            retryable = is_retryable_pubchem_error(exc)
            logger.warning(
                "PubChem %s failed on attempt %s/%s: %s",
                label,
                attempt,
                total,
                exc,
            )
            if retryable and attempt < total:
                sleep(backoff_seconds(attempt))
                continue
            raise
    raise last_error if last_error is not None else TimeoutError("PubChem request failed")
