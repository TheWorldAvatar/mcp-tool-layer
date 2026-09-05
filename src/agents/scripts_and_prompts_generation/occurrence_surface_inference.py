"""GPT-5 adjudication for non-deterministic occurrence-surface facets and leftover linkers."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Callable, Mapping

from src.agents.scripts_and_prompts_generation.level1_code_repair import invoke_json
from src.agents.scripts_and_prompts_generation.occurrence_surface_units import (
    DECISION_SCHEMA,
    INSTRUCTION_SCHEMA,
    LINKER_KIND,
    compile_fallback_instruction,
    compile_occurrence_surface,
    discover_occurrence_surface_candidates,
    install_membership_only_operation_units,
    is_deterministic_candidate,
)


JsonPlanner = Callable[[str, str], dict[str, Any]]


def invoke_occurrence_judge(model: str, prompt: str) -> dict[str, Any]:
    result = invoke_json(
        model,
        prompt,
        timeout_seconds=600,
        max_attempts=3,
        provider_max_retries=0,
    )
    return dict(result.data)


def _input_sha256(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _partition_candidates(
    candidates: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    deterministic: list[dict[str, Any]] = []
    judged: list[dict[str, Any]] = []
    for raw in candidates.get("candidates") or []:
        if not isinstance(raw, Mapping):
            continue
        item = dict(raw)
        if is_deterministic_candidate(item):
            deterministic.append(item)
        else:
            judged.append(item)
    return deterministic, judged


def _judged_payload(candidates: Mapping[str, Any]) -> dict[str, Any]:
    _, judged = _partition_candidates(candidates)
    return {
        "schema_version": candidates.get("schema_version"),
        "candidates": judged,
        "selection_policy": (
            "Judge only candidates that code could not decide from T-Box "
            "structure. Owner-local optional quantities, fresh dependents, "
            "reusable descriptors, one-hop reusable descriptors, unique "
            "incoming parents, and unique ordered-member membership are "
            "already bundled by code."
        ),
    }


def _deterministic_decisions(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "candidate_id": str(item.get("candidate_id") or ""),
            "decision": (
                "expose" if item.get("kind") == LINKER_KIND else "bundle"
            ),
            "evidence_quotes": [],
            "rationale": (
                "Deterministic T-Box structure: unique ordered-member "
                "membership, unique incoming parent, owner-local optional "
                "facet, or leftover root linker."
            ),
        }
        for item in items
        if str(item.get("candidate_id") or "")
    ]


def _facet_prompt(
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
        "You are deciding only those occurrence-surface candidates that code could not "
        "decide from T-Box structure. Use only the supplied T-Box comments and "
        "structural evidence. Do not invent class names, properties, or examples.\n\n"
        "If structural_evidence.optional_creator_argument is true, decide `bundle`. "
        "If the candidate is a unique incoming parent or unique ordered-member "
        "membership, decide `bundle`. Do not keep a facet separate merely because it "
        "is optional, because a container can have many members, because the owner "
        "also has a public create_* tool, or because the T-Box does not state an exact "
        "cardinality axiom.\n\n"
        "Decide `separate` only when the supplied evidence shows the facet cannot be "
        "expressed as one optional label or value argument on this owner.\n\n"
        "For `leftover_public_linker` candidates, decide `expose` only when the subject "
        "is an already-created root or container and the object is a reusable descriptor "
        "that no public occurrence creator owns. Otherwise `omit`.\n\n"
        "Evidence quote protocol:\n"
        "- `bundle` and `expose` should copy at least one exact contiguous substring from "
        "that candidate's `tbox_evidence` when any evidence string exists.\n"
        "- If `tbox_evidence` is empty, `evidence_quotes` may be empty.\n"
        "- Do not paraphrase or quote structural_evidence.\n"
        "- `separate` and `omit` may use an empty evidence list.\n\n"
        "Return one JSON object only:\n"
        "{\n"
        f'  "schema_version": "{DECISION_SCHEMA}",\n'
        '  "decisions": [\n'
        "    {\n"
        '      "candidate_id": "<exact supplied id>",\n'
        '      "decision": "bundle|separate|expose|omit",\n'
        '      "evidence_quotes": ["<exact substring copied from supplied T-Box evidence>"],\n'
        '      "rationale": "<brief occurrence-boundary reasoning>"\n'
        "    }\n"
        "  ]\n"
        "}\n\n"
        "Do not invent candidates, ontology facts, or quotations. Every candidate must "
        "appear exactly once."
        + repair
        + "\n\nCANDIDATES:\n"
        + json.dumps(candidates, indent=2, ensure_ascii=False)
    )


def _allowed_decisions(candidate: Mapping[str, Any]) -> set[str]:
    if str(candidate.get("kind") or "") == LINKER_KIND:
        return {"expose", "omit"}
    return {"bundle", "separate"}


def _evidence_texts(candidate: Mapping[str, Any]) -> list[str]:
    return [
        str(value)
        for value in (candidate.get("tbox_evidence") or {}).values()
        if str(value).strip()
    ]


def _salvage_quotes(quotes: list[str], evidence_texts: list[str]) -> list[str]:
    """Keep verbatim quotes; otherwise copy text already present on the candidate."""
    kept = [
        quote
        for quote in quotes
        if any(quote in evidence for evidence in evidence_texts)
    ]
    if kept or not evidence_texts:
        return kept
    return [evidence_texts[0]]


def _validate_decisions(
    candidates: Mapping[str, Any],
    raw: Mapping[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    items = [
        item
        for item in candidates.get("candidates") or []
        if isinstance(item, Mapping)
    ]
    by_id = {
        str(item.get("candidate_id") or ""): item
        for item in items
        if str(item.get("candidate_id") or "")
    }
    errors: list[str] = []
    raw_decisions = [
        item for item in raw.get("decisions") or [] if isinstance(item, Mapping)
    ]
    seen_valid: set[str] = set()
    normalized: list[dict[str, Any]] = []
    quote_repairs: list[str] = []
    for index, item in enumerate(raw_decisions):
        candidate_id = str(item.get("candidate_id") or "")
        candidate = by_id.get(candidate_id)
        if candidate is None:
            errors.append(f"decisions[{index}] references an unknown candidate")
            continue
        if candidate_id in seen_valid:
            continue
        decision = str(item.get("decision") or "")
        allowed = _allowed_decisions(candidate)
        if decision not in allowed:
            errors.append(f"{candidate_id}: decision must be one of {sorted(allowed)}")
            continue
        if is_deterministic_candidate(candidate) and decision != "bundle":
            errors.append(f"{candidate_id}: deterministic candidate must be bundle")
            continue
        supplied_quotes = [
            str(value)
            for value in item.get("evidence_quotes") or []
            if str(value).strip()
        ]
        quotes = _salvage_quotes(supplied_quotes, _evidence_texts(candidate))
        if quotes != supplied_quotes:
            quote_repairs.append(candidate_id)
        normalized.append(
            {
                "candidate_id": candidate_id,
                "decision": decision,
                "evidence_quotes": quotes,
                "rationale": str(item.get("rationale") or "").strip(),
            }
        )
        seen_valid.add(candidate_id)
    for candidate_id in by_id:
        if candidate_id not in seen_valid:
            errors.append(f"{candidate_id}: candidate must be decided exactly once")
    payload = {
        "schema_version": DECISION_SCHEMA,
        "input_sha256": _input_sha256(candidates),
        "decisions": normalized,
    }
    if quote_repairs:
        payload["quote_repairs"] = quote_repairs
    return payload, errors


def _prior_is_usable(bundle: Mapping[str, Any]) -> bool:
    return str(bundle.get("fallback") or "") != "separate_optional_facets"


def _prior_bundles(
    checkpoint: Path | None,
    prior_decisions: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    bundles: list[dict[str, Any]] = []
    if isinstance(prior_decisions, Mapping) and _prior_is_usable(prior_decisions):
        bundles.append(dict(prior_decisions))
    if checkpoint is not None and checkpoint.is_file():
        cached = json.loads(checkpoint.read_text(encoding="utf-8"))
        if isinstance(cached, Mapping) and _prior_is_usable(cached):
            bundles.append(dict(cached))
    return bundles


def _reuse_judged_decisions(
    judged: list[dict[str, Any]],
    priors: list[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    prior_map: dict[str, dict[str, Any]] = {}
    for bundle in priors:
        for raw in bundle.get("decisions") or []:
            if not isinstance(raw, Mapping):
                continue
            candidate_id = str(raw.get("candidate_id") or "")
            if candidate_id:
                prior_map.setdefault(candidate_id, dict(raw))
    reused: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    for item in judged:
        candidate_id = str(item.get("candidate_id") or "")
        previous = prior_map.get(candidate_id)
        decision = str((previous or {}).get("decision") or "")
        if previous is not None and decision in _allowed_decisions(item):
            reused.append(
                {
                    "candidate_id": candidate_id,
                    "decision": decision,
                    "evidence_quotes": [
                        str(value)
                        for value in previous.get("evidence_quotes") or []
                        if str(value).strip()
                    ],
                    "rationale": str(previous.get("rationale") or "").strip()
                    or "Reused prior occurrence-surface decision.",
                }
            )
        else:
            missing.append(item)
    return reused, missing


def _facet_signature(item: Mapping[str, Any]) -> tuple[str, str, str]:
    return (
        str(item.get("kind") or ""),
        str(item.get("predicate_iri") or ""),
        str(item.get("child_predicate_iri") or ""),
    )


def _inherit_ordered_sibling_decisions(
    *,
    all_candidates: list[dict[str, Any]],
    reused: list[dict[str, Any]],
    missing: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Copy judged facet decisions onto newly included ordered siblings."""
    ordered_owners = {
        str(item.get("owner_class_iri") or "")
        for item in all_candidates
        if item.get("kind") == "container_membership"
        and str(item.get("owner_class_iri") or "")
    }
    judged_by_id = {
        str(item.get("candidate_id") or ""): item
        for item in all_candidates
        if str(item.get("candidate_id") or "")
    }
    signature_decision: dict[tuple[str, str, str], Mapping[str, Any]] = {}
    for decision in reused:
        candidate = judged_by_id.get(str(decision.get("candidate_id") or ""))
        if candidate is None:
            continue
        owner = str(candidate.get("owner_class_iri") or "")
        if owner not in ordered_owners:
            continue
        signature_decision.setdefault(_facet_signature(candidate), decision)
    quantity_sibling = next(
        (
            decision
            for decision in reused
            if str(decision.get("decision") or "") == "bundle"
            and str(
                (judged_by_id.get(str(decision.get("candidate_id") or "")) or {}).get(
                    "kind"
                )
                or ""
            )
            == "owner_quantity"
            and str(
                (judged_by_id.get(str(decision.get("candidate_id") or "")) or {}).get(
                    "owner_class_iri"
                )
                or ""
            )
            in ordered_owners
        ),
        None,
    )
    inherited: list[dict[str, Any]] = []
    still_missing: list[dict[str, Any]] = []
    for item in missing:
        owner = str(item.get("owner_class_iri") or "")
        sibling = signature_decision.get(_facet_signature(item))
        if owner in ordered_owners and sibling is None and item.get("kind") == "owner_quantity":
            sibling = quantity_sibling
        if owner in ordered_owners and sibling is not None:
            inherited.append(
                {
                    "candidate_id": str(item.get("candidate_id") or ""),
                    "decision": str(sibling.get("decision") or ""),
                    "evidence_quotes": [
                        str(value)
                        for value in sibling.get("evidence_quotes") or []
                        if str(value).strip()
                    ],
                    "rationale": (
                        "Inherited from an already-judged ordered sibling with "
                        "the same structural facet."
                    ),
                }
            )
        else:
            still_missing.append(item)
    return reused + inherited, still_missing


def _fallback_decisions(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "candidate_id": str(item.get("candidate_id") or ""),
            "decision": "omit" if item.get("kind") == LINKER_KIND else "separate",
            "evidence_quotes": [],
            "rationale": "Fail-closed fallback after invalid semantic judgement.",
        }
        for item in items
        if str(item.get("candidate_id") or "")
    ]


def _complete_accepted_decisions(
    candidates: Mapping[str, Any],
    accepted: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    have = {
        str(item.get("candidate_id") or "")
        for item in accepted
        if str(item.get("candidate_id") or "")
    }
    missing = [
        item
        for item in candidates.get("candidates") or []
        if isinstance(item, Mapping)
        and str(item.get("candidate_id") or "")
        and str(item.get("candidate_id") or "") not in have
    ]
    if not missing:
        return list(accepted), []
    fallback = _fallback_decisions(missing)
    errors = [
        f"{item['candidate_id']}: kept other decisions; this candidate used fallback"
        for item in fallback
    ]
    return list(accepted) + fallback, errors


def _judge_missing(
    planner: JsonPlanner,
    model: str,
    missing: list[dict[str, Any]],
    judged_payload: Mapping[str, Any],
    *,
    batch_size: int | None = None,
) -> tuple[list[dict[str, Any]], list[str], str]:
    if batch_size and batch_size > 0 and len(missing) > batch_size:
        judged: list[dict[str, Any]] = []
        errors: list[str] = []
        prompt_sha = ""
        for start in range(0, len(missing), batch_size):
            chunk = missing[start : start + batch_size]
            print(
                f"OCCURRENCE_JUDGE_BATCH {start + 1}-{start + len(chunk)}/{len(missing)}",
                flush=True,
            )
            part, part_errors, prompt_sha = _judge_missing(
                planner,
                model,
                chunk,
                judged_payload,
            )
            judged.extend(part)
            errors.extend(part_errors)
        return judged, errors, prompt_sha
    payload = {
        "schema_version": judged_payload.get("schema_version"),
        "candidates": missing,
        "selection_policy": judged_payload.get("selection_policy"),
    }
    prompt = _facet_prompt(payload)
    prompt_sha = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    validation_errors: list[str] = []
    previous_response: dict[str, Any] | None = None
    judged: list[dict[str, Any]] = []
    best: list[dict[str, Any]] = []
    for _attempt in range(2):
        raw_result = planner(
            model,
            _facet_prompt(
                payload,
                repair_errors=validation_errors,
                previous_response=previous_response,
            ),
        )
        raw = dict(raw_result.data) if hasattr(raw_result, "data") else dict(raw_result)
        bundle, validation_errors = _validate_decisions(payload, raw)
        judged = list(bundle.get("decisions") or [])
        if len(judged) > len(best):
            best = judged
        if not validation_errors:
            break
        previous_response = raw
    if validation_errors:
        judged, validation_errors = _complete_accepted_decisions(payload, best)
    return judged, validation_errors, prompt_sha


def _attach_compiled(
    *,
    parsed: Mapping[str, Any],
    contract: dict[str, Any],
    iteration_plan: Mapping[str, Any],
    decision_bundle: dict[str, Any],
) -> dict[str, Any]:
    contract["occurrence_surface_decisions"] = decision_bundle
    compiled = compile_occurrence_surface(
        parsed=parsed,
        contract=contract,
        iteration_plan=iteration_plan,
    )
    instruction_bundle = {
        "schema_version": INSTRUCTION_SCHEMA,
        "text": compiled.get("instruction") or compile_fallback_instruction(compiled),
        "source": "compiled_operational_instruction",
    }
    compiled["instruction"] = str(instruction_bundle["text"] or "")
    contract["occurrence_surface_instruction"] = instruction_bundle
    contract["occurrence_surface_units"] = compiled
    primitive_units = contract.get("materialization_operation_units") or {}
    primitive_units["merged_predicate_locals"] = sorted(
        {
            *(primitive_units.get("merged_predicate_locals") or []),
            *(compiled.get("bundled_predicate_locals") or []),
        }
    )
    contract["materialization_operation_units"] = primitive_units
    decision_bundle["instruction"] = instruction_bundle
    return compiled


def infer_occurrence_surface(
    context: Any,
    *,
    planner: JsonPlanner,
    model: str,
    checkpoint_path: str | Path | None = None,
    prior_decisions: Mapping[str, Any] | None = None,
    judge_batch_size: int | None = None,
) -> dict[str, Any]:
    """Discover, adjudicate remaining facets, compile, and persist the occurrence surface."""
    parsed = getattr(context, "parsed", {}) or {}
    contract = getattr(context, "contract", {}) or {}
    iteration_plan = getattr(context, "iteration_blueprint", {}) or {}
    install_membership_only_operation_units(
        parsed=parsed,
        contract=contract,
        iteration_plan=iteration_plan,
    )
    candidates = discover_occurrence_surface_candidates(
        parsed=parsed,
        contract=contract,
        iteration_plan=iteration_plan,
    )
    contract["occurrence_surface_candidates"] = candidates
    deterministic, judged = _partition_candidates(candidates)
    judged_payload = _judged_payload(candidates)
    input_sha = _input_sha256(candidates)
    judged_sha = _input_sha256(judged_payload)
    judge_prompt = _facet_prompt(judged_payload)
    judge_prompt_sha = hashlib.sha256(judge_prompt.encode("utf-8")).hexdigest()
    checkpoint = Path(checkpoint_path) if checkpoint_path is not None else None
    priors = _prior_bundles(checkpoint, prior_decisions)
    for cached in priors:
        if (
            cached.get("schema_version") == DECISION_SCHEMA
            and cached.get("judged_input_sha256") == judged_sha
            and cached.get("judge_model") == model
            and cached.get("judge_prompt_sha256") == judge_prompt_sha
            and cached.get("decisions")
            and cached.get("fallback") != "separate_optional_facets"
        ):
            compiled = _attach_compiled(
                parsed=parsed,
                contract=contract,
                iteration_plan=iteration_plan,
                decision_bundle=cached,
            )
            if not compiled.get("instruction"):
                compiled["instruction"] = compile_fallback_instruction(compiled)
            return cached

    reused, missing = _reuse_judged_decisions(judged, priors)
    reused, missing = _inherit_ordered_sibling_decisions(
        all_candidates=list(candidates.get("candidates") or []),
        reused=reused,
        missing=missing,
    )
    validation_errors: list[str] = []
    used_llm = False
    if not missing:
        judged_decisions = reused
    else:
        used_llm = True
        judged_decisions, validation_errors, judge_prompt_sha = _judge_missing(
            planner,
            model,
            missing,
            judged_payload,
            batch_size=judge_batch_size,
        )
        judged_decisions = reused + judged_decisions

    decision_bundle: dict[str, Any] = {
        "schema_version": DECISION_SCHEMA,
        "input_sha256": input_sha,
        "judged_input_sha256": judged_sha,
        "judge_model": model,
        "judge_prompt_sha256": judge_prompt_sha,
        "decisions": _deterministic_decisions(deterministic) + judged_decisions,
        "reused_prior_decision_count": len(reused),
        "llm_judged_count": 0 if not used_llm else len(missing),
    }
    if validation_errors:
        decision_bundle["fallback"] = "invalid_candidates_only"
        decision_bundle["validation_errors"] = validation_errors

    _attach_compiled(
        parsed=parsed,
        contract=contract,
        iteration_plan=iteration_plan,
        decision_bundle=decision_bundle,
    )
    if checkpoint is not None:
        checkpoint.parent.mkdir(parents=True, exist_ok=True)
        checkpoint.write_text(
            json.dumps(decision_bundle, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    return decision_bundle
