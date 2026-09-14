"""Compile public occurrence tools from candidates plus decisions."""

from __future__ import annotations

import re
from typing import Any, Mapping

from src.extraction_prompt_generation.compile.operation_units import (
    _base_creator_contracts,
    _local_name,
    _python_name,
)
from src.extraction_prompt_generation.compile.reuse_policy import (
    prohibited_class_locals,
)
from src.kg_building_mcp_generation_v2.surface.helpers import (
    LINKER_KIND,
    LOOP_GUARD_SCHEMA,
    MEMBERSHIP_KIND,
    ROOT_QUANTITY_KIND,
    UNIT_SCHEMA,
    _closure_map,
    _matches_class,
    _reusable_by_iri,
    _top_entity,
    collect_extension_bridge_class_iris,
    extension_focus_iri,
    extension_public_reusable_owner_iris,
)

_TOOL_MENTION = re.compile(
    r"\b(create_[A-Za-z0-9_]+|link_[A-Za-z0-9_]+|init_memory|export_memory|"
    r"inspect_ordered_members|skip_semantic_obligation|add_[A-Za-z0-9_]+|"
    r"check_existing_[A-Za-z0-9_]+|"
    r"update_[A-Za-z0-9_]+)\b"
)


def public_owner_may_omit_parent(tool: Mapping[str, Any]) -> bool:
    """Return whether a public create_* may omit parent_iri.

    The bound top entity is never a public owner. Adopted-focus,
    ordered-member, and unique-incoming-parent owners may omit or already
    compile parent_iri. A parentless non-focus class is still a public
    heading unless it is already a nested fresh dependent of another
    public owner; those nested classes are dropped after compile.
    """
    if str(tool.get("parent_parameter") or ""):
        return True
    if tool.get("adopted_focus"):
        return True
    if tool.get("ordered_member"):
        return True
    return False


def _nested_fresh_target_iris(public_tools: list[Mapping[str, Any]]) -> set[str]:
    return {
        str(item.get("target_class_iri") or "")
        for tool in public_tools
        for item in tool.get("fresh_dependents") or []
        if str(item.get("target_class_iri") or "")
    }


def keep_as_public_owner(
    tool: Mapping[str, Any], nested_targets: set[str]
) -> bool:
    """Keep compiled public create_* unless it is parentless and nested."""
    if public_owner_may_omit_parent(tool):
        return True
    owner_iri = str(tool.get("owner_class_iri") or "")
    return not owner_iri or owner_iri not in nested_targets


def _decision_map(decisions: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(item.get("candidate_id") or ""): dict(item)
        for item in decisions.get("decisions") or []
        if isinstance(item, Mapping) and str(item.get("candidate_id") or "")
    }

def _semantic_optional_argument_names(tool: Mapping[str, Any]) -> list[str]:
    """Return stable public arguments that contribute to occurrence identity."""
    names: list[str] = []
    ordering = _python_name(str(tool.get("ordering_property_local") or ""))
    for item in tool.get("datatype_inputs") or []:
        name = _python_name(str(item.get("property_local") or ""))
        if name and name != ordering:
            names.append(name)
    for item in list(tool.get("quantities") or []) + list(
        tool.get("parent_quantities") or []
    ):
        names.append(str(item.get("parameter") or ""))
    for group in ("fresh_dependents", "reusable_links"):
        for item in tool.get(group) or []:
            names.append(str(item.get("label_parameter") or ""))
            names.extend(
                str(value.get("parameter_name") or "")
                for value in item.get("datatype_inputs") or []
            )
    for item in tool.get("nested_reusable_links") or []:
        names.append(str(item.get("label_parameter") or ""))
    return list(dict.fromkeys(name for name in names if name))

def compile_occurrence_surface(
    *,
    parsed: Mapping[str, Any],
    contract: Mapping[str, Any],
    iteration_plan: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Compile the public occurrence surface from candidates plus decisions."""
    creators = _base_creator_contracts(parsed=parsed, contract=contract)
    by_iri = {
        str(item.get("class_iri") or ""): item
        for item in creators
        if str(item.get("class_iri") or "")
    }
    reusable = _reusable_by_iri(contract)
    prohibited = prohibited_class_locals(contract.get("reuse_policy"))
    top_local, top_iri = _top_entity(contract)
    focus_iri = extension_focus_iri(contract)
    kept_reusable_owners = extension_public_reusable_owner_iris(
        parsed=parsed, contract=contract
    )
    candidate_bundle = contract.get("occurrence_surface_candidates") or {}
    decisions = _decision_map(contract.get("occurrence_surface_decisions") or {})
    candidates = [
        item
        for item in candidate_bundle.get("candidates") or []
        if isinstance(item, Mapping)
    ]
    bundled_predicates: set[str] = set()
    errors: list[str] = []
    datatype_iri_by_local = {
        _local_name(str(item.get("property_iri") or "")): str(
            item.get("property_iri") or ""
        )
        for item in (
            (contract.get("ontology_publish_contract") or {}).get(
                "datatype_properties"
            )
            or []
        )
        if str(item.get("property_iri") or "")
    }

    def _accepted(candidate: Mapping[str, Any]) -> bool:
        if candidate.get("kind") == MEMBERSHIP_KIND:
            return True
        if candidate.get("decision_space") == "deterministic_bundle":
            return True
        if candidate.get("decision_space") == "deterministic_expose":
            return True
        item = decisions.get(str(candidate.get("candidate_id") or ""))
        if item is None:
            return False
        if candidate.get("kind") == LINKER_KIND:
            return str(item.get("decision") or "") == "expose"
        return str(item.get("decision") or "") == "bundle"

    tools: dict[str, dict[str, Any]] = {}
    for creator in creators:
        owner_iri = str(creator.get("class_iri") or "")
        owner_local = str(creator.get("class_local") or "")
        skip_reusable = (
            reusable.get(owner_iri) is True and owner_iri not in kept_reusable_owners
        )
        if (
            not owner_iri
            or (owner_local in prohibited and not creator.get("ordered_member"))
            or skip_reusable
            or (top_iri and owner_iri == top_iri)
            or creator.get("external_range_class")
        ):
            continue
        is_focus = bool(focus_iri and owner_iri == focus_iri)
        tool = {
            "name": f"create_{owner_local}",
            "owner_class_local": owner_local,
            "owner_class_iri": owner_iri,
            "primitive_tool": str(creator.get("public_tool") or f"create_{owner_local}"),
            "ordered_member": bool(creator.get("ordered_member")),
            "ordering_property_local": str(creator.get("ordering_property_local") or ""),
            "ordering_property_iri": str(
                creator.get("ordering_property_iri")
                or datatype_iri_by_local.get(
                    str(creator.get("ordering_property_local") or ""),
                    "",
                )
            ),
            "parent_parameter": "",
            "parent_predicate_local": "",
            "parent_predicate_iri": "",
            "parent_unique_incoming": False,
            "parent_binds_to_session_root": bool(
                creator.get("ordered_member")
            )
            and not is_focus,
            "parent_via_primitive": bool(creator.get("ordered_member")),
            "idempotent": bool(creator.get("ordered_member")),
            "parent_quantities": [],
            "datatype_inputs": [
                dict(item) for item in creator.get("datatype_inputs") or []
            ],
            "quantities": [],
            "fresh_dependents": [],
            "reusable_links": [],
            "nested_reusable_links": [],
        }
        if is_focus:
            tool["adopted_focus"] = True
        if creator.get("ordered_member"):
            tool["parent_parameter"] = "parent_iri"
        tools[owner_iri] = tool

    for candidate in candidates:
        if not _accepted(candidate):
            continue
        kind = str(candidate.get("kind") or "")
        owner_iri = str(candidate.get("owner_class_iri") or "")
        predicate_local = str(candidate.get("predicate_local") or "")
        if kind in {LINKER_KIND, ROOT_QUANTITY_KIND}:
            continue
        tool = tools.get(owner_iri)
        if tool is None:
            if kind == "parent_link":
                continue
            errors.append(
                f"{candidate.get('candidate_id')}: bundled facet has no public owner"
            )
            continue
        if kind == MEMBERSHIP_KIND:
            tool["parent_parameter"] = "parent_iri"
            tool["parent_predicate_local"] = predicate_local
            tool["parent_predicate_iri"] = str(candidate.get("predicate_iri") or "")
            tool["parent_via_primitive"] = True
            tool["parent_binds_to_session_root"] = True
            bundled_predicates.add(predicate_local)
            continue
        if kind == "parent_link":
            evidence = candidate.get("structural_evidence") or {}
            unique_incoming = bool(evidence.get("unique_incoming_parent_predicate"))
            unique_from_top = bool(evidence.get("unique_incoming_from_top_entity"))
            unique_from_focus = bool(
                evidence.get("unique_incoming_from_extension_focus")
            )
            containers = {
                str(value) for value in candidate.get("container_class_iris") or [] if str(value)
            }
            tool["parent_parameter"] = "parent_iri"
            tool["parent_predicate_local"] = predicate_local
            tool["parent_predicate_iri"] = str(candidate.get("predicate_iri") or "")
            tool["parent_unique_incoming"] = bool(unique_incoming and unique_from_top)
            tool["parent_binds_to_session_root"] = bool(
                (unique_from_top or (top_iri and top_iri in containers))
                and not unique_from_focus
            )
            tool["parent_via_primitive"] = False
            if tool["parent_unique_incoming"]:
                tool["idempotent"] = True
            bundled_predicates.add(predicate_local)
            continue
        if kind == "owner_quantity":
            tool["quantities"].append(
                {
                    "parameter": _python_name(predicate_local),
                    "predicate_local": predicate_local,
                    "predicate_iri": str(candidate.get("predicate_iri") or ""),
                    "range_iri": str(candidate.get("target_class_iri") or ""),
                }
            )
            bundled_predicates.add(predicate_local)
            continue
        if kind == "fresh_dependent":
            target_iri = str(candidate.get("target_class_iri") or "")
            target = by_iri.get(target_iri) or {}
            prefix = _python_name(predicate_local)
            tool["fresh_dependents"].append(
                {
                    "label_parameter": f"{prefix}_label",
                    "predicate_local": predicate_local,
                    "predicate_iri": str(candidate.get("predicate_iri") or ""),
                    "target_class_local": str(candidate.get("target_class_local") or ""),
                    "target_class_iri": target_iri,
                    "create_tool": str(target.get("public_tool") or ""),
                    "datatype_inputs": [
                        {
                            **dict(item),
                            "parameter_name": _python_name(
                                f"{prefix}_{item.get('property_local')}"
                            ),
                        }
                        for item in (
                            candidate.get("dependent_datatype_inputs")
                            or target.get("datatype_inputs")
                            or []
                        )
                        if str(item.get("property_local") or "")
                        != str(target.get("ordering_property_local") or "")
                    ],
                }
            )
            bundled_predicates.add(predicate_local)
            continue
        if kind == "reusable_link":
            prefix = _python_name(predicate_local)
            target_iri = str(candidate.get("target_class_iri") or "")
            target = by_iri.get(target_iri) or {}
            datatype_inputs = [
                {
                    **dict(item),
                    "parameter_name": _python_name(
                        f"{prefix}_{item.get('property_local')}"
                    ),
                }
                for item in target.get("datatype_inputs") or []
                if str(item.get("property_local") or "")
            ]
            tool["reusable_links"].append(
                {
                    "label_parameter": f"{prefix}_label",
                    "predicate_local": predicate_local,
                    "predicate_iri": str(candidate.get("predicate_iri") or ""),
                    "target_class_local": str(candidate.get("target_class_local") or ""),
                    "target_class_iri": target_iri,
                    "create_tool": str(target.get("public_tool") or ""),
                    "create_fresh_with_datatypes": bool(datatype_inputs),
                    "datatype_inputs": datatype_inputs,
                }
            )
            bundled_predicates.add(predicate_local)
            continue
        if kind == "nested_reusable_link":
            child_local = str(candidate.get("child_predicate_local") or "")
            tool["nested_reusable_links"].append(
                {
                    "label_parameter": f"{_python_name(child_local)}_label",
                    "parent_predicate_local": predicate_local,
                    "parent_predicate_iri": str(candidate.get("predicate_iri") or ""),
                    "child_predicate_local": child_local,
                    "child_predicate_iri": str(candidate.get("child_predicate_iri") or ""),
                    "intermediate_class_iri": str(
                        candidate.get("intermediate_class_iri") or ""
                    ),
                    "target_class_local": str(candidate.get("target_class_local") or ""),
                    "target_class_iri": str(candidate.get("target_class_iri") or ""),
                }
            )
            bundled_predicates.add(child_local)
            continue
        errors.append(f"{candidate.get('candidate_id')}: unsupported bundled kind {kind}")

    root_quantities = [
        item
        for item in candidates
        if item.get("kind") == ROOT_QUANTITY_KIND and _accepted(item)
    ]
    unique_parent_tools = [
        tool
        for tool in tools.values()
        if tool.get("parent_unique_incoming") and not tool.get("ordered_member")
    ]
    public_linkers: list[dict[str, Any]] = []
    if len(unique_parent_tools) == 1:
        host = unique_parent_tools[0]
        for item in root_quantities:
            predicate_local = str(item.get("predicate_local") or "")
            host.setdefault("parent_quantities", []).append(
                {
                    "parameter": _python_name(predicate_local),
                    "predicate_local": predicate_local,
                    "predicate_iri": str(item.get("predicate_iri") or ""),
                    "range_iri": str(item.get("target_class_iri") or ""),
                }
            )
            bundled_predicates.add(predicate_local)
    else:
        for item in root_quantities:
            predicate_local = str(item.get("predicate_local") or "")
            public_linkers.append(
                {
                    "name": f"link_{_python_name(predicate_local)}",
                    "predicate_local": predicate_local,
                    "predicate_iri": str(item.get("predicate_iri") or ""),
                    "subject_class_local": str(item.get("owner_class_local") or ""),
                    "subject_class_iri": str(item.get("owner_class_iri") or ""),
                    "object_class_local": "",
                    "object_class_iri": str(item.get("target_class_iri") or ""),
                    "quantity_range_iri": str(item.get("target_class_iri") or ""),
                }
            )
            bundled_predicates.add(predicate_local)

    for candidate in candidates:
        if candidate.get("kind") != LINKER_KIND or not _accepted(candidate):
            continue
        predicate_local = str(candidate.get("predicate_local") or "")
        if predicate_local in bundled_predicates:
            continue
        public_linkers.append(
            {
                "name": f"link_{_python_name(predicate_local)}",
                "predicate_local": predicate_local,
                "predicate_iri": str(candidate.get("predicate_iri") or ""),
                "subject_class_local": str(candidate.get("owner_class_local") or ""),
                "subject_class_iri": str(candidate.get("owner_class_iri") or ""),
                "object_class_local": str(candidate.get("target_class_local") or ""),
                "object_class_iri": str(candidate.get("target_class_iri") or ""),
            }
        )
        bundled_predicates.add(predicate_local)

    reusable_classes = [
        {
            "class_local": str(item.get("class_local") or ""),
            "class_iri": str(item.get("class_iri") or ""),
            "create_tool": str(item.get("public_tool") or ""),
        }
        for item in creators
        if reusable.get(str(item.get("class_iri") or "")) is True
        and str(item.get("public_tool") or "")
    ]
    public_tools = sorted(tools.values(), key=lambda item: str(item.get("name") or ""))
    for tool in public_tools:
        parent = str(tool.get("parent_parameter") or "")
        ordering = _python_name(str(tool.get("ordering_property_local") or ""))
        if tool.get("adopted_focus"):
            identity_kind = "adopted_focus"
            identity_args = []
        elif tool.get("ordered_member") and parent and ordering:
            identity_kind = "ordered"
            identity_args = [parent, ordering]
        elif tool.get("parent_unique_incoming") and parent:
            identity_kind = "unique_parent"
            identity_args = [parent]
        else:
            identity_kind = "semantic_occurrence"
            identity_args = [parent] if parent else []
            identity_args.append("label")
        tool["idempotent"] = True
        tool["identity_contract"] = {
            "kind": identity_kind,
            "identity_args": list(dict.fromkeys(arg for arg in identity_args if arg)),
        }
        if identity_kind == "unique_parent":
            for item in tool.get("fresh_dependents") or []:
                if str(item.get("label_parameter") or "") and str(
                    item.get("create_tool") or ""
                ):
                    item["default_label_from_owner"] = True
            for item in tool.get("reusable_links") or []:
                if item.get("create_fresh_with_datatypes") and str(
                    item.get("label_parameter") or ""
                ):
                    item["default_label_from_owner"] = True
    bridges = set(collect_extension_bridge_class_iris(contract=contract))
    closure = _closure_map(contract)
    if bridges:
        for tool in public_tools:
            for group in ("fresh_dependents", "reusable_links"):
                for item in tool.get(group) or []:
                    target_iri = str(item.get("target_class_iri") or "")
                    if target_iri and _matches_class(target_iri, bridges, closure):
                        item["required_bridge_link"] = True
                        item["default_label_from_owner"] = True
    nested_targets = _nested_fresh_target_iris(public_tools)
    public_tools = [
        tool for tool in public_tools if keep_as_public_owner(tool, nested_targets)
    ]
    for linker in public_linkers:
        linker["identity_contract"] = {
            "kind": "semantic_link",
            "identity_args": ["subject_iri", "object_label"],
        }
    compiled = {
        "schema_version": UNIT_SCHEMA,
        "policy_source": "deterministic_tbox_occurrence_surface",
        "public_tools": public_tools,
        "public_linkers": sorted(public_linkers, key=lambda item: str(item.get("name") or "")),
        "reusable_classes": sorted(
            reusable_classes, key=lambda item: str(item.get("class_local") or "")
        ),
        "bundled_predicate_locals": sorted(bundled_predicates),
        "lifecycle_tools": [
            "init_memory",
            "export_memory",
            "inspect_ordered_members",
            "skip_semantic_obligation",
        ],
        "instruction": "",
        "errors": errors,
        "top_entity_class_local": top_local,
        "top_entity_class_iri": top_iri,
    }
    compiled["instruction"] = compile_fallback_instruction(compiled)
    compiled["loop_guard"] = compile_loop_guard_contract(compiled)
    return compiled

def compile_loop_guard_contract(compiled: Mapping[str, Any]) -> dict[str, Any]:
    """Project host-side idempotent identities from the compiled public surface."""
    unique_parent: list[dict[str, Any]] = []
    ordered_members: list[dict[str, Any]] = []
    mutations: list[dict[str, Any]] = []
    for tool in compiled.get("public_tools") or []:
        name = str(tool.get("name") or "")
        identity = tool.get("identity_contract") or {}
        identity_args = [
            str(value) for value in identity.get("identity_args") or [] if str(value)
        ]
        identity_kind = str(identity.get("kind") or "semantic_occurrence")
        if not name:
            continue
        item = {
            "name": name,
            "identity_kind": identity_kind,
            "identity_args": identity_args,
        }
        mutations.append(item)
        if identity_kind == "ordered":
            ordered_members.append({"name": name, "identity_args": identity_args})
        elif identity_kind in {"unique_parent", "adopted_focus"}:
            unique_parent.append({"name": name, "identity_args": identity_args})
    for linker in compiled.get("public_linkers") or []:
        name = str(linker.get("name") or "")
        identity = linker.get("identity_contract") or {}
        if name:
            mutations.append(
                {
                    "name": name,
                    "identity_kind": str(identity.get("kind") or "semantic_link"),
                    "identity_args": [
                        str(value)
                        for value in identity.get("identity_args") or []
                        if str(value)
                    ],
                }
            )
    unique_parent.sort(key=lambda item: str(item.get("name") or ""))
    ordered_members.sort(key=lambda item: str(item.get("name") or ""))
    mutations.sort(key=lambda item: str(item.get("name") or ""))
    return {
        "schema_version": LOOP_GUARD_SCHEMA,
        "unique_parent_tools": unique_parent,
        "ordered_member_tools": ordered_members,
        "mutation_tools": mutations,
    }

def is_deterministic_candidate(candidate: Mapping[str, Any]) -> bool:
    return (
        str(candidate.get("kind") or "") == MEMBERSHIP_KIND
        or str(candidate.get("decision_space") or "")
        in {"deterministic_bundle", "deterministic_expose"}
    )

def public_tool_names(compiled: Mapping[str, Any]) -> set[str]:
    names = {str(item) for item in compiled.get("lifecycle_tools") or []}
    names.update(str(item.get("name") or "") for item in compiled.get("public_tools") or [])
    names.update(str(item.get("name") or "") for item in compiled.get("public_linkers") or [])
    names.discard("")
    return names

def mentioned_tool_names(instruction: str) -> set[str]:
    return set(_TOOL_MENTION.findall(instruction or ""))

def compile_fallback_instruction(compiled: Mapping[str, Any]) -> str:
    """Operational instruction compiled only from the public surface contract."""
    creates = [str(item.get("name") or "") for item in compiled.get("public_tools") or []]
    linkers = [str(item.get("name") or "") for item in compiled.get("public_linkers") or []]
    reusable = [
        str(item.get("class_local") or "")
        for item in compiled.get("reusable_classes") or []
        if str(item.get("class_local") or "")
    ]
    create_list = ", ".join(f"`{name}`" for name in creates) or "the compiled create tools"
    linker_list = (
        ", ".join(f"`{name}`" for name in linkers)
        if linkers
        else "no additional public linkers"
    )
    reusable_list = ", ".join(reusable) if reusable else "the compiled reusable classes"
    has_non_root_parent = any(
        item.get("parent_parameter")
        and not item.get("parent_binds_to_session_root", True)
        for item in compiled.get("public_tools") or []
    )
    adopted_focus_tools = [
        str(item.get("name") or "")
        for item in compiled.get("public_tools") or []
        if (item.get("identity_contract") or {}).get("kind") == "adopted_focus"
        and str(item.get("name") or "")
    ]
    adopted_focus_text = (
        " Compiled adopted-focus create_* tools ("
        + ", ".join(f"`{name}`" for name in adopted_focus_tools)
        + ") reuse the pipeline-seeded enrichment IRI for that owner class; do not "
        "mint a second individual of that class. "
        if adopted_focus_tools
        else ""
    )
    parent_binding = (
        "When a create_* tool description says parent_iri is the session bound root, "
        "and for every public linker subject_iri, use the exact bound root IRI supplied "
        "by the pipeline. When a create_* tool description says parent_iri is another "
        "created occurrence, pass that occurrence's returned IRI; do not substitute the "
        "bound root. Use returned child handles only as that parent_iri or where an "
        "explicit public argument requests one. "
        if has_non_root_parent
        else (
            "For every public create_* parent_iri and every public linker subject_iri, use the "
            "exact bound root IRI supplied by the pipeline; never pass a created child handle "
            "as that argument. Use returned child handles "
            "only where an explicit public argument requests one. "
        )
    )
    return (
        "The pipeline has already called init_memory for the bound root; do not call "
        "it again. The MCP never reads the ledger. Read each occurrence heading in "
        f"the supplied ledger exactly once and issue the matching create_* call ({create_list}). "
        "Each public create_* binds to exactly one owner class. Headings of different owner "
        "classes remain distinct occurrences even when their labels match, so neither "
        "call satisfies the other. Put every supported detail from that heading into the same "
        "creator call through its compiled optional arguments. Do not split those details into "
        "later calls. Sentinel or empty optional labels mean that facet is absent. "
        f"{adopted_focus_text}{parent_binding}Never pass an argument absent "
        "from the selected tool signature. Treat each tool description's allowed-arguments "
        "list and nested ownership paths as authoritative: a nested property's ontology name "
        "is not a keyword unless that exact keyword is listed; use the compiled flat/prefixed "
        "argument on its owning occurrence call. "
        "Take each order value from the heading and do not "
        "invent order positions. A unique parent-owned occurrence is created once; "
        "later calls for the same parent return the committed IRI. Identity fields of a "
        "compiled representation link belong on that owner call. References stay on the owner "
        "that compiled them. Occurrence owners and non-reusable "
        "dependents are always fresh. "
        f"Reusable classes ({reusable_list}) are resolved or created inside the semantic "
        "transaction according to the compiled reuse policy; never create or choose their "
        "IRIs yourself. "
        f"The only public label-resolved linkers are: {linker_list}. "
        "Calls for independent headings may be emitted together in one assistant turn. "
        "Reuse only returned IRIs. A failed optional quantity facet is omitted with a structured "
        "warning while its valid owner remains committed. If that warning explicitly records "
        "an OM-2 parser failure and marks the source value skippable after a parser-verified "
        "representation failure, call "
        "skip_semantic_obligation once with its exact obligation_id. Other rejected "
        "transactions mutate nothing; correct that occurrence once from the structured error "
        "and continue. When a structured relationship rejection explicitly instructs you to "
        "skip the invalid relationship and no compatible source-grounded repair applies, call "
        "skip_semantic_obligation once with its exact semantic_fingerprint and a concise "
        "reason. Never skip a rejected owner creation. "
        "An already_committed receipt with graph_changed false is not progress. "
        "Before export_memory, call every public create_* that matches a ledger heading. "
        "Do not export while any of those tools is still unused. After every "
        "heading has exactly one successful semantic operation, call export_memory once. "
        "Export applies graph-only orphan pruning from the bound top entity and "
        "ordered-member repairs without reading "
        "source hints or a pipeline ledger manifest."
    )

