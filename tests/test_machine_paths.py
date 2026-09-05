from pathlib import Path

import pytest

from src.pipelines.utils.machine_paths import (
    configured_runtime_root,
    is_safe_external_runtime,
    resolve_scenario_runtime,
)
from src.pipelines.utils.runtime_cleanup import prepare_pipeline_runtime


def test_env_runtime_root_wins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TWA_RUNTIME_ROOT", str(tmp_path / "t"))
    monkeypatch.delenv("TWA_MACHINE_CONFIG", raising=False)
    assert configured_runtime_root(tmp_path) == tmp_path / "t"


def test_user_and_repo_config_are_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    (home / ".twa").mkdir(parents=True)
    repo.mkdir()
    (home / ".twa" / "config.json").write_text(
        '{"runtime_root": "C:/from-user"}', encoding="utf-8"
    )
    (repo / "twa.local.json").write_text(
        '{"runtime_root": "C:/from-repo"}', encoding="utf-8"
    )
    monkeypatch.delenv("TWA_RUNTIME_ROOT", raising=False)
    monkeypatch.delenv("TWA_MACHINE_CONFIG", raising=False)
    monkeypatch.setattr(
        "src.pipelines.utils.machine_paths.USER_CONFIG_PATH",
        home / ".twa" / "config.json",
    )

    assert configured_runtime_root(repo) == Path("C:/from-user")


def test_new_run_uses_short_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    run_dir = repo / "scenarios" / "mops" / "runs" / "20260822_eval30_ext30"
    monkeypatch.setenv("TWA_RUNTIME_ROOT", str(tmp_path / "t"))
    runtime = resolve_scenario_runtime(
        repo=repo,
        run_dir=run_dir,
        run_id="20260822_eval30_ext30",
        configured_data_dir=run_dir / "runtime",
    )
    assert runtime == tmp_path / "t" / "20260822_eval30_ext30"
    assert len(str(runtime)) < len(str(run_dir / "runtime"))


def test_populated_in_repo_runtime_is_not_orphaned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    run_dir = repo / "scenarios" / "mops" / "runs" / "20260822_eval30_ext30"
    existing = run_dir / "runtime"
    (existing / "a014d993").mkdir(parents=True)
    monkeypatch.setenv("TWA_RUNTIME_ROOT", str(tmp_path / "t"))

    runtime = resolve_scenario_runtime(
        repo=repo,
        run_dir=run_dir,
        run_id="20260822_eval30_ext30",
        configured_data_dir=existing,
    )
    assert runtime == existing


def test_cleanup_allows_machine_runtime_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    root = tmp_path / "t"
    runtime = root / "20260822_eval30_ext30"
    (runtime / "central_memory").mkdir(parents=True)
    (runtime / "central_memory" / "stale.ttl").write_text("x", encoding="utf-8")
    monkeypatch.setenv("TWA_RUNTIME_ROOT", str(root))

    prepared = prepare_pipeline_runtime(
        data_dir=runtime,
        repository_root=repo,
        config_path=repo / "pipeline.json",
        selected_hashes=["a014d993"],
        resume_existing_runtime=False,
    )

    assert prepared == runtime.resolve()
    assert not (runtime / "central_memory").exists()


def test_cleanup_refuses_arbitrary_external_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    monkeypatch.setenv("TWA_RUNTIME_ROOT", str(tmp_path / "t"))
    target = tmp_path / "not-the-runtime-root" / "papers"
    target.mkdir(parents=True)
    sentinel = target / "must-survive.txt"
    sentinel.write_text("safe", encoding="utf-8")

    with pytest.raises(ValueError, match="refusing to delete|runtime_root"):
        prepare_pipeline_runtime(
            data_dir=target,
            repository_root=repo,
            config_path=repo / "pipeline.json",
            selected_hashes=None,
            resume_existing_runtime=False,
        )
    assert sentinel.read_text(encoding="utf-8") == "safe"


def test_external_runtime_must_be_exactly_one_run_id(
    tmp_path: Path,
) -> None:
    root = tmp_path / "t"
    assert is_safe_external_runtime(root / "20260822_eval30_ext30", root)
    assert not is_safe_external_runtime(root, root)
    assert not is_safe_external_runtime(root / "20260822_eval30_ext30" / "nested", root)
