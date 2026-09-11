"""Iteration-scoped artifact generation contract. Do not import validate.report.

Assembles one prompt's T-Box slice, slots, and sub-contracts. Importing
`validate.report` here would cycle with authoring. See contracts/README.md.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.extraction_prompt_generation.compile.context import (
    AgenticGenerationContext,
)
from src.extraction_prompt_generation.compile.extension_prompt import (
    EXTENSION_EXTRACTION_RUNTIME_SLOTS,
)
from src.extraction_prompt_generation.compile.operation_units import (
    owned_entity_tool_contracts as _owned_entity_tool_contracts,
)
from src.extraction_prompt_generation.generate.extraction_prompts.contracts.role_and_guidance import (
    _generic_prompt_pipeline_role,
)
from src.extraction_prompt_generation.generate.extraction_prompts.contracts.scope import (
    _iteration_owned_scope,
    _prompt_iteration_spec,
)
from src.extraction_prompt_generation.generate.extraction_prompts.contracts.sub_contracts import (
    _iter1_pipeline_top_entity_contract,
    _lexical_quantity_hint_contract,
    _pre_extraction_candidate_type_contract,
    _subclass_decision_contract,
)
from src.extraction_prompt_generation.generate.extraction_prompts.contracts.tbox_slice import (
    _prompt_tbox_slice,
    _subclass_comment_projection,
    _warning_marked_tbox_contract,
)
from src.extraction_prompt_generation.tbox.property_contract import (
    derive_iteration_property_contract,
)


def _class_and_parent_locals(
    parsed: dict[str, Any], class_local: str
) -> set[str]:
    """Close one class under parsed parent_classes. Locals only."""
    classes = parsed.get("classes") or {}
    queued = {class_local} if class_local in classes else set()
    pending = list(queued)
    while pending:
        current = pending.pop()
        for parent in (classes.get(current) or {}).get("parent_classes") or []:
            parent_local = str(parent).strip()
            if parent_local in classes and parent_local not in queued:
                queued.add(parent_local)
                pending.append(parent_local)
    return queued


def _class_in_property_domain(
    parsed: dict[str, Any], class_local: str, property_local: str
) -> bool:
    """True when class_local or an ancestor is in the property domain."""
    spec = (parsed.get("properties") or {}).get(property_local) or {}
    domains = {
        str(value).strip()
        for value in (spec.get("domains") or [spec.get("domain")])
        if str(value or "").strip()
    }
    if not domains:
        return False
    return bool(_class_and_parent_locals(parsed, class_local) & domains)


def _linked_class_datatype_property_locals(
    parsed: dict[str, Any], class_local: str
) -> list[str]:
    """Datatype property locals declared on a parsed class, already inherited."""
    cls = (parsed.get("classes") or {}).get(class_local) or {}
    return sorted(
        str(name).strip()
        for name in (cls.get("datatype_properties") or {})
        if str(name).strip()
    )


def _nested_owned_dependent_scalar_contract(
    context: AgenticGenerationContext,
    iteration_owned_scope: dict[str, Any],
) -> list[dict[str, Any]]:
    """Scalars of a linked range class that must be emitted under the owner occurrence.

    Built from this iteration's owned object properties whose range is a
    linked materialization class (not itself an occurrence in this iteration).
    Compiled same-operation owned_dependent edges, when present, are merged
    in. No application ontology names are hardcoded.
    """
    parsed = getattr(context, "parsed", {}) or {}
    owned_classes = {
        str(value).strip()
        for value in iteration_owned_scope.get("classes") or []
        if str(value).strip()
    }
    linked_classes = {
        str(value).strip()
        for value in iteration_owned_scope.get("linked_materialization_classes")
        or []
        if str(value).strip()
    }
    owned_object_properties = {
        str(value).strip()
        for value in iteration_owned_scope.get("object_properties") or []
        if str(value).strip()
    }
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()

    def _append_row(
        *,
        owner_class_local: str,
        predicate_local: str,
        dependent_class_local: str,
        property_locals: list[str],
    ) -> None:
        locals_ = [
            str(name).strip()
            for name in property_locals
            if str(name).strip()
        ]
        key = (owner_class_local, predicate_local, dependent_class_local)
        if (
            not owner_class_local
            or not predicate_local
            or not dependent_class_local
            or not locals_
            or key in seen
        ):
            return
        seen.add(key)
        rows.append(
            {
                "owner_class_local": owner_class_local,
                "predicate_local": predicate_local,
                "dependent_class_local": dependent_class_local,
                "property_locals": list(dict.fromkeys(locals_)),
            }
        )

    try:
        creators = _owned_entity_tool_contracts(context)
    except ValueError:
        creators = []
    for creator in creators:
        owner_class = str(creator.get("class_local") or "").strip()
        if owner_class not in owned_classes:
            continue
        for edge in creator.get("required_edges") or []:
            if str(edge.get("role") or "") != "owned_dependent":
                continue
            if str(edge.get("target_resolution") or "") != "same_operation_create":
                continue
            _append_row(
                owner_class_local=owner_class,
                predicate_local=str(edge.get("predicate_local") or "").strip(),
                dependent_class_local=str(
                    edge.get("dependent_class_local") or ""
                ).strip(),
                property_locals=[
                    str(item.get("property_local") or "")
                    for item in edge.get("datatype_inputs") or []
                ],
            )

    if not linked_classes:
        return rows

    for predicate_local in sorted(owned_object_properties):
        spec = (parsed.get("properties") or {}).get(predicate_local) or {}
        if str(spec.get("kind") or "") != "object":
            continue
        range_local = str(spec.get("range") or "").strip()
        if range_local not in linked_classes or range_local in owned_classes:
            continue
        property_locals = _linked_class_datatype_property_locals(parsed, range_local)
        if not property_locals:
            continue
        for owner_class in sorted(owned_classes):
            if not _class_in_property_domain(parsed, owner_class, predicate_local):
                continue
            _append_row(
                owner_class_local=owner_class,
                predicate_local=predicate_local,
                dependent_class_local=range_local,
                property_locals=property_locals,
            )
    return rows


def _format_nested_owned_dependent_scalar_contract(
    rows: list[dict[str, Any]],
) -> str:
    """Render compiled nested-owner scalar attachments. Generic English only."""
    if not rows:
        return ""
    lines = [
        "Nested owned-dependent scalars (mechanically injected; this section "
        "overrides any closed scalar list in the LLM-authored file):",
        "- This iteration's semantic ledger emits one occurrence per owned owner "
        "class. A same-operation owned dependent is not a separate occurrence in "
        "this iteration.",
        "- When an owner occurrence asserts a listed object-role predicate, emit "
        "that predicate as a standalone property-local line with the source "
        "identity token (`predicate: <verbatim token>`).",
        "- Under that same owner occurrence, emit every source-supported dependent "
        "scalar as a standalone property-local line using the listed property local "
        "exactly (`property: <value>`). Prose must not be the sole carrier.",
        "- Do not leave those scalars only on a sibling occurrence of the "
        "dependent class. Downstream graph construction reads them from the owner "
        "occurrence.",
        "- Do not omit a listed property because an LLM-authored allowed-scalar "
        "list did not repeat it.",
        "",
        "Compiled owner attachments:",
    ]
    for row in rows:
        props = ", ".join(
            f"`{name}`" for name in row.get("property_locals") or [] if str(name)
        )
        lines.append(
            f"- Owner `{row.get('owner_class_local')}` via "
            f"`{row.get('predicate_local')}` to owned "
            f"`{row.get('dependent_class_local')}`: emit under the "
            f"`{row.get('owner_class_local')}` occurrence: {props}."
        )
    return "\n".join(lines)


def _semantic_scalar_output_contract(
    context: AgenticGenerationContext,
    iteration_owned_scope: dict[str, Any],
    *,
    nested_owned_dependent_scalar_contract: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Describe scalar facts that semantic-text paragraphs must preserve."""
    relevant_classes = {
        str(value)
        for value in (
            list(iteration_owned_scope.get("classes") or [])
            + list(
                iteration_owned_scope.get("linked_materialization_classes")
                or []
            )
        )
        if str(value)
    }
    formats = {
        "int": "<integer>",
        "bool": "true|false",
        "float": "<number>",
        "str": "<exact source string>",
    }
    by_property: dict[str, dict[str, Any]] = {}
    for creator in _owned_entity_tool_contracts(context):
        if str(creator.get("class_local") or "") not in relevant_classes:
            continue
        for datatype_input in creator.get("datatype_inputs") or []:
            property_local = str(datatype_input.get("property_local") or "")
            if not property_local:
                continue
            python_type = str(datatype_input.get("python_type") or "str")
            item = by_property.setdefault(
                property_local,
                {
                    "property_local": property_local,
                    "python_type": python_type,
                    "required": bool(datatype_input.get("required")),
                    "applicable_creator_tools": [],
                    "natural_language_requirement": (
                        f"Include {property_local} with its complete "
                        f"{formats.get(python_type, '<value>')} value as a standalone "
                        "property-local line under the owning occurrence "
                        f"(`{property_local}: {formats.get(python_type, '<value>')}`). "
                        "Prose must not be the sole carrier. Do not drop a "
                        "source-supported value."
                    ),
                },
            )
            item["required"] = bool(
                item["required"] or datatype_input.get("required")
            )
            item["applicable_creator_tools"].append(
                str(creator.get("public_tool") or "")
            )
    for row in nested_owned_dependent_scalar_contract or []:
        owner_class = str(row.get("owner_class_local") or "").strip()
        predicate = str(row.get("predicate_local") or "").strip()
        dependent_class = str(row.get("dependent_class_local") or "").strip()
        if not owner_class or not predicate:
            continue
        for property_local in row.get("property_locals") or []:
            name = str(property_local).strip()
            if not name:
                continue
            item = by_property.setdefault(
                name,
                {
                    "property_local": name,
                    "python_type": "str",
                    "required": False,
                    "applicable_creator_tools": [],
                    "natural_language_requirement": "",
                },
            )
            attachments = item.setdefault("nested_owner_attachments", [])
            attachment = {
                "owner_class_local": owner_class,
                "predicate_local": predicate,
                "dependent_class_local": dependent_class,
            }
            if attachment not in attachments:
                attachments.append(attachment)
            python_type = str(item.get("python_type") or "str")
            value_shape = formats.get(python_type, "<value>")
            owners = list(
                dict.fromkeys(
                    str(entry.get("owner_class_local") or "")
                    for entry in attachments
                    if str(entry.get("owner_class_local") or "")
                )
            )
            owner_text = ", ".join(f"`{local}`" for local in owners)
            item["natural_language_requirement"] = (
                f"When an owner occurrence of {owner_text} asserts the listed "
                f"object-role predicate, emit {name} as a standalone "
                f"property-local line under that owner occurrence "
                f"(`{name}: {value_shape}`). Do not leave it only on a sibling "
                "occurrence of the dependent class. Prose must not be the sole "
                "carrier. Do not drop a source-supported value."
            )
    return list(by_property.values())


def _prompt_artifact_generation_contract(
    context: AgenticGenerationContext, target: Path
) -> dict[str, Any]:
    """Build an iteration-scoped, T-Box-derived contract for one runtime prompt."""
    iteration_spec = _prompt_iteration_spec(context, target)
    configured_inputs = iteration_spec.get("inputs") or {}
    is_extension = context.ontology.role == "extension"
    if target.name == "EXTRACTION_ITER_1.md" and not is_extension:
        runtime_slots = ["{paper_content}"]
        pipeline_top_entity = _iter1_pipeline_top_entity_contract(context)
        class_local = pipeline_top_entity["class_local"]
        class_spec = (context.parsed.get("classes") or {}).get(class_local) or {}
        tbox_scope = {
            "classes": {
                class_local: {
                    "iri": pipeline_top_entity["class_iri"],
                    "parent_classes": list(class_spec.get("parent_classes") or []),
                    "comment": str(class_spec.get("comment") or ""),
                }
            },
            "properties": {},
            "pipeline_selected_top_entity": pipeline_top_entity,
        }
        required_links: list[dict[str, Any]] = []
    else:
        runtime_slots = (
            list(EXTENSION_EXTRACTION_RUNTIME_SLOTS)
            if is_extension
            else [
                "{paper_content}",
                "{entity_label}",
                "{entity_uri}",
                "{accumulated_hints}",
            ]
        )
        tbox_scope = _prompt_tbox_slice(context, iteration_spec)
        required_links = context.contract.get("required_links") or []
        top_entity_local = str(
            (context.contract.get("top_entity") or {}).get("class_local") or ""
        ).strip()
        top_entity_spec = (context.parsed.get("classes") or {}).get(
            top_entity_local
        ) or {}
        if top_entity_local and top_entity_spec:
            tbox_scope["pipeline_top_entity_semantics"] = {
                "local": top_entity_local,
                "iri": str(top_entity_spec.get("iri") or ""),
                "comment": str(top_entity_spec.get("comment") or ""),
            }
        if is_extension:
            inherited_root = dict(context.contract.get("top_entity") or {})
            extension_focus = dict(context.contract.get("extension_focus") or {})
            tbox_scope["inherited_scoped_root"] = {
                "class_local": str(inherited_root.get("class_local") or ""),
                "class_iri": str(inherited_root.get("class_iri") or ""),
                "inherited_from_ontology": str(
                    inherited_root.get("inherited_from_ontology") or ""
                ),
                "reuse_required": True,
            }
            tbox_scope["extension_focus"] = extension_focus
    if isinstance(configured_inputs, dict) and configured_inputs.get("file_path"):
        runtime_slots.append("{iteration_input}")
    deterministic_property_contract: dict[str, Any] = {}
    normalized_ordering_properties: list[dict[str, Any]] = []
    if target.name.startswith(("EXTRACTION_ITER_", "PRE_EXTRACTION_ITER_")):
        iteration_number = iteration_spec.get("iteration_number")
        if iteration_number is None:
            iteration_number = (iteration_spec.get("parent_iteration") or {}).get(
                "iteration_number"
            )
        if iteration_number is not None:
            plan_path = (
                Path(context.output_root)
                / "iterations"
                / context.ontology.name
                / "iterations.json"
            )
            plan = (
                json.loads(plan_path.read_text(encoding="utf-8"))
                if plan_path.is_file()
                else getattr(context, "iteration_blueprint", {})
            )
            deterministic_property_contract = derive_iteration_property_contract(
                parsed=context.parsed,
                compiled_plan=plan,
                iteration_number=iteration_number,
            )
            property_locals = {
                str(item.get("local") or "")
                for item in deterministic_property_contract.get("properties") or []
                if isinstance(item, dict)
            }
            properties = context.parsed.get("properties") or {}
            normalized_ordering_properties = [
                {
                    "local": str(local),
                    "iri": str((properties.get(local) or {}).get("iri") or ""),
                    "range": str((properties.get(local) or {}).get("range") or ""),
                }
                for local in sorted(
                    set(
                        (context.integrity_profile or {}).get(
                            "single_valued_ordering_properties"
                        )
                        or []
                    )
                    & property_locals
                )
            ]
    evidence_accounting_contract = (
        {
            "schema_version": "generic-evidence-accounting.v1",
            "implementation_scope": "prompt_instructions_only",
            "mechanical_validator_or_script_required": False,
            "principle": (
                "Treat pre-extraction evidence as a closed ledger. Every in-scope "
                "evidence atom must have exactly one explicit disposition and must "
                "never be silently omitted."
            ),
            "pre_extraction": {
                "atomicity": (
                    "one owned operation per evidence atom. A clause-to-sequence member "
                    "required by subclass_decision_contract or tbox_scope is an owned "
                    "operation, not an operation-local context fact. When a matched "
                    "pattern lists several members, emit one evidence atom per member in "
                    "the stated order; the same verbatim span may be reused on each atom. "
                    "When a T-Box comment says one occurrence may carry several values or "
                    "links for one event, emit one atom for that event, not one atom per "
                    "value. Operation-local context facts that are not themselves sequence "
                    "members remain properties of the nearest source-supported operation "
                    "and must never become standalone atoms."
                ),
                "planning_protocol": [
                    {
                        "phase": "target_scope_resolution",
                        "rule": (
                            "The first span that names the target is an identity "
                            "anchor, not a start bound. Quote the complete producing "
                            "workflow before extracting or numbering any evidence: "
                            "any earlier same-source operations that later in-scope "
                            "sentences consume, the identifying span, and this "
                            "target's exclusive continuation. The identifying span "
                            "and exclusive continuation must be uniquely attributable "
                            "to the bound target-entity label from the runtime slots. "
                            "If one continuous passage produces a shared intermediate "
                            "and then names several distinct outcomes, copy the "
                            "unsplit prefix into this target and exclude sibling "
                            "exclusive continuations, headings, and product "
                            "identifiers even when they are adjacent or similarly named."
                        ),
                    },
                    {
                        "phase": "dependency_resolution",
                        "rule": (
                            "From target passages, discover every same-source dependency "
                            "on another passage or entity, locate each referenced source "
                            "scope, and record every explicit deletion, replacement, "
                            "insertion, or value override."
                        ),
                    },
                    {
                        "phase": "effective_workflow_planning",
                        "rule": (
                            "Construct the current target's effective evidence set by "
                            "copying referenced facts, applying all explicit "
                            "modifications, and retaining direct target facts. "
                            "Do not summarize or truncate referenced source evidence."
                        ),
                    },
                    {
                        "phase": "ledger_emission",
                        "rule": (
                            "Only after scope and dependencies are resolved, emit "
                            "atomic evidence in effective target-workflow order."
                        ),
                    },
                ],
                "early_numbering_forbidden": True,
                "early_completion_forbidden": True,
                "stable_ids": (
                    "assign E001, E002, ... in effective target-evidence order "
                    "after dependency modifications are applied"
                ),
                "verbatim_grounding_required": True,
                "normalized_output_ordering_properties": normalized_ordering_properties,
                "candidate_property_role_exclusions": ["normalized_output_ordering"],
                "ordering_cue_field": (
                    "Store verbatim sequence language only in ordering_cue. When the "
                    "same verbatim span is reused for several sequence-member atoms, "
                    "put the member-local cue in ordering_cue so each atom is distinct. "
                    "During PRE, never emit a property listed in "
                    "normalized_output_ordering_properties."
                ),
                "completion_obligations": [
                    "The current target's complete producing workflow has been "
                    "located and recorded, including any consumed same-source prefix.",
                    "Every same-source dependency from the target is resolved.",
                    "Every referenced source scope is scanned from beginning to end.",
                    "Every explicit modification has been applied or marked unresolved.",
                    "Every effective owned operation has one evidence atom.",
                    "Every matched clause-to-sequence member has its own evidence atom and "
                    "was not collapsed into another member's properties.",
                    "Identity-bearing product and in-document heading/anchor atoms are "
                    "grounded on the bound target-entity label, not a sibling procedure.",
                    "Every explicit operation-local context fact that is not itself a "
                    "sequence member is attached as a property to the nearest "
                    "source-supported compatible operation; no context-only or "
                    "empty-candidate evidence atom is emitted.",
                ],
                "fixed_json_schema": {
                    "scope_resolution": {
                        "target_evidence": [
                            "<verbatim complete producing-workflow span, not merely the first identifying mention>"
                        ],
                        "source_dependencies": [
                            {
                                "reference_quote": "<verbatim reference span>",
                                "referenced_source_scope": "<verbatim identifier>",
                                "modifications": ["<verbatim modification span>"],
                                "resolution": "resolved|unresolved",
                            }
                        ],
                        "completion_attestation": {
                            "target_located": True,
                            "all_references_resolved": True,
                            "all_modifications_applied": True,
                            "effective_workflow_complete": True,
                        },
                    },
                    "evidence": [
                        {
                            "evidence_id": "E001",
                            "source_order": 1,
                            "ordering_cue": "<verbatim sequence cue or empty string>",
                            "verbatim_quote": "<exact source span>",
                            "candidate_types": ["<T-Box class local>"],
                            "candidate_properties": {
                                "<T-Box property local>": "<verbatim value>"
                            },
                        }
                    ],
                },
            },
            "main_extraction": {
                "hint_schema": "ref-entity-relations.v1",
                "silent_omission_forbidden": True,
                "source_order_before_classification": True,
                "preserve_source_order": True,
                "fixed_json_schema": {
                    "entities": [
                        {
                            "ref": "<stable occurrence-local reference>",
                            "class": "<T-Box class local>",
                            "label": "<canonical source-grounded label>",
                            "datatype_properties": {
                                "<T-Box datatype property or approved lexical quantity hint local>": "<grounded literal>"
                            },
                        }
                    ],
                    "relations": [
                        {
                            "subject_ref": "<entity ref>",
                            "property": "<T-Box object property local>",
                            "object_ref": "<entity ref>",
                        }
                    ],
                },
                "type_fidelity": (
                    "Emit values using the T-Box range type; never substitute a "
                    "boolean or descriptive phrase for an integer. The only interchange "
                    "exception is generation_contract.lexical_quantity_hint_contract."
                ),
                "unresolved_policy": (
                    "Represent ambiguity explicitly as unresolved instead of dropping it."
                ),
                "source_dependency_transform": [
                    "copy referenced evidence in its original relative order",
                    "apply explicit deletions, replacements, and insertions",
                    "recompute the target's normalized ordering property when applicable",
                    "never summarize or silently compress referenced evidence",
                ],
                "final_self_audit": [
                    "Every emitted entity uses ref, class, label, and datatype_properties.",
                    "Every emitted object relation uses subject_ref, property, and object_ref.",
                    "Labels contain identity text only; scalar payload stays in datatype_properties.",
                    "Every relation endpoint resolves to an entity ref from this output or the "
                    "accumulated prior-hint identity registry.",
                    (
                        "Values for every normalized_output_ordering_properties entry "
                        "are unique and contiguous when that T-Box contract requires it."
                    ),
                    "All values conform to their T-Box range types.",
                ],
            },
        }
        if target.name.startswith(("EXTRACTION_ITER_", "PRE_EXTRACTION_ITER_"))
        else {}
    )
    if (
        target.name.startswith("EXTRACTION_ITER_")
        and iteration_spec.get("hint_representation") == "semantic-text.v1"
    ):
        evidence_accounting_contract["main_extraction"] = {
            "hint_schema": "semantic-text.v1",
            "required_heading": "SEMANTIC_HINTS_V1",
            "serialization_policy": (
                "SEMANTIC_HINTS_V1 natural-language semantic ledger: short subclass "
                "label, ordered sequence position when applicable, and source-supported "
                "scalars as standalone property-local lines under the owning "
                "occurrence. Prose must not be the sole carrier for those scalars. "
                "Object properties whose range class is not an iteration-owned "
                "occurrence, and whose domain is not an iteration-owned occurrence, "
                "are emitted once as a standalone property-local line immediately "
                "after the required heading when source-grounded. Do not emit JSON, "
                "RDF, refs, IRIs, quantity nodes, tool calls, or graph layout."
            ),
            "silent_omission_forbidden": True,
            "preserve_source_order": True,
            "final_self_audit": [
                "Every source-supported iteration-owned occurrence appears once in source order.",
                "Every matched clause-to-sequence member is its own occurrence and was not "
                "collapsed into another member's properties.",
                "Identity-bearing product and in-document heading/anchor occurrences use the "
                "bound target-entity label, not a sibling procedure.",
                "Every source-supported relation and contextual role required by the active "
                "T-Box comments appears under the correct occurrence.",
                "Exact quantity lexemes remain attached to the correct source-grounded occurrence.",
                "No graph serialization requirement has displaced semantic coverage.",
            ],
        }
    subclass_decision_contract = (
        _subclass_decision_contract(tbox_scope)
        if target.name.startswith(("EXTRACTION_ITER_", "PRE_EXTRACTION_ITER_"))
        else {}
    )
    warning_marked_tbox_contract = (
        _warning_marked_tbox_contract(tbox_scope)
        if target.name.startswith(("EXTRACTION_ITER_", "PRE_EXTRACTION_ITER_"))
        else {}
    )
    subclass_comment_projection = (
        _subclass_comment_projection(tbox_scope)
        if target.name.startswith(("EXTRACTION_ITER_", "PRE_EXTRACTION_ITER_"))
        else {}
    )
    if target.name.startswith(("EXTRACTION_ITER_", "PRE_EXTRACTION_ITER_")):
        lexical_quantity_hint_contract = _lexical_quantity_hint_contract(
            context,
            tbox_scope,
            role="extraction",
            hint_representation=str(
                iteration_spec.get("hint_representation") or ""
            ).strip(),
        )
    else:
        lexical_quantity_hint_contract = {}
    pre_extraction_candidate_type_contract = (
        _pre_extraction_candidate_type_contract(
            context,
            tbox_scope,
            subclass_decision_contract,
        )
        if target.name.startswith("PRE_EXTRACTION_ITER_")
        else {}
    )
    if target.name.startswith(("EXTRACTION_ITER_", "PRE_EXTRACTION_ITER_")):
        from src.extraction_prompt_generation.compile.materialization_closure import (
            creator_surface_class_locals,
        )

        iteration_owned_scope = _iteration_owned_scope(
            iteration_spec,
            materializable_class_locals=creator_surface_class_locals(context),
        )
    else:
        iteration_owned_scope = {}
    relationship_contracts_for_prompt = (
        context.contract.get("relationship_tool_contracts") or {}
    )
    relationship_target_contracts = {
        property_local: dict(spec)
        for property_local, spec in relationship_contracts_for_prompt.items()
        if property_local in set(iteration_owned_scope.get("object_properties") or [])
    }
    pipeline_required_link_contracts: list[dict[str, Any]] = []
    nested_owned_dependent_scalar_contract = (
        _nested_owned_dependent_scalar_contract(context, iteration_owned_scope)
        if target.name.startswith("EXTRACTION_ITER_")
        and iteration_spec.get("hint_representation") == "semantic-text.v1"
        else []
    )
    semantic_scalar_output_contract = (
        _semantic_scalar_output_contract(
            context,
            iteration_owned_scope,
            nested_owned_dependent_scalar_contract=(
                nested_owned_dependent_scalar_contract
            ),
        )
        if target.name.startswith("EXTRACTION_ITER_")
        and iteration_spec.get("hint_representation") == "semantic-text.v1"
        else []
    )
    current_iteration_number = float(iteration_spec.get("iteration_number") or 0)
    prior_hint_representations = [
        {
            "iteration_number": prior.get("iteration_number"),
            "hint_representation": str(
                prior.get("hint_representation") or "ref-entity-relations.v1"
            ),
        }
        for prior in (
            (getattr(context, "iteration_blueprint", {}) or {}).get("iterations")
            or []
        )
        if isinstance(prior, dict)
        and float(prior.get("iteration_number") or 0) < current_iteration_number
    ]

    return {
        "ontology_name": context.ontology.name,
        "prompt_artifact": target.name,
        "generic_pipeline_role": (
            {
                "role": "extension_scoped_extraction",
                "scope_policy": (
                    "Reuse the inherited upstream scoped root URI and label. "
                    "The entity_label/entity_uri slots identify that upstream root, not an "
                    "extension-focus instance. Never create, retype, or reinterpret that root."
                ),
                "required_sequence": [
                    "Use the supplied entity label and IRI only as inherited scope context.",
                    "Extract one or more source-supported extension-focus instances relevant to that scope.",
                    "Keep the inherited scope class distinct from the extension focus class.",
                ],
            }
            if is_extension
            else _generic_prompt_pipeline_role(target)
        ),
        "iteration_spec": iteration_spec,
        "accumulated_prior_hint_representations": prior_hint_representations,
        "iteration_owned_scope": iteration_owned_scope,
        "relationship_target_contracts": relationship_target_contracts,
        "nested_owned_dependent_scalar_contract": (
            nested_owned_dependent_scalar_contract
        ),
        "semantic_scalar_output_contract": semantic_scalar_output_contract,
        "tbox_scope": tbox_scope,
        "subclass_decision_contract": subclass_decision_contract,
        "warning_marked_tbox_contract": warning_marked_tbox_contract,
        "subclass_comment_projection": subclass_comment_projection,
        "lexical_quantity_hint_contract": lexical_quantity_hint_contract,
        "pre_extraction_candidate_type_contract": (
            pre_extraction_candidate_type_contract
        ),
        "deterministic_property_contract": deterministic_property_contract,
        "normalized_output_ordering_properties": normalized_ordering_properties,
        "evidence_accounting_contract": evidence_accounting_contract,
        "required_links": required_links,
        "pipeline_required_link_contracts": pipeline_required_link_contracts,
        "runtime_binding_contract": {
            "allowed_slots": runtime_slots,
            "llm_authored_slots": [
                slot
                for slot in runtime_slots
                if slot != "{accumulated_hints}"
                or target.name.startswith("PRE_EXTRACTION_ITER_")
            ],
            "mechanically_injected_slots": (
                ["{accumulated_hints}"]
                if target.name.startswith("EXTRACTION_ITER_")
                and "{accumulated_hints}" in runtime_slots
                else []
            ),
            "iteration_input_meaning": (
                "The content of iteration_spec.inputs.file_path for the current entity."
                if "{iteration_input}" in runtime_slots
                else ""
            ),
            "unknown_slots_forbidden": True,
        },
        "representation_policy": {
            "interchange_is_contract_bound": True,
            "required_hint_representation": (
                "closed-ledger.v1"
                if target.name.startswith("PRE_EXTRACTION_ITER_")
                else str(
                    iteration_spec.get("hint_representation")
                    or "ref-entity-relations.v1"
                )
            ),
            "do_not_invent_a_parallel_representation": True,
            "runtime_placeholders_must_be_preserved": True,
            "fixture_facts_must_not_be_prepopulated": True,
        },
    }
