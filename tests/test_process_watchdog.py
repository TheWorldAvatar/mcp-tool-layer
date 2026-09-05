from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from src.pipelines.mop_derivation.derive import _env_timeout, _run_derivation_command
from src.pipelines.utils.process_watchdog import (
    STALL_EXIT_CODE,
    is_stalled,
    latest_log_mtime,
    run_subprocess_timeout,
    wait_process_with_stall,
)
from src.agents.mops.cbu_derivation.utils.organic_utils import _organic_agent_timeout_seconds


class _FakeProc:
    def __init__(self, codes: list[int | None]) -> None:
        self.pid = 4242
        self._codes = list(codes)
        self.waited = False

    def poll(self) -> int | None:
        if not self._codes:
            return None
        return self._codes.pop(0)

    def wait(self, timeout: float | None = None) -> int:
        self.waited = True
        return STALL_EXIT_CODE


def test_wait_process_with_stall_returns_when_process_exits() -> None:
    proc = _FakeProc([0])
    killed: list[int] = []
    code = wait_process_with_stall(
        proc,  # type: ignore[arg-type]
        log_paths=[],
        started=0.0,
        stall_seconds=900,
        now_fn=lambda: 10.0,
        sleep_fn=lambda _: None,
        kill_fn=killed.append,
    )
    assert code == 0
    assert killed == []


def test_wait_process_with_stall_kills_silent_process() -> None:
    proc = _FakeProc([None])
    killed: list[int] = []
    code = wait_process_with_stall(
        proc,  # type: ignore[arg-type]
        log_paths=[],
        started=0.0,
        stall_seconds=900,
        now_fn=lambda: 1000.0,
        sleep_fn=lambda _: None,
        kill_fn=killed.append,
        log_mtime_fn=lambda _: 50.0,
    )
    assert code == STALL_EXIT_CODE
    assert killed == [4242]
    assert proc.waited is True


def test_wait_process_with_stall_kills_at_max_seconds() -> None:
    proc = _FakeProc([None])
    killed: list[int] = []
    code = wait_process_with_stall(
        proc,  # type: ignore[arg-type]
        log_paths=[],
        started=0.0,
        stall_seconds=0,
        max_seconds=60,
        now_fn=lambda: 60.0,
        sleep_fn=lambda _: None,
        kill_fn=killed.append,
        log_mtime_fn=lambda _: 59.0,
    )
    assert code == STALL_EXIT_CODE
    assert killed == [4242]


def test_run_subprocess_timeout_kills_sleep(tmp_path: Path) -> None:
    started = time.time()
    code = run_subprocess_timeout(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        cwd=str(tmp_path),
        env=os.environ.copy(),
        timeout_seconds=1,
    )
    assert code == STALL_EXIT_CODE
    assert time.time() - started < 15


def test_env_timeout_and_organic_default(monkeypatch) -> None:
    monkeypatch.delenv("CBU_ORGANIC_TIMEOUT_SECONDS", raising=False)
    assert _env_timeout("CBU_ORGANIC_TIMEOUT_SECONDS", 1500) == 1500
    monkeypatch.setenv("CBU_ORGANIC_TIMEOUT_SECONDS", "12")
    assert _env_timeout("CBU_ORGANIC_TIMEOUT_SECONDS", 1500) == 12.0
    monkeypatch.delenv("ORGANIC_AGENT_TIMEOUT_SECONDS", raising=False)
    assert _organic_agent_timeout_seconds() == 720.0
    monkeypatch.setenv("ORGANIC_AGENT_TIMEOUT_SECONDS", "30")
    assert _organic_agent_timeout_seconds() == 30.0


def test_run_derivation_command_maps_timeout_to_called_process_error(monkeypatch) -> None:
    calls: list[float] = []

    def fake_run(*args, **kwargs) -> int:
        calls.append(kwargs["timeout_seconds"])
        return STALL_EXIT_CODE

    monkeypatch.setattr(
        "src.pipelines.mop_derivation.derive.run_subprocess_timeout",
        fake_run,
    )
    logger = type("L", (), {"info": lambda *a, **k: None})()
    try:
        _run_derivation_command(
            ["python", "-c", "pass"],
            cwd=".",
            env={},
            timeout_seconds=5,
            logger=logger,  # type: ignore[arg-type]
            label="organic CBU derivation",
        )
    except Exception as exc:
        assert getattr(exc, "returncode", None) == STALL_EXIT_CODE
    else:
        raise AssertionError("expected CalledProcessError")
    assert calls == [5]


def test_latest_log_mtime_and_is_stalled_helpers(tmp_path: Path) -> None:
    path = tmp_path / "w00.log"
    path.write_text("x")
    assert latest_log_mtime([tmp_path / "missing.log"]) is None
    assert latest_log_mtime([path]) == path.stat().st_mtime
    assert is_stalled(started=0.0, last_log=100.0, now=1000.0, stall_seconds=900) is True
    assert is_stalled(started=0.0, last_log=200.0, now=1000.0, stall_seconds=900) is False
