"""Independent LLM panels for cross-iteration RDF continuity and refinement."""

from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable

from rdflib import Graph

from src.agents.scripts_and_prompts_generation.level1_code_repair import (
    LLMJsonResult,
    invoke_json,
)
from src.agents.scripts_and_prompts_generation.llm_framework_integrity_microjudge import (
    Aspect,
    SourceItem,
    aspects_for_item,
    parse_semantic_hint_items,
    project_contract,
    project_item_neighborhood,
)


_DECISIONS = {"preserved", "valid_refinement", "regression", "uncertain"}


def _safe_id(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", str(value)).strip("-") or "aspect"


def _neighborhood_graph(graph: Graph, item: SourceItem, aspect: Aspect) -> Graph:
    projected = project_item_neighborhood(graph, item, aspect)
    result = Graph()
    if projected.strip():
        result.parse(data=projected, format="turtle")
    return result


def _continuity_prompt(
    *,
    iteration: int,
    item: SourceItem,
    aspect: Aspect,
    contract_slice: dict[str, Any],
    old_neighborhood: str,
    final_neighborhood: str,
    complete_final_abox: str,
    removed_turtle: str,
    confirmation: bool,
) -> str:
    return (
        "You are one independent cross-iteration RDF continuity microjudge. You have "
        "no access to any other judge's answer. Judge exactly one previously accepted "
        "source aspect against the final graph.\n\n"
        "Classify only this transition:\n"
        "- preserved: the prior source obligation remains semantically satisfied.\n"
        "- valid_refinement: representation changed, but the final graph is a more precise "
        "or ontology-compatible realization with no loss of the prior source meaning.\n"
        "- regression: concrete evidence proves that a previously satisfied source "
        "obligation became absent, weaker, wrong, detached, or attached to the wrong owner.\n"
        "- uncertain: supplied evidence cannot distinguish refinement from regression.\n\n"
        "The old RDF spelling or IRI is not automatically immutable. A changed class or "
        "target is not a regression by itself. Conversely, replacing a source-specific "
        "value with a more generic value is not refinement. RDF/OWL domain and range do "
        "not create obligations. Do not inspect any other item or aspect. A regression "
        "decision requires concrete source, old-graph, and final-graph evidence. The local "
        "final projection may be empty when an IRI, class, or label was refined; in that "
        "case you MUST search the complete final A-Box below for semantically equivalent, "
        "more specific, owner-linked, or operation-level realizations before deciding.\n"
        f"CONFIRMATION ROUND: {str(confirmation).lower()}\n\n"
        "Return JSON with exactly these keys:\n"
        '{"decision":"preserved|valid_refinement|regression|uncertain",'
        '"source_item_id":"","aspect_id":"","summary":"","source_evidence":"",'
        '"old_abox_evidence":"","final_abox_evidence":"","confidence":0.0}\n\n'
        f"PRIOR ITERATION: {iteration}\n"
        f"SOURCE ITEM ID: {item.item_id}\n"
        f"SOURCE ITEM:\n{item.evidence}\n\n"
        f"ASPECT ID: {aspect.aspect_id}\n"
        f"FIELD NAME: {aspect.field_name}\n"
        f"FIELD VALUE: {aspect.field_value}\n\n"
        "RELEVANT ONTOLOGY CONTRACT:\n"
        f"{json.dumps(contract_slice, ensure_ascii=False, sort_keys=True)}\n\n"
        f"PRIOR ACCEPTED LOCAL A-BOX:\n{old_neighborhood}\n\n"
        f"FINAL LOCAL A-BOX:\n{final_neighborhood}\n\n"
        f"COMPLETE FINAL A-BOX:\n{complete_final_abox}\n\n"
        f"PRIOR LOCAL TRIPLES NOT FOUND VERBATIM IN FINAL GRAPH:\n{removed_turtle}\n"
    )


def _validate_vote(
    data: dict[str, Any],
    *,
    item: SourceItem,
    aspect: Aspect,
) -> dict[str, Any]:
    required = {
        "decision",
        "source_item_id",
        "aspect_id",
        "summary",
        "source_evidence",
        "old_abox_evidence",
        "final_abox_evidence",
        "confidence",
    }
    if set(data) != required:
        raise ValueError("continuity vote keys differ from required schema")
    if str(data["decision"]) not in _DECISIONS:
        raise ValueError("continuity decision is invalid")
    if str(data["source_item_id"]) != item.item_id:
        raise ValueError("continuity source_item_id changed")
    if str(data["aspect_id"]) != aspect.aspect_id:
        raise ValueError("continuity aspect_id changed")
    confidence = float(data["confidence"])
    if not 0.0 <= confidence <= 1.0:
        raise ValueError("continuity confidence must be within [0, 1]")
    normalized = {key: str(data[key]) for key in required - {"confidence"}}
    normalized["confidence"] = confidence
    return normalized


def _invoke_vote(
    *,
    invoke: Callable[..., LLMJsonResult],
    model: str,
    prompt: str,
    item: SourceItem,
    aspect: Aspect,
) -> tuple[dict[str, Any], list[LLMJsonResult]]:
    calls: list[LLMJsonResult] = []
    current_prompt = prompt
    errors: list[str] = []
    for _ in range(3):
        result = invoke(model, current_prompt, max_attempts=3)
        calls.append(result)
        try:
            return _validate_vote(result.data, item=item, aspect=aspect), calls
        except (TypeError, ValueError) as exc:
            errors.append(str(exc))
            current_prompt = (
                prompt
                + "\n\nYour previous JSON failed mechanical validation: "
                + str(exc)
                + "\nReturn only corrected JSON for the same transition."
            )
    return (
        {
            "decision": "uncertain",
            "source_item_id": item.item_id,
            "aspect_id": aspect.aspect_id,
            "summary": "Continuity vote schema remained invalid.",
            "source_evidence": "",
            "old_abox_evidence": "",
            "final_abox_evidence": "",
            "confidence": 0.0,
            "schema_errors": errors,
        },
        calls,
    )


def _run_panel(
    *,
    invoke: Callable[..., LLMJsonResult],
    models: list[str],
    prompt: str,
    item: SourceItem,
    aspect: Aspect,
) -> tuple[list[dict[str, Any]], list[LLMJsonResult]]:
    ordered: list[tuple[int, str, dict[str, Any], list[LLMJsonResult]]] = []
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {
            pool.submit(
                _invoke_vote,
                invoke=invoke,
                model=model,
                prompt=prompt,
                item=item,
                aspect=aspect,
            ): (index, model)
            for index, model in enumerate(models)
        }
        for future in as_completed(futures):
            index, model = futures[future]
            vote, calls = future.result()
            ordered.append((index, model, vote, calls))
    votes: list[dict[str, Any]] = []
    results: list[LLMJsonResult] = []
    for _, model, vote, calls in sorted(ordered):
        votes.append({"model": model, **vote})
        results.extend(calls)
    return votes, results


def _unanimous(votes: list[dict[str, Any]]) -> str | None:
    decisions = {str(vote.get("decision")) for vote in votes}
    return next(iter(decisions)) if len(decisions) == 1 else None


def judge_iteration_continuity(
    *,
    prior_iterations: list[dict[str, Any]],
    final_abox_path: Path,
    ontology_contract: dict[str, Any],
    model: str,
    reviewer_model: str | None = None,
    verifier_model: str | None = None,
    invoke: Callable[..., LLMJsonResult] = invoke_json,
) -> dict[str, Any]:
    """Audit changed prior aspects; only two unanimous regression panels block."""
    models = [
        model,
        reviewer_model or model,
        verifier_model or reviewer_model or model,
    ]
    final_graph = Graph()
    final_graph.parse(str(final_abox_path), format="turtle")
    complete_final_abox = str(final_graph.serialize(format="turtle"))
    panels: list[dict[str, Any]] = []
    results: list[LLMJsonResult] = []

    for prior in sorted(prior_iterations, key=lambda item: int(item["iteration"])):
        iteration = int(prior["iteration"])
        old_graph = Graph()
        old_graph.parse(str(prior["abox_path"]), format="turtle")
        hints = str(prior.get("hints_content") or "")
        for item in parse_semantic_hint_items(hints):
            for aspect in aspects_for_item(item, ontology_contract):
                old_local = _neighborhood_graph(old_graph, item, aspect)
                missing = Graph()
                for triple in old_local:
                    if triple not in final_graph:
                        missing.add(triple)
                panel_id = (
                    f"iteration-{iteration}::{_safe_id(item.item_id)}::"
                    f"{_safe_id(aspect.aspect_id)}"
                )
                if not missing:
                    panels.append(
                        {
                            "panel_id": panel_id,
                            "iteration": iteration,
                            "source_item_id": item.item_id,
                            "aspect_id": aspect.aspect_id,
                            "decision": "preserved",
                            "confirmed_regression": False,
                            "mechanical_exact_preservation": True,
                            "detection_votes": [],
                            "escalation_votes": [],
                            "confirmation_votes": [],
                        }
                    )
                    continue

                final_local = _neighborhood_graph(final_graph, item, aspect)
                prompt = _continuity_prompt(
                    iteration=iteration,
                    item=item,
                    aspect=aspect,
                    contract_slice=project_contract(
                        ontology_contract, item, aspect
                    ),
                    old_neighborhood=str(old_local.serialize(format="turtle")),
                    final_neighborhood=str(final_local.serialize(format="turtle")),
                    complete_final_abox=complete_final_abox,
                    removed_turtle=str(missing.serialize(format="turtle")),
                    confirmation=False,
                )
                detection, calls = _run_panel(
                    invoke=invoke,
                    models=models,
                    prompt=prompt,
                    item=item,
                    aspect=aspect,
                )
                results.extend(calls)
                decision = _unanimous(detection)
                escalation: list[dict[str, Any]] = []
                if decision in {None, "uncertain"}:
                    escalation, calls = _run_panel(
                        invoke=invoke,
                        models=models,
                        prompt=prompt
                        + "\n\nThis is a fresh escalation panel after disagreement. "
                        "Prior votes are unavailable.",
                        item=item,
                        aspect=aspect,
                    )
                    results.extend(calls)
                    decision = _unanimous(escalation)

                confirmation: list[dict[str, Any]] = []
                confirmed_regression = False
                if decision == "regression":
                    confirmation_prompt = _continuity_prompt(
                        iteration=iteration,
                        item=item,
                        aspect=aspect,
                        contract_slice=project_contract(
                            ontology_contract, item, aspect
                        ),
                        old_neighborhood=str(
                            old_local.serialize(format="turtle")
                        ),
                        final_neighborhood=str(
                            final_local.serialize(format="turtle")
                        ),
                        complete_final_abox=complete_final_abox,
                        removed_turtle=str(missing.serialize(format="turtle")),
                        confirmation=True,
                    )
                    confirmation, calls = _run_panel(
                        invoke=invoke,
                        models=models,
                        prompt=confirmation_prompt,
                        item=item,
                        aspect=aspect,
                    )
                    results.extend(calls)
                    confirmed_regression = _unanimous(confirmation) == "regression"

                panels.append(
                    {
                        "panel_id": panel_id,
                        "iteration": iteration,
                        "source_item_id": item.item_id,
                        "aspect_id": aspect.aspect_id,
                        "decision": (
                            "regression"
                            if confirmed_regression
                            else decision
                            if decision in _DECISIONS
                            else "uncertain"
                        ),
                        "confirmed_regression": confirmed_regression,
                        "mechanical_exact_preservation": False,
                        "detection_votes": detection,
                        "escalation_votes": escalation,
                        "confirmation_votes": confirmation,
                    }
                )

    regressions = [
        panel for panel in panels if panel["confirmed_regression"]
    ]
    uncertain = [
        panel for panel in panels if panel["decision"] == "uncertain"
    ]
    token_usage: dict[str, int] = {}
    elapsed_seconds = 0.0
    for result in results:
        elapsed_seconds += result.elapsed_seconds
        for key, value in (result.token_usage or {}).items():
            if isinstance(value, int):
                token_usage[key] = token_usage.get(key, 0) + value
    return {
        "schema_version": "llm-iteration-continuity.v1",
        "policy": "changed_aspects_three_vote_detection_and_confirmation",
        "blocking": True,
        "accepted": not regressions,
        "summary": (
            f"{len(regressions)} confirmed regression(s); "
            f"{len(uncertain)} non-blocking uncertain transition(s)."
        ),
        "confirmed_regressions": regressions,
        "uncertain_transitions": uncertain,
        "panels": panels,
        "final_abox_path": str(final_abox_path),
        "elapsed_seconds": round(elapsed_seconds, 3),
        "token_usage": token_usage,
    }
