"""LLM generation task prompt for one artifact.

Builds the JSON exact-edits task: role, contract, guidance, and focused
validation. See generate/README.md.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.extraction_prompt_generation.compile.context import (
    AgenticGenerationContext,
)
from src.extraction_prompt_generation.generate.authoring_progress import (
    _focused_validation_projection,
)
from src.extraction_prompt_generation.generate.extraction_prompts.contracts import (
    _extension_meta_prompt_policy,
    _is_enrichment_iteration_spec,
    _prompt_artifact_generation_contract,
    _prompt_generation_guidance,
    _prompt_role_contract,
)


def _generation_task(
    *,
    context: AgenticGenerationContext,
    report: dict[str, Any],
    round_index: int,
    generate_prompts: bool,
    target: Path | None = None,
) -> str:
    """Build the exact-edits task JSON for one prompt artifact.

    This is the only generation meta-prompt. CLI flags cannot select another
    authoring path or a different instruction template.
    """
    is_prompt = target is not None and target.suffix == ".md"
    generation_contract = (
        _prompt_artifact_generation_contract(context, target)
        if target is not None
        else {}
    )
    task = {
        "round": round_index,
        "ontology": {
            "name": context.ontology.name,
            "role": context.ontology.role,
            "ttl_file": context.ontology.ttl_file,
        },
        "generation_contract": generation_contract,
        "fixed_runtime_api": {},
        "artifact_role_contract": _prompt_role_contract(target, generation_contract),
        "owned_entity_tool_contracts": {},
        "read_only_upstream_artifacts": {},
        "machine_validation": _focused_validation_projection(report, None),
        "requested_artifacts": {
            "scripts": False,
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
        "needed to produce robust final prompts. Decide which editable files need "
        "changes yourself; do not rely on filename keyword routing. Preserve correct behavior. "
        "Use only T-Box and contract knowledge, never fixture-specific entities or values. "
        "Runtime prompts must preserve required data placeholders; those placeholders are "
        "intentional bindings, not TODO/template residue. A prompt's scope must exactly match "
        "generation_contract.iteration_spec. "
        "The prompt must not broaden into the ontology-wide task. "
        + (
            "For EXTRACTION_ITER_1.md, require exactly one JSON object with entities and "
            "relations. Copy generation_contract.tbox_scope comments into the Authoritative "
            "T-Box section character-for-character; do not distill, compress, or paraphrase "
            "them. Assign sequential occurrence refs as `<ClassLocal>-1`, `<ClassLocal>-2`, "
            "... and name the empty-result JSON object whose entities and relations arrays "
            "are empty. Never author `# Extraction Prompt:` or the e2e1 shortest-identifier "
            "line-only contract. "
            if (
                is_prompt
                and target is not None
                and target.name == "EXTRACTION_ITER_1.md"
                and not is_semantic_text_prompt
            )
            else ""
        )
        + "For EXTRACTION_ITER_2 and later, the authored file must contain the exact "
        "phrase `current target entity only`. Tell the extractor to prefer source "
        "spans matching that entity label, and not copy fields from another top entity "
        "when the current target has distinct source evidence. Identity-bearing "
        "owned occurrences for this iteration (the procedure product and the "
        "in-document procedure heading or anchor) must use the bound target-entity "
        "label from the runtime slots; do not bind a sibling procedure's heading or "
        "product identifier. "
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
            + (
                "Do not paste generation_contract.subclass_comment_projection or compiled "
                "T-Box comment blocks into this LLM-authored file. Operationalize "
                "subclass_decision_contract into a working checklist in this file: "
                "numbered clause-to-sequence patterns, each stating that every listed "
                "member is a separate atom or occurrence and must not be collapsed into "
                "another member's properties, plus evidence-rule lines per candidate "
                "subclass that keep every T-Box gate that decides how many atoms a "
                "match emits (split, merge, reuse-same-span, do-not-consume-other-members, "
                "multi-value-on-one-occurrence). Do not replace those atomization or "
                "cardinality gates with a single type-identity line. Do not replace that "
                "checklist with a pointer to the appended companion. The deterministic "
                "companion still splices the full iteration contract after authorship. "
                if is_semantic_text_prompt
                else "Project generation_contract.subclass_comment_projection completely into "
                "that checklist, including every subclass comment. "
            )
            + "Integrate generation_contract.lexical_quantity_hint_contract exactly: "
            + (
                "preserve each listed explicit source quantity as a complete exact lexeme "
                "on a standalone `<predicate_local>: <lexeme>` line under the owning "
                "occurrence, for downstream materialization. Prose must not be the sole "
                "carrier. Never tell "
                "the runtime to omit a listed quantity because its target class or object ref "
                "is absent, and do not reinterpret this interchange as T-Box datatype semantics "
                "or as JSON datatype_properties. "
                "For every entry in generation_contract.semantic_scalar_output_contract, "
                "require that same standalone property-local line form. "
                "When generation_contract.nested_owned_dependent_scalar_contract is "
                "non-empty, require each listed dependent scalar as a standalone "
                "property-local line under the owner occurrence that asserts that "
                "object-role predicate. Do not confine those scalars to a sibling "
                "occurrence of the dependent class. Do not publish a closed "
                "owner-class scalar list that omits a listed nested property. "
                "For an in-scope object property whose range class is not an "
                "iteration-owned occurrence and whose domain is not an iteration-owned "
                "occurrence, emit it once as a standalone property-local line immediately "
                "after the required heading when source-grounded; do not place it inside "
                "an occurrence. "
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
            "prompt's execution protocol, including its atomicity rule that a "
            "clause-to-sequence member is an owned operation and not a context fact. "
            "PRE extraction must complete its target-first "
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
            "This is a simple_extension extraction goal. Paper content and the T-Box are supplied "
            "by separate runtime wrapper channels, so do not emit placeholders for either. Use "
            "only extension_runtime_prompt_policy.canonical_runtime_slots and no value from "
            "extension_runtime_prompt_policy.forbidden_runtime_slots. The entity slots identify "
            "the inherited upstream scope, not an extension-focus instance. Ask for one or more "
            "extension-focus instances relevant to that scope and never retype or reinterpret "
            "the inherited entity as the extension focus. "
            if is_extension_prompt
            else ""
        )
        + "You are generating the artifact through a plain LLM call and must not request tools "
        "while producing the edit payload. This restriction is meta-level only and must never "
        "appear in generated artifact content.\n\n"
        + _prompt_generation_guidance(target, generation_contract)
        + "\n\n"
        + json.dumps(task, ensure_ascii=False)
    )
    return prompt
