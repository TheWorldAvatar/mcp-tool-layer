from __future__ import annotations

from pathlib import Path

from src.pipelines.utils.atomic_replace import replace_with_retry


def test_replace_with_retry_waits_out_permission_error(tmp_path: Path, monkeypatch) -> None:
    from src.pipelines.utils import atomic_replace

    source = tmp_path / "src.json"
    destination = tmp_path / "dst.json"
    source.write_text("new", encoding="utf-8")
    destination.write_text("old", encoding="utf-8")

    attempts = 0
    real_replace = atomic_replace.os.replace

    def flaky_replace(src, dst):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise PermissionError("destination temporarily open")
        return real_replace(src, dst)

    monkeypatch.setattr(atomic_replace.os, "replace", flaky_replace)
    monkeypatch.setattr(atomic_replace.time, "sleep", lambda _seconds: None)

    replace_with_retry(source, destination)

    assert attempts == 3
    assert destination.read_text(encoding="utf-8") == "new"
