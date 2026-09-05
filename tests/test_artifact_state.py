from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from src.agents.scripts_and_prompts_generation.artifact_state import (
    ArtifactStateStore,
)
from src.agents.scripts_and_prompts_generation.agentic_generation_runner import (
    _resumable_artifact_snapshots,
)


def test_artifact_state_persists_attempt_hash_and_validation(tmp_path: Path) -> None:
    artifact = tmp_path / "scripts" / "onto" / "main.py"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("value = 1\n", encoding="utf-8")

    store = ArtifactStateStore(tmp_path, "onto")
    store.initialize([artifact])
    store.transition(artifact, "generating")
    store.transition(
        artifact,
        "passed",
        validation={"ok": True, "stage_ok": True, "failures": []},
    )

    persisted = json.loads(store.path.read_text(encoding="utf-8"))
    record = persisted["artifacts"]["scripts/onto/main.py"]
    assert record["status"] == "passed"
    assert record["attempt"] == 1
    assert record["content_sha256"] == hashlib.sha256(
        artifact.read_bytes()
    ).hexdigest()
    assert record["validation"] == {
        "ok": True,
        "stage_ok": True,
        "failure_count": 0,
    }
    assert [item["status"] for item in record["history"]] == [
        "generating",
        "passed",
    ]
    assert not list(store.path.parent.glob("*.tmp"))


def test_artifact_state_recovers_in_progress_state_after_restart(
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "prompts" / "onto" / "ITER_1.md"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("prompt\n", encoding="utf-8")
    first = ArtifactStateStore(tmp_path, "onto")
    first.initialize([artifact])
    first.transition(artifact, "repairing")

    restarted = ArtifactStateStore(tmp_path, "onto")
    assert restarted.recover_interrupted() == ["prompts/onto/ITER_1.md"]
    record = restarted.snapshot()["artifacts"]["prompts/onto/ITER_1.md"]
    assert record["status"] == "interrupted"
    assert record["reason"] == "previous_process_ended_during_artifact"


def test_artifact_state_rejects_unknown_state(tmp_path: Path) -> None:
    artifact = tmp_path / "main.py"
    store = ArtifactStateStore(tmp_path, "onto")
    store.initialize([artifact])

    with pytest.raises(ValueError, match="Unsupported artifact state"):
        store.transition(artifact, "unknown")


def test_artifact_state_reinitializes_invalid_json(tmp_path: Path) -> None:
    path = tmp_path / "reports" / "onto" / "artifact_states.json"
    path.parent.mkdir(parents=True)
    path.write_text("{broken", encoding="utf-8")

    store = ArtifactStateStore(tmp_path, "onto")
    artifact = tmp_path / "main.py"
    store.initialize([artifact])

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert payload["artifacts"]["main.py"]["status"] == "pending"


def test_artifact_state_only_reuses_passed_bytes_with_matching_hash(
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "scripts" / "onto" / "main.py"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("accepted\n", encoding="utf-8")
    store = ArtifactStateStore(tmp_path, "onto")
    store.initialize([artifact])
    store.transition(artifact, "passed")

    assert store.is_matching_passed(artifact)
    assert store.should_preserve_existing(artifact)

    artifact.write_text("changed\n", encoding="utf-8")
    assert not store.is_matching_passed(artifact)
    assert not store.should_preserve_existing(artifact)


def test_artifact_state_preserves_nonempty_interrupted_work(tmp_path: Path) -> None:
    artifact = tmp_path / "scripts" / "onto" / "checks.py"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("partial progress\n", encoding="utf-8")
    store = ArtifactStateStore(tmp_path, "onto")
    store.initialize([artifact])
    store.transition(artifact, "repairing")

    restarted = ArtifactStateStore(tmp_path, "onto")
    restarted.recover_interrupted()

    assert restarted.record_for(artifact)["status"] == "interrupted"
    assert restarted.should_preserve_existing(artifact)


def test_runner_captures_resumable_bytes_before_scaffolding(tmp_path: Path) -> None:
    scripts = tmp_path / "scripts" / "onto"
    prompts = tmp_path / "prompts" / "onto"
    scripts.mkdir(parents=True)
    prompts.mkdir(parents=True)
    passed = scripts / "base.py"
    interrupted = scripts / "checks.py"
    pending = prompts / "ITER.md"
    passed.write_text("accepted\n", encoding="utf-8")
    interrupted.write_text("partial\n", encoding="utf-8")
    pending.write_text("untracked\n", encoding="utf-8")
    store = ArtifactStateStore(tmp_path, "onto")
    store.initialize([passed, interrupted, pending])
    store.transition(passed, "passed")
    store.transition(interrupted, "repairing")
    context = type(
        "Context",
        (),
        {
            "output_root": str(tmp_path),
            "scripts_dir": str(scripts),
            "prompts_dir": str(prompts),
            "ontology": type("Ontology", (), {"name": "onto"})(),
        },
    )()

    snapshots = _resumable_artifact_snapshots(context, set())

    assert snapshots == {
        passed: passed.read_bytes(),
        interrupted: interrupted.read_bytes(),
    }
