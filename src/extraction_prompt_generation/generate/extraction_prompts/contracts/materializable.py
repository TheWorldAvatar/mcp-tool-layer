"""Deterministic `*.inc` rendering, T-Box splice, and component write.

GPT-5 does not author these companions. See contracts/README.md.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable, Mapping

from src.extraction_prompt_generation.compile.context import (
    AgenticGenerationContext,
)
from src.extraction_prompt_generation.generate.extraction_prompts.contracts.generation_contract import (
    _format_nested_owned_dependent_scalar_contract,
    _prompt_artifact_generation_contract,
)
from src.extraction_prompt_generation.generate.extraction_prompts.contracts.markers import (
    _DETERMINISTIC_TBOX_BEGIN,
    _DETERMINISTIC_TBOX_END,
    _NESTED_OWNED_SCALAR_BEGIN,
    _NESTED_OWNED_SCALAR_END,
)
from src.extraction_prompt_generation.generate.extraction_prompts.contracts.role_and_guidance import (
    _semantic_text_natural_ledger_rules,
)


def _materializable_prompt_component_path(target: Path) -> Path:
    """Sibling `*.materializable.inc` path for one EXTRACTION / PRE_EXTRACTION file."""
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
    from src.extraction_prompt_generation.pipeline.formatters import (
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
        nested_block = _format_nested_owned_dependent_scalar_contract(
            list(
                generation_contract.get("nested_owned_dependent_scalar_contract")
                or []
            )
        )
        if nested_block:
            component = f"{component.rstrip()}\n\n{nested_block}"
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


def _strip_nested_owned_scalar_splice(text: str) -> str:
    """Remove any previously spliced nested-owned-scalar block."""
    while _NESTED_OWNED_SCALAR_BEGIN in text:
        start = text.find(_NESTED_OWNED_SCALAR_BEGIN)
        stop = text.find(_NESTED_OWNED_SCALAR_END, start)
        if stop < 0:
            text = text[:start]
            break
        text = text[:start] + text[stop + len(_NESTED_OWNED_SCALAR_END) :]
    return text.strip()


def _splice_nested_owned_scalar_into_extraction_prompt(
    target: Path,
    component_text: str,
) -> None:
    """Idempotently append compiled nested-owner scalars onto a semantic-text prompt."""
    if not component_text.strip():
        return
    if not target.name.upper().startswith("EXTRACTION_ITER_") or not target.is_file():
        return
    body = _strip_nested_owned_scalar_splice(
        target.read_text(encoding="utf-8", errors="replace")
    )
    if (
        target.name.upper() == "EXTRACTION_ITER_1.MD"
        and "SEMANTIC_HINTS_V1" not in body
    ):
        return
    target.write_text(
        f"{body.rstrip()}\n\n"
        f"{_NESTED_OWNED_SCALAR_BEGIN}\n"
        f"{component_text.rstrip()}\n"
        f"{_NESTED_OWNED_SCALAR_END}\n",
        encoding="utf-8",
    )


def _detach_nested_owned_scalar_from_prompt(target: Path) -> None:
    """Leave only LLM-authored extraction text before a generation or repair edit."""
    if not target.name.upper().startswith("EXTRACTION_ITER_") or not target.is_file():
        return
    body = _strip_nested_owned_scalar_splice(
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
    generation_contract = _prompt_artifact_generation_contract(context, target)
    nested_block = _format_nested_owned_dependent_scalar_contract(
        list(
            generation_contract.get("nested_owned_dependent_scalar_contract") or []
        )
    )
    if (
        nested_block
        and (generation_contract.get("iteration_spec") or {}).get(
            "hint_representation"
        )
        == "semantic-text.v1"
    ):
        _splice_nested_owned_scalar_into_extraction_prompt(target, nested_block)
    return component
