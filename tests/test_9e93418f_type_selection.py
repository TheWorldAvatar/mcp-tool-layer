from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.agents.scripts_and_prompts_generation.agentic_generation_runner import (
    _tbox_comment_fidelity_contract,
)
from src.agents.scripts_and_prompts_generation.pure_llm_generation import (
    _warning_marked_tbox_contract,
)
from src.pipelines.main_ontology_extractions.extract import (
    _build_closed_ledger_audit_prompt,
    _build_type_selection_judge_prompt,
    _parse_type_selection_judgement,
    _run_type_selection_judge,
)


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (
    "H2TEI (100 mg) dissolved in 3 mL DEF and Cr(OAc)2 (50 mg) in 5 mL "
    "DEF were mixed and placed in a 20 mL vial. The vial was sit in room "
    "temperature for 2 days. Purple octahedral shape crystals of Cr-3 were obtained."
)
CANDIDATE = {
    "evidence": [
        {
            "evidence_id": "E001",
            "verbatim_quote": "H2TEI (100 mg)",
            "candidate_types": ["Add"],
        },
        {
            "evidence_id": "E002",
            "verbatim_quote": "3 mL DEF",
            "candidate_types": ["Add"],
        },
        {
            "evidence_id": "E003",
            "verbatim_quote": "Cr(OAc)2 (50 mg)",
            "candidate_types": ["Add"],
        },
        {
            "evidence_id": "E004",
            "verbatim_quote": "5 mL DEF",
            "candidate_types": ["Add"],
        },
        {
            "evidence_id": "E005",
            "verbatim_quote": "were mixed and placed in a 20 mL vial",
            "candidate_types": ["Transfer"],
        },
        {
            "evidence_id": "E006",
            "verbatim_quote": "The vial was sit in room temperature for 2 days",
            "candidate_types": ["HeatChill"],
        },
        {
            "evidence_id": "E007",
            "verbatim_quote": "Purple octahedral shape crystals of Cr-3 were obtained",
            "candidate_types": ["Crystallize"],
        },
    ]
}


def _judgement(evidence_ids: set[str] | None = None) -> str:
    checks = []
    for item in CANDIDATE["evidence"]:
        evidence_id = item["evidence_id"]
        if evidence_ids is not None and evidence_id not in evidence_ids:
            continue
        excluded = evidence_id in {"E005", "E007"}
        checks.append(
            {
                "candidate_evidence_id": evidence_id,
                "selected_types": item["candidate_types"],
                "verdict": "excluded" if excluded else "pass",
                "corrected_types": [] if excluded else item["candidate_types"],
                "source_evidence": item["verbatim_quote"],
                "reason": (
                    "The marked T-Box boundary says this wording does not independently "
                    "instantiate the selected class."
                    if excluded
                    else "The selected class satisfies the applicable T-Box boundary."
                ),
            }
        )
    return json.dumps({"type_checks": checks})


def test_warning_marker_contract_is_generic_and_materialized_for_9e93418f() -> None:
    ttl = (ROOT / "data" / "ontologies" / "ontosynthesis.ttl").read_text(
        encoding="utf-8"
    )
    for class_local in ("Crystallize", "HeatChill", "Stir", "Transfer"):
        assert f"ontosyn:{class_local} a owl:Class" in ttl
    assert ttl.count('rdfs:comment """【Warning】') == 4

    contract = _warning_marked_tbox_contract(
        {
            "classes": {
                "ChoiceAlpha": {"comment": "【Warning】 Apply the declared threshold."},
                "ChoiceBeta": {"comment": "Ordinary binding rule."},
            },
            "properties": {},
        }
    )
    requirements = " ".join(contract["requirements"])
    assert [row["local"] for row in contract["marked_comments"]] == ["ChoiceAlpha"]
    assert "domain-specific" in requirements
    assert "Transfer" not in requirements
    assert "Crystallize" not in requirements
    assert "【Warning】" in _tbox_comment_fidelity_contract()
    for name in ("PRE_EXTRACTION_ITER_3.md", "EXTRACTION_ITER_3.md"):
        prompt = (
            ROOT
            / "ai_generated_contents_ontosyn_regen_v3"
            / "prompts"
            / "ontosynthesis"
            / name
        ).read_text(encoding="utf-8")
        assert "attention" in prompt.lower()
        assert "marked" in prompt.lower()
        assert (
            "marker changes attention only" in prompt.lower()
            or "marker is attention only" in prompt.lower()
            or "explicit comparison" in prompt.lower()
            or "mandatory comparison" in prompt.lower()
            or "marked comment" in prompt.lower()
        )
        if name.startswith("PRE_EXTRACTION"):
            assert (
                "【Warning】" in prompt
                or "(Warning)" in prompt
                or "warning-marked t-box attention block" in prompt.lower()
            )
            for class_local in ("Crystallize", "HeatChill", "Stir", "Transfer"):
                assert (
                    f"{class_local} 【Warning】" in prompt
                    or f"【Warning】 {class_local}" in prompt
                    or f"{class_local} (Warning)" in prompt
                    or (
                        "warning-marked t-box attention block" in prompt.lower()
                        and class_local in prompt
                    )
                )
        else:
            for class_local in ("Crystallize", "HeatChill", "Stir", "Transfer"):
                assert (
                    f"{class_local} (marked)" in prompt
                    or f"{class_local}: 【Warning】" in prompt
                    or f"{class_local} — complete marked comment" in prompt
                    or (
                        f"Candidate: {class_local}" in prompt
                        and "【Warning】" in prompt
                    )
                )
            assert "mixed/combined and placed in a vial" in prompt.lower()


def test_non_type_auditor_explicitly_excludes_type_selection() -> None:
    prompt = _build_closed_ledger_audit_prompt(
        original_prompt="T-Box contract",
        source_text=SOURCE,
        candidate_text=json.dumps(CANDIDATE),
    )
    assert "NOT a type-selection judge" in prompt
    assert "exclusive responsibility" in prompt
    assert "expected_type" not in prompt
    assert "classification_violations" not in prompt


@pytest.mark.asyncio
async def test_dedicated_type_judge_rejects_only_two_9e93418f_overtypes() -> None:
    class FakeTypeLlm:
        def __init__(self) -> None:
            self.prompts: list[str] = []

        async def ainvoke(self, prompt: str) -> str:
            self.prompts.append(prompt)
            evidence_id = next(
                item["evidence_id"]
                for item in CANDIDATE["evidence"]
                if f'"evidence_id": "{item["evidence_id"]}"' in prompt
            )
            return _judgement({evidence_id})

    llm = FakeTypeLlm()
    feedback = await _run_type_selection_judge(
        audit_llm=llm,
        original_prompt="T-Box comments include 【Warning】 boundaries.",
        source_text=SOURCE,
        candidate_text=json.dumps(CANDIDATE),
    )

    assert len(feedback) == 2
    assert feedback[0].startswith("TYPE_SELECTION_EXCLUDED `E005`")
    assert feedback[1].startswith("TYPE_SELECTION_EXCLUDED `E007`")
    assert len(llm.prompts) == len(CANDIDATE["evidence"])
    assert "exclusive responsibility" in llm.prompts[0]
    assert "【Warning】" in llm.prompts[0]
    assert "ORIGINAL PRE-EXTRACTION PROMPT" not in llm.prompts[0]
    assert all(prompt.count('"evidence_id":') == 1 for prompt in llm.prompts)


def test_type_judge_schema_requires_exact_candidate_coverage() -> None:
    incomplete = json.loads(_judgement())
    incomplete["type_checks"].pop()
    with pytest.raises(ValueError, match="cover every candidate evidence atom"):
        _parse_type_selection_judgement(
            json.dumps(incomplete),
            candidate_text=json.dumps(CANDIDATE),
        )

    prompt = _build_type_selection_judge_prompt(
        original_prompt="T-Box comments include 【Warning】 boundaries.",
        source_text=SOURCE,
        candidate_text=json.dumps(CANDIDATE),
    )
    assert "Do not audit coverage" in prompt
    assert "Do not create a missing atom" in prompt
