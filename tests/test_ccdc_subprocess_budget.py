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
    kwargs_list: list[dict] = []

    def fake_run(command, **kwargs):
        calls.append(command)
        kwargs_list.append(kwargs)
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
    assert kwargs_list[0].get("stdin") is subprocess.DEVNULL
    assert "cmd.exe" not in " ".join(calls[0]).lower()


def test_parenthetical_nickname_hits_hardcoded_without_csd(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        wsl_ccdc,
        "_run_csd_windows_ccdc_safe",
        lambda _args: (_ for _ in ()).throw(
            AssertionError("hardcoded hit must not start licensed CSD subprocess")
        ),
    )
    hits = wsl_ccdc.search_ccdc_by_mop_name(
        "{[Cp3Zr3mu3-O(mu2-OH)3]4(BTC)4}4+ (ZrT-2)"
    )
    assert hits[0][1] == "950331"


def test_unknown_name_uses_exact_csd_search(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    def fake_safe(args):
        calls.append(list(args))
        return 0, '[["UREMAH", "1479717"]]', ""

    monkeypatch.setattr(wsl_ccdc, "_lookup_hardcoded_ccdc", lambda _name: [])
    monkeypatch.setattr(wsl_ccdc, "_run_csd_windows_ccdc_safe", fake_safe)
    assert wsl_ccdc.search_ccdc_by_mop_name("VMOP-11", exact=False) == [
        ("UREMAH", "1479717")
    ]
    assert calls == [["search", "--name", "VMOP-11", "--exact"]]


def test_unknown_name_empty_csd_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(wsl_ccdc, "_lookup_hardcoded_ccdc", lambda _name: [])
    monkeypatch.setattr(
        wsl_ccdc, "_run_csd_windows_ccdc_safe", lambda _args: (0, "[]", "")
    )
    assert wsl_ccdc.search_ccdc_by_mop_name("VMOC-3·2pyrene", exact=False) == []


def test_unknown_doi_uses_live_csd(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    def fake_safe(args):
        calls.append(list(args))
        return (
            0,
            '[{"refcode": "GUZLEU", "chemical_name": "x", "formula": "",'
            ' "ccdc_number": "1439771", "doi": "10.1039/C5RA26357C"}]',
            "",
        )

    monkeypatch.setattr(wsl_ccdc, "_run_csd_windows_ccdc_safe", fake_safe)
    rows = wsl_ccdc.search_ccdc_by_doi("10.1039/C5RA26357C")
    assert calls == [["search_doi", "--doi", "10.1039/C5RA26357C"]]
    assert [row["ccdc_number"] for row in rows] == ["1439771"]


def test_live_csd_timeout_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wsl_ccdc, "_lookup_hardcoded_ccdc", lambda _name: [])
    monkeypatch.setattr(
        wsl_ccdc,
        "_run_csd_windows_ccdc_safe",
        lambda _args: (-1, "", "timeout after 180s"),
    )
    assert wsl_ccdc.search_ccdc_by_mop_name("VMOP-11") == []
    assert wsl_ccdc.search_ccdc_by_doi("10.1000/example") == []


def test_subprocess_env_injects_windows_roots(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wsl_ccdc.os, "environ", {"CSD_PYTHON_EXE": r"C:\csd311\python.exe"})
    monkeypatch.setattr(wsl_ccdc.os, "name", "nt")
    env = wsl_ccdc._subprocess_env()
    assert env["SYSTEMROOT"]
    assert env["PATH"]


def test_csd_lock_wait_timeout_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    from contextlib import contextmanager

    @contextmanager
    def fake_lock():
        raise TimeoutError("timed out waiting for CSD lock")
        yield

    monkeypatch.setattr(wsl_ccdc, "_exclusive_csd_lock", fake_lock)
    monkeypatch.setattr(wsl_ccdc, "resolve_csd_python_exe", lambda: "python")
    assert wsl_ccdc._run_csd_windows_ccdc_safe(["search", "--name", "VMOP-11"]) == (
        -1,
        "",
        "timed out waiting for CSD lock",
    )
