"""Tests for the mechanical boundary around pure-LLM artifact editing."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.agents.scripts_and_prompts_generation import unified_diff_editor
from src.agents.scripts_and_prompts_generation.level1_code_repair import LLMJsonResult
from src.agents.scripts_and_prompts_generation.structured_prompt_editor import (
    apply_structured_edits,
)
from src.agents.scripts_and_prompts_generation.unified_diff_editor import (
    apply_llm_unified_diff,
)


def test_unified_diff_applies_only_to_allowed_targets(tmp_path: Path) -> None:
    prompt = tmp_path / "prompts" / "onto" / "ITER.md"
    script = tmp_path / "scripts" / "onto" / "main.py"
    prompt.parent.mkdir(parents=True)
    script.parent.mkdir(parents=True)
    prompt.write_text("before\n", encoding="utf-8")
    script.write_text("VALUE = 1\n", encoding="utf-8")

    report = apply_llm_unified_diff(
        output_root=tmp_path,
        targets=[prompt],
        patch_unified_diff=(
            "diff --git a/prompts/onto/ITER.md b/prompts/onto/ITER.md\n"
            "--- a/prompts/onto/ITER.md\n"
            "+++ b/prompts/onto/ITER.md\n"
            "@@ -1 +1 @@\n"
            "-before\n"
            "+after\n"
        ),
    )

    assert report["ok"], report
    assert prompt.read_text(encoding="utf-8") == "after\n"
    assert script.read_text(encoding="utf-8") == "VALUE = 1\n"


def test_unified_diff_applies_lf_patch_to_crlf_target(tmp_path: Path) -> None:
    target = tmp_path / "scripts" / "onto" / "main.py"
    target.parent.mkdir(parents=True)
    target.write_bytes(b'VALUE = 1\r\nNAME = "old"\r\n')

    report = apply_llm_unified_diff(
        output_root=tmp_path,
        targets=[target],
        patch_unified_diff=(
            "diff --git a/scripts/onto/main.py b/scripts/onto/main.py\n"
            "--- a/scripts/onto/main.py\n"
            "+++ b/scripts/onto/main.py\n"
            "@@ -1,2 +1,2 @@\n"
            " VALUE = 1\n"
            '-NAME = "old"\n'
            '+NAME = "new"\n'
        ),
    )

    assert report["ok"], report
    assert 'NAME = "new"' in target.read_text(encoding="utf-8")


def test_full_file_lf_patch_applies_to_large_crlf_target(tmp_path: Path) -> None:
    target = tmp_path / "scripts" / "onto" / "base.py"
    target.parent.mkdir(parents=True)
    old_lines = [f"VALUE_{index} = {index}" for index in range(1, 301)]
    target.write_bytes(("\r\n".join(old_lines) + "\r\n").encode())
    new_lines = [*old_lines[:149], "VALUE_150 = 'changed'", *old_lines[150:]]
    patch_lines = [
        "diff --git a/scripts/onto/base.py b/scripts/onto/base.py",
        "--- a/scripts/onto/base.py",
        "+++ b/scripts/onto/base.py",
        "@@ -1,300 +1,300 @@",
        *(f"-{line}" for line in old_lines),
        *(f"+{line}" for line in new_lines),
    ]

    report = apply_llm_unified_diff(
        output_root=tmp_path,
        targets=[target],
        patch_unified_diff="\n".join(patch_lines) + "\n",
    )

    assert report["ok"], report
    assert "VALUE_150 = 'changed'" in target.read_text(encoding="utf-8")


def test_unified_diff_rejects_unauthorised_target_without_partial_write(
    tmp_path: Path,
) -> None:
    prompt = tmp_path / "prompts" / "onto" / "ITER.md"
    script = tmp_path / "scripts" / "onto" / "main.py"
    prompt.parent.mkdir(parents=True)
    script.parent.mkdir(parents=True)
    prompt.write_text("before\n", encoding="utf-8")
    script.write_text("VALUE = 1\n", encoding="utf-8")

    report = apply_llm_unified_diff(
        output_root=tmp_path,
        targets=[prompt],
        patch_unified_diff=(
            "diff --git a/scripts/onto/main.py b/scripts/onto/main.py\n"
            "--- a/scripts/onto/main.py\n"
            "+++ b/scripts/onto/main.py\n"
            "@@ -1 +1 @@\n"
            "-VALUE = 1\n"
            "+VALUE = 2\n"
        ),
    )

    assert not report["ok"]
    assert report["failures"] == [
        "unauthorised_patch_targets:scripts/onto/main.py"
    ]
    assert prompt.read_text(encoding="utf-8") == "before\n"
    assert script.read_text(encoding="utf-8") == "VALUE = 1\n"


def test_unified_diff_enforces_changed_file_limit(tmp_path: Path) -> None:
    first = tmp_path / "scripts" / "onto" / "first.py"
    second = tmp_path / "scripts" / "onto" / "second.py"
    first.parent.mkdir(parents=True)
    first.write_text("A = 1\n", encoding="utf-8")
    second.write_text("B = 1\n", encoding="utf-8")

    report = apply_llm_unified_diff(
        output_root=tmp_path,
        targets=[first, second],
        max_changed_files=1,
        patch_unified_diff=(
            "--- a/scripts/onto/first.py\n+++ b/scripts/onto/first.py\n"
            "@@ -1 +1 @@\n-A = 1\n+A = 2\n"
            "--- a/scripts/onto/second.py\n+++ b/scripts/onto/second.py\n"
            "@@ -1 +1 @@\n-B = 1\n+B = 2\n"
        ),
    )

    assert not report["ok"]
    assert report["failures"] == ["patch_target_limit_exceeded:2>1"]
    assert first.read_text(encoding="utf-8") == "A = 1\n"
    assert second.read_text(encoding="utf-8") == "B = 1\n"


def test_llm_editor_rejects_oversized_target_inventory_before_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    targets = []
    for name in ("one.py", "two.py"):
        path = tmp_path / name
        path.write_text("VALUE = 1\n", encoding="utf-8")
        targets.append(path)
    monkeypatch.setattr(
        unified_diff_editor,
        "invoke_json",
        lambda *args, **kwargs: pytest.fail("model must not be called"),
    )

    report = unified_diff_editor.run_llm_unified_diff_editor(
        model_name="test",
        output_root=tmp_path,
        targets=targets,
        task_prompt="repair",
        max_targets=1,
    )

    assert not report["ok"]
    assert report["failures"] == ["editor_target_limit_exceeded:2>1"]


def test_rejected_patch_is_retained_for_audit(tmp_path: Path) -> None:
    target = tmp_path / "prompts" / "onto" / "ITER.md"
    target.parent.mkdir(parents=True)
    target.write_text("", encoding="utf-8")
    rejected = "this is not a unified diff"

    report = apply_llm_unified_diff(
        output_root=tmp_path,
        targets=[target],
        patch_unified_diff=rejected,
    )

    assert not report["ok"]
    assert report["rejected_patch_unified_diff"] == rejected


def test_prompt_editor_compatibility_protocol_is_unified_diff(tmp_path: Path) -> None:
    target = tmp_path / "prompts" / "onto" / "ITER.md"
    target.parent.mkdir(parents=True)
    target.write_text("old rule\n", encoding="utf-8")

    report = apply_structured_edits(
        output_root=tmp_path,
        targets=[target],
        response={
            "patch_unified_diff": (
                "diff --git a/prompts/onto/ITER.md b/prompts/onto/ITER.md\n"
                "--- a/prompts/onto/ITER.md\n"
                "+++ b/prompts/onto/ITER.md\n"
                "@@ -1 +1 @@\n"
                "-old rule\n"
                "+new general rule\n"
            )
        },
    )

    assert report["ok"], report
    assert report["backend"] == "pure_llm_unified_diff"
    assert target.read_text(encoding="utf-8") == "new general rule\n"


@pytest.mark.parametrize(
    "header,expected",
    [
        (
            "diff --git a/../outside.py b/../outside.py\n"
            "--- a/../outside.py\n+++ b/../outside.py\n",
            "Unsafe patch path",
        ),
        (
            "diff --git a/C:/outside.py b/C:/outside.py\n"
            "--- a/C:/outside.py\n+++ b/C:/outside.py\n",
            "Absolute patch path",
        ),
        (
            "diff --git a/prompts/onto/NEW.md b/prompts/onto/NEW.md\n"
            "new file mode 100644\n--- /dev/null\n+++ b/prompts/onto/NEW.md\n",
            "create/delete/rename/mode/binary",
        ),
    ],
)
def test_unified_diff_rejects_unsafe_operations(
    tmp_path: Path, header: str, expected: str
) -> None:
    target = tmp_path / "prompts" / "onto" / "ITER.md"
    target.parent.mkdir(parents=True)
    target.write_text("before\n", encoding="utf-8")

    report = apply_llm_unified_diff(
        output_root=tmp_path,
        targets=[target],
        patch_unified_diff=header + "@@ -0,0 +1 @@\n+unsafe\n",
    )

    assert not report["ok"]
    assert expected in report["failures"][0]
    assert target.read_text(encoding="utf-8") == "before\n"


def test_unified_diff_rejects_nontrivial_hunk_without_context(tmp_path: Path) -> None:
    target = tmp_path / "scripts" / "onto" / "main.py"
    target.parent.mkdir(parents=True)
    target.write_text("A = 1\nB = 2\nC = 3\nD = 4\n", encoding="utf-8")

    report = apply_llm_unified_diff(
        output_root=tmp_path,
        targets=[target],
        patch_unified_diff=(
            "--- a/scripts/onto/main.py\n"
            "+++ b/scripts/onto/main.py\n"
            "@@ -1,3 +1,3 @@\n"
            "-A = 1\n-B = 2\n-C = 3\n"
            "+A = 2\n+B = 3\n+C = 4\n"
        ),
    )

    assert not report["ok"]
    assert report["failures"] == [
        "unified_diff_hunk_requires_context:hunk=1:found=0:required=1"
    ]
    assert target.read_text(encoding="utf-8") == "A = 1\nB = 2\nC = 3\nD = 4\n"


def test_git_apply_failure_reports_non_verbatim_hunk_context(tmp_path: Path) -> None:
    target = tmp_path / "scripts" / "onto" / "main.py"
    target.parent.mkdir(parents=True)
    target.write_text(
        "def export_memory():\n    return graph.serialize(format=\"turtle\")\n",
        encoding="utf-8",
    )

    report = apply_llm_unified_diff(
        output_root=tmp_path,
        targets=[target],
        patch_unified_diff=(
            "--- a/scripts/onto/main.py\n"
            "+++ b/scripts/onto/main.py\n"
            "@@ -1,2 +1,2 @@\n"
            " def export_memory():\n"
            '-    return graph.serialize(format="ttl")\n'
            "+    return serialize_turtle(graph)\n"
        ),
    )

    assert not report["ok"]
    assert report["failures"][0].startswith("git_apply_check_failed:")
    assert any(
        failure.startswith("hunk_old_context_not_verbatim:")
        for failure in report["failures"]
    )


def test_unique_exact_old_side_rebases_hunk_coordinate(tmp_path: Path) -> None:
    target = tmp_path / "scripts" / "onto" / "main.py"
    target.parent.mkdir(parents=True)
    target.write_text("A = 1\nB = 2\nC = 3\n", encoding="utf-8")

    report = apply_llm_unified_diff(
        output_root=tmp_path,
        targets=[target],
        patch_unified_diff=(
            "--- a/scripts/onto/main.py\n"
            "+++ b/scripts/onto/main.py\n"
            "@@ -3,2 +3,2 @@\n"
            " B = 2\n"
            "-C = 3\n"
            "+C = 4\n"
        ),
    )

    assert report["ok"], report
    assert target.read_text(encoding="utf-8") == "A = 1\nB = 2\nC = 4\n"
    assert report["coordinate_rebases"] == [
        {
            "path": "scripts/onto/main.py",
            "original_header": "@@ -3,2 +3,2 @@",
            "rebased_header": "@@ -2,2 +2,2 @@",
            "reason": "complete old-side sequence has one exact location",
        }
    ]


def test_repeated_old_side_is_not_rebased_ambiguously(tmp_path: Path) -> None:
    target = tmp_path / "scripts" / "onto" / "main.py"
    target.parent.mkdir(parents=True)
    target.write_text("A = 1\nA = 1\nA = 1\n", encoding="utf-8")

    report = apply_llm_unified_diff(
        output_root=tmp_path,
        targets=[target],
        patch_unified_diff=(
            "--- a/scripts/onto/main.py\n"
            "+++ b/scripts/onto/main.py\n"
            "@@ -4 +4 @@\n"
            "-A = 1\n"
            "+A = 2\n"
        ),
    )

    assert not report["ok"]
    assert report["failures"][0].startswith("ambiguous_hunk_coordinate_rebase:")
    assert target.read_text(encoding="utf-8") == "A = 1\nA = 1\nA = 1\n"


def test_unified_diff_rejects_apply_patch_residue(tmp_path: Path) -> None:
    target = tmp_path / "scripts" / "onto" / "main.py"
    target.parent.mkdir(parents=True)
    target.write_text("A = 1\n", encoding="utf-8")

    report = apply_llm_unified_diff(
        output_root=tmp_path,
        targets=[target],
        patch_unified_diff=(
            "--- a/scripts/onto/main.py\n"
            "+++ b/scripts/onto/main.py\n"
            "@@ -1 +1 @@\n-A = 1\n+A = 2\n"
            "*** End Patch"
        ),
    )

    assert not report["ok"]
    assert report["failures"] == [
        "unified_diff_contains_non_diff_residue:*** End Patch"
    ]
    assert target.read_text(encoding="utf-8") == "A = 1\n"


def test_llm_editor_uses_one_model_attempt_per_orchestrator_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "prompts" / "onto" / "ITER.md"
    target.parent.mkdir(parents=True)
    target.write_text("before\n", encoding="utf-8")
    calls: list[dict[str, object]] = []
    sleeps: list[float] = []

    def fake_invoke(
        model: str,
        prompt: str,
        *,
        timeout_seconds: int | None = None,
        max_attempts: int = 3,
        provider_max_retries: int | None = None,
    ) -> LLMJsonResult:
        calls.append(
            {
                "model": model,
                "timeout_seconds": timeout_seconds,
                "max_attempts": max_attempts,
                "provider_max_retries": provider_max_retries,
            }
        )
        return LLMJsonResult(
            data={"patch_unified_diff": ""},
            elapsed_seconds=0.0,
            token_usage={},
        )

    monkeypatch.setattr(unified_diff_editor, "invoke_json", fake_invoke)
    monkeypatch.setattr(unified_diff_editor.time, "sleep", sleeps.append)
    report = unified_diff_editor.run_llm_unified_diff_editor(
        model_name="test-model",
        output_root=tmp_path,
        targets=[target],
        task_prompt="Make a change.",
        max_attempts=2,
    )

    assert not report["ok"]
    assert len(calls) == 2
    assert all(call["max_attempts"] == 1 for call in calls)
    assert all(call["provider_max_retries"] == 0 for call in calls)
    assert sleeps == [5]


def test_validation_failure_rolls_back_applied_patch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "prompts" / "onto" / "ITER.md"
    target.parent.mkdir(parents=True)
    target.write_text("before\n", encoding="utf-8")
    existing_patch = tmp_path / ".llm_candidate.patch"
    existing_patch.write_text("must survive\n", encoding="utf-8")

    def fake_invoke(
        model: str,
        prompt: str,
        *,
        timeout_seconds: int | None = None,
        max_attempts: int = 3,
        provider_max_retries: int | None = None,
    ) -> LLMJsonResult:
        return LLMJsonResult(
            data={
                "patch_unified_diff": (
                    "diff --git a/prompts/onto/ITER.md b/prompts/onto/ITER.md\n"
                    "--- a/prompts/onto/ITER.md\n"
                    "+++ b/prompts/onto/ITER.md\n"
                    "@@ -1 +1 @@\n"
                    "-before\n"
                    "+invalid candidate\n"
                )
            },
            elapsed_seconds=0.0,
            token_usage={},
        )

    monkeypatch.setattr(unified_diff_editor, "invoke_json", fake_invoke)
    report = unified_diff_editor.run_llm_unified_diff_editor(
        model_name="test-model",
        output_root=tmp_path,
        targets=[target],
        task_prompt="Make a change.",
        max_attempts=1,
        validate=lambda: {"ok": False, "failures": ["contract_failed"]},
    )

    assert not report["ok"]
    assert target.read_text(encoding="utf-8") == "before\n"
    assert existing_patch.read_text(encoding="utf-8") == "must survive\n"
    assert not list(tmp_path.glob(".llm_candidate_*.patch"))


def test_validation_exception_rolls_back_all_changed_targets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = tmp_path / "scripts" / "onto" / "first.py"
    second = tmp_path / "scripts" / "onto" / "second.py"
    first.parent.mkdir(parents=True)
    first.write_text("FIRST = 1\n", encoding="utf-8")
    second.write_text("SECOND = 1\n", encoding="utf-8")

    def fake_invoke(*args, **kwargs):
        return LLMJsonResult(
            data={
                "patch_unified_diff": (
                    "diff --git a/scripts/onto/first.py b/scripts/onto/first.py\n"
                    "--- a/scripts/onto/first.py\n"
                    "+++ b/scripts/onto/first.py\n"
                    "@@ -1 +1 @@\n"
                    "-FIRST = 1\n"
                    "+FIRST = 2\n"
                    "diff --git a/scripts/onto/second.py b/scripts/onto/second.py\n"
                    "--- a/scripts/onto/second.py\n"
                    "+++ b/scripts/onto/second.py\n"
                    "@@ -1 +1 @@\n"
                    "-SECOND = 1\n"
                    "+SECOND = 2\n"
                )
            },
            elapsed_seconds=0.0,
            token_usage={},
        )

    monkeypatch.setattr(unified_diff_editor, "invoke_json", fake_invoke)
    report = unified_diff_editor.run_llm_unified_diff_editor(
        model_name="test-model",
        output_root=tmp_path,
        targets=[first, second],
        task_prompt="Change both.",
        max_attempts=1,
        validate=lambda: (_ for _ in ()).throw(RuntimeError("validation crashed")),
    )

    assert not report["ok"]
    assert first.read_text(encoding="utf-8") == "FIRST = 1\n"
    assert second.read_text(encoding="utf-8") == "SECOND = 1\n"
