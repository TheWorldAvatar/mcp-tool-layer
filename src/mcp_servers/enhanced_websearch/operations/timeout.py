"""Timeout + retry helpers for enhanced_websearch HTTP and Docling calls."""

from __future__ import annotations

import logging
import os
import queue
import threading
import time
from typing import Callable, TypeVar

logger = logging.getLogger(__name__)

_T = TypeVar("_T")


def env_float(name: str, default: float, *, minimum: float = 1.0) -> float:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return max(minimum, float(raw))
    except ValueError:
        return default


def env_int(name: str, default: int, *, minimum: int = 1) -> int:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return max(minimum, int(raw))
    except ValueError:
        return default


def run_with_timeout(callback: Callable[[], _T], timeout_seconds: float) -> _T:
    """Run a blocking callback; raise if it exceeds timeout_seconds."""
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
        raise TimeoutError(f"request exceeded {timeout_seconds:g} seconds")
    ok, value = result_queue.get_nowait()
    if ok:
        return value  # type: ignore[return-value]
    raise value  # type: ignore[misc]


def call_with_retry(
    label: str,
    callback: Callable[[], _T],
    *,
    timeout_seconds: float | None = None,
    attempts: int = 3,
    sleep: Callable[[float], None] = time.sleep,
) -> _T:
    """Retry a callback. Optionally wrap each attempt in a hard timeout."""
    last_error: Exception | None = None
    total = max(1, int(attempts))
    for attempt in range(1, total + 1):
        try:
            if timeout_seconds is None:
                return callback()
            return run_with_timeout(callback, timeout_seconds)
        except Exception as exc:
            last_error = exc
            logger.warning(
                "%s failed on attempt %s/%s: %s",
                label,
                attempt,
                total,
                exc,
            )
            if attempt < total:
                sleep(min(float(attempt), 2.0))
    if last_error is not None:
        raise last_error
    raise TimeoutError(f"{label} failed")
