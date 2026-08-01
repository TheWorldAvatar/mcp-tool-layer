from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from src.agents.scripts_and_prompts_generation import exact_edit_editor
from src.agents.scripts_and_prompts_generation.exact_edit_editor import (
    apply_exact_edit_payload,
)
from src.agents.scripts_and_prompts_generation.level1_code_repair import LLMJsonResult


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _payload(path: str, digest: str, operations: list[dict[str, str]]) -> dict:
    return {
        "schema_version": "exact-edits.v1",
        "files": [
            {
                "path": path,
                "expected_sha256": digest,
                "operations": operations,
            }
        ],
        "summary": "test",
    }


def test_exact_edit_applies_unique_replacement_and_builds_audit_diff(
    tmp_path: Path,
) -> None:
    target = tmp_path / "scripts" / "onto" / "main.py"
    target.parent.mkdir(parents=True)
    target.write_text("A = 1\nB = 2\n", encoding="utf-8")
    payload = _payload(
        "scripts/onto/main.py",
        _digest(target),
        [
            {
                "edit_id": "edit-b",
                "kind": "replace_exact",
                "old_text": "B = 2",
                "new_text": "B = 3",
            }
        ],
    )

    report = apply_exact_edit_payload(
        output_root=tmp_path,
        targets=[target],
        edit_payload=payload,
    )

    assert report["ok"], report
    assert target.read_text(encoding="utf-8") == "A = 1\nB = 3\n"
    assert "--- a/scripts/onto/main.py" in report["patch_unified_diff"]
    assert "+B = 3" in report["patch_unified_diff"]
    assert report["files"][0]["before_sha256"] != report["files"][0]["after_sha256"]


@pytest.mark.parametrize(
    "content,old,code",
    [
        ("A = 1\n", "B = 2", "exact_edit_no_match"),
        ("A = 1\nA = 1\n", "A = 1", "exact_edit_ambiguous_match"),
    ],
)
def test_exact_edit_rejects_non_unique_old_text(
    tmp_path: Path, content: str, old: str, code: str
) -> None:
    target = tmp_path / "main.py"
    target.write_text(content, encoding="utf-8")
    payload = _payload(
        "main.py",
        _digest(target),
        [
            {
                "edit_id": "edit",
                "kind": "replace_exact",
                "old_text": old,
                "new_text": "changed",
            }
        ],
    )

    report = apply_exact_edit_payload(
        output_root=tmp_path,
        targets=[target],
        edit_payload=payload,
    )

    assert not report["ok"]
    assert report["failures"][0]["code"] == code
    assert target.read_text(encoding="utf-8") == content


def test_exact_edit_rejects_overlap_atomically(tmp_path: Path) -> None:
    target = tmp_path / "main.py"
    target.write_text("alpha beta gamma\n", encoding="utf-8")
    payload = _payload(
        "main.py",
        _digest(target),
        [
            {
                "edit_id": "wide",
                "kind": "replace_exact",
                "old_text": "alpha beta",
                "new_text": "one",
            },
            {
                "edit_id": "inner",
                "kind": "replace_exact",
                "old_text": "beta",
                "new_text": "two",
            },
        ],
    )

    report = apply_exact_edit_payload(
        output_root=tmp_path,
        targets=[target],
        edit_payload=payload,
    )

    assert not report["ok"]
    assert any(item["code"] == "exact_edit_overlap" for item in report["failures"])
    assert target.read_text(encoding="utf-8") == "alpha beta gamma\n"


def test_exact_edit_multi_file_validation_failure_restores_original_bytes(
    tmp_path: Path,
) -> None:
    first = tmp_path / "first.py"
    second = tmp_path / "second.py"
    first.write_bytes(b"A = 1\r\n")
    second.write_text("B = 1\n", encoding="utf-8")
    before_first = first.read_bytes()
    before_second = second.read_bytes()
    payload = {
        "schema_version": "exact-edits.v1",
        "files": [
            {
                "path": "first.py",
                "expected_sha256": _digest(first),
                "operations": [
                    {
                        "edit_id": "first",
                        "kind": "replace_exact",
                        "old_text": "A = 1",
                        "new_text": "A = 2",
                    }
                ],
            },
            {
                "path": "second.py",
                "expected_sha256": _digest(second),
                "operations": [
                    {
                        "edit_id": "second",
                        "kind": "replace_exact",
                        "old_text": "B = 1",
                        "new_text": "B = 2",
                    }
                ],
            },
        ],
    }

    report = apply_exact_edit_payload(
        output_root=tmp_path,
        targets=[first, second],
        edit_payload=payload,
        validate=lambda: {"ok": False, "failures": ["behavior rejected"]},
    )

    assert not report["ok"]
    assert report["failures"] == [{"code": "candidate_validation_failed"}]
    assert report["validation"]["failures"] == ["behavior rejected"]
    assert report["rollback_performed"]
    assert first.read_bytes() == before_first
    assert second.read_bytes() == before_second


def test_exact_edit_preserves_crlf_and_bom(tmp_path: Path) -> None:
    target = tmp_path / "main.py"
    target.write_bytes(b"\xef\xbb\xbfA = 1\r\nB = 2\r\n")
    payload = _payload(
        "main.py",
        _digest(target),
        [
            {
                "edit_id": "edit",
                "kind": "replace_exact",
                "old_text": "B = 2",
                "new_text": "B = 3",
            }
        ],
    )

    report = apply_exact_edit_payload(
        output_root=tmp_path,
        targets=[target],
        edit_payload=payload,
    )

    assert report["ok"], report
    assert target.read_bytes() == b"\xef\xbb\xbfA = 1\r\nB = 3\r\n"
    assert report["files"][0]["bom"]
    assert report["files"][0]["line_ending"] == "crlf"


def test_exact_edit_generates_empty_file_once(tmp_path: Path) -> None:
    target = tmp_path / "main.py"
    target.write_text("", encoding="utf-8")
    payload = _payload(
        "main.py",
        _digest(target),
        [
            {
                "edit_id": "generate",
                "kind": "replace_entire_file",
                "new_text": "VALUE = 1\n",
            }
        ],
    )

    report = apply_exact_edit_payload(
        output_root=tmp_path,
        targets=[target],
        edit_payload=payload,
    )

    assert report["ok"], report
    assert target.read_text(encoding="utf-8") == "VALUE = 1\n"


def test_exact_llm_retry_uses_original_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "main.py"
    target.write_text("VALUE = 1\n", encoding="utf-8")
    calls: list[str] = []
    responses = [
        {
            "schema_version": "exact-edits.v1",
            "files": [
                {
                    "path": "main.py",
                    "expected_sha256": _digest(target),
                    "operations": [
                        {
                            "edit_id": "bad",
                            "kind": "replace_exact",
                            "old_text": "missing",
                            "new_text": "VALUE = 2",
                        }
                    ],
                }
            ],
        },
        _payload(
            "main.py",
            _digest(target),
            [
                {
                    "edit_id": "good",
                    "kind": "replace_exact",
                    "old_text": "VALUE = 1",
                    "new_text": "VALUE = 2",
                }
            ],
        ),
    ]

    def fake_invoke(*args, **kwargs):
        calls.append(args[1])
        return LLMJsonResult(
            data=responses.pop(0),
            elapsed_seconds=0.0,
            token_usage={},
        )

    monkeypatch.setattr(exact_edit_editor, "invoke_json", fake_invoke)
    report = exact_edit_editor.run_llm_exact_edit_editor(
        model_name="test",
        output_root=tmp_path,
        targets=[target],
        task_prompt="repair",
        max_attempts=2,
    )

    assert report["ok"], report
    assert len(calls) == 2
    assert "exact_edit_no_match" in calls[1]
    assert target.read_text(encoding="utf-8") == "VALUE = 2\n"


def test_exact_llm_retry_reports_unauthorized_and_allowed_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "scripts" / "onto" / "checks.py"
    target.parent.mkdir(parents=True)
    target.write_text("VALUE = 1\n", encoding="utf-8")
    calls: list[str] = []
    responses = [
        _payload(
            "checks.py",
            _digest(target),
            [
                {
                    "edit_id": "wrong-path",
                    "kind": "replace_exact",
                    "old_text": "VALUE = 1",
                    "new_text": "VALUE = 2",
                }
            ],
        ),
        _payload(
            "scripts/onto/checks.py",
            _digest(target),
            [
                {
                    "edit_id": "right-path",
                    "kind": "replace_exact",
                    "old_text": "VALUE = 1",
                    "new_text": "VALUE = 2",
                }
            ],
        ),
    ]

    def fake_invoke(*args, **kwargs):
        calls.append(args[1])
        return LLMJsonResult(
            data=responses.pop(0),
            elapsed_seconds=0.0,
            token_usage={},
        )

    monkeypatch.setattr(exact_edit_editor, "invoke_json", fake_invoke)
    report = exact_edit_editor.run_llm_exact_edit_editor(
        model_name="test",
        output_root=tmp_path,
        targets=[target],
        task_prompt="repair",
        max_attempts=2,
    )

    assert report["ok"], report
    assert '"failure_class": "edit_protocol"' in calls[1]
    assert '"unauthorized_paths": ["checks.py"]' in calls[1]
    assert '"allowed_editable_paths": ["scripts/onto/checks.py"]' in calls[1]
    assert target.read_text(encoding="utf-8") == "VALUE = 2\n"


def test_exact_edit_failure_codes_are_finite_and_unknown_codes_are_normalized() -> None:
    assert exact_edit_editor._edit_failure("exact_edit_no_match") == {
        "code": "exact_edit_no_match"
    }
    assert exact_edit_editor._edit_failure("model_invented_problem") == {
        "code": "exact_edit_internal_error",
        "detail": "unregistered_failure_code:model_invented_problem",
    }
    assert all(
        spec["failure_class"] and spec["retry_hint"]
        for spec in exact_edit_editor.EDIT_FAILURE_SPECS.values()
    )


def test_exact_llm_progress_separates_edit_and_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "main.py"
    target.write_text("VALUE = 1\n", encoding="utf-8")
    progress: list[str] = []

    monkeypatch.setattr(
        exact_edit_editor,
        "invoke_json",
        lambda *_args, **_kwargs: LLMJsonResult(
            data=_payload(
                "main.py",
                _digest(target),
                [
                    {
                        "edit_id": "change",
                        "kind": "replace_exact",
                        "old_text": "VALUE = 1",
                        "new_text": "VALUE = 2",
                    }
                ],
            ),
            elapsed_seconds=0.0,
            token_usage={},
        ),
    )

    report = exact_edit_editor.run_llm_exact_edit_editor(
        model_name="test",
        output_root=tmp_path,
        targets=[target],
        task_prompt="repair",
        validate=lambda: {
            "ok": True,
            "failures": [],
            "observations": [
                {"stage": "slot", "status": "pass"},
                {"stage": "semantic", "status": "pass"},
            ],
        },
        progress=progress.append,
    )

    assert report["ok"], report
    assert any("phase=generate" in message for message in progress)
    assert any("phase=edit result=candidate-written" in message for message in progress)
    assert any(
        "phase=review validation=pass validation_gates=semantic:pass,slot:pass"
        in message
        for message in progress
    )
    assert any("phase=result ok=True committed=True" in message for message in progress)
