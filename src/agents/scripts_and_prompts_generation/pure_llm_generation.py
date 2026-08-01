"""Plain-LLM generation and repair over isolated artifact candidates."""

from __future__ import annotations

import json
import hashlib
import importlib.util
import inspect
import os
import re
import sys
from pathlib import Path
import tempfile
import types
from typing import Any, Callable

from src.agents.scripts_and_prompts_generation.agentic_generation_context import (
    AgenticGenerationContext,
)
from src.agents.scripts_and_prompts_generation.agentic_generation_validation import (
    build_validation_report,
    validate_prompt_runtime_bindings,
)
from src.agents.scripts_and_prompts_generation.artifact_surface_contract import (
    derive_main_surface_contract,
)
from src.agents.scripts_and_prompts_generation.level1_code_repair import invoke_json
from src.agents.scripts_and_prompts_generation.repair_skill_catalog import (
    repair_skill_catalog,
    repair_skill_ids,
)
from src.agents.scripts_and_prompts_generation.llm_artifact_editor import (
    DEFAULT_EDIT_BACKEND,
    EditBackend,
    run_llm_artifact_editor,
)

# Legacy monkeypatch seam retained for older tests and downstream integrations.
run_llm_unified_diff_editor = run_llm_artifact_editor


MCP_CAPABILITY_SECURITY_CONTRACT = """
Capability-security contract for every generated or repaired MCP Python artifact:
- Treat package-local `_fixed_rdf_runtime.py` and `_relationship_contract.json` as
  read-only infrastructure compiled from the active T-Box.
- Generic low-level helpers may exist and may be used internally, including
  `add_object_property`, `add_object_triple`, `add_type`, `create_individual`,
  generic `_add_literal`/`_add_object`, and direct RDF graph operations.
- Never register or otherwise expose generic low-level mutation helpers to the Agent
  through the MCP tool registry, decorators, exported tool mappings, or list_tools.
- The Agent-visible MCP surface may expose only explicit lifecycle tools and
  T-Box-derived property/class-specific tools such as `create_<class>`,
  and `add_<predicate>`. Datatype properties are inputs of their domain
  `create_<class>` tools, not separate Agent-visible setters.
- Preserve public property/class-specific tool names and explicit
  FastMCP-publishable signatures.
- Use only symbols and semantics from the supplied T-Box-derived contract; never add
  fixture-specific or otherwise hard-coded domain knowledge.
""".strip()


_FIXED_RDF_RUNTIME_PUBLIC_API = (
    "new_graph",
    "retained_graph",
    "initialize_retained_graph",
    "safe_filename_component",
    "resolve_case_dirname",
    "scoped_memory_paths",
    "load_from_turtle_file",
    "package_relationship_capabilities",
    "package_entity_capabilities",
    "package_ordered_entity_capabilities",
    "package_om2_quantity_creator",
    "create_om2_quantity",
    "package_datatype_capabilities",
    "reset_graph",
    "serialize_turtle",
    "abox_graph",
    "export_graph_result",
    "success_result",
    "error_result",
    "result_json",
    "success_json",
    "error_json",
)


def _fixed_rdf_runtime_api_contract() -> dict[str, Any]:
    """Generate the prompt contract from the actual fixed runtime implementation."""
    from src.agents.scripts_and_prompts_generation import fixed_rdf_runtime

    api: dict[str, Any] = {}
    for name in _FIXED_RDF_RUNTIME_PUBLIC_API:
        value = getattr(fixed_rdf_runtime, name)
        api[name] = {
            "signature": str(inspect.signature(value)),
            "docstring": inspect.getdoc(value) or "",
        }
    return {
        "module_import": "from . import _fixed_rdf_runtime as rdf_runtime",
        "public_api": api,
        "capability_usage": {
            "entity": (
                "entity_capabilities = rdf_runtime.package_entity_capabilities(); "
                "creator = entity_capabilities[class_iri]; iri = creator(label)"
            ),
            "relationship": (
                "relationship_capabilities = "
                "rdf_runtime.package_relationship_capabilities(); "
                "writer = relationship_capabilities[predicate_iri]; "
                "result = writer(subject_iri, object_iri)"
            ),
            "datatype": (
                "datatype_capabilities = rdf_runtime.package_datatype_capabilities(); "
                "writer = datatype_capabilities[predicate_iri]; "
                "result = writer(subject_iri, value)"
            ),
        },
        "atomic_creator_prevalidation_example": {
            "purpose": (
                "Validate every public create_* input before the first bound mutator call, "
                "so one invalid datatype cannot leave a partially-created entity."
            ),
            "generic_pattern": (
                "def create_Class(label: str, order: int, *, optional_text: str | None = None, "
                "optional_flag: bool | None = None) -> str:\n"
                "    if not isinstance(label, str) or not label.strip(): return "
                "rdf_runtime.error_json(code='invalid-label', message='...')\n"
                "    if isinstance(order, bool) or not isinstance(order, int) or order < 1: "
                "return rdf_runtime.error_json(code='invalid-order', message='...')\n"
                "    if optional_text is not None and not isinstance(optional_text, str): return "
                "rdf_runtime.error_json(code='invalid-datatype', message='...')\n"
                "    if optional_flag is not None and not isinstance(optional_flag, bool): return "
                "rdf_runtime.error_json(code='invalid-datatype', message='...')\n"
                "    iri = ordered_creators[BOUND_CLASS_IRI](label, order)\n"
                "    if optional_text is not None: datatype_writers[BOUND_TEXT_IRI](iri, optional_text)\n"
                "    if optional_flag is not None: datatype_writers[BOUND_FLAG_IRI](iri, optional_flag)\n"
                "    return rdf_runtime.success_json(iri=iri, message='...')"
            ),
            "rules": [
                "Use only explicit parameters projected by owned_entity_tool_contracts.",
                "Validate all non-None datatype inputs before entity/order/datatype mutation.",
                "For integer inputs reject bool even though bool subclasses int in Python.",
                "Do not snapshot, clear, or restore the graph in generated code.",
                "Do not duplicate RDF domain/range or identity validation owned by fixed runtime.",
            ],
        },
        "prohibitions": [
            "Do not use getattr/hasattr to discover alternative mutation APIs.",
            "Do not catch TypeError to guess fixed-runtime signatures.",
            "Do not fall back to direct Graph.add or reimplement graph state, IRI minting, "
            "contract enforcement, Turtle serialization, load/reset/export, or envelopes.",
        ],
    }


def _artifact_role_contract(target: Path | None) -> dict[str, Any]:
    name = target.name if target is not None else ""
    if name.endswith("_creation_base.py"):
        return {
            "role": "minimal_fixed_runtime_module_adapter",
            "must": [
                "Import the fixed runtime through the exact module_import in fixed_runtime_api.",
                "Use the package-relative import exactly; never import through a repository-level "
                "`scripts` package or another absolute package alias.",
                "Expose the imported module under the single stable public alias `rdf_runtime`.",
                "Set __all__ to exactly [`rdf_runtime`].",
            ],
            "must_not": [
                "Define pass-through wrappers for individual fixed-runtime functions.",
                "Define or re-export create_<class> tools.",
                "Define or re-export add_<property> tools.",
                "Embed any T-Box class, property, domain, range, or datatype IRI.",
                "Implement graph mutation, IRI minting, validation, serialization, lifecycle, "
                "or result-envelope logic already supplied by the fixed runtime.",
            ],
        }
    if name.endswith("_creation_entities.py"):
        return {
            "role": "tbox_bound_entity_tools",
            "must": [
                "Define exactly one public tool for every and only every entry in "
                "owned_entity_tool_contracts.",
                "For every contract entry, expose `label: str`, followed by every entry in its "
                "`datatype_inputs` as an explicitly typed optional keyword parameter. For an "
                "ordered member, `order: int` is required rather than optional and is written "
                "atomically by package_ordered_entity_capabilities()[exact class_iri].",
                "For ordered creators, require order >= 1 in the public schema and call the "
                "fixed atomic creator once with label and order. Never create first and set "
                "order in a separate public tool call.",
                "After creating or reusing the entity, write each supplied non-ordering datatype "
                "input through the exact property writer in "
                "rdf_runtime.package_datatype_capabilities(). Omitted optional inputs write no "
                "triple. Never accept a caller-selected datatype predicate.",
                "Datatype-property completeness is closed over creator signatures: every "
                "`datatype_inputs` entry must occur on every applicable domain creator, with "
                "the T-Box-derived Python type and an implementation path to its exact writer.",
                "Before calling any bound entity, ordered-entity, or datatype mutator, validate "
                "label, required order, and every supplied datatype input using the projected "
                "Python types. If any validation fails, return rdf_runtime.error_json and leave "
                "the retained graph unchanged. Follow fixed_runtime_api."
                "atomic_creator_prevalidation_example as the generic pattern.",
                "Call the selected bound creator, then return "
                "rdf_runtime.success_json(iri=created_iri, message=...).",
                "When the contract indicates OM-2 relationship ranges, import and re-export "
                "`create_om2_quantity` directly from `._fixed_rdf_runtime`. Do not define a local "
                "wrapper. The fixed public adapter owns T-Box range enforcement and standard "
                "success/rejection envelopes.",
                "Preserve the fixed creator's entity identity contract: label is a required "
                "non-empty string, normalization strips surrounding whitespace, invalid labels "
                "are rejected before graph mutation, and repeated exact class plus normalized "
                "label calls reuse the same IRI.",
            ],
            "must_not": [
                "Expose creators for classes absent from owned_entity_tool_contracts, even if "
                "the fixed capability map contains referenced external or datatype classes.",
                "Accept a class IRI or caller-selected class parameter in ontology entity "
                "creators; the sole exception is the bounded `quantity_class_iri` parameter of "
                "`create_om2_quantity`.",
                "Implement OM-2 parsing, unit mapping, quantity graph mutation, or an unbounded "
                "OM-2 class creator in generated code.",
                "Locally define or wrap `create_om2_quantity`; its callable provenance must remain "
                "`_fixed_rdf_runtime`.",
                "Create object relationships or caller-selected/arbitrary datatype assertions. "
                "Only explicit datatype parameters projected in the current creator's "
                "`datatype_inputs` may be written.",
                "Reimplement IRI minting, rdf:type mutation, labels, or JSON serialization.",
                "Catch or convert fixed-runtime entity contract rejection into a success result.",
                "Use *args/**kwargs or wrapper signature metadata overrides.",
            ],
        }
    if name.endswith("_creation_relationships.py"):
        return {
            "role": "tbox_bound_object_property_tools",
            "must": [
                "Define explicit add_<predicate_local> signatures.",
                "Bind each tool to its exact T-Box predicate capability from "
                "package_relationship_capabilities().",
                "Declare one literal __all__ containing every and only the object-property adders.",
            ],
            "must_not": [
                "Define or expose any datatype-property setter. Datatype inputs belong directly "
                "to their T-Box domain entity creators.",
            ],
        }
    if name.endswith("_creation_checks.py"):
        return {
            "role": "read_only_tbox_entity_discovery_and_integrity_checks",
            "must": [
                "Define `check_ordered_members() -> str` and exactly one bounded "
                "`check_existing_<class_local>() -> str` for every entry in "
                "existing_entity_check_contracts.",
                "Each existing-entity check must read the retained graph, select subjects "
                "with the exact contract class IRI, and return a JSON success envelope with "
                "an `entities` list containing each matching IRI and all available rdfs:label "
                "values. This lets later iterations resolve real persisted IRIs before linking.",
                "Declare exactly one module-level literal `__all__` equal to "
                "expected_public_manifest in the same order; do not compute, append to, "
                "reassign, or omit this manifest.",
                "Read only rdf_runtime.retained_graph() and return a JSON string report.",
                "Validate every ordered member linked through the contract member predicates.",
                "Require exactly one positive integer order per member, unique and contiguous "
                "orders 1..N within each parent, no non-reusable member linked to multiple "
                "parents, and every parent-type-preserving member to carry each required "
                "explicit ancestor rdf:type.",
                "For contiguity, N is the count of all ordered members linked to that parent "
                "before excluding members with missing, duplicate, or invalid order values. "
                "Compare valid observed orders with set(range(1, N + 1)); never derive N from "
                "max(observed), len(unique observed orders), or only valid-order members. "
                "Example: three linked ordered members with orders 1, 2, and missing must emit "
                "both missing_order and non_contiguous_order.",
                "Return status `ok` only when there are no violations; otherwise return status "
                "`rejected` with a structured violations list.",
            ],
            "must_not": [
                "Mutate, repair, reorder, renumber, add, or remove any graph triple.",
                "Use Graph.add/remove/parse/update or any fixed-runtime mutation capability.",
                "Hard-code ontology IRIs outside ordered_check_contract and "
                "existing_entity_check_contracts.",
                "Define or export generic graph inspection, caller-selected class/predicate "
                "tools, or any check absent from expected_public_manifest.",
            ],
        }
    if name == "main.py":
        return {
            "role": "closed_world_mcp_surface_adapter",
            "must": [
                "Treat generation_contract.expected_mcp_tools as a closed-world allowlist: the "
                "registered FastMCP tool names must equal it exactly, with no missing or extra tool.",
                "Instantiate the real installed server with `from fastmcp import FastMCP` and "
                "`mcp = FastMCP(name=...)`; register tools only through `mcp.tool(name=...)(fn)`.",
                "Register every public callable listed in the literal __all__ manifests of the "
                "validated entities, relationships, and checks modules under its unchanged name.",
                "Define and register exactly the approved lifecycle tools in "
                "generation_contract.lifecycle_tools by importing the tested callables "
                "`init_memory` and `export_memory` from package-local `_fixed_rdf_runtime`; "
                "never reimplement lifecycle behavior in main.py.",
                "Keep imported modules, capability maps/factories, retained graph objects, and "
                "all implementation helpers private and absent from the MCP registry.",
                "Use package-relative imports and preserve the upstream callable signatures and "
                "structured JSON return envelopes.",
            ],
            "must_not": [
                "Discover tools by scanning globals(), dir(), callable(), prefixes, or arbitrary "
                "module attributes; registration must use only the supplied manifest-derived list.",
                "Define a custom registry, registry facade, tool dictionary, fake FastMCP class, "
                "or any `mcp` object other than an instance of the installed fastmcp.FastMCP.",
                "Register the fixed runtime module or any callable obtained directly from it.",
                "Expose a caller-selected RDF class, predicate, triple, graph, file loader, "
                "capability factory/map, reset/debug operation, module, or class.",
                "Expose unrestricted Turtle/file ingestion or return a mutable Graph object.",
                "Expose any aggregate hint materializer or batch orchestration tool; hint-to-tool "
                "orchestration belongs exclusively to the KG agent and its prompt.",
                "Silently omit an allowlisted tool or add a convenience tool outside the allowlist.",
            ],
        }
    if target is not None and target.suffix == ".md":
        return {
            "role": "runtime_prompt_template",
            "must": [
                "Keep the required runtime placeholders so the pipeline can inject source text, "
                "entity context, identifiers, or extracted hints at execution time.",
                "Implement only the current iteration or sub-iteration responsibility declared "
                "by generation_contract.iteration_spec; do not repeat the ontology-wide task.",
                "For KG_BUILDING_ITER_1, implement generation_contract.generic_pipeline_role "
                "exactly and bind its abstract root capability only through tbox_scope.top_entity.",
                "Use generation_contract.tbox_scope as the domain authority for this prompt and "
                "do not copy unrelated classes or properties merely because they exist globally.",
                "Ensure every domain-specific trigger, example, exclusion, disambiguation rule, "
                "and exception is directly supported by generation_contract.tbox_scope.",
                "Instruct the runtime agent to derive facts only from the injected source and the "
                "active T-Box-derived contract.",
                "Describe semantic responsibilities without imposing a fixture-specific output "
                "shape or requiring graph-isomorphic serialization.",
                "Accept any unambiguous representation that preserves the facts, ordering, "
                "provenance, and uncertainty needed by this iteration.",
            ],
            "must_not": [
                "Contain pre-populated A-Box individuals, source quotations, quantities, ordered "
                "members, products, or links; those belong to runtime output, not the template.",
                "Replace runtime placeholders with benchmark, fixture, or example facts.",
                "Be judged as a completed extraction or completed KG build before runtime inputs "
                "have been injected.",
                "Require canonical JSON, fixed section keys, or one serialization syntax as a "
                "condition of semantic correctness.",
                "Expand an iteration-specific prompt into a full ontology extraction checklist.",
                "Add domain-specific examples, exclusions, or scientific interpretation rules "
                "that are not present in generation_contract.tbox_scope.",
            ],
        }
    return {"role": "artifact_specific", "must": []}


def _owned_entity_tool_contracts(
    context: AgenticGenerationContext,
) -> list[dict[str, Any]]:
    """Describe every T-Box-contract class eligible for a bounded public creator."""
    parsed = getattr(context, "parsed", {}) or {}
    profile = context.contract.get("ordered_member_profile") or {}
    ordered_classes = {
        str(value).strip()
        for value in profile.get("ordered_member_classes") or []
        if str(value).strip()
    }
    ordering_properties = [
        str(value).strip()
        for value in profile.get("single_valued_ordering_properties") or []
        if str(value).strip()
    ]
    datatype_properties = (
        (context.contract.get("ontology_publish_contract") or {}).get(
            "datatype_properties"
        )
        or []
    )
    python_type_by_range = {
        "http://www.w3.org/2001/XMLSchema#string": "str",
        "http://www.w3.org/2001/XMLSchema#boolean": "bool",
        "http://www.w3.org/2001/XMLSchema#integer": "int",
        "http://www.w3.org/2001/XMLSchema#int": "int",
        "http://www.w3.org/2001/XMLSchema#double": "float",
        "http://www.w3.org/2001/XMLSchema#float": "float",
        "http://www.w3.org/2001/XMLSchema#decimal": "float",
    }
    subclass_closure = {
        str(item.get("class_iri") or ""): {
            str(value) for value in item.get("superclass_iris") or []
        }
        for item in (
            (context.contract.get("ontology_publish_contract") or {}).get(
                "subclass_closure"
            )
            or []
        )
        if str(item.get("class_iri") or "")
    }

    def local_name(iri: str) -> str:
        return iri.rsplit("#", 1)[-1].rsplit("/", 1)[-1].rsplit(":", 1)[-1]

    owned = [
        {
            "class_local": str(class_local),
            "class_iri": str(spec.get("iri") or ""),
            "public_tool": f"create_{class_local}",
            "fixed_capability_key": str(spec.get("iri") or ""),
            "parent_classes": list(spec.get("parent_classes") or []),
            "ordered_member": str(class_local) in ordered_classes,
            "ordering_property_local": (
                ordering_properties[0]
                if str(class_local) in ordered_classes
                and len(ordering_properties) == 1
                else ""
            ),
            "datatype_inputs": [
                {
                    "property_local": local_name(str(item.get("property_iri") or "")),
                    "property_iri": str(item.get("property_iri") or ""),
                    "range_iri": str((item.get("range_iris") or [""])[0]),
                    "python_type": python_type_by_range.get(
                        str((item.get("range_iris") or [""])[0]), "str"
                    ),
                    "required": (
                        str(class_local) in ordered_classes
                        and local_name(str(item.get("property_iri") or ""))
                        in ordering_properties
                    ),
                }
                for item in datatype_properties
                if (
                    str(spec.get("iri") or "")
                    in {str(value) for value in item.get("domain_iris") or []}
                    or bool(
                        subclass_closure.get(str(spec.get("iri") or ""), set())
                        & {str(value) for value in item.get("domain_iris") or []}
                    )
                )
            ],
        }
        for class_local, spec in sorted((parsed.get("classes") or {}).items())
        if str(class_local).strip() and str((spec or {}).get("iri") or "").strip()
    ]
    owned.extend(
        {
            "class_local": str((spec or {}).get("class_local") or ""),
            "class_iri": str((spec or {}).get("class_iri") or ""),
            "public_tool": str((spec or {}).get("tool_name") or ""),
            "fixed_capability_key": str((spec or {}).get("class_iri") or ""),
            "parent_classes": [],
            "ordered_member": False,
            "ordering_property_local": "",
            "datatype_inputs": [],
            "external_range_class": True,
        }
        for spec in context.contract.get("external_class_creators") or []
        if str((spec or {}).get("class_iri") or "").strip()
        and str((spec or {}).get("tool_name") or "").strip()
    )
    return owned


def _existing_entity_check_contracts(
    context: AgenticGenerationContext,
) -> list[dict[str, str]]:
    """Describe bounded read-only entity discovery tools from the active T-Box."""
    checks = [
        {
            "class_local": str(class_local),
            "class_iri": str(spec.get("iri") or ""),
            "public_tool": f"check_existing_{class_local}",
        }
        for class_local, spec in sorted(
            ((getattr(context, "parsed", {}) or {}).get("classes") or {}).items()
        )
        if str(class_local).strip()
        and str((spec or {}).get("iri") or "").strip()
        and str(class_local).isidentifier()
    ]
    checks.extend(
        {
            "class_local": str((spec or {}).get("class_local") or ""),
            "class_iri": str((spec or {}).get("class_iri") or ""),
            "public_tool": str((spec or {}).get("check_tool_name") or ""),
        }
        for spec in context.contract.get("external_class_creators") or []
        if str((spec or {}).get("class_iri") or "").strip()
        and str((spec or {}).get("check_tool_name") or "").strip()
    )
    return checks


def _artifact_dependency_constraints(targets: list[Path]) -> list[dict[str, str]]:
    """Return domain-independent architecture ordering constraints."""
    role_paths: dict[str, str] = {}
    for target in targets:
        relative = target.as_posix()
        if target.name.endswith("_creation_base.py"):
            role_paths["base"] = relative
        elif target.name.endswith("_creation_entities.py"):
            role_paths["entities"] = relative
        elif target.name.endswith("_creation_relationships.py"):
            role_paths["relationships"] = relative
        elif target.name.endswith("_creation_checks.py"):
            role_paths["checks"] = relative
        elif target.name == "main.py":
            role_paths["main"] = relative
    pairs = (
        ("base", "entities"),
        ("base", "relationships"),
        ("entities", "checks"),
        ("relationships", "checks"),
        ("checks", "main"),
    )
    return [
        {"before": role_paths[before], "after": role_paths[after]}
        for before, after in pairs
        if before in role_paths and after in role_paths
    ]


def _artifact_generation_contract(
    context: AgenticGenerationContext,
    target: Path | None,
) -> dict[str, Any]:
    """Project the T-Box contract to facts owned by the current artifact."""
    name = target.name if target is not None else ""
    if name.endswith("_creation_base.py"):
        return {
            "ontology_name": context.ontology.name,
            "scope": "domain-independent fixed-runtime adapter",
        }
    if name.endswith("_creation_entities.py"):
        return {
            "ontology_name": context.ontology.name,
            "owned_entity_tool_contracts": _owned_entity_tool_contracts(context),
            "om2_quantity_class_iris": sorted(
                {
                    str(range_iri)
                    for item in (
                        (
                            context.contract.get("ontology_publish_contract")
                            or {}
                        ).get("object_properties")
                        or []
                    )
                    for range_iri in item.get("range_iris") or []
                    if "ontology-of-units-of-measure.org/resource/om-2/"
                    in str(range_iri)
                }
            ),
        }
    if name.endswith("_creation_relationships.py"):
        return {
            "ontology_name": context.ontology.name,
            "relationship_tool_contracts": (
                context.contract.get("relationship_tool_contracts") or {}
            ),
            "external_class_creators": (
                context.contract.get("external_class_creators") or []
            ),
        }
    if name.endswith("_creation_checks.py"):
        existing_checks = _existing_entity_check_contracts(context)
        expected_manifest = [
            "check_ordered_members",
            *(item["public_tool"] for item in existing_checks),
        ]
        profile = context.contract.get("ordered_member_profile") or {}
        classes = context.parsed.get("classes") or {}
        properties = context.parsed.get("properties") or {}
        member_locals = list(
            profile.get("individually_linked_object_properties") or []
        )
        order_locals = list(
            profile.get("single_valued_ordering_properties") or []
        )
        return {
            "ontology_name": context.ontology.name,
            "existing_entity_check_contracts": existing_checks,
            "expected_public_manifest": expected_manifest,
            "ordered_check_contract": {
                "contiguity_algorithm": {
                    "member_count_basis": (
                        "Count every linked ordered member under the parent before filtering "
                        "missing, duplicate, or invalid order literals."
                    ),
                    "expected_orders": "set(range(1, linked_ordered_member_count + 1))",
                    "observed_orders": "set of valid positive integer order values",
                    "forbidden_shortcuts": [
                        "range(1, max(observed_orders) + 1)",
                        "range(1, len(observed_orders) + 1)",
                        "count only members with valid order literals",
                    ],
                    "generic_example": {
                        "linked_ordered_members": 3,
                        "order_values": [1, 2, None],
                        "required_violation_codes": [
                            "missing_order",
                            "non_contiguous_order",
                        ],
                    },
                },
                "member_link_predicates": [
                    {
                        "local": local,
                        "iri": str((properties.get(local) or {}).get("iri") or ""),
                    }
                    for local in member_locals
                ],
                "ordering_predicates": [
                    {
                        "local": local,
                        "iri": str((properties.get(local) or {}).get("iri") or ""),
                    }
                    for local in order_locals
                ],
                "ordered_classes": [
                    {
                        "local": local,
                        "iri": str((classes.get(local) or {}).get("iri") or ""),
                    }
                    for local in profile.get("ordered_member_classes") or []
                ],
                "non_reusable_class_iris": [
                    str((classes.get(local) or {}).get("iri") or "")
                    for local in profile.get("non_reusable_classes") or []
                ],
                "required_explicit_ancestor_types": {
                    str((classes.get(local) or {}).get("iri") or ""): [
                        str((classes.get(parent) or {}).get("iri") or "")
                        for parent in (classes.get(local) or {}).get(
                            "parent_classes"
                        )
                        or []
                    ]
                    for local in profile.get("parent_type_preserving_classes") or []
                },
                "violation_codes": [
                    "missing_order",
                    "multiple_orders",
                    "invalid_order",
                    "duplicate_order",
                    "non_contiguous_order",
                    "multiple_parents",
                    "missing_explicit_ancestor_type",
                ],
            },
        }
    if name == "main.py":
        surface = derive_main_surface_contract(context.scripts_dir)
        return {
            "ontology_name": context.ontology.name,
            **surface,
            "datatype_property_contracts": (
                (context.contract.get("ontology_publish_contract") or {}).get(
                    "datatype_properties"
                )
                or []
            ),
            "datatype_completeness_policy": {
                "all_paths_must_be_creator_inputs": True,
                "separate_datatype_setters_forbidden": True,
                "ordering_owner": "ordered entity creator",
                "semantic_review_required": True,
            },
        }
    if target is not None and target.suffix == ".md":
        return _prompt_artifact_generation_contract(context, target)
    return _generation_contract_projection(context)


def _prompt_iteration_spec(
    context: AgenticGenerationContext, target: Path
) -> dict[str, Any]:
    """Return the exact iteration/sub-iteration specification owned by a prompt."""
    plan_path = (
        Path(context.output_root)
        / "iterations"
        / context.ontology.name
        / "iterations.json"
    )
    try:
        plan = (
            json.loads(plan_path.read_text(encoding="utf-8"))
            if plan_path.is_file()
            else getattr(context, "iteration_blueprint", {})
        )
    except (OSError, json.JSONDecodeError):
        return {}

    stem = target.stem
    for iteration in plan.get("iterations") or []:
        if not isinstance(iteration, dict):
            continue
        iter_token = str(iteration.get("iteration_number") or "").replace(".", "_")
        candidates = {
            f"EXTRACTION_ITER_{iter_token}",
            f"KG_BUILDING_ITER_{iter_token}",
            f"PRE_EXTRACTION_ITER_{iter_token}",
        }
        if stem in candidates:
            return {
                key: value
                for key, value in iteration.items()
                if key not in {"sub_iterations"}
            }
        for sub_iteration in iteration.get("sub_iterations") or []:
            if not isinstance(sub_iteration, dict):
                continue
            sub_token = str(
                sub_iteration.get("iteration_number") or ""
            ).replace(".", "_")
            if stem == f"EXTRACTION_ITER_{sub_token}":
                return {
                    "parent_iteration": {
                        key: value
                        for key, value in iteration.items()
                        if key not in {"sub_iterations"}
                    },
                    "sub_iteration": dict(sub_iteration),
                }
    return {}


def _prompt_tbox_slice(
    context: AgenticGenerationContext, iteration_spec: dict[str, Any]
) -> dict[str, Any]:
    """Project T-Box symbols connected to the current iteration's declared scope."""
    parsed = getattr(context, "parsed", {}) or {}
    classes = parsed.get("classes") or {}
    properties = parsed.get("properties") or {}
    semantic_scope = iteration_spec.get("semantic_scope") or {}
    focus_classes = {
        str(item.get("local") or "").strip()
        for item in semantic_scope.get("classes") or []
        if isinstance(item, dict) and str(item.get("local") or "").strip()
    }
    focus_properties = {
        str(item.get("local") or "").strip()
        for item in semantic_scope.get("object_properties") or []
        if isinstance(item, dict) and str(item.get("local") or "").strip()
    }
    if not focus_classes:
        responsibilities = iteration_spec.get("responsibilities") or {}
        focus_classes = {
            str(local).strip()
            for local in responsibilities.get("classes") or []
            if str(local).strip()
        }
        focus_properties = {
            str(local).strip()
            for local in responsibilities.get("object_properties") or []
            if str(local).strip()
        }
    if not focus_classes:
        top_local = str(
            (context.contract.get("top_entity") or {}).get("class_local") or ""
        ).strip()
        if top_local:
            focus_classes.add(top_local)
    changed = True
    while changed:
        changed = False
        for local, spec in classes.items():
            parents = set((spec or {}).get("parent_classes") or [])
            if parents & focus_classes and local not in focus_classes:
                focus_classes.add(local)
                changed = True
    relevant_properties: dict[str, Any] = {}
    for local, spec in properties.items():
        domains = {
            str(value)
            for value in (
                (spec or {}).get("domains")
                or [(spec or {}).get("domain")]
            )
            if str(value or "").strip()
        }
        range_local = str((spec or {}).get("range") or "").strip()
        if (
            str(local) in focus_properties
            or domains & focus_classes
            or range_local in focus_classes
        ):
            relevant_properties[str(local)] = {
                "iri": str((spec or {}).get("iri") or ""),
                "kind": str((spec or {}).get("kind") or ""),
                "domains": sorted(domains),
                "range": range_local,
                "comment": str((spec or {}).get("comment") or ""),
            }
    return {
        "classes": {
            str(local): {
                "iri": str((classes.get(local) or {}).get("iri") or ""),
                "parent_classes": list(
                    (classes.get(local) or {}).get("parent_classes") or []
                ),
                "comment": str((classes.get(local) or {}).get("comment") or ""),
            }
            for local in sorted(focus_classes)
        },
        "properties": relevant_properties,
    }


def _iter1_pipeline_top_entity_contract(
    context: AgenticGenerationContext,
) -> dict[str, Any]:
    """Project the pipeline-selected top entity into Iteration 1 generation."""
    policies = getattr(
        context,
        "pipeline_runtime_policies",
        (context.contract.get("runtime_policies") or {}),
    )
    iter1_rules = (
        ((policies.get("iter1_top_entity_kg") or {}).get("prompt_rules") or {})
        if isinstance(policies, dict)
        else {}
    )
    extraction_policy = (
        (policies.get("top_entity_extraction") or {})
        if isinstance(policies, dict)
        else {}
    )
    configured_local = str(
        iter1_rules.get("top_level_entity_name") or ""
    ).strip()
    prefixes = [
        str(value).strip()
        for value in (extraction_policy.get("count_lines_starting_with") or [])
        if str(value).strip()
    ]
    if configured_local and prefixes and prefixes != [configured_local]:
        raise ValueError(
            "Iteration 1 pipeline top-entity policy conflicts with the "
            f"top-entity extraction line prefixes: {configured_local!r} vs {prefixes!r}"
        )
    selected_local = str(
        (context.contract.get("top_entity") or {}).get("class_local") or ""
    ).strip()
    class_local = (
        selected_local
        or configured_local
        or (prefixes[0] if len(prefixes) == 1 else "")
    )
    classes = context.parsed.get("classes") or {}
    class_spec = classes.get(class_local) or {}
    class_iri = str(class_spec.get("iri") or "").strip()
    if not class_local or not class_iri:
        raise ValueError(
            "Iteration 1 requires a pipeline-selected top entity that exists "
            f"in the active T-Box; got {class_local!r}"
        )
    return {
        "class_local": class_local,
        "class_iri": class_iri,
        "source": "pipeline_runtime_policy",
        "line_prefix": class_local,
        "identifier_code_regex": str(
            extraction_policy.get("identifier_code_regex") or ""
        ).strip(),
        "output_contract": (
            f"Return zero or more lines only in the exact form "
            f"`{class_local}-N [<source-supported label or identifier>]`. "
            "Do not return prose, a no-findings sentence, headings, or schema explanations."
        ),
        "empty_result_contract": (
            "If no source-supported top entity exists, return an empty response. "
            "Do not emit an explanatory sentence."
        ),
    }


def _generic_prompt_pipeline_role(target: Path) -> dict[str, Any]:
    """Return ontology-independent pipeline responsibilities for a prompt slot."""
    if target.name == "KG_BUILDING_ITER_1.md":
        return {
            "role": "top_entity_abox_materialization",
            "input_semantics": (
                "Upstream hints identify zero or more source-supported top entities."
            ),
            "required_sequence": [
                "Open or resume scoped retained memory.",
                "Read each source-supported root label from the upstream hints while taking the "
                "root class only from the active T-Box projection.",
                "Create or reuse one root individual for every source-supported root label.",
                "Do not create non-top entities or downstream relationship targets in this pass.",
                "Export retained memory as the final tool action.",
            ],
            "scope_policy": (
                "Use the runtime scope already configured by the orchestrator; never invent, "
                "derive, or hardcode a scope name in the prompt."
            ),
            "hint_policy": (
                "Hints need only provide source-supported labels. They must not be required to "
                "repeat the root class because that class is supplied by the T-Box projection."
            ),
            "hint_representation": (
                "Accept JSON labels and the pipeline's generic plain-text wrapper "
                "`<type-prefix>-<index> [<label>]`; for wrapped lines use only the bracketed "
                "payload as the label, never the whole routing line."
            ),
            "tool_binding_policy": (
                "Render the exact creator_tool supplied by tbox_scope.top_entity into the runtime "
                "prompt as a concrete tool name with active-T-Box provenance; never leave a "
                "tbox_scope reference for the runtime agent and never guess or select a creator "
                "by scanning create_* names."
            ),
            "identity_policy": (
                "Preserve one stable identity per normalized top-entity label and reuse an "
                "existing root when the generated tool reports one. Deduplicate equal normalized "
                "labels before invoking the creator."
            ),
            "empty_input_policy": (
                "Do not invent a root when no source-supported top-entity hint is present."
            ),
            "domain_neutrality": (
                "Do not enumerate domain-specific child types, relations, examples, or exclusions."
            ),
        }
    if target.name.startswith("KG_BUILDING_ITER_"):
        return {
            "role": "iteration_hint_materialization",
            "input_semantics": (
                "The runtime iteration_hints slot carries the extraction hints produced for this "
                "iteration and is the primary materialization authority."
            ),
            "required_sequence": [
                "Always open or resume scoped retained memory before checks or mutations.",
                "For potentially pre-existing hinted entities, use the exact T-Box-derived "
                "existing-entity checks supplied by the generated tool surface before creation.",
                "Create or reuse only entities present in the current iteration hints.",
                "Assert only current-iteration T-Box-compatible links justified by those hints.",
                "Export retained memory as the final tool action.",
            ],
            "rejection_policy": (
                "A rejected intermediate call may be corrected and continued; successful final "
                "export is the commit boundary."
            ),
            "domain_neutrality": (
                "All concrete classes, properties, checks, and creators must come from the active "
                "T-Box projection or generated tool surface."
            ),
        }
    return {
        "role": "iteration_scoped_runtime_prompt",
        "required_sequence": [],
    }


def _top_entity_tbox_projection(context: AgenticGenerationContext) -> dict[str, Any]:
    """Project only the active-T-Box root capability needed by the first KG pass."""
    top = context.contract.get("top_entity") or {}
    class_local = str(top.get("class_local") or "").strip()
    class_iri = str(top.get("class_iri") or "").strip()
    creator_suffix = re.sub(r"[^A-Za-z0-9_]", "_", class_local)
    return {
        "top_entity": {
            "class_local": class_local,
            "class_iri": class_iri,
            "creator_tool": f"create_{creator_suffix}" if creator_suffix else "",
            "allows_multiple_source_roots": bool(top.get("iter1_allows_multiple")),
            "reuse_scoped_root": bool(top.get("main_pass_reuses_scoped_root")),
            "source": "active_tbox_projection",
        }
    }


def _resolve_top_entity_from_tbox(
    context: AgenticGenerationContext, *, model_name: str
) -> dict[str, Any]:
    """Ask the LLM to select and justify the T-Box top entity when absent."""
    current = dict(context.contract.get("top_entity") or {})
    if str(current.get("class_local") or "").strip():
        return current
    classes = context.parsed.get("classes") or {}
    properties = context.parsed.get("properties") or {}
    prompt = (
        "You are selecting the single top-entity class for an ontology-driven extraction "
        "and KG pipeline. Infer it directly from the active T-Box: prefer the root entity "
        "whose outgoing properties organize the main downstream entities and process scope. "
        "Do not rely on a special top-role RDF annotation and do not use pipeline policy. "
        "Return JSON only with exactly: class_local, rationale, evidence. Evidence must be a "
        "non-empty list of exact class/property local names from the supplied T-Box that "
        "support the choice.\n\n"
        + json.dumps(
            {
                "ontology": context.ontology.name,
                "classes": {
                    local: {
                        "iri": (spec or {}).get("iri"),
                        "parent_classes": (spec or {}).get("parent_classes") or [],
                        "comment": (spec or {}).get("comment") or "",
                    }
                    for local, spec in classes.items()
                },
                "properties": {
                    local: {
                        "kind": (spec or {}).get("kind"),
                        "domains": (spec or {}).get("domains")
                        or [(spec or {}).get("domain")],
                        "range": (spec or {}).get("range"),
                        "comment": (spec or {}).get("comment") or "",
                    }
                    for local, spec in properties.items()
                },
            },
            ensure_ascii=False,
        )
    )
    result = invoke_json(
        model_name,
        prompt,
        timeout_seconds=300,
        max_attempts=3,
        provider_max_retries=0,
    ).data
    class_local = str(result.get("class_local") or "").strip()
    if class_local not in classes:
        raise ValueError(
            "top_entity_selection_invalid: selected class is absent from active T-Box: "
            f"{class_local!r}"
        )
    evidence = [
        str(value).strip()
        for value in result.get("evidence") or []
        if str(value).strip()
    ]
    allowed_evidence = set(classes) | set(properties)
    invalid_evidence = sorted(set(evidence) - allowed_evidence)
    if not evidence or invalid_evidence:
        raise ValueError(
            "top_entity_selection_invalid: evidence must contain active-T-Box local names; "
            f"invalid={invalid_evidence}"
        )
    selected = {
        "status": "known",
        "class_iri": str((classes.get(class_local) or {}).get("iri") or ""),
        "class_local": class_local,
        "source": "llm_tbox_semantic_selection",
        "rationale": str(result.get("rationale") or "").strip(),
        "evidence": evidence,
        "iter1_allows_multiple": bool(current.get("iter1_allows_multiple", True)),
        "main_pass_reuses_scoped_root": bool(
            current.get("main_pass_reuses_scoped_root", False)
        ),
    }
    if not selected["class_iri"] or not selected["rationale"]:
        raise ValueError(
            "top_entity_selection_invalid: T-Box selection requires class IRI and rationale"
        )
    context.contract["top_entity"] = selected
    return selected


def _prompt_artifact_generation_contract(
    context: AgenticGenerationContext, target: Path
) -> dict[str, Any]:
    """Build an iteration-scoped, T-Box-derived contract for one runtime prompt."""
    iteration_spec = _prompt_iteration_spec(context, target)
    configured_inputs = iteration_spec.get("inputs") or {}
    if target.name == "EXTRACTION_ITER_1.md":
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
    elif target.name == "KG_BUILDING_ITER_1.md":
        runtime_slots = ["{doi}", "{paper_content}", "{top_entities}"]
        tbox_scope = _top_entity_tbox_projection(context)
        required_links: list[dict[str, Any]] = []
    else:
        runtime_slots = (
            ["{iteration_hints}", "{doi}", "{entity_label}", "{entity_uri}"]
            if target.name.startswith("KG_BUILDING_ITER_")
            else ["{paper_content}", "{entity_label}", "{entity_uri}"]
        )
        tbox_scope = _prompt_tbox_slice(context, iteration_spec)
        required_links = context.contract.get("required_links") or []
    if (
        target.name != "KG_BUILDING_ITER_1.md"
        and not target.name.startswith("KG_BUILDING_ITER_")
        and isinstance(configured_inputs, dict)
        and configured_inputs.get("file_path")
    ):
        runtime_slots.append("{iteration_input}")
    return {
        "ontology_name": context.ontology.name,
        "prompt_artifact": target.name,
        "generic_pipeline_role": _generic_prompt_pipeline_role(target),
        "iteration_spec": iteration_spec,
        "tbox_scope": tbox_scope,
        "required_links": required_links,
        "runtime_binding_contract": {
            "allowed_slots": runtime_slots,
            "iteration_input_meaning": (
                "The content of iteration_spec.inputs.file_path for the current entity."
                if "{iteration_input}" in runtime_slots
                else ""
            ),
            "unknown_slots_forbidden": True,
        },
        "representation_policy": {
            "semantic_output_is_format_independent": True,
            "do_not_require_canonical_json_shape": True,
            "runtime_placeholders_must_be_preserved": True,
            "fixture_facts_must_not_be_prepopulated": True,
        },
    }


def _generation_contract_projection(
    context: AgenticGenerationContext,
) -> dict[str, Any]:
    """Return domain facts that may enter generation and repair prompts."""
    allowed_keys = (
        "ontology_name",
        "ttl_file",
        "namespace_uri",
        "contract_layers",
        "top_entity",
        "required_links",
        "ontology_publish_contract",
        "ordered_member_profile",
        "relationship_domain_contracts",
        "relationship_tool_contracts",
        "external_class_creators",
        "step_scoped_object_properties",
        "required_step_scoped_object_properties",
        "om2_quantity_properties",
        "ontology_symbol_locals",
    )
    return {
        key: context.contract.get(key)
        for key in allowed_keys
        if key in context.contract
    }


def _artifact_generation_guidance(target: Path | None) -> str:
    """Return only instructions relevant to the current artifact role."""
    name = target.name if target is not None else ""
    if name.endswith("_creation_base.py"):
        return (
            "Generate the minimal fixed-runtime module adapter described by "
            "artifact_role_contract. Do not implement ontology tools."
        )
    if name.endswith("_creation_entities.py"):
        return (
            "Generate only the public entity creators in owned_entity_tool_contracts. "
            "Treat that list as exhaustive: it may include exact external range/restriction "
            "classes authorized by the T-Box contract, while unlisted fixed-runtime capabilities "
            "remain private. Preserve fixed-runtime "
            "label validation, no-mutation-on-rejection, and class-plus-normalized-label identity "
            "reuse; do not reimplement or weaken these semantics. Project each creator's complete "
            "`datatype_inputs` list directly into its public signature. Non-ordering datatype "
            "inputs are optional keyword parameters with their exact Python types; `order` remains "
            "required for ordered creators. Apply the generic "
            "fixed_runtime_api.atomic_creator_prevalidation_example: validate every public input "
            "before the first mutator call, then create/reuse and send supplied values to exact "
            "writers from package_datatype_capabilities(). Invalid calls must return rejection "
            "with zero graph mutation. Do not generate public `set_<property>` tools."
        )
    if name.endswith("_creation_relationships.py"):
        return (
            "Generate only object-property tools from relationship_tool_contracts. Every public "
            "`add_<predicate_local>` must expose "
            "`object_iri: Annotated[str, Field(description=...)]`; its description must contain "
            "the exact phrases `absolute IRI` and `never a label/name/literal/plain text`, list "
            "every contract range local, and reference only contract creator_tools. Do not define "
            "or export datatype setters; datatype properties are owned by domain create_* inputs."
        )
    if name == "main.py":
        return (
            "Integrate only validated upstream artifacts. Register exactly and only the names in "
            "generation_contract.expected_mcp_tools; this is a closed-world equality contract, "
            "not a minimum required set. The domain-dependent portion of that list was read at "
            "generation time from this run's sibling literal __all__ manifests; do not infer or "
            "recompute it from domain conventions. Register those upstream functions explicitly "
            "without reflective discovery. You must use the installed framework exactly as "
            "`from fastmcp import FastMCP`, `mcp = FastMCP(name=...)`, and "
            "`mcp.tool(name='<exact-name>')(<callable>)`; never implement a custom MCP registry, "
            "facade, tool map, or replacement class. Every MCP-published wrapper must use "
            "explicit typed parameters because FastMCP rejects *args and **kwargs. Do not publish "
            "runtime callables, modules, Graph objects, loaders, factories, maps, internal "
            "helpers, aggregate hint materializers, or batch orchestration tools. The only "
            "fixed-runtime callables main.py may publish are the approved imported lifecycle "
            "tools `init_memory` and `export_memory`. "
            "`init_memory` is an idempotent open-or-resume adapter over fixed runtime "
            "infrastructure. It must never expose reset, replace, clear, or caller-selected mode "
            "parameters, and repeated calls must not erase successful mutations. It may resume "
            "only the canonical persisted artifact derived internally from its DOI and entity "
            "scope. `export_memory` must use "
            "the fixed runtime A-Box projection by default; embedded T-Box/schema triples may be "
            "included only through an explicit non-default internal choice. "
            "The absence of an extra tool is part of correctness."
        )
    if name.endswith("_creation_checks.py"):
        return (
            "Generate only the read-only ordered-member integrity checker described by "
            "ordered_check_contract. Its output must identify parent/member/order evidence for "
            "every violation and it must leave the retained graph byte-for-byte unchanged. "
            "Implement ordered_check_contract.contiguity_algorithm literally: first collect all "
            "linked ordered members per parent, let N be that full count, then compare valid "
            "observed order values with 1..N. A missing or invalid order member still contributes "
            "to N and can therefore require both its local violation and non_contiguous_order."
        )
    if name.startswith("EXTRACTION_ITER_") and name.endswith(".md"):
        if name == "EXTRACTION_ITER_1.md":
            return (
                "Identify source-supported pipeline-selected top entities and return only the "
                "configured normalized top-entity lines. The output line protocol is a runtime "
                "contract, not an optional serialization preference."
            )
        return (
            "Extract source-grounded semantics allowed by the active T-Box. Do not prescribe or "
            "require one serialization shape: JSON, text, nested, flat, IDs, labels, and references "
            "are all acceptable when their meaning is unambiguous. Include every required runtime "
            "input slot, but never turn a representation convention into a semantic requirement."
        )
    if name.startswith("KG_BUILDING_") and name.endswith(".md"):
        return (
            "This is a runtime agent prompt and must explicitly require the supplied MCP tools "
            "for graph mutation, lifecycle handling, and export."
        )
    return "Generate only the artifact described by artifact_role_contract."


def _main_read_only_dependency_context(
    context: AgenticGenerationContext,
    target: Path | None,
) -> dict[str, Any]:
    """Expose validated sibling APIs to main generation without making them editable."""
    if target is None or target.name != "main.py":
        return {}
    surface = derive_main_surface_contract(context.scripts_dir)
    root = Path(context.scripts_dir)
    dependencies: dict[str, Any] = {}
    for owner in sorted(set(surface["tool_owners"].values())):
        path = root / owner
        module_contract = {
            name: {
                "signature": str(inspect.signature(value)),
                "docstring": inspect.getdoc(value) or "",
            }
            for name, value in _import_public_manifest_callables(path).items()
        }
        dependencies[owner] = {
            "source": path.read_text(encoding="utf-8", errors="replace"),
            "public_callables": module_contract,
        }
    base_matches = sorted(root.glob("*_creation_base.py"))
    if len(base_matches) == 1:
        base = base_matches[0]
        dependencies[base.name] = {
            "source": base.read_text(encoding="utf-8", errors="replace"),
            "public_callables": {},
        }
    return {
        "status": "read_only",
        "instruction": (
            "These are frozen, already validated artifacts from this run. Import and call "
            "their manifest-listed functions exactly as shown. They are evidence, not editable "
            "targets. Public entity and relationship functions return serialized JSON envelopes; "
            "parse an envelope before using its iri field internally."
        ),
        "artifacts": dependencies,
    }


def _import_public_manifest_callables(path: Path) -> dict[str, Callable[..., Any]]:
    """Import one generated sibling and return only its explicit public manifest."""
    package_name = f"_main_prompt_context_{abs(hash(str(path.parent.resolve())))}"
    for name in list(sys.modules):
        if name == package_name or name.startswith(package_name + "."):
            del sys.modules[name]
    package = types.ModuleType(package_name)
    package.__path__ = [str(path.parent.resolve())]  # type: ignore[attr-defined]
    sys.modules[package_name] = package
    module_name = f"{package_name}.{path.stem}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not import read-only dependency {path.name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
        manifest = getattr(module, "__all__", None)
        if not isinstance(manifest, list):
            raise ValueError(f"{path.name}: __all__ must be a list")
        return {
            name: value
            for name in manifest
            if callable(value := getattr(module, name, None))
        }
    finally:
        for name in list(sys.modules):
            if name == package_name or name.startswith(package_name + "."):
                del sys.modules[name]


def _with_mcp_capability_security(prompt: str) -> str:
    """Attach the production capability contract to every script-editing LLM task."""
    return prompt.rstrip() + "\n\n" + MCP_CAPABILITY_SECURITY_CONTRACT + "\n"


def _validation_outcome(
    mechanical: dict[str, Any],
    *,
    accepted: bool,
    rejection_failure: str,
    delta_review: dict[str, Any],
) -> dict[str, Any]:
    """Ensure reviewer acceptance overrides stale mechanical status fields."""
    return {
        **mechanical,
        "ok": accepted,
        "failures": [] if accepted else [rejection_failure],
        "delta_review": delta_review,
    }


def _editable_artifacts(
    context: AgenticGenerationContext,
    *,
    generate_scripts: bool,
    generate_prompts: bool,
) -> list[Path]:
    targets: list[Path] = []
    if generate_scripts:
        targets.extend(
            path
            for path in sorted(Path(context.scripts_dir).glob("*.py"))
            if path.name != "__init__.py"
            and path.name not in {"_fixed_om2_runtime.py", "_fixed_rdf_runtime.py"}
            and not path.name.startswith("main_part_")
            and "_attempt_" not in path.name
        )
    if generate_prompts:
        targets.extend(sorted(Path(context.prompts_dir).glob("*.md")))
    return targets


def _inspection_artifacts(
    context: AgenticGenerationContext, editable_targets: list[Path]
) -> list[Path]:
    """Return readable package evidence without expanding patch permissions."""
    readable = list(editable_targets)
    readable.extend(sorted(Path(context.scripts_dir).glob("*.py")))
    readable.extend(sorted(Path(context.prompts_dir).glob("*.md")))
    return list(dict.fromkeys(path.resolve() for path in readable if path.is_file()))


def _generation_task(
    *,
    context: AgenticGenerationContext,
    report: dict[str, Any],
    round_index: int,
    generate_scripts: bool,
    generate_prompts: bool,
    target: Path | None = None,
) -> str:
    is_prompt = target is not None and target.suffix == ".md"
    task = {
        "round": round_index,
        "ontology": {
            "name": context.ontology.name,
            "role": context.ontology.role,
            "ttl_file": context.ontology.ttl_file,
        },
        "generation_contract": _artifact_generation_contract(context, target),
        "fixed_runtime_api": {} if is_prompt else _fixed_rdf_runtime_api_contract(),
        "artifact_role_contract": _artifact_role_contract(target),
        "owned_entity_tool_contracts": (
            {} if is_prompt else _owned_entity_tool_contracts(context)
        ),
        "read_only_upstream_artifacts": _main_read_only_dependency_context(
            context, target
        ),
        "machine_validation": (
            _focused_validation_projection(
                report,
                None,
            )
            if is_prompt
            else report
        ),
        "requested_artifacts": {
            "scripts": generate_scripts,
            "prompts": generate_prompts,
            "current_target": (
                target.resolve()
                .relative_to(Path(context.output_root).resolve())
                .as_posix()
                if target is not None
                else ""
            ),
        },
    }
    prompt = (
        "You are the sole content decision-maker for generated ontology pipeline artifacts. "
        "Inspect every editable file, the T-Box-derived generation contract, and machine "
        "feedback. Return one unified diff that makes the smallest coherent set of changes "
        "needed to produce robust final scripts and prompts. Decide which editable files need "
        "changes yourself; do not rely on filename keyword routing. Preserve correct behavior. "
        "Use only T-Box and contract knowledge, never fixture-specific entities or values. "
        "For Python, use the package-local fixed runtimes only through the supplied "
        "fixed_runtime_api; both fixed files are read-only infrastructure. "
        "Python must remain formatted, lint-clean, syntactically valid, and package-compatible. "
        "Runtime prompts must preserve required data placeholders; those placeholders are "
        "intentional bindings, not TODO/template residue. A prompt's scope must exactly match "
        "generation_contract.iteration_spec and must not broaden into the ontology-wide task. "
        "Runtime binding slots must use single braces such as `{paper_content}`; never emit "
        "double-brace placeholder residue. "
        "You are generating the artifact through a plain LLM call and must not request tools "
        "while producing the edit payload. This restriction is meta-level only and must never "
        "appear in generated artifact content.\n\n"
        + _artifact_generation_guidance(target)
        + "\n\n"
        + json.dumps(task, ensure_ascii=False)
    )
    return (
        _with_mcp_capability_security(prompt)
        if generate_scripts
        else prompt
    )


def _fixed_artifact_dependency_order(
    *,
    root: Path,
    targets: list[Path],
) -> list[str]:
    """Apply the pipeline's fixed architecture order without semantic planning."""

    def order_key(path: Path) -> tuple[int, int, str]:
        name = path.name
        if name.endswith("_creation_base.py"):
            return (0, 0, name)
        if name.endswith("_creation_entities.py"):
            return (1, 0, name)
        if name.endswith("_creation_relationships.py"):
            return (2, 0, name)
        if name.endswith("_creation_checks.py"):
            return (3, 0, name)
        if name == "main.py":
            return (4, 0, name)
        match = re.fullmatch(r"EXTRACTION_ITER_(\d+)\.md", name)
        if match:
            return (5, int(match.group(1)), name)
        match = re.fullmatch(r"KG_BUILDING_ITER_(\d+)\.md", name)
        if match:
            return (6, int(match.group(1)), name)
        return (7, 0, path.as_posix())

    return [
        path.resolve().relative_to(root.resolve()).as_posix()
        for path in sorted(targets, key=order_key)
    ]


def _validate_generated_prompt(
    *,
    model_name: str,
    context: AgenticGenerationContext,
    target: Path,
    foreign_contracts: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    """Run mechanical gates, then one LLM semantic gate, for one prompt."""
    from src.agents.scripts_and_prompts_generation.semantic_script_review import (
        review_generated_prompt_semantics_with_llm,
    )

    relative = target.resolve().relative_to(Path(context.output_root).resolve()).as_posix()
    mechanical = build_validation_report(
        context,
        foreign_contracts=foreign_contracts,
        write_report=True,
        prompts_required=False,
        active_artifacts=[relative],
    )
    if not mechanical.get("stage_ok"):
        return {**mechanical, "ok": False, "semantic_review": None}
    semantic_review = review_generated_prompt_semantics_with_llm(
        context=context,
        artifact_path=target,
        model_name=model_name,
    )
    if semantic_review.get("decision") == "pass":
        return {
            **mechanical,
            "ok": True,
            "stage_ok": True,
            "semantic_review": semantic_review,
        }
    semantic_failure = (
        "LLM prompt semantic review requires repair:\n"
        + json.dumps(semantic_review, ensure_ascii=False)
    )
    semantic_observation = {
        "id": f"prompt:{target.name}#llm-semantic",
        "observation_id": f"prompt:{target.name}#llm-semantic",
        "status": "fail",
        "stage": "prompt_semantic",
        "message": semantic_review.get("summary") or semantic_failure,
        "observed_artifacts": [relative],
        "evidence": {
            "failures": semantic_review.get("critical_errors") or [],
            "semantic_review": semantic_review,
        },
    }
    return {
        **mechanical,
        "ok": False,
        "stage_ok": False,
        "failures": [*(mechanical.get("failures") or []), semantic_failure],
        "observations": [
            *(mechanical.get("observations") or []),
            semantic_observation,
        ],
        "semantic_review": semantic_review,
    }


def _validate_generated_prompt_hard_gates(target: Path) -> dict[str, Any]:
    """Validate prompt bindings and mechanical residue before semantic repair."""
    text = target.read_text(encoding="utf-8", errors="replace")
    binding = validate_prompt_runtime_bindings(target)
    failures = list(binding.get("failures") or [])
    residue = sorted(set(re.findall(r"TODO|FIXME|\{\{[^}\n]+\}\}", text, re.IGNORECASE)))
    if residue:
        failures.append(
            f"{target.name}: unresolved prompt placeholder/residue: "
            + ", ".join(residue[:8])
        )
    if not text.strip():
        failures.append(f"{target.name}: prompt artifact is empty")
    return {
        "ok": not failures,
        "failures": failures,
        "evidence": {
            "runtime_binding": binding.get("evidence") or {},
            "unresolved_residue": residue,
        },
    }


def _prompt_semantic_repair_task(
    *,
    context: AgenticGenerationContext,
    target: Path,
    report: dict[str, Any],
) -> str:
    """Build bounded T-Box-fidelity repair instructions for one frozen prompt."""
    relative = target.resolve().relative_to(Path(context.output_root).resolve()).as_posix()
    failures = [
        observation
        for observation in report.get("observations") or []
        if observation.get("status") == "fail"
    ]
    return (
        "Repair exactly one generated runtime prompt after its mechanical runtime-binding "
        "gates have passed. Preserve all required slots, current iteration scope, and valid "
        "instructions. Resolve only the supplied T-Box-fidelity or prompt-semantic failures. "
        "T-Box comments are binding: remove or rewrite instructions that enable a class or "
        "property whose comment forbids that use. Do not add fixture facts, source content, "
        "or new runtime slots. For every failure, use error as the defect, location as the "
        "only place to inspect, and known_correct_fix as the required correction. Do not "
        "invent fallbacks for an upstream-contract blocker; report it as non-repairable in "
        "this prompt. Return the smallest complete patch.\n\n"
        + json.dumps(
            {
                "target": relative,
                "prompt_generation_contract": _prompt_artifact_generation_contract(
                    context, target
                ),
                "active_tbox_scope": _prompt_tbox_slice(
                    context, _prompt_iteration_spec(context, target)
                ),
                "failing_observations": failures,
            },
            ensure_ascii=False,
        )
    )


def _repair_generated_prompt_semantics(
    *,
    model_name: str,
    context: AgenticGenerationContext,
    target: Path,
    foreign_contracts: list[dict[str, Any]] | None,
    report: dict[str, Any],
    edit_backend: EditBackend,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Repair one hard-gate-clean prompt against its targeted full validation evidence."""
    upstream_blockers = [
        failure
        for failure in report.get("failures") or []
        if "repairability=not repairable in the prompt file" in str(failure)
    ]
    if upstream_blockers:
        return report, {
            "ok": False,
            "failure_class": "upstream_contract",
            "repairability": "not_repairable_in_prompt",
            "failures": upstream_blockers,
            "feedback": {
                "error": "The prompt requires an upstream contract value that is absent.",
                "location": "context.contract.top_entity.class_local",
                "known_correct_fix": (
                    "Populate the top-entity class from the active T-Box and regenerate "
                    "KG_BUILDING_ITER_1.md so the concrete create_<class_local> tool is rendered."
                ),
            },
        }
    current_report = report

    def validate() -> dict[str, Any]:
        nonlocal current_report
        current_report = _validate_generated_prompt(
            model_name=model_name,
            context=context,
            target=target,
            foreign_contracts=foreign_contracts,
        )
        return current_report

    patch = run_llm_unified_diff_editor(
        model_name=model_name,
        output_root=Path(context.output_root),
        targets=[target],
        task_prompt=_prompt_semantic_repair_task(
            context=context,
            target=target,
            report=report,
        ),
        max_attempts=5,
        validate=validate,
        max_targets=1,
        progress=lambda message: print(f"[prompt_semantic_repair] {message}", flush=True),
        edit_backend=edit_backend,
    )
    return current_report if patch.get("ok") else report, patch


def _progress_summary(value: Any, *, limit: int = 50) -> str:
    """Render a concise, one-line progress objective for terminal output."""
    text = " ".join(str(value or "").split())
    return text[:limit] + ("…" if len(text) > limit else "")


def _progress_paths(paths: Any, *, limit: int = 3) -> str:
    """Render a bounded list of artifact basenames for terminal progress."""
    values = [Path(str(path)).name for path in (paths or [])]
    suffix = ",…" if len(values) > limit else ""
    return ",".join(values[:limit]) + suffix


def _progress_validation(report: dict[str, Any]) -> str:
    """Summarise stage review without misleadingly calling it full success."""
    observations = report.get("observations") or []
    statuses: dict[str, list[str]] = {}
    for observation in observations:
        if not isinstance(observation, dict):
            continue
        stage = str(observation.get("stage") or "unspecified")
        statuses.setdefault(stage, []).append(str(observation.get("status") or "unknown"))
    gates = ",".join(
        f"{stage}:{'fail' if 'fail' in values else 'pass'}"
        for stage, values in sorted(statuses.items())
    )
    failures = report.get("failures") or []
    failure_text = (
        _progress_summary(
            next(
                (
                    item.get("code") if isinstance(item, dict) else item
                    for item in failures
                ),
                "",
            )
        )
        if failures
        else ""
    )
    return (
        f"stage_ok={bool(report.get('stage_ok'))} "
        f"report_ok={bool(report.get('ok'))}"
        + (f" gates={gates}" if gates else "")
        + (f" first_failure={failure_text}" if failure_text else "")
    )


def _package_synthesis_task(
    *,
    context: AgenticGenerationContext,
    report: dict[str, Any],
) -> str:
    return _with_mcp_capability_security(
        (
        "You are the package integration LLM. All artifacts were generated independently and "
        "must now be coordinated as one executable ontology package. Return one unified diff "
        "across only the editable artifacts. Reconcile imports, MCP registration, create/add "
        "tool surfaces, materialization adapters, and shared prompt contracts. Generated code "
        "must import and use package-local fixed runtimes with explicit relative imports "
        "such as `from ._fixed_om2_runtime import ...`; never use the invalid top-level form "
        "`import _fixed_om2_runtime`. Use `from ._fixed_rdf_runtime import ...` for graph "
        "state, Turtle serialization, and export envelopes. Fixed files are infrastructure "
        "and are not editable. "
        "Do not require extraction hint field names to equal create_* parameter "
        "names: use an adapter when necessary and judge correctness by the final materialized "
        "KG. Resolve shared root causes rather than copying fixture values or implementing "
        "validator-specific no-op markers.\n\n"
        + json.dumps(
            {
                "ontology": context.ontology.name,
                "generation_contract": _generation_contract_projection(context),
                "package_validation": report,
            },
            ensure_ascii=False,
        )
        )
    )


def _runtime_adapter_synthesis_task(
    *,
    context: AgenticGenerationContext,
    report: dict[str, Any],
) -> str:
    return _with_mcp_capability_security(
        (
        "You are implementing only the package runtime adapter in main.py after sibling "
        "creation modules already exist. Return a unified diff for main.py. Import sibling "
        "modules with package-relative imports. Import `init_memory` and `export_memory` directly "
        "from `._fixed_rdf_runtime`; do not define lifecycle wrappers or lifecycle behavior in "
        "main.py. Register those two tested callables and every manifest-listed atomic "
        "create/add/check tool. Do not "
        "define or register an aggregate hint materializer or batch orchestration tool. "
        "The fixed lifecycle callables own canonical path mapping, idempotent resume, and "
        "A-Box-only persisted export. Use "
        "`from ._fixed_om2_runtime import ...` for fixed OM-2 infrastructure. Relationship "
        "tools must obtain property-specific writers from "
        "`package_relationship_capabilities()` and bind each `add_<predicate>` to its fixed "
        "predicate capability; generated code must never accept a caller-supplied predicate "
        "IRI, call `Graph.add`, or expose/recreate a generic triple/object-property writer. "
        "Entity tools must likewise obtain class-specific creators from "
        "`package_entity_capabilities()` and bind each `create_<class>` to its fixed class; "
        "generated code must never accept a caller-supplied class IRI or expose/recreate "
        "generic `create_individual`/`add_type` behavior. Datatype setters must obtain "
        "property-specific writers from `package_datatype_capabilities()`; generated code "
        "must never accept a caller-supplied datatype predicate or write literals through "
        "a generic `_add_literal`/graph mutation helper. A missing package capability is a "
        "structured fail-closed error, never permission to fall back to direct RDF mutation. "
        "Do not return a "
        "dict-only mock graph, no-op marker implementation, or fixture-specific values. "
        "Implement the current validation failures as runtime behavior, not strings.\n\n"
        + json.dumps(
            {
                "ontology": context.ontology.name,
                "generation_contract": _generation_contract_projection(context),
                "package_validation": report,
            },
            ensure_ascii=False,
        )
        )
    )


def _creation_foundation_synthesis_task(
    *,
    context: AgenticGenerationContext,
    report: dict[str, Any],
) -> str:
    return _with_mcp_capability_security(
        (
        "You are implementing the RDF creation foundation used by a later main.py runtime "
        "adapter. Modify only the selected base/entities/relationships/checks modules. Replace "
        "dict-command scaffolding with coherent rdflib-backed creation and linking behavior: "
        "shared GRAPH state, stable typed entity creation and reuse, object and datatype links, "
        "positive integer ordering, exact T-Box-derived preferred union domains, and any "
        "contract-authorized OM-2 integration through `from ._fixed_om2_runtime import ...`. Expose all "
        "T-Box-required create_* and add_* operations. Do not edit main.py, do not invent "
        "fixture values, and do not satisfy checks with no-op marker strings. Return one "
        "unified diff whose imports and public APIs form a dependency-complete foundation.\n\n"
        + json.dumps(
            {
                "ontology": context.ontology.name,
                "generation_contract": _generation_contract_projection(context),
                "package_validation": report,
            },
            ensure_ascii=False,
        )
        )
    )


def _failure_count(report: dict[str, Any]) -> int:
    return len(report.get("failures") or [])


def _observation_key(observation: dict[str, Any]) -> str:
    check_id = str(observation.get("check_id") or "").strip()
    subject_key = str(observation.get("subject_key") or "").strip()
    return f"{check_id}::{subject_key}" if subject_key else check_id


def _report_observations(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    observations = report.get("observations") or []
    if not isinstance(observations, list):
        raise ValueError("validation observations must be a list")
    indexed: dict[str, dict[str, Any]] = {}
    for raw in observations:
        if not isinstance(raw, dict):
            raise ValueError("validation observation must be an object")
        key = _observation_key(raw)
        if not key:
            raise ValueError("validation observation requires check_id")
        if key in indexed:
            raise ValueError(f"duplicate validation observation: {key}")
        status = str(raw.get("status") or "").strip().casefold()
        if status not in {"pass", "fail", "blocked", "not_run"}:
            raise ValueError(f"unsupported observation status for {key}: {status!r}")
        indexed[key] = {**raw, "status": status, "observation_id": key}
    return indexed


def _observation_transition_report(
    *,
    before_report: dict[str, Any],
    after_report: dict[str, Any],
    focus_observation_ids: list[str],
) -> dict[str, Any]:
    """Compare stable validator observations without interpreting messages."""
    before = _report_observations(before_report)
    after = _report_observations(after_report)
    focus_ids = list(dict.fromkeys(focus_observation_ids))
    unknown = sorted(set(focus_ids) - set(before))
    if unknown:
        raise ValueError(f"focus references unknown observations: {unknown}")

    resolved: list[str] = []
    progressed: list[str] = []
    persisting: list[str] = []
    newly_unmasked: list[str] = []
    regressions: list[str] = []
    missing: list[str] = []
    newly_failed: list[str] = []
    for key in sorted(set(before) | set(after)):
        before_item = before.get(key)
        after_item = after.get(key)
        before_status = (before_item or {}).get("status", "not_run")
        after_status = (after_item or {}).get("status", "not_run")
        if before_item is not None and after_item is None:
            missing.append(key)
            regressions.append(key)
            continue
        if before_item is None and after_status == "fail":
            newly_failed.append(key)
            regressions.append(key)
            continue
        if before_status == "pass" and after_status != "pass":
            regressions.append(key)
        if before_status in {"blocked", "not_run"} and after_status == "fail":
            newly_unmasked.append(key)
        if key in focus_ids:
            if before_status == "fail" and after_status == "pass":
                resolved.append(key)
            elif before_status in {"blocked", "not_run"} and after_status in {
                "pass",
                "fail",
            }:
                progressed.append(key)
            elif after_status != "pass":
                persisting.append(key)
    return {
        "resolved_observation_ids": resolved,
        "progressed_observation_ids": progressed,
        "persisting_focus_observation_ids": persisting,
        "newly_unmasked_observation_ids": newly_unmasked,
        "newly_failed_observation_ids": newly_failed,
        "missing_observation_ids": missing,
        "regression_observation_ids": regressions,
        "focus_progress": bool(resolved or progressed),
        "protected_regression": bool(regressions),
    }


def _is_strict_validation_improvement(
    before_failures: set[str],
    after_failures: set[str],
) -> bool:
    """Accept partial progress only when it removes failures without regressions."""
    return (
        len(after_failures) < len(before_failures)
        and not (after_failures - before_failures)
    )


def _request_repair_focus(
    *,
    model_name: str,
    context: AgenticGenerationContext,
    report: dict[str, Any],
    active_focus: dict[str, Any] | None,
    previous_steps: list[dict[str, Any]],
    max_target_files: int,
) -> dict[str, Any]:
    """Ask an LLM to select or resume one causal family of validator observations."""
    observations = _report_observations(report)
    failing = {
        key: item for key, item in observations.items() if item["status"] == "fail"
    }
    if not failing:
        return {
            "status": "complete",
            "focus_id": "",
            "observation_ids": [],
            "dependency_ids": [],
            "max_target_files": max_target_files,
        }
    prompt = (
        "You are the focused-repair scheduler. Select exactly one small causal family of "
        "failing validation observations, or resume the active focus when it is still ready. "
        "Prefer upstream blockers and local foundation obligations before global adapters and "
        "runtime semantics. Do not classify from message wording alone: use check IDs, stages, "
        "blocked_by metadata, observed artifacts, and evidence. Return JSON only:\n"
        '{"status":"selected|resume|defer|blocked","focus_id":"stable short id",'
        '"observation_ids":["exact failing observation id"],'
        '"dependency_ids":["exact observation id"],"objective":"...",'
        '"selection_reason":"...","completion_evidence":["..."],'
        '"repair_skill_ids":["selected generic golden skill id"],'
        '"max_target_files":1}\n'
        f"Choose one to four failing observation IDs and at most {max_target_files} target "
        "files for the later plan. Dependency IDs must reference known observations.\n\n"
        + json.dumps(
            {
                "ontology": context.ontology.name,
                "failing_observations": failing,
                "all_observations": observations,
                "golden_repair_skills": repair_skill_catalog(),
                "active_focus": active_focus,
                "previous_steps": _project_step_history(previous_steps),
            },
            ensure_ascii=False,
        )
    )
    response = invoke_json(
        model_name,
        prompt,
        timeout_seconds=300,
        max_attempts=3,
        provider_max_retries=0,
    )
    focus = response.data
    status = str(focus.get("status") or "").strip().casefold()
    if status not in {"selected", "resume", "defer", "blocked"}:
        raise ValueError(f"unsupported repair-focus status: {status!r}")
    selected = list(
        dict.fromkeys(str(value) for value in (focus.get("observation_ids") or []))
    )
    dependencies = list(
        dict.fromkeys(str(value) for value in (focus.get("dependency_ids") or []))
    )
    if status in {"selected", "resume"} and not 1 <= len(selected) <= 4:
        raise ValueError("repair focus must select one to four observations")
    invalid = sorted(set(selected) - set(failing))
    if invalid:
        raise ValueError(f"repair focus selected non-failing observations: {invalid}")
    invalid_dependencies = sorted(set(dependencies) - set(observations))
    if invalid_dependencies:
        raise ValueError(
            f"repair focus selected unknown dependencies: {invalid_dependencies}"
        )
    unready_dependencies = sorted(
        dependency_id
        for dependency_id in dependencies
        if observations[dependency_id]["status"] != "pass"
    )
    if status in {"selected", "resume"} and unready_dependencies:
        raise ValueError(
            f"repair focus has unready dependencies: {unready_dependencies}"
        )
    requested_max = int(focus.get("max_target_files") or max_target_files)
    if not 1 <= requested_max <= max_target_files:
        raise ValueError("repair focus exceeded max_target_files")
    selected_skills = list(
        dict.fromkeys(str(value) for value in (focus.get("repair_skill_ids") or []))
    )
    invalid_skills = sorted(set(selected_skills) - repair_skill_ids())
    if invalid_skills:
        raise ValueError(f"repair focus selected unknown skills: {invalid_skills}")
    focus.update(
        {
            "status": status,
            "observation_ids": selected,
            "dependency_ids": dependencies,
            "max_target_files": requested_max,
            "repair_skill_ids": selected_skills,
            "llm_call": {
                "elapsed_seconds": round(response.elapsed_seconds, 3),
                "token_usage": response.token_usage,
                "backend": "pure_llm_json",
            },
        }
    )
    return focus


def _project_step_history(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Project prior LLM decisions without recursively resending bulky artifacts."""
    projected: list[dict[str, Any]] = []
    for step in steps:
        scope = step.get("inspection_scope") or {}
        diagnosis = step.get("diagnosis") or {}
        plan = step.get("plan") or {}
        review = step.get("delta_review") or {}
        focus = step.get("focus") or {}
        projected.append(
            {
                "step_index": step.get("step_index"),
                "accepted": step.get("accepted"),
                "focus": {
                    "focus_id": focus.get("focus_id"),
                    "status": focus.get("status"),
                    "observation_ids": focus.get("observation_ids") or [],
                    "objective": focus.get("objective"),
                },
                "before_failure_count": step.get("before_failure_count"),
                "after_failure_count": step.get("after_failure_count"),
                "inspection": {
                    "question": scope.get("inspection_question"),
                    "paths": scope.get("inspect_paths") or [],
                    "hypotheses": scope.get("hypotheses") or [],
                },
                "diagnosis": {
                    "status": diagnosis.get("status"),
                    "confidence": diagnosis.get("confidence"),
                    "causal_findings": diagnosis.get("causal_findings") or [],
                    "unresolved_questions": diagnosis.get("unresolved_questions") or [],
                },
                "impact_plan": {
                    "status": plan.get("status"),
                    "objective": plan.get("objective"),
                    "targets": plan.get("targets") or [],
                    "dependency_order": plan.get("dependency_order") or [],
                    "alternative_to_rejected_strategies": plan.get(
                        "alternative_to_rejected_strategies"
                    ),
                },
                "delta_review": {
                    "decision": review.get("decision"),
                    "reason": review.get("reason"),
                    "resolved_or_improved": review.get("resolved_or_improved") or [],
                    "regressions": review.get("regressions") or [],
                    "next_evidence_needed": review.get("next_evidence_needed") or [],
                },
                "planning_failure": step.get("planning_failure"),
            }
        )
    return projected


def _focused_validation_projection(
    report: dict[str, Any],
    focus: dict[str, Any] | None,
    *,
    max_items: int = 8,
    max_text: int = 1200,
) -> dict[str, Any]:
    """Bound repair evidence to the selected observations and their direct blockers."""
    focus_ids = set((focus or {}).get("observation_ids") or [])
    observations = report.get("observations") or []
    selected = [
        observation
        for observation in observations
        if not focus_ids or _observation_key(observation) in focus_ids
    ]
    blocker_ids = {
        str(blocker)
        for observation in selected
        for blocker in observation.get("blocked_by") or []
    }
    selected.extend(
        observation
        for observation in observations
        if _observation_key(observation) in blocker_ids and observation not in selected
    )

    def clip(value: Any) -> str:
        text = str(value or "")
        return text if len(text) <= max_text else text[:max_text] + "…[truncated]"

    projected_observations = []
    for observation in selected[:max_items]:
        evidence = observation.get("evidence") or {}
        projected_observations.append(
            {
                "observation_id": _observation_key(observation),
                "status": observation.get("status"),
                "stage": observation.get("stage"),
                "message": clip(observation.get("message")),
                "failures": [
                    clip(item) for item in (evidence.get("failures") or [])[:max_items]
                ],
                "structured_evidence": {
                    key: value
                    for key, value in evidence.items()
                    if key
                    in {
                        "phase",
                        "contract_inputs",
                        "signature",
                        "missing_inputs",
                        "valid_call",
                        "invalid_call",
                        "repair_hint",
                        "target_artifact",
                    }
                },
                "observed_artifacts": observation.get("observed_artifacts") or [],
                "blocked_by": observation.get("blocked_by") or [],
            }
        )
    return {
        "failure_count": _failure_count(report),
        "focus_observations": projected_observations,
        "failure_summary": [
            clip(item) for item in (report.get("failures") or [])[:max_items]
        ],
    }


def _validate_selected_paths(
    values: Any,
    *,
    allowed: set[str],
    field: str,
    minimum: int,
    maximum: int,
) -> list[str]:
    if not isinstance(values, list):
        raise ValueError(f"{field} must be a list")
    selected = list(dict.fromkeys(str(item) for item in values))
    invalid = sorted(set(selected) - allowed)
    if invalid:
        raise ValueError(f"{field} selected paths outside inventory: {invalid}")
    if not minimum <= len(selected) <= maximum:
        raise ValueError(f"{field} must select {minimum} to {maximum} paths")
    return selected


def _request_inspection_scope(
    *,
    model_name: str,
    context: AgenticGenerationContext,
    targets: list[Path],
    report: dict[str, Any],
    step_index: int,
    previous_steps: list[dict[str, Any]],
    focus: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Let the LLM decide which artifacts must be inspected for causal diagnosis."""
    root = Path(context.output_root).resolve()
    editable = {path.resolve() for path in targets}
    inventory = [
        {
            "path": path.resolve().relative_to(root).as_posix(),
            "kind": "script" if path.suffix == ".py" else "prompt",
            "editable": path.resolve() in editable,
        }
        for path in _inspection_artifacts(context, targets)
    ]
    payload = {
        "step_index": step_index,
        "ontology": context.ontology.name,
        "artifact_inventory": inventory,
        "target_contracts": {
            target.resolve().relative_to(root).as_posix(): _artifact_generation_contract(
                context, target
            )
            for target in targets
        },
        "current_validation": _focused_validation_projection(report, focus),
        "active_focus": focus,
        "previous_steps": _project_step_history(previous_steps)[-3:],
    }
    prompt = (
        "You are the inspection planner for generated ontology pipeline artifacts. Based on "
        "the complete current validation evidence and prior attempted strategies, decide which "
        "files the causal diagnosis must read next. Do not infer ownership by filename alone: "
        "reason about runtime imports, tool registration, symbol definitions, prompt-to-tool "
        "dependencies, and generation contracts. Do not propose a patch yet. Return JSON only:\n"
        '{"status":"inspect|complete|blocked","inspection_question":"...",'
        '"inspect_paths":["exact inventory-relative path"],'
        '"hypotheses":[{"observation_ids":["exact active-focus observation id"],'
        '"possible_cause":"...",'
        '"evidence_needed":"..."}],"why_these_files":"..."}\n'
        "For inspect status choose one to six inventory paths. Read-only artifacts may be "
        "inspected as evidence but can never become patch targets. Hypotheses may reference only "
        "the active focus observations. For complete or blocked choose "
        "none. A previously rejected strategy may still justify inspecting the same files, but "
        "you must explicitly seek new causal evidence rather than paraphrasing the old plan.\n\n"
        + json.dumps(payload, ensure_ascii=False)
    )
    response = invoke_json(
        model_name,
        prompt,
        timeout_seconds=300,
        max_attempts=3,
        provider_max_retries=0,
    )
    scope = response.data
    status = str(scope.get("status") or "").strip().casefold()
    if status not in {"inspect", "complete", "blocked"}:
        raise ValueError(f"Unsupported inspection status: {status!r}")
    allowed = {item["path"] for item in inventory}
    selected = _validate_selected_paths(
        scope.get("inspect_paths") or [],
        allowed=allowed,
        field="inspect_paths",
        minimum=1 if status == "inspect" else 0,
        maximum=6 if status == "inspect" else 0,
    )
    scope["status"] = status
    scope["inspect_paths"] = selected
    if focus and status == "inspect":
        allowed_observations = set(focus.get("observation_ids") or [])
        referenced = {
            str(observation_id)
            for hypothesis in (scope.get("hypotheses") or [])
            if isinstance(hypothesis, dict)
            for observation_id in (hypothesis.get("observation_ids") or [])
        }
        invalid_observations = sorted(referenced - allowed_observations)
        if invalid_observations:
            raise ValueError(
                "inspection hypotheses escaped active focus: "
                f"{invalid_observations}"
            )
    scope["llm_call"] = {
        "elapsed_seconds": round(response.elapsed_seconds, 3),
        "token_usage": response.token_usage,
        "backend": "pure_llm_json",
    }
    return scope


def _request_causal_diagnosis(
    *,
    model_name: str,
    context: AgenticGenerationContext,
    targets: list[Path],
    report: dict[str, Any],
    inspection_scope: dict[str, Any],
    previous_steps: list[dict[str, Any]],
    focus: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Ask a dedicated LLM to locate causes and evidence without planning edits."""
    root = Path(context.output_root).resolve()
    allowed = {
        path.resolve().relative_to(root).as_posix(): path.resolve()
        for path in _inspection_artifacts(context, targets)
    }
    max_source_chars = 18000
    inspected = {
        relative: (
            lambda text: (
                text
                if len(text) <= max_source_chars
                else text[:max_source_chars] + "\n…[source truncated]"
            )
        )(allowed[relative].read_text(encoding="utf-8", errors="replace"))
        for relative in inspection_scope["inspect_paths"]
    }
    selected_skill_ids = set((focus or {}).get("repair_skill_ids") or [])
    selected_skills = [
        item
        for item in repair_skill_catalog()
        if str(item.get("skill_id") or "") in selected_skill_ids
    ]
    prompt = (
        "You are the causal diagnosis specialist. Read the selected artifact contents and trace "
        "the current validation failures to concrete code or prompt locations. Explain how "
        "each causal location affects imports, registered MCP tools, called functions, prompt "
        "contracts, and downstream validators. Do not choose repair targets or propose a patch; "
        "your only job is evidence-backed diagnosis. Return "
        "JSON only with exactly these keys:\n"
        '{"status":"diagnosed|insufficient_evidence",'
        '"causal_findings":[{"observation_ids":["exact active-focus observation id"],'
        '"source_path":"exact inspected path","symbols_or_sections":["..."],'
        '"cause":"...","evidence":"...","downstream_impact":'
        '[{"path":"inventory path","impact":"..."}]}],'
        '"unresolved_questions":["..."],"confidence":"high|medium|low"}\n'
        "Every finding must cite actual inspected content. If evidence is insufficient, report "
        "that explicitly rather than guessing ownership from filenames.\n\n"
        + json.dumps(
            {
                "ontology": context.ontology.name,
                "inspection_scope": inspection_scope,
                "active_focus": focus,
                "inspected_artifacts": inspected,
                "artifact_inventory": sorted(allowed),
                "target_contracts": {
                    target.resolve().relative_to(root).as_posix(): (
                        _artifact_generation_contract(context, target)
                    )
                    for target in targets
                },
                "golden_repair_skills": selected_skills,
                "current_validation": _focused_validation_projection(report, focus),
                "previous_steps": _project_step_history(previous_steps)[-3:],
            },
            ensure_ascii=False,
        )
    )
    response = invoke_json(
        model_name,
        prompt,
        timeout_seconds=300,
        max_attempts=3,
        provider_max_retries=0,
    )
    diagnosis = response.data
    status = str(diagnosis.get("status") or "").strip().casefold()
    if status not in {"diagnosed", "insufficient_evidence"}:
        raise ValueError(f"Unsupported causal-diagnosis status: {status!r}")
    findings = diagnosis.get("causal_findings")
    if status == "diagnosed" and not isinstance(findings, list):
        raise ValueError("Diagnosed result must include causal_findings")
    if status == "diagnosed":
        allowed_focus = set((focus or {}).get("observation_ids") or [])
        inspected_paths = set(inspection_scope.get("inspect_paths") or [])
        for finding in findings:
            if not isinstance(finding, dict):
                raise ValueError("causal finding must be an object")
            observation_ids = set(
                str(value) for value in (finding.get("observation_ids") or [])
            )
            if focus and (not observation_ids or not observation_ids <= allowed_focus):
                raise ValueError("causal finding escaped active focus")
            if str(finding.get("source_path") or "") not in inspected_paths:
                raise ValueError("causal finding source was not inspected")
    diagnosis["status"] = status
    diagnosis["llm_call"] = {
        "elapsed_seconds": round(response.elapsed_seconds, 3),
        "token_usage": response.token_usage,
        "backend": "pure_llm_json",
    }
    return diagnosis


def _request_impact_plan(
    *,
    model_name: str,
    context: AgenticGenerationContext,
    targets: list[Path],
    report: dict[str, Any],
    inspection_scope: dict[str, Any],
    diagnosis: dict[str, Any],
    previous_steps: list[dict[str, Any]],
    focus: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Ask a separate LLM to order dependencies and select a bounded patch step."""
    root = Path(context.output_root).resolve()
    allowed = {
        path.resolve().relative_to(root).as_posix(): path.resolve() for path in targets
    }
    selected_skill_ids = set((focus or {}).get("repair_skill_ids") or [])
    selected_skills = [
        item
        for item in repair_skill_catalog()
        if str(item.get("skill_id") or "") in selected_skill_ids
    ]
    prompt = (
        "You are the impact and dependency planner. Given an independent evidence-backed "
        "diagnosis, decide one bounded next repair step. Analyze how modifying each causal "
        "location can affect imports, callers, MCP tool registration, prompts, and validators. "
        "Do not write code or a diff. Return JSON only:\n"
        '{"status":"actionable|blocked","objective":"...",'
        '"dependency_order":["editable targets only, in patch order"],'
        '"impact_plan":[{"source_path":"...","change_intent":"...",'
        '"affected_paths":[{"path":"inventory path","expected_effect":"..."}]}],'
        '"targets":["bounded exact inventory paths"],'
        '"required_coedits":["editable paths that must change atomically"],'
        '"read_only_dependencies":["inspected paths that must not be edited"],'
        '"deferred_dependents":[{"path":"inventory path","interface_preservation":"..."}],'
        '"must_preserve":["..."],"acceptance_focus":["..."],'
        '"alternative_to_rejected_strategies":"..."}\n'
        "Targets must follow from the diagnosis and impact plan, not filename conventions. "
        "`dependency_order` is strictly the ordering of editable `targets`; put every inspected "
        "non-target dependency only in `read_only_dependencies`, never in `dependency_order`. "
        "Select the smallest dependency-complete step. A shared prompt or runtime contract may "
        "require coordinated edits across all affected files; do not split an atomic package "
        "repair merely to minimize target count. If the diagnosis is insufficient, "
        "return blocked with no targets.\n\n"
        + json.dumps(
            {
                "ontology": context.ontology.name,
                "artifact_inventory": sorted(allowed),
                "target_contracts": {
                    target.resolve().relative_to(root).as_posix(): (
                        _artifact_generation_contract(context, target)
                    )
                    for target in targets
                },
                "golden_repair_skills": selected_skills,
                "current_validation": _focused_validation_projection(report, focus),
                "active_focus": focus,
                "inspection_scope": inspection_scope,
                "causal_diagnosis": diagnosis,
                "previous_steps": _project_step_history(previous_steps)[-3:],
            },
            ensure_ascii=False,
        )
    )
    response = invoke_json(
        model_name,
        prompt,
        timeout_seconds=300,
        max_attempts=3,
        provider_max_retries=0,
    )
    plan = response.data
    status = str(plan.get("status") or "").strip().casefold()
    if status not in {"actionable", "blocked"}:
        raise ValueError(f"Unsupported impact-plan status: {status!r}")
    selected = _validate_selected_paths(
        plan.get("targets") or [],
        allowed=set(allowed),
        field="impact plan targets",
        minimum=1 if status == "actionable" else 0,
        maximum=(
            int((focus or {}).get("max_target_files") or 8)
            if status == "actionable"
            else 0
        ),
    )
    plan["status"] = status
    plan["targets"] = selected
    required_coedits = list(
        dict.fromkeys(str(value) for value in (plan.get("required_coedits") or []))
    )
    read_only_dependencies = list(
        dict.fromkeys(
            str(value) for value in (plan.get("read_only_dependencies") or [])
        )
    )
    invalid_coedits = sorted(set(required_coedits) - set(allowed))
    if invalid_coedits:
        raise ValueError(f"required co-edits outside editable inventory: {invalid_coedits}")
    missing_coedits = sorted(set(required_coedits) - set(selected))
    if missing_coedits:
        raise ValueError(f"impact plan omitted required co-edits: {missing_coedits}")
    edited_read_only = sorted(set(read_only_dependencies) & set(selected))
    if edited_read_only:
        raise ValueError(f"read-only dependencies selected for editing: {edited_read_only}")
    dependency_order = list(
        dict.fromkeys(str(value) for value in (plan.get("dependency_order") or []))
    )
    if set(dependency_order) - set(selected):
        raise ValueError("impact plan dependency order references unselected targets")
    plan["required_coedits"] = required_coedits
    plan["read_only_dependencies"] = read_only_dependencies
    plan["dependency_order"] = dependency_order
    plan["llm_call"] = {
        "elapsed_seconds": round(response.elapsed_seconds, 3),
        "token_usage": response.token_usage,
        "backend": "pure_llm_json",
    }
    return plan


def _artifact_hashes(paths: list[Path], root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for path in paths:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        result[path.resolve().relative_to(root.resolve()).as_posix()] = digest
    return result


def _write_integration_manifest(
    *,
    context: AgenticGenerationContext,
    targets: list[Path],
    model_name: str,
    max_targets: int,
    report: dict[str, Any],
    steps: list[dict[str, Any]],
    active_focus: dict[str, Any] | None,
    stop_reason: str,
) -> Path:
    root = Path(context.output_root).resolve()
    report_dir = root / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    path = report_dir / "focused_integration_checkpoint.json"
    payload = {
        "schema_version": 1,
        "effective_mode": "focused_package_integration",
        "ontology": context.ontology.name,
        "model": model_name,
        "contract_fingerprint": hashlib.sha256(
            json.dumps(context.contract, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest(),
        "artifact_inventory": sorted(_artifact_hashes(targets, root)),
        "artifact_hashes": _artifact_hashes(targets, root),
        "accepted_rounds": sum(bool(step.get("accepted")) for step in steps),
        "rounds": steps,
        "active_focus": active_focus,
        "validation": report,
        "globally_valid": bool(report.get("ok")),
        "stop_reason": stop_reason,
        "max_targets_per_round": max_targets,
    }
    fd, temp_name = tempfile.mkstemp(
        prefix=".focused-integration-", suffix=".json", dir=report_dir
    )
    os.close(fd)
    temp = Path(temp_name)
    try:
        temp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)
    return path


def _repair_task(
    *,
    context: AgenticGenerationContext,
    plan: dict[str, Any],
    report: dict[str, Any],
    focus: dict[str, Any] | None = None,
) -> str:
    return _with_mcp_capability_security(
        (
        "Implement exactly the LLM-planned repair step below as one unified diff. Stay within "
        "the selected files. Resolve the shared root cause, preserve the listed behavior, and "
        "do not attempt unrelated cleanup. Use only T-Box and generation-contract knowledge; "
        "never use fixture-specific entities or values. The candidate will be accepted only "
        "when the active focus makes validator-observed progress without a protected "
        "regression; unrelated cleanup is out of scope. Apply every skill selected in "
        "`active_focus.repair_skill_ids`, using its standard repairs and avoiding its "
        "anti-patterns. For RDF export failures, use the read-only package-local "
        "`._fixed_rdf_runtime`: materialize real semantic triples before export, then return "
        "its non-empty parseable Turtle payload. Empty-graph serialization, prefix-only Turtle, "
        "dummy triples, and custom serializer reimplementation are not valid repairs. For "
        "relationship parameter metadata, apply the corresponding per-property entry from "
        "`generation_contract.relationship_tool_contracts` directly: use the exact public "
        "`object_iri: Annotated[str, Field(description=...)]` parameter, import Annotated from "
        "typing and Field from pydantic without a fallback shim, include exact `absolute IRI` "
        "and `never a label/name/literal/plain text` phrases, exact range locals, and only the "
        "listed creator_tools. Do not preserve an incompatible target_iri API.\n\n"
        + json.dumps(
            {
                "plan": plan,
                "active_focus": focus,
                "golden_repair_skills": repair_skill_catalog(),
                "generation_contract": _generation_contract_projection(context),
                "current_validation": {
                    "failure_count": _failure_count(report),
                    "failures": list(report.get("failures") or []),
                    "feedback": report.get("feedback") or {},
                },
            },
            ensure_ascii=False,
        )
        )
    )


def _request_delta_review(
    *,
    model_name: str,
    plan: dict[str, Any],
    before_report: dict[str, Any],
    after_report: dict[str, Any],
    mechanical_validation: dict[str, Any],
) -> dict[str, Any]:
    """Ask an independent LLM whether the observed validation delta supports acceptance."""
    prompt = (
        "You are an independent repair delta reviewer. Compare machine validation before and "
        "after the patch against the impact plan and acceptance focus. Decide whether the patch "
        "made causal progress without unacceptable regression. Do not edit files or propose a "
        "new patch. Return JSON only:\n"
        '{"decision":"accept|reject|needs_more_evidence","reason":"...",'
        '"resolved_or_improved":["verbatim failures or outcomes"],'
        '"regressions":["new or worsened outcomes"],'
        '"next_evidence_needed":["..."]}\n'
        "Machine validation success is always acceptable. Do not use raw failure count or set "
        "difference alone. Determine whether a newly reported failure is an actual regression "
        "caused by the patch or a pre-existing downstream defect that only became observable "
        "because the patch unlocked imports, execution, or deeper validation. Reject actual "
        "regressions; an newly observable downstream defect may coexist with causal progress "
        "when the planned foundation defect was demonstrably resolved.\n\n"
        + json.dumps(
            {
                "impact_plan": plan,
                "before_validation": before_report,
                "after_validation": after_report,
                "mechanical_validation": mechanical_validation,
            },
            ensure_ascii=False,
        )
    )
    response = invoke_json(
        model_name,
        prompt,
        timeout_seconds=300,
        max_attempts=3,
        provider_max_retries=0,
    )
    review = response.data
    decision = str(review.get("decision") or "").strip().casefold()
    if decision not in {"accept", "reject", "needs_more_evidence"}:
        raise ValueError(f"Unsupported delta-review decision: {decision!r}")
    review["decision"] = decision
    review["llm_call"] = {
        "elapsed_seconds": round(response.elapsed_seconds, 3),
        "token_usage": response.token_usage,
        "backend": "pure_llm_json",
    }
    return review


def run_semantic_observation_repair(
    *,
    model_name: str,
    context: AgenticGenerationContext,
    diagnosis: dict[str, Any],
    before_semantic_report: dict[str, Any],
    validate_candidate: Callable[[], dict[str, Any]],
    edit_backend: EditBackend = DEFAULT_EDIT_BACKEND,
) -> dict[str, Any]:
    """Apply a diagnosis-selected semantic repair with review and rollback."""
    root = Path(context.output_root).resolve()
    targets = [Path(path).resolve() for path in diagnosis.get("target_artifacts") or []]
    if not targets or any(not path.is_relative_to(root) for path in targets):
        raise ValueError("semantic repair targets must be inside the generated package")
    plan = {
        "objective": diagnosis.get("summary") or "Resolve semantic A-Box observations",
        "targets": [path.relative_to(root).as_posix() for path in targets],
        "dependency_order": diagnosis.get("dependency_order") or [],
        "must_preserve": diagnosis.get("must_preserve") or [],
        "acceptance_focus": diagnosis.get("acceptance_evidence") or [],
        "causal_findings": diagnosis.get("causal_findings") or [],
        "repair_kind": diagnosis.get("repair_kind"),
    }
    candidate_evaluation: dict[str, Any] = {}
    delta_review: dict[str, Any] = {}

    def validate() -> dict[str, Any]:
        nonlocal candidate_evaluation, delta_review
        candidate_evaluation = validate_candidate()
        health_ok = bool(candidate_evaluation.get("health_ok"))
        semantic = candidate_evaluation.get("semantic_report") or {}
        before_acceptance = before_semantic_report.get("acceptance") or {}
        after_acceptance = semantic.get("acceptance") or {}
        before_score = float(before_acceptance.get("overall_score") or 0.0)
        after_score = float(after_acceptance.get("overall_score") or 0.0)
        before_dimensions = (
            (before_semantic_report.get("consensus") or {}).get("scores") or {}
        )
        after_dimensions = (semantic.get("consensus") or {}).get("scores") or {}
        regressed_dimensions = [
            name
            for name, value in before_dimensions.items()
            if float(after_dimensions.get(name) or 0.0) < float(value)
        ]
        mechanical = {
            "health_ok": health_ok,
            "before_score": before_score,
            "after_score": after_score,
            "score_improved": after_score > before_score,
            "semantic_accepted": bool(after_acceptance.get("accepted")),
            "regressed_dimensions": regressed_dimensions,
        }
        delta_review = _request_delta_review(
            model_name=model_name,
            plan=plan,
            before_report={"semantic_report": before_semantic_report},
            after_report={"semantic_report": semantic},
            mechanical_validation=mechanical,
        )
        accepted = (
            health_ok
            and not regressed_dimensions
            and delta_review.get("decision") == "accept"
            and (
                bool(after_acceptance.get("accepted"))
                or after_score > before_score
                or bool(delta_review.get("resolved_or_improved"))
            )
        )
        return _validation_outcome(
            mechanical,
            accepted=accepted,
            rejection_failure="semantic_repair_no_protected_progress",
            delta_review=delta_review,
        )

    patch = run_llm_unified_diff_editor(
        model_name=model_name,
        output_root=root,
        targets=targets,
        task_prompt=_with_mcp_capability_security(
            (
            "Implement the diagnosis-selected semantic repair. The semantic judge supplies "
            "evidence and acceptance criteria but does not prescribe code. Trace the diagnosed "
            "root cause and make the smallest dependency-complete edit. Use only generic "
            "T-Box/generation-contract rules; never encode fixture entities, values, or DOI. "
            "Preserve healthy behavior and do not edit outside the selected artifacts.\n\n"
            + json.dumps(
                {
                    "diagnosis": diagnosis,
                    "semantic_observations": before_semantic_report.get("observations") or [],
                    "generation_contract": _generation_contract_projection(context),
                    "golden_repair_skills": repair_skill_catalog(),
                },
                ensure_ascii=False,
            )
            )
        ),
        max_attempts=5,
        validate=validate,
        max_targets=len(targets),
        progress=lambda message: print(f"[semantic_repair] {message}", flush=True),
        edit_backend=edit_backend,
    )
    return {
        "ok": bool(patch.get("ok")),
        "plan": plan,
        "patch": patch,
        "candidate_evaluation": candidate_evaluation,
        "delta_review": delta_review,
    }


def _run_stage_focused_repair(
    *,
    model_name: str,
    context: AgenticGenerationContext,
    targets: list[Path],
    report: dict[str, Any],
    foreign_contracts: list[dict[str, Any]] | None,
    active_artifacts: list[str],
    max_focus_targets: int,
    edit_backend: EditBackend,
    semantic_validate: Callable[[], dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Run one bounded focused repair before generating the next artifact."""
    print(
        "[pure_llm] phase=repair-plan step=1/1 action=select-focus "
        f"targets={_progress_paths(targets)}",
        flush=True,
    )
    focus = _request_repair_focus(
        model_name=model_name,
        context=context,
        report=report,
        active_focus=None,
        previous_steps=[],
        max_target_files=max_focus_targets,
    )
    if focus["status"] not in {"selected", "resume"}:
        print(
            "[pure_llm] phase=repair-plan result=not-actionable "
            f"focus_status={focus['status']}",
            flush=True,
        )
        return report, {
            "focus": focus,
            "accepted": False,
            "stage_clean": bool(report.get("stage_ok")),
        }
    print(
        "[pure_llm] phase=repair-plan action=focus-selected "
        f"focus={focus.get('focus_id') or 'unnamed'} "
        f'objective="{_progress_summary(focus.get("objective"))}" '
        f"observations={len(focus['observation_ids'])}",
        flush=True,
    )
    print("[pure_llm] phase=repair-plan action=select-inspection-scope", flush=True)
    scope = _request_inspection_scope(
        model_name=model_name,
        context=context,
        targets=targets,
        report=report,
        step_index=1,
        previous_steps=[],
        focus=focus,
    )
    if scope["status"] != "inspect":
        print(
            "[pure_llm] phase=repair-plan result=not-actionable "
            f"inspection_status={scope['status']}",
            flush=True,
        )
        return report, {
            "focus": focus,
            "inspection_scope": scope,
            "accepted": False,
            "stage_clean": bool(report.get("stage_ok")),
        }
    print(
        "[pure_llm] phase=repair-plan action=inspect "
        f"files={_progress_paths(scope['inspect_paths'])}",
        flush=True,
    )
    print("[pure_llm] phase=repair-plan action=diagnose", flush=True)
    diagnosis = _request_causal_diagnosis(
        model_name=model_name,
        context=context,
        targets=targets,
        report=report,
        inspection_scope=scope,
        previous_steps=[],
        focus=focus,
    )
    print(
        "[pure_llm] phase=repair-plan action=diagnose-result "
        f"status={diagnosis['status']} findings={len(diagnosis.get('causal_findings') or [])}",
        flush=True,
    )
    print("[pure_llm] phase=repair-plan action=plan-impact", flush=True)
    plan = _request_impact_plan(
        model_name=model_name,
        context=context,
        targets=targets,
        report=report,
        inspection_scope=scope,
        diagnosis=diagnosis,
        previous_steps=[],
        focus=focus,
    )
    if plan["status"] != "actionable":
        print(
            "[pure_llm] phase=repair-plan result=not-actionable "
            f"plan_status={plan['status']}",
            flush=True,
        )
        return report, {
            "focus": focus,
            "inspection_scope": scope,
            "diagnosis": diagnosis,
            "plan": plan,
            "accepted": False,
            "stage_clean": bool(report.get("stage_ok")),
        }
    print(
        "[pure_llm] phase=repair-plan action=plan-ready "
        f'targets={_progress_paths(plan["targets"])} '
        f'objective="{_progress_summary(plan.get("objective"))}"',
        flush=True,
    )
    root = Path(context.output_root).resolve()
    target_by_relative = {
        path.resolve().relative_to(root).as_posix(): path for path in targets
    }
    selected_targets = [target_by_relative[path] for path in plan["targets"]]
    candidate_report: dict[str, Any] = {}
    observation_delta: dict[str, Any] = {}
    delta_review: dict[str, Any] = {}
    semantic_validation: dict[str, Any] = {}

    def validate() -> dict[str, Any]:
        nonlocal candidate_report, observation_delta, delta_review, semantic_validation
        candidate_report = build_validation_report(
            context,
            foreign_contracts=foreign_contracts,
            write_report=True,
            prompts_required=False,
            active_artifacts=active_artifacts,
        )
        print(
            "[pure_llm] phase=repair-review action=stage-validation "
            + _progress_validation(candidate_report),
            flush=True,
        )
        observation_delta = _observation_transition_report(
            before_report=report,
            after_report=candidate_report,
            focus_observation_ids=focus["observation_ids"],
        )
        mechanical = {
            **observation_delta,
            "before_failure_count": _failure_count(report),
            "after_failure_count": _failure_count(candidate_report),
            "after_stage_ok": bool(candidate_report.get("stage_ok")),
        }
        delta_review = _request_delta_review(
            model_name=model_name,
            plan=plan,
            before_report=report,
            after_report=candidate_report,
            mechanical_validation=mechanical,
        )
        print(
            "[pure_llm] phase=repair-review action=delta-review "
            f"decision={delta_review.get('decision') or 'unknown'} "
            f'reason="{_progress_summary(delta_review.get("reason"))}"',
            flush=True,
        )
        reviewer_progress = bool(
            delta_review.get("resolved_or_improved")
            or delta_review.get("newly_unmasked")
        )
        semantic_validation = semantic_validate() if semantic_validate else {}
        if semantic_validate:
            print(
                "[pure_llm] phase=repair-review action=semantic-review "
                f"decision={semantic_validation.get('decision') or 'unknown'} "
                f"critical_errors={len(semantic_validation.get('critical_errors') or [])}",
                flush=True,
            )
        semantic_resolved = bool(
            semantic_validate
            and semantic_validation.get("decision") == "pass"
            and not semantic_validation.get("critical_errors")
        )
        accepted = (
            not observation_delta["protected_regression"]
            and (
                observation_delta["focus_progress"]
                or semantic_resolved
                or (
                    delta_review["decision"] == "accept"
                    and reviewer_progress
                )
            )
        )
        return _validation_outcome(
            mechanical,
            accepted=accepted,
            rejection_failure="stage_focus_did_not_make_protected_progress",
            delta_review=delta_review,
        )

    patch = run_llm_unified_diff_editor(
        model_name=model_name,
        output_root=Path(context.output_root),
        targets=selected_targets,
        task_prompt=_repair_task(
            context=context,
            plan=plan,
            report=report,
            focus=focus,
        ),
        max_attempts=5,
        validate=validate,
        progress=lambda message: print(f"[pure_llm] {message}", flush=True),
        edit_backend=edit_backend,
    )
    accepted = bool(patch.get("ok"))
    print(
        "[pure_llm] phase=repair result="
        f"{'accepted' if accepted else 'rejected'} "
        f"stage_clean={bool((candidate_report if accepted else report).get('stage_ok'))}",
        flush=True,
    )
    return (
        candidate_report if accepted else report,
        {
            "focus": focus,
            "inspection_scope": scope,
            "diagnosis": diagnosis,
            "plan": plan,
            "patch": patch,
            "observation_delta": observation_delta,
            "delta_review": delta_review,
            "semantic_validation": semantic_validation,
            "accepted": accepted,
            "stage_clean": bool(
                (candidate_report if accepted else report).get("stage_ok")
            ),
        },
    )


def run_pure_llm_generation_rounds(
    context: AgenticGenerationContext,
    *,
    model_name: str = "gpt-5.2",
    foreign_contracts: list[dict[str, Any]] | None = None,
    max_rounds: int = 2,
    generate_scripts: bool = True,
    generate_prompts: bool = True,
    repair_only: bool = False,
    generation_only: bool = False,
    package_synthesis: bool = False,
    runtime_adapter_synthesis: bool = False,
    creation_foundation_synthesis: bool = False,
    creation_foundation_module: str | None = None,
    focused_repair: bool = False,
    incremental_generation_repair: bool = False,
    max_focus_targets: int = 3,
    focused_package_integration: bool = False,
    edit_backend: EditBackend = "exact_edits",
) -> dict[str, Any]:
    """Generate and repair artifacts using plain LLM artifact-edit calls."""
    requested_package_synthesis = package_synthesis
    if package_synthesis:
        focused_package_integration = True
        package_synthesis = False
    if focused_package_integration:
        focused_repair = True
        if not 1 <= max_focus_targets <= 3:
            raise ValueError(
                "focused package integration requires max_focus_targets between 1 and 3"
            )
    if repair_only and generation_only:
        raise ValueError("repair_only and generation_only are mutually exclusive")
    targets = _editable_artifacts(
        context,
        generate_scripts=generate_scripts,
        generate_prompts=generate_prompts,
    )
    if not targets:
        return {
            "mode": "pure_llm_unified_diff",
            "model": model_name,
            "ok": False,
            "failures": ["no_editable_generation_targets"],
            "history": [],
        }

    generation_snapshots = {path: path.read_bytes() for path in targets}
    history: list[dict[str, Any]] = []
    report = build_validation_report(
        context,
        foreign_contracts=foreign_contracts,
        write_report=True,
        prompts_required=generate_prompts,
    )
    initial_files: list[dict[str, Any]] = []
    if repair_only:
        if any(not path.read_text(encoding="utf-8", errors="replace").strip() for path in targets):
            return {
                "mode": "pure_llm_repair_only",
                "model": model_name,
                "ok": False,
                "failures": ["repair_only_checkpoint_contains_empty_artifacts"],
                "final_report": report,
                "history": [],
            }
        history.append(
            {
                "round": 0,
                "mode": "repair_checkpoint",
                "validation": report,
            }
        )
        if creation_foundation_synthesis and not report.get("ok"):
            foundation_targets = [
                path
                for path in targets
                if path.suffix == ".py" and path.name != "main.py"
                and (
                    not creation_foundation_module
                    or path.name == creation_foundation_module
                )
            ]
            if not foundation_targets:
                raise ValueError("Creation foundation synthesis has no target modules")
            before_report = report
            foundation_report: dict[str, Any] = {}
            foundation_delta_review: dict[str, Any] = {}

            def validate_creation_foundation() -> dict[str, Any]:
                nonlocal foundation_report, foundation_delta_review
                foundation_report = build_validation_report(
                    context,
                    foreign_contracts=foreign_contracts,
                    write_report=True,
                    prompts_required=generate_prompts,
                )
                before_failures = set(before_report.get("failures") or [])
                after_failures = set(foundation_report.get("failures") or [])
                improved = _is_strict_validation_improvement(
                    before_failures, after_failures
                )
                if not foundation_report.get("ok") and not improved:
                    foundation_delta_review = _request_delta_review(
                        model_name=model_name,
                        plan={
                            "objective": (
                                "Repair the selected RDF creation-foundation module "
                                "without editing downstream main.py behavior."
                            ),
                            "targets": [
                                path.relative_to(Path(context.output_root))
                                .as_posix()
                                for path in foundation_targets
                            ],
                            "acceptance_focus": [
                                "The selected foundation defects are resolved.",
                                "Newly observable main.py failures are distinguished "
                                "from regressions caused by the foundation patch.",
                            ],
                        },
                        before_report=before_report,
                        after_report=foundation_report,
                        mechanical_validation={
                            "before_failure_count": len(before_failures),
                            "after_failure_count": len(after_failures),
                            "resolved_failures": sorted(
                                before_failures - after_failures
                            ),
                            "introduced_failures": sorted(
                                after_failures - before_failures
                            ),
                        },
                    )
                accepted = (
                    bool(foundation_report.get("ok"))
                    or improved
                    or foundation_delta_review.get("decision") == "accept"
                )
                return {
                    "ok": accepted,
                    "failures": []
                    if accepted
                    else [
                        "creation_foundation_delta_rejected:"
                        + str(
                            foundation_delta_review.get("reason")
                            or "no strict improvement"
                        )
                    ],
                    "before_failure_count": len(before_failures),
                    "after_failure_count": len(after_failures),
                    "resolved_failures": sorted(before_failures - after_failures),
                    "introduced_failures": sorted(after_failures - before_failures),
                    "delta_review": foundation_delta_review,
                }

            foundation_patch = run_llm_unified_diff_editor(
                model_name=model_name,
                output_root=Path(context.output_root),
                targets=foundation_targets,
                task_prompt=_creation_foundation_synthesis_task(
                    context=context,
                    report=report,
                ),
                max_attempts=5,
                validate=validate_creation_foundation,
                progress=lambda message: print(f"[pure_llm] {message}", flush=True),
                edit_backend=edit_backend,
            )
            if foundation_patch.get("ok"):
                report = foundation_report
            history.append(
                {
                    "round": 1,
                    "mode": "creation_foundation_synthesis",
                    "patch": foundation_patch,
                    "validation": report,
                }
            )
        if runtime_adapter_synthesis and not report.get("ok"):
            main_targets = [path for path in targets if path.name == "main.py"]
            if len(main_targets) != 1:
                raise ValueError("Runtime adapter synthesis requires exactly one main.py")
            before_report = report
            runtime_report: dict[str, Any] = {}

            def validate_runtime_adapter() -> dict[str, Any]:
                nonlocal runtime_report
                runtime_report = build_validation_report(
                    context,
                    foreign_contracts=foreign_contracts,
                    write_report=True,
                    prompts_required=generate_prompts,
                )
                before_failures = set(before_report.get("failures") or [])
                after_failures = set(runtime_report.get("failures") or [])
                improved = _is_strict_validation_improvement(
                    before_failures, after_failures
                )
                return {
                    "ok": bool(runtime_report.get("ok")) or improved,
                    "failures": []
                    if bool(runtime_report.get("ok")) or improved
                    else ["runtime_adapter_synthesis_did_not_reduce_failures"],
                    "before_failure_count": len(before_failures),
                    "after_failure_count": len(after_failures),
                    "resolved_failures": sorted(before_failures - after_failures),
                    "introduced_failures": sorted(after_failures - before_failures),
                }

            runtime_patch = run_llm_unified_diff_editor(
                model_name=model_name,
                output_root=Path(context.output_root),
                targets=main_targets,
                task_prompt=_runtime_adapter_synthesis_task(
                    context=context,
                    report=report,
                ),
                max_attempts=5,
                validate=validate_runtime_adapter,
                progress=lambda message: print(f"[pure_llm] {message}", flush=True),
                edit_backend=edit_backend,
            )
            if runtime_patch.get("ok"):
                report = runtime_report
            history.append(
                {
                    "round": 1,
                    "mode": "runtime_adapter_synthesis",
                    "patch": runtime_patch,
                    "validation": report,
                }
            )
        if package_synthesis and not report.get("ok"):
            before_report = report
            package_report: dict[str, Any] = {}
            package_targets = [path for path in targets if path.suffix == ".py"]

            def validate_repair_package() -> dict[str, Any]:
                nonlocal package_report
                package_report = build_validation_report(
                    context,
                    foreign_contracts=foreign_contracts,
                    write_report=True,
                    prompts_required=generate_prompts,
                )
                before_count = _failure_count(before_report)
                after_count = _failure_count(package_report)
                before_failures = set(before_report.get("failures") or [])
                after_failures = set(package_report.get("failures") or [])
                improved = _is_strict_validation_improvement(
                    before_failures, after_failures
                )
                return {
                    "ok": bool(package_report.get("ok")) or improved,
                    "failures": []
                    if bool(package_report.get("ok")) or improved
                    else ["package_synthesis_did_not_reduce_failures"],
                    "before_failure_count": before_count,
                    "after_failure_count": after_count,
                    "resolved_failures": sorted(before_failures - after_failures),
                    "introduced_failures": sorted(after_failures - before_failures),
                }

            synthesis_report = run_llm_unified_diff_editor(
                model_name=model_name,
                output_root=Path(context.output_root),
                targets=package_targets,
                task_prompt=_package_synthesis_task(context=context, report=report),
                max_attempts=5,
                validate=validate_repair_package,
                progress=lambda message: print(f"[pure_llm] {message}", flush=True),
                edit_backend=edit_backend,
            )
            if synthesis_report.get("ok"):
                report = package_report
            history.append(
                {
                    "round": 1,
                    "mode": "package_synthesis",
                    "patch": synthesis_report,
                    "validation": report,
                }
            )
    else:
        artifact_dependency_order = _fixed_artifact_dependency_order(
            root=Path(context.output_root),
            targets=targets,
        )
        target_by_relative = {
            path.resolve()
            .relative_to(Path(context.output_root).resolve())
            .as_posix(): path
            for path in targets
        }
        generation_targets = [
            target_by_relative[relative] for relative in artifact_dependency_order
        ]
        for target_index, target in enumerate(generation_targets, start=1):
            if target.name == "KG_BUILDING_ITER_1.md":
                _resolve_top_entity_from_tbox(context, model_name=model_name)
            print(
                f"[pure_llm] artifact={target_index}/{len(generation_targets)} "
                f"phase=generate target={target.name} "
                f'objective="{_progress_summary("Generate the artifact against its ontology contract")}"',
                flush=True,
            )
            active_artifacts = [
                path.resolve()
                .relative_to(Path(context.output_root).resolve())
                .as_posix()
                for path in generation_targets[:target_index]
            ]
            existing_text = target.read_text(encoding="utf-8", errors="replace")
            if incremental_generation_repair and existing_text.strip():
                stage_report = build_validation_report(
                    context,
                    foreign_contracts=foreign_contracts,
                    write_report=True,
                    prompts_required=False,
                    active_artifacts=active_artifacts,
                )
                artifact_record = {
                    "target": str(target),
                    "patch": {
                        "ok": True,
                        "changed_files": [],
                        "checkpoint_reused": True,
                    },
                    "stage_validation": stage_report,
                    "stage_clean": bool(stage_report.get("stage_ok")),
                }
                if not artifact_record["stage_clean"] and focused_repair:
                    stage_repairs: list[dict[str, Any]] = []
                    for _ in range(max(1, max_rounds)):
                        stage_report, stage_repair = _run_stage_focused_repair(
                            model_name=model_name,
                            context=context,
                            targets=generation_targets[:target_index],
                            report=stage_report,
                            foreign_contracts=foreign_contracts,
                            active_artifacts=active_artifacts,
                            max_focus_targets=max_focus_targets,
                            edit_backend=edit_backend,
                        )
                        stage_repairs.append(stage_repair)
                        if stage_report.get("stage_ok") or not stage_repair.get(
                            "accepted"
                        ):
                            break
                    artifact_record["stage_repairs"] = stage_repairs
                    artifact_record["stage_repair"] = (
                        stage_repairs[-1] if stage_repairs else {}
                    )
                    artifact_record["stage_validation"] = stage_report
                    artifact_record["stage_clean"] = bool(
                        stage_report.get("stage_ok")
                    )
                initial_files.append(artifact_record)
                if not artifact_record["stage_clean"]:
                    break
                continue
            patch_report = run_llm_unified_diff_editor(
                model_name=model_name,
                output_root=Path(context.output_root),
                targets=[target],
                task_prompt=_generation_task(
                    context=context,
                    report=report,
                    round_index=1,
                    generate_scripts=target.suffix == ".py",
                    generate_prompts=target.suffix == ".md",
                    target=target,
                ),
                max_attempts=5,
                validate=(
                    (lambda target=target: _validate_generated_prompt_hard_gates(target))
                    if target.suffix == ".md"
                    else None
                ),
                progress=lambda message: print(f"[pure_llm] {message}", flush=True),
                edit_backend=edit_backend,
            )
            artifact_record: dict[str, Any] = {
                "target": str(target),
                "patch": patch_report,
            }
            if not patch_report.get("ok"):
                initial_files.append(artifact_record)
                break
            if target.suffix == ".md":
                print(
                    f"[pure_llm] artifact={target_index}/{len(generation_targets)} "
                    "phase=artifact-review scope=prompt-hard-gates action=start",
                    flush=True,
                )
                stage_report = _validate_generated_prompt(
                    model_name=model_name,
                    context=context,
                    target=target,
                    foreign_contracts=foreign_contracts,
                )
                artifact_record["hard_gate_validation"] = patch_report.get(
                    "validation", {}
                )
                artifact_record["semantic_validation"] = stage_report
                artifact_record["stage_clean"] = bool(stage_report.get("stage_ok"))
                print(
                    f"[pure_llm] artifact={target_index}/{len(generation_targets)} "
                    "phase=artifact-review scope=prompt-semantic "
                    + _progress_validation(stage_report),
                    flush=True,
                )
                semantic_repairs: list[dict[str, Any]] = []
                if not stage_report.get("stage_ok"):
                    print(
                        f"[pure_llm] artifact={target_index}/{len(generation_targets)} "
                        "phase=repair scope=prompt-semantic action=start",
                        flush=True,
                    )
                    stage_report, semantic_patch = _repair_generated_prompt_semantics(
                        model_name=model_name,
                        context=context,
                        target=target,
                        foreign_contracts=foreign_contracts,
                        report=stage_report,
                        edit_backend=edit_backend,
                    )
                    semantic_repairs.append(semantic_patch)
                artifact_record["semantic_repairs"] = semantic_repairs
                artifact_record["stage_validation"] = stage_report
                artifact_record["stage_clean"] = bool(stage_report.get("stage_ok"))
                print(
                    f"[pure_llm] artifact={target_index}/{len(generation_targets)} "
                    "phase=artifact-review scope=prompt-final "
                    + _progress_validation(stage_report),
                    flush=True,
                )
                if not artifact_record["stage_clean"]:
                    initial_files.append(artifact_record)
                    break
            elif incremental_generation_repair:
                print(
                    f"[pure_llm] artifact={target_index}/{len(generation_targets)} "
                    "phase=artifact-review scope=stage-contract action=start",
                    flush=True,
                )
                stage_report = build_validation_report(
                    context,
                    foreign_contracts=foreign_contracts,
                    write_report=True,
                    prompts_required=False,
                    active_artifacts=(
                        [
                            target.resolve()
                            .relative_to(Path(context.output_root).resolve())
                            .as_posix()
                        ]
                        if target.suffix == ".md"
                        else active_artifacts
                    ),
                )
                artifact_record["stage_validation"] = stage_report
                artifact_record["stage_clean"] = bool(stage_report.get("stage_ok"))
                print(
                    f"[pure_llm] artifact={target_index}/{len(generation_targets)} "
                    "phase=artifact-review scope=stage-contract "
                    + _progress_validation(stage_report),
                    flush=True,
                )
                if not artifact_record["stage_clean"] and (
                    focused_repair or target.suffix == ".md"
                ):
                    stage_repairs: list[dict[str, Any]] = []
                    for _ in range(max(1, max_rounds)):
                        stage_report, stage_repair = _run_stage_focused_repair(
                            model_name=model_name,
                            context=context,
                            targets=(
                                [target]
                                if target.suffix == ".md"
                                else generation_targets[:target_index]
                            ),
                            report=stage_report,
                            foreign_contracts=foreign_contracts,
                            active_artifacts=(
                                [
                                    target.resolve()
                                    .relative_to(Path(context.output_root).resolve())
                                    .as_posix()
                                ]
                                if target.suffix == ".md"
                                else active_artifacts
                            ),
                            max_focus_targets=1 if target.suffix == ".md" else max_focus_targets,
                            edit_backend=edit_backend,
                        )
                        stage_repairs.append(stage_repair)
                        if stage_report.get("stage_ok") or not stage_repair.get(
                            "accepted"
                        ):
                            break
                    artifact_record["stage_repairs"] = stage_repairs
                    artifact_record["stage_repair"] = (
                        stage_repairs[-1] if stage_repairs else {}
                    )
                    artifact_record["stage_validation"] = stage_report
                    artifact_record["stage_clean"] = bool(
                        stage_report.get("stage_ok")
                    )
                semantic_reviews: list[dict[str, Any]] = []
                if artifact_record["stage_clean"]:
                    from src.agents.scripts_and_prompts_generation.semantic_script_review import (
                        review_generated_artifact_semantics_with_llm,
                    )

                    print(
                        f"[pure_llm] artifact={target_index}/{len(generation_targets)} "
                        "phase=artifact-review scope=llm-semantic action=start",
                        flush=True,
                    )
                    semantic_review = review_generated_artifact_semantics_with_llm(
                        context=context,
                        artifact_path=target,
                        model_name=model_name,
                    )
                    semantic_reviews.append(semantic_review)
                    print(
                        f"[pure_llm] artifact={target_index}/{len(generation_targets)} "
                        "phase=artifact-review scope=llm-semantic "
                        f"decision={semantic_review.get('decision') or 'unknown'} "
                        f"critical_errors={len(semantic_review.get('critical_errors') or [])}",
                        flush=True,
                    )
                    for _ in range(max(1, max_rounds)):
                        if semantic_review.get("decision") == "pass":
                            break
                        semantic_report = build_validation_report(
                            context,
                            foreign_contracts=foreign_contracts,
                            write_report=True,
                            prompts_required=False,
                            active_artifacts=[
                                target.resolve()
                                .relative_to(Path(context.output_root).resolve())
                                .as_posix()
                            ],
                            extra_failures=[
                                "LLM artifact semantic review requires repair:\n"
                                + json.dumps(semantic_review, ensure_ascii=False)
                            ],
                        )
                        stage_report, semantic_repair = _run_stage_focused_repair(
                            model_name=model_name,
                            context=context,
                            targets=[target],
                            report=semantic_report,
                            foreign_contracts=foreign_contracts,
                            active_artifacts=[
                                target.resolve()
                                .relative_to(Path(context.output_root).resolve())
                                .as_posix()
                            ],
                            max_focus_targets=1,
                            edit_backend=edit_backend,
                            semantic_validate=(
                                lambda target=target: review_generated_artifact_semantics_with_llm(
                                    context=context,
                                    artifact_path=target,
                                    model_name=model_name,
                                )
                            ),
                        )
                        artifact_record.setdefault("semantic_repairs", []).append(
                            semantic_repair
                        )
                        if not semantic_repair.get("accepted"):
                            break
                        semantic_review = review_generated_artifact_semantics_with_llm(
                            context=context,
                            artifact_path=target,
                            model_name=model_name,
                        )
                        semantic_reviews.append(semantic_review)
                    artifact_record["semantic_reviews"] = semantic_reviews
                    artifact_record["stage_clean"] = bool(
                        stage_report.get("stage_ok")
                        and semantic_reviews
                        and semantic_reviews[-1].get("decision") == "pass"
                    )
                if not artifact_record["stage_clean"]:
                    initial_files.append(artifact_record)
                    break
            initial_files.append(artifact_record)
        report = build_validation_report(
            context,
            foreign_contracts=foreign_contracts,
            write_report=True,
            prompts_required=generate_prompts,
            extra_failures=[
                failure
                for item in initial_files
                for failure in (item["patch"].get("failures") or [])
            ],
        )
        history.append(
            {
                "round": 1,
                "mode": "per_file_initial_generation",
                "files": initial_files,
                "validation": report,
            }
        )
        if not all(item["patch"].get("ok") for item in initial_files):
            for path, content in generation_snapshots.items():
                path.write_bytes(content)
            return {
                "mode": "pure_llm_unified_diff",
                "model": model_name,
                "ok": False,
                "final_report": report,
                "history": history,
                "rolled_back": True,
            }
        if incremental_generation_repair and not all(
            item.get("stage_clean", True) for item in initial_files
        ):
            return {
                "mode": "pure_llm_incremental_generation",
                "model": model_name,
                "ok": False,
                "final_report": report,
                "history": history,
                "generation_complete": False,
                "checkpoint_preserved": True,
                "artifact_dependency_order": artifact_dependency_order,
                "stopped_at_artifact": initial_files[-1]["target"],
            }
        if package_synthesis and not report.get("ok"):
            before_report = report
            package_report: dict[str, Any] = {}

            def validate_package() -> dict[str, Any]:
                nonlocal package_report
                package_report = build_validation_report(
                    context,
                    foreign_contracts=foreign_contracts,
                    write_report=True,
                    prompts_required=generate_prompts,
                )
                before_count = _failure_count(before_report)
                after_count = _failure_count(package_report)
                before_failures = set(before_report.get("failures") or [])
                after_failures = set(package_report.get("failures") or [])
                improved = _is_strict_validation_improvement(
                    before_failures, after_failures
                )
                return {
                    "ok": bool(package_report.get("ok")) or improved,
                    "failures": []
                    if bool(package_report.get("ok")) or improved
                    else ["package_synthesis_did_not_reduce_failures"],
                    "before_failure_count": before_count,
                    "after_failure_count": after_count,
                    "resolved_failures": sorted(before_failures - after_failures),
                    "introduced_failures": sorted(after_failures - before_failures),
                }

            synthesis_report = run_llm_unified_diff_editor(
                model_name=model_name,
                output_root=Path(context.output_root),
                targets=targets,
                task_prompt=_package_synthesis_task(context=context, report=report),
                max_attempts=5,
                validate=validate_package,
                progress=lambda message: print(f"[pure_llm] {message}", flush=True),
                edit_backend=edit_backend,
            )
            if synthesis_report.get("ok"):
                report = package_report
            history.append(
                {
                    "round": 2,
                    "mode": "package_synthesis",
                    "patch": synthesis_report,
                    "validation": report,
                }
            )
        if generation_only:
            return {
                "mode": "pure_llm_generation_checkpoint",
                "model": model_name,
                "ok": bool(report.get("ok")),
                "generation_complete": True,
                "checkpoint_preserved": True,
                "final_report": report,
                "history": history,
                "repair_steps": [],
            }

    repair_steps: list[dict[str, Any]] = []
    active_focus: dict[str, Any] | None = None
    target_by_relative = {
        path.resolve().relative_to(Path(context.output_root).resolve()).as_posix(): path
        for path in targets
    }
    for step_index in range(1, max(0, max_rounds) + 1):
        if report.get("ok"):
            break
        try:
            if focused_repair and active_focus is None:
                active_focus = _request_repair_focus(
                    model_name=model_name,
                    context=context,
                    report=report,
                    active_focus=None,
                    previous_steps=repair_steps,
                    max_target_files=max_focus_targets,
                )
                if active_focus["status"] in {"complete", "blocked", "defer"}:
                    repair_steps.append(
                        {
                            "step_index": step_index,
                            "focus": active_focus,
                            "accepted": False,
                            "before_failure_count": _failure_count(report),
                            "after_failure_count": _failure_count(report),
                        }
                    )
                    break
            inspection_scope = _request_inspection_scope(
                model_name=model_name,
                context=context,
                targets=targets,
                report=report,
                step_index=step_index,
                previous_steps=repair_steps,
                focus=active_focus,
            )
            if inspection_scope["status"] == "blocked":
                repair_steps.append(
                    {
                        "step_index": step_index,
                        "inspection_scope": inspection_scope,
                        "accepted": False,
                        "before_failure_count": _failure_count(report),
                        "after_failure_count": _failure_count(report),
                    }
                )
                if focused_repair:
                    active_focus = None
                    continue
                break
            if inspection_scope["status"] == "complete":
                diagnosis = next(
                    (
                        step["diagnosis"]
                        for step in reversed(repair_steps)
                        if (step.get("diagnosis") or {}).get("status") == "diagnosed"
                    ),
                    None,
                )
                if diagnosis is None:
                    raise ValueError(
                        "Inspection marked evidence complete without a prior diagnosis"
                    )
            else:
                diagnosis = _request_causal_diagnosis(
                    model_name=model_name,
                    context=context,
                    targets=targets,
                    report=report,
                    inspection_scope=inspection_scope,
                    previous_steps=repair_steps,
                    focus=active_focus,
                )
            plan = _request_impact_plan(
                model_name=model_name,
                context=context,
                targets=targets,
                report=report,
                inspection_scope=inspection_scope,
                diagnosis=diagnosis,
                previous_steps=repair_steps,
                focus=active_focus,
            )
        except Exception as exc:
            repair_steps.append(
                {
                    "step_index": step_index,
                    "accepted": False,
                    "planning_failure": f"{type(exc).__name__}:{exc}",
                    "before_failure_count": _failure_count(report),
                    "after_failure_count": _failure_count(report),
                }
            )
            if focused_repair:
                # Preserve the active semantic focus and return the structured
                # planning rejection through previous_steps. The next planner
                # call can then choose an editable alternative without changing
                # the read-only dependency boundary.
                continue
            break
        if plan["status"] == "blocked":
            repair_steps.append(
                {
                    "step_index": step_index,
                    "inspection_scope": inspection_scope,
                    "diagnosis": diagnosis,
                    "plan": plan,
                    "accepted": False,
                    "before_failure_count": _failure_count(report),
                    "after_failure_count": _failure_count(report),
                }
            )
            if focused_repair:
                active_focus = None
                continue
            break
        selected_targets = [target_by_relative[path] for path in plan["targets"]]
        before_count = _failure_count(report)
        candidate_report: dict[str, Any] = {}
        delta_review: dict[str, Any] = {}
        observation_delta: dict[str, Any] = {}

        def validate_step() -> dict[str, Any]:
            nonlocal candidate_report, delta_review, observation_delta
            fast_report = build_validation_report(
                context,
                foreign_contracts=foreign_contracts,
                write_report=False,
                prompts_required=generate_prompts,
                active_artifacts=[
                    target.resolve()
                    .relative_to(Path(context.output_root).resolve())
                    .as_posix()
                    for target in selected_targets
                ],
            )
            if focused_repair and active_focus:
                fast_delta = _observation_transition_report(
                    before_report=report,
                    after_report=fast_report,
                    focus_observation_ids=active_focus["observation_ids"],
                )
                if (
                    fast_delta["focus_progress"]
                    and not fast_delta["protected_regression"]
                ):
                    candidate_report = build_validation_report(
                        context,
                        foreign_contracts=foreign_contracts,
                        write_report=True,
                        prompts_required=generate_prompts,
                    )
                    mechanical = {
                        "before_failure_count": before_count,
                        "after_failure_count": _failure_count(candidate_report),
                        "after_machine_ok": bool(candidate_report.get("ok")),
                        **_observation_transition_report(
                            before_report=report,
                            after_report=candidate_report,
                            focus_observation_ids=active_focus["observation_ids"],
                        ),
                    }
                    return _validation_outcome(
                        mechanical,
                        accepted=True,
                        rejection_failure="",
                        delta_review={
                            "decision": "machine_focus_resolved",
                            "reason": (
                                "The selected observation passed target-level "
                                "validation without a protected regression."
                            ),
                        },
                    )
            fast_failures = list(fast_report.get("failures") or [])
            if fast_failures:
                candidate_report = fast_report
                delta_review = {
                    "decision": "reject",
                    "reason": "target-level mechanical validation failed",
                }
                return {
                    "ok": False,
                    "failures": fast_failures,
                    "failure_class": "target_mechanical",
                    "retry_hint": (
                        "Fix only the reported active-target contract failures; "
                        "do not request another package-wide semantic review yet."
                    ),
                }
            candidate_report = build_validation_report(
                context,
                foreign_contracts=foreign_contracts,
                write_report=True,
                prompts_required=generate_prompts,
            )
            mechanical = {
                "before_failure_count": before_count,
                "after_failure_count": _failure_count(candidate_report),
                "after_machine_ok": bool(candidate_report.get("ok")),
            }
            if focused_repair and active_focus:
                observation_delta = _observation_transition_report(
                    before_report=report,
                    after_report=candidate_report,
                    focus_observation_ids=active_focus["observation_ids"],
                )
                mechanical.update(observation_delta)
            delta_review = _request_delta_review(
                model_name=model_name,
                plan=plan,
                before_report=report,
                after_report=candidate_report,
                mechanical_validation=mechanical,
            )
            focus_resolved_without_regression = (
                bool(observation_delta.get("focus_progress"))
                and not observation_delta.get("protected_regression")
            )
            accepted = (
                bool(candidate_report.get("ok"))
                or focus_resolved_without_regression
                or (
                    delta_review["decision"] == "accept"
                    and (
                        not focused_repair
                        or focus_resolved_without_regression
                    )
                )
            )
            return _validation_outcome(
                mechanical,
                accepted=accepted,
                rejection_failure=(
                    f"delta_reviewer_{delta_review['decision']}:"
                    f"{delta_review.get('reason')}"
                ),
                delta_review=delta_review,
            )

        print(
            f"[pure_llm] repair step {step_index}/{max(0, max_rounds)}: "
            f"{plan.get('objective')} targets={plan['targets']}",
            flush=True,
        )
        patch_report = run_llm_unified_diff_editor(
            model_name=model_name,
            output_root=Path(context.output_root),
            targets=selected_targets,
            task_prompt=_repair_task(
                context=context, plan=plan, report=report, focus=active_focus
            ),
            max_attempts=5,
            validate=validate_step,
            max_targets=max_focus_targets if focused_package_integration else None,
            progress=lambda message: print(f"[pure_llm] {message}", flush=True),
            edit_backend=edit_backend,
        )
        accepted = bool(patch_report.get("ok"))
        if accepted:
            report = candidate_report
        else:
            # The editor has rolled the rejected step back. Rebuild the report so the
            # persisted validation evidence describes the restored candidate.
            report = build_validation_report(
                context,
                foreign_contracts=foreign_contracts,
                write_report=True,
                prompts_required=generate_prompts,
            )
        step_record = {
            "step_index": step_index,
            "focus": active_focus,
            "inspection_scope": inspection_scope,
            "diagnosis": diagnosis,
            "plan": plan,
            "patch": patch_report,
            "delta_review": delta_review,
            "observation_delta": observation_delta,
            "accepted": accepted,
            "before_failure_count": before_count,
            "after_failure_count": _failure_count(report),
            "validation": report,
        }
        repair_steps.append(step_record)
        history.append({"round": step_index + 1, "mode": "staged_repair", **step_record})
        if focused_package_integration:
            history[-1]["mode"] = "focused_package_integration"
        if focused_repair and accepted:
            remaining = observation_delta.get("persisting_focus_observation_ids") or []
            if remaining:
                active_focus = {
                    **(active_focus or {}),
                    "status": "resume",
                    "observation_ids": remaining,
                }
            else:
                active_focus = None
        if focused_package_integration:
            _write_integration_manifest(
                context=context,
                targets=targets,
                model_name=model_name,
                max_targets=max_focus_targets,
                report=report,
                steps=repair_steps,
                active_focus=active_focus,
                stop_reason="globally_valid" if report.get("ok") else "round_incomplete",
            )

    stop_reason = (
        "globally_valid"
        if report.get("ok")
        else "round_limit"
        if repair_steps
        else "no_repair_rounds"
    )
    manifest_path: Path | None = None
    if focused_package_integration:
        manifest_path = _write_integration_manifest(
            context=context,
            targets=targets,
            model_name=model_name,
            max_targets=max_focus_targets,
            report=report,
            steps=repair_steps,
            active_focus=active_focus,
            stop_reason=stop_reason,
        )
    result = {
        "mode": (
            "focused_package_integration"
            if focused_package_integration
            else "pure_llm_repair_only"
            if repair_only
            else "pure_llm_unified_diff"
        ),
        "requested_mode": (
            "package_synthesis"
            if requested_package_synthesis
            else "focused_package_integration"
            if focused_package_integration
            else None
        ),
        "effective_mode": (
            "focused_package_integration"
            if focused_package_integration
            else "pure_llm_generation"
        ),
        "deprecated_alias_used": requested_package_synthesis,
        "model": model_name,
        "ok": bool(report.get("ok"))
        and (repair_only or all(item["patch"].get("ok") for item in initial_files))
        and (
            len(history) == 1
            or bool((history[-1].get("patch") or {}).get("ok"))
        ),
        "final_report": report,
        "history": history,
        "repair_steps": repair_steps,
        "globally_valid": bool(report.get("ok")),
        "improved_but_incomplete": bool(repair_steps)
        and any(step.get("accepted") for step in repair_steps)
        and not report.get("ok"),
        "stop_reason": stop_reason,
        "max_targets_per_round": max_focus_targets
        if focused_package_integration
        else None,
        "max_observed_targets": max(
            (len((step.get("plan") or {}).get("targets") or []) for step in repair_steps),
            default=0,
        ),
        "checkpoint_manifest": str(manifest_path) if manifest_path else None,
    }
    if not result["ok"] and not repair_only:
        for path, content in generation_snapshots.items():
            path.write_bytes(content)
        result["rolled_back"] = True
    elif repair_only:
        # Each candidate is already transactional: the unified-diff editor restores
        # rejected changes. Preserve accepted repair deltas even when the package
        # still has unrelated failures and therefore is not globally complete yet.
        result["checkpoint_preserved"] = True
    return result
