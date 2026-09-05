"""Kill idle or over-time pipeline subprocesses, including Windows process trees."""

from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Callable, Sequence

STALL_EXIT_CODE = 124


def latest_log_mtime(paths: Sequence[Path]) -> float | None:
    """Newest mtime among existing log files, or None if none exist yet."""
    mtimes = [path.stat().st_mtime for path in paths if path.is_file()]
    return max(mtimes) if mtimes else None


def is_stalled(
    *,
    started: float,
    last_log: float | None,
    now: float,
    stall_seconds: float,
) -> bool:
    """True when a paper has produced no log activity for ``stall_seconds``."""
    if stall_seconds <= 0:
        return False
    reference = last_log if last_log is not None else started
    return (now - reference) >= stall_seconds


def kill_process_tree(pid: int) -> None:
    """Kill ``pid`` and its descendants. On Windows this uses ``taskkill /T``."""
    if pid <= 0:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            check=False,
            capture_output=True,
            text=True,
        )
        return
    try:
        os.killpg(pid, signal.SIGKILL)
    except OSError:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass


def wait_process_with_stall(
    proc: subprocess.Popen[bytes] | subprocess.Popen[str],
    *,
    log_paths: Sequence[Path],
    started: float,
    stall_seconds: float,
    max_seconds: float = 0,
    poll_seconds: float = 15,
    now_fn: Callable[[], float] = time.time,
    sleep_fn: Callable[[float], None] = time.sleep,
    kill_fn: Callable[[int], None] = kill_process_tree,
    log_mtime_fn: Callable[[Sequence[Path]], float | None] = latest_log_mtime,
) -> int:
    """Wait for ``proc``, but kill it if logs go silent or the wall clock expires."""
    while True:
        code = proc.poll()
        if code is not None:
            return code
        now = now_fn()
        if max_seconds > 0 and (now - started) >= max_seconds:
            kill_fn(proc.pid)
            try:
                proc.wait(timeout=30)
            except Exception:
                pass
            return STALL_EXIT_CODE
        last_log = log_mtime_fn(log_paths)
        if is_stalled(
            started=started,
            last_log=last_log,
            now=now,
            stall_seconds=stall_seconds,
        ):
            kill_fn(proc.pid)
            try:
                proc.wait(timeout=30)
            except Exception:
                pass
            return STALL_EXIT_CODE
        sleep_fn(poll_seconds if stall_seconds >= 60 else min(poll_seconds, 0.05))


def run_subprocess_timeout(
    cmd: Sequence[str],
    *,
    cwd: str,
    env: dict[str, str] | None,
    timeout_seconds: float,
) -> int:
    """Run ``cmd`` and kill the process tree if it exceeds ``timeout_seconds``."""
    popen_kwargs: dict[str, object] = {
        "cwd": cwd,
        "env": env,
    }
    if os.name != "nt":
        popen_kwargs["start_new_session"] = True
    proc = subprocess.Popen(list(cmd), **popen_kwargs)
    if timeout_seconds <= 0:
        return int(proc.wait())
    try:
        return int(proc.wait(timeout=timeout_seconds))
    except subprocess.TimeoutExpired:
        kill_process_tree(proc.pid)
        try:
            proc.wait(timeout=30)
        except Exception:
            pass
        return STALL_EXIT_CODE
