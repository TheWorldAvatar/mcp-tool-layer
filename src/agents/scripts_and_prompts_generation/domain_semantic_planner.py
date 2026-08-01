from __future__ import annotations

import json
from typing import Any, Callable

from src.agents.scripts_and_prompts_generation.domain_generation_config import (
    DomainGenerationConfig,
    PLANNING_MODEL,
)
from src.agents.scripts_and_prompts_generation.level1_code_repair import invoke_json


JsonPlanner = Callable[[str, str], dict[str, Any]]


def _invoke(model: str, prompt: str) -> dict[str, Any]:
    return invoke_json(
        model,
        prompt,
        timeout_seconds=600,
        max_attempts=3,
        provider_max_retries=0,
    ).data


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


def plan_domain_semantics(
    *,
    config: DomainGenerationConfig,
    parsed: dict[str, Any],
    contract: dict[str, Any],
    planner: JsonPlanner | None = None,
) -> dict[str, Any]:
    """Use GPT-5 for the two authorized domain-semantic planning decisions."""
    run = planner or _invoke
    projection = _ontology_projection(parsed)
    classes = projection["classes"]
    properties = projection["properties"]

    top_prompt = (
        "Select the single top entity class for this ontology-driven extraction and KG "
        "pipeline. Use only the supplied active T-Box projection. Prefer the class whose "
        "outgoing object properties organize the main downstream entity graph. Do not rely "
        "on iteration names or runtime configuration. Return JSON only with class_local, "
        "rationale, and evidence, where evidence is a non-empty list of exact supplied class "
        "or property local names.\n\n"
        + json.dumps(projection, ensure_ascii=False)
    )
    top_raw = run(PLANNING_MODEL, top_prompt)
    top_local = str(top_raw.get("class_local") or "").strip()
    if top_local not in classes:
        raise ValueError(f"planner selected unknown top entity class: {top_local!r}")
    evidence = [
        str(value).strip()
        for value in top_raw.get("evidence") or []
        if str(value).strip()
    ]
    invalid_evidence = sorted(set(evidence) - (set(classes) | set(properties)))
    if not evidence or invalid_evidence:
        raise ValueError(
            f"invalid top entity evidence: invalid={invalid_evidence}"
        )
    rationale = str(top_raw.get("rationale") or "").strip()
    if not rationale:
        raise ValueError("top entity planner must provide a rationale")
    top_entity = {
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

    profile = config.profile
    iteration_prompt = (
        "Partition the supplied active T-Box into the requested downstream main iterations "
        "after Iteration 1. Iteration 1 is fixed and creates only the selected top entity. "
        "You decide which classes and properties each later iteration owns. Do not emit model "
        "names, MCP names, paths, agent switches, validation regexes, or source examples. "
        "Every supplied non-top materializable class must be assigned exactly once to a main "
        "iteration unless it is an abstract parent used only for organization. Every object "
        "property needed to link assigned classes, and every T-Box-required link, must be "
        "assigned. Enrichment passes may only enrich one main iteration and must not own new "
        "classes. Return JSON only: {iterations:[{iteration_number,name,description,"
        "responsibilities:{classes,object_properties},requires_pre_extraction,"
        "enrichment_focus:[{name,description}]}]}. Use exactly the requested number of main "
        "iterations and total enrichment passes.\n\n"
        + json.dumps(
            {
                "workflow_profile": config.workflow_profile,
                "profile_constraints": profile,
                "top_entity": top_entity,
                "required_links": contract.get("required_links") or [],
                "ordered_member_profile": contract.get("ordered_member_profile") or {},
                "tbox": projection,
            },
            ensure_ascii=False,
        )
    )
    iteration_raw = run(PLANNING_MODEL, iteration_prompt)
    iterations = iteration_raw.get("iterations") or []
    if not isinstance(iterations, list):
        raise ValueError("iteration planner response must contain an iterations array")
    if len(iterations) != int(profile["main_iterations"]):
        raise ValueError(
            f"{config.workflow_profile} requires {profile['main_iterations']} main iterations"
        )
    enrichment_count = sum(
        len((iteration or {}).get("enrichment_focus") or [])
        for iteration in iterations
        if isinstance(iteration, dict)
    )
    if enrichment_count != int(profile["enrichment_passes"]):
        raise ValueError(
            f"{config.workflow_profile} requires {profile['enrichment_passes']} enrichment passes"
        )
    pre_count = sum(
        bool((iteration or {}).get("requires_pre_extraction"))
        for iteration in iterations
        if isinstance(iteration, dict)
    )
    if pre_count != int(profile["pre_extraction_iterations"]):
        raise ValueError(
            f"{config.workflow_profile} requires {profile['pre_extraction_iterations']} "
            "pre-extraction iterations"
        )

    return {
        "schema_version": "domain-semantic-decisions.v1",
        "model": PLANNING_MODEL,
        "top_entity": top_entity,
        "iteration_decomposition": {"iterations": iterations},
    }
