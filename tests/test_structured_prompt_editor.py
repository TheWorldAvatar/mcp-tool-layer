from __future__ import annotations

from pathlib import Path

from src.agents.scripts_and_prompts_generation import structured_prompt_editor
from src.agents.scripts_and_prompts_generation.structured_prompt_editor import (
    apply_structured_edits,
)


def _package(tmp_path: Path) -> tuple[Path, Path, Path]:
    prompt = tmp_path / "prompts" / "onto" / "ITER.md"
    script = tmp_path / "scripts" / "onto" / "main.py"
    prompt.parent.mkdir(parents=True)
    script.parent.mkdir(parents=True)
    prompt.write_text("Keep this.\nOld general rule.\n", encoding="utf-8")
    script.write_text("VALUE = 1\n", encoding="utf-8")
    return tmp_path, prompt, script


def test_structured_editor_applies_selected_exact_replacement(tmp_path: Path) -> None:
    root, prompt, script = _package(tmp_path)
    report = apply_structured_edits(
        output_root=root,
        targets=[prompt],
        response={
            "edits": [
                {
                    "path": prompt.as_posix(),
                    "replacements": [
                        {
                            "old": "Old general rule.",
                            "new": "New ontology-derived rule.",
                        }
                    ],
                }
            ]
        },
    )
    assert report["ok"], report
    assert "New ontology-derived rule." in prompt.read_text(encoding="utf-8")
    assert script.read_text(encoding="utf-8") == "VALUE = 1\n"


def test_structured_editor_rejects_outside_and_ambiguous_replacements(
    tmp_path: Path,
) -> None:
    root, prompt, _ = _package(tmp_path)
    outside = tmp_path.parent / "outside.md"
    report = apply_structured_edits(
        output_root=root,
        targets=[prompt],
        response={
            "edits": [
                {
                    "path": outside.as_posix(),
                    "replacements": [{"old": "x", "new": "y"}],
                }
            ]
        },
    )
    assert not report["ok"]
    assert any("outside_diagnosis" in item for item in report["failures"])

    prompt.write_text("duplicate\nduplicate\n", encoding="utf-8")
    ambiguous = apply_structured_edits(
        output_root=root,
        targets=[prompt],
        response={
            "edits": [
                {
                    "path": prompt.as_posix(),
                    "replacements": [{"old": "duplicate", "new": "changed"}],
                }
            ]
        },
    )
    assert not ambiguous["ok"]
    assert any("match_count" in item for item in ambiguous["failures"])


def test_proven_conflict_allows_minimal_non_additive_replacement(
    tmp_path: Path, monkeypatch
) -> None:
    root, prompt, _ = _package(tmp_path)
    captured = {}

    def fake_editor(**kwargs):
        captured.update(kwargs)
        return {"ok": True, "changed_files": [prompt.as_posix()]}

    monkeypatch.setattr(
        structured_prompt_editor, "run_llm_artifact_editor", fake_editor
    )

    structured_prompt_editor.run_structured_prompt_editor(
        model_name="test",
        output_root=root,
        targets=[prompt],
        diagnosis={"allow_conflict_replacement": True},
        contract={},
    )

    assert captured["additive_only"] is False
    assert "minimal conflict-replacement editing" in captured["task_prompt"]
