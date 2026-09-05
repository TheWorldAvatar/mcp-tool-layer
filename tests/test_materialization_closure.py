from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from src.agents.scripts_and_prompts_generation import semantic_script_review
from src.agents.scripts_and_prompts_generation.agentic_generation_runner import (
    generate_deterministic_prompt_slice,
    generate_deterministic_script_slice,
)
from src.agents.scripts_and_prompts_generation.agentic_generation_validation import (
    build_validation_report,
)
from src.agents.scripts_and_prompts_generation.domain_artifact_compiler import (
    build_domain_generation_context,
)
from src.agents.scripts_and_prompts_generation.materialization_closure import (
    compile_materialization_obligation_graph,
    restrict_classes_to_creator_surface,
)
from src.agents.scripts_and_prompts_generation.semantic_script_review import (
    _validate_paired_materialization_review,
)


ROOT = Path(__file__).resolve().parents[1]
DOMAIN_CONFIG = ROOT / "configs" / "domains" / "ontomock.json"


def _planner(model: str, prompt: str) -> dict:
    return {
        "class_local": "ProcessRun",
        "rationale": "ProcessRun is the workflow root.",
        "evidence": ["ProcessRun", "hasAction"],
    }


def _synthetic_context(
    tmp_path: Path,
    *,
    target: str,
    creator_tools: list[str],
    required: bool = False,
    prohibited: bool = False,
    kg_text: str = "",
) -> SimpleNamespace:
    prompts = tmp_path / "prompts"
    prompts.mkdir(parents=True, exist_ok=True)
    if kg_text:
        (prompts / "KG_BUILDING_ITER_2.md").write_text(kg_text, encoding="utf-8")
    target_iri = f"https://example.test/{target}"
    predicate_iri = "https://example.test/hasTarget"
    return SimpleNamespace(
        ontology=SimpleNamespace(name="synthetic", role="main"),
        prompts_dir=str(prompts),
        parsed={
            "classes": {
                target: {
                    "iri": target_iri,
                }
            },
            "properties": {
                "hasTarget": {
                    "iri": predicate_iri,
                    "kind": "object",
                    "range": target,
                }
            },
        },
        contract={
            "top_entity": {},
            "reuse_policy": {
                "classes": (
                    [
                        {
                            "class_local": target,
                            "reuse_scope": "prohibited",
                            "reusable": False,
                        }
                    ]
                    if prohibited
                    else []
                )
            },
            "relationship_tool_contracts": {
                "hasTarget": {
                    "predicate_iri": predicate_iri,
                    "range_locals": [target],
                    "creator_tools": creator_tools,
                }
            },
            "required_links": (
                [
                    {
                        "predicate_iri": predicate_iri,
                        "target_class_iri": target_iri,
                        "min_count": 1,
                    }
                ]
                if required
                else []
            ),
        },
        iteration_blueprint={
            "iterations": [
                {
                    "iteration_number": 2,
                    "responsibilities": {
                        "classes": [],
                        "object_properties": ["hasTarget"],
                    },
                }
            ]
        },
    )


def test_ontomock_fixed_om2_and_external_creator_close_paths(
    tmp_path: Path,
) -> None:
    context = build_domain_generation_context(
        domain_config_path=DOMAIN_CONFIG,
        output_root=tmp_path,
        repository_root=ROOT,
        write_files=True,
        planner=_planner,
    )

    report = compile_materialization_obligation_graph(context)
    by_subject = {
        (item["predicate_local"], item["class_local"]): item
        for item in report["obligations"]
        if item["predicate_local"]
    }

    duration_paths = by_subject[("hasDuration", "Duration")][
        "materialization_paths"
    ]
    metric_paths = by_subject[("hasMetric", "ExternalMetric")][
        "materialization_paths"
    ]
    assert any(path["kind"] == "fixed_om2_creator" for path in duration_paths)
    assert any(
        path["kind"] in {"generated_creator", "generated_external_creator"}
        for path in metric_paths
    )
    assert not any(
        item["subject"] in {"hasDuration->Duration", "hasMetric->ExternalMetric"}
        for item in report["contradictions"]
    )


def test_prompt_prose_does_not_create_materialization_contradictions(
    tmp_path: Path,
) -> None:
    context = _synthetic_context(
        tmp_path,
        target="Target",
        creator_tools=["create_Target"],
        kg_text=(
            "Create Target when source evidence requires one. "
            "DoAlt must not create a new Input."
        ),
    )
    report = compile_materialization_obligation_graph(
        context,
        creator_surface={"Target": "create_Target"},
    )

    assert report["contradictions"] == []
    assert report["ok"]


def test_merged_predicate_is_not_a_standalone_closure_obligation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _synthetic_context(
        tmp_path,
        target="Target",
        creator_tools=["create_Target"],
    )
    context.contract["materialization_operation_units"] = {
        "merged_predicate_locals": ["hasTarget"],
    }
    monkeypatch.setattr(
        "src.agents.scripts_and_prompts_generation.materialization_closure."
        "derive_creator_surface",
        lambda _context: [],
    )

    report = compile_materialization_obligation_graph(
        context,
        creator_surface={"Target": "create_Target"},
    )

    assert not any(
        item["kind"] == "owned_object_property_target"
        and item["predicate_local"] == "hasTarget"
        for item in report["obligations"]
    )
    assert not any(
        item["code"] == "target_without_materialization_path"
        for item in report["contradictions"]
    )


def test_parent_owned_without_creator_is_dropped() -> None:
    context = SimpleNamespace(
        parsed={
            "classes": {
                "Alpha": {"iri": "https://example.test/Alpha"},
                "Beta": {
                    "iri": "https://example.test/Beta",
                    "parent_classes": ["Alpha"],
                },
            },
            "properties": {
                "usesAlpha": {
                    "iri": "https://example.test/usesAlpha",
                    "kind": "object",
                    "range": "Alpha",
                }
            },
        },
        contract={
            "top_entity": {},
            "reuse_policy": {},
            "relationship_tool_contracts": {
                "usesAlpha": {
                    "predicate_iri": "https://example.test/usesAlpha",
                    "range_locals": ["Alpha"],
                    "materialization_target_locals": ["Beta"],
                    "creator_tools": ["create_Beta"],
                }
            },
            "required_links": [],
            "ordered_member_profile": {
                "most_specific_subclass_targets": {"Alpha": ["Beta"]},
            },
        },
        iteration_blueprint={
            "iterations": [
                {
                    "iteration_number": 2,
                    "responsibilities": {
                        "classes": ["Alpha", "Beta"],
                        "object_properties": ["usesAlpha"],
                    },
                }
            ]
        },
    )
    report = compile_materialization_obligation_graph(
        context,
        creator_surface={"Beta": "create_Beta"},
    )
    assert not any(
        item["code"] == "owned_class_without_materialization_path"
        for item in report["contradictions"]
    )
    owned = {
        item["class_local"]
        for item in report["obligations"]
        if item["kind"] == "owned_class_materialization"
    }
    assert owned == {"Beta"}


def test_restrict_classes_to_creator_surface_is_domain_generic() -> None:
    assert restrict_classes_to_creator_surface(
        ["Alpha", "Beta", "Alpha"],
        {"Beta"},
    ) == ["Beta"]
    source = Path(
        restrict_classes_to_creator_surface.__code__.co_filename
    ).read_text(encoding="utf-8")
    start = source.index("def restrict_classes_to_creator_surface")
    end = source.index("\ndef ", start + 1)
    helper = source[start:end]
    for forbidden in (
        "Device",
        "Document",
        "Material",
        "OntoSpecies",
        "ontospecies",
    ):
        assert forbidden not in helper


def test_owned_property_target_without_creator_is_hard_failure(
    tmp_path: Path,
) -> None:
    context = _synthetic_context(
        tmp_path,
        target="Target",
        creator_tools=[],
    )
    report = compile_materialization_obligation_graph(
        context,
        creator_surface=[],
    )

    assert any(
        item["code"] == "target_without_materialization_path"
        for item in report["contradictions"]
    )
    obligation = report["obligations"][0]
    assert obligation["required_when"] == "source_evidence_present"
    assert obligation["capability_required"] is True


def test_required_link_to_prohibited_class_fails_closed(
    tmp_path: Path,
) -> None:
    context = _synthetic_context(
        tmp_path,
        target="ProhibitedType",
        creator_tools=[],
        required=True,
        prohibited=True,
    )
    report = compile_materialization_obligation_graph(
        context,
        creator_surface=[],
    )
    codes = {item["code"] for item in report["contradictions"]}

    assert "required_link_without_materialization_path" in codes
    assert "prohibited_class_required" in codes


def test_optional_property_target_for_prohibited_class_is_not_an_obligation(
    tmp_path: Path,
) -> None:
    context = _synthetic_context(
        tmp_path,
        target="ProhibitedType",
        creator_tools=[],
        prohibited=True,
    )

    report = compile_materialization_obligation_graph(
        context,
        creator_surface=[],
    )

    assert report["ok"]
    assert report["obligations"] == []
    assert report["contradictions"] == []


def test_full_validation_emits_materialization_hard_gate(tmp_path: Path) -> None:
    context = build_domain_generation_context(
        domain_config_path=DOMAIN_CONFIG,
        output_root=tmp_path,
        repository_root=ROOT,
        write_files=True,
        planner=_planner,
    )
    generate_deterministic_prompt_slice(context)
    generate_deterministic_script_slice(context)

    report = build_validation_report(
        context,
        write_report=False,
        prompts_required=True,
    )
    observation = next(
        item
        for item in report["observations"]
        if item["check_id"] == "generation.materialization_closure"
    )

    assert observation["evidence"]["hard_gate"] is True
    assert (
        observation["evidence"]["schema_version"]
        == "materialization_contradiction.v1"
    )
    assert observation["status"] == "pass"


def test_paired_review_uses_closed_json_schema() -> None:
    valid = {
        "decision": "pass",
        "summary": "The pair is complete.",
        "critical_errors": [],
        "noncritical_observations": [],
        "confidence": 0.9,
    }
    assert _validate_paired_materialization_review(valid)["decision"] == "pass"

    with pytest.raises(ValueError, match="unexpected keys"):
        _validate_paired_materialization_review({**valid, "extra": True})

    repair = {
        "decision": "repair",
        "summary": "One creator-input path is incomplete.",
        "critical_errors": [
            {
                "finding": "KG prompt drops hasOrder.",
                "iteration": "3",
                "evidence": ["create_DoStep is called without order"],
                "expected_behavior": (
                    "Pass extracted hasOrder to create_DoStep as its required order input."
                ),
                "contract_evidence": ["create_DoStep.order is required int"],
                "repair_targets": ["KG_BUILDING_ITER_3.md"],
            }
        ],
        "noncritical_observations": [],
        "confidence": 1.0,
    }
    assert _validate_paired_materialization_review(repair)["decision"] == "repair"


def test_required_creator_input_requires_complete_prompt_contract_path(
    tmp_path: Path,
) -> None:
    context = _synthetic_context(
        tmp_path,
        target="Target",
        creator_tools=["create_Target"],
    )
    creator_surface = [
        {
            "class_local": "Target",
            "class_iri": "https://example.test/Target",
            "tool": "create_Target",
            "path_kind": "generated_creator",
            "datatype_inputs": [
                {
                    "property_local": "hasOrder",
                    "property_iri": "https://example.test/hasOrder",
                    "range_iri": "http://www.w3.org/2001/XMLSchema#integer",
                    "python_type": "int",
                    "required": True,
                },
                {
                    "property_local": "isEnabled",
                    "property_iri": "https://example.test/isEnabled",
                    "range_iri": "http://www.w3.org/2001/XMLSchema#boolean",
                    "python_type": "bool",
                    "required": False,
                },
            ],
        }
    ]
    extraction_contract = {
        "tbox_scope": {
            "classes": {"Target": {"parent_classes": []}},
            "properties": {
                "hasOrder": {"domains": ["Target"]},
                "isEnabled": {"domains": ["Target"]},
            },
        }
    }
    kg_contract = {
        "tbox_scope": {
            "classes": {"Target": {"parent_classes": []}},
            "properties": {"isEnabled": {"domains": ["Target"]}},
        }
    }

    report = compile_materialization_obligation_graph(
        context,
        creator_surface=creator_surface,
        prompt_generation_contracts={
            "EXTRACTION_ITER_2.md": extraction_contract,
            "KG_BUILDING_ITER_2.md": kg_contract,
        },
    )

    creator_inputs = [
        item for item in report["obligations"] if item["kind"] == "creator_input"
    ]
    assert {
        (item["predicate_local"], item["input_requirement"])
        for item in creator_inputs
    } == {("hasOrder", "required"), ("isEnabled", "optional")}
    required = next(
        item for item in creator_inputs if item["predicate_local"] == "hasOrder"
    )
    assert required["datatype_input"]["python_type"] == "int"
    assert required["transitive_contract_path"]["extraction"]["domain_applies"]
    assert not required["transitive_contract_path"]["kg"]["domain_applies"]
    assert any(
        item["code"] == "required_creator_input_without_prompt_contract_path"
        for item in report["contradictions"]
    )


def test_paired_review_receives_exact_tool_and_tbox_contracts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = build_domain_generation_context(
        domain_config_path=DOMAIN_CONFIG,
        output_root=tmp_path,
        repository_root=ROOT,
        write_files=True,
        planner=_planner,
    )
    prompts_dir = Path(context.prompts_dir)
    prompts_dir.mkdir(parents=True, exist_ok=True)
    (prompts_dir / "EXTRACTION_ITER_3.md").write_text(
        "Extract ordered actions.", encoding="utf-8"
    )
    (prompts_dir / "KG_BUILDING_ITER_3.md").write_text(
        "Materialize ordered actions.", encoding="utf-8"
    )
    captured: dict[str, Any] = {}

    def fake_invoke_json(_model: str, prompt: str, **_kwargs):
        captured["prompt"] = prompt
        return SimpleNamespace(
            data={
                "decision": "pass",
                "summary": "complete",
                "critical_errors": [],
                "noncritical_observations": [],
                "confidence": 1.0,
            }
        )

    monkeypatch.setattr(semantic_script_review, "invoke_json", fake_invoke_json)
    def fake_build_validation_report(*_args, **kwargs):
        captured["validation_kwargs"] = kwargs
        return {"failures": [], "observations": []}

    monkeypatch.setattr(
        semantic_script_review,
        "build_validation_report",
        fake_build_validation_report,
    )

    review = semantic_script_review.review_paired_prompt_materialization_with_llm(
        context=context,
        model_name="test-model",
        closure_report={"contradictions": [], "obligations": [], "ok": True},
    )

    prompt = captured["prompt"]
    assert review["decision"] == "pass"
    assert captured["validation_kwargs"]["write_report"] is False
    assert captured["validation_kwargs"]["include_prompt_checks"] is True
    assert set(captured["validation_kwargs"]["active_artifacts"]) == {
        "prompts/ontomock/EXTRACTION_ITER_3.md",
        "prompts/ontomock/KG_BUILDING_ITER_3.md",
    }
    assert '"name": "init_memory"' in prompt
    assert '"name": "export_memory"' in prompt
    assert "init_or_resume_scoped_memory" in prompt
    assert "export_retained_memory" in prompt
    assert '"name": "create_DoStep"' in prompt
    assert '"name": "create_DoAlt"' in prompt
    assert '"property_local": "hasOrder"' in prompt
    assert '"property_local": "isEnabled"' in prompt
    assert '"relationship_target_contracts"' in prompt
    assert '"agent_tool_contract"' in prompt
    assert "create_DoStep(label: str, hasOrder: int, isEnabled: bool | None = None)" in prompt
    assert '"natural_language_requirement": "Integrate hasOrder with its complete ' in prompt
    assert '"natural_language_requirement": "Integrate isEnabled with its complete ' in prompt
    assert "acts on an existing input without introducing an unstated material" in prompt
    assert "introduces one named Input" in prompt
    assert "must never be generalized to a sibling" in prompt
    assert "set_<datatype_property>" in prompt
    assert "MANDATORY OBJECT-PROPERTY PATH CHECKLIST" in prompt
    assert "Mode A — extraction-emitted range entity" in prompt
    assert "Mode B — lexical quantity interchange" in prompt
    assert "Mode C — pipeline-owned prior identity" in prompt
    assert "Mode D — prior-iteration identity only" in prompt
    assert "mandatory_object_property_path_checklist" in prompt
    assert "materialization_closure.contradictions=[] is not proof" in prompt
    assert semantic_script_review._PAIRED_OBJECT_PROPERTY_PATH_CHECKLIST in prompt
