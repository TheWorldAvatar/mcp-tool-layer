"""LLM adjudication for code-selected atomic materialization candidates."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Callable, Mapping

from src.agents.scripts_and_prompts_generation.materialization_operation_units import (
    compile_materialization_operation_units,
    discover_materialization_operation_candidates,
)
from src.agents.scripts_and_prompts_generation.level1_code_repair import invoke_json


JsonPlanner = Callable[[str, str], dict[str, Any]]
SCHEMA_VERSION = "materialization-operation-decisions.v1"


def invoke_operation_judge(model: str, prompt: str) -> dict[str, Any]:
    result = invoke_json(
        model,
        prompt,
        timeout_seconds=600,
        max_attempts=3,
        provider_max_retries=0,
    )
    return dict(result.data)


def _input_sha256(candidates: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        candidates, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _judge_prompt(
    candidates: Mapping[str, Any],
    *,
    repair_errors: list[str] | None = None,
    previous_response: Mapping[str, Any] | None = None,
) -> str:
    repair = (
        "\nREPAIR MODE\nThe previous response was invalid:\n- "
        + "\n- ".join(repair_errors or [])
        + "\nChange only the fields identified by these errors. Preserve every valid "
        "candidate decision and valid field from the previous response. Return the complete "
        "corrected object, not a patch.\nPREVIOUS RESPONSE:\n"
        + json.dumps(previous_response or {}, indent=2, ensure_ascii=False)
        if repair_errors
        else ""
    )
    return (
        "You are deciding operation boundaries for an ontology-derived MCP tool surface.\n"
        "The code has already removed structurally illegal candidates. Judge every remaining "
        "candidate independently from its supplied T-Box comments and structural evidence.\n\n"
        "Choose `merge` only when the relation is an invariant part of creating one owner "
        "occurrence, so splitting it into later Agent calls can create an invalid partial "
        "business operation. Keep it `separate` when the relation is optional, plural/unbounded, "
        "reusable/shared, cross-iteration, evidence-dependent after creation, or semantically "
        "independent.\n\n"
        "For an owned dependent, `merge` requires evidence that each owner has exactly one "
        "fresh owner-local dependent occurrence. For container membership, `merge` requires "
        "evidence that every created ordered member belongs to exactly one existing container "
        "and the supplied membership predicate is the unique compatible predicate.\n\n"
        "Cardinality is evaluated from the created owner/member occurrence toward the required "
        "edge target, not from the container toward its collection. A container may have many "
        "ordered members while each created member still requires exactly one container. For a "
        "`container_membership` candidate, choose `merge` with `cardinality=exactly_one` when "
        "the supplied structural evidence marks the class as an ordered member, identifies one "
        "unique compatible membership predicate, identifies a single-valued ordering property, "
        "and the T-Box evidence describes membership in the container, unless supplied evidence "
        "affirmatively permits a member to be shared across containers or created independently. "
        "Do not downgrade such a candidate merely because the container can contain many members "
        "or because the T-Box does not spell out an inverse cardinality axiom.\n\n"
        "For `owned_dependent`, do not infer freshness or exactly-one cardinality from domain/range "
        "alone. Both must be supported by the supplied lifecycle/cardinality wording; otherwise "
        "choose `separate`.\n\n"
        "Evidence quote protocol:\n"
        "- For `merge`, copy at least one exact, contiguous substring from a string value under "
        "that candidate's `tbox_evidence`; copying one complete evidence value is safest.\n"
        "- Do not paraphrase, normalize punctuation, add ellipses, or quote structural_evidence.\n"
        "- For `separate`, `evidence_quotes` may be empty. If no exact T-Box substring directly "
        "supports the separation reason, return an empty list instead of a rationale paraphrase.\n\n"
        "Return one JSON object only:\n"
        "{\n"
        f'  "schema_version": "{SCHEMA_VERSION}",\n'
        '  "decisions": [\n'
        "    {\n"
        '      "candidate_id": "<exact supplied id>",\n'
        '      "decision": "merge|separate",\n'
        '      "cardinality": "exactly_one|optional|plural_or_unbounded|unclear",\n'
        '      "lifecycle": "fresh_per_owner|shared_or_reusable|existing_reference|unclear",\n'
        '      "evidence_quotes": ["<exact substring copied from supplied T-Box evidence>"],\n'
        '      "rationale": "<brief operation-boundary reasoning>"\n'
        "    }\n"
        "  ]\n"
        "}\n\n"
        "Do not invent candidates, ontology facts, class names, property names, or quotations. "
        "Every candidate must appear exactly once. A lack of sufficient evidence means "
        "`separate`."
        + repair
        + "\n\nCANDIDATES:\n"
        + json.dumps(candidates, indent=2, ensure_ascii=False)
    )


def _validate_decisions(
    candidates: Mapping[str, Any],
    raw: Mapping[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    candidate_items = [
        item
        for item in candidates.get("candidates") or []
        if isinstance(item, Mapping)
    ]
    by_id = {
        str(item.get("candidate_id") or ""): item
        for item in candidate_items
        if str(item.get("candidate_id") or "")
    }
    errors: list[str] = []
    if str(raw.get("schema_version") or "") != SCHEMA_VERSION:
        errors.append(f"schema_version must be {SCHEMA_VERSION}")
    raw_decisions = [
        item for item in raw.get("decisions") or [] if isinstance(item, Mapping)
    ]
    counts: dict[str, int] = {}
    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(raw_decisions):
        candidate_id = str(item.get("candidate_id") or "")
        counts[candidate_id] = counts.get(candidate_id, 0) + 1
        candidate = by_id.get(candidate_id)
        if candidate is None:
            errors.append(f"decisions[{index}] references an unknown candidate")
            continue
        decision = str(item.get("decision") or "")
        if decision not in {"merge", "separate"}:
            errors.append(f"{candidate_id}: decision must be merge or separate")
            continue
        cardinality = str(item.get("cardinality") or "unclear")
        lifecycle = str(item.get("lifecycle") or "unclear")
        quotes = [
            str(value)
            for value in item.get("evidence_quotes") or []
            if str(value).strip()
        ]
        evidence_texts = [
            str(value)
            for value in (candidate.get("tbox_evidence") or {}).values()
            if str(value)
        ]
        invalid_quotes = [
            quote
            for quote in quotes
            if not any(quote in evidence for evidence in evidence_texts)
        ]
        if invalid_quotes:
            errors.append(f"{candidate_id}: evidence quote is not verbatim T-Box text")
        if decision == "merge":
            if not quotes:
                errors.append(f"{candidate_id}: merge requires verbatim T-Box evidence")
            if cardinality != "exactly_one":
                errors.append(f"{candidate_id}: merge requires exactly_one cardinality")
            if candidate.get("kind") == "owned_dependent" and lifecycle != "fresh_per_owner":
                errors.append(f"{candidate_id}: owned-dependent merge requires fresh_per_owner")
            if candidate.get("kind") == "container_membership":
                structural = candidate.get("structural_evidence") or {}
                if not (
                    structural.get("ordered_member")
                    and structural.get("unique_compatible_membership_predicate")
                    and structural.get("single_valued_ordering_property")
                ):
                    errors.append(
                        f"{candidate_id}: membership merge lacks unique ordered structure"
                    )
        normalized.append(
            {
                "candidate_id": candidate_id,
                "decision": decision,
                "cardinality": cardinality,
                "lifecycle": lifecycle,
                "evidence_quotes": quotes,
                "rationale": str(item.get("rationale") or "").strip(),
            }
        )
    for candidate_id in by_id:
        if counts.get(candidate_id, 0) != 1:
            errors.append(f"{candidate_id}: candidate must be decided exactly once")
    return {
        "schema_version": SCHEMA_VERSION,
        "input_sha256": _input_sha256(candidates),
        "decisions": normalized,
    }, errors


def validate_materialization_operation_decisions(
    candidates: Mapping[str, Any],
    decisions: Mapping[str, Any],
) -> list[str]:
    """Return deterministic gate failures for one persisted LLM judgement."""
    _normalized, errors = _validate_decisions(candidates, decisions)
    return errors


def infer_materialization_operation_decisions(
    context: Any,
    *,
    planner: JsonPlanner,
    model: str,
    checkpoint_path: str | Path | None = None,
) -> dict[str, Any]:
    """Discover, adjudicate, validate, checkpoint, and compile operation units."""
    candidates = discover_materialization_operation_candidates(
        parsed=getattr(context, "parsed", {}) or {},
        contract=getattr(context, "contract", {}) or {},
        iteration_plan=getattr(context, "iteration_blueprint", {}) or {},
    )
    context.contract["materialization_operation_candidates"] = candidates
    input_sha = _input_sha256(candidates)
    judge_prompt_sha = hashlib.sha256(
        _judge_prompt(candidates).encode("utf-8")
    ).hexdigest()
    checkpoint = Path(checkpoint_path) if checkpoint_path is not None else None
    if checkpoint is not None and checkpoint.is_file():
        cached = json.loads(checkpoint.read_text(encoding="utf-8"))
        if (
            isinstance(cached, dict)
            and cached.get("schema_version") == SCHEMA_VERSION
            and cached.get("input_sha256") == input_sha
            and cached.get("judge_model") == model
            and cached.get("judge_prompt_sha256") == judge_prompt_sha
        ):
            context.contract["materialization_operation_decisions"] = cached
            context.contract["materialization_operation_units"] = (
                compile_materialization_operation_units(
                    parsed=context.parsed,
                    contract=context.contract,
                    iteration_plan=context.iteration_blueprint,
                )
            )
            return cached

    if not candidates.get("candidates"):
        decision_bundle = {
            "schema_version": SCHEMA_VERSION,
            "input_sha256": input_sha,
            "judge_model": model,
            "judge_prompt_sha256": judge_prompt_sha,
            "decisions": [],
        }
    else:
        validation_errors: list[str] = []
        decision_bundle: dict[str, Any] = {}
        previous_response: dict[str, Any] | None = None
        for _attempt in range(2):
            raw_result = planner(
                model,
                _judge_prompt(
                    candidates,
                    repair_errors=validation_errors,
                    previous_response=previous_response,
                ),
            )
            raw = (
                dict(raw_result.data)
                if hasattr(raw_result, "data")
                else dict(raw_result)
            )
            decision_bundle, validation_errors = _validate_decisions(candidates, raw)
            if not validation_errors:
                break
            previous_response = raw
        if validation_errors:
            decision_bundle = {
                "schema_version": SCHEMA_VERSION,
                "input_sha256": input_sha,
                "judge_model": model,
                "judge_prompt_sha256": judge_prompt_sha,
                "decisions": [
                    {
                        "candidate_id": str(item.get("candidate_id") or ""),
                        "decision": "separate",
                        "cardinality": "unclear",
                        "lifecycle": "unclear",
                        "evidence_quotes": [],
                        "rationale": "Fail-closed fallback after invalid semantic judgement.",
                    }
                    for item in candidates.get("candidates") or []
                ],
                "fallback": "legacy_split",
                "validation_errors": validation_errors,
            }
        else:
            decision_bundle["judge_model"] = model
            decision_bundle["judge_prompt_sha256"] = judge_prompt_sha
    context.contract["materialization_operation_decisions"] = decision_bundle
    context.contract["materialization_operation_units"] = (
        compile_materialization_operation_units(
            parsed=context.parsed,
            contract=context.contract,
            iteration_plan=context.iteration_blueprint,
        )
    )
    if checkpoint is not None:
        checkpoint.parent.mkdir(parents=True, exist_ok=True)
        checkpoint.write_text(
            json.dumps(decision_bundle, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    return decision_bundle
