"""LLM-based semantic quality assessment for document-grounded A-Boxes."""

from __future__ import annotations

import json
import hashlib
from pathlib import Path
from typing import Any, Callable

from rdflib import Graph

from src.agents.scripts_and_prompts_generation.level1_code_repair import (
    LLMJsonResult,
    invoke_json,
)


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


def build_semantic_judge_prompt(
    *,
    document_text: str,
    ontology_contract: dict[str, Any],
    abox_turtle: str,
) -> str:
    """Build a domain-neutral rubric prompt from runtime evidence."""
    return (
        "You are an independent semantic evaluator. Assess whether the candidate RDF A-Box "
        "faithfully represents the source document under the supplied machine-derived ontology "
        "contract. Do not require graph isomorphism, identical IRIs, a particular node layout, "
        "or agreement with a reference A-Box. Different RDF structures are acceptable when they "
        "express the same grounded meaning.\n\n"
        "Score each dimension from 0.0 to 1.0:\n"
        "- groundedness: asserted content is supported by the document or an applicable "
        "ontology-contract rule\n"
        "- coverage: important document facts representable by the contract are captured\n"
        "- semantic_correctness: classes and relationships convey the correct meaning\n"
        "- quantity_fidelity: numbers, units, and ordering are preserved where present\n"
        "- hallucination_control: unsupported substantive assertions are absent\n\n"
        "Evidence and scoring policy:\n"
        "- Treat both the source document and the supplied ontology contract as authoritative "
        "evidence. An assertion is grounded when it is directly supported by the document OR "
        "when a T-Box comment/rule authorizes a default, inheritance rule, derivation, or "
        "override policy whose stated conditions hold in the document/A-Box context.\n"
        "- Apply ontology rules exactly as supplied and generically. Do not import domain "
        "knowledge or invent defaults that are absent from the ontology contract. When a "
        "document statement conflicts with an ontology override rule, follow the precedence "
        "declared by that rule.\n"
        "- Do not mark an ontology-authorized default or derivation unsupported merely because "
        "the resulting value is not stated verbatim in the document. Cite the relevant "
        "ontology-contract rule as evidence in the assessment/reason.\n"
        "- Free-text literal content is outside this semantic score. Do not lower any dimension, "
        "create a critical error, or add an unsupported finding because of wording, factual "
        "content, speculation, completeness, or provenance inside descriptive/free-text string "
        "literals. Continue scoring structured assertions such as types, relationships, "
        "quantities, controlled identifiers, booleans, dates, and other contract-governed "
        "non-free-text values. A string-valued field is free text when the ontology contract "
        "describes it as a description, note, narrative, comment, summary, or other open-ended "
        "text; infer this only from the supplied contract, never from domain-specific names.\n\n"
        "Auditable deduction policy:\n"
        "- Begin every dimension at 1.0 and subtract only evidence-backed deductions.\n"
        "- Return one deduction-ledger item for every subtraction. Each item must contain exactly "
        "`dimension`, `severity`, `amount`, `obligation_kind`, `document_evidence`, "
        "`ontology_evidence`, `abox_evidence`, and `reason`.\n"
        "- Allowed severities and maximum deduction per item are: none=0.00, low=0.03, "
        "medium=0.10, high=0.30, critical=1.00. Use none only for explicitly non-penalized "
        "observations and set amount to 0. A conditional or optional fact can be at most low. "
        "A required ontology obligation or explicit important document fact may be medium/high "
        "only when concrete evidence proves it is missing or wrong.\n"
        "- Every score must equal 1.0 minus that dimension's ledger amounts, rounded to 4 "
        "decimals. A score below 1.0 without a matching deduction is invalid. overall_score must "
        "equal the arithmetic mean of the five audited dimension scores, rounded to 4 decimals.\n"
        "- Do not place positive/no-violation statements in unsupported_findings. Findings with "
        "severity none are informational and must not affect scores.\n\n"
        "Return only one JSON object with exactly these keys:\n"
        '{"scores":{"groundedness":0.0,"coverage":0.0,"semantic_correctness":0.0,'
        '"quantity_fidelity":0.0,"hallucination_control":0.0},'
        '"overall_score":0.0,"deductions":[{"dimension":"","severity":"","amount":0.0,'
        '"obligation_kind":"","document_evidence":"","ontology_evidence":"",'
        '"abox_evidence":"","reason":""}],"critical_errors":[],'
        '"supported_findings":[{"document_evidence":"","abox_evidence":"","assessment":""}],'
        '"missing_findings":[{"document_evidence":"","expected_semantics":"","severity":""}],'
        '"unsupported_findings":[{"abox_evidence":"","reason":"","severity":""}],'
        '"confidence":0.0,"summary":""}\n'
        "Every finding must cite concrete document, ontology-contract, and/or A-Box evidence. "
        "Before reporting unsupported content, explicitly check whether an ontology comment "
        "licenses it as a default or derivation and whether it is excluded as free text. Do not infer that "
        "logical consistency alone proves coverage. Do not penalize harmless provenance, "
        "identifier, or intermediate-node choices.\n\n"
        f"SOURCE DOCUMENT:\n{document_text}\n\n"
        "ONTOLOGY CONTRACT (machine-derived):\n"
        f"{json.dumps(ontology_contract, ensure_ascii=False, sort_keys=True)}\n\n"
        f"CANDIDATE A-BOX (Turtle):\n{abox_turtle}\n"
    )


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


def judge_semantic_abox(
    *,
    document_text: str,
    ontology_contract: dict[str, Any],
    abox_path: Path,
    models: list[str],
    invoke: Callable[..., LLMJsonResult] = invoke_json,
    disagreement_threshold: float = 0.2,
    acceptance_threshold: float = SEMANTIC_ACCEPTANCE_THRESHOLD,
    adjudicator_model: str | None = None,
) -> dict[str, Any]:
    """Run independent LLM judges and report a non-isomorphic semantic consensus."""
    graph = Graph()
    graph.parse(str(abox_path), format="turtle")
    abox_turtle = str(graph.serialize(format="turtle"))
    prompt = build_semantic_judge_prompt(
        document_text=document_text,
        ontology_contract=ontology_contract,
        abox_turtle=abox_turtle,
    )
    judge_models = [str(model).strip() for model in models if str(model).strip()]
    if not judge_models:
        raise ValueError("at least one semantic judge model is required")

    judgements: list[dict[str, Any]] = []
    total_usage: dict[str, int] = {}
    total_elapsed = 0.0
    for model in judge_models:
        result = invoke(model, prompt, max_attempts=3)
        judgement = _validated_judgement(result.data)
        judgements.append({"model": model, **judgement})
        total_elapsed += result.elapsed_seconds
        for key, value in (result.token_usage or {}).items():
            if isinstance(value, int):
                total_usage[key] = total_usage.get(key, 0) + value

    overall_scores = [item["overall_score"] for item in judgements]
    disagreement = max(overall_scores) - min(overall_scores)
    dimension_disagreement = max(
        max(item["scores"][name] for item in judgements)
        - min(item["scores"][name] for item in judgements)
        for name in DIMENSIONS
    )
    penalty_presence_disagreement = any(
        min(item["scores"][name] for item in judgements) < acceptance_threshold
        <= max(item["scores"][name] for item in judgements)
        for name in DIMENSIONS
    )
    audit_disagreement = (
        dimension_disagreement > disagreement_threshold
        or penalty_presence_disagreement
    )
    adjudication: dict[str, Any] | None = None
    adjudication_required = disagreement > disagreement_threshold or audit_disagreement
    if adjudication_required and adjudicator_model:
        adjudication_prompt = (
            prompt
            + "\n\nINDEPENDENT JUDGE REPORTS:\n"
            + json.dumps(judgements, ensure_ascii=False)
            + "\nAct as an adjudicator. Re-evaluate the original evidence, resolve the "
            "disagreement, and return the same JSON schema. Do not average scores mechanically."
        )
        result = invoke(adjudicator_model, adjudication_prompt, max_attempts=3)
        adjudication = {
            "model": adjudicator_model,
            **_validated_judgement(result.data),
        }
        total_elapsed += result.elapsed_seconds
        for key, value in (result.token_usage or {}).items():
            if isinstance(value, int):
                total_usage[key] = total_usage.get(key, 0) + value

    consensus_source = [adjudication] if adjudication else judgements
    consensus_scores = {
        name: round(
            sum(item["scores"][name] for item in consensus_source)
            / len(consensus_source),
            4,
        )
        for name in DIMENSIONS
    }
    consensus = {
        "overall_score": round(
            sum(item["overall_score"] for item in consensus_source)
            / len(consensus_source),
            4,
        ),
        "scores": consensus_scores,
        "max_overall_disagreement": round(disagreement, 4),
        "max_dimension_disagreement": round(dimension_disagreement, 4),
        "penalty_presence_disagreement": penalty_presence_disagreement,
        "needs_adjudication": adjudication_required and adjudication is None,
        "critical_errors": [
            error
            for item in consensus_source
            for error in item.get("critical_errors") or []
        ],
    }
    acceptance = semantic_acceptance(
        consensus,
        threshold=acceptance_threshold,
    )
    observations = semantic_observations(consensus_source)
    return {
        "schema_version": "semantic-abox-soft-score.v1",
        "ok": True,
        "policy": "non_isomorphic_llm_soft_score",
        "blocking": True,
        "judges": judgements,
        "adjudication": adjudication,
        "consensus": consensus,
        "acceptance": acceptance,
        "observations": observations,
        "abox_path": str(abox_path),
        "triple_count": len(graph),
        "elapsed_seconds": round(total_elapsed, 3),
        "token_usage": total_usage,
    }
