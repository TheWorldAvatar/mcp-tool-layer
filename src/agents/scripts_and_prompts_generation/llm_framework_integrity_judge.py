"""Dedicated LLM gate for exhaustive, domain-neutral RDF framework integrity."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable

from rdflib import Graph

from src.agents.scripts_and_prompts_generation.level1_code_repair import (
    LLMJsonResult,
    invoke_json,
)
from src.agents.scripts_and_prompts_generation.llm_framework_integrity_microjudge import (
    run_microjudge_integrity,
)


def build_framework_integrity_prompt(
    *,
    document_text: str,
    ontology_contract: dict[str, Any],
    abox_turtle: str,
) -> str:
    """Build the dedicated exhaustive structure-audit prompt."""
    return (
        "You are the blocking framework-integrity auditor for a scoped RDF A-Box. "
        "This call evaluates graph structure only; do not perform broad scientific "
        "quality scoring.\n\n"
        "Audit procedure:\n"
        "1. Exhaustively enumerate every source-supported entity or occurrence in the "
        "current owned scope. Preserve source identifiers, labels, and order markers.\n"
        "2. Derive each entity's required structural role only from explicit source "
        "relationships, OWL minimum/exact cardinality or existential restrictions, ordering "
        "semantics, and explicit edge-integrity annotations. RDF/OWL domain and range axioms "
        "constrain typing when a property is used; they do not require every domain-class "
        "instance to have that property. Never convert domain/range compatibility into a "
        "missing-edge obligation.\n"
        "3. Inspect the candidate A-Box and mark every source item pass or fail. A typed "
        "node is not complete when its intended owner, parent, collection, member, or peer "
        "relationship is missing or attached to the wrong scoped root.\n"
        "4. Detect detached shells, missing relationship integration, wrong scoped roots, "
        "and parallel replacement identities. Do not use domain-specific hard-coded names "
        "or fixed count formulas.\n"
        "5. Apply iteration_audit_scope before enumerating source_items. A source mention is "
        "relevant only when the active T-Box and iteration-owned relationship surface require "
        "it at this layer. If the T-Box excludes an occurrence from the current ownership "
        "layer, omit it from source_items and do not report its absence as a defect. The same "
        "source identity may legitimately have distinct occurrences in different ownership "
        "layers; never merge their roles or datatype values across layers.\n"
        "6. Do not sample. source_items must account for every relevant in-scope source item. When "
        "many items share one defect, create one consolidated failure whose "
        "affected_source_items lists every affected item id.\n"
        "7. Produce repair instructions that are complete, concise, and directly actionable "
        "by a KG-building agent using atomic create/add tools. Every instruction must transform "
        "the observed candidate value into the expected source value; never reverse the "
        "expected and observed directions.\n"
        "8. Produce a URI-level atomic repair_plan. Copy every existing subject/object IRI "
        "verbatim from the candidate A-Box and every predicate/class IRI from the ontology "
        "contract. Never identify an existing entity by label alone and never invent a "
        "replacement IRI. Emit one repair-plan entry per affected source item and per RDF "
        "relationship; do not collapse multiple triples into one prose action. Use an empty "
        "IRI only when it is inherently unavailable because the entity must first be created.\n\n"
        "Return only one JSON object with exactly these top-level keys:\n"
        "{"
        '"accepted":false,'
        '"summary":"",'
        '"source_items":[{'
        '"id":"","evidence":"","expected_structure":"","status":"pass|fail",'
        '"failure_ids":[]'
        "}],"
        '"failures":[{'
        '"failure_id":"","kind":"entity_materialization|relationship_integration|'
        'scope_identity|ordering|other",'
        '"severity":"high|critical",'
        '"affected_source_items":[],'
        '"expected_structure":"","observed_problem":"",'
        '"ontology_evidence":"","abox_evidence":"","repair_instruction":""'
        "}],"
        '"repair_plan":[{'
        '"priority":1,'
        '"operation":"add_relationship|remove_relationship|create_entity|'
        'reuse_identity|set_type|other",'
        '"tool_name":"",'
        '"subject_iri":"","predicate_iri":"","object_iri":"","class_iri":"",'
        '"source_item_id":"","action":"","failure_ids":[]'
        "}],"
        '"coverage_accounting":{'
        '"complete":false,"unaccounted_source_items":[],"notes":""'
        "},"
        '"confidence":0.0'
        "}\n\n"
        "Acceptance rules:\n"
        "- accepted=true only when failures is empty, every source item has status=pass, "
        "and coverage_accounting.complete=true with no unaccounted_source_items.\n"
        "- Any detached or wrongly attached source-supported entity is blocking.\n"
        "- Every failure field must contain concrete source, ontology, and A-Box evidence. "
        "Never return representative examples in place of a complete affected-item list.\n\n"
        "- A missing relationship is a failure only when its existence is explicitly "
        "supported by the corresponding source item or by an actual minimum/exact-cardinality, "
        "existential, ordering, or integrity constraint. ontology_evidence must identify that "
        "exact obligation; a domain/range declaration alone is insufficient.\n"
        "- For add_relationship/remove_relationship, subject_iri, predicate_iri, and "
        "object_iri must all be absolute IRIs. For create_entity, class_iri must be an "
        "absolute IRI. tool_name must name the exact exposed atomic tool when the contract "
        "provides one; otherwise use an empty string.\n"
        "- Every failed source item must have at least one dedicated repair_plan entry whose "
        "source_item_id exactly matches that source item.\n\n"
        f"CURRENT SOURCE HINTS:\n{document_text}\n\n"
        "ONTOLOGY CONTRACT:\n"
        f"{json.dumps(ontology_contract, ensure_ascii=False, sort_keys=True)}\n\n"
        f"CANDIDATE A-BOX (Turtle):\n{abox_turtle}\n"
    )


def _validated_framework_report(data: dict[str, Any]) -> dict[str, Any]:
    """Validate report shape and internal accounting, not graph semantics."""
    required = {
        "accepted",
        "summary",
        "source_items",
        "failures",
        "repair_plan",
        "coverage_accounting",
        "confidence",
    }
    if set(data) != required:
        raise ValueError(
            "framework report top-level keys differ from the required schema: "
            f"expected={sorted(required)}, actual={sorted(data)}"
        )
    accepted = data["accepted"]
    if not isinstance(accepted, bool):
        raise ValueError("accepted must be boolean")
    confidence = float(data["confidence"])
    if not 0.0 <= confidence <= 1.0:
        raise ValueError("confidence must be within [0, 1]")

    source_items = data["source_items"]
    failures = data["failures"]
    repair_plan = data["repair_plan"]
    accounting = data["coverage_accounting"]
    if not isinstance(source_items, list) or not isinstance(failures, list):
        raise ValueError("source_items and failures must be lists")
    if not isinstance(repair_plan, list) or not isinstance(accounting, dict):
        raise ValueError("repair_plan must be a list and coverage_accounting an object")

    source_ids: set[str] = set()
    normalized_items: list[dict[str, Any]] = []
    for index, raw in enumerate(source_items):
        if not isinstance(raw, dict):
            raise ValueError(f"source_items[{index}] must be an object")
        item_id = str(raw.get("id") or "").strip()
        status = str(raw.get("status") or "").strip()
        if not item_id or item_id in source_ids:
            raise ValueError(f"source_items[{index}] has missing or duplicate id")
        if status not in {"pass", "fail"}:
            raise ValueError(f"source_items[{index}] status must be pass or fail")
        source_ids.add(item_id)
        normalized_items.append(
            {
                "id": item_id,
                "evidence": str(raw.get("evidence") or "").strip(),
                "expected_structure": str(
                    raw.get("expected_structure") or ""
                ).strip(),
                "status": status,
                "failure_ids": [
                    str(value).strip()
                    for value in raw.get("failure_ids") or []
                    if str(value).strip()
                ],
            }
        )

    allowed_kinds = {
        "entity_materialization",
        "relationship_integration",
        "scope_identity",
        "ordering",
        "other",
    }
    failure_ids: set[str] = set()
    normalized_failures: list[dict[str, Any]] = []
    required_failure_text = (
        "expected_structure",
        "observed_problem",
        "ontology_evidence",
        "abox_evidence",
        "repair_instruction",
    )
    for index, raw in enumerate(failures):
        if not isinstance(raw, dict):
            raise ValueError(f"failures[{index}] must be an object")
        failure_id = str(raw.get("failure_id") or "").strip()
        kind = str(raw.get("kind") or "").strip()
        severity = str(raw.get("severity") or "").strip()
        affected = [
            str(value).strip()
            for value in raw.get("affected_source_items") or []
            if str(value).strip()
        ]
        if not failure_id or failure_id in failure_ids:
            raise ValueError(f"failures[{index}] has missing or duplicate failure_id")
        if kind not in allowed_kinds or severity not in {"high", "critical"}:
            raise ValueError(f"failures[{index}] has invalid kind or severity")
        if not affected or any(value not in source_ids for value in affected):
            raise ValueError(f"failures[{index}] has invalid affected_source_items")
        text = {
            key: str(raw.get(key) or "").strip() for key in required_failure_text
        }
        if any(not value for value in text.values()):
            raise ValueError(f"failures[{index}] lacks required evidence or repair text")
        failure_ids.add(failure_id)
        normalized_failures.append(
            {
                "failure_id": failure_id,
                "kind": kind,
                "severity": severity,
                "affected_source_items": affected,
                **text,
            }
        )

    for item in normalized_items:
        refs = item["failure_ids"]
        if any(value not in failure_ids for value in refs):
            raise ValueError(f"source item {item['id']} references an unknown failure")
        if item["status"] == "fail" and not refs:
            raise ValueError(f"failed source item {item['id']} has no failure_ids")
        if item["status"] == "pass" and refs:
            raise ValueError(f"passing source item {item['id']} references failures")

    complete = accounting.get("complete")
    unaccounted = accounting.get("unaccounted_source_items")
    if not isinstance(complete, bool) or not isinstance(unaccounted, list):
        raise ValueError("coverage_accounting has invalid complete/unaccounted fields")
    normalized_accounting = {
        "complete": complete,
        "unaccounted_source_items": [
            str(value).strip() for value in unaccounted if str(value).strip()
        ],
        "notes": str(accounting.get("notes") or "").strip(),
    }
    should_accept = (
        not normalized_failures
        and all(item["status"] == "pass" for item in normalized_items)
        and complete
        and not normalized_accounting["unaccounted_source_items"]
    )
    if accepted is not should_accept:
        raise ValueError(
            f"accepted={accepted} conflicts with validated accounting={should_accept}"
        )

    def absolute_iri(value: Any) -> str:
        iri = str(value or "").strip()
        if iri and not re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", iri):
            raise ValueError(f"repair plan value is not an absolute IRI: {iri!r}")
        return iri

    allowed_operations = {
        "add_relationship",
        "remove_relationship",
        "create_entity",
        "reuse_identity",
        "set_type",
        "other",
    }
    required_plan_keys = {
        "priority",
        "operation",
        "tool_name",
        "subject_iri",
        "predicate_iri",
        "object_iri",
        "class_iri",
        "source_item_id",
        "action",
        "failure_ids",
    }
    normalized_plan: list[dict[str, Any]] = []
    plan_keys: set[tuple[str, str, str, str, str]] = set()
    for index, raw in enumerate(repair_plan):
        if not isinstance(raw, dict):
            raise ValueError(f"repair_plan[{index}] must be an object")
        if set(raw) != required_plan_keys:
            raise ValueError(
                f"repair_plan[{index}] keys differ from the required URI-level schema: "
                f"missing={sorted(required_plan_keys - set(raw))}, "
                f"extra={sorted(set(raw) - required_plan_keys)}"
            )
        refs = [
            str(value).strip()
            for value in raw.get("failure_ids") or []
            if str(value).strip()
        ]
        if not refs or any(value not in failure_ids for value in refs):
            raise ValueError(f"repair_plan[{index}] references an unknown failure")
        operation = str(raw.get("operation") or "").strip()
        if operation not in allowed_operations:
            raise ValueError(f"repair_plan[{index}] has invalid operation")
        source_item_id = str(raw.get("source_item_id") or "").strip()
        if source_item_id not in source_ids:
            raise ValueError(f"repair_plan[{index}] has invalid source_item_id")
        subject_iri = absolute_iri(raw.get("subject_iri"))
        predicate_iri = absolute_iri(raw.get("predicate_iri"))
        object_iri = absolute_iri(raw.get("object_iri"))
        class_iri = absolute_iri(raw.get("class_iri"))
        action = str(raw.get("action") or "").strip()
        if not action:
            raise ValueError(f"repair_plan[{index}] requires an action")
        if operation in {"add_relationship", "remove_relationship"} and not all(
            (subject_iri, predicate_iri, object_iri)
        ):
            raise ValueError(
                f"repair_plan[{index}] relationship operation requires exact triple IRIs"
            )
        if operation == "create_entity" and not class_iri:
            raise ValueError(
                f"repair_plan[{index}] create_entity requires an exact class_iri"
            )
        plan_key = (
            operation,
            subject_iri,
            predicate_iri,
            object_iri,
            source_item_id,
        )
        if plan_key in plan_keys:
            raise ValueError(f"repair_plan[{index}] duplicates an atomic repair")
        plan_keys.add(plan_key)
        normalized_plan.append(
            {
                "priority": int(raw.get("priority")),
                "operation": operation,
                "tool_name": str(raw.get("tool_name") or "").strip(),
                "subject_iri": subject_iri,
                "predicate_iri": predicate_iri,
                "object_iri": object_iri,
                "class_iri": class_iri,
                "source_item_id": source_item_id,
                "action": action,
                "failure_ids": refs,
            }
        )
    if normalized_failures and not normalized_plan:
        raise ValueError("failed framework report requires a repair_plan")
    failed_source_ids = {
        item["id"] for item in normalized_items if item["status"] == "fail"
    }
    planned_source_ids = {item["source_item_id"] for item in normalized_plan}
    missing_repairs = sorted(failed_source_ids - planned_source_ids)
    if missing_repairs:
        raise ValueError(
            "repair_plan lacks dedicated URI-level entries for failed source items: "
            + json.dumps(missing_repairs, ensure_ascii=False)
        )

    return {
        "accepted": accepted,
        "summary": str(data["summary"] or "").strip(),
        "source_items": normalized_items,
        "failures": normalized_failures,
        "repair_plan": normalized_plan,
        "coverage_accounting": normalized_accounting,
        "confidence": confidence,
    }


def _invoke_validated(
    *,
    invoke: Callable[..., LLMJsonResult],
    model: str,
    prompt: str,
    max_validation_attempts: int = 3,
) -> tuple[dict[str, Any], list[LLMJsonResult]]:
    attempts: list[LLMJsonResult] = []
    current_prompt = prompt
    errors: list[str] = []
    for _ in range(max_validation_attempts):
        result = invoke(model, current_prompt, max_attempts=3)
        attempts.append(result)
        try:
            return _validated_framework_report(result.data), attempts
        except (TypeError, ValueError) as exc:
            errors.append(str(exc))
            current_prompt = (
                prompt
                + "\n\nYOUR PREVIOUS JSON FAILED THE SCHEMA/ACCOUNTING VALIDATOR:\n"
                + str(exc)
                + "\nPREVIOUS JSON:\n"
                + json.dumps(result.data, ensure_ascii=False)
                + "\nReturn a corrected complete report in the exact required schema."
            )
    raise ValueError(
        "framework-integrity judge failed schema validation: "
        + json.dumps(errors, ensure_ascii=False)
    )


def judge_framework_integrity(
    *,
    document_text: str,
    ontology_contract: dict[str, Any],
    abox_path: Path,
    model: str,
    reviewer_model: str | None = None,
    verifier_model: str | None = None,
    invoke: Callable[..., LLMJsonResult] = invoke_json,
) -> dict[str, Any]:
    """Run independent per-item/per-aspect panels and aggregate mechanically."""
    graph = Graph()
    graph.parse(str(abox_path), format="turtle")
    models = [
        model,
        reviewer_model or model,
        verifier_model or reviewer_model or model,
    ]
    micro = run_microjudge_integrity(
        document_text=document_text,
        ontology_contract=ontology_contract,
        graph=graph,
        models=models,
        invoke=invoke,
    )

    confirmed = micro["confirmed_failures"]
    failure_ids_by_item: dict[str, list[str]] = {}
    failures = []
    repair_plan = []
    for index, finding in enumerate(confirmed, start=1):
        item = finding["item"]
        aspect = finding["aspect"]
        votes = finding["votes"]
        repair = finding["repair"]
        failure_id = f"micro-{index:04d}"
        failure_ids_by_item.setdefault(item.item_id, []).append(failure_id)
        evidence_vote = votes[0] if votes else {}
        kind = (
            "entity_materialization"
            if aspect.kind == "entity_presence"
            else "relationship_integration"
            if aspect.kind in {"explicit_field", "owner_integration"}
            else "other"
        )
        failures.append(
            {
                "failure_id": failure_id,
                "kind": kind,
                "severity": "critical" if aspect.kind == "entity_presence" else "high",
                "affected_source_items": [item.item_id],
                "expected_structure": (
                    f"{aspect.aspect_id}: {aspect.field_name}={aspect.field_value}"
                ).strip(": ="),
                "observed_problem": evidence_vote.get("summary")
                or f"Confirmed missing aspect {aspect.aspect_id}",
                "ontology_evidence": evidence_vote.get("ontology_evidence")
                or "Confirmed by three independent aspect judges.",
                "abox_evidence": evidence_vote.get("abox_evidence")
                or "Confirmed absent from the supplied local A-Box neighborhood.",
                "repair_instruction": repair["action"],
            }
        )
        repair_plan.append(
            {
                "priority": index,
                **repair,
                "source_item_id": item.item_id,
                "failure_ids": [failure_id],
            }
        )

    source_items = []
    for item in micro["items"]:
        failure_ids = failure_ids_by_item.get(item.item_id, [])
        source_items.append(
            {
                "id": item.item_id,
                "evidence": item.evidence,
                "expected_structure": "; ".join(
                    f"{key}: {value}" for key, value in item.fields
                )
                or f"Materialized {item.class_hint or 'source item'} occurrence",
                "status": "fail" if failure_ids else "pass",
                "failure_ids": failure_ids,
            }
        )
    final = _validated_framework_report(
        {
            "accepted": not failures,
            "summary": (
                f"{len(failures)} independently confirmed framework-integrity "
                "failure(s)."
                if failures
                else "No framework-integrity failure survived independent aspect voting."
            ),
            "source_items": source_items,
            "failures": failures,
            "repair_plan": repair_plan,
            "coverage_accounting": {
                "complete": True,
                "unaccounted_source_items": [],
                "notes": (
                    "Pipeline-derived accounting over every parsed source item and aspect."
                ),
            },
            "confidence": 1.0 if failures else 0.99,
        }
    )

    results = micro["llm_results"]
    token_usage: dict[str, int] = {}
    elapsed_seconds = 0.0
    for result in results:
        elapsed_seconds += result.elapsed_seconds
        for key, value in (result.token_usage or {}).items():
            if isinstance(value, int):
                token_usage[key] = token_usage.get(key, 0) + value

    observations = []
    for item in final["failures"]:
        failure_repairs = [
            repair
            for repair in final["repair_plan"]
            if item["failure_id"] in repair["failure_ids"]
        ]
        evidence = dict(item)
        evidence["uri_level_repair_plan"] = failure_repairs
        observations.append(
            {
            "observation_id": f"framework_integrity::{item['failure_id']}",
            "check_id": f"framework_integrity.{item['kind']}",
            "subject_key": ",".join(item["affected_source_items"]),
            "stage": "framework-integrity",
            "status": "fail",
            "observed_artifacts": ["candidate_abox"],
            "blocked_by": [],
            "evidence": evidence,
            "reported_by": models,
            }
        )
    uncertain_panels = [
        panel for panel in micro["panels"] if panel["decision"] == "uncertain"
    ]
    observations.extend(
        {
            "observation_id": (
                "framework_integrity::uncertain::"
                + re.sub(
                    r"[^A-Za-z0-9_.-]+",
                    "-",
                    f"{panel['source_item_id']}::{panel['aspect_id']}",
                )
            ),
            "check_id": "framework_integrity.uncertain",
            "subject_key": panel["source_item_id"],
            "stage": "framework-integrity",
            "status": "uncertain",
            "observed_artifacts": ["candidate_abox"],
            "blocked_by": ["independent_panel_disagreement"],
            "evidence": {
                "aspect_id": panel["aspect_id"],
                "detection_votes": panel["detection_votes"],
                "escalation_votes": panel["escalation_votes"],
            },
            "reported_by": models,
        }
        for panel in uncertain_panels
    )
    return {
        "schema_version": "llm-framework-integrity.v1",
        "policy": "independent_per_item_per_aspect_unanimous_panels",
        "blocking": True,
        "accepted": final["accepted"],
        "draft": final,
        "reviewed": final,
        "final": final,
        "microjudge_panels": micro["panels"],
        "observations": observations,
        "abox_path": str(abox_path),
        "triple_count": len(graph),
        "elapsed_seconds": round(elapsed_seconds, 3),
        "token_usage": token_usage,
    }
