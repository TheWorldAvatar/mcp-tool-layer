from __future__ import annotations

from pathlib import Path

from src.agents.scripts_and_prompts_generation.level1_code_repair import (
    LLMJsonResult,
)
from src.agents.scripts_and_prompts_generation.llm_iteration_continuity_judge import (
    judge_iteration_continuity,
)


def _vote(decision: str) -> dict:
    return {
        "decision": decision,
        "source_item_id": "Equipment: vessel A",
        "aspect_id": "entity_presence",
        "summary": f"Transition is {decision}.",
        "source_evidence": "Equipment: vessel A",
        "old_abox_evidence": "The prior graph contains the vessel.",
        "final_abox_evidence": "The final graph was compared.",
        "confidence": 1.0,
    }


def _write_graphs(tmp_path: Path, *, changed: bool) -> tuple[Path, Path]:
    old = tmp_path / "iteration_2.ttl"
    final = tmp_path / "final.ttl"
    old.write_text(
        "@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .\n"
        '<urn:vessel> a <urn:Equipment> ; rdfs:label "vessel A" .',
        encoding="utf-8",
    )
    final.write_text(
        (
            "@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .\n"
            '<urn:vessel> a <urn:LabEquipment> ; rdfs:label "vessel A" .'
            if changed
            else "@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .\n"
            '<urn:vessel> a <urn:Equipment> ; rdfs:label "vessel A" .'
        ),
        encoding="utf-8",
    )
    return old, final


def _run(
    *,
    old: Path,
    final: Path,
    invoke,
) -> dict:
    return judge_iteration_continuity(
        prior_iterations=[
            {
                "iteration": 2,
                "hints_content": "SEMANTIC_HINTS_V1\n\nEquipment: vessel A\n",
                "abox_path": old,
            }
        ],
        final_abox_path=final,
        ontology_contract={
            "classes": [
                {"class_iri": "urn:Equipment"},
                {"class_iri": "urn:LabEquipment"},
            ]
        },
        model="judge-a",
        reviewer_model="judge-b",
        verifier_model="judge-c",
        invoke=invoke,
    )


def test_exactly_preserved_aspect_needs_no_llm_vote(tmp_path: Path) -> None:
    old, final = _write_graphs(tmp_path, changed=False)

    def invoke(*_args, **_kwargs):
        raise AssertionError("exactly preserved RDF must not invoke an LLM")

    report = _run(old=old, final=final, invoke=invoke)

    assert report["accepted"] is True
    assert report["panels"][0]["mechanical_exact_preservation"] is True
    assert report["panels"][0]["decision"] == "preserved"


def test_unanimous_valid_refinement_is_accepted(tmp_path: Path) -> None:
    old, final = _write_graphs(tmp_path, changed=True)
    prompts: list[str] = []

    def invoke(_model: str, prompt: str, **_kwargs) -> LLMJsonResult:
        prompts.append(prompt)
        return LLMJsonResult(
            data=_vote("valid_refinement"),
            elapsed_seconds=0.1,
            token_usage={"total_tokens": 4},
        )

    report = _run(old=old, final=final, invoke=invoke)

    assert report["accepted"] is True
    assert len(prompts) == 3
    assert report["panels"][0]["decision"] == "valid_refinement"
    assert report["token_usage"]["total_tokens"] == 12


def test_changed_aspect_receives_complete_final_abox_when_local_match_is_empty(
    tmp_path: Path,
) -> None:
    old = tmp_path / "iteration_2.ttl"
    final = tmp_path / "final.ttl"
    old.write_text(
        "@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .\n"
        '<urn:old> a <https://example.test/Equipment> ; '
        'rdfs:label "Teflon-lined reactor" .',
        encoding="utf-8",
    )
    final.write_text(
        "@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .\n"
        '<urn:synthesis> <https://example.test/hasEquipment> <urn:new> .\n'
        '<urn:new> a <https://example.test/LabEquipment> ; '
        'rdfs:label "25 mL Teflon-lined reactor" .',
        encoding="utf-8",
    )
    prompts: list[str] = []

    def invoke(_model: str, prompt: str, **_kwargs) -> LLMJsonResult:
        prompts.append(prompt)
        return LLMJsonResult(
            data=_vote("valid_refinement"),
            elapsed_seconds=0.1,
            token_usage={},
        )

    report = _run(old=old, final=final, invoke=invoke)

    assert report["accepted"] is True
    assert len(prompts) == 3
    assert all("COMPLETE FINAL A-BOX:" in prompt for prompt in prompts)
    assert all("25 mL Teflon-lined reactor" in prompt for prompt in prompts)
    assert all("hasEquipment" in prompt for prompt in prompts)


def test_regression_requires_two_unanimous_independent_panels(
    tmp_path: Path,
) -> None:
    old, final = _write_graphs(tmp_path, changed=True)
    prompts: list[str] = []

    def invoke(_model: str, prompt: str, **_kwargs) -> LLMJsonResult:
        prompts.append(prompt)
        return LLMJsonResult(
            data=_vote("regression"),
            elapsed_seconds=0.1,
            token_usage={"total_tokens": 5},
        )

    report = _run(old=old, final=final, invoke=invoke)

    assert report["accepted"] is False
    assert len(prompts) == 6
    assert sum("CONFIRMATION ROUND: true" in item for item in prompts) == 3
    assert report["panels"][0]["confirmed_regression"] is True
    assert len(report["confirmed_regressions"]) == 1


def test_disagreement_remains_non_blocking_uncertain(tmp_path: Path) -> None:
    old, final = _write_graphs(tmp_path, changed=True)
    prompts: list[str] = []

    def invoke(model: str, prompt: str, **_kwargs) -> LLMJsonResult:
        prompts.append(prompt)
        decision = "regression" if model == "judge-a" else "valid_refinement"
        return LLMJsonResult(
            data=_vote(decision),
            elapsed_seconds=0.1,
            token_usage={},
        )

    report = _run(old=old, final=final, invoke=invoke)

    assert report["accepted"] is True
    assert len(prompts) == 6
    assert report["panels"][0]["decision"] == "uncertain"
    assert len(report["uncertain_transitions"]) == 1
