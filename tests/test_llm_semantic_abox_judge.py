from __future__ import annotations

from pathlib import Path

from src.agents.scripts_and_prompts_generation.level1_code_repair import (
    LLMJsonResult,
)
from src.agents.scripts_and_prompts_generation.llm_semantic_abox_judge import (
    _validated_judgement,
    build_semantic_judge_prompt,
    judge_semantic_abox,
    semantic_acceptance,
    semantic_observations,
)
from src.agents.scripts_and_prompts_generation.llm_extraction_judge import (
    build_extraction_judge_prompt,
    judge_extraction_semantics,
)


def _judgement(score: float) -> LLMJsonResult:
    amount = round(1.0 - score, 4)
    severity = (
        "none"
        if amount == 0
        else "low"
        if amount <= 0.03
        else "medium"
        if amount <= 0.10
        else "high"
        if amount <= 0.30
        else "critical"
    )
    return LLMJsonResult(
        data={
            "scores": {
                "groundedness": score,
                "coverage": score,
                "semantic_correctness": score,
                "quantity_fidelity": score,
                "hallucination_control": score,
            },
            "overall_score": score,
            "deductions": [
                {
                    "dimension": dimension,
                    "severity": severity,
                    "amount": amount,
                    "obligation_kind": "explicit_document_fact",
                    "document_evidence": "Neutral evidence.",
                    "ontology_evidence": "",
                    "abox_evidence": "Candidate evidence.",
                    "reason": "Synthetic test deduction.",
                }
                for dimension in (
                    "groundedness",
                    "coverage",
                    "semantic_correctness",
                    "quantity_fidelity",
                    "hallucination_control",
                )
                if amount
            ],
            "critical_errors": [],
            "supported_findings": [],
            "missing_findings": [],
            "unsupported_findings": [],
            "confidence": 0.8,
            "summary": "Evidence-based assessment.",
        },
        elapsed_seconds=1.0,
        token_usage={"total_tokens": 10},
    )


def test_semantic_judge_prompt_does_not_require_graph_isomorphism() -> None:
    prompt = build_semantic_judge_prompt(
        document_text="A neutral item was heated for 2 h.",
        ontology_contract={"classes": ["NeutralItem"]},
        abox_turtle="<urn:item> <urn:hasDuration> <urn:duration> .",
    )

    assert "Do not require graph isomorphism" in prompt
    assert "quantity_fidelity" in prompt
    assert "machine-derived" in prompt
    assert "Auditable deduction policy" in prompt
    assert "score below 1.0 without a matching deduction is invalid" in prompt


def test_semantic_judge_accepts_tbox_authorized_defaults_generically() -> None:
    prompt = build_semantic_judge_prompt(
        document_text="A neutral operation occurred.",
        ontology_contract={
            "datatype_properties": [
                {
                    "property_iri": "urn:hasPolicyValue",
                    "comment": (
                        "Default to true unless explicitly stated otherwise; "
                        "an explicit source value overrides the default."
                    ),
                }
            ]
        },
        abox_turtle="<urn:step> <urn:hasPolicyValue> true .",
    )

    assert "source document and the supplied ontology contract" in prompt
    assert "default, inheritance rule, derivation, or override policy" in prompt
    assert "not stated verbatim in the document" in prompt
    assert "isSealed" not in prompt


def test_semantic_judge_excludes_contract_identified_free_text_from_scoring() -> None:
    prompt = build_semantic_judge_prompt(
        document_text="A neutral item exists.",
        ontology_contract={
            "datatype_properties": [
                {
                    "property_iri": "urn:hasNarrative",
                    "comment": "An open-ended narrative description.",
                }
            ]
        },
        abox_turtle='<urn:item> <urn:hasNarrative> "unverified prose" .',
    )

    assert "Free-text literal content is outside this semantic score" in prompt
    assert "Do not lower any dimension" in prompt
    assert "infer this only from the supplied contract" in prompt


def test_semantic_judge_prompt_scopes_prior_facts_and_negative_evidence() -> None:
    prompt = build_semantic_judge_prompt(
        document_text=(
            "For the occurrence:\n- hasYield: 49%\n"
            "No laboratory equipment is explicitly named."
        ),
        ontology_contract={
            "iteration_audit_scope": {
                "policy": "trusted prior iterations; negative evidence means omit",
                "owned_object_properties": ["hasEquipment", "hasYield"],
            }
        },
        abox_turtle=(
            "<urn:syn> <urn:hasChemicalInput> <urn:in> ; "
            "<urn:hasYield> <urn:yield> ."
        ),
    )

    assert "Iteration-scoped audit" in prompt
    assert "trusted out-of-scope context" in prompt
    assert "Prior-iteration structure that remains in the graph is not a hallucination" in prompt
    assert "Negative evidence" in prompt
    assert "Correct omission is successful coverage" in prompt
    assert "Do not invent a positive obligation" in prompt


def test_extraction_judge_is_format_independent() -> None:
    prompt = build_extraction_judge_prompt(
        document_text="A sample was heated for 2 h.",
        ontology_contract={"classes": ["Step"]},
        extracted_content={"arbitrary": {"nested": ["heated", "2 h"]}},
        prior_feedback=[
            "Require hasAmount for a reported yield.",
            "Reject hasAmount for the same reported yield.",
        ],
    )

    assert "Evaluate meaning, not serialization" in prompt
    assert "Do not require field names" in prompt
    assert "does not define an extraction serialization schema" in prompt
    assert '"deductions"' in prompt
    assert "strict closed evidence boundary" in prompt
    assert "Never use external knowledge" in prompt
    assert "A source token supports only the meaning explicitly expressed" in prompt
    assert "Do not coerce a source value into a merely available property" in prompt
    assert "molecular formula" not in prompt
    assert "elemental analysis" not in prompt
    assert "yield property" not in prompt
    assert "Do not alternately require and prohibit the same assertion" in prompt
    assert "Require hasAmount for a reported yield." in prompt


def test_extraction_judge_uses_soft_semantic_acceptance() -> None:
    report = judge_extraction_semantics(
        document_text="A neutral item exists.",
        ontology_contract={"classes": ["NeutralItem"]},
        extracted_content="Neutral item",
        models=["judge-a"],
        invoke=lambda *_args, **_kwargs: _judgement(0.96),
    )

    assert report["policy"] == "format_independent_llm_soft_score"
    assert report["acceptance"]["accepted"] is True


def test_extraction_judge_repairs_invalid_score_ledger() -> None:
    invalid = _judgement(0.96)
    invalid.data.pop("deductions")
    responses = iter([invalid, _judgement(0.96)])
    prompts: list[str] = []

    def invoke(_model: str, prompt: str, **_kwargs) -> LLMJsonResult:
        prompts.append(prompt)
        return next(responses)

    report = judge_extraction_semantics(
        document_text="A neutral item exists.",
        ontology_contract={"classes": ["NeutralItem"]},
        extracted_content="Neutral item",
        models=["judge-a"],
        invoke=invoke,
    )

    assert report["acceptance"]["accepted"] is True
    assert len(prompts) == 2
    assert "missing `deductions`" in prompts[1]


def test_semantic_judge_aggregates_independent_soft_scores(tmp_path: Path) -> None:
    abox = tmp_path / "abox.ttl"
    abox.write_text("<urn:item> a <urn:NeutralItem> .\n", encoding="utf-8")
    responses = iter([_judgement(0.8), _judgement(0.6)])

    report = judge_semantic_abox(
        document_text="A neutral item exists.",
        ontology_contract={"classes": ["NeutralItem"]},
        abox_path=abox,
        models=["judge-a", "judge-b"],
        invoke=lambda *_args, **_kwargs: next(responses),
    )

    assert report["blocking"] is True
    assert report["consensus"]["overall_score"] == 0.7
    assert report["consensus"]["needs_adjudication"] is True
    assert report["acceptance"]["accepted"] is False
    assert report["observations"] == []
    assert report["triple_count"] == 1


def test_semantic_acceptance_requires_every_dimension_and_no_critical_errors() -> None:
    consensus = {
        "overall_score": 0.97,
        "scores": {
            "groundedness": 0.98,
            "coverage": 0.94,
            "semantic_correctness": 0.99,
            "quantity_fidelity": 0.96,
            "hallucination_control": 0.97,
        },
        "critical_errors": [],
    }

    report = semantic_acceptance(consensus)

    assert report["accepted"] is False
    assert report["failing_dimensions"] == {"coverage": 0.94}


def test_semantic_judge_uses_adjudicator_when_judges_disagree(tmp_path: Path) -> None:
    abox = tmp_path / "abox.ttl"
    abox.write_text("<urn:item> a <urn:NeutralItem> .\n", encoding="utf-8")
    responses = iter([_judgement(0.95), _judgement(0.5), _judgement(0.96)])

    report = judge_semantic_abox(
        document_text="A neutral item exists.",
        ontology_contract={"classes": ["NeutralItem"]},
        abox_path=abox,
        models=["judge-a", "judge-b"],
        adjudicator_model="judge-c",
        invoke=lambda *_args, **_kwargs: next(responses),
    )

    assert report["adjudication"]["model"] == "judge-c"
    assert report["consensus"]["overall_score"] == 0.96
    assert report["consensus"]["needs_adjudication"] is False
    assert report["acceptance"]["accepted"] is True


def test_score_is_recomputed_from_evidence_backed_deductions() -> None:
    data = _judgement(0.97).data

    validated = _validated_judgement(data)

    assert validated["scores"]["coverage"] == 0.97
    assert validated["overall_score"] == 0.97
    assert validated["score_audit"]["reproducible"] is True


def test_unexplained_low_score_is_rejected() -> None:
    data = _judgement(1.0).data
    data["scores"]["coverage"] = 0.91
    data["overall_score"] = 0.982

    try:
        _validated_judgement(data)
    except ValueError as exc:
        assert "not reproducible from deductions" in str(exc)
    else:
        raise AssertionError("unexplained score should be rejected")


def test_optional_low_severity_deduction_is_capped() -> None:
    data = _judgement(1.0).data
    data["scores"]["coverage"] = 0.95
    data["overall_score"] = 0.99
    data["deductions"] = [
        {
            "dimension": "coverage",
            "severity": "low",
            "amount": 0.05,
            "obligation_kind": "conditional_optional",
            "document_evidence": "A heading.",
            "ontology_evidence": "Create when identifiable.",
            "abox_evidence": "No anchor node.",
            "reason": "Conditional anchor omitted.",
        }
    ]

    try:
        _validated_judgement(data)
    except ValueError as exc:
        assert "exceeds low limit" in str(exc)
    else:
        raise AssertionError("low-severity deduction cap should be enforced")


def test_severity_none_findings_do_not_become_fail_observations() -> None:
    observations = semantic_observations(
        [
            {
                "model": "judge-a",
                "unsupported_findings": [
                    {
                        "abox_evidence": "No violation.",
                        "reason": "Ontology-authorized default.",
                        "severity": "none",
                    }
                ],
            }
        ]
    )

    assert observations == []


def test_dimension_threshold_split_requests_adjudication(tmp_path: Path) -> None:
    abox = tmp_path / "abox.ttl"
    abox.write_text("<urn:item> a <urn:NeutralItem> .\n", encoding="utf-8")
    first = _judgement(0.96)
    second = _judgement(0.96)
    second.data["scores"]["coverage"] = 0.94
    second.data["overall_score"] = 0.956
    second.data["deductions"] = [
        {
            "dimension": "coverage",
            "severity": "medium",
            "amount": 0.06,
            "obligation_kind": "required_document_fact",
            "document_evidence": "Required fact.",
            "ontology_evidence": "Representable fact.",
            "abox_evidence": "Missing fact.",
            "reason": "Required fact omitted.",
        },
        *[
            item
            for item in second.data["deductions"]
            if item["dimension"] != "coverage"
        ],
    ]
    responses = iter([first, second])

    report = judge_semantic_abox(
        document_text="A neutral item exists.",
        ontology_contract={"classes": ["NeutralItem"]},
        abox_path=abox,
        models=["judge-a", "judge-b"],
        invoke=lambda *_args, **_kwargs: next(responses),
    )

    assert report["consensus"]["penalty_presence_disagreement"] is True
    assert report["consensus"]["needs_adjudication"] is True
