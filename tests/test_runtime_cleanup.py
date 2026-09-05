import json
from pathlib import Path

import pytest

from src.pipelines.utils.runtime_cleanup import (
    RUNTIME_MANIFEST,
    prepare_pipeline_runtime,
    validate_runtime_path_budget,
)


def _runtime(repo: Path) -> Path:
    return repo / "scenarios" / "mops" / "runs" / "clean-test" / "runtime"


def test_windows_runtime_budget_rejects_long_run_id(tmp_path: Path) -> None:
    runtime = (
        tmp_path
        / "scenarios"
        / "mops"
        / "runs"
        / ("very-long-run-name-" * 12)
        / "runtime"
    )

    with pytest.raises(ValueError, match="Shorten the run ID/path"):
        validate_runtime_path_budget(runtime, enforce_windows=True)


def test_windows_runtime_budget_accepts_short_run_id(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)

    assert validate_runtime_path_budget(
        runtime,
        enforce_windows=True,
        max_path_chars=len(str(runtime.resolve())) + 112,
    ) == runtime.resolve()


def test_fresh_start_removes_entire_runtime_including_shared_memory(
    tmp_path: Path,
) -> None:
    runtime = _runtime(tmp_path)
    (runtime / "central_memory").mkdir(parents=True)
    (runtime / "central_memory" / "ontosynthesis_reusable_entities.ttl").write_text(
        "stale", encoding="utf-8"
    )
    (runtime / "unselected-hash").mkdir()
    (runtime / "unselected-hash" / "stale.json").write_text("{}", encoding="utf-8")
    (runtime / "global_state.json").write_text("{}", encoding="utf-8")

    prepared = prepare_pipeline_runtime(
        data_dir=runtime,
        repository_root=tmp_path,
        config_path=tmp_path / "pipeline.json",
        selected_hashes=["selected-hash"],
        resume_existing_runtime=False,
    )

    assert prepared == runtime
    assert not (runtime / "central_memory").exists()
    assert not (runtime / "unselected-hash").exists()
    assert not (runtime / "global_state.json").exists()
    manifest = json.loads((runtime / RUNTIME_MANIFEST).read_text(encoding="utf-8"))
    assert manifest["mode"] == "fresh"
    assert manifest["selected_hashes"] == ["selected-hash"]
    assert "central_memory" in manifest["cleanup_scope"]


def test_resume_mode_preserves_shared_memory(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    stale = runtime / "central_memory" / "state.ttl"
    stale.parent.mkdir(parents=True)
    stale.write_text("preserve", encoding="utf-8")

    prepare_pipeline_runtime(
        data_dir=runtime,
        repository_root=tmp_path,
        config_path=tmp_path / "pipeline.json",
        selected_hashes=None,
        resume_existing_runtime=True,
    )

    assert stale.read_text(encoding="utf-8") == "preserve"


def test_fresh_start_reuses_only_conversion_markdown(tmp_path: Path) -> None:
    source = (
        tmp_path
        / "scenarios"
        / "mops"
        / "runs"
        / "source"
        / "runtime"
    )
    source_doi = source / "abcdef12"
    source_doi.mkdir(parents=True)
    (source_doi / "abcdef12.md").write_text("converted", encoding="utf-8")
    (source_doi / "abcdef12_text.md").write_text("text", encoding="utf-8")
    (source_doi / "abcdef12_stitched.md").write_text(
        "stitched", encoding="utf-8"
    )
    (source_doi / "top_entities.txt").write_text("stale", encoding="utf-8")
    (source_doi / "mcp_run").mkdir()
    (source_doi / "mcp_run" / "iter2_hints_x.txt").write_text(
        "stale", encoding="utf-8"
    )
    (source / "central_memory").mkdir()
    (source / "central_memory" / "state.ttl").write_text(
        "stale", encoding="utf-8"
    )
    target = _runtime(tmp_path)

    prepare_pipeline_runtime(
        data_dir=target,
        repository_root=tmp_path,
        config_path=tmp_path / "pipeline.json",
        selected_hashes=None,
        resume_existing_runtime=False,
        reuse_conversion_artifacts_from=source,
        reuse_stitched_markdown=True,
    )

    assert (target / "abcdef12" / "abcdef12.md").read_text(
        encoding="utf-8"
    ) == "converted"
    assert not (target / "abcdef12" / "top_entities.txt").exists()
    assert (target / "abcdef12" / "abcdef12_stitched.md").read_text(
        encoding="utf-8"
    ) == "stitched"
    assert not (target / "abcdef12" / "mcp_run").exists()
    assert not (target / "central_memory").exists()
    manifest = json.loads((target / RUNTIME_MANIFEST).read_text(encoding="utf-8"))
    assert manifest["reused_conversion_source"] == str(source.resolve())
    assert sorted(manifest["reused_conversion_artifacts"]) == [
        "abcdef12/abcdef12.md",
        "abcdef12/abcdef12_stitched.md",
        "abcdef12/abcdef12_text.md",
    ]


@pytest.mark.parametrize(
    "relative_path",
    [
        "data",
        "scenarios/mops/runtime",
        "scenarios/mops/runs/runtime",
        "scenarios/mops/runs/run-id/output",
    ],
)
def test_cleanup_refuses_non_scenario_runtime_paths(
    tmp_path: Path, relative_path: str
) -> None:
    target = tmp_path / relative_path
    target.mkdir(parents=True)
    sentinel = target / "must-survive.txt"
    sentinel.write_text("safe", encoding="utf-8")

    with pytest.raises(ValueError, match="refusing to delete"):
        prepare_pipeline_runtime(
            data_dir=target,
            repository_root=tmp_path,
            config_path=tmp_path / "pipeline.json",
            selected_hashes=None,
            resume_existing_runtime=False,
        )

    assert sentinel.read_text(encoding="utf-8") == "safe"
