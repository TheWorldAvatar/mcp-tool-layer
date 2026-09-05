from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from src.mcp_servers.ccdc.operations import wsl_ccdc


def test_resolve_csd_python_exe_uses_csd_python_exe_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = tmp_path / "python.exe"
    fake.write_text("", encoding="utf-8")
    monkeypatch.setenv("CSD_PYTHON_EXE", str(fake))
    assert wsl_ccdc.resolve_csd_python_exe() == str(fake.resolve())


def test_run_csd_windows_ccdc_uses_hardcoded_interpreter_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = tmp_path / "csd311-python.exe"
    fake.write_text("", encoding="utf-8")
    monkeypatch.setattr(wsl_ccdc, "resolve_csd_python_exe", lambda: str(fake))
    monkeypatch.setattr(wsl_ccdc, "_ccdc_subprocess_timeout", lambda: 30.0)
    calls: list[list[str]] = []

    def fake_run(command, **_kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "[]", "")

    monkeypatch.setattr(wsl_ccdc.subprocess, "run", fake_run)
    result = wsl_ccdc._run_csd_windows_ccdc(["search", "--name", "VMOC-3"])
    assert result.returncode == 0
    assert len(calls) == 1
    assert calls[0][0] == str(fake)
    assert calls[0][1:4] == [
        "-m",
        "src.mcp_servers.ccdc.operations.windows_ccdc",
        "search",
    ]
    assert "cmd.exe" not in " ".join(calls[0]).lower()


def test_unknown_name_search_fails_closed_without_subprocess(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(wsl_ccdc, "_lookup_hardcoded_ccdc", lambda _name: [])
    monkeypatch.setattr(
        wsl_ccdc,
        "_run_csd_windows_ccdc_safe",
        lambda _args: (_ for _ in ()).throw(
            AssertionError("unknown name must not start licensed CSD subprocess")
        ),
    )
    assert wsl_ccdc.search_ccdc_by_mop_name("VMOC-3·2pyrene", exact=False) == []


def test_unknown_doi_search_fails_closed_without_subprocess(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        wsl_ccdc,
        "_run_csd_windows_ccdc_safe",
        lambda _args: (_ for _ in ()).throw(
            AssertionError("unknown DOI must not start licensed CSD subprocess")
        ),
    )
    assert wsl_ccdc.search_ccdc_by_doi("10.1000/example") == []
