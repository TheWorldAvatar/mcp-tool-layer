"""Format-independent LLM assessment of document extraction semantics."""

from __future__ import annotations

import json
from typing import Any, Callable

from src.agents.scripts_and_prompts_generation.level1_code_repair import (
    LLMJsonResult,
    invoke_json,
)
from src.agents.scripts_and_prompts_generation.llm_semantic_abox_judge import (
    DIMENSIONS,
    SEMANTIC_ACCEPTANCE_THRESHOLD,
    _validated_judgement,
    semantic_acceptance,
    semantic_observations,
)


def build_extraction_judge_prompt(
    *,
    document_text: str,
    ontology_contract: dict[str, Any],
    extracted_content: Any,
) -> str:
    """Build a domain-neutral judge prompt without prescribing output shape."""
    return (
        "You are an independent semantic evaluator. Assess whether the extracted content "
        "faithfully captures the source document under the supplied machine-derived ontology "
        "contract. Evaluate meaning, not serialization. The extraction may use any JSON, text, "
        "nested, flat, identifier, reference, list, or object layout. Do not require field names, "
        "class-section keys, labels, IDs, reference conventions, ordering of fields, or agreement "
        "with a reference extraction. Treat semantically equivalent representations as equivalent.\n\n"
        "Score each dimension from 0.0 to 1.0:\n"
        "- groundedness: extracted claims are supported by the document\n"
        "- coverage: important document facts representable by the ontology are captured\n"
        "- semantic_correctness: extracted entities, relations, and roles convey the right meaning\n"
        "- quantity_fidelity: numbers, units, and ordering are preserved where present\n"
        "- hallucination_control: unsupported substantive claims are absent\n\n"
        "Return only one JSON object with exactly these keys:\n"
        '{"scores":{"groundedness":0.0,"coverage":0.0,"semantic_correctness":0.0,'
        '"quantity_fidelity":0.0,"hallucination_control":0.0},'
        '"overall_score":0.0,"critical_errors":[],'
        '"supported_findings":[{"document_evidence":"","extraction_evidence":"","assessment":""}],'
        '"missing_findings":[{"document_evidence":"","expected_semantics":"","severity":""}],'
        '"unsupported_findings":[{"extraction_evidence":"","reason":"","severity":""}],'
        '"confidence":0.0,"summary":""}\n'
        "Every finding must cite concrete source and/or extraction evidence. Do not penalize "
        "representation choices. The ontology contract limits what is semantically relevant; it "
        "does not define an extraction serialization schema.\n\n"
        f"SOURCE DOCUMENT:\n{document_text}\n\n"
        "ONTOLOGY CONTRACT (machine-derived):\n"
        f"{json.dumps(ontology_contract, ensure_ascii=False, sort_keys=True)}\n\n"
        "EXTRACTED CONTENT (representation is unconstrained):\n"
        f"{json.dumps(extracted_content, ensure_ascii=False, default=str)}\n"
    )


def judge_extraction_semantics(
    *,
    document_text: str,
    ontology_contract: dict[str, Any],
    extracted_content: Any,
    models: list[str],
    invoke: Callable[..., LLMJsonResult] = invoke_json,
    disagreement_threshold: float = 0.2,
    acceptance_threshold: float = SEMANTIC_ACCEPTANCE_THRESHOLD,
    adjudicator_model: str | None = None,
) -> dict[str, Any]:
    """Run independent, format-agnostic extraction judges."""
    prompt = build_extraction_judge_prompt(
        document_text=document_text,
        ontology_contract=ontology_contract,
        extracted_content=extracted_content,
    )
    judge_models = [str(model).strip() for model in models if str(model).strip()]
    if not judge_models:
        raise ValueError("at least one extraction judge model is required")

    judgements: list[dict[str, Any]] = []
    total_usage: dict[str, int] = {}
    total_elapsed = 0.0
    for model in judge_models:
        result = invoke(model, prompt, max_attempts=3)
        judgements.append({"model": model, **_validated_judgement(result.data)})
        total_elapsed += result.elapsed_seconds
        for key, value in (result.token_usage or {}).items():
            if isinstance(value, int):
                total_usage[key] = total_usage.get(key, 0) + value

    overall_scores = [item["overall_score"] for item in judgements]
    disagreement = max(overall_scores) - min(overall_scores)
    adjudication: dict[str, Any] | None = None
    if disagreement > disagreement_threshold and adjudicator_model:
        result = invoke(
            adjudicator_model,
            prompt
            + "\n\nINDEPENDENT JUDGE REPORTS:\n"
            + json.dumps(judgements, ensure_ascii=False)
            + "\nRe-evaluate the evidence and return the same JSON schema.",
            max_attempts=3,
        )
        adjudication = {
            "model": adjudicator_model,
            **_validated_judgement(result.data),
        }
        total_elapsed += result.elapsed_seconds
        for key, value in (result.token_usage or {}).items():
            if isinstance(value, int):
                total_usage[key] = total_usage.get(key, 0) + value

    consensus_source = [adjudication] if adjudication else judgements
    consensus = {
        "overall_score": round(
            sum(item["overall_score"] for item in consensus_source)
            / len(consensus_source),
            4,
        ),
        "scores": {
            name: round(
                sum(item["scores"][name] for item in consensus_source)
                / len(consensus_source),
                4,
            )
            for name in DIMENSIONS
        },
        "max_overall_disagreement": round(disagreement, 4),
        "needs_adjudication": disagreement > disagreement_threshold
        and adjudication is None,
        "critical_errors": [
            error
            for item in consensus_source
            for error in item.get("critical_errors") or []
        ],
    }
    return {
        "schema_version": "semantic-extraction-soft-score.v1",
        "ok": True,
        "policy": "format_independent_llm_soft_score",
        "blocking": True,
        "judges": judgements,
        "adjudication": adjudication,
        "consensus": consensus,
        "acceptance": semantic_acceptance(
            consensus,
            threshold=acceptance_threshold,
        ),
        "observations": semantic_observations(consensus_source),
        "elapsed_seconds": round(total_elapsed, 3),
        "token_usage": total_usage,
    }
