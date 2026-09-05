from __future__ import annotations

from pathlib import Path

from src.agents.scripts_and_prompts_generation.level1_code_repair import (
    LLMJsonResult,
)
from src.agents.scripts_and_prompts_generation.llm_framework_integrity_judge import (
    _validated_framework_report,
    build_framework_integrity_prompt,
    judge_framework_integrity,
)
from src.agents.scripts_and_prompts_generation.llm_framework_integrity_microjudge import (
    aspects_for_item,
    parse_semantic_hint_items,
)


def _failed_report(item_ids: list[str]) -> dict:
    failure_id = "missing-membership"
    return {
        "accepted": False,
        "summary": "All scoped members are detached from the root.",
        "source_items": [
            {
                "id": item_id,
                "evidence": f"Source occurrence {item_id}",
                "expected_structure": "Member connected to scoped root.",
                "status": "fail",
                "failure_ids": [failure_id],
            }
            for item_id in item_ids
        ],
        "failures": [
            {
                "failure_id": failure_id,
                "kind": "relationship_integration",
                "severity": "critical",
                "affected_source_items": item_ids,
                "expected_structure": "Every member is linked to the scoped root.",
                "observed_problem": "All typed members are detached.",
                "ontology_evidence": "The T-Box defines the root-to-member relation.",
                "abox_evidence": "No root-to-member triples are present.",
                "repair_instruction": "Add the missing relation for every listed member.",
            }
        ],
        "repair_plan": [
            {
                "priority": index + 1,
                "operation": "add_relationship",
                "tool_name": "add_contains",
                "subject_iri": "urn:root",
                "predicate_iri": "urn:contains",
                "object_iri": f"urn:{item_id}",
                "class_iri": "",
                "source_item_id": item_id,
                "action": f"Link {item_id} to the scoped root.",
                "failure_ids": [failure_id],
            }
            for index, item_id in enumerate(item_ids)
        ],
        "coverage_accounting": {
            "complete": True,
            "unaccounted_source_items": [],
            "notes": "All source occurrences were enumerated.",
        },
        "confidence": 1.0,
    }


def _passing_report(item_ids: list[str]) -> dict:
    return {
        "accepted": True,
        "summary": "All scoped members are structurally integrated.",
        "source_items": [
            {
                "id": item_id,
                "evidence": f"Source occurrence {item_id}",
                "expected_structure": "Member connected to scoped root.",
                "status": "pass",
                "failure_ids": [],
            }
            for item_id in item_ids
        ],
        "failures": [],
        "repair_plan": [],
        "coverage_accounting": {
            "complete": True,
            "unaccounted_source_items": [],
            "notes": "All source occurrences were verified.",
        },
        "confidence": 1.0,
    }


def test_framework_prompt_requires_exhaustive_consolidated_feedback() -> None:
    prompt = build_framework_integrity_prompt(
        document_text="member-1; member-2",
        ontology_contract={"object_properties": []},
        abox_turtle="<urn:root> a <urn:Root> .",
    )

    assert "Do not sample" in prompt
    assert "affected_source_items lists every affected item id" in prompt
    assert "domain-specific hard-coded names" in prompt
    assert "hasSynthesisStep" not in prompt
    assert "URI-level atomic repair_plan" in prompt
    assert "one repair-plan entry per affected source item" in prompt
    assert "domain and range axioms" in prompt
    assert "do not require every domain-class instance" in prompt
    assert "source mention is relevant only when" in prompt
    assert "omit it from source_items" in prompt
    assert "never reverse the expected and observed directions" in prompt
    assert "solvent" not in prompt
    assert "ChemicalInput" not in prompt


def test_microjudge_splits_items_and_only_explicit_field_aspects() -> None:
    items = parse_semantic_hint_items(
        "SEMANTIC_HINTS_V1\n\n"
        "Add (Step 1)\n"
        "hasOrder: 1\n"
        "hasVessel: vessel A\n\n"
        "Heat (Step 2)\n"
        "hasOrder: 2\n"
        "hasTemperature: 90 C\n"
    )

    assert [item.item_id for item in items] == ["Add (Step 1)", "Heat (Step 2)"]
    add_aspects = {
        aspect.aspect_id for aspect in aspects_for_item(items[0], {})
    }
    heat_aspects = {
        aspect.aspect_id for aspect in aspects_for_item(items[1], {})
    }
    assert "field:hasVessel" in add_aspects
    assert "field:hasVessel" not in heat_aspects
    assert "owner_integration" in add_aspects
    assert "owner_integration" in heat_aspects


def test_microjudge_parses_class_identity_and_root_field_blocks() -> None:
    items = parse_semantic_hint_items(
        "SEMANTIC_HINTS_V1\n\n"
        "ChemicalInput: VCl3\n"
        "hasAmount: 0.05 g\n\n"
        "hasEquipment: vessel A\n"
        "hasYield: 35 %\n"
    )

    assert items[0].class_hint == "ChemicalInput"
    assert items[0].marker == "VCl3"
    assert items[0].fields == (("hasAmount", "0.05 g"),)
    assert items[1].class_hint == ""
    assert items[1].fields == (("hasEquipment", "vessel A"),)
    assert items[2].fields == (("hasYield", "35 %"),)
    assert {
        aspect.aspect_id for aspect in aspects_for_item(items[1], {})
    } == {"field:hasEquipment"}
    assert {
        aspect.aspect_id for aspect in aspects_for_item(items[2], {})
    } == {"field:hasYield"}


def test_framework_report_rejects_inconsistent_acceptance() -> None:
    report = _failed_report(["member-1"])
    report["accepted"] = True

    try:
        _validated_framework_report(report)
    except ValueError as exc:
        assert "conflicts with validated accounting" in str(exc)
    else:
        raise AssertionError("inconsistent accepted flag must be rejected")


def test_framework_judge_requires_independent_detection_confirmation_and_repair(
    tmp_path: Path,
) -> None:
    abox = tmp_path / "abox.ttl"
    abox.write_text("<urn:root> a <urn:Root> .", encoding="utf-8")
    prompts: list[str] = []

    def invoke(_model: str, prompt: str, **_kwargs) -> LLMJsonResult:
        prompts.append(prompt)
        if "Plan exactly one atomic repair" in prompt:
            data = {
                "operation": "create_entity",
                "tool_name": "create_Member",
                "subject_iri": "",
                "predicate_iri": "",
                "object_iri": "",
                "class_iri": "urn:Member",
                "action": "Create the missing member occurrence.",
            }
        else:
            data = {
                "decision": "fail",
                "source_item_id": "member-1",
                "aspect_id": "entity_presence",
                "summary": "The source member is not materialized.",
                "source_evidence": "member-1",
                "ontology_evidence": "urn:Member",
                "abox_evidence": "No urn:Member node is present.",
                "confidence": 1.0,
            }
        return LLMJsonResult(
            data=data,
            elapsed_seconds=0.1,
            token_usage={"total_tokens": 10},
        )

    report = judge_framework_integrity(
        document_text="member-1",
        ontology_contract={"object_properties": []},
        abox_path=abox,
        model="audit-model",
        reviewer_model="review-model",
        invoke=invoke,
    )

    assert len(prompts) == 9
    assert sum("CONFIRMATION ROUND: true" in prompt for prompt in prompts) == 3
    assert sum("Plan exactly one atomic repair" in prompt for prompt in prompts) == 3
    assert report["accepted"] is False
    assert [item["id"] for item in report["final"]["source_items"]] == ["member-1"]
    assert report["observations"][0]["evidence"]["repair_instruction"]
    repairs = report["observations"][0]["evidence"]["uri_level_repair_plan"]
    assert repairs[0]["operation"] == "create_entity"
    assert report["token_usage"]["total_tokens"] == 90


def test_independent_panel_disagreement_cannot_block(
    tmp_path: Path,
) -> None:
    abox = tmp_path / "abox.ttl"
    abox.write_text(
        "<urn:root> <urn:contains> <urn:member-1> .",
        encoding="utf-8",
    )
    prompts: list[str] = []

    def invoke(model: str, prompt: str, **_kwargs) -> LLMJsonResult:
        prompts.append(prompt)
        decision = "fail" if model == "draft-model" else "pass"
        return LLMJsonResult(
            data={
                "decision": decision,
                "source_item_id": "member-1",
                "aspect_id": "entity_presence",
                "summary": f"{model} decision",
                "source_evidence": "member-1",
                "ontology_evidence": "urn:Member",
                "abox_evidence": "Local graph inspected.",
                "confidence": 1.0,
            },
            elapsed_seconds=0.1,
            token_usage={"total_tokens": 5},
        )

    report = judge_framework_integrity(
        document_text="member-1",
        ontology_contract={"object_properties": ["urn:contains"]},
        abox_path=abox,
        model="draft-model",
        reviewer_model="review-model",
        verifier_model="verification-model",
        invoke=invoke,
    )

    assert report["accepted"] is True
    assert report["final"]["failures"] == []
    assert len(prompts) == 6
    assert report["observations"][0]["status"] == "uncertain"
    assert report["observations"][0]["blocked_by"] == [
        "independent_panel_disagreement"
    ]


def test_framework_report_rejects_label_only_relationship_repair() -> None:
    report = _failed_report(["member-1"])
    report["repair_plan"][0]["object_iri"] = ""

    try:
        _validated_framework_report(report)
    except ValueError as exc:
        assert "requires exact triple IRIs" in str(exc)
    else:
        raise AssertionError("relationship repairs must identify exact RDF terms")


def test_framework_report_requires_repair_for_every_failed_source_item() -> None:
    report = _failed_report(["member-1", "member-2"])
    report["repair_plan"] = report["repair_plan"][:1]

    try:
        _validated_framework_report(report)
    except ValueError as exc:
        assert "member-2" in str(exc)
    else:
        raise AssertionError("every failed source item needs an atomic repair")
