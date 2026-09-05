"""Format-independent LLM assessment of document extraction semantics."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable

from src.agents.scripts_and_prompts_generation.level1_code_repair import (
    LLMJsonResult,
    invoke_json,
)
from src.agents.scripts_and_prompts_generation.llm_semantic_abox_judge import (
    DIMENSIONS,
    SEMANTIC_ACCEPTANCE_THRESHOLD,
    invoke_validated_judgement,
    semantic_acceptance,
    semantic_observations,
)


def build_extraction_judge_prompt(
    *,
    document_text: str,
    ontology_contract: dict[str, Any],
    extracted_content: Any,
    reference_content: Any | None = None,
    prior_feedback: list[str] | None = None,
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
        "Auditable scoring policy:\n"
        "- Begin every dimension at 1.0 and subtract only evidence-backed deductions.\n"
        "- Include one `deductions` ledger item per subtraction, using the exact fields shown "
        "below. Allowed severity/amount maxima are none=0.00, low=0.03, medium=0.10, "
        "high=0.30, critical=1.00.\n"
        "- Every dimension score must equal 1.0 minus its deduction amounts, rounded to 4 "
        "decimals. overall_score must equal the arithmetic mean of the five scores.\n\n"
        "Return only one JSON object with exactly these keys:\n"
        '{"scores":{"groundedness":0.0,"coverage":0.0,"semantic_correctness":0.0,'
        '"quantity_fidelity":0.0,"hallucination_control":0.0},'
        '"overall_score":0.0,"deductions":[{"dimension":"","severity":"","amount":0.0,'
        '"obligation_kind":"","document_evidence":"","ontology_evidence":"",'
        '"abox_evidence":"","reason":""}],"critical_errors":[],'
        '"supported_findings":[{"document_evidence":"","extraction_evidence":"","assessment":""}],'
        '"missing_findings":[{"document_evidence":"","expected_semantics":"","severity":""}],'
        '"unsupported_findings":[{"extraction_evidence":"","reason":"","severity":""}],'
        '"confidence":0.0,"summary":""}\n'
        "Every finding must cite concrete source and/or extraction evidence. Do not penalize "
        "representation choices. The ontology contract limits what is semantically relevant; it "
        "does not define an extraction serialization schema.\n"
        "- A grounded reference, inheritance, delegation, or provenance relation can itself "
        "represent the referenced content. Do not deduct coverage merely because the extraction "
        "does not duplicate or eagerly expand every fact reachable through that relation. Require "
        "closure expansion only when the supplied ontology contract explicitly requires "
        "materialization of the referenced facts for this entity.\n"
        "- Distinguish an omitted source fact from a downstream materialization failure. This "
        "judge evaluates extracted content only; if a fact is present in the extraction, do not "
        "penalize extraction coverage because a later KG may fail to assert it.\n"
        "- Use a strict closed evidence boundary: only the supplied source document and an "
        "explicit applicable ontology-contract rule may create a coverage obligation. Never use "
        "external knowledge, memorized facts, customary interpretations, typical roles, or "
        "expected practice to require a missing fact.\n"
        "- A source token supports only the meaning explicitly expressed by that token in the "
        "supplied source context. Do not construct, normalize into a new claim, canonicalize, or "
        "require an additional value from a name, identifier, measurement, composition-like "
        "string, or general familiarity unless the source states that value or the contract "
        "supplies an explicit applicable derivation rule.\n"
        "- Property availability, domain, or range does not make a property mandatory. Report a "
        "missing property only when the source explicitly supplies its value and the contract "
        "permits that exact semantic mapping, or when an explicit contract rule requires it. "
        "Do not coerce a source value into a merely available property whose contract meaning "
        "does not exactly match the source meaning.\n"
        "- Be conservative under ambiguity. If a proposed missing assertion could also be judged "
        "unsupported or contract-inapplicable from the same supplied evidence, emit no deduction "
        "for it rather than forcing a retry.\n\n"
        f"SOURCE DOCUMENT:\n{document_text}\n\n"
        "ONTOLOGY CONTRACT (machine-derived):\n"
        f"{json.dumps(ontology_contract, ensure_ascii=False, sort_keys=True)}\n\n"
        "EXTRACTED CONTENT (representation is unconstrained):\n"
        f"{json.dumps(extracted_content, ensure_ascii=False, default=str)}\n"
        + (
            "\nSOURCE-GROUNDED FIXTURE PROJECTION FOR THIS STAGE:\n"
            + f"{json.dumps(reference_content, ensure_ascii=False, default=str)}\n"
            + "Use this projection as an auditable list of expected stage-owned facts. "
            "Compare semantics rather than serialization and do not require facts outside "
            "this projection solely because they appear elsewhere in the document.\n"
            if reference_content is not None
            else ""
        )
        + (
            "\nPRIOR RETRY AUDIT FEEDBACK (fallible consistency evidence, not ontology "
            "rules):\n"
            + "\n\n".join(str(item) for item in prior_feedback if str(item).strip())
            + "\nBefore issuing a new deduction, check it against these prior findings. Do "
            "not alternately require and prohibit the same assertion from unchanged source and "
            "contract evidence. Reverse a prior position only when the current reason cites "
            "specific supplied source or contract evidence that resolves the contradiction; "
            "otherwise make the ambiguous finding non-penalizing.\n"
            if prior_feedback
            else ""
        )
    )


def judge_extraction_semantics(
    *,
    document_text: str,
    ontology_contract: dict[str, Any],
    extracted_content: Any,
    models: list[str],
    reference_content: Any | None = None,
    prior_feedback: list[str] | None = None,
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
        reference_content=reference_content,
        prior_feedback=prior_feedback,
    )
    judge_models = [str(model).strip() for model in models if str(model).strip()]
    if not judge_models:
        raise ValueError("at least one extraction judge model is required")

    judgements: list[dict[str, Any]] = []
    total_usage: dict[str, int] = {}
    total_elapsed = 0.0
    for model in judge_models:
        judgement, results = invoke_validated_judgement(
            invoke=invoke,
            model=model,
            prompt=prompt,
        )
        judgements.append({"model": model, **judgement})
        for result in results:
            total_elapsed += result.elapsed_seconds
            for key, value in (result.token_usage or {}).items():
                if isinstance(value, int):
                    total_usage[key] = total_usage.get(key, 0) + value

    overall_scores = [item["overall_score"] for item in judgements]
    disagreement = max(overall_scores) - min(overall_scores)
    adjudication: dict[str, Any] | None = None
    if disagreement > disagreement_threshold and adjudicator_model:
        adjudicated, results = invoke_validated_judgement(
            invoke=invoke,
            model=adjudicator_model,
            prompt=prompt
            + "\n\nINDEPENDENT JUDGE REPORTS:\n"
            + json.dumps(judgements, ensure_ascii=False)
            + "\nRe-evaluate the evidence and return the same JSON schema.",
        )
        adjudication = {
            "model": adjudicator_model,
            **adjudicated,
        }
        for result in results:
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


def build_extraction_delta_judge_prompt(
    *,
    document_text: str,
    ontology_contract: dict[str, Any],
    reference_content: Any,
    before_content: Any,
    after_content: Any,
    repair_focus: dict[str, Any],
) -> str:
    """Build a paired prompt whose verdict is independent of absolute score severity."""
    return (
        "You are a paired semantic regression evaluator. Compare BEFORE and AFTER extraction "
        "within one shared judgement. Do not assign numeric scores or severities.\n\n"
        "Use this fixed classification policy:\n"
        "1. target_fixed is true only when AFTER fixes the stated repair focus.\n"
        "   Evaluate target_fixed only from the current extraction artifacts. Ignore any repair "
        "acceptance condition about downstream KG, later iterations, or final TTL because those "
        "stages are intentionally unavailable in this targeted comparison.\n"
        "2. A new regression exists only when a source/T-Box/fixture-projected obligation was "
        "satisfied in BEFORE but is absent, unsupported, semantically wrong, or quantitatively "
        "wrong in AFTER.\n"
        "3. A defect present in both BEFORE and AFTER is an unchanged_defect, never a regression, "
        "regardless of wording, salience, or perceived severity.\n"
        "4. An improvement is a BEFORE defect corrected in AFTER.\n"
        "5. Ignore formatting, ordering, identifiers, verbosity, and representation changes when "
        "the semantics are equivalent.\n"
        "   In particular, removing procedural prose from an amount is not a regression when the "
        "same numeric value, multiplicity, and unit remain present.\n"
        "6. verdict must be accept exactly when target_fixed is true and new_regressions is empty; "
        "otherwise verdict must be reject.\n\n"
        "Return only one JSON object with exactly these keys:\n"
        '{"target_fixed":false,"target_evidence":[{"before":"","after":"","assessment":""}],'
        '"new_regressions":[{"category":"","obligation":"","before_evidence":"",'
        '"after_evidence":"","source_or_contract_evidence":"","reason":""}],'
        '"unchanged_defects":[{"obligation":"","before_evidence":"","after_evidence":""}],'
        '"improvements":[{"obligation":"","before_evidence":"","after_evidence":"",'
        '"is_target":false}],'
        '"verdict":"reject","confidence":0.0,"summary":""}\n'
        "Allowed new_regressions.category values are new_missing_fact, new_unsupported_fact, "
        "new_semantic_error, and new_quantity_error. Cite both BEFORE and AFTER evidence for "
        "every regression; an empty or generic BEFORE citation is invalid.\n\n"
        f"REPAIR FOCUS:\n{json.dumps(repair_focus, ensure_ascii=False, sort_keys=True)}\n\n"
        f"SOURCE DOCUMENT:\n{document_text}\n\n"
        "ONTOLOGY CONTRACT:\n"
        f"{json.dumps(ontology_contract, ensure_ascii=False, sort_keys=True)}\n\n"
        "SOURCE-GROUNDED STAGE PROJECTION:\n"
        f"{json.dumps(reference_content, ensure_ascii=False, sort_keys=True, default=str)}\n\n"
        "BEFORE EXTRACTION:\n"
        f"{json.dumps(before_content, ensure_ascii=False, sort_keys=True, default=str)}\n\n"
        "AFTER EXTRACTION:\n"
        f"{json.dumps(after_content, ensure_ascii=False, sort_keys=True, default=str)}\n"
    )


def validate_extraction_delta_judgement(payload: dict[str, Any]) -> None:
    """Validate paired delta output and derive the verdict mechanically."""
    required = {
        "target_fixed",
        "target_evidence",
        "new_regressions",
        "unchanged_defects",
        "improvements",
        "verdict",
        "confidence",
        "summary",
    }
    if set(payload) != required:
        raise ValueError(
            "delta judgement keys must equal " + ", ".join(sorted(required))
        )
    if not isinstance(payload["target_fixed"], bool):
        raise ValueError("target_fixed must be boolean")
    for key in (
        "target_evidence",
        "new_regressions",
        "unchanged_defects",
        "improvements",
    ):
        if not isinstance(payload[key], list):
            raise ValueError(f"{key} must be a list")
    allowed_categories = {
        "new_missing_fact",
        "new_unsupported_fact",
        "new_semantic_error",
        "new_quantity_error",
    }
    regression_fields = {
        "category",
        "obligation",
        "before_evidence",
        "after_evidence",
        "source_or_contract_evidence",
        "reason",
    }
    for index, item in enumerate(payload["new_regressions"]):
        if not isinstance(item, dict) or set(item) != regression_fields:
            raise ValueError(f"new_regressions[{index}] has invalid fields")
        if item["category"] not in allowed_categories:
            raise ValueError(f"new_regressions[{index}] has invalid category")
        if not str(item["before_evidence"]).strip():
            raise ValueError(
                f"new_regressions[{index}] requires concrete BEFORE evidence"
            )
    improvement_fields = {
        "obligation",
        "before_evidence",
        "after_evidence",
        "is_target",
    }
    for index, item in enumerate(payload["improvements"]):
        if not isinstance(item, dict) or set(item) != improvement_fields:
            raise ValueError(f"improvements[{index}] has invalid fields")
        if not isinstance(item["is_target"], bool):
            raise ValueError(f"improvements[{index}].is_target must be boolean")
    has_target_improvement = any(
        bool(item["is_target"]) for item in payload["improvements"]
    )
    if payload["target_fixed"] != has_target_improvement:
        raise ValueError(
            "target_fixed must equal whether improvements contains is_target=true"
        )
    expected_verdict = (
        "accept"
        if payload["target_fixed"] and not payload["new_regressions"]
        else "reject"
    )
    if payload["verdict"] != expected_verdict:
        raise ValueError(
            f"verdict must be {expected_verdict} from target/regression fields"
        )
    confidence = payload["confidence"]
    if not isinstance(confidence, (int, float)) or isinstance(confidence, bool):
        raise ValueError("confidence must be numeric")
    if not 0.0 <= float(confidence) <= 1.0:
        raise ValueError("confidence must be between 0 and 1")


def judge_extraction_delta_stability(
    *,
    document_text: str,
    ontology_contract: dict[str, Any],
    reference_content: Any,
    before_content: Any,
    after_content: Any,
    repair_focus: dict[str, Any],
    model: str,
    repeats: int = 3,
    max_workers: int | None = None,
    invoke: Callable[..., LLMJsonResult] = invoke_json,
) -> dict[str, Any]:
    """Repeat one paired judgement and require unanimous regression verdicts."""
    prompt = build_extraction_delta_judge_prompt(
        document_text=document_text,
        ontology_contract=ontology_contract,
        reference_content=reference_content,
        before_content=before_content,
        after_content=after_content,
        repair_focus=repair_focus,
    )
    repeat_count = max(1, repeats)
    worker_count = max(1, min(repeat_count, max_workers or repeat_count))
    indexed_results: dict[int, LLMJsonResult] = {}

    def run_once(run_index: int) -> tuple[int, LLMJsonResult]:
        attempt_prompt = prompt
        for schema_attempt in range(1, 4):
            result = invoke(
                model=model,
                prompt=attempt_prompt,
                max_attempts=3,
                provider_max_retries=0,
            )
            try:
                validate_extraction_delta_judgement(result.data)
                return run_index, result
            except ValueError as exc:
                if schema_attempt >= 3:
                    raise
                attempt_prompt = (
                    prompt
                    + "\n\n## REQUIRED SCHEMA REPAIR\n"
                    + f"The previous output failed validation: {exc}\n"
                    + "Return a corrected complete JSON object only. Do not add, "
                    + "remove, or rename fields.\n"
                    + "Previous invalid JSON:\n"
                    + json.dumps(result.data, ensure_ascii=False)
                )
        raise RuntimeError("unreachable delta-judge schema retry state")

    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = [
            executor.submit(run_once, run_index)
            for run_index in range(1, repeat_count + 1)
        ]
        for future in as_completed(futures):
            run_index, result = future.result()
            indexed_results[run_index] = result

    runs: list[dict[str, Any]] = []
    total_usage: dict[str, int] = {}
    total_elapsed = 0.0
    for run_index in range(1, repeat_count + 1):
        result = indexed_results[run_index]
        judgement = result.data
        validate_extraction_delta_judgement(judgement)
        runs.append({"run": run_index, **judgement})
        total_elapsed += result.elapsed_seconds
        for key, value in (result.token_usage or {}).items():
            if isinstance(value, int):
                total_usage[key] = total_usage.get(key, 0) + value
    signatures = {
        (
            bool(run["target_fixed"]),
            str(run["verdict"]),
            tuple(
                sorted(
                    (
                        str(item["category"]),
                        str(item["obligation"]).strip().casefold(),
                    )
                    for item in run["new_regressions"]
                )
            ),
        )
        for run in runs
    }
    unanimous = len(signatures) == 1
    accepted = bool(
        unanimous
        and runs[0]["target_fixed"]
        and runs[0]["verdict"] == "accept"
        and not runs[0]["new_regressions"]
    )
    return {
        "schema_version": "semantic-extraction-paired-delta.v1",
        "ok": True,
        "policy": "paired_fixed_regression_classification",
        "repeats": len(runs),
        "unanimous": unanimous,
        "accepted": accepted,
        "runs": runs,
        "elapsed_seconds": round(total_elapsed, 3),
        "token_usage": total_usage,
    }
