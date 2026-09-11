"""Prompt role contracts, generation guidance, and semantic-ledger rules.

Frozen English. Do not rewrite the strings. See contracts/README.md.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.extraction_prompt_generation.compile.extension_prompt import (
    extension_extraction_runtime_policy,
)
from src.extraction_prompt_generation.generate.extraction_prompts.contracts.scope import (
    _is_enrichment_iteration_spec,
)


def _extension_meta_prompt_policy(target: Path | None = None) -> dict[str, Any]:
    """In-code extension extraction slot contract. KG prompts are not authored."""
    if target is not None and target.name.startswith("KG_BUILDING_"):
        raise ValueError("KG-building prompts are not part of this generation package")
    return extension_extraction_runtime_policy()


def _generic_prompt_pipeline_role(target: Path) -> dict[str, Any]:
    """Return ontology-independent pipeline responsibilities for a prompt slot."""
    del target
    return {
        "role": "iteration_scoped_runtime_prompt",
        "required_sequence": [],
    }


def _prompt_role_contract(
    target: Path | None,
    generation_contract: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Must / must-not English for one prompt filename. Do not rewrite the strings."""
    name = target.name if target is not None else ""
    if target is not None and target.suffix == ".md":
        prompt_specific_must: list[str] = []
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
            if target.name != "EXTRACTION_ITER_1.md" or semantic_hints:
                prompt_specific_must.extend(
                    [
                        "Extract hints for the current target entity only. Prefer source "
                        "spans whose headings, labels, or local context match the current "
                        "target entity label. Do not copy fields from another top entity "
                        "when the current target entity has distinct source evidence. "
                        "Identity-bearing owned occurrences (the procedure product and "
                        "the in-document procedure heading or anchor) must use the bound "
                        "target-entity label; do not bind a sibling procedure's heading "
                        "or product identifier.",
                    ]
                )
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
                        "an occurrence is ordered, emit hasOrder as a standalone "
                        "property-local line (e.g., hasOrder: 3). Do not require or treat "
                        "the heading form `<SubclassLocal> (Order: <n>)` as the order "
                        "encoding, and do not require parenthetical range tags, "
                        "one-space-indented identity children, or an `(inherited global "
                        "context)` suffix.",
                        "Require every source-supported in-scope scalar, exact quantity "
                        "lexeme, and ordering fact as a standalone property-local line "
                        "under the owning occurrence. Prose must not be the sole carrier "
                        "for those values. Separate "
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
                        "include its complete source-grounded value as a standalone "
                        "property-local line under the owning occurrence.",
                        "When generation_contract.nested_owned_dependent_scalar_contract is "
                        "non-empty, require each listed dependent scalar as a standalone "
                        "property-local line under the owner occurrence that asserts that "
                        "object-role predicate. Do not confine those scalars to a sibling "
                        "occurrence of the dependent class. Do not publish a closed "
                        "owner-class scalar list that omits a listed nested property.",
                        "For an in-scope object property whose range class is not an "
                        "iteration-owned occurrence and whose domain is not an "
                        "iteration-owned occurrence, emit it once as a standalone "
                        "property-local line immediately after the required heading when "
                        "source-grounded.",
                        "Keep object-role identity tokens as written in the source. Lookup may "
                        "verify or reject a mismatch but must not replace that token.",
                        *_semantic_text_natural_ledger_rules(),
                    ]
                )
            elif target.name == "EXTRACTION_ITER_1.md":
                prompt_specific_must.extend(
                    [
                    "Require the ref-entity-relations.v1 hint schema: top-level `entities` and "
                    "`relations` arrays; each entity uses the literal field names `ref`, `class`, "
                    "`label`, and `datatype_properties` (`class`, never `type`); each relation uses "
                    "the literal field names `subject_ref`, `property`, and `object_ref`.",
                    "The entity label must be the most specific source-supported procedure "
                    "identity. Never instruct the runtime to use the shortest stable "
                    "source-supported identifier, and never make `Class-N [label]` lines "
                    "the only allowed output.",
                    "Require exactly one authoritative JSON object. Do not define a parallel "
                    "`records` output, MAIN wrapper, nested schema-name wrapper, separate duplicate "
                    "hint structure, or legacy `*_label` relationship representation.",
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
                    "instead of inventing unresolved supporting-class or quantity refs.",
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
        return {
            "role": "runtime_prompt_template",
            "must": [
                "Keep the required runtime placeholders so the pipeline can inject source text, "
                "entity context, identifiers, or extracted hints at execution time.",
                "Implement only the current iteration or sub-iteration responsibility "
                "declared by generation_contract.iteration_spec; do not repeat the "
                "ontology-wide task.",
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
                "Be judged as a completed extraction before runtime inputs have been injected.",
                "Invent canonical JSON, fixed section keys, or a serialization syntax that is not "
                "explicitly required by the generation contract's pipeline interchange schema.",
                "Expand an iteration-specific prompt into a full ontology extraction checklist.",
                "Add domain-specific examples, exclusions, or scientific interpretation rules "
                "that are not present in generation_contract.tbox_scope.",
            ],
        }
    return {"role": "artifact_specific", "must": []}


def _prompt_generation_guidance(
    target: Path | None,
    generation_contract: dict[str, Any] | None = None,
) -> str:
    """Return only instructions relevant to the current artifact role."""
    name = target.name if target is not None else ""
    iteration_spec = (
        (generation_contract or {}).get("iteration_spec") or {}
        if isinstance(generation_contract, dict)
        else {}
    )
    semantic_text = (
        str(iteration_spec.get("hint_representation") or "").strip()
        == "semantic-text.v1"
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
            "step splices the iteration T-Box contract after LLM authorship. "
            "Operationalize evidence_accounting_contract.atomicity: each matched "
            "clause-to-sequence member is its own evidence atom; reuse the same "
            "verbatim span when needed and put the member-local cue in ordering_cue. "
            "Do not attach a sequence member as a property of another member."
        )
    if name.startswith("EXTRACTION_ITER_") and name.endswith(".md"):
        if name == "EXTRACTION_ITER_1.md" and not semantic_text:
            return (
                "Identify source-supported pipeline-selected top entities. Require exactly one "
                "ref-entity-relations.v1 JSON object whose entity labels are the most specific "
                "source-supported procedure identity. Copy generation_contract.tbox_scope "
                "comments into the Authoritative T-Box section verbatim; do not distill, "
                "compress, or paraphrase them. Assign sequential refs as `<ClassLocal>-n` and "
                "specify the empty-result JSON object with empty entities and relations arrays. "
                "Do not author the e2e1 line-only `Class-N [label]` contract or tell the "
                "runtime to use the shortest stable identifier. The top-entity runtime may "
                "later normalize JSON records to `<Class>-<n> [<label>]` lines; that is "
                "runtime, not the prompt output contract."
            )
        return (
            "Extract source-grounded semantics allowed by the active T-Box. Follow the exact "
            "interchange selected by generation_contract.iteration_spec.hint_representation and "
            "generation_contract.evidence_accounting_contract: semantic-text.v1 requires only its "
            "natural-language SEMANTIC_HINTS_V1 ledger, while ref-entity-relations.v1 requires only its fixed JSON "
            "object. Never add a parallel or alternative representation. Include every required "
            "runtime input slot. "
            "Do not paste compiled T-Box comment blocks into this artifact. Operationalize "
            "the subclass decision checklist in this file as numbered clause-to-sequence "
            "patterns that require one atom or occurrence per listed member, plus "
            "evidence-rule lines per candidate subclass that keep every T-Box gate "
            "that decides how many atoms a match emits. Do not replace "
            "that checklist with a pointer to the appended companion. A later deterministic "
            "step still splices the full iteration T-Box contract after LLM authorship. "
            "The authored file must contain the exact phrase `current target entity only`. "
            "Render each allowed runtime placeholder exactly once; do not repeat "
            "`{paper_content}`, `{entity_label}`, or `{entity_uri}`. "
            "Keep class creation coverage distinct from any one relationship's linking policy: "
            "when a class created in this iteration is the T-Box range required by downstream "
            "iteration properties, extract every source-grounded target needed downstream even "
            "when some targets must not be linked by a different current-iteration property."
        )
    return "Generate only the artifact described by artifact_role_contract."


def _semantic_text_natural_ledger_rules() -> list[str]:
    """0829/0901-style natural-language SEMANTIC_HINTS_V1 rules.

    Uses T-Box placeholders only. Do not name application ontology classes here.
    """
    return [
        (
            "Begin every occurrence with a short subclass label. When an occurrence is "
            "ordered, emit hasOrder as a standalone property-local line (e.g., hasOrder: 3). "
            "Do not require the heading form `<SubclassLocal> (Order: <n>)`."
        ),
        (
            "Keep object-role identity tokens as written in the source. Do not replace a "
            "source token with a catalog, systematic, or registry name."
        ),
        (
            "Include every source-supported scalar, alias, and description "
            "in the owning occurrence. Do not invent a mandatory nested-child layout or "
            "parenthetical range-local tags."
        ),
        (
            "When an owner occurrence asserts an object-role predicate whose range class "
            "is a same-operation owned dependent, emit every source-supported datatype "
            "scalar of that dependent as a standalone property-local line under the owner "
            "occurrence. Do not leave those scalars only on a sibling occurrence of the "
            "dependent class. Do not publish a closed owner-class scalar list that omits "
            "those properties."
        ),
        (
            "When a lookup returns many parallel aliases, keep the source-attested name "
            "plus at most a few verified identity aliases. Do not paste a catalog or "
            "semicolon-separated synonym dump into the ledger."
        ),
    ]


def _semantic_text_ox_sensitive_ledger_rules() -> list[str]:
    """Backward-compatible alias; the restored contract is 0829/0901 natural language."""
    return _semantic_text_natural_ledger_rules()
