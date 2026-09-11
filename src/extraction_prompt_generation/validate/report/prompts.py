"""Prompt-file quality, slot-binding, and schema-contract checks.

Does not prescribe wording. See report/README.md.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from src.extraction_prompt_generation.compile.context import (
    AgenticGenerationContext,
)
from src.extraction_prompt_generation.validate.report.common import (
    _read_texts,
)


def _prompt_quality_report(
    context: AgenticGenerationContext,
    prompt_paths: list[Path] | None = None,
) -> tuple[list[str], list[str]]:
    """Validate generic prompt usability without prescribing wording or layout."""
    failures: list[str] = []
    warnings: list[str] = []
    prompt_texts = (
        {
            path.name: path.read_text(encoding="utf-8", errors="replace")
            for path in prompt_paths or []
            if path.is_file()
        }
        if prompt_paths is not None
        else _read_texts(Path(context.prompts_dir), "*.md")
    )
    if not prompt_texts:
        warnings.append("No prompt files found; prompt quality validation skipped")
        return failures, warnings
    placeholder_re = re.compile(r"TODO|FIXME|\{\{[^}\n]+\}\}", re.IGNORECASE)
    for name, text in prompt_texts.items():
        if not text.strip():
            failures.append(f"{name}: prompt artifact is empty")
            continue
        matches = sorted(set(m.group(0) for m in placeholder_re.finditer(text)))
        if matches:
            failures.append(
                f"{name}: unresolved prompt placeholder/residue: {', '.join(matches[:8])}"
            )
    return failures, warnings


def _prompt_tbox_fidelity_report(
    context: AgenticGenerationContext,
    prompt_paths: list[Path] | None = None,
) -> tuple[list[str], list[str]]:
    """Reserve T-Box fidelity decisions for the single-artifact LLM reviewer."""
    return [], []


def validate_prompt_runtime_bindings(
    path: Path,
    context: AgenticGenerationContext | None = None,
) -> dict[str, Any]:
    """Validate that a generated prompt exposes its runtime data channel."""
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    name = path.name
    required_groups: list[tuple[str, ...]] = []
    allowed_slots: set[str]
    forbidden_slots: set[str] = set()
    is_extension = bool(
        context is not None
        and getattr(getattr(context, "ontology", None), "role", "") == "extension"
    )
    if is_extension and name.startswith("EXTRACTION_"):
        required_groups.extend(
            [
                ("{entity_label}",),
                ("{entity_uri}",),
            ]
        )
        allowed_slots = {"entity_label", "entity_uri"}
        forbidden_slots = {
            "paper_content",
            "iteration_hints",
            "top_entities",
            "hints",
            "main_ontology_a_box",
        }
    elif name.startswith("KG_BUILDING_"):
        return {
            "ok": True,
            "failures": [],
            "required_groups": [],
            "allowed_slots": [],
            "evidence": {"skipped": "kg_building_prompts_are_not_generated"},
        }
    else:
        required_groups.append(("{paper_content}",))
        allowed_slots = {
            "paper_content",
            "context",
            "entity_label",
            "entity_uri",
            "doi",
            "hash",
            "top_entities",
            "hints",
            "iteration_input",
            "iteration_hints",
            "accumulated_hints",
            "entity_identity_dossier",
        }
    if name.startswith("EXTRACTION_") and name != "EXTRACTION_ITER_1.md":
        required_groups.extend(
            [
                ("{entity_label}",),
                ("{entity_uri}",),
            ]
        )
    slot_matches = re.findall(
        r"(?<!\{)\{([A-Za-z_][A-Za-z0-9_]*)\}(?!\})",
        text,
    )
    slot_counts = {
        slot: slot_matches.count(slot) for slot in sorted(set(slot_matches))
    }
    observed_slots = set(slot_counts)
    failures: list[str] = []
    missing_groups = [
        group for group in required_groups if not any(slot in text for slot in group)
    ]
    for group in missing_groups:
        failures.append(
            f"{name}: missing runtime binding; accepted slot group: "
            + " | ".join(group)
        )
    unknown_slots = sorted(observed_slots - allowed_slots)
    if unknown_slots:
        failures.append(
            f"{name}: unknown runtime binding slot(s): "
            + ", ".join(f"{{{slot}}}" for slot in unknown_slots)
        )
    observed_forbidden_slots = sorted(observed_slots & forbidden_slots)
    if observed_forbidden_slots:
        failures.append(
            f"{name}: forbidden runtime binding slot(s) for this runtime contract: "
            + ", ".join(f"{{{slot}}}" for slot in observed_forbidden_slots)
        )
    duplicated_slots = {
        slot: count for slot, count in slot_counts.items() if count > 1
    }
    if duplicated_slots:
        failures.append(
            f"{name}: each runtime binding must appear exactly once; duplicates: "
            + ", ".join(
                f"{{{slot}}}={count}"
                for slot, count in sorted(duplicated_slots.items())
            )
        )
    return {
        "ok": not failures,
        "failures": failures,
        "observed_artifacts": [str(path)],
        "evidence": {
            "required_slot_groups": [list(group) for group in required_groups],
            "missing_slot_groups": [list(group) for group in missing_groups],
            "unknown_slots": unknown_slots,
            "forbidden_slots": observed_forbidden_slots,
            "slot_counts": slot_counts,
            "duplicated_slots": duplicated_slots,
        },
    }


def _prompt_runtime_binding_report(
    context: AgenticGenerationContext,
    prompt_paths: list[Path] | None = None,
) -> tuple[list[str], list[str], list[dict[str, Any]]]:
    failures: list[str] = []
    obligations: list[dict[str, Any]] = []
    paths = (
        sorted(prompt_paths)
        if prompt_paths is not None
        else sorted(Path(context.prompts_dir).glob("*.md"))
    )
    for path in paths:
        result = validate_prompt_runtime_bindings(path, context)
        item_failures = list(result["failures"])
        failures.extend(item_failures)
        obligations.append(
            {
                "subject_key": path.name,
                "failures": item_failures,
                "observed_artifacts": [str(path)],
                "evidence": result["evidence"],
            }
        )
    return failures, [], obligations


def _iteration_prompt_schema_contract_report(
    context: AgenticGenerationContext,
    prompt_paths: list[Path] | None = None,
) -> tuple[list[str], list[str]]:
    """Defer natural-language interchange semantics to the LLM reviewer.

    Keyword and regex matching cannot distinguish an instruction that authorizes
    a competing output shape from one that explicitly prohibits it. Mechanical
    prompt validation is therefore limited elsewhere to objective file and
    runtime-slot structure; this semantic contract intentionally has no
    script-based gate.
    """
    del context, prompt_paths
    return [], []
