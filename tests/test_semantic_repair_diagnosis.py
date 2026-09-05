from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from src.agents.scripts_and_prompts_generation.content_diagnosis import (
    repair_artifact_inventory,
    validate_repair_diagnosis,
)
from src.agents.scripts_and_prompts_generation.agentic_generation_llm_agents import (
    run_content_diagnosis_agent_sync,
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
            "schema_version": "prompt-enhancement-diagnosis.v2",
            "status": "mixed",
            "repair_kind": "mixed",
            "summary": "prompt and runtime both violate the contract",
            "target_artifacts": ["extract.md", "main.py"],
            "dependency_order": ["extract.md", "main.py"],
            "must_preserve": [],
            "acceptance_evidence": ["both defects are independently evidenced"],
            "diagnostic_confidence": 0.9,
            "causal_findings": [
                {
                    "observation_ids": ["evidence.mixed"],
                    "source_path": "attempt.json",
                    "symbols_or_sections": ["tool trace", "prompt rule"],
                    "cause": "shared contract gap",
                    "evidence": "independent prompt and runtime failures",
                    "downstream_impact": "materialization is incomplete",
                }
            ],
        },
        inventory,
        evidence_ids={"evidence.mixed"},
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


def test_diagnosis_agent_repairs_invalid_schema(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inventory = _inventory(tmp_path)
    responses = [
        {
            "schema_version": "prompt-enhancement-diagnosis.v2",
            "status": "mixed",
            "repair_kind": "mixed",
            "summary": "both layers may be involved",
            "target_artifacts": [],
            "dependency_order": [],
            "must_preserve": [],
            "acceptance_evidence": [],
            "causal_findings": [],
            "diagnostic_confidence": 0.5,
        },
        {
            "schema_version": "prompt-enhancement-diagnosis.v2",
            "status": "insufficient_evidence",
            "repair_kind": "none",
            "summary": "no defensible editable target",
            "target_artifacts": [],
            "dependency_order": [],
            "must_preserve": [],
            "acceptance_evidence": [],
            "causal_findings": [],
            "diagnostic_confidence": 0.4,
        },
    ]

    def fake_invoke_json(*_args, **_kwargs):
        return SimpleNamespace(data=responses.pop(0), token_usage={})

    monkeypatch.setattr(
        "src.agents.scripts_and_prompts_generation.agentic_generation_llm_agents."
        "invoke_json",
        fake_invoke_json,
    )
    result = run_content_diagnosis_agent_sync(
        model_name="gpt-5",
        payload={"evidence_index": []},
        inventory=inventory,
    )
    assert result["diagnosis"]["repair_kind"] == "none"
    assert [item["status"] for item in result["llm_call"]["validation_attempts"]] == [
        "rejected",
        "accepted",
    ]
