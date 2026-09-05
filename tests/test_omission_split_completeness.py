import json

import pytest

from src.pipelines.top_entity_extraction.omission_split_completeness import (
    apply_complete_omission,
    assess_split_completeness,
    listed_covers_named_outcome,
    run_split_complete_omission,
)


def test_parent_label_does_not_cover_named_outcomes() -> None:
    assert listed_covers_named_outcome("IRMOP-51", "IRMOP-51") is True
    assert (
        listed_covers_named_outcome("IRMOP-51", "IRMOP-51 (triclinic form)") is False
    )
    assert (
        listed_covers_named_outcome(
            "IRMOP-51 (triclinic form)",
            "IRMOP-51 (triclinic form)",
        )
        is True
    )
    assert (
        listed_covers_named_outcome(
            "Synthesis of IRMOP-51 (triclinic form)",
            "IRMOP-51 (triclinic form)",
        )
        is True
    )
    assert (
        listed_covers_named_outcome(
            "IRMOP-51 (triclinic form)",
            "IRMOP-51 (cubic form)",
        )
        is False
    )
    assert listed_covers_named_outcome("IRMOP-51", "IRMOP-51_cubic") is False
    assert listed_covers_named_outcome("VMOC-3", "VMOC-3-2anthracene") is False
    assert listed_covers_named_outcome("Zr-bpydc", "Zr-bpydc-CuCl2") is False


def test_short_identity_covers_longer_alias_wording() -> None:
    long_vmop18 = (
        "Synthesis of [NH2Me2]4{[V6O6(OCH3)9(VO4)]4(TATB)4}-(MeOH)24 (VMOP-18)"
    )
    listed_line = "ChemicalSynthesis-1 [VMOP-18]"
    assert listed_covers_named_outcome("VMOP-18", long_vmop18) is True
    assert listed_covers_named_outcome(listed_line, long_vmop18) is True
    assert listed_covers_named_outcome("VMOP-18", "Synthesis of VMOP-18") is True
    assert listed_covers_named_outcome(long_vmop18, "VMOP-18") is True
    assert listed_covers_named_outcome("VMOP-18", "VMOP-19") is False
    assert listed_covers_named_outcome("VMOP-18", "VMOP-18 from VOSO4") is False


def test_apply_does_not_script_drop_existing_labels() -> None:
    payload = {
        "split_groups": [
            {
                "named_outcomes": [
                    "IRMOP-51 (cubic form)",
                    "IRMOP-51 (triclinic form)",
                ]
            }
        ],
        "missing_candidates": [
            {
                "candidate_label": "IRMOP-51 (cubic form)",
                "source_evidence": "cubic IRMOP-51 was isolated independently.",
                "exclusion_status": "cleared",
                "ambiguity_status": "resolved",
                "reason": "An independently executed named outcome is absent.",
            },
            {
                "candidate_label": "IRMOP-51 (triclinic form)",
                "source_evidence": "triclinic IRMOP-51 was isolated independently.",
                "exclusion_status": "cleared",
                "ambiguity_status": "resolved",
                "reason": "An independently executed named outcome is absent.",
            },
        ],
    }
    augmented, applied = apply_complete_omission(
        candidate_text="ChemicalSynthesis-2 [IRMOP-51]\n",
        payload=payload,
        source_text=(
            "The cubic IRMOP-51 was isolated independently. "
            "The triclinic IRMOP-51 was isolated independently."
        ),
        line_prefix="ChemicalSynthesis",
    )
    assert applied["added_count"] == 2
    assert "[IRMOP-51]" in augmented
    assert "IRMOP-51 (cubic form)" in augmented
    assert "IRMOP-51 (triclinic form)" in augmented


def test_partial_sibling_recall_is_incomplete() -> None:
    payload = {
        "split_groups": [
            {
                "named_outcomes": [
                    "IRMOP-51 (cubic form)",
                    "IRMOP-51 (triclinic form)",
                ]
            }
        ],
        "missing_candidates": [
            {
                "candidate_label": "IRMOP-51 (triclinic form)",
                "sibling_outcomes_same_passage": ["IRMOP-51 (cubic form)"],
            }
        ],
    }
    report = assess_split_completeness(
        "ChemicalSynthesis-2 [IRMOP-51]\n",
        payload,
    )
    assert report["complete"] is False
    assert report["already_listed"] == []
    assert "IRMOP-51 (cubic form)" in report["unemitted_still_missing"]


def test_short_listed_identities_cover_claimed_formula_aliases() -> None:
    payload = {
        "split_groups": [
            {
                "named_outcomes": [
                    "Synthesis of [NH2Me2]4{[V6O6(OCH3)9(VO4)]4(TATB)4}-(MeOH)24 (VMOP-18)",
                    "Synthesis of [NH2Me2]8{[V6O6(OCH3)9(SO4)]4(TATB)4}-(MeOH)8 (VMOP-19)",
                ]
            }
        ],
        "missing_candidates": [],
    }
    report = assess_split_completeness(
        "ChemicalSynthesis-1 [VMOP-18]\nChemicalSynthesis-2 [VMOP-19]\n",
        payload,
    )
    assert report["complete"] is True
    assert report["still_missing"] == []
    assert report["unemitted_still_missing"] == []


def test_complete_sibling_recall_passes_when_parent_is_listed() -> None:
    payload = {
        "split_groups": [
            {
                "named_outcomes": [
                    "IRMOP-51 (cubic form)",
                    "IRMOP-51 (triclinic form)",
                ]
            }
        ],
        "missing_candidates": [
            {"candidate_label": "IRMOP-51 (cubic form)"},
            {"candidate_label": "IRMOP-51 (triclinic form)"},
        ],
    }
    report = assess_split_completeness(
        "ChemicalSynthesis-1 [IRMOP-50]\nChemicalSynthesis-2 [IRMOP-51]\n",
        payload,
    )
    assert report["complete"] is True
    assert report["already_listed"] == []
    assert report["unemitted_still_missing"] == []


def test_virtual_group_from_sibling_field() -> None:
    payload = {
        "split_groups": [],
        "missing_candidates": [
            {
                "candidate_label": "IRMOP-51 (triclinic form)",
                "sibling_outcomes_same_passage": ["IRMOP-51 (cubic form)"],
            }
        ],
    }
    report = assess_split_completeness(
        "ChemicalSynthesis-2 [IRMOP-51]\n",
        payload,
    )
    assert report["complete"] is False
    assert "IRMOP-51 (cubic form)" in report["unemitted_still_missing"]


def test_missing_split_groups_field_is_incomplete() -> None:
    report = assess_split_completeness(
        "ChemicalSynthesis-1 [IRMOP-50]\n",
        {"missing_candidates": []},
    )
    assert report["complete"] is False
    assert "missing_split_groups_field" in report["issues"]


def test_apply_does_not_add_longer_alias_of_listed_identity() -> None:
    payload = {
        "missing_candidates": [
            {
                "candidate_label": (
                    "Synthesis of [NH2Me2]4{[V6O6(OCH3)9(VO4)]4(TATB)4}"
                    "-(MeOH)24 (VMOP-18)"
                ),
                "source_evidence": "Reddish brown crystals of VMOP-18 were isolated.",
                "exclusion_status": "cleared",
                "ambiguity_status": "resolved",
                "reason": "Alias of the already listed VMOP-18 identity.",
            },
            {
                "candidate_label": (
                    "Synthesis of [NH2Me2]8{[V6O6(OCH3)9(SO4)]4(TATB)4}"
                    "-(MeOH)8 (VMOP-19)"
                ),
                "source_evidence": "VMOP-19 was obtained by the same method.",
                "exclusion_status": "cleared",
                "ambiguity_status": "resolved",
                "reason": "Alias of the already listed VMOP-19 identity.",
            },
        ]
    }
    augmented, applied = apply_complete_omission(
        candidate_text=(
            "ChemicalSynthesis-1 [VMOP-18]\n"
            "ChemicalSynthesis-2 [VMOP-19]\n"
        ),
        payload=payload,
        source_text=(
            "Reddish brown crystals of VMOP-18 were isolated. "
            "VMOP-19 was obtained by the same method."
        ),
        line_prefix="ChemicalSynthesis",
    )
    assert applied["added_count"] == 0
    assert "[VMOP-18]" in augmented
    assert "[VMOP-19]" in augmented
    assert "TATB" not in augmented
    assert [item["effective_decision"] for item in applied["candidate_checks"]] == [
        "reject",
        "reject",
    ]


def test_apply_adds_only_grounded_novel_candidates() -> None:
    payload = {
        "missing_candidates": [
            {
                "candidate_label": "IRMOP-51 (cubic form)",
                "source_evidence": "cubic IRMOP-51 was isolated independently.",
                "exclusion_status": "cleared",
                "ambiguity_status": "resolved",
                "reason": "The cubic route is an independently executed named outcome.",
            },
            {
                "candidate_label": "IRMOP-51",
                "source_evidence": "cubic IRMOP-51 was isolated independently.",
                "exclusion_status": "cleared",
                "ambiguity_status": "resolved",
                "reason": "Already listed as a parent.",
            },
        ]
    }
    augmented, applied = apply_complete_omission(
        candidate_text="ChemicalSynthesis-2 [IRMOP-51]\n",
        payload=payload,
        source_text="The cubic IRMOP-51 was isolated independently.",
        line_prefix="ChemicalSynthesis",
    )
    assert "IRMOP-51 (cubic form)" in augmented
    assert applied["added_count"] == 1
    assert [item["effective_decision"] for item in applied["candidate_checks"]] == [
        "add",
        "reject",
    ]


@pytest.mark.asyncio
async def test_incomplete_judgement_retries_then_accepts() -> None:
    payloads = [
        {
            "split_groups": [
                {
                    "named_outcomes": [
                        "IRMOP-51 (cubic form)",
                        "IRMOP-51 (triclinic form)",
                    ]
                }
            ],
            "missing_candidates": [
                {
                    "candidate_label": "IRMOP-51 (triclinic form)",
                    "source_evidence": "triclinic IRMOP-51 was isolated.",
                    "class_contract_evidence": "create exactly N instances",
                    "exclusion_status": "cleared",
                    "ambiguity_status": "resolved",
                    "sibling_outcomes_same_passage": ["IRMOP-51 (cubic form)"],
                    "reason": "Only one sibling was added.",
                }
            ],
        },
        {
            "split_groups": [
                {
                    "named_outcomes": [
                        "IRMOP-51 (cubic form)",
                        "IRMOP-51 (triclinic form)",
                    ]
                }
            ],
            "missing_candidates": [
                {
                    "candidate_label": "IRMOP-51 (cubic form)",
                    "source_evidence": "cubic IRMOP-51 was isolated.",
                    "class_contract_evidence": "create exactly N instances",
                    "exclusion_status": "cleared",
                    "ambiguity_status": "resolved",
                    "sibling_outcomes_same_passage": ["IRMOP-51 (triclinic form)"],
                    "reason": "Cubic sibling was missing.",
                },
                {
                    "candidate_label": "IRMOP-51 (triclinic form)",
                    "source_evidence": "triclinic IRMOP-51 was isolated.",
                    "class_contract_evidence": "create exactly N instances",
                    "exclusion_status": "cleared",
                    "ambiguity_status": "resolved",
                    "sibling_outcomes_same_passage": ["IRMOP-51 (cubic form)"],
                    "reason": "Triclinic sibling was missing.",
                },
            ],
        },
    ]

    class FakeLlm:
        def __init__(self) -> None:
            self.prompts: list[str] = []

        async def ainvoke(self, prompt: str) -> str:
            self.prompts.append(prompt)
            return json.dumps(payloads[len(self.prompts) - 1])

    llm = FakeLlm()
    source = "The cubic IRMOP-51 was isolated. The triclinic IRMOP-51 was isolated."
    report = await run_split_complete_omission(
        llm=llm,
        candidate_text="ChemicalSynthesis-2 [IRMOP-51]\n",
        source_text=source,
        top_class_iri="https://example.test/ChemicalSynthesis",
        top_class_comment="Create exactly N instances for independently executed outcomes.",
        line_prefix="ChemicalSynthesis",
        max_attempts=3,
    )
    assert report["ok"] is True
    assert report["attempts"] == 2
    assert "unemitted_still_missing" in llm.prompts[1]
    assert "IRMOP-51 (cubic form)" in report["candidate_text_out"]
    assert "IRMOP-51 (triclinic form)" in report["candidate_text_out"]
    assert "[IRMOP-51]" in report["candidate_text_out"]
    assert report["applied"]["added_count"] == 2
