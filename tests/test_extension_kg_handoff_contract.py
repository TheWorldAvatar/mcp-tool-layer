from __future__ import annotations

from pathlib import Path

from src.agents.scripts_and_prompts_generation.agentic_generation_validation import (
    validate_prompt_runtime_bindings,
)
from src.agents.scripts_and_prompts_generation.extension_prompt_contract import (
    EXTENSION_KG_HANDOFF_CHANNEL,
    EXTENSION_KG_MODE_A_MARKER,
    ensure_extension_kg_mode_a_handoff,
    is_invalid_extension_kg_handoff_finding,
    sanitize_paired_extension_handoff_review,
)


def test_mode_a_block_does_not_add_forbidden_slots() -> None:
    source = (
        "{doi}\n{entity_label}\n{entity_uri}\n"
        "{enrichment_targets}\n{main_ontology_a_box}\n{paper_content}\n"
    )
    updated, changed = ensure_extension_kg_mode_a_handoff(source)
    assert changed
    assert EXTENSION_KG_MODE_A_MARKER in updated
    assert updated.count("{paper_content}") == 1
    assert "{iteration_hints}" not in updated
    again, changed_again = ensure_extension_kg_mode_a_handoff(updated)
    assert again == updated
    assert not changed_again


def test_0829_iteration_hints_finding_is_dropped() -> None:
    finding = {
        "finding": (
            "Object property hasChemicalBuildingUnit must use Mode A, but the "
            "KG prompt injects {paper_content} instead of reading iteration_hints."
        ),
        "expected_behavior": (
            "Adopt Mode A: Read iteration_hints in ref-entity-relations.v1."
        ),
        "evidence": [
            "KG prompt footer injects: {paper_content} "
            "(no instruction to parse iteration_hints.entities/relations)."
        ],
        "repair_targets": ["KG_BUILDING_ITER_1.md"],
    }
    assert is_invalid_extension_kg_handoff_finding(finding)
    review = sanitize_paired_extension_handoff_review(
        {
            "decision": "repair",
            "summary": "Mode A handoff is required",
            "critical_errors": [finding],
            "noncritical_observations": [],
        },
        is_extension=True,
    )
    assert review["decision"] == "pass"
    assert review["critical_errors"] == []


def test_0830_main_graph_slot_is_not_a_hints_finding() -> None:
    finding = {
        "finding": (
            "KG prompt authors and invites consumption of a main-ontology "
            "A-Box hints slot in a Mode A extension KG, contrary to the "
            "contract rule that this slot must not be authored for this handoff."
        ),
        "expected_behavior": (
            "Do not author or require a main-ontology hints placeholder."
        ),
        "evidence": [
            "KG prompt text labels {main_ontology_a_box} as Main-ontology A-Box hints"
        ],
        "repair_targets": ["KG_BUILDING_ITER_1.md"],
    }
    assert is_invalid_extension_kg_handoff_finding(finding)


def test_unrelated_finding_is_kept() -> None:
    finding = {
        "finding": "KG omits export_memory as the final lifecycle call.",
        "expected_behavior": "Call export_memory with the exact signature.",
        "evidence": ["Last step is add_hasChemicalBuildingUnit"],
        "repair_targets": ["KG_BUILDING_ITER_1.md"],
    }
    assert not is_invalid_extension_kg_handoff_finding(finding)
    review = sanitize_paired_extension_handoff_review(
        {
            "decision": "repair",
            "summary": "lifecycle gap",
            "critical_errors": [finding],
        },
        is_extension=True,
    )
    assert review["decision"] == "repair"
    assert len(review["critical_errors"]) == 1


def test_slot_contract_still_forbids_iteration_hints(
    tmp_path: Path,
) -> None:
    path = tmp_path / "KG_BUILDING_ITER_1.md"
    path.write_text("{iteration_hints}\n", encoding="utf-8")
    context = type(
        "Ctx",
        (),
        {"ontology": type("Ont", (), {"role": "extension"})()},
    )()
    result = validate_prompt_runtime_bindings(path, context)
    assert result["ok"] is False
    assert any("iteration_hints" in item for item in result["failures"])
    assert EXTENSION_KG_HANDOFF_CHANNEL == "{paper_content}"
