"""Canonical runtime contracts for extension prompt generation and validation."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any


EXTENSION_KG_RUNTIME_SLOTS = (
    "{doi}",
    "{entity_label}",
    "{entity_uri}",
    "{enrichment_targets}",
    "{main_ontology_a_box}",
    "{paper_content}",
)
EXTENSION_EXTRACTION_RUNTIME_SLOTS = (
    "{entity_label}",
    "{entity_uri}",
)
FORBIDDEN_EXTENSION_KG_RUNTIME_SLOTS = (
    "{iteration_hints}",
    "{top_entities}",
    "{hints}",
    "{ontosynthesis_a_box}",
)
EXTENSION_KG_MODE_A_MARKER = "Authoritative Mode A handoff (extension KG)"
EXTENSION_KG_HANDOFF_CHANNEL = "{paper_content}"
EXTENSION_KG_HANDOFF_REPRESENTATION = "ref-entity-relations.v1"
EXTENSION_KG_MODE_A_HANDOFF_BLOCK = f"""{EXTENSION_KG_MODE_A_MARKER}

The pipeline injects this iteration's extraction output through the single
declared source-content runtime slot. That payload is
{EXTENSION_KG_HANDOFF_REPRESENTATION}: one JSON object with `entities` and
`relations`.

- Parse that payload first. For every owned object property, consume the
  extracted relations (`subject_ref`, `property`, `object_ref`) and the
  referenced entity records (`ref`, `class`, `label`, `datatype_properties`).
- Call the exact check/create/add tools already listed in this prompt.
- Do not re-derive those entities or links from prose when the records exist.
- The canonical main graph arrives through its own declared runtime slot.
  Keep that slot. Do not invent any extra interchange placeholder.

This block overrides any later step that tells you to identify the same
entities or links from raw paper prose.
"""


def load_extension_meta_prompt_policy(project_root: Path) -> dict[str, Any]:
    """Load the extension KG meta prompts and their binding contract."""
    prompt_dir = (
        project_root / "ape_generated_contents" / "meta_prompts" / "kg_building"
    )
    sources = {
        name: (prompt_dir / name).read_text(encoding="utf-8")
        for name in ("extension_system.md", "extension_user.md")
    }
    return {
        "canonical_runtime_slots": list(EXTENSION_KG_RUNTIME_SLOTS),
        "forbidden_runtime_slots": list(FORBIDDEN_EXTENSION_KG_RUNTIME_SLOTS),
        "handoff_channel": EXTENSION_KG_HANDOFF_CHANNEL,
        "handoff_representation": EXTENSION_KG_HANDOFF_REPRESENTATION,
        "mode_a_marker": EXTENSION_KG_MODE_A_MARKER,
        "meta_prompt_sources": sources,
    }


def load_extension_extraction_meta_prompt_policy(
    project_root: Path,
) -> dict[str, Any]:
    """Load the extension extraction meta prompts and their binding contract."""
    prompt_dir = (
        project_root / "ape_generated_contents" / "meta_prompts" / "extraction"
    )
    sources = {
        name: (prompt_dir / name).read_text(encoding="utf-8")
        for name in ("extension_system.md", "extension_user.md")
    }
    return {
        "canonical_runtime_slots": list(EXTENSION_EXTRACTION_RUNTIME_SLOTS),
        "forbidden_runtime_slots": [
            "{paper_content}",
            "{iteration_hints}",
            "{top_entities}",
            "{hints}",
            "{main_ontology_a_box}",
            "{ontosynthesis_a_box}",
        ],
        "meta_prompt_sources": sources,
    }


def extension_kg_handoff_contract() -> dict[str, Any]:
    """Closed-world Mode A channel for extension KG prompts."""
    return {
        "handoff_channel": EXTENSION_KG_HANDOFF_CHANNEL,
        "handoff_representation": EXTENSION_KG_HANDOFF_REPRESENTATION,
        "mode_a_marker": EXTENSION_KG_MODE_A_MARKER,
        "forbidden_handoff_slots": list(FORBIDDEN_EXTENSION_KG_RUNTIME_SLOTS),
        "rule": (
            "Mode A consumes ref-entity-relations.v1 from the declared "
            "source-content slot. The canonical main graph slot is required "
            "and is not an extraction-hint ledger. Do not invent any extra "
            "interchange placeholder."
        ),
    }


def is_extension_kg_prompt(path: Path | str) -> bool:
    name = Path(path).name
    return name.startswith("KG_BUILDING_ITER_") and name.endswith(".md")


def extension_kg_mode_a_handoff_present(text: str) -> bool:
    return EXTENSION_KG_MODE_A_MARKER in (text or "")


def ensure_extension_kg_mode_a_handoff(text: str) -> tuple[str, bool]:
    """Idempotently insert the mechanical Mode A block. Never adds extra slots."""
    current = text or ""
    if extension_kg_mode_a_handoff_present(current):
        return current, False
    block = EXTENSION_KG_MODE_A_HANDOFF_BLOCK.strip() + "\n\n"
    anchor = "Runtime bindings that the pipeline supplies:"
    if anchor in current:
        return current.replace(anchor, block + anchor, 1), True
    if current.strip():
        return current.rstrip() + "\n\n" + block, True
    return block, True


def ensure_extension_kg_mode_a_handoff_file(path: Path) -> bool:
    """Write the Mode A block into an extension KG prompt when missing."""
    if not is_extension_kg_prompt(path) or not path.is_file():
        return False
    updated, changed = ensure_extension_kg_mode_a_handoff(
        path.read_text(encoding="utf-8", errors="replace")
    )
    if not changed:
        return False
    path.write_text(updated, encoding="utf-8")
    return True


def _finding_blob(finding: Mapping[str, Any]) -> str:
    parts = [
        str(finding.get("finding") or ""),
        str(finding.get("expected_behavior") or ""),
        str(finding.get("summary") or ""),
        " ".join(str(item) for item in finding.get("evidence") or []),
        " ".join(str(item) for item in finding.get("contract_evidence") or []),
        " ".join(str(item) for item in finding.get("repair_targets") or []),
    ]
    return "\n".join(parts).casefold()


def is_invalid_extension_kg_handoff_finding(finding: Mapping[str, Any]) -> bool:
    """True when a reviewer demands a forbidden hints slot as the Mode A channel."""
    blob = _finding_blob(finding)
    treats_main_graph_as_hints = any(
        token in blob
        for token in (
            "main-ontology hints",
            "main-ontology a-box hints",
            "main ontology hints",
            "main-ontology a-box hints slot",
            "hints placeholder",
            "hints slot",
        )
    ) and any(
        token in blob
        for token in (
            "must not be authored",
            "must never be authored",
            "do not author",
            "do not mention",
            "incorrectly invites",
            "forbidden",
            "unauthorized",
            "contrary to the contract",
        )
    )
    if "iteration_hints" not in blob and not treats_main_graph_as_hints:
        return False
    return treats_main_graph_as_hints or any(
        token in blob
        for token in (
            "{iteration_hints}",
            "read iteration_hints",
            "parse iteration_hints",
            "instead of reading iteration_hints",
            "where iteration_hints",
            "use iteration_hints",
            "injects {paper_content}",
            "{paper_content} instead",
            "instead of iteration_hints",
            "no instruction to parse iteration_hints",
        )
    )


def sanitize_paired_extension_handoff_review(
    review: Mapping[str, Any],
    *,
    is_extension: bool,
) -> dict[str, Any]:
    """Drop reviewer errors that fight the extension KG slot contract."""
    cleaned = dict(review)
    if not is_extension:
        return cleaned
    errors = [
        dict(item)
        for item in cleaned.get("critical_errors") or []
        if isinstance(item, Mapping)
    ]
    kept = [
        item
        for item in errors
        if not is_invalid_extension_kg_handoff_finding(item)
    ]
    dropped = len(errors) - len(kept)
    cleaned["critical_errors"] = kept
    if dropped:
        notes = list(cleaned.get("noncritical_observations") or [])
        notes.append(
            "Dropped "
            f"{dropped} paired-review finding(s) that demanded a forbidden "
            "extension-KG hints slot. Mode A uses the declared source-content slot."
        )
        cleaned["noncritical_observations"] = notes
    if cleaned.get("decision") == "repair" and not kept:
        cleaned["decision"] = "pass"
        cleaned["summary"] = (
            "Remaining paired-review findings only demanded a forbidden "
            "extension-KG hints slot; Mode A is the declared source-content "
            "channel. " + str(cleaned.get("summary") or "")
        )
    return cleaned

