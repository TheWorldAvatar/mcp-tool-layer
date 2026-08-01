"""Tests for LLM-driven diagnosis and prompt patch protocol."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.agents.scripts_and_prompts_generation.agentic_generation_llm_agents import (
    _prompt_protocol_report,
)
from src.agents.scripts_and_prompts_generation.content_diagnosis import (
    artifact_manifest,
    fixture_literals,
    parse_json_object,
    prompt_inventory,
    redact_diagnosis,
    redact_fixture_evidence,
    validate_diagnosis,
    validate_single_prompt_focus,
)


def test_diagnosis_selects_only_inventory_targets(tmp_path: Path) -> None:
    prompts = tmp_path / "prompts" / "synthetic"
    prompts.mkdir(parents=True)
    target = prompts / "ARBITRARY_PHASE.md"
    target.write_text("Preserve structured fields.\n", encoding="utf-8")
    inventory = prompt_inventory(prompts)
    diagnosis = validate_diagnosis(
        {
            "status": "needs_revision",
            "issues": [{"issue_id": "I1"}],
            "target_prompt_set": [target.resolve().as_posix()],
        },
        inventory,
    )
    assert diagnosis["status"] == "actionable"
    assert diagnosis["target_prompt_set"] == [target.resolve().as_posix()]
    diagnosis_by_name = validate_diagnosis(
        {
            "status": "actionable",
            "issues": [],
            "target_prompt_set": target.name,
        },
        inventory,
    )
    assert diagnosis_by_name["target_prompt_set"] == [target.resolve().as_posix()]

    with pytest.raises(ValueError, match="outside inventory"):
        validate_diagnosis(
            {
                "status": "actionable",
                "issues": [{"issue_id": "I1"}],
                "target_prompt_set": [(tmp_path / "scripts" / "bad.py").as_posix()],
            },
            inventory,
        )


def test_diagnosis_parser_and_editor_projection_redact_instances() -> None:
    parsed = parse_json_object(
        'analysis\n```json\n{"status":"actionable","issues":[],"target_prompt_set":[]}\n```'
    )
    assert parsed["status"] == "actionable"
    projected = redact_diagnosis(
        {
            "status": "actionable",
            "summary": "DMF was omitted",
            "issues": [
                {
                    "issue_id": "I1",
                    "category": "missing",
                    "stage": "extraction",
                    "root_cause": "DMF alias handling",
                    "target_prompts": [],
                    "must_preserve": [],
                    "suggested_change": "Always emit DMF",
                }
            ],
            "target_prompt_set": [],
        },
        {"DMF"},
    )
    assert "DMF" not in json.dumps(projected)
    assert "INSTANCE_REDACTED" in json.dumps(projected)


def test_fixture_literals_include_document_identifiers() -> None:
    literals = fixture_literals(
        {
            "document_md": "# Specific experiment\n\nDOI: 10.1000/example-case\n",
            "hints": {},
        }
    )

    assert "10.1000/example-case" in literals
    assert "Specific experiment" in literals


def test_fixture_evidence_is_redacted_before_diagnosis() -> None:
    redacted = redact_fixture_evidence(
        {"source": "Specific experiment reports value DMF"},
        {"Specific experiment", "DMF"},
    )

    assert "Specific experiment" not in json.dumps(redacted)
    assert "DMF" not in json.dumps(redacted)
    assert json.dumps(redacted).count("INSTANCE_REDACTED") == 2


def test_prompt_focus_requires_one_iteration_prompt_owner() -> None:
    diagnosis = validate_single_prompt_focus(
        {
            "repair_kind": "prompt",
            "target_artifacts": [
                "/tmp/prompts/ontosynthesis/EXTRACTION_ITER_3_1.md"
            ],
            "causal_findings": [{"cause": "Required structured relation omitted"}],
            "must_preserve": ["Keep T-Box datatype rules."],
        }
    )

    assert diagnosis["focus"] == {
        "owner_layer": "extraction",
        "artifact": "/tmp/prompts/ontosynthesis/EXTRACTION_ITER_3_1.md",
        "iteration": "3.1",
        "failure_mode": "Required structured relation omitted",
        "must_preserve": ["Keep T-Box datatype rules."],
    }

    with pytest.raises(ValueError, match="exactly one"):
        validate_single_prompt_focus(
            {
                "repair_kind": "prompt",
                "target_artifacts": [
                    "/tmp/prompts/ontosynthesis/EXTRACTION_ITER_2.md",
                    "/tmp/prompts/ontosynthesis/KG_BUILDING_ITER_2.md",
                ],
                "causal_findings": [{"cause": "too broad"}],
            }
        )


def test_prompt_protocol_requires_read_patch_real_diff_and_immutable_scripts(
    tmp_path: Path,
) -> None:
    target = tmp_path / "prompts" / "synthetic" / "ARBITRARY_PHASE.md"
    script = tmp_path / "scripts" / "synthetic" / "main.py"
    target.parent.mkdir(parents=True)
    script.parent.mkdir(parents=True)
    target.write_text("before\n", encoding="utf-8")
    script.write_text("VALUE = 1\n", encoding="utf-8")
    before_manifest = artifact_manifest(tmp_path)
    before_targets = {target.resolve().as_posix(): "before\n"}
    target.write_text("before\nafter\n", encoding="utf-8")
    report = _prompt_protocol_report(
        before_manifest=before_manifest,
        after_manifest=artifact_manifest(tmp_path),
        before_targets=before_targets,
        targets=[target.resolve().as_posix()],
        agent_result={
            "metadata": {
                "tool_activity": {
                    "executed_tool_names": [
                        "read_workspace_file",
                        "apply_unified_patch",
                    ]
                }
            }
        },
        output_root=tmp_path,
    )
    assert report["ok"], report
    assert report["changed_scripts"] == []
    assert report["target_diffs"][target.resolve().as_posix()]


def test_prompt_protocol_rejects_noop_and_agent_error(tmp_path: Path) -> None:
    target = tmp_path / "prompts" / "synthetic" / "ARBITRARY_PHASE.md"
    target.parent.mkdir(parents=True)
    target.write_text("same\n", encoding="utf-8")
    manifest = artifact_manifest(tmp_path)
    report = _prompt_protocol_report(
        before_manifest=manifest,
        after_manifest=manifest,
        before_targets={target.resolve().as_posix(): "same\n"},
        targets=[target.resolve().as_posix()],
        agent_result={"metadata": {"error": "ExceptionGroup", "tool_activity": {}}},
        output_root=tmp_path,
    )
    assert not report["ok"]
    assert any(item.startswith("prompt_agent_error") for item in report["failures"])
    assert "no_prompt_diff" in report["failures"]


def test_artifact_manifest_includes_runtime_package_files(tmp_path: Path) -> None:
    files = {
        "scripts/onto/main.py": "VALUE = 1\n",
        "prompts/onto/ITER.md": "prompt\n",
        "sparqls/onto/top_entity_parsing.sparql": "SELECT * WHERE { ?s ?p ?o }\n",
        "iterations/onto/iterations.json": "{}\n",
        "ontology_structures/onto/generation_contract.json": "{}\n",
    }
    for relative, content in files.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    manifest = artifact_manifest(tmp_path)
    assert set(manifest) == set(files)
