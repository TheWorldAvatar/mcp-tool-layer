"""Prompt binding and output-boundary injection for main extraction."""

from __future__ import annotations

import json
from typing import Any

from src.extraction_runtime.llm import strip_code_fences

from models.MCPConfig import load_mcp_set_extraction_validation
from src.extraction_runtime.steps.main_extraction.inheritance import (
    BRIEF_BEGIN as PROCEDURE_INHERITANCE_BRIEF_BEGIN,
)

REF_ENTITY_REPRESENTATION = "ref-entity-relations.v1"
SEMANTIC_HINT_REPRESENTATION = "semantic-text.v1"
LOOKUP_ALIAS_RULE_BEGIN = "---- PIPELINE-INJECTED LOOKUP ALIAS RULE: BEGIN ----"
LOOKUP_ALIAS_RULE_END = "---- PIPELINE-INJECTED LOOKUP ALIAS RULE: END ----"
LOOKUP_ALIAS_RULE = (
    f"{LOOKUP_ALIAS_RULE_BEGIN}\n"
    "Do not copy a lookup catalog, synonym dump, or semicolon-separated alias list "
    "into hints, properties, or later tool arguments.\n"
    "For any chemical or species name field, keep the source-attested name plus at most "
    "a few verified identity aliases (one common name, one abbreviation, one systematic "
    "name). Drop product, brand, trade, catalog, purity, and registry-id strings.\n"
    "Never repeat an alias list. If a lookup returns many names, record identity only; "
    "do not paste the payload.\n"
    "This rule overrides any earlier instruction to include a complete lookup synonym list.\n"
    f"{LOOKUP_ALIAS_RULE_END}"
)


def inject_lookup_alias_rule(prompt: str) -> str:
    if LOOKUP_ALIAS_RULE_BEGIN in prompt:
        return prompt
    return prompt.rstrip() + "\n\n" + LOOKUP_ALIAS_RULE + "\n"


def bind_runtime_context(
    prompt_template: str,
    *,
    doi_hash: str = "",
    entity_label: str,
    entity_uri: str,
    source_text: str,
    iteration_input: str = "",
    accumulated_hints: str = "",
    identity_dossier: dict | None = None,
) -> str:
    declared_doi = "{doi}" in prompt_template or "{hash}" in prompt_template
    declared_label = "{entity_label}" in prompt_template
    declared_uri = "{entity_uri}" in prompt_template
    declared_dossier = "{entity_identity_dossier}" in prompt_template
    dossier_text = json.dumps(
        identity_dossier or {},
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    prompt = prompt_template.replace("{doi}", doi_hash).replace("{hash}", doi_hash)
    prompt = prompt.replace("{entity_label}", entity_label)
    prompt = prompt.replace("{entity_uri}", entity_uri)
    prompt = prompt.replace("{entity_identity_dossier}", dossier_text)
    declared_source = "{paper_content}" in prompt or "{context}" in prompt
    prompt = prompt.replace("{paper_content}", source_text).replace("{context}", source_text)
    declared_iteration_input = "{iteration_input}" in prompt
    prompt = prompt.replace("{iteration_input}", iteration_input)
    declared_accumulated_hints = "{accumulated_hints}" in prompt
    prompt = prompt.replace("{accumulated_hints}", accumulated_hints)

    additions: list[str] = []
    missing_identity: list[str] = []
    if doi_hash and not declared_doi:
        missing_identity.append(f"Document DOI/hash: {doi_hash}")
    if not declared_label:
        missing_identity.append(f"Current entity label: {entity_label}")
    if not declared_uri:
        missing_identity.append(f"Current entity exact URI: {entity_uri}")
    if missing_identity:
        additions.extend(
            [
                "---- PIPELINE-INJECTED ENTITY RUNTIME CONTEXT: BEGIN ----",
                *missing_identity,
                "---- PIPELINE-INJECTED ENTITY RUNTIME CONTEXT: END ----",
            ]
        )
    if identity_dossier and not declared_dossier:
        additions.extend(
            [
                "---- PIPELINE-INJECTED ENTITY IDENTITY DOSSIER: BEGIN ----",
                "This dossier is the authoritative identity scope for the current entity.",
                "Use only its explicit fields and facts; do not infer missing identity facts.",
                "Do not substitute, merge, or redirect the current entity to another top-entity scope.",
                dossier_text,
                "---- PIPELINE-INJECTED ENTITY IDENTITY DOSSIER: END ----",
            ]
        )
    if not declared_source:
        additions.extend(
            [
                "---- PIPELINE-INJECTED SOURCE TEXT: BEGIN ----",
                source_text,
                "---- PIPELINE-INJECTED SOURCE TEXT: END ----",
            ]
        )
    if iteration_input and not declared_iteration_input:
        additions.extend(
            [
                "---- PIPELINE-INJECTED ITERATION INPUT: BEGIN ----",
                iteration_input,
                "---- PIPELINE-INJECTED ITERATION INPUT: END ----",
            ]
        )
    if accumulated_hints and not declared_accumulated_hints:
        additions.extend(
            [
                "---- PIPELINE-INJECTED ACCUMULATED PRIOR HINTS: BEGIN ----",
                "The following prior hints are a read-only semantic identity registry.",
                "Do not re-emit an entity or top-entity link already represented here.",
                "When the current iteration must reference an existing entity, reuse its exact class and label.",
                "Emit only new stage-owned entities, new stage-owned links, or source-supported fields that the prior hints do not already contain.",
                "The current target entity may be repeated only as a minimal shell needed to attach new stage-owned links.",
                accumulated_hints,
                "---- PIPELINE-INJECTED ACCUMULATED PRIOR HINTS: END ----",
            ]
        )
    bound = prompt.rstrip() + ("\n\n" + "\n".join(additions) + "\n" if additions else "")
    return inject_lookup_alias_rule(bound)


def inject_procedure_inheritance_brief(prompt: str, brief: str) -> str:
    normalized = str(brief or "").strip()
    if not normalized or PROCEDURE_INHERITANCE_BRIEF_BEGIN in prompt:
        return prompt
    return prompt.rstrip() + "\n\n" + normalized + "\n"


def append_closed_ledger_output_boundary(prompt: str, brief: str = "") -> str:
    inheritance_requirement = (
        "Respect same-as / following / analogously inheritance in the injected "
        "context. Cover inherited operations the target did not explicitly change. "
        "The effective_workflow is a starting map, not a license to drop unmentioned "
        "inherited operations."
        if str(brief or "").strip()
        else "Derive complete operation coverage from the target source and active T-Box."
    )
    return (
        prompt.rstrip()
        + "\n\n---- PIPELINE-INJECTED CLOSED-LEDGER OUTPUT BOUNDARY: BEGIN ----\n"
        + inheritance_requirement
        + "\nReturn exactly one JSON object and no prose or Markdown fence. It must contain "
        "a `scope_resolution` object with `completion_attestation`, and an `evidence` array. "
        "The completion_attestation must set target_located, all_references_resolved, "
        "all_modifications_applied, and effective_workflow_complete to true only after the "
        "complete ledger has been built. Each evidence row must use sequential E### "
        "evidence_id and one-based source_order and must contain a non-empty verbatim_quote, "
        "a non-empty candidate_types array, and a candidate_properties object. Preserve "
        "identity, amount, role, and operation-local qualifiers on the same semantic "
        "occurrence. Do not include hasOrder in candidate_properties.\n"
        "---- PIPELINE-INJECTED CLOSED-LEDGER OUTPUT BOUNDARY: END ----\n"
    )


def append_ref_entity_output_boundary(prompt: str) -> str:
    return (
        prompt.rstrip()
        + "\n\n---- PIPELINE-INJECTED FINAL OUTPUT BOUNDARY: BEGIN ----\n"
        "The prior-hints and identity-dossier blocks above are read-only inputs. "
        "Do not quote, copy, wrap, or re-emit those blocks.\n"
        "Return only one JSON object for the current iteration delta. It must have "
        "exactly two top-level arrays named `entities` and `relations` and must contain "
        "no heading, commentary, Markdown fence, or second JSON document.\n"
        "---- PIPELINE-INJECTED FINAL OUTPUT BOUNDARY: END ----\n"
    )


def append_semantic_hint_output_boundary(prompt: str) -> str:
    return (
        prompt.rstrip()
        + "\n\n---- PIPELINE-INJECTED SEMANTIC OUTPUT BOUNDARY: BEGIN ----\n"
        "Return a concise natural-language semantic ledger headed exactly "
        "`SEMANTIC_HINTS_V1`. Do not output JSON, RDF, entity refs, IRIs, or tool calls. "
        "Describe every source-supported, iteration-owned occurrence and relation in source "
        "order, preserving exact lexical values and every occurrence boundary required by the "
        "active T-Box comments and integrity annotations already supplied in the prompt. "
        "Do not invent missing values, domain rules, or placeholder entities. "
        "A tool result that reports no match, ok=false, matched=false, or empty content "
        "is unresolved. Copy lookup values only when the tool actually returned them; "
        "do not invent lookup values to fill a miss.\n"
        "---- PIPELINE-INJECTED SEMANTIC OUTPUT BOUNDARY: END ----\n"
    )


def required_executed_tool_groups(validation: dict[str, Any] | None) -> list[dict[str, Any]]:
    groups = (validation or {}).get("required_executed_tool_groups") or []
    return [item for item in groups if isinstance(item, dict)]


def format_required_tool_contract_block(groups: list[dict[str, Any]]) -> str:
    if not groups:
        return ""
    lines = [
        "## Required MCP Tool Contract",
        (
            "Before returning the final extraction output for this attempt, you MUST "
            "execute the following MCP tool activity with the tools available in this run."
        ),
        (
            "Do not return final extraction output until every required group below has been "
            "satisfied for every applicable entity occurrence by at least one completed tool "
            "call in this attempt whose arguments identify that entity. A call for one entity "
            "does not satisfy the requirement for another entity unless the tool explicitly "
            "accepts a batch and returns separately attributable results for every entity. "
            "A completed call that returns no match, ok=false, or empty content still "
            "satisfies the call requirement; treat that lookup as unresolved."
        ),
        "",
        "Required tool groups:",
    ]
    for group in groups:
        name = str(group.get("name") or "required_mcp_lookup").strip() or "required_mcp_lookup"
        candidates = [
            str(item).strip()
            for item in (group.get("any_of") or [])
            if str(item).strip()
        ]
        if not candidates:
            continue
        lines.append(
            f"- `{name}`: for every applicable entity occurrence, call at least one of "
            f"{candidates} with arguments identifying that entity."
        )
    lines.extend(
        [
            "",
            "After the required tool calls complete, return only the extraction output "
            "required by the prompt above. If a lookup is unresolved, leave those "
            "lookup-only values unset; do not invent lookup values to replace a "
            "missing tool result.",
        ]
    )
    return "\n".join(lines).strip()


def format_required_tool_feedback(
    tool_activity_errors: list[str],
    *,
    groups: list[dict[str, Any]],
    executed_tool_names: list[str] | None = None,
) -> str:
    executed = [
        str(name).strip()
        for name in (executed_tool_names or [])
        if str(name).strip()
    ]
    lines = [
        "REQUIRED MCP TOOL ACTIVITY FEEDBACK:",
        "The previous attempt returned extraction output without satisfying the required "
        "MCP tool contract. This is a recoverable tool-activity miss, not a final failure yet.",
    ]
    for error in tool_activity_errors:
        lines.append(f"- {error}")
    if groups:
        lines.append("Required groups for the next attempt:")
        for group in groups:
            name = str(group.get("name") or "required_mcp_lookup").strip()
            candidates = [
                str(item).strip()
                for item in (group.get("any_of") or [])
                if str(item).strip()
            ]
            if candidates:
                lines.append(
                    f"- `{name}`: for every applicable entity occurrence, call at least one "
                    f"of {candidates} with arguments identifying that entity."
                )
    lines.extend(
        [
            f"Tools executed in the previous attempt: {executed or []}",
            "ACTION FOR THIS RETRY:",
            "1. For every applicable entity occurrence not yet covered, call at least one "
            "required tool from each missing group with arguments identifying that entity.",
            "2. Only after those tool calls have executed, return the extraction output.",
            "3. Do not answer with JSON-only output before the required tool activity occurs.",
        ]
    )
    return "\n".join(lines)


def required_tool_activity_errors(
    metadata: dict[str, Any] | None,
    validation: dict[str, Any] | None,
) -> list[str]:
    groups = required_executed_tool_groups(validation)
    if not groups:
        return []
    activity = (metadata or {}).get("tool_activity") or {}
    executed = {
        str(name).strip()
        for name in (activity.get("executed_tool_names") or [])
        if str(name).strip()
    }
    errors: list[str] = []
    for group in groups:
        candidates = {
            str(name).strip()
            for name in (group.get("any_of") or [])
            if str(name).strip()
        }
        if candidates and executed.isdisjoint(candidates):
            errors.append(
                f"{group.get('name') or 'required MCP lookup'} requires one of "
                f"{sorted(candidates)}; executed={sorted(executed)}"
            )
    return errors


def inject_required_tool_contract(
    prompt: str,
    validation: dict[str, Any] | None,
    *,
    use_agent: bool,
) -> str:
    if not use_agent:
        return prompt
    block = format_required_tool_contract_block(required_executed_tool_groups(validation))
    if not block:
        return prompt
    marker = "## Required MCP Tool Contract"
    if marker in prompt:
        return prompt
    return f"{prompt.rstrip()}\n\n{block}\n"


def _ledger_target_evidence_passages(ledger_text: str) -> list[str]:
    """Read the complete target passage already stored on a closed ledger."""
    try:
        payload = json.loads(strip_code_fences(ledger_text))
    except Exception:
        return []
    if not isinstance(payload, dict):
        return []
    scope = payload.get("scope_resolution")
    raw = scope.get("target_evidence") if isinstance(scope, dict) else None
    if isinstance(raw, str) and raw.strip():
        return [raw.strip()]
    if not isinstance(raw, list):
        return []
    return [str(item).strip() for item in raw if str(item).strip()]


def append_complete_target_passage(prompt: str, ledger_text: str) -> str:
    """Hand the whole target passage to the next step so later sentences are not dropped."""
    passages = _ledger_target_evidence_passages(ledger_text)
    if not passages:
        return prompt
    return (
        prompt.rstrip()
        + "\n\n---- PIPELINE-INJECTED COMPLETE TARGET PASSAGE: BEGIN ----\n"
        "This is the complete producing workflow. The first span that names the "
        "target is an identity anchor, not a start bound. Earlier same-source "
        "operations consumed by later sentences count, and later sentences count. "
        "Cover every in-scope operation. Do not start at the first identifying "
        "mention and do not stop after the first sentence.\n\n"
        + "\n\n".join(passages)
        + "\n---- PIPELINE-INJECTED COMPLETE TARGET PASSAGE: END ----\n"
    )


def _inheritance_brief_payload(brief: str) -> dict[str, Any]:
    start = brief.find("{")
    end = brief.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("procedure inheritance brief has no JSON object")
    payload = json.loads(brief[start : end + 1])
    if not isinstance(payload, dict) or not isinstance(
        payload.get("effective_workflow"), list
    ):
        raise ValueError("procedure inheritance brief has invalid structured payload")
    return payload


def _inheritance_context_passages(brief: str) -> list[str]:
    if not str(brief or "").strip():
        return []
    try:
        payload = _inheritance_brief_payload(brief)
    except Exception:
        return []
    passages: list[str] = []
    seen: set[str] = set()

    def _add(quote: str) -> None:
        text = str(quote or "").strip()
        if text and text not in seen:
            seen.add(text)
            passages.append(text)

    for dependency in payload.get("dependencies") or []:
        if isinstance(dependency, dict):
            _add(str(dependency.get("source_evidence") or ""))
    for workflow in payload.get("base_workflows") or []:
        atoms = workflow.get("atoms") if isinstance(workflow, dict) else None
        if not isinstance(atoms, list):
            continue
        for atom in atoms:
            if isinstance(atom, dict):
                _add(str(atom.get("source_evidence") or ""))
    return passages


def append_complete_inheritance_context(prompt: str, brief: str) -> str:
    """Hand the full inherited source context to the next step."""
    if not str(brief or "").strip():
        return prompt
    passages = _inheritance_context_passages(brief)
    block = (
        "Respect same-as / following / analogously inheritance. The inherited "
        "context is complete. Apply only the target's explicit changes; do not "
        "drop inherited operations just because the target restatement does not "
        "repeat them."
    )
    if passages:
        block = block + "\n\n" + "\n\n".join(passages)
    return (
        prompt.rstrip()
        + "\n\n---- PIPELINE-INJECTED COMPLETE INHERITANCE CONTEXT: BEGIN ----\n"
        + block
        + "\n---- PIPELINE-INJECTED COMPLETE INHERITANCE CONTEXT: END ----\n"
    )


def merge_extraction_validation(
    iteration_validation: dict | None,
    mcp_set_name: str | None,
) -> dict[str, Any]:
    merged = dict(iteration_validation or {})
    set_validation = load_mcp_set_extraction_validation(mcp_set_name)
    if set_validation and "required_executed_tool_groups" not in merged:
        merged["required_executed_tool_groups"] = set_validation.get(
            "required_executed_tool_groups"
        ) or []
    return merged
