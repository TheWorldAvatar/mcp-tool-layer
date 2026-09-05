"""Hard timeout and durable event journal for generation-time LLM calls."""

from __future__ import annotations

import json
import os
import queue
import threading
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, TypeVar
from uuid import uuid4


T = TypeVar("T")
TERMINAL_EVENTS = frozenset(
    {"completed", "failed", "timed_out", "invalid_response", "interrupted"}
)
_JOURNAL_PATH: ContextVar[Path | None] = ContextVar(
    "generation_llm_journal_path", default=None
)
_WRITE_LOCK = threading.Lock()


class LLMInvocationTimeout(TimeoutError):
    """Raised when an LLM provider call exceeds the hard wall-clock limit."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _append(path: Path, event: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = (
        json.dumps(event, ensure_ascii=False, sort_keys=True, default=str) + "\n"
    ).encode("utf-8")
    with _WRITE_LOCK:
        descriptor = os.open(
            path,
            os.O_APPEND
            | os.O_CREAT
            | os.O_WRONLY
            | getattr(os, "O_BINARY", 0),
        )
        try:
            os.write(descriptor, line)
        finally:
            os.close(descriptor)


def configure_llm_invocation_journal(
    output_root: str | Path, *, recover: bool = True
) -> Path:
    """Select a run journal and close stale calls from a previous process."""
    path = Path(output_root).resolve() / "reports" / "llm_invocations.jsonl"
    _JOURNAL_PATH.set(path)
    if recover:
        recover_incomplete_invocations(path)
    return path


def current_journal_path() -> Path | None:
    return _JOURNAL_PATH.get()


def append_invocation_event(event: dict[str, Any]) -> None:
    path = current_journal_path()
    if path is None:
        return
    _append(path, {"timestamp": _utc_now(), "pid": os.getpid(), **event})


def recover_incomplete_invocations(path: str | Path) -> list[str]:
    journal = Path(path)
    if not journal.is_file():
        return []
    active: dict[str, dict[str, Any]] = {}
    for raw in journal.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            continue
        call_id = str(event.get("call_id") or "")
        kind = str(event.get("event") or "")
        if not call_id:
            continue
        if kind == "started":
            active[call_id] = event
        elif kind in TERMINAL_EVENTS:
            active.pop(call_id, None)
    for call_id, started in active.items():
        _append(
            journal,
            {
                "timestamp": _utc_now(),
                "pid": os.getpid(),
                "call_id": call_id,
                "event": "interrupted",
                "attempt": started.get("attempt"),
                "reason": "process_ended_without_terminal_llm_event",
            },
        )
    return sorted(active)


def new_call_id() -> str:
    return uuid4().hex


def invoke_with_hard_timeout(
    callback: Callable[[], T],
    *,
    timeout_seconds: float | None,
) -> T:
    """Run a provider call in a daemon thread with a hard caller-side deadline."""
    if timeout_seconds is None:
        return callback()
    if timeout_seconds <= 0:
        return callback()
    result_queue: queue.Queue[tuple[bool, Any]] = queue.Queue(maxsize=1)

    def run() -> None:
        try:
            result_queue.put((True, callback()))
        except BaseException as exc:  # propagate the provider exception unchanged
            result_queue.put((False, exc))

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    worker.join(timeout_seconds)
    if worker.is_alive():
        raise LLMInvocationTimeout(
            f"LLM invocation exceeded {timeout_seconds:g} seconds"
        )
    ok, value = result_queue.get_nowait()
    if ok:
        return value
    raise value
