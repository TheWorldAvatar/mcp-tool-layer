"""Shared retry/backoff for fragile corpus SPARQL fetches."""

from __future__ import annotations

import time
from typing import Callable, TypeVar

from mini_marie.marie.chemistry.limits import (
    WARM_MAX_RETRIES,
    WARM_RETRY_BACKOFF_BASE,
    WARM_RETRY_BACKOFF_MAX,
)

T = TypeVar("T")


def retry_corpus_fetch(label: str, fetch_fn: Callable[[], T]) -> T:
    last_exc: Exception | None = None
    for attempt in range(1, WARM_MAX_RETRIES + 1):
        try:
            return fetch_fn()
        except Exception as exc:
            last_exc = exc
            if attempt >= WARM_MAX_RETRIES:
                break
            delay = min(WARM_RETRY_BACKOFF_MAX, WARM_RETRY_BACKOFF_BASE * attempt)
            print(
                f"  {label} retry {attempt}/{WARM_MAX_RETRIES} after {delay:.0f}s: {exc}",
                flush=True,
            )
            time.sleep(delay)
    assert last_exc is not None
    raise last_exc
