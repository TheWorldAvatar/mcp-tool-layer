"""Plain-LLM generation and repair over isolated artifact candidates."""

from __future__ import annotations

import json
import hashlib
import importlib.util
import inspect
import multiprocessing
import os
import re
import shutil
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import types
from typing import Any, Callable, Iterable, Mapping

from src.agents.scripts_and_prompts_generation.agentic_generation_context import (
    AgenticGenerationContext,
)
from src.agents.scripts_and_prompts_generation.agentic_generation_validation import (
    build_validation_report,
    validate_prompt_runtime_bindings,
)
from src.agents.scripts_and_prompts_generation.artifact_surface_contract import (
    LIFECYCLE_TOOL_NAMES,
    derive_main_surface_contract,
)
from src.agents.scripts_and_prompts_generation.artifact_state import (
    ArtifactStateStore,
)
from src.agents.scripts_and_prompts_generation.extension_prompt_contract import (
    EXTENSION_EXTRACTION_RUNTIME_SLOTS,
    EXTENSION_KG_RUNTIME_SLOTS,
    ensure_extension_kg_mode_a_handoff_file,
    extension_kg_handoff_contract,
    extension_kg_mode_a_handoff_present,
    load_extension_extraction_meta_prompt_policy,
    load_extension_meta_prompt_policy,
)
from src.agents.scripts_and_prompts_generation.level1_code_repair import invoke_json
from src.agents.scripts_and_prompts_generation.llm_invocation_runtime import (
    configure_llm_invocation_journal,
)
from src.agents.scripts_and_prompts_generation.repair_skill_catalog import (
    repair_skill_catalog,
    repair_skill_ids,
)
from src.agents.scripts_and_prompts_generation.reuse_policy import (
    existing_entity_check_contracts,
    prohibited_class_locals,
)
from src.agents.scripts_and_prompts_generation.llm_artifact_editor import (
    DEFAULT_EDIT_BACKEND,
    EditBackend,
    run_llm_artifact_editor,
)
from src.agents.scripts_and_prompts_generation.tbox_property_contract_experiment import (
    derive_iteration_property_contract,
)

# Legacy monkeypatch seam retained for older tests and downstream integrations.
run_llm_unified_diff_editor = run_llm_artifact_editor


def _extension_meta_prompt_policy(target: Path | None = None) -> dict[str, Any]:
    """Load the canonical extension policy for the target prompt kind."""
    root = Path(__file__).resolve().parents[3]
    if target is not None and target.name.startswith("EXTRACTION_"):
        return load_extension_extraction_meta_prompt_policy(root)
    return load_extension_meta_prompt_policy(root)


MCP_CAPABILITY_SECURITY_CONTRACT = """
Capability-security contract for every generated or repaired MCP Python artifact:
- Treat package-local `_fixed_rdf_runtime.py`, `_reuse_pair_judge.py`, and
  `_relationship_contract.json` as read-only infrastructure compiled from the
  active T-Box and generic identity-reuse contract.
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
    "central_memory_paths",
    "load_central_reuse_memory",
    "publish_reusable_entities_to_central_memory",
    "load_from_turtle_file",
    "package_relationship_capabilities",
    "package_entity_capabilities",
    "package_ordered_entity_capabilities",
    "package_om2_quantity_creator",
    "create_om2_quantity",
    "package_datatype_capabilities",
    "reset_graph",
    "atomic_graph_transaction",
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
                "result = writer(subject_iri, object_iri, reuse_authorization_token)"
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
                "def create_Class(label: str, ordering_param: int, *, optional_text: str | None = None, "
                "optional_flag: bool | None = None) -> str:\n"
                "    if not isinstance(label, str) or not label.strip(): return "
                "rdf_runtime.error_json(code='invalid-label', message='...')\n"
                "    if isinstance(ordering_param, bool) or not isinstance(ordering_param, int) "
                "or ordering_param < 1: "
                "return rdf_runtime.error_json(code='invalid-order', message='...')\n"
                "    if optional_text is not None and not isinstance(optional_text, str): return "
                "rdf_runtime.error_json(code='invalid-datatype', message='...')\n"
                "    if optional_flag is not None and not isinstance(optional_flag, bool): return "
                "rdf_runtime.error_json(code='invalid-datatype', message='...')\n"
                "    iri = ordered_creators[BOUND_CLASS_IRI](label, ordering_param)\n"
                "    if optional_text is not None: datatype_writers[BOUND_TEXT_IRI](iri, optional_text)\n"
                "    if optional_flag is not None: datatype_writers[BOUND_FLAG_IRI](iri, optional_flag)\n"
                "    return rdf_runtime.success_json(iri=iri, message='...')"
            ),
            "rules": [
                "Use only explicit parameters projected by owned_entity_tool_contracts.",
                "Validate all non-None datatype inputs before entity/order/datatype mutation.",
                "For integer inputs reject bool even though bool subclasses int in Python.",
                "Do not snapshot, clear, or restore the graph in generated code.",
                "When required_edges is non-empty, place every mutator call inside one "
                "rdf_runtime.atomic_graph_transaction() context.",
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


def _artifact_role_contract(
    target: Path | None,
    generation_contract: dict[str, Any] | None = None,
) -> dict[str, Any]:
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
                "This artifact is the concrete implementation module, not a manifest-only stub "
                "or forwarding placeholder. Its literal __all__ must contain every implemented "
                "public creator and must never be empty when contracts are non-empty.",
                "Define exactly one public tool for every and only every entry in "
                "owned_entity_tool_contracts.",
                "For every contract entry, expose `label: str`, followed by every entry in its "
                "`datatype_inputs` as explicitly typed keyword parameters. For an ordered "
                "member, expose exactly one required integer parameter bound to "
                "`ordering_property_local`; its Python name is chosen by the generated script "
                "and is discovered from the validated public signature rather than prescribed "
                "by this contract. Write it atomically through "
                "package_ordered_entity_capabilities()[exact class_iri].",
                "Treat every datatype base/default fallback declared in "
                "`datatype_inputs[].tbox_comment` as a binding creator-code responsibility. "
                "Implement the correctly typed base default in the public Python signature "
                "instead of `None` and ensure the datatype writer persists that value when the "
                "caller omits the argument. Never delegate a T-Box-declared base default to an "
                "extraction prompt, KG-building prompt, or runtime Agent. Preserve caller-supplied "
                "overrides and never invent a default absent from the T-Box. When the comment also "
                "contains conditional or inherited override rules, keep the base fallback in code "
                "and preserve those contextual rules without using them as a reason to omit the "
                "creator default.",
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
                "Treat each creator contract's `required_edges` and `dependent_entities` as a "
                "single atomic business operation. Expose every contract-declared existing-IRI, "
                "dependent-label, and dependent-datatype parameter explicitly. Prevalidate every "
                "input, then execute all entity, datatype, and required-edge mutations inside one "
                "`rdf_runtime.atomic_graph_transaction()` block.",
                "For a `same_operation_create` dependent, always mint a fresh target with its "
                "exact fixed capability, write its supplied datatype inputs, and write the exact "
                "contract predicate in the declared direction. Return the dependent IRI as result "
                "metadata. For an `existing_iri_parameter` edge, use only the named IRI parameter.",
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
                "Preserve the fixed creator's reuse-policy-aware identity contract: label is a "
                "required non-empty string, normalization strips surrounding whitespace, and "
                "invalid labels are rejected before graph mutation. Only classes explicitly marked "
                "`reusable: true` may return an existing same-class normalized-label IRI. Every "
                "non-reusable class, especially contextual occurrence and numerical-payload "
                "classes, must mint a fresh IRI even when class and label match an existing entity.",
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
                "Create object relationships absent from the current creator's `required_edges`, "
                "or caller-selected/arbitrary datatype assertions. Only contract-projected owner "
                "and dependent datatype inputs may be written.",
                "Reimplement IRI minting, rdf:type mutation, labels, or JSON serialization.",
                "Catch or convert fixed-runtime entity contract rejection into a success result.",
                "Use *args/**kwargs or wrapper signature metadata overrides.",
            ],
        }
    if name.endswith("_creation_relationships.py"):
        return {
            "role": "tbox_bound_object_property_tools",
            "must": [
                "This artifact is the concrete implementation module, not a manifest-only stub. "
                "Implement every supplied standalone relationship contract in this file.",
                "Define explicit add_<predicate_local> signatures.",
                "Bind each tool to its exact T-Box predicate capability from "
                "package_relationship_capabilities().",
                "Delegate every accepted subject/object pair unchanged to that bound capability. "
                "The package capability is the sole enforcement point for class reuse-policy "
                "decisions, including rejection when a non-reusable occurrence is consumed by "
                "more than one ordered member or role.",
                "Preserve every package-capability rejection as a failure response with no graph "
                "mutation; never retry it through another predicate or convert it to success.",
                "Keep FastMCP-exposed `Annotated[..., Field(...)]` annotations eagerly evaluated: "
                "do not enable `from __future__ import annotations` in this module. The installed "
                "FastMCP/Pydantic runtime cannot reliably resolve deferred Field metadata after "
                "these functions are imported into main.py for registration.",
                "Declare one literal __all__ containing every and only the object-property adders.",
                "Exclude every predicate listed in `merged_predicate_locals`; those edges are "
                "owned exclusively by atomic entity creators.",
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
                "Each `check_existing_<class_local>` must accept "
                "`proposed_entity_json: str = \"\"` and the keyword-only compatibility input "
                "`label: str = \"\"`. When JSON is absent and label is non-empty, convert the "
                "label to the proposed-entity JSON object before applying the same bounded check. "
                "This compatibility input must not change lookup scope or authorize reuse.",
                "Each existing-entity check must obey its contract lookup_scope: `central` checks "
                "load ontology-wide reuse memory; `document` checks load only the current DOI's "
                "document memory; either may authorize reuse only when reuse_authorized is true. "
                "`scoped` checks read only rdf_runtime.retained_graph() and are "
                "reference-resolution-only for exact prior occurrences.",
                "When a `central` or `document` check is called with both proposed_entity_json and label empty "
                "(or with invalid proposed-entity evidence), fail closed immediately via "
                "`return rdf_runtime.error_json(code='PROPOSED_ENTITY_EVIDENCE_REQUIRED', "
                "message='...')`. Do not pass `status=` into error_json: envelope status is "
                "always the literal `rejected`, while the machine-readable reason belongs only "
                "in `code='PROPOSED_ENTITY_EVIDENCE_REQUIRED'`.",
                "A `central` or `document` check must reject a missing/invalid proposed-entity JSON object, build "
                "one pair request per visible candidate, call "
                "`._reuse_pair_judge.judge_reuse_pairs(requests)` with that one positional list, "
                "hide every denied candidate, and call "
                "`rdf_runtime.register_central_reuse_authorization(candidate_iri=..., "
                "pair_id=..., judgement=...)` exactly once for every approved candidate. The "
                "registration return value is the token; return it as "
                "`reuse_authorization_token` inside the bounded `instances` list. "
                "Never treat class-level reuse eligibility or candidate presence as authorization.",
                "Reuse fail-closed example with no ontology class names: a central or document "
                "check called with empty proposed_entity_json and empty label returns immediately "
                "via error_json(code='PROPOSED_ENTITY_EVIDENCE_REQUIRED') and does not inspect "
                "candidates. After valid proposed-entity evidence, build one request per visible "
                "candidate and call judge_reuse_pairs(requests) with that single positional list; "
                "for each approved candidate call register_central_reuse_authorization with only "
                "candidate_iri, pair_id, and judgement. Do not treat class-level reuse_authorized "
                "or a non-empty candidate list as approval.",
                "Return a JSON success envelope with each matching IRI, labels, types, datatype "
                "values, relations, lookup scope, and reuse-authorization metadata. A scoped check "
                "must never authorize cross-occurrence, cross-top-entity, or cross-document reuse.",
                "For a central or document response, top-level `reuse_authorized` reports whether at least one "
                "candidate was actually approved in this call, not whether the class is eligible "
                "for reuse. It must therefore be false when the approved `instances` list is empty.",
                "Every existing-entity response must expose the literal metadata fields "
                "`lookup_scope`, `reuse_authorized`, and `reference_resolution_only`; "
                "`reference_resolution_only` must be the exact boolean inverse of "
                "`reuse_authorized`. Return candidates in one bounded list.",
                "Declare exactly one module-level literal `__all__` equal to "
                "expected_public_manifest in the same order; do not compute, append to, "
                "reassign, or omit this manifest.",
                "Use only the graph source fixed by each existing_entity_check_contract and return "
                "a JSON string report without mutation.",
                "Validate every ordered member linked through the contract member predicates.",
                "Require exactly one positive integer order per member, unique and contiguous "
                "orders 1..N within each parent, no non-reusable member linked to multiple "
                "parents, and missing_explicit_ancestor_type exactly from "
                "ordered_check_contract.required_explicit_ancestor_types: if a linked member "
                "has an explicit rdf:type that is a mapping key, every listed ancestor IRI "
                "must also be an explicit rdf:type on that member.",
                "Ancestor-type example with no ontology class names: two linked members have "
                "valid orders 1 and 2. The first is typed only as a concrete subclass that is "
                "a mapping key; the second has that subclass type plus every mapped ancestor. "
                "Emit missing_explicit_ancestor_type for the first only. Do not emit it because "
                "a member is typed only as an ancestor, or because it merely lacks some type "
                "from the ordered class family.",
                "For contiguity, N is the count of all ordered members linked to that parent "
                "before excluding members with missing, duplicate, or invalid order values. "
                "Compare valid observed orders with set(range(1, N + 1)); never derive N from "
                "max(observed), len(unique observed orders), or only valid-order members. "
                "Example: three linked ordered members with orders 1, 2, and missing must emit "
                "both missing_order and non_contiguous_order.",
                "Enforce every ordered_check_contract.operation_invariants entry without "
                "ontology-specific assumptions. Each typed owner must have exactly the declared "
                "required edge count. A same-operation dependent must have the declared explicit "
                "type, exactly one owner through that edge, and, when exclusive_target is true, "
                "must not be the object of any other listed exclusive predicate. Every ordered "
                "owner must have exactly one inverse container-membership edge, including zero "
                "parent as an invalid_parent_count violation.",
                "Return status `ok` only when there are no violations; otherwise return status "
                "`rejected` with a structured `violations` list. Every violations item must be "
                "an object whose required discriminator is exactly `code`, for example "
                "`{\"code\": \"missing_order\", \"member\": \"...\"}`. Never use "
                "`violation_code`, `type`, `kind`, or another alias in place of `code`.",
            ],
            "must_not": [
                "Mutate, repair, reorder, renumber, add, or remove any graph triple.",
                "Use Graph.add/remove/parse/update or any fixed-runtime mutation capability.",
                "Guess or probe alternative judge/authorization signatures, accept a judge "
                "failure, default a missing judgement to approval, accept a caller-supplied token, "
                "or swallow an authorization exception.",
                "Hard-code ontology IRIs outside ordered_check_contract and "
                "existing_entity_check_contracts.",
                "Define or export generic graph inspection, caller-selected class/predicate "
                "tools, or any check absent from expected_public_manifest.",
                "Use `violation_code`, `type`, `kind`, or any field other than the exact required "
                "`code` key as the discriminator of an ordered-check violations item.",
                "Reinterpret missing_explicit_ancestor_type as family membership or as a "
                "most-specific-subclass check.",
                "Pass `status=` into `rdf_runtime.error_json` / `error_result`, or set envelope "
                "`status` to the rejection code string (for example "
                "`PROPOSED_ENTITY_EVIDENCE_REQUIRED`). Envelope status must remain `rejected`.",
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
                "Provide the executable stdio entry point exactly through an "
                "`if __name__ == '__main__': mcp.run()` guard so launching this module keeps "
                "the MCP server alive.",
                "Register every public callable listed in the literal __all__ manifests of the "
                "validated entities, relationships, and checks modules under its unchanged name.",
                "Define and register exactly the approved lifecycle tools in "
                "generation_contract.lifecycle_tools. If commit_gate_contract is absent, preserve "
                "the legacy path by importing and registering both tested lifecycle callables "
                "directly from package-local `_fixed_rdf_runtime`. If commit_gate_contract is "
                "present, register `init_memory` directly and implement `export_memory` only as "
                "a signature-preserving adapter that calls the declared check first, returns its "
                "rejection unchanged, and delegates to fixed export only on status `ok`.",
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
                "Register the fixed runtime module or any callable obtained directly from it, "
                "except the explicitly approved init lifecycle callable and the fixed export "
                "delegate hidden behind the commit-gate adapter.",
                "Expose a caller-selected RDF class, predicate, triple, graph, file loader, "
                "capability factory/map, reset/debug operation, module, or class.",
                "Expose unrestricted Turtle/file ingestion or return a mutable Graph object.",
                "Expose any aggregate hint materializer or batch orchestration tool; hint-to-tool "
                "orchestration belongs exclusively to the KG agent and its prompt.",
                "Silently omit an allowlisted tool or add a convenience tool outside the allowlist.",
            ],
        }
    if target is not None and target.suffix == ".md":
        prompt_specific_must: list[str] = []
        onepass_fragment = _is_onepass_kg_fragment(target)
        iteration_spec = (generation_contract or {}).get("iteration_spec") or {}
        semantic_hints = (
            iteration_spec.get("hint_representation") == "semantic-text.v1"
        )
        runtime_slots = set(
            ((generation_contract or {}).get("runtime_binding_contract") or {}).get(
                "allowed_slots"
            )
            or []
        )
        has_accumulated_hints = "{accumulated_hints}" in runtime_slots
        is_enrichment_prompt = _is_enrichment_iteration_spec(iteration_spec)
        if target.name.startswith("EXTRACTION_ITER_"):
            if is_enrichment_prompt:
                prompt_specific_must.extend(
                    [
                        "This artifact is an enrichment sub-iteration only. Emit an "
                        "entities/relations patch in the parent interchange schema, preserve "
                        "the exact prior ref/class/label for every existing entity, and place "
                        "newly supported scalar values only under datatype_properties.",
                        "Do not retype, renumber, or replace the authoritative parent occurrence "
                        "list; add only source-supported missing details owned by this "
                        "sub-iteration.",
                    ]
                )
            elif semantic_hints:
                prompt_specific_must.extend(
                    [
                        "Require a natural-language semantic ledger headed exactly "
                        "`SEMANTIC_HINTS_V1`; forbid JSON, RDF, refs, IRIs, quantity nodes, "
                        "tool calls, and graph layout. Instruct the runtime to begin its answer "
                        "with that header, but do not place a literal answer header or begin-output "
                        "marker at the end of the prompt template because deterministic companion "
                        "instructions are appended after the LLM-authored component.",
                        "Require each occurrence to begin with a short subclass label. When "
                        "an occurrence is ordered, record its sequence position as a "
                        "contiguous integer. Do not require the heading form "
                        "`<SubclassLocal> (Order: <n>)`, parenthetical range tags, "
                        "one-space-indented identity children, or an `(inherited global "
                        "context)` suffix.",
                        "Require every source-supported in-scope property, relation, exact "
                        "quantity lexeme, and ordering fact to appear in the owning occurrence, "
                        "either in the occurrence prose or as a property-local line. Separate "
                        "occurrences with a blank line. Forbid tables, summaries, representative "
                        "samples, truncation, ellipses, JSON, RDF, refs, and IRIs.",
                        "Preserve every source-supported iteration-owned occurrence required by "
                        "the active T-Box comments and formal OWL/RDFS structure. Leave graph "
                        "construction to the KG-building agent.",
                        "Derive every domain-specific occurrence boundary exclusively from the "
                        "active T-Box projection and structured iteration contract.",
                        "Treat source-grounded datatype/property-closure semantics supplied by "
                        "the deterministic property contract as in-scope semantic evidence even "
                        "when they are not iteration-owned object properties. Do not emit them "
                        "as JSON fields.",
                        "For every entry in generation_contract.semantic_scalar_output_contract, "
                        "include its complete source-grounded value in the owning occurrence.",
                        "Keep object-role identity tokens as written in the source. Lookup may "
                        "verify or reject a mismatch but must not replace that token.",
                        *_semantic_text_natural_ledger_rules(),
                    ]
                )
            else:
                prompt_specific_must.extend(
                    [
                    "Require the ref-entity-relations.v1 hint schema: top-level `entities` and "
                    "`relations` arrays; each entity uses the literal field names `ref`, `class`, "
                    "`label`, and `datatype_properties` (`class`, never `type`); each relation uses "
                    "the literal field names `subject_ref`, `property`, and `object_ref`.",
                    "Require exactly one authoritative JSON object. Do not define a parallel "
                    "`records` output, MAIN wrapper, nested schema-name wrapper, separate duplicate "
                    "hint structure, or legacy `*_label` relationship representation.",
                    ]
                )
                if has_accumulated_hints:
                    prompt_specific_must.extend(
                        [
                    "Treat accumulated prior hints as an identity registry. Reuse an exact prior "
                    "`ref` for the same occurrence, assign distinct refs to distinct non-reusable "
                    "occurrences, and never encode numerical values, roles, top-entity scope, or "
                    "other payload in labels or refs.",
                    "An accumulated identity registry is not a completeness mask: the current "
                    "iteration must still emit every newly evidenced entity and relation owned by "
                    "its declared stage. Never turn identity preservation into a blanket ban on "
                    "creating the current stage's new facts.",
                    "If a required upstream or top-entity ref is absent from the identity registry, "
                    "never create a replacement anchor, unresolved placeholder entity, or invented "
                    "ref. Omit the unresolved relation and let pipeline contract feedback report "
                    "the upstream identity blocker.",
                    "Lexical quantity evidence preserved under datatype_properties for a T-Box "
                    "object property guides later target-node materialization only. Never encode "
                    "it as a subject-to-itself relation, and never emit that relation unless a "
                    "distinct current or accumulated-prior target ref exists.",
                    "Require every relation endpoint to resolve to a current entity ref, an exact "
                    "accumulated-prior ref, or an explicit absolute IRI. Omit blocked relations "
                    "instead of inventing unresolved Equipment, Vessel, or quantity refs.",
                    "Render each reusable class's supplied reuse_scope and match_basis faithfully. "
                    "Never replace class-specific identity criteria with a universal exact-label "
                    "deduplication rule. Preserve every explicit prohibition in match_basis "
                    "verbatim so it cannot be weakened by paraphrase. Non-reusable prior "
                    "occurrences may be resolved only by their exact scoped ref, never "
                    "deduplicated by label.",
                        ]
                    )
                else:
                    prompt_specific_must.extend(
                        [
                            "No accumulated prior-hint registry is available in this runtime "
                            "contract. Assign opaque occurrence-local refs to newly extracted "
                            "entities and do not instruct the runtime to consume undeclared hints.",
                            "Require every relation endpoint to resolve to a current output ref or "
                            "an explicit absolute IRI supplied through an allowed runtime binding. "
                            "Omit blocked relations instead of inventing unresolved refs.",
                        ]
                    )
        if target.name.startswith("KG_BUILDING"):
            prompt_specific_must.extend(
                [
                    "Respect generation_contract.iteration_owned_scope as the closed set of "
                    "primary classes and object properties this iteration may create or add. "
                    "Express that responsibility naturally; no canonical wording is required. "
                    "The only permitted class exception is an exact class listed in "
                    "generation_contract.iteration_spec.linked_materialization_classes; "
                    "preserve its occurrence boundary when present.",
                    (
                        "Consume semantic-text.v1 as an audited natural-language SEMANTIC_HINTS_V1 "
                        "ledger. Recover source-grounded values from the owning occurrence, "
                        "including standalone `P: <lexeme>` lines when present, without "
                        "requiring refs, IDs, JSON fields, datatype_properties objects, or a graph "
                        "layout from extraction. Honor the active T-Box comments, integrity "
                        "annotations, iteration ownership, and linked_materialization_classes "
                        "exactly; do not invent a domain-specific exception."
                        if semantic_hints
                        else "Consume ref-entity-relations.v1 hints by materializing entity records "
                        "first, binding each stable ref to its returned IRI, and then adding relation "
                        "records by resolving subject_ref and object_ref."
                    ),
                    "Enumerate every entry in generation_contract.agent_tool_contract.creator_tools "
                    "by its exact name and exact_call_signature, including every class listed in "
                    "iteration_spec.linked_materialization_classes. Omitting any listed creator "
                    "while instructing its use is a critical error. Render every lifecycle, creator, "
                    "relationship, check, and fixed creator signature on its own canonical line "
                    "with exactly two leading spaces followed by `- ` and the unquoted "
                    "exact_call_signature, for example `  - create_Thing(label: str) -> str`. "
                    "These lines are a machine-readable one-pass interface and must not use "
                    "Markdown backticks around the signature.",
                    "When a creator entry has atomic_operation=true, describe its required_edges "
                    "as creator-owned effects and pass all listed existing-IRI, dependent-label, "
                    "and dependent-datatype parameters in that single call. Never instruct a "
                    "second creator or relationship call for an edge owned by that operation.",
                    "Treat generation_contract.merged_predicate_locals as an exclusion list for "
                    "standalone relationship operations. A merged predicate may be described only "
                    "as an effect of its owning atomic creator: never name, recommend, or invent "
                    "an add_<merged-predicate> call, even when older prose or a per-iteration "
                    "relationship checklist would otherwise request one.",
                    "Separate lookup from reuse authorization: reusable classes use central-memory "
                    "checks under their complete match basis; non-reusable classes use scoped-memory "
                    "checks only to resolve exact already-created occurrence refs and must never be "
                    "deduplicated across occurrences, top entities, or documents.",
                    "Treat an exact prior IRI in the pipeline identity dossier as already authorized "
                    "for that scoped prior fact: init_memory restores its one-hop type, label, and "
                    "relation. Bind that exact IRI directly and do not require a central lookup or "
                    "generic reuse token. Central authorization applies only to generic reusable "
                    "candidates not supplied as exact prior dossier facts.",
                    "If a prior non-reusable object_ref cannot be resolved from scoped memory, report "
                    "an upstream identity/materialization blocker instead of creating a replacement.",
                    "Treat generation_contract.agent_tool_contract as the exact closed-world "
                    "runtime API. Name init_memory and export_memory with their exact parameters, "
                    "and name every invoked create_*, add_*, check_existing_*, and fixed creator "
                    "with the exact parameter names in exact_call_signature. Generic phrases such "
                    "as creator tool, relation-add tool, open memory, or export retained memory "
                    "are not executable instructions.",
                    (
                        "When generation_contract.lexical_quantity_hint_contract.properties is "
                        "non-empty, recover each listed predicate's complete source lexeme from "
                        "the owning semantic-text occurrence paragraph, then perform the fixed-"
                        "quantity-creator→add_* handoff. Never require standalone property-local "
                        "lines, JSON datatype_properties, or an extraction-emitted quantity entity "
                        "for those predicates."
                        if semantic_hints
                        else "When generation_contract.lexical_quantity_hint_contract.properties is "
                        "non-empty, treat each listed predicate as a lexical→fixed-quantity-creator→"
                        "add_* handoff from iteration_hints datatype_properties. Never require an "
                        "extraction-emitted quantity entity or object_ref for those predicates."
                    ),
                    "Pass creator datatype inputs directly by their listed parameter names. Never "
                    "invent a nested `creator_input` object or prefix such as "
                    "`creator_input.sequenceIndex`."
                ]
            )
            if onepass_fragment:
                control_plane_markers = (
                    "closed set of primary classes",
                    "Render every lifecycle",
                    "init_memory restores",
                    "Name init_memory and export_memory",
                )
                prompt_specific_must = [
                    instruction
                    for instruction in prompt_specific_must
                    if not any(
                        marker in instruction for marker in control_plane_markers
                    )
                ]
                prompt_specific_must.extend(
                    [
                        "Render only the creator, relationship, check, and fixed-creator entries "
                        "present in generation_contract.agent_tool_contract, each on its canonical "
                        "machine-readable signature line. Lifecycle tools are deliberately absent "
                        "because the outer combined prompt owns the shared session.",
                        "Express iteration_owned_scope only as this fragment's focused positive "
                        "semantic contribution. Never state or imply that the combined runtime may "
                        "use only this fragment's classes, properties, creators, or hint section.",
                        "Follow generation_contract.onepass_fragment_contract exactly. Omit all "
                        "per-iteration lifecycle, deferral, ignore-other-hints, other-creator "
                        "prohibition, and independent completion language.",
                    ]
                )
        if target.name == "KG_BUILDING_ITER_1.md":
            prompt_specific_must.extend(
                [
                    "Treat the pipeline-seeded Iteration-1 identity lock/dossier as the sole "
                    "authority for root identity. Call init_memory, bind exact locked root URIs, "
                    "attach only source-supported and T-Box-authorized facts when applicable, "
                    "then call export_memory as the final action.",
                    "Never require or invoke a top-root creator in Iteration 1. A missing locked "
                    "identity is an upstream blocker, not permission to mint a replacement.",
                    "Use only the public lifecycle signatures init_memory(doi, "
                    "top_level_entity_name) and export_memory(doi, top_level_entity_name).",
                ]
            )
        return {
            "role": "runtime_prompt_template",
            "must": [
                "Keep the required runtime placeholders so the pipeline can inject source text, "
                "entity context, identifiers, or extracted hints at execution time.",
                (
                    "Implement the current iteration's focused positive semantic contribution "
                    "without turning that focus into a session-wide execution restriction."
                    if onepass_fragment
                    else "Implement only the current iteration or sub-iteration responsibility "
                    "declared by generation_contract.iteration_spec; do not repeat the "
                    "ontology-wide task."
                ),
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
                *prompt_specific_must,
            ],
            "must_not": [
                "Contain pre-populated A-Box individuals, source quotations, quantities, ordered "
                "members, products, or links; those belong to runtime output, not the template.",
                "Replace runtime placeholders with benchmark, fixture, or example facts.",
                "Be judged as a completed extraction or completed KG build before runtime inputs "
                "have been injected.",
                "Invent canonical JSON, fixed section keys, or a serialization syntax that is not "
                "explicitly required by the generation contract's pipeline interchange schema.",
                "Expand an iteration-specific prompt into a full ontology extraction checklist.",
                "Add domain-specific examples, exclusions, or scientific interpretation rules "
                "that are not present in generation_contract.tbox_scope.",
                *(
                    [
                        "Contain init_memory/export_memory instructions or signatures, a standalone "
                        "tool sequence, or an independent success/failure/completion declaration.",
                        "Say this iteration only, defer work to another iteration, ignore ordered "
                        "member hints, or prohibit a creator merely because another iteration owns it.",
                        "Turn this fragment's positive inventory into a union-session restriction "
                        "such as `use only the tools listed above` or `do not introduce classes or "
                        "properties beyond this fragment`. Sibling fragments extend the available "
                        "surface in the combined prompt.",
                    ]
                    if onepass_fragment
                    else []
                ),
            ],
        }
    return {"role": "artifact_specific", "must": []}


def _owned_entity_tool_contracts(
    context: AgenticGenerationContext,
) -> list[dict[str, Any]]:
    """Project creator contracts from deterministic atomic operation units."""
    from src.agents.scripts_and_prompts_generation.materialization_operation_units import (
        compile_materialization_operation_units,
        operation_creator_contracts,
    )

    compiled = compile_materialization_operation_units(
        parsed=getattr(context, "parsed", {}) or {},
        contract=context.contract,
        iteration_plan=getattr(context, "iteration_blueprint", {}) or {},
    )
    if compiled.get("errors"):
        raise ValueError(
            "Invalid materialization operation units: "
            + "; ".join(str(item) for item in compiled["errors"])
        )
    context.contract["materialization_operation_units"] = compiled
    return operation_creator_contracts(compiled)


def _existing_entity_check_contracts(
    context: AgenticGenerationContext,
) -> list[dict[str, Any]]:
    """Describe bounded read-only entity discovery tools from the active T-Box."""
    return existing_entity_check_contracts(
        parsed=getattr(context, "parsed", {}) or {},
        contract=context.contract,
    )


def _existing_entity_check_manifest(
    context: AgenticGenerationContext,
) -> list[str]:
    """Derive the ordered public check manifest from the shared check contracts."""
    return [
        "check_ordered_members",
        *(
            str(item["public_tool"])
            for item in _existing_entity_check_contracts(context)
        ),
    ]


def _required_explicit_ancestor_types(
    context: AgenticGenerationContext,
) -> dict[str, list[str]]:
    """Map concrete ordered-class IRIs to required explicit ancestor IRIs."""
    profile = (getattr(context, "contract", {}) or {}).get(
        "ordered_member_profile"
    ) or {}
    classes = (getattr(context, "parsed", {}) or {}).get("classes") or {}
    locals_ = [
        str(item).strip()
        for item in (profile.get("parent_type_preserving_classes") or [])
        if str(item).strip()
    ]
    if not locals_:
        locals_ = [
            str(item).strip()
            for item in (profile.get("ordered_member_classes") or [])
            if str(item).strip()
        ]
    mapping: dict[str, list[str]] = {}
    for local in locals_:
        class_data = classes.get(local) or {}
        class_iri = str(class_data.get("iri") or "").strip()
        if not class_iri:
            continue
        ancestors = [
            str((classes.get(parent) or {}).get("iri") or "").strip()
            for parent in class_data.get("parent_classes") or []
            if str((classes.get(parent) or {}).get("iri") or "").strip()
        ]
        if ancestors:
            mapping[class_iri] = ancestors
    return mapping


def _generated_runtime_signature(
    generated_module: types.ModuleType, tool_name: str
) -> inspect.Signature:
    """Resolve one public tool through main or its imported runtime modules."""
    candidates: list[Any] = []
    direct = getattr(generated_module, tool_name, None)
    if callable(direct):
        candidates.append(direct)
    for value in vars(generated_module).values():
        if not isinstance(value, types.ModuleType):
            continue
        nested = getattr(value, tool_name, None)
        if callable(nested) and all(nested is not item for item in candidates):
            candidates.append(nested)
    if not candidates:
        raise RuntimeError(
            f"Generated runtime does not expose callable {tool_name!r}; "
            "refusing to fall back to ontology property names in the KG prompt API."
        )
    signatures = {str(inspect.signature(candidate)) for candidate in candidates}
    if len(signatures) != 1:
        raise RuntimeError(
            f"Generated runtime exposes ambiguous signatures for {tool_name!r}: "
            + ", ".join(sorted(signatures))
        )
    return inspect.signature(candidates[0])


def _prompt_agent_tool_contract(
    context: AgenticGenerationContext,
    iteration_owned_scope: dict[str, Any],
    relationship_target_contracts: dict[str, Any],
) -> dict[str, Any]:
    """Project exact Agent-visible names and parameters into KG prompt context."""
    from src.agents.scripts_and_prompts_generation import fixed_rdf_runtime
    from src.agents.scripts_and_prompts_generation.creator_atomicity import (
        resolve_ordering_parameter_name,
    )

    generated_module = None
    scripts_dir_raw = str(getattr(context, "scripts_dir", "") or "").strip()
    generated_main = Path(scripts_dir_raw) / "main.py" if scripts_dir_raw else None
    if generated_main is not None and generated_main.exists():
        from src.agents.scripts_and_prompts_generation.agentic_generation_validation import (
            _import_generated_main_module,
        )

        try:
            generated_module = _import_generated_main_module(
                Path(scripts_dir_raw),
                context.ontology.name,
            )
        except Exception:
            # Stage-local and deterministic prompt projections may run before
            # sibling scripts are complete. Final package validation compares
            # the prompt contract against the importable runtime fail-closed.
            generated_module = None

    def generated_signature(tool_name: str) -> inspect.Signature | None:
        if generated_module is None:
            return None
        try:
            return _generated_runtime_signature(generated_module, tool_name)
        except RuntimeError:
            return None

    relevant_classes = {
        str(value)
        for value in (
            list(iteration_owned_scope.get("classes") or [])
            + list(iteration_owned_scope.get("linked_materialization_classes") or [])
        )
        if str(value)
    }
    for spec in relationship_target_contracts.values():
        relevant_classes.update(
            str(value)
            for value in (
                spec.get("materialization_target_locals")
                or spec.get("range_locals")
                or []
            )
            if str(value)
        )

    creator_tools = []
    for creator in _owned_entity_tool_contracts(context):
        if str(creator.get("class_local") or "") not in relevant_classes:
            continue
        tool_name = str(creator.get("public_tool") or "")
        signature = generated_signature(tool_name)
        ordering_parameter = (
            resolve_ordering_parameter_name(creator, signature)
            if signature is not None and creator.get("ordered_member")
            else str(creator.get("ordering_property_local") or "")
        )
        parameters = [{"name": "label", "python_type": "str", "required": True}]
        semantic_parameter_bindings = {"label": "label"}
        required_parameters: list[dict[str, Any]] = []
        optional_parameters: list[dict[str, Any]] = []

        def add_parameter(
            *,
            name: str,
            python_type: str,
            required: bool,
            semantic_key: str,
        ) -> None:
            if not name or any(
                item.get("name") == name
                for item in [*parameters, *required_parameters, *optional_parameters]
            ):
                return
            semantic_parameter_bindings[semantic_key] = name
            parameter = {
                "name": name,
                "python_type": python_type,
                "required": required,
                "call_syntax": (
                    f"{name}: {python_type}"
                    if required
                    else f"{name}: {python_type} | None = None"
                ),
            }
            (required_parameters if required else optional_parameters).append(parameter)

        for datatype_input in creator.get("datatype_inputs") or []:
            python_type = str(datatype_input.get("python_type") or "str")
            required = bool(datatype_input.get("required"))
            property_local = str(datatype_input.get("property_local") or "")
            parameter_name = (
                ordering_parameter
                if property_local
                == str(creator.get("ordering_property_local") or "")
                and ordering_parameter
                else property_local
            )
            add_parameter(
                name=parameter_name,
                python_type=python_type,
                required=required,
                semantic_key=property_local,
            )
        for edge in creator.get("required_edges") or []:
            if edge.get("target_resolution") == "existing_iri_parameter":
                add_parameter(
                    name=str(edge.get("parameter_name") or ""),
                    python_type=str(edge.get("parameter_type") or "str"),
                    required=True,
                    semantic_key=f"edge:{edge.get('predicate_local')}:existing_iri",
                )
            if edge.get("target_resolution") == "same_operation_create":
                add_parameter(
                    name=str(edge.get("label_parameter") or ""),
                    python_type="str",
                    required=True,
                    semantic_key=f"edge:{edge.get('predicate_local')}:label",
                )
                for dependent_input in edge.get("datatype_inputs") or []:
                    add_parameter(
                        name=str(dependent_input.get("parameter_name") or ""),
                        python_type=str(
                            dependent_input.get("python_type") or "str"
                        ),
                        required=bool(dependent_input.get("required")),
                        semantic_key=(
                            f"edge:{edge.get('predicate_local')}:"
                            f"{dependent_input.get('property_local')}"
                        ),
                    )
        parameters.extend(required_parameters)
        parameters.extend(optional_parameters)
        creator_tools.append(
            {
                "name": tool_name,
                "class_local": str(creator.get("class_local") or ""),
                "parameters": parameters,
                "semantic_parameter_bindings": semantic_parameter_bindings,
                "required_edges": list(creator.get("required_edges") or []),
                "atomic_operation": bool(creator.get("required_edges")),
                "exact_call_signature": (
                    f"{tool_name}{signature}"
                    if signature is not None
                    else f"{tool_name}("
                    + ", ".join(
                        (
                            f"{parameter['name']}: {parameter['python_type']}"
                            if parameter["required"]
                            else str(parameter["call_syntax"])
                        )
                        for parameter in parameters
                    )
                    + ")"
                ),
            }
        )

    merged_predicates = {
        str(value)
        for value in (
            (
                context.contract.get("materialization_operation_units") or {}
            ).get("merged_predicate_locals")
            or []
        )
    }
    relationship_tools = [
        {
            "name": f"add_{property_local}",
            "predicate_local": property_local,
            "exact_call_signature": (
                f"add_{property_local}(subject_iri: str, object_iri: str, "
                "reuse_authorization_token: str | None = None)"
            ),
            "subject_classes": list(spec.get("domain_locals") or []),
            "object_classes": list(spec.get("range_locals") or []),
        }
        for property_local, spec in sorted(relationship_target_contracts.items())
        if property_local not in merged_predicates
    ]
    check_tools = [
        {
            "name": str(item.get("public_tool") or ""),
            "class_local": str(item.get("class_local") or ""),
            "exact_call_signature": (
                f"{item.get('public_tool')}(proposed_entity_json: str = \"\", "
                "*, label: str = \"\")"
            ),
        }
        for item in _existing_entity_check_contracts(context)
        if str(item.get("class_local") or "") in relevant_classes
    ]
    fixed_creator_tools = []
    if any(
        spec.get("fixed_runtime_range_iris")
        for spec in relationship_target_contracts.values()
    ):
        fixed_creator_tools.append(
            {
                "name": "create_om2_quantity",
                "exact_call_signature": (
                    "create_om2_quantity"
                    + str(inspect.signature(fixed_rdf_runtime.create_om2_quantity))
                ),
            }
        )
    return {
        "surface_policy": "closed_world_exact_agent_visible_contract",
        "lifecycle_tools": [
            {
                "name": name,
                "exact_call_signature": f"{name}{inspect.signature(getattr(fixed_rdf_runtime, name))}",
            }
            for name in LIFECYCLE_TOOL_NAMES
        ],
        "creator_tools": creator_tools,
        "fixed_creator_tools": fixed_creator_tools,
        "relationship_tools": relationship_tools,
        "check_tools": check_tools,
        "requirements": [
            "Name every invoked tool exactly; generic phrases such as creator tool, relation-add tool, open memory, or export retained memory are insufficient.",
            "Use parameter names exactly as listed in exact_call_signature; never prefix creator parameters with creator_input.",
        ],
    }


def _semantic_scalar_output_contract(
    context: AgenticGenerationContext,
    iteration_owned_scope: dict[str, Any],
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
                        f"{formats.get(python_type, '<value>')} value in the owning "
                        "occurrence, either in the occurrence prose or as a "
                        "property-local line. Do not drop a source-supported value."
                    ),
                },
            )
            item["required"] = bool(
                item["required"] or datatype_input.get("required")
            )
            item["applicable_creator_tools"].append(
                str(creator.get("public_tool") or "")
            )
    return list(by_property.values())


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
                        (context.contract.get("ontology_publish_contract") or {}).get(
                            "object_properties"
                        )
                        or []
                    )
                    for range_iri in item.get("range_iris") or []
                    if "ontology-of-units-of-measure.org/resource/om-2/"
                    in str(range_iri)
                }
            ),
        }
    if name.endswith("_creation_relationships.py"):
        from src.agents.scripts_and_prompts_generation.materialization_operation_units import (
            standalone_relationship_tool_contracts,
        )

        _owned_entity_tool_contracts(context)
        operation_units = (
            context.contract.get("materialization_operation_units") or {}
        )
        return {
            "ontology_name": context.ontology.name,
            "relationship_tool_contracts": standalone_relationship_tool_contracts(
                context.contract.get("relationship_tool_contracts") or {},
                operation_units,
            ),
            "merged_predicate_locals": list(
                operation_units.get("merged_predicate_locals") or []
            ),
            "external_class_creators": (
                context.contract.get("external_class_creators") or []
            ),
        }
    if name.endswith("_creation_checks.py"):
        creator_contracts = _owned_entity_tool_contracts(context)
        existing_checks = _existing_entity_check_contracts(context)
        profile = context.contract.get("ordered_member_profile") or {}
        classes = context.parsed.get("classes") or {}
        properties = context.parsed.get("properties") or {}
        member_locals = list(profile.get("individually_linked_object_properties") or [])
        order_locals = list(profile.get("single_valued_ordering_properties") or [])
        return {
            "ontology_name": context.ontology.name,
            "existing_entity_check_contracts": existing_checks,
            "expected_public_manifest": _existing_entity_check_manifest(context),
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
                "required_explicit_ancestor_types": (
                    _required_explicit_ancestor_types(context)
                ),
                "ancestor_algorithm": {
                    "source": "ordered_check_contract.required_explicit_ancestor_types",
                    "authoritative": True,
                    "rule": (
                        "If a linked member has an explicit rdf:type that is a key in "
                        "required_explicit_ancestor_types, every listed ancestor IRI must "
                        "also appear as an explicit rdf:type on that same member. Missing "
                        "any mapped ancestor emits missing_explicit_ancestor_type. Do not "
                        "infer ancestor types."
                    ),
                    "generic_example": {
                        "setup": (
                            "required_explicit_ancestor_types maps one concrete ordered-class "
                            "IRI to one ancestor IRI. Two members are linked to the same "
                            "parent with valid contiguous orders 1 and 2."
                        ),
                        "invalid_member": (
                            "typed only as the concrete subclass; the mapped ancestor type "
                            "is absent"
                        ),
                        "valid_member": (
                            "typed as the concrete subclass and the mapped ancestor"
                        ),
                        "required_violation_codes": [
                            "missing_explicit_ancestor_type"
                        ],
                        "non_violations": [
                            "Do not emit this code because a member is typed only as the ancestor.",
                            "Do not emit this code because a member lacks any type from the ordered class family.",
                        ],
                    },
                    "forbidden_reinterpretations": [
                        "member must have some rdf:type from the ordered class family",
                        "member must have a concrete subclass type instead of only the ancestor",
                        "RDFS/OWL inference of ancestor types",
                    ],
                },
                "violation_codes": [
                    "missing_order",
                    "multiple_orders",
                    "invalid_order",
                    "duplicate_order",
                    "non_contiguous_order",
                    "invalid_parent_count",
                    "invalid_owned_dependent_count",
                    "owned_dependent_wrong_type",
                    "owned_dependent_not_exclusive",
                    "multiple_parents",
                    "missing_explicit_ancestor_type",
                ],
                "output_schema": {
                    "status": {
                        "valid_graph": "ok",
                        "invalid_graph": "rejected",
                    },
                    "violations": {
                        "type": "array",
                        "item_type": "object",
                        "required_fields": ["code"],
                        "code_field": {
                            "name": "code",
                            "type": "string",
                            "allowed_values_source": "ordered_check_contract.violation_codes",
                        },
                        "forbidden_discriminator_aliases": [
                            "violation_code",
                            "type",
                            "kind",
                        ],
                        "example": {
                            "code": "missing_order",
                            "member": "urn:example:ordered-member",
                        },
                    },
                },
                "operation_invariants": [
                    {
                        "owner_class_local": creator.get("class_local"),
                        "owner_class_iri": creator.get("class_iri"),
                        "required_edges": list(
                            creator.get("required_edges") or []
                        ),
                    }
                    for creator in creator_contracts
                    if creator.get("required_edges")
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
            **(
                {
                    "commit_gate_contract": {
                        "check_tool": "check_ordered_members",
                        "run_immediately_before_export": True,
                        "block_export_on_rejection": True,
                        "operation_invariants": [
                            {
                                "owner_class_iri": creator.get("class_iri"),
                                "required_edges": list(
                                    creator.get("required_edges") or []
                                ),
                            }
                            for creator in _owned_entity_tool_contracts(context)
                            if creator.get("required_edges")
                        ],
                    }
                }
                if (
                    (
                        context.contract.get("materialization_operation_units")
                        or {}
                    ).get("merged_predicate_locals")
                )
                else {}
            ),
        }
    if target is not None and target.suffix == ".md":
        return _prompt_artifact_generation_contract(context, target)
    return _generation_contract_projection(context)


def _iteration_has_semantic_scope(iteration: dict[str, Any]) -> bool:
    semantic_scope = iteration.get("semantic_scope") or {}
    if any(
        isinstance(item, dict) and str(item.get("local") or "").strip()
        for item in (semantic_scope.get("classes") or [])
    ):
        return True
    responsibilities = iteration.get("responsibilities") or {}
    return any(str(item).strip() for item in (responsibilities.get("classes") or []))


def _enrich_iteration_spec_with_compiled_scope(
    context: AgenticGenerationContext, iteration: dict[str, Any]
) -> dict[str, Any]:
    """Attach compiled semantic scope when runtime iterations omit it."""
    if _iteration_has_semantic_scope(iteration):
        return iteration
    compiled_iterations = [
        item
        for item in (getattr(context, "iteration_blueprint", {}) or {}).get(
            "iterations"
        )
        or []
        if isinstance(item, dict) and _iteration_has_semantic_scope(item)
    ]
    if not compiled_iterations:
        return iteration
    semantic_source = next(
        (
            item
            for item in compiled_iterations
            if item.get("iteration_number") == iteration.get("iteration_number")
        ),
        compiled_iterations[0] if len(compiled_iterations) == 1 else None,
    )
    if semantic_source is None:
        return iteration
    enriched = dict(iteration)
    if semantic_source.get("responsibilities"):
        enriched["responsibilities"] = dict(semantic_source["responsibilities"])
    if semantic_source.get("semantic_scope"):
        enriched["semantic_scope"] = dict(semantic_source["semantic_scope"])
    return enriched


_ONEPASS_KG_PROMPT_RE = re.compile(
    r"^KG_BUILDING_ITER_(?P<iteration>\d+)_ONEPASS\.md$"
)


def _is_onepass_kg_fragment(target: Path | None) -> bool:
    return bool(target is not None and _ONEPASS_KG_PROMPT_RE.fullmatch(target.name))


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
            f"KG_BUILDING_ITER_{iter_token}_ONEPASS",
            f"PRE_EXTRACTION_ITER_{iter_token}",
        }
        if stem in candidates:
            return _enrich_iteration_spec_with_compiled_scope(
                context,
                {
                    key: value
                    for key, value in iteration.items()
                    if key not in {"sub_iterations"}
                },
            )
        for sub_iteration in iteration.get("sub_iterations") or []:
            if not isinstance(sub_iteration, dict):
                continue
            sub_token = str(sub_iteration.get("iteration_number") or "").replace(
                ".", "_"
            )
            if stem == f"EXTRACTION_ITER_{sub_token}":
                parent = _enrich_iteration_spec_with_compiled_scope(
                    context,
                    {
                        key: value
                        for key, value in iteration.items()
                        if key not in {"sub_iterations"}
                    },
                )
                return {
                    "parent_iteration": parent,
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
    scope_owner = (
        iteration_spec.get("parent_iteration")
        if isinstance(iteration_spec.get("parent_iteration"), dict)
        else iteration_spec
    )
    if not isinstance(scope_owner, dict):
        scope_owner = {}
    semantic_scope = scope_owner.get("semantic_scope") or {}
    focus_classes = {
        str(item.get("local") or "").strip()
        for item in semantic_scope.get("classes") or []
        if isinstance(item, dict) and str(item.get("local") or "").strip()
    }
    if bool(scope_owner.get("has_pre_extraction")) or bool(
        scope_owner.get("requires_pre_extraction")
    ):
        focus_classes.update(
            str(value).strip()
            for value in (
                (context.contract.get("ordered_member_profile") or {}).get(
                    "ordered_member_classes"
                )
                or []
            )
            if str(value).strip() in classes
        )
    focus_properties = {
        str(item.get("local") or "").strip()
        for item in semantic_scope.get("object_properties") or []
        if isinstance(item, dict) and str(item.get("local") or "").strip()
    }
    materialization_classes = {
        str(value).strip()
        for value in scope_owner.get("linked_materialization_classes") or []
        if str(value).strip() in classes
    }
    if not focus_classes:
        responsibilities = scope_owner.get("responsibilities") or {}
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
    if not focus_classes and not focus_properties:
        raise ValueError(
            "Prompt generation requires a non-empty semantic_scope "
            "(classes or object_properties) for iteration "
            f"{scope_owner.get('iteration_number')!r}; refusing to invent scope from "
            "the top entity fallback"
        )
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
            for value in ((spec or {}).get("domains") or [(spec or {}).get("domain")])
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
    for local, spec in properties.items():
        if str((spec or {}).get("kind") or "") != "datatype":
            continue
        if str(local) in relevant_properties:
            continue
        domains = {
            str(value)
            for value in ((spec or {}).get("domains") or [(spec or {}).get("domain")])
            if str(value or "").strip()
        }
        if not (domains & materialization_classes):
            continue
        relevant_properties[str(local)] = {
            "iri": str((spec or {}).get("iri") or ""),
            "kind": "datatype",
            "domains": sorted(domains),
            "range": str((spec or {}).get("range") or "").strip(),
            "comment": str((spec or {}).get("comment") or ""),
        }
    visible_classes = set(focus_classes) | materialization_classes
    return {
        "classes": {
            str(local): {
                "iri": str((classes.get(local) or {}).get("iri") or ""),
                "parent_classes": list(
                    (classes.get(local) or {}).get("parent_classes") or []
                ),
                "comment": str((classes.get(local) or {}).get("comment") or ""),
            }
            for local in sorted(visible_classes)
        },
        "properties": relevant_properties,
    }


def _iteration_owned_scope(
    iteration_spec: dict[str, Any],
    *,
    materializable_class_locals: set[str] | None = None,
) -> dict[str, list[str]]:
    """Return the exact compiled class/property ownership surface for one prompt."""
    scope_owner = (
        iteration_spec.get("parent_iteration")
        if isinstance(iteration_spec.get("parent_iteration"), dict)
        else iteration_spec
    )
    if not isinstance(scope_owner, dict):
        scope_owner = {}
    semantic_scope = scope_owner.get("semantic_scope") or {}
    classes = [
        str(item.get("local") or "").strip()
        for item in semantic_scope.get("classes") or []
        if isinstance(item, dict) and str(item.get("local") or "").strip()
    ]
    object_properties = [
        str(item.get("local") or "").strip()
        for item in semantic_scope.get("object_properties") or []
        if isinstance(item, dict) and str(item.get("local") or "").strip()
    ]
    responsibilities = scope_owner.get("responsibilities") or {}
    if not classes:
        classes = [
            str(value).strip()
            for value in responsibilities.get("classes") or []
            if str(value).strip()
        ]
    if not object_properties:
        object_properties = [
            str(value).strip()
            for value in responsibilities.get("object_properties") or []
            if str(value).strip()
        ]
    materialization_classes = [
        str(value).strip()
        for value in scope_owner.get("linked_materialization_classes") or []
        if str(value).strip()
    ]
    if materializable_class_locals is not None:
        from src.agents.scripts_and_prompts_generation.materialization_closure import (
            restrict_classes_to_creator_surface,
        )

        classes = restrict_classes_to_creator_surface(
            classes, materializable_class_locals
        )
        materialization_classes = restrict_classes_to_creator_surface(
            materialization_classes, materializable_class_locals
        )
    return {
        "classes": list(dict.fromkeys(classes)),
        "object_properties": list(dict.fromkeys(object_properties)),
        "linked_materialization_classes": list(
            dict.fromkeys(materialization_classes)
        ),
    }


def _subclass_decision_contract(tbox_scope: dict[str, Any]) -> dict[str, Any]:
    """Derive a domain-neutral subclass checklist contract from one T-Box slice."""
    classes = tbox_scope.get("classes") or {}
    decision_points: list[dict[str, Any]] = []
    for parent_local, parent_spec in sorted(classes.items()):
        candidates = [
            {
                "class_local": str(child_local),
                "comment": str((child_spec or {}).get("comment") or ""),
            }
            for child_local, child_spec in sorted(classes.items())
            if str(parent_local)
            in {
                str(value)
                for value in (child_spec or {}).get("parent_classes") or []
            }
        ]
        if candidates:
            decision_points.append(
                {
                    "parent_class_local": str(parent_local),
                    "parent_comment": str((parent_spec or {}).get("comment") or ""),
                    "candidate_subclasses": candidates,
                }
            )
    return {
        "schema_version": "tbox-subclass-decision-contract.v1",
        "source": "generation_contract.tbox_scope",
        "decision_points": decision_points,
        "checklist_requirements": [
            "Build the runtime subclass decision checklist only from these decision points and "
            "their verbatim T-Box comments.",
            "For every evidence atom that may instantiate a listed parent, evaluate every "
            "candidate subclass's positive conditions, exclusions, and disambiguation rules.",
            "Select one most-specific supported subclass, or mark the atom unresolved when the "
            "T-Box-derived evidence threshold is not met; never silently omit it.",
            "Do not add ontology-local triggers, examples, priorities, or exceptions from model "
            "knowledge, source fixtures, configuration, or this meta-contract.",
        ],
    }


def _warning_marked_tbox_contract(tbox_scope: dict[str, Any]) -> dict[str, Any]:
    """Project high-risk T-Box comments without adding domain-specific semantics."""
    marked: list[dict[str, str]] = []
    for section in ("classes", "properties"):
        for local, spec in sorted((tbox_scope.get(section) or {}).items()):
            comment = str((spec or {}).get("comment") or "")
            if "【Warning】" in comment:
                marked.append(
                    {
                        "section": section,
                        "local": str(local),
                        "comment": comment,
                    }
                )
    return {
        "schema_version": "warning-marked-tbox-contract.v1",
        "marker": "【Warning】",
        "marked_comments": marked,
        "requirements": [
            "Treat every marked comment as a high-risk class or field boundary requiring an "
            "explicit source-to-rule comparison before selecting or excluding that class or field.",
            "Evaluate the complete marked comment, including its positive threshold, exclusions, "
            "priority rules, and non-duplication rules; a lexical resemblance alone is insufficient.",
            "Compare all applicable marked alternatives before making the choice, and prefer "
            "unresolved or omitted output when the marked evidence threshold is not met.",
            "Do not derive any domain-specific trigger, example, class priority, or exception from "
            "this generic marker contract; all semantics must come from the marked T-Box comments.",
        ],
    }


def _subclass_comment_projection(tbox_scope: dict[str, Any]) -> dict[str, Any]:
    """Project every scoped subclass annotation without ontology-specific rules."""
    classes = tbox_scope.get("classes") or {}
    subclass_annotations: list[dict[str, Any]] = []
    for child_local, child_spec in sorted(classes.items()):
        for parent_local in sorted(
            {
                str(value)
                for value in (child_spec or {}).get("parent_classes") or []
                if str(value) in classes
            }
        ):
            subclass_annotations.append(
                {
                    "parent_class_local": parent_local,
                    "subclass_local": str(child_local),
                    "comment": str((child_spec or {}).get("comment") or ""),
                }
            )
    return {
        "schema_version": "tbox-subclass-comment-projection.v1",
        "source": "generation_contract.tbox_scope.classes",
        "subclasses": subclass_annotations,
        "requirements": [
            "Apply every projected subclass comment when deciding "
            "whether a source occurrence belongs to that subclass.",
            "Do not add ontology-local class names, triggers, or exceptions outside this "
            "T-Box-derived projection.",
        ],
    }


def _is_enrichment_iteration_spec(iteration_spec: Mapping[str, Any] | None) -> bool:
    """True only for compiled sub-iteration / enrichment prompt targets."""
    spec = iteration_spec or {}
    return bool(spec.get("parent_iteration")) or bool(spec.get("sub_iteration"))


def _lexical_quantity_hint_contract(
    context: AgenticGenerationContext,
    tbox_scope: dict[str, Any],
    *,
    role: str = "extraction",
    hint_representation: str = "",
) -> dict[str, Any]:
    """Project pipeline-materializable quantity links without domain assumptions."""
    scoped_properties = set((tbox_scope.get("properties") or {}).keys())
    properties = []
    for item in context.contract.get("om2_quantity_properties") or []:
        predicate_local = str((item or {}).get("predicate_local") or "").strip()
        if not predicate_local or predicate_local not in scoped_properties:
            continue
        properties.append(
            {
                "predicate_local": predicate_local,
                "predicate_iri": str((item or {}).get("predicate_iri") or "").strip(),
                "domain_locals": [
                    value.strip()
                    for value in str((item or {}).get("domain_locals") or "").split(",")
                    if value.strip()
                ],
                "range_iris": [
                    value.strip()
                    for value in str((item or {}).get("range_iris") or "").split(",")
                    if value.strip()
                ],
            }
        )
    semantic_text = str(hint_representation or "").strip() == "semantic-text.v1"
    if role == "kg":
        if semantic_text:
            rules = [
                "For every listed predicate P, if the semantic-text.v1 iteration_hints ledger "
                "states a source-grounded value for P as a standalone `P: <lexeme>` line under "
                "the owning occurrence, recover that complete exact lexeme, call the "
                "fixed/runtime quantity creator from "
                "relationship_target_contracts[P].creator_tools (use the lexeme as that creator's "
                "label input), then assert P with the matching add_* tool using the returned "
                "quantity IRI.",
                "Do not expect extraction to emit a quantity entity, object_ref, JSON object, or "
                "datatype_properties field for a listed predicate; the standalone key-value line "
                "is the authorized interchange for later quantity-node materialization.",
                "This interchange does not reclassify the T-Box object property as a datatype "
                "property.",
                "When the ledger line is present, do not invent an alternate label source or "
                "numerical normalization. When it is absent, omit the quantity link rather than "
                "inventing a value.",
            ]
        else:
            rules = [
                "For every listed predicate P whose complete lexical value appears under a source "
                "entity's datatype_properties[P] in iteration_hints, read that exact lexeme, call "
                "the exact fixed/runtime quantity creator from "
                "relationship_target_contracts[P].creator_tools (use the lexeme as that creator's "
                "label input), then assert P with the matching add_* tool using the returned "
                "quantity IRI.",
                "Do not expect extraction to emit a quantity entity, object_ref, or relation for a "
                "listed predicate; the lexical datatype_properties field is the authorized "
                "interchange for later quantity-node materialization.",
                "This interchange does not reclassify the T-Box object property as a datatype "
                "property.",
                "When the lexical field is present, do not invent an alternate label source or "
                "numerical normalization. When it is absent, omit the quantity link rather than "
                "inventing a value.",
            ]
    elif semantic_text:
        rules = [
            "For every explicit source quantity owned by one listed predicate, preserve the "
            "complete source lexical value in the owning occurrence, either in the occurrence "
            "prose or as a `<predicate_local>: <lexeme>` line.",
            "Those lexemes are the pipeline interchange for later quantity-node "
            "materialization; this does not reclassify the T-Box object property as a datatype "
            "property and must not be emitted as JSON datatype_properties.",
            "Do not require or invent a quantity object ref, and do not omit the lexical value "
            "merely because the quantity target class is outside the extraction entity scope.",
            "Preserve qualitative values and complete multiplicity expressions without inventing "
            "a numerical normalization.",
        ]
    else:
        rules = [
            "For every explicit source quantity owned by one listed predicate, preserve the "
            "complete source lexical value under that predicate local in the source entity's "
            "datatype_properties object.",
            "This is a pipeline interchange exception for later deterministic quantity-node "
            "materialization; it does not reclassify the T-Box object property as a datatype property.",
            "Do not require or invent a quantity object ref, and do not omit the lexical value "
            "merely because the quantity target class is outside the extraction entity scope.",
            "Preserve qualitative values and complete multiplicity expressions without inventing "
            "a numerical normalization.",
        ]
    return {
        "schema_version": "pipeline-lexical-quantity-hints.v1",
        "role": role,
        "hint_representation": str(hint_representation or "").strip() or None,
        "properties": properties,
        "rules": rules,
    }


def _pre_extraction_candidate_type_contract(
    context: AgenticGenerationContext,
    tbox_scope: dict[str, Any],
    subclass_decision_contract: dict[str, Any],
) -> dict[str, Any]:
    """Derive the closed PRE ledger type surface from T-Box and reuse policy."""
    scoped_classes = tbox_scope.get("classes") or {}
    parent_classes = {
        str(item.get("parent_class_local") or "").strip()
        for item in subclass_decision_contract.get("decision_points") or []
        if str(item.get("parent_class_local") or "").strip()
    }
    non_reusable = {
        str(item.get("class_local") or "").strip()
        for item in ((context.contract.get("reuse_policy") or {}).get("classes") or [])
        if isinstance(item, dict)
        and item.get("reusable") is False
        and str(item.get("class_local") or "").strip()
    }
    parsed_classes = context.parsed.get("classes") or {}
    ordered_members = {
        str(value).strip()
        for value in (
            (context.contract.get("ordered_member_profile") or {}).get(
                "ordered_member_classes"
            )
            or []
        )
        if str(value).strip() in parsed_classes
    }
    parent_classes.update(
        str(parent).strip()
        for spec in parsed_classes.values()
        for parent in ((spec or {}).get("parent_classes") or [])
        if str(parent).strip() in parsed_classes
    )
    linked_non_reusable_ranges = {
        str((spec or {}).get("range") or "").strip()
        for spec in (tbox_scope.get("properties") or {}).values()
        if str((spec or {}).get("range") or "").strip() in non_reusable
        and str((spec or {}).get("range") or "").strip() in parsed_classes
    }
    candidate_universe = (
        set(scoped_classes)
        | ordered_members
        | linked_non_reusable_ranges
    )
    prohibited = {
        str(local)
        for local in candidate_universe
        for spec in [scoped_classes.get(local) or parsed_classes.get(local) or {}]
        if (spec or {}).get("creatable") is False
    } | prohibited_class_locals(context.contract.get("reuse_policy"))
    allowed = sorted(
        (candidate_universe & non_reusable) - parent_classes - prohibited
    )
    return {
        "schema_version": "tbox-pre-ledger-candidate-types.v1",
        "allowed_candidate_types": allowed,
        "rules": [
            "candidate_types is a closed enumeration: use only allowed_candidate_types.",
            "Do not place reusable context, equipment, environment, supplier, or "
            "classification classes in candidate_types; preserve their wording only inside "
            "verbatim evidence or candidate_properties when the property surface allows it.",
            "Every evidence atom must contain at least one allowed candidate type. A location, "
            "equipment, environment, duration, or other contextual "
            "fact must never become a standalone evidence atom with empty candidate_types. "
            "Attach it as a candidate_property to the nearest source-supported operation whose "
            "property surface permits it; if no owned operation can carry it, retain it only in "
            "scope/verbatim context rather than inventing a type or emitting an empty array.",
            "verbatim_quote must be one contiguous substring copied character-for-character "
            "from the supplied source. When an entity-specific condition is embedded in a "
            "shared sentence, quote the complete unchanged source sentence; never splice, "
            "specialize, normalize, or paraphrase it into an entity-specific sentence.",
        ],
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
    configured_local = str(iter1_rules.get("top_level_entity_name") or "").strip()
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
            "role": "locked_top_entity_abox_binding",
            "input_semantics": (
                "The mechanically injected top_entities value is the exact output of "
                "EXTRACTION_ITER_1 and identifies zero or more source-supported top-entity "
                "labels. The active T-Box projection supplies their top class, and the "
                "pipeline-owned identity lock supplies their exact pre-seeded IRIs."
            ),
            "required_sequence": [
                "Call init_memory with the supplied document identifier and the exact "
                "orchestrator-owned shared memory scope injected at runtime. Never hardcode the "
                "T-Box class name or an extracted entity label as top_level_entity_name.",
                "Bind every root label to the exact pre-seeded URI in the pipeline identity lock "
                "or identity dossier; the lock is the sole root-identity authority.",
                "Bind every pipeline-owned required-link target to its exact pre-seeded identity. "
                "For a DOI-identified Document, call its creator with the exact DOI label only as "
                "an idempotent bind to the existing locked IRI; never mint a substitute target.",
                "When the active T-Box and supplied source evidence permit facts owned by this "
                "pass, attach only those facts to the locked subjects.",
                "Do not call any top-root creator or mint, replace, retype, or deduplicate a root.",
                "Call export_memory with the same document identifier and scope as the final tool action.",
            ],
            "scope_policy": (
                "Use the runtime scope mechanically appended from iter1_top_entity_kg."
                "global_state_entity_name. It is a shared memory context label, distinct from "
                "both the T-Box top class and every source-supported top-entity label. Never "
                "invent, derive, or hardcode that scope in the authored prompt."
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
                "The Agent-visible lifecycle is exactly init_memory(doi, top_level_entity_name) "
                "and export_memory(doi, top_level_entity_name). No top-root creator participates "
                "in this iteration."
            ),
            "identity_policy": (
                "Treat the pipeline identity lock/dossier as authoritative. Bind labels to its "
                "exact URIs and fail with an upstream identity blocker when a hinted root is absent."
            ),
            "empty_input_policy": (
                "Do not invent a root when no source-supported top-entity hint is present."
            ),
            "domain_neutrality": (
                "Do not enumerate domain-specific child types, relations, examples, or exclusions."
            ),
        }
    if _is_onepass_kg_fragment(target):
        return {
            "role": "onepass_iteration_semantic_fragment",
            "input_semantics": (
                "The runtime iteration_hints slot denotes this iteration's labelled section "
                "inside the combined all-iteration semantic ledger. This fragment contributes "
                "focused positive materialization semantics to one shared execution session."
            ),
            "required_sequence": [
                "Interpret the matching iteration hint section using this iteration's T-Box "
                "scope and materialization contracts.",
                "Materialize every source-supported responsibility positively owned by this "
                "fragment while preserving occurrence identity and creator atomicity.",
                "Leave session lifecycle and whole-graph completion to the combined one-pass "
                "controller outside this fragment.",
            ],
            "composition_policy": (
                "Iteration ownership limits which positive semantics this fragment contributes; "
                "it must not become a global ban on creators or hint sections contributed by "
                "other one-pass fragments."
            ),
            "domain_neutrality": (
                "All concrete classes, properties, checks, and creators must come from the active "
                "T-Box projection or generated tool surface."
            ),
        }
    if target.name.startswith("KG_BUILDING_ITER_"):
        return {
            "role": "iteration_hint_materialization",
            "input_semantics": (
                "The runtime iteration_hints slot carries the extraction hints produced for this "
                "iteration and is the primary materialization authority for extracted facts. "
                "Pipeline-owned runtime bindings and required-link contracts are independent "
                "authorities for deterministic provenance and scope links."
            ),
            "required_sequence": [
                "Always open or resume scoped retained memory before checks or mutations.",
                "For potentially pre-existing hinted entities, use the exact T-Box-derived "
                "existing-entity checks supplied by the generated tool surface before creation.",
                "Create or reuse only entities present in the current iteration hints, except "
                "deterministic targets explicitly supplied by a pipeline_required_link_contract.",
                "Assert only current-iteration T-Box-compatible links justified by those hints "
                "or explicitly required by a pipeline_required_link_contract.",
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
    return {
        "top_entity": {
            "class_local": class_local,
            "class_iri": class_iri,
            "allows_multiple_source_roots": bool(top.get("iter1_allows_multiple")),
            "reuse_scoped_root": bool(top.get("main_pass_reuses_scoped_root")),
            "identity_authority": "pipeline_seeded_iter1_identity_lock",
            "creation_forbidden": True,
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
    onepass_fragment = _is_onepass_kg_fragment(target)
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
    elif target.name == "KG_BUILDING_ITER_1.md" and not is_extension:
        runtime_slots = ["{doi}", "{paper_content}", "{top_entities}"]
        tbox_scope = _top_entity_tbox_projection(context)
        required_links: list[dict[str, Any]] = []
    else:
        runtime_slots = (
            (
                list(EXTENSION_KG_RUNTIME_SLOTS)
                if target.name.startswith("KG_BUILDING_ITER_")
                else list(EXTENSION_EXTRACTION_RUNTIME_SLOTS)
            )
            if is_extension
            else (
                ["{iteration_hints}", "{doi}", "{entity_label}", "{entity_uri}"]
                if target.name.startswith("KG_BUILDING_ITER_")
                else [
                    "{paper_content}",
                    "{entity_label}",
                    "{entity_uri}",
                    "{accumulated_hints}",
                ]
            )
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
    if (
        target.name != "KG_BUILDING_ITER_1.md"
        and not target.name.startswith("KG_BUILDING_ITER_")
        and isinstance(configured_inputs, dict)
        and configured_inputs.get("file_path")
    ):
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
                    "one owned operation per evidence atom; operation-local context facts are "
                    "properties of the nearest source-supported operation, never standalone atoms"
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
                            "target's exclusive continuation. If one continuous "
                            "passage produces a shared intermediate and then names "
                            "several distinct outcomes, copy the unsplit prefix "
                            "into this target and exclude sibling exclusive "
                            "continuations."
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
                    "Store verbatim sequence language only in ordering_cue. During PRE, "
                    "never emit a property listed in normalized_output_ordering_properties."
                ),
                "completion_obligations": [
                    "The current target's complete producing workflow has been "
                    "located and recorded, including any consumed same-source prefix.",
                    "Every same-source dependency from the target is resolved.",
                    "Every referenced source scope is scanned from beginning to end.",
                    "Every explicit modification has been applied or marked unresolved.",
                    "Every effective owned operation has one evidence atom.",
                    "Every explicit operation-local context fact is attached as a property to "
                    "the nearest source-supported compatible operation; no context-only or "
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
                "properties/relations/quantity "
                "lexemes written in occurrence prose or as property-local lines. Do "
                "not emit JSON, RDF, refs, IRIs, quantity nodes, tool calls, or "
                "graph layout."
            ),
            "silent_omission_forbidden": True,
            "preserve_source_order": True,
            "final_self_audit": [
                "Every source-supported iteration-owned occurrence appears once in source order.",
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
    elif target.name.startswith("KG_BUILDING_ITER_"):
        lexical_quantity_hint_contract = _lexical_quantity_hint_contract(
            context,
            tbox_scope,
            role="kg",
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
    if target.name.startswith(
        ("KG_BUILDING_ITER_", "EXTRACTION_ITER_", "PRE_EXTRACTION_ITER_")
    ):
        from src.agents.scripts_and_prompts_generation.materialization_closure import (
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
    if target.name.startswith("KG_BUILDING_ITER_"):
        from src.agents.scripts_and_prompts_generation.materialization_operation_units import (
            standalone_relationship_tool_contracts,
        )

        relationship_contracts_for_prompt = standalone_relationship_tool_contracts(
            relationship_contracts_for_prompt,
            context.contract.get("materialization_operation_units") or {},
        )
    relationship_target_contracts = {
        property_local: dict(spec)
        for property_local, spec in relationship_contracts_for_prompt.items()
        if property_local in set(iteration_owned_scope.get("object_properties") or [])
    }
    pipeline_required_link_contracts: list[dict[str, Any]] = []
    if target.name.startswith("KG_BUILDING_ITER_"):
        all_relationship_contracts = (
            context.contract.get("relationship_tool_contracts") or {}
        )
        iteration_match = re.fullmatch(
            r"KG_BUILDING_ITER_(\d+)(?:_ONEPASS)?\.md", target.name
        )
        current_prompt_iteration = int(
            float(
                iteration_spec.get("iteration_number")
                or (iteration_match.group(1) if iteration_match else 0)
            )
        )
        for binding in (
            context.contract.get("runtime_required_link_bindings") or []
        ):
            if (
                int(binding.get("materialization_iteration") or 0)
                != current_prompt_iteration
            ):
                continue
            predicate_iri = str(binding.get("predicate_iri") or "").strip()
            identity_slot = str(binding.get("identity_slot") or "").strip()
            if identity_slot not in runtime_slots:
                raise ValueError(
                    f"{target.name}: required-link identity slot {identity_slot!r} "
                    "is not available in this prompt's runtime binding contract"
                )
            matching_links = [
                link
                for link in (
                    context.contract.get("required_links") or required_links
                )
                if str(link.get("predicate_iri") or "").strip() == predicate_iri
            ]
            if not matching_links:
                raise ValueError(
                    "runtime.required_link_bindings references a predicate that is "
                    f"not a T-Box required link: {predicate_iri}"
                )
            for link in matching_links:
                subject_iri = str(link.get("subject_class_iri") or "")
                subject_local = subject_iri.rsplit("#", 1)[-1].rsplit("/", 1)[-1]
                target_iri = str(link.get("target_class_iri") or "")
                target_local = target_iri.rsplit("#", 1)[-1].rsplit("/", 1)[-1]
                predicate_local = predicate_iri.rsplit("#", 1)[-1].rsplit(
                    "/", 1
                )[-1]
                pipeline_required_link_contracts.append(
                    {
                        "subject_class_iri": subject_iri,
                        "subject_class_local": subject_local,
                        "predicate_iri": predicate_iri,
                        "predicate_local": predicate_local,
                        "target_class_iri": target_iri,
                        "target_class_local": target_local,
                        "identity_source_slot": identity_slot,
                        "materialization_iteration": current_prompt_iteration,
                        "identity_semantics": (
                            f"The {identity_slot} runtime binding is the exact "
                            "pipeline-owned target identity for this required link."
                        ),
                        "extraction_policy": (
                            "Do not invent or emit the pipeline-owned target solely to "
                            "satisfy this link; KG materialization receives its identity "
                            "through the declared runtime binding."
                        ),
                        "kg_materialization_policy": (
                            "After init_memory, the exact bound target identity is already "
                            "typed in scoped memory. Reuse that IRI as the subject of this "
                            "required link. Calling the matching create_* tool is allowed "
                            "only when it adopts the bound IRI; never mint a replacement. "
                            "Extraction hints need not repeat the pipeline-owned target."
                        ),
                    }
                )
                required_relationship = all_relationship_contracts.get(
                    predicate_local
                )
                if not isinstance(required_relationship, dict):
                    raise ValueError(
                        "required-link binding has no generated relationship tool "
                        f"contract: {predicate_local}"
                    )
                if predicate_local in relationship_contracts_for_prompt:
                    relationship_target_contracts.setdefault(
                        predicate_local, dict(required_relationship)
                    )
    prompt_agent_scope = {
        **iteration_owned_scope,
        "linked_materialization_classes": list(
            iteration_spec.get("linked_materialization_classes") or []
        ),
    }
    agent_tool_contract = (
        _prompt_agent_tool_contract(
            context,
            prompt_agent_scope,
            relationship_target_contracts,
        )
        if target.name.startswith("KG_BUILDING_ITER_")
        else {}
    )
    if onepass_fragment:
        agent_tool_contract = {
            **agent_tool_contract,
            "lifecycle_tools": [],
        }
    semantic_scalar_output_contract = (
        _semantic_scalar_output_contract(context, iteration_owned_scope)
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
                "role": (
                    "extension_scoped_materialization"
                    if target.name.startswith("KG_BUILDING_ITER_")
                    else "extension_scoped_extraction"
                ),
                "scope_policy": (
                    "Reuse the inherited upstream scoped root URI and label. "
                    "The entity_label/entity_uri slots identify that upstream root, not an "
                    "extension-focus instance. Never create, retype, or reinterpret that root. "
                    "init_memory seeds each enrichment-target IRI into scoped memory with its "
                    "declared class type. Use those IRIs as subjects for domain-matching links. "
                    "Do not mint a replacement identity for a bound target class."
                ),
                "required_sequence": (
                    [
                        "Open the extension memory for the supplied inherited scope.",
                        "Materialize only source-supported primary-T-Box extension facts.",
                        "Link extension facts to the inherited scope only through T-Box-supported links.",
                        "Export extension memory as the final tool action.",
                    ]
                    if target.name.startswith("KG_BUILDING_ITER_")
                    else [
                        "Use the supplied entity label and IRI only as inherited scope context.",
                        "Extract one or more source-supported extension-focus instances relevant to that scope.",
                        "Keep the inherited scope class distinct from the extension focus class.",
                    ]
                ),
            }
            if is_extension
            else _generic_prompt_pipeline_role(target)
        ),
        "iteration_spec": iteration_spec,
        "onepass_fragment_contract": (
            {
                "enabled": True,
                "role": "focused_semantic_fragment_for_later_composition",
                "preserve": [
                    "iteration-scoped positive domain semantics",
                    "T-Box class and property comments",
                    "relationship target and direction semantics",
                    "creator atomic-operation contracts",
                    "reuse and occurrence-identity contracts",
                ],
                "forbidden_control_plane": [
                    "per-iteration init_memory or export_memory instructions or signatures",
                    "this-iteration-only execution scope",
                    "deferral to a later iteration",
                    "instructions to ignore ordered-member hints",
                    "prohibitions on creators solely because another iteration owns them",
                    "independent success, failure, completion, commit, or final-response declarations",
                ],
                "composition_rule": (
                    "State only this iteration's positive contribution. Never convert focused "
                    "ownership into a global execution restriction. The outer one-pass wrapper "
                    "owns lifecycle, union scope, and completion."
                ),
            }
            if onepass_fragment
            else {}
        ),
        "accumulated_prior_hint_representations": prior_hint_representations,
        "iteration_owned_scope": iteration_owned_scope,
        "relationship_target_contracts": relationship_target_contracts,
        "agent_tool_contract": agent_tool_contract,
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
        "reuse_policy": (
            {
                "authorized_checks": [
                    item
                    for item in _existing_entity_check_contracts(context)
                    if str(item.get("class_local") or "").strip()
                    in set(iteration_owned_scope.get("classes") or [])
                ],
                "rules": [
                    "Only classes with an authorized check are eligible for generic reuse.",
                    "Each authorized check reads an independent ontology-wide central identity memory spanning top entities and documents, never the current scoped graph.",
                    "Call the exact check_existing tool before create for an eligible hinted class.",
                    "Reuse only when returned details and central provenance satisfy the complete reuse_scope and match_basis.",
                    "If no candidate fully matches, call create; never reuse by label alone unless the match basis explicitly permits it.",
                ],
            }
            if target.name.startswith("KG_BUILDING_ITER_")
            else {}
        ),
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
            **(
                extension_kg_handoff_contract()
                if is_extension and target.name.startswith("KG_BUILDING_ITER_")
                else {}
            ),
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


def _materializable_prompt_component_path(target: Path) -> Path:
    return target.with_name(f"{target.stem}.materializable.inc")


def _external_mcp_prompt_component_text(
    generation_contract: Mapping[str, Any],
) -> str:
    """Render configured extraction MCP use as a deterministic runtime component."""
    from models.MCPConfig import load_mcp_set_tool_purposes

    iteration_spec = generation_contract.get("iteration_spec") or {}
    tools = [
        str(value).strip()
        for value in (
            iteration_spec.get("extraction_mcp_tools")
            or iteration_spec.get("mcp_tools")
            or []
        )
        if str(value).strip()
    ]
    if not tools:
        return ""
    purposes = load_mcp_set_tool_purposes(
        iteration_spec.get("extraction_mcp_set_name")
        or iteration_spec.get("mcp_set_name")
    )
    validation = iteration_spec.get("extraction_validation") or {}
    required_groups = validation.get("required_executed_tool_groups") or []
    lines = [
        "External MCP Use Contract (mechanically injected):",
        "- The following configured external MCP groups are active runtime capabilities. "
        "Use each group proactively whenever its stated purpose applies to an in-scope, "
        "source-supported identity; do not ignore an applicable configured group merely "
        "because a source label already exists.",
        "- If a group has no applicable source-supported identity in the current scope, do "
        "not fabricate an entity or an irrelevant tool call.",
    ]
    generic_purpose = (
        "Use this configured external capability for its advertised runtime purpose, "
        "only on source-supported in-scope identities."
    )
    for name in tools:
        lines.append(f"- `{name}`: {purposes.get(name) or generic_purpose}")
    for group in required_groups:
        if not isinstance(group, Mapping):
            continue
        any_of = [
            str(value).strip()
            for value in group.get("any_of") or []
            if str(value).strip()
        ]
        if any_of:
            lines.append(
                f"- Required executed tool group `{group.get('name') or 'external_lookup'}`: "
                f"for every applicable in-scope entity occurrence, call at least one of "
                f"{', '.join(f'`{name}`' for name in any_of)} with arguments identifying that "
                "entity. A call for one entity does not cover another unless the tool explicitly "
                "accepts a batch and returns separately attributable results; tool-group "
                "execution is validated mechanically."
            )
    lines.append(
        "- External results may enrich an already source-supported identity, but they must "
        "not create participation evidence, a new procedure occurrence, or a relation absent "
        "from the source."
    )
    lines.append(
        "- A tool result that reports no match, ok=false, matched=false, or empty content "
        "is unresolved. Copy lookup values only when the tool actually returned them; do "
        "not invent lookup values to fill a miss."
    )
    return "\n".join(lines)


def _tbox_ancestor_class_locals(
    parsed: Mapping[str, Any],
    class_locals: Iterable[str],
) -> list[str]:
    """Close a class set under parsed parent_classes. No ontology-specific names."""
    classes = parsed.get("classes") or {}
    queued = {
        str(local).strip()
        for local in class_locals
        if str(local).strip() in classes
    }
    pending = list(queued)
    while pending:
        current = pending.pop()
        for parent in (classes.get(current) or {}).get("parent_classes") or []:
            parent_local = str(parent).strip()
            if parent_local in classes and parent_local not in queued:
                queued.add(parent_local)
                pending.append(parent_local)
    return sorted(queued)


def _properties_touching_classes(
    parsed: Mapping[str, Any],
    class_locals: set[str],
) -> list[str]:
    """Return properties whose domain or range intersects the class set."""
    touching: list[str] = []
    for local, spec in (parsed.get("properties") or {}).items():
        domains = {
            str(value).strip()
            for value in ((spec or {}).get("domains") or [(spec or {}).get("domain")])
            if str(value or "").strip()
        }
        range_local = str((spec or {}).get("range") or "").strip()
        if domains & class_locals or range_local in class_locals:
            touching.append(str(local))
    return sorted(dict.fromkeys(touching))


def _format_verbatim_tbox_comment_blocks(
    parsed: Mapping[str, Any],
    *,
    class_locals: Iterable[str],
    include_ancestors: bool = False,
) -> str:
    """Copy scoped class and property comments verbatim from the parsed T-Box."""
    classes = parsed.get("classes") or {}
    selected_classes = {
        str(local).strip()
        for local in class_locals
        if str(local).strip() in classes
    }
    if include_ancestors:
        selected_classes.update(_tbox_ancestor_class_locals(parsed, selected_classes))
    blocks: list[str] = []
    for class_local in sorted(selected_classes):
        comment = str((classes.get(class_local) or {}).get("comment") or "").strip()
        if not comment:
            continue
        parents = [
            str(parent).strip()
            for parent in (classes.get(class_local) or {}).get("parent_classes") or []
            if str(parent).strip() in classes
        ]
        header = f"- Class `{class_local}`"
        if parents:
            header += f" (parents: {', '.join(f'`{parent}`' for parent in parents)})"
        blocks.append(f"{header}:\n{comment}")
    properties = parsed.get("properties") or {}
    for property_local in _properties_touching_classes(parsed, selected_classes):
        comment = str(
            (properties.get(property_local) or {}).get("comment") or ""
        ).strip()
        if not comment:
            continue
        blocks.append(f"- Property `{property_local}`:\n{comment}")
    return "\n\n".join(blocks)


def _pre_extraction_tbox_component_text(
    context: AgenticGenerationContext,
    generation_contract: Mapping[str, Any],
    *,
    allowed_classes: set[str],
) -> str:
    """Deterministic PRE T-Box dump from compiled scope only."""
    tbox_classes = {
        str(local).strip()
        for local in ((generation_contract.get("tbox_scope") or {}).get("classes") or {})
        if str(local).strip()
    }
    comment_blocks = _format_verbatim_tbox_comment_blocks(
        context.parsed,
        class_locals=set(allowed_classes) | tbox_classes,
        include_ancestors=True,
    )
    if not comment_blocks:
        return ""
    return (
        "Scoped T-Box Contract (mechanically injected):\n"
        "- The comments below are copied verbatim from the compiled iteration T-Box "
        "scope.\n"
        "- They are binding for candidate type identity, sequencing, and property "
        "evidence.\n"
        "- Use only the class and property locals that appear in this compiled scope.\n"
        "- Do not add types, properties, examples, priorities, or exceptions that are "
        "not present below.\n"
        "- Do not weaken a comment by paraphrase.\n\n"
        f"{comment_blocks}"
    )


def _materializable_prompt_component_text(
    context: AgenticGenerationContext,
    target: Path,
) -> str:
    """Render the extraction schema from compiled scope, outside LLM authorship."""
    is_extraction = target.name.upper().startswith("EXTRACTION_ITER_")
    is_pre_extraction = target.name.upper().startswith("PRE_EXTRACTION_ITER_")
    if not (is_extraction or is_pre_extraction):
        return ""
    generation_contract = _prompt_artifact_generation_contract(context, target)
    if is_extraction and "pipeline_selected_top_entity" in (
        generation_contract.get("tbox_scope") or {}
    ):
        return ""
    owned_scope = generation_contract.get("iteration_owned_scope") or {}
    allowed_classes = {
        str(local)
        for local in (
            list(owned_scope.get("classes") or [])
            + list(owned_scope.get("linked_materialization_classes") or [])
        )
        if str(local)
    }
    allowed_object_properties = {
        str(local)
        for local in owned_scope.get("object_properties") or []
        if str(local)
    }
    lexical_object_properties = {
        str(item.get("predicate_local") or "")
        for item in (
            generation_contract.get("lexical_quantity_hint_contract") or {}
        ).get("properties")
        or []
        if isinstance(item, dict) and str(item.get("predicate_local") or "")
    }
    top_entity_local = str(
        (context.contract.get("top_entity") or {}).get("class_local") or ""
    ).strip()
    for property_local in lexical_object_properties:
        property_spec = (context.parsed.get("properties") or {}).get(
            property_local
        ) or {}
        lexical_domains = {
            str(domain).strip()
            for domain in (
                property_spec.get("domains") or [property_spec.get("domain")]
            )
            if str(domain or "").strip()
        }
        # An owned class already carries its own lexical fields. The only
        # non-owned carrier admitted here is the pipeline-locked top entity,
        # needed by property-only summary iterations. Do not reintroduce
        # abstract, prohibited, or otherwise unowned lexical domains.
        if top_entity_local in lexical_domains:
            allowed_classes.add(top_entity_local)
    if is_pre_extraction:
        component = _pre_extraction_tbox_component_text(
            context,
            generation_contract,
            allowed_classes=allowed_classes,
        )
        if not component:
            raise ValueError(
                f"{target.name}: compiled scoped T-Box comments are empty"
            )
        return component
    # Lazy import avoids the runner <-> pure-generation module import cycle.
    from src.agents.scripts_and_prompts_generation.agentic_generation_runner import (
        _format_materializable_hint_contract,
    )

    rendered_scope = _format_materializable_hint_contract(
        context,
        allowed_classes=allowed_classes,
        allowed_object_properties=allowed_object_properties,
        lexical_object_properties=lexical_object_properties,
    ).strip()
    if not rendered_scope:
        raise ValueError(
            f"{target.name}: compiled Materializable Hint Contract is empty"
        )
    iteration_spec = generation_contract.get("iteration_spec") or {}
    runtime_slots = set(
        (generation_contract.get("runtime_binding_contract") or {}).get(
            "allowed_slots"
        )
        or []
    )
    has_accumulated_hints = "{accumulated_hints}" in runtime_slots
    external_mcp_contract = _external_mcp_prompt_component_text(generation_contract)
    if iteration_spec.get("hint_representation") == "semantic-text.v1":
        semantic_scope = (
            rendered_scope.replace("Entity class", "Semantic entity class")
            .replace("Relation `", "Semantic relation `")
            .replace("`subject_ref` class", "subject class")
            .replace("`object_ref` class", "object class")
            .replace(
                " -> `datatype_properties` accepts:",
                " supports source-grounded property evidence:",
            )
            .replace("Field `", "Property `")
        )
        component = (
            "Semantic Hint Contract (mechanically injected):\n"
            f"{semantic_scope}\n\n"
            + (
                "Accumulated prior extraction context (read-only input; it is not output):\n"
                "{accumulated_hints}\n\n"
                if has_accumulated_hints
                else ""
            )
            +
            "Semantic-ledger rules:\n"
            "- Return only a natural-language ledger headed exactly `SEMANTIC_HINTS_V1`.\n"
            "- The runtime answer must begin with that header, but this prompt component must "
            "not end with a literal answer header or a begin-output marker.\n"
            + "".join(f"- {rule}\n" for rule in _semantic_text_natural_ledger_rules())
            +
            "- Separate occurrences with a blank line.\n"
            "- Describe source-grounded entity, operation, property, quantity, ordering, "
            "and uncertainty semantics using only the active class/property locals and their "
            "T-Box comments above.\n"
            "- Preserve exact source quantity lexemes and source order; do not silently omit "
            "a supported iteration-owned occurrence or relation.\n"
            "- Do not emit JSON, RDF, refs, IRIs, endpoint IDs, quantity nodes, tool calls, "
            "or graph layout. Entity identity resolution and graph construction belong to "
            "the KG-building agent.\n"
            + (
                "- Treat prior context only as semantic context. Never copy its serialization, "
                "refs, or graph identifiers into this ledger."
                if has_accumulated_hints
                else ""
            )
        )
        return (
            f"{external_mcp_contract}\n\n{component}"
            if external_mcp_contract
            else component
        )
    component = (
        "Materializable Hint Contract:\n"
        f"{rendered_scope}\n\n"
        + (
            "Accumulated prior-hint identity registry (read-only input; it is not output):\n"
            "{accumulated_hints}\n\n"
            if has_accumulated_hints
            else ""
        )
        +
        "Grounded entity and relation rules:\n"
        "- Emit an entity only when it denotes an entity occurrence explicitly supported "
        "by the source"
        + (
            ", or when it preserves an exact entity ref already present in the provided hints"
            if has_accumulated_hints
            else ""
        )
        + ".\n"
        "- For every newly extracted entity, assign an opaque local ref token. Never mint "
        "or guess an absolute IRI. An absolute IRI may appear only when it is the exact "
        "current target IRI"
        + (
            " or an exact IRI supplied by the prior registry/dossier"
            if has_accumulated_hints
            else ""
        )
        + ".\n"
        "- Every datatype-property value must conform to the datatype declared by the "
        "contract. In particular, an XSD boolean must be emitted as the JSON boolean "
        "`true` or `false`, never as a quoted descriptive phrase. Preserve descriptive "
        "source wording in the entity label or another contract-accepted string property, "
        "not in a boolean field.\n"
        "- Never invent an entity merely to supply a relation endpoint, satisfy a range, "
        "or make the output look complete.\n"
        "- Every relation endpoint must resolve to an exact current entity ref"
        + (
            ", an exact prior-hint ref, or an explicit dossier IRI"
            if has_accumulated_hints
            else ", or an explicit absolute IRI supplied by an allowed runtime binding"
        )
        + ". If either endpoint is unresolved, "
        "omit the relation; never substitute a boolean, label, placeholder, or the source "
        "entity's own ref.\n"
        "- Lexical evidence for an object property is not a scalar datatype value. Emit "
        "the relation only when the source supports a distinct target entity and a "
        "resolvable target ref; never turn it into a self-link.\n"
        "- Return one JSON object with `entities` and `relations` arrays and no markdown "
        "code fence."
    )
    return (
        f"{external_mcp_contract}\n\n{component}"
        if external_mcp_contract
        else component
    )


_DETERMINISTIC_TBOX_BEGIN = (
    "----- DETERMINISTIC T-BOX CONTRACT (mechanically spliced; do not edit) -----"
)
_DETERMINISTIC_TBOX_END = "----- END DETERMINISTIC T-BOX CONTRACT -----"


def _is_pre_extraction_prompt(target: Path) -> bool:
    name = target.name.upper()
    return name.startswith("PRE_EXTRACTION_ITER_") and name.endswith(".MD")


def _strip_deterministic_tbox_splice(text: str) -> str:
    """Remove any previously spliced deterministic T-Box block."""
    while _DETERMINISTIC_TBOX_BEGIN in text:
        start = text.find(_DETERMINISTIC_TBOX_BEGIN)
        stop = text.find(_DETERMINISTIC_TBOX_END, start)
        if stop < 0:
            text = text[:start]
            break
        text = text[:start] + text[stop + len(_DETERMINISTIC_TBOX_END) :]
    return text.strip()


def _prompt_contains_deterministic_component(prompt: str, component_text: str) -> bool:
    component = component_text.strip()
    return bool(
        _DETERMINISTIC_TBOX_BEGIN in prompt
        or (component and component in prompt)
    )


def _splice_deterministic_tbox_into_pre_prompt(
    target: Path,
    component_text: str,
) -> None:
    """Idempotently append the compiled T-Box contract onto a PRE prompt."""
    if not _is_pre_extraction_prompt(target) or not component_text.strip():
        return
    if not target.is_file():
        return
    body = _strip_deterministic_tbox_splice(
        target.read_text(encoding="utf-8", errors="replace")
    )
    target.write_text(
        f"{body.rstrip()}\n\n"
        f"{_DETERMINISTIC_TBOX_BEGIN}\n"
        f"{component_text.rstrip()}\n"
        f"{_DETERMINISTIC_TBOX_END}\n",
        encoding="utf-8",
    )


def _detach_deterministic_tbox_from_pre_prompt(target: Path) -> None:
    """Leave only LLM-authored PRE text before a generation or repair edit."""
    if not _is_pre_extraction_prompt(target) or not target.is_file():
        return
    body = _strip_deterministic_tbox_splice(
        target.read_text(encoding="utf-8", errors="replace")
    )
    target.write_text(
        f"{body.rstrip()}\n" if body.strip() else "",
        encoding="utf-8",
    )


def _write_materializable_prompt_component(
    context: AgenticGenerationContext,
    target: Path,
) -> Path | None:
    text = _materializable_prompt_component_text(context, target)
    if not text:
        return None
    component = _materializable_prompt_component_path(target)
    component.parent.mkdir(parents=True, exist_ok=True)
    component.write_text(text.rstrip() + "\n", encoding="utf-8")
    _splice_deterministic_tbox_into_pre_prompt(target, text)
    return component


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
        "reuse_policy",
        "step_scoped_object_properties",
        "required_step_scoped_object_properties",
        "materialization_operation_units",
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
            "Generate the concrete implementation of every public entity creator in "
            "owned_entity_tool_contracts; never emit an empty or manifest-only stub. "
            "Treat that list as exhaustive: it may include exact external range/restriction "
            "classes authorized by the T-Box contract, while unlisted fixed-runtime capabilities "
            "remain private. Preserve fixed-runtime label validation, no-mutation-on-rejection, "
            "and reuse-policy-aware identity semantics; do not reimplement or weaken them. Only "
            "classes explicitly marked reusable may reuse by exact class plus normalized label. "
            "Non-reusable contextual occurrence or numerical-payload classes must always receive "
            "a fresh IRI, including repeated calls with the same label. Project each creator's complete "
            "`datatype_inputs` list directly into its public signature. Non-ordering datatype "
            "inputs use their exact Python types and remain optional unless their T-Box comment "
            "declares a base/default fallback; in that case, implementing the default is owned by "
            "the generated creator code, not by extraction or KG-building Agents. Use the correctly "
            "typed T-Box default as the Python signature default and persist it when the caller "
            "omits the argument. If the comment also declares conditional or inherited overrides, "
            "retain the base fallback in code while preserving those contextual override semantics; "
            "never use contextual rules to justify `None` or delegating the base default upstream. "
            "`order` remains required for ordered creators. Apply the generic "
            "fixed_runtime_api.atomic_creator_prevalidation_example: validate every public input "
            "before the first mutator call, then create/reuse and send supplied values to exact "
            "writers from package_datatype_capabilities(). Invalid calls must return rejection "
            "with zero graph mutation. When a creator contract contains required_edges, expose "
            "all projected edge/dependent inputs and perform the complete operation inside one "
            "atomic_graph_transaction. Create same-operation dependents through their exact "
            "fixed capability and write only the declared required edges. Do not generate public "
            "`set_<property>` tools."
        )
    if name.endswith("_creation_relationships.py"):
        return (
            "Generate concrete object-property tool implementations from "
            "relationship_tool_contracts; never emit a manifest-only stub. Predicates "
            "owned by atomic creator operations are absent by construction and must not be "
            "reintroduced. Every public "
            "`add_<predicate_local>` must expose "
            "`subject_iri: Annotated[str, Field(description=...)]` and "
            "`object_iri: Annotated[str, Field(description=...)]`, plus "
            "`reuse_authorization_token: str | None = None`. Pass that token as the third "
            "argument to the immutable package relationship capability; never discard it. "
            "The subject description must "
            "contain the exact phrases `absolute IRI` and "
            "`never a label/name/literal/plain text` and list every contract domain local. "
            "The object description must contain "
            "the exact phrases `absolute IRI` and `never a label/name/literal/plain text`, list "
            "every contract range local, and reference only contract creator_tools. Do not define "
            "or export datatype setters; datatype properties are owned by domain create_* inputs. "
            "Pass each validated subject/object pair unchanged to its exact package-bound "
            "relationship capability. Do not reproduce class reuse decisions in generated code: "
            "the package contract owns those decisions. Preserve capability rejection, including "
            "non-reusable occurrence reuse rejection, as failure without fallback or mutation."
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
            "helpers, aggregate hint materializers, or batch orchestration tools. Publish "
            "`init_memory` directly from fixed runtime. When commit_gate_contract is present, "
            "publish `export_memory` only through a signature-preserving local adapter that calls "
            "the declared read-only check first, returns rejection unchanged, and delegates "
            "successful graphs to fixed export. "
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
            "Generate exactly the existing_entity_check_contracts plus the read-only ordered-member "
            "integrity checker described by ordered_check_contract. Do not generate check_existing "
            "for any unlisted class. Every check_existing tool is zero-argument and must return "
            "structured JSON containing each candidate's exact IRI, all labels, RDF types, datatype "
            "values with datatype/language metadata, outgoing relations, incoming relations, "
            "central provenance, reuse_scope, and match_basis. Each check must read the independent "
            "ontology-wide central reuse memory rather than the scoped retained graph. All checks "
            "must leave both central and scoped graph state unchanged. "
            "The ordered check output must identify parent/member/order evidence for "
            "every violation and it must leave the retained graph byte-for-byte unchanged. "
            "Enforce operation_invariants generically: exact inverse membership counts and each "
            "same-operation dependent's exact count, explicit type, sole owner, and declared "
            "exclusive-target constraints. Every "
            "item in the returned `violations` array must follow "
            "ordered_check_contract.output_schema and use the exact discriminator key `code`; "
            "never emit `violation_code`, `type`, or `kind` as an alias. "
            "Implement ordered_check_contract.contiguity_algorithm literally: first collect all "
            "linked ordered members per parent, let N be that full count, then compare valid "
            "observed order values with 1..N. A missing or invalid order member still contributes "
            "to N and can therefore require both its local violation and non_contiguous_order. "
            "Implement missing_explicit_ancestor_type only from "
            "ordered_check_contract.required_explicit_ancestor_types. Example with no ontology "
            "class names: two linked members have valid orders 1 and 2; the first is typed only "
            "as a concrete subclass that is a mapping key; the second has that subclass type plus "
            "every mapped ancestor. Emit the code for the first only. Do not emit it for "
            "ancestor-only typing or because a member merely lacks a family type. "
            "Reuse fail-closed example with no class names: empty proposed_entity_json and "
            "empty label must return error_json(code='PROPOSED_ENTITY_EVIDENCE_REQUIRED') "
            "before any candidate scan. After valid evidence, call "
            "judge_reuse_pairs(requests) once with a single positional list, then "
            "register_central_reuse_authorization(candidate_iri=, pair_id=, judgement=) "
            "only for approved candidates."
        )
    if name.startswith("PRE_EXTRACTION_ITER_") and name.endswith(".md"):
        return (
            "Produce only the closed-ledger.v1 JSON evidence object required by "
            "generation_contract.evidence_accounting_contract. Derive candidate types, "
            "properties, subclass decisions, and every domain rule exclusively from the active "
            "T-Box projection and structured contracts. Bind accumulated prior hints using "
            "generation_contract.accumulated_prior_hint_representations; do not relabel their "
            "representation as the current iteration's output representation. "
            "Do not paste compiled T-Box comments into this artifact; a later deterministic "
            "step splices the iteration T-Box contract after LLM authorship."
        )
    if name.startswith("EXTRACTION_ITER_") and name.endswith(".md"):
        if name == "EXTRACTION_ITER_1.md":
            return (
                "Identify source-supported pipeline-selected top entities. A single "
                "ref-entity-relations.v1 JSON object is compatible with this stage because the "
                "top-entity runtime normalizes its entity records to the downstream canonical "
                "`<Class>-<n> [<label>]` lines before validation and KG building. Do not emit a "
                "parallel second representation."
            )
        return (
            "Extract source-grounded semantics allowed by the active T-Box. Follow the exact "
            "interchange selected by generation_contract.iteration_spec.hint_representation and "
            "generation_contract.evidence_accounting_contract: semantic-text.v1 requires only its "
            "natural-language SEMANTIC_HINTS_V1 ledger, while ref-entity-relations.v1 requires only its fixed JSON "
            "object. Never add a parallel or alternative representation. Include every required "
            "runtime input slot. "
            "Keep class creation coverage distinct from any one relationship's linking policy: "
            "when a class created in this iteration is the T-Box range required by downstream "
            "iteration properties, extract every source-grounded target needed downstream even "
            "when some targets must not be linked by a different current-iteration property."
        )
    if _is_onepass_kg_fragment(target):
        return (
            "Generate a focused positive semantic/materialization fragment for later composition "
            "inside one shared whole-graph session. Preserve the iteration's T-Box semantics, "
            "relationship directions, reuse rules, creator atomicity, materializable fields, and "
            "all non-lifecycle tool signatures. Do not render init_memory, export_memory, a "
            "standalone tool sequence, an independent completion/failure declaration, deferral "
            "to another iteration, instructions to ignore ordered-member hints, or bans on "
            "creators merely because they belong to another iteration. When another fragment "
            "owns a referenced target or operation, describe only this fragment's positive "
            "responsibility and leave union execution to the outer one-pass controller."
        )
    if name.startswith("KG_BUILDING_") and name.endswith(".md"):
        return (
            "This is a runtime agent prompt and must explicitly require the supplied MCP tools "
            "for graph mutation, lifecycle handling, and export. For every instructed object-"
            "property mutation, preserve the T-Box direction explicitly: the add_* tool's "
            "subject_iri is an instance of a relationship_tool_contracts domain_iris class and "
            "object_iri is an instance of a range_iris class. State subject and object roles "
            "unambiguously; never use a directionally ambiguous phrase such as only 'link A to B'. "
            "For every hinted class listed in reuse_policy.authorized_checks, require the exact "
            "check_existing tool before create and require comparison of returned labels, values, "
            "types, relations, and central provenance against the complete scope and match basis. "
            "State that checks see candidates across top entities and documents but that visibility "
            "does not override document/top-entity scope restrictions. Reuse a returned IRI "
            "only on a full match; otherwise create. Classes absent from authorized_checks must "
            "never be generically reused or deduplicated by label. Never substitute an arbitrary "
            "same-class candidate. If "
            "the referenced target is absent and its creator is outside this iteration's scope, "
            "stop with an upstream-materialization blocker instead of creating or mislinking it."
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
            and path.name not in {
                "_fixed_om2_runtime.py",
                "_fixed_rdf_runtime.py",
                "_reuse_pair_judge.py",
            }
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
    generation_contract = _artifact_generation_contract(context, target)
    task = {
        "round": round_index,
        "ontology": {
            "name": context.ontology.name,
            "role": context.ontology.role,
            "ttl_file": context.ontology.ttl_file,
        },
        "generation_contract": generation_contract,
        "fixed_runtime_api": {} if is_prompt else _fixed_rdf_runtime_api_contract(),
        "artifact_role_contract": _artifact_role_contract(
            target, generation_contract
        ),
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
    is_extension_prompt = bool(context.ontology.role == "extension" and is_prompt)
    is_extension_kg_prompt = bool(
        is_extension_prompt
        and target is not None
        and target.name.startswith("KG_BUILDING_ITER_")
    )
    is_kg_prompt = bool(
        is_prompt
        and target is not None
        and target.name.startswith("KG_BUILDING_ITER_")
    )
    is_onepass_kg_fragment = _is_onepass_kg_fragment(target)
    prompt_iteration_spec = (
        (generation_contract.get("iteration_spec") or {})
        if isinstance(generation_contract, dict)
        else {}
    )
    is_enrichment_prompt = bool(
        is_prompt and _is_enrichment_iteration_spec(prompt_iteration_spec)
    )
    is_semantic_text_prompt = bool(
        is_prompt
        and str(prompt_iteration_spec.get("hint_representation") or "").strip()
        == "semantic-text.v1"
    )
    if is_extension_prompt:
        task["extension_runtime_prompt_policy"] = _extension_meta_prompt_policy(target)
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
        "generation_contract.iteration_spec. "
        + (
            "For a one-pass iteration fragment, scope means focused positive semantic "
            "contribution, never a global restriction on the combined session. "
            if is_onepass_kg_fragment
            else "The prompt must not broaden into the ontology-wide task. "
        )
        +
        "Render each generation_contract.runtime_binding_contract.llm_authored_slots entry "
        "exactly once; refer back to that single bound data block instead of repeating a "
        "placeholder in multiple instructions. Never render an entry from "
        "generation_contract.runtime_binding_contract.mechanically_injected_slots in the "
        "LLM-authored file because the deterministic companion component supplies it. "
        "Runtime binding slots must use single braces such as `{paper_content}`; never emit "
        "double-brace placeholder residue. "
        + (
            "For extraction and pre-extraction prompts, "
            "generation_contract.deterministic_property_contract is the complete "
            "iteration-scoped property surface compiled from the active T-Box. "
            "Use it for coherent extraction decisions and output requirements. For main "
            "EXTRACTION artifacts, do not reproduce a Materializable Hint Contract section "
            "inside the LLM-authored file: the generator attaches that exact scope as a "
            "separate deterministic prompt component. Do not omit listed properties or "
            "invent properties outside it. "
            "Never invent an entity to fill a relation endpoint or schema range. Emit a new "
            "entity only from explicit source evidence, and otherwise preserve only exact "
            "refs supplied in existing hints. "
            "Interpret domain semantics only from generation_contract.tbox_scope, "
            "including its verbatim comments and formal OWL/RDFS structure. Do not infer "
            "domain behavior from this meta-instruction or add ontology-local rules "
            "that are absent from the supplied T-Box-derived context. "
            "Use generation_contract.subclass_decision_contract to render an explicit "
            "subclass decision checklist in the runtime prompt. The checklist must cover "
            "every supplied decision point and candidate subclass, but all positive gates, "
            "exclusions, and tie-breaking semantics must remain verbatim-derived from "
            "generation_contract.tbox_scope rather than this domain-neutral instruction. "
            "Render generation_contract.warning_marked_tbox_contract as a separate mandatory "
            "attention block. Before any class or field choice governed by a marked comment, "
            "the runtime prompt must require an explicit comparison against the complete marked "
            "comment and all applicable marked alternatives. The marker changes attention only; "
            "it must never introduce domain-specific semantics absent from the T-Box. "
            "Project generation_contract.subclass_comment_projection completely into that "
            "checklist, including every subclass comment. "
            "Integrate generation_contract.lexical_quantity_hint_contract exactly: "
            + (
                "preserve each listed explicit source quantity as a complete exact lexeme "
                "in the owning occurrence, either in the occurrence prose or as a "
                "`<predicate_local>: <lexeme>` line, for downstream materialization. Never tell "
                "the runtime to omit a listed quantity because its target class or object ref "
                "is absent, and do not reinterpret this interchange as T-Box datatype semantics "
                "or as JSON datatype_properties. "
                if is_semantic_text_prompt
                else "preserve each listed explicit source quantity under its predicate local "
                "in the source entity datatype_properties for deterministic downstream "
                "materialization. Never tell the runtime to omit a listed quantity because its "
                "target class or object ref is absent, and do not reinterpret this pipeline "
                "interchange field as T-Box datatype semantics. "
            )
            + "For PRE artifacts, render generation_contract.pre_extraction_candidate_type_contract "
            "as the closed candidate_types enumeration. Never advertise or emit any other class "
            "as a candidate type, never permit an empty candidate_types array, and keep reusable "
            "context classes outside the ledger type surface. "
            "Integrate generation_contract.evidence_accounting_contract as the "
            "prompt's execution protocol. PRE extraction must complete its target-first "
            "scope, source-dependency, and effective-evidence planning phases before assigning "
            "stable atomic evidence IDs in the fixed JSON schema. It must keep textual "
            "ordering cues outside candidate_properties and never emit a property listed "
            "in normalized_output_ordering_properties during "
            "PRE extraction. "
            + (
                "Main extraction must return only the SEMANTIC_HINTS_V1 natural-language "
                "ledger required by evidence_accounting_contract; it must not emit JSON, RDF, "
                "refs, IRIs, or any parallel output schema. Tell the runtime to begin its answer "
                "with the header but do not append a literal header or begin-output marker to the "
                "prompt template. Require a short subclass label on every occurrence and, "
                "when an occurrence is ordered, its sequence position as a contiguous "
                "integer; require every source-supported property, relation, and complete "
                "value to appear in that occurrence. Do not require the heading form "
                "`<SubclassLocal> (Order: <n>)`, parenthetical range tags, indented identity "
                "children, or an `(inherited global context)` suffix. Forbid tables, summaries, "
                "representative samples, truncation, and ellipses. Separate occurrences with a "
                "blank line. "
                if is_semantic_text_prompt
                else "Main extraction must return exactly the fixed "
                "ref-entity-relations.v1 JSON object from main_extraction.fixed_json_schema, "
                "with entities and relations as its top-level arrays; it must not expose the PRE "
                "ledger, records, evidence_accounting, or any parallel output schema. "
            )
            + "Preserve "
            "source order, prohibit silent omission, and perform the specified final self-audit "
            "before returning. Mechanically injected accumulated_hints contain facts from "
            "completed earlier iterations, not a pre-existing output for the current iteration. "
            "Use them for identity and dependency context, but still extract every supported "
            "current-iteration occurrence; never describe a main EXTRACTION artifact as an "
            "enrichment-only pass. "
            "This remains "
            "prompt-only behavior; do not request or describe scripts, validators, "
            "tools, or external repair loops. "
            if is_prompt
            and target is not None
            and target.name.startswith(("EXTRACTION_ITER_", "PRE_EXTRACTION_ITER_"))
            and not is_enrichment_prompt
            else ""
        )
        + (
            "This artifact is an enrichment sub-iteration. Integrate only the enrichment "
            "responsibilities declared by iteration_spec.sub_iteration / parent_iteration: "
            "emit a patch against the parent interchange, preserve exact prior refs/classes/"
            "labels, and add only newly supported owned details. Do not restate or replace the "
            "parent occurrence list. "
            if is_enrichment_prompt
            and target is not None
            and target.name.startswith("EXTRACTION_ITER_")
            else ""
        )
        + (
            "For KG-building prompts, generation_contract.pipeline_required_link_contracts "
            "define deterministic pipeline-owned links whose target identity comes from a "
            "runtime slot rather than extraction hints. Render those contracts explicitly. "
            "Their KG materialization policy overrides any generic statement that all created "
            "entities and links must be repeated in iteration_hints; use the exact declared "
            "creator and relationship tools from agent_tool_contract, enumerating every "
            "agent_tool_contract.creator_tools entry including "
            "iteration_spec.linked_materialization_classes. "
            + (
                "Integrate generation_contract.lexical_quantity_hint_contract exactly when its "
                "properties list is non-empty: for each listed predicate P, recover the complete "
                "exact lexeme from the owning natural-language occurrence paragraph in "
                "iteration_hints, call the exact fixed/runtime quantity creator listed under "
                "relationship_target_contracts[P].creator_tools while preserving that lexeme as "
                "the creator label input, then assert P with the matching add_* tool. Do not "
                "expect JSON datatype_properties, a quantity entity, or an object_ref for P, and "
                "do not require or invent a standalone property-local ledger line. "
                if is_semantic_text_prompt
                else "Integrate generation_contract.lexical_quantity_hint_contract exactly when its "
                "properties list is non-empty: for each listed predicate P, read the complete "
                "lexical value from the owning source entity's datatype_properties[P] in "
                "iteration_hints, call the exact fixed/runtime quantity creator listed under "
                "relationship_target_contracts[P].creator_tools while preserving that lexeme as "
                "the creator label input, then assert P with the matching add_* tool. Do not "
                "expect extraction to mint a quantity entity or object_ref for P, and do not "
                "invent an alternate label source when the lexical field is present. "
            )
            if is_kg_prompt
            else ""
        )
        + (
            "This is a focused KG one-pass fragment that will be concatenated with sibling "
            "iteration fragments under one outer runtime controller. Preserve all positive "
            "domain semantics and exact non-lifecycle tool contracts for this iteration, but "
            "do not emit init_memory or export_memory anywhere. Do not say or imply `this "
            "iteration only`, defer work to a later iteration, ignore ordered-member hints, "
            "forbid creators owned by another iteration, or declare this fragment independently "
            "complete, successful, failed, committed, exported, or final. Ownership partitions "
            "semantic responsibility; it does not restrict the combined session's union tool "
            "surface. Never express the fragment inventory as `use only the tools listed`, "
            "`do not introduce classes or properties beyond this fragment`, or equivalent "
            "union-session restrictions. "
            if is_onepass_kg_fragment
            else ""
        )
        + (
            "This is a simple_extension runtime prompt. Its complete allowed placeholder set is "
            "extension_runtime_prompt_policy.canonical_runtime_slots. Do not emit any value "
            "from extension_runtime_prompt_policy.forbidden_runtime_slots; extension KG "
            "building receives the extracted source through `{paper_content}` and the "
            "canonical main graph through `{main_ontology_a_box}`. Mode A is mandatory for "
            "owned object properties: consume the extraction JSON in `{paper_content}` as "
            "ref-entity-relations.v1 (`entities` and `relations`). Never author a main-ontology "
            "hints placeholder; that slot is not supplied. "
            if is_extension_kg_prompt
            else ""
        )
        + (
            "This is a simple_extension extraction goal. Paper content and the T-Box are supplied "
            "by separate runtime wrapper channels, so do not emit placeholders for either. Use "
            "only extension_runtime_prompt_policy.canonical_runtime_slots and no value from "
            "extension_runtime_prompt_policy.forbidden_runtime_slots. The entity slots identify "
            "the inherited upstream scope, not an extension-focus instance. Ask for one or more "
            "extension-focus instances relevant to that scope and never retype or reinterpret "
            "the inherited entity as the extension focus. "
            if is_extension_prompt and not is_extension_kg_prompt
            else ""
        )
        + "You are generating the artifact through a plain LLM call and must not request tools "
        "while producing the edit payload. This restriction is meta-level only and must never "
        "appear in generated artifact content.\n\n"
        + _artifact_generation_guidance(target)
        + "\n\n"
        + json.dumps(task, ensure_ascii=False)
    )
    return _with_mcp_capability_security(prompt) if generate_scripts else prompt


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
        match = _ONEPASS_KG_PROMPT_RE.fullmatch(name)
        if match:
            return (7, int(match.group("iteration")), name)
        return (8, 0, path.as_posix())

    return [
        path.resolve().relative_to(root.resolve()).as_posix()
        for path in sorted(targets, key=order_key)
    ]


def _parallel_generation_wave(
    target: Path,
    generation_targets: list[Path],
) -> list[Path]:
    """Return independent targets that may be authored in the same DAG wave."""
    if target.name.endswith("_creation_entities.py"):
        return [
            candidate
            for candidate in generation_targets
            if candidate.name.endswith(
                ("_creation_entities.py", "_creation_relationships.py")
            )
        ]
    if target.suffix == ".md":
        return [
            candidate for candidate in generation_targets if candidate.suffix == ".md"
        ]
    return [target]


def _isolated_worker_context(
    context: AgenticGenerationContext,
    worker_root: Path,
) -> AgenticGenerationContext:
    """Remap output paths while preserving ontology and semantic inputs."""
    source_root = Path(context.output_root).resolve()

    def remap(raw_path: str) -> str:
        relative = Path(raw_path).resolve().relative_to(source_root)
        return str(worker_root / relative)

    return replace(
        context,
        output_root=str(worker_root),
        ontology_structure_dir=remap(context.ontology_structure_dir),
        scripts_dir=remap(context.scripts_dir),
        prompts_dir=remap(context.prompts_dir),
        parsed_summary_path=remap(context.parsed_summary_path),
        parsed_markdown_path=remap(context.parsed_markdown_path),
        contract_path=remap(context.contract_path),
        integrity_profile_path=remap(context.integrity_profile_path),
        report_path=remap(context.report_path),
        config_provenance_path=remap(context.config_provenance_path),
    )


def _generate_isolated_artifact_worker(
    payload: tuple[
        AgenticGenerationContext,
        dict[str, Any],
        str,
        str,
        EditBackend,
    ],
) -> tuple[str, dict[str, Any], bytes]:
    """Generate one candidate inside a process-local copied output tree."""
    context, report, relative, model_name, edit_backend = payload
    target = Path(context.output_root) / relative
    if target.suffix == ".md":
        _write_materializable_prompt_component(context, target)
        _detach_deterministic_tbox_from_pre_prompt(target)
    patch = run_llm_unified_diff_editor(
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
            (lambda: _validate_generated_prompt_hard_gates(target, context))
            if target.suffix == ".md"
            else None
        ),
        edit_backend=edit_backend,
    )
    if patch.get("ok") and target.suffix == ".md":
        _write_materializable_prompt_component(context, target)
    candidate = target.read_bytes() if patch.get("ok") else b""
    return relative, patch, candidate


def _persist_parallel_candidate_attempts(
    *,
    output_root: Path,
    ontology_name: str,
    artifact: str,
    patch: dict[str, Any],
) -> None:
    """Persist worker attempt validation before its temporary tree is deleted."""
    attempts = list(patch.get("attempts") or [])
    if not attempts:
        attempts = [
            {
                "attempt": None,
                "ok": bool(patch.get("ok")),
                "failures": list(patch.get("failures") or []),
                "validation": patch.get("validation") or {},
                "elapsed_seconds": patch.get("elapsed_seconds"),
                "token_usage": patch.get("token_usage") or {},
            }
        ]
    log_path = (
        output_root
        / "reports"
        / ontology_name
        / "parallel_candidate_attempts.jsonl"
    )
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8", newline="\n") as stream:
        for attempt in attempts:
            validation = attempt.get("validation") or {}
            record = {
                "schema_version": 1,
                "recorded_at": datetime.now(timezone.utc).isoformat(),
                "ontology": ontology_name,
                "artifact": artifact,
                "attempt": attempt.get("attempt"),
                "ok": bool(attempt.get("ok")),
                "failures": list(attempt.get("failures") or []),
                "validation_failures": list(validation.get("failures") or []),
                "validation": validation,
                "rollback_performed": bool(attempt.get("rollback_performed")),
                "changed_files": list(attempt.get("changed_files") or []),
                "elapsed_seconds": attempt.get("elapsed_seconds"),
                "token_usage": attempt.get("token_usage") or {},
            }
            stream.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def _generate_artifact_wave(
    *,
    context: AgenticGenerationContext,
    report: dict[str, Any],
    targets: list[Path],
    model_name: str,
    edit_backend: EditBackend,
    max_workers: int,
) -> dict[Path, dict[str, Any]]:
    """Author candidates in isolation without publishing them to the shared tree."""
    root = Path(context.output_root).resolve()
    target_by_relative = {
        target.resolve().relative_to(root).as_posix(): target for target in targets
    }
    workspaces: list[tempfile.TemporaryDirectory[str]] = []
    payloads = []
    for relative in target_by_relative:
        workspace = tempfile.TemporaryDirectory(prefix="artifact_candidate_")
        workspaces.append(workspace)
        worker_root = Path(workspace.name) / "output"
        shutil.copytree(root, worker_root)
        worker_context = _isolated_worker_context(context, worker_root)
        payloads.append((worker_context, report, relative, model_name, edit_backend))

    generated: dict[Path, dict[str, Any]] = {}
    try:
        with ProcessPoolExecutor(
            max_workers=min(max_workers, len(targets)),
            mp_context=multiprocessing.get_context("spawn"),
        ) as executor:
            futures = [
                executor.submit(_generate_isolated_artifact_worker, payload)
                for payload in payloads
            ]
            for future in as_completed(futures):
                relative, patch, candidate = future.result()
                _persist_parallel_candidate_attempts(
                    output_root=root,
                    ontology_name=context.ontology.name,
                    artifact=relative,
                    patch=patch,
                )
                target = target_by_relative[relative]
                if patch.get("ok"):
                    patch = {**patch, "_isolated_candidate_bytes": candidate}
                generated[target] = patch
    finally:
        for workspace in workspaces:
            workspace.cleanup()
    return generated


def _publish_isolated_candidate(target: Path, patch: dict[str, Any]) -> dict[str, Any]:
    """Publish one accepted worker candidate when its serial review begins."""
    candidate = patch.pop("_isolated_candidate_bytes", None)
    if patch.get("ok"):
        if not isinstance(candidate, bytes):
            return {
                **patch,
                "ok": False,
                "failures": [
                    *list(patch.get("failures") or []),
                    "isolated_candidate_bytes_missing",
                ],
            }
        target.write_bytes(candidate)
    return patch


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

    relative = (
        target.resolve().relative_to(Path(context.output_root).resolve()).as_posix()
    )
    if target.suffix == ".md":
        _write_materializable_prompt_component(context, target)
    mechanical = build_validation_report(
        context,
        foreign_contracts=foreign_contracts,
        write_report=False,
        prompts_required=False,
        active_artifacts=[relative],
    )
    if not mechanical.get("stage_ok"):
        return {**mechanical, "ok": False, "semantic_review": None}
    review_path = target
    review_temp: Any = None
    authored = target.read_text(encoding="utf-8", errors="replace")
    component_text = (
        _materializable_prompt_component_text(context, target)
        if target.name.upper().startswith(("EXTRACTION_ITER_", "PRE_EXTRACTION_ITER_"))
        else ""
    )
    if component_text and not _prompt_contains_deterministic_component(
        authored, component_text
    ):
        review_temp = tempfile.TemporaryDirectory(prefix="composed_prompt_review_")
        review_path = Path(review_temp.name) / target.name
        review_path.write_text(
            authored.rstrip() + "\n\n" + component_text.rstrip() + "\n",
            encoding="utf-8",
        )
    try:
        semantic_review = review_generated_prompt_semantics_with_llm(
            context=context,
            artifact_path=review_path,
            model_name=model_name,
        )
    finally:
        if review_temp is not None:
            review_temp.cleanup()
    if semantic_review.get("decision") == "pass":
        return {
            **mechanical,
            "ok": True,
            "stage_ok": True,
            "semantic_review": semantic_review,
        }
    semantic_failure = "LLM prompt semantic review requires repair:\n" + json.dumps(
        semantic_review, ensure_ascii=False
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


def _detach_mechanically_injected_runtime_slots(
    target: Path,
    context: AgenticGenerationContext,
) -> list[str]:
    """Detach runtime slots owned by a deterministic companion component."""
    if target.suffix != ".md" or not target.is_file():
        return []
    contract = _prompt_artifact_generation_contract(context, target)
    slots = [
        str(slot)
        for slot in (
            (contract.get("runtime_binding_contract") or {}).get(
                "mechanically_injected_slots"
            )
            or []
        )
        if str(slot)
    ]
    if not slots:
        return []
    text = target.read_text(encoding="utf-8", errors="replace")
    changed: list[str] = []
    replacement = "[runtime context supplied by deterministic companion component]"
    for slot in slots:
        if slot in text:
            text = text.replace(slot, replacement)
            changed.append(slot)
    if changed:
        target.write_text(text, encoding="utf-8", newline="")
    return changed


def _semantic_text_natural_ledger_rules() -> list[str]:
    """0829/0901-style natural-language SEMANTIC_HINTS_V1 rules.

    Uses T-Box placeholders only. Do not name application ontology classes here.
    """
    return [
        (
            "Begin every occurrence with a short subclass label. When an occurrence is "
            "ordered, record its sequence position as a contiguous integer. Do not require "
            "the heading form `<SubclassLocal> (Order: <n>)`."
        ),
        (
            "Keep object-role identity tokens as written in the source. Do not replace a "
            "source token with a catalog, systematic, or registry name."
        ),
        (
            "Include every source-supported scalar, alias, amount, formula, and description "
            "in the owning occurrence. Do not invent a mandatory nested-child layout or "
            "parenthetical range-local tags."
        ),
        (
            "When a lookup returns multiple parallel aliases packed in one string, include "
            "the complete semicolon-separated payload. Do not truncate or summarize the "
            "lookup payload."
        ),
    ]


def _semantic_text_ox_sensitive_ledger_rules() -> list[str]:
    """Backward-compatible alias; the restored contract is 0829/0901 natural language."""
    return _semantic_text_natural_ledger_rules()


_JSON_LEDGER_MANDATE_PATTERNS = (
    r"ref-entity-relations\.v1",
    r"top-level `entities` and `relations`",
    r"emit a JSON object",
    r"return exactly one JSON object",
)
_ORDER_HEADING_MANDATE_PATTERNS = (
    r"<SubclassLocal>\s*\(\s*Order:\s*<n>\s*\)",
    r"heading parentheses must contain only",
    r"\(inherited global context\)",
    r"one-space-indented child",
)


def _semantic_text_structured_ledger_expectation_failures(
    text: str,
    target_name: str,
) -> list[str]:
    """Fail when a semantic-text prompt leaves the 0829/0901 natural-language contract."""
    failures: list[str] = []
    if "SEMANTIC_HINTS_V1" not in text:
        failures.append(
            f"{target_name}: semantic-text.v1 extraction must require SEMANTIC_HINTS_V1"
        )
    json_hits: list[str] = []
    for pattern in _JSON_LEDGER_MANDATE_PATTERNS:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            json_hits.append(match.group(0))
    if json_hits:
        unique = ", ".join(sorted(set(json_hits))[:8])
        failures.append(
            f"{target_name}: natural-language ledger must not require JSON output: {unique}"
        )
    heading_hits: list[str] = []
    for pattern in _ORDER_HEADING_MANDATE_PATTERNS:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            sentence_start = max(text.rfind(".", 0, match.start()) + 1, match.start() - 240)
            sentence = text[sentence_start : match.end() + 80].casefold()
            if any(
                marker in sentence
                for marker in (
                    "do not require",
                    "do not write",
                    "do not use",
                    "do not invent",
                    "don't require",
                    "never require",
                    "forbid",
                    "must not require",
                    "must not mandate",
                )
            ):
                continue
            heading_hits.append(match.group(0))
    if heading_hits:
        unique = ", ".join(sorted(set(heading_hits))[:8])
        failures.append(
            f"{target_name}: 0829/0901 ledger must not mandate 0902 surface schema: {unique}"
        )
    return failures


def _validate_generated_prompt_hard_gates(
    target: Path,
    context: AgenticGenerationContext,
) -> dict[str, Any]:
    """Validate prompt bindings and mechanical residue before semantic repair."""
    if (
        context.ontology.role == "extension"
        and target.name.startswith("KG_BUILDING_ITER_")
    ):
        ensure_extension_kg_mode_a_handoff_file(target)
    detached_mechanical_slots = _detach_mechanically_injected_runtime_slots(
        target, context
    )
    text = target.read_text(encoding="utf-8", errors="replace")
    binding = validate_prompt_runtime_bindings(target, context)
    failures = list(binding.get("failures") or [])
    if (
        context.ontology.role == "extension"
        and target.name.startswith("KG_BUILDING_ITER_")
        and not extension_kg_mode_a_handoff_present(text)
    ):
        failures.append(
            f"{target.name}: missing mechanical Mode A handoff block; "
            "extension KG must consume ref-entity-relations.v1 from the "
            "declared source-content slot"
        )
    residue = sorted(
        set(re.findall(r"TODO|FIXME|\{\{[^}\n]+\}\}", text, re.IGNORECASE))
    )
    if residue:
        failures.append(
            f"{target.name}: unresolved prompt placeholder/residue: "
            + ", ".join(residue[:8])
        )
    if not text.strip():
        failures.append(f"{target.name}: prompt artifact is empty")
    candidate_type_evidence: dict[str, Any] = {}
    kg_owned_scope_evidence: dict[str, Any] = {}
    enrichment_lock_evidence: dict[str, Any] = {}
    fixed_classification_schema_evidence: list[str] = []
    structured_ledger_expectation: list[str] = []
    if target.name.upper().startswith(
        ("EXTRACTION_ITER_", "KG_BUILDING_ITER_")
    ) and target.name.upper() not in {"EXTRACTION_ITER_1.MD", "KG_BUILDING_ITER_1.MD"}:
        prompt_contract = _prompt_artifact_generation_contract(context, target)
        iteration_spec = prompt_contract.get("iteration_spec") or {}
        if (
            target.name.upper().startswith("EXTRACTION_ITER_")
            and str(iteration_spec.get("hint_representation") or "").strip()
            == "semantic-text.v1"
        ):
            structured_ledger_expectation = (
                _semantic_text_structured_ledger_expectation_failures(
                    text, target.name
                )
            )
            failures.extend(structured_ledger_expectation)
            fixed_schema_patterns = (
                r"selected\s+class\s*:",
                r"selection\s+rationale\s*:",
                r"activeclass\s*:",
                r"\bclass\s*:\s*<[^>\n]+>",
                r"example\s+header\s+line\s+shape\s*:\s*occurrence\s*:",
            )
            fixed_classification_schema_evidence = sorted(
                {
                    match.group(0)
                    for pattern in fixed_schema_patterns
                    for match in re.finditer(pattern, text, re.IGNORECASE)
                }
            )
            if fixed_classification_schema_evidence:
                failures.append(
                    f"{target.name}: natural-language classification rationale must not "
                    "be replaced by a fixed classification field schema: "
                    + ", ".join(fixed_classification_schema_evidence)
                )
        if not _is_enrichment_iteration_spec(iteration_spec):
            lowered = text.casefold()
            enrichment_lock_hits = [
                phrase
                for phrase in (
                    "enrichment mode",
                    "enrich with missing details only",
                    "do not add, remove, or retype previously established",
                    "treat the previously extracted step list",
                    "ordered synthesistep enrichment",
                    "ordered synthesisstep enrichment",
                )
                if phrase in lowered
            ]
            enrichment_lock_evidence = {
                "is_enrichment_iteration": False,
                "hits": enrichment_lock_hits,
            }
            if enrichment_lock_hits:
                failures.append(
                    f"{target.name}: main iteration prompt contains enrichment-lock "
                    "language reserved for sub-iteration enrichment artifacts: "
                    + ", ".join(enrichment_lock_hits)
                )
        else:
            enrichment_lock_evidence = {"is_enrichment_iteration": True, "hits": []}
    if target.name.upper().startswith(("EXTRACTION_ITER_", "PRE_EXTRACTION_ITER_")):
        component_path = _materializable_prompt_component_path(target)
        expected_component = _materializable_prompt_component_text(context, target)
        if (
            target.name.upper().startswith("EXTRACTION_ITER_")
            and "{accumulated_hints}" in text
        ):
            failures.append(
                f"{target.name}: {{accumulated_hints}} is mechanically injected by "
                "the deterministic component and must not appear in the LLM-authored prompt"
            )
        if expected_component and not component_path.is_file():
            failures.append(
                f"{target.name}: deterministic materializable contract component is missing"
            )
        elif expected_component and component_path.read_text(
            encoding="utf-8", errors="replace"
        ).rstrip() != expected_component.rstrip():
            failures.append(
                f"{target.name}: deterministic materializable contract component "
                "does not equal the compiled iteration scope"
            )
    if target.name.upper().startswith("KG_BUILDING_ITER_"):
        prompt_contract = _prompt_artifact_generation_contract(context, target)
        expected_scope = prompt_contract.get("iteration_owned_scope") or {}
        kg_owned_scope_evidence = {
            "expected": {
                key: sorted(set(expected_scope.get(key) or []))
                for key in ("classes", "object_properties")
            },
            "validation_owner": "llm_semantic_reviewer",
        }
    if target.name.upper().startswith("PRE_EXTRACTION_"):
        prompt_contract = _prompt_artifact_generation_contract(context, target)
        expected_types = set(
            (
                prompt_contract.get("pre_extraction_candidate_type_contract") or {}
            ).get("allowed_candidate_types")
            or []
        )
        candidate_type_evidence = {
            "expected": sorted(expected_types),
            "validation_owner": "llm_semantic_reviewer",
        }
    return {
        "ok": not failures,
        "failures": failures,
        "evidence": {
            "runtime_binding": binding.get("evidence") or {},
            "detached_mechanically_injected_slots": detached_mechanical_slots,
            "fixed_classification_schema": fixed_classification_schema_evidence,
            "structured_ledger_expectation": structured_ledger_expectation,
            "unresolved_residue": residue,
            "onepass_control_plane_hits": (
                binding.get("evidence") or {}
            ).get("onepass_control_plane_hits", []),
            "kg_iteration_owned_scope": kg_owned_scope_evidence,
            "pre_extraction_candidate_types": candidate_type_evidence,
            "main_iteration_enrichment_lock": enrichment_lock_evidence,
        },
    }


def _prompt_semantic_repair_task(
    *,
    context: AgenticGenerationContext,
    target: Path,
    report: dict[str, Any],
) -> str:
    """Build bounded T-Box-fidelity repair instructions for one frozen prompt."""
    relative = (
        target.resolve().relative_to(Path(context.output_root).resolve()).as_posix()
    )
    failures = [
        observation
        for observation in report.get("observations") or []
        if observation.get("status") == "fail"
    ]
    prompt_contract = _prompt_artifact_generation_contract(context, target)
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
                "prompt_generation_contract": prompt_contract,
                "active_tbox_scope": dict(prompt_contract.get("tbox_scope") or {}),
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
        progress=lambda message: print(
            f"[prompt_semantic_repair] {message}", flush=True
        ),
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
        statuses.setdefault(stage, []).append(
            str(observation.get("status") or "unknown")
        )
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
            if key in focus_ids and before_status == "fail":
                resolved.append(key)
                continue
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
    return len(after_failures) < len(before_failures) and not (
        after_failures - before_failures
    )


def _stage_and_semantic_reviews_pass(
    *,
    candidate_report: dict[str, Any],
    semantic_validation: dict[str, Any],
    semantic_review_required: bool,
) -> bool:
    """Return true when every required full-candidate gate is green."""
    semantic_passed = not semantic_review_required or (
        semantic_validation.get("decision") == "pass"
        and not semantic_validation.get("critical_errors")
    )
    return bool(candidate_report.get("stage_ok")) and semantic_passed


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
        "files for the later plan. Dependency IDs must reference known observations that "
        "already pass and are outside observation_ids; do not list a selected failure as "
        "its own dependency.\n\n"
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
    # The scheduler occasionally echoes a selected prompt failure as a dependency.
    # A focus owns its selected failures, so treating one as a prerequisite makes
    # every local prompt repair impossible. Preserve the selected focus and discard
    # only these self-referential dependency entries.
    dependencies = [
        dependency_id for dependency_id in dependencies if dependency_id not in selected
    ]
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
    selected = list(
        dict.fromkeys(_normalize_planner_inventory_path(item, allowed) for item in values)
    )
    invalid = sorted(set(selected) - allowed)
    if invalid:
        raise ValueError(f"{field} selected paths outside inventory: {invalid}")
    if not minimum <= len(selected) <= maximum:
        raise ValueError(f"{field} must select {minimum} to {maximum} paths")
    return selected


def _normalize_planner_inventory_path(value: Any, allowed: set[str]) -> str:
    """Accept an exact path even when the planner appends ``::change intent``."""
    raw = str(value).strip()
    if raw in allowed:
        return raw
    if "::" in raw:
        candidate = raw.split("::", 1)[0].strip()
        if candidate in allowed:
            return candidate
    return raw


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
            target.resolve()
            .relative_to(root)
            .as_posix(): _artifact_generation_contract(context, target)
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
                f"inspection hypotheses escaped active focus: {invalid_observations}"
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
    inspected = {
        relative: allowed[relative].read_text(encoding="utf-8", errors="replace")
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
        "return blocked with no targets. Ancestor-type repairs must copy "
        "ordered_check_contract.required_explicit_ancestor_types and ancestor_algorithm "
        "exactly; do not rewrite missing_explicit_ancestor_type as family membership or "
        "most-specific-subclass typing.\n\n"
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
        dict.fromkeys(
            _normalize_planner_inventory_path(value, set(allowed))
            for value in (plan.get("required_coedits") or [])
        )
    )
    read_only_dependencies = list(
        dict.fromkeys(
            _normalize_planner_inventory_path(value, set(allowed))
            for value in (plan.get("read_only_dependencies") or [])
        )
    )
    invalid_coedits = sorted(set(required_coedits) - set(allowed))
    if invalid_coedits:
        raise ValueError(
            f"required co-edits outside editable inventory: {invalid_coedits}"
        )
    missing_coedits = sorted(set(required_coedits) - set(selected))
    if missing_coedits:
        raise ValueError(f"impact plan omitted required co-edits: {missing_coedits}")
    edited_read_only = sorted(set(read_only_dependencies) & set(selected))
    if edited_read_only:
        raise ValueError(
            f"read-only dependencies selected for editing: {edited_read_only}"
        )
    dependency_order = list(
        dict.fromkeys(
            _normalize_planner_inventory_path(value, set(allowed))
            for value in (plan.get("dependency_order") or [])
        )
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
            json.dumps(context.contract, sort_keys=True, ensure_ascii=False).encode(
                "utf-8"
            )
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
        temp.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
        )
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
            "`generation_contract.relationship_tool_contracts` directly: annotate both public "
            "`subject_iri` and `object_iri` as `Annotated[str, Field(description=...)]`, import "
            "Annotated from typing and Field from pydantic without a fallback shim, include exact "
            "`absolute IRI` and `never a label/name/literal/plain text` phrases, list exact domain "
            "locals for the subject and exact range locals for the object, and reference only the "
            "listed creator_tools in the object description. Do not preserve an incompatible "
            "target_iri API.\n\n"
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


def _paired_prompt_review_targets(
    *,
    context: AgenticGenerationContext,
    review: dict[str, Any],
    editable_targets: list[Path],
) -> tuple[list[Path], list[str]]:
    """Resolve reviewer-selected prompt files without widening the edit boundary."""
    prompts_root = Path(context.prompts_dir).resolve()
    editable = {path.resolve() for path in editable_targets}
    resolved: list[Path] = []
    failures: list[str] = []
    seen: set[Path] = set()
    for finding in review.get("critical_errors") or []:
        for raw_target in finding.get("repair_targets") or []:
            raw = str(raw_target).strip()
            candidate = (prompts_root / raw).resolve()
            if (
                not raw
                or Path(raw).is_absolute()
                or candidate.parent != prompts_root
                or candidate.suffix.casefold() != ".md"
            ):
                failures.append(f"paired_repair_target_outside_prompts_dir:{raw}")
                continue
            if not candidate.is_file():
                failures.append(f"paired_repair_target_missing:{raw}")
                continue
            if candidate not in editable:
                failures.append(f"paired_repair_target_not_editable:{raw}")
                continue
            if candidate not in seen:
                seen.add(candidate)
                resolved.append(candidate)
    if not resolved and not failures:
        failures.append("paired_repair_has_no_targets")
    if failures:
        return [], failures
    root = Path(context.output_root).resolve()
    by_relative = {
        path.resolve().relative_to(root).as_posix(): path for path in resolved
    }
    return (
        [
            by_relative[relative]
            for relative in _fixed_artifact_dependency_order(
                root=root,
                targets=resolved,
            )
        ],
        [],
    )


def _paired_prompt_review_ready(
    *,
    context: AgenticGenerationContext,
    all_editable_targets: list[Path],
    selected_targets: list[Path],
    generation_only: bool,
) -> bool:
    """Gate package-level paired review until every expected pair is editable."""
    if generation_only:
        return False
    expected = [
        path.resolve()
        for path in all_editable_targets
        if path.suffix == ".md"
        and path.name.startswith(("EXTRACTION_ITER_", "KG_BUILDING_ITER_"))
        and not _is_onepass_kg_fragment(path)
    ]
    if not expected:
        return False
    expected_set = set(expected)
    selected_set = {path.resolve() for path in selected_targets}
    if not expected_set.issubset(selected_set):
        return False
    if any(
        not path.is_file()
        or not path.read_text(encoding="utf-8", errors="replace").strip()
        for path in expected
    ):
        return False
    extraction = {
        path.stem.removeprefix("EXTRACTION_ITER_")
        for path in expected
        if path.name.startswith("EXTRACTION_ITER_")
    }
    kg = {
        path.stem.removeprefix("KG_BUILDING_ITER_")
        for path in expected
        if path.name.startswith("KG_BUILDING_ITER_")
    }
    if not extraction or extraction != kg:
        return False
    ontology = getattr(context, "ontology", None)
    blueprint = getattr(context, "iteration_blueprint", {}) or {}
    if getattr(ontology, "role", None) == "main":
        planned_iterations = {
            str(item.get("iteration_number"))
            for item in blueprint.get("iterations") or []
            if isinstance(item, dict) and item.get("iteration_number") is not None
        }
        # Iteration 1 is the pipeline-owned top-identity pass and is therefore
        # absent from the semantic decomposition blueprint, but when emitted it
        # still forms a required extraction/KG prompt pair.
        if "1" in extraction:
            planned_iterations.add("1")
        if planned_iterations and extraction != planned_iterations:
            return False
    return True


def _apply_paired_prompt_materialization_review(
    *,
    context: AgenticGenerationContext,
    model_name: str,
    report: dict[str, Any],
    history: list[dict[str, Any]],
    all_editable_targets: list[Path],
    selected_targets: list[Path],
    generation_only: bool,
    generate_prompts: bool,
    foreign_contracts: list[dict[str, Any]] | None,
    edit_backend: EditBackend,
    protected_target_snapshots: dict[Path, bytes],
) -> tuple[dict[str, Any], dict[str, Any] | None, list[dict[str, Any]]]:
    """Run package-level EXTRACTION/KG paired review when prompt pairs are ready."""
    paired_materialization_review: dict[str, Any] | None = None
    paired_repair_history: list[dict[str, Any]] = []
    prompt_targets = [
        path
        for path in all_editable_targets
        if path.suffix == ".md" and not _is_onepass_kg_fragment(path)
    ]
    # Paired review is package-scoped: use the full prompt pair even when only
    # one side changed during checkpoint resume.
    review_targets = prompt_targets or list(selected_targets)
    if not (
        generate_prompts
        and _paired_prompt_review_ready(
            context=context,
            all_editable_targets=all_editable_targets,
            selected_targets=review_targets,
            generation_only=generation_only,
        )
    ):
        return report, paired_materialization_review, paired_repair_history

    from src.agents.scripts_and_prompts_generation.semantic_script_review import (
        review_paired_prompt_materialization_with_llm,
    )

    paired_materialization_review = review_paired_prompt_materialization_with_llm(
        context=context,
        model_name=model_name,
    )
    history.append(
        {
            "mode": "paired_prompt_materialization_review",
            "review": paired_materialization_review,
            "validation": report,
        }
    )
    if paired_materialization_review.get("decision") == "repair":
        report, paired_materialization_review, paired_repair_history = (
            _run_paired_prompt_repairs(
                context=context,
                model_name=model_name,
                review=paired_materialization_review,
                report=report,
                editable_targets=review_targets,
                foreign_contracts=foreign_contracts,
                edit_backend=edit_backend,
                accepted_snapshots=protected_target_snapshots,
            )
        )
        history.extend(paired_repair_history)
    if paired_materialization_review.get("decision") != "pass":
        paired_failure = (
            "Paired prompt materialization semantic review requires repair:\n"
            + json.dumps(paired_materialization_review, ensure_ascii=False)
        )
        routing_failures = [
            failure
            for item in paired_repair_history
            for failure in item.get("routing_failures") or []
        ]
        report = build_validation_report(
            context,
            foreign_contracts=foreign_contracts,
            write_report=True,
            prompts_required=True,
            extra_failures=[paired_failure, *routing_failures],
        )
    return report, paired_materialization_review, paired_repair_history


def _paired_prompt_repair_task(
    *,
    finding: dict[str, Any],
    target: Path,
    completed_target_names: list[str],
    accepted_findings: list[dict[str, Any]],
    focused_feedback: dict[str, Any] | None,
) -> str:
    """Build one compact finding-scoped task for exactly one prompt file."""
    finding_context = {
        "where_it_is_wrong": {
            "finding": str(finding.get("finding") or ""),
            "iteration": str(finding.get("iteration") or ""),
            "evidence": list(finding.get("evidence") or []),
        },
        "what_is_correct": {
            "expected_behavior": str(finding.get("expected_behavior") or ""),
            "contract_evidence": list(finding.get("contract_evidence") or []),
        },
        "current_target": target.name,
        "repair_targets": list(finding.get("repair_targets") or []),
        "completed_targets_for_this_finding": completed_target_names,
        "previously_resolved_findings": [
            {
                "finding": str(item.get("finding") or ""),
                "expected_behavior": str(item.get("expected_behavior") or ""),
            }
            for item in accepted_findings
        ],
        "focused_feedback": focused_feedback or {},
    }
    return (
        "Repair exactly the one paired semantic finding below in the one editable Markdown "
        "runtime prompt. Do not perform unrelated cleanup and do not attempt to edit another "
        "file. Preserve previously resolved findings. Use the supplied expected behavior and "
        "direct contract evidence; never invent fixture-specific facts. This is one file step "
        "inside a sequential multi-file repair, so make every change this file needs even when "
        "another listed target will be edited afterward. If the target is an extension KG "
        "prompt, never add a forbidden main-ontology hints placeholder; Mode A consumes "
        "ref-entity-relations.v1 from the already declared source-content slot. Ignore any "
        "finding that asks to replace that slot.\n\n"
        + json.dumps(finding_context, ensure_ascii=False)
    )


def _run_paired_prompt_repairs(
    *,
    context: AgenticGenerationContext,
    model_name: str,
    review: dict[str, Any],
    report: dict[str, Any],
    editable_targets: list[Path],
    foreign_contracts: list[dict[str, Any]] | None,
    edit_backend: EditBackend,
    accepted_snapshots: dict[Path, bytes] | None = None,
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    """Resolve findings one at a time, then run at most five global reviews."""
    from src.agents.scripts_and_prompts_generation.semantic_script_review import (
        review_paired_prompt_finding_with_llm,
        review_paired_prompt_materialization_with_llm,
    )

    current_review = review
    current_report = report
    records: list[dict[str, Any]] = []
    accepted_findings: list[dict[str, Any]] = []
    prompts_root = Path(context.prompts_dir).resolve()
    max_global_reviews = 5

    for global_review_index in range(1, max_global_reviews + 1):
        records.append(
            {
                "mode": "paired_prompt_global_review",
                "global_review": global_review_index,
                "decision": current_review.get("decision"),
                "summary": str(current_review.get("summary") or ""),
                "finding_count": len(current_review.get("critical_errors") or []),
            }
        )
        if current_review.get("decision") == "pass":
            current_report = build_validation_report(
                context,
                foreign_contracts=foreign_contracts,
                write_report=True,
                prompts_required=True,
            )
            return current_report, current_review, records
        # The fifth LLM review is the final certification attempt. A failing
        # result is returned as-is rather than making uncertified edits.
        if global_review_index == max_global_reviews:
            records[-1]["final_review_exhausted"] = True
            break

        for finding_index, raw_finding in enumerate(
            current_review.get("critical_errors") or [], start=1
        ):
            finding = dict(raw_finding)
            ordered_targets, routing_failures = _paired_prompt_review_targets(
                context=context,
                review={"critical_errors": [finding]},
                editable_targets=editable_targets,
            )
            finding_record: dict[str, Any] = {
                "mode": "paired_prompt_finding_repair",
                "global_review": global_review_index,
                "finding_index": finding_index,
                "finding": str(finding.get("finding") or ""),
                "expected_behavior": str(
                    finding.get("expected_behavior") or ""
                ),
                "targets": [path.name for path in ordered_targets],
                "file_edits": [],
                "focused_verdicts": [],
                "accepted": False,
            }
            records.append(finding_record)
            if routing_failures:
                finding_record.update(
                    {
                        "routing_failures": routing_failures,
                        "fail_closed": True,
                    }
                )
                return current_report, current_review, records

            finding_snapshots = {
                path: path.read_bytes() for path in ordered_targets
            }
            focused_feedback: dict[str, Any] | None = None
            finding_attempt = 0
            while True:
                finding_attempt += 1
                completed_names: list[str] = []
                edit_failed = False
                for target in ordered_targets:

                    def validate_target(
                        target: Path = target,
                    ) -> dict[str, Any]:
                        binding = validate_prompt_runtime_bindings(target, context)
                        return {
                            "ok": bool(binding.get("ok")),
                            "failures": list(binding.get("failures") or []),
                            "target": target.name,
                        }

                    edit = run_llm_unified_diff_editor(
                        model_name=model_name,
                        output_root=Path(context.output_root),
                        targets=[target],
                        task_prompt=_paired_prompt_repair_task(
                            finding=finding,
                            target=target,
                            completed_target_names=completed_names,
                            accepted_findings=accepted_findings,
                            focused_feedback=focused_feedback,
                        ),
                        max_attempts=5,
                        validate=validate_target,
                        max_targets=1,
                        progress=lambda message: print(
                            f"[pure_llm] {message}", flush=True
                        ),
                        edit_backend=edit_backend,
                    )
                    finding_record["file_edits"].append(
                        {
                            "finding_attempt": finding_attempt,
                            "target": target.name,
                            "ok": bool(edit.get("ok")),
                            "backend": str(edit.get("backend") or ""),
                            "changed_files": list(edit.get("changed_files") or []),
                            "summary": str(
                                (edit.get("edit_payload") or {}).get("summary")
                                or edit.get("summary")
                                or ""
                            ),
                            "failure_codes": list(
                                edit.get("failure_codes") or []
                            ),
                            "failure_messages": list(
                                edit.get("failure_messages") or []
                            ),
                        }
                    )
                    if not edit.get("ok"):
                        edit_failed = True
                        break
                    completed_names.append(target.name)
                if edit_failed:
                    for path, content in finding_snapshots.items():
                        path.write_bytes(content)
                    finding_record["fail_closed"] = True
                    return current_report, current_review, records

                target_sources = {
                    path.resolve().relative_to(prompts_root).as_posix(): (
                        path.read_text(encoding="utf-8", errors="replace")
                    )
                    for path in ordered_targets
                }
                focused_verdict = review_paired_prompt_finding_with_llm(
                    model_name=model_name,
                    finding=finding,
                    target_sources=target_sources,
                    accepted_findings=accepted_findings,
                )
                finding_record["focused_verdicts"].append(
                    {
                        "finding_attempt": finding_attempt,
                        **focused_verdict,
                    }
                )
                if focused_verdict.get("decision") == "resolved":
                    finding_record["accepted"] = True
                    accepted_findings.append(finding)
                    if accepted_snapshots is not None:
                        accepted_snapshots.update(
                            {
                                path: path.read_bytes()
                                for path in ordered_targets
                            }
                        )
                    break
                focused_feedback = {
                    "summary": str(focused_verdict.get("summary") or ""),
                    "unresolved_reasons": list(
                        focused_verdict.get("unresolved_reasons") or []
                    ),
                }

        current_review = review_paired_prompt_materialization_with_llm(
            context=context,
            model_name=model_name,
        )
    return current_report, current_review, records


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
        before_dimensions = (before_semantic_report.get("consensus") or {}).get(
            "scores"
        ) or {}
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
                        "semantic_observations": before_semantic_report.get(
                            "observations"
                        )
                        or [],
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
        f"targets={_progress_paths(plan['targets'])} "
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
            write_report=False,
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
        semantic_validation = semantic_validate() if semantic_validate else {}
        if semantic_validate:
            print(
                "[pure_llm] phase=repair-review action=semantic-review "
                f"decision={semantic_validation.get('decision') or 'unknown'} "
                f"critical_errors={len(semantic_validation.get('critical_errors') or [])}",
                flush=True,
            )
        if _stage_and_semantic_reviews_pass(
            candidate_report=candidate_report,
            semantic_validation=semantic_validation,
            semantic_review_required=semantic_validate is not None,
        ):
            delta_review = {
                "decision": "accept",
                "reason": "Full stage validation and semantic review passed.",
                "resolved_or_improved": [],
                "regressions": [],
                "next_evidence_needed": [],
                "skipped": True,
            }
            print(
                "[pure_llm] phase=repair-review action=all-green-short-circuit "
                "decision=accept",
                flush=True,
            )
            return _validation_outcome(
                mechanical,
                accepted=True,
                rejection_failure="unreachable_all_green_rejection",
                delta_review=delta_review,
            )
        before_failures = set(report.get("failures") or [])
        after_failures = set(candidate_report.get("failures") or [])
        strict_partial_progress = _is_strict_validation_improvement(
            before_failures,
            after_failures,
        )
        if strict_partial_progress and not observation_delta["protected_regression"]:
            delta_review = {
                "decision": "accept",
                "reason": (
                    "Deterministic partial progress: validation failures strictly decreased "
                    "without introducing a new failure."
                ),
                "resolved_or_improved": sorted(before_failures - after_failures),
                "regressions": [],
                "next_evidence_needed": sorted(after_failures),
                "skipped": True,
            }
            print(
                "[pure_llm] phase=repair-review action=strict-partial-progress "
                f"decision=accept before={len(before_failures)} after={len(after_failures)}",
                flush=True,
            )
            return _validation_outcome(
                mechanical,
                accepted=True,
                rejection_failure="unreachable_strict_partial_progress_rejection",
                delta_review=delta_review,
            )
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
        semantic_resolved = bool(
            semantic_validate
            and semantic_validation.get("decision") == "pass"
            and not semantic_validation.get("critical_errors")
        )
        accepted = not observation_delta["protected_regression"] and (
            observation_delta["focus_progress"]
            or semantic_resolved
            or (delta_review["decision"] == "accept" and reviewer_progress)
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
    parallel_generation: bool = True,
    max_generation_workers: int = 5,
    edit_backend: EditBackend = "exact_edits",
    target_artifacts: list[str] | None = None,
    protected_artifacts: dict[Path, bytes] | None = None,
) -> dict[str, Any]:
    """Generate and repair artifacts using plain LLM artifact-edit calls."""
    configure_llm_invocation_journal(context.output_root)
    if max_generation_workers < 1:
        raise ValueError("max_generation_workers must be at least 1")
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
    all_editable_targets = _editable_artifacts(
        context,
        generate_scripts=generate_scripts,
        generate_prompts=generate_prompts,
    )
    targets = list(all_editable_targets)
    protected_target_snapshots: dict[Path, bytes] = dict(protected_artifacts or {})
    if target_artifacts:
        requested = {str(item).replace("\\", "/") for item in target_artifacts}
        root = Path(context.output_root).resolve()
        selected = [
            path
            for path in targets
            if path.name in requested
            or path.resolve().relative_to(root).as_posix() in requested
        ]
        matched = {
            requested_name
            for requested_name in requested
            if any(
                path.name == requested_name
                or path.resolve().relative_to(root).as_posix() == requested_name
                for path in selected
            )
        }
        unknown = sorted(requested - matched)
        if unknown:
            return {
                "mode": "pure_llm_targeted",
                "model": model_name,
                "ok": False,
                "failures": ["unknown_target_artifacts: " + ", ".join(unknown)],
                "history": [],
            }
        targets = selected
        selected_resolved = {path.resolve() for path in selected}
        protected_target_snapshots.update(
            {
                path: path.read_bytes()
                for path in all_editable_targets
                if path.resolve() not in selected_resolved and path.is_file()
            }
        )

    artifact_states = ArtifactStateStore(
        context.output_root, context.ontology.name
    )
    artifact_states.initialize(targets)
    artifact_states.recover_interrupted()
    resumed_passed_targets = [
        path for path in targets if artifact_states.is_matching_passed(path)
    ]
    if resumed_passed_targets:
        resumed_set = {path.resolve() for path in resumed_passed_targets}
        protected_target_snapshots.update(
            {path: path.read_bytes() for path in resumed_passed_targets}
        )
        targets = [path for path in targets if path.resolve() not in resumed_set]
        print(
            "[pure_llm] phase=checkpoint-resume action=skip-passed "
            f"targets={_progress_paths(resumed_passed_targets)}",
            flush=True,
        )

    for prompt_target in targets:
        if prompt_target.suffix == ".md":
            _write_materializable_prompt_component(context, prompt_target)
            _detach_deterministic_tbox_from_pre_prompt(prompt_target)

    def _restore_non_target_artifacts() -> None:
        for path, content in protected_target_snapshots.items():
            if not path.is_file() or path.read_bytes() != content:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
    if not targets and resumed_passed_targets:
        report = build_validation_report(
            context,
            foreign_contracts=foreign_contracts,
            write_report=True,
            prompts_required=generate_prompts,
            include_prompt_checks=generate_prompts,
        )
        history: list[dict[str, Any]] = []
        report, paired_materialization_review, paired_repair_history = (
            _apply_paired_prompt_materialization_review(
                context=context,
                model_name=model_name,
                report=report,
                history=history,
                all_editable_targets=all_editable_targets,
                selected_targets=list(resumed_passed_targets),
                generation_only=generation_only,
                generate_prompts=generate_prompts,
                foreign_contracts=foreign_contracts,
                edit_backend=edit_backend,
                protected_target_snapshots=protected_target_snapshots,
            )
        )
        return {
            "mode": "pure_llm_checkpoint_resume",
            "model": model_name,
            "ok": bool(report.get("ok"))
            and (
                paired_materialization_review is None
                or paired_materialization_review.get("decision") == "pass"
            ),
            "failures": list(report.get("failures") or []),
            "history": history,
            "resumed_passed": [str(path) for path in resumed_passed_targets],
            "final_report": report,
            "checkpoint_preserved": True,
            "paired_materialization_review": paired_materialization_review,
            "paired_repair_history": paired_repair_history,
        }
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
    targeted_active_artifacts = (
        [
            path.resolve()
            .relative_to(Path(context.output_root).resolve())
            .as_posix()
            for path in targets
        ]
        if target_artifacts
        else None
    )
    report = build_validation_report(
        context,
        foreign_contracts=foreign_contracts,
        write_report=True,
        prompts_required=generate_prompts,
        include_prompt_checks=generate_prompts,
        active_artifacts=targeted_active_artifacts,
    )
    initial_files: list[dict[str, Any]] = []

    def _append_artifact_record(record: dict[str, Any]) -> None:
        target_path = Path(record["target"])
        patch_ok = bool((record.get("patch") or {}).get("ok"))
        stage_clean = bool(record.get("stage_clean", patch_ok))
        validation = record.get("stage_validation")
        artifact_states.transition(
            target_path,
            "passed" if patch_ok and stage_clean else "failed",
            reason=None if patch_ok and stage_clean else "artifact_generation_failed",
            validation=validation if isinstance(validation, dict) else None,
        )
        initial_files.append(record)

    if repair_only:
        empty_targets = [
            path
            for path in targets
            if not path.read_text(encoding="utf-8", errors="replace").strip()
        ]
        if empty_targets:
            for path in empty_targets:
                artifact_states.transition(
                    path,
                    "failed",
                    reason="repair_only_checkpoint_artifact_empty",
                )
            return {
                "mode": "pure_llm_repair_only",
                "model": model_name,
                "ok": False,
                "failures": ["repair_only_checkpoint_contains_empty_artifacts"],
                "final_report": report,
                "history": [],
            }
        for path in targets:
            artifact_states.transition(path, "repairing")
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
                if path.suffix == ".py"
                and path.name != "main.py"
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
                    include_prompt_checks=generate_prompts,
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
                                path.relative_to(Path(context.output_root)).as_posix()
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
                raise ValueError(
                    "Runtime adapter synthesis requires exactly one main.py"
                )
            before_report = report
            runtime_report: dict[str, Any] = {}

            def validate_runtime_adapter() -> dict[str, Any]:
                nonlocal runtime_report
                runtime_report = build_validation_report(
                    context,
                    foreign_contracts=foreign_contracts,
                    write_report=True,
                    prompts_required=generate_prompts,
                    include_prompt_checks=generate_prompts,
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
                    include_prompt_checks=generate_prompts,
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
        parallel_patches: dict[Path, dict[str, Any]] = {}
        for target_index, target in enumerate(generation_targets, start=1):
            artifact_states.transition(target, "generating")
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
            queued_parallel_patch = parallel_patches.pop(target, None)
            if (
                incremental_generation_repair
                and existing_text.strip()
                and queued_parallel_patch is None
            ):
                artifact_states.transition(target, "validating")
                stage_report = build_validation_report(
                    context,
                    foreign_contracts=foreign_contracts,
                    write_report=False,
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
                    artifact_record["stage_clean"] = bool(stage_report.get("stage_ok"))
                _append_artifact_record(artifact_record)
                if not artifact_record["stage_clean"]:
                    break
                continue
            if queued_parallel_patch is not None:
                patch_report = _publish_isolated_candidate(
                    target, queued_parallel_patch
                )
            elif parallel_generation:
                wave = [
                    candidate
                    for candidate in _parallel_generation_wave(
                        target, generation_targets
                    )
                    if not candidate.read_text(
                        encoding="utf-8", errors="replace"
                    ).strip()
                    and candidate not in parallel_patches
                ]
                if len(wave) > 1:
                    print(
                        f"[pure_llm] phase=parallel-generation workers="
                        f"{min(max_generation_workers, len(wave))} "
                        f"targets={','.join(item.name for item in wave)}",
                        flush=True,
                    )
                    parallel_patches.update(
                        _generate_artifact_wave(
                            context=context,
                            report=report,
                            targets=wave,
                            model_name=model_name,
                            edit_backend=edit_backend,
                            max_workers=max_generation_workers,
                        )
                    )
                    patch_report = _publish_isolated_candidate(
                        target, parallel_patches.pop(target)
                    )
                else:
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
                            (
                                lambda target=target: (
                                    _validate_generated_prompt_hard_gates(
                                        target, context
                                    )
                                )
                            )
                            if target.suffix == ".md"
                            else None
                        ),
                        progress=lambda message: print(
                            f"[pure_llm] {message}", flush=True
                        ),
                        edit_backend=edit_backend,
                    )
            else:
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
                        (
                            lambda target=target: _validate_generated_prompt_hard_gates(
                                target, context
                            )
                        )
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
                _append_artifact_record(artifact_record)
                break
            _restore_non_target_artifacts()
            if target.suffix == ".md":
                _write_materializable_prompt_component(context, target)
            artifact_states.transition(target, "validating")
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
                    artifact_states.transition(target, "repairing")
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
                    if target.suffix == ".md":
                        _write_materializable_prompt_component(context, target)
                artifact_record["semantic_repairs"] = semantic_repairs
                artifact_record["stage_validation"] = stage_report
                artifact_record["stage_clean"] = bool(stage_report.get("stage_ok"))
                print(
                    f"[pure_llm] artifact={target_index}/{len(generation_targets)} "
                    "phase=artifact-review scope=prompt-final "
                    + _progress_validation(stage_report),
                    flush=True,
                )
                _restore_non_target_artifacts()
                if not artifact_record["stage_clean"]:
                    _append_artifact_record(artifact_record)
                    break
                # A later target in the same targeted generation run may invoke
                # validators that rewrite the deterministic prompt slice. Once
                # this target passes its final semantic gate, protect the
                # accepted bytes just like a pre-existing non-target artifact.
                protected_target_snapshots[target] = target.read_bytes()
            elif incremental_generation_repair:
                print(
                    f"[pure_llm] artifact={target_index}/{len(generation_targets)} "
                    "phase=artifact-review scope=stage-contract action=start",
                    flush=True,
                )
                stage_report = build_validation_report(
                    context,
                    foreign_contracts=foreign_contracts,
                    write_report=False,
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
                        artifact_states.transition(target, "repairing")
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
                            max_focus_targets=1
                            if target.suffix == ".md"
                            else max_focus_targets,
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
                    artifact_record["stage_clean"] = bool(stage_report.get("stage_ok"))
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
                        artifact_states.transition(target, "repairing")
                        semantic_report = build_validation_report(
                            context,
                            foreign_contracts=foreign_contracts,
                            write_report=False,
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
                                lambda target=target: (
                                    review_generated_artifact_semantics_with_llm(
                                        context=context,
                                        artifact_path=target,
                                        model_name=model_name,
                                    )
                                )
                            ),
                        )
                        artifact_record.setdefault("semantic_repairs", []).append(
                            semantic_repair
                        )
                        if not semantic_repair.get("accepted"):
                            break
                        # _run_stage_focused_repair already ran semantic_validate
                        # against the accepted candidate. Reuse that exact verdict;
                        # an immediate independent re-review can contradict a pass
                        # stochastically and discard a mechanically green artifact.
                        semantic_review = dict(
                            semantic_repair.get("semantic_validation") or {}
                        )
                        if not semantic_review:
                            break
                        semantic_reviews.append(semantic_review)
                    artifact_record["semantic_reviews"] = semantic_reviews
                    artifact_record["stage_clean"] = bool(
                        stage_report.get("stage_ok")
                        and semantic_reviews
                        and semantic_reviews[-1].get("decision") == "pass"
                    )
                if not artifact_record["stage_clean"]:
                    _append_artifact_record(artifact_record)
                    break
            _append_artifact_record(artifact_record)
        report = build_validation_report(
            context,
            foreign_contracts=foreign_contracts,
            write_report=True,
            prompts_required=generate_prompts,
            include_prompt_checks=generate_prompts,
            extra_failures=[
                failure
                for item in initial_files
                for failure in (
                    (item["patch"].get("failures") or [])
                    + (
                        (item.get("stage_validation") or {}).get("failures") or []
                        if not item.get("stage_clean", True)
                        else []
                    )
                )
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
            if not incremental_generation_repair:
                passed_targets = {
                    Path(item["target"]).resolve()
                    for item in initial_files
                    if item.get("stage_clean")
                    and (item.get("patch") or {}).get("ok")
                }
                for path, content in generation_snapshots.items():
                    if path.resolve() not in passed_targets:
                        path.write_bytes(content)
            return {
                "mode": (
                    "pure_llm_incremental_generation"
                    if incremental_generation_repair
                    else "pure_llm_unified_diff"
                ),
                "model": model_name,
                "ok": False,
                "final_report": report,
                "history": history,
                **(
                    {
                        "generation_complete": False,
                        "checkpoint_preserved": True,
                        "artifact_dependency_order": artifact_dependency_order,
                        "stopped_at_artifact": initial_files[-1]["target"],
                    }
                    if incremental_generation_repair
                    else {"rolled_back": True}
                ),
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
                    include_prompt_checks=generate_prompts,
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
                include_prompt_checks=generate_prompts,
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
                        include_prompt_checks=generate_prompts,
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
                include_prompt_checks=generate_prompts,
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
            focus_resolved_without_regression = bool(
                observation_delta.get("focus_progress")
            ) and not observation_delta.get("protected_regression")
            accepted = (
                bool(candidate_report.get("ok"))
                or focus_resolved_without_regression
                or (
                    delta_review["decision"] == "accept"
                    and (not focused_repair or focus_resolved_without_regression)
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
                include_prompt_checks=generate_prompts,
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
        history.append(
            {"round": step_index + 1, "mode": "staged_repair", **step_record}
        )
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
                stop_reason="globally_valid"
                if report.get("ok")
                else "round_incomplete",
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
    paired_materialization_review: dict[str, Any] | None = None
    paired_repair_history: list[dict[str, Any]] = []
    report, paired_materialization_review, paired_repair_history = (
        _apply_paired_prompt_materialization_review(
            context=context,
            model_name=model_name,
            report=report,
            history=history,
            all_editable_targets=all_editable_targets,
            selected_targets=targets,
            generation_only=generation_only,
            generate_prompts=generate_prompts,
            foreign_contracts=foreign_contracts,
            edit_backend=edit_backend,
            protected_target_snapshots=protected_target_snapshots,
        )
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
            paired_materialization_review is None
            or paired_materialization_review.get("decision") == "pass"
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
            (
                len((step.get("plan") or {}).get("targets") or [])
                for step in repair_steps
            ),
            default=0,
        ),
        "checkpoint_manifest": str(manifest_path) if manifest_path else None,
        "paired_materialization_review": paired_materialization_review,
        "paired_repair_history": paired_repair_history,
    }
    targeted_checkpoint_clean = bool(target_artifacts) and bool(initial_files) and all(
        bool(item.get("stage_clean")) and bool((item.get("patch") or {}).get("ok"))
        for item in initial_files
    )
    if result["ok"]:
        # Final package validation may regenerate the deterministic artifact
        # slice. Reapply every accepted targeted prompt after all validation
        # and reporting side effects have finished.
        _restore_non_target_artifacts()
    if not result["ok"] and not repair_only and not targeted_checkpoint_clean:
        passed_targets = {
            Path(item["target"]).resolve()
            for item in initial_files
            if item.get("stage_clean") and (item.get("patch") or {}).get("ok")
        }
        passed_targets.update(path.resolve() for path in resumed_passed_targets)
        rolled_any = False
        for path, content in generation_snapshots.items():
            if path.resolve() in passed_targets:
                continue
            path.write_bytes(content)
            rolled_any = True
        if rolled_any:
            result["rolled_back"] = True
        else:
            result["checkpoint_preserved"] = True
    elif targeted_checkpoint_clean:
        result["checkpoint_preserved"] = True
    elif repair_only:
        # Each candidate is already transactional: the unified-diff editor restores
        # rejected changes. Preserve accepted repair deltas even when the package
        # still has unrelated failures and therefore is not globally complete yet.
        accepted_repair_targets = {
            Path(str(raw_target)).name
            for step in repair_steps
            if step.get("accepted")
            for raw_target in ((step.get("plan") or {}).get("targets") or [])
        }
        for target in targets:
            target_passed = bool(report.get("ok")) or (
                target.name in accepted_repair_targets
            )
            artifact_states.transition(
                target,
                "passed" if target_passed else "failed",
                reason=None
                if target_passed
                else "repair_only_validation_incomplete",
                validation=report,
            )
        result["checkpoint_preserved"] = True
    return result
