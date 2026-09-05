"""Domain-neutral LLM resolution of inherited procedures.

This module deliberately limits deterministic code to schema validation,
consensus aggregation, caching, and prompt rendering.  All decisions about
whether text expresses a dependency or a workflow modification belong to three
independent LLM votes supplied with the active T-Box contract.

A panel that cannot agree is retried up to RESOLUTION_ATTEMPT_BUDGET times.
If the budget is exhausted, resolution fail-opens to source-only extraction
instead of aborting the surrounding MAIN step.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import tempfile
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable

from src.agents.scripts_and_prompts_generation.level1_code_repair import (
    LLMJsonResult,
    invoke_json,
)


SCHEMA_VERSION = "procedure-inheritance-v2"
RESOLUTION_SCHEMA_VERSION = "procedure-inheritance-resolution.v3"
RECONCILIATION_SCHEMA_VERSION = "procedure-inheritance-reconciliation.v1"
RESOLUTION_ATTEMPT_BUDGET = 3
BRIEF_BEGIN = "---- PROCEDURE_INHERITANCE_BRIEF: BEGIN ----"
BRIEF_END = "---- PROCEDURE_INHERITANCE_BRIEF: END ----"
SCHEMA_REPAIR_TEMPERATURE = 0.3
_MODIFICATION_KINDS = {"insert", "delete", "replace", "refine"}
_MAX_COLLECTION_ITEMS = 1000

_ATOM_KEYS = {
    "atom_id",
    "order",
    "owner_ref",
    "operation",
    "source_evidence",
    "origin_ref",
    "occurrence_payload",
    "applied_modification_ids",
}
_OCCURRENCE_KEYS = {
    "occurrence_id",
    "material_identity",
    "amount",
    "role",
    "qualifiers",
    "mixture_group_id",
}
_QUALIFIER_KEYS = {"name", "value", "source_evidence"}
_DEPENDENCY_KEYS = {
    "dependency_id",
    "referencing_procedure_ref",
    "base_procedure_ref",
    "source_evidence",
}
_BASE_WORKFLOW_KEYS = {"base_procedure_ref", "atoms"}
_MODIFICATION_KEYS = {
    "modification_id",
    "kind",
    "target_atom_ids",
    "replacement_atoms",
    "source_evidence",
}
_VOTE_KEYS = {
    "schema_version",
    "target",
    "inheritance_present",
    "dependencies",
    "base_workflows",
    "modifications",
    "effective_workflow",
    "unresolved_references",
    "rationale",
}
_RECONCILIATION_KEYS = {
    "schema_version",
    "target_ref",
    "selected_candidate_index",
    "selected_status",
    "candidate_assessments",
    "rationale",
}
_CANDIDATE_ASSESSMENT_KEYS = {
    "candidate_index",
    "status",
    "semantic_gaps",
}


class ProcedureInheritanceResolutionError(RuntimeError):
    """Raised when the independent panel cannot resolve inheritance safely."""


def _require_string(value: Any, path: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        raise ValueError(f"{path} must be a non-empty string")
    return value


def _require_string_list(value: Any, path: str) -> list[str]:
    if not isinstance(value, list) or len(value) > _MAX_COLLECTION_ITEMS:
        raise ValueError(f"{path} must be a bounded array")
    for index, item in enumerate(value):
        _require_string(item, f"{path}[{index}]")
    return list(value)


def _validate_atom(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != _ATOM_KEYS:
        raise ValueError(f"{path} keys differ from the required atom schema")
    atom = dict(value)
    _require_string(atom["atom_id"], f"{path}.atom_id")
    if not isinstance(atom["order"], int) or isinstance(atom["order"], bool):
        raise ValueError(f"{path}.order must be an integer")
    if atom["order"] < 1:
        raise ValueError(f"{path}.order must be positive")
    for key in ("owner_ref", "operation", "source_evidence", "origin_ref"):
        _require_string(atom[key], f"{path}.{key}")
    occurrence = atom["occurrence_payload"]
    if not isinstance(occurrence, dict) or set(occurrence) != _OCCURRENCE_KEYS:
        raise ValueError(
            f"{path}.occurrence_payload keys differ from the required schema"
        )
    if occurrence["occurrence_id"] != atom["atom_id"]:
        raise ValueError(
            f"{path}.occurrence_payload.occurrence_id must equal atom_id"
        )
    for key in ("material_identity", "amount", "role", "mixture_group_id"):
        field = occurrence[key]
        if field is not None:
            _require_string(field, f"{path}.occurrence_payload.{key}")
    qualifiers = occurrence["qualifiers"]
    if not isinstance(qualifiers, list) or len(qualifiers) > _MAX_COLLECTION_ITEMS:
        raise ValueError(f"{path}.occurrence_payload.qualifiers must be a bounded array")
    for index, qualifier in enumerate(qualifiers):
        qualifier_path = f"{path}.occurrence_payload.qualifiers[{index}]"
        if not isinstance(qualifier, dict) or set(qualifier) != _QUALIFIER_KEYS:
            raise ValueError(f"{qualifier_path} keys differ from required schema")
        for key in _QUALIFIER_KEYS:
            _require_string(qualifier[key], f"{qualifier_path}.{key}")
    occurrence["qualifiers"] = sorted(
        (dict(qualifier) for qualifier in qualifiers),
        key=lambda item: (item["name"], item["value"]),
    )
    if occurrence["amount"] is not None and occurrence["material_identity"] is None:
        raise ValueError(
            f"{path}.occurrence_payload.amount requires material_identity; "
            "process duration and other operation parameters "
            "belong in qualifiers"
        )
    _require_string_list(
        atom["applied_modification_ids"],
        f"{path}.applied_modification_ids",
    )
    return atom


def _validate_atom_list(value: Any, path: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) > _MAX_COLLECTION_ITEMS:
        raise ValueError(f"{path} must be a bounded array")
    atoms = [_validate_atom(item, f"{path}[{index}]") for index, item in enumerate(value)]
    ids = [atom["atom_id"] for atom in atoms]
    orders = [atom["order"] for atom in atoms]
    if len(ids) != len(set(ids)):
        raise ValueError(f"{path} atom_id values must be unique")
    if orders != list(range(1, len(atoms) + 1)):
        raise ValueError(f"{path} order values must be contiguous from 1")
    return atoms


def validate_inheritance_vote(
    data: dict[str, Any],
    *,
    target_procedure_ref: str,
) -> dict[str, Any]:
    """Validate only the fixed transport schema, never semantic content."""
    if not isinstance(data, dict) or set(data) != _VOTE_KEYS:
        raise ValueError("inheritance vote keys differ from the required schema")
    if data["schema_version"] != SCHEMA_VERSION:
        raise ValueError("inheritance vote schema_version is invalid")
    target = data["target"]
    if not isinstance(target, dict) or set(target) != {
        "procedure_ref",
        "source_evidence",
    }:
        raise ValueError("target keys differ from the required schema")
    if target.get("procedure_ref") != target_procedure_ref:
        raise ValueError("target.procedure_ref changed")
    _require_string(target.get("source_evidence"), "target.source_evidence", allow_empty=True)
    if not isinstance(data["inheritance_present"], bool):
        raise ValueError("inheritance_present must be boolean")

    dependencies = data["dependencies"]
    if not isinstance(dependencies, list) or len(dependencies) > _MAX_COLLECTION_ITEMS:
        raise ValueError("dependencies must be a bounded array")
    normalized_dependencies: list[dict[str, Any]] = []
    for index, dependency in enumerate(dependencies):
        path = f"dependencies[{index}]"
        if not isinstance(dependency, dict) or set(dependency) != _DEPENDENCY_KEYS:
            raise ValueError(f"{path} keys differ from the required schema")
        for key in _DEPENDENCY_KEYS:
            _require_string(dependency[key], f"{path}.{key}")
        normalized_dependencies.append(dict(dependency))
    dependency_base_refs = {
        dependency["base_procedure_ref"]
        for dependency in normalized_dependencies
    }
    allowed_referencing_refs = {target_procedure_ref, *dependency_base_refs}
    for index, dependency in enumerate(normalized_dependencies):
        if dependency["referencing_procedure_ref"] not in allowed_referencing_refs:
            raise ValueError(
                f"dependencies[{index}].referencing_procedure_ref is outside the "
                "declared dependency closure"
            )
        if dependency["base_procedure_ref"] == target_procedure_ref:
            raise ValueError(
                f"dependencies[{index}].base_procedure_ref must identify a distinct "
                "referenced procedure"
            )

    base_workflows = data["base_workflows"]
    if not isinstance(base_workflows, list) or len(base_workflows) > _MAX_COLLECTION_ITEMS:
        raise ValueError("base_workflows must be a bounded array")
    normalized_base_workflows: list[dict[str, Any]] = []
    for index, workflow in enumerate(base_workflows):
        path = f"base_workflows[{index}]"
        if not isinstance(workflow, dict) or set(workflow) != _BASE_WORKFLOW_KEYS:
            raise ValueError(f"{path} keys differ from the required schema")
        _require_string(workflow["base_procedure_ref"], f"{path}.base_procedure_ref")
        normalized_base_workflows.append(
            {
                "base_procedure_ref": workflow["base_procedure_ref"],
                "atoms": _validate_atom_list(workflow["atoms"], f"{path}.atoms"),
            }
        )

    modifications = data["modifications"]
    if not isinstance(modifications, list) or len(modifications) > _MAX_COLLECTION_ITEMS:
        raise ValueError("modifications must be a bounded array")
    normalized_modifications: list[dict[str, Any]] = []
    for index, modification in enumerate(modifications):
        path = f"modifications[{index}]"
        if not isinstance(modification, dict) or set(modification) != _MODIFICATION_KEYS:
            raise ValueError(f"{path} keys differ from the required schema")
        _require_string(modification["modification_id"], f"{path}.modification_id")
        if modification["kind"] not in _MODIFICATION_KINDS:
            raise ValueError(f"{path}.kind is invalid")
        targets = _require_string_list(
            modification["target_atom_ids"],
            f"{path}.target_atom_ids",
        )
        replacements = _validate_atom_list(
            modification["replacement_atoms"],
            f"{path}.replacement_atoms",
        )
        kind = modification["kind"]
        if kind == "insert" and (targets or not replacements):
            raise ValueError(
                f"{path} insert requires no target_atom_ids and at least one replacement atom"
            )
        if kind == "delete" and (not targets or replacements):
            raise ValueError(
                f"{path} delete requires target_atom_ids and no replacement atoms"
            )
        if kind in {"replace", "refine"} and (not targets or not replacements):
            raise ValueError(
                f"{path} {kind} requires target_atom_ids and replacement atoms"
            )
        _require_string(modification["source_evidence"], f"{path}.source_evidence")
        normalized_modifications.append(
            {
                **dict(modification),
                "target_atom_ids": targets,
                "replacement_atoms": replacements,
            }
        )

    effective_workflow = _validate_atom_list(
        data["effective_workflow"],
        "effective_workflow",
    )
    if any(
        atom["owner_ref"] != target_procedure_ref
        for atom in effective_workflow
    ):
        raise ValueError(
            "effective_workflow atoms must all be owned by target_procedure_ref"
        )
    unresolved = _require_string_list(
        data["unresolved_references"],
        "unresolved_references",
    )
    _require_string(data["rationale"], "rationale")

    if not data["inheritance_present"] and (
        normalized_dependencies
        or normalized_base_workflows
        or normalized_modifications
        or effective_workflow
        or unresolved
    ):
        raise ValueError("no-inheritance vote must contain empty inheritance arrays")
    if data["inheritance_present"] and not normalized_dependencies:
        raise ValueError(
            "inheritance_present=true requires at least one explicit "
            "procedure-to-procedure dependency; global/shared context is not inheritance"
        )

    return {
        "schema_version": SCHEMA_VERSION,
        "target": dict(target),
        "inheritance_present": data["inheritance_present"],
        "dependencies": normalized_dependencies,
        "base_workflows": normalized_base_workflows,
        "modifications": normalized_modifications,
        "effective_workflow": effective_workflow,
        "unresolved_references": unresolved,
        "rationale": data["rationale"],
    }


def build_inheritance_vote_prompt(
    *,
    source_text: str,
    target_procedure_ref: str,
    target_procedure_label: str,
    tbox_contract: str | dict[str, Any],
    target_identity_dossier: dict[str, Any] | None = None,
    top_entity_manifest: Any = None,
) -> str:
    """Create the domain-neutral semantic task given to each independent voter."""
    contract_text = (
        json.dumps(tbox_contract, ensure_ascii=False, sort_keys=True)
        if isinstance(tbox_contract, dict)
        else str(tbox_contract)
    )
    return (
        "You are one independent procedure-inheritance resolver. You cannot see any "
        "other vote. Use semantic reasoning over the complete source and the active "
        "T-Box contract. Do not use lexical heuristics, keyword rules, regular "
        "expressions, or string similarity as evidence.\n\n"
        "Determine whether the exact target procedure depends on one or more referenced "
        "procedures. Recursively resolve all dependency layers. Reconstruct every base "
        "workflow, then apply only source-supported insert, delete, replace, and refine "
        "modifications in source order. Preserve target ownership. Every operation "
        "occurrence must carry one occurrence_payload that keeps its source-supported "
        "identity, amount, role, and qualifiers together on that same atom. "
        "An explicit multi-part group with multiple introduced components MUST produce one distinct "
        "introduction atom per component/occurrence; never encode several components in an array or "
        "treat a group annotation as a substitute for separate atoms. Return one "
        "completely flattened effective_workflow. A reference "
        "or modification that cannot be resolved from the supplied evidence must be listed "
        "in unresolved_references; never guess it.\n\n"
        "STRICT ACTIVATION GATE: inheritance_present=true if and only if the target "
        "procedure explicitly instructs reuse of a distinct procedure's workflow "
        "(including semantically equivalent wording such as the same procedure, following "
        "that method, or prepared analogously). Every "
        "true verdict therefore requires at least one procedure-to-procedure dependency "
        "whose base_procedure_ref identifies that distinct procedure. Document-, section-, "
        "family-, or procedure-wide shared context (atmosphere, environment, equipment, "
        "general conditions, defaults, provenance, or any other globally scoped fact) is "
        "NEVER procedure inheritance and must never activate this resolver. A referenced "
        "input, intermediate, resource, or other entity may have its own creation history; "
        "merely using or citing that entity, resolving its identity or provenance, or "
        "having access to its creation workflow does not instruct the target to reuse that "
        "workflow and therefore is not procedure inheritance. A fully "
        "specified standalone target has inheritance_present=false even when shared context "
        "applies to it. In that false branch return dependencies, base_workflows, "
        "modifications, effective_workflow, and unresolved_references all empty; PRE will "
        "derive the workflow directly from the source and active T-Box.\n\n"
        "Use these domain-neutral canonicalization rules so independent semantic votes "
        "serialize the same resolved workflow. Number dependencies D001, D002, ...; "
        "modifications M001, M002, ...; each base workflow's atoms B001, B002, ...; "
        "replacement atoms R001, R002, ...; and flattened effective atoms W001, W002, ... "
        "in source order. In base_workflows, owner_ref and origin_ref are that exact base "
        "procedure ref. In effective_workflow, EVERY owner_ref is the exact target "
        "procedure ref; origin_ref remains the base ref for inherited atoms and the target "
        "ref for explicit target modifications. An insert has an empty target_atom_ids "
        "array and one or more replacement_atoms; delete has targets and no replacements; "
        "replace/refine have both. If an inserted participant has no explicit finer-grained "
        "anchor, place it after the contiguous source-co-present initial introduction "
        "occurrences and before the next processing operation rather than inventing an "
        "anchor. The occurrence amount field is only an occurrence amount and "
        "requires material_identity; duration, rate, and "
        "other operation parameters belong in qualifiers. Sort qualifiers by semantic "
        "property name and then value. When the source explicitly names a multi-part "
        "group, its already-separate component atoms must share one non-null canonical "
        "mixture_group_id such as MIX001. Do not assign that group to unrelated "
        "co-present participants or later inserted participants unless the source explicitly "
        "includes them as members of that group.\n\n"
        "If the target has no inheritance semantics, set inheritance_present=false and "
        "return all five inheritance arrays empty. Otherwise every atom_id, dependency_id, "
        "and modification_id must be stable and source-grounded. Atom order must be "
        "contiguous from 1 within every independent atom array; each modification's "
        "replacement_atoms array restarts its local order at 1. Return JSON only, with "
        "exactly this schema:\n"
        "{\n"
        f'  "schema_version": "{SCHEMA_VERSION}",\n'
        '  "target": {"procedure_ref": "", "source_evidence": ""},\n'
        '  "inheritance_present": true,\n'
        '  "dependencies": [{"dependency_id": "", "referencing_procedure_ref": "", '
        '"base_procedure_ref": "", "source_evidence": ""}],\n'
        '  "base_workflows": [{"base_procedure_ref": "", "atoms": [ATOM]}],\n'
        '  "modifications": [{"modification_id": "", '
        '"kind": "insert|delete|replace|refine", "target_atom_ids": [""], '
        '"replacement_atoms": [ATOM], "source_evidence": ""}],\n'
        '  "effective_workflow": [ATOM],\n'
        '  "unresolved_references": [""],\n'
        '  "rationale": ""\n'
        "}\n"
        "ATOM has exactly: atom_id (string), order (positive integer), owner_ref "
        "(string), operation (string), source_evidence (string), origin_ref (string), "
        "occurrence_payload (object), and applied_modification_ids (array of strings). "
        "occurrence_payload has exactly occurrence_id (must equal atom_id), "
        "material_identity (string or null), amount (exact source-supported lexical value "
        "or null), role (string or null), qualifiers (array of objects with exactly name, "
        "value, source_evidence), and mixture_group_id (shared string or null). "
        "mixture_group_id only links already-separate atoms and cannot hold components.\n\n"
        f"EXACT TARGET PROCEDURE REF: {target_procedure_ref}\n"
        f"TARGET LABEL (identity aid only): {target_procedure_label}\n\n"
        "AUTHORITATIVE TARGET IDENTITY DOSSIER:\n"
        f"{json.dumps(target_identity_dossier or {}, ensure_ascii=False, sort_keys=True)}\n\n"
        "AUTHORITATIVE TOP-ENTITY MANIFEST:\n"
        f"{json.dumps(top_entity_manifest if top_entity_manifest is not None else [], ensure_ascii=False, sort_keys=True)}\n\n"
        f"ACTIVE T-BOX CONTRACT:\n<<<TBOX\n{contract_text}\nTBOX\n>>>\n\n"
        f"COMPLETE SOURCE:\n<<<SOURCE\n{source_text}\nSOURCE\n>>>\n"
    )


def _invoke_vote(
    *,
    invoke: Callable[..., LLMJsonResult],
    model: str,
    prompt: str,
    target_procedure_ref: str,
) -> tuple[dict[str, Any], list[LLMJsonResult]]:
    calls: list[LLMJsonResult] = []
    current_prompt = prompt
    errors: list[str] = []
    for attempt in range(3):
        result = invoke(
            model,
            current_prompt,
            max_attempts=3,
            temperature=SCHEMA_REPAIR_TEMPERATURE if attempt else 0.0,
        )
        calls.append(result)
        try:
            candidate = copy.deepcopy(result.data)
            # replacement_atoms is a self-contained local sequence. Preserve the
            # model's array order and normalize only its transport-level numbering.
            modifications = candidate.get("modifications")
            if isinstance(modifications, list):
                for modification in modifications:
                    if not isinstance(modification, dict):
                        continue
                    replacements = modification.get("replacement_atoms")
                    if not isinstance(replacements, list):
                        continue
                    for index, atom in enumerate(replacements, start=1):
                        if isinstance(atom, dict):
                            atom["order"] = index
            # ``inheritance_present=false`` is the semantic verdict. The schema
            # defines all inheritance collections as empty in that branch, so
            # discard model-populated schema examples mechanically rather than
            # reinterpreting any procedure content.
            if candidate.get("inheritance_present") is False:
                for field in (
                    "dependencies",
                    "base_workflows",
                    "modifications",
                    "effective_workflow",
                    "unresolved_references",
                ):
                    candidate[field] = []
            return (
                validate_inheritance_vote(
                    candidate,
                    target_procedure_ref=target_procedure_ref,
                ),
                calls,
            )
        except (TypeError, ValueError) as exc:
            errors.append(str(exc))
            current_prompt = (
                prompt
                + "\n\nYour prior JSON response was:\n"
                + json.dumps(result.data, ensure_ascii=False, indent=2)
                + "\n\nThat response failed mechanical schema validation: "
                + str(exc)
                + "\nRepair the transport/schema defect. If the failure says a true "
                "inheritance verdict lacks an explicit procedure-to-procedure dependency, "
                "do not invent one: set inheritance_present=false and return every "
                "inheritance array empty. Return the complete corrected JSON object."
            )
    raise ProcedureInheritanceResolutionError(
        "inheritance vote schema remained invalid: " + "; ".join(errors)
    )


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _dependency_signature(value: dict[str, Any]) -> tuple[Any, ...]:
    """Project only structured identity fields, never compare evidence wording."""
    return (
        value["dependency_id"],
        value["referencing_procedure_ref"],
        value["base_procedure_ref"],
    )


def _atom_signature(value: dict[str, Any]) -> tuple[Any, ...]:
    """Compare structured occurrence assertions while ignoring evidence prose."""
    occurrence = value["occurrence_payload"]
    return (
        value["atom_id"],
        value["order"],
        value["owner_ref"],
        value["operation"],
        value["origin_ref"],
        occurrence["occurrence_id"],
        occurrence["material_identity"],
        occurrence["amount"],
        occurrence["role"],
        tuple(
            (qualifier["name"], qualifier["value"])
            for qualifier in occurrence["qualifiers"]
        ),
        occurrence["mixture_group_id"],
        tuple(value["applied_modification_ids"]),
    )


def _base_workflow_signature(value: dict[str, Any]) -> tuple[Any, ...]:
    return (
        value["base_procedure_ref"],
        tuple(_atom_signature(atom) for atom in value["atoms"]),
    )


def _modification_signature(value: dict[str, Any]) -> tuple[Any, ...]:
    """Project the structured edit identity without lexical evidence matching."""
    return (
        value["modification_id"],
        value["kind"],
        tuple(value["target_atom_ids"]),
        tuple(_atom_signature(atom) for atom in value["replacement_atoms"]),
    )


def aggregate_inheritance_votes(
    votes: list[dict[str, Any]],
    *,
    target_procedure_ref: str,
    target_procedure_label: str = "",
) -> dict[str, Any]:
    """Mechanically require exact three-vote semantic agreement."""
    if len(votes) != 3:
        raise ValueError("procedure inheritance requires exactly three votes")
    normalized = [
        validate_inheritance_vote(vote, target_procedure_ref=target_procedure_ref)
        for vote in votes
    ]
    presence = {vote["inheritance_present"] for vote in normalized}
    reasons: list[str] = []
    if len(presence) != 1:
        reasons.append("panel disagreed whether inheritance is present")
    elif presence == {False}:
        return {
            "schema_version": RESOLUTION_SCHEMA_VERSION,
            "status": "no_inheritance",
            "target": {
                "procedure_ref": target_procedure_ref,
                "procedure_label": target_procedure_label,
            },
            "dependencies": [],
            "base_workflows": [],
            "modifications": [],
            "effective_workflow": [],
            "unresolved_reasons": [],
            "vote_fingerprints": [
                hashlib.sha256(_canonical(vote).encode("utf-8")).hexdigest()
                for vote in normalized
            ],
        }

    consensus_fields: dict[str, Any] = {}
    identity_projections: dict[str, Callable[[dict[str, Any]], tuple[Any, ...]]] = {
        "dependencies": _dependency_signature,
        "modifications": _modification_signature,
    }
    for field in ("dependencies", "modifications"):
        signature_lists = {
            tuple(identity_projections[field](item) for item in vote[field])
            for vote in normalized
        }
        if len(signature_lists) != 1:
            reasons.append(f"panel disagreed on {field}")
        else:
            consensus_fields[field] = normalized[0][field]
    structured_list_signatures: dict[str, Callable[[dict[str, Any]], tuple[Any, ...]]] = {
        "base_workflows": _base_workflow_signature,
        "effective_workflow": _atom_signature,
    }
    for field, signature in structured_list_signatures.items():
        values = {
            tuple(signature(item) for item in vote[field])
            for vote in normalized
        }
        if len(values) != 1:
            reasons.append(f"panel disagreed on {field}")
        else:
            consensus_fields[field] = normalized[0][field]
    unresolved_values = {
        tuple(vote["unresolved_references"])
        for vote in normalized
    }
    if len(unresolved_values) != 1:
        reasons.append("panel disagreed on unresolved_references")
    else:
        consensus_fields["unresolved_references"] = normalized[0][
            "unresolved_references"
        ]
    if consensus_fields.get("unresolved_references"):
        reasons.extend(
            f"unresolved reference: {item}"
            for item in consensus_fields["unresolved_references"]
        )
    if reasons:
        status = "unresolved"
        dependencies: list[dict[str, Any]] = []
        modifications: list[dict[str, Any]] = []
        for field, destination, signature in (
            ("dependencies", dependencies, _dependency_signature),
            ("modifications", modifications, _modification_signature),
        ):
            first = {signature(item): item for item in normalized[0][field]}
            common = set(first)
            for vote in normalized[1:]:
                common &= {signature(item) for item in vote[field]}
            destination.extend(first[key] for key in sorted(common, key=repr))
        return {
            "schema_version": RESOLUTION_SCHEMA_VERSION,
            "status": status,
            "target": {
                "procedure_ref": target_procedure_ref,
                "procedure_label": target_procedure_label,
            },
            "dependencies": dependencies,
            "base_workflows": [],
            "modifications": modifications,
            "effective_workflow": [],
            "unresolved_reasons": reasons,
            "vote_fingerprints": [
                hashlib.sha256(_canonical(vote).encode("utf-8")).hexdigest()
                for vote in normalized
            ],
        }

    return {
        "schema_version": RESOLUTION_SCHEMA_VERSION,
        "status": "resolved",
        "target": {
            "procedure_ref": target_procedure_ref,
            "procedure_label": target_procedure_label,
        },
        "dependencies": consensus_fields["dependencies"],
        "base_workflows": consensus_fields["base_workflows"],
        "modifications": consensus_fields["modifications"],
        "effective_workflow": consensus_fields["effective_workflow"],
        "unresolved_reasons": [],
        "vote_fingerprints": [
            hashlib.sha256(_canonical(vote).encode("utf-8")).hexdigest()
            for vote in normalized
        ],
    }


def _cache_key(
    *,
    source_text: str,
    target_procedure_ref: str,
    target_procedure_label: str,
    tbox_contract: str | dict[str, Any],
    target_identity_dossier: dict[str, Any] | None,
    top_entity_manifest: Any,
    models: list[str],
) -> str:
    payload = {
        "schema_version": RESOLUTION_SCHEMA_VERSION,
        "source_text": source_text,
        "target_procedure_ref": target_procedure_ref,
        "target_procedure_label": target_procedure_label,
        "tbox_contract": tbox_contract,
        "target_identity_dossier": target_identity_dossier or {},
        "top_entity_manifest": (
            top_entity_manifest if top_entity_manifest is not None else []
        ),
        "models": models,
    }
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


def _load_cache(path: Path, cache_key: str) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    if (
        isinstance(payload, dict)
        and payload.get("cache_key") == cache_key
        and isinstance(payload.get("resolution"), dict)
    ):
        return payload["resolution"]
    return None


def _write_cache(path: Path, cache_key: str, resolution: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        dir=str(path.parent),
        prefix=".inheritance.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(
                {"cache_key": cache_key, "resolution": resolution},
                stream,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            stream.write("\n")
        os.replace(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _vote_structural_signature(vote: dict[str, Any]) -> tuple[Any, ...]:
    """Project fixed structured assertions, excluding evidence prose."""
    return (
        vote["inheritance_present"],
        tuple(_dependency_signature(item) for item in vote["dependencies"]),
        tuple(_base_workflow_signature(item) for item in vote["base_workflows"]),
        tuple(_modification_signature(item) for item in vote["modifications"]),
        tuple(_atom_signature(item) for item in vote["effective_workflow"]),
        tuple(vote["unresolved_references"]),
    )


def _distinct_reconciliation_candidates(
    votes: list[dict[str, Any]],
) -> list[tuple[int, dict[str, Any]]]:
    """Collapse structurally identical votes without interpreting their content."""
    seen: set[tuple[Any, ...]] = set()
    candidates: list[tuple[int, dict[str, Any]]] = []
    for index, vote in enumerate(votes, start=1):
        signature = _vote_structural_signature(vote)
        if signature in seen:
            continue
        seen.add(signature)
        candidates.append((index, vote))
    return candidates


def _build_reconciliation_prompt(
    *,
    source_text: str,
    target_procedure_ref: str,
    target_procedure_label: str,
    tbox_contract: str | dict[str, Any],
    target_identity_dossier: dict[str, Any] | None,
    top_entity_manifest: Any,
    candidates: list[tuple[int, dict[str, Any]]],
) -> str:
    contract_text = (
        json.dumps(tbox_contract, ensure_ascii=False, sort_keys=True)
        if isinstance(tbox_contract, dict)
        else str(tbox_contract)
    )
    candidate_payload = [
        {"candidate_index": index, "candidate": vote}
        for index, vote in candidates
    ]
    return (
        "You are one independent semantic reconciler for a procedure-inheritance "
        "panel. You cannot see other reconciliation votes. The initial independent "
        "resolvers produced structurally different candidates. Use semantic reasoning "
        "over the complete source and active T-Box to decide which candidates are "
        "complete and correct. Do not use keywords, regular expressions, "
        "substring matching, string similarity, majority voting, or evidence-wording "
        "similarity. Audit every candidate atom, dependency, modification, occurrence "
        "payload, qualifier, origin, owner, and order. Reject omitted source-supported "
        "facts, invented facts, sibling contamination, merged grouped components, "
        "misattached amounts or identities, and untraceable atoms. Select a candidate "
        "only if it uniquely preserves every inherited base atom, applies every explicit "
        "modification, keeps explicit group members as separate introduction occurrences, "
        "owns every effective atom by the exact target, and grounds every assertion. "
        "Apply the strict activation gate before inspecting workflow quality: a complete "
        "inheritance candidate must contain at least one source-grounded dependency from "
        "the target (or dependency closure) to a distinct referenced procedure. Shared "
        "global/document/section/procedure-family context, including atmosphere or "
        "environment, is never procedure inheritance. If the target has no explicit "
        "procedure-to-procedure reference, every inheritance_present=true candidate is "
        "incomplete; the correct resolver result is no-inheritance with an empty workflow, "
        "not selection of a standalone workflow candidate. "
        "Apply the same representation canonicalization as the initial task: when an "
        "inserted participant has no explicit relative anchor inside a contiguous initial "
        "introduction block, its canonical position is after all inherited source-co-present "
        "initial introduction occurrences and before the next processing operation. Do not infer "
        "that a generic 'include it in the existing process' phrasing makes the inserted "
        "participant a member of an explicitly named group, co-introduced simultaneously, or "
        "assigned that mixture_group_id unless the source actually states this. Treat "
        "deviations from this canonical position as representational differences, not "
        "permission to invent a different operation chronology. "
        "If no candidate is semantically complete, select null and return unresolved. "
        "If one or more candidates are complete, return resolved and select the lowest "
        "candidate_index among the candidates you marked complete. This index rule is "
        "only a canonical transport tie-break for semantically equivalent complete "
        "representations; do not use it when judging completeness.\n\n"
        "Return JSON only with exactly this schema:\n"
        "{\n"
        f'  "schema_version": "{RECONCILIATION_SCHEMA_VERSION}",\n'
        '  "target_ref": "",\n'
        '  "selected_candidate_index": 1,\n'
        '  "selected_status": "resolved|unresolved",\n'
        '  "candidate_assessments": [\n'
        '    {"candidate_index": 1, "status": "complete|incomplete", '
        '"semantic_gaps": []}\n'
        "  ],\n"
        '  "rationale": ""\n'
        "}\n"
        "For unresolved, selected_candidate_index must be null. For resolved, select the "
        "lowest index you marked complete. Return exactly one "
        "assessment for each supplied candidate index. A complete candidate has an empty "
        "semantic_gaps array; an incomplete candidate has one or more concrete non-empty "
        "semantic gap descriptions.\n\n"
        f"EXACT TARGET REF: {target_procedure_ref}\n"
        f"TARGET LABEL: {target_procedure_label}\n\n"
        "AUTHORITATIVE TARGET IDENTITY DOSSIER:\n"
        f"{json.dumps(target_identity_dossier or {}, ensure_ascii=False, sort_keys=True)}\n\n"
        "AUTHORITATIVE TOP-ENTITY MANIFEST:\n"
        f"{json.dumps(top_entity_manifest if top_entity_manifest is not None else [], ensure_ascii=False, sort_keys=True)}\n\n"
        f"ACTIVE T-BOX CONTRACT:\n<<<TBOX\n{contract_text}\nTBOX\n>>>\n\n"
        f"COMPLETE SOURCE:\n<<<SOURCE\n{source_text}\nSOURCE\n>>>\n\n"
        "STRUCTURALLY DISTINCT CANDIDATES:\n<<<CANDIDATES\n"
        f"{json.dumps(candidate_payload, ensure_ascii=False, indent=2)}\n"
        "CANDIDATES\n>>>\n"
    )


def _validate_reconciliation_vote(
    data: dict[str, Any],
    *,
    target_procedure_ref: str,
    candidate_indices: set[int],
) -> dict[str, Any]:
    """Validate reconciliation transport and coverage, never semantic content."""
    if not isinstance(data, dict) or set(data) != _RECONCILIATION_KEYS:
        raise ValueError("reconciliation vote keys differ from required schema")
    if data["schema_version"] != RECONCILIATION_SCHEMA_VERSION:
        raise ValueError("reconciliation schema_version is invalid")
    if data["target_ref"] != target_procedure_ref:
        raise ValueError("reconciliation changed target_ref")
    if data["selected_status"] not in {"resolved", "unresolved"}:
        raise ValueError("reconciliation selected_status is invalid")
    selected = data["selected_candidate_index"]
    if data["selected_status"] == "resolved":
        if not isinstance(selected, int) or isinstance(selected, bool):
            raise ValueError("resolved reconciliation requires an integer selection")
        if selected not in candidate_indices:
            raise ValueError("reconciliation selected an unknown candidate")
    elif selected is not None:
        raise ValueError("unresolved reconciliation requires a null selection")
    assessments = data["candidate_assessments"]
    if not isinstance(assessments, list) or len(assessments) > _MAX_COLLECTION_ITEMS:
        raise ValueError("candidate_assessments must be a bounded array")
    indexed: dict[int, dict[str, Any]] = {}
    for index, assessment in enumerate(assessments):
        if (
            not isinstance(assessment, dict)
            or set(assessment) != _CANDIDATE_ASSESSMENT_KEYS
        ):
            raise ValueError(
                f"candidate_assessments[{index}] keys differ from required schema"
            )
        candidate_index = assessment["candidate_index"]
        if (
            not isinstance(candidate_index, int)
            or isinstance(candidate_index, bool)
            or candidate_index not in candidate_indices
            or candidate_index in indexed
        ):
            raise ValueError(
                f"candidate_assessments[{index}] has invalid candidate_index"
            )
        if assessment["status"] not in {"complete", "incomplete"}:
            raise ValueError(f"candidate_assessments[{index}].status is invalid")
        gaps = _require_string_list(
            assessment["semantic_gaps"],
            f"candidate_assessments[{index}].semantic_gaps",
        )
        if assessment["status"] == "complete" and gaps:
            raise ValueError(
                f"candidate_assessments[{index}] complete candidate must have no gaps"
            )
        if assessment["status"] == "incomplete" and not gaps:
            raise ValueError(
                f"candidate_assessments[{index}] incomplete candidate requires gaps"
            )
        indexed[candidate_index] = {
            **dict(assessment),
            "semantic_gaps": gaps,
        }
    if set(indexed) != candidate_indices:
        raise ValueError("candidate_assessments does not cover every candidate")
    if data["selected_status"] == "resolved":
        complete = {
            index
            for index, assessment in indexed.items()
            if assessment["status"] == "complete"
        }
        if not complete or selected != min(complete):
            raise ValueError(
                "resolved reconciliation must select the lowest complete candidate index"
            )
    _require_string(data["rationale"], "reconciliation.rationale")
    return {
        **dict(data),
        "candidate_assessments": [
            indexed[index] for index in sorted(indexed)
        ],
    }


def _invoke_reconciliation_vote(
    *,
    invoke: Callable[..., LLMJsonResult],
    model: str,
    prompt: str,
    target_procedure_ref: str,
    candidate_indices: set[int],
) -> dict[str, Any]:
    current_prompt = prompt
    errors: list[str] = []
    for attempt in range(3):
        result = invoke(
            model,
            current_prompt,
            max_attempts=3,
            temperature=SCHEMA_REPAIR_TEMPERATURE if attempt else 0.0,
        )
        try:
            return _validate_reconciliation_vote(
                result.data,
                target_procedure_ref=target_procedure_ref,
                candidate_indices=candidate_indices,
            )
        except (TypeError, ValueError) as exc:
            errors.append(str(exc))
            current_prompt = (
                prompt
                + "\n\nYour prior response failed mechanical schema validation: "
                + str(exc)
                + "\nReturn corrected JSON for the same semantic reconciliation."
            )
    raise ProcedureInheritanceResolutionError(
        "reconciliation vote schema remained invalid: " + "; ".join(errors)
    )


def _reconcile_disagreed_votes(
    *,
    votes: list[dict[str, Any]],
    source_text: str,
    target_procedure_ref: str,
    target_procedure_label: str,
    tbox_contract: str | dict[str, Any],
    target_identity_dossier: dict[str, Any] | None,
    top_entity_manifest: Any,
    models: list[str],
    invoke: Callable[..., LLMJsonResult],
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    candidates = _distinct_reconciliation_candidates(votes)
    prompt = _build_reconciliation_prompt(
        source_text=source_text,
        target_procedure_ref=target_procedure_ref,
        target_procedure_label=target_procedure_label,
        tbox_contract=tbox_contract,
        target_identity_dossier=target_identity_dossier,
        top_entity_manifest=top_entity_manifest,
        candidates=candidates,
    )
    candidate_indices = {index for index, _ in candidates}
    ordered: list[tuple[int, dict[str, Any]]] = []
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {
            pool.submit(
                _invoke_reconciliation_vote,
                invoke=invoke,
                model=vote_model,
                prompt=prompt,
                target_procedure_ref=target_procedure_ref,
                candidate_indices=candidate_indices,
            ): index
            for index, vote_model in enumerate(models)
        }
        for future in as_completed(futures):
            ordered.append((futures[future], future.result()))
    reconciliation_votes = [vote for _, vote in sorted(ordered)]
    selection_counts = Counter(
        (vote["selected_status"], vote["selected_candidate_index"])
        for vote in reconciliation_votes
    )
    (status, selected_index), count = selection_counts.most_common(1)[0]
    # The fixed three-member semantic panel decides by an actual majority.
    # Deterministic code never interprets candidate content or evidence prose.
    if count < 2:
        return None, reconciliation_votes
    if status != "resolved" or selected_index is None:
        return None, reconciliation_votes
    selected_vote = next(
        vote for index, vote in candidates if index == selected_index
    )
    return selected_vote, reconciliation_votes


def _empty_fail_open_resolution(
    *,
    target_procedure_ref: str,
    target_procedure_label: str,
    reasons: list[str],
    attempts: int,
    last_resolution: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Keep source-only extraction after the retry budget is exhausted."""
    if last_resolution is not None:
        resolution = dict(last_resolution)
    else:
        resolution = {
            "schema_version": RESOLUTION_SCHEMA_VERSION,
            "status": "unresolved",
            "target": {
                "procedure_ref": target_procedure_ref,
                "procedure_label": target_procedure_label,
            },
            "dependencies": [],
            "base_workflows": [],
            "modifications": [],
            "effective_workflow": [],
            "unresolved_reasons": reasons,
            "vote_fingerprints": [],
            "panel_votes": [],
        }
    resolution["status"] = "unresolved"
    resolution["fail_open"] = True
    resolution["resolution_attempts"] = attempts
    if reasons:
        resolution["unresolved_reasons"] = reasons
    return resolution


def _resolve_procedure_inheritance_attempt(
    *,
    source_text: str,
    target_procedure_ref: str,
    target_procedure_label: str,
    tbox_contract: str | dict[str, Any],
    models: list[str],
    target_identity_dossier: dict[str, Any] | None,
    top_entity_manifest: Any,
    invoke: Callable[..., LLMJsonResult],
) -> dict[str, Any]:
    prompt = build_inheritance_vote_prompt(
        source_text=source_text,
        target_procedure_ref=target_procedure_ref,
        target_procedure_label=target_procedure_label,
        tbox_contract=tbox_contract,
        target_identity_dossier=target_identity_dossier,
        top_entity_manifest=top_entity_manifest,
    )
    ordered: list[tuple[int, dict[str, Any]]] = []
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {
            pool.submit(
                _invoke_vote,
                invoke=invoke,
                model=vote_model,
                prompt=prompt,
                target_procedure_ref=target_procedure_ref,
            ): index
            for index, vote_model in enumerate(models)
        }
        for future in as_completed(futures):
            vote, _calls = future.result()
            ordered.append((futures[future], vote))
    panel_votes = [vote for _, vote in sorted(ordered)]
    resolution = aggregate_inheritance_votes(
        panel_votes,
        target_procedure_ref=target_procedure_ref,
        target_procedure_label=target_procedure_label,
    )
    reconciliation_votes: list[dict[str, Any]] = []
    initial_unresolved_reasons = list(resolution.get("unresolved_reasons") or [])
    if resolution.get("status") == "unresolved":
        selected_vote, reconciliation_votes = _reconcile_disagreed_votes(
            votes=panel_votes,
            source_text=source_text,
            target_procedure_ref=target_procedure_ref,
            target_procedure_label=target_procedure_label,
            tbox_contract=tbox_contract,
            target_identity_dossier=target_identity_dossier,
            top_entity_manifest=top_entity_manifest,
            models=models,
            invoke=invoke,
        )
        if selected_vote is not None:
            resolution = aggregate_inheritance_votes(
                [selected_vote, selected_vote, selected_vote],
                target_procedure_ref=target_procedure_ref,
                target_procedure_label=target_procedure_label,
            )
            resolution["reconciled_initial_disagreement"] = initial_unresolved_reasons
    # Persist the validated semantic votes for disagreement diagnosis.
    # Aggregation and acceptance still depend only on the fixed schema
    # projections above; these records never alter a verdict.
    resolution["panel_votes"] = panel_votes
    if reconciliation_votes:
        resolution["reconciliation_votes"] = reconciliation_votes
    return resolution


def resolve_procedure_inheritance(
    *,
    source_text: str,
    target_procedure_ref: str,
    target_procedure_label: str,
    tbox_contract: str | dict[str, Any],
    model: str,
    target_identity_dossier: dict[str, Any] | None = None,
    top_entity_manifest: Any = None,
    reviewer_model: str | None = None,
    verifier_model: str | None = None,
    invoke: Callable[..., LLMJsonResult] = invoke_json,
    cache_path: str | Path | None = None,
    attempt_budget: int = RESOLUTION_ATTEMPT_BUDGET,
) -> dict[str, Any]:
    """Resolve and flatten one target procedure using three independent votes."""
    models = [
        model,
        reviewer_model or model,
        verifier_model or reviewer_model or model,
    ]
    cache_key = _cache_key(
        source_text=source_text,
        target_procedure_ref=target_procedure_ref,
        target_procedure_label=target_procedure_label,
        tbox_contract=tbox_contract,
        target_identity_dossier=target_identity_dossier,
        top_entity_manifest=top_entity_manifest,
        models=models,
    )
    cache = Path(cache_path) if cache_path is not None else None
    if cache is not None:
        cached = _load_cache(cache, cache_key)
        if cached is not None:
            return cached

    last_resolution: dict[str, Any] | None = None
    last_reasons: list[str] = []
    budget = max(1, int(attempt_budget))
    for attempt in range(1, budget + 1):
        try:
            resolution = _resolve_procedure_inheritance_attempt(
                source_text=source_text,
                target_procedure_ref=target_procedure_ref,
                target_procedure_label=target_procedure_label,
                tbox_contract=tbox_contract,
                models=models,
                target_identity_dossier=target_identity_dossier,
                top_entity_manifest=top_entity_manifest,
                invoke=invoke,
            )
        except Exception as exc:
            last_reasons = [str(exc)]
            if attempt < budget:
                continue
            resolution = _empty_fail_open_resolution(
                target_procedure_ref=target_procedure_ref,
                target_procedure_label=target_procedure_label,
                reasons=last_reasons,
                attempts=attempt,
                last_resolution=last_resolution,
            )
            if cache is not None:
                _write_cache(cache, cache_key, resolution)
            return resolution
        resolution["resolution_attempts"] = attempt
        last_resolution = resolution
        if resolution.get("status") in {"resolved", "no_inheritance"}:
            if cache is not None:
                _write_cache(cache, cache_key, resolution)
            return resolution
        last_reasons = list(resolution.get("unresolved_reasons") or ["panel disagreement"])
        if attempt < budget:
            continue
        resolution = _empty_fail_open_resolution(
            target_procedure_ref=target_procedure_ref,
            target_procedure_label=target_procedure_label,
            reasons=last_reasons,
            attempts=attempt,
            last_resolution=resolution,
        )
        if cache is not None:
            _write_cache(cache, cache_key, resolution)
        return resolution
    resolution = _empty_fail_open_resolution(
        target_procedure_ref=target_procedure_ref,
        target_procedure_label=target_procedure_label,
        reasons=last_reasons or ["panel disagreement"],
        attempts=budget,
        last_resolution=last_resolution,
    )
    if cache is not None:
        _write_cache(cache, cache_key, resolution)
    return resolution


def render_procedure_inheritance_brief(resolution: dict[str, Any]) -> str:
    """Render a resolved panel output; unresolved fail-opens to source-only."""
    status = resolution.get("status")
    if status in {"no_inheritance", "unresolved"}:
        return ""
    if status != "resolved":
        return ""
    payload = {
        "schema_version": resolution.get("schema_version"),
        "target": resolution.get("target"),
        "dependencies": resolution.get("dependencies"),
        "base_workflows": resolution.get("base_workflows"),
        "modifications": resolution.get("modifications"),
        "effective_workflow": resolution.get("effective_workflow"),
    }
    return (
        f"{BRIEF_BEGIN}\n"
        "Respect same-as / following / analogously inheritance. The inherited "
        "context is complete. Apply only the target's explicit changes; do not "
        "drop inherited operations just because the target restatement does not "
        "repeat them.\n"
        + json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
        + f"\n{BRIEF_END}"
    )
