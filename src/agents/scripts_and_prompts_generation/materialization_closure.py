"""Compile iteration-scoped materialization capability obligations.

The closure is deliberately evidence-driven.  An optional source fact does not
assert that an individual must exist; it asserts that, when the fact is found,
the generated pipeline has a legal way to materialize its target.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Mapping

from src.agents.scripts_and_prompts_generation.reuse_policy import (
    prohibited_class_locals,
)

SCHEMA_VERSION = "materialization_contradiction.v1"


def _local_name(value: Any) -> str:
    text = str(value or "").strip().rstrip("/#")
    return text.rsplit("#", 1)[-1].rsplit("/", 1)[-1].rsplit(":", 1)[-1]


def _prohibited_classes(
    parsed: Mapping[str, Any],
    contract: Mapping[str, Any] | None = None,
) -> set[str]:
    parsed_prohibited = {
        str(local)
        for local, raw in (parsed.get("classes") or {}).items()
        if isinstance(raw, Mapping) and raw.get("creatable") is False
    }
    return parsed_prohibited | prohibited_class_locals(
        dict((contract or {}).get("reuse_policy") or {})
    )


def derive_creator_surface(context: Any) -> list[dict[str, Any]]:
    """Project the generated/fixed creator surface from authoritative contracts."""
    parsed = getattr(context, "parsed", {}) or {}
    contract = getattr(context, "contract", {}) or {}
    prohibited = _prohibited_classes(parsed, contract)
    surface: dict[tuple[str, str], dict[str, Any]] = {}

    from src.agents.scripts_and_prompts_generation.pure_llm_generation import (
        _owned_entity_tool_contracts,
    )

    for raw in _owned_entity_tool_contracts(context):
        spec = raw if isinstance(raw, Mapping) else {}
        local = str(spec.get("class_local") or "").strip()
        tool = str(spec.get("public_tool") or "").strip()
        if not local or not tool or local in prohibited:
            continue
        item = {
            "class_local": local,
            "class_iri": str(spec.get("class_iri") or ""),
            "tool": tool,
            "path_kind": "generated_creator",
            "datatype_inputs": [
                dict(value)
                for value in spec.get("datatype_inputs") or []
                if isinstance(value, Mapping)
            ],
            "required_edges": [
                dict(value)
                for value in spec.get("required_edges") or []
                if isinstance(value, Mapping)
            ],
        }
        surface[(local, item["path_kind"])] = item

    for raw in contract.get("external_class_creators") or []:
        spec = raw if isinstance(raw, Mapping) else {}
        local = str(spec.get("class_local") or _local_name(spec.get("class_iri")))
        tool = str(spec.get("tool_name") or "")
        if local and tool:
            item = {
                "class_local": local,
                "class_iri": str(spec.get("class_iri") or ""),
                "tool": tool,
                "path_kind": "generated_external_creator",
                "datatype_inputs": [],
            }
            surface[(local, item["path_kind"])] = item

    for raw in (contract.get("relationship_tool_contracts") or {}).values():
        spec = raw if isinstance(raw, Mapping) else {}
        fixed_iris = {
            str(value) for value in spec.get("fixed_runtime_range_iris") or []
        }
        for iri in fixed_iris:
            local = _local_name(iri)
            if local:
                item = {
                    "class_local": local,
                    "class_iri": iri,
                    "tool": "create_om2_quantity",
                    "path_kind": "fixed_om2_creator",
                    "datatype_inputs": [],
                }
                surface[(local, item["path_kind"])] = item
    return sorted(
        surface.values(),
        key=lambda item: (item["class_local"], item["path_kind"], item["tool"]),
    )


def creator_surface_class_locals(
    context: Any | None = None,
    creator_surface: Any | None = None,
) -> set[str]:
    """Return class locals that have a legal create_* or fixed creator path."""
    if creator_surface is None:
        if context is None:
            return set()
        surface: Any = derive_creator_surface(context)
    else:
        surface = creator_surface
    return set(_normalize_creator_surface(surface))


def restrict_classes_to_creator_surface(
    classes: Iterable[Any],
    creator_locals: Iterable[str],
) -> list[str]:
    """Keep declared class locals that also appear on the creator surface."""
    allowed = {str(value).strip() for value in creator_locals if str(value).strip()}
    seen: set[str] = set()
    kept: list[str] = []
    for raw in classes:
        local = str(raw).strip()
        if not local or local in seen or local not in allowed:
            continue
        seen.add(local)
        kept.append(local)
    return kept


def _normalize_creator_surface(
    creator_surface: Any,
) -> dict[str, list[dict[str, Any]]]:
    by_class: dict[str, list[dict[str, Any]]] = {}
    if isinstance(creator_surface, Mapping):
        values: list[Any] = []
        for local, raw in creator_surface.items():
            if isinstance(raw, str):
                values.append({"class_local": local, "tool": raw})
            elif isinstance(raw, Mapping):
                values.append({"class_local": local, **raw})
            elif isinstance(raw, list):
                values.extend(
                    {"class_local": local, **item}
                    for item in raw
                    if isinstance(item, Mapping)
                )
    else:
        values = list(creator_surface or [])
    for raw in values:
        if not isinstance(raw, Mapping):
            continue
        local = str(
            raw.get("class_local")
            or _local_name(raw.get("class_iri"))
        ).strip()
        tool = str(raw.get("tool") or raw.get("public_tool") or "").strip()
        if not local or not tool:
            continue
        by_class.setdefault(local, []).append(
            {
                "kind": str(raw.get("path_kind") or "generated_creator"),
                "tool": tool,
                "class_iri": str(raw.get("class_iri") or ""),
                "datatype_inputs": [
                    dict(value)
                    for value in raw.get("datatype_inputs") or []
                    if isinstance(value, Mapping)
                ],
                "required_edges": [
                    dict(value)
                    for value in raw.get("required_edges") or []
                    if isinstance(value, Mapping)
                ],
            }
        )
    return by_class


def _contract_property_path(
    prompt_contract: Mapping[str, Any],
    *,
    class_local: str,
    property_local: str,
) -> dict[str, Any]:
    """Describe a contract-backed class/datatype path without reading prompt prose."""
    tbox_scope = prompt_contract.get("tbox_scope") or {}
    classes = tbox_scope.get("classes") or {}
    properties = tbox_scope.get("properties") or {}
    property_spec = properties.get(property_local) or {}
    domains = {
        str(value)
        for value in (
            property_spec.get("domains") or [property_spec.get("domain")]
        )
        if str(value or "").strip()
    }
    return {
        "contract_present": bool(prompt_contract),
        "class_present": class_local in classes,
        "property_present": property_local in properties,
        "declared_domains": sorted(domains),
        "domain_applies": bool(
            class_local in classes and property_local in properties
        ),
    }


def _prompt_contract_for(
    prompt_generation_contracts: Mapping[str, Any], number: str, role: str
) -> dict[str, Any]:
    for key in (
        f"{role}:{number}",
        f"{role}_{number}",
        f"{role.upper()}_ITER_{number}.md",
        f"{'KG_BUILDING' if role == 'kg' else 'EXTRACTION'}_ITER_{number}.md",
    ):
        value = prompt_generation_contracts.get(key)
        if isinstance(value, Mapping):
            return dict(value)
    return {}


def compile_materialization_obligation_graph(
    context: Any,
    *,
    prompt_generation_contracts: Mapping[str, Any] | None = None,
    creator_surface: Any | None = None,
) -> dict[str, Any]:
    """Compile the per-iteration graph and deterministic contradictions."""
    parsed = getattr(context, "parsed", {}) or {}
    contract = getattr(context, "contract", {}) or {}
    plan = getattr(context, "iteration_blueprint", {}) or {}
    prompt_contracts = prompt_generation_contracts or {}
    prohibited = _prohibited_classes(parsed, contract)
    derived_surface = derive_creator_surface(context)
    if creator_surface is None:
        creator_items = derived_surface
    else:
        # Fixed infrastructure is not part of the generated entity-tool surface
        # supplied by callers, but it remains an authoritative capability path.
        explicit_items: list[Any] = []
        if isinstance(creator_surface, Mapping):
            for local, raw in creator_surface.items():
                if isinstance(raw, str):
                    explicit_items.append(
                        {"class_local": str(local), "tool": raw}
                    )
                elif isinstance(raw, Mapping):
                    explicit_items.append(
                        {"class_local": str(local), **raw}
                    )
                elif isinstance(raw, list):
                    explicit_items.extend(
                        {"class_local": str(local), **item}
                        for item in raw
                        if isinstance(item, Mapping)
                    )
        else:
            explicit_items = list(creator_surface or [])
        creator_items = [
            *explicit_items,
            *(
                item
                for item in derived_surface
                if item.get("path_kind") == "fixed_om2_creator"
            ),
        ]
    creators = _normalize_creator_surface(creator_items)
    materializable = set(creators)
    relationship_specs = contract.get("relationship_tool_contracts") or {}
    merged_predicates = {
        str(value)
        for value in (
            (contract.get("materialization_operation_units") or {}).get(
                "merged_predicate_locals"
            )
            or []
        )
        if str(value)
    }
    required_links = contract.get("required_links") or []
    top_local = str((contract.get("top_entity") or {}).get("class_local") or "")
    prior_identity: set[str] = {top_local} if top_local else set()
    obligations: list[dict[str, Any]] = []
    contradictions: list[dict[str, Any]] = []
    iteration_graphs: list[dict[str, Any]] = []
    creator_input_obligation_keys: set[tuple[str, str, str, str]] = set()

    def add_contradiction(
        code: str,
        iteration: str,
        subject: str,
        message: str,
        evidence: dict[str, Any],
    ) -> None:
        contradictions.append(
            {
                "id": f"contradiction-{len(contradictions) + 1}",
                "code": code,
                "iteration": iteration,
                "subject": subject,
                "message": message,
                "evidence": evidence,
                "authoritative": True,
            }
        )

    for raw_iteration in plan.get("iterations") or []:
        if not isinstance(raw_iteration, Mapping):
            continue
        iteration = dict(raw_iteration)
        number = str(iteration.get("iteration_number") or "")
        responsibilities = iteration.get("responsibilities") or {}
        owned_classes = {
            str(value)
            for value in restrict_classes_to_creator_surface(
                responsibilities.get("classes") or [],
                materializable,
            )
        }
        owned_properties = {
            str(value)
            for value in responsibilities.get("object_properties") or []
            if str(value)
        }
        linked_classes = {
            str(value)
            for value in restrict_classes_to_creator_surface(
                iteration.get("linked_materialization_classes") or [],
                materializable,
            )
        }
        extraction_contract = _prompt_contract_for(
            prompt_contracts, number, "extraction"
        )
        kg_contract = _prompt_contract_for(prompt_contracts, number, "kg")
        iteration_obligation_ids: list[str] = []

        def add_creator_input_obligations(
            class_local: str,
            paths: list[dict[str, Any]],
        ) -> None:
            for path in paths:
                tool = str(path.get("tool") or "")
                for raw_input in path.get("datatype_inputs") or []:
                    datatype_input = (
                        dict(raw_input) if isinstance(raw_input, Mapping) else {}
                    )
                    property_local = str(
                        datatype_input.get("property_local") or ""
                    ).strip()
                    if not property_local:
                        continue
                    key = (number, class_local, tool, property_local)
                    if key in creator_input_obligation_keys:
                        continue
                    creator_input_obligation_keys.add(key)
                    extraction_path = _contract_property_path(
                        extraction_contract,
                        class_local=class_local,
                        property_local=property_local,
                    )
                    kg_path = _contract_property_path(
                        kg_contract,
                        class_local=class_local,
                        property_local=property_local,
                    )
                    required = bool(datatype_input.get("required"))
                    obligation = {
                        "id": f"obligation-{len(obligations) + 1}",
                        "iteration": number,
                        "kind": "creator_input",
                        "source": "creator_contract",
                        "class_local": class_local,
                        "predicate_local": property_local,
                        "creator_tool": tool,
                        "datatype_input": datatype_input,
                        "input_requirement": "required" if required else "optional",
                        "required_when": (
                            "always_for_created_instance"
                            if required
                            else "source_evidence_present"
                        ),
                        "capability_required": True,
                        "transitive_contract_path": {
                            "extraction": extraction_path,
                            "kg": kg_path,
                            "complete": bool(
                                extraction_path["domain_applies"]
                                and kg_path["domain_applies"]
                            ),
                        },
                    }
                    obligations.append(obligation)
                    iteration_obligation_ids.append(obligation["id"])
                    contracts_available = bool(extraction_contract and kg_contract)
                    if (
                        required
                        and contracts_available
                        and not obligation["transitive_contract_path"]["complete"]
                    ):
                        add_contradiction(
                            "required_creator_input_without_prompt_contract_path",
                            number,
                            f"{tool}.{property_local}",
                            (
                                f"Required creator input {tool}.{property_local} has no complete "
                                "extraction-to-KG contract path"
                            ),
                            {"obligation": obligation},
                        )

        for class_local in sorted(owned_classes | linked_classes):
            paths = list(creators.get(class_local) or [])
            obligation = {
                "id": f"obligation-{len(obligations) + 1}",
                "iteration": number,
                "kind": "owned_class_materialization",
                "source": "linked" if class_local in linked_classes else "iteration_owned",
                "class_local": class_local,
                "predicate_local": "",
                "required_when": "source_evidence_present",
                "capability_required": True,
                "materialization_paths": paths,
            }
            obligations.append(obligation)
            iteration_obligation_ids.append(obligation["id"])
            add_creator_input_obligations(class_local, paths)
            if class_local in prohibited:
                add_contradiction(
                    "prohibited_class_required",
                    number,
                    class_local,
                    f"Iteration {number} requires prohibited class {class_local} to be materializable",
                    {"obligation": obligation},
                )
            elif not paths:
                add_contradiction(
                    "owned_class_without_materialization_path",
                    number,
                    class_local,
                    f"Iteration-owned class {class_local} has no generated creator",
                    {"obligation": obligation},
                )
            for path in paths:
                for edge in path.get("required_edges") or []:
                    predicate_local = str(
                        edge.get("predicate_local") or ""
                    ).strip()
                    edge_obligation = {
                        "id": f"obligation-{len(obligations) + 1}",
                        "iteration": number,
                        "kind": "operation_unit_required_edge",
                        "source": "materialization_operation_unit",
                        "class_local": class_local,
                        "predicate_local": predicate_local,
                        "required_when": "always_for_created_instance",
                        "capability_required": True,
                        "creator_tool": str(path.get("tool") or ""),
                        "edge_contract": dict(edge),
                        "materialization_paths": [
                            {
                                "kind": "atomic_creator",
                                "tool": str(path.get("tool") or ""),
                            }
                        ],
                    }
                    obligations.append(edge_obligation)
                    iteration_obligation_ids.append(edge_obligation["id"])

        for property_local in sorted(owned_properties):
            if property_local in merged_predicates:
                continue
            spec = relationship_specs.get(property_local) or {}
            range_locals = {
                str(value)
                for value in (
                    spec.get("materialization_target_locals")
                    or spec.get("range_locals")
                    or []
                )
                if str(value)
            }
            if not range_locals:
                parsed_range = _local_name(
                    ((parsed.get("properties") or {}).get(property_local) or {}).get(
                        "range"
                    )
                )
                if parsed_range:
                    range_locals.add(parsed_range)
            for target_local in sorted(range_locals):
                paths = list(creators.get(target_local) or [])
                if target_local in prior_identity:
                    paths.append(
                        {
                            "kind": "prior_identity",
                            "tool": "",
                            "class_iri": "",
                        }
                    )
                required_link = next(
                    (
                        item
                        for item in required_links
                        if _local_name((item or {}).get("predicate_iri"))
                        == property_local
                        and (
                            not (item or {}).get("target_class_iri")
                            or _local_name((item or {}).get("target_class_iri"))
                            == target_local
                        )
                    ),
                    None,
                )
                if target_local in prohibited and required_link is None:
                    continue
                obligation = {
                    "id": f"obligation-{len(obligations) + 1}",
                    "iteration": number,
                    "kind": (
                        "validator_required_link"
                        if required_link
                        else "owned_object_property_target"
                    ),
                    "source": "required_link" if required_link else "iteration_owned",
                    "class_local": target_local,
                    "predicate_local": property_local,
                    "required_when": (
                        "always" if required_link else "source_evidence_present"
                    ),
                    "capability_required": True,
                    "materialization_paths": paths,
                }
                obligations.append(obligation)
                iteration_obligation_ids.append(obligation["id"])
                add_creator_input_obligations(target_local, paths)
                if not paths:
                    add_contradiction(
                        (
                            "required_link_without_materialization_path"
                            if required_link
                            else "target_without_materialization_path"
                        ),
                        number,
                        f"{property_local}->{target_local}",
                        (
                            f"Validator-required link {property_local} has no materialization path "
                            f"for {target_local}"
                            if required_link
                            else f"Owned property {property_local} cannot materialize target {target_local}"
                        ),
                        {"obligation": obligation, "relationship_contract": spec},
                    )
                if required_link and target_local in prohibited:
                    add_contradiction(
                        "prohibited_class_required",
                        number,
                        f"{property_local}->{target_local}",
                        f"Required link {property_local} demands prohibited target {target_local}",
                        {"obligation": obligation, "required_link": required_link},
                    )

        iteration_graphs.append(
            {
                "iteration": number,
                "obligation_ids": iteration_obligation_ids,
                "prior_identity_classes": sorted(prior_identity),
                "prompt_evidence": {
                    "extraction_contract": extraction_contract,
                    "kg_contract": kg_contract,
                },
            }
        )
        prior_identity.update(owned_classes)

    return {
        "schema_version": SCHEMA_VERSION,
        "ontology": str(getattr(getattr(context, "ontology", None), "name", "")),
        "semantics": {
            "optional_source_facts": "do_not_assert_instances",
            "capability_obligation": "when_source_evidence_exists_a_legal_materialization_path_must_exist",
        },
        "creator_surface": list(creator_items or []),
        "iterations": iteration_graphs,
        "obligations": obligations,
        "contradictions": contradictions,
        "ok": not contradictions,
    }


def materialization_closure_failures(report: Mapping[str, Any]) -> list[str]:
    return [
        f"materialization closure [{item.get('code')}]: {item.get('message')}"
        for item in report.get("contradictions") or []
        if isinstance(item, Mapping)
    ]
