"""Format-independent extraction semantic judge from the official runtime."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Callable

from src.extraction_runtime.llm import invoke_json as _invoke_json_object


DIMENSIONS = (
    "groundedness",
    "coverage",
    "semantic_correctness",
    "quantity_fidelity",
    "hallucination_control",
)
SEMANTIC_ACCEPTANCE_THRESHOLD = 0.95
DEDUCTION_LIMITS = {
    "none": 0.0,
    "low": 0.03,
    "medium": 0.10,
    "high": 0.30,
    "critical": 1.0,
}
SCORE_AUDIT_TOLERANCE = 0.005


@dataclass
class LLMJsonResult:
    data: dict[str, Any]
    elapsed_seconds: float = 0.0
    token_usage: dict[str, Any] | None = None
    raw_response: str = ""


def invoke_json(model: str, prompt: str, max_attempts: int = 3) -> LLMJsonResult:
    last_error = ""
    for attempt in range(max(1, max_attempts)):
        try:
            payload = _invoke_json_object(prompt, model_name=model)
            return LLMJsonResult(data=payload)
        except Exception as exc:
            last_error = str(exc)
            prompt = (
                prompt
                + "\n\nPrevious response was not parseable JSON. Return only one "
                "valid JSON object. Previous parse error: "
                + last_error
            )
    raise ValueError(f"semantic judge JSON invoke failed: {last_error}")


def _observation_id(kind: str, finding: dict[str, Any]) -> str:
    """Return a stable identifier for one evidence-backed semantic finding."""
    evidence = json.dumps(finding, ensure_ascii=False, sort_keys=True)
    digest = hashlib.sha256(evidence.encode("utf-8")).hexdigest()[:16]
    return f"semantic.{kind}::{digest}"


def semantic_observations(judgements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Project judge findings into stable, repair-pipeline observations."""
    observations: dict[str, dict[str, Any]] = {}
    finding_groups = (
        ("critical", "critical_errors"),
        ("missing", "missing_findings"),
        ("unsupported", "unsupported_findings"),
    )
    for judgement in judgements:
        model = str(judgement.get("model") or "")
        for kind, field in finding_groups:
            for raw in judgement.get(field) or []:
                finding = raw if isinstance(raw, dict) else {"description": str(raw)}
                if str(finding.get("severity") or "").strip().casefold() == "none":
                    continue
                observation_id = _observation_id(kind, finding)
                existing = observations.setdefault(
                    observation_id,
                    {
                        "observation_id": observation_id,
                        "check_id": f"semantic_abox.{kind}",
                        "subject_key": observation_id.rsplit("::", 1)[-1],
                        "stage": "semantic-content",
                        "status": "fail",
                        "observed_artifacts": ["candidate_abox"],
                        "blocked_by": [],
                        "evidence": finding,
                        "reported_by": [],
                    },
                )
                if model and model not in existing["reported_by"]:
                    existing["reported_by"].append(model)
    return sorted(observations.values(), key=lambda item: item["observation_id"])


def semantic_acceptance(
    consensus: dict[str, Any],
    *,
    threshold: float = SEMANTIC_ACCEPTANCE_THRESHOLD,
) -> dict[str, Any]:
    """Apply the all-dimensions semantic content gate."""
    scores = dict(consensus.get("scores") or {})
    failing_dimensions = {
        name: float(scores.get(name) or 0.0)
        for name in DIMENSIONS
        if float(scores.get(name) or 0.0) < threshold
    }
    overall = float(consensus.get("overall_score") or 0.0)
    critical_errors = list(consensus.get("critical_errors") or [])
    failures: list[str] = []
    if overall < threshold:
        failures.append("overall_score_below_threshold")
    if failing_dimensions:
        failures.append("dimension_score_below_threshold")
    if critical_errors:
        failures.append("critical_errors_present")
    return {
        "accepted": not failures,
        "threshold": threshold,
        "failures": failures,
        "overall_score": overall,
        "failing_dimensions": failing_dimensions,
        "critical_errors": critical_errors,
    }

def _validated_judgement(data: dict[str, Any]) -> dict[str, Any]:
    scores = data.get("scores")
    if not isinstance(scores, dict):
        raise ValueError("semantic judge response is missing `scores`")
    normalized_scores: dict[str, float] = {}
    for name in DIMENSIONS:
        value = float(scores.get(name))
        if not 0.0 <= value <= 1.0:
            raise ValueError(f"semantic judge score `{name}` is outside [0, 1]")
        normalized_scores[name] = value
    raw_scores = dict(normalized_scores)
    deductions = _validated_deductions(data.get("deductions"))
    audited_scores = {
        name: round(
            max(
                0.0,
                1.0
                - sum(
                    item["amount"]
                    for item in deductions
                    if item["dimension"] == name
                ),
            ),
            4,
        )
        for name in DIMENSIONS
    }
    inconsistent = {
        name: {
            "reported": raw_scores[name],
            "audited": audited_scores[name],
        }
        for name in DIMENSIONS
        if abs(raw_scores[name] - audited_scores[name]) > SCORE_AUDIT_TOLERANCE
    }
    if inconsistent:
        raise ValueError(
            "semantic judge scores are not reproducible from deductions: "
            + json.dumps(inconsistent, ensure_ascii=False, sort_keys=True)
        )
    overall = float(data.get("overall_score"))
    audited_overall = round(sum(audited_scores.values()) / len(DIMENSIONS), 4)
    if abs(overall - audited_overall) > SCORE_AUDIT_TOLERANCE:
        raise ValueError(
            "semantic judge overall_score is not the audited dimension mean: "
            f"reported={overall}, audited={audited_overall}"
        )
    confidence = float(data.get("confidence"))
    if not 0.0 <= overall <= 1.0 or not 0.0 <= confidence <= 1.0:
        raise ValueError("semantic judge overall_score/confidence is outside [0, 1]")
    return {
        **data,
        "raw_scores": raw_scores,
        "scores": audited_scores,
        "raw_overall_score": overall,
        "overall_score": audited_overall,
        "deductions": deductions,
        "score_audit": {
            "reproducible": True,
            "policy": "one_minus_evidence_backed_deductions",
            "severity_limits": DEDUCTION_LIMITS,
        },
        "confidence": confidence,
        "critical_errors": list(data.get("critical_errors") or []),
        "supported_findings": list(data.get("supported_findings") or []),
        "missing_findings": list(data.get("missing_findings") or []),
        "unsupported_findings": list(data.get("unsupported_findings") or []),
        "summary": str(data.get("summary") or ""),
    }


def _validated_deductions(raw: Any) -> list[dict[str, Any]]:
    """Validate a finite, evidence-backed ledger used to reproduce every score."""
    if not isinstance(raw, list):
        raise ValueError("semantic judge response is missing `deductions`")
    validated: list[dict[str, Any]] = []
    required_text = (
        "obligation_kind",
        "document_evidence",
        "ontology_evidence",
        "abox_evidence",
        "reason",
    )
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValueError(f"deductions[{index}] must be an object")
        dimension = str(item.get("dimension") or "").strip()
        severity = str(item.get("severity") or "").strip().casefold()
        amount = float(item.get("amount"))
        if dimension not in DIMENSIONS:
            raise ValueError(f"deductions[{index}] has unknown dimension {dimension!r}")
        if severity not in DEDUCTION_LIMITS:
            raise ValueError(f"deductions[{index}] has unknown severity {severity!r}")
        if amount < 0.0 or amount > DEDUCTION_LIMITS[severity] + 1e-9:
            raise ValueError(
                f"deductions[{index}] amount {amount} exceeds {severity} limit "
                f"{DEDUCTION_LIMITS[severity]}"
            )
        evidence = {name: str(item.get(name) or "").strip() for name in required_text}
        obligation_kind = evidence["obligation_kind"].casefold()
        if any(marker in obligation_kind for marker in ("optional", "conditional")) and (
            amount > DEDUCTION_LIMITS["low"] + 1e-9
        ):
            raise ValueError(
                f"deductions[{index}] conditional/optional amount {amount} exceeds "
                f"low-severity limit {DEDUCTION_LIMITS['low']}"
            )
        if not evidence["reason"] or not evidence["obligation_kind"]:
            raise ValueError(f"deductions[{index}] lacks reason/obligation_kind")
        if amount > 0 and not any(
            evidence[name]
            for name in ("document_evidence", "ontology_evidence", "abox_evidence")
        ):
            raise ValueError(f"deductions[{index}] lacks concrete evidence")
        validated.append(
            {
                "dimension": dimension,
                "severity": severity,
                "amount": round(amount, 4),
                **evidence,
            }
        )
    return validated


def invoke_validated_judgement(
    *,
    invoke: Callable[..., LLMJsonResult],
    model: str,
    prompt: str,
    max_validation_attempts: int = 3,
) -> tuple[dict[str, Any], list[LLMJsonResult]]:
    """Repair structurally invalid judge JSON with explicit validator feedback."""
    attempts: list[LLMJsonResult] = []
    current_prompt = prompt
    errors: list[str] = []
    for _ in range(max_validation_attempts):
        result = invoke(model, current_prompt, max_attempts=3)
        attempts.append(result)
        try:
            return _validated_judgement(result.data), attempts
        except (TypeError, ValueError) as exc:
            errors.append(str(exc))
            current_prompt = (
                prompt
                + "\n\nYOUR PREVIOUS JSON WAS REJECTED BY THE DETERMINISTIC SCORE AUDITOR:\n"
                + str(exc)
                + "\nPREVIOUS JSON:\n"
                + json.dumps(result.data, ensure_ascii=False)
                + "\nReturn a corrected complete JSON object in the exact requested schema. "
                "Include `deductions` even when it is an empty list, and recompute every score "
                "and overall_score exactly from that ledger."
            )
    raise ValueError(
        "semantic judge failed deterministic validation after "
        f"{max_validation_attempts} attempts: {errors}"
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

