"""Mechanical hard gates for generated extraction prompts.

Placeholders, ledger headings, and mechanically injected slots. English
contract strings are not rewritten here. See contracts/README.md.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from src.extraction_prompt_generation.compile.context import (
    AgenticGenerationContext,
)
from src.extraction_prompt_generation.generate.extraction_prompts.contracts.generation_contract import (
    _prompt_artifact_generation_contract,
)
from src.extraction_prompt_generation.generate.extraction_prompts.contracts.materializable import (
    _materializable_prompt_component_path,
    _materializable_prompt_component_text,
    _strip_nested_owned_scalar_splice,
)
from src.extraction_prompt_generation.generate.extraction_prompts.contracts.scope import (
    _is_enrichment_iteration_spec,
)
from src.extraction_prompt_generation.slim_family_lock import slim_family_failures


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


def _token_present(text: str, token: str) -> bool:
    """Whole-token match for class or property locals."""
    return bool(re.search(rf"\b{re.escape(token)}\b", text))


def _owner_and_property_cooccur(
    text: str, owner_class: str, property_local: str, *, window: int = 800
) -> bool:
    """True when owner and nested property appear in the same authored window."""
    for match in re.finditer(rf"\b{re.escape(property_local)}\b", text):
        start = max(0, match.start() - window)
        stop = min(len(text), match.end() + window)
        if _token_present(text[start:stop], owner_class):
            return True
    return False


def _nested_owned_dependent_scalar_failures(
    text: str,
    rows: list[dict[str, Any]],
    target_name: str,
) -> list[str]:
    """Fail when authored text drops compiled nested owner-occurrence scalars."""
    if not rows:
        return []
    authored = _strip_nested_owned_scalar_splice(text)
    failures: list[str] = []
    owners_by_property: dict[str, list[str]] = {}
    for row in rows:
        owner = str(row.get("owner_class_local") or "").strip()
        for raw_prop in row.get("property_locals") or []:
            prop = str(raw_prop).strip()
            if not prop or not owner:
                continue
            owners_by_property.setdefault(prop, [])
            if owner not in owners_by_property[prop]:
                owners_by_property[prop].append(owner)
    for prop, owners in owners_by_property.items():
        if not _token_present(authored, prop):
            failures.append(
                f"{target_name}: authored prompt must mention nested owned-dependent "
                f"scalar `{prop}` under an owner occurrence "
                f"({', '.join(f'`{name}`' for name in owners)})"
            )
            continue
        if not any(
            _owner_and_property_cooccur(authored, owner, prop) for owner in owners
        ):
            failures.append(
                f"{target_name}: nested scalar `{prop}` must be required under an "
                f"owner occurrence ({', '.join(f'`{name}`' for name in owners)}), "
                "not only on a sibling dependent-class heading"
            )
    return failures


def _validate_generated_prompt_hard_gates(
    target: Path,
    context: AgenticGenerationContext,
) -> dict[str, Any]:
    """Validate prompt bindings and mechanical residue before semantic repair."""
    from src.extraction_prompt_generation.validate.report import (
        validate_prompt_runtime_bindings,
    )

    detached_mechanical_slots = _detach_mechanically_injected_runtime_slots(
        target, context
    )
    text = target.read_text(encoding="utf-8", errors="replace")
    binding = validate_prompt_runtime_bindings(target, context)
    failures = list(binding.get("failures") or [])
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
    failures.extend(slim_family_failures(target.name, text))
    if (
        str(context.ontology.role or "") == "extension"
        and target.name.upper().startswith("EXTRACTION_ITER_")
        and "SEMANTIC_HINTS_V1" not in text
    ):
        failures.append(
            f"{target.name}: extension extraction is locked to semantic-text.v1 "
            "(SEMANTIC_HINTS_V1); JSON ref-entity-relations.v1 is forbidden"
        )
    candidate_type_evidence: dict[str, Any] = {}
    enrichment_lock_evidence: dict[str, Any] = {}
    fixed_classification_schema_evidence: list[str] = []
    structured_ledger_expectation: list[str] = []
    nested_scalar_failures: list[str] = []
    if target.name.upper().startswith("EXTRACTION_ITER_") and not (
        target.name.upper() == "EXTRACTION_ITER_1.MD"
        and str(context.ontology.role or "") != "extension"
    ):
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
            nested_scalar_failures = _nested_owned_dependent_scalar_failures(
                text,
                list(
                    prompt_contract.get("nested_owned_dependent_scalar_contract")
                    or []
                ),
                target.name,
            )
            failures.extend(nested_scalar_failures)
        if (
            not _is_enrichment_iteration_spec(iteration_spec)
            and "current target entity only" not in text.casefold()
        ):
            failures.append(
                f"{target.name}: EXTRACTION_ITER_2+ must lock extraction to the "
                "current target entity only"
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
                    "ordered member enrichment",
                    "ordered-member enrichment",
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
            "nested_owned_dependent_scalars": nested_scalar_failures,
            "unresolved_residue": residue,
            "pre_extraction_candidate_types": candidate_type_evidence,
            "main_iteration_enrichment_lock": enrichment_lock_evidence,
        },
    }
