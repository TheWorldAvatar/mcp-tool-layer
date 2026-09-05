"""Atomic file replace with a short retry for transient Windows destination locks."""

from __future__ import annotations

import os
import time
from typing import Union

PathLike = Union[str, os.PathLike[str]]


def is_transient_replace_error(exc: BaseException) -> bool:
    if isinstance(exc, PermissionError):
        return True
    if getattr(exc, "winerror", None) == 5:
        return True
    return getattr(exc, "errno", None) == 13


def replace_with_retry(
    source: PathLike,
    destination: PathLike,
    *,
    attempts: int = 20,
    base_sleep: float = 0.1,
) -> None:
    """Replace destination atomically, waiting out brief reader locks."""
    last_error: BaseException | None = None
    for attempt in range(attempts):
        try:
            os.replace(source, destination)
            return
        except OSError as exc:
            last_error = exc
            if not is_transient_replace_error(exc) or attempt == attempts - 1:
                raise
            time.sleep(base_sleep * (attempt + 1))
    if last_error is not None:
        raise last_error
