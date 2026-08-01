from __future__ import annotations

from pathlib import Path

import pytest

from src.agents.scripts_and_prompts_generation.content_diagnosis import (
    repair_artifact_inventory,
    validate_repair_diagnosis,
)


def _inventory(tmp_path: Path) -> list[dict[str, object]]:
    prompts = tmp_path / "prompts"
    scripts = tmp_path / "scripts"
    traces = tmp_path / "traces"
    prompts.mkdir()
    scripts.mkdir()
    traces.mkdir()
    (prompts / "extract.md").write_text("Extract.", encoding="utf-8")
    (scripts / "main.py").write_text("def run(): pass\n", encoding="utf-8")
    trace = traces / "attempt.json"
    trace.write_text('{"tool_calls":[]}', encoding="utf-8")
    return repair_artifact_inventory(
        prompts_dir=prompts,
        scripts_dir=scripts,
        evidence_paths=[trace],
    )


def test_mixed_diagnosis_selects_prompt_and_script_from_inventory(
    tmp_path: Path,
) -> None:
    inventory = _inventory(tmp_path)
    diagnosis = validate_repair_diagnosis(
        {
            "status": "mixed",
            "repair_kind": "mixed",
            "target_artifacts": ["extract.md", "main.py"],
            "causal_findings": [{"cause": "shared contract gap"}],
        },
        inventory,
    )

    assert {Path(path).name for path in diagnosis["target_artifacts"]} == {
        "extract.md",
        "main.py",
    }


def test_diagnosis_cannot_edit_runtime_evidence(tmp_path: Path) -> None:
    inventory = _inventory(tmp_path)

    with pytest.raises(ValueError, match="read-only evidence"):
        validate_repair_diagnosis(
            {
                "status": "script_actionable",
                "repair_kind": "script",
                "target_artifacts": ["attempt.json"],
                "causal_findings": [{"cause": "trace evidence"}],
            },
            inventory,
        )
