"""Generic golden repair guidance selected semantically by LLM repair roles."""

from __future__ import annotations

from typing import Any


REPAIR_SKILLS: tuple[dict[str, Any], ...] = (
    {
        "skill_id": "syntax-import-recovery",
        "purpose": "Restore parseability and importability before deeper runtime work.",
        "standard_repairs": [
            "Make the smallest syntax-valid edit around the reported symbol or line.",
            "Preserve existing public names and package-relative imports.",
            "Validate syntax and import smoke before changing runtime semantics.",
        ],
        "anti_patterns": ["full-file rewrite", "suppressing import errors", "no-op stubs"],
        "acceptance_evidence": ["syntax passes", "import smoke passes"],
    },
    {
        "skill_id": "mcp-tool-discoverability",
        "purpose": "Expose public tools through the canonical registry used by callers.",
        "standard_repairs": [
            "Define the exact public callable name at module scope.",
            "Register the callable on the canonical exported MCP object.",
            "Keep registry introspection compatible with the runtime and validator.",
        ],
        "anti_patterns": ["duplicate shadow registry", "marker strings", "unregistered wrappers"],
        "acceptance_evidence": ["tool is discoverable by exact public name"],
    },
    {
        "skill_id": "callable-signature-adapter",
        "purpose": "Adapt stable public calling conventions without discarding semantic inputs.",
        "standard_repairs": [
            "Keep one explicit public signature matching the caller contract.",
            "Parse and normalize inputs in a private adapter.",
            "Return the documented serializable result shape.",
        ],
        "anti_patterns": ["catch-all no-op arguments", "silent input dropping", "fixture branches"],
        "acceptance_evidence": ["runtime invocation succeeds", "result schema parses"],
    },
    {
        "skill_id": "hint-key-normalization",
        "purpose": "Canonicalize repeat-indexed or tool-shaped keys to declared schema locals.",
        "standard_repairs": [
            "Normalize at the adapter boundary before validation or dispatch.",
            "Use declared contract inventories rather than fixture-specific aliases.",
            "Reject unsupported keys only after canonicalization.",
        ],
        "anti_patterns": ["hard-coded document values", "substring routing", "silent dropping"],
        "acceptance_evidence": ["canonical and repeat-indexed forms materialize equivalently"],
    },
    {
        "skill_id": "graph-reachability-top-linking",
        "purpose": "Ensure every materialized node is reachable from the declared top graph.",
        "standard_repairs": [
            "Create or reuse the top entity before dependent nodes.",
            "Add the T-Box-declared top or parent link for every created node.",
            "Handle external quantity ranges through the same reachability contract.",
        ],
        "anti_patterns": ["orphan nodes", "invented predicates", "post-hoc fixture links"],
        "acceptance_evidence": ["all created nodes are reachable from top_iri"],
    },
    {
        "skill_id": "positive-integer-ordering",
        "purpose": "Enforce ordered-member values as unique positive xsd:integer literals.",
        "standard_repairs": [
            "Validate integer type and value greater than zero at the creation boundary.",
            "Serialize using the ontology datatype contract.",
            "Reject missing, fractional, duplicate, zero, and negative orders.",
        ],
        "anti_patterns": ["string order literals", "implicit float coercion", "renumbering fixtures"],
        "acceptance_evidence": ["ordering probes pass for valid and invalid cases"],
    },
    {
        "skill_id": "creator-atomicity",
        "purpose": (
            "Ensure a complex create_* call performs zero RDF mutation unless every "
            "public input is valid."
        ),
        "standard_repairs": [
            "Validate label, required order, and every non-None datatype input before the first bound mutator call.",
            "Use the Python types projected by datatype_inputs, including rejecting bool for integer-only inputs.",
            "Return the fixed runtime error envelope immediately when any input is invalid.",
            "After all validation passes, call the bound creator once and write only supplied datatype values.",
        ],
        "anti_patterns": [
            "public set_<property> tools",
            "whole-graph snapshot or rollback in generated code",
            "deleting or recreating an existing entity",
            "ontology-specific or fixture-specific validation branches",
            "calling an entity or datatype writer before all input checks complete",
        ],
        "acceptance_evidence": [
            "tool:create_<Class>#invalid_input_no_mutation passes for new and reused entities",
            "valid complex creator call writes every supplied datatype",
        ],
    },
    {
        "skill_id": "entity-reuse-normalization",
        "purpose": "Reuse semantically equivalent labeled targets before minting new individuals.",
        "standard_repairs": [
            "Normalize labels in one shared lookup boundary.",
            "Retry normalized and context-suffix-stripped forms before minting.",
            "Treat list-valued labels as separate targets.",
        ],
        "anti_patterns": ["global fuzzy matching", "fixture aliases", "merging distinct list items"],
        "acceptance_evidence": ["equivalent labels reuse IRIs without collapsing distinct entities"],
    },
    {
        "skill_id": "package-relative-import",
        "purpose": "Use package-local infrastructure without environment-dependent imports.",
        "standard_repairs": [
            "Use explicit relative imports from the generated package.",
            "Import only symbols actually used by the module.",
            "Do not hide invalid imports behind broad fallbacks.",
        ],
        "anti_patterns": ["top-level sibling import", "invented external module", "wildcard fallback"],
        "acceptance_evidence": ["package import succeeds from its parent environment"],
    },
    {
        "skill_id": "rdf-runtime-export",
        "purpose": (
            "Expose actually materialized RDF as non-empty, parseable Turtle through the "
            "public runtime boundary."
        ),
        "standard_repairs": [
            "Use explicit relative imports from `._fixed_rdf_runtime` for graph state and export.",
            "Materialize real domain triples before calling `serialize_turtle` or `export_graph_result`.",
            "Return the fixed runtime's non-empty `ttl` payload through the public adapter.",
            "Preserve public tool names, signatures, registration, and semantic input handling.",
        ],
        "anti_patterns": [
            "returning status sentinels such as `ok` where RDF is expected",
            "serializing an empty graph",
            "prefix-only placeholder Turtle",
            "custom serializer reimplementation",
            "validator-specific dummy triples",
        ],
        "acceptance_evidence": [
            "public materialization creates semantic triples",
            "returned ttl is non-empty and rdflib-parseable",
            "top_iri and graph reachability checks can run",
        ],
    },
    {
        "skill_id": "prompt-contract-fidelity",
        "purpose": "Align prompts with T-Box comments, materializable hints, and evidence rules.",
        "standard_repairs": [
            "State generic schema and evidence obligations explicitly.",
            "Keep extraction fields consumable by the runtime adapter.",
            "Preserve source-grounded, negation, ordering, and linked-target rules.",
        ],
        "anti_patterns": ["fixture entities", "unresolved placeholders", "domain-value memorization"],
        "acceptance_evidence": ["prompt contract checks pass without fixture-specific content"],
    },
    {
        "skill_id": "relationship-parameter-metadata",
        "purpose": (
            "Align object-property tool metadata with the T-Box-compiled relationship "
            "tool contract."
        ),
        "standard_repairs": [
            "Treat relationship_tool_contracts as authoritative for each predicate.",
            "Keep object_iri as Annotated[str, Field(description=...)] and require an absolute IRI, never a label/name/literal/plain text.",
            "Reference only creator_tools explicitly listed by the contract and require the subject IRI returned by a successful creator call.",
            "For external or creator-free targets, require an existing absolute IRI without inventing a create_* tool.",
            "Preserve public add_<predicate_local> names, signatures, runtime behavior, and concise docstrings.",
        ],
        "anti_patterns": [
            "creator names inferred from domain knowledge",
            "range aliases not present in the contract",
            "hard-coded ontology or instance names",
            "plain-text object targets",
        ],
        "acceptance_evidence": [
            "relationship parameter metadata validation passes against relationship_tool_contracts"
        ],
    },
    {
        "skill_id": "small-unified-diff",
        "purpose": "Produce a mechanically valid patch against the exact supplied file revision.",
        "standard_repairs": [
            "Treat `editable_files.content` as the sole source of truth for the current revision.",
            "Copy every removed and unchanged context line character-for-character from that content.",
            "Use numeric hunk headers and prefer one small hunk per changed symbol.",
            "Before responding, locate each complete old-side hunk sequence verbatim in the supplied content.",
            "On retry, discard the rejected diff and regenerate against the original supplied content.",
        ],
        "anti_patterns": [
            "bare @@ headers",
            "context reconstructed from memory",
            "context copied from a rejected candidate",
            "estimated old text",
            "whole-file rewrites",
        ],
        "acceptance_evidence": ["git apply --check succeeds"],
    },
)


def repair_skill_catalog() -> list[dict[str, Any]]:
    """Return JSON-safe copies for LLM prompts."""
    return [dict(skill) for skill in REPAIR_SKILLS]


def repair_skill_ids() -> set[str]:
    return {str(skill["skill_id"]) for skill in REPAIR_SKILLS}
