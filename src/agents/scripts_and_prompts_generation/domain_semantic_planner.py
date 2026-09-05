from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Callable

from src.agents.scripts_and_prompts_generation.domain_generation_config import (
    DomainGenerationConfig,
    PLANNING_MODEL,
)
from src.agents.scripts_and_prompts_generation.deterministic_iteration_assigner import (
    assign_iteration_ownership,
)
from src.agents.scripts_and_prompts_generation.level1_code_repair import (
    LLMJsonResult,
    invoke_json,
)


JsonPlanner = Callable[[str, str], dict[str, Any] | LLMJsonResult]
MAX_SEMANTIC_ATTEMPTS = 3


def _invoke(model: str, prompt: str) -> LLMJsonResult:
    return invoke_json(
        model,
        prompt,
        timeout_seconds=600,
        max_attempts=3,
        provider_max_retries=0,
    )


def _json_digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _planner_result(value: dict[str, Any] | LLMJsonResult) -> LLMJsonResult:
    if isinstance(value, LLMJsonResult):
        return value
    if not isinstance(value, dict):
        raise TypeError("semantic planner must return a JSON object")
    return LLMJsonResult(
        data=value,
        elapsed_seconds=0.0,
        token_usage={},
        raw_response=json.dumps(value, ensure_ascii=False),
    )


def _write_attempt(
    planning_dir: Path | None,
    *,
    phase: str,
    attempt: int,
    prompt: str,
    result: LLMJsonResult,
    validation: dict[str, Any],
) -> None:
    if planning_dir is None:
        return
    planning_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": "semantic-planning-attempt.v1",
        "phase": phase,
        "attempt": attempt,
        "model": PLANNING_MODEL,
        "prompt_sha256": _json_digest(prompt),
        "raw_response": result.raw_response,
        "parsed_response": result.data,
        "elapsed_seconds": result.elapsed_seconds,
        "token_usage": result.token_usage,
        "validation": validation,
    }
    (planning_dir / f"{phase}_attempt_{attempt}.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _repair_prompt(
    base_prompt: str,
    *,
    rejected: dict[str, Any],
    validation: dict[str, Any],
) -> str:
    return (
        base_prompt
        + "\n\nThe previous candidate failed deterministic validation. Repair it using only "
        "the supplied T-Box and fixed slots. Return a complete replacement JSON object. "
        "Do not explain the repair.\n\n"
        + json.dumps(
            {
                "validation_errors": validation["errors"],
                "rejected_candidate": rejected,
            },
            ensure_ascii=False,
        )
    )


def _ontology_projection(parsed: dict[str, Any]) -> dict[str, Any]:
    return {
        "classes": {
            local: {
                "iri": (spec or {}).get("iri"),
                "parents": (spec or {}).get("parent_classes") or [],
                "comment": (spec or {}).get("comment") or "",
            }
            for local, spec in (parsed.get("classes") or {}).items()
        },
        "properties": {
            local: {
                "iri": (spec or {}).get("iri"),
                "kind": (spec or {}).get("kind"),
                "domains": (spec or {}).get("domains")
                or [(spec or {}).get("domain")],
                "range": (spec or {}).get("range"),
                "comment": (spec or {}).get("comment") or "",
            }
            for local, spec in (parsed.get("properties") or {}).items()
        },
    }


def _local_name(value: Any) -> str:
    text = str(value or "").strip()
    if "#" in text:
        return text.rsplit("#", 1)[-1]
    return text.rstrip("/").rsplit("/", 1)[-1]


def _validate_top_candidate(
    candidate: dict[str, Any],
    *,
    classes: dict[str, Any],
    properties: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    errors: list[dict[str, Any]] = []
    top_local = str(candidate.get("class_local") or "").strip()
    if top_local not in classes:
        errors.append(
            {
                "code": "invalid_top_entity",
                "detail": f"unknown class_local {top_local!r}",
            }
        )
    evidence = [
        str(value).strip()
        for value in candidate.get("evidence") or []
        if str(value).strip()
    ]
    invalid_evidence = sorted(set(evidence) - (set(classes) | set(properties)))
    if not evidence or invalid_evidence:
        errors.append(
            {
                "code": "invalid_top_entity",
                "detail": f"evidence is empty or unknown: {invalid_evidence}",
            }
        )
    rationale = str(candidate.get("rationale") or "").strip()
    if not rationale:
        errors.append(
            {"code": "invalid_top_entity", "detail": "rationale is empty"}
        )
    validation = {"ok": not errors, "errors": errors}
    if errors:
        return validation, None
    return validation, {
        "status": "known",
        "class_local": top_local,
        "class_iri": str(classes[top_local].get("iri") or ""),
        "source": "gpt-5_tbox_semantic_selection",
        "model": PLANNING_MODEL,
        "rationale": rationale,
        "evidence": evidence,
        "iter1_allows_multiple": True,
        "main_pass_reuses_scoped_root": False,
    }


def _validate_assignment_candidate(
    candidate: dict[str, Any],
    *,
    profile: dict[str, Any],
    classes: dict[str, Any],
    properties: dict[str, Any],
    contract: dict[str, Any],
    top_local: str,
    allow_top_assignment: bool = False,
) -> dict[str, Any]:
    errors: list[dict[str, Any]] = []
    allowed_classes = set(classes) | {
        str((item or {}).get("class_local") or "").strip()
        for item in contract.get("external_class_creators") or []
        if str((item or {}).get("class_local") or "").strip()
    }
    relationship_contracts = contract.get("relationship_tool_contracts") or {}
    relationship_specs = (
        relationship_contracts.values()
        if isinstance(relationship_contracts, dict)
        else relationship_contracts
    )
    allowed_classes.update(
        str(target).strip()
        for spec in relationship_specs
        if isinstance(spec, dict)
        and "create_om2_quantity" in (spec.get("creator_tools") or [])
        for target in spec.get("external_targets") or []
        if str(target).strip()
    )
    slots = profile["slots"]
    main_slot_ids = [str(slot["id"]) for slot in slots]
    assignments = candidate.get("assignments")
    if not isinstance(assignments, list):
        assignments = []
        errors.append(
            {"code": "invalid_assignments", "detail": "assignments must be an array"}
        )
    seen_slots = [
        str(item.get("slot") or "").strip()
        for item in assignments
        if isinstance(item, dict)
    ]
    unknown_slots = sorted(set(seen_slots) - set(main_slot_ids))
    missing_slots = sorted(set(main_slot_ids) - set(seen_slots))
    duplicate_slots = sorted(
        {slot for slot in seen_slots if seen_slots.count(slot) > 1}
    )
    if unknown_slots:
        errors.append(
            {"code": "unknown_profile_slot", "detail": unknown_slots}
        )
    if missing_slots or duplicate_slots or len(assignments) != len(main_slot_ids):
        errors.append(
            {
                "code": "wrong_assignment_slot_set",
                "detail": {
                    "expected": main_slot_ids,
                    "missing": missing_slots,
                    "duplicates": duplicate_slots,
                },
            }
        )

    assigned_classes: list[str] = []
    assigned_properties: list[str] = []
    for item in assignments:
        if not isinstance(item, dict):
            errors.append(
                {"code": "invalid_assignments", "detail": "assignment must be an object"}
            )
            continue
        slot_classes = [
            str(value).strip()
            for value in item.get("classes") or []
            if str(value).strip()
        ]
        slot_properties = [
            str(value).strip()
            for value in item.get("object_properties") or []
            if str(value).strip()
        ]
        assigned_classes.extend(slot_classes)
        assigned_properties.extend(slot_properties)
        if not slot_classes and not slot_properties:
            errors.append(
                {
                    "code": "empty_assignment_scope",
                    "detail": str(item.get("slot") or ""),
                }
            )
        if not str(item.get("rationale") or "").strip():
            errors.append(
                {
                    "code": "missing_assignment_rationale",
                    "detail": str(item.get("slot") or ""),
                }
            )
    unknown_classes = sorted(set(assigned_classes) - allowed_classes)
    unknown_properties = sorted(set(assigned_properties) - set(properties))
    if unknown_classes:
        errors.append({"code": "unknown_class", "detail": unknown_classes})
    if unknown_properties:
        errors.append({"code": "unknown_property", "detail": unknown_properties})
    duplicate_classes = sorted(
        {value for value in assigned_classes if assigned_classes.count(value) > 1}
    )
    duplicate_properties = sorted(
        {
            value
            for value in assigned_properties
            if assigned_properties.count(value) > 1
        }
    )
    if duplicate_classes:
        errors.append(
            {"code": "duplicate_class_assignment", "detail": duplicate_classes}
        )
    if duplicate_properties:
        errors.append(
            {"code": "duplicate_property_assignment", "detail": duplicate_properties}
        )
    ownership = candidate.get("ownership_provenance") or {}
    expected_classes = set((ownership.get("classes") or {}).keys())
    expected_properties = set(
        (ownership.get("object_properties") or {}).keys()
    )
    if expected_classes and set(assigned_classes) != expected_classes:
        errors.append(
            {
                "code": "incomplete_class_ownership",
                "detail": {
                    "missing": sorted(expected_classes - set(assigned_classes)),
                    "unexpected": sorted(set(assigned_classes) - expected_classes),
                },
            }
        )
    if expected_properties and set(assigned_properties) != expected_properties:
        errors.append(
            {
                "code": "incomplete_property_ownership",
                "detail": {
                    "missing": sorted(
                        expected_properties - set(assigned_properties)
                    ),
                    "unexpected": sorted(
                        set(assigned_properties) - expected_properties
                    ),
                },
            }
        )
    if top_local in assigned_classes and not allow_top_assignment:
        errors.append(
            {
                "code": "top_entity_reassigned",
                "detail": "Iteration 1 exclusively owns the top entity class",
            }
        )

    assignment_by_slot = {
        str(item.get("slot") or "").strip(): item
        for item in assignments
        if isinstance(item, dict)
    }
    foundation_slot = next(
        (
            str(slot.get("id") or "").strip()
            for slot in slots
            if str(slot.get("slot_kind") or "") == "foundation"
        ),
        "",
    )
    ordered_slot = next(
        (
            str(slot.get("id") or "").strip()
            for slot in slots
            if str(slot.get("slot_kind") or "") == "ordered"
        ),
        "",
    )
    ordered_classes = {
        str(value).strip()
        for value in (
            (contract.get("ordered_member_profile") or {}).get(
                "ordered_member_classes"
            )
            or []
        )
        if str(value).strip()
    }
    if ordered_slot and ordered_classes:
        actual_ordered_classes = {
            str(value).strip()
            for value in (
                assignment_by_slot.get(ordered_slot, {}).get("classes") or []
            )
            if str(value).strip()
        }
        misplaced_ordered_classes = sorted(
            (set(assigned_classes) & ordered_classes) - actual_ordered_classes
        )
        if misplaced_ordered_classes:
            errors.append(
                {
                    "code": "ordered_class_outside_ordered_slot",
                    "detail": misplaced_ordered_classes,
                }
            )
        ordered_domain_properties = {
            local
            for local, spec in properties.items()
            if set(spec.get("domains") or []) & ordered_classes
        }
        actual_ordered_properties = {
            str(value).strip()
            for value in (
                assignment_by_slot.get(ordered_slot, {}).get(
                    "object_properties"
                )
                or []
            )
            if str(value).strip()
        }
        misplaced_ordered_properties = sorted(
            (set(assigned_properties) & ordered_domain_properties)
            - actual_ordered_properties
        )
        if misplaced_ordered_properties:
            errors.append(
                {
                    "code": "ordered_property_outside_ordered_slot",
                    "detail": misplaced_ordered_properties,
                }
            )
        if foundation_slot:
            top_link_ranges = {
                str(spec.get("range") or "").strip()
                for spec in properties.values()
                if top_local in set(spec.get("domains") or [])
                and str(spec.get("range") or "").strip()
            }
            ordered_reference_ranges = {
                str(spec.get("range") or "").strip()
                for spec in properties.values()
                if set(spec.get("domains") or []) & ordered_classes
                and str(spec.get("range") or "").strip()
            }
            non_reusable_classes = {
                str(item.get("class_local") or "").strip()
                for item in (
                    (contract.get("reuse_policy") or {}).get("classes") or []
                )
                if isinstance(item, dict)
                and item.get("reusable") is False
                and str(item.get("class_local") or "").strip()
            }
            required_foundation_refs = (
                top_link_ranges
                & ordered_reference_ranges
                & set(assigned_classes)
                & non_reusable_classes
            )
            actual_foundation_classes = {
                str(value).strip()
                for value in (
                    assignment_by_slot.get(foundation_slot, {}).get("classes")
                    or []
                )
                if str(value).strip()
            }
            missing_foundation_refs = sorted(
                required_foundation_refs - actual_foundation_classes
            )
            if missing_foundation_refs:
                errors.append(
                    {
                        "code": "ordered_reference_not_materialized_in_foundation",
                        "detail": missing_foundation_refs,
                    }
                )

    if "enrichment_focus" in candidate:
        errors.append(
            {
                "code": "unexpected_semantic_enrichment_focus",
                "detail": (
                    "domain semantic planning owns main slots only; enrichment must "
                    "not be planned or recorded"
                ),
            }
        )

    required_properties = {
        _local_name((item or {}).get("predicate_iri"))
        for item in contract.get("required_links") or []
        if _local_name((item or {}).get("predicate_iri"))
        and (
            not (item or {}).get("subject_class_iri")
            or str((item or {}).get("subject_class_iri"))
            == str(classes[top_local].get("iri") or "")
        )
    }
    unassigned_required = sorted(required_properties - set(assigned_properties))
    if unassigned_required:
        errors.append(
            {"code": "required_link_unassigned", "detail": unassigned_required}
        )
    return {"ok": not errors, "errors": errors}


def _materialize_iterations(
    candidate: dict[str, Any], profile: dict[str, Any]
) -> list[dict[str, Any]]:
    by_slot = {
        str(item["slot"]): item for item in candidate.get("assignments") or []
    }
    iterations: list[dict[str, Any]] = []
    for slot in profile["slots"]:
        slot_id = str(slot["id"])
        assignment = by_slot[slot_id]
        iterations.append(
            {
                "profile_slot": slot_id,
                "slot_kind": str(slot.get("slot_kind") or ""),
                "iteration_number": int(slot["iteration_number"]),
                "name": slot_id,
                "description": str(assignment["rationale"]).strip(),
                "responsibilities": {
                    "classes": list(assignment.get("classes") or []),
                    "object_properties": list(
                        assignment.get("object_properties") or []
                    ),
                },
                "requires_pre_extraction": bool(
                    slot.get("requires_pre_extraction", False)
                ),
            }
        )
    return iterations


def plan_top_entity_semantics(
    *,
    parsed: dict[str, Any],
    planner: JsonPlanner | None = None,
    planning_dir: str | Path | None = None,
    phase: str = "top_entity",
    planning_purpose: str = "active ontology root",
) -> dict[str, Any]:
    """Select and validate one ontology root from an active T-Box projection."""
    run = planner or _invoke
    audit_dir = Path(planning_dir) if planning_dir is not None else None
    projection = _ontology_projection(parsed)
    classes = projection["classes"]
    properties = projection["properties"]
    top_prompt = (
        "Select the single top entity class for this ontology-driven extraction and KG "
        f"pipeline. Planning purpose: {planning_purpose}. "
        "Use only the supplied active T-Box projection. Prefer the class whose "
        "outgoing object properties organize the main downstream entity graph. Do not rely "
        "on iteration names or runtime configuration. Return JSON only with class_local, "
        "rationale, and evidence, where evidence is a non-empty list of exact supplied class "
        "or property local names.\n\n"
        + json.dumps(projection, ensure_ascii=False)
    )
    top_entity: dict[str, Any] | None = None
    top_candidate: dict[str, Any] = {}
    prompt = top_prompt
    top_validation: dict[str, Any] = {"ok": False, "errors": []}
    for attempt in range(1, MAX_SEMANTIC_ATTEMPTS + 1):
        result = _planner_result(run(PLANNING_MODEL, prompt))
        top_candidate = result.data
        top_validation, top_entity = _validate_top_candidate(
            top_candidate, classes=classes, properties=properties
        )
        _write_attempt(
            audit_dir,
            phase=phase,
            attempt=attempt,
            prompt=prompt,
            result=result,
            validation=top_validation,
        )
        if top_validation["ok"]:
            break
        prompt = _repair_prompt(
            top_prompt, rejected=top_candidate, validation=top_validation
        )
    if top_entity is None:
        raise ValueError(
            "semantic top-entity planning failed after "
            f"{MAX_SEMANTIC_ATTEMPTS} attempts: {top_validation['errors']}"
        )
    return top_entity


def plan_domain_semantics(
    *,
    config: DomainGenerationConfig,
    parsed: dict[str, Any],
    contract: dict[str, Any],
    planner: JsonPlanner | None = None,
    planning_dir: str | Path | None = None,
    top_entity_owner: str = "iteration1",
    selected_root: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Use GPT-5 for the two authorized domain-semantic planning decisions."""
    if top_entity_owner not in {"iteration1", "downstream"}:
        raise ValueError("top_entity_owner must be 'iteration1' or 'downstream'")
    run = planner or _invoke
    audit_dir = Path(planning_dir) if planning_dir is not None else None
    projection = _ontology_projection(parsed)
    classes = projection["classes"]
    properties = projection["properties"]
    selected_planner = (
        (lambda _model, _prompt: dict(selected_root))
        if selected_root is not None
        else run
    )
    top_entity = plan_top_entity_semantics(
        parsed=parsed,
        planner=selected_planner,
        planning_dir=audit_dir,
        phase=(
            "extension_focus"
            if top_entity_owner == "downstream"
            else "top_entity"
        ),
        planning_purpose=(
            "extension semantic focus; this is not a pipeline top entity"
            if top_entity_owner == "downstream"
            else "active ontology root"
        ),
    )

    profile = config.profile
    iteration_candidate = assign_iteration_ownership(
        profile=profile,
        parsed=parsed,
        contract=contract,
        top_local=str(top_entity["class_local"]),
        allow_top_assignment=top_entity_owner == "downstream",
    )
    iteration_validation = _validate_assignment_candidate(
        iteration_candidate,
        profile=profile,
        classes=classes,
        properties=properties,
        contract=contract,
        top_local=str(top_entity["class_local"]),
        allow_top_assignment=top_entity_owner == "downstream",
    )
    if not iteration_validation["ok"]:
        raise ValueError(
            "deterministic semantic assignment failed validation: "
            f"{iteration_validation['errors']}"
        )

    iterations = _materialize_iterations(iteration_candidate, profile)
    accepted = {
        "schema_version": "domain-semantic-decisions.v2",
        "model": PLANNING_MODEL,
        "top_entity": top_entity,
        "assignments": iteration_candidate,
        "iteration_decomposition": {"iterations": iterations},
    }
    if audit_dir is not None:
        audit_dir.mkdir(parents=True, exist_ok=True)
        (audit_dir / "deterministic_iteration_ownership.json").write_text(
            json.dumps(
                {
                    "schema_version": "deterministic-iteration-ownership.v1",
                    "validation": iteration_validation,
                    "candidate": iteration_candidate,
                },
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        (audit_dir / "accepted_semantic_plan.json").write_text(
            json.dumps(accepted, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    return accepted
