"""Run independent GPT reviews of binary class reusability for one T-Box."""

from __future__ import annotations

import argparse
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from rdflib import Graph, OWL, RDF, RDFS, URIRef

from src.agents.scripts_and_prompts_generation.level1_code_repair import (
    invoke_json,
)


REUSE_SCOPES = {
    "global",
    "document",
    "top_entity",
    "global_value",
    "global_reference",
}
CONFIDENCE_VALUES = {"high", "medium", "low"}


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _is_candidate_class(value: Any) -> bool:
    if not isinstance(value, URIRef):
        return False
    text = str(value)
    return text not in {
        str(OWL.Thing),
        str(OWL.Nothing),
        str(RDFS.Resource),
        str(RDFS.Class),
    } and not text.startswith("http://www.w3.org/2001/XMLSchema#")


def _class_inventory(graph: Graph) -> list[str]:
    """Collect declared and structurally referenced named classes."""
    candidates: set[URIRef] = set()
    for class_type in (OWL.Class, RDFS.Class):
        candidates.update(
            value
            for value in graph.subjects(RDF.type, class_type)
            if _is_candidate_class(value)
        )
    for child, parent in graph.subject_objects(RDFS.subClassOf):
        if _is_candidate_class(child):
            candidates.add(child)
        if _is_candidate_class(parent):
            candidates.add(parent)
    for predicate in (RDFS.domain, RDFS.range, OWL.onClass):
        candidates.update(
            value
            for value in graph.objects(None, predicate)
            if _is_candidate_class(value)
        )
    for list_head in graph.objects(None, OWL.unionOf):
        candidates.update(
            value for value in graph.items(list_head) if _is_candidate_class(value)
        )
    return sorted(str(value) for value in candidates)


def _render_prompt(
    *,
    template: str,
    tbox_content: str,
    tbox_sha256: str,
    class_inventory: list[str],
    materialization_plan: dict[str, Any],
    supporting_tboxes: list[dict[str, Any]],
    cross_tbox_contexts: list[dict[str, Any]],
) -> str:
    return (
        template.replace(
            "{class_inventory_json}",
            json.dumps(class_inventory, ensure_ascii=False, indent=2),
        )
        .replace(
            "{materialization_plan_json}",
            json.dumps(materialization_plan, ensure_ascii=False, indent=2),
        )
        .replace(
            "{supporting_tboxes_json}",
            json.dumps(supporting_tboxes, ensure_ascii=False, indent=2),
        )
        .replace(
            "{cross_tbox_contexts_json}",
            json.dumps(cross_tbox_contexts, ensure_ascii=False, indent=2),
        )
        .replace("{tbox_sha256}", tbox_sha256)
        .replace("{tbox_content}", tbox_content)
    )


def _materialization_projection(plan: dict[str, Any]) -> dict[str, Any]:
    """Keep only occurrence and ownership evidence relevant to reuse."""
    return {
        "schema_version": "reuse-materialization-projection.v1",
        "ontology": plan.get("ontology"),
        "iterations": [
            {
                "iteration_number": item.get("iteration_number"),
                "slot_kind": item.get("slot_kind"),
                "per_entity": item.get("per_entity"),
                "responsibilities": item.get("responsibilities") or {},
                "linked_materialization_classes": (
                    item.get("linked_materialization_classes") or []
                ),
            }
            for item in plan.get("iterations") or []
            if isinstance(item, dict)
        ],
    }


def _render_format_retry_prompt(
    *,
    original_prompt: str,
    invalid_result: dict[str, Any],
    validation_errors: list[str],
) -> str:
    """Request a schema-only correction without exposing other trial information."""
    return (
        original_prompt
        + "\n\n"
        + "FORMAT CORRECTION REQUEST\n"
        + "Your previous JSON response for this trial failed machine validation. "
        + "Return a corrected complete JSON object for the original task. Preserve "
        + "the semantic decisions unless changing one is required to resolve a listed "
        + "validation error. Do not discuss the correction and do not use Markdown.\n\n"
        + "Exact validation errors:\n"
        + json.dumps(validation_errors, ensure_ascii=False, indent=2)
        + "\n\nInvalid JSON object:\n"
        + json.dumps(invalid_result, ensure_ascii=False, indent=2)
    )


def _validate_result(
    result: dict[str, Any],
    *,
    class_inventory: list[str],
    tbox_sha256: str,
) -> dict[str, Any]:
    errors: list[str] = []
    if result.get("schema_version") != "single-tbox-operational-reusability.v3":
        errors.append("schema_version must be single-tbox-operational-reusability.v3")
    if result.get("decision_target") != "pipeline_reuse_enabled":
        errors.append("decision_target must be pipeline_reuse_enabled")
    if result.get("tbox_sha256") != tbox_sha256:
        errors.append("tbox_sha256 does not match the supplied T-Box")

    reusable = result.get("reusable_classes")
    non_reusable = result.get("non_reusable_classes")
    if not isinstance(reusable, list):
        errors.append("reusable_classes must be a list")
        reusable = []
    if not isinstance(non_reusable, list):
        errors.append("non_reusable_classes must be a list")
        non_reusable = []

    decisions: dict[str, str] = {}
    inventory = set(class_inventory)
    for decision, items in (("reusable", reusable), ("non_reusable", non_reusable)):
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                errors.append(f"{decision}[{index}] must be an object")
                continue
            class_iri = str(item.get("class_iri") or "").strip()
            if not class_iri:
                errors.append(f"{decision}[{index}] is missing class_iri")
                continue
            if class_iri not in inventory:
                errors.append(f"{decision} contains out-of-inventory class {class_iri}")
            if class_iri in decisions:
                errors.append(f"class appears more than once: {class_iri}")
            decisions[class_iri] = decision
            confidence = str(item.get("confidence") or "")
            if confidence not in CONFIDENCE_VALUES:
                errors.append(f"{class_iri} has invalid confidence {confidence!r}")
            tbox_evidence = item.get("tbox_evidence")
            if not isinstance(tbox_evidence, list) or not tbox_evidence:
                errors.append(f"{class_iri} must contain non-empty tbox_evidence")
            pipeline_evidence = item.get("pipeline_evidence")
            if not isinstance(pipeline_evidence, list) or not pipeline_evidence:
                errors.append(f"{class_iri} must contain non-empty pipeline_evidence")
            contextual_value_veto = item.get("contextual_value_veto")
            if not isinstance(contextual_value_veto, dict):
                errors.append(f"{class_iri} must contain contextual_value_veto")
                veto_applies = None
            else:
                veto_applies = contextual_value_veto.get("applies")
                if not isinstance(veto_applies, bool):
                    errors.append(
                        f"{class_iri} contextual_value_veto.applies must be boolean"
                    )
                for field in ("direct_contextual_properties", "repeated_owner_paths"):
                    if not isinstance(contextual_value_veto.get(field), list):
                        errors.append(
                            f"{class_iri} contextual_value_veto.{field} must be a list"
                        )
                if not isinstance(
                    contextual_value_veto.get("ownership_recoverable_after_merge"),
                    bool,
                ):
                    errors.append(
                        f"{class_iri} contextual_value_veto."
                        "ownership_recoverable_after_merge must be boolean"
                    )
                if not str(contextual_value_veto.get("explanation") or "").strip():
                    errors.append(
                        f"{class_iri} contextual_value_veto.explanation is required"
                    )
            if decision == "reusable":
                if veto_applies is True:
                    errors.append(
                        f"{class_iri} cannot be reusable when contextual-value veto applies"
                    )
                scope = str(item.get("reuse_scope") or "")
                if scope not in REUSE_SCOPES:
                    errors.append(f"{class_iri} has invalid reuse_scope {scope!r}")
                if not str(item.get("match_basis") or "").strip():
                    errors.append(f"{class_iri} is missing match_basis")
                if not str(item.get("false_merge_risk") or "").strip():
                    errors.append(f"{class_iri} is missing false_merge_risk")
            elif not str(item.get("reason") or "").strip():
                errors.append(f"{class_iri} is missing non-reuse reason")

    missing = sorted(inventory - set(decisions))
    if missing:
        errors.append("missing inventory classes: " + ", ".join(missing))
    return {
        "ok": not errors,
        "errors": errors,
        "inventory_count": len(class_inventory),
        "classified_count": len(decisions),
        "reusable_count": sum(value == "reusable" for value in decisions.values()),
        "non_reusable_count": sum(
            value == "non_reusable" for value in decisions.values()
        ),
    }


def _reference_decisions(path: Path | None) -> dict[str, str]:
    if path is None:
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    prefixes = payload.get("prefixes") or {}

    def expand(value: str) -> str:
        prefix, separator, local = str(value).partition(":")
        if separator and prefix in prefixes:
            return str(prefixes[prefix]) + local
        return str(value)

    decisions: dict[str, str] = {}
    for value in payload.get("reusable_classes") or []:
        decisions[expand(value)] = "reusable"
    for value in payload.get("non_reusable_classes") or []:
        decisions[expand(value)] = "non_reusable"
    return decisions


def _decision_map(result: dict[str, Any]) -> dict[str, str]:
    decisions: dict[str, str] = {}
    for item in result.get("reusable_classes") or []:
        if isinstance(item, dict) and item.get("class_iri"):
            decisions[str(item["class_iri"])] = "reusable"
    for item in result.get("non_reusable_classes") or []:
        if isinstance(item, dict) and item.get("class_iri"):
            decisions[str(item["class_iri"])] = "non_reusable"
    return decisions


def _decision_projection(
    result: dict[str, Any],
) -> dict[str, tuple[str, str | None]]:
    projection: dict[str, tuple[str, str | None]] = {}
    for item in result.get("reusable_classes") or []:
        if isinstance(item, dict) and item.get("class_iri"):
            projection[str(item["class_iri"])] = (
                "reusable",
                str(item.get("reuse_scope") or ""),
            )
    for item in result.get("non_reusable_classes") or []:
        if isinstance(item, dict) and item.get("class_iri"):
            projection[str(item["class_iri"])] = ("non_reusable", None)
    return projection


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def run_experiment(
    *,
    tbox_path: Path,
    prompt_path: Path,
    output_dir: Path,
    model: str,
    trials: int,
    parallelism: int,
    materialization_plan_path: Path,
    reference_path: Path | None = None,
    format_retry_limit: int = 2,
    supporting_tbox_paths: list[Path] | None = None,
    cross_tbox_context_paths: list[Path] | None = None,
) -> dict[str, Any]:
    """Run independent reviews and summarize decision stability."""
    if trials < 1:
        raise ValueError("trials must be at least 1")
    if format_retry_limit < 0:
        raise ValueError("format_retry_limit must be at least 0")
    tbox_content = tbox_path.read_text(encoding="utf-8")
    tbox_sha256 = _sha256_text(tbox_content)
    graph = Graph()
    graph.parse(data=tbox_content, format="turtle")
    class_inventory = _class_inventory(graph)
    template = prompt_path.read_text(encoding="utf-8")
    materialization_plan = json.loads(
        materialization_plan_path.read_text(encoding="utf-8")
    )
    if not isinstance(materialization_plan, dict):
        raise ValueError("materialization plan must be a JSON object")
    materialization_plan_sha256 = _sha256_text(
        json.dumps(
            materialization_plan,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    supporting_tboxes = []
    for path in supporting_tbox_paths or []:
        content = path.read_text(encoding="utf-8")
        supporting_tboxes.append(
            {
                "path": str(path),
                "sha256": _sha256_text(content),
                "content": content,
            }
        )
    cross_tbox_contexts = []
    for path in cross_tbox_context_paths or []:
        content = path.read_text(encoding="utf-8")
        cross_tbox_contexts.append(
            {
                "path": str(path),
                "sha256": _sha256_text(content),
                "content": json.loads(content),
            }
        )
    prompt = _render_prompt(
        template=template,
        tbox_content=tbox_content,
        tbox_sha256=tbox_sha256,
        class_inventory=class_inventory,
        materialization_plan=_materialization_projection(materialization_plan),
        supporting_tboxes=supporting_tboxes,
        cross_tbox_contexts=cross_tbox_contexts,
    )
    prompt_sha256 = _sha256_text(prompt)
    reference = _reference_decisions(reference_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_json(
        output_dir / "run_manifest.json",
        {
            "schema_version": "tbox-reusability-experiment.v1",
            "model": model,
            "trials": trials,
            "parallelism": parallelism,
            "format_retry_limit": format_retry_limit,
            "tbox_path": str(tbox_path),
            "tbox_sha256": tbox_sha256,
            "prompt_path": str(prompt_path),
            "prompt_sha256": prompt_sha256,
            "materialization_plan_path": str(materialization_plan_path),
            "materialization_plan_sha256": materialization_plan_sha256,
            "supporting_tboxes": [
                {"path": item["path"], "sha256": item["sha256"]}
                for item in supporting_tboxes
            ],
            "cross_tbox_contexts": [
                {"path": item["path"], "sha256": item["sha256"]}
                for item in cross_tbox_contexts
            ],
            "class_inventory": class_inventory,
            "reference_path": str(reference_path) if reference_path else None,
        },
    )

    def run_trial(trial: int) -> dict[str, Any]:
        attempt_history: list[dict[str, Any]] = []
        effective_prompt = prompt
        final_attempt: dict[str, Any] | None = None
        accepted_attempt: int | None = None
        for attempt in range(1, format_retry_limit + 2):
            try:
                response = invoke_json(
                    model,
                    effective_prompt,
                    timeout_seconds=600,
                    max_attempts=3,
                    provider_max_retries=0,
                )
                validation = _validate_result(
                    response.data,
                    class_inventory=class_inventory,
                    tbox_sha256=tbox_sha256,
                )
                attempt_payload = {
                    "schema_version": "tbox-reusability-trial-attempt.v1",
                    "trial": trial,
                    "attempt": attempt,
                    "attempt_type": "initial" if attempt == 1 else "format_retry",
                    "model": model,
                    "tbox_sha256": tbox_sha256,
                    "prompt_sha256": prompt_sha256,
                    "attempt_prompt_sha256": _sha256_text(effective_prompt),
                    "parsed_response": response.data,
                    "raw_response": response.raw_response,
                    "elapsed_seconds": response.elapsed_seconds,
                    "token_usage": response.token_usage,
                    "validation": validation,
                }
            except Exception as exc:
                attempt_payload = {
                    "schema_version": "tbox-reusability-trial-attempt.v1",
                    "trial": trial,
                    "attempt": attempt,
                    "attempt_type": "initial" if attempt == 1 else "format_retry",
                    "model": model,
                    "tbox_sha256": tbox_sha256,
                    "prompt_sha256": prompt_sha256,
                    "attempt_prompt_sha256": _sha256_text(effective_prompt),
                    "error": f"{type(exc).__name__}: {exc}",
                    "validation": {"ok": False, "errors": [str(exc)]},
                }
                attempt_path = output_dir / f"trial_{trial}_attempt_{attempt}.json"
                _write_json(attempt_path, attempt_payload)
                attempt_history.append(
                    {
                        "attempt": attempt,
                        "artifact": attempt_path.name,
                        "attempt_type": attempt_payload["attempt_type"],
                        "validation": attempt_payload["validation"],
                        "error": attempt_payload["error"],
                    }
                )
                final_attempt = attempt_payload
                break

            attempt_path = output_dir / f"trial_{trial}_attempt_{attempt}.json"
            _write_json(attempt_path, attempt_payload)
            attempt_history.append(
                {
                    "attempt": attempt,
                    "artifact": attempt_path.name,
                    "attempt_type": attempt_payload["attempt_type"],
                    "validation": validation,
                }
            )
            final_attempt = attempt_payload
            if validation["ok"]:
                accepted_attempt = attempt
                break
            if attempt <= format_retry_limit:
                effective_prompt = _render_format_retry_prompt(
                    original_prompt=prompt,
                    invalid_result=response.data,
                    validation_errors=validation["errors"],
                )

        if final_attempt is None:
            raise RuntimeError("trial completed without an attempt")
        payload = {
            key: value
            for key, value in final_attempt.items()
            if key
            not in {
                "schema_version",
                "attempt",
                "attempt_type",
                "attempt_prompt_sha256",
            }
        }
        payload.update(
            {
                "schema_version": "tbox-reusability-trial.v1",
                "trial": trial,
                "accepted_attempt": accepted_attempt,
                "attempt_count": len(attempt_history),
                "attempt_history": attempt_history,
                "recovered_format_failure": (
                    accepted_attempt is not None and accepted_attempt > 1
                ),
                "format_retry_exhausted": (
                    accepted_attempt is None
                    and "parsed_response" in final_attempt
                    and len(attempt_history) == format_retry_limit + 1
                ),
            }
        )
        _write_json(output_dir / f"trial_{trial}.json", payload)
        return payload

    trial_results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max(1, min(parallelism, trials))) as executor:
        futures = {
            executor.submit(run_trial, trial): trial for trial in range(1, trials + 1)
        }
        for future in as_completed(futures):
            trial_results.append(future.result())
    trial_results.sort(key=lambda item: int(item["trial"]))

    valid_results = [
        item
        for item in trial_results
        if (item.get("validation") or {}).get("ok")
        and isinstance(item.get("parsed_response"), dict)
    ]
    decision_maps = [_decision_map(item["parsed_response"]) for item in valid_results]
    decision_projections = [
        _decision_projection(item["parsed_response"]) for item in valid_results
    ]
    class_stability: list[dict[str, Any]] = []
    for class_iri in class_inventory:
        votes = [decisions.get(class_iri) for decisions in decision_maps]
        projection_votes = [
            decisions.get(class_iri) for decisions in decision_projections
        ]
        reusable_votes = votes.count("reusable")
        non_reusable_votes = votes.count("non_reusable")
        majority = (
            "reusable"
            if reusable_votes > non_reusable_votes
            else "non_reusable"
            if non_reusable_votes > reusable_votes
            else "tie"
        )
        class_stability.append(
            {
                "class_iri": class_iri,
                "reusable_votes": reusable_votes,
                "non_reusable_votes": non_reusable_votes,
                "scope_votes": {
                    str(scope): projection_votes.count(("reusable", scope))
                    for scope in sorted(
                        {
                            scope
                            for decision, scope in projection_votes
                            if decision == "reusable" and scope is not None
                        }
                    )
                },
                "unanimous": bool(projection_votes)
                and len(set(projection_votes)) == 1,
                "majority_decision": majority,
                "reference_decision": reference.get(class_iri),
                "majority_matches_reference": (
                    majority == reference[class_iri]
                    if class_iri in reference and majority != "tie"
                    else None
                ),
            }
        )

    summary = {
        "schema_version": "tbox-reusability-experiment-summary.v1",
        "model": model,
        "tbox_sha256": tbox_sha256,
        "prompt_sha256": prompt_sha256,
        "materialization_plan_sha256": materialization_plan_sha256,
        "supporting_tbox_hashes": [
            {"path": item["path"], "sha256": item["sha256"]}
            for item in supporting_tboxes
        ],
        "cross_tbox_context_hashes": [
            {"path": item["path"], "sha256": item["sha256"]}
            for item in cross_tbox_contexts
        ],
        "requested_trials": trials,
        "valid_trials": len(valid_results),
        "all_trials_valid": len(valid_results) == trials,
        "format_retry_limit": format_retry_limit,
        "total_attempt_count": sum(
            int(item.get("attempt_count") or 0) for item in trial_results
        ),
        "trial_attempt_counts": {
            str(item["trial"]): int(item.get("attempt_count") or 0)
            for item in trial_results
        },
        "recovered_format_failures": sum(
            bool(item.get("recovered_format_failure")) for item in trial_results
        ),
        "recovered_format_failure_trials": [
            item["trial"]
            for item in trial_results
            if item.get("recovered_format_failure")
        ],
        "exhausted_format_failures": sum(
            bool(item.get("format_retry_exhausted")) for item in trial_results
        ),
        "exhausted_format_failure_trials": [
            item["trial"]
            for item in trial_results
            if item.get("format_retry_exhausted")
        ],
        "inventory_count": len(class_inventory),
        "unanimous_class_count": sum(
            bool(item["unanimous"]) for item in class_stability
        ),
        "disagreement_classes": [
            item["class_iri"] for item in class_stability if not item["unanimous"]
        ],
        "majority_reference_matches": sum(
            item["majority_matches_reference"] is True for item in class_stability
        ),
        "majority_reference_mismatches": [
            item["class_iri"]
            for item in class_stability
            if item["majority_matches_reference"] is False
        ],
        "class_stability": class_stability,
    }
    summary["passed_10_of_10_gate"] = bool(
        trials == 10
        and summary["all_trials_valid"]
        and summary["unanimous_class_count"] == summary["inventory_count"]
    )
    _write_json(output_dir / "summary.json", summary)
    return summary


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run independent GPT binary reusability reviews for one T-Box."
    )
    parser.add_argument("--tbox", type=Path, required=True)
    parser.add_argument("--prompt", type=Path, required=True)
    parser.add_argument("--materialization-plan", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--model", default="gpt-5")
    parser.add_argument("--trials", type=int, default=10)
    parser.add_argument("--parallelism", type=int, default=3)
    parser.add_argument("--format-retry-limit", type=int, default=2)
    parser.add_argument("--supporting-tbox", action="append", type=Path, default=[])
    parser.add_argument("--cross-tbox-context", action="append", type=Path, default=[])
    parser.add_argument("--reference-policy", type=Path)
    return parser


def main() -> int:
    args = _parser().parse_args()
    if args.trials < 1:
        raise ValueError("--trials must be at least 1")
    if args.format_retry_limit < 0:
        raise ValueError("--format-retry-limit must be at least 0")
    summary = run_experiment(
        tbox_path=args.tbox,
        prompt_path=args.prompt,
        output_dir=args.output_dir,
        model=args.model,
        trials=args.trials,
        parallelism=args.parallelism,
        materialization_plan_path=args.materialization_plan,
        reference_path=args.reference_policy,
        format_retry_limit=args.format_retry_limit,
        supporting_tbox_paths=args.supporting_tbox,
        cross_tbox_context_paths=args.cross_tbox_context,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["passed_10_of_10_gate"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
