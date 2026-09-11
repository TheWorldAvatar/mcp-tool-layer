"""Generate a stable class-reuse policy from T-Box + compiled plan.

Default generation is ten independent GPT-5 trials with a unanimous
binary+scope gate. The official review prompt is compiler infrastructure.
Normalization lives in `reuse_policy.py`. See compile/README.md.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from rdflib import Graph, OWL, RDF, RDFS, URIRef

from models.locked_llm import LOCKED_GENERATION_MODEL


PROMPT_PATH = Path(__file__).with_name("reuse_judgment_prompt.md")
OFFICIAL_PROMPT_PATH = (
    Path(__file__).resolve().parents[3]
    / "configs"
    / "meta_task"
    / "gpt5_single_tbox_binary_reusability_prompt.md"
)
GENERATED_SCHEMA = "llm-class-reuse-policy.v1"
JUDGMENT_SCHEMA = "single-tbox-operational-reusability.v3"
STABLE_DERIVATION_MODE = "gpt5_first_valid_trial"
DEFAULT_REUSE_TRIALS = 1
DEFAULT_REUSE_PARALLELISM = 1
REUSE_SCOPES = {
    "global",
    "document",
    "top_entity",
    "global_value",
    "global_reference",
}
CONFIDENCE_VALUES = {"high", "medium", "low"}
DEFAULT_NON_REUSE_SCOPE = "occurrence_local"


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _json_digest(value: Any) -> str:
    return _sha256_text(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )


def _local_name(iri: str) -> str:
    text = str(iri or "").rstrip("/")
    return text.rsplit("#", 1)[-1] if "#" in text else text.rsplit("/", 1)[-1]


def _display_path(path: Path, repository_root: Path | None) -> str:
    resolved = path.resolve()
    if repository_root is not None:
        try:
            return resolved.relative_to(repository_root.resolve()).as_posix()
        except ValueError:
            pass
    return str(resolved)


_VOCABULARY_IRI_PREFIXES = (
    "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "http://www.w3.org/2000/01/rdf-schema#",
    "http://www.w3.org/2002/07/owl#",
    "http://www.w3.org/2001/XMLSchema#",
)


def _is_candidate_class(value: Any) -> bool:
    if not isinstance(value, URIRef):
        return False
    text = str(value)
    if any(text.startswith(prefix) for prefix in _VOCABULARY_IRI_PREFIXES):
        return False
    return text not in {
        str(OWL.Thing),
        str(OWL.Nothing),
        str(RDFS.Resource),
        str(RDFS.Class),
    }


def class_inventory_from_ttl(tbox_content: str) -> list[str]:
    """Collect declared and structurally referenced named classes."""
    graph = Graph()
    graph.parse(data=tbox_content, format="turtle")
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


def materialization_projection(
    plan: dict[str, Any], *, top_entity: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Keep only occurrence and ownership evidence relevant to reuse."""
    projection: dict[str, Any] = {
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
    if isinstance(top_entity, dict) and (
        top_entity.get("class_iri") or top_entity.get("class_local")
    ):
        projection["top_entity"] = {
            "class_local": str(top_entity.get("class_local") or "").strip(),
            "class_iri": str(top_entity.get("class_iri") or "").strip(),
        }
    return projection


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


def _render_format_retry_prompt(
    *,
    original_prompt: str,
    invalid_result: dict[str, Any],
    validation_errors: list[str],
) -> str:
    return (
        original_prompt
        + "\n\n"
        + "FORMAT CORRECTION REQUEST\n"
        + "Your previous JSON response failed machine validation. "
        + "Return a corrected complete JSON object for the original task. Preserve "
        + "the semantic decisions unless changing one is required to resolve a listed "
        + "validation error. Do not discuss the correction and do not use Markdown.\n\n"
        + "Exact validation errors:\n"
        + json.dumps(validation_errors, ensure_ascii=False, indent=2)
        + "\n\nInvalid JSON object:\n"
        + json.dumps(invalid_result, ensure_ascii=False, indent=2)
    )


def validate_judgment(
    result: dict[str, Any],
    *,
    class_inventory: list[str],
    tbox_sha256: str,
) -> dict[str, Any]:
    errors: list[str] = []
    if result.get("schema_version") != JUDGMENT_SCHEMA:
        errors.append(f"schema_version must be {JUDGMENT_SCHEMA}")
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


def reuse_trial_settings() -> tuple[int, int]:
    """Return (trials, parallelism) from env, defaulting to one valid trial."""
    trials = int(os.environ.get("TWA_REUSE_TRIALS") or DEFAULT_REUSE_TRIALS)
    parallelism = int(
        os.environ.get("TWA_REUSE_PARALLELISM") or DEFAULT_REUSE_PARALLELISM
    )
    if trials < 1:
        raise ValueError("TWA_REUSE_TRIALS must be at least 1")
    if parallelism < 1:
        raise ValueError("TWA_REUSE_PARALLELISM must be at least 1")
    return trials, parallelism


def stability_gate_passed(summary: dict[str, Any], trials: int) -> bool:
    """True when every requested trial is valid and every class is unanimous."""
    if not isinstance(summary, dict):
        return False
    if int(summary.get("requested_trials") or 0) != trials:
        return False
    if not summary.get("all_trials_valid"):
        return False
    inventory = int(summary.get("inventory_count") or -1)
    unanimous = int(summary.get("unanimous_class_count") or -2)
    if inventory < 1 or unanimous != inventory:
        return False
    if trials == DEFAULT_REUSE_TRIALS:
        return bool(summary.get("passed_10_of_10_gate"))
    return True


def _stability_gate_error(summary: dict[str, Any], trials: int) -> str:
    disagreements = [
        str(item)
        for item in (summary.get("disagreement_classes") or [])
        if str(item).strip()
    ]
    if trials == DEFAULT_REUSE_TRIALS:
        prefix = "class-reuse 10/10 stability gate failed"
    else:
        prefix = f"class-reuse stability gate failed ({trials} trials)"
    parts = [
        f"valid_trials={summary.get('valid_trials')}/{trials}",
        (
            "unanimous="
            f"{summary.get('unanimous_class_count')}/"
            f"{summary.get('inventory_count')}"
        ),
    ]
    if disagreements:
        parts.append("disagreements=" + ", ".join(disagreements))
    return prefix + ": " + "; ".join(parts)


def first_valid_trial(trials_dir: Path) -> dict[str, Any]:
    """Load the first valid independent trial artifact from an experiment dir."""
    for path in sorted(trials_dir.glob("trial_*.json")):
        if "_attempt_" in path.name:
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        if (payload.get("validation") or {}).get("ok") and isinstance(
            payload.get("parsed_response"), dict
        ):
            return payload
    raise ValueError(f"no valid representative trial in {trials_dir}")


def compile_policy_from_judgment(
    result: dict[str, Any],
    *,
    tbox_sha256: str,
    inputs_digest: str,
    model: str,
    derivation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    classes: list[dict[str, Any]] = []
    for item in result.get("reusable_classes") or []:
        if not isinstance(item, dict):
            continue
        class_iri = str(item.get("class_iri") or "").strip()
        classes.append(
            {
                "class_iri": class_iri,
                "class_local": _local_name(class_iri),
                "reusable": True,
                "reuse_scope": str(item.get("reuse_scope") or "").strip(),
                "match_basis": str(item.get("match_basis") or "").strip(),
                "rationale": str(item.get("false_merge_risk") or "").strip(),
                "confidence": str(item.get("confidence") or "").strip(),
            }
        )
    for item in result.get("non_reusable_classes") or []:
        if not isinstance(item, dict):
            continue
        class_iri = str(item.get("class_iri") or "").strip()
        classes.append(
            {
                "class_iri": class_iri,
                "class_local": _local_name(class_iri),
                "reusable": False,
                "reuse_scope": DEFAULT_NON_REUSE_SCOPE,
                "match_basis": "not applicable",
                "rationale": str(item.get("reason") or "").strip(),
                "confidence": str(item.get("confidence") or "").strip(),
            }
        )
    return {
        "schema_version": GENERATED_SCHEMA,
        "status": "generated",
        "tbox_sha256": tbox_sha256,
        "inputs_digest": inputs_digest,
        "derivation": derivation
        or {
            "mode": "single_gpt5_judgment",
            "semantic_authority": "tbox_bundle",
            "model": model,
        },
        "classes": sorted(classes, key=lambda item: str(item["class_iri"])),
    }


def assemble_stable_reuse_policy(
    *,
    summary: dict[str, Any],
    representative_judgment: dict[str, Any],
    tbox_sha256: str,
    inputs_digest: str,
    model: str,
    trials: int,
    representative_trial: int | None = None,
) -> dict[str, Any]:
    """Compile an operational policy from one valid trial.

    A 10/10 gate is recorded when present; it does not block generation.
    """
    return compile_policy_from_judgment(
        representative_judgment,
        tbox_sha256=tbox_sha256,
        inputs_digest=inputs_digest,
        model=model,
        derivation={
            "mode": STABLE_DERIVATION_MODE,
            "semantic_authority": "tbox_bundle",
            "model": model,
            "trials": trials,
            "valid_trials": summary.get("valid_trials"),
            "passed_stability_gate": stability_gate_passed(summary, trials),
            "representative_trial": representative_trial,
        },
    )


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _cached_policy_matches(
    path: Path,
    *,
    inputs_digest: str,
    class_inventory: list[str],
) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("schema_version") != GENERATED_SCHEMA:
        return None
    if (payload.get("derivation") or {}).get("mode") != STABLE_DERIVATION_MODE:
        return None
    if payload.get("inputs_digest") != inputs_digest:
        return None
    observed = {
        str(item.get("class_iri") or "").strip()
        for item in payload.get("classes") or []
        if isinstance(item, dict)
    }
    if observed != set(class_inventory):
        return None
    try:
        from src.extraction_prompt_generation.compile.reuse_policy import (
            normalize_reuse_policy,
        )

        normalize_reuse_policy(
            payload,
            source_text=path.read_text(encoding="utf-8"),
        )
    except ValueError:
        return None
    return payload


def _usable_existing_trial(
    trials_dir: Path,
    *,
    tbox_sha256: str,
    plan_sha256: str,
) -> dict[str, Any] | None:
    inputs_path = trials_dir / "generation_inputs.json"
    if inputs_path.is_file():
        try:
            recorded = json.loads(inputs_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(recorded, dict):
            return None
        if recorded.get("tbox_sha256") != tbox_sha256:
            return None
    elif not trials_dir.is_dir():
        return None
    try:
        return first_valid_trial(trials_dir)
    except ValueError:
        return None


def _cached_stable_summary(
    trials_dir: Path,
    *,
    inputs_digest: str,
    trials: int,
) -> dict[str, Any] | None:
    inputs_path = trials_dir / "generation_inputs.json"
    summary_path = trials_dir / "summary.json"
    if not inputs_path.is_file() or not summary_path.is_file():
        return None
    try:
        recorded = json.loads(inputs_path.read_text(encoding="utf-8"))
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(recorded, dict) or not isinstance(summary, dict):
        return None
    if recorded.get("inputs_digest") != inputs_digest:
        return None
    try:
        first_valid_trial(trials_dir)
    except ValueError:
        return None
    return summary


def generate_reuse_policy(
    *,
    primary_tbox: Path,
    supporting_tboxes: tuple[Path, ...],
    compiled_plan: dict[str, Any],
    domain_config_path: Path,
    output_path: Path,
    model: str = LOCKED_GENERATION_MODEL,
    top_entity: dict[str, Any] | None = None,
    repository_root: Path | None = None,
    format_retry_limit: int = 2,
    trials: int | None = None,
    parallelism: int | None = None,
) -> dict[str, Any]:
    """Generate reuse from T-Box + compiled plan.

    Default is one GPT-5 trial. Extra trials are optional evidence. Generation
    uses the first valid trial and does not wait for a 10/10 gate.
    """
    model = LOCKED_GENERATION_MODEL
    if format_retry_limit < 0:
        raise ValueError("format_retry_limit must be at least 0")
    default_trials, default_parallelism = reuse_trial_settings()
    trial_count = default_trials if trials is None else int(trials)
    worker_count = (
        default_parallelism if parallelism is None else int(parallelism)
    )
    if trial_count < 1:
        raise ValueError("trials must be at least 1")
    if worker_count < 1:
        raise ValueError("parallelism must be at least 1")
    tbox_content = primary_tbox.read_text(encoding="utf-8")
    tbox_sha256 = _sha256_text(tbox_content)
    class_inventory = class_inventory_from_ttl(tbox_content)
    if not class_inventory:
        raise ValueError(f"primary T-Box has no class inventory: {primary_tbox}")
    prompt_path = (
        OFFICIAL_PROMPT_PATH if OFFICIAL_PROMPT_PATH.is_file() else PROMPT_PATH
    )
    template = prompt_path.read_text(encoding="utf-8")
    supporting_payload = []
    for path in supporting_tboxes:
        content = path.read_text(encoding="utf-8")
        supporting_payload.append(
            {
                "path": _display_path(path, repository_root),
                "sha256": _sha256_text(content),
            }
        )
    domain_text = domain_config_path.read_text(encoding="utf-8")
    domain_payload = json.loads(domain_text)
    if not isinstance(domain_payload, dict):
        raise ValueError("domain config must be a JSON object")
    plan_projection = materialization_projection(
        compiled_plan, top_entity=top_entity
    )
    inputs_digest = _json_digest(
        {
            "model": model,
            "trials": trial_count,
            "format_retry_limit": format_retry_limit,
            "prompt_template_sha256": _sha256_text(template),
            "prompt_path": _display_path(prompt_path, repository_root),
            "tbox_sha256": tbox_sha256,
            "supporting_tboxes": supporting_payload,
            "cross_tbox_contexts": [
                {
                    "path": _display_path(domain_config_path, repository_root),
                    "sha256": _sha256_text(domain_text),
                }
            ],
            "plan_sha256": _json_digest(plan_projection),
            "class_inventory": class_inventory,
            "derivation_mode": STABLE_DERIVATION_MODE,
        }
    )
    cached = _cached_policy_matches(
        output_path,
        inputs_digest=inputs_digest,
        class_inventory=class_inventory,
    )
    if cached is not None:
        return {**cached, "_cached": True}

    from src.extraction_prompt_generation.compile.tbox_reusability_experiment import (
        run_experiment,
    )

    trials_dir = output_path.parent / "reuse_trials"
    plan_path = trials_dir / "materialization_plan.json"
    plan_sha256 = _json_digest(plan_projection)
    existing_trial = _usable_existing_trial(
        trials_dir,
        tbox_sha256=tbox_sha256,
        plan_sha256=plan_sha256,
    )
    summary = _cached_stable_summary(
        trials_dir,
        inputs_digest=inputs_digest,
        trials=trial_count,
    )
    experiment_cached = existing_trial is not None or summary is not None
    if existing_trial is None and summary is None:
        _write_json(plan_path, plan_projection)
        _write_json(
            trials_dir / "generation_inputs.json",
            {
                "schema_version": "reuse-generation-inputs.v1",
                "inputs_digest": inputs_digest,
                "model": model,
                "trials": trial_count,
                "tbox_sha256": tbox_sha256,
                "plan_sha256": plan_sha256,
            },
        )
        summary = run_experiment(
            tbox_path=primary_tbox,
            prompt_path=prompt_path,
            output_dir=trials_dir,
            model=model,
            trials=trial_count,
            parallelism=worker_count,
            materialization_plan_path=plan_path,
            format_retry_limit=format_retry_limit,
            supporting_tbox_paths=list(supporting_tboxes),
            cross_tbox_context_paths=[domain_config_path],
        )
    if summary is None:
        summary = {
            "requested_trials": trial_count,
            "valid_trials": 1,
            "all_trials_valid": True,
            "unanimous_class_count": len(class_inventory),
            "inventory_count": len(class_inventory),
            "disagreement_classes": [],
            "passed_10_of_10_gate": False,
        }
    representative = existing_trial or first_valid_trial(trials_dir)
    policy = assemble_stable_reuse_policy(
        summary=summary,
        representative_judgment=representative["parsed_response"],
        tbox_sha256=tbox_sha256,
        inputs_digest=inputs_digest,
        model=model,
        trials=trial_count,
        representative_trial=int(representative.get("trial") or 0) or None,
    )
    _write_json(output_path, policy)
    _write_json(
        output_path.with_name("reuse_judgment.json"),
        {
            "schema_version": "llm-class-reuse-judgment.v1",
            "cached": experiment_cached,
            "model": model,
            "tbox_sha256": tbox_sha256,
            "inputs_digest": inputs_digest,
            "class_inventory": class_inventory,
            "derivation_mode": STABLE_DERIVATION_MODE,
            "trials": trial_count,
            "representative_trial": representative.get("trial"),
            "summary_path": str(trials_dir / "summary.json"),
            "judgment": representative["parsed_response"],
        },
    )
    return {**policy, "_cached": experiment_cached}
