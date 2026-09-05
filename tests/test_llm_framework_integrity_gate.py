from __future__ import annotations

import os
from pathlib import Path

import pytest
from rdflib import Graph, URIRef

from src.agents.scripts_and_prompts_generation.level1_code_repair import (
    LLMJsonResult,
)
from src.agents.scripts_and_prompts_generation.llm_semantic_abox_judge import (
    build_semantic_judge_prompt,
    judge_semantic_abox,
)
from src.pipelines.main_kg_building.build import (
    MAIN_KG_MAX_TOOL_CALLS,
    MAIN_KG_RECURSION_LIMIT,
)


def _framework_contract() -> dict:
    return {
        "classes": [
            {"class_iri": "urn:Root"},
            {"class_iri": "urn:Member"},
        ],
        "object_properties": [
            {
                "property_iri": "urn:contains",
                "domain_iris": ["urn:Root"],
                "range_iris": ["urn:Member"],
            }
        ],
        "iteration_audit_scope": {
            "owned_classes": ["Member"],
            "owned_object_properties": ["contains"],
        },
        "framework_integrity_audit": {
            "blocking": True,
            "priority": "highest",
            "policy": "Every scoped entity must be structurally integrated.",
        },
    }


def _fake_integrity_judge(_model: str, prompt: str, **_kwargs) -> LLMJsonResult:
    abox = prompt.split("CANDIDATE A-BOX (Turtle):", 1)[-1]
    graph = Graph()
    graph.parse(data=abox, format="turtle")
    linked = (
        URIRef("urn:root"),
        URIRef("urn:contains"),
        URIRef("urn:member"),
    ) in graph
    deductions = []
    critical_errors = []
    scores = {
        "groundedness": 1.0,
        "coverage": 1.0,
        "semantic_correctness": 1.0,
        "quantity_fidelity": 1.0,
        "hallucination_control": 1.0,
    }
    if not linked:
        critical_errors = ["The scoped member is a detached typed shell."]
        for dimension in ("coverage", "semantic_correctness"):
            scores[dimension] = 0.0
            deductions.append(
                {
                    "dimension": dimension,
                    "severity": "critical",
                    "amount": 1.0,
                    "obligation_kind": "framework_integrity",
                    "document_evidence": "The member belongs to the root.",
                    "ontology_evidence": "urn:contains connects Root to Member.",
                    "abox_evidence": "The typed member has no ownership edge.",
                    "reason": "A detached shell does not satisfy graph completeness.",
                }
            )
    overall = round(sum(scores.values()) / len(scores), 4)
    return LLMJsonResult(
        data={
            "scores": scores,
            "overall_score": overall,
            "deductions": deductions,
            "critical_errors": critical_errors,
            "supported_findings": [],
            "missing_findings": [],
            "unsupported_findings": [],
            "confidence": 1.0,
            "summary": "Synthetic LLM framework-integrity judgement.",
        },
        elapsed_seconds=0.0,
        token_usage={},
    )


def test_main_kg_tool_budget_is_four_hundred_calls() -> None:
    assert MAIN_KG_MAX_TOOL_CALLS == 400
    assert MAIN_KG_RECURSION_LIMIT >= (MAIN_KG_MAX_TOOL_CALLS * 2)


def test_framework_integrity_prompt_is_generic_and_blocking() -> None:
    prompt = build_semantic_judge_prompt(
        document_text="One member belongs to one root.",
        ontology_contract=_framework_contract(),
        abox_turtle="<urn:member> a <urn:Member> .",
    )

    assert "highest-priority blocking" in prompt
    assert "detached shells" in prompt
    assert "fixed edge counts" in prompt
    assert "hasSynthesisStep" not in prompt
    assert "SynthesisStep" not in prompt


def test_llm_framework_integrity_gate_is_stable_across_repeated_runs(
    tmp_path: Path,
) -> None:
    linked = tmp_path / "linked.ttl"
    linked.write_text(
        "<urn:root> a <urn:Root> ; <urn:contains> <urn:member> .\n"
        "<urn:member> a <urn:Member> .\n",
        encoding="utf-8",
    )
    detached = tmp_path / "detached.ttl"
    detached.write_text(
        "<urn:root> a <urn:Root> .\n<urn:member> a <urn:Member> .\n",
        encoding="utf-8",
    )

    for _ in range(20):
        accepted = judge_semantic_abox(
            document_text="One member belongs to one root.",
            ontology_contract=_framework_contract(),
            abox_path=linked,
            models=["framework-judge"],
            invoke=_fake_integrity_judge,
        )
        rejected = judge_semantic_abox(
            document_text="One member belongs to one root.",
            ontology_contract=_framework_contract(),
            abox_path=detached,
            models=["framework-judge"],
            invoke=_fake_integrity_judge,
        )

        assert accepted["acceptance"]["accepted"] is True
        assert rejected["acceptance"]["accepted"] is False
        assert rejected["acceptance"]["critical_errors"]


@pytest.mark.skipif(
    os.environ.get("RUN_LIVE_LLM_FRAMEWORK_GATE") != "1",
    reason="explicit opt-in required for live LLM gate probe",
)
def test_live_llm_framework_integrity_gate_repeatedly_rejects_detached_shell(
    tmp_path: Path,
) -> None:
    detached = tmp_path / "detached-live.ttl"
    detached.write_text(
        "<urn:root> a <urn:Root> .\n<urn:member> a <urn:Member> .\n",
        encoding="utf-8",
    )
    runs = int(os.environ.get("LLM_FRAMEWORK_GATE_RUNS", "3"))
    model = os.environ.get("LLM_FRAMEWORK_GATE_MODEL", "gpt-4o")

    for _ in range(runs):
        report = judge_semantic_abox(
            document_text=(
                "The source explicitly states that the member belongs to the root."
            ),
            ontology_contract=_framework_contract(),
            abox_path=detached,
            models=[model],
        )
        assert report["acceptance"]["accepted"] is False
        assert report["acceptance"]["failures"]
