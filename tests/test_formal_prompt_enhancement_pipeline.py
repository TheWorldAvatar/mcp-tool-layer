from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.agents.scripts_and_prompts_generation.agentic_generation_context import (
    AgenticGenerationContext,
    OntologySpec,
)
from src.agents.scripts_and_prompts_generation.content_diagnosis import (
    STATUS_REPAIR_KIND_MATRIX,
    validate_repair_diagnosis,
)
from src.agents.scripts_and_prompts_generation.level1_code_repair import (
    LLMJsonResult,
)
from src.agents.scripts_and_prompts_generation.llm_extraction_judge import (
    judge_extraction_delta_stability,
    validate_extraction_delta_judgement,
)
from src.agents.scripts_and_prompts_generation.prompt_enhancement_pipeline import (
    _candidate_improves,
    _editor_projection,
    _evidence_index,
    _failure_origin_evidence,
    _stage_fixture_projection,
    _target_extraction_improves,
    run_formal_prompt_enhancement,
    run_targeted_extraction_trials,
)


def _context(tmp_path: Path) -> AgenticGenerationContext:
    scripts = tmp_path / "scripts" / "ontosynthesis"
    prompts = tmp_path / "prompts" / "ontosynthesis"
    scripts.mkdir(parents=True)
    prompts.mkdir(parents=True)
    (scripts / "main.py").write_text("def run(): pass\n", encoding="utf-8")
    (prompts / "EXTRACTION_ITER_2.md").write_text(
        "Extract {paper_content} for {entity_uri}.", encoding="utf-8"
    )
    return AgenticGenerationContext(
        ontology=OntologySpec(
            name="ontosynthesis",
            ttl_file="data/ontologies/ontosynthesis.ttl",
            meta_task_config_path="configs/meta_task/meta_task_config.json",
            role="main",
        ),
        output_root=str(tmp_path),
        ontology_structure_dir=str(tmp_path / "ontology_structures" / "ontosynthesis"),
        scripts_dir=str(scripts),
        prompts_dir=str(prompts),
        parsed_summary_path=str(tmp_path / "parsed.json"),
        parsed_markdown_path=str(tmp_path / "parsed.md"),
        contract_path=str(tmp_path / "contract.json"),
        integrity_profile_path=str(tmp_path / "integrity.json"),
        report_path=str(tmp_path / "report.json"),
        config_provenance_path=str(tmp_path / "provenance.json"),
        parsed={"properties": {"hasRequiredLink": {}}},
        contract={"top_entity": {"class_local": "ChemicalSynthesis"}},
        integrity_profile={},
        pipeline_runtime_policies={},
        iteration_blueprint={},
        config_provenance={},
    )


def _judge(overall: float, **scores: float) -> dict:
    return {
        "consensus": {"overall_score": overall, "scores": scores},
        "acceptance": {"accepted": overall >= 0.95},
        "judges": [],
        "observations": [],
    }


def _evaluation(extraction: float = 0.9, semantic: float = 0.8) -> dict:
    return {
        "ok": True,
        "abox_build": {"ok": True, "predicted_hints": {}},
        "reasoner": {"ok": True},
        "extraction_soft_judge": _judge(
            extraction, groundedness=extraction, coverage=extraction
        ),
        "semantic_soft_judge": _judge(
            semantic, groundedness=semantic, coverage=semantic
        ),
        "repeats": [{"repeat": 1}],
    }


def test_strict_diagnosis_rejects_status_route_mismatch(tmp_path: Path) -> None:
    prompt = tmp_path / "EXTRACTION_ITER_2.md"
    prompt.write_text("Extract.", encoding="utf-8")
    inventory = [
        {
            "path": prompt.resolve().as_posix(),
            "name": prompt.name,
            "kind": "prompt",
            "editable": True,
        }
    ]
    with pytest.raises(ValueError, match="status/repair_kind mismatch"):
        validate_repair_diagnosis(
            {
                "schema_version": "prompt-enhancement-diagnosis.v2",
                "status": "script_actionable",
                "repair_kind": "prompt",
                "summary": "wrong route",
                "target_artifacts": [prompt.name],
                "dependency_order": [prompt.name],
                "must_preserve": [],
                "acceptance_evidence": [],
                "causal_findings": [],
                "diagnostic_confidence": 0.9,
            },
            inventory,
        )


def test_editor_projection_removes_fixture_literals() -> None:
    projected = _editor_projection(
        {
            "status": "actionable",
            "summary": "DMF identity was omitted",
            "target_artifacts": ["/tmp/EXTRACTION_ITER_2.md"],
            "must_preserve": [],
            "causal_findings": [
                {
                    "observation_ids": ["extraction.judge1.deduction1"],
                    "cause": "DMF identity was omitted",
                }
            ],
        },
        {"DMF"},
    )
    assert "DMF" not in json.dumps(projected)
    assert "[INSTANCE_REDACTED]" in json.dumps(projected)


def test_evidence_index_assigns_stable_layered_ids() -> None:
    report = _evaluation()
    report["extraction_soft_judge"]["judges"] = [
        {
            "deductions": [
                {
                    "reason": "missing extraction fact",
                    "observation_ids": ["semantic.missing::stable"],
                }
            ]
        }
    ]
    report["semantic_soft_judge"]["observations"] = [{"reason": "wrong triple"}]
    ids = [item["evidence_id"] for item in _evidence_index(report)]
    assert "extraction.judge1.deduction1" in ids
    assert "semantic.missing::stable" in ids
    assert "kg_building.observation1" in ids
    assert "repeat.1.summary" in ids


def test_post_publish_feedback_is_first_highest_priority_evidence(
    tmp_path: Path,
) -> None:
    feedback_dir = tmp_path / "post_publish_feedback" / "entity"
    feedback_dir.mkdir(parents=True)
    (feedback_dir / "structural_attempt_1.json").write_text(
        json.dumps(
            {
                "evidence_id": "kg.post_publish.required-link",
                "priority": "highest",
                "failure_kind": "post_publish_structural_validation",
                "retry_status": "resolved",
            }
        ),
        encoding="utf-8",
    )
    report = _evaluation()
    report["abox_build"]["case_dir"] = str(tmp_path)

    evidence = _evidence_index(report)

    assert evidence[0]["evidence_id"] == "kg.post_publish.required-link"
    assert evidence[0]["kind"] == "post_publish_structural_failure"
    assert evidence[0]["payload"]["priority"] == "highest"


def test_candidate_requires_monotonic_dimension_improvement() -> None:
    before = _evaluation(0.8, 0.7)
    improved = _evaluation(0.81, 0.72)
    assert _candidate_improves(before, improved) == (True, [])
    regressed = _evaluation(0.79, 0.9)
    ok, failures = _candidate_improves(before, regressed)
    assert not ok
    assert any("regressed" in failure for failure in failures)


def test_target_extraction_gate_ignores_downstream_scores() -> None:
    before = {"ok": True, "extraction_soft_judge": _judge(0.8, coverage=0.8)}
    after = {"ok": True, "extraction_soft_judge": _judge(0.9, coverage=0.9)}
    assert _target_extraction_improves(before, after) == (True, [])


def test_stage_fixture_projection_uses_semantic_plan_ownership(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    plan_dir = tmp_path / "semantic_planning" / "ontosynthesis"
    plan_dir.mkdir(parents=True)
    (plan_dir / "accepted_semantic_plan.json").write_text(
        json.dumps(
            {
                "assignments": {
                    "assignments": [
                        {
                            "slot": "iter2",
                            "classes": ["ChemicalInput"],
                            "object_properties": ["hasChemicalInput"],
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    projection = _stage_fixture_projection(
        context=context,
        fixture={
            "hints": {
                "ChemicalSynthesis": [
                    {
                        "label": "S1",
                        "hasChemicalInput": "I1",
                        "hasSynthesisStep": "Step1",
                    }
                ],
                "ChemicalInput": [{"label": "I1"}],
                "Add": [{"label": "Step1"}],
            }
        },
        iteration=2,
    )
    assert set(projection["hints"]) == {"ChemicalSynthesis", "ChemicalInput"}
    assert "hasSynthesisStep" not in projection["hints"]["ChemicalSynthesis"][0]


def _delta_payload(*, target_fixed: bool = True, regressions: list | None = None) -> dict:
    regressions = regressions or []
    return {
        "target_fixed": target_fixed,
        "target_evidence": [],
        "new_regressions": regressions,
        "unchanged_defects": [],
        "improvements": (
            [
                {
                    "obligation": "target",
                    "before_evidence": "before",
                    "after_evidence": "after",
                    "is_target": True,
                }
            ]
            if target_fixed
            else []
        ),
        "verdict": "accept" if target_fixed and not regressions else "reject",
        "confidence": 0.9,
        "summary": "paired result",
    }


def test_delta_judge_verdict_is_derived_from_fixed_fields() -> None:
    invalid = _delta_payload()
    invalid["verdict"] = "reject"
    with pytest.raises(ValueError, match="verdict must be accept"):
        validate_extraction_delta_judgement(invalid)


def test_delta_stability_ignores_wording_drift_when_verdict_is_stable() -> None:
    calls = 0

    def invoke(**_kwargs) -> LLMJsonResult:
        nonlocal calls
        calls += 1
        payload = _delta_payload()
        payload["summary"] = f"wording {calls}"
        return LLMJsonResult(
            data=payload,
            elapsed_seconds=0.1,
            token_usage={},
            raw_response=json.dumps(payload),
        )

    result = judge_extraction_delta_stability(
        document_text="doc",
        ontology_contract={},
        reference_content={},
        before_content={},
        after_content={},
        repair_focus={},
        model="test",
        repeats=3,
        invoke=invoke,
    )
    assert result["unanimous"] is True
    assert result["accepted"] is True
    assert len(result["runs"]) == 3


def test_delta_stability_repairs_schema_invalid_judgement() -> None:
    calls = 0

    def invoke(**kwargs) -> LLMJsonResult:
        nonlocal calls
        calls += 1
        payload = _delta_payload()
        if calls == 1:
            payload["new_regressions"] = [{"unexpected": "field"}]
        else:
            assert "REQUIRED SCHEMA REPAIR" in kwargs["prompt"]
        return LLMJsonResult(
            data=payload,
            elapsed_seconds=0.1,
            token_usage={},
            raw_response=json.dumps(payload),
        )

    result = judge_extraction_delta_stability(
        document_text="doc",
        ontology_contract={},
        reference_content={},
        before_content={},
        after_content={},
        repair_focus={},
        model="test",
        repeats=1,
        invoke=invoke,
    )
    assert calls == 2
    assert result["accepted"] is True


def test_targeted_trials_persist_independent_repeat_reports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = 0

    def run_trial(**kwargs):
        nonlocal calls
        calls += 1
        return {
            "ok": True,
            "case_dir": str(kwargs["output_dir"] / "case"),
            "frozen_pre_extraction_sha256": {"entity_text_CS-1.txt": "fixed"},
        }

    monkeypatch.setattr(
        "src.agents.scripts_and_prompts_generation.prompt_enhancement_pipeline."
        "_run_targeted_extraction_evaluation",
        run_trial,
    )
    output = tmp_path / "trials"
    result = run_targeted_extraction_trials(
        context=_context(tmp_path),
        artifact_root=tmp_path,
        fixture={"document_md": "doc"},
        baseline_case_dir=tmp_path / "baseline",
        iteration=3,
        sub_iteration=None,
        output_dir=output,
        repeats=3,
        parallelism=1,
    )

    assert calls == 3
    assert result["repeats"] == 3
    assert result["parallelism"] == 1
    assert result["frozen_pre_extraction_sha256"] == {
        "entity_text_CS-1.txt": "fixed"
    }
    assert (output / "trials_summary.json").is_file()


def test_non_prompt_diagnosis_writes_handoff_without_editing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = _context(tmp_path)
    fixture = tmp_path / "fixture.json"
    fixture.write_text(json.dumps({"document_md": "Document."}), encoding="utf-8")

    monkeypatch.setattr(
        "src.agents.scripts_and_prompts_generation.prompt_enhancement_pipeline."
        "_evaluate_generated_package",
        lambda **_: _evaluation(),
    )
    monkeypatch.setattr(
        "src.agents.scripts_and_prompts_generation.prompt_enhancement_pipeline."
        "_run_consensus_diagnosis",
        lambda **_: {
            "agreement": "unanimous",
            "diagnosis": {
                "schema_version": "prompt-enhancement-diagnosis.v2",
                "status": "script_actionable",
                "repair_kind": "script",
                "summary": "runtime lookup defect",
                "target_artifacts": [
                    (tmp_path / "scripts" / "ontosynthesis" / "main.py").as_posix()
                ],
                "dependency_order": [],
                "must_preserve": [],
                "acceptance_evidence": [],
                "causal_findings": [],
                "diagnostic_confidence": 0.9,
            },
        },
    )
    result = run_formal_prompt_enhancement(
        context=context,
        fixture_path=fixture,
        model="gpt-5",
        max_rounds=1,
    )
    assert result["status"] == "handoff"
    assert result["final_artifact_root"] == str(tmp_path)
    assert (
        tmp_path / "scripts" / "ontosynthesis" / "main.py"
    ).read_text(encoding="utf-8") == "def run(): pass\n"
    assert (tmp_path / "prompt_enhancement" / "round_1" / "handoff.json").is_file()


def test_diagnosis_calibration_matrix_covers_all_routes() -> None:
    path = (
        Path(__file__).parent
        / "fixtures"
        / "diagnosis_calibration"
        / "cases.json"
    )
    cases = json.loads(path.read_text(encoding="utf-8"))
    assert {
        case["expected_repair_kind"] for case in cases
    } >= {
        "prompt",
        "script",
        "mixed",
        "model_instability",
        "none",
    }
    for case in cases:
        assert (
            case["expected_repair_kind"]
            in STATUS_REPAIR_KIND_MATRIX[case["expected_status"]]
        )


def test_failure_origin_separates_hint_call_and_export_layers(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path / "package")
    case_dir = tmp_path / "runtime"
    traces = case_dir / "responses"
    traces.mkdir(parents=True)
    (traces / "attempt.trace.json").write_text(
        json.dumps({"tool_calls": [], "tool_outputs": []}), encoding="utf-8"
    )
    abox = tmp_path / "candidate.ttl"
    abox.write_text("@prefix ex: <https://example.com/> .\n", encoding="utf-8")
    evaluation = _evaluation()
    evaluation["abox_path"] = str(abox)
    evaluation["abox_build"] = {
        "ok": True,
        "case_dir": str(case_dir),
        "predicted_hints": {
            "step": {"hasRequiredLink": "target (unresolved)"}
        },
    }
    evaluation["semantic_soft_judge"]["judges"] = [
        {
            "deductions": [
                {
                    "dimension": "coverage",
                    "ontology_evidence": (
                        "Each step must have exactly one hasRequiredLink."
                    ),
                    "reason": "hasRequiredLink is missing from the final A-Box.",
                }
            ]
        }
    ]
    origins = _failure_origin_evidence(
        context=context,
        evaluation=evaluation,
    )
    finding = next(
        item for item in origins if item["property_local"] == "hasRequiredLink"
    )
    assert finding["first_failure_origin"] == "kg_prompt_or_model_behavior"
    assert finding["hint_present"] is True
    assert finding["tool_called"] is False
    assert finding["secondary_findings"]
