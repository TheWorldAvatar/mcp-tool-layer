from __future__ import annotations

from pathlib import Path

from src.agents.scripts_and_prompts_generation import llm_artifact_editor


def test_artifact_editor_defaults_to_exact(monkeypatch, tmp_path: Path) -> None:
    target = tmp_path / "main.py"
    target.write_text("VALUE = 1\n", encoding="utf-8")
    calls = []

    def fake_exact(**kwargs):
        calls.append(("exact", kwargs))
        return {"ok": True, "failures": [], "changed_files": ["main.py"]}

    monkeypatch.setattr(llm_artifact_editor, "run_llm_exact_edit_editor", fake_exact)
    report = llm_artifact_editor.run_llm_artifact_editor(
        model_name="test",
        output_root=tmp_path,
        targets=[target],
        task_prompt="edit",
    )

    assert report["ok"]
    assert calls[0][0] == "exact"


def test_artifact_editor_supports_explicit_unified_fallback(
    monkeypatch, tmp_path: Path
) -> None:
    target = tmp_path / "main.py"
    target.write_text("VALUE = 1\n", encoding="utf-8")

    def fake_diff(**kwargs):
        return {"ok": False, "failures": ["git_apply_check_failed:bad hunk"]}

    monkeypatch.setattr(
        llm_artifact_editor, "run_llm_unified_diff_editor", fake_diff
    )
    report = llm_artifact_editor.run_llm_artifact_editor(
        model_name="test",
        output_root=tmp_path,
        targets=[target],
        task_prompt="edit",
        edit_backend="unified_diff",
    )

    assert not report["ok"]
    assert report["replay_protocol"] == "unified-diff.v1"
    assert report["failure_codes"] == ["git_apply_check_failed"]
