"""Deterministic EXTRACTION / PRE_EXTRACTION scaffolds and compiled companions.

These templates exist so slot files and `*.materializable.inc` exist before
GPT-5 runs. When authoring is enabled, `experiment.py` blanks the `.md`
files so the final prompt text is model-authored.

See pipeline/README.md.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.extraction_prompt_generation.compile.context import (
    AgenticGenerationContext,
)
from src.extraction_prompt_generation.generate.extraction_prompts.contracts import (
    _prompt_tbox_slice,
    _subclass_decision_contract,
    _write_materializable_prompt_component,
)
from src.extraction_prompt_generation.pipeline.formatters import (
    _configured_prompt_addon,
    _format_class_rows,
    _format_linked_target_scalar_contract,
    _format_materializable_hint_contract,
    _format_mutually_exclusive_property_contract,
    _format_property_rows,
    _format_required_links,
    _format_required_step_scoped_object_contract,
    _format_reusable_label_contract,
    _format_value_kind_priority_contract,
    _tbox_comment_fidelity_contract,
    _top_entity_selection_contract,
)
from src.extraction_prompt_generation.pipeline.plan import _iteration_plan
from src.extraction_prompt_generation.pipeline.runtime_support import (
    generate_runtime_support_slice,
)


def _extraction_prompt(context: AgenticGenerationContext) -> str:
    top = context.contract.get("top_entity") or {}
    top_local = str(top.get("class_local") or "Entity").strip() or "Entity"
    return f"""Purpose
- Identify and return only the top-entity occurrences of the T-Box class {top_local} from the provided source text, scoped strictly to this iteration.
- Do not broaden into ontology-wide extraction or emit properties/relations beyond what is required for top-entity identification.

Authoritative T-Box scope for {top_local} (verbatim)
{_top_entity_selection_contract(context)}

Identity-bearing label
- The {top_local} label must be the most specific source-supported procedure identity.
- Preserve an explicit product identifier and any route, method, direction, or transformation qualifier needed to distinguish the workflow from every other procedure in the same document.
- Never use a label that could refer collectively to several procedures.
- Do not invent qualifiers that are absent from the source.
- Never use the shortest stable identifier when a more specific procedure title exists.

Runtime input
- Source text to analyze (appears exactly once below):
{{paper_content}}

Output format
- Return exactly one JSON object with the literal top-level fields entities and relations.
- Each entity must include the literal fields:
  - ref: a stable occurrence-local identifier you assign in first-appearance source order using the class prefix, e.g., "{top_local}-1", "{top_local}-2", ...
  - class: the literal T-Box local name "{top_local}"
  - label: the most specific source-grounded procedure identity per Identity-bearing label rules
  - datatype_properties: an object; for this iteration leave it empty ({{}}). Do not invent properties.
- relations: for this iteration, return an empty array [] (no relations are emitted in the top-entity pass).
- Preserve source order: list entities in the order of their first identifying mention in the text.
- If no valid {top_local} entities are present, return {{"entities":[], "relations":[]}}.
- Do not emit any parallel representation, wrapper objects, or auxiliary ledgers.
- Do not return normalized `Class-N [label]` lines as the only output.

{_tbox_comment_fidelity_contract()}
"""


def _iteration_extraction_prompt(
    context: AgenticGenerationContext, iteration: dict[str, Any]
) -> str:
    responsibilities = iteration.get("responsibilities") or {}
    owned_classes = ", ".join(
        str(value)
        for value in responsibilities.get("classes") or []
        if str(value).strip()
    ) or "none"
    owned_properties = ", ".join(
        str(value)
        for value in responsibilities.get("object_properties") or []
        if str(value).strip()
    ) or "none"
    external_tools = {
        str(name).strip()
        for name in (
            iteration.get("extraction_mcp_tools") or iteration.get("mcp_tools") or []
        )
        if str(name).strip()
    }
    enrichment_profile = (
        iteration.get("external_enrichment_profile")
        if isinstance(iteration.get("external_enrichment_profile"), dict)
        else {}
    )
    allowed_classes = set((context.parsed.get("classes") or {}).keys())
    allowed_properties = set((context.parsed.get("properties") or {}).keys())
    target_class = str(enrichment_profile.get("target_class") or "").strip()
    field_map = {
        str(source): str(target)
        for source, target in (enrichment_profile.get("field_map") or {}).items()
        if str(source).strip()
        and str(target).strip()
        and str(target) in allowed_properties
    }
    enrichment_provider = str(enrichment_profile.get("provider") or "").strip()
    enrichment_lines: list[str] = []
    if (
        enrichment_provider
        and enrichment_provider in external_tools
        and target_class in allowed_classes
        and field_map
    ):
        enrichment_lines = [
            "External enrichment contract:",
            f"- Use `{enrichment_provider}` only for source-supported `{target_class}` identities.",
            "- Preserve the source label and map provider values only to these T-Box fields:",
            *(
                f"  - `{source}` -> `{target}`"
                for source, target in sorted(field_map.items())
            ),
            "- External results may enrich an existing source-supported identity, but never prove participation or create a new source fact.",
        ]
    external_enrichment = "\n".join(enrichment_lines)
    reusable_label_contract = _format_reusable_label_contract(context)
    if str(iteration.get("hint_representation") or "").strip() == "semantic-text.v1":
        from src.extraction_prompt_generation.generate.extraction_prompts.contracts import (
            _semantic_text_natural_ledger_rules,
        )

        ledger_rules = "\n".join(
            f"- {rule}" for rule in _semantic_text_natural_ledger_rules()
        )
        return f"""RUNTIME INPUTS (bound once; refer back without repeating placeholders)
- Source paper content: {{paper_content}}
- Target entity label: {{entity_label}}
- Target entity URI: {{entity_uri}}

SCOPE OF THIS PROMPT (iteration {iteration.get("iteration_number")} only)
- Name: {iteration.get("name") or ""}
- Description: {iteration.get("description") or ""}
- Extract ordered in-scope subclass occurrences and their in-scope properties for exactly these classes: {owned_classes}.
- Object properties in scope: {owned_properties}.
- Do not broaden beyond this iteration-owned surface. Do not invent additional classes or properties.

OUTPUT POLICY
- Begin your answer with the exact header: SEMANTIC_HINTS_V1
- Return a natural-language semantic ledger only. No JSON, RDF, IRIs, tables, summaries, truncation, or ellipses.
{ledger_rules}
- For datatype scalars listed in this iteration's semantic_scalar_output_contract, emit each as a standalone property-local line (e.g., hasOrder: 3); do not rely on prose for scalars.
- Separate occurrences with a single blank line. Preserve source order. Do not silently omit any in-scope occurrence.
- Extract hints for the current target entity only.
- Prefer source spans whose headings, labels, or local context match the current target entity label.
- Do not copy fields from another top entity when the current target entity has distinct source evidence.
- Leave entity identity resolution and graph construction to the KG-building agent.
- Treat accumulated prior hints as read-only context, not a completeness mask for this iteration.

ORDERING REQUIREMENTS
- Each emitted ordered occurrence must include hasOrder as a contiguous integer starting at 1 and strictly increasing by 1 with no gaps; follow the procedure’s source order.
- Do not encode order only as `<SubclassLocal> (Order: <n>)`.

{external_enrichment}
{reusable_label_contract}

{_tbox_comment_fidelity_contract()}
"""
    return f"""# Extraction Prompt: {context.ontology.name} Iteration {iteration.get("iteration_number")}

Task:
Extract source-supported hints for this iteration only.

Iteration Scope:
- Name: {iteration.get("name") or ""}
- Description: {iteration.get("description") or ""}
- Stage-owned classes: {owned_classes}
- Stage-owned object properties: {owned_properties}

Current Target Entity:
- Label: {{entity_label}}
- IRI: {{entity_uri}}

Rules:
- Use only the source text and the ontology-derived class/property context below.
- Treat every T-Box `comment=` value below as a binding extraction rule for the corresponding class or property.
- Extract hints for the current target entity only.
- Treat the pipeline-injected accumulated prior hints as a read-only semantic identity registry.
- Every semantic entity must have one stable `ref`. Reuse the exact prior `ref`, class, and canonical label when a new stage-owned relation references an entity already present in accumulated prior hints.
- Do not re-emit a prior semantic entity under a new `ref` or altered label, and do not repeat a prior relation.
- Emit entity records only for stage-owned classes, except for the minimal Current Target Entity identity record required to attach new stage-owned relations.
- If a source fact is already represented in accumulated prior hints, omit it from this iteration rather than restating it.
- Prior existence never satisfies a new stage-owned property by itself. For every stage-owned property, compare the source with the accumulated hints and emit each source-supported property that is not already represented.
- When a new stage-owned property links two prior entities, emit only the relation record and reuse both exact prior refs. A ref-only relation is not a duplicate entity.
- Do not emit classes described as reserved, future, optional placeholders, or execution anchors unless the source explicitly contains an instance required by this iteration.
- Prefer source spans whose headings, labels, or local context match the current target entity label.
- Do not copy fields from another top entity when the current target entity has distinct source evidence.
- Before emitting JSON, find the class section whose `label` equals the Current Target Entity label; for that section, scan the Materializable Hint Contract and include every source-supported datatype field accepted by that class. Identifier values in lists or tables paired with the target label are source-supported evidence.
- Emit only fields that are both source-supported and listed in the Materializable Hint Contract below.
- Omit missing, uncertain, or unsupported fields.
- Treat structured source labels, table headers, bullet labels, and nearby section labels as strong evidence for matching datatype fields when their normalized words match the field local name, CSV-style display label, or T-Box comment wording.
- For explicit structured source sections, tables, or bullet lists, evaluate every listed item before returning JSON; do not stop after the first matching field in the section.
- If a T-Box comment contains an explicit convention, positive example, negative example, priority instruction, exception, or "do not" rule, apply that instruction literally and before using general keyword matching.
- If a property comment says a field is not supported by a source phrase, requires a more specific source condition, or prefers a sibling/canonical field for that phrase, follow that comment even when the phrase contains words that otherwise resemble the field name.
- If a property comment defines normalization examples or says which modifiers to keep or remove, emit the normalized value required by the comment rather than preserving the full source phrase.
- If a class exposes a catch-all, other, note, or free-text datatype field and its T-Box comment says to use it for explicit items not covered by canonical fields, collect all source-listed unmatched items for that class in that field.
- If the T-Box marks datatype fields with `value_kind=binary_checklist`, evaluate every such field for the relevant class against the source before returning JSON; binary/canonical checklist fields have higher priority than free-text or catch-all fallback fields for the same fact.
- If the T-Box marks datatype fields with `value_kind=free_text_fallback`, use them only for explicit source items that no binary/canonical sibling field covers; never use a fallback to replace or hide a source-supported binary field.
- When a linked class has only binary checklist fields supported by the source, still emit its entity record and the corresponding relation record; do not drop it because no free-text field was filled.
- For identification or demographic fields, inspect source header blocks and nearby header tables before the main body when the class or property comments say those fields come from identifying or administrative source regions.
- For short acronym-like datatype fields, if the exact acronym/token appears in source text in a relevant diagnosis, indication, observation, or field-value context and the T-Box comment says that token activates the field, emit the configured active checklist value for that field.
- If source evidence supports any datatype field for a class linked from the current top entity, emit both its entity record and the relation record from the top entity. After emitting one datatype field, scan the same structured source region for all other accepted fields of that class.
- Use exact class local names in each entity record's `class` field and exact property local names in each relation record's `property` field.
- Each entity record must separate `ref`, `class`, canonical `label`, and `datatype_properties`. Datatype values belong only inside `datatype_properties`.
- Emit a class section only when the current target label or a source-supported linked target actually denotes an instance of that class; do not coerce one semantic category into another merely because both classes are available.
- For ordered-member classes, ordering fields listed in the Materializable Hint Contract must be unique positive integers starting at 1. Do not emit duplicate, skipped, decimal, fractional, zero, negative, or between-step order values such as 1.5; renumber the ordered members as consecutive integers in source order.
- Do not emit the same source operation, label, or ordering value as two different ordered-member class sections; choose the single most specific class supported by the T-Box class comments.
- Do not emit a generic ordered-member parent class as a placeholder when source evidence supports one of its specific subclasses in the Materializable Hint Contract; emit the specific subclass section and its supported fields instead.
- If the T-Box declares an ordered-member class whose comment says it introduces a linked target object, emit one ordered member per source-supported linked target in source order.
- Labels are identity names only. Never append amounts, concentrations, durations, temperatures, roles, or other scalar/context payload to `label`; put those values in `datatype_properties`.
- Relations must use `subject_ref`, `property`, and `object_ref`; never use a label as an object reference. If the linked target is already present in prior hints, reuse its exact ref.
- For every entity class, never invent a scoped label such as `<source label> for <Current Target Entity>` or `<source label> (<Current Target Entity>)` unless that complete phrase is explicitly the entity's own name in the source. Contextual participation must be represented by links, not label text.
- A `ref` identifies exactly one semantic entity occurrence and must not be shared by different class instances. Repeated occurrences of a non-reusable class require different refs even when their canonical labels match.
- For ordered-member relations whose target has scalar fields, point to the same target ref whose entity record carries those scalar fields; never mint a second ref merely to transport values.
- For every required ordered-member object link declared below, emit a relation record when the source identifies a target object present in current or prior hints.
- For multiple linked targets, emit one relation record per target ref and one entity record per newly introduced target.
- If a linked target label also explicitly supplies a value accepted by a target scalar field, preserve that source-supported value in the scalar field.
- Follow object-property comments when they require source-supported links to target objects; do not omit a required link merely because another scalar field was filled.
- If a general parameter string contains a scalar value that has a dedicated generated field in the Materializable Hint Contract, also emit the dedicated field instead of leaving the scalar only inside the parameter string.
- For classes listed in the Mutually Exclusive Property Contract, emit at most one active property from each group for one entity instance. Use source evidence and T-Box comments to choose the best-supported one; omit the rest.
- When a T-Box comment distinguishes final, confirmed, provisional, preliminary, intermediate, or subordinate evidence, follow that evidence priority exactly; do not promote provisional or intermediate source statements into final/confirmed fields unless the source explicitly makes them final or confirmed.
- If the T-Box exposes a procedure-inheritance object field and the source says the current procedure follows the same, similar, or previous conditions as another source-supported procedure, treat the referenced procedure text as source-supported context for this target: carry over its ordered members and linked targets, then apply only the explicit modifications stated for the current target.
- Existing refs are not source evidence for adding new relations; emit a relation only when the Source text explicitly supports it.
- If evidence belongs to a more specific class whose generated tool lists the relevant field, emit that specific class instead of a generic parent/container class.
- Never emit schema placeholders or routing fields; use only the class sections and field names listed below.
- Output only a compact JSON object, with no markdown fences or explanatory prose.

{external_enrichment}
Reusable Entity Label Contract:
{reusable_label_contract}

Materializable Hint Contract:
{_format_materializable_hint_contract(context)}

{_format_value_kind_priority_contract(context)}

Linked Target Scalar Contract:
{_format_linked_target_scalar_contract(context)}

Required Ordered-Member Object-Link Contract:
{_format_required_step_scoped_object_contract(context)}

{_format_mutually_exclusive_property_contract(context)}
{_tbox_comment_fidelity_contract()}

Expected JSON Shape:
Hint Schema: ref-entity-relations.v1
- Return exactly one JSON object with top-level arrays `entities` and `relations`.
- Every entity is `{{"ref": "...", "class": "<exact class local>", "label": "<canonical identity label>", "datatype_properties": {{...}}}}`.
- Every relation is `{{"subject_ref": "...", "property": "<exact object-property local>", "object_ref": "..."}}`.
- Every ref used by a relation must resolve to exactly one entity in current or accumulated prior hints.
- Never encode a datatype value in `ref`, `label`, `subject_ref`, or `object_ref`.

Classes and Properties:
{_format_class_rows(context)}

Datatype Properties:
{_format_property_rows(context, kind="datatype")}

Object Properties:
{_format_property_rows(context, kind="object")}

{_configured_prompt_addon(context)}
Source:
{{paper_content}}
"""


def _pre_extraction_prompt(
    context: AgenticGenerationContext, iteration: dict[str, Any]
) -> str:
    tbox_scope = _prompt_tbox_slice(context, iteration)
    subclass_contract = _subclass_decision_contract(tbox_scope)
    return f"""# Pre-Extraction Prompt: {context.ontology.name} Iteration {iteration.get("iteration_number")}

Task:
Build a closed ledger of the shortest source spans relevant to this iteration scope and classify
each operation only under the supplied T-Box-derived scope.

Iteration Scope:
- Name: {iteration.get("name") or ""}
- Description: {iteration.get("description") or ""}

Rules:
- Treat every class/property comment and integrity annotation in T-Box-Derived Scope as binding.
- For each in-scope operation atom, apply every relevant Subclass Decision Checklist decision
  point before choosing one most-specific candidate class.
- A T-Box comment containing `【Warning】` marks a high-risk choice boundary. Before selecting
  or excluding a governed candidate, compare the source against the complete marked comment
  and all applicable warning-marked alternatives. The marker changes attention only and adds
  no domain semantics beyond those comments.
- Record one explicit disposition for every operation atom. If no candidate reaches its
  T-Box-derived evidence threshold, mark the atom unresolved instead of omitting it.
- Do not invent domain rules, triggers, examples, or exceptions outside T-Box-Derived Scope.
- Preserve source order and keep distinct source operations distinct.

T-Box-Derived Scope:
{json.dumps(tbox_scope, indent=2, ensure_ascii=False)}

Subclass Decision Checklist:
{json.dumps(subclass_contract, indent=2, ensure_ascii=False)}

Return only the closed evidence ledger, with verbatim source spans and nearby headings.

Source:
{{paper_content}}
"""


def _sub_iteration_extraction_prompt(
    context: AgenticGenerationContext,
    iteration: dict[str, Any],
    sub_iteration: dict[str, Any],
) -> str:
    reusable_label_contract = _format_reusable_label_contract(context)
    return f"""# Enrichment Extraction Prompt: {context.ontology.name} Iteration {sub_iteration.get("iteration_number")}

Task:
Refine or enrich the existing extraction hints for this sub-iteration only.

Parent Iteration:
- Name: {iteration.get("name") or ""}
- Description: {iteration.get("description") or ""}

Sub-Iteration Scope:
- Name: {sub_iteration.get("name") or ""}
- Description: {sub_iteration.get("description") or ""}

Current Target Entity:
- Label: {{entity_label}}
- IRI: {{entity_uri}}

Rules:
- Use only the provided source text and existing hints.
- Treat every T-Box `comment=` value below as a binding extraction rule for the corresponding class or property.
- Preserve the `ref`, class, and canonical label of every entity already present in the existing hints. Enrichment must update the matching ref, never create a renamed duplicate.
- Preserve exact class and field names from the Materializable Hint Contract below.
- Omit unsupported additions and any key that is not listed for its class.
- Treat structured source labels, table headers, bullet labels, and nearby section labels as strong evidence for matching datatype fields when their normalized words match the field local name, CSV-style display label, or T-Box comment wording.
- For explicit structured source sections, tables, or bullet lists, evaluate every listed item before returning JSON; do not stop after the first matching field in the section.
- If a T-Box comment contains an explicit convention, positive example, negative example, priority instruction, exception, or "do not" rule, apply that instruction literally and before using general keyword matching.
- If a property comment says a field is not supported by a source phrase, requires a more specific source condition, or prefers a sibling/canonical field for that phrase, follow that comment even when the phrase contains words that otherwise resemble the field name.
- If a property comment defines normalization examples or says which modifiers to keep or remove, emit the normalized value required by the comment rather than preserving the full source phrase.
- If a class exposes a catch-all, other, note, or free-text datatype field and its T-Box comment says to use it for explicit items not covered by canonical fields, collect all source-listed unmatched items for that class in that field.
- If the T-Box marks datatype fields with `value_kind=binary_checklist`, evaluate every such field for the relevant class against the source before returning JSON; binary/canonical checklist fields have higher priority than free-text or catch-all fallback fields for the same fact.
- If the T-Box marks datatype fields with `value_kind=free_text_fallback`, use them only for explicit source items that no binary/canonical sibling field covers; never use a fallback to replace or hide a source-supported binary field.
- When a linked class has only binary checklist fields supported by the source, still emit its entity record and the corresponding relation record; do not drop it because no free-text field was filled.
- For identification or demographic fields, inspect source header blocks and nearby header tables before the main body when the class or property comments say those fields come from identifying or administrative source regions.
- For short acronym-like datatype fields, if the exact acronym/token appears in source text in a relevant diagnosis, indication, observation, or field-value context and the T-Box comment says that token activates the field, emit the configured active checklist value for that field.
- If source evidence supports datatype fields for a linked class, emit or update its entity record and emit the relation by refs. Scan the same source region for all accepted datatype fields before returning JSON.
- Emit a class section only when the current target label, an existing hinted member, or a source-supported linked target actually denotes an instance of that class; do not coerce one semantic category into another merely because both classes are available.
- For ordered-member classes, ordering fields listed in the Materializable Hint Contract must be unique positive integers starting at 1. Do not emit duplicate, skipped, decimal, fractional, zero, negative, or between-step order values such as 1.5; renumber the ordered members as consecutive integers in source order.
- Do not emit the same source operation, label, or ordering value as two different ordered-member class sections; choose the single most specific class supported by the T-Box class comments.
- Do not emit a generic ordered-member parent class as a placeholder when source evidence supports one of its specific subclasses in the Materializable Hint Contract; emit the specific subclass section and its supported fields instead.
- If the T-Box declares an ordered-member class whose comment says it introduces a linked target object, emit one ordered member per source-supported linked target in source order.
- Labels are identity names only. Put all scalar or role payload in `datatype_properties`, never in labels or refs.
- Relations must use `subject_ref`, exact property local name, and `object_ref`. Reuse exact existing refs.
- For every entity class, never invent a scoped label such as `<source label> for <Current Target Entity>` or `<source label> (<Current Target Entity>)` unless that complete phrase is explicitly the entity's own name in the source. Contextual participation must be represented by links, not label text.
- One ref identifies one semantic entity occurrence. Distinct non-reusable occurrences require distinct refs even when their canonical labels match.
- For ordered-member relations, link to the same target ref whose entity record carries the scalar fields.
- Emit one relation record per source-supported target ref; do not collapse multiple targets into one label string.
- If a linked target label also explicitly supplies a value accepted by a target scalar field, preserve that source-supported value in the scalar field.
- Follow object-property comments when they require source-supported links to target objects; do not omit a required link merely because another scalar field was filled.
- If a general parameter string contains a scalar value that has a dedicated generated field in the Materializable Hint Contract, also emit the dedicated field instead of leaving the scalar only inside the parameter string.
- For classes listed in the Mutually Exclusive Property Contract, emit at most one active property from each group for one entity instance. Use source evidence and T-Box comments to choose the best-supported one; omit the rest.
- When a T-Box comment distinguishes final, confirmed, provisional, preliminary, intermediate, or subordinate evidence, follow that evidence priority exactly; do not promote provisional or intermediate source statements into final/confirmed fields unless the source explicitly makes them final or confirmed.
- If the T-Box exposes a procedure-inheritance object field and the source says the current procedure follows the same, similar, or previous conditions as another source-supported procedure, treat the referenced procedure text as source-supported context for this target: carry over its ordered members and linked targets, then apply only the explicit modifications stated for the current target.
- Existing refs are not source evidence for adding new relations; only source-supported relations may be emitted.
- If enrichment evidence belongs to a more specific class whose generated tool lists the relevant field, emit that specific class instead of a generic parent/container class.
- Return only a compact JSON object, with no markdown fences, bullets, checklists, or explanatory prose.
- The JSON must be directly mergeable into the existing `entities` and `relations` arrays.
- To enrich an existing entity, repeat its exact `ref`, `class`, and `label`, then add only supported `datatype_properties`.
- For source-supported links shared by ordered members, emit one relation per affected `subject_ref`.

Reusable Entity Label Contract:
{reusable_label_contract}

Materializable Hint Contract:
{_format_materializable_hint_contract(context)}

{_format_value_kind_priority_contract(context)}

Linked Target Scalar Contract:
{_format_linked_target_scalar_contract(context)}

Required Ordered-Member Object-Link Contract:
{_format_required_step_scoped_object_contract(context)}

{_format_mutually_exclusive_property_contract(context)}
{_tbox_comment_fidelity_contract()}

Expected JSON Shape:
Hint Schema: ref-entity-relations.v1
- Top-level arrays are `entities` and `relations`.
- Entity records contain exact `ref`, `class`, canonical `label`, and `datatype_properties`.
- Relation records contain exact `subject_ref`, `property`, and `object_ref`.

Classes and Properties:
{_format_class_rows(context)}

Datatype Properties:
{_format_property_rows(context, kind="datatype")}

Object Properties:
{_format_property_rows(context, kind="object")}

{_configured_prompt_addon(context)}
Source and Existing Hints:
{{paper_content}}
"""


def generate_deterministic_prompt_slice(context: AgenticGenerationContext) -> list[str]:
    """Write EXTRACTION / PRE_EXTRACTION slots, SPARQL support, and `.inc` files."""
    prompts_dir = Path(context.prompts_dir)
    prompts_dir.mkdir(parents=True, exist_ok=True)
    files = (
        {}
        if context.ontology.role == "extension"
        else {
            "EXTRACTION_ITER_1.md": _extraction_prompt(context),
        }
    )
    iterations = _iteration_plan(context)
    for iteration in iterations.get("iterations") or []:
        iter_num = iteration.get("iteration_number")
        files[f"EXTRACTION_ITER_{iter_num}.md"] = _iteration_extraction_prompt(
            context, iteration
        )
        if iteration.get("has_pre_extraction"):
            files[f"PRE_EXTRACTION_ITER_{iter_num}.md"] = _pre_extraction_prompt(
                context, iteration
            )
        for sub_iteration in iteration.get("sub_iterations") or []:
            if not isinstance(sub_iteration, dict):
                continue
            sub_num = str(sub_iteration.get("iteration_number") or "").replace(".", "_")
            if sub_num:
                files[f"EXTRACTION_ITER_{sub_num}.md"] = (
                    _sub_iteration_extraction_prompt(
                        context,
                        iteration,
                        sub_iteration,
                    )
                )
    written: list[str] = []
    for name, content in files.items():
        path = prompts_dir / name
        path.write_text(content, encoding="utf-8")
        written.append(str(path))
    written.extend(generate_runtime_support_slice(context, iterations=iterations))
    for name in files:
        path = prompts_dir / name
        component = _write_materializable_prompt_component(context, path)
        if component is not None:
            written.append(str(component))
    return written
